from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd


VALUE_COLUMNS = [
    "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "trade_count", "taker_buy_base_volume",
    "taker_buy_quote_volume",
]


class CandleHistoryStore:
    """Durable closed-candle archive used by live inference."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=30)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS candles (
                timeframe TEXT NOT NULL, symbol TEXT NOT NULL,
                open_time INTEGER NOT NULL, open REAL NOT NULL,
                high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
                volume REAL NOT NULL, close_time INTEGER NOT NULL,
                quote_volume REAL NOT NULL, trade_count REAL NOT NULL,
                taker_buy_base_volume REAL NOT NULL,
                taker_buy_quote_volume REAL NOT NULL,
                PRIMARY KEY (timeframe, symbol, open_time)
            ) WITHOUT ROWID
            """
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def save(self, timeframe: str, symbol: str, frame: pd.DataFrame) -> int:
        if frame.empty:
            return 0
        clean = frame[~frame.index.duplicated(keep="last")].sort_index()
        rows = []
        for timestamp, row in clean.iterrows():
            rows.append((
                timeframe, symbol,
                int(pd.Timestamp(timestamp).timestamp() * 1000),
                *(float(row[column]) for column in VALUE_COLUMNS[:5]),
                int(row["close_time"]),
                *(float(row[column]) for column in VALUE_COLUMNS[6:]),
            ))
        self.connection.executemany(
            """
            INSERT INTO candles (
                timeframe,symbol,open_time,open,high,low,close,volume,
                close_time,quote_volume,trade_count,taker_buy_base_volume,
                taker_buy_quote_volume
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(timeframe,symbol,open_time) DO UPDATE SET
                open=excluded.open, high=excluded.high, low=excluded.low,
                close=excluded.close, volume=excluded.volume,
                close_time=excluded.close_time,
                quote_volume=excluded.quote_volume,
                trade_count=excluded.trade_count,
                taker_buy_base_volume=excluded.taker_buy_base_volume,
                taker_buy_quote_volume=excluded.taker_buy_quote_volume
            """, rows,
        )
        self.connection.commit()
        return len(rows)

    def load(self, timeframe: str, symbol: str) -> pd.DataFrame:
        rows = self.connection.execute(
            """
            SELECT open_time,open,high,low,close,volume,close_time,quote_volume,
                   trade_count,taker_buy_base_volume,taker_buy_quote_volume
            FROM candles WHERE timeframe=? AND symbol=? ORDER BY open_time
            """, (timeframe, symbol),
        ).fetchall()
        if not rows:
            empty = pd.DataFrame(columns=VALUE_COLUMNS)
            empty.index = pd.DatetimeIndex([], name="open_time", tz="UTC")
            return empty
        frame = pd.DataFrame(rows, columns=["open_time", *VALUE_COLUMNS])
        frame["open_time"] = pd.to_datetime(frame["open_time"], unit="ms", utc=True)
        return frame.set_index("open_time")

    def count(self, timeframe: str, symbol: str) -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) FROM candles WHERE timeframe=? AND symbol=?",
            (timeframe, symbol),
        ).fetchone()
        return int(row[0])
