#!/usr/bin/env python3
"""Smart Money скан политических рынков Polymarket.

Параллельный анализ топовых политических событий.
Результат: MD отчёт с ранжированием по edge.

Usage:
    python politics/smart_money_scan.py [--top-events 25] [--holders 20] [--workers 6]
"""

import argparse
import json
import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import httpx
from tqdm import tqdm

# === Config ===
GAMMA_API = "https://gamma-api.polymarket.com"
DATA_API = "https://data-api.polymarket.com"
CACHE_PATH = Path("/tmp/pm_trader_cache.json")
CACHE_TTL = 3600
SHRINKAGE_K = 30
MAX_WEIGHT_SHARE = 0.15  # макс. доля одного трейдера в сигнале
MIN_VOLUME = 100_000  # минимум $100k объёма
MIN_LIQUIDITY = 5_000  # минимум $5k ликвидности


@dataclass
class MarketResult:
    event_title: str
    event_slug: str
    market_title: str
    condition_id: str
    yes_price: float
    no_price: float
    volume: float
    liquidity: float
    flow: float
    flow_label: str
    smart_implied: float
    edge: float
    yes_weight: float
    no_weight: float
    top_yes: list = field(default_factory=list)
    top_no: list = field(default_factory=list)
    n_holders: int = 0


# === Cache ===
def load_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            data = json.loads(CACHE_PATH.read_text())
            if time.time() - data.get("_ts", 0) < CACHE_TTL:
                return data
        except:
            pass
    return {"_ts": time.time()}


def save_cache(cache: dict):
    cache["_ts"] = time.time()
    CACHE_PATH.write_text(json.dumps(cache, default=str))


# === API ===
def fetch_political_events(limit: int = 25) -> list[dict]:
    """Получить топовые политические события."""
    all_events = []
    offset = 0
    pbar = tqdm(desc="Загрузка событий", unit=" events")

    while True:
        r = httpx.get(f"{GAMMA_API}/events", params={
            "active": True, "closed": False, "limit": 100, "offset": offset, "tag": "politics"
        }, timeout=30)
        r.raise_for_status()
        events = r.json()
        if not events:
            break

        for ev in events:
            tags = [t.get("label", "").lower() for t in ev.get("tags", [])]
            is_political = any(t in (
                "politics", "us politics", "elections", "government",
                "world politics", "trump", "congress", "geopolitics"
            ) for t in tags)
            if not is_political:
                continue

            vol = float(ev.get("volume", 0) or 0)
            if vol < MIN_VOLUME:
                continue

            markets = []
            for m in ev.get("markets", []):
                if not m.get("active") or m.get("closed"):
                    continue
                try:
                    prices = json.loads(m.get("outcomePrices", "[]"))
                    yes_p = float(prices[0]) if prices else 0
                    no_p = float(prices[1]) if len(prices) > 1 else 1 - yes_p
                    if yes_p >= 0.97 or yes_p <= 0.03:
                        continue
                    clob_ids = json.loads(m.get("clobTokenIds", "[]"))
                    mkt_vol = float(m.get("volume", 0) or 0)
                    mkt_liq = float(m.get("liquidity", 0) or 0)
                    if mkt_liq < MIN_LIQUIDITY:
                        continue
                    markets.append({
                        "title": m.get("groupItemTitle", m.get("question", "?")),
                        "question": m.get("question", "?"),
                        "condition_id": m.get("conditionId", ""),
                        "yes_price": yes_p,
                        "no_price": no_p,
                        "volume": mkt_vol,
                        "liquidity": mkt_liq,
                    })
                except:
                    continue

            if markets:
                all_events.append({
                    "title": ev.get("title", "?"),
                    "slug": ev.get("slug", ""),
                    "volume": vol,
                    "markets": markets,
                })

        pbar.update(len(events))
        offset += 100
        if len(events) < 100:
            break

    pbar.close()

    # Sort by volume, take top N
    all_events.sort(key=lambda x: x["volume"], reverse=True)
    selected = all_events[:limit]
    total_markets = sum(len(e["markets"]) for e in selected)
    print(f"\nОтобрано: {len(selected)} событий, {total_markets} рынков")
    return selected


def fetch_holders(condition_id: str, limit: int = 20) -> list[dict]:
    """Получить холдеров рынка."""
    r = httpx.get(f"{DATA_API}/holders", params={
        "market": condition_id, "limit": limit
    }, timeout=30)
    r.raise_for_status()
    holders = []
    for token_group in r.json():
        for h in token_group.get("holders", []):
            holders.append({
                "wallet": h.get("proxyWallet", ""),
                "name": h.get("pseudonym") or h.get("name") or h.get("proxyWallet", "")[:16],
                "amount": float(h.get("amount", 0)),
                "side": "YES" if h.get("outcomeIndex", 0) == 0 else "NO",
            })
    return holders


def fetch_trader_stats(wallet: str, cache: dict) -> dict:
    """Получить P&L трейдера (с кешированием, thread-safe read)."""
    cache_key = f"trader_{wallet}"
    if cache_key in cache:
        return cache[cache_key]

    stats = {
        "wallet": wallet,
        "total_profit": 0, "realized_pnl": 0, "unrealized_pnl": 0,
        "total_volume": 0, "n_trades": 0, "portfolio_value": 0,
        "categories": {},
    }

    try:
        r = httpx.get(f"{DATA_API}/positions", params={
            "user": wallet, "limit": 200, "sortBy": "CURRENT", "sortDir": "desc"
        }, timeout=30)
        r.raise_for_status()
        for p in r.json():
            stats["unrealized_pnl"] += float(p.get("cashPnl", 0) or 0)
            stats["total_volume"] += float(p.get("totalBought", 0) or 0)
            stats["portfolio_value"] += float(p.get("currentValue", 0) or 0)
            title = p.get("title", "").lower()
            if any(kw in title for kw in ("trump", "biden", "election", "president", "congress", "senate")):
                stats["categories"]["politics"] = stats["categories"].get("politics", 0) + 1
            elif any(kw in title for kw in ("bitcoin", "btc", "crypto", "ethereum")):
                stats["categories"]["crypto"] = stats["categories"].get("crypto", 0) + 1
            else:
                stats["categories"]["other"] = stats["categories"].get("other", 0) + 1
        stats["n_trades"] += len(r.json())
    except:
        pass

    try:
        r = httpx.get(f"{DATA_API}/closed-positions", params={
            "user": wallet, "limit": 200
        }, timeout=30)
        r.raise_for_status()
        for p in r.json():
            stats["realized_pnl"] += float(p.get("realizedPnl", 0) or 0)
            stats["total_volume"] += float(p.get("totalBought", 0) or 0)
        stats["n_trades"] += len(r.json())
    except:
        pass

    stats["total_profit"] = stats["realized_pnl"] + stats["unrealized_pnl"]
    cache[cache_key] = stats
    return stats


def analyze_single_market(market: dict, event: dict, holder_limit: int,
                          cache: dict, workers: int) -> MarketResult | None:
    """Анализ одного рынка — Smart Money Flow."""
    cid = market["condition_id"]

    try:
        holders = fetch_holders(cid, limit=holder_limit)
    except Exception:
        return None

    if len(holders) < 4:
        return None

    # Fetch trader stats in parallel
    wallets = list({h["wallet"] for h in holders if h["wallet"]})

    def _fetch(w):
        return w, fetch_trader_stats(w, cache)

    trader_stats = {}
    with ThreadPoolExecutor(max_workers=min(workers, len(wallets))) as ex:
        futures = {ex.submit(_fetch, w): w for w in wallets}
        for f in as_completed(futures):
            try:
                w, st = f.result()
                trader_stats[w] = st
            except:
                pass

    # Score holders
    scored = []
    for h in holders:
        st = trader_stats.get(h["wallet"])
        if not st:
            continue

        price = market["yes_price"] if h["side"] == "YES" else market["no_price"]
        pos_value = h["amount"] * price
        portfolio = max(st["portfolio_value"], 1.0)
        conviction = min(pos_value / portfolio, 1.0)
        shrinkage = st["n_trades"] / (st["n_trades"] + SHRINKAGE_K)

        # Politics bonus
        total_cats = sum(st["categories"].values()) or 1
        politics_share = st["categories"].get("politics", 0) / total_cats

        # [v2] Log-scale profit — сжимаем outliers
        profit_sign = 1.0 if st["total_profit"] >= 0 else -1.0
        log_profit = profit_sign * math.log1p(abs(st["total_profit"]))

        # [v2] ROI multiplier — награда за скилл
        roi = st["total_profit"] / max(st["total_volume"], 1)
        roi_mult = 1.0 + min(max(roi, -0.5), 2.0)

        # [v2] Штраф за unrealized losses
        health = 1.0
        if st["unrealized_pnl"] < 0 and st["realized_pnl"] > 0:
            health = max(0.2, 1.0 + st["unrealized_pnl"] / (abs(st["realized_pnl"]) + 1))

        weight = log_profit * roi_mult * health * conviction * shrinkage
        if politics_share > 0.3:
            weight *= 1.0 + politics_share * 0.5

        side_sign = 1.0 if h["side"] == "YES" else -1.0

        scored.append({
            "name": h["name"],
            "side": h["side"],
            "tokens": h["amount"],
            "profit": st["total_profit"],
            "roi": roi * 100,
            "trades": st["n_trades"],
            "weight": abs(weight),
            "signed_weight": weight * side_sign,
        })

    if not scored:
        return None

    # [v2] Кап: ни один трейдер не даёт больше MAX_WEIGHT_SHARE сигнала
    total_abs = sum(abs(s["signed_weight"]) for s in scored) or 1
    cap = MAX_WEIGHT_SHARE * total_abs
    for s in scored:
        if abs(s["signed_weight"]) > cap:
            s["signed_weight"] = math.copysign(cap, s["signed_weight"])
            s["weight"] = cap

    total_abs = sum(abs(s["signed_weight"]) for s in scored) or 1
    flow = sum(s["signed_weight"] for s in scored) / total_abs

    yes_w = sum(s["weight"] for s in scored if s["side"] == "YES")
    no_w = sum(s["weight"] for s in scored if s["side"] == "NO")
    total_w = yes_w + no_w
    implied = yes_w / total_w if total_w > 0 else 0.5

    flow_label = ("STRONG YES" if flow > 0.3 else "YES" if flow > 0.1
                  else "NEUTRAL" if flow > -0.1 else "NO" if flow > -0.3
                  else "STRONG NO")

    edge = implied - market["yes_price"]

    top_yes = sorted([s for s in scored if s["side"] == "YES"],
                     key=lambda x: abs(x["weight"]), reverse=True)[:3]
    top_no = sorted([s for s in scored if s["side"] == "NO"],
                    key=lambda x: abs(x["weight"]), reverse=True)[:3]

    return MarketResult(
        event_title=event["title"],
        event_slug=event["slug"],
        market_title=market["title"],
        condition_id=cid,
        yes_price=market["yes_price"],
        no_price=market["no_price"],
        volume=market["volume"],
        liquidity=market["liquidity"],
        flow=flow,
        flow_label=flow_label,
        smart_implied=implied,
        edge=edge,
        yes_weight=yes_w,
        no_weight=no_w,
        top_yes=top_yes,
        top_no=top_no,
        n_holders=len(holders),
    )


def generate_report(results: list[MarketResult]) -> str:
    """Генерация MD отчёта."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = [
        f"# Smart Money Analysis — Political Markets ({now})",
        "",
        "## Методология",
        "",
        "Conviction-weighted Smart Money Flow по топ-холдерам каждого рынка.",
        "**Weight** = `log(profit) × ROI_mult × health × conviction × shrinkage × politics_bonus`",
        "**Edge** = Smart Implied - PM Price (положительный = YES недооценён)",
        "",
    ]

    # Sort by absolute edge
    by_edge = sorted(results, key=lambda r: abs(r.edge), reverse=True)

    # === TOP OPPORTUNITIES ===
    lines.append("## TOP-20 Opportunities (по абсолютному edge)")
    lines.append("")
    lines.append("| # | Событие | Рынок | PM Price | Smart $ | Edge | Flow | Ликв. |")
    lines.append("|---|---------|-------|----------|---------|------|------|-------|")

    for i, r in enumerate(by_edge[:20], 1):
        side = "YES" if r.edge > 0 else "NO"
        edge_str = f"**{r.edge:+.1%}** {side}"
        lines.append(
            f"| {i} | {r.event_title[:30]} | {r.market_title[:25]} | "
            f"{r.yes_price:.0%} | {r.smart_implied:.0%} | {edge_str} | "
            f"{r.flow:+.2f} {r.flow_label} | ${r.liquidity:,.0f} |"
        )

    # === BEST YES BETS ===
    yes_bets = sorted([r for r in results if r.edge > 0.05], key=lambda r: r.edge, reverse=True)
    lines.append("")
    lines.append(f"## Best YES Bets (edge > +5%, {len(yes_bets)} found)")
    lines.append("")
    if yes_bets:
        lines.append("| Событие | Рынок | PM | Smart $ | Edge | Flow | Vol | Liq |")
        lines.append("|---------|-------|----|---------|------|------|-----|-----|")
        for r in yes_bets[:30]:
            lines.append(
                f"| {r.event_title[:30]} | {r.market_title[:25]} | "
                f"{r.yes_price:.0%} | {r.smart_implied:.0%} | {r.edge:+.1%} | "
                f"{r.flow:+.2f} | ${r.volume:,.0f} | ${r.liquidity:,.0f} |"
            )

    # === BEST NO BETS ===
    no_bets = sorted([r for r in results if r.edge < -0.05], key=lambda r: r.edge)
    lines.append("")
    lines.append(f"## Best NO Bets (edge < -5%, {len(no_bets)} found)")
    lines.append("")
    if no_bets:
        lines.append("| Событие | Рынок | PM YES | Smart $ | Edge (buy NO) | Flow | Vol | Liq |")
        lines.append("|---------|-------|--------|---------|---------------|------|-----|-----|")
        for r in no_bets[:30]:
            no_edge = -r.edge
            lines.append(
                f"| {r.event_title[:30]} | {r.market_title[:25]} | "
                f"{r.yes_price:.0%} | {r.smart_implied:.0%} | +{no_edge:.1%} | "
                f"{r.flow:+.2f} | ${r.volume:,.0f} | ${r.liquidity:,.0f} |"
            )

    # === BY EVENT ===
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Детальный анализ по событиям")
    lines.append("")

    events_map: dict[str, list[MarketResult]] = {}
    for r in results:
        events_map.setdefault(r.event_slug, []).append(r)

    # Sort events by max edge
    event_list = sorted(events_map.items(),
                        key=lambda x: max(abs(r.edge) for r in x[1]), reverse=True)

    for slug, mkts in event_list:
        ev_title = mkts[0].event_title
        lines.append(f"### {ev_title}")
        lines.append("")
        lines.append(f"| Рынок | PM | Smart $ | Edge | Flow | Top YES | Top NO |")
        lines.append(f"|-------|----|---------|------|------|---------|--------|")

        for r in sorted(mkts, key=lambda x: abs(x.edge), reverse=True):
            top_y = r.top_yes[0]["name"][:15] if r.top_yes else "—"
            top_n = r.top_no[0]["name"][:15] if r.top_no else "—"
            lines.append(
                f"| {r.market_title[:25]} | {r.yes_price:.0%} | "
                f"{r.smart_implied:.0%} | {r.edge:+.1%} | "
                f"{r.flow:+.2f} | {top_y} | {top_n} |"
            )
        lines.append("")

    # === STATS ===
    lines.append("---")
    lines.append("")
    lines.append("## Статистика")
    lines.append("")
    lines.append(f"- Проанализировано событий: {len(events_map)}")
    lines.append(f"- Проанализировано рынков: {len(results)}")
    lines.append(f"- Рынков с edge > +5%: {len(yes_bets)}")
    lines.append(f"- Рынков с edge < -5%: {len(no_bets)}")
    avg_abs_edge = sum(abs(r.edge) for r in results) / len(results) if results else 0
    lines.append(f"- Средний |edge|: {avg_abs_edge:.1%}")
    lines.append("")
    lines.append(f"*Отчёт сгенерирован: {now}. Данные: Polymarket Data API.*")
    lines.append(f"*Методология: conviction-weighted Smart Money Flow (politics bonus +50%).*")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Smart Money скан политических рынков")
    parser.add_argument("--top-events", type=int, default=25, help="Количество топ-событий (по умолчанию 25)")
    parser.add_argument("--holders", type=int, default=20, help="Холдеров на сторону (по умолчанию 20)")
    parser.add_argument("--workers", type=int, default=6, help="Параллельных потоков (по умолчанию 6)")
    parser.add_argument("--output", type=str, default=None, help="Путь для MD отчёта")
    args = parser.parse_args()

    print(f"=== Smart Money Political Scan ===")
    print(f"Top events: {args.top_events} | Holders: {args.holders} | Workers: {args.workers}")
    print()

    # 1. Загрузка событий
    events = fetch_political_events(limit=args.top_events)

    # 2. Подсчёт всех рынков
    all_markets = []
    for ev in events:
        for m in ev["markets"]:
            all_markets.append((ev, m))

    print(f"\nВсего рынков для анализа: {len(all_markets)}")
    cache = load_cache()

    # 3. Анализ каждого рынка
    results: list[MarketResult] = []
    pbar = tqdm(total=len(all_markets), desc="Анализ рынков", unit=" mkt")

    for ev, mkt in all_markets:
        try:
            result = analyze_single_market(mkt, ev, args.holders, cache, args.workers)
            if result:
                results.append(result)
        except Exception as e:
            pass  # skip broken markets
        pbar.update(1)

        # Save cache periodically
        if pbar.n % 20 == 0:
            save_cache(cache)

    pbar.close()
    save_cache(cache)

    print(f"\nУспешно проанализировано: {len(results)} рынков")

    # 4. Генерация отчёта
    report = generate_report(results)

    output_path = args.output or f"politics/smart_money_politics_{datetime.now().strftime('%Y-%m-%d')}.md"
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(report)
    print(f"\nОтчёт сохранён: {output_path}")

    # 5. Краткий вывод в консоль
    by_edge = sorted(results, key=lambda r: abs(r.edge), reverse=True)
    print(f"\n{'='*80}")
    print(f"TOP-10 по edge:")
    print(f"{'='*80}")
    for i, r in enumerate(by_edge[:10], 1):
        side = "BUY YES" if r.edge > 0 else "BUY NO"
        print(f"  {i:>2}. {r.event_title[:35]:<35} {r.market_title[:20]:<20} "
              f"PM:{r.yes_price:>4.0%} Smart:{r.smart_implied:>4.0%} "
              f"Edge:{r.edge:>+5.1%} → {side}")


if __name__ == "__main__":
    main()
