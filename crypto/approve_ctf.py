"""One-time script: approve CTF tokens (setApprovalForAll) for Polymarket exchange contracts."""
import os
import time
from pathlib import Path
from dotenv import load_dotenv
from web3 import Web3

load_dotenv(Path(__file__).parent / ".env")

pk = os.getenv("PK")
w3 = Web3(Web3.HTTPProvider("https://polygon-bor-rpc.publicnode.com"))
account = w3.eth.account.from_key(pk)
eoa = account.address
print(f"Wallet: {eoa}")

ctf_address = w3.to_checksum_address("0x4D97DCd97eC945f40cF65F87097ACe5EA0476045")
abi = [
    {"inputs": [{"name": "operator", "type": "address"}, {"name": "approved", "type": "bool"}],
     "name": "setApprovalForAll", "outputs": [], "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [{"name": "owner", "type": "address"}, {"name": "operator", "type": "address"}],
     "name": "isApprovedForAll", "outputs": [{"name": "", "type": "bool"}],
     "stateMutability": "view", "type": "function"},
]
ctf = w3.eth.contract(address=ctf_address, abi=abi)

exchanges = [
    ("Exchange", "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"),
    ("NegRisk Exchange", "0xC5d563A36AE78145C45a50134d48A1215220f80a"),
    ("NegRisk Adapter", "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"),
]

nonce = w3.eth.get_transaction_count(eoa, "latest")
gas_price = max(w3.eth.gas_price * 2, w3.to_wei(50, "gwei"))
print(f"Nonce: {nonce}, Gas: {gas_price / 1e9:.1f} gwei")

for name, ex in exchanges:
    ex_addr = w3.to_checksum_address(ex)
    approved = ctf.functions.isApprovedForAll(eoa, ex_addr).call()
    print(f"\n{name}: approved = {approved}")

    if not approved:
        print(f"  Sending setApprovalForAll... nonce={nonce}")
        tx = ctf.functions.setApprovalForAll(ex_addr, True).build_transaction({
            "from": eoa, "nonce": nonce, "gas": 100000,
            "gasPrice": gas_price, "chainId": 137,
        })
        signed = w3.eth.account.sign_transaction(tx, pk)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        print(f"  TX: 0x{tx_hash.hex()}")
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        print(f"  Status: {'OK' if receipt.status == 1 else 'REVERTED'}")
        nonce += 1
    else:
        print(f"  Already approved")

print("\nDone!")
