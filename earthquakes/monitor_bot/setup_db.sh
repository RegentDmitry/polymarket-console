#!/bin/bash
# Setup database for Monitor Bot

set -e

DB_HOST="${DB_HOST:-172.24.192.1}"
DB_PORT="${DB_PORT:-5432}"
DB_NAME="${DB_NAME:-earthquake_monitor}"
DB_USER="${DB_USER:-postgres}"

echo "=================================================="
echo "Earthquake Monitor Bot - Database Setup"
echo "=================================================="
echo ""
echo "Database: $DB_NAME"
echo "Host: $DB_HOST:$DB_PORT"
echo "User: $DB_USER"
echo ""

# Check if psql is available
if ! command -v psql &> /dev/null; then
    echo "❌ psql not found!"
    echo ""
    echo "Please install PostgreSQL client or use pgAdmin to run schema.sql manually."
    exit 1
fi

# Test connection
echo "Testing connection..."
if ! PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d postgres -c "SELECT 1" &> /dev/null; then
    echo "❌ Cannot connect to PostgreSQL!"
    echo ""
    echo "Please check:"
    echo "1. PostgreSQL is running"
    echo "2. Host/port are correct"
    echo "3. DB_PASSWORD is set in .env"
    exit 1
fi

echo "✓ Connection successful"
echo ""

# Create database if not exists
echo "Creating database if not exists..."
PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d postgres -c "CREATE DATABASE $DB_NAME" 2>/dev/null || true

# Apply schema
echo "Applying schema..."
PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -f "$(dirname "$0")/schema.sql"

echo ""
echo "=================================================="
echo "✓ Database setup complete!"
echo "=================================================="
echo ""
echo "You can now run the monitor bot:"
echo "  python -m monitor_bot"
echo ""
