"""
Configuration for Monitor Bot.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from earthquakes directory
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(env_path)


class MonitorBotConfig:
    """Monitor Bot configuration."""

    # Storage options
    USE_DATABASE = os.getenv("USE_DATABASE", "false").lower() == "true"  # PostgreSQL опционально
    JSON_CACHE_FILE = Path(__file__).parent / "data" / "events_cache.json"
    JSON_SAVE_INTERVAL = 300  # Save to JSON every 5 minutes
    JSON_RETENTION_HOURS = 24  # Keep only last 24 hours in JSON

    # Database (используется только если USE_DATABASE=true)
    DB_HOST = os.getenv("DB_HOST", "172.24.192.1")
    DB_PORT = int(os.getenv("DB_PORT", "5432"))
    DB_NAME = os.getenv("DB_NAME", "earthquake_monitor")
    DB_USER = os.getenv("DB_USER", "postgres")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "dbpass")

    @property
    def database_url(self) -> str:
        return f"postgresql://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"

    # Magnitude thresholds
    MIN_MAGNITUDE_TRACK = 4.5       # Track M4.5+ (для быстрого тестирования - 10-15 событий/день)
    MIN_MAGNITUDE_SIGNIFICANT = 7.0  # Highlight M7.0+ (красный цвет)
    MIN_MAGNITUDE_WARNING = 6.5      # Warning color M6.5+ (оранжевый)

    # Edge time highlighting (minutes)
    EDGE_TIME_HIGHLIGHT = 10         # Подсветить зелёным если > 10 минут

    # USGS confirmation timeouts (hours)
    USGS_TIMEOUT_HOURS = 24          # Mark as "USGS unlikely" after 24 hours
    USGS_WARNING_HOURS = 6           # Show warning after 6 hours without USGS

    # Collectors to run
    ACTIVE_COLLECTORS = ["jma", "emsc", "gfz", "usgs", "iris", "ingv"]

    # UI settings
    MAX_EVENTS_DISPLAY = 20          # Максимум событий в таблице
    LOG_MAX_LINES = 100              # Максимум строк в логе

    # Polling intervals (seconds)
    JMA_POLL_INTERVAL = 30
    EMSC_RECONNECT_INTERVAL = 60
    GFZ_POLL_INTERVAL = 60
    USGS_POLL_INTERVAL = 60

    # Event matching thresholds
    MATCH_TIME_WINDOW_SEC = 300      # 5 minutes
    MATCH_DISTANCE_KM = 100          # 100 km radius

    # Source URLs
    JMA_URL = "https://www.jma.go.jp/bosai/quake/data/list.json"
    EMSC_WS_URL = "wss://www.seismicportal.eu/standing_order/websocket"
    GFZ_URL = "https://geofon.gfz-potsdam.de/fdsnws/event/1/query"
    USGS_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.geojson"  # 24 часа для тестирования


config = MonitorBotConfig()
