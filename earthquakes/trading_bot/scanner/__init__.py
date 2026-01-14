"""Market scanners for different strategies."""

from .base import BaseScanner
from .earthquake import EarthquakeScanner

__all__ = ["BaseScanner", "EarthquakeScanner"]
