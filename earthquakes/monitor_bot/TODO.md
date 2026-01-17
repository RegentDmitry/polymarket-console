# Monitor Bot - TODO List

Реал-тайм мониторинг землетрясений с визуализацией в TUI и записью в PostgreSQL.

## Phase 1: Тестирование

Исправлено:
- USGS collector (был неправильный URL в monitor/config.py)
- Отрицательный Edge Time (теперь показывается "-" для исторических данных)

Осталось протестировать:
- Все 5 источников работают корректно
- Events матчатся между источниками (Srcs > 1)
- Real-time события показывают положительный Edge
- UI обновления работают
- Запись в БД корректная

## Phase 2: Подготовка к деплою

### 2.1 Database Migrations

- Создать migrations/ директорию
- 001_initial_schema.sql - создание таблиц
- Migration runner скрипт (apply_migrations.py)
- Rollback поддержка
- README с инструкциями по миграциям

### 2.2 Configuration Management

- .env.example для сервера
- Валидация конфигурации при старте
- Поддержка переменных окружения для всех настроек

### 2.3 Deployment Scripts

- install.sh для установки на сервере
- Systemd service файл (опционально)

### 2.4 Monitoring & Logging

- Логирование в файл (monitor_bot.log)
- Log rotation
- Error handling и graceful shutdown
- Health check endpoint (опционально)

## Phase 3: Улучшения (опционально)

### 3.1 Advanced Features

- Фильтры: показывать только M6.5+, только pending, и т.д.
- Экспорт данных (CSV, JSON)
- Статистика по источникам (кто быстрее всех)
- График edge time distribution

### 3.2 Alerts

- Звуковой алерт при M7.0+
- Telegram уведомления (опционально)
- Discord webhook (опционально)

### 3.3 Market Integration

- Автоматическое обновление market_reactions
- Отслеживание цен Polymarket при обнаружении
- Potential trade signals

---

## Технические решения

### Минимальный порог магнитуды
**M4.5** (для тестирования) - 10-15 событий/день:
- Быстро показывает работу системы
- После тестирования можно изменить на M6.0 для продакшена

### UI Layout
```
┌─────────────────────────────────────────────────────────────────────┐
│ Earthquake Monitor Bot                                    Q: Quit   │
├─────────────────────────────────────────────────────────────────────┤
│ Status: Running | Sources: 5 active | Events: 12 | Pending: 2      │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│ Recent Earthquakes (M6.0+)                                          │
│ ┌───────────────────────────────────────────────────────────────┐  │
│ │ Mag  │ Location              │ Srcs │ Detected  │ USGS   │Edge│  │
│ ├──────┼───────────────────────┼──────┼───────────┼────────┼────┤  │
│ │ M7.2 │ Near coast of Japan   │  3   │ 14:23:15  │ Pending│ -  │  │
│ │ M6.8 │ Tonga region          │  2   │ 14:15:42  │ 14:28  │12m │  │
│ │ M6.4 │ Chile                 │  1   │ 13:55:10  │ 14:02  │ 7m │  │
│ └───────────────────────────────────────────────────────────────┘  │
│                                                                     │
│ Activity Log                                                        │
│ ┌───────────────────────────────────────────────────────────────┐  │
│ │ 14:23:15 [JMA] New M7.2 at Near coast of Japan               │  │
│ │ 14:23:45 [EMSC] Matched M7.2 to existing event (2 sources)   │  │
│ │ 14:24:10 [GFZ] Matched M7.2 to existing event (3 sources)    │  │
│ └───────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

### Database Strategy
- **Local dev**: PostgreSQL на Windows host (172.24.192.1)
- **Server deploy**: Отдельная БД, конфигурация через .env
- **Migrations**: SQL файлы для версионирования схемы

### Color Scheme
- M7.0+: `[bold red]` + sound alert
- M6.5-6.9: `[bold yellow]`
- M6.0-6.4: `[cyan]`
- Edge > 10 min: `[on green]` background
- Pending USGS: `[italic]`
