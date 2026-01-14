# Trading Bot Troubleshooting

## Problem: TUI не запускается или сразу завершается

### Symptoms
- Бот стартует, показывает "Starting TUI...", но сразу возвращается в командную строку
- TUI интерфейс не отображается
- Видны ANSI escape коды в выводе

### Причина
Textual TUI требует интерактивный терминал (TTY). В WSL2 и некоторых других окружениях TTY может быть недоступен или неправильно настроен.

## Решения

### Решение 1: Запуск напрямую (рекомендуется для WSL2)

```bash
cd /mnt/c/github/polymarket_console/earthquakes
.venv/bin/python -m trading_bot
```

Это запустит бот напрямую без промежуточного bash скрипта.

### Решение 2: Использовать обновленный скрипт

Обновленный `run_trading_bot.sh` теперь использует `exec` для правильной работы с TTY:

```bash
./run_trading_bot.sh
```

### Решение 3: Windows Terminal (лучше всего для WSL2)

Если вы используете WSL2 в Windows:

1. Установите Windows Terminal из Microsoft Store
2. Откройте Windows Terminal
3. Выберите профиль WSL/Ubuntu
4. Запустите бот:
   ```bash
   cd /mnt/c/github/polymarket_console/earthquakes
   bash run_trading_bot.sh
   ```

Windows Terminal имеет отличную поддержку ANSI escape кодов и TTY.

### Решение 4: tmux/screen (для удаленных серверов)

Если вы работаете через SSH или хотите, чтобы бот продолжал работать:

```bash
# Установить tmux
sudo apt-get install tmux

# Запустить в tmux
tmux new -s trading_bot
cd /mnt/c/github/polymarket_console/earthquakes
./run_trading_bot.sh

# Отключиться: Ctrl+B, затем D
# Подключиться обратно: tmux attach -t trading_bot
```

### Решение 5: Проверка окружения

Проверьте, доступен ли TTY:

```bash
python3 -c "import sys; print(f'stdin: {sys.stdin.isatty()}')"
```

Должно вывести `stdin: True`. Если `False`, попробуйте другой терминал.

## Режимы запуска

### DRY RUN (безопасный режим, по умолчанию)
```bash
./run_trading_bot.sh
```
Только анализ, без реальных сделок.

### LIVE с подтверждением
```bash
./run_trading_bot.sh --live
```
Реальные сделки, каждая требует подтверждения.

### AUTO (полностью автоматический)
```bash
./run_trading_bot.sh --live --auto
```
⚠️ ОСТОРОЖНО - автоматически выполняет все сделки!

## Дополнительные параметры

```bash
./run_trading_bot.sh \
  --interval 10m \      # интервал сканирования (30s, 5m, 1h)
  --min-edge 0.02 \     # минимальный edge (2%)
  --min-apy 0.50 \      # минимальный APY (50%)
  --max-positions 10    # максимум открытых позиций
```

## Горячие клавиши в TUI

- `Q` - выход
- `R` - немедленное сканирование
- `↑/↓` - навигация по списку возможностей
- `Enter` - открыть позицию (в режиме LIVE)

## Проблемы с импортами

Если видите ошибки импорта:

```bash
cd /mnt/c/github/polymarket_console/earthquakes
.venv/bin/pip install -r trading_bot/requirements.txt
.venv/bin/pip install eth-account web3 poly_eip712_structs
```

## Логи и отладка

Логи сохраняются в:
- `trading_bot/data/active/` - активные позиции
- `trading_bot/data/history/` - история сделок

Для детальной отладки запустите с Python напрямую и смотрите traceback:

```bash
.venv/bin/python -m trading_bot 2>&1 | tee bot.log
```
