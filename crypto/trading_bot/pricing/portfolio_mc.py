"""
Portfolio outcome distribution via correlated MC simulation.

Simulates correlated BTC+ETH Student-t price paths, checks which
touch barriers are hit for each open position, and computes the
probability distribution of portfolio P&L at expiration.

Uses hybrid model: Student-t for dip (touch-below), GBM for reach (touch-above).
"""

import math
import time
from dataclasses import dataclass
from typing import Dict, List, Tuple

import numpy as np


@dataclass
class PositionSpec:
    """Simplified position for MC simulation."""
    slug: str
    currency: str          # "BTC" or "ETH"
    strike: float
    is_up: bool            # True = touch-above (reach), False = touch-below (dip)
    outcome: str           # "YES" or "NO"
    entry_size: float      # $ invested
    tokens: float          # tokens held
    days_remaining: float


@dataclass
class PortfolioOutcome:
    """Result of portfolio MC simulation."""
    percentiles: Dict[int, float]   # {5: -120, 25: -40, 50: 10, 75: 80, 95: 200}
    mean_pnl: float
    median_pnl: float
    win_prob: float                 # P(total P&L > 0)
    expected_value: float           # mean final portfolio value
    n_paths: int
    compute_time_ms: float


# BTC-ETH historical correlation (~0.7)
DEFAULT_CORRELATION = 0.70


def simulate_portfolio_outcomes(
    positions: List[PositionSpec],
    btc_spot: float,
    eth_spot: float,
    btc_iv: float,
    eth_iv: float,
    btc_drift: float = 0.0,
    eth_drift: float = 0.0,
    btc_df: float = 2.61,
    eth_df: float = 2.88,
    correlation: float = DEFAULT_CORRELATION,
    n_paths: int = 100_000,
    balance: float = 0.0,
) -> PortfolioOutcome:
    """Simulate portfolio P&L distribution at expiration.

    Generates correlated BTC+ETH paths with hybrid model:
    - Dip positions: Student-t paths (fat tails)
    - Reach positions: GBM paths (normal tails)

    Returns percentile distribution of portfolio outcomes.
    """
    t0 = time.monotonic()

    if not positions:
        return PortfolioOutcome(
            percentiles={p: 0.0 for p in [5, 10, 25, 50, 75, 90, 95]},
            mean_pnl=0.0, median_pnl=0.0, win_prob=0.5,
            expected_value=balance, n_paths=0, compute_time_ms=0.0,
        )

    max_days = max(int(math.ceil(p.days_remaining)) for p in positions)
    max_days = max(max_days, 1)

    # Generate correlated paths (both Student-t and GBM)
    paths = _generate_correlated_paths(
        btc_spot, eth_spot, btc_iv, eth_iv,
        btc_drift, eth_drift, btc_df, eth_df,
        correlation, max_days, n_paths,
    )

    # For each position, compute payout per MC path
    total_pnl = np.zeros(n_paths)
    total_cost = 0.0

    for pos in positions:
        days = max(int(math.ceil(pos.days_remaining)), 1)
        day_idx = min(days - 1, max_days - 1)

        # Hybrid: dip uses Student-t, reach uses GBM
        if pos.currency == "BTC":
            if pos.is_up:
                path_max = paths["btc_gbm_max"][:, day_idx]
                path_min = paths["btc_gbm_min"][:, day_idx]
            else:
                path_max = paths["btc_t_max"][:, day_idx]
                path_min = paths["btc_t_min"][:, day_idx]
        else:
            if pos.is_up:
                path_max = paths["eth_gbm_max"][:, day_idx]
                path_min = paths["eth_gbm_min"][:, day_idx]
            else:
                path_max = paths["eth_t_max"][:, day_idx]
                path_min = paths["eth_t_min"][:, day_idx]

        # Did barrier get touched?
        if pos.is_up:
            touched = path_max >= pos.strike
        else:
            touched = path_min <= pos.strike

        # Payout: YES+touched → tokens, YES+not → 0, NO+touched → 0, NO+not → tokens
        if pos.outcome == "YES":
            payout = np.where(touched, pos.tokens, 0.0)
        else:
            payout = np.where(~touched, pos.tokens, 0.0)

        total_pnl += (payout - pos.entry_size)
        total_cost += pos.entry_size

    pct_keys = [5, 10, 25, 50, 75, 90, 95]
    pct_values = np.percentile(total_pnl, pct_keys)
    percentiles = dict(zip(pct_keys, [float(v) for v in pct_values]))

    elapsed_ms = (time.monotonic() - t0) * 1000

    return PortfolioOutcome(
        percentiles=percentiles,
        mean_pnl=float(np.mean(total_pnl)),
        median_pnl=float(np.median(total_pnl)),
        win_prob=float(np.mean(total_pnl > 0)),
        expected_value=balance + total_cost + float(np.mean(total_pnl)),
        n_paths=n_paths,
        compute_time_ms=elapsed_ms,
    )


def _generate_correlated_paths(
    btc_spot: float, eth_spot: float,
    btc_iv: float, eth_iv: float,
    btc_drift: float, eth_drift: float,
    btc_df: float, eth_df: float,
    correlation: float,
    n_days: int, n_paths: int,
) -> Dict[str, np.ndarray]:
    """Generate correlated BTC+ETH price paths — both Student-t and GBM.

    Correlation via correlated standard normals (Cholesky).
    Student-t: z * sqrt(df / chi2(df)) for fat tails.
    GBM: same correlated normals, normal tails.

    Returns dict with running max/min for each variant:
        btc_t_max, btc_t_min, btc_gbm_max, btc_gbm_min,
        eth_t_max, eth_t_min, eth_gbm_max, eth_gbm_min
    Each shape: (n_paths, n_days)
    """
    rng = np.random.default_rng()
    T = n_days / 365
    dt = T / n_days

    # Correlated standard normals via Cholesky
    z_btc = rng.standard_normal((n_paths, n_days))
    z_ind = rng.standard_normal((n_paths, n_days))
    rho = max(-0.99, min(0.99, correlation))
    z_eth = rho * z_btc + math.sqrt(1 - rho ** 2) * z_ind

    # Student-t innovations: z * sqrt(df / chi2)
    chi2_btc = rng.chisquare(btc_df, size=(n_paths, n_days))
    chi2_eth = rng.chisquare(eth_df, size=(n_paths, n_days))
    t_btc = z_btc * np.sqrt(btc_df / chi2_btc)
    t_eth = z_eth * np.sqrt(eth_df / chi2_eth)

    # IV scaling
    def t_scale(iv, df):
        t_var = df / (df - 2) if df > 2 else 10.0
        return iv * math.sqrt(dt / t_var)

    gbm_scale_btc = btc_iv * math.sqrt(dt)
    gbm_scale_eth = eth_iv * math.sqrt(dt)
    t_scale_btc = t_scale(btc_iv, btc_df)
    t_scale_eth = t_scale(eth_iv, eth_df)

    # Drift per step
    btc_drift_step = (btc_drift - 0.5 * btc_iv ** 2) * dt
    eth_drift_step = (eth_drift - 0.5 * eth_iv ** 2) * dt

    # Build log-return paths
    btc_t_lr = btc_drift_step + t_btc * t_scale_btc
    btc_gbm_lr = btc_drift_step + z_btc * gbm_scale_btc
    eth_t_lr = eth_drift_step + t_eth * t_scale_eth
    eth_gbm_lr = eth_drift_step + z_eth * gbm_scale_eth

    # Cumulative → prices
    def to_prices_and_extremes(spot, log_returns):
        prices = spot * np.exp(np.cumsum(log_returns, axis=1))
        return (
            np.maximum.accumulate(prices, axis=1),
            np.minimum.accumulate(prices, axis=1),
        )

    btc_t_max, btc_t_min = to_prices_and_extremes(btc_spot, btc_t_lr)
    btc_gbm_max, btc_gbm_min = to_prices_and_extremes(btc_spot, btc_gbm_lr)
    eth_t_max, eth_t_min = to_prices_and_extremes(eth_spot, eth_t_lr)
    eth_gbm_max, eth_gbm_min = to_prices_and_extremes(eth_spot, eth_gbm_lr)

    return {
        "btc_t_max": btc_t_max, "btc_t_min": btc_t_min,
        "btc_gbm_max": btc_gbm_max, "btc_gbm_min": btc_gbm_min,
        "eth_t_max": eth_t_max, "eth_t_min": eth_t_min,
        "eth_gbm_max": eth_gbm_max, "eth_gbm_min": eth_gbm_min,
    }


def positions_to_specs(
    positions: list,
    crypto_markets: list,
) -> List[PositionSpec]:
    """Map Position objects to PositionSpec using CryptoMarket data."""
    market_map = {m.slug: m for m in crypto_markets}

    specs = []
    for pos in positions:
        cm = market_map.get(pos.market_slug)
        if cm is None:
            continue

        specs.append(PositionSpec(
            slug=pos.market_slug,
            currency=cm.currency,
            strike=cm.strike,
            is_up=cm.is_up,
            outcome=pos.outcome,
            entry_size=pos.entry_size,
            tokens=pos.tokens,
            days_remaining=cm.days_remaining,
        ))

    return specs
