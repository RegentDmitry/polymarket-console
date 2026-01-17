#!/bin/bash
# Reset Monitor Bot database for fresh testing

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=================================================="
echo "Reset Monitor Bot Database"
echo "=================================================="
echo ""

# Load .env
if [ -f ".env" ]; then
    set -a
    source .env
    set +a
else
    echo "❌ .env file not found!"
    exit 1
fi

echo "This will DELETE ALL data from:"
echo "  - earthquake_events"
echo "  - source_reports"
echo "  - market_reactions"
echo ""
read -p "Are you sure? (yes/no): " confirm

if [ "$confirm" != "yes" ]; then
    echo "Cancelled."
    exit 0
fi

echo ""
echo "Clearing database..."

psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" <<SQL
-- Clear all tables
TRUNCATE TABLE market_reactions CASCADE;
TRUNCATE TABLE source_reports CASCADE;
TRUNCATE TABLE earthquake_events CASCADE;

SELECT 'Database cleared successfully!' as status;
SQL

echo ""
echo "✓ Database reset complete"
echo ""
echo "You can now run: bash run_monitor_bot.sh"
