"""Skill scoring for traders based on track record."""

import math
from . import db


def compute_skill_score(address: str) -> float:
    """Compute skill score for a trader.

    Based on data available from Polymarket API:
    - realized_pnl: total profit from closed positions
    - total_markets: number of unique markets traded
    - avg_roi: average return on investment (%)
    - total_invested: total capital deployed

    Score formula:
      score = profitability × experience × roi_factor × size_factor

    Higher = more skilled and consistent trader.
    """
    trader = db.get_trader(address)
    if not trader:
        return 0.0

    total_markets = trader["total_markets"]
    realized_pnl = trader["realized_pnl"]
    avg_roi = trader["avg_roi"]
    total_invested = trader["total_invested"]

    if total_markets < 5 or total_invested < 10:
        return 0.0  # not enough data

    # 1. Profitability: log-scaled realized PnL (handles outliers)
    if realized_pnl <= 0:
        return 0.0  # only score profitable traders
    profitability = math.log1p(realized_pnl)

    # 2. Experience: log(1 + markets) — more markets = more reliable signal
    experience = math.log1p(total_markets)

    # 3. ROI factor: reward high ROI, penalize negative
    # avg_roi is in percent, e.g. 50 = 50%
    roi_pct = avg_roi / 100.0  # convert to fraction
    roi_factor = max(0.1, min(3.0, 1.0 + roi_pct))

    # 4. Size factor: prefer $100-$10k invested (not dust, not whales)
    # Traders with $100-$10k get full credit, below/above get penalized
    if total_invested < 50:
        size_factor = 0.3
    elif total_invested < 500:
        size_factor = 0.7
    elif total_invested < 50000:
        size_factor = 1.0
    else:
        size_factor = 0.8  # slight whale penalty

    score = profitability * experience * roi_factor * size_factor

    return round(score, 4)


def score_all_traders(verbose: bool = True) -> int:
    """Recompute skill scores for all traders. Returns count scored."""
    with db.get_cursor(commit=False) as cur:
        cur.execute("""
            SELECT address, alias, total_markets
            FROM traders
            WHERE total_markets >= 5 AND realized_pnl > 0
        """)
        traders = cur.fetchall()

    if verbose:
        print(f"Scoring {len(traders)} profitable traders (5+ markets)...")

    scored = 0
    for t in traders:
        score = compute_skill_score(t["address"])
        db.update_trader_stats(t["address"], skill_score=score)
        scored += 1

    if verbose:
        top = db.get_top_traders(limit=30, exclude_mm=True)
        print(f"\nTop 30 skilled traders (non-MM):")
        print(f"  {'#':<4} {'Alias':<25} {'Score':>7} {'Mkts':>6} {'Invested':>10} {'PnL':>12} {'ROI':>8}")
        print(f"  {'-'*80}")
        for i, t in enumerate(top):
            name = t["alias"] or t["address"][:16]
            print(f"  {i+1:<4} {name[:25]:<25} {t['skill_score']:>7.2f} {t['total_markets']:>6} "
                  f"${t['total_invested']:>9,.0f} ${t['realized_pnl']:>+11,.0f} {t['avg_roi']:>+7.1f}%")

    return scored
