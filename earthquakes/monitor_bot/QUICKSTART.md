# Monitor Bot - Quick Start

Быстрый старт для локального тестирования.

## Шаг 1: Настройка PostgreSQL

### Windows + pgAdmin

1. Открыть pgAdmin
2. Создать новую БД:
   - Правый клик на Databases → Create → Database
   - Name: `earthquake_monitor`
   - Save
3. Открыть Query Tool для `earthquake_monitor`
4. Открыть файл `monitor_bot/schema.sql`
5. Execute (F5)

✅ Должно появиться: `Database schema created successfully!`

### Через командную строку

```bash
# Создать БД
createdb -h 172.24.192.1 -U postgres earthquake_monitor

# Применить схему
psql -h 172.24.192.1 -U postgres -d earthquake_monitor -f monitor_bot/schema.sql
```

## Шаг 2: Настройка .env

```bash
cd earthquakes

# Если нет .env
cp .env.example .env

# Редактировать .env
nano .env
```

Проверить/добавить:
```bash
DB_HOST=172.24.192.1
DB_PORT=5432
DB_NAME=earthquake_monitor
DB_USER=postgres
DB_PASSWORD=your_password
```

## Шаг 3: Установка зависимостей

```bash
cd earthquakes

# Активировать venv (если есть)
source .venv/bin/activate

# Установить зависимости monitor_bot
pip install -r monitor_bot/requirements.txt
```

## Шаг 4: Запуск

```bash
# Простой запуск
python -m monitor_bot

# ИЛИ через скрипт
bash run_monitor_bot.sh
```

## Что вы увидите

```
┌─────────────────────────────────────────────────────────────┐
│ Earthquake Monitor Bot                          Q: Quit     │
├─────────────────────────────────────────────────────────────┤
│ Status: Running | Sources: 5 active | Events: 0 | Pending: 0│
├─────────────────────────────────────────────────────────────┤
│ Mag  │ Location         │ Srcs │ Detected │ USGS  │ Edge   │
├──────┼──────────────────┼──────┼──────────┼───────┼────────┤
│      │ Waiting for earthquakes...                           │
└─────────────────────────────────────────────────────────────┘
```

В логе внизу должно быть:
```
14:23:15 Monitor Bot started
14:23:15 Tracking: M6.0+, Highlighting: M7.0+
14:23:16 Connected to PostgreSQL: 172.24.192.1:5432/earthquake_monitor
14:23:16 Initialized: JMA
14:23:16 Initialized: EMSC
14:23:16 Initialized: GFZ
14:23:16 Initialized: GEONET
14:23:16 Initialized: USGS
14:23:17 Loaded 0 events from last 24 hours
14:23:17 Starting 5 collectors...
```

## Что дальше?

- **Подождите** - события M6.0+ случаются каждые ~1-3 дня
- **Проверьте БД** - данные пишутся в real-time
- **Нажмите C** - очистить лог
- **Нажмите Q** - выход

## Проверка работы

### Проверить БД

```bash
psql -h 172.24.192.1 -U postgres -d earthquake_monitor

# В psql:
SELECT COUNT(*) FROM earthquake_events;
SELECT COUNT(*) FROM source_reports;
SELECT * FROM extended_history LIMIT 5;
```

### Проверить логи

Лог панель в TUI показывает:
- Инициализацию коллекторов
- Новые события
- Совпадения от разных источников

## Troubleshooting

### `Database connection failed`

1. PostgreSQL запущен?
2. Пароль правильный в `.env`?
3. Попробуйте подключиться вручную:
   ```bash
   psql -h 172.24.192.1 -U postgres -d earthquake_monitor
   ```

### `No module named 'textual'`

```bash
pip install -r monitor_bot/requirements.txt
```

### `No module named 'monitor'`

Убедитесь что запускаете из директории `earthquakes/`:
```bash
cd /mnt/c/github/polymarket_console/earthquakes
python -m monitor_bot
```

### События не появляются

Это нормально! M6.0+ землетрясения случаются не каждый день.

Чтобы увидеть систему в работе быстрее:
1. Уменьшите порог в `monitor_bot/config.py`:
   ```python
   MIN_MAGNITUDE_TRACK = 5.0  # Временно
   ```
2. Перезапустите бота

Тогда будут видны M5.0+ (чаще происходят).

## Следующие шаги

1. ✅ Локально работает
2. Изучить TODO.md для Phase 2
3. Подготовить миграции для сервера
4. Деплой на удалённый сервер
