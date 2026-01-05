# Advanced Earthquake Monitoring: Gaining Information Edge

## Executive Summary

USGS is an **aggregator** that receives earthquake data with significant delay (~20 minutes for international events). Regional seismological agencies publish data **seconds to minutes** after an earthquake. This creates a potential **5-20 minute information advantage** for Polymarket trading.

---

## USGS Latency Analysis

### Official USGS Delays

| Data Type | Latency |
|-----------|---------|
| Earthquakes outside USA | **~20 minutes** (average) |
| Some contributing networks | **minutes to days** |
| Web page cache | +60 seconds |
| Feed cache (7-day feeds) | 1-5 minutes |
| ENS notifications (large events) | up to 45 minutes |

> *"Some data from contributing networks are delayed by several minutes or more, while some may arrive days after the event"*
> — [USGS FAQ](https://www.usgs.gov/faqs/how-quickly-earthquake-information-posted-usgs-website-and-sent-out-earthquake-notification)

### Why the Delay?

1. USGS NEIC acquires data from 2,000+ stations via 130+ networks worldwide
2. Physical propagation: seismic waves take time to reach distant stations
3. Data transmission: not all networks send data in real-time
4. Processing: initial estimates require analysis from multiple stations
5. Caching: web infrastructure adds additional delays

---

## M7+ Earthquake Zones

**90% of the world's largest earthquakes** occur along the [Pacific Ring of Fire](https://en.wikipedia.org/wiki/Ring_of_Fire).

### Primary Monitoring Targets

| Region | Annual M7+ Events | Local Agency | Data Speed |
|--------|-------------------|--------------|------------|
| **Japan** | 1-3 | JMA | **seconds** |
| **Indonesia** | 1-2 | BMKG | 2-5 min |
| **Philippines** | 0-1 | PHIVOLCS | 3-5 min |
| **Chile** | 1-2 | CSN | 1-3 min |
| **Alaska/Aleutians** | ~1 | USGS regional | 2-5 min |
| **Taiwan** | 0-1 | CWA | 10-20 sec |
| **New Zealand** | 0-1 | GeoNet | ~5 min |
| **Mexico** | 0-1 | SSN/SASMEX | 1-3 min |

### Secondary Zone: Alpine-Himalayan Belt (5-6% of large earthquakes)

- Mediterranean (EMSC coverage)
- Iran, Turkey, Caucasus
- Himalayas (India, Nepal, Pakistan)
- Indonesia (western)

---

## Regional Data Sources

### Tier 1: Maximum Speed (seconds)

#### Japan - JMA (Japan Meteorological Agency)

| Metric | Value |
|--------|-------|
| First alert | **3-10 seconds** after P-wave |
| Seismometers | 4,235 |
| Data publication | **seconds to minutes** |

**Data Access:**
```
JSON endpoint: https://www.jma.go.jp/bosai/quake/data/list.json
City dictionary: https://www.data.jma.go.jp/multi/data/dictionary/city.json
Web interface: https://www.data.jma.go.jp/multi/quake/index.html?lang=en
```

**Third-party apps:**
- [NERV App](https://nerv.app/en/) — processes JMA data in **<1 second**
- P2PQuake — crowdsourced network, WebSocket available

#### Taiwan - CWA (Central Weather Administration)

| Metric | Value |
|--------|-------|
| Alert time | **10-20 seconds** after earthquake |
| Stations | ~170 |
| Website | [scweb.cwa.gov.tw](https://scweb.cwa.gov.tw/en-us/earthquake/data) |

**Note:** No public API, requires web scraping.

---

### Tier 2: Fast (1-5 minutes)

#### Europe - EMSC (European-Mediterranean Seismological Centre)

| Metric | Value |
|--------|-------|
| "Flashsourcing" detection | **tens of seconds** |
| Coverage | Europe/Mediterranean (M5+), Global (M7+) |

**Data Access:**
```
WebSocket: ws://www.seismicportal.eu/standing_order/websocket
REST API: https://www.seismicportal.eu/fdsnws/event/1/query
Documentation: https://github.com/EMSC-CSEM/webservices101
```

#### New Zealand - GeoNet

| Metric | Value |
|--------|-------|
| Publication | **~5 minutes** (most events) |
| Network | ~700 locations |

**Data Access:**
```
API: https://shakinglayers.geonet.org.nz/api
Quake Search: https://quakesearch.geonet.org.nz/
```

#### Indonesia - BMKG

| Metric | Value |
|--------|-------|
| Sensors | 400+ |
| Data | Real-time via InaTEWS |

**Data Access:**
```
Real-time: https://inatews.bmkg.go.id/eng/realtime
Repository: https://repogempa.bmkg.go.id/
```

**Note:** API access requires registration (contact inatews@bmkg.go.id).

#### Chile - CSN (Centro Sismologico Nacional)

| Metric | Value |
|--------|-------|
| Coverage | Full Chile coastline |
| Data sharing | Real-time to USGS NEIC |

**Data Access:**
```
Website: https://www.sismologia.cl/
```

**Note:** Limited public API, data flows to USGS.

---

### Tier 3: Aggregators (10-20+ minutes)

| Source | Coverage | Delay |
|--------|----------|-------|
| USGS | Global | ~20 min (international) |
| EMSC (M7+ outside Europe) | Global | 10-15 min |

---

## Latency Comparison Visualization

```
M7.0 Event in Japan:

JMA (local):        ████ ~10 sec
NERV App:           █████ ~11 sec
Taiwan CWA:         ██████ ~20 sec (if near Taiwan)
EMSC:               ████████████ ~2 min
GeoNet:             ████████████████████ ~5 min
USGS (international): ████████████████████████████████████████ ~20 min

Potential edge window: 15-19 minutes
```

---

## Implementation Strategy

### Phase 1: Japan Monitoring (Highest Priority)

Japan offers the best combination of:
- Fastest data publication (seconds)
- High M7+ frequency
- Reliable JSON API

```python
# Polling approach
import httpx
import time

JMA_URL = "https://www.jma.go.jp/bosai/quake/data/list.json"

def poll_jma():
    response = httpx.get(JMA_URL, timeout=10)
    return response.json()

# Poll every 30 seconds
while True:
    data = poll_jma()
    # Check for new M7+ events
    # Alert if found
    time.sleep(30)
```

### Phase 2: Multi-Source Monitoring

Priority order:
1. **JMA** (Japan) — JSON polling
2. **EMSC** (Europe/Global) — WebSocket
3. **CWA** (Taiwan) — Web scraping
4. **GeoNet** (New Zealand) — REST API
5. **BMKG** (Indonesia) — Web scraping

### Phase 3: Automated Trading

When M7+ detected:
1. Verify magnitude meets threshold
2. Calculate fair prices for all affected markets
3. Check orderbook liquidity
4. Execute trades automatically

---

## Trading Scenarios

### Scenario A: M7.0+ Detected (Before USGS)

If we detect a M7.0+ earthquake 15 minutes before USGS publishes:

| Market | Current Price | Fair Price | Edge | Action |
|--------|---------------|------------|------|--------|
| "Another 7.0+ by Jan 31" | 69% | **100%** | +31% | BUY YES |
| "7.0+ in 2026: 20+" | 11% | ~17% | +6% | BUY YES |
| "7.0+ by June 30: 8+" | 75% | ~86% | +11% | BUY YES |

### Scenario B: Binary M8.0+ Market

If M8.0+ detected before market reacts:

| Market | Current Price | Fair Price | Action |
|--------|---------------|------------|--------|
| "Megaquake by June 30" | 8% | **100%** | BUY YES immediately |

**Risk:** M8.0+ is rare (~1/year globally). False positives from magnitude revisions.

---

## Risk Assessment

### Technical Risks

| Risk | Mitigation |
|------|------------|
| JMA API downtime | Multiple source fallback |
| Magnitude revision | Wait for confirmation from 2+ sources |
| Network latency | Deploy close to data sources |
| Rate limiting | Respect polling intervals |

### Market Risks

| Risk | Mitigation |
|------|------------|
| Other bots competing | Speed optimization, multiple sources |
| Low liquidity | Check orderbook before trading |
| Slippage | Limit orders, size limits |
| Market manipulation | Cross-reference multiple sources |

### Resolution Risks

| Risk | Mitigation |
|------|------------|
| USGS magnitude differs from JMA | Only trade clear cases (M7.2+ for M7.0 threshold) |
| Resolution disputes | Understand Polymarket rules |
| Earthquake location outside coverage | Multi-regional monitoring |

---

## Data Source URLs Summary

### Real-Time Feeds

| Source | URL | Format |
|--------|-----|--------|
| JMA Earthquake List | https://www.jma.go.jp/bosai/quake/data/list.json | JSON |
| EMSC WebSocket | ws://www.seismicportal.eu/standing_order/websocket | JSON |
| EMSC REST | https://www.seismicportal.eu/fdsnws/event/1/query | Various |
| GeoNet API | https://api.geonet.org.nz/quake | JSON |
| USGS GeoJSON | https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_hour.geojson | GeoJSON |

### Web Interfaces (for scraping)

| Source | URL |
|--------|-----|
| JMA (English) | https://www.data.jma.go.jp/multi/quake/index.html?lang=en |
| Taiwan CWA | https://scweb.cwa.gov.tw/en-us/earthquake/data |
| Indonesia BMKG | https://inatews.bmkg.go.id/eng/realtime |
| Philippines PHIVOLCS | https://earthquake.phivolcs.dost.gov.ph/ |
| Chile CSN | https://www.sismologia.cl/ |

---

## Validation Strategy

To verify the information advantage hypothesis:

1. **Log timestamps** from multiple sources for the same event
2. **Compare** JMA publication time vs USGS publication time
3. **Calculate** actual edge window
4. **Track** market price movements relative to data publication

Example validation query:
```python
# For each M7+ event, record:
event_data = {
    "jma_time": "2025-12-28T14:05:23+09:00",
    "usgs_time": "2025-12-28T14:24:15+00:00",
    "delta_minutes": 19,
    "market_price_at_jma": 0.69,
    "market_price_at_usgs": 0.85,
    "final_resolution": "YES"
}
```

---

## References

- [USGS NEIC](https://www.usgs.gov/programs/earthquake-hazards/national-earthquake-information-center-neic)
- [USGS FAQ - Posting Speed](https://www.usgs.gov/faqs/how-quickly-earthquake-information-posted-usgs-website-and-sent-out-earthquake-notification)
- [JMA Earthquake Activities](https://www.jma.go.jp/jma/en/Activities/earthquake.html)
- [JMA EEW System](https://www.jma.go.jp/jma/en/Activities/eew.html)
- [NERV App Guidelines](https://nerv.app/en/guideline.html)
- [EMSC WebServices](https://github.com/EMSC-CSEM/webservices101)
- [GeoNet Data Types](https://www.geonet.org.nz/data/types/eq_catalogue)
- [Ring of Fire - Wikipedia](https://en.wikipedia.org/wiki/Ring_of_Fire)
- [Pacific Ring of Fire - National Geographic](https://education.nationalgeographic.org/resource/plate-tectonics-ring-fire/)

---

*Last updated: January 2026*
