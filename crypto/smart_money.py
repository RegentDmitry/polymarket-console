#!/usr/bin/env python3
"""Smart Money Analyzer для Polymarket.

Анализирует крупнейших холдеров любого рынка, оценивает их историческую
прибыльность и рассчитывает conviction-weighted Smart Money Flow.

Использование:
  python crypto/smart_money.py what-price-will-bitcoin-hit-before-2027
  python crypto/smart_money.py https://polymarket.com/event/some-slug
  python crypto/smart_money.py what-price-will-bitcoin-hit-before-2027 --market "120,000"
  python crypto/smart_money.py what-price-will-bitcoin-hit-before-2027 --top 10
"""

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

# === Config ===
GAMMA_API = "https://gamma-api.polymarket.com"
DATA_API = "https://data-api.polymarket.com"
CACHE_PATH = Path("/tmp/pm_trader_cache.json")
CACHE_TTL = 3600  # 1 час
REQUEST_TIMEOUT = 30
HOLDER_LIMIT = 30  # топ холдеров на сторону
SHRINKAGE_K = 30   # байесовский порог доверия


# === Data Models ===

@dataclass
class Holder:
    wallet: str
    name: str
    amount: float  # токенов
    side: str  # "YES" or "NO"
    outcome_index: int


@dataclass
class TraderStats:
    wallet: str
    name: str
    total_profit: float = 0.0  # realized + unrealized
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    total_volume: float = 0.0
    n_open_positions: int = 0
    n_closed_positions: int = 0
    n_total_trades: int = 0
    portfolio_value: float = 0.0
    categories: dict = field(default_factory=dict)  # tag -> count


@dataclass
class MarketInfo:
    title: str
    condition_id: str
    yes_price: float
    no_price: float
    volume: float
    liquidity: float
    yes_token_id: str
    no_token_id: str


# === Cache ===

def load_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            data = json.loads(CACHE_PATH.read_text())
            if time.time() - data.get("_ts", 0) < CACHE_TTL:
                return data
        except (json.JSONDecodeError, KeyError):
            pass
    return {"_ts": time.time()}


def save_cache(cache: dict):
    cache["_ts"] = time.time()
    CACHE_PATH.write_text(json.dumps(cache, default=str))


# === API Functions ===

def fetch_event(slug: str) -> dict:
    """Получить событие и все его рынки."""
    resp = httpx.get(
        f"{GAMMA_API}/events",
        params={"slug": slug},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data:
        print(f"Событие '{slug}' не найдено")
        sys.exit(1)
    return data[0]


def parse_markets(event: dict) -> list[MarketInfo]:
    """Распарсить рынки из события."""
    markets = []
    for m in event.get("markets", []):
        try:
            outcome_prices = json.loads(m.get("outcomePrices", "[]"))
            yes_price = float(outcome_prices[0]) if outcome_prices else 0
            no_price = float(outcome_prices[1]) if len(outcome_prices) > 1 else 1 - yes_price

            clob_ids = json.loads(m.get("clobTokenIds", "[]"))
            yes_token = clob_ids[0] if clob_ids else ""
            no_token = clob_ids[1] if len(clob_ids) > 1 else ""

            if yes_price >= 0.98 or yes_price <= 0.005:
                continue

            markets.append(MarketInfo(
                title=m.get("groupItemTitle", m.get("question", "?")),
                condition_id=m.get("conditionId", ""),
                yes_price=yes_price,
                no_price=no_price,
                volume=float(m.get("volume", 0) or 0),
                liquidity=float(m.get("liquidity", 0) or 0),
                yes_token_id=yes_token,
                no_token_id=no_token,
            ))
        except (json.JSONDecodeError, IndexError, ValueError):
            continue
    return markets


def fetch_holders(condition_id: str, limit: int = HOLDER_LIMIT) -> list[Holder]:
    """Получить крупнейших холдеров YES и NO."""
    resp = httpx.get(
        f"{DATA_API}/holders",
        params={"market": condition_id, "limit": limit},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()

    holders = []
    for token_group in data:
        for h in token_group.get("holders", []):
            side = "YES" if h.get("outcomeIndex", 0) == 0 else "NO"
            holders.append(Holder(
                wallet=h.get("proxyWallet", ""),
                name=h.get("pseudonym") or h.get("name", "anon"),
                amount=float(h.get("amount", 0)),
                side=side,
                outcome_index=h.get("outcomeIndex", 0),
            ))
    return holders


def fetch_trader_stats(wallet: str, cache: dict) -> TraderStats:
    """Получить статистику трейдера (с кешированием)."""
    cache_key = f"trader_{wallet}"
    if cache_key in cache and cache_key != "_ts":
        cached = cache[cache_key]
        return TraderStats(**cached)

    stats = TraderStats(wallet=wallet, name="")

    # Открытые позиции
    try:
        resp = httpx.get(
            f"{DATA_API}/positions",
            params={"user": wallet, "limit": 200, "sortBy": "CURRENT", "sortDir": "desc"},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        positions = resp.json()

        for p in positions:
            pnl = float(p.get("cashPnl", 0) or 0)
            stats.unrealized_pnl += pnl
            bought = float(p.get("totalBought", 0) or 0)
            stats.total_volume += bought
            current = float(p.get("currentValue", 0) or 0)
            stats.portfolio_value += current

            title = p.get("title", "").lower()
            for tag in _extract_tags(title):
                stats.categories[tag] = stats.categories.get(tag, 0) + 1

        stats.n_open_positions = len(positions)
        if positions and not stats.name:
            stats.name = wallet[:12] + "..."
    except Exception:
        pass

    # Закрытые позиции
    try:
        resp = httpx.get(
            f"{DATA_API}/closed-positions",
            params={"user": wallet, "limit": 200},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        closed = resp.json()

        for p in closed:
            rpnl = float(p.get("realizedPnl", 0) or 0)
            stats.realized_pnl += rpnl

            title = p.get("title", "").lower()
            for tag in _extract_tags(title):
                stats.categories[tag] = stats.categories.get(tag, 0) + 1

        stats.n_closed_positions = len(closed)
    except Exception:
        pass

    stats.total_profit = stats.realized_pnl + stats.unrealized_pnl
    stats.n_total_trades = stats.n_open_positions + stats.n_closed_positions

    # Кешируем
    cache[cache_key] = {
        "wallet": stats.wallet,
        "name": stats.name,
        "total_profit": stats.total_profit,
        "realized_pnl": stats.realized_pnl,
        "unrealized_pnl": stats.unrealized_pnl,
        "total_volume": stats.total_volume,
        "n_open_positions": stats.n_open_positions,
        "n_closed_positions": stats.n_closed_positions,
        "n_total_trades": stats.n_total_trades,
        "portfolio_value": stats.portfolio_value,
        "categories": stats.categories,
    }
    return stats


def _extract_tags(title: str) -> list[str]:
    """Извлечь теги из названия рынка для категоризации."""
    tags = []
    keywords = {
        "bitcoin": "crypto", "btc": "crypto", "ethereum": "crypto", "crypto": "crypto",
        "trump": "politics", "biden": "politics", "election": "politics", "president": "politics",
        "earthquake": "science", "weather": "science", "climate": "science",
        "nfl": "sports", "nba": "sports", "super bowl": "sports",
        "fed": "economics", "inflation": "economics", "gdp": "economics",
    }
    for kw, tag in keywords.items():
        if kw in title:
            tags.append(tag)
    return tags or ["other"]


# === Analysis ===

def analyze_market(market: MarketInfo, holders: list[Holder], cache: dict) -> dict:
    """Анализ одного рынка — Smart Money Flow."""
    unique_wallets = {h.wallet for h in holders if h.wallet}
    print(f"  Загрузка статистики {len(unique_wallets)} трейдеров...", end="", flush=True)

    trader_stats = {}
    for i, wallet in enumerate(unique_wallets):
        if (i + 1) % 10 == 0:
            print(f" {i+1}", end="", flush=True)
        stats = fetch_trader_stats(wallet, cache)
        trader_stats[wallet] = stats
        time.sleep(0.1)  # rate limit
    print(" done")

    # Рассчитываем веса
    scored_holders = []
    for h in holders:
        stats = trader_stats.get(h.wallet)
        if not stats:
            continue

        # Conviction: доля позиции от портфеля
        position_value = h.amount * (market.yes_price if h.side == "YES" else market.no_price)
        portfolio = max(stats.portfolio_value, 1.0)
        conviction = min(position_value / portfolio, 1.0)

        # Shrinkage: доверие к статистике
        shrinkage = stats.n_total_trades / (stats.n_total_trades + SHRINKAGE_K)

        # Профильность: crypto-рынки у холдера
        total_cats = sum(stats.categories.values()) or 1
        crypto_share = stats.categories.get("crypto", 0) / total_cats

        # Weight = profit × conviction × shrinkage
        weight = stats.total_profit * conviction * shrinkage

        # Бонус за профильность (+50% для crypto-трейдеров на crypto-рынках)
        if crypto_share > 0.3:
            weight *= 1.0 + crypto_share * 0.5

        side_sign = 1.0 if h.side == "YES" else -1.0

        scored_holders.append({
            "name": h.name,
            "wallet": h.wallet,
            "side": h.side,
            "tokens": h.amount,
            "position_value": position_value,
            "profit": stats.total_profit,
            "roi": (stats.total_profit / max(stats.total_volume, 1)) * 100,
            "trades": stats.n_total_trades,
            "conviction": conviction,
            "crypto_share": crypto_share,
            "weight": weight,
            "signed_weight": weight * side_sign,
            "portfolio_value": stats.portfolio_value,
        })

    # Smart Money Flow
    total_abs_weight = sum(abs(h["signed_weight"]) for h in scored_holders) or 1
    smart_flow = sum(h["signed_weight"] for h in scored_holders) / total_abs_weight

    # Implied price (smart money weighted)
    yes_weight = sum(h["weight"] for h in scored_holders if h["side"] == "YES")
    no_weight = sum(h["weight"] for h in scored_holders if h["side"] == "NO")
    total_weight = yes_weight + no_weight
    smart_implied = yes_weight / total_weight if total_weight > 0 else 0.5

    return {
        "market": market,
        "holders": sorted(scored_holders, key=lambda x: abs(x["weight"]), reverse=True),
        "smart_flow": smart_flow,
        "smart_implied": smart_implied,
        "yes_weight": yes_weight,
        "no_weight": no_weight,
    }


# === Output ===

def print_market_analysis(result: dict):
    """Вывести результат анализа рынка."""
    market = result["market"]
    holders = result["holders"]
    flow = result["smart_flow"]
    implied = result["smart_implied"]

    print(f"\n{'='*75}")
    print(f"  {market.title}  |  YES: {market.yes_price:.0%}  |  Vol: ${market.volume:,.0f}  |  Liq: ${market.liquidity:,.0f}")
    print(f"{'='*75}")

    # YES holders
    yes_holders = [h for h in holders if h["side"] == "YES"]
    no_holders = [h for h in holders if h["side"] == "NO"]

    for label, group in [("YES HOLDERS (топ по весу)", yes_holders), ("NO HOLDERS (топ по весу)", no_holders)]:
        if not group:
            continue
        print(f"\n  {label}:")
        print(f"  {'Имя':<22} {'Токены':>10} {'$Позиция':>10} {'Профит':>10} {'ROI':>7} {'Trades':>7} {'Crypto':>7} {'Вес':>10}")
        print(f"  {'-'*87}")
        for h in group[:8]:
            profit_str = f"{'+'if h['profit']>=0 else ''}{h['profit']:,.0f}"
            print(f"  {h['name'][:22]:<22} {h['tokens']:>10,.0f} ${h['position_value']:>9,.0f} ${profit_str:>9} {h['roi']:>+6.0f}% {h['trades']:>7} {h['crypto_share']:>6.0%} {h['weight']:>+10,.0f}")

    # Smart Money Summary
    flow_label = "STRONG YES" if flow > 0.3 else "YES" if flow > 0.1 else "NEUTRAL" if flow > -0.1 else "NO" if flow > -0.3 else "STRONG NO"
    edge = implied - market.yes_price

    print(f"\n  {'─'*75}")
    print(f"  SMART MONEY FLOW: {flow:+.2f} ({flow_label})")
    print(f"  PM Price:       {market.yes_price:.0%}")
    print(f"  Smart Implied:  {implied:.0%}")
    print(f"  Edge:           {edge:+.1%}")
    print(f"  YES weight: {result['yes_weight']:+,.0f}  |  NO weight: {result['no_weight']:+,.0f}")
    print()


def print_summary_table(results: list[dict]):
    """Сводная таблица по всем рынкам."""
    if len(results) <= 1:
        return

    print(f"\n{'='*90}")
    print(f"  СВОДНАЯ ТАБЛИЦА — Smart Money Flow")
    print(f"{'='*90}")
    print(f"  {'Рынок':<25} {'PM Price':>9} {'Smart $':>9} {'Edge':>7} {'Flow':>7} {'Signal':>12}")
    print(f"  {'-'*73}")

    sorted_results = sorted(results, key=lambda r: abs(r["smart_implied"] - r["market"].yes_price), reverse=True)
    for r in sorted_results:
        m = r["market"]
        edge = r["smart_implied"] - m.yes_price
        flow = r["smart_flow"]
        label = "STRONG YES" if flow > 0.3 else "YES" if flow > 0.1 else "—" if flow > -0.1 else "NO" if flow > -0.3 else "STRONG NO"
        print(f"  {m.title[:25]:<25} {m.yes_price:>8.0%} {r['smart_implied']:>8.0%} {edge:>+6.1%} {flow:>+6.2f} {label:>12}")

    print()


# === CLI ===

def parse_slug(input_str: str) -> str:
    """Извлечь slug из URL или строки."""
    if "polymarket.com" in input_str:
        match = re.search(r'/event/([^/?#]+)', input_str)
        if match:
            return match.group(1)
    return input_str.strip().strip("/")


def main():
    parser = argparse.ArgumentParser(description="Smart Money Analyzer для Polymarket")
    parser.add_argument("slug", help="Slug события или URL (например: what-price-will-bitcoin-hit-before-2027)")
    parser.add_argument("--market", "-m", help="Фильтр по названию рынка (подстрока)")
    parser.add_argument("--top", "-t", type=int, default=HOLDER_LIMIT, help=f"Количество холдеров на сторону (по умолчанию {HOLDER_LIMIT})")
    parser.add_argument("--no-cache", action="store_true", help="Игнорировать кеш")
    args = parser.parse_args()

    slug = parse_slug(args.slug)
    cache = {} if args.no_cache else load_cache()

    print(f"Загрузка события: {slug}")
    event = fetch_event(slug)
    print(f"Событие: {event.get('title', '?')}")

    markets = parse_markets(event)
    print(f"Активных рынков: {len(markets)}")

    if args.market:
        markets = [m for m in markets if args.market.lower() in m.title.lower()]
        if not markets:
            print(f"Рынок с '{args.market}' не найден")
            sys.exit(1)
        print(f"Отфильтровано: {len(markets)} рынков")

    results = []
    for i, market in enumerate(markets):
        print(f"\n[{i+1}/{len(markets)}] Анализ: {market.title}")
        try:
            holders = fetch_holders(market.condition_id, limit=args.top)
            if not holders:
                print("  Нет холдеров")
                continue
            result = analyze_market(market, holders, cache)
            results.append(result)
            print_market_analysis(result)
        except Exception as e:
            print(f"  Ошибка: {e}")
            continue

    print_summary_table(results)
    save_cache(cache)
    print(f"Кеш сохранён: {CACHE_PATH}")


if __name__ == "__main__":
    main()
