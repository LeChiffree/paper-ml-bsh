from __future__ import annotations

import gzip
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


RAW_COLUMNS = (
    "open", "high", "low", "close", "volume", "close_time", "quote_volume",
    "trade_count", "taker_buy_base_volume", "taker_buy_quote_volume",
)


class IncrementalShadowRecorder:
    """One-shot 5m reference/delta capture; never participates in trade decisions."""

    def __init__(self, root: Path, duration_hours: float = 3.0):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.pointer = self.root / "current.json"
        self.lock = threading.Lock()
        self.previous_latest: dict[str, tuple[int, tuple[float, ...]]] = {}
        self.capture_count = 0
        self.error: str | None = None
        now = time.time()
        state: dict[str, Any] | None = None
        if self.pointer.exists():
            try:
                state = json.loads(self.pointer.read_text(encoding="utf-8"))
            except Exception:
                state = None
        if state and state.get("completed"):
            self.session_dir = Path(state["session_dir"])
            self.started_at = float(state["started_at"])
            self.deadline = float(state["deadline"])
            self.capture_count = int(state.get("capture_count", 0))
            self.error = state.get("error")
            self.completed = True
            return
        if state and float(state.get("deadline", 0)) > now:
            self.session_dir = Path(state["session_dir"])
            self.started_at = float(state["started_at"])
            self.deadline = float(state["deadline"])
            self.capture_count = int(state.get("capture_count", 0))
            self.error = state.get("error")
        else:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            self.session_dir = self.root / stamp
            self.started_at = now
            self.deadline = now + duration_hours * 3600
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.completed = False
        self._write_state()

    def _write_state(self) -> None:
        payload = {
            "session_dir": str(self.session_dir.resolve()),
            "started_at": self.started_at,
            "deadline": self.deadline,
            "completed": self.completed,
            "capture_count": self.capture_count,
            "error": self.error,
        }
        self.pointer.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def active(self) -> bool:
        return not self.completed and time.time() <= self.deadline

    def status(self) -> dict[str, Any]:
        return {
            "active": self.active(), "completed": self.completed,
            "started_at": datetime.fromtimestamp(self.started_at, timezone.utc).isoformat(),
            "deadline": datetime.fromtimestamp(self.deadline, timezone.utc).isoformat(),
            "capture_count": self.capture_count, "session_dir": str(self.session_dir),
            "error": self.error,
        }

    @staticmethod
    def _latest_signature(frame: pd.DataFrame) -> tuple[int, tuple[float, ...]]:
        row = frame.iloc[-1]
        timestamp = int(frame.index[-1].value // 1_000_000)
        values = tuple(float(row.get(column, np.nan)) for column in RAW_COLUMNS)
        return timestamp, values

    def _save_initial_state(self, frames: dict[str, pd.DataFrame]) -> None:
        symbols: list[str] = []
        timestamps: list[int] = []
        values: list[list[float]] = []
        for symbol, frame in frames.items():
            clean = frame.tail(1000)
            symbols.extend([symbol] * len(clean))
            timestamps.extend((clean.index.asi8 // 1_000_000).tolist())
            values.extend(clean.reindex(columns=RAW_COLUMNS).to_numpy(dtype=float).tolist())
        np.savez_compressed(
            self.session_dir / "initial_5m_state.npz",
            symbols=np.asarray(symbols), timestamps=np.asarray(timestamps, dtype="int64"),
            columns=np.asarray(RAW_COLUMNS), values=np.asarray(values, dtype="float64"),
        )

    def capture(
        self,
        signal_time: int,
        frames: dict[str, pd.DataFrame],
        features: pd.DataFrame,
        predictions: dict[str, dict[str, Any]],
        timings: dict[str, Any],
    ) -> None:
        with self.lock:
            if self.completed:
                return
            if time.time() > self.deadline:
                self.completed = True
                self._write_state()
                return
            started = time.perf_counter()
            try:
                if not (self.session_dir / "initial_5m_state.npz").exists():
                    self._save_initial_state(frames)
                appended = revised = unchanged = 0
                latest_rows: list[list[float]] = []
                latest_symbols: list[str] = []
                latest_times: list[int] = []
                for symbol, frame in frames.items():
                    signature = self._latest_signature(frame)
                    previous = self.previous_latest.get(symbol)
                    if previous is None or signature[0] > previous[0]:
                        appended += 1
                    elif signature != previous:
                        revised += 1
                    else:
                        unchanged += 1
                    self.previous_latest[symbol] = signature
                    latest_symbols.append(symbol)
                    latest_times.append(signature[0])
                    latest_rows.append(list(signature[1]))
                stem = str(signal_time)
                feature_columns = [column for column in features.columns if column != "symbol"]
                np.savez_compressed(
                    self.session_dir / f"cycle_{stem}.npz",
                    feature_symbols=np.asarray(features.index.astype(str)),
                    feature_names=np.asarray(feature_columns),
                    feature_values=features[feature_columns].to_numpy(dtype="float32"),
                    delta_symbols=np.asarray(latest_symbols),
                    delta_timestamps=np.asarray(latest_times, dtype="int64"),
                    delta_columns=np.asarray(RAW_COLUMNS),
                    delta_values=np.asarray(latest_rows, dtype="float64"),
                )
                with gzip.open(self.session_dir / f"predictions_{stem}.json.gz", "wt", encoding="utf-8") as handle:
                    json.dump(predictions, handle, separators=(",", ":"))
                self.capture_count += 1
                metric = {
                    "signal_time": signal_time, "captured_at": datetime.now(timezone.utc).isoformat(),
                    "symbols": len(frames), "appended": appended, "revised": revised,
                    "unchanged": unchanged, **timings,
                    "capture_ms": round((time.perf_counter() - started) * 1000, 1),
                }
                with (self.session_dir / "metrics.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(metric, separators=(",", ":")) + "\n")
                self._write_state()
            except Exception as exc:
                self.error = f"{type(exc).__name__}: {exc}"
                self._write_state()
