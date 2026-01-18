"""
Earthquake data collectors from various sources.
"""

from .base import BaseCollector
from .jma import JMACollector
from .emsc import EMSCCollector
from .gfz import GFZCollector
from .usgs import USGSCollector
from .iris import IRISCollector
from .ingv import INGVCollector

__all__ = [
    "BaseCollector",
    "JMACollector",
    "EMSCCollector",
    "GFZCollector",
    "USGSCollector",
    "IRISCollector",
    "INGVCollector",
]
