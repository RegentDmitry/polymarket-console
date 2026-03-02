"""
Scanner for Polymarket crypto (BTC/ETH) markets.

Searches Gamma API for touch-barrier markets like:
- "Will BTC hit $100,000 by end of 2026?"
- "Will ETH drop below $1,600 by March 2026?"
"""

import re
import json
import httpx
from typing import Optional, Dict, List
from dataclasses import dataclass


GAMMA_API_URL = "https://gamma-api.polymarket.com"

# Keywords to search for crypto markets
SEARCH_KEYWORDS = ["bitcoin", "ethereum", "BTC", "ETH"]

# Slug patterns that match keywords but are NOT crypto price markets
SLUG_BLACKLIST_PATTERNS = [
    "congress", "president", "trump", "election", "nfl", "nba",
    "super-bowl", "olympics", "world-cup", "elon", "regulation",
    "sec-", "etf-", "dominance", "market-cap-flip",
]

# Question patterns that indicate a touch-barrier market (price hitting a level)
TOUCH_PATTERNS = [
    r'will\s+(?:bitcoin|btc|ethereum|eth)\s+(?:hit|reach|touch|exceed|surpass|cross)',
    r'will\s+(?:bitcoin|btc|ethereum|eth)\s+(?:drop|fall|crash|decline)\s+(?:below|under|to)',
    r'will\s+(?:bitcoin|btc|ethereum|eth)\s+(?:price|be)\s+(?:above|below|over|under)',
    r'(?:bitcoin|btc|ethereum|eth)\s+(?:to|above|below|hit|reach)\s+\$',
    r'\$[\d,]+\s+(?:bitcoin|btc|ethereum|eth)',
]


@dataclass
class CryptoMarketInfo:
    """Information about a crypto touch-barrier market."""
    slug: str
    question: str
    description: str
    active: bool
    closed: bool
    end_date_iso: Optional[str]
    currency: str           # "BTC" or "ETH"
    strike: Optional[float]
    direction: str          # "above" or "below"
    condition_id: str
    token_ids: Dict[str, str]  # {"Yes": "...", "No": "..."}


class CryptoScanner:
    """Scanner for crypto touch-barrier markets on Polymarket."""

    def __init__(self, timeout: int = 30):
        self.timeout = timeout

    def get_event_by_slug(self, slug: str) -> Optional[dict]:
        """Get event data by slug from Gamma API."""
        try:
            response = httpx.get(
                f"{GAMMA_API_URL}/events",
                params={"slug": slug},
                timeout=self.timeout,
            )
            response.raise_for_status()
            events = response.json()
            return events[0] if events else None
        except Exception as e:
            print(f"Error fetching {slug}: {e}")
            return None

    def search_crypto_markets(self) -> List[dict]:
        """Search for crypto touch-barrier markets with pagination.

        Returns:
            List of matching events (deduplicated, filtered to real crypto price markets)
        """
        try:
            seen_ids = set()
            crypto_events = []
            offset = 0
            batch_size = 500

            while True:
                response = httpx.get(
                    f"{GAMMA_API_URL}/events",
                    params={"closed": "false", "limit": batch_size, "offset": offset},
                    timeout=self.timeout,
                )
                response.raise_for_status()
                events = response.json()
                if not events:
                    break

                for event in events:
                    title = event.get("title", "").lower()
                    slug = event.get("slug", "").lower()

                    # Skip blacklisted patterns
                    if any(bp in slug for bp in SLUG_BLACKLIST_PATTERNS):
                        continue

                    # Check if event relates to BTC/ETH price
                    combined = f"{title} {slug}"
                    has_crypto_keyword = any(
                        kw.lower() in combined
                        for kw in SEARCH_KEYWORDS
                    )

                    if not has_crypto_keyword:
                        continue

                    # Check if it's a touch-barrier market (price hitting a level)
                    # Look at title and individual market questions
                    is_touch = False
                    for pattern in TOUCH_PATTERNS:
                        if re.search(pattern, title, re.IGNORECASE):
                            is_touch = True
                            break

                    if not is_touch:
                        # Also check individual market questions
                        for market in event.get("markets", []):
                            q = market.get("question", "")
                            for pattern in TOUCH_PATTERNS:
                                if re.search(pattern, q, re.IGNORECASE):
                                    is_touch = True
                                    break
                            if is_touch:
                                break

                    if not is_touch:
                        continue

                    event_id = event.get("id", event.get("slug"))
                    if event_id not in seen_ids:
                        seen_ids.add(event_id)
                        crypto_events.append(event)

                offset += len(events)
                if len(events) < batch_size:
                    break

            return crypto_events

        except Exception as e:
            print(f"Error searching crypto markets: {e}")
            return []

    def extract_market_info(self, event: dict) -> List[CryptoMarketInfo]:
        """Extract CryptoMarketInfo from event.

        Handles both single-market events and multi-binary events
        (where one event has multiple strike/expiry markets).
        """
        results = []
        markets = event.get("markets", [])

        for market in markets:
            if market.get("closed", False) or not market.get("active", True):
                continue

            slug = market.get("slug", "")
            question = market.get("question", "")
            description = market.get("description", "")
            end_date = market.get("endDateIso")
            condition_id = market.get("conditionId", "")

            # Detect currency
            currency = self._detect_currency(question)
            if not currency:
                currency = self._detect_currency(event.get("title", ""))
            if not currency:
                continue

            # Parse strike price
            strike = self._parse_strike(question)
            if strike is None:
                strike = self._parse_strike(event.get("title", ""))
            if strike is None:
                continue

            # Parse direction
            direction = self._parse_direction(question)

            # Parse token IDs
            token_ids = {}
            try:
                t_list = json.loads(market.get("clobTokenIds", "[]"))
                o_list = json.loads(market.get("outcomes", "[]"))
                for i, outcome in enumerate(o_list):
                    if i < len(t_list):
                        token_ids[outcome] = t_list[i]
            except Exception:
                pass

            if not token_ids:
                continue

            results.append(CryptoMarketInfo(
                slug=slug,
                question=question,
                description=description,
                active=True,
                closed=False,
                end_date_iso=end_date,
                currency=currency,
                strike=strike,
                direction=direction,
                condition_id=condition_id,
                token_ids=token_ids,
            ))

        return results

    def _detect_currency(self, text: str) -> Optional[str]:
        """Detect BTC or ETH from text."""
        text_lower = text.lower()
        # Check BTC first (more common)
        if any(kw in text_lower for kw in ["bitcoin", "btc"]):
            return "BTC"
        if any(kw in text_lower for kw in ["ethereum", "eth"]):
            return "ETH"
        return None

    def _parse_strike(self, text: str) -> Optional[float]:
        """Parse strike price from question text.

        Handles formats:
        - "$100,000" / "$100000"
        - "$100k" / "$100K" / "$150k"
        - "100,000" / "100000" (without $)
        """
        # Pattern: $100k or $100K (check FIRST — before plain $100)
        match = re.search(r'\$\s*([\d.]+)\s*[kK]', text)
        if match:
            try:
                return float(match.group(1)) * 1000
            except ValueError:
                pass

        # Pattern: $100,000 or $100000
        match = re.search(r'\$\s*([\d,]+)', text)
        if match:
            price_str = match.group(1).replace(",", "")
            try:
                return float(price_str)
            except ValueError:
                pass

        return None

    def _parse_direction(self, text: str) -> str:
        """Parse direction (above/below) from question text."""
        text_lower = text.lower()

        # "below" indicators
        below_patterns = [
            r'dip\s+to',
            r'drop\s+(?:below|under|to)',
            r'fall\s+(?:below|under|to)',
            r'crash\s+(?:below|under|to)',
            r'decline\s+(?:below|under|to)',
            r'below\s+\$',
            r'under\s+\$',
        ]
        for pattern in below_patterns:
            if re.search(pattern, text_lower):
                return "below"

        # Default: above (hit, reach, exceed, cross, touch)
        return "above"
