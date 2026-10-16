"""SQLite unit-of-work shared by HTTP handlers and maintenance commands."""
import os
import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

active_connection = ContextVar("oilcoin_connection", default=None)
SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY);
INSERT OR IGNORE INTO schema_version VALUES (1);
CREATE TABLE IF NOT EXISTS chain_state (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS blocks (height INTEGER PRIMARY KEY, hash TEXT UNIQUE NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pending_transactions (position INTEGER PRIMARY KEY, tx_id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS accounts (
 address TEXT PRIMARY KEY, label TEXT NOT NULL, password_hash TEXT NOT NULL,
 public_key TEXT NOT NULL, encrypted_private_key TEXT NOT NULL,
 created_at TEXT NOT NULL, is_demo INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS game_events (
 id INTEGER PRIMARY KEY, address TEXT NOT NULL REFERENCES accounts(address),
 event_key TEXT UNIQUE NOT NULL, xp INTEGER NOT NULL CHECK(xp>=0), created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS game_events_address ON game_events(address);
CREATE TABLE IF NOT EXISTS upgrades (
 id INTEGER PRIMARY KEY, address TEXT NOT NULL REFERENCES accounts(address),
 level INTEGER NOT NULL CHECK(level BETWEEN 1 AND 5), tx_id TEXT UNIQUE NOT NULL,
 UNIQUE(address,level)
);
CREATE TABLE IF NOT EXISTS rate_limits (key TEXT PRIMARY KEY, window INTEGER NOT NULL, attempts INTEGER NOT NULL);
"""

SCHEMA_VERSION = 5
FARM_SCHEMA = """
CREATE TABLE IF NOT EXISTS estates (
 address TEXT PRIMARY KEY REFERENCES accounts(address),
 crude INTEGER NOT NULL CHECK(crude>=0), fuel INTEGER NOT NULL CHECK(fuel>=0),
 parts INTEGER NOT NULL CHECK(parts>=0), ready_crude INTEGER NOT NULL CHECK(ready_crude>=0),
 last_tick INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS buildings (
 address TEXT NOT NULL REFERENCES estates(address), kind TEXT NOT NULL CHECK(kind IN ('hq','pump','depot','refinery','garage')),
 level INTEGER NOT NULL CHECK(level BETWEEN 1 AND 5), PRIMARY KEY(address,kind)
);
CREATE TABLE IF NOT EXISTS construction_jobs (
 id INTEGER PRIMARY KEY, address TEXT NOT NULL REFERENCES estates(address), kind TEXT NOT NULL,
 target_level INTEGER NOT NULL CHECK(target_level BETWEEN 2 AND 5), tx_id TEXT UNIQUE NOT NULL,
 duration INTEGER NOT NULL CHECK(duration>0), created_at INTEGER NOT NULL,
 started_at INTEGER, ready_at INTEGER, claimed_at INTEGER
);
CREATE TABLE IF NOT EXISTS production_jobs (
 id INTEGER PRIMARY KEY, address TEXT NOT NULL REFERENCES estates(address), recipe TEXT NOT NULL,
 batches INTEGER NOT NULL CHECK(batches IN (1,3)), output_resource TEXT NOT NULL,
 output_amount INTEGER NOT NULL CHECK(output_amount>0), started_at INTEGER NOT NULL,
 ready_at INTEGER NOT NULL, claimed_at INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS one_production ON production_jobs(address) WHERE claimed_at IS NULL;
CREATE TABLE IF NOT EXISTS expedition_jobs (
 id INTEGER PRIMARY KEY, address TEXT NOT NULL REFERENCES estates(address), sector INTEGER NOT NULL CHECK(sector BETWEEN 1 AND 6),
 approach TEXT NOT NULL, power INTEGER NOT NULL, reward_parts INTEGER NOT NULL,
 reward_crude INTEGER NOT NULL, reward_xp INTEGER NOT NULL, reward_points INTEGER NOT NULL,
 started_at INTEGER NOT NULL, ready_at INTEGER NOT NULL, claimed_at INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS one_expedition ON expedition_jobs(address) WHERE claimed_at IS NULL;
CREATE TABLE IF NOT EXISTS farm_events (
 id INTEGER PRIMARY KEY, address TEXT NOT NULL REFERENCES estates(address), event_key TEXT NOT NULL,
 title TEXT NOT NULL, points INTEGER NOT NULL CHECK(points>=0), created_at INTEGER NOT NULL,
 UNIQUE(address,event_key)
);
CREATE INDEX IF NOT EXISTS farm_scores ON farm_events(created_at,address);
"""

MARKET_SCHEMA = """
CREATE TABLE IF NOT EXISTS base_layouts (
 address TEXT PRIMARY KEY REFERENCES estates(address), revision INTEGER NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS extraction_days (
 address TEXT NOT NULL REFERENCES estates(address), day TEXT NOT NULL,
 collected INTEGER NOT NULL CHECK(collected>=0), PRIMARY KEY(address,day)
);
CREATE TABLE IF NOT EXISTS market_orders (
 id INTEGER PRIMARY KEY, seller TEXT NOT NULL REFERENCES estates(address),
 resource TEXT NOT NULL CHECK(resource IN ('crude','fuel','parts')),
 quantity INTEGER NOT NULL CHECK(quantity BETWEEN 1 AND 1000),
 price_units INTEGER NOT NULL CHECK(price_units>0), fee_units INTEGER NOT NULL CHECK(fee_units>0),
 status TEXT NOT NULL CHECK(status IN ('open','pending','filled','cancelled')),
 created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
 buyer TEXT REFERENCES estates(address), tx_id TEXT UNIQUE, filled_at INTEGER,
 CHECK ((status IN ('open','cancelled') AND buyer IS NULL AND tx_id IS NULL) OR
        (status IN ('pending','filled') AND buyer IS NOT NULL AND tx_id IS NOT NULL AND buyer!=seller))
);
CREATE INDEX IF NOT EXISTS market_book ON market_orders(status,resource,created_at);
CREATE INDEX IF NOT EXISTS market_seller ON market_orders(seller,status);
CREATE INDEX IF NOT EXISTS market_buyer ON market_orders(buyer,status);
"""

def default_path():
    return os.environ.get("DATABASE_PATH", str(Path(__file__).parent / "data" / "oilcoin.db"))

COLONY_SCHEMA = """
DROP INDEX IF EXISTS one_builder;
CREATE UNIQUE INDEX IF NOT EXISTS one_building_upgrade ON construction_jobs(address,kind) WHERE claimed_at IS NULL;
CREATE TABLE IF NOT EXISTS colony_profiles (
 address TEXT PRIMARY KEY REFERENCES accounts(address), payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runtime_state (key TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS chest_events (
 id INTEGER PRIMARY KEY, address TEXT NOT NULL REFERENCES accounts(address),
 kind TEXT NOT NULL, payload TEXT NOT NULL, created_at INTEGER NOT NULL,
 request_id TEXT NOT NULL, UNIQUE(address,request_id)
);
CREATE TABLE IF NOT EXISTS arena_queue (
 address TEXT PRIMARY KEY REFERENCES accounts(address), joined_at INTEGER NOT NULL,
 heartbeat INTEGER NOT NULL, room TEXT, UNIQUE(room)
);
CREATE TABLE IF NOT EXISTS arena_matches (
 id TEXT PRIMARY KEY, p1 TEXT NOT NULL REFERENCES accounts(address),
 p2 TEXT NOT NULL REFERENCES accounts(address), state TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('active','finished')),
 created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, CHECK(p1!=p2)
);
CREATE INDEX IF NOT EXISTS arena_active ON arena_matches(status);
CREATE INDEX IF NOT EXISTS arena_p1 ON arena_matches(p1,created_at);
CREATE INDEX IF NOT EXISTS arena_p2 ON arena_matches(p2,created_at);
CREATE TABLE IF NOT EXISTS arena_rewards (
 address TEXT NOT NULL REFERENCES accounts(address), day TEXT NOT NULL,
 opponent TEXT NOT NULL REFERENCES accounts(address), PRIMARY KEY(address,day,opponent)
);
CREATE TABLE IF NOT EXISTS npc_deals (
 address TEXT NOT NULL REFERENCES accounts(address), slot INTEGER NOT NULL,
 offer INTEGER NOT NULL, PRIMARY KEY(address,slot,offer)
);
"""

CAMPAIGN_SCHEMA = """
CREATE TABLE IF NOT EXISTS pve_matches (
 id TEXT PRIMARY KEY, address TEXT NOT NULL REFERENCES accounts(address),
 state TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('active','finished')),
 created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS one_pve_battle ON pve_matches(address) WHERE status='active';
CREATE TABLE IF NOT EXISTS pve_rewards (
 address TEXT NOT NULL REFERENCES accounts(address), reward_key TEXT NOT NULL,
 match_id TEXT NOT NULL REFERENCES pve_matches(id), created_at INTEGER NOT NULL,
 PRIMARY KEY(address,reward_key)
);
CREATE TABLE IF NOT EXISTS chest_orders (
 id INTEGER PRIMARY KEY, address TEXT NOT NULL REFERENCES accounts(address),
 kind TEXT NOT NULL, price_units INTEGER NOT NULL CHECK(price_units>0),
 fee_units INTEGER NOT NULL CHECK(fee_units>=0), tx_id TEXT NOT NULL UNIQUE,
 request_id TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','opened')),
 created_at INTEGER NOT NULL, opened_at INTEGER, UNIQUE(address,request_id)
);
"""

def connect(path=None):
    path = str(path or default_path())
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn

def initialize(path=None):
    conn = connect(path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        exists = conn.execute("SELECT name FROM sqlite_master WHERE name='schema_version'").fetchone()
        if exists and [row[0] for row in conn.execute("SELECT version FROM schema_version")] not in ([1], [2], [3], [4], [SCHEMA_VERSION]):
            raise RuntimeError("Unsupported database schema version")
        conn.executescript("BEGIN IMMEDIATE;\n" + SCHEMA + FARM_SCHEMA + MARKET_SCHEMA + COLONY_SCHEMA + CAMPAIGN_SCHEMA + "\nDELETE FROM schema_version; INSERT INTO schema_version VALUES (5); COMMIT;")
    finally:
        conn.close()

@contextmanager
def unit_of_work(path=None, write=True):
    existing = active_connection.get()
    if existing is not None:
        yield existing
        return
    conn = connect(path)
    token = active_connection.set(conn)
    try:
        conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        active_connection.reset(token)
        conn.close()
