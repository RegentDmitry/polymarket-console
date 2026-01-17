# Monitor Bot - TODO List

Реал-тайм мониторинг землетрясений с визуализацией в TUI и записью в PostgreSQL.

## Database Migrations

- Создать migrations/ директорию
- 001_initial_schema.sql - создание таблиц
- Migration runner скрипт (apply_migrations.py)
- Rollback поддержка
- README с инструкциями по миграциям

## Configuration Management

- .env.example для сервера
- Валидация конфигурации при старте
- Поддержка переменных окружения для всех настроек

## Deployment Scripts

- install.sh для установки на сервере
- Systemd service файл (опционально)
