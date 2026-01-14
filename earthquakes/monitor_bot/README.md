# Earthquake Monitor Bot

Real-time earthquake monitoring system with TUI visualization and PostgreSQL storage.

## Features

- 🌍 **Multi-source monitoring**: JMA, EMSC, GFZ, GeoNet, USGS
- ⚡ **Real-time updates**: WebSocket and HTTP polling
- 🎨 **Rich TUI interface**: Live table with color-coded events
- 💾 **PostgreSQL storage**: All events and source reports saved
- 🔔 **Edge time tracking**: Shows detection advantage vs USGS
- 📊 **Event deduplication**: Multiple sources merged into single event

## Quick Start

### 1. Setup PostgreSQL

Create database and schema:

```bash
# Create database
createdb earthquake_monitor

# Apply schema
psql -d earthquake_monitor -f monitor_bot/schema.sql
```

Or using pgAdmin:
1. Create database `earthquake_monitor`
2. Execute `monitor_bot/schema.sql`

### 2. Configure Environment

```bash
# earthquakes/.env
DB_HOST=172.24.192.1    # Windows host from WSL
DB_PORT=5432
DB_NAME=earthquake_monitor
DB_USER=postgres
DB_PASSWORD=your_password
```

### 3. Install Dependencies

```bash
cd earthquakes
pip install -r monitor_bot/requirements.txt
```

### 4. Run Monitor Bot

```bash
python -m monitor_bot
```

## UI Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│ Earthquake Monitor Bot                                    Q: Quit   │
├─────────────────────────────────────────────────────────────────────┤
│ Status: Running | Sources: 5 active | Events: 12 | Pending: 2      │
├─────────────────────────────────────────────────────────────────────┤
│ Mag  │ Location              │ Srcs │ Detected  │ USGS Pub│ Edge  │
├──────┼───────────────────────┼──────┼───────────┼─────────┼───────┤
│ M7.2 │ Near coast of Japan   │  3   │ 14:23:15  │ Pending │  -    │
│ M6.8 │ Tonga region          │  2   │ 14:15:42  │ 14:28   │ 12m   │
│ M6.4 │ Chile                 │  1   │ 13:55:10  │ 14:02   │  7m   │
└─────────────────────────────────────────────────────────────────────┘
```

### Color Coding

- **M7.0+**: Red, bold (significant events)
- **M6.5-6.9**: Yellow, bold (warning level)
- **M6.0-6.4**: Cyan (tracked)
- **Edge > 10 min**: Green (significant detection advantage)
- **Pending**: Italic yellow (not yet in USGS)

### Keyboard Shortcuts

- `Q` - Quit
- `C` - Clear activity log

## Configuration

Edit `monitor_bot/config.py` to customize:

```python
MIN_MAGNITUDE_TRACK = 6.0         # Minimum magnitude to track
MIN_MAGNITUDE_SIGNIFICANT = 7.0   # Highlight threshold
EDGE_TIME_HIGHLIGHT = 10          # Minutes for edge highlighting
ACTIVE_COLLECTORS = ["jma", "emsc", "gfz", "geonet", "usgs"]
```

## Data Sources

| Source | Region | Latency | Method |
|--------|--------|---------|--------|
| JMA | Japan | ~15 sec | HTTP poll (30s) |
| EMSC | Europe/Global M7+ | ~2 min | WebSocket |
| GeoNet | New Zealand | ~5 min | HTTP poll (60s) |
| GFZ | Global | ~9 min | HTTP poll (60s) |
| USGS | Global | ~15 min | HTTP poll (60s) |

## Database Schema

### Tables

- `earthquake_events` - Deduplicated events
- `source_reports` - Raw reports from sources
- `market_reactions` - Market price tracking (future)

### Views

- `extended_history` - Events with USGS + early detections
- `source_performance` - Statistics per source

## Troubleshooting

### Database connection failed

1. Check PostgreSQL is running
2. Verify credentials in `.env`
3. Test connection: `psql -h 172.24.192.1 -U postgres -d earthquake_monitor`

### No events appearing

1. Check sources are active (status bar)
2. Verify magnitude threshold (default M6.0+)
3. Check activity log for errors

### High CPU usage

1. Reduce poll intervals in `config.py`
2. Disable some collectors
3. Increase `MIN_MAGNITUDE_TRACK`

## Development

### Project Structure

```
monitor_bot/
├── __init__.py
├── __main__.py          # Entry point
├── config.py            # Configuration
├── schema.sql           # Database schema
├── ui/
│   ├── __init__.py
│   └── app.py           # Textual TUI
├── requirements.txt
├── TODO.md              # Development roadmap
└── README.md            # This file
```

### Dependencies

- `textual>=0.63.0` - TUI framework
- `asyncpg>=0.29.0` - PostgreSQL async driver
- `httpx>=0.27.0` - HTTP client
- `websockets>=12.0` - WebSocket client

## License

MIT
