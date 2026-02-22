"""Shared constants for earthquake trading bot."""

# Magnitude discount for non-USGS extra events by source count.
# Fewer sources = larger discount (less confidence in reported magnitude).
# Example: GFZ alone reports M6.5 → effective M5.7 (discount 0.8)
EXTRA_EVENT_MAG_DISCOUNT = {
    1: 0.8,   # 1 source → -0.8
    2: 0.5,   # 2 sources → -0.5
    3: 0.0,   # 3+ sources → no discount (high confidence)
}

# Max age (minutes) of extra event before it's ignored.
# USGS typically publishes M6+ within 10-20 minutes.
# If 30 min passed and USGS hasn't confirmed, likely a different magnitude.
EXTRA_EVENT_MAX_AGE_MINUTES = 30


def get_mag_discount(source_count: int) -> float:
    """Get magnitude discount for given number of confirming sources."""
    if source_count >= 3:
        return 0.0
    return EXTRA_EVENT_MAG_DISCOUNT.get(source_count, 0.8)
