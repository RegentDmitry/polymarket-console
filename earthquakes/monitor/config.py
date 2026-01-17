"""
Configuration for earthquake monitoring system.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from earthquakes directory
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(env_path)


class Config:
    # Database
    DB_HOST = os.getenv("DB_HOST", "172.24.192.1")
    DB_PORT = int(os.getenv("DB_PORT", "5432"))
    DB_NAME = os.getenv("DB_NAME", "earthquake_monitor")
    DB_USER = os.getenv("DB_USER", "postgres")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "dbpass")

    @property
    def database_url(self) -> str:
        return f"postgresql://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"

    # Polling intervals (seconds)
    JMA_POLL_INTERVAL = 30
    EMSC_RECONNECT_INTERVAL = 60
    GFZ_POLL_INTERVAL = 60
    GEONET_POLL_INTERVAL = 60
    USGS_POLL_INTERVAL = 60

    # Magnitude thresholds
    MIN_MAGNITUDE_TRACK = 4.5      # Track M4.5+ events (для тестирования)
    MIN_MAGNITUDE_SIGNIFICANT = 7.0  # Mark as significant

    # Event matching thresholds
    MATCH_TIME_WINDOW_SEC = 300    # 5 minutes
    MATCH_DISTANCE_KM = 100        # 100 km radius

    # Source URLs
    JMA_URL = "https://www.jma.go.jp/bosai/quake/data/list.json"
    EMSC_WS_URL = "wss://www.seismicportal.eu/standing_order/websocket"
    GFZ_URL = "https://geofon.gfz-potsdam.de/fdsnws/event/1/query"
    GEONET_URL = "https://api.geonet.org.nz/quake"
    USGS_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.geojson"


config = Config()
