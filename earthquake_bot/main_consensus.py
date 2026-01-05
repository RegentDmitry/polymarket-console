#!/usr/bin/env python3
"""
Earthquake Trading Bot — КОНСЕНСУСНАЯ МОДЕЛЬ.

Объединяет простую и интегрированную модели:
- M7.0+: доверяем интегрированной, показываем простую для справки
- M8.0+: показываем только при консенсусе (обе модели согласны на направление)
- Если модели расходятся — возможность НЕ показывается

Использование:
    python main_consensus.py              # Режим анализа
    python main_consensus.py --debug      # Режим отладки
    python main_consensus.py --auto       # Автоматическая торговля
    python main_consensus.py --show-all   # Показать все, включая расхождения

"""

import argparse
import math
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from usgs_client import USGSClient
from polymarket_client import PolymarketClient
from markets import EARTHQUAKE_ANNUAL_RATES

# Импортируем модели
from main_integrated import (
    IntegratedModel,
    SimpleModel,
    MARKET_CONFIGS,
    MIN_EDGE,
    MIN_ANNUAL_RETURN,
    MIN_BET_USD,
    MAX_LIQUIDITY_PCT,
    KELLY_FRACTION,
    MAX_BET_PCT,
    kelly_criterion,
    get_orderbook_data,
    get_orderbook_tiers,
    M7_MEAN,
    M7_STD,
    M8_MEAN,
)


# ============================================================================
# CONSENSUS OPPORTUNITY
# ============================================================================

@dataclass
class ConsensusOpportunity:
    """Возможность с консенсусом моделей."""
    event: str
    outcome: str
    side: str  # "YES" or "NO"
    token_id: str
    condition_id: str

    # Интегрированная модель (основная для M7.0+)
    integrated_fair: float
    integrated_edge: float

    # Простая модель (для справки)
    simple_fair: float
    simple_edge: float

    # Рыночные данные
    market_price: float
    liquidity_usd: Optional[float]
    remaining_days: float

    # Метаданные
    magnitude: float
    current_count: int

    # Консенсус
    consensus: str  # "AGREE", "DISAGREE", "WEAK"
    confidence: str  # "HIGH", "MEDIUM", "LOW"

    @property
    def primary_fair(self) -> float:
        """Основная fair price (интегрированная для M7, усреднённая для M8+)."""
        if self.magnitude < 8.0:
            return self.integrated_fair
        else:
            # Для M8.0+ — среднее при консенсусе
            return (self.integrated_fair + self.simple_fair) / 2

    @property
    def primary_edge(self) -> float:
        """Основной edge."""
        return self.primary_fair - self.market_price

    @property
    def kelly(self) -> float:
        """Kelly criterion на основе primary_fair."""
        if self.market_price <= 0 or self.market_price >= 1:
            return 0.0
        odds = (1 - self.market_price) / self.market_price
        return kelly_criterion(self.primary_fair, odds)

    @property
    def expected_return(self) -> float:
        """ROI."""
        if self.market_price <= 0:
            return 0.0
        return self.primary_fair / self.market_price - 1

    @property
    def annual_return(self) -> float:
        """APY."""
        if self.remaining_days <= 0:
            return 0.0
        return self.expected_return * (365 / self.remaining_days)

    @property
    def model_diff(self) -> float:
        """Разница между моделями."""
        return abs(self.integrated_fair - self.simple_fair)

    @property
    def model_diff_pct(self) -> float:
        """Разница в процентных пунктах."""
        return self.model_diff * 100


def determine_consensus(
    integrated_fair: float,
    simple_fair: float,
    market_price: float,
    magnitude: float,
) -> tuple[str, str]:
    """
    Определить консенсус между моделями.

    Returns:
        (consensus, confidence)
        consensus: "AGREE", "DISAGREE", "WEAK"
        confidence: "HIGH", "MEDIUM", "LOW"
    """
    # Edge для каждой модели
    int_edge = integrated_fair - market_price
    simple_edge = simple_fair - market_price

    # Направление (BUY или не BUY)
    int_buy = int_edge > MIN_EDGE
    simple_buy = simple_edge > MIN_EDGE

    # Для YES/NO — смотрим знак edge
    int_side = "YES" if int_edge > 0 else "NO"
    simple_side = "YES" if simple_edge > 0 else "NO"

    # Разница между моделями
    diff = abs(integrated_fair - simple_fair)

    # Определяем консенсус
    if int_side == simple_side:
        # Модели согласны на направление
        if int_buy and simple_buy:
            # Обе видят возможность
            consensus = "AGREE"
            if diff < 0.03:  # Разница < 3%
                confidence = "HIGH"
            elif diff < 0.07:  # Разница < 7%
                confidence = "MEDIUM"
            else:
                confidence = "LOW"
        elif int_buy or simple_buy:
            # Только одна видит возможность
            consensus = "WEAK"
            confidence = "LOW"
        else:
            # Ни одна не видит возможность
            consensus = "AGREE"
            confidence = "HIGH"
    else:
        # Модели на разных сторонах
        consensus = "DISAGREE"
        confidence = "LOW"

    return consensus, confidence


def analyze_with_consensus(
    poly: PolymarketClient,
    usgs: USGSClient,
) -> list[ConsensusOpportunity]:
    """Анализ с обеими моделями и определением консенсуса."""

    all_opportunities = []
    now = datetime.now(timezone.utc)

    # Получаем данные с Polymarket
    all_prices = poly.get_all_earthquake_prices()

    # Получаем недавние события для ETAS
    recent_events = []
    try:
        recent_quakes = usgs.get_earthquakes(now - timedelta(days=30), now, 6.0)
        recent_events = [
            {"time": q.time, "magnitude": q.magnitude}
            for q in recent_quakes
        ]
    except Exception:
        pass

    for event_slug, markets in all_prices.items():
        if event_slug not in MARKET_CONFIGS:
            continue

        config = MARKET_CONFIGS[event_slug]
        magnitude = config["magnitude"]
        start = config["start"]
        end = config["end"]

        # Создаём обе модели
        integrated_model = IntegratedModel(magnitude=magnitude)
        simple_model = SimpleModel(magnitude=magnitude)

        # Получаем текущее количество
        earthquakes = usgs.get_earthquakes(start, now, magnitude)
        current_count = len(earthquakes)

        remaining_days = max(0, (end - now).total_seconds() / 86400)

        # Собираем рыночные цены
        market_prices = {}
        token_ids = {}
        condition_ids = {}

        for market in markets:
            if not market.active:
                continue

            yes_outcome = None
            no_outcome = None
            for outcome in market.outcomes:
                if outcome.outcome_name == "Yes":
                    yes_outcome = outcome
                elif outcome.outcome_name == "No":
                    no_outcome = outcome

            if yes_outcome is None or yes_outcome.closed:
                continue

            q = market.question.lower()

            import re
            for name, _, _ in config.get("outcomes", []):
                matched = False

                if name.endswith("+"):
                    num = name[:-1]
                    matched = bool(re.search(rf'\b{num}\s+or\s+more\b', q))
                elif "-" in name and name[0].isdigit():
                    parts = name.split("-")
                    if len(parts) == 2:
                        matched = bool(re.search(rf'between\s+{parts[0]}\s+and\s+{parts[1]}', q))
                elif name.startswith("<"):
                    num = name[1:]
                    matched = bool(re.search(rf'fewer\s+than\s+{num}\b', q))
                else:
                    matched = bool(re.search(rf'exactly\s+{name}\b', q))

                if matched:
                    market_prices[name] = yes_outcome.yes_price
                    token_ids[(name, "YES")] = yes_outcome.token_id
                    condition_ids[name] = market.condition_id
                    if no_outcome:
                        token_ids[(name, "NO")] = no_outcome.token_id
                    break
            else:
                market_prices[yes_outcome.outcome_name] = yes_outcome.yes_price
                token_ids[(yes_outcome.outcome_name, "YES")] = yes_outcome.token_id
                condition_ids[yes_outcome.outcome_name] = market.condition_id
                if no_outcome:
                    token_ids[("No", "NO")] = no_outcome.token_id

        # Анализируем каждый исход
        if config["type"] == "count":
            for outcome_name, min_k, max_k in config["outcomes"]:
                if outcome_name not in market_prices:
                    continue

                # Интегрированная модель
                int_fair_yes = integrated_model.probability_count(
                    min_count=min_k,
                    max_count=max_k,
                    remaining_days=remaining_days,
                    current_count=current_count,
                    recent_events=recent_events,
                    now=now,
                    end_date=end,
                )

                # Простая модель
                simple_fair_yes = simple_model.probability_count(
                    min_count=min_k,
                    max_count=max_k,
                    remaining_days=remaining_days,
                    current_count=current_count,
                )

                mkt_yes = market_prices[outcome_name]

                # Проверяем YES
                consensus, confidence = determine_consensus(
                    int_fair_yes, simple_fair_yes, mkt_yes, magnitude
                )

                int_edge = int_fair_yes - mkt_yes
                simple_edge = simple_fair_yes - mkt_yes

                if int_edge > MIN_EDGE or simple_edge > MIN_EDGE:
                    all_opportunities.append(ConsensusOpportunity(
                        event=event_slug,
                        outcome=outcome_name,
                        side="YES",
                        token_id=token_ids.get((outcome_name, "YES"), ""),
                        condition_id=condition_ids.get(outcome_name, ""),
                        integrated_fair=int_fair_yes,
                        integrated_edge=int_edge,
                        simple_fair=simple_fair_yes,
                        simple_edge=simple_edge,
                        market_price=mkt_yes,
                        liquidity_usd=None,
                        remaining_days=remaining_days,
                        magnitude=magnitude,
                        current_count=current_count,
                        consensus=consensus,
                        confidence=confidence,
                    ))

                # Проверяем NO
                int_fair_no = 1 - int_fair_yes
                simple_fair_no = 1 - simple_fair_yes
                mkt_no = 1 - mkt_yes

                consensus_no, confidence_no = determine_consensus(
                    int_fair_no, simple_fair_no, mkt_no, magnitude
                )

                int_edge_no = int_fair_no - mkt_no
                simple_edge_no = simple_fair_no - mkt_no

                if int_edge_no > MIN_EDGE or simple_edge_no > MIN_EDGE:
                    all_opportunities.append(ConsensusOpportunity(
                        event=event_slug,
                        outcome=outcome_name,
                        side="NO",
                        token_id=token_ids.get((outcome_name, "NO"), ""),
                        condition_id=condition_ids.get(outcome_name, ""),
                        integrated_fair=int_fair_no,
                        integrated_edge=int_edge_no,
                        simple_fair=simple_fair_no,
                        simple_edge=simple_edge_no,
                        market_price=mkt_no,
                        liquidity_usd=None,
                        remaining_days=remaining_days,
                        magnitude=magnitude,
                        current_count=current_count,
                        consensus=consensus_no,
                        confidence=confidence_no,
                    ))

        elif config["type"] == "binary":
            # Вероятность хотя бы одного события
            int_fair_yes = integrated_model.probability_at_least_one(
                remaining_days=remaining_days,
                current_count=current_count,
                recent_events=recent_events,
                now=now,
                end_date=end,
            )

            simple_fair_yes = simple_model.probability_at_least_one(
                remaining_days=remaining_days,
                current_count=current_count,
            )

            for outcome_name in ["Yes", "YES"]:
                if outcome_name not in market_prices:
                    continue

                mkt_yes = market_prices[outcome_name]
                mkt_no = 1 - mkt_yes

                # YES side
                consensus, confidence = determine_consensus(
                    int_fair_yes, simple_fair_yes, mkt_yes, magnitude
                )

                int_edge = int_fair_yes - mkt_yes
                simple_edge = simple_fair_yes - mkt_yes

                if int_edge > MIN_EDGE or simple_edge > MIN_EDGE:
                    all_opportunities.append(ConsensusOpportunity(
                        event=event_slug,
                        outcome="Yes",
                        side="YES",
                        token_id=token_ids.get((outcome_name, "YES"), ""),
                        condition_id=condition_ids.get(outcome_name, ""),
                        integrated_fair=int_fair_yes,
                        integrated_edge=int_edge,
                        simple_fair=simple_fair_yes,
                        simple_edge=simple_edge,
                        market_price=mkt_yes,
                        liquidity_usd=None,
                        remaining_days=remaining_days,
                        magnitude=magnitude,
                        current_count=current_count,
                        consensus=consensus,
                        confidence=confidence,
                    ))

                # NO side
                int_fair_no = 1 - int_fair_yes
                simple_fair_no = 1 - simple_fair_yes

                consensus_no, confidence_no = determine_consensus(
                    int_fair_no, simple_fair_no, mkt_no, magnitude
                )

                int_edge_no = int_fair_no - mkt_no
                simple_edge_no = simple_fair_no - mkt_no

                if int_edge_no > MIN_EDGE or simple_edge_no > MIN_EDGE:
                    all_opportunities.append(ConsensusOpportunity(
                        event=event_slug,
                        outcome="No",
                        side="NO",
                        token_id=token_ids.get(("No", "NO"), ""),
                        condition_id=condition_ids.get(outcome_name, ""),
                        integrated_fair=int_fair_no,
                        integrated_edge=int_edge_no,
                        simple_fair=simple_fair_no,
                        simple_edge=simple_edge_no,
                        market_price=mkt_no,
                        liquidity_usd=None,
                        remaining_days=remaining_days,
                        magnitude=magnitude,
                        current_count=current_count,
                        consensus=consensus_no,
                        confidence=confidence_no,
                    ))

    # Обновляем данные из ордербука
    print("Проверяю ордербуки...")
    updated = []
    for opp in all_opportunities:
        if opp.condition_id:
            outcome_to_check = "Yes" if opp.side == "YES" else "No"
            best_ask, liquidity, token_id = get_orderbook_data(poly, opp.condition_id, outcome_to_check)

            if liquidity is not None:
                opp.liquidity_usd = liquidity
            if token_id:
                opp.token_id = token_id

            if best_ask is not None and best_ask > 0:
                # Обновляем цену и пересчитываем
                opp.market_price = best_ask
                opp.integrated_edge = opp.integrated_fair - best_ask
                opp.simple_edge = opp.simple_fair - best_ask

                # Пересчитываем консенсус
                opp.consensus, opp.confidence = determine_consensus(
                    opp.integrated_fair, opp.simple_fair, best_ask, opp.magnitude
                )

        # Фильтруем по edge
        if opp.primary_edge > MIN_EDGE:
            updated.append(opp)

    all_opportunities = updated

    # Фильтруем по APY
    all_opportunities = [
        opp for opp in all_opportunities
        if opp.annual_return >= MIN_ANNUAL_RETURN
    ]

    # Сортируем по APY
    all_opportunities.sort(key=lambda x: x.annual_return, reverse=True)

    return all_opportunities


def filter_by_consensus(
    opportunities: list[ConsensusOpportunity],
    show_all: bool = False,
) -> list[ConsensusOpportunity]:
    """
    Фильтрация по правилам консенсуса.

    Правила:
    - M7.0+: показываем всё (интегрированная — основная)
    - M8.0+: показываем только AGREE или WEAK с HIGH/MEDIUM confidence
    - DISAGREE: не показываем (если не show_all)
    """
    if show_all:
        return opportunities

    filtered = []
    for opp in opportunities:
        if opp.magnitude < 8.0:
            # M7.0+ — показываем всё
            filtered.append(opp)
        else:
            # M8.0+ — только консенсус
            if opp.consensus == "AGREE":
                filtered.append(opp)
            elif opp.consensus == "WEAK" and opp.confidence in ["HIGH", "MEDIUM"]:
                filtered.append(opp)
            # DISAGREE — пропускаем

    return filtered


def print_consensus_opportunities(
    opportunities: list[ConsensusOpportunity],
    poly: PolymarketClient,
    show_all: bool = False,
):
    """Вывести возможности с консенсусом."""

    # Фильтруем
    filtered = filter_by_consensus(opportunities, show_all)

    # Группируем по событию
    best_per_event: dict[str, ConsensusOpportunity] = {}
    for opp in filtered:
        if opp.event not in best_per_event or opp.annual_return > best_per_event[opp.event].annual_return:
            best_per_event[opp.event] = opp

    selected = list(best_per_event.values())
    selected.sort(key=lambda x: x.annual_return, reverse=True)

    # Считаем скрытые
    all_best = {}
    for opp in opportunities:
        if opp.event not in all_best or opp.annual_return > all_best[opp.event].annual_return:
            all_best[opp.event] = opp
    hidden_count = len(all_best) - len(best_per_event)

    print("\n" + "=" * 80)
    print("КОНСЕНСУСНАЯ МОДЕЛЬ")
    print("=" * 80)

    if not selected:
        print(f"\nНет возможностей с консенсусом моделей")
        if hidden_count > 0:
            print(f"(скрыто {hidden_count} возможностей с расхождением моделей)")
        return []

    for i, opp in enumerate(selected, 1):
        # Символы консенсуса
        if opp.consensus == "AGREE":
            consensus_symbol = "[AGREE]"
        elif opp.consensus == "WEAK":
            consensus_symbol = "[WEAK]"
        else:
            consensus_symbol = "[DISAGREE]"

        confidence_symbol = {
            "HIGH": "+++",
            "MEDIUM": "++",
            "LOW": "+",
        }.get(opp.confidence, "?")

        url = f"https://polymarket.com/event/{opp.event}"
        print(f"\n{i}. {url}")
        print(f"   {consensus_symbol} {confidence_symbol} | M{opp.magnitude}+")
        print(f"   BUY {opp.side} на '{opp.outcome}'")

        # Основная оценка
        if opp.magnitude < 8.0:
            print(f"   Основная (интегр.): {opp.integrated_fair*100:.1f}%  |  Edge: {opp.integrated_edge*100:+.1f}%")
            print(f"   Справка (простая):  {opp.simple_fair*100:.1f}%  |  Edge: {opp.simple_edge*100:+.1f}%")
        else:
            print(f"   Консенсус: {opp.primary_fair*100:.1f}%  |  Edge: {opp.primary_edge*100:+.1f}%")
            print(f"   Интегр.: {opp.integrated_fair*100:.1f}%  |  Простая: {opp.simple_fair*100:.1f}%  |  Δ: {opp.model_diff_pct:.1f}%")

        print(f"   Рынок: {opp.market_price*100:.1f}%  |  Дней: {opp.remaining_days:.0f}")
        print(f"   ROI: {opp.expected_return*100:+.1f}%  |  APY: {opp.annual_return*100:+.0f}%")

        # Уровни ордербука
        if poly and opp.token_id:
            tiers = get_orderbook_tiers(poly, opp.token_id, opp.primary_fair, opp.remaining_days)
            if tiers:
                print(f"   Инвестиции по уровням:")

                apy_groups = {}
                for tier in tiers:
                    apy_rounded = int(tier["apy"] * 100)
                    if apy_rounded not in apy_groups:
                        apy_groups[apy_rounded] = 0
                    apy_groups[apy_rounded] = tier["cumulative_usd"]

                shown = 0
                prev_cumulative = 0
                for apy_pct in sorted(apy_groups.keys(), reverse=True):
                    if apy_pct < MIN_ANNUAL_RETURN * 100:
                        break
                    cumulative = apy_groups[apy_pct]
                    if cumulative > prev_cumulative + 10:
                        print(f"     → ${cumulative:,.0f} с APY {apy_pct}%+")
                        prev_cumulative = cumulative
                        shown += 1
                        if shown >= 5:
                            break

    print(f"\n" + "-" * 80)
    print(f"Показано {len(selected)} возможностей с консенсусом")
    if hidden_count > 0:
        print(f"Скрыто {hidden_count} возможностей с расхождением моделей (--show-all чтобы показать)")

    return selected


def save_consensus_report(
    opportunities: list[ConsensusOpportunity],
    poly: PolymarketClient,
    show_all: bool = False,
    output_dir: Path = None,
) -> Path:
    """Сохранить отчёт в markdown."""
    if output_dir is None:
        output_dir = Path(__file__).parent / "output"

    output_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    filename = now.strftime("%Y-%m-%d_%H-%M") + "_consensus_UTC.md"
    filepath = output_dir / filename

    filtered = filter_by_consensus(opportunities, show_all)

    # Группируем
    best_per_event = {}
    for opp in filtered:
        if opp.event not in best_per_event or opp.annual_return > best_per_event[opp.event].annual_return:
            best_per_event[opp.event] = opp

    selected = sorted(best_per_event.values(), key=lambda x: x.annual_return, reverse=True)

    lines = []
    lines.append("# Earthquake Bot Report (Consensus Model)")
    lines.append("")
    lines.append(f"**Время:** {now.strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("")
    lines.append("## Правила консенсуса")
    lines.append("")
    lines.append("| Магнитуда | Правило |")
    lines.append("|-----------|---------|")
    lines.append("| M7.0+ | Интегрированная модель (основная), простая для справки |")
    lines.append("| M8.0+ | Только при согласии моделей на направление |")
    lines.append("| DISAGREE | Не показывается (модели на разных сторонах) |")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Торговые возможности")
    lines.append("")

    if not selected:
        lines.append("Нет возможностей с консенсусом моделей")
    else:
        for i, opp in enumerate(selected, 1):
            lines.append(f"### {i}. {opp.event}")
            lines.append("")
            lines.append(f"**Ссылка:** https://polymarket.com/event/{opp.event}")
            lines.append("")
            lines.append(f"**Консенсус:** {opp.consensus} | **Уверенность:** {opp.confidence} | **Магнитуда:** M{opp.magnitude}+")
            lines.append("")
            lines.append("| Параметр | Значение |")
            lines.append("|----------|----------|")
            lines.append(f"| Позиция | BUY {opp.side} на '{opp.outcome}' |")

            if opp.magnitude < 8.0:
                lines.append(f"| Интегр. модель | {opp.integrated_fair*100:.1f}% (Edge: {opp.integrated_edge*100:+.1f}%) |")
                lines.append(f"| Простая модель | {opp.simple_fair*100:.1f}% (Edge: {opp.simple_edge*100:+.1f}%) |")
            else:
                lines.append(f"| Консенсус | {opp.primary_fair*100:.1f}% (Edge: {opp.primary_edge*100:+.1f}%) |")
                lines.append(f"| Интегр. / Простая | {opp.integrated_fair*100:.1f}% / {opp.simple_fair*100:.1f}% |")

            lines.append(f"| Рынок | {opp.market_price*100:.1f}% |")
            lines.append(f"| Разница моделей | {opp.model_diff_pct:.1f}% |")
            lines.append(f"| Дней | {opp.remaining_days:.0f} |")
            lines.append(f"| ROI | {opp.expected_return*100:+.1f}% |")
            lines.append(f"| APY | {opp.annual_return*100:+.0f}% |")
            lines.append("")

            # Уровни
            if poly and opp.token_id:
                tiers = get_orderbook_tiers(poly, opp.token_id, opp.primary_fair, opp.remaining_days)
                if tiers:
                    lines.append("**Инвестиции по уровням:**")
                    lines.append("")
                    lines.append("| Сумма | Мин APY |")
                    lines.append("|-------|---------|")

                    apy_groups = {}
                    for tier in tiers:
                        apy_rounded = int(tier["apy"] * 100)
                        if apy_rounded not in apy_groups:
                            apy_groups[apy_rounded] = 0
                        apy_groups[apy_rounded] = tier["cumulative_usd"]

                    shown = 0
                    prev = 0
                    for apy_pct in sorted(apy_groups.keys(), reverse=True):
                        if apy_pct < MIN_ANNUAL_RETURN * 100:
                            break
                        cum = apy_groups[apy_pct]
                        if cum > prev + 10:
                            lines.append(f"| ${cum:,.0f} | {apy_pct}%+ |")
                            prev = cum
                            shown += 1
                            if shown >= 5:
                                break

                    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(f"*Показано {len(selected)} возможностей с консенсусом*")

    content = "\n".join(lines)
    filepath.write_text(content, encoding="utf-8")

    return filepath


def allocate_portfolio(
    opportunities: list[ConsensusOpportunity],
    bankroll: float,
    show_all: bool = False,
) -> list[tuple[ConsensusOpportunity, float]]:
    """Распределить банкролл."""

    filtered = filter_by_consensus(opportunities, show_all)

    if not filtered:
        return []

    # Группируем по событию
    best_per_event = {}
    for opp in filtered:
        if opp.event not in best_per_event or opp.annual_return > best_per_event[opp.event].annual_return:
            best_per_event[opp.event] = opp

    selected = list(best_per_event.values())

    if not selected:
        return []

    # Score с учётом confidence
    scores = []
    for opp in selected:
        base_score = opp.primary_edge * opp.annual_return

        # Бонус за confidence
        conf_mult = {
            "HIGH": 1.5,
            "MEDIUM": 1.0,
            "LOW": 0.5,
        }.get(opp.confidence, 1.0)

        score = base_score * conf_mult
        scores.append((opp, score))

    total_score = sum(s for _, s in scores)
    if total_score <= 0:
        return []

    allocations = []
    remaining = bankroll

    for opp, score in sorted(scores, key=lambda x: x[1], reverse=True):
        base_alloc = bankroll * (score / total_score)

        if opp.liquidity_usd:
            liq_limit = opp.liquidity_usd * MAX_LIQUIDITY_PCT
            base_alloc = min(base_alloc, liq_limit)

        if base_alloc < MIN_BET_USD:
            base_alloc = MIN_BET_USD

        alloc = min(base_alloc, remaining)

        if alloc >= MIN_BET_USD:
            allocations.append((opp, alloc))
            remaining -= alloc

    return allocations


def execute_trade(
    poly: PolymarketClient,
    opp: ConsensusOpportunity,
    amount: float,
    debug: bool = True,
) -> bool:
    """Выполнить сделку."""
    print(f"\n{'=' * 50}")
    print(f"СДЕЛКА: BUY {opp.side} ${amount:.2f}")
    print(f"Рынок: {opp.event}")
    print(f"Исход: {opp.outcome}")
    print(f"Консенсус: {opp.consensus} | Уверенность: {opp.confidence}")
    print(f"Edge: {opp.primary_edge*100:+.1f}%")
    print(f"{'=' * 50}")

    if debug:
        confirm = input("\nПодтвердить? [y/n]: ").strip().lower()
        if confirm != "y":
            print("Отменено.")
            return False

    if not opp.token_id:
        print("Ошибка: token_id не найден")
        return False

    try:
        result = poly.create_market_order(
            token_id=opp.token_id,
            side="BUY",
            amount=amount,
        )
        print(f"Успешно! Order ID: {result.get('orderID', 'N/A')}")
        return True
    except Exception as e:
        print(f"Ошибка: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Earthquake Trading Bot (Consensus Model)")
    parser.add_argument("--debug", action="store_true", help="Режим отладки")
    parser.add_argument("--auto", action="store_true", help="Автоматическая торговля")
    parser.add_argument("--bankroll", type=float, default=230.0, help="Банкролл в USD")
    parser.add_argument("--show-all", action="store_true", help="Показать все, включая расхождения")
    args = parser.parse_args()

    print("=" * 80)
    print("EARTHQUAKE TRADING BOT (CONSENSUS MODEL)")
    print("=" * 80)
    print(f"Время: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Режим: {'AUTO' if args.auto else ('DEBUG' if args.debug else 'ANALYSIS')}")
    print()
    print("Правила консенсуса:")
    print("  • M7.0+: интегрированная модель (основная), простая для справки")
    print("  • M8.0+: только при согласии моделей на направление")
    print("  • DISAGREE: скрывается (модели на разных сторонах)")
    if args.show_all:
        print("  • --show-all: показываем ВСЕ возможности")

    # Инициализация
    poly = PolymarketClient()
    usgs = USGSClient()

    # Анализ
    print("\nАнализирую рынки обеими моделями...")
    opportunities = analyze_with_consensus(poly, usgs)

    # Вывод
    selected = print_consensus_opportunities(opportunities, poly, args.show_all)

    # Сохраняем отчёт
    if selected:
        report_path = save_consensus_report(opportunities, poly, args.show_all)
        print(f"\nОтчёт сохранён: {report_path}")

    # Торговля
    if (args.debug or args.auto) and opportunities:
        allocations = allocate_portfolio(opportunities, args.bankroll, args.show_all)

        if allocations:
            print("\n" + "=" * 80)
            print("ТОРГОВЛЯ")
            print("=" * 80)

            for opp, bet_size in allocations:
                if args.auto:
                    execute_trade(poly, opp, bet_size, debug=False)
                elif args.debug:
                    execute_trade(poly, opp, bet_size, debug=True)

    print("\n" + "=" * 80)
    print("Готово!")


if __name__ == "__main__":
    main()
