from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS scenarios (
    scenario_id INTEGER PRIMARY KEY,
    timeframe TEXT NOT NULL,
    scenario TEXT NOT NULL,
    strategy TEXT NOT NULL,
    target TEXT NOT NULL,
    filter_name TEXT NOT NULL,
    threshold REAL,
    horizon INTEGER NOT NULL,
    tp_pct REAL NOT NULL,
    sl_pct REAL NOT NULL,
    vol_low REAL,
    vol_high REAL,
    enabled INTEGER NOT NULL,
    blocked_reason TEXT,
    initial_cash REAL NOT NULL,
    cash REAL NOT NULL,
    realized_pnl REAL NOT NULL DEFAULT 0,
    trade_count INTEGER NOT NULL DEFAULT 0,
    win_count INTEGER NOT NULL DEFAULT 0,
    loss_count INTEGER NOT NULL DEFAULT 0,
    time_count INTEGER NOT NULL DEFAULT 0,
    skipped_capital INTEGER NOT NULL DEFAULT 0,
    skipped_same_symbol INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS positions (
    position_id INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    target TEXT NOT NULL,
    signal_time INTEGER NOT NULL,
    entry_time INTEGER NOT NULL,
    deadline INTEGER NOT NULL,
    status TEXT NOT NULL,
    notional REAL NOT NULL,
    entry_price REAL,
    quantity REAL,
    tp_price REAL,
    sl_price REAL,
    exit_time INTEGER,
    exit_price REAL,
    exit_reason TEXT,
    gross_pnl REAL,
    fees REAL,
    funding REAL,
    net_pnl REAL,
    tp_score REAL,
    range_prediction REAL,
    volatility_prediction REAL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(scenario_id, symbol, signal_time),
    FOREIGN KEY(scenario_id) REFERENCES scenarios(scenario_id)
);

CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
CREATE INDEX IF NOT EXISTS idx_positions_symbol_status ON positions(symbol, status);

CREATE TABLE IF NOT EXISTS signals (
    signal_id INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    signal_time INTEGER NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT,
    tp_score REAL,
    range_prediction REAL,
    volatility_prediction REAL,
    created_at TEXT NOT NULL,
    UNIQUE(scenario_id, symbol, signal_time)
);

CREATE INDEX IF NOT EXISTS idx_signals_created ON signals(created_at);

CREATE TABLE IF NOT EXISTS spot_demo_orders (
    mirror_id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id INTEGER NOT NULL UNIQUE,
    scenario_id INTEGER NOT NULL,
    paper_symbol TEXT NOT NULL,
    spot_symbol TEXT,
    venue TEXT NOT NULL DEFAULT 'SPOT',
    status TEXT NOT NULL,
    quote_amount REAL NOT NULL,
    base_quantity TEXT,
    buy_order_id TEXT,
    buy_quote TEXT,
    sell_order_id TEXT,
    sell_quote TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(position_id) REFERENCES positions(position_id)
);

CREATE INDEX IF NOT EXISTS idx_spot_demo_status ON spot_demo_orders(status);

CREATE TABLE IF NOT EXISTS leverage_positions (
    leverage_position_id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id INTEGER NOT NULL UNIQUE,
    scenario_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    leverage INTEGER NOT NULL,
    margin_usdt REAL NOT NULL,
    exposure_usdt REAL NOT NULL,
    status TEXT NOT NULL,
    signal_time INTEGER NOT NULL,
    entry_time INTEGER NOT NULL,
    deadline INTEGER NOT NULL,
    entry_price REAL,
    quantity REAL,
    tp_price REAL,
    sl_price REAL,
    liquidation_price REAL,
    exit_time INTEGER,
    exit_price REAL,
    exit_reason TEXT,
    gross_pnl REAL,
    entry_fee REAL NOT NULL DEFAULT 0,
    exit_fee REAL NOT NULL DEFAULT 0,
    funding REAL NOT NULL DEFAULT 0,
    net_pnl REAL,
    return_pct REAL,
    funding_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(position_id) REFERENCES positions(position_id),
    FOREIGN KEY(scenario_id) REFERENCES scenarios(scenario_id)
);

CREATE INDEX IF NOT EXISTS idx_leverage_positions_status ON leverage_positions(status);
CREATE INDEX IF NOT EXISTS idx_leverage_positions_scenario ON leverage_positions(scenario_id);

CREATE TABLE IF NOT EXISTS execution_account_positions (
    account_position_id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id INTEGER NOT NULL,
    account_id TEXT NOT NULL,
    market_type TEXT NOT NULL,
    leverage INTEGER NOT NULL,
    scenario_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    status TEXT NOT NULL,
    signal_time INTEGER NOT NULL,
    entry_time INTEGER NOT NULL,
    deadline INTEGER NOT NULL,
    margin_usdt REAL NOT NULL,
    exposure_usdt REAL NOT NULL,
    entry_price REAL,
    quantity REAL,
    tp_price REAL,
    sl_price REAL,
    liquidation_price REAL,
    exit_time INTEGER,
    exit_price REAL,
    exit_reason TEXT,
    gross_pnl REAL,
    entry_fee REAL NOT NULL DEFAULT 0,
    exit_fee REAL NOT NULL DEFAULT 0,
    funding REAL NOT NULL DEFAULT 0,
    net_pnl REAL,
    return_pct REAL,
    funding_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(position_id, account_id),
    FOREIGN KEY(position_id) REFERENCES positions(position_id),
    FOREIGN KEY(scenario_id) REFERENCES scenarios(scenario_id)
);

CREATE INDEX IF NOT EXISTS idx_execution_account_status
ON execution_account_positions(account_id, status);
CREATE INDEX IF NOT EXISTS idx_execution_account_position
ON execution_account_positions(position_id);

CREATE TABLE IF NOT EXISTS futures_demo_mirrors (
    mirror_id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_position_id INTEGER NOT NULL UNIQUE,
    position_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    leverage INTEGER NOT NULL,
    margin_usdt REAL NOT NULL,
    exposure_usdt REAL NOT NULL,
    status TEXT NOT NULL,
    quantity TEXT,
    entry_order_id TEXT,
    entry_avg_price TEXT,
    exit_order_id TEXT,
    exit_avg_price TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(account_position_id) REFERENCES execution_account_positions(account_position_id),
    FOREIGN KEY(position_id) REFERENCES positions(position_id)
);

CREATE INDEX IF NOT EXISTS idx_futures_demo_mirrors_status
ON futures_demo_mirrors(status);

CREATE TABLE IF NOT EXISTS events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    level TEXT NOT NULL,
    category TEXT NOT NULL,
    message TEXT NOT NULL,
    payload TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);

CREATE TABLE IF NOT EXISTS equity_snapshots (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_id INTEGER NOT NULL,
    cash REAL NOT NULL,
    reserved REAL NOT NULL,
    realized_pnl REAL NOT NULL,
    open_positions INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self.connection() as conn:
            conn.executescript(SCHEMA)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(spot_demo_orders)")}
            if "venue" not in columns:
                conn.execute("ALTER TABLE spot_demo_orders ADD COLUMN venue TEXT NOT NULL DEFAULT 'SPOT'")

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        with self._lock, self.connection() as conn:
            cur = conn.execute(sql, tuple(params))
            return int(cur.lastrowid or 0)

    def executemany(self, sql: str, rows: Iterable[Iterable[Any]]) -> None:
        with self._lock, self.connection() as conn:
            conn.executemany(sql, rows)

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self._lock, self.connection() as conn:
            return [dict(row) for row in conn.execute(sql, tuple(params)).fetchall()]

    def scalar(self, sql: str, params: Iterable[Any] = ()) -> Any:
        with self._lock, self.connection() as conn:
            row = conn.execute(sql, tuple(params)).fetchone()
            return None if row is None else row[0]

    def event(self, level: str, category: str, message: str, payload: Any = None) -> None:
        encoded = None if payload is None else json.dumps(payload, ensure_ascii=False, default=str)
        self.execute(
            "INSERT INTO events(level,category,message,payload,created_at) VALUES(?,?,?,?,?)",
            (level, category, message, encoded, utc_now()),
        )

    def set_meta(self, key: str, value: Any) -> None:
        self.execute(
            """INSERT INTO meta(key,value,updated_at) VALUES(?,?,?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
            (key, json.dumps(value, ensure_ascii=False, default=str), utc_now()),
        )

    def get_meta(self, key: str, default: Any = None) -> Any:
        raw = self.scalar("SELECT value FROM meta WHERE key=?", (key,))
        return default if raw is None else json.loads(raw)
