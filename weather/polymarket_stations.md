# Polymarket Temperature Markets — Станции резолюции

Все 18 городов резолвятся по Weather Underground, источник — METAR/ASOS аэропортовые станции.
Те же данные доступны бесплатно через IEM (mesonet.agron.iastate.edu).

## Станции

| City | Станция | ICAO | Unit | IEM Station | WU Path |
|------|---------|------|------|-------------|---------|
| Chicago | O'Hare Intl Airport | KORD | °F | ORD | us/il/chicago/KORD |
| NYC | LaGuardia Airport | KLGA | °F | LGA | us/ny/new-york-city/KLGA |
| Dallas | Love Field | KDAL | °F | DAL | us/tx/dallas/KDAL |
| Miami | Miami Intl Airport | KMIA | °F | MIA | us/fl/miami/KMIA |
| Atlanta | Hartsfield-Jackson Intl | KATL | °F | ATL | us/ga/atlanta/KATL |
| Seattle | Seattle-Tacoma Intl | KSEA | °F | SEA | us/wa/seatac/KSEA |
| Toronto | Pearson Intl Airport | CYYZ | °C | CYYZ | ca/mississauga/CYYZ |
| London | London City Airport | EGLC | °C | EGLC | gb/london/EGLC |
| Paris | Charles de Gaulle | LFPG | °C | LFPG | fr/paris/LFPG |
| Munich | Munich Airport | EDDM | °C | EDDM | de/munich/EDDM |
| Ankara | Esenboğa Intl Airport | LTAC | °C | LTAC | tr/çubuk/LTAC |
| Seoul | Incheon Intl Airport | RKSI | °C | RKSI | kr/incheon/RKSI |
| Tokyo | Tokyo Haneda Airport | RJTT | °C | RJTT | jp/tokyo/RJTT |
| Tel Aviv | Ben Gurion Intl Airport | LLBG | °C | LLBG | il/tel-aviv/LLBG |
| Buenos Aires | Minister Pistarini (Ezeiza) | SAEZ | °C | SAEZ | ar/ezeiza/SAEZ |
| Sao Paulo | Guarulhos Intl Airport | SBGR | °C | SBGR | br/guarulhos/SBGR |
| Lucknow | Chaudhary Charan Singh Intl | VILK | °C | VILK | in/lucknow/VILK |
| Wellington | Wellington Intl Airport | NZWN | °C | NZWN | nz/wellington/NZWN |

## Примечания

- **US станции**: IEM использует код без K (KORD → ORD), международные — полный ICAO
- **Wellington (NZWN)**: нет daily.py на IEM, нужен hourly asos.py
- **Dallas**: Polymarket использует **Love Field (KDAL)**, НЕ DFW — проверить cities.json
- **London**: **City Airport (EGLC)**, НЕ Heathrow — проверить cities.json
- **Tokyo, Tel Aviv**: новые города, не в текущем cities.json бота

## WU URL формат

```
https://www.wunderground.com/history/daily/{WU_Path}
```

Пример: https://www.wunderground.com/history/daily/us/il/chicago/KORD

## IEM API для актуалов

```
# Daily max (US + большинство станций)
https://mesonet.agron.iastate.edu/cgi-bin/request/daily.py?network={NET}&stations={STA}&var=max_temp_f&format=csv

# Hourly METAR (для станций без daily.py, например Wellington)
https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?station={ICAO}&data=tmpf&format=onlycomma
```
