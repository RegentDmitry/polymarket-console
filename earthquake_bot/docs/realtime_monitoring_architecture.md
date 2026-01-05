# Real-Time Earthquake Monitoring System Architecture

## Overview

Multi-source earthquake monitoring system that aggregates data from regional seismological agencies faster than USGS, providing information edge for Polymarket trading.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           DATA SOURCES (Parallel)                           │
├─────────────┬─────────────┬─────────────┬─────────────┬─────────────────────┤
│    JMA      │    EMSC     │    GFZ      │   GeoNet    │       USGS          │
│  (Japan)    │  (Europe)   │ (Germany)   │    (NZ)     │    (Reference)      │
│  ~10-15s    │   ~2 min    │  ~6-9 min   │   ~5 min    │    ~13-20 min       │
└──────┬──────┴──────┬──────┴──────┬──────┴──────┬──────┴──────────┬──────────┘
       │             │             │             │                 │
       └─────────────┴─────────────┴─────────────┴─────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          COLLECTOR SERVICE                                   │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  Async Workers (asyncio)                                            │    │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐  │    │
│  │  │JMA Poller│ │EMSC WS   │ │GFZ Poller│ │GeoNet    │ │USGS      │  │    │
│  │  │ 30 sec   │ │WebSocket │ │ 60 sec   │ │Poller    │ │Poller    │  │    │
│  │  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘  │    │
│  │       │            │            │            │            │        │    │
│  │       └────────────┴────────────┴────────────┴────────────┘        │    │
│  │                              │                                      │    │
│  │                              ▼                                      │    │
│  │  ┌─────────────────────────────────────────────────────────────┐   │    │
│  │  │              EVENT DEDUPLICATION & MATCHING                 │   │    │
│  │  │  - Match by location (lat/lon within 100km)                 │   │    │
│  │  │  - Match by time (within 5 minutes)                         │   │    │
│  │  │  - Merge multiple source reports into single event          │   │    │
│  │  └─────────────────────────────────────────────────────────────┘   │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         PostgreSQL DATABASE                                  │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────────┐  │
│  │ earthquake_     │  │ source_         │  │ market_                     │  │
│  │ events          │  │ reports         │  │ reactions                   │  │
│  │                 │  │                 │  │                             │  │
│  │ - event_id (PK) │  │ - report_id     │  │ - reaction_id               │  │
│  │ - best_magnitude│  │ - event_id (FK) │  │ - event_id (FK)             │  │
│  │ - latitude      │  │ - source        │  │ - market_slug               │  │
│  │ - longitude     │  │ - magnitude     │  │ - price_before              │  │
│  │ - depth_km      │  │ - reported_at   │  │ - price_after               │  │
│  │ - first_detected│  │ - received_at   │  │ - detected_at               │  │
│  │ - usgs_id       │  │ - raw_data      │  │ - usgs_published_at         │  │
│  │ - location_name │  │                 │  │ - edge_minutes              │  │
│  └─────────────────┘  └─────────────────┘  └─────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
                                   │
                    ┌──────────────┴──────────────┐
                    │                             │
                    ▼                             ▼
┌─────────────────────────────────┐  ┌────────────────────────────────────────┐
│       ALERT SERVICE             │  │        ANALYSIS SERVICE                │
│                                 │  │                                        │
│  - Telegram/Discord webhook     │  │  - Extended USGS history               │
│  - Desktop notification         │  │  - Market reaction analysis            │
│  - Sound alert for M7+          │  │  - Edge window calculation             │
│                                 │  │  - Magnitude comparison stats          │
└─────────────────────────────────┘  └────────────────────────────────────────┘
```

---

## Database Schema

### Core Tables

#### 1. `earthquake_events` - Deduplicated earthquake events

```sql
CREATE TABLE earthquake_events (
    event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Best available data (updated as more sources report)
    best_magnitude DECIMAL(3,1) NOT NULL,
    best_magnitude_type VARCHAR(10),  -- Mw, ML, mb, Ms
    latitude DECIMAL(8,5) NOT NULL,
    longitude DECIMAL(8,5) NOT NULL,
    depth_km DECIMAL(6,2),
    location_name TEXT,

    -- Timestamps
    event_time TIMESTAMPTZ NOT NULL,           -- When earthquake occurred
    first_detected_at TIMESTAMPTZ NOT NULL,    -- When we first saw it
    usgs_published_at TIMESTAMPTZ,             -- When USGS published (NULL if not yet)

    -- Reference IDs from sources
    usgs_id VARCHAR(50) UNIQUE,
    jma_id VARCHAR(50),
    emsc_id VARCHAR(50),
    gfz_id VARCHAR(50),

    -- Metadata
    source_count INTEGER DEFAULT 1,
    is_significant BOOLEAN DEFAULT FALSE,      -- M >= 7.0
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_events_time ON earthquake_events(event_time DESC);
CREATE INDEX idx_events_magnitude ON earthquake_events(best_magnitude DESC);
CREATE INDEX idx_events_significant ON earthquake_events(is_significant) WHERE is_significant = TRUE;
CREATE INDEX idx_events_usgs ON earthquake_events(usgs_id) WHERE usgs_id IS NOT NULL;
```

#### 2. `source_reports` - Raw reports from each source

```sql
CREATE TABLE source_reports (
    report_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id UUID REFERENCES earthquake_events(event_id),

    -- Source identification
    source VARCHAR(20) NOT NULL,  -- 'jma', 'emsc', 'gfz', 'geonet', 'usgs', 'cwa', 'bmkg'
    source_event_id VARCHAR(100),

    -- Reported values
    magnitude DECIMAL(3,1) NOT NULL,
    magnitude_type VARCHAR(10),
    latitude DECIMAL(8,5),
    longitude DECIMAL(8,5),
    depth_km DECIMAL(6,2),
    location_name TEXT,

    -- Timestamps
    event_time TIMESTAMPTZ NOT NULL,    -- Source's reported event time
    reported_at TIMESTAMPTZ,            -- When source published (if available)
    received_at TIMESTAMPTZ NOT NULL,   -- When our system received it

    -- Raw data for debugging
    raw_data JSONB,

    created_at TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(source, source_event_id)
);

CREATE INDEX idx_reports_event ON source_reports(event_id);
CREATE INDEX idx_reports_source ON source_reports(source);
CREATE INDEX idx_reports_received ON source_reports(received_at DESC);
```

#### 3. `market_reactions` - Track Polymarket price movements

```sql
CREATE TABLE market_reactions (
    reaction_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id UUID REFERENCES earthquake_events(event_id),

    -- Market info
    market_slug VARCHAR(200) NOT NULL,
    outcome VARCHAR(50) NOT NULL,       -- 'Yes', 'No', '8+', etc.
    token_id VARCHAR(100),

    -- Prices
    price_at_detection DECIMAL(5,4),    -- Price when we detected earthquake
    price_at_usgs DECIMAL(5,4),         -- Price when USGS published
    price_1h_after DECIMAL(5,4),        -- Price 1 hour after detection
    price_final DECIMAL(5,4),           -- Final price before resolution

    -- Timing
    detected_at TIMESTAMPTZ NOT NULL,
    usgs_published_at TIMESTAMPTZ,

    -- Analysis
    edge_minutes DECIMAL(6,2),          -- Minutes between detection and USGS
    price_move_pct DECIMAL(5,2),        -- % price change

    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_reactions_event ON market_reactions(event_id);
CREATE INDEX idx_reactions_market ON market_reactions(market_slug);
```

#### 4. `extended_history` - USGS history + our early detections

```sql
CREATE VIEW extended_history AS
SELECT
    COALESCE(e.usgs_id, e.event_id::text) as id,
    e.event_time as time,
    e.best_magnitude as magnitude,
    e.latitude,
    e.longitude,
    e.depth_km,
    e.location_name as place,
    e.usgs_id IS NOT NULL as in_usgs,
    e.first_detected_at,
    e.usgs_published_at,
    EXTRACT(EPOCH FROM (e.usgs_published_at - e.first_detected_at))/60 as detection_advantage_minutes
FROM earthquake_events e
WHERE e.best_magnitude >= 6.5
ORDER BY e.event_time DESC;
```

---

## Data Sources Implementation

### 1. JMA (Japan) - Highest Priority

```python
# Poll interval: 30 seconds
# URL: https://www.jma.go.jp/bosai/quake/data/list.json
# Latency: ~10-15 seconds after event
# Coverage: Japan and surrounding region

class JMACollector:
    URL = "https://www.jma.go.jp/bosai/quake/data/list.json"
    POLL_INTERVAL = 30  # seconds

    async def poll(self):
        async with httpx.AsyncClient() as client:
            response = await client.get(self.URL, timeout=10)
            data = response.json()

        for quake in data:
            if quake.get('mag', 0) >= 6.5:
                yield {
                    'source': 'jma',
                    'source_event_id': quake['eid'],
                    'magnitude': quake['mag'],
                    'magnitude_type': 'MJMA',
                    'latitude': quake['lat'],
                    'longitude': quake['lon'],
                    'depth_km': quake['dep'],
                    'event_time': parse_jma_time(quake['at']),
                    'location_name': quake['en_anm'],
                    'raw_data': quake,
                }
```

### 2. EMSC (Europe) - WebSocket

```python
# WebSocket: ws://www.seismicportal.eu/standing_order/websocket
# Latency: ~1-2 minutes
# Coverage: Europe M5+, Global M7+

class EMSCCollector:
    WS_URL = "ws://www.seismicportal.eu/standing_order/websocket"

    async def connect(self):
        async with websockets.connect(self.WS_URL) as ws:
            async for message in ws:
                data = json.loads(message)
                if data.get('action') == 'create':
                    quake = data['data']['properties']
                    if quake.get('mag', 0) >= 6.5:
                        yield self._parse_event(quake)
```

### 3. GFZ (Germany)

```python
# Poll interval: 60 seconds
# URL: https://geofon.gfz-potsdam.de/fdsnws/event/1/query
# Latency: ~6-9 minutes
# Coverage: Global

class GFZCollector:
    URL = "https://geofon.gfz-potsdam.de/fdsnws/event/1/query"
    POLL_INTERVAL = 60

    async def poll(self):
        params = {
            'format': 'json',
            'minmagnitude': 6.5,
            'orderby': 'time',
            'limit': 20
        }
        # ...
```

### 4. GeoNet (New Zealand)

```python
# Poll interval: 60 seconds
# URL: https://api.geonet.org.nz/quake?MMI=-1
# Latency: ~5 minutes
# Coverage: New Zealand and Pacific

class GeoNetCollector:
    URL = "https://api.geonet.org.nz/quake"
    POLL_INTERVAL = 60
```

### 5. USGS (Reference)

```python
# Poll interval: 60 seconds
# URL: https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_hour.geojson
# Latency: ~13-20 minutes (international)
# Coverage: Global
# Purpose: Reference source, resolution authority

class USGSCollector:
    URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_hour.geojson"
    POLL_INTERVAL = 60

    async def poll(self):
        async with httpx.AsyncClient() as client:
            response = await client.get(self.URL, timeout=30)
            data = response.json()

        for feature in data['features']:
            props = feature['properties']
            if props.get('mag', 0) >= 6.5:
                yield {
                    'source': 'usgs',
                    'source_event_id': feature['id'],
                    'magnitude': props['mag'],
                    'magnitude_type': props.get('magType', 'Mw'),
                    'latitude': feature['geometry']['coordinates'][1],
                    'longitude': feature['geometry']['coordinates'][0],
                    'depth_km': feature['geometry']['coordinates'][2],
                    'event_time': datetime.fromtimestamp(props['time']/1000, tz=timezone.utc),
                    'location_name': props.get('place'),
                    'raw_data': feature,
                }
```

---

## Event Matching Algorithm

```python
def match_event(new_report: dict, existing_events: list) -> Optional[UUID]:
    """
    Match incoming report to existing event.

    Criteria:
    1. Time difference < 5 minutes
    2. Distance < 100 km
    3. Magnitude difference < 1.0
    """
    report_time = new_report['event_time']
    report_lat = new_report['latitude']
    report_lon = new_report['longitude']
    report_mag = new_report['magnitude']

    for event in existing_events:
        # Time check
        time_diff = abs((report_time - event.event_time).total_seconds())
        if time_diff > 300:  # 5 minutes
            continue

        # Distance check (Haversine)
        distance = haversine(report_lat, report_lon, event.latitude, event.longitude)
        if distance > 100:  # km
            continue

        # Magnitude check (optional, for sanity)
        if abs(report_mag - event.best_magnitude) > 1.0:
            continue

        return event.event_id

    return None  # New event
```

---

## Best Magnitude Selection

```python
def calculate_best_magnitude(reports: list) -> tuple[float, str]:
    """
    Select best magnitude estimate from multiple sources.

    Priority:
    1. USGS Mw (if available) - authoritative
    2. JMA (if M < 8.0) - good for shallow Japan events
    3. GFZ Mw
    4. EMSC
    5. Average of all Mw estimates
    """
    usgs_report = next((r for r in reports if r.source == 'usgs'), None)
    if usgs_report and usgs_report.magnitude_type == 'Mw':
        return usgs_report.magnitude, 'Mw (USGS)'

    # For non-USGS, prefer Mw estimates
    mw_reports = [r for r in reports if r.magnitude_type in ('Mw', 'mw', 'mww')]
    if mw_reports:
        avg_mw = sum(r.magnitude for r in mw_reports) / len(mw_reports)
        return round(avg_mw, 1), f'Mw (avg of {len(mw_reports)})'

    # Fallback: highest magnitude (conservative for trading)
    max_report = max(reports, key=lambda r: r.magnitude)
    return max_report.magnitude, f'{max_report.magnitude_type} ({max_report.source})'
```

---

## Alert System

```python
class AlertService:
    def __init__(self, config):
        self.telegram_bot_token = config.get('TELEGRAM_BOT_TOKEN')
        self.telegram_chat_id = config.get('TELEGRAM_CHAT_ID')
        self.min_magnitude = 7.0

    async def send_alert(self, event: EarthquakeEvent, is_new: bool):
        if event.best_magnitude < self.min_magnitude:
            return

        emoji = "🔴" if event.best_magnitude >= 7.5 else "🟠"
        status = "NEW" if is_new else "UPDATE"

        message = f"""
{emoji} **{status}: M{event.best_magnitude} Earthquake**

📍 {event.location_name}
🕐 {event.event_time.strftime('%Y-%m-%d %H:%M:%S UTC')}
📊 Sources: {event.source_count}
⏱️ Detected: {event.first_detected_at.strftime('%H:%M:%S')}

USGS: {'✅ Published' if event.usgs_id else '⏳ Pending'}
"""

        await self._send_telegram(message)

        if event.best_magnitude >= 7.5:
            self._play_sound_alert()
```

---

## Extended History API

```python
class ExtendedHistoryService:
    """
    Provides USGS-compatible history with early detections appended.
    """

    async def get_history(
        self,
        start_date: datetime,
        end_date: datetime,
        min_magnitude: float = 7.0,
    ) -> list[dict]:
        """
        Returns earthquakes in USGS-compatible format,
        including events detected before USGS publication.
        """
        query = """
            SELECT
                COALESCE(usgs_id, event_id::text) as id,
                event_time as time,
                best_magnitude as mag,
                latitude as lat,
                longitude as lon,
                depth_km as depth,
                location_name as place,
                usgs_id IS NOT NULL as confirmed_by_usgs,
                first_detected_at,
                usgs_published_at
            FROM earthquake_events
            WHERE event_time BETWEEN $1 AND $2
              AND best_magnitude >= $3
            ORDER BY event_time DESC
        """
        return await self.db.fetch(query, start_date, end_date, min_magnitude)
```

---

## Market Reaction Analysis

```python
class MarketAnalyzer:
    """
    Analyze whether market reacted before USGS publication.
    """

    async def analyze_reaction(self, event_id: UUID):
        event = await self.db.get_event(event_id)

        if not event.usgs_published_at:
            return None  # USGS hasn't published yet

        # Get relevant markets
        markets = self._get_affected_markets(event.best_magnitude)

        for market in markets:
            # Get price at our detection time
            price_at_detection = await self.polymarket.get_historical_price(
                market.token_id,
                event.first_detected_at
            )

            # Get price at USGS publication
            price_at_usgs = await self.polymarket.get_historical_price(
                market.token_id,
                event.usgs_published_at
            )

            # Calculate edge
            edge_minutes = (event.usgs_published_at - event.first_detected_at).total_seconds() / 60
            price_move = (price_at_usgs - price_at_detection) / price_at_detection * 100

            await self.db.save_reaction({
                'event_id': event_id,
                'market_slug': market.slug,
                'outcome': market.outcome,
                'price_at_detection': price_at_detection,
                'price_at_usgs': price_at_usgs,
                'edge_minutes': edge_minutes,
                'price_move_pct': price_move,
            })
```

---

## Configuration

```python
# config.py
CONFIG = {
    # Database
    'DATABASE_URL': 'postgresql://postgres:dbpass@172.24.192.1:5432/earthquake_monitor',

    # Polling intervals (seconds)
    'JMA_POLL_INTERVAL': 30,
    'EMSC_RECONNECT_INTERVAL': 60,
    'GFZ_POLL_INTERVAL': 60,
    'GEONET_POLL_INTERVAL': 60,
    'USGS_POLL_INTERVAL': 60,

    # Thresholds
    'MIN_MAGNITUDE_TRACK': 6.5,      # Track M6.5+ events
    'MIN_MAGNITUDE_ALERT': 7.0,      # Alert for M7.0+ events
    'MIN_MAGNITUDE_URGENT': 7.5,     # Sound alert for M7.5+

    # Event matching
    'MATCH_TIME_WINDOW_SEC': 300,    # 5 minutes
    'MATCH_DISTANCE_KM': 100,

    # Alerts
    'TELEGRAM_BOT_TOKEN': 'your_token',
    'TELEGRAM_CHAT_ID': 'your_chat_id',
}
```

---

## File Structure

```
earthquake_bot/
├── monitor/
│   ├── __init__.py
│   ├── main.py                 # Entry point
│   ├── config.py               # Configuration
│   ├── database.py             # DB connection & queries
│   │
│   ├── collectors/
│   │   ├── __init__.py
│   │   ├── base.py             # Base collector class
│   │   ├── jma.py              # JMA collector
│   │   ├── emsc.py             # EMSC WebSocket collector
│   │   ├── gfz.py              # GFZ collector
│   │   ├── geonet.py           # GeoNet collector
│   │   └── usgs.py             # USGS collector
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   ├── event_matcher.py    # Deduplication logic
│   │   ├── magnitude.py        # Best magnitude selection
│   │   ├── alert.py            # Alert service
│   │   └── history.py          # Extended history
│   │
│   └── models/
│       ├── __init__.py
│       └── events.py           # Data models
│
├── analysis/
│   ├── market_reactions.py     # Market reaction analysis
│   └── statistics.py           # Edge window statistics
│
├── migrations/
│   └── 001_initial.sql         # Database schema
│
└── docs/
    └── realtime_monitoring_architecture.md
```

---

## Running the System

```bash
# Start the monitor
cd earthquake_bot
python -m monitor.main

# Or with specific sources
python -m monitor.main --sources jma,emsc,usgs

# Analysis mode
python -m analysis.market_reactions --event-id <uuid>
```

---

## Key Metrics to Track

1. **Detection Advantage**: Time between our first detection and USGS publication
2. **Magnitude Accuracy**: Difference between early estimate and final USGS magnitude
3. **Market Reaction Time**: Did market move before or after USGS publication?
4. **Source Reliability**: Which sources are fastest and most accurate?

---

## Future Enhancements

1. **Additional Sources**:
   - Taiwan CWA (web scraping)
   - Indonesia BMKG
   - Chile CSN
   - Philippines PHIVOLCS

2. **Machine Learning**:
   - Predict final USGS magnitude from early reports
   - Identify false positives (magnitude downgrades)

3. **Auto-Trading**:
   - Automatic order placement when M7.3+ detected
   - Risk management based on magnitude uncertainty
