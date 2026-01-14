"""
Клиент для получения данных о рынках и торговли на Polymarket.
Обёртка над polymarket_console с фокусом на earthquake markets.
"""

import os
import sys
import json
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from polymarket_console import ClobClient
from polymarket_console.clob_types import ApiCreds, OrderArgs, MarketOrderArgs, OrderType
from polymarket_console.order_builder.constants import BUY, SELL


# Gamma API для поиска рынков
GAMMA_API_URL = "https://gamma-api.polymarket.com"
# Data API для позиций и истории
DATA_API_URL = "https://data-api.polymarket.com"


@dataclass
class MarketPrice:
    """Цены на рынке для одного исхода."""
    outcome_name: str
    token_id: str
    yes_price: float  # Цена покупки YES (0-1)
    no_price: float   # Цена покупки NO (0-1)
    volume: Optional[float] = None
    closed: bool = False


@dataclass
class MarketData:
    """Полные данные о рынке."""
    condition_id: str
    question: str
    slug: str
    outcomes: list[MarketPrice]
    end_date: Optional[str] = None
    active: bool = True
    neg_risk_market_id: Optional[str] = None


class PolymarketClient:
    """Клиент для работы с Polymarket."""

    def __init__(self, env_path: Optional[Path] = None):
        """Инициализация клиента."""
        if env_path is None:
            env_path = Path(__file__).parent / ".env"
        load_dotenv(env_path)

        self.host = os.getenv("CLOB_API_URL", "https://clob.polymarket.com")
        self.chain_id = int(os.getenv("CHAIN_ID", "137"))
        self.signature_type = int(os.getenv("SIGNATURE_TYPE", "1"))
        self.pk = os.getenv("PK")

        self.client = ClobClient(
            host=self.host,
            key=self.pk,
            chain_id=self.chain_id,
            signature_type=self.signature_type,
        )

        # Загружаем API credentials если есть
        api_key = os.getenv("CLOB_API_KEY")
        api_secret = os.getenv("CLOB_SECRET")
        api_passphrase = os.getenv("CLOB_PASS_PHRASE")

        if api_key and api_secret and api_passphrase:
            creds = ApiCreds(
                api_key=api_key,
                api_secret=api_secret,
                api_passphrase=api_passphrase,
            )
            self.client.set_api_creds(creds)

    def get_address(self) -> str:
        """Получить адрес кошелька."""
        return self.client.get_address()

    def search_earthquake_markets(self) -> list[dict]:
        """
        Найти все earthquake события через Gamma API.

        Returns:
            Список событий с рынками
        """
        # Список известных earthquake event slugs (только активные)
        earthquake_slugs = [
            # M7.0+ рынки
            "how-many-7pt0-or-above-earthquakes-by-june-30",
            "how-many-7pt0-or-above-earthquakes-in-2026",
            "another-7pt0-or-above-earthquake-by-555",
            # M9.0+ / M10.0+ рынки
            "9pt0-or-above-earthquake-before-2027",
            "10pt0-or-above-earthquake-before-2027",
            # Megaquake (M8.0+) рынки
            "megaquake-by-january-31",
            "megaquake-by-march-31",
            "megaquake-by-june-30",
        ]

        all_events = []
        for slug in earthquake_slugs:
            try:
                event = self.get_event_by_slug(slug)
                if event:
                    all_events.append(event)
            except Exception as e:
                print(f"Ошибка получения {slug}: {e}")

        return all_events

    def get_event_by_slug(self, slug: str) -> Optional[dict]:
        """
        Получить событие (event) по slug через Gamma API.

        Args:
            slug: Slug события, например "how-many-7pt0-or-above-earthquakes-by-june-30"

        Returns:
            Данные события включая все рынки (markets)
        """
        try:
            response = httpx.get(
                f"{GAMMA_API_URL}/events",
                params={"slug": slug},
                timeout=30,
            )
            response.raise_for_status()
            events = response.json()
            return events[0] if events else None
        except Exception as e:
            print(f"Ошибка Gamma API для {slug}: {e}")
            return None

    def get_market_prices_from_event(self, event: dict) -> list[MarketData]:
        """
        Извлечь данные о ценах из события Gamma API.

        Args:
            event: Данные события от Gamma API

        Returns:
            Список MarketData для каждого рынка в событии
        """
        result = []
        markets = event.get("markets", [])
        neg_risk_market_id = event.get("negRiskMarketID")

        for market in markets:
            condition_id = market.get("conditionId")
            question = market.get("question", "")
            slug = market.get("slug", "")
            end_date = market.get("endDateIso")
            active = market.get("active", True)
            closed = market.get("closed", False)

            # Парсим цены
            outcome_prices = json.loads(market.get("outcomePrices", "[]"))
            outcomes_raw = json.loads(market.get("outcomes", "[]"))
            token_ids = json.loads(market.get("clobTokenIds", "[]"))

            outcomes = []
            for i, outcome_name in enumerate(outcomes_raw):
                price = float(outcome_prices[i]) if i < len(outcome_prices) else 0
                token_id = token_ids[i] if i < len(token_ids) else ""

                outcomes.append(MarketPrice(
                    outcome_name=outcome_name,
                    token_id=token_id,
                    yes_price=price,
                    no_price=1 - price,
                    volume=market.get("volumeNum"),
                    closed=closed,
                ))

            result.append(MarketData(
                condition_id=condition_id,
                question=question,
                slug=slug,
                outcomes=outcomes,
                end_date=end_date,
                active=active and not closed,
                neg_risk_market_id=neg_risk_market_id,
            ))

        return result

    def get_all_earthquake_prices(self) -> dict[str, list[MarketData]]:
        """
        Получить цены всех earthquake рынков.

        Returns:
            Словарь {event_slug: [MarketData, ...]}
        """
        result = {}
        events = self.search_earthquake_markets()

        for event in events:
            slug = event.get("slug", "unknown")
            markets = self.get_market_prices_from_event(event)
            result[slug] = markets

        return result

    def get_clob_market(self, condition_id: str) -> Optional[dict]:
        """
        Получить данные рынка из CLOB API по condition_id.
        Возвращает полные token_id, которые нужны для ордербука.
        """
        try:
            response = httpx.get(
                f"{self.host}/markets/{condition_id}",
                timeout=30,
            )
            if response.status_code == 200:
                return response.json()
            return None
        except Exception:
            return None

    def get_orderbook(self, token_id: str) -> dict:
        """Получить ордербук для токена."""
        return self.client.get_order_book(token_id)

    def get_orderbook_by_condition(self, condition_id: str) -> dict:
        """
        Получить ордербуки для всех токенов рынка по condition_id.

        Returns:
            dict: {outcome_name: {"asks": [...], "bids": [...], "token_id": ...}}
        """
        result = {}
        market = self.get_clob_market(condition_id)
        if not market:
            return result

        for token in market.get("tokens", []):
            token_id = token.get("token_id")
            outcome = token.get("outcome")
            try:
                ob = self.client.get_order_book(token_id)
                result[outcome] = {
                    "asks": ob.get("asks", []),
                    "bids": ob.get("bids", []),
                    "token_id": token_id,
                }
            except Exception:
                result[outcome] = {"asks": [], "bids": [], "token_id": token_id}

        return result

    def get_balance(self) -> dict:
        """Получить баланс аккаунта."""
        from polymarket_console.clob_types import BalanceAllowanceParams, AssetType

        params = BalanceAllowanceParams(
            asset_type=AssetType.COLLATERAL,
            signature_type=self.signature_type,
        )
        return self.client.get_balance_allowance(params)

    def create_limit_order(
        self,
        token_id: str,
        side: str,  # "BUY" or "SELL"
        price: float,
        size: float,
    ) -> dict:
        """
        Создать лимитный ордер.

        Args:
            token_id: ID токена
            side: "BUY" или "SELL"
            price: Цена (0-1)
            size: Количество токенов

        Returns:
            Результат размещения ордера
        """
        order_args = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
            side=BUY if side == "BUY" else SELL,
        )

        signed_order = self.client.create_order(order_args)
        return self.client.post_order(signed_order, OrderType.GTC)

    def create_market_order(
        self,
        token_id: str,
        side: str,
        amount: float,  # В долларах для BUY, в токенах для SELL
    ) -> dict:
        """
        Создать маркет-ордер.

        Args:
            token_id: ID токена
            side: "BUY" или "SELL"
            amount: Сумма в USD (для BUY) или количество токенов (для SELL)

        Returns:
            Результат размещения ордера
        """
        order_args = MarketOrderArgs(
            token_id=token_id,
            amount=amount,
            side=BUY if side == "BUY" else SELL,
        )

        signed_order = self.client.create_market_order(order_args)
        return self.client.post_order(signed_order, OrderType.FOK)

    def get_open_orders(self) -> list:
        """Получить открытые ордера."""
        return self.client.get_orders()

    def cancel_order(self, order_id: str) -> dict:
        """Отменить ордер."""
        return self.client.cancel(order_id)

    def cancel_all_orders(self) -> dict:
        """Отменить все ордера."""
        return self.client.cancel_all()

    def get_positions(self) -> list[dict]:
        """
        Получить все позиции пользователя через Data API.

        Returns:
            Список позиций с полями:
            - asset: token_id
            - size: количество токенов
            - avgCost: средняя цена входа
            - currentValue: текущая стоимость
            - profit: P&L
            - market: информация о рынке
        """
        address = self.get_address()
        if not address:
            return []

        try:
            response = httpx.get(
                f"{DATA_API_URL}/positions",
                params={"user": address},
                timeout=30,
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Ошибка получения позиций: {e}")
            return []


if __name__ == "__main__":
    print("Тестирование Polymarket Client...")
    print("=" * 60)

    client = PolymarketClient()
    print(f"Адрес: {client.get_address()}")

    print("\nПолучаю все earthquake рынки...")
    all_prices = client.get_all_earthquake_prices()

    for event_slug, markets in all_prices.items():
        print(f"\n{'=' * 60}")
        print(f"EVENT: {event_slug}")
        print("=" * 60)

        for market in markets:
            status = "CLOSED" if not market.active else "ACTIVE"
            print(f"\n  [{status}] {market.question[:55]}...")

            for outcome in market.outcomes:
                if outcome.closed:
                    print(f"    {outcome.outcome_name}: RESOLVED")
                else:
                    print(f"    {outcome.outcome_name}: YES={outcome.yes_price*100:.1f}%  NO={outcome.no_price*100:.1f}%")
