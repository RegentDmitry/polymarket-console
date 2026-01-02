import os

from polymarket_console.client import ClobClient
from polymarket_console.clob_types import ApiCreds, MarketOrderArgs, OrderType
from dotenv import load_dotenv
from polymarket_console.constants import AMOY
from polymarket_console.order_builder.constants import SELL


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

    # create a market buy order for the equivalent of 100 USDC at the market price
    order_args = MarketOrderArgs(
        token_id="71321045679252212594626385532706912750332728571942532289631379312455583992563",
        amount=100,  # SHARES
        side=SELL,
    )
    signed_order = client.create_market_order(order_args)
    resp = client.post_order(signed_order, orderType=OrderType.FOK)
    print(resp)
    print("Done!")


main()
