"""PostgreSQL storage for Skilled Trader Scouting."""

import psycopg2
from psycopg2.extras import execute_values, RealDictCursor
from contextlib import contextmanager

DB_CONFIG = {
    "host": "172.24.192.1",
    "port": 5432,
    "dbname": "polymarket",
    "user": "postgres",
    "password": "dbpass",
}

SCHEMA_VERSION = 1


def get_conn():
    return psycopg2.connect(**DB_CONFIG)


@contextmanager
def get_cursor(commit=True):
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        yield cur
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def init_schema():
    """Create tables if not exist."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS markets (
            slug            TEXT NOT NULL,
            condition_id    TEXT PRIMARY KEY,
            title           TEXT NOT NULL,
            outcome         TEXT,           -- 'YES'/'NO'/NULL (NULL=unresolved)
            end_date        TIMESTAMPTZ,
            volume          DOUBLE PRECISION DEFAULT 0,
            liquidity       DOUBLE PRECISION DEFAULT 0,
            category        TEXT DEFAULT 'other',
            scanned_at      TIMESTAMPTZ DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS idx_markets_slug ON markets(slug);
        CREATE INDEX IF NOT EXISTS idx_markets_outcome ON markets(outcome);

        CREATE TABLE IF NOT EXISTS traders (
            address         TEXT PRIMARY KEY,
            alias           TEXT,
            first_seen      TIMESTAMPTZ DEFAULT now(),
            last_seen       TIMESTAMPTZ DEFAULT now(),
            is_mm           BOOLEAN DEFAULT FALSE,
            skill_score     DOUBLE PRECISION DEFAULT 0,
            total_markets   INT DEFAULT 0,
            win_count       INT DEFAULT 0,
            loss_count      INT DEFAULT 0,
            total_invested  DOUBLE PRECISION DEFAULT 0,
            total_returned  DOUBLE PRECISION DEFAULT 0,
            realized_pnl    DOUBLE PRECISION DEFAULT 0,
            avg_roi         DOUBLE PRECISION DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_traders_skill ON traders(skill_score DESC);
        CREATE INDEX IF NOT EXISTS idx_traders_mm ON traders(is_mm);

        CREATE TABLE IF NOT EXISTS positions (
            id              SERIAL PRIMARY KEY,
            address         TEXT NOT NULL REFERENCES traders(address),
            condition_id    TEXT NOT NULL REFERENCES markets(condition_id),
            outcome_side    TEXT NOT NULL,       -- 'YES' or 'NO'
            size            DOUBLE PRECISION DEFAULT 0,
            avg_price       DOUBLE PRECISION DEFAULT 0,
            initial_value   DOUBLE PRECISION DEFAULT 0,  -- cost basis
            current_value   DOUBLE PRECISION DEFAULT 0,
            realized_pnl    DOUBLE PRECISION DEFAULT 0,
            percent_pnl     DOUBLE PRECISION DEFAULT 0,
            is_closed       BOOLEAN DEFAULT FALSE,
            created_at      TIMESTAMPTZ DEFAULT now(),
            updated_at      TIMESTAMPTZ DEFAULT now(),
            UNIQUE(address, condition_id, outcome_side)
        );
        CREATE INDEX IF NOT EXISTS idx_positions_address ON positions(address);
        CREATE INDEX IF NOT EXISTS idx_positions_market ON positions(condition_id);
        CREATE INDEX IF NOT EXISTS idx_positions_closed ON positions(is_closed);

        CREATE TABLE IF NOT EXISTS signals (
            id              SERIAL PRIMARY KEY,
            condition_id    TEXT NOT NULL,
            slug            TEXT,
            title           TEXT,
            signal_type     TEXT NOT NULL,       -- 'BUY_YES', 'BUY_NO', 'CONFLICT'
            confidence      DOUBLE PRECISION DEFAULT 0,
            n_traders       INT DEFAULT 0,
            avg_entry       DOUBLE PRECISION DEFAULT 0,
            created_at      TIMESTAMPTZ DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS idx_signals_time ON signals(created_at DESC);

        CREATE TABLE IF NOT EXISTS schema_version (
            version INT PRIMARY KEY
        );
    """)
    cur.execute("INSERT INTO schema_version (version) VALUES (%s) ON CONFLICT DO NOTHING", (SCHEMA_VERSION,))
    conn.commit()
    cur.close()
    conn.close()


# === CRUD: Markets ===

def upsert_market(condition_id: str, slug: str, title: str,
                  outcome: str = None, end_date=None,
                  volume: float = 0, liquidity: float = 0,
                  category: str = "other"):
    with get_cursor() as cur:
        cur.execute("""
            INSERT INTO markets (condition_id, slug, title, outcome, end_date, volume, liquidity, category)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (condition_id) DO UPDATE SET
                title = EXCLUDED.title,
                outcome = COALESCE(EXCLUDED.outcome, markets.outcome),
                end_date = COALESCE(EXCLUDED.end_date, markets.end_date),
                volume = EXCLUDED.volume,
                liquidity = EXCLUDED.liquidity,
                category = EXCLUDED.category,
                scanned_at = now()
        """, (condition_id, slug, title, outcome, end_date, volume, liquidity, category))


def get_market(condition_id: str) -> dict | None:
    with get_cursor(commit=False) as cur:
        cur.execute("SELECT * FROM markets WHERE condition_id = %s", (condition_id,))
        return cur.fetchone()


def get_resolved_markets() -> list[dict]:
    with get_cursor(commit=False) as cur:
        cur.execute("SELECT * FROM markets WHERE outcome IS NOT NULL ORDER BY scanned_at DESC")
        return cur.fetchall()


# === CRUD: Traders ===

def upsert_trader(address: str, alias: str = None):
    with get_cursor() as cur:
        cur.execute("""
            INSERT INTO traders (address, alias)
            VALUES (%s, %s)
            ON CONFLICT (address) DO UPDATE SET
                alias = COALESCE(EXCLUDED.alias, traders.alias),
                last_seen = now()
        """, (address, alias))


def get_trader(address: str) -> dict | None:
    with get_cursor(commit=False) as cur:
        cur.execute("SELECT * FROM traders WHERE address = %s", (address,))
        return cur.fetchone()


def get_top_traders(limit: int = 50, exclude_mm: bool = True) -> list[dict]:
    with get_cursor(commit=False) as cur:
        mm_filter = "AND NOT is_mm" if exclude_mm else ""
        cur.execute(f"""
            SELECT * FROM traders
            WHERE total_markets >= 5 {mm_filter}
            ORDER BY skill_score DESC
            LIMIT %s
        """, (limit,))
        return cur.fetchall()


def update_trader_stats(address: str, **kwargs):
    """Update arbitrary trader fields."""
    valid = {"skill_score", "total_markets", "win_count", "loss_count",
             "total_invested", "total_returned", "realized_pnl", "avg_roi",
             "is_mm", "alias"}
    fields = {k: v for k, v in kwargs.items() if k in valid}
    if not fields:
        return
    set_clause = ", ".join(f"{k} = %s" for k in fields)
    values = list(fields.values()) + [address]
    with get_cursor() as cur:
        cur.execute(f"UPDATE traders SET {set_clause} WHERE address = %s", values)


# === CRUD: Positions ===

def upsert_position(address: str, condition_id: str, outcome_side: str,
                    size: float = 0, avg_price: float = 0,
                    initial_value: float = 0, current_value: float = 0,
                    realized_pnl: float = 0, percent_pnl: float = 0,
                    is_closed: bool = False):
    with get_cursor() as cur:
        cur.execute("""
            INSERT INTO positions (address, condition_id, outcome_side, size, avg_price,
                                   initial_value, current_value, realized_pnl, percent_pnl, is_closed)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (address, condition_id, outcome_side) DO UPDATE SET
                size = EXCLUDED.size,
                avg_price = EXCLUDED.avg_price,
                initial_value = EXCLUDED.initial_value,
                current_value = EXCLUDED.current_value,
                realized_pnl = EXCLUDED.realized_pnl,
                percent_pnl = EXCLUDED.percent_pnl,
                is_closed = EXCLUDED.is_closed,
                updated_at = now()
        """, (address, condition_id, outcome_side, size, avg_price,
              initial_value, current_value, realized_pnl, percent_pnl, is_closed))


def get_trader_positions(address: str, closed_only: bool = False) -> list[dict]:
    with get_cursor(commit=False) as cur:
        clause = "AND is_closed = TRUE" if closed_only else ""
        cur.execute(f"""
            SELECT p.*, m.title, m.slug, m.outcome as market_outcome
            FROM positions p
            JOIN markets m ON p.condition_id = m.condition_id
            WHERE p.address = %s {clause}
            ORDER BY p.updated_at DESC
        """, (address,))
        return cur.fetchall()


def get_market_holders(condition_id: str) -> list[dict]:
    with get_cursor(commit=False) as cur:
        cur.execute("""
            SELECT p.*, t.alias, t.skill_score, t.is_mm
            FROM positions p
            JOIN traders t ON p.address = t.address
            WHERE p.condition_id = %s
            ORDER BY p.size DESC
        """, (condition_id,))
        return cur.fetchall()


# === CRUD: Signals ===

def add_signal(condition_id: str, slug: str, title: str,
               signal_type: str, confidence: float,
               n_traders: int, avg_entry: float = 0):
    with get_cursor() as cur:
        cur.execute("""
            INSERT INTO signals (condition_id, slug, title, signal_type, confidence, n_traders, avg_entry)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (condition_id, slug, title, signal_type, confidence, n_traders, avg_entry))


def get_recent_signals(limit: int = 20) -> list[dict]:
    with get_cursor(commit=False) as cur:
        cur.execute("SELECT * FROM signals ORDER BY created_at DESC LIMIT %s", (limit,))
        return cur.fetchall()


# === Stats ===

def get_db_stats() -> dict:
    with get_cursor(commit=False) as cur:
        cur.execute("SELECT COUNT(*) as n FROM traders")
        n_traders = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) as n FROM markets")
        n_markets = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) as n FROM positions")
        n_positions = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) as n FROM markets WHERE outcome IS NOT NULL")
        n_resolved = cur.fetchone()["n"]
        return {
            "traders": n_traders,
            "markets": n_markets,
            "positions": n_positions,
            "resolved_markets": n_resolved,
        }
