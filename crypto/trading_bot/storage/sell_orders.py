"""
Storage for active sell limit orders.
Tracks which positions have pending sell orders on the exchange.
"""

import json
from pathlib import Path
from typing import Optional


class SellOrderStore:
    """Simple JSON store for active sell limit orders."""

    def __init__(self, data_dir: str = None):
        if data_dir is None:
            data_dir = str(Path(__file__).parent.parent / "data")
        self.file_path = Path(data_dir) / "sell_orders.json"
        self._data: dict = {}
        self._load()

    def _load(self):
        if self.file_path.exists():
            try:
                with open(self.file_path) as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, IOError):
                self._data = {}
        else:
            self._data = {}

    def _save_to_disk(self):
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.file_path, "w") as f:
            json.dump(self._data, f, indent=2)

    def get(self, position_id: str) -> Optional[dict]:
        return self._data.get(position_id)

    def save(self, position_id: str, order_id: str, price: float,
             token_id: str, size: float, market_slug: str):
        self._data[position_id] = {
            "order_id": order_id,
            "price": price,
            "token_id": token_id,
            "size": size,
            "market_slug": market_slug,
        }
        self._save_to_disk()

    def remove(self, position_id: str):
        if position_id in self._data:
            del self._data[position_id]
            self._save_to_disk()

    def load_all(self) -> dict:
        return dict(self._data)
