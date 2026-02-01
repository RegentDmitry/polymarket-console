"""
Scanner for Polymarket earthquake markets.
"""

import httpx
from typing import Optional, Dict, List
from dataclasses import dataclass
from datetime import datetime


GAMMA_API_URL = "https://gamma-api.polymarket.com"


@dataclass
class MarketInfo:
    """Information about a single market."""
    slug: str
    question: str
    active: bool
    closed: bool
    end_date_iso: Optional[str]
    magnitude: float  # Inferred from question
    market_type: str  # "binary" or "count"
    outcomes: Optional[List] = None  # For count markets


class PolymarketScanner:
    """Scanner for earthquake markets on Polymarket."""

    def __init__(self, timeout: int = 30):
        self.timeout = timeout

    def get_event_by_slug(self, slug: str) -> Optional[dict]:
        """
        Get event data by slug from Gamma API.

        Args:
            slug: Event slug

        Returns:
            Event data or None if not found
        """
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

    def search_markets_by_keywords(self, keywords: Optional[List[str]] = None) -> List[dict]:
        """
        Search for earthquake/megaquake markets with pagination.

        Args:
            keywords: List of search keywords (default: ["earthquake", "megaquake"])

        Returns:
            List of matching events (deduplicated, filtered to real earthquake markets)
        """
        if keywords is None:
            keywords = ["earthquake", "megaquake"]

        # Slugs that match keywords but are NOT earthquake markets (sports teams etc.)
        SLUG_BLACKLIST_PATTERNS = ["quakers", "sje-", "mls-", "cwbb-", "cbb-"]

        try:
            seen_ids = set()
            earthquake_events = []
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

                    # Skip blacklisted slugs (sports teams etc.)
                    if any(bp in slug for bp in SLUG_BLACKLIST_PATTERNS):
                        continue

                    # Check if slug or title contains any keyword
                    for keyword in keywords:
                        kw = keyword.lower()
                        if kw in slug or kw in title:
                            event_id = event.get("id", event.get("slug"))
                            if event_id not in seen_ids:
                                seen_ids.add(event_id)
                                earthquake_events.append(event)
                            break

                offset += len(events)
                if len(events) < batch_size:
                    break

            return earthquake_events

        except Exception as e:
            print(f"Error searching markets: {e}")
            return []

    def search_markets_by_keyword(self, keyword: str = "earthquake") -> List[dict]:
        """Search for markets by keyword (legacy, calls search_markets_by_keywords)."""
        return self.search_markets_by_keywords([keyword])

    def extract_market_metadata(self, event: dict) -> Dict[str, MarketInfo]:
        """
        Extract metadata from event for JSON config.

        Args:
            event: Event data from Gamma API

        Returns:
            Dict mapping market slug to MarketInfo
        """
        result = {}
        markets = event.get("markets", [])

        for market in markets:
            slug = market.get("slug", "")
            question = market.get("question", "")
            active = market.get("active", True)
            closed = market.get("closed", False)
            end_date_iso = market.get("endDateIso")

            # Try to infer magnitude and type from question
            magnitude = self._infer_magnitude(question)
            market_type = self._infer_market_type(market)
            outcomes = self._extract_outcomes(market) if market_type == "count" else None

            result[slug] = MarketInfo(
                slug=slug,
                question=question,
                active=active,
                closed=closed,
                end_date_iso=end_date_iso,
                magnitude=magnitude,
                market_type=market_type,
                outcomes=outcomes,
            )

        return result

    def _infer_magnitude(self, question: str) -> float:
        """Infer earthquake magnitude from question text."""
        question_lower = question.lower()

        # Look for patterns like "7.0+", "7pt0", "8.0 or above"
        if "10pt0" in question_lower or "10.0" in question_lower:
            return 10.0
        elif "9pt0" in question_lower or "9.0" in question_lower:
            return 9.0
        elif "8pt0" in question_lower or "8.0" in question_lower or "megaquake" in question_lower:
            return 8.0
        elif "7pt0" in question_lower or "7.0" in question_lower:
            return 7.0
        elif "6pt5" in question_lower or "6.5" in question_lower:
            return 6.5
        elif "6pt0" in question_lower or "6.0" in question_lower:
            return 6.0
        elif "5pt0" in question_lower or "5.0" in question_lower:
            return 5.0

        return 7.0  # Default

    def _infer_market_type(self, market: dict) -> str:
        """Infer if market is binary or count-based."""
        outcomes_raw = market.get("outcomes", "[]")
        import json
        outcomes = json.loads(outcomes_raw)

        # Binary markets have Yes/No
        if len(outcomes) == 2 and set(outcomes) == {"Yes", "No"}:
            return "binary"
        else:
            return "count"

    def _extract_outcomes(self, market: dict) -> Optional[List]:
        """Extract outcome ranges for count markets."""
        import json
        outcomes_raw = json.loads(market.get("outcomes", "[]"))

        result = []
        for outcome in outcomes_raw:
            # Try to parse ranges like "2", "3-5", "8+"
            if "-" in outcome:
                # Range like "3-5"
                parts = outcome.split("-")
                try:
                    min_val = int(parts[0].strip())
                    max_val = int(parts[1].strip())
                    result.append([outcome, min_val, max_val])
                except ValueError:
                    result.append([outcome, None, None])
            elif "+" in outcome:
                # Open-ended like "8+"
                try:
                    min_val = int(outcome.replace("+", "").strip())
                    result.append([outcome, min_val, None])
                except ValueError:
                    result.append([outcome, None, None])
            elif "<" in outcome:
                # Less than, like "<5"
                try:
                    max_val = int(outcome.replace("<", "").strip())
                    result.append([outcome, 0, max_val - 1])
                except ValueError:
                    result.append([outcome, None, None])
            else:
                # Single value
                try:
                    val = int(outcome.strip())
                    result.append([outcome, val, val])
                except ValueError:
                    result.append([outcome, None, None])

        return result if result else None

    def get_market_start_date(self, event: dict) -> Optional[str]:
        """Get market start date from event."""
        created_at = event.get("createdAt")
        if created_at:
            return created_at
        return None
