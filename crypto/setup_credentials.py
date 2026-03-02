"""
Generate CLOB API credentials for the crypto bot wallet.
Run once: cd crypto && python setup_credentials.py
Uses earthquake venv which has py_clob_client installed.
"""

import os
import sys
from pathlib import Path

# Use earthquake venv's packages
eq_venv = Path(__file__).parent.parent / "earthquakes" / ".venv" / "lib"
for p in eq_venv.glob("python*/site-packages"):
    sys.path.insert(0, str(p))

from dotenv import load_dotenv
from py_clob_client.client import ClobClient

env_path = Path(__file__).parent / ".env"
load_dotenv(env_path)


def main():
    pk = os.getenv("PK")
    if not pk:
        print("Error: PK not found in .env")
        return

    sig_type = int(os.getenv("SIGNATURE_TYPE", "0"))
    host = "https://clob.polymarket.com"

    print(f"Connecting to {host}...")
    print(f"Signature Type: {sig_type} (EOA/MetaMask)")

    client = ClobClient(host=host, key=pk, chain_id=137, signature_type=sig_type)

    ok = client.get_ok()
    print(f"Server OK: {ok}")

    # Derive or create API creds
    print("\nDeriving API credentials...")
    creds = client.create_or_derive_api_creds()

    if not creds:
        print("Failed to create credentials")
        return

    print(f"\nAPI_KEY:    {creds.api_key}")
    print(f"SECRET:     {creds.api_secret}")
    print(f"PASSPHRASE: {creds.api_passphrase}")

    # Update .env
    content = env_path.read_text()
    content = content.replace("CLOB_API_KEY=", f"CLOB_API_KEY={creds.api_key}", 1)
    content = content.replace("CLOB_SECRET=", f"CLOB_SECRET={creds.api_secret}", 1)
    content = content.replace("CLOB_PASS_PHRASE=", f"CLOB_PASS_PHRASE={creds.api_passphrase}", 1)
    env_path.write_text(content)

    print("\n.env updated!")

    # Verify
    client.set_api_creds(creds)
    try:
        orders = client.get_orders()
        print(f"Open orders: {len(orders)} — L2 auth works!")
    except Exception as e:
        print(f"L2 check failed: {e}")


if __name__ == "__main__":
    main()
