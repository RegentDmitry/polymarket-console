"""
Скрипт для создания API credentials Polymarket.
Запустите один раз для генерации ключей.
"""

import os
import sys
from pathlib import Path

# Добавляем родительскую директорию в path для импорта polymarket_console
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

# Загружаем .env
env_path = Path(__file__).parent / ".env"
load_dotenv(env_path)

from polymarket_console import ClobClient


def main():
    pk = os.getenv("PK")
    if not pk:
        print("Ошибка: PK не найден в .env")
        return

    chain_id = int(os.getenv("CHAIN_ID", "137"))
    signature_type = int(os.getenv("SIGNATURE_TYPE", "1"))
    host = os.getenv("CLOB_API_URL", "https://clob.polymarket.com")

    print(f"Подключаюсь к {host}...")
    print(f"Chain ID: {chain_id}")
    print(f"Signature Type: {signature_type} (Magic wallet)")

    try:
        client = ClobClient(
            host=host,
            key=pk,
            chain_id=chain_id,
            signature_type=signature_type,
        )

        # Проверяем подключение
        ok = client.get_ok()
        print(f"Сервер доступен: {ok}")

        # Получаем адрес кошелька
        address = client.get_address()
        print(f"Адрес кошелька: {address}")

        # Создаём или получаем API credentials
        print("\nСоздаю API credentials...")
        creds = client.create_or_derive_api_creds()

        if creds:
            print("\n" + "=" * 50)
            print("API CREDENTIALS СОЗДАНЫ!")
            print("=" * 50)
            print(f"API_KEY: {creds.api_key}")
            print(f"SECRET: {creds.api_secret}")
            print(f"PASSPHRASE: {creds.api_passphrase}")
            print("=" * 50)

            # Обновляем .env файл
            update_env_file(env_path, creds)
            print("\n.env файл обновлён!")

            # Проверяем что credentials работают
            client.set_api_creds(creds)
            print("\nПроверяю доступ к аккаунту...")

            # Пробуем получить открытые ордера (требует L2 auth)
            try:
                orders = client.get_orders()
                print(f"Открытых ордеров: {len(orders)}")
                print("L2 аутентификация работает!")
            except Exception as e:
                print(f"Ошибка L2: {e}")

        else:
            print("Не удалось создать credentials")

    except Exception as e:
        print(f"Ошибка: {e}")
        import traceback
        traceback.print_exc()


def update_env_file(env_path: Path, creds):
    """Обновляет .env файл с новыми credentials."""
    content = env_path.read_text()

    # Заменяем пустые значения на полученные
    content = content.replace("CLOB_API_KEY=", f"CLOB_API_KEY={creds.api_key}")
    content = content.replace("CLOB_SECRET=", f"CLOB_SECRET={creds.api_secret}")
    content = content.replace("CLOB_PASS_PHRASE=", f"CLOB_PASS_PHRASE={creds.api_passphrase}")

    env_path.write_text(content)


if __name__ == "__main__":
    main()
