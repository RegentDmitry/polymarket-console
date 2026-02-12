# Server Deployment

Деплой ботов на удалённый Linux сервер.

## Сервер

- **IP:** 62.112.10.73
- **User:** root
- **Path:** /opt/polymarket/earthquakes

## SSH подключение

Из WSL настроены алиасы в `~/.ssh/config`:

```bash
ssh ws        # Просто зайти на сервер
ssh monitor   # Подключиться к monitor bot
ssh trade     # Подключиться к trading bot
ssh update    # Подключиться к update bot
```

## Tmux сессии

Боты запускаются в tmux сессиях для работы в фоне.

### Управление tmux

**Выход из сессии (бот продолжает работать):**
```
Ctrl+B, затем D
```

**Просмотр всех сессий:**
```bash
ssh ws "tmux ls"
```

**Подключение к сессии вручную:**
```bash
ssh ws
tmux attach -t trade
```

### Запуск ботов

```bash
ssh ws

cd /opt/polymarket/earthquakes

# Monitor bot
tmux new-session -d -s monitor "./run_monitor_bot.sh"

# Update bot
tmux new-session -d -s update "./run_update_bot.sh"

# Trading bot
tmux new-session -d -s trade "./run_trading_bot.sh --min-edge 0.01 --min-apy 0.20 --live --auto"
```

### Остановка ботов

```bash
# Остановить конкретного бота
tmux kill-session -t trade

# Остановить всех
tmux kill-server
```

### Перезапуск бота

```bash
tmux kill-session -t trade
cd /opt/polymarket/earthquakes
tmux new-session -d -s trade "./run_trading_bot.sh --min-edge 0.01 --min-apy 0.20 --live --auto"
```

## Установка зависимостей

При первом запуске `run_*.sh` скрипты автоматически:
1. Создают виртуальное окружение `.venv`
2. Устанавливают все зависимости из `requirements.txt`
3. Устанавливают `polymarket_console` из родительской директории

## Синхронизация кода

```bash
# С локальной машины (WSL)
rsync -avz --exclude='.venv' --exclude='__pycache__' --exclude='.git' \
  /mnt/c/GitHub/polymarket_console/earthquakes/ \
  root@62.112.10.73:/opt/polymarket/earthquakes/
```

## Скрипты обслуживания

### Консолидация позиций

Бот делает несколько мелких покупок на одном рынке, создавая отдельный файл на каждую. Скрипт объединяет их в одну позицию со средневзвешенной ценой.

```bash
# Dry-run (только показать план):
.venv/bin/python scripts/consolidate_positions.py

# Применить:
.venv/bin/python scripts/consolidate_positions.py --apply

# Сохранить старые файлы в history/ (вместо удаления):
.venv/bin/python scripts/consolidate_positions.py --apply --keep
```

> **Примечание:** С февраля 2026 бот автоматически мержит покупки в существующую позицию (merge-on-buy), поэтому новые дубли не должны появляться. Скрипт нужен для фикса старых позиций.

### Swap native USDC → USDC.e

Polymarket UI при выводе отправляет native USDC, а бот работает с USDC.e. См. [deposit-withdrawal.md](deposit-withdrawal.md).

```bash
POLYGON_RPC=https://polygon-bor-rpc.publicnode.com .venv/bin/python scripts/swap_usdc.py
```

### Ресинхронизация позиций

Отменяет все ордера, удаляет активные позиции и пересоздаёт из API Polymarket.

```bash
.venv/bin/python scripts/resync_positions.py
```

## Логи

```bash
# Trading bot
ssh ws "tail -100 /opt/polymarket/earthquakes/trading_bot/data/logs/bot_$(date +%Y-%m-%d).log"

# Monitor bot
ssh ws "tail -100 /opt/polymarket/earthquakes/monitor_bot/data/logs/monitor_$(date +%Y-%m-%d).log"

# Проверка ошибок
ssh ws "grep -iE 'ERROR|Exception' /opt/polymarket/earthquakes/trading_bot/data/logs/bot_$(date +%Y-%m-%d).log | tail -20"
```
