"""
Dune Analytics loader for Polymarket political market trades.

Usage:
    # Set up: create parameterized query in Dune web UI, get query_id
    # Set DUNE_API_KEY env var or put in .env

    from dune_loader import DuneLoader
    loader = DuneLoader(query_id=1234567)
    trades = loader.fetch_trades(token_id="11283...")
    positions = loader.reconstruct_positions(trades, as_of="2026-01-15")
"""

import os
import json
import time
import hashlib
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

import requests
from tqdm import tqdm

CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)

# Also check for NegRisk - most political markets use this
DUNE_BASE = "https://api.dune.com/api/v1"


class DuneLoader:
    def __init__(self, query_id: int, api_key: str | None = None):
        self.query_id = query_id
        self.api_key = api_key or os.environ.get("DUNE_API_KEY")
        if not self.api_key:
            # Try .env file
            env_path = Path(__file__).parent.parent.parent / ".env"
            if env_path.exists():
                for line in env_path.read_text().splitlines():
                    if line.startswith("DUNE_API_KEY="):
                        self.api_key = line.split("=", 1)[1].strip().strip("\"'")
                        break
        if not self.api_key:
            raise ValueError("DUNE_API_KEY not found in env or .env file")

        self.headers = {"X-Dune-API-Key": self.api_key}
        self._pending_executions: dict[str, str] = {}  # token_id -> execution_id

    def submit_query(self, token_id: str) -> str | None:
        """Submit a Dune query without waiting. Returns execution_id."""
        cache_file = CACHE_DIR / f"dune_trades_{hashlib.md5(token_id.encode()).hexdigest()[:12]}.json"
        if cache_file.exists():
            return None  # cached

        resp = requests.post(
            f"{DUNE_BASE}/query/{self.query_id}/execute",
            headers=self.headers,
            json={"query_parameters": {"token_id": token_id}},
        )
        resp.raise_for_status()
        exec_id = resp.json()["execution_id"]
        self._pending_executions[token_id] = exec_id
        return exec_id

    def poll_and_download(self, token_id: str, execution_id: str) -> list[dict]:
        """Poll for completion and download results."""
        for _ in range(120):
            time.sleep(5)
            status_resp = requests.get(
                f"{DUNE_BASE}/execution/{execution_id}/status",
                headers=self.headers,
            )
            status_resp.raise_for_status()
            status = status_resp.json()
            if status["is_execution_finished"]:
                credits = status.get("execution_cost_credits", 0)
                print(f"  Done ({token_id[:12]}...): {credits:.1f} credits")
                break
        else:
            raise TimeoutError(f"Query {execution_id} timed out")

        if status["state"] != "QUERY_STATE_COMPLETED":
            raise RuntimeError(f"Query failed: {status['state']}")

        all_rows: list[dict] = []
        offset = 0
        limit = 50000
        while True:
            result_resp = requests.get(
                f"{DUNE_BASE}/execution/{execution_id}/results",
                headers=self.headers,
                params={"limit": limit, "offset": offset},
            )
            result_resp.raise_for_status()
            rows = result_resp.json().get("result", {}).get("rows", [])
            all_rows.extend(rows)
            if len(rows) < limit:
                break
            offset += limit

        # Cache
        cache_file = CACHE_DIR / f"dune_trades_{hashlib.md5(token_id.encode()).hexdigest()[:12]}.json"
        cache_file.write_text(json.dumps(all_rows))
        return all_rows

    def fetch_trades_batch(self, token_ids: list[str]) -> dict[str, list[dict]]:
        """Submit all queries, then poll all in parallel. Returns {token_id: trades}."""
        results: dict[str, list[dict]] = {}
        pending: dict[str, str] = {}  # token_id -> exec_id

        # Submit all non-cached
        for tid in token_ids:
            cache_file = CACHE_DIR / f"dune_trades_{hashlib.md5(tid.encode()).hexdigest()[:12]}.json"
            if cache_file.exists():
                results[tid] = json.loads(cache_file.read_text())
                print(f"  Cached: {tid[:16]}... ({len(results[tid])} trades)")
            else:
                try:
                    exec_id = self.submit_query(tid)
                    if exec_id:
                        pending[tid] = exec_id
                        print(f"  Submitted: {tid[:16]}...")
                        time.sleep(2)  # respect Dune rate limits
                except Exception as e:
                    print(f"  Submit error ({tid[:16]}...): {e}")

        # Poll all pending
        if pending:
            print(f"  Waiting for {len(pending)} Dune queries...")
            for tid, exec_id in pending.items():
                try:
                    rows = self.poll_and_download(tid, exec_id)
                    results[tid] = rows
                    print(f"  Downloaded: {tid[:16]}... ({len(rows)} trades)")
                except Exception as e:
                    print(f"  Error ({tid[:16]}...): {e}")

        return results

    def fetch_trades(self, token_id: str, no_cache: bool = False) -> list[dict]:
        """Fetch all trades for a token from Dune. Caches locally."""
        cache_file = CACHE_DIR / f"dune_trades_{hashlib.md5(token_id.encode()).hexdigest()[:12]}.json"

        if cache_file.exists() and not no_cache:
            return json.loads(cache_file.read_text())

        # Execute query with parameter
        print(f"  Executing Dune query {self.query_id} for token {token_id[:20]}...")
        resp = requests.post(
            f"{DUNE_BASE}/query/{self.query_id}/execute",
            headers=self.headers,
            json={"query_parameters": {"token_id": token_id}},
        )
        resp.raise_for_status()
        execution_id = resp.json()["execution_id"]

        # Poll for completion
        for i in range(120):  # max 10 minutes
            time.sleep(5)
            status_resp = requests.get(
                f"{DUNE_BASE}/execution/{execution_id}/status",
                headers=self.headers,
            )
            status_resp.raise_for_status()
            status = status_resp.json()

            if status["is_execution_finished"]:
                state = status["state"]
                credits = status.get("execution_cost_credits", 0)
                print(f"  Done: {state}, credits: {credits}")
                break
        else:
            raise TimeoutError(f"Query {execution_id} timed out after 10 min")

        if status["state"] != "QUERY_STATE_COMPLETED":
            raise RuntimeError(f"Query failed: {status['state']}")

        # Fetch results (paginated)
        all_rows: list[dict] = []
        offset = 0
        limit = 50000
        while True:
            result_resp = requests.get(
                f"{DUNE_BASE}/execution/{execution_id}/results",
                headers=self.headers,
                params={"limit": limit, "offset": offset},
            )
            result_resp.raise_for_status()
            data = result_resp.json()
            rows = data.get("result", {}).get("rows", [])
            all_rows.extend(rows)

            if len(rows) < limit:
                break
            offset += limit
            print(f"  Fetched {len(all_rows)} rows...")

        print(f"  Total: {len(all_rows)} trades")

        # Cache
        cache_file.write_text(json.dumps(all_rows))
        return all_rows

    def reconstruct_positions(
        self, trades: list[dict], token_id: str, as_of: str | None = None
    ) -> dict[str, float]:
        """
        Reconstruct token balances for all addresses at a given date.

        Args:
            trades: list of trade dicts from Dune
            token_id: the token we're tracking
            as_of: ISO date string "YYYY-MM-DD" or None for all trades

        Returns:
            dict of {address: token_balance} (positive = holds tokens)
        """
        balances: defaultdict[str, float] = defaultdict(float)

        for t in trades:
            trade_time = t.get("evt_block_time", "")
            if as_of and trade_time[:10] > as_of:
                break  # trades are ordered by time

            maker_asset = str(t.get("maker_asset", ""))
            taker_asset = str(t.get("taker_asset", ""))
            maker = t.get("maker", "").lower()
            taker = t.get("taker", "").lower()
            maker_amount = int(t.get("makerAmountFilled", t.get("makeramountfilled", 0))) / 1e6
            taker_amount = int(t.get("takerAmountFilled", t.get("takeramountfilled", 0))) / 1e6

            if maker_asset == token_id:
                # Maker gives tokens → sells, taker receives tokens → buys
                balances[maker] -= maker_amount
                balances[taker] += maker_amount
            elif taker_asset == token_id:
                # Taker gives tokens → sells, maker receives tokens → buys
                balances[taker] -= taker_amount
                balances[maker] += taker_amount

        # Filter out zero/near-zero balances
        return {addr: bal for addr, bal in balances.items() if abs(bal) > 0.01}

    def get_top_holders(
        self, positions: dict[str, float], top_n: int = 30
    ) -> tuple[list[tuple[str, float]], list[tuple[str, float]]]:
        """
        Get top holders for YES side (positive balance) and NO side (negative balance / short).

        Returns:
            (yes_holders, no_holders) - each is list of (address, balance)
        """
        yes_holders = sorted(
            [(addr, bal) for addr, bal in positions.items() if bal > 0.01],
            key=lambda x: x[1],
            reverse=True,
        )[:top_n]

        # Negative balance = net sellers of YES token = bearish = proxy for NO side
        no_holders = sorted(
            [(addr, -bal) for addr, bal in positions.items() if bal < -0.01],
            key=lambda x: x[1],
            reverse=True,
        )[:top_n]

        return yes_holders, no_holders

    def get_price_at(
        self, trades: list[dict], token_id: str, date: str
    ) -> float | None:
        """Get approximate token price on a given date from trades."""
        prices: list[float] = []
        for t in trades:
            trade_time = t.get("evt_block_time", "")
            if trade_time[:10] != date:
                continue

            maker_asset = str(t.get("maker_asset", ""))
            maker_amount = int(t.get("makerAmountFilled", t.get("makeramountfilled", 0)))
            taker_amount = int(t.get("takerAmountFilled", t.get("takeramountfilled", 0)))

            if maker_amount == 0 or taker_amount == 0:
                continue

            if maker_asset == token_id:
                # Maker gives tokens, taker gives USDC
                price = taker_amount / maker_amount
            else:
                # Taker gives tokens, maker gives USDC
                price = maker_amount / taker_amount

            if 0.001 < price < 1.0:
                prices.append(price)

        return sum(prices) / len(prices) if prices else None

    def daily_prices(self, trades: list[dict], token_id: str) -> dict[str, float]:
        """Get daily average prices from trades."""
        daily: defaultdict[str, list[float]] = defaultdict(list)

        for t in trades:
            trade_time = t.get("evt_block_time", "")
            date = trade_time[:10]

            maker_asset = str(t.get("maker_asset", ""))
            maker_amount = int(t.get("makerAmountFilled", t.get("makeramountfilled", 0)))
            taker_amount = int(t.get("takerAmountFilled", t.get("takeramountfilled", 0)))

            if maker_amount == 0 or taker_amount == 0:
                continue

            if maker_asset == token_id:
                price = taker_amount / maker_amount
            else:
                price = maker_amount / taker_amount

            if 0.001 < price < 1.0:
                daily[date].append(price)

        return {d: sum(ps) / len(ps) for d, ps in sorted(daily.items())}


def discover_closed_political_markets(
    tag_slug: str = "politics",
    min_volume: float = 100_000,
    limit: int = 100,
) -> list[dict]:
    """Discover closed political markets from Gamma API."""
    markets: list[dict] = []
    offset = 0

    while offset < limit:
        batch = min(100, limit - offset)
        resp = requests.get(
            "https://gamma-api.polymarket.com/events",
            params={
                "tag_slug": tag_slug,
                "closed": "true",
                "limit": batch,
                "offset": offset,
            },
            timeout=30,
        )
        resp.raise_for_status()
        events = resp.json()

        if not events:
            break

        for event in events:
            tags = [
                t.get("slug", "")
                for t in event.get("tags", [])
                if isinstance(t, dict)
            ]

            for m in event.get("markets", []):
                vol = float(m.get("volumeNum", 0))
                if vol < min_volume:
                    continue

                outcomes = m.get("outcomePrices")
                if not outcomes:
                    continue

                # Parse token IDs
                raw_tokens = m.get("clobTokenIds", [])
                if isinstance(raw_tokens, str):
                    raw_tokens = json.loads(raw_tokens)
                if not raw_tokens or len(raw_tokens) < 2:
                    continue

                # Determine resolution
                yes_won: bool | None = None
                try:
                    outcome_prices = (
                        json.loads(outcomes)
                        if isinstance(outcomes, str)
                        else outcomes
                    )
                    yes_won = outcome_prices[0] == "1" or outcome_prices[0] == 1
                except (json.JSONDecodeError, IndexError, TypeError):
                    yes_won = None

                markets.append(
                    {
                        "event_title": event.get("title", ""),
                        "event_slug": event.get("slug", ""),
                        "question": m.get("question", ""),
                        "condition_id": m.get("conditionId", ""),
                        "yes_token": raw_tokens[0],
                        "no_token": raw_tokens[1],
                        "volume": vol,
                        "end_date": m.get("endDate", ""),
                        "closed_time": m.get("closedTime", ""),
                        "yes_won": yes_won,
                        "tags": tags,
                        "neg_risk": m.get("negRisk", False),
                    }
                )

        offset += batch
        if len(events) < batch:
            break

    return markets


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Dune loader for Polymarket trades")
    parser.add_argument(
        "--query-id", type=int, required=True, help="Dune saved query ID"
    )
    parser.add_argument("--token-id", help="Token ID to fetch trades for")
    parser.add_argument("--slug", help="Event slug to discover markets")
    parser.add_argument(
        "--as-of", help="Reconstruct positions as of date (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--discover", action="store_true", help="Discover closed political markets"
    )
    parser.add_argument("--tag", default="politics", help="Tag slug for discovery")
    parser.add_argument("--min-volume", type=float, default=100_000)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()

    if args.discover:
        print(
            f"Discovering closed markets (tag={args.tag}, min_vol=${args.min_volume:,.0f})..."
        )
        markets = discover_closed_political_markets(
            tag_slug=args.tag,
            min_volume=args.min_volume,
            limit=args.limit,
        )
        print(f"\nFound {len(markets)} markets:")
        for m in markets[:30]:
            neg = " [NegRisk]" if m["neg_risk"] else ""
            won = (
                "YES"
                if m["yes_won"]
                else "NO" if m["yes_won"] is not None else "?"
            )
            print(
                f'  ${m["volume"]:>12,.0f} | {won:>3} won | {m["question"][:60]}{neg}'
            )

        # Save to cache
        cache_file = CACHE_DIR / f"markets_{args.tag}.json"
        cache_file.write_text(json.dumps(markets, indent=2))
        print(f"\nSaved to {cache_file}")

    elif args.token_id:
        loader = DuneLoader(query_id=args.query_id)
        trades = loader.fetch_trades(args.token_id, no_cache=args.no_cache)

        if args.as_of:
            positions = loader.reconstruct_positions(
                trades, args.token_id, as_of=args.as_of
            )
            yes_holders, _ = loader.get_top_holders(positions)
            print(f"\nTop YES holders as of {args.as_of}:")
            for addr, bal in yes_holders[:15]:
                print(f"  {addr[:12]}... : {bal:>12,.1f} tokens")

        # Show daily prices
        prices = loader.daily_prices(trades, args.token_id)
        print(f"\nDaily prices ({len(prices)} days):")
        for date, price in list(prices.items())[:10]:
            print(f"  {date}: {price:.4f}")
        if len(prices) > 10:
            print(f"  ... ({len(prices) - 10} more days)")
            for date, price in list(prices.items())[-5:]:
                print(f"  {date}: {price:.4f}")
