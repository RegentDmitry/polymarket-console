# Earthquake Monitoring System

## Overview

Real-time earthquake monitoring system that aggregates data from multiple seismological agencies, providing information edge for Polymarket trading by detecting earthquakes before USGS publication.

**Key insight**: USGS publishes international earthquakes with ~13-20 minute delay. Regional agencies (JMA, EMSC) publish within seconds to minutes. This creates a **10-15 minute information advantage**.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           DATA SOURCES (Parallel)                           │
├─────────────┬─────────────┬─────────────┬─────────────┬─────────────────────┤
│    JMA      │    EMSC     │    GFZ      │   GeoNet    │       USGS          │
│  (Japan)    │  (Europe)   │ (Germany)   │    (NZ)     │    (Reference)      │
│  ~15 sec    │   ~2 min    │  ~9 min     │   ~5 min    │    ~15 min          │
│  poll 30s   │  WebSocket  │  poll 60s   │  poll 60s   │    poll 60s         │
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
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │ VIEWS                                                               │    │
│  │ - extended_history: USGS + early detections                         │    │
│  │ - source_performance: speed statistics per source                   │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
                                   │
                    ┌──────────────┴──────────────┐
                    │                             │
                    ▼                             ▼
┌─────────────────────────────────┐  ┌────────────────────────────────────────┐
│   EXTENDED USGS CLIENT          │  │        TRADING BOT (main.py)           │
│                                 │  │                                        │
│  - get_extended_earthquakes()   │  │  - Shows extended history at startup  │
│  - get_pending_events()         │  │  - "У ВАС ЕСТЬ ПРЕИМУЩЕСТВО!"          │
│  - count_extended_earthquakes() │  │  - Uses extended history for analysis │
└─────────────────────────────────┘  └────────────────────────────────────────┘
```

---

## File Structure

```
earthquake_bot/
├── extended_usgs_client.py      # Extended history client for trading bot
├── main.py                      # Trading bot (updated with extended history)
│
├── monitor/                     # Real-time monitoring system
│   ├── __init__.py
│   ├── config.py                # Configuration (DB, intervals, thresholds)
│   ├── database.py              # PostgreSQL operations
│   ├── main.py                  # Main monitoring service entry point
│   ├── models.py                # Data models (SourceReport, EarthquakeEvent)
│   ├── requirements.txt         # Dependencies
│   │
│   ├── collectors/              # Data collectors (one per source)
│   │   ├── __init__.py
│   │   ├── base.py              # Base collector class
│   │   ├── jma.py               # Japan Meteorological Agency (~15 sec)
│   │   ├── emsc.py              # European-Mediterranean (WebSocket, ~2 min)
│   │   ├── gfz.py               # GeoForschungsZentrum Germany (~9 min)
│   │   ├── geonet.py            # New Zealand GeoNet (~5 min)
│   │   └── usgs.py              # USGS reference source (~15 min)
│   │
│   └── services/
│       ├── __init__.py
│       ├── event_matcher.py     # Deduplication by coordinates/time
│       └── history.py           # Extended history queries
│
└── docs/
    ├── monitoring_system.md             # This file
    ├── advanced_monitoring_possibilities.md  # Research on data sources
    └── realtime_monitoring_architecture.md   # Detailed architecture
```

---

## Data Sources

### Source Comparison

| Source | Region | Latency | Method | URL |
|--------|--------|---------|--------|-----|
| **JMA** | Japan | ~15 sec | HTTP poll (30s) | `https://www.jma.go.jp/bosai/quake/data/list.json` |
| **EMSC** | Europe/Global M7+ | ~2 min | WebSocket | `wss://www.seismicportal.eu/standing_order/websocket` |
| **GFZ** | Global | ~9 min | HTTP poll (60s) | `https://geofon.gfz-potsdam.de/fdsnws/event/1/query` |
| **GeoNet** | New Zealand | ~5 min | HTTP poll (60s) | `https://api.geonet.org.nz/quake` |
| **USGS** | Global | ~15 min | HTTP poll (60s) | `https://earthquake.usgs.gov/.../4.5_hour.geojson` |

### Latency Timeline

```
Event occurs                    ████ 0 sec
JMA detection                   █████ ~15 sec
EMSC WebSocket                  ████████████ ~2 min
GeoNet                          ████████████████████ ~5 min
GFZ                             ████████████████████████████ ~9 min
USGS publication                ████████████████████████████████████████ ~15 min

                                |<--- EDGE WINDOW: ~14 minutes --->|
```

---

## Database Schema

### Tables

```sql
-- Main deduplicated events
CREATE TABLE earthquake_events (
    event_id UUID PRIMARY KEY,
    best_magnitude DECIMAL(3,1) NOT NULL,
    best_magnitude_type VARCHAR(10),
    latitude DECIMAL(8,5) NOT NULL,
    longitude DECIMAL(8,5) NOT NULL,
    depth_km DECIMAL(6,2),
    location_name TEXT,
    event_time TIMESTAMPTZ NOT NULL,
    first_detected_at TIMESTAMPTZ NOT NULL,  -- When WE detected
    usgs_published_at TIMESTAMPTZ,            -- When USGS published
    usgs_id VARCHAR(50) UNIQUE,
    jma_id VARCHAR(50),
    emsc_id VARCHAR(50),
    gfz_id VARCHAR(50),
    geonet_id VARCHAR(50),
    source_count INTEGER DEFAULT 1,
    is_significant BOOLEAN DEFAULT FALSE
);

-- Raw reports from each source
CREATE TABLE source_reports (
    report_id UUID PRIMARY KEY,
    event_id UUID REFERENCES earthquake_events(event_id),
    source VARCHAR(20) NOT NULL,  -- 'jma', 'emsc', 'gfz', etc.
    source_event_id VARCHAR(100),
    magnitude DECIMAL(3,1) NOT NULL,
    magnitude_type VARCHAR(10),
    latitude DECIMAL(8,5),
    longitude DECIMAL(8,5),
    event_time TIMESTAMPTZ NOT NULL,
    reported_at TIMESTAMPTZ,      -- When source published
    received_at TIMESTAMPTZ NOT NULL,  -- When we received
    raw_data JSONB,
    UNIQUE(source, source_event_id)
);

-- Market reaction tracking
CREATE TABLE market_reactions (
    reaction_id UUID PRIMARY KEY,
    event_id UUID REFERENCES earthquake_events(event_id),
    market_slug VARCHAR(200) NOT NULL,
    outcome VARCHAR(50) NOT NULL,
    price_at_detection DECIMAL(5,4),
    price_at_usgs DECIMAL(5,4),
    edge_minutes DECIMAL(6,2),
    price_move_pct DECIMAL(5,2)
);
```

### Views

```sql
-- Extended history (USGS + early detections)
CREATE VIEW extended_history AS
SELECT
    COALESCE(usgs_id, event_id::text) as id,
    event_time as time,
    best_magnitude as magnitude,
    usgs_id IS NOT NULL as in_usgs,
    EXTRACT(EPOCH FROM (usgs_published_at - first_detected_at))/60
        as detection_advantage_minutes
FROM earthquake_events
WHERE best_magnitude >= 6.5;

-- Source performance statistics
CREATE VIEW source_performance AS
SELECT
    source,
    COUNT(*) as total_reports,
    AVG(EXTRACT(EPOCH FROM (received_at - event_time))) as avg_delay_seconds
FROM source_reports
GROUP BY source;
```

---

## Event Matching Algorithm

Incoming reports are matched to existing events by:

1. **Time**: Event time within 5 minutes
2. **Distance**: Coordinates within 100 km (Haversine formula)
3. **Magnitude**: Difference < 1.5 (sanity check)

```python
def is_match(report, event):
    time_diff = abs(report.event_time - event.event_time)
    if time_diff > 300 seconds:
        return False

    distance = haversine(report.lat, report.lon, event.lat, event.lon)
    if distance > 100 km:
        return False

    return True
```

---

## Best Magnitude Selection

Priority order:
1. **USGS Mw** (authoritative for Polymarket resolution)
2. **Average of all Mw estimates** from sources
3. **Highest magnitude** (conservative for trading)

---

## Usage

### 1. Start Monitoring Service

```bash
cd earthquake_bot

# Install dependencies
pip install -r monitor/requirements.txt

# Run all collectors
python -m monitor.main

# Run specific collectors only
python -m monitor.main --sources jma,usgs

# Check current status
python -m monitor.main --status
```

### 2. Run Trading Bot

```bash
cd earthquake_bot

# With extended history check
python main.py

# Without extended history
python main.py --no-extended

# Trading modes
python main.py --debug      # Confirm before trades
python main.py --auto       # Automatic trading
```

### 3. Example Output

When extended history events exist:
```
===========================================================================
EARTHQUAKE TRADING BOT
===========================================================================
Время: 2026-01-04 19:30 UTC
Режим: ANALYSIS

!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
!!! EXTENDED HISTORY: СОБЫТИЯ ОБНАРУЖЕНЫ ДО USGS !!!
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
  M7.2 | Near coast of Japan | 3 источников | 5 мин назад
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
*** У ВАС ЕСТЬ ИНФОРМАЦИОННОЕ ПРЕИМУЩЕСТВО! ***
*** Эти события ЕЩЁ НЕ В USGS! ***
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
```

---

## Database Connection

PostgreSQL is accessed from WSL via Windows host IP:

```python
# monitor/config.py
DB_HOST = "172.24.192.1"  # Windows host from WSL
DB_PORT = 5432
DB_NAME = "earthquake_monitor"
DB_USER = "postgres"
DB_PASSWORD = "dbpass"
```

---

## Key Metrics

1. **Detection Advantage**: Minutes between our detection and USGS publication
2. **Source Reliability**: Which sources are fastest and most accurate
3. **Magnitude Accuracy**: Difference between early estimate and final USGS
4. **Market Reaction**: Did price move before/after USGS publication

---

## Trading Strategy Implications

### When Extended History Shows M7.0+:

1. **Check magnitude buffer**: JMA M7.3+ likely stays M7.0+ in USGS
2. **Check sources**: 2+ sources confirming = higher confidence
3. **Act fast**: Edge window is ~10-15 minutes
4. **Binary markets**: "Another M7.0+ by date" becomes near-certain YES

### Magnitude Conversion Warning

| JMA/CWA Shows | USGS Likely | Action |
|---------------|-------------|--------|
| M7.3+ | M7.0+ | Trade confidently |
| M7.1-7.2 | M6.8-7.1 | Wait for confirmation |
| M7.0 | M6.6-7.0 | Risky, may drop below 7.0 |

---

## Future Enhancements

1. **More sources**: Taiwan CWA, Indonesia BMKG, Chile CSN
2. **Auto-trading**: Execute trades automatically on M7.3+ detection
3. **ML prediction**: Predict final USGS magnitude from early reports
4. **Telegram alerts**: Real-time notifications for M7+ events
