import os

from polymarket_console.client import ClobClient
from polymarket_console.clob_types import ApiCreds
from dotenv import load_dotenv

from polymarket_console.constants import AMOY


load_dotenv()


def main():
    host = os.getenv("CLOB_API_URL", "https://clob.polymarket.com")
    key = os.getenv("PK")
    creds = ApiCreds(
        api_key=os.getenv("CLOB_API_KEY"),
        api_secret=os.getenv("CLOB_SECRET"),
        api_passphrase=os.getenv("CLOB_PASS_PHRASE"),
    )
    chain_id = AMOY
    client = ClobClient(host, key=key, chain_id=chain_id, creds=creds)

    # Replace with the readonly API key you want to delete
    readonly_api_key = "019aee85-4ea1-79cd-a287-8508f21209a2"

    resp = client.delete_readonly_api_key(readonly_api_key)
    print(resp)
    print("Done!")


main()
