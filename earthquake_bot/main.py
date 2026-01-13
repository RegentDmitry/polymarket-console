#!/usr/bin/env python3
"""
Earthquake Trading Bot для Polymarket.

Использование:
    python main.py              # Режим анализа (без торговли)
    python main.py --debug      # Режим отладки (подтверждение перед торговлей)
    python main.py --auto       # Автоматическая торговля

"""

import argparse
import math
import sys
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from usgs_client import USGSClient
from polymarket_client import PolymarketClient
from markets import EARTHQUAKE_ANNUAL_RATES


# ============================================================================
# КОНФИГУРАЦИЯ
# ============================================================================

# Минимальный edge для торговли (0.5% = 0.005)
MIN_EDGE = 0.005

# Минимальная годовая доходность (3% = 0.03)
MIN_ANNUAL_RETURN = 0.03

# Минимальная ставка в долларах
MIN_BET_USD = 5.0

# Максимальная ставка в % от ликвидности (чтобы избежать slippage)
MAX_LIQUIDITY_PCT = 0.10

# Lambda по умолчанию для M7.0+
DEFAULT_LAMBDA_M7 = 15.0


# ============================================================================
# РАСЧЁТ МАКСИМАЛЬНОГО БАНКРОЛЛА
# ============================================================================

def calculate_max_bankroll(
    opportunities: list,
    poly: 'PolymarketClient',
    check_liquidity: bool = True,
) -> dict:
    """
    Рассчитать максимальный банкролл, который можно выгодно инвестировать.

    Returns:
        dict с полями:
        - max_bankroll_kelly: макс. банкролл по Kelly-аллокациям
        - max_bankroll_liquidity: макс. банкролл по ликвидности
        - max_bankroll: итоговый (минимум из двух)
        - total_kelly_pct: суммарная Kelly-аллокация в %
        - opportunities_details: детали по каждой возможности
    """
    if not opportunities:
        return {
            "max_bankroll_kelly": 0,
            "max_bankroll_liquidity": 0,
            "max_bankroll": 0,
            "total_kelly_pct": 0,
            "opportunities_details": [],
        }

    details = []
    total_kelly_allocation = 0
    total_liquidity_usd = 0

    for opp in opportunities:
        # Kelly allocation (с учётом фракции и капа)
        kelly_alloc = min(opp.kelly * KELLY_FRACTION, MAX_BET_PCT)
        total_kelly_allocation += kelly_alloc

        # Проверяем ликвидность в ордербуке
        liquidity_usd = None
        if check_liquidity and opp.token_id:
            try:
                orderbook = poly.get_orderbook(opp.token_id)
                # Для BUY смотрим asks (продавцы)
                asks = orderbook.get("asks", [])
                # Считаем доступную ликвидность до цены +10% от текущей
                max_price = min(1.0, opp.market_price * 1.10)
                liquidity_usd = 0
                for ask in asks:
                    price = float(ask.get("price", 0))
                    size = float(ask.get("size", 0))
                    if price <= max_price:
                        liquidity_usd += price * size
                total_liquidity_usd += liquidity_usd
            except Exception:
                liquidity_usd = None

        details.append({
            "outcome": f"{opp.event}:{opp.outcome}:{opp.side}",
            "kelly_alloc_pct": kelly_alloc * 100,
            "liquidity_usd": liquidity_usd,
        })

    # Максимальный банкролл по Kelly
    # Если total_kelly_allocation = 0.25 (25%), то при банкролле $1000
    # мы инвестируем $250. Чтобы инвестировать всё - нужен банкролл = сумма / allocation
    # Но логичнее: при каком банкролле все ставки >= MIN_BET_USD?

    # Подход 1: банкролл, при котором минимальная Kelly-ставка = MIN_BET_USD
    min_kelly = min(opp.kelly for opp in opportunities) if opportunities else 0
    if min_kelly > 0:
        # bankroll * min_kelly * KELLY_FRACTION = MIN_BET_USD
        min_bankroll_for_all = MIN_BET_USD / (min_kelly * KELLY_FRACTION)
    else:
        min_bankroll_for_all = MIN_BET_USD / MAX_BET_PCT

    # Подход 2: банкролл, при котором сумма ставок = банкролл (100% deployment)
    # sum(min(kelly_i * KELLY_FRACTION, MAX_BET_PCT)) * bankroll = bankroll
    # Это возможно только если total_kelly_allocation >= 1.0
    if total_kelly_allocation >= 1.0:
        max_bankroll_kelly = float('inf')  # Можем инвестировать любой банкролл
    else:
        # При total_kelly_allocation < 1 часть банкролла останется неинвестированной
        # "Максимальный полезный" - субъективно; возьмём 10x от минимального
        max_bankroll_kelly = min_bankroll_for_all * 10

    # Максимальный банкролл по ликвидности
    if check_liquidity and total_liquidity_usd > 0:
        # Ликвидность ограничивает размер ставок
        # Если ликвидность = $500, и Kelly хочет 25% банкролла, то макс банкролл = 500/0.25 = $2000
        max_bankroll_liquidity = total_liquidity_usd / total_kelly_allocation if total_kelly_allocation > 0 else total_liquidity_usd
    else:
        max_bankroll_liquidity = float('inf')

    # Итоговый максимум
    max_bankroll = min(max_bankroll_kelly, max_bankroll_liquidity)
    if max_bankroll == float('inf'):
        max_bankroll = 100000  # Разумный верхний предел

    return {
        "max_bankroll_kelly": max_bankroll_kelly if max_bankroll_kelly != float('inf') else None,
        "max_bankroll_liquidity": max_bankroll_liquidity if max_bankroll_liquidity != float('inf') else None,
        "max_bankroll": max_bankroll,
        "total_kelly_pct": total_kelly_allocation * 100,
        "total_liquidity_usd": total_liquidity_usd if check_liquidity else None,
        "min_bankroll": min_bankroll_for_all,
        "opportunities_details": details,
    }


# ============================================================================
# МОДЕЛЬ
# ============================================================================

def poisson_prob(k: int, lam: float) -> float:
    """P(X = k) для распределения Пуассона."""
    if k < 0 or lam <= 0:
        return 0.0
    return (lam ** k) * math.exp(-lam) / math.factorial(k)


def poisson_range(min_k: int, max_k: Optional[int], lam: float) -> float:
    """P(min_k <= X <= max_k)."""
    if max_k is None:
        return 1 - sum(poisson_prob(i, lam) for i in range(min_k))
    return sum(poisson_prob(i, lam) for i in range(min_k, max_k + 1))


def kelly_criterion(prob: float, odds: float) -> float:
    """Kelly fraction = (p * b - q) / b."""
    if prob <= 0 or prob >= 1 or odds <= 0:
        return 0.0
    q = 1 - prob
    return max(0, (prob * odds - q) / odds)


# ============================================================================
# MARKET CONFIGS
# ============================================================================

MARKET_CONFIGS = {
    "how-many-7pt0-or-above-earthquakes-by-june-30": {
        "magnitude": 7.0,
        "start": datetime(2025, 12, 4, 17, 0, 0, tzinfo=timezone.utc),
        "end": datetime(2026, 6, 30, 23, 59, 59, tzinfo=timezone.utc),
        "type": "count",
        "outcomes": [
            ("2", 2, 2), ("3", 3, 3), ("4", 4, 4), ("5", 5, 5),
            ("6", 6, 6), ("7", 7, 7), ("8+", 8, None),
        ],
    },
    "how-many-7pt0-or-above-earthquakes-in-2026": {
        "magnitude": 7.0,
        "start": datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
        "end": datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc),
        "type": "count",
        "outcomes": [
            ("<5", 0, 4), ("5-7", 5, 7), ("8-10", 8, 10), ("11-13", 11, 13),
            ("14-16", 14, 16), ("17-19", 17, 19), ("20+", 20, None),
        ],
    },
    "10pt0-or-above-earthquake-before-2027": {
        "magnitude": 10.0,
        "start": datetime(2025, 12, 8, 12, 0, 0, tzinfo=timezone.utc),
        "end": datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc),
        "type": "binary",
    },
    "9pt0-or-above-earthquake-before-2027": {
        "magnitude": 9.0,
        "start": datetime(2025, 12, 8, 12, 0, 0, tzinfo=timezone.utc),
        "end": datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc),
        "type": "binary",
    },
    # Megaquake (M8.0+) рынки
    "megaquake-by-january-31": {
        "magnitude": 8.0,
        "start": datetime(2025, 12, 28, 0, 0, 0, tzinfo=timezone.utc),
        "end": datetime(2026, 1, 31, 23, 59, 59, tzinfo=timezone.utc),
        "type": "binary",
    },
    "megaquake-by-march-31": {
        "magnitude": 8.0,
        "start": datetime(2025, 12, 28, 0, 0, 0, tzinfo=timezone.utc),
        "end": datetime(2026, 3, 31, 23, 59, 59, tzinfo=timezone.utc),
        "type": "binary",
    },
    "megaquake-by-june-30": {
        "magnitude": 8.0,
        "start": datetime(2025, 12, 28, 0, 0, 0, tzinfo=timezone.utc),
        "end": datetime(2026, 6, 30, 23, 59, 59, tzinfo=timezone.utc),
        "type": "binary",
    },
}


@dataclass
class Opportunity:
    """Торговая возможность."""
    event: str
    outcome: str
    side: str  # "YES" or "NO"
    token_id: str
    fair_price: float
    market_price: float
    edge: float
    kelly: float
    current_count: int
    lambda_used: float
    remaining_days: float = 0  # Дней до резолюции
    condition_id: str = ""
    liquidity_usd: Optional[float] = None  # Общая ликвидность в ордербуке
    usable_liquidity: Optional[float] = None  # Ликвидность по ценам, проходящим фильтры

    @property
    def expected_return(self) -> float:
        """Ожидаемая доходность сделки."""
        if self.market_price <= 0:
            return 0.0
        # Покупаем по market_price, ожидаемый выигрыш = fair_price
        return self.fair_price / self.market_price - 1

    @property
    def annual_return(self) -> float:
        """Годовая доходность (APY)."""
        if self.remaining_days <= 0:
            return 0.0
        # Простая аннуализация
        return self.expected_return * (365 / self.remaining_days)


def get_orderbook_data(poly: 'PolymarketClient', condition_id: str, outcome: str) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """
    Получить данные ордербука для покупки из CLOB API.

    Args:
        poly: Polymarket клиент
        condition_id: ID условия рынка
        outcome: "Yes" или "No"

    Returns:
        (best_ask_price, liquidity_usd, token_id)
        - best_ask_price: Реальная цена покупки (лучший ask)
        - liquidity_usd: Сумма в USD доступная для покупки
        - token_id: ID токена
    """
    import httpx

    try:
        # Получаем рынок из CLOB
        clob_market = poly.get_clob_market(condition_id)
        if not clob_market or not clob_market.get("enable_order_book"):
            return None, None, None

        # Находим нужный токен
        for token in clob_market.get("tokens", []):
            if token.get("outcome") == outcome:
                token_id = token.get("token_id")

                # Получаем ордербук
                response = httpx.get(
                    f"{poly.host}/book",
                    params={"token_id": token_id},
                    timeout=30,
                )
                if response.status_code != 200:
                    return None, None, token_id

                ob = response.json()
                asks = ob.get("asks", [])

                if not asks:
                    return None, None, token_id

                # Best ask - минимальная цена среди asks
                best_ask = min(float(ask.get("price", 1.0)) for ask in asks)

                # Ликвидность - сумма (price * size) для всех asks
                liquidity = 0.0
                for ask in asks:
                    price = float(ask.get("price", 0))
                    size = float(ask.get("size", 0))
                    liquidity += price * size

                return best_ask, liquidity, token_id

        return None, None, None
    except Exception:
        return None, None, None


def get_liquidity_from_clob(poly: 'PolymarketClient', condition_id: str, outcome: str) -> Optional[float]:
    """Обёртка для обратной совместимости."""
    _, liquidity, _ = get_orderbook_data(poly, condition_id, outcome)
    return liquidity


def analyze_market(
    event_slug: str,
    config: dict,
    usgs: USGSClient,
    market_prices: dict[str, float],  # outcome -> YES price
    annual_rate: float = DEFAULT_LAMBDA_M7,
) -> list[Opportunity]:
    """Анализировать один рынок."""
    opportunities = []
    now = datetime.now(timezone.utc)

    magnitude = config["magnitude"]
    start = config["start"]
    end = config["end"]

    # Получаем текущее количество
    earthquakes = usgs.get_earthquakes(start, now, magnitude)
    current_count = len(earthquakes)

    # Lambda для оставшегося периода
    remaining_days = max(0, (end - now).total_seconds() / 86400)
    lam = annual_rate * (remaining_days / 365)

    if config["type"] == "count":
        for outcome_name, min_k, max_k in config["outcomes"]:
            if outcome_name not in market_prices:
                continue

            # Модельная вероятность
            min_additional = max(0, min_k - current_count)
            if max_k is not None:
                max_additional = max_k - current_count
                if max_additional < 0:
                    fair_yes = 0.0
                else:
                    fair_yes = poisson_range(min_additional, max_additional, lam)
            else:
                fair_yes = poisson_range(min_additional, None, lam)

            mkt_yes = market_prices[outcome_name]
            mkt_no = 1 - mkt_yes

            # Edge для YES
            edge_yes = fair_yes - mkt_yes
            if edge_yes > MIN_EDGE:
                odds = (1 - mkt_yes) / mkt_yes if mkt_yes > 0 else 0
                opportunities.append(Opportunity(
                    event=event_slug,
                    outcome=outcome_name,
                    side="YES",
                    token_id="",  # Заполним позже
                    fair_price=fair_yes,
                    market_price=mkt_yes,
                    edge=edge_yes,
                    kelly=kelly_criterion(fair_yes, odds),
                    current_count=current_count,
                    lambda_used=lam,
                    remaining_days=remaining_days,
                ))

            # Edge для NO
            fair_no = 1 - fair_yes
            edge_no = fair_no - mkt_no
            if edge_no > MIN_EDGE:
                odds = (1 - mkt_no) / mkt_no if mkt_no > 0 else 0
                opportunities.append(Opportunity(
                    event=event_slug,
                    outcome=outcome_name,
                    side="NO",
                    token_id="",
                    fair_price=fair_no,
                    market_price=mkt_no,
                    edge=edge_no,
                    kelly=kelly_criterion(fair_no, odds),
                    current_count=current_count,
                    lambda_used=lam,
                    remaining_days=remaining_days,
                ))

    elif config["type"] == "binary":
        # Вероятность хотя бы одного события
        if current_count > 0:
            fair_yes = 1.0
        else:
            fair_yes = 1 - poisson_prob(0, lam)

        for outcome_name in ["Yes", "YES"]:
            if outcome_name in market_prices:
                mkt_yes = market_prices[outcome_name]
                mkt_no = 1 - mkt_yes

                edge_yes = fair_yes - mkt_yes
                if edge_yes > MIN_EDGE:
                    odds = (1 - mkt_yes) / mkt_yes if mkt_yes > 0 else 0
                    opportunities.append(Opportunity(
                        event=event_slug,
                        outcome="Yes",
                        side="YES",
                        token_id="",
                        fair_price=fair_yes,
                        market_price=mkt_yes,
                        edge=edge_yes,
                        kelly=kelly_criterion(fair_yes, odds),
                        current_count=current_count,
                        lambda_used=lam,
                        remaining_days=remaining_days,
                    ))

                fair_no = 1 - fair_yes
                edge_no = fair_no - mkt_no
                if edge_no > MIN_EDGE:
                    odds = (1 - mkt_no) / mkt_no if mkt_no > 0 else 0
                    opportunities.append(Opportunity(
                        event=event_slug,
                        outcome="No",
                        side="NO",
                        token_id="",
                        fair_price=fair_no,
                        market_price=mkt_no,
                        edge=edge_no,
                        kelly=kelly_criterion(fair_no, odds),
                        current_count=current_count,
                        remaining_days=remaining_days,
                        lambda_used=lam,
                    ))

    return opportunities


def run_analysis(poly: PolymarketClient, usgs: USGSClient) -> list[Opportunity]:
    """Запустить анализ всех рынков."""
    all_opportunities = []

    # Получаем данные с Polymarket
    all_prices = poly.get_all_earthquake_prices()

    for event_slug, markets in all_prices.items():
        if event_slug not in MARKET_CONFIGS:
            continue

        config = MARKET_CONFIGS[event_slug]

        # Собираем рыночные цены и condition_ids
        market_prices = {}
        token_ids = {}
        condition_ids = {}  # outcome_name -> condition_id
        for market in markets:
            if not market.active:
                continue

            # Для count markets находим YES токен (первый outcome обычно "Yes")
            yes_outcome = None
            no_outcome = None
            for outcome in market.outcomes:
                if outcome.outcome_name == "Yes":
                    yes_outcome = outcome
                elif outcome.outcome_name == "No":
                    no_outcome = outcome

            if yes_outcome is None or yes_outcome.closed:
                continue

            # Извлекаем название исхода из вопроса
            q = market.question.lower()

            # Для count markets — используем точное сопоставление
            import re
            for name, _, _ in config.get("outcomes", []):
                matched = False

                if name.endswith("+"):
                    # "8+" -> "8 or more"
                    num = name[:-1]
                    matched = bool(re.search(rf'\b{num}\s+or\s+more\b', q))
                elif "-" in name and name[0].isdigit():
                    # "14-16" -> "between 14 and 16"
                    parts = name.split("-")
                    if len(parts) == 2:
                        matched = bool(re.search(rf'between\s+{parts[0]}\s+and\s+{parts[1]}', q))
                elif name.startswith("<"):
                    # "<5" -> "fewer than 5"
                    num = name[1:]
                    matched = bool(re.search(rf'fewer\s+than\s+{num}\b', q))
                else:
                    # "3" -> "exactly 3"
                    matched = bool(re.search(rf'exactly\s+{name}\b', q))

                if matched:
                    market_prices[name] = yes_outcome.yes_price
                    token_ids[(name, "YES")] = yes_outcome.token_id
                    condition_ids[name] = market.condition_id
                    if no_outcome:
                        token_ids[(name, "NO")] = no_outcome.token_id
                    break
            else:
                # Для binary markets
                market_prices[yes_outcome.outcome_name] = yes_outcome.yes_price
                token_ids[(yes_outcome.outcome_name, "YES")] = yes_outcome.token_id
                condition_ids[yes_outcome.outcome_name] = market.condition_id
                if no_outcome:
                    token_ids[("No", "NO")] = no_outcome.token_id

        # Анализируем
        annual_rate = EARTHQUAKE_ANNUAL_RATES.get(config["magnitude"], DEFAULT_LAMBDA_M7)
        opps = analyze_market(event_slug, config, usgs, market_prices, annual_rate)

        # Добавляем token_id и condition_id
        for opp in opps:
            key = (opp.outcome, opp.side)
            opp.token_id = token_ids.get(key, "")

            # Для binary markets outcome может быть "Yes"/"No"
            if opp.outcome in condition_ids:
                opp.condition_id = condition_ids[opp.outcome]
            elif "Yes" in condition_ids:
                opp.condition_id = condition_ids["Yes"]

        all_opportunities.extend(opps)

    # Получаем реальные цены и ликвидность из ордербука
    print("Проверяю ордербуки...")
    updated_opportunities = []
    for opp in all_opportunities:
        if opp.condition_id:
            # Для YES покупаем Yes токен, для NO покупаем No токен
            outcome_to_check = "Yes" if opp.side == "YES" else "No"
            best_ask, liquidity, token_id = get_orderbook_data(poly, opp.condition_id, outcome_to_check)

            opp.liquidity_usd = liquidity
            if token_id:
                opp.token_id = token_id

            # Используем реальную цену из ордербука если она есть
            if best_ask is not None and best_ask > 0:
                old_price = opp.market_price
                opp.market_price = best_ask

                # Пересчитываем edge с реальной ценой
                opp.edge = opp.fair_price - opp.market_price

                # Пересчитываем Kelly
                if opp.market_price > 0 and opp.market_price < 1:
                    odds = (1 - opp.market_price) / opp.market_price
                    opp.kelly = kelly_criterion(opp.fair_price, odds)
                else:
                    opp.kelly = 0

        # Только положительный edge после пересчёта
        if opp.edge > MIN_EDGE:
            updated_opportunities.append(opp)

    all_opportunities = updated_opportunities

    # Фильтруем по минимальной годовой доходности
    all_opportunities = [
        opp for opp in all_opportunities
        if opp.annual_return >= MIN_ANNUAL_RETURN
    ]

    # Сортируем по годовой доходности (не по edge)
    all_opportunities.sort(key=lambda x: x.annual_return, reverse=True)

    return all_opportunities


def allocate_portfolio(opportunities: list[Opportunity], bankroll: float) -> list[tuple[Opportunity, float]]:
    """
    Распределить банкролл по возможностям.

    - Выбирает лучшую возможность на каждое событие (по APY)
    - Распределяет банкролл пропорционально score = edge × APY
    - Учитывает лимит ликвидности

    Returns:
        Список (opportunity, allocated_amount)
    """
    if not opportunities:
        return []

    # 1. Группируем по событию, выбираем лучший по APY
    best_per_event: dict[str, Opportunity] = {}
    for opp in opportunities:
        if opp.event not in best_per_event or opp.annual_return > best_per_event[opp.event].annual_return:
            best_per_event[opp.event] = opp

    selected = list(best_per_event.values())

    if not selected:
        return []

    # 2. Считаем score для каждой возможности
    scores = []
    for opp in selected:
        # Score = edge × APY (оба важны)
        score = opp.edge * opp.annual_return
        scores.append((opp, score))

    total_score = sum(s for _, s in scores)
    if total_score <= 0:
        return []

    # 3. Распределяем банкролл пропорционально score
    allocations = []
    remaining = bankroll

    for opp, score in sorted(scores, key=lambda x: x[1], reverse=True):
        # Базовая аллокация по score
        base_alloc = bankroll * (score / total_score)

        # Лимит по ликвидности (не больше 10% ликвидности)
        if opp.liquidity_usd:
            liq_limit = opp.liquidity_usd * MAX_LIQUIDITY_PCT
            base_alloc = min(base_alloc, liq_limit)

        # Минимум $5
        if base_alloc < MIN_BET_USD:
            base_alloc = MIN_BET_USD

        # Не больше чем осталось
        alloc = min(base_alloc, remaining)

        if alloc >= MIN_BET_USD:
            allocations.append((opp, alloc))
            remaining -= alloc

    return allocations


def get_orderbook_tiers(poly: 'PolymarketClient', token_id: str, fair_price: float, remaining_days: float) -> list[dict]:
    """
    Получить уровни из ордербука с расчётом APY для каждого.

    Returns:
        Список словарей с полями:
        - price: цена покупки
        - size_usd: сколько можно купить на этом уровне (в USD)
        - cumulative_usd: сколько можно купить до этого уровня включительно
        - roi: ROI на этом уровне
        - apy: APY на этом уровне
    """
    import httpx

    try:
        response = httpx.get(
            f"{poly.host}/book",
            params={"token_id": token_id},
            timeout=30,
        )
        if response.status_code != 200:
            return []

        ob = response.json()
        asks = ob.get("asks", [])

        if not asks:
            return []

        # Сортируем по цене (от лучшей к худшей)
        asks = sorted(asks, key=lambda x: float(x.get("price", 1.0)))

        tiers = []
        cumulative = 0.0

        for ask in asks:
            price = float(ask.get("price", 0))
            size = float(ask.get("size", 0))

            if price <= 0 or price >= 1 or size <= 0:
                continue

            size_usd = price * size
            cumulative += size_usd

            # ROI и APY для этой цены
            roi = fair_price / price - 1 if price > 0 else 0
            apy = roi * (365 / remaining_days) if remaining_days > 0 else 0

            tiers.append({
                "price": price,
                "size_usd": size_usd,
                "cumulative_usd": cumulative,
                "roi": roi,
                "apy": apy,
            })

        return tiers
    except Exception:
        return []


def get_spread_info(poly: 'PolymarketClient', token_id: str) -> dict:
    """
    Получить информацию о спреде для оценки возможности активной торговли.

    Returns:
        dict с полями:
        - best_bid: лучшая цена продажи
        - best_ask: лучшая цена покупки
        - spread: абсолютный спред
        - spread_pct: спред в процентах от ask
        - bid_liquidity: ликвидность на bid (сколько можно продать)
        - ask_liquidity: ликвидность на ask (сколько можно купить)
        - active_trading_ok: рекомендация по активной торговле
    """
    import httpx

    try:
        response = httpx.get(
            f"{poly.host}/book",
            params={"token_id": token_id},
            timeout=30,
        )
        if response.status_code != 200:
            return {}

        ob = response.json()
        asks = ob.get("asks", [])
        bids = ob.get("bids", [])

        if not asks or not bids:
            return {
                "best_bid": 0,
                "best_ask": float(asks[0]["price"]) if asks else 0,
                "spread": 0,
                "spread_pct": 0,
                "bid_liquidity": 0,
                "ask_liquidity": sum(float(a["price"]) * float(a["size"]) for a in asks) if asks else 0,
                "active_trading_ok": False,
                "reason": "Нет bids — только hold to expiry",
            }

        best_ask = min(float(a["price"]) for a in asks)
        best_bid = max(float(b["price"]) for b in bids)
        spread = best_ask - best_bid
        spread_pct = (spread / best_ask * 100) if best_ask > 0 else 0

        # Ликвидность на лучших уровнях
        ask_liquidity = sum(float(a["price"]) * float(a["size"]) for a in asks if float(a["price"]) == best_ask)
        bid_liquidity = sum(float(b["price"]) * float(b["size"]) for b in bids if float(b["price"]) == best_bid)

        # Рекомендация по активной торговле
        # Спред < 5% — активная торговля возможна
        # Спред 5-10% — возможна, но дорого
        # Спред > 10% — только hold to expiry
        if spread_pct < 5:
            active_trading_ok = True
            reason = "Узкий спред — активная торговля OK"
        elif spread_pct < 10:
            active_trading_ok = True
            reason = "Средний спред — активная торговля возможна"
        else:
            active_trading_ok = False
            reason = f"Широкий спред ({spread_pct:.0f}%) — только hold to expiry"

        return {
            "best_bid": best_bid,
            "best_ask": best_ask,
            "spread": spread,
            "spread_pct": spread_pct,
            "bid_liquidity": bid_liquidity,
            "ask_liquidity": ask_liquidity,
            "active_trading_ok": active_trading_ok,
            "reason": reason,
        }
    except Exception:
        return {}


def print_opportunities(opportunities: list[Opportunity], bankroll: float, poly: 'PolymarketClient' = None):
    """Вывести возможности с информацией о доступных инвестициях."""
    print("\n" + "=" * 75)
    print("ТОРГОВЫЕ ВОЗМОЖНОСТИ")
    print("=" * 75)

    if not opportunities:
        print(f"\nНет возможностей с edge > {MIN_EDGE:.0%} и APY > {MIN_ANNUAL_RETURN:.0%}")
        return

    # Группируем по событию, выбираем лучший по APY
    best_per_event: dict[str, Opportunity] = {}
    for opp in opportunities:
        if opp.event not in best_per_event or opp.annual_return > best_per_event[opp.event].annual_return:
            best_per_event[opp.event] = opp

    selected = list(best_per_event.values())

    # Сортируем по APY, потом по ROI
    selected.sort(key=lambda x: (x.annual_return, x.expected_return), reverse=True)

    for i, opp in enumerate(selected, 1):
        prob_win = opp.fair_price
        prob_lose = 1 - prob_win

        url = f"https://polymarket.com/event/{opp.event}"
        print(f"\n{i}. {url}")
        print(f"   BUY {opp.side} на '{opp.outcome}'")
        print(f"   Модель: {opp.fair_price*100:.1f}%  |  Рынок: {opp.market_price*100:.1f}%  |  Edge: {opp.edge*100:+.1f}%")
        print(f"   Выигрыш: {prob_win*100:.1f}%  |  Проигрыш: {prob_lose*100:.1f}%  |  Дней: {opp.remaining_days:.0f}")
        print(f"   ROI: {opp.expected_return*100:+.1f}%  |  APY: {opp.annual_return*100:+.0f}%")

        # Показываем уровни из ордербука
        if poly and opp.token_id:
            tiers = get_orderbook_tiers(poly, opp.token_id, opp.fair_price, opp.remaining_days)
            if tiers:
                print(f"   Инвестиции по уровням:")

                # Группируем по APY (округляем до целых %)
                apy_groups = {}
                for tier in tiers:
                    apy_rounded = int(tier["apy"] * 100)
                    if apy_rounded not in apy_groups:
                        apy_groups[apy_rounded] = 0
                    apy_groups[apy_rounded] = tier["cumulative_usd"]

                # Показываем только значимые уровни (с положительным APY)
                shown = 0
                prev_cumulative = 0
                for apy_pct in sorted(apy_groups.keys(), reverse=True):
                    if apy_pct < MIN_ANNUAL_RETURN * 100:
                        break
                    cumulative = apy_groups[apy_pct]
                    if cumulative > prev_cumulative + 10:  # Показываем если добавляется хотя бы $10
                        print(f"     → ${cumulative:,.0f} с APY {apy_pct}%+")
                        prev_cumulative = cumulative
                        shown += 1
                        if shown >= 5:  # Максимум 5 уровней
                            break

                # Общая доступная сумма (только если min APY >= порога)
                if tiers:
                    total_available = tiers[-1]["cumulative_usd"]
                    min_apy = tiers[-1]["apy"]
                    if total_available > prev_cumulative and min_apy >= MIN_ANNUAL_RETURN:
                        print(f"     → ${total_available:,.0f} всего (мин APY {int(min_apy * 100)}%)")

            # Показываем информацию о спреде и возможности активной торговли
            spread_info = get_spread_info(poly, opp.token_id)
            if spread_info:
                spread_pct = spread_info.get("spread_pct", 0)
                bid = spread_info.get("best_bid", 0)
                ask = spread_info.get("best_ask", 0)
                bid_liq = spread_info.get("bid_liquidity", 0)
                reason = spread_info.get("reason", "")
                active_ok = spread_info.get("active_trading_ok", False)

                # Символ для быстрого понимания
                symbol = "✓" if active_ok else "✗"

                print(f"   Спред: {spread_pct:.1f}% (bid: {bid:.2f}, ask: {ask:.2f}, bid liquidity: ${bid_liq:.0f})")
                print(f"   Активная торговля: {symbol} {reason}")

    print(f"\n" + "-" * 75)
    print(f"Всего {len(selected)} возможностей")

    return selected  # Возвращаем для сохранения в файл


def save_report_to_markdown(
    opportunities: list[Opportunity],
    poly: 'PolymarketClient',
    output_dir: Path = None,
) -> Path:
    """Сохранить отчёт в markdown файл."""
    if output_dir is None:
        output_dir = Path(__file__).parent / "output"

    output_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    filename = now.strftime("%Y-%m-%d_%H-%M") + "_UTC.md"
    filepath = output_dir / filename

    lines = []
    lines.append(f"# Earthquake Bot Report")
    lines.append(f"")
    lines.append(f"**Время:** {now.strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"")
    lines.append(f"---")
    lines.append(f"")
    lines.append(f"## Торговые возможности")
    lines.append(f"")

    if not opportunities:
        lines.append(f"Нет возможностей с edge > {MIN_EDGE:.0%} и APY > {MIN_ANNUAL_RETURN:.0%}")
    else:
        for i, opp in enumerate(opportunities, 1):
            prob_win = opp.fair_price
            prob_lose = 1 - prob_win

            lines.append(f"### {i}. {opp.event}")
            lines.append(f"")
            lines.append(f"**Ссылка:** https://polymarket.com/event/{opp.event}")
            lines.append(f"")
            lines.append(f"| Параметр | Значение |")
            lines.append(f"|----------|----------|")
            lines.append(f"| Позиция | BUY {opp.side} на '{opp.outcome}' |")
            lines.append(f"| Модель | {opp.fair_price*100:.1f}% |")
            lines.append(f"| Рынок | {opp.market_price*100:.1f}% |")
            lines.append(f"| Edge | {opp.edge*100:+.1f}% |")
            lines.append(f"| Выигрыш | {prob_win*100:.1f}% |")
            lines.append(f"| Проигрыш | {prob_lose*100:.1f}% |")
            lines.append(f"| Дней до резолюции | {opp.remaining_days:.0f} |")
            lines.append(f"| ROI | {opp.expected_return*100:+.1f}% |")
            lines.append(f"| APY | {opp.annual_return*100:+.0f}% |")
            lines.append(f"")

            # Уровни из ордербука
            if poly and opp.token_id:
                tiers = get_orderbook_tiers(poly, opp.token_id, opp.fair_price, opp.remaining_days)
                if tiers:
                    lines.append(f"**Инвестиции по уровням:**")
                    lines.append(f"")
                    lines.append(f"| Сумма | Мин APY |")
                    lines.append(f"|-------|---------|")

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
                            lines.append(f"| ${cumulative:,.0f} | {apy_pct}%+ |")
                            prev_cumulative = cumulative
                            shown += 1
                            if shown >= 5:
                                break

                    lines.append(f"")

    lines.append(f"---")
    lines.append(f"")
    lines.append(f"*Всего {len(opportunities)} возможностей*")

    content = "\n".join(lines)
    filepath.write_text(content, encoding="utf-8")

    return filepath


def execute_trade(
    poly: PolymarketClient,
    opp: Opportunity,
    amount: float,
    debug: bool = True,
) -> bool:
    """Выполнить сделку."""
    print(f"\n{'=' * 50}")
    print(f"СДЕЛКА: BUY {opp.side} ${amount:.2f}")
    print(f"Рынок: {opp.event}")
    print(f"Исход: {opp.outcome}")
    print(f"Edge: {opp.edge*100:+.1f}%")
    print(f"Token ID: {opp.token_id[:30]}...")
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


def check_extended_history() -> tuple[bool, list]:
    """
    Check for extended history events (detected before USGS).

    Returns:
        (has_extended, pending_events)
    """
    try:
        from extended_usgs_client import ExtendedUSGSClient
        extended = ExtendedUSGSClient()
        pending = extended.get_pending_events(min_magnitude=7.0)
        return len(pending) > 0, pending
    except Exception as e:
        print(f"Extended history unavailable: {e}")
        return False, []


def main():
    parser = argparse.ArgumentParser(description="Earthquake Trading Bot")
    parser.add_argument("--debug", action="store_true", help="Режим отладки")
    parser.add_argument("--auto", action="store_true", help="Автоматическая торговля")
    parser.add_argument("--bankroll", type=float, default=230.0, help="Банкролл в USD")
    parser.add_argument("--no-extended", action="store_true", help="Не проверять extended history")
    args = parser.parse_args()

    print("=" * 75)
    print("EARTHQUAKE TRADING BOT")
    print("=" * 75)
    print(f"Время: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Режим: {'AUTO' if args.auto else ('DEBUG' if args.debug else 'ANALYSIS')}")
    print(f"Min Edge: {MIN_EDGE*100:.0f}%  |  Min APY: {MIN_ANNUAL_RETURN*100:.0f}%")

    # Проверяем extended history (события обнаруженные до USGS)
    has_extended = False
    pending_events = []
    if not args.no_extended:
        has_extended, pending_events = check_extended_history()

        if has_extended:
            print("\n" + "!" * 75)
            print("!!! EXTENDED HISTORY: СОБЫТИЯ ОБНАРУЖЕНЫ ДО USGS !!!")
            print("!" * 75)
            for event in pending_events:
                mag = float(event['magnitude'])
                place = event['place'] or 'Unknown'
                sources = event['source_count']
                mins = float(event['minutes_since_detection'])
                print(f"  M{mag:.1f} | {place} | {sources} источников | {mins:.0f} мин назад")
            print("!" * 75)
            print(f"*** У ВАС ЕСТЬ ИНФОРМАЦИОННОЕ ПРЕИМУЩЕСТВО! ***")
            print("*** Эти события ЕЩЁ НЕ В USGS! ***")
            print("!" * 75 + "\n")
        else:
            print("\nExtended history: нет новых событий (все уже в USGS)")

    # Инициализация
    poly = PolymarketClient()
    usgs = USGSClient()

    # Анализ
    print("\nАнализирую рынки...")
    opportunities = run_analysis(poly, usgs)

    # Вывод возможностей с уровнями из ордербука
    selected = print_opportunities(opportunities, args.bankroll, poly)

    # Сохраняем отчёт в markdown
    if selected:
        report_path = save_report_to_markdown(selected, poly)
        print(f"\nОтчёт сохранён: {report_path}")

    # Торговля
    if (args.debug or args.auto) and opportunities:
        allocations = allocate_portfolio(opportunities, args.bankroll)

        if allocations:
            print("\n" + "=" * 75)
            print("ТОРГОВЛЯ")
            print("=" * 75)

            for opp, bet_size in allocations:
                if args.auto:
                    execute_trade(poly, opp, bet_size, debug=False)
                elif args.debug:
                    execute_trade(poly, opp, bet_size, debug=True)

    print("\n" + "=" * 75)
    print("Готово!")


if __name__ == "__main__":
    main()
