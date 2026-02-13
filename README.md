# Polymarket Console

Trading tools and analysis suite for [Polymarket](https://polymarket.com) prediction markets.

Built on top of [py-clob-client](https://github.com/Polymarket/py-clob-client) — the official Python client for Polymarket's CLOB API. The original library lives in `polymarket_console/` and handles all order signing, placement, and market data queries.

## Project Structure

### `polymarket_console/` — CLOB Client Library
Fork of [py-clob-client](https://github.com/Polymarket/py-clob-client). Handles authentication, order building, signing, and API communication with Polymarket's Central Limit Order Book.

### `earthquakes/` — Earthquake Trading Bot
Automated trading system for earthquake prediction markets on Polymarket.

- **Trading bot** (`trading_bot/`) — scans markets, calculates edge using USGS seismic data, places orders automatically
- **Monitor bot** (`monitor_bot/`) — TUI dashboard for tracking positions, P&L, and market state
- **Update bot** (`update_bot/`) — refreshes market data and probability models using Claude API
- Entry point: `run_trading_bot.sh`, `run_monitor_bot.sh`, `run_update_bot.sh`
- Backtesting: `backtest.py`, `backtest_edge_strategy.py`, `backtest_integrated_model.py`

### `crypto/` — Crypto Market Analysis
Tools for analyzing BTC prediction markets on Polymarket.

- **`smart_money.py`** — Smart Money v2 analysis: identifies top traders per market, weights by profitability/ROI/experience, calculates implied probability vs market price
- **`deribit_compare.py`** — compares Polymarket prices with Deribit options implied probabilities (Black-Scholes terminal + first-passage touch probability)
- **`btc_model.ipynb`** — Jupyter notebook for BTC price modeling
- Strategy docs in `manual/` — exit strategies, portfolio notes, SM methodology guide

### `politics/` — Political Market Analysis
Smart Money analysis for political prediction markets.

- **`smart_money_scan.py`** — parallel scanner for 50+ political events (140+ markets), identifies mispriced positions
- Exit strategies and portfolio strategy documentation
- SM scan reports by date

### `reports/` — Portfolio Review Reports
Auto-generated daily portfolio review reports (Markdown). Created by the `/review-portfolio` Claude Code macro.

### `.claude/commands/` — Claude Code Macros
- **`review-portfolio.md`** — full portfolio review workflow: SM analysis, Deribit comparison, news context, strategy trigger checks, and report generation

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

For the earthquake bot, see `earthquakes/QUICKSTART.md`.

## Key Dependencies

- `py-clob-client` core: `eth-account`, `httpx[http2]`, `poly_eip712_structs`
- Crypto analysis: `numpy`, `scipy` (for Black-Scholes), `requests`
- Earthquake bot: see `earthquakes/requirements.txt`

## License

MIT — see [LICENSE](LICENSE).
