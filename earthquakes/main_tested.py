#!/usr/bin/env python3
"""
Earthquake Trading Bot с правилами выбора модели из бэктеста.

Правила (на основе backtest_1980_2023_intervals.md):
- M7.0+: Интегрированная модель (кроме интервалов <5, 14-16, 2, 7)
- M8.0+: Смешанные правила по интервалам
- M9.0+: Простая модель

Учитывает уже произошедшие события при расчёте вероятностей.

Использование:
    python main_tested.py              # Режим анализа
    python main_tested.py --debug      # Режим отладки
    python main_tested.py --auto       # Автоматическая торговля
"""

import argparse
import math
import sys
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

# Импортируем модели
from main import (
    MIN_EDGE, MIN_ANNUAL_RETURN, MIN_BET_USD, MAX_LIQUIDITY_PCT,
    load_market_configs, Opportunity, kelly_criterion,
    get_orderbook_data, get_orderbook_tiers, get_orderbook_full,
    get_token_id_from_condition, get_spread_info,
    allocate_portfolio, execute_trade,
)
from main_integrated import IntegratedModel
from markets import EARTHQUAKE_ANNUAL_RATES

# Для USGS
from usgs_client import USGSClient
from polymarket_client import PolymarketClient


# ============================================================================
# ПРАВИЛА ВЫБОРА МОДЕЛИ (из бэктеста)
# ============================================================================

# Правила для M7.0+ годовых интервалов (365 дней)
M7_YEAR_RULES = {
    "<5": "simple",
    "5-7": "integrated",
    "8-10": "integrated",
    "11-13": "integrated",
    "14-16": "simple",      # Простая чуть лучше для центра
    "17-19": "integrated",  # +5.5% улучшение!
    "20+": "integrated",
}

# Правила для M7.0+ полугодовых интервалов (182 дня)
M7_HALFYEAR_RULES = {
    "2": "simple",
    "3": "integrated",
    "4": "integrated",
    "5": "integrated",
    "6": "integrated",
    "7": "simple",
    "8+": "integrated",     # +5.2% улучшение
}

# Правила для M7.0+ квартальных интервалов (91 день)
M7_QUARTER_RULES = {
    "0-1": "integrated",
    "2-3": "integrated",
    "4-5": "integrated",
    "6+": "integrated",
}

# Правила для M7.0+ месячных интервалов (30 дней)
M7_MONTH_RULES = {
    "0": "integrated",
    "1": "integrated",
    "2": "integrated",
    "3+": "integrated",
}

# Правила для M8.0+
M8_RULES = {
    # Годовые
    "0": "integrated",      # +6% улучшение
    "1": "consensus",       # +2.6% улучшение
    "2": "integrated",
    "3+": "simple",
    # Полугодовые
    # "1": "integrated",    # (переопределено выше)
    # "2": "simple",
    # "3+": "simple",
    # Бинарные
    "1+": "simple",
    "2+": "simple",
}

# M9.0+ всегда простая модель
M9_RULES = {
    "1+": "simple",
}


def get_model_for_interval(
    magnitude: float,
    period_days: float,
    interval_name: str,
) -> str:
    """
    Определить какую модель использовать для данного интервала.

    Returns:
        "simple", "integrated", или "consensus"
    """
    if magnitude >= 9.0:
        return "simple"  # M9.0+ всегда простая

    if magnitude >= 8.0:
        # M8.0+ — по правилам
        if interval_name in M8_RULES:
            return M8_RULES[interval_name]
        # Для годовых интервалов M8.0+
        if period_days > 300:
            if interval_name == "0":
                return "integrated"
            elif interval_name == "1":
                return "consensus"
            elif interval_name == "2":
                return "integrated"
            else:
                return "simple"
        return "simple"  # По умолчанию простая для M8.0+

    # M7.0+
    if period_days > 300:
        # Годовые интервалы
        return M7_YEAR_RULES.get(interval_name, "integrated")
    elif period_days > 150:
        # Полугодовые
        return M7_HALFYEAR_RULES.get(interval_name, "integrated")
    elif period_days > 60:
        # Квартальные
        return M7_QUARTER_RULES.get(interval_name, "integrated")
    else:
        # Месячные и короче
        return M7_MONTH_RULES.get(interval_name, "integrated")


# ============================================================================
# ПРОСТАЯ МОДЕЛЬ
# ============================================================================

class SimpleModel:
    """Простая модель Пуассона."""

    def __init__(self, magnitude: float):
        self.magnitude = magnitude
        self.annual_rate = EARTHQUAKE_ANNUAL_RATES.get(magnitude, 15.0)

    def predict_range(
        self,
        min_count: int,
        max_count: Optional[int],
        period_days: float,
        current_count: int = 0,
        **kwargs,
    ) -> float:
        """
        P(итого будет в [min_count, max_count]) с учётом current_count.

        Логика:
        - Если current_count > max_count — вероятность 0 (уже перебрали)
        - Если current_count >= min_count и max_count is None — нужно >=0 ещё
        - Иначе нужно ещё от (min_count - current_count) до (max_count - current_count)
        """
        lam = self.annual_rate * (period_days / 365.0)

        # Уже перебрали верхнюю границу?
        if max_count is not None and current_count > max_count:
            return 0.0

        # Сколько ещё нужно событий
        min_additional = max(0, min_count - current_count)

        if max_count is None:
            # Интервал типа "20+" — нужно ещё min_additional или больше
            if min_additional == 0:
                return 1.0  # Уже достигли
            return 1.0 - self._poisson_cdf(min_additional - 1, lam)

        max_additional = max_count - current_count

        if max_additional < 0:
            return 0.0  # Уже перебрали

        if max_additional < min_additional:
            return 0.0  # Невозможный диапазон

        # P(min_additional <= X <= max_additional)
        prob = self._poisson_cdf(max_additional, lam)
        if min_additional > 0:
            prob -= self._poisson_cdf(min_additional - 1, lam)

        return max(0.0, min(1.0, prob))

    def _poisson_pmf(self, k: int, lam: float) -> float:
        if k < 0 or lam <= 0:
            return 0.0
        return (lam ** k) * math.exp(-lam) / math.factorial(k)

    def _poisson_cdf(self, k: int, lam: float) -> float:
        return sum(self._poisson_pmf(i, lam) for i in range(k + 1))


# ============================================================================
# КОНСЕНСУСНАЯ МОДЕЛЬ
# ============================================================================

class ConsensusModel:
    """Консенсусная модель — среднее между простой и интегрированной."""

    def __init__(self, magnitude: float):
        self.simple = SimpleModel(magnitude)
        self.integrated = IntegratedModel(magnitude)

    def predict_range(
        self,
        min_count: int,
        max_count: Optional[int],
        period_days: float,
        current_count: int = 0,
        now: datetime = None,
        recent_events: list = None,
        **kwargs,
    ) -> float:
        simple_prob = self.simple.predict_range(
            min_count, max_count, period_days, current_count
        )
        integrated_prob = self.integrated.probability_count(
            min_count, max_count, period_days, current_count,
            now=now,
            recent_events=recent_events or [],
        )

        # Взвешенное среднее (интегрированная чуть важнее)
        return 0.4 * simple_prob + 0.6 * integrated_prob


# ============================================================================
# ТЕСТИРОВАННАЯ МОДЕЛЬ (выбор по правилам)
# ============================================================================

class TestedModel:
    """
    Модель, выбирающая алгоритм на основе бэктеста.

    Для каждого интервала выбирает лучшую модель по результатам бэктеста.
    """

    def __init__(self, magnitude: float):
        self.magnitude = magnitude
        self.simple = SimpleModel(magnitude)
        self.integrated = IntegratedModel(magnitude)
        self.consensus = ConsensusModel(magnitude)

    def predict_range(
        self,
        min_count: int,
        max_count: Optional[int],
        period_days: float,
        current_count: int = 0,
        interval_name: str = "",
        **kwargs,
    ) -> tuple[float, str]:
        """
        Предсказать вероятность с автоматическим выбором модели.

        Returns:
            (probability, model_used)
        """
        # Определяем какую модель использовать
        model_type = get_model_for_interval(
            self.magnitude, period_days, interval_name
        )

        if model_type == "simple":
            prob = self.simple.predict_range(
                min_count, max_count, period_days, current_count
            )
        elif model_type == "consensus":
            prob = self.consensus.predict_range(
                min_count, max_count, period_days, current_count, **kwargs
            )
        else:  # integrated
            prob = self.integrated.probability_count(
                min_count, max_count, period_days, current_count,
                now=kwargs.get('forecast_date'),
                recent_events=kwargs.get('recent_events', []),
            )

        return prob, model_type


# ============================================================================
# АНАЛИЗ РЫНКОВ
# ============================================================================

@dataclass
class TestedOpportunity(Opportunity):
    """Расширенная возможность с информацией о выборе модели."""
    model_used: str = ""
    simple_prob: float = 0.0
    integrated_prob: float = 0.0


def analyze_market_tested(
    event_slug: str,
    config: dict,
    usgs: USGSClient,
    market_prices: dict[str, float],
    poly: PolymarketClient = None,
) -> list[TestedOpportunity]:
    """Анализировать рынок с тестированной моделью."""
    opportunities = []
    now = datetime.now(timezone.utc)

    magnitude = config["magnitude"]
    start = config["start"]
    end = config["end"]

    # Получаем текущее количество событий
    earthquakes = usgs.get_earthquakes(start, now, magnitude)
    current_count = len(earthquakes)

    # Оставшиеся дни
    remaining_days = max(0, (end - now).total_seconds() / 86400)

    if remaining_days <= 0:
        return []  # Рынок уже завершён

    # Полная длительность рынка (для выбора правил)
    total_days = (end - start).total_seconds() / 86400

    # Создаём модели
    tested_model = TestedModel(magnitude)
    simple_model = SimpleModel(magnitude)
    integrated_model = IntegratedModel(magnitude)

    # Получаем недавние события для ETAS (конвертируем в словари)
    recent_start = now - timedelta(days=30)
    recent_eq_objects = usgs.get_earthquakes(recent_start, now, magnitude - 0.5)
    recent_earthquakes = [
        {'time': eq.time, 'magnitude': eq.magnitude}
        for eq in recent_eq_objects
    ]

    if config["type"] == "count":
        for outcome_name, min_k, max_k in config["outcomes"]:
            if outcome_name not in market_prices:
                continue

            # Проверяем, возможен ли ещё этот интервал
            if max_k is not None and current_count > max_k:
                # Уже перебрали — вероятность 0
                fair_yes = 0.0
                model_used = "impossible"
            elif min_k is not None and current_count >= min_k and max_k is None:
                # Интервал типа "20+" и уже достигли — нужно не провалиться ниже
                # Но "20+" означает 20 или больше в конце, так что если уже 20+, вероятность = 1
                fair_yes = 1.0
                model_used = "certain"
            else:
                # Используем тестированную модель
                fair_yes, model_used = tested_model.predict_range(
                    min_k, max_k, remaining_days, current_count,
                    interval_name=outcome_name,
                    forecast_date=now,
                    recent_events=recent_earthquakes,
                )

            # Также считаем простую и интегрированную для сравнения
            simple_prob = simple_model.predict_range(
                min_k, max_k, remaining_days, current_count
            )
            integrated_prob = integrated_model.probability_count(
                min_k, max_k, remaining_days, current_count,
                recent_events=recent_earthquakes,
                now=now,
            )

            mkt_yes = market_prices[outcome_name]
            mkt_no = 1 - mkt_yes

            # Edge для YES
            edge_yes = fair_yes - mkt_yes
            odds = (1 - mkt_yes) / mkt_yes if mkt_yes > 0 else 0
            opp = TestedOpportunity(
                event=event_slug,
                outcome=outcome_name,
                side="YES",
                token_id="",
                fair_price=fair_yes,
                market_price=mkt_yes,
                edge=edge_yes,
                kelly=kelly_criterion(fair_yes, odds) if edge_yes > 0 else 0,
                current_count=current_count,
                lambda_used=remaining_days,
                remaining_days=remaining_days,
                model_used=model_used,
                simple_prob=simple_prob,
                integrated_prob=integrated_prob,
            )
            opportunities.append(opp)

            # Edge для NO
            fair_no = 1 - fair_yes
            edge_no = fair_no - mkt_no
            odds_no = (1 - mkt_no) / mkt_no if mkt_no > 0 else 0
            opp = TestedOpportunity(
                event=event_slug,
                outcome=outcome_name,
                side="NO",
                token_id="",
                fair_price=fair_no,
                market_price=mkt_no,
                edge=edge_no,
                kelly=kelly_criterion(fair_no, odds_no) if edge_no > 0 else 0,
                current_count=current_count,
                lambda_used=remaining_days,
                remaining_days=remaining_days,
                model_used=model_used,
                simple_prob=1 - simple_prob,
                integrated_prob=1 - integrated_prob,
            )
            opportunities.append(opp)

    elif config["type"] == "binary":
        # Бинарный рынок (будет/не будет хотя бы одно событие)
        if current_count > 0:
            fair_yes = 1.0
            model_used = "certain"
        else:
            fair_yes, model_used = tested_model.predict_range(
                1, None, remaining_days, current_count,
                interval_name="1+",
                forecast_date=now,
                recent_events=recent_earthquakes,
            )

        simple_prob = simple_model.predict_range(1, None, remaining_days, current_count)
        integrated_prob = integrated_model.probability_count(
            1, None, remaining_days, current_count,
            now=now,
            recent_events=recent_earthquakes,
        )

        for outcome_name in ["Yes", "YES"]:
            if outcome_name in market_prices:
                mkt_yes = market_prices[outcome_name]
                mkt_no = 1 - mkt_yes

                # YES side
                edge_yes = fair_yes - mkt_yes
                odds = (1 - mkt_yes) / mkt_yes if mkt_yes > 0 else 0
                opp = TestedOpportunity(
                    event=event_slug,
                    outcome="Yes",
                    side="YES",
                    token_id="",
                    fair_price=fair_yes,
                    market_price=mkt_yes,
                    edge=edge_yes,
                    kelly=kelly_criterion(fair_yes, odds) if edge_yes > 0 else 0,
                    current_count=current_count,
                    lambda_used=remaining_days,
                    remaining_days=remaining_days,
                    model_used=model_used,
                    simple_prob=simple_prob,
                    integrated_prob=integrated_prob,
                )
                opportunities.append(opp)

                # NO side
                fair_no = 1 - fair_yes
                edge_no = fair_no - mkt_no
                odds_no = (1 - mkt_no) / mkt_no if mkt_no > 0 else 0
                opp = TestedOpportunity(
                    event=event_slug,
                    outcome="No",
                    side="NO",
                    token_id="",
                    fair_price=fair_no,
                    market_price=mkt_no,
                    edge=edge_no,
                    kelly=kelly_criterion(fair_no, odds_no) if edge_no > 0 else 0,
                    current_count=current_count,
                    lambda_used=remaining_days,
                    remaining_days=remaining_days,
                    model_used=model_used,
                    simple_prob=1 - simple_prob,
                    integrated_prob=1 - integrated_prob,
                )
                opportunities.append(opp)
                break  # Found the outcome, no need to check other variants

    return opportunities


def calculate_usable_liquidity_from_tiers(tiers: list[dict], fair_price: float,
                                          remaining_days: float, min_edge: float,
                                          min_apy: float) -> float:
    """
    Рассчитать usable liquidity из уже полученных tiers (без запроса).

    Args:
        tiers: Уровни ордербука из get_orderbook_full()
        fair_price: Справедливая цена по модели
        remaining_days: Дней до резолюции
        min_edge: Минимальный edge
        min_apy: Минимальный APY

    Returns:
        Сумма в USD, которую можно купить с edge >= min_edge и APY >= min_apy
    """
    usable = 0.0

    for tier in tiers:
        price = tier["price"]
        size_usd = tier["size_usd"]

        # Рассчитываем edge и APY для этой цены
        edge = fair_price - price
        roi = (fair_price - price) / price if price > 0 else 0
        apy = roi * (365 / remaining_days) if remaining_days > 0 else 0

        # Если проходит фильтры - добавляем
        if edge >= min_edge and apy >= min_apy:
            usable += size_usd
        else:
            # Дальше цены только хуже - выходим
            break

    return usable


def get_usable_liquidity(poly: PolymarketClient, token_id: str,
                         fair_price: float, remaining_days: float,
                         min_edge: float, min_apy: float) -> float:
    """
    Рассчитать ликвидность, доступную по ценам, проходящим фильтры.

    DEPRECATED: Используйте calculate_usable_liquidity_from_tiers() с get_orderbook_full()
    для избежания дублирующихся запросов.

    Args:
        poly: Polymarket клиент
        token_id: ID токена
        fair_price: Справедливая цена по модели
        remaining_days: Дней до резолюции
        min_edge: Минимальный edge
        min_apy: Минимальный APY

    Returns:
        Сумма в USD, которую можно купить с edge >= min_edge и APY >= min_apy
    """
    if not token_id:
        return 0.0

    tiers = get_orderbook_tiers(poly, token_id, fair_price, remaining_days)
    return calculate_usable_liquidity_from_tiers(tiers, fair_price, remaining_days, min_edge, min_apy)


def process_opportunity_orderbook(opp: 'TestedOpportunity', poly: PolymarketClient,
                                  min_edge: float, min_apy: float,
                                  clob_cache: dict) -> 'TestedOpportunity':
    """
    Обработать одну возможность - получить данные ордербука.
    Используется для параллельной обработки.

    ОПТИМИЗИРОВАНО v2:
    - Кеширование get_clob_market результатов
    - get_orderbook_full() за ОДИН запрос вместо двух (get_orderbook_data + get_usable_liquidity)

    Args:
        opp: Возможность для обработки
        poly: Polymarket клиент
        min_edge: Минимальный edge
        min_apy: Минимальный APY
        clob_cache: Словарь для кеширования результатов get_clob_market

    Returns:
        Обновлённая возможность
    """
    if opp.condition_id:
        outcome_to_check = "Yes" if opp.side == "YES" else "No"

        # Получаем token_id с кешированием
        cache_key = f"{opp.condition_id}:{outcome_to_check}"
        if cache_key in clob_cache:
            token_id = clob_cache[cache_key]
        else:
            token_id = get_token_id_from_condition(poly, opp.condition_id, outcome_to_check)
            clob_cache[cache_key] = token_id

        if token_id:
            opp.token_id = token_id

            # Получаем ВСЕ данные ордербука за ОДИН запрос
            best_ask, liquidity, tiers = get_orderbook_full(
                poly, token_id, opp.fair_price, opp.remaining_days
            )

            opp.liquidity_usd = liquidity

            if best_ask is not None and best_ask > 0:
                opp.market_price = best_ask
                opp.edge = opp.fair_price - opp.market_price

                if opp.market_price > 0 and opp.market_price < 1:
                    odds = (1 - opp.market_price) / opp.market_price
                    opp.kelly = kelly_criterion(opp.fair_price, odds)
                else:
                    opp.kelly = 0

                # Рассчитываем usable liquidity из уже полученных tiers (БЕЗ нового запроса!)
                opp.usable_liquidity = calculate_usable_liquidity_from_tiers(
                    tiers, opp.fair_price, opp.remaining_days, min_edge, min_apy
                )

    return opp


def run_analysis(poly: PolymarketClient, usgs: USGSClient,
                 progress_callback=None,
                 min_edge: float = MIN_EDGE,
                 min_apy: float = MIN_ANNUAL_RETURN) -> list[TestedOpportunity]:
    """Запустить анализ всех рынков."""
    all_opportunities = []

    # Загружаем конфиги из JSON (hot-reload при каждом скане)
    market_configs = load_market_configs()

    # Получаем данные с Polymarket
    if progress_callback:
        progress_callback("Fetching prices...")
    all_prices = poly.get_all_earthquake_prices()

    # Count events to analyze
    events_to_analyze = [slug for slug in all_prices.keys() if slug in market_configs]
    total_events = len(events_to_analyze)
    current_event = 0

    for event_slug, markets in all_prices.items():
        if event_slug not in market_configs:
            continue

        current_event += 1
        if progress_callback:
            progress_callback(f"Analyzing {current_event}/{total_events}...")

        config = market_configs[event_slug]

        # Собираем рыночные цены и condition_ids
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

        # Анализируем
        opps = analyze_market_tested(event_slug, config, usgs, market_prices, poly)

        # Добавляем token_id и condition_id
        for opp in opps:
            key = (opp.outcome, opp.side)
            opp.token_id = token_ids.get(key, "")

            if opp.outcome in condition_ids:
                opp.condition_id = condition_ids[opp.outcome]
            elif "Yes" in condition_ids:
                opp.condition_id = condition_ids["Yes"]

        all_opportunities.extend(opps)

    # Получаем реальные цены из ордербука (параллельно!)
    if progress_callback:
        progress_callback(f"Checking orderbooks ({len(all_opportunities)})...")
    else:
        print("Проверяю ордербуки...")

    # Кеш для get_clob_market результатов (thread-safe благодаря GIL)
    clob_cache = {}

    # Параллельная обработка ордербуков (до 10 одновременно)
    updated_opportunities = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        # Запускаем все задачи
        future_to_opp = {
            executor.submit(process_opportunity_orderbook, opp, poly, min_edge, min_apy, clob_cache): opp
            for opp in all_opportunities
        }

        # Собираем результаты по мере готовности
        for future in as_completed(future_to_opp):
            try:
                updated_opp = future.result()
                updated_opportunities.append(updated_opp)
            except Exception as e:
                # В случае ошибки добавляем оригинальную возможность
                original_opp = future_to_opp[future]
                updated_opportunities.append(original_opp)
                print(f"Error processing {original_opp.event_slug}: {e}")

    all_opportunities = updated_opportunities

    # Сортируем по годовой доходности (лучшие первыми)
    all_opportunities.sort(key=lambda x: x.annual_return, reverse=True)

    return all_opportunities


def print_opportunities(opportunities: list[TestedOpportunity], poly: PolymarketClient = None):
    """Вывести возможности с информацией о модели."""
    print("\n" + "=" * 80)
    print("ТОРГОВЫЕ ВОЗМОЖНОСТИ (Тестированная модель)")
    print("=" * 80)

    if not opportunities:
        print(f"\nНет возможностей с edge > {MIN_EDGE:.0%} и APY > {MIN_ANNUAL_RETURN:.0%}")
        return

    # Группируем по событию
    best_per_event: dict[str, TestedOpportunity] = {}
    for opp in opportunities:
        if opp.event not in best_per_event or opp.annual_return > best_per_event[opp.event].annual_return:
            best_per_event[opp.event] = opp

    selected = list(best_per_event.values())
    selected.sort(key=lambda x: (x.annual_return, x.expected_return), reverse=True)

    for i, opp in enumerate(selected, 1):
        url = f"https://polymarket.com/event/{opp.event}"
        model_tag = opp.model_used.upper()

        print(f"\n{i}. {url}")
        print(f"   BUY {opp.side} на '{opp.outcome}'")
        print(f"   Модель: {opp.fair_price*100:.1f}% ({model_tag})  |  Рынок: {opp.market_price*100:.1f}%  |  Edge: {opp.edge*100:+.1f}%")
        print(f"   Событий: {opp.current_count}  |  Дней: {opp.remaining_days:.0f}  |  ROI: {opp.expected_return*100:+.1f}%  |  APY: {opp.annual_return*100:+.0f}%")

        # Уровни из ордербука
        if poly and opp.token_id:
            tiers = get_orderbook_tiers(poly, opp.token_id, opp.fair_price, opp.remaining_days)
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

            # Показываем информацию о спреде и возможности активной торговли
            spread_info = get_spread_info(poly, opp.token_id)
            if spread_info:
                spread_pct = spread_info.get("spread_pct", 0)
                bid = spread_info.get("best_bid", 0)
                ask = spread_info.get("best_ask", 0)
                bid_liq = spread_info.get("bid_liquidity", 0)
                reason = spread_info.get("reason", "")
                active_ok = spread_info.get("active_trading_ok", False)

                symbol = "✓" if active_ok else "✗"

                print(f"   Спред: {spread_pct:.1f}% (bid: {bid:.2f}, ask: {ask:.2f}, bid liquidity: ${bid_liq:.0f})")
                print(f"   Активная торговля: {symbol} {reason}")

    print(f"\n" + "-" * 80)
    print(f"Всего {len(selected)} возможностей")

    return selected


def save_report_to_markdown(
    opportunities: list[TestedOpportunity],
    poly: PolymarketClient,
    output_dir: Path = None,
) -> Path:
    """Сохранить отчёт в markdown файл."""
    if output_dir is None:
        output_dir = Path(__file__).parent / "output"

    output_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    filename = now.strftime("%Y-%m-%d_%H-%M") + "_tested_UTC.md"
    filepath = output_dir / filename

    lines = []
    lines.append(f"# Earthquake Bot Report (Tested Model)")
    lines.append(f"")
    lines.append(f"**Время:** {now.strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"")
    lines.append(f"## Правила выбора модели")
    lines.append(f"")
    lines.append(f"- **M7.0+**: Интегрированная (кроме интервалов <5, 14-16, 2, 7)")
    lines.append(f"- **M8.0+**: Смешанные правила")
    lines.append(f"- **M9.0+**: Простая")
    lines.append(f"")
    lines.append(f"---")
    lines.append(f"")
    lines.append(f"## Торговые возможности")
    lines.append(f"")

    if not opportunities:
        lines.append(f"Нет возможностей с edge > {MIN_EDGE:.0%} и APY > {MIN_ANNUAL_RETURN:.0%}")
    else:
        for i, opp in enumerate(opportunities, 1):
            lines.append(f"### {i}. {opp.event}")
            lines.append(f"")
            lines.append(f"**Ссылка:** https://polymarket.com/event/{opp.event}")
            lines.append(f"")
            lines.append(f"| Параметр | Значение |")
            lines.append(f"|----------|----------|")
            lines.append(f"| Позиция | BUY {opp.side} на '{opp.outcome}' |")
            lines.append(f"| Модель | {opp.fair_price*100:.1f}% ({opp.model_used.upper()}) |")
            lines.append(f"| Рынок | {opp.market_price*100:.1f}% |")
            lines.append(f"| Edge | {opp.edge*100:+.1f}% |")
            lines.append(f"| Событий | {opp.current_count} |")
            lines.append(f"| Дней | {opp.remaining_days:.0f} |")
            lines.append(f"| ROI | {opp.expected_return*100:+.1f}% |")
            lines.append(f"| APY | {opp.annual_return*100:+.0f}% |")
            lines.append(f"")

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


def main():
    parser = argparse.ArgumentParser(description="Earthquake Trading Bot (Tested Model)")
    parser.add_argument("--debug", action="store_true", help="Режим отладки")
    parser.add_argument("--auto", action="store_true", help="Автоматическая торговля")
    parser.add_argument("--bankroll", type=float, default=230.0, help="Банкролл в USD")
    args = parser.parse_args()

    print("=" * 80)
    print("EARTHQUAKE TRADING BOT — TESTED MODEL")
    print("=" * 80)
    print(f"Время: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Режим: {'AUTO' if args.auto else ('DEBUG' if args.debug else 'ANALYSIS')}")
    print(f"")
    print(f"Правила выбора модели (из бэктеста):")
    print(f"  M7.0+: Интегрированная (кроме <5, 14-16, 2, 7)")
    print(f"  M8.0+: Смешанные правила по интервалам")
    print(f"  M9.0+: Простая")
    print(f"")
    print(f"Min Edge: {MIN_EDGE*100:.0f}%  |  Min APY: {MIN_ANNUAL_RETURN*100:.0f}%")

    # Инициализация
    poly = PolymarketClient()
    usgs = USGSClient()

    # Анализ
    print("\nАнализирую рынки...")
    opportunities = run_analysis(poly, usgs)

    # Вывод
    selected = print_opportunities(opportunities, poly)

    # Сохраняем отчёт
    if selected:
        report_path = save_report_to_markdown(selected, poly)
        print(f"\nОтчёт сохранён: {report_path}")

    # Торговля
    if (args.debug or args.auto) and opportunities:
        allocations = allocate_portfolio(opportunities, args.bankroll)

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
