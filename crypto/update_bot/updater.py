"""
JSON updater for crypto markets configuration.

Discovers BTC/ETH touch-barrier markets on Polymarket and saves
them to crypto_markets.json for use by the trading bot.
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Callable
from datetime import datetime

from .scanner import CryptoScanner, CryptoMarketInfo


class CryptoMarketsUpdater:
    """Updates crypto_markets.json with latest BTC/ETH market data."""

    def __init__(self, json_path: Path, scanner: CryptoScanner):
        self.json_path = json_path
        self.scanner = scanner

    def load_current_config(self) -> dict:
        """Load current crypto_markets.json."""
        if not self.json_path.exists():
            return {}

        try:
            with open(self.json_path, "r") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def save_config(self, config: dict):
        """Save updated config to JSON file with compact formatting."""
        lines = ["{"]

        items = list(config.items())
        for i, (slug, data) in enumerate(items):
            is_last = (i == len(items) - 1)

            lines.append(f'  "{slug}": {{')

            # Required fields
            lines.append(f'    "currency": "{data["currency"]}",')
            lines.append(f'    "strike": {data["strike"]},')
            lines.append(f'    "direction": "{data["direction"]}",')
            if "question" in data:
                q_escaped = data["question"].replace('"', '\\"')
                lines.append(f'    "question": "{q_escaped}",')
            lines.append(f'    "start": "{data["start"]}",')
            lines.append(f'    "end": "{data["end"]}",')
            lines.append(f'    "type": "binary"')

            # Optional: condition_id
            if "condition_id" in data:
                lines[-1] += ","
                lines.append(f'    "condition_id": "{data["condition_id"]}"')

            # Optional: token_ids
            if "token_ids" in data:
                lines[-1] += ","
                token_ids_str = json.dumps(data["token_ids"])
                lines.append(f'    "token_ids": {token_ids_str}')

            # Close market entry
            if is_last:
                lines.append("  }")
            else:
                lines.append("  },")

        lines.append("}")

        with open(self.json_path, "w") as f:
            f.write("\n".join(lines))
            f.write("\n")

    def update(
        self,
        output_callback: Optional[Callable[[str], None]] = None,
    ) -> tuple[bool, str, dict]:
        """Discover crypto markets and update JSON.

        Args:
            output_callback: Optional callback for progress updates

        Returns:
            Tuple of (success, message, stats_dict)
        """
        try:
            if output_callback:
                output_callback("Connecting to Polymarket Gamma API...")

            # Load current config
            before_config = self.load_current_config()
            before_slugs = set(before_config.keys())

            if output_callback:
                output_callback(f"Current config: {len(before_config)} markets")
                output_callback("")

            # Discover crypto markets
            if output_callback:
                output_callback("Searching for BTC/ETH touch-barrier markets...")

            events = self.scanner.search_crypto_markets()

            if output_callback:
                output_callback(f"Found {len(events)} crypto events")
                output_callback("")

            # Process each event
            new_config = {}
            total_markets = 0

            for event in events:
                market_infos = self.scanner.extract_market_info(event)

                for info in market_infos:
                    total_markets += 1

                    # Parse end date for more accurate date
                    end_date = info.end_date_iso or "2026-12-31T23:59:59Z"

                    # Try to extract more precise end date from question
                    precise_end = self._parse_end_date_from_question(info.question)
                    if precise_end:
                        end_date = precise_end

                    entry = {
                        "currency": info.currency,
                        "strike": info.strike,
                        "direction": info.direction,
                        "question": info.question,
                        "start": event.get("createdAt", datetime.now().isoformat()),
                        "end": end_date,
                        "type": "binary",
                    }

                    if info.condition_id:
                        entry["condition_id"] = info.condition_id

                    if info.token_ids:
                        entry["token_ids"] = info.token_ids

                    # Log changes
                    if info.slug in before_config:
                        if before_config[info.slug] != entry:
                            if output_callback:
                                output_callback(
                                    f"  Updated: {info.slug[:50]} "
                                    f"({info.currency} {'↑' if info.direction == 'above' else '↓'} "
                                    f"${info.strike:,.0f})"
                                )
                    else:
                        if output_callback:
                            output_callback(
                                f"  Added: {info.slug[:50]} "
                                f"({info.currency} {'↑' if info.direction == 'above' else '↓'} "
                                f"${info.strike:,.0f})"
                            )

                    new_config[info.slug] = entry

            # Also keep manually-added markets that weren't discovered
            # (in case Gamma API doesn't find them)
            for slug, data in before_config.items():
                if slug not in new_config:
                    # Check if the market's end date has passed
                    end_str = data.get("end", "")
                    try:
                        end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                        if end_dt < datetime.now(end_dt.tzinfo):
                            if output_callback:
                                output_callback(f"  Expired: {slug[:50]}")
                            continue
                    except (ValueError, TypeError):
                        pass

                    # Keep the market (might have been added manually)
                    new_config[slug] = data
                    if output_callback:
                        output_callback(f"  Kept (not in API): {slug[:50]}")

            if output_callback:
                output_callback("")
                output_callback(f"Total markets found: {total_markets}")
                output_callback(f"Active markets in config: {len(new_config)}")

            # Calculate stats
            after_slugs = set(new_config.keys())
            added_slugs = after_slugs - before_slugs
            removed_slugs = before_slugs - after_slugs
            updated_count = 0
            for slug in before_slugs & after_slugs:
                if before_config.get(slug) != new_config.get(slug):
                    updated_count += 1

            stats = {
                "added": len(added_slugs),
                "updated": updated_count,
                "removed": len(removed_slugs),
                "total": len(new_config),
            }

            # Save
            if output_callback:
                output_callback("")
                output_callback("Saving configuration...")

            self.save_config(new_config)

            if output_callback:
                output_callback("Done!")

            return (True, "Successfully updated from Polymarket API", stats)

        except Exception as e:
            import traceback
            error_msg = f"Error during update: {e}"
            if output_callback:
                output_callback(f"Error: {e}")
                for line in traceback.format_exc().split("\n"):
                    if line.strip():
                        output_callback(f"  {line}")
            return (False, error_msg, {})

    def _parse_end_date_from_question(self, question: str) -> Optional[str]:
        """Try to extract end date from question text.

        Handles patterns like:
        - "by end of 2026"
        - "by March 31, 2026"
        - "by December 31, 2026"
        - "in 2026"
        """
        month_names = {
            "january": "01", "february": "02", "march": "03",
            "april": "04", "may": "05", "june": "06",
            "july": "07", "august": "08", "september": "09",
            "october": "10", "november": "11", "december": "12",
        }

        # Pattern: "by March 31, 2026" or "by December 31 2026"
        match = re.search(
            r'by\s+(january|february|march|april|may|june|july|august|'
            r'september|october|november|december)\s+(\d{1,2}),?\s*(\d{4})',
            question, re.IGNORECASE
        )
        if match:
            month = month_names[match.group(1).lower()]
            day = int(match.group(2))
            year = match.group(3)
            return f"{year}-{month}-{day:02d}T23:59:59Z"

        # Pattern: "by end of 2026" or "in 2026"
        match = re.search(r'(?:by\s+end\s+of|in)\s+(\d{4})', question, re.IGNORECASE)
        if match:
            year = match.group(1)
            return f"{year}-12-31T23:59:59Z"

        return None

    def is_available(self) -> bool:
        """Check if updater is available (always true - uses direct API)."""
        return True
