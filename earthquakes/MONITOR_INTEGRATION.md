# Monitor Bot Integration for Trading Bot

Monitor Bot теперь экспортирует данные в JSON для использования Trading Bot.

## Архитектура

```
┌─────────────────────────────┐
│  Monitor Bot                │
│  - Собирает из 5 источников │
│  - Рассчитывает Edge Time   │
│  - Сохраняет в RAM          │
│         ↓                    │
│  Каждые 5 минут:            │
│  events_cache.json ←────────┤
└─────────────────────────────┘
              │
              │ JSON файл (atomic writes)
              │
              ↓
┌─────────────────────────────┐
│  Trading Bot                │
│  - Читает JSON кеш          │
│  - Получает Edge Time       │
│  - Принимает решения        │
└─────────────────────────────┘
```

## JSON Cache Location

```
earthquakes/monitor_bot/data/events_cache.json
```

**Содержит только события НЕ подтверждённые USGS** - это даёт информационное преимущество!

**Формат:**
```json
{
  "last_updated": "2026-01-17T15:30:00+00:00",
  "event_count": 3,
  "events": [
    {
      "event_id": "uuid-here",
      "best_magnitude": 6.0,
      "location_name": "OFF COAST OF OREGON",
      "latitude": 45.5,
      "longitude": -130.2,
      "event_time": "2026-01-17T14:50:00+00:00",
      "first_detected_at": "2026-01-17T14:52:43+00:00",
      "usgs_published_at": null,
      "detection_advantage_minutes": null,
      "usgs_id": null,
      "jma_id": null,
      "emsc_id": "1234567",
      "gfz_id": null,
      "geonet_id": null,
      "source_count": 1,
      "is_significant": false
    }
  ]
}
```

## Usage in Trading Bot

### Simple Integration

```python
from monitor_cache import get_edge_time

# In your trading logic:
usgs_event_id = "us7000p123"
edge_minutes = get_edge_time(usgs_event_id)

if edge_minutes is not None and edge_minutes > 5:
    print(f"Information advantage: {edge_minutes:.1f} minutes")
    # Trade with confidence
else:
    print("No edge or USGS published first")
    # Skip or trade conservatively
```

### Advanced Integration

```python
from monitor_cache import get_monitor_events, get_event_sources

events = get_monitor_events()

for event in events:
    usgs_id = event.get("usgs_id")
    if not usgs_id:
        continue  # Not in USGS yet

    edge = event.get("detection_advantage_minutes")
    magnitude = event["best_magnitude"]
    sources = get_event_sources(usgs_id)

    print(f"M{magnitude} - Edge: {edge}min - Sources: {sources}")

    # Trading decision based on edge and source count
    if edge and edge > 10 and len(sources) >= 3:
        print("  → STRONG SIGNAL - Multiple sources, big edge")
    elif edge and edge > 5:
        print("  → MODERATE SIGNAL - Some edge")
    else:
        print("  → WEAK SIGNAL - No edge")
```

## Key Fields for Trading

| Field | Type | Description |
|-------|------|-------------|
| `usgs_id` | null | **Always null** - JSON contains only pending events NOT in USGS |
| `detection_advantage_minutes` | null | **Always null** - calculated only when USGS confirms |
| `best_magnitude` | float | Best magnitude estimate from available sources |
| `source_count` | int | Number of sources that detected this (1+) |
| `jma_id`, `emsc_id`, etc. | str\|null | Source IDs (null if not detected by that source) |
| `first_detected_at` | ISO 8601 | When we first detected this event |
| `event_time` | ISO 8601 | Estimated earthquake occurrence time |

## Update Triggers (Event-Driven)

JSON обновляется **мгновенно** при:
1. ✅ **Новое землетрясение обнаружено** (не в USGS) → Trading bot видит сразу
2. ✅ **Событие получило подтверждение от нового источника** (source_count++)
3. ❌ **Событие удаляется из JSON когда USGS подтверждает** (больше нет преимущества)
4. 🔄 **Fallback: каждые 5 минут** (периодическое обновление)

**Debouncing:** Не чаще раз в 10 секунд (защита от спама при буре событий)

## Performance

- **Update latency:** < 1 секунда при значимом событии
- **Read frequency:** Safe to read every 30-60 seconds (или чаще!)
- **Atomic writes:** No risk of reading corrupted data
- **File size:** ~10-50KB for 24 hours of M4.5+ events
- **Read time:** < 1ms

## Configuration

Monitor Bot settings in `monitor_bot/config.py`:

```python
USE_DATABASE = False  # Disable PostgreSQL (JSON-only)
JSON_SAVE_INTERVAL = 300  # Save every 5 minutes
JSON_RETENTION_HOURS = 24  # Keep last 24 hours
```

Set `USE_DATABASE=true` in `.env` to enable PostgreSQL for long-term storage.

## Testing

```bash
# Test monitor cache
cd earthquakes
python monitor_cache.py

# Output:
# Cache info: {'exists': True, 'last_updated': '...', 'event_count': 15}
# Total events: 15
#
# First event:
#   Magnitude: M6.0
#   Location: OFF COAST OF OREGON
#   USGS ID: us7000p123
#   Edge Time: 6.2 min
```

## Troubleshooting

**Q: JSON file doesn't exist**
- Monitor Bot hasn't run yet, or no events in last 24 hours
- Check `monitor_bot/data/` directory

**Q: Edge time is always None**
- Events are historical (loaded after USGS published)
- Or USGS published before other sources detected

**Q: How often is JSON updated?**
- **Event-driven:** Мгновенно при новом событии или USGS подтверждении
- **Fallback:** Каждые 5 минут (configurable via `JSON_SAVE_INTERVAL`)
- **Debouncing:** Не чаще раз в 10 секунд (защита от спама)

**Q: Is it safe to read during write?**
- Yes! Atomic writes guarantee you always read complete file
- Either old snapshot or new snapshot, never partial
