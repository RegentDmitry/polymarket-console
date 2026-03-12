"""
Weather temperature scanner — core trading logic.

Fetches ensemble forecasts, computes fair bucket probabilities via Normal
distribution model, generates BUY/SELL signals with edge computation.
"""

from typing import Callable, Dict, List, Optional

from ..calibration import CityCalibration
from ..market_data.forecast import ForecastData
from ..market_data.polymarket import WeatherMarket, WeatherPolymarketData
from ..models.signal import Signal, SignalType
from ..pricing import bucket_fair_price
from ..logger import get_logger

# Default calibration path (relative to cities.json)
_CALIBRATION_FILE = "backtest/data/calibration_results_weighted.json"


class WeatherScanner:
    """Scans weather temperature markets for trading opportunities."""

    def __init__(self, config):
        from ..config import WeatherBotConfig
        self.config: WeatherBotConfig = config
        self.polymarket = WeatherPolymarketData(config.markets_json)

        # Load per-city calibration
        cal_path = config.cities_json.parent / _CALIBRATION_FILE
        calibration = CityCalibration(cal_path)
        self.forecast = ForecastData(config.cities_json, calibration=calibration)

        # Caches from last scan
        self._fair_prices: Dict[str, float] = {}  # market_slug -> fair
        self._token_ids: Dict[str, str] = {}       # market_slug -> yes_token_id

    def scan_for_entries(self, progress_callback: Optional[Callable] = None,
                         ) -> List[Signal]:
        """Scan for BUY opportunities (including top-ups of held positions).

        Returns list of BUY signals sorted by edge descending.
        """
        logger = get_logger()

        # Load markets from JSON + refresh PM prices
        self.polymarket.update_from_json()
        if progress_callback:
            progress_callback("Refreshing prices...")
        self.polymarket.refresh_prices()

        active_markets = self.polymarket.get_active_markets()
        if progress_callback:
            progress_callback(f"Scanning {len(active_markets)} buckets...")

        signals: List[Signal] = []

        for market in active_markets:
            fc = self.forecast.get_forecast(market.city, market.date, market.unit)
            if not fc:
                continue

            # Compute fair price — always compute for positions panel
            fair = bucket_fair_price(
                fc.forecast, fc.sigma, market.bucket_lower, market.bucket_upper,
                df=fc.df,
            )

            # Cache for exit scanning and UI display
            self._fair_prices[market.market_slug] = fair
            self._token_ids[market.market_slug] = market.yes_token_id

            # Skip trading signals if too close to expiry
            if market.hours_remaining < self.config.min_hours_to_expiry:
                continue

            # Skip cheap buckets where sigma errors are amplified
            if market.yes_price < self.config.min_market_price:
                continue

            # Quick edge check against best ask (skip obvious non-edges)
            if fair - market.yes_price < self.config.min_edge:
                continue

            # Check liquidity and get weighted price (actual fill price)
            best_ask, liq_usd, weighted_price = (
                WeatherPolymarketData.get_usable_liquidity(
                    market.yes_token_id, fair
                )
            )

            if liq_usd < self.config.min_position_size:
                continue

            # Edge = fair - weighted_price (actual expected fill price)
            fill_price = weighted_price if weighted_price > 0 else market.yes_price
            edge = fair - fill_price

            if edge < self.config.min_edge:
                continue

            # Edge cap: skip suspiciously high edge (likely model error)
            if edge > self.config.max_edge_cap:
                continue

            signal = Signal(
                type=SignalType.BUY,
                market_id=market.condition_id,
                market_slug=market.market_slug,
                market_name=market.question,
                outcome="YES",
                current_price=fill_price,
                fair_price=fair,
                edge=edge,
                days_remaining=market.days_remaining,
                liquidity=liq_usd,
                token_id=market.yes_token_id,
                model_used=f"Normal(σ={fc.sigma:.1f})",
                city=market.city,
                date=market.date,
                bucket_label=market.bucket_label,
                forecast=fc.forecast,
                sigma=fc.sigma,
            )
            signals.append(signal)
            logger.log_signal(signal)

        # Compute fair prices for ALL markets including expired (for positions panel)
        for market in self.polymarket._all_markets_map.values():
            if market.market_slug in self._fair_prices:
                continue  # already computed above
            fc = self.forecast.get_forecast(market.city, market.date, market.unit)
            if not fc:
                continue
            fair = bucket_fair_price(
                fc.forecast, fc.sigma, market.bucket_lower, market.bucket_upper,
                df=fc.df,
            )
            self._fair_prices[market.market_slug] = fair

        # Sort by edge descending
        signals.sort(key=lambda s: -s.edge)

        if progress_callback:
            progress_callback(f"Found {len(signals)} opportunities")

        return signals

    def scan_for_exits(self, positions: list,
                       current_prices: Dict[str, float]) -> List[Signal]:
        """Scan for SELL signals (forecast changed, edge gone).

        For weather, this is rare — we mostly hold to resolution.
        Only sell if new forecast makes our position negative edge.
        """
        logger = get_logger()
        signals: List[Signal] = []

        for pos in positions:
            fair = self._fair_prices.get(pos.market_slug)
            if fair is None:
                continue

            current = current_prices.get(pos.market_slug, pos.entry_price)

            # Edge gone: fair price dropped below market price
            # This means our YES position is now overpriced
            edge_now = fair - current
            if edge_now < -0.03:  # negative edge > 3%
                signal = Signal(
                    type=SignalType.SELL,
                    market_id=pos.market_id,
                    market_slug=pos.market_slug,
                    market_name=pos.market_name,
                    outcome=pos.outcome,
                    current_price=current,
                    fair_price=fair,
                    edge=edge_now,
                    position_id=pos.id,
                    reason="forecast_changed",
                    token_id=pos.token_id or "",
                    city=pos.city,
                    date=pos.date,
                    bucket_label=pos.bucket_label,
                )
                signals.append(signal)
                logger.log_signal(signal)

        return signals

    def get_current_prices(self) -> Dict[str, float]:
        """Get current YES prices for all loaded markets (including expired)."""
        return {slug: m.yes_price
                for slug, m in self.polymarket._all_markets_map.items()
                if m.yes_price > 0}

    def get_fair_prices(self) -> Dict[str, float]:
        """Get cached fair prices from last scan."""
        return dict(self._fair_prices)

    def get_forecast_cache_info(self) -> Dict[str, Optional[float]]:
        """Get cache age in minutes per city. None = not cached."""
        return {city: self.forecast.cache_age(city)
                for city in self.forecast.cities}

    def get_cached_forecasts(self) -> Dict[str, dict]:
        """Get all cached forecast data for display.

        Returns {city: {date: {forecast, sigma, models}}} for cached cities.
        """
        result = {}
        for city, entry in self.forecast._cache.items():
            if entry and "data" in entry:
                result[city] = {
                    "unit": entry.get("unit", "F"),
                    "dates": entry["data"],
                }
        return result
