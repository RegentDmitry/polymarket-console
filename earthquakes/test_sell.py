"""
Debug script: test sell limit order (no on-chain approval).
Run from earthquakes/ dir with the right python.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from polymarket_client import PolymarketClient
from polymarket_console.clob_types import BalanceAllowanceParams, AssetType

client = PolymarketClient()
print(f"Address: {client.get_address()}")

token_id = "41372014694999370754615718846987313813708957489001648644147300608069418187533"

# Check current state
params = BalanceAllowanceParams(asset_type=AssetType.CONDITIONAL, token_id=token_id)
bal = client.client.get_balance_allowance(params)
print(f"Balance: {bal['balance']}")
print(f"Allowances:")
for addr, val in bal.get("allowances", {}).items():
    status = "OK" if int(val) > 0 else "MISSING"
    print(f"  {addr}: {status}")

missing = [a for a, v in bal.get("allowances", {}).items() if int(v) == 0]
if missing:
    print(f"\nMISSING approval for: {missing}")
    print("Need to run on-chain setApprovalForAll for these. Skipping sell test.")
    sys.exit(1)

# Try sell
print("\n--- Attempting SELL limit: 1 token @ 0.07 ---")
try:
    result = client.create_limit_order(
        token_id=token_id,
        side="SELL",
        price=0.07,
        size=1.0,
    )
    print(f"SUCCESS: {result}")
    # Cancel it immediately
    order_id = result.get("orderID") or result.get("order_id")
    if order_id:
        print(f"Cancelling test order {order_id}...")
        client.client.cancel(order_id)
        print("Cancelled.")
except Exception as e:
    print(f"FAILED: {e}")
