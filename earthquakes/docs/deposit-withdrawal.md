# Deposit & Withdrawal — Earthquake Trading Bot

## Кошелёк бота

- **Тип**: EOA (Externally Owned Account) — обычный кошелёк, управляемый приватным ключом
- **Адрес**: `0xff36fc6De4CCDd290C14EE69244c21c1803Ad5b7`
- **Сеть**: Polygon (chain_id: 137)
- **Приватный ключ**: хранится в `earthquakes/.env` (переменная `PK`)
- **FUNDER**: закомментирован — бот торгует напрямую с EOA, а не через proxy wallet Polymarket

## Что лежит на кошельке

| Актив | Контракт | Описание |
|-------|----------|----------|
| USDC.e (bridged) | `0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174` | Основной токен для торговли бота |
| USDC (native) | `0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359` | Приходит при выводе с Polymarket UI — **бот НЕ использует** |
| MATIC (POL) | нативный | Газ для транзакций на Polygon |
| Conditional Tokens | по рынку | Открытые позиции (покупки на Polymarket) |

> **ВАЖНО:** На Polygon существуют ДВА токена USDC — bridged (USDC.e) и native (USDC). Бот работает только с USDC.e. Если на кошельке есть native USDC (например, после вывода с Polymarket), бот его не увидит и не сможет использовать. Нужен swap через DEX (см. ниже).

## Пополнение (Deposit)

### USDC.e (для торговли)
1. Отправить **USDC.e** на адрес `0xff36fc6De4CCDd290C14EE69244c21c1803Ad5b7`
2. **Обязательно сеть Polygon** (не Ethereum mainnet, не Arbitrum и т.д.)
3. USDC.e контракт: `0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174`
4. **НЕ отправляй native USDC** (`0x3c499c...`) — бот его не использует

### Если на кошельке оказался native USDC
Polymarket UI при выводе отправляет **native USDC**, а не USDC.e. Чтобы бот мог использовать эти средства, нужен swap:

**Вариант 1: Через DEX (QuickSwap/Uniswap)**
1. Импортировать PK в MetaMask
2. Открыть https://quickswap.exchange/#/swap
3. Swap USDC (native) → USDC.e, 1:1

**Вариант 2: Скриптом** (уже есть в проекте)
```bash
# На сервере:
cd /opt/polymarket/earthquakes && POLYGON_RPC=https://polygon-bor-rpc.publicnode.com .venv/bin/python scripts/swap_usdc.py

# Локально:
POLYGON_RPC=https://polygon-bor-rpc.publicnode.com .venv/bin/python earthquakes/scripts/swap_usdc.py
```
Скрипт `earthquakes/scripts/swap_usdc.py` — свапает ВЕСЬ native USDC → USDC.e через Uniswap V3 (пул 0.05%, slippage 0.5%). Использует PK из `.env`.

### MATIC/POL (для газа)
1. Отправить **MATIC** на тот же адрес `0xff36fc6De4CCDd290C14EE69244c21c1803Ad5b7`
2. Сеть: Polygon
3. Нужно ~0.5-1 MATIC для комфортной торговли (сейчас 17.63 — хватает надолго)

## Вывод (Withdrawal)

### Вариант 1: Скрипт (программно)
Поскольку кошелёк EOA, можно подписать транзакцию перевода USDC через PK из `.env`:
```python
from web3 import Web3

w3 = Web3(Web3.HTTPProvider("https://polygon-rpc.com"))
account = w3.eth.account.from_key(PK)

# USDC на Polygon
usdc = w3.eth.contract(
    address="0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
    abi=[{"name":"transfer","type":"function","inputs":[{"name":"to","type":"address"},{"name":"amount","type":"uint256"}],"outputs":[{"name":"","type":"bool"}]}]
)

tx = usdc.functions.transfer(DESTINATION_ADDRESS, AMOUNT_IN_6_DECIMALS).build_transaction({
    "from": account.address,
    "nonce": w3.eth.get_transaction_count(account.address),
    "gas": 100000,
    "gasPrice": w3.eth.gas_price,
})
signed = account.sign_transaction(tx)
tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
```

### Вариант 2: Импорт в MetaMask
1. Импортировать PK из `.env` в MetaMask (Import Account → Private Key)
2. Переключить сеть на Polygon
3. Отправить USDC через интерфейс MetaMask

## Важно

- **Polymarket UI не показывает баланс этого кошелька** — сайт привязан к proxy wallet аккаунта, а бот торгует с отдельного EOA
- **Позиции на сайте не видны** — conditional tokens лежат на EOA, а не на proxy wallet
- Свободный USDC можно вывести в любой момент, но conditional tokens станут USDC только после разрешения рынков (win) или продажи (sell order исполнен)
- **Не делись PK** — он даёт полный контроль над кошельком и всеми средствами
