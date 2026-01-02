import os

from polymarket_console.client import ClobClient
from polymarket_console.clob_types import ApiCreds, OpenOrderParams
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

    resp = client.get_orders(
        OpenOrderParams(
            market="0x37a6a2dd9f3469495d9ec2467b0a764c5905371a294ce544bc3b2c944eb3e84a",
        )
    )
    print(resp)
    print("Done!")


main()
