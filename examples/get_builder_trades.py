import os

from polymarket_console.client import ClobClient
from polymarket_console.clob_types import ApiCreds, TradeParams
from py_builder_signing_sdk.config import BuilderConfig, BuilderApiKeyCreds

from dotenv import load_dotenv
from pprint import pprint

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
    builder_config = BuilderConfig(
        local_builder_creds=BuilderApiKeyCreds(
            key=os.getenv("BUILDER_API_KEY"),
            secret=os.getenv("BUILDER_SECRET"),
            passphrase=os.getenv("BUILDER_PASS_PHRASE"),
        )
    )

    client = ClobClient(
        host, key=key, chain_id=chain_id, creds=creds, builder_config=builder_config
    )

    resp = client.get_builder_trades()
    pprint(resp)
    print("Done!")


main()
