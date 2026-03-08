"""
Setup Polymarket CLOB API credentials for weather bot wallet.
Run once to generate API keys.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

env_path = Path(__file__).parent / ".env"
load_dotenv(env_path)

from polymarket_console import ClobClient


def main():
    pk = os.getenv("PK")
    if not pk:
        print("Error: PK not found in .env")
        return

    chain_id = int(os.getenv("CHAIN_ID", "137"))
    host = os.getenv("CLOB_API_URL", "https://clob.polymarket.com")

    print(f"Connecting to {host}...")
    print(f"Chain ID: {chain_id}")

    try:
        client = ClobClient(
            host=host,
            key=pk,
            chain_id=chain_id,
            signature_type=0,  # EOA wallet
        )

        ok = client.get_ok()
        print(f"Server OK: {ok}")

        address = client.get_address()
        print(f"Wallet: {address}")

        print("\nCreating API credentials...")
        creds = client.create_or_derive_api_creds()

        if creds:
            print("\n" + "=" * 50)
            print("API CREDENTIALS CREATED!")
            print("=" * 50)
            print(f"API_KEY: {creds.api_key}")
            print(f"SECRET: {creds.api_secret}")
            print(f"PASSPHRASE: {creds.api_passphrase}")
            print("=" * 50)

            update_env_file(env_path, creds)
            print("\n.env updated!")

            # Verify L2 auth
            client.set_api_creds(creds)
            print("\nVerifying L2 auth...")
            try:
                orders = client.get_orders()
                print(f"Open orders: {len(orders)}")
                print("L2 auth works!")
            except Exception as e:
                print(f"L2 error: {e}")
        else:
            print("Failed to create credentials")

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()


def update_env_file(env_path: Path, creds):
    """Update .env with new credentials."""
    content = env_path.read_text()
    content = content.replace("CLOB_API_KEY=", f"CLOB_API_KEY={creds.api_key}")
    content = content.replace("CLOB_SECRET=", f"CLOB_SECRET={creds.api_secret}")
    content = content.replace("CLOB_PASS_PHRASE=", f"CLOB_PASS_PHRASE={creds.api_passphrase}")
    env_path.write_text(content)


if __name__ == "__main__":
    main()
