"""One-time script: approve USDC.e for Polymarket exchange contracts."""
import os
import sys
import time
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")
from web3 import Web3

pk = os.getenv("PK")
w3 = Web3(Web3.HTTPProvider("https://polygon-bor-rpc.publicnode.com"))
account = w3.eth.account.from_key(pk)
eoa = account.address
print(f"Wallet: {eoa}")

usdc_address = w3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")
erc20_abi = [
    {"inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
     "name": "approve", "outputs": [{"name": "", "type": "bool"}],
     "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
     "name": "allowance", "outputs": [{"name": "", "type": "uint256"}],
     "stateMutability": "view", "type": "function"},
]
usdc = w3.eth.contract(address=usdc_address, abi=erc20_abi)

exchanges = [
    ("Exchange", "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"),
    ("NegRisk Exchange", "0xC5d563A36AE78145C45a50134d48A1215220f80a"),
    ("NegRisk Adapter", "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"),
]

MAX_UINT256 = 2**256 - 1

# Wait for any pending TX to clear
print("Waiting for pending TXs to clear...")
for i in range(30):
    confirmed = w3.eth.get_transaction_count(eoa, "latest")
    pending = w3.eth.get_transaction_count(eoa, "pending")
    if confirmed == pending:
        break
    print(f"  confirmed={confirmed} pending={pending}, waiting...")
    time.sleep(5)

nonce = w3.eth.get_transaction_count(eoa, "latest")
print(f"Nonce: {nonce}")

gas_price = max(w3.eth.gas_price * 2, w3.to_wei(50, "gwei"))
print(f"Gas price: {gas_price / 1e9:.1f} gwei")

for name, ex in exchanges:
    ex_addr = w3.to_checksum_address(ex)
    current = usdc.functions.allowance(eoa, ex_addr).call()
    print(f"\n{name}: allowance = ${current / 1e6:.2f}")

    if current < 1000 * 10**6:
        # Try estimating gas first
        try:
            estimated = usdc.functions.approve(ex_addr, MAX_UINT256).estimate_gas({"from": eoa})
            print(f"  Estimated gas: {estimated}")
        except Exception as e:
            print(f"  Gas estimate failed: {e}")
            print(f"  Trying approve(0) first...")
            # Some USDC implementations require approve(0) before setting new value
            try:
                tx0 = usdc.functions.approve(ex_addr, 0).build_transaction({
                    "from": eoa, "nonce": nonce, "gas": 100000,
                    "gasPrice": gas_price, "chainId": 137,
                })
                signed0 = w3.eth.account.sign_transaction(tx0, pk)
                h0 = w3.eth.send_raw_transaction(signed0.raw_transaction)
                print(f"  approve(0) TX: 0x{h0.hex()}")
                r0 = w3.eth.wait_for_transaction_receipt(h0, timeout=120)
                print(f"  approve(0) status: {r0.status}")
                nonce += 1
            except Exception as e2:
                print(f"  approve(0) also failed: {e2}")
                continue

        print(f"  Sending approve(MAX)... nonce={nonce}")
        tx = usdc.functions.approve(ex_addr, MAX_UINT256).build_transaction({
            "from": eoa, "nonce": nonce, "gas": 100000,
            "gasPrice": gas_price, "chainId": 137,
        })
        signed = w3.eth.account.sign_transaction(tx, pk)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        print(f"  TX: 0x{tx_hash.hex()}")
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        print(f"  Status: {'OK' if receipt.status == 1 else 'REVERTED'} (gas used: {receipt.gasUsed})")
        nonce += 1

        new_a = usdc.functions.allowance(eoa, ex_addr).call()
        print(f"  New allowance: ${new_a / 1e6:.2f}")
    else:
        print(f"  Already approved")

print("\nDone!")
