"""
JSON updater for earthquake markets configuration.
"""

import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Set, Optional, Callable
from datetime import datetime
from .scanner import PolymarketScanner, MarketInfo
from .claude_client import ClaudeCodeClient

# Import PolymarketClient from parent directory
sys.path.insert(0, str(Path(__file__).parent.parent))
from polymarket_client import PolymarketClient


class MarketsUpdater:
    """Updates earthquake_markets.json with latest market data."""

    def __init__(self, json_path: Path, scanner: PolymarketScanner, working_dir: Optional[Path] = None):
        self.json_path = json_path
        self.scanner = scanner
        self.working_dir = working_dir or json_path.parent
        self.claude_client = ClaudeCodeClient(self.working_dir)
        self.poly_client = PolymarketClient()  # For fetching condition_id and token_ids

    def load_current_config(self) -> dict:
        """Load current earthquake_markets.json."""
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

            # Start market entry
            lines.append(f'  "{slug}": {{')

            # Add fields
            lines.append(f'    "magnitude": {data["magnitude"]},')
            lines.append(f'    "start": "{data["start"]}",')
            lines.append(f'    "end": "{data["end"]}",')
            lines.append(f'    "type": "{data["type"]}"')

            # Add outcomes on one line if present
            if "outcomes" in data:
                outcomes_str = json.dumps(data["outcomes"])
                lines[-1] += ","  # Add comma to type line
                lines.append(f'    "outcomes": {outcomes_str}')

            # Add condition_id if present
            if "condition_id" in data:
                lines[-1] += ","  # Add comma to previous line
                lines.append(f'    "condition_id": "{data["condition_id"]}"')

            # Add condition_ids if present (for count markets)
            if "condition_ids" in data:
                lines[-1] += ","  # Add comma to previous line
                condition_ids_str = json.dumps(data["condition_ids"])
                lines.append(f'    "condition_ids": {condition_ids_str}')

            # Add token_ids if present
            if "token_ids" in data:
                lines[-1] += ","  # Add comma to previous line
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

    def update_from_slugs(self, slugs: List[str]) -> tuple[int, int, int]:
        """
        Update JSON config by fetching data for given slugs.

        Args:
            slugs: List of market slugs to check

        Returns:
            Tuple of (added, updated, removed) counts
        """
        current_config = self.load_current_config()
        new_config = {}

        added = 0
        updated = 0

        # Fetch and update each slug
        for slug in slugs:
            event = self.scanner.get_event_by_slug(slug)
            if not event:
                # Market not found - keep old config if it exists
                if slug in current_config:
                    new_config[slug] = current_config[slug]
                continue

            markets = self.scanner.extract_market_metadata(event)

            # Use first market from event (typically there's only one)
            for market_slug, market_info in markets.items():
                # Skip closed markets
                if market_info.closed or not market_info.active:
                    continue

                # Prepare config entry
                entry = {
                    "magnitude": market_info.magnitude,
                    "start": market_info.end_date_iso or event.get("createdAt", ""),
                    "end": market_info.end_date_iso or "",
                    "type": market_info.market_type,
                }

                if market_info.outcomes:
                    entry["outcomes"] = market_info.outcomes

                # Check if this is new or updated
                if market_slug not in current_config:
                    added += 1
                elif current_config[market_slug] != entry:
                    updated += 1

                new_config[market_slug] = entry

        # Count removed markets
        removed = len(current_config) - len(new_config)

        # Save updated config
        self.save_config(new_config)

        return (added, updated, removed)

    def discover_and_update(self) -> tuple[int, int, int]:
        """
        Discover earthquake markets and update JSON.

        Returns:
            Tuple of (added, updated, removed) counts
        """
        current_config = self.load_current_config()
        current_slugs = set(current_config.keys())

        # Search for earthquake markets
        events = self.scanner.search_markets_by_keyword("earthquake")

        new_slugs = set()
        for event in events:
            markets = event.get("markets", [])
            for market in markets:
                slug = market.get("slug", "")
                if slug:
                    new_slugs.add(slug)

        # Combine current and discovered slugs
        all_slugs = list(current_slugs | new_slugs)

        return self.update_from_slugs(all_slugs)

    def format_update_summary(self, added: int, updated: int, removed: int) -> str:
        """Format update summary as text."""
        lines = []
        if added > 0:
            lines.append(f"✓ Added {added} new market(s)")
        if updated > 0:
            lines.append(f"✓ Updated {updated} market(s)")
        if removed > 0:
            lines.append(f"✓ Removed {removed} closed market(s)")
        if added == 0 and updated == 0 and removed == 0:
            lines.append("No changes detected")

        return "\n".join(lines)

    def update_via_claude(
        self,
        output_callback: Optional[Callable[[str], None]] = None
    ) -> tuple[bool, str, dict]:
        """
        Update markets JSON using direct Polymarket API scanner.
        (Name kept for compatibility, no longer uses Claude)

        Args:
            output_callback: Optional callback to receive progress updates

        Returns:
            Tuple of (success, message, stats_dict)
            stats_dict contains: {"added": int, "updated": int, "removed": int, "total": int}
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

            # Fetch ALL earthquake market data with condition_ids and token_ids
            if output_callback:
                output_callback("Fetching condition_ids and token_ids from Polymarket...")

            try:
                all_prices = self.poly_client.get_all_earthquake_prices()
            except Exception as e:
                if output_callback:
                    output_callback(f"Warning: Failed to fetch market data: {e}")
                all_prices = {}

            if output_callback:
                output_callback("")

            # Check existing markets from config
            if output_callback:
                output_callback("Checking existing markets...")

            new_config = {}
            total_events_checked = 0

            # Check each slug from current config (these are event slugs)
            for event_slug in before_slugs:
                total_events_checked += 1
                if output_callback and total_events_checked % 2 == 0:
                    output_callback(f"Checked {total_events_checked}/{len(before_slugs)}...")

                # Fetch current state of the event
                event = self.scanner.get_event_by_slug(event_slug)
                if not event:
                    if output_callback:
                        output_callback(f"  Not found: {event_slug[:50]}")
                    continue

                # Check if event is closed
                event_closed = event.get("closed", False)
                if event_closed:
                    if output_callback:
                        output_callback(f"  Closed: {event_slug[:50]}")
                    continue

                # Get event metadata
                title = event.get("title", "")
                markets = event.get("markets", [])
                start_date = self.scanner.get_market_start_date(event)

                # Determine event type and extract data
                # Count events have multiple binary markets
                is_count_event = len(markets) > 2

                # Extract magnitude from title
                magnitude = self.scanner._infer_magnitude(title)

                # Get end date from first market
                end_date = None
                if markets:
                    end_date = markets[0].get("endDateIso", "2026-12-31T23:59:59Z")

                # Build config entry for this event
                config_entry = {
                    "magnitude": magnitude,
                    "start": start_date or datetime.now().isoformat(),
                    "end": end_date or "2026-12-31T23:59:59Z",
                }

                if is_count_event:
                    # Count event - extract outcomes from market questions
                    config_entry["type"] = "count"
                    outcomes = []
                    for market in markets:
                        # Include all markets (even closed) to preserve outcome structure
                        question = market.get("question", "")
                        question_lower = question.lower()

                        # Parse different outcome formats:
                        # "exactly 2" -> ["2", 2, 2]
                        # "between 5 and 7" -> ["5-7", 5, 7]
                        # "fewer than 5" or "<5" -> ["<5", 0, 4]
                        # "8 or more" or "8+" -> ["8+", 8, None]

                        if "exactly" in question_lower:
                            match = re.search(r'exactly (\d+)', question_lower)
                            if match:
                                num = int(match.group(1))
                                outcomes.append([str(num), num, num])
                        elif "between" in question_lower:
                            match = re.search(r'between (\d+) and (\d+)', question_lower)
                            if match:
                                min_val = int(match.group(1))
                                max_val = int(match.group(2))
                                outcomes.append([f"{min_val}-{max_val}", min_val, max_val])
                        elif "fewer than" in question_lower or "less than" in question_lower:
                            match = re.search(r'(?:fewer|less) than (\d+)', question_lower)
                            if match:
                                max_val = int(match.group(1))
                                outcomes.append([f"<{max_val}", 0, max_val - 1])
                        elif " or more" in question_lower:
                            match = re.search(r'(\d+) or more', question_lower)
                            if match:
                                num = int(match.group(1))
                                outcomes.append([f"{num}+", num, None])

                    if outcomes:
                        # Sort outcomes by min value
                        outcomes.sort(key=lambda x: x[1] if x[1] is not None else 999)
                        config_entry["outcomes"] = outcomes
                else:
                    # Binary event
                    config_entry["type"] = "binary"

                # Add condition_id and token_ids from polymarket data
                if event_slug in all_prices:
                    poly_markets = all_prices[event_slug]

                    if is_count_event:
                        # Count market - multiple condition_ids
                        config_entry["condition_ids"] = {}
                        config_entry["token_ids"] = {}

                        for market in poly_markets:
                            # Match market question to outcome
                            question_lower = market.question.lower()
                            for outcome_data in outcomes:
                                outcome_name = outcome_data[0]  # e.g., "<5", "5-7", etc.

                                # Match patterns
                                matched = False
                                if "-" in outcome_name and outcome_name[0].isdigit():
                                    # Range like "5-7"
                                    parts = outcome_name.split("-")
                                    if len(parts) == 2:
                                        matched = bool(re.search(rf'between\s+{parts[0]}\s+and\s+{parts[1]}', question_lower))
                                elif outcome_name.startswith("<"):
                                    # Less than like "<5"
                                    num = outcome_name[1:]
                                    matched = bool(re.search(rf'fewer\s+than\s+{num}\b', question_lower))
                                elif outcome_name.endswith("+"):
                                    # Or more like "8+"
                                    num = outcome_name[:-1]
                                    matched = bool(re.search(rf'{num}\s+or\s+more', question_lower))
                                else:
                                    # Exact like "2"
                                    matched = bool(re.search(rf'exactly\s+{outcome_name}\b', question_lower))

                                if matched:
                                    config_entry["condition_ids"][outcome_name] = market.condition_id
                                    config_entry["token_ids"][outcome_name] = {}
                                    for outcome in market.outcomes:
                                        config_entry["token_ids"][outcome_name][outcome.outcome_name] = outcome.token_id
                                    break
                    else:
                        # Binary market - single condition_id
                        if poly_markets:
                            market = poly_markets[0]  # First market
                            config_entry["condition_id"] = market.condition_id
                            config_entry["token_ids"] = {}
                            for outcome in market.outcomes:
                                config_entry["token_ids"][outcome.outcome_name] = outcome.token_id

                # Use event slug as key
                new_config[event_slug] = config_entry

                # Check if changed
                if event_slug in before_config:
                    if before_config[event_slug] != config_entry:
                        if output_callback:
                            output_callback(f"  Updated: {event_slug[:50]}")
                else:
                    if output_callback:
                        output_callback(f"  Added: {event_slug[:50]}")

            if output_callback:
                output_callback("")
                output_callback(f"Processed events: {total_events_checked}")
                output_callback(f"Active events: {len(new_config)}")

            # Calculate statistics
            after_slugs = set(new_config.keys())
            added_slugs = after_slugs - before_slugs
            removed_slugs = before_slugs - after_slugs
            updated_slugs = before_slugs & after_slugs

            # Count actual updates (changed data)
            updated_count = 0
            for slug in updated_slugs:
                if before_config.get(slug) != new_config.get(slug):
                    updated_count += 1

            stats = {
                "added": len(added_slugs),
                "updated": updated_count,
                "removed": len(removed_slugs),
                "total": len(new_config),
            }

            # Save new configuration
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

    def is_claude_available(self) -> bool:
        """Check if updater is available (always true - uses direct API)."""
        return True
