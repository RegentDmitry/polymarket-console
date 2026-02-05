#!/usr/bin/env python3
"""
Swap native USDC to USDC.e (bridged) on Polygon via Uniswap V3.
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

# Load environment
load_dotenv(Path(__file__).parent.parent / ".env")

# Config
PK = os.getenv("PK")
RPC = os.getenv("POLYGON_RPC", "https://polygon-rpc.com")

# Addresses on Polygon
USDC_NATIVE = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"  # Native USDC (6 decimals)
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"      # USDC.e bridged (6 decimals)
UNISWAP_ROUTER = "0xE592427A0AEce92De3Edee1F18E0157C05861564"  # Uniswap V3 SwapRouter

# ABIs
ERC20_ABI = [
    {"constant": True, "inputs": [{"name": "_owner", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"},
    {"constant": False, "inputs": [{"name": "_spender", "type": "address"}, {"name": "_value", "type": "uint256"}], "name": "approve", "outputs": [{"name": "", "type": "bool"}], "type": "function"},
    {"constant": True, "inputs": [{"name": "_owner", "type": "address"}, {"name": "_spender", "type": "address"}], "name": "allowance", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
]

# Uniswap V3 SwapRouter exactInputSingle
SWAP_ROUTER_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"name": "tokenIn", "type": "address"},
                    {"name": "tokenOut", "type": "address"},
                    {"name": "fee", "type": "uint24"},
                    {"name": "recipient", "type": "address"},
                    {"name": "deadline", "type": "uint256"},
                    {"name": "amountIn", "type": "uint256"},
                    {"name": "amountOutMinimum", "type": "uint256"},
                    {"name": "sqrtPriceLimitX96", "type": "uint160"},
                ],
                "name": "params",
                "type": "tuple",
            }
        ],
        "name": "exactInputSingle",
        "outputs": [{"name": "amountOut", "type": "uint256"}],
        "stateMutability": "payable",
        "type": "function",
    }
]


def main():
    if not PK:
        print("Error: PK not found in .env")
        sys.exit(1)

    w3 = Web3(Web3.HTTPProvider(RPC))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    if not w3.is_connected():
        print("Error: Cannot connect to Polygon RPC")
        sys.exit(1)

    account = w3.eth.account.from_key(PK)
    address = account.address
    print(f"Wallet: {address}")

    # Check balances
    usdc_native = w3.eth.contract(address=Web3.to_checksum_address(USDC_NATIVE), abi=ERC20_ABI)
    usdc_e = w3.eth.contract(address=Web3.to_checksum_address(USDC_E), abi=ERC20_ABI)

    native_balance = usdc_native.functions.balanceOf(address).call()
    bridged_balance = usdc_e.functions.balanceOf(address).call()
    matic_balance = w3.eth.get_balance(address)

    print(f"USDC (native): ${native_balance / 1e6:.2f}")
    print(f"USDC.e: ${bridged_balance / 1e6:.2f}")
    print(f"MATIC: {w3.from_wei(matic_balance, 'ether'):.4f}")

    if native_balance == 0:
        print("\nNo native USDC to swap.")
        sys.exit(0)

    if matic_balance < w3.to_wei(0.01, 'ether'):
        print("\nNot enough MATIC for gas.")
        sys.exit(1)

    amount_in = native_balance  # Swap all
    print(f"\nSwapping ${amount_in / 1e6:.2f} USDC → USDC.e...")

    # Step 1: Approve router to spend USDC
    allowance = usdc_native.functions.allowance(address, UNISWAP_ROUTER).call()
    if allowance < amount_in:
        print("Approving Uniswap router...")
        approve_tx = usdc_native.functions.approve(
            Web3.to_checksum_address(UNISWAP_ROUTER),
            2**256 - 1  # Max approval
        ).build_transaction({
            "from": address,
            "nonce": w3.eth.get_transaction_count(address),
            "gas": 100000,
            "gasPrice": w3.eth.gas_price,
            "chainId": 137,
        })
        signed = w3.eth.account.sign_transaction(approve_tx, PK)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        print(f"Approve tx: {tx_hash.hex()}")
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        if receipt["status"] != 1:
            print("Approval failed!")
            sys.exit(1)
        print("Approved!")

    # Step 2: Swap via Uniswap V3
    router = w3.eth.contract(address=Web3.to_checksum_address(UNISWAP_ROUTER), abi=SWAP_ROUTER_ABI)

    # 0.05% fee pool (500) - most liquid for stablecoin pairs
    # Allow 0.5% slippage for stablecoins
    min_out = int(amount_in * 0.995)
    deadline = w3.eth.get_block("latest")["timestamp"] + 300  # 5 min

    swap_params = {
        "tokenIn": Web3.to_checksum_address(USDC_NATIVE),
        "tokenOut": Web3.to_checksum_address(USDC_E),
        "fee": 500,  # 0.05%
        "recipient": address,
        "deadline": deadline,
        "amountIn": amount_in,
        "amountOutMinimum": min_out,
        "sqrtPriceLimitX96": 0,
    }

    swap_tx = router.functions.exactInputSingle(swap_params).build_transaction({
        "from": address,
        "nonce": w3.eth.get_transaction_count(address),
        "gas": 200000,
        "gasPrice": w3.eth.gas_price,
        "chainId": 137,
        "value": 0,
    })

    signed = w3.eth.account.sign_transaction(swap_tx, PK)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"Swap tx: {tx_hash.hex()}")

    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    if receipt["status"] != 1:
        print("Swap failed!")
        sys.exit(1)

    # Check new balance
    new_bridged = usdc_e.functions.balanceOf(address).call()
    print(f"\nSwap complete!")
    print(f"New USDC.e balance: ${new_bridged / 1e6:.2f}")


if __name__ == "__main__":
    main()
