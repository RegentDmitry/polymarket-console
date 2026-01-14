"""
Client for calling Claude API to update markets.
"""

import os
import json
from pathlib import Path
from typing import Optional, Callable

try:
    from anthropic import Anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False


class ClaudeCodeClient:
    """Client for interacting with Claude API."""

    def __init__(self, working_dir: Path):
        self.working_dir = working_dir
        self.api_key = os.getenv("ANTHROPIC_API_KEY")

    def is_available(self) -> bool:
        """Check if Claude API is available."""
        if not ANTHROPIC_AVAILABLE:
            return False
        return self.api_key is not None

    def update_markets_json(
        self,
        json_path: Path,
        output_callback: Optional[Callable[[str], None]] = None
    ) -> tuple[bool, str]:
        """
        Call Claude API to update earthquake_markets.json.

        Args:
            json_path: Path to the JSON file to update
            output_callback: Optional callback to receive real-time output

        Returns:
            Tuple of (success, message)
        """
        if not ANTHROPIC_AVAILABLE:
            return (False, "anthropic package not installed. Run: pip install anthropic")

        if not self.api_key:
            return (False, "ANTHROPIC_API_KEY not set in environment")

        prompt = self._build_update_prompt(json_path)

        try:
            if output_callback:
                output_callback("Подключение к Claude API...")

            client = Anthropic(api_key=self.api_key)

            if output_callback:
                output_callback("Отправка запроса на обновление рынков...")

            # Call Claude API with streaming
            response_text = ""

            with client.messages.stream(
                model="claude-sonnet-4-20250514",
                max_tokens=4096,
                messages=[{
                    "role": "user",
                    "content": prompt
                }]
            ) as stream:
                for text in stream.text_stream:
                    response_text += text
                    if output_callback and text.strip():
                        # Stream output line by line
                        for line in text.split('\n'):
                            if line.strip():
                                output_callback(line)

            if output_callback:
                output_callback("Обновление завершено")

            return (True, "Successfully updated markets JSON via Claude API")

        except Exception as e:
            return (False, f"Error calling Claude API: {e}")

    def _build_update_prompt(self, json_path: Path) -> str:
        """Build the prompt for Claude to update markets."""

        # Read current JSON if exists
        current_json = "{}"
        if json_path.exists():
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    current_json = f.read()
            except:
                pass

        prompt = f"""Обнови файл {json_path} актуальными данными о рынках землетрясений с Polymarket.

Текущее содержимое файла:
```json
{current_json}
```

Задача:
1. Зайди на Polymarket Gamma API (https://gamma-api.polymarket.com/events) и найди все активные рынки про землетрясения (earthquake)
2. Для каждого рынка извлеки:
   - magnitude (магнитуда из названия, например 7.0, 8.0, 9.0)
   - start (дата создания или начала рынка в ISO формате)
   - end (дата окончания рынка в ISO формате)
   - type ("binary" или "count")
   - outcomes (для count-рынков: массив [["название", min, max], ...])

3. Обнови JSON:
   - Добавь новые рынки
   - Обнови изменившиеся рынки
   - Удали закрытые (closed) рынки

4. Верни ТОЛЬКО обновленный JSON в формате:

```json
{{
  "market-slug": {{
    "magnitude": 7.0,
    "start": "2025-12-28T00:00:00Z",
    "end": "2026-01-31T23:59:59Z",
    "type": "binary"
  }},
  "count-market-slug": {{
    "magnitude": 7.0,
    "start": "2026-01-01T00:00:00Z",
    "end": "2026-12-31T23:59:59Z",
    "type": "count",
    "outcomes": [
      ["<5", 0, 4],
      ["5-7", 5, 7],
      ["20+", 20, null]
    ]
  }}
}}
```

В конце выведи краткую статистику: сколько рынков добавлено, обновлено, удалено.

ВАЖНО: Верни JSON, который я смогу сохранить в файл {json_path}. Используй правильное форматирование (indent=2).
"""
        return prompt
