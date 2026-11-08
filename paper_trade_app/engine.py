from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd

from binance_client import BinanceClient
from config import SIGNAL_TIMEFRAMES, TIMEFRAME_MINUTES, Settings
from db import Database, utc_now
from exact_features import FEATURE_ORDER, latest_exact_matrix
from model_registry import ModelRegistry, ScenarioSpec, load_scenarios
from spot_demo import SpotDemoClient
from futures_demo import FuturesDemoClient
from incremental_shadow import IncrementalShadowRecorder
from leverage_config import LEVERAGE_BY_SCENARIO
from execution_accounts import EXECUTION_ACCOUNTS
from candle_history import CandleHistoryStore


LOG = logging.getLogger(__name__)


class DataIntegrityError(RuntimeError):
    pass


class PaperTradeEngine:
    def __init__(self, settings: Settings, db: Database):
        self.settings = settings
        self.db = db
        self.registry = ModelRegistry(settings.package_dir)
        self.scenarios = load_scenarios(settings.package_dir, self.registry)
        requested_scenario_ids = settings.active_scenario_ids()
        known_scenario_ids = {spec.scenario_id for spec in self.scenarios}
        missing_scenario_ids = requested_scenario_ids - known_scenario_ids
        if missing_scenario_ids:
            raise ValueError(
                f"Unknown active scenario IDs: {sorted(missing_scenario_ids)}"
            )
        for spec in self.scenarios:
            if spec.scenario_id not in requested_scenario_ids:
                spec.enabled = False
                spec.blocked_reason = "inactive_by_allowlist"
        self.active_scenario_ids = {
            spec.scenario_id for spec in self.scenarios if spec.enabled
        }
        self.client = BinanceClient(
            settings.binance_rest_url,
            settings.binance_ws_url,
            settings.request_concurrency,
        )
        self.spot_client = SpotDemoClient(
            settings.spot_demo_rest_url, settings.binance_api_key, settings.binance_api_secret
        )
        self.futures_clients = {
            "futures_2x": FuturesDemoClient(
                settings.futures_demo_rest_url, settings.futures_2x_api_key, settings.futures_2x_api_secret
            ),
            "futures_5x": FuturesDemoClient(
                settings.futures_demo_rest_url, settings.futures_5x_api_key, settings.futures_5x_api_secret
            ),
            "futures_10x": FuturesDemoClient(
                settings.futures_demo_rest_url, settings.futures_10x_api_key, settings.futures_10x_api_secret
            ),
        }
        self.futures_client = self.futures_clients["futures_10x"]
        self.spot_scenario_ids: set[int] = set()
        self.leverage_by_scenario = dict(LEVERAGE_BY_SCENARIO)
        self._funding_cache: dict[str, tuple[float, list[dict[str, float | int]]]] = {}
        self.spot_demo_connected = False
        self.spot_demo_error: str | None = None
        self.futures_demo_connected = False
        self.futures_demo_error: str | None = None
        self.demo_connections: dict[str, dict[str, Any]] = {
            account_id: {
                "execution_enabled": account_id in settings.motorlab_demo_accounts(),
                "configured": client.configured, "connected": False, "error": None,
            }
            for account_id, client in self.futures_clients.items()
        }
        self.demo_connections["spot_1x"] = {
            "execution_enabled": "spot_1x" in settings.motorlab_demo_accounts(),
            "configured": self.spot_client.configured, "connected": False, "error": None
        }
        self.candles: dict[tuple[str, str], pd.DataFrame] = {}
        self.history_store = (
            CandleHistoryStore(settings.history_db_path)
            if settings.full_history_enabled else None
        )
        self.last_market_prices: dict[str, float] = {}
        self.state = "created"
        self.started_at: float | None = None
        self.last_cycle_at: str | None = None
        self.last_inference_ms: float | None = None
        self.last_feature_ms: float | None = None
        self.last_error: str | None = None
        self.coverage: dict[str, int] = {tf: 0 for tf in SIGNAL_TIMEFRAMES}
        self.last_close: dict[str, str | None] = {tf: None for tf in SIGNAL_TIMEFRAMES}
        self.feature_missing: dict[str, int] = {tf: 0 for tf in SIGNAL_TIMEFRAMES}
        self.refresh_ms: dict[str, float | None] = {tf: None for tf in SIGNAL_TIMEFRAMES}
        self.cycle_start_lag_ms: dict[str, int | None] = {tf: None for tf in SIGNAL_TIMEFRAMES}
        self.integrity_silent_pairs = settings.silent_integrity_pairs()
        self.critical_symbols: set[str] = {"BTCUSDT", "ETHUSDT"}
        self.critical_symbols_updated_at = 0.0
        self._critical_symbols_lock = asyncio.Lock()
        self._excluded_symbols: dict[str, set[str]] = {
            tf: set() for tf in SIGNAL_TIMEFRAMES
        }
        self.incremental_shadow = IncrementalShadowRecorder(
            settings.db_path.parent / "incremental_shadow",
            settings.incremental_shadow_hours,
        )
        self._shadow_tasks: set[asyncio.Task] = set()
        self.stop_event = asyncio.Event()
        self.tasks: list[asyncio.Task] = []
        # A single global lock made coincident 5m/15m/1h/4h boundaries run in
        # series.  Each timeframe owns its own lock so different timeframes can
        # refresh and infer concurrently while duplicate runs of the same
        # timeframe are still prevented.
        self._cycle_locks = {tf: asyncio.Lock() for tf in SIGNAL_TIMEFRAMES}
        self._last_processed_boundary: dict[str, int] = {}
        self._demo_connection_lock = asyncio.Lock()
        self._seed_scenarios()

    async def _refresh_demo_connections(self) -> None:
        """Refresh demo account health so a transient startup timeout can recover."""
        async with self._demo_connection_lock:
            if self.settings.legacy_demo_enabled or self.demo_connections["spot_1x"]["execution_enabled"]:
                try:
                    if not self.spot_client.symbols:
                        await self.spot_client.exchange_info()
                    account = await self.spot_client.account()
                    self.spot_demo_connected = bool(account.get("canTrade"))
                    self.spot_demo_error = None if self.spot_demo_connected else "Spot Demo trading is disabled"
                    self.demo_connections["spot_1x"].update(connected=self.spot_demo_connected, error=self.spot_demo_error)
                except Exception as exc:
                    self.spot_demo_connected = False
                    self.spot_demo_error = f"{type(exc).__name__}: {exc}"
                    self.demo_connections["spot_1x"].update(connected=False, error=self.spot_demo_error)
            for account_id, client in self.futures_clients.items():
                state = self.demo_connections[account_id]
                if not state["execution_enabled"]:
                    state.update(connected=False, error="paper_only")
                    continue
                if not client.configured:
                    state.update(connected=False, error="separate_api_credentials_required")
                    continue
                try:
                    account = await client.initialize() if not client.symbols else await client.account()
                    connected = bool(account.get("canTrade"))
                    error = None if connected else "Futures Demo trading is disabled"
                    state.update(connected=connected, error=error)
                except Exception as exc:
                    state.update(connected=False, error=f"{type(exc).__name__}: {exc}")
            primary = self.demo_connections["futures_10x"]
            self.futures_demo_connected = bool(primary["connected"])
            self.futures_demo_error = primary["error"]

    async def _demo_connection_loop(self) -> None:
        while not self.stop_event.is_set():
            await asyncio.sleep(60)
            await self._refresh_demo_connections()

    def _seed_scenarios(self) -> None:
        now = utc_now()
        for spec in self.scenarios:
            self.db.execute(
                """INSERT INTO scenarios(
                    scenario_id,timeframe,scenario,strategy,target,filter_name,threshold,
                    horizon,tp_pct,sl_pct,vol_low,vol_high,enabled,blocked_reason,
                    initial_cash,cash,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(scenario_id) DO UPDATE SET
                    timeframe=excluded.timeframe,scenario=excluded.scenario,
                    strategy=excluded.strategy,target=excluded.target,
                    filter_name=excluded.filter_name,threshold=excluded.threshold,
                    horizon=excluded.horizon,tp_pct=excluded.tp_pct,sl_pct=excluded.sl_pct,
                    vol_low=excluded.vol_low,vol_high=excluded.vol_high,
                    enabled=excluded.enabled,blocked_reason=excluded.blocked_reason,
                    updated_at=excluded.updated_at""",
                (
                    spec.scenario_id,
                    spec.timeframe,
                    spec.scenario,
                    spec.strategy,
                    spec.target,
                    spec.filter_name,
                    spec.threshold,
                    spec.horizon,
                    spec.tp_pct,
                    spec.sl_pct,
                    spec.vol_low,
                    spec.vol_high,
                    int(spec.enabled),
                    spec.blocked_reason,
                    self.settings.initial_cash_tl,
                    self.settings.initial_cash_tl,
                    now,
                ),
            )
        self.db.set_meta("feature_contract", "portable-exact-v1")
        self.db.set_meta("pipeline_defined_features", [])
        self.db.set_meta("under_specified_features", [])

    async def start(self) -> None:
        if self.tasks:
            return
        self.state = "starting"
        self.started_at = time.time()
        self.stop_event.clear()
        self.db.event(
            "info",
            "engine",
            "Paper trade engine starting",
            {
                "symbols": len(self.registry.symbols),
                "models": len(self.registry.models),
                "scenarios": len(self.scenarios),
            },
        )
        await self.client.start()
        await self._refresh_critical_symbols(force=True)
        demo_enabled = self.settings.legacy_demo_enabled or self.settings.motorlab_demo_enabled
        if demo_enabled:
            await self._refresh_demo_connections()
            if self.settings.legacy_demo_enabled and self.spot_demo_error:
                self.db.event("error", "spot_demo", "Spot Demo connection failed", self.spot_demo_error)
            if self.futures_demo_error:
                self.db.event("error", "futures_demo", "Futures Demo connection failed", self.futures_demo_error)
        self.tasks = [asyncio.create_task(self._bootstrap_and_schedule(), name="scheduler")]
        if demo_enabled:
            self.tasks.append(
                asyncio.create_task(self._demo_connection_loop(), name="demo-connection-monitor")
            )
        if self.settings.live_enabled:
            self.tasks.append(
                asyncio.create_task(
                    self.client.stream_closed_1m(
                        self.registry.symbols, self.on_1m_candle, self.stop_event
                    ),
                    name="one-minute-stream",
                )
            )

    async def stop(self) -> None:
        self.stop_event.set()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
        if self._shadow_tasks:
            await asyncio.gather(*self._shadow_tasks, return_exceptions=True)
        await self.client.close()
        await self.spot_client.close()
        await asyncio.gather(*(client.close() for client in self.futures_clients.values()))
        if self.history_store is not None:
            self.history_store.close()
        self.state = "stopped"
        self.db.event("warning", "engine", "Paper trade engine stopped")

    async def _bootstrap_and_schedule(self) -> None:
        try:
            await self.bootstrap()
            self.state = "running" if self.settings.live_enabled else "shadow-ready"
            while not self.stop_event.is_set():
                now = datetime.now(timezone.utc)
                wait = 60 - now.second + self.settings.candle_grace_seconds
                await asyncio.sleep(max(1, wait))
                await self.run_due_cycles()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.state = "error"
            self.last_error = f"{type(exc).__name__}: {exc}"
            self.db.event("error", "engine", "Engine scheduler failed", self.last_error)
            LOG.exception("Engine scheduler failed")

    async def bootstrap(self) -> None:
        self.state = "backfilling"
        active_timeframes = sorted({spec.timeframe for spec in self.scenarios if spec.enabled})
        total = len(self.registry.symbols) * len(active_timeframes)
        completed = 0

        async def load_one(tf: str, symbol: str) -> None:
            nonlocal completed
            if self.history_store is not None:
                cached = self.history_store.load(tf, symbol)
                start_ms = int(pd.Timestamp(self.settings.history_start_utc).timestamp() * 1000)
                if not cached.empty:
                    start_ms = int(cached.index[-1].timestamp() * 1000) + 1
                downloaded = await self.client.historical_klines(symbol, tf, start_ms)
                self.history_store.save(tf, symbol, downloaded)
                frame = pd.concat([cached, downloaded])
                frame = frame[~frame.index.duplicated(keep="last")].sort_index()
            else:
                frame = await self.client.klines(
                    symbol, tf, limit=min(self.settings.warmup_bars, 1500)
                )
            frame = await self._ensure_complete_candles(tf, symbol, frame)
            self.candles[(tf, symbol)] = frame
            completed += 1
            self.coverage[tf] = sum(1 for s in self.registry.symbols if (tf, s) in self.candles)
            if completed % 25 == 0 or completed == total:
                self.db.set_meta("bootstrap_progress", {"completed": completed, "total": total})

        for tf in active_timeframes:
            jobs = [load_one(tf, symbol) for symbol in self.registry.symbols]
            results = await asyncio.gather(*jobs, return_exceptions=True)
            failures = [
                (symbol, str(item)) for symbol, item in zip(self.registry.symbols, results)
                if isinstance(item, Exception)
            ]
            visible_failures = [
                item for item in failures if not self._integrity_alert_suppressed(tf, item[0])
            ]
            critical_failures = [item for item in visible_failures if item[0] in self.critical_symbols]
            if critical_failures:
                self.db.event(
                    "warning",
                    "data_integrity",
                    f"{tf} bootstrap has critical-volume symbols missing",
                    {"timeframe": tf, "symbols": [item[0] for item in critical_failures],
                     "top_volume_count": 20},
                )
            valid = [self.candles[(tf, s)].index[-1] for s in self.registry.symbols if (tf, s) in self.candles]
            if valid:
                self.last_close[tf] = max(valid).isoformat()
        self.db.event("info", "data", "Historical candle bootstrap complete", self.coverage)

    async def run_due_cycles(self) -> None:
        now = datetime.now(timezone.utc)
        active_timeframes = {
            spec.timeframe for spec in self.scenarios if spec.enabled
        }
        due = []
        if "5m" in active_timeframes and now.minute % 5 == 0:
            due.append("5m")
        if "15m" in active_timeframes and now.minute % 15 == 0:
            due.append("15m")
        if "1h" in active_timeframes and now.minute == 0:
            due.append("1h")
        if "4h" in active_timeframes and now.minute == 0 and now.hour % 4 == 0:
            due.append("4h")
        jobs = []
        for tf in due:
            boundary = int(now.timestamp()) // (TIMEFRAME_MINUTES[tf] * 60)
            if self._last_processed_boundary.get(tf) == boundary:
                continue
            # Reserve the boundary before scheduling so a second scheduler tick
            # cannot enqueue the same candle while this cycle is still running.
            self._last_processed_boundary[tf] = boundary
            jobs.append(self.run_timeframe_cycle(tf))
        # A slower timeframe must never make another timeframe miss its entry
        # window. Each timeframe has its own lock and candle-integrity gate.
        if jobs:
            await asyncio.gather(*jobs)

    async def run_timeframe_cycle(self, timeframe: str) -> None:
        """Refresh and evaluate one timeframe without blocking other timeframes."""
        async with self._cycle_locks[timeframe]:
            cycle_started = time.perf_counter()
            minutes = TIMEFRAME_MINUTES[timeframe]
            boundary_ms = int(pd.Timestamp.now(tz="UTC").floor(f"{minutes}min").timestamp() * 1000)
            if not hasattr(self, "cycle_start_lag_ms"):
                self.cycle_start_lag_ms = {}
            if not hasattr(self, "refresh_ms"):
                self.refresh_ms = {}
            self.cycle_start_lag_ms[timeframe] = int(time.time() * 1000) - boundary_ms
            if await self.refresh_timeframe(timeframe) is False:
                self.refresh_ms[timeframe] = (time.perf_counter() - cycle_started) * 1000
                return
            self.refresh_ms[timeframe] = (time.perf_counter() - cycle_started) * 1000
            await self._evaluate_timeframe_unlocked(timeframe)

    def _integrity_alert_suppressed(self, timeframe: str, symbol: str) -> bool:
        return (timeframe, symbol) in self.integrity_silent_pairs

    async def _refresh_critical_symbols(self, force: bool = False) -> None:
        if not force and time.time() - self.critical_symbols_updated_at < 900:
            return
        async with self._critical_symbols_lock:
            if not force and time.time() - self.critical_symbols_updated_at < 900:
                return
            try:
                volumes = await self.client.quote_volumes_24h()
                ranked = sorted(
                    self.registry.symbols,
                    key=lambda symbol: volumes.get(symbol, 0.0),
                    reverse=True,
                )
                self.critical_symbols = set(ranked[:20]) | {"BTCUSDT", "ETHUSDT"}
                self.critical_symbols_updated_at = time.time()
                self.db.set_meta("critical_volume_symbols", sorted(self.critical_symbols))
            except Exception as exc:
                # Keep the last successful ranking. BTC and ETH always remain critical.
                self.db.event(
                    "warning", "data_integrity",
                    "Top-volume symbol ranking could not be refreshed",
                    {"error": f"{type(exc).__name__}: {exc}"},
                )

    def _candle_integrity(self, timeframe: str, frame: pd.DataFrame) -> dict[str, Any]:
        minutes = TIMEFRAME_MINUTES[timeframe]
        step = pd.Timedelta(minutes=minutes)
        now = pd.Timestamp.now(tz="UTC")
        expected_last = now.floor(f"{minutes}min") - step
        clean = frame[~frame.index.duplicated(keep="last")].sort_index()
        if clean.empty:
            return {"valid": False, "missing": 0, "reason": "empty", "expected_last": expected_last}
        internal = pd.date_range(clean.index[0], clean.index[-1], freq=step)
        internal_missing = len(internal.difference(clean.index))
        stale_bars = max(0, int((expected_last - clean.index[-1]) // step))
        future_bars = max(0, int((clean.index[-1] - expected_last) // step))
        missing = internal_missing + stale_bars
        reason = None
        if len(clean) < 250:
            reason = f"insufficient_warmup:{len(clean)}"
        elif internal_missing:
            reason = f"internal_gap:{internal_missing}"
        elif stale_bars:
            reason = f"stale:{stale_bars}"
        elif future_bars:
            reason = f"future_or_open_candle:{future_bars}"
        return {"valid": reason is None, "missing": missing, "reason": reason,
                "expected_last": expected_last, "actual_last": clean.index[-1]}

    async def _ensure_complete_candles(
        self, timeframe: str, symbol: str, frame: pd.DataFrame,
        full_history_repair: bool = True,
    ) -> pd.DataFrame:
        frame = frame[~frame.index.duplicated(keep="last")].sort_index()
        if getattr(self, "history_store", None) is None:
            frame = frame.tail(self.settings.warmup_bars)
        before = self._candle_integrity(timeframe, frame)
        if before["valid"]:
            return frame

        # Never synthesize candles. Re-fetch the complete closed-candle window
        # from Binance and accept it only after a second continuity check.
        if getattr(self, "history_store", None) is not None and full_history_repair:
            start_ms = int(pd.Timestamp(self.settings.history_start_utc).timestamp() * 1000)
            repaired = await self.client.historical_klines(symbol, timeframe, start_ms)
            self.history_store.save(timeframe, symbol, repaired)
        elif getattr(self, "history_store", None) is not None:
            payload = {
                "symbol": symbol, "timeframe": timeframe,
                "missing_bars": before["missing"], "reason": before["reason"],
                "repaired": False, "repair_scope": "recent_only",
            }
            if not self._integrity_alert_suppressed(timeframe, symbol):
                self.db.event("error", "data_integrity", "Recent candle refresh incomplete", payload)
            raise DataIntegrityError(
                f"{symbol} {timeframe} candle integrity failed: {before['reason']}"
            )
        else:
            repaired = await self.client.klines(
                symbol, timeframe, limit=min(self.settings.warmup_bars, 1500)
            )
            repaired = repaired[~repaired.index.duplicated(keep="last")].sort_index().tail(
                self.settings.warmup_bars
            )
        after = self._candle_integrity(timeframe, repaired)
        payload = {
            "symbol": symbol, "timeframe": timeframe,
            "missing_bars": before["missing"], "reason": before["reason"],
            "repaired": after["valid"],
        }
        if not after["valid"]:
            payload["after_reason"] = after["reason"]
            if not self._integrity_alert_suppressed(timeframe, symbol):
                self.db.event("error", "data_integrity", "Candle integrity repair failed", payload)
            raise DataIntegrityError(
                f"{symbol} {timeframe} candle integrity failed: {after['reason']}"
            )
        if before["missing"] >= self.settings.data_gap_warning_bars:
            self.db.event("warning", "data_integrity", "Large candle gap repaired", payload)
        return repaired

    async def refresh_timeframe(self, timeframe: str) -> bool:
        await self._refresh_critical_symbols()
        async def refresh(symbol: str) -> tuple[str, pd.DataFrame | Exception]:
            try:
                result = await self.client.klines(symbol, timeframe, limit=5)
                old = self.candles.get((timeframe, symbol), pd.DataFrame())
                combined = pd.concat([old, result])
                combined = combined[~combined.index.duplicated(keep="last")].sort_index()
                integrity = self._candle_integrity(timeframe, combined)
                if not integrity["valid"] and str(integrity["reason"]).startswith("stale:"):
                    # Binance occasionally publishes a just-closed candle a few seconds late.
                    # Retry only the recent window; never re-download years of history here.
                    await asyncio.sleep(3)
                    retry = await self.client.klines(symbol, timeframe, limit=5)
                    result = pd.concat([result, retry])
                    result = result[~result.index.duplicated(keep="last")].sort_index()
                return symbol, result
            except Exception as exc:
                return symbol, exc

        results = await asyncio.gather(*(refresh(s) for s in self.registry.symbols))
        failures = []
        latest = []
        for symbol, result in results:
            if isinstance(result, Exception):
                failures.append((symbol, str(result)))
                continue
            old = self.candles.get((timeframe, symbol), pd.DataFrame())
            combined = pd.concat([old, result])
            combined = combined[~combined.index.duplicated(keep="last")].sort_index()
            try:
                combined = await self._ensure_complete_candles(
                    timeframe, symbol, combined, full_history_repair=False
                )
            except Exception as exc:
                failures.append((symbol, str(exc)))
                continue
            self.candles[(timeframe, symbol)] = combined
            history_store = getattr(self, "history_store", None)
            if history_store is not None and not result.empty:
                history_store.save(timeframe, symbol, result)
            if not result.empty:
                latest.append(result.index[-1])
        self.coverage[timeframe] = sum(
            1 for symbol in self.registry.symbols if (timeframe, symbol) in self.candles
        )
        if latest:
            self.last_close[timeframe] = max(latest).isoformat()
        if failures:
            visible_failures = [
                item for item in failures
                if not self._integrity_alert_suppressed(timeframe, item[0])
            ]
            critical_failures = [
                item for item in visible_failures if item[0] in self.critical_symbols
            ]
            if critical_failures:
                self.db.event(
                    "error",
                    "data_integrity",
                    f"{timeframe} cycle blocked: critical-volume candle missing",
                    {"timeframe": timeframe,
                     "symbols": [item[0] for item in critical_failures],
                     "failures": critical_failures[:10], "top_volume_count": 20},
                )
                self._excluded_symbols[timeframe] = {item[0] for item in failures}
                self.last_error = (
                    f"{timeframe} blocked by critical candle integrity: "
                    f"{', '.join(item[0] for item in critical_failures)}"
                )
                return False
            self._excluded_symbols[timeframe] = {item[0] for item in failures}
            self.db.event(
                "info", "data",
                f"{timeframe} continuing without {len(failures)} non-critical symbols",
                {"timeframe": timeframe, "symbols": sorted(self._excluded_symbols[timeframe])},
            )
        else:
            self._excluded_symbols[timeframe].clear()
        return True

    def _feature_matrix(self, timeframe: str) -> pd.DataFrame:
        available = {
            symbol: self.candles[(timeframe, symbol)]
            for symbol in self.registry.symbols
            if (timeframe, symbol) in self.candles
            and symbol not in self._excluded_symbols[timeframe]
        }
        if "BTCUSDT" not in available or "ETHUSDT" not in available:
            raise RuntimeError(f"BTC/ETH context missing for {timeframe}")
        matrix, missing_total = latest_exact_matrix(available)
        self.feature_missing[timeframe] = missing_total
        if matrix.empty:
            raise RuntimeError(f"No valid feature rows for {timeframe}")
        return matrix

    def _calculate_predictions(
        self, timeframe: str
    ) -> tuple[pd.DataFrame, float, float, dict[str, dict[str, Any]]]:
        """CPU-heavy feature generation and inference, executed off the event loop."""
        feature_started = time.perf_counter()
        features = self._feature_matrix(timeframe)
        feature_ms = (time.perf_counter() - feature_started) * 1000
        inference_started = time.perf_counter()
        predictions: dict[str, dict[str, Any]] = {}
        needed_targets: set[str] = set()
        for spec in self.scenarios:
            if spec.timeframe != timeframe or not spec.enabled:
                continue
            needed_targets.add(spec.target)
            if spec.filter_name in {"range_full", "range_half_volatility", "range_full_volatility"}:
                needed_targets.add(f"future_range_{spec.horizon}mum_pct")
            if spec.filter_name in {"volatility", "range_half_volatility", "range_full_volatility"}:
                needed_targets.add(f"realized_volatility_{spec.horizon}mum_pct")
        for target in needed_targets:
            predictions[target] = self.registry.predict(timeframe, target, features)
        inference_ms = (time.perf_counter() - inference_started) * 1000
        return features, feature_ms, inference_ms, predictions

    async def evaluate_timeframe(self, timeframe: str) -> None:
        async with self._cycle_locks[timeframe]:
            await self._evaluate_timeframe_unlocked(timeframe)

    async def _evaluate_timeframe_unlocked(self, timeframe: str) -> None:
        try:
            features, feature_ms, inference_ms, predictions = await asyncio.to_thread(
                self._calculate_predictions, timeframe
            )
            symbol_count = len(features)
            self.last_feature_ms = feature_ms
            self.last_inference_ms = inference_ms
            signal_time = self._signal_time_ms(timeframe)
            entry_time = signal_time + TIMEFRAME_MINUTES[timeframe] * 60_000
            lag_ms = int(time.time() * 1000) - entry_time
            max_lag_ms = self.settings.entry_max_lag_seconds * 1_000
            entry_eligible = -5_000 <= lag_ms <= max_lag_ms
            if entry_eligible:
                self._apply_scenarios(timeframe, signal_time, predictions)
            self.last_cycle_at = utc_now()
            self.last_error = None
            self.db.event(
                "info",
                "cycle",
                f"{timeframe} cycle completed",
                {
                    "symbols": symbol_count,
                    "feature_ms": round(feature_ms, 1),
                    "inference_ms": round(inference_ms, 1),
                    "refresh_ms": round(self.refresh_ms.get(timeframe) or 0, 1),
                    "cycle_start_lag_ms": self.cycle_start_lag_ms.get(timeframe),
                    "missing_features": self.feature_missing[timeframe],
                    "entry_eligible": entry_eligible,
                    "entry_lag_ms": lag_ms,
                    "entry_max_lag_ms": max_lag_ms,
                },
            )
            if timeframe == "5m" and self.incremental_shadow.active():
                frames = {
                    symbol: self.candles[(timeframe, symbol)]
                    for symbol in self.registry.symbols
                    if (timeframe, symbol) in self.candles
                    and symbol not in self._excluded_symbols[timeframe]
                }
                timings = {
                    "refresh_ms": round(self.refresh_ms.get(timeframe) or 0, 1),
                    "feature_ms": round(feature_ms, 1),
                    "inference_ms": round(inference_ms, 1),
                    "entry_lag_ms": lag_ms,
                    "entry_eligible": entry_eligible,
                }
                task = asyncio.create_task(asyncio.to_thread(
                    self.incremental_shadow.capture, signal_time, frames,
                    features.copy(), predictions, timings,
                ))
                self._shadow_tasks.add(task)
                task.add_done_callback(self._shadow_tasks.discard)
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            self.db.event("error", "cycle", f"{timeframe} cycle failed", self.last_error)
            LOG.exception("%s cycle failed", timeframe)

    def _signal_time_ms(self, timeframe: str) -> int:
        values = [
            frame.index[-1]
            for (tf, symbol), frame in self.candles.items()
            if tf == timeframe and symbol not in self._excluded_symbols[timeframe]
            and not frame.empty
        ]
        if not values:
            return int(time.time() * 1000)
        return int(min(values).timestamp() * 1000)

    def _apply_scenarios(
        self,
        timeframe: str,
        signal_time: int,
        predictions: dict[str, dict[str, Any]],
    ) -> None:
        for spec in self.scenarios:
            if spec.timeframe != timeframe or not spec.enabled:
                continue
            classifier = predictions.get(spec.target, {})
            range_target = f"future_range_{spec.horizon}mum_pct"
            vol_target = f"realized_volatility_{spec.horizon}mum_pct"
            candidates = []
            for symbol, cls in classifier.items():
                if cls["prediction"] != "TP":
                    continue
                score = float(cls["tp_score"])
                threshold = spec.threshold
                if threshold is not None and score < threshold:
                    continue
                range_value = predictions.get(range_target, {}).get(symbol)
                vol_value = predictions.get(vol_target, {}).get(symbol)
                vol_pass = vol_value is not None and spec.vol_low <= vol_value <= spec.vol_high
                passes = {
                    "signal_only": True,
                    "threshold": True,
                    "range_full": range_value is not None and range_value >= spec.tp_pct,
                    "range_half_volatility": range_value is not None and range_value >= spec.tp_pct / 2 and vol_pass,
                    "range_full_volatility": range_value is not None and range_value >= spec.tp_pct and vol_pass,
                    "volatility": vol_pass,
                }.get(spec.filter_name, False)
                if passes:
                    candidates.append((symbol, score, range_value, vol_value))
            candidates.sort(
                key=lambda item: (
                    -item[1],
                    -float(item[2] if item[2] is not None else -math.inf),
                    item[0],
                )
            )
            for symbol, score, range_value, vol_value in candidates:
                self._accept_candidate(spec, symbol, signal_time, score, range_value, vol_value)

    def _accept_candidate(
        self,
        spec: ScenarioSpec,
        symbol: str,
        signal_time: int,
        score: float,
        range_value: float | None,
        vol_value: float | None,
    ) -> None:
        stackable = spec.scenario_id in self.active_scenario_ids
        existing = None if stackable else self.db.scalar(
            "SELECT 1 FROM positions WHERE scenario_id=? AND symbol=? AND status IN ('PENDING','OPEN')",
            (spec.scenario_id, symbol),
        )
        if existing:
            self.db.execute(
                "UPDATE scenarios SET skipped_same_symbol=skipped_same_symbol+1,updated_at=? WHERE scenario_id=?",
                (utc_now(), spec.scenario_id),
            )
            self._record_signal(spec, symbol, signal_time, "rejected", "same_symbol", score, range_value, vol_value)
            return
        entry_time = signal_time + TIMEFRAME_MINUTES[spec.timeframe] * 60_000
        deadline = entry_time + spec.horizon * TIMEFRAME_MINUTES[spec.timeframe] * 60_000
        now = utc_now()
        try:
            position_id = self.db.execute(
                """INSERT INTO positions(
                    scenario_id,symbol,timeframe,target,signal_time,entry_time,deadline,status,
                    notional,tp_score,range_prediction,volatility_prediction,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    spec.scenario_id,
                    symbol,
                    spec.timeframe,
                    spec.target,
                    signal_time,
                    entry_time,
                    deadline,
                    "PENDING",
                    self.settings.trade_notional_tl,
                    score,
                    range_value,
                    vol_value,
                    now,
                    now,
                ),
            )
        except Exception:
            return
        margin = self.settings.execution_account_margin_usdt
        self.db.executemany(
            """INSERT OR IGNORE INTO execution_account_positions(
            position_id,account_id,market_type,leverage,scenario_id,symbol,timeframe,
            status,signal_time,entry_time,deadline,margin_usdt,exposure_usdt,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                (
                    position_id, account_id, config["market"], config["leverage"],
                    spec.scenario_id, symbol, spec.timeframe, "PENDING", signal_time,
                    entry_time, deadline,
                    margin, margin * config["leverage"], now, now,
                )
                for account_id, config in EXECUTION_ACCOUNTS.items()
            ),
        )
        self._record_signal(spec, symbol, signal_time, "accepted", None, score, range_value, vol_value)

    def _record_signal(
        self,
        spec: ScenarioSpec,
        symbol: str,
        signal_time: int,
        decision: str,
        reason: str | None,
        score: float,
        range_value: float | None,
        vol_value: float | None,
    ) -> None:
        self.db.execute(
            """INSERT OR IGNORE INTO signals(
                scenario_id,symbol,timeframe,signal_time,decision,reason,tp_score,
                range_prediction,volatility_prediction,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                spec.scenario_id,
                symbol,
                spec.timeframe,
                signal_time,
                decision,
                reason,
                score,
                range_value,
                vol_value,
                utc_now(),
            ),
        )

    async def on_1m_candle(self, symbol: str, candle: dict) -> None:
        self.last_market_prices[symbol] = float(candle["close"])
        open_time = int(candle["open_time"])
        positions = self.db.query(
            """SELECT p.*,s.tp_pct,s.sl_pct
            FROM positions p JOIN scenarios s USING(scenario_id)
            WHERE p.symbol=? AND p.status IN ('PENDING','OPEN')
              AND EXISTS (
                SELECT 1 FROM execution_account_positions e
                WHERE e.position_id=p.position_id
              )""",
            (symbol,),
        )
        for position in positions:
            if position["status"] == "PENDING" and open_time >= position["entry_time"]:
                entry_price = float(candle["open"])
                if position["timeframe"] == "4h":
                    entry_price *= 1.0002
                quantity = position["notional"] / entry_price
                self.db.execute(
                    """UPDATE positions SET status='OPEN',entry_price=?,quantity=?,tp_price=?,sl_price=?,updated_at=?
                    WHERE position_id=? AND status='PENDING'""",
                    (
                        entry_price,
                        quantity,
                        entry_price * (1 + position["tp_pct"] / 100),
                        entry_price * (1 - position["sl_pct"] / 100),
                        utc_now(),
                        position["position_id"],
                    ),
                )
                position.update(
                    {
                        "status": "OPEN",
                        "entry_price": entry_price,
                        "quantity": quantity,
                        "tp_price": entry_price * (1 + position["tp_pct"] / 100),
                        "sl_price": entry_price * (1 - position["sl_pct"] / 100),
                    }
                )
                await self._open_execution_accounts(position)
            if position["status"] != "OPEN":
                continue
            await self._liquidate_execution_accounts(position, candle)
            reason = None
            exit_price = None
            if candle["low"] <= position["sl_price"]:
                reason = "stop"
                exit_price = position["sl_price"]
            elif candle["high"] >= position["tp_price"]:
                reason = "take"
                exit_price = position["tp_price"]
            elif candle["close_time"] >= position["deadline"]:
                reason = "time"
                exit_price = float(candle["close"])
            if reason:
                await self._close_position(position, int(candle["close_time"]), float(exit_price), reason)

    async def _close_position(
        self, position: dict, exit_time: int, exit_price: float, reason: str
    ) -> None:
        if position["timeframe"] == "4h":
            exit_price *= 0.9998
        gross = position["quantity"] * (exit_price - position["entry_price"])
        fee_rate = 0.0005 if position["timeframe"] == "4h" else 0.001
        exit_notional = position["quantity"] * exit_price
        fees = (position["notional"] + exit_notional) * fee_rate
        funding = 0.0
        net = gross - fees - funding
        now = utc_now()
        changed = self.db.execute(
            """UPDATE positions SET status='CLOSED',exit_time=?,exit_price=?,exit_reason=?,
            gross_pnl=?,fees=?,funding=?,net_pnl=?,updated_at=? WHERE position_id=? AND status='OPEN'""",
            (
                exit_time,
                exit_price,
                reason,
                gross,
                fees,
                funding,
                net,
                now,
                position["position_id"],
            ),
        )
        outcome_column = {"take": "win_count", "stop": "loss_count", "time": "time_count"}[reason]
        self.db.execute(
            f"""UPDATE scenarios SET cash=cash+?,realized_pnl=realized_pnl+?,trade_count=trade_count+1,
            {outcome_column}={outcome_column}+1,updated_at=? WHERE scenario_id=?""",
            (net, net, now, position["scenario_id"]),
        )
        self.db.event(
            "info",
            "position",
            f"{reason.upper()} {position['symbol']} scenario {position['scenario_id']}",
            {"net_pnl": round(net, 4), "exit_price": exit_price},
        )
        await self._close_execution_accounts(position, exit_time, exit_price, reason)
        if self.settings.legacy_demo_enabled:
            await self._close_leverage_paper(position, exit_time, exit_price, reason)
            await self._close_spot_demo(position)

    async def _open_execution_accounts(self, position: dict[str, Any]) -> None:
        rows = self.db.query(
            """SELECT * FROM execution_account_positions
            WHERE position_id=? AND status='PENDING'""",
            (position["position_id"],),
        )
        entry_price = float(position["entry_price"])
        now = utc_now()
        for row in rows:
            exposure = float(row["exposure_usdt"])
            leverage = int(row["leverage"])
            quantity = exposure / entry_price
            account = EXECUTION_ACCOUNTS[row["account_id"]]
            liquidation_price = None
            if row["market_type"] == "FUTURES":
                liquidation_price = entry_price * (
                    1 - (1 / leverage) + self.settings.leverage_paper_maintenance_rate
                )
            entry_fee = exposure * float(account["fee_rate"])
            self.db.execute(
                """UPDATE execution_account_positions SET status='OPEN',entry_price=?,
                quantity=?,tp_price=?,sl_price=?,liquidation_price=?,entry_fee=?,updated_at=?
                WHERE account_position_id=? AND status='PENDING'""",
                (
                    entry_price, quantity, position["tp_price"], position["sl_price"],
                    liquidation_price, entry_fee, now, row["account_position_id"],
                ),
            )
            if (
                getattr(self.settings, "motorlab_demo_enabled", False)
                and row["account_id"] in self.settings.motorlab_demo_accounts()
            ):
                await self._open_motorlab_demo(row)

    async def _open_motorlab_demo(self, row: dict[str, Any]) -> None:
        if row["account_id"] == "spot_1x":
            await self._open_motorlab_spot_demo(row)
            return
        if row["account_id"] not in self.futures_clients:
            return
        client = self.futures_clients[row["account_id"]]
        connection = self.demo_connections[row["account_id"]]
        leverage = int(row["leverage"])
        now = utc_now()
        self.db.execute(
            """INSERT OR IGNORE INTO futures_demo_mirrors(
            account_position_id,position_id,symbol,leverage,margin_usdt,exposure_usdt,
            status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                row["account_position_id"], row["position_id"], row["symbol"], leverage,
                self.settings.motorlab_demo_margin_usdt,
                self.settings.motorlab_demo_margin_usdt * leverage, "OPENING", now, now,
            ),
        )
        existing = self.db.query(
            "SELECT * FROM futures_demo_mirrors WHERE account_position_id=?",
            (row["account_position_id"],),
        )[0]
        if existing["status"] != "OPENING":
            return
        try:
            if not connection["connected"]:
                await self._refresh_demo_connections()
            if not connection["connected"]:
                raise RuntimeError(connection["error"] or "Futures Demo is disconnected")
            if not client.supports(row["symbol"]):
                raise RuntimeError("symbol_not_available_on_futures_demo")
            order = await client.market_buy_leveraged_margin(
                row["symbol"], self.settings.motorlab_demo_margin_usdt, leverage,
                f"ml{leverage}e{row['account_position_id']}",
            )
            self.db.execute(
                """UPDATE futures_demo_mirrors SET status='OPEN',quantity=?,
                entry_order_id=?,entry_avg_price=?,error=NULL,updated_at=?
                WHERE account_position_id=?""",
                (
                    order.get("executedQty") or order.get("requestedQuantity"),
                    str(order.get("orderId")), str(order.get("avgPrice") or ""),
                    utc_now(), row["account_position_id"],
                ),
            )
            self.db.event("info", "motorlab_demo", f"OPEN {row['symbol']} {leverage}x", {
                "account_position_id": row["account_position_id"],
                "order_id": order.get("orderId"), "leverage": leverage,
                "margin_usdt": self.settings.motorlab_demo_margin_usdt,
                "notional_usdt": self.settings.motorlab_demo_margin_usdt * leverage,
            })
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            self.db.execute(
                """UPDATE futures_demo_mirrors SET status='OPEN_ERROR',error=?,updated_at=?
                WHERE account_position_id=?""",
                (error, utc_now(), row["account_position_id"]),
            )
            self.db.event("error", "motorlab_demo", f"OPEN ERROR {row['symbol']} {leverage}x", error)

    async def _open_motorlab_spot_demo(self, row: dict[str, Any]) -> None:
        now = utc_now()
        try:
            if not self.spot_demo_connected:
                await self._refresh_demo_connections()
            if not self.spot_demo_connected:
                raise RuntimeError(self.spot_demo_error or "Spot Demo is disconnected")
            symbol = self.spot_client.spot_symbol(row["symbol"])
            if not symbol:
                raise RuntimeError("symbol_not_available_on_spot_demo")
            order = await self.spot_client.market_buy(
                symbol, self.settings.motorlab_demo_margin_usdt,
                f"mlspe{row['account_position_id']}",
            )
            quantity = self.spot_client.net_base_quantity(order)
            self.db.execute(
                """INSERT OR REPLACE INTO spot_demo_orders(
                position_id,scenario_id,paper_symbol,spot_symbol,venue,status,quote_amount,
                base_quantity,buy_order_id,buy_quote,error,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (row["position_id"], row["scenario_id"], row["symbol"], symbol, "SPOT",
                 "OPEN", self.settings.motorlab_demo_margin_usdt, format(quantity, "f"),
                 str(order.get("orderId")), str(order.get("cummulativeQuoteQty") or ""),
                 None, now, now),
            )
        except Exception as exc:
            self.db.event("error", "motorlab_demo", f"SPOT OPEN ERROR {row['symbol']}", str(exc))

    async def _close_motorlab_demo(self, row: dict[str, Any]) -> None:
        if row["account_id"] == "spot_1x":
            mirror = self.db.query(
                "SELECT * FROM spot_demo_orders WHERE position_id=? AND status='OPEN'",
                (row["position_id"],),
            )
            if mirror:
                item = mirror[0]
                try:
                    order = await self.spot_client.market_sell(
                        item["spot_symbol"], Decimal(item["base_quantity"]),
                        f"mlspx{row['account_position_id']}",
                    )
                    self.db.execute(
                        """UPDATE spot_demo_orders SET status='CLOSED',sell_order_id=?,
                        sell_quote=?,updated_at=? WHERE mirror_id=?""",
                        (str(order.get("orderId")), str(order.get("cummulativeQuoteQty") or ""),
                         utc_now(), item["mirror_id"]),
                    )
                except Exception as exc:
                    self.db.execute(
                        "UPDATE spot_demo_orders SET status='CLOSE_ERROR',error=?,updated_at=? WHERE mirror_id=?",
                        (f"{type(exc).__name__}: {exc}", utc_now(), item["mirror_id"]),
                    )
            return
        mirror = self.db.query(
            "SELECT * FROM futures_demo_mirrors WHERE account_position_id=? AND status='OPEN'",
            (row["account_position_id"],),
        )
        if not mirror:
            return
        item = mirror[0]
        client = self.futures_clients.get(row["account_id"])
        if client is None:
            return
        try:
            order = await client.market_close(
                row["symbol"], Decimal(str(item["quantity"])),
                f"ml{row['leverage']}x{row['account_position_id']}",
            )
            self.db.execute(
                """UPDATE futures_demo_mirrors SET status='CLOSED',exit_order_id=?,
                exit_avg_price=?,error=NULL,updated_at=? WHERE account_position_id=?""",
                (str(order.get("orderId")), str(order.get("avgPrice") or ""),
                 utc_now(), row["account_position_id"]),
            )
        except Exception as exc:
            self.db.execute(
                """UPDATE futures_demo_mirrors SET status='CLOSE_ERROR',error=?,updated_at=?
                WHERE account_position_id=?""",
                (f"{type(exc).__name__}: {exc}", utc_now(), row["account_position_id"]),
            )

    async def _liquidate_execution_accounts(
        self, position: dict[str, Any], candle: dict[str, Any]
    ) -> None:
        rows = self.db.query(
            """SELECT * FROM execution_account_positions WHERE position_id=?
            AND status='OPEN' AND market_type='FUTURES' AND liquidation_price IS NOT NULL""",
            (position["position_id"],),
        )
        for row in rows:
            if float(candle["low"]) <= float(row["liquidation_price"]):
                await self._close_execution_account_row(
                    row, int(candle["close_time"]), float(row["liquidation_price"]), "liquidation"
                )

    async def _close_execution_accounts(
        self, position: dict[str, Any], exit_time: int, exit_price: float, reason: str
    ) -> None:
        rows = self.db.query(
            """SELECT * FROM execution_account_positions
            WHERE position_id=? AND status='OPEN'""",
            (position["position_id"],),
        )
        for row in rows:
            await self._close_execution_account_row(row, exit_time, exit_price, reason)

    async def _close_execution_account_row(
        self, row: dict[str, Any], exit_time: int, exit_price: float, reason: str
    ) -> None:
        if (
            getattr(self.settings, "motorlab_demo_enabled", False)
            and row["account_id"] in self.settings.motorlab_demo_accounts()
        ):
            await self._close_motorlab_demo(row)
        quantity = float(row["quantity"])
        gross = quantity * (exit_price - float(row["entry_price"]))
        fee_rate = float(EXECUTION_ACCOUNTS[row["account_id"]]["fee_rate"])
        exit_fee = quantity * exit_price * fee_rate
        funding = 0.0
        funding_error = None
        if row["market_type"] == "FUTURES":
            funding, funding_error = await self._funding_cost(
                row["symbol"], int(row["entry_time"]), exit_time,
                float(row["exposure_usdt"]),
            )
        net = gross - float(row["entry_fee"] or 0) - exit_fee - funding
        return_pct = 100 * net / float(row["margin_usdt"])
        self.db.execute(
            """UPDATE execution_account_positions SET status='CLOSED',exit_time=?,
            exit_price=?,exit_reason=?,gross_pnl=?,exit_fee=?,funding=?,net_pnl=?,
            return_pct=?,funding_error=?,updated_at=?
            WHERE account_position_id=? AND status='OPEN'""",
            (
                exit_time, exit_price, reason, gross, exit_fee, funding, net,
                return_pct, funding_error, utc_now(), row["account_position_id"],
            ),
        )

    def _open_leverage_paper(self, position: dict[str, Any]) -> None:
        mirror = self.db.query(
            "SELECT * FROM leverage_positions WHERE position_id=? AND status='PENDING'",
            (position["position_id"],),
        )
        if not mirror:
            return
        row = mirror[0]
        entry_price = float(position["entry_price"])
        exposure = float(row["exposure_usdt"])
        quantity = exposure / entry_price
        leverage = int(row["leverage"])
        liquidation_price = entry_price * (
            1 - (1 / leverage) + self.settings.leverage_paper_maintenance_rate
        )
        entry_fee = exposure * self.settings.leverage_paper_fee_rate
        self.db.execute(
            """UPDATE leverage_positions SET status='OPEN',entry_price=?,quantity=?,
            tp_price=?,sl_price=?,liquidation_price=?,entry_fee=?,updated_at=?
            WHERE position_id=? AND status='PENDING'""",
            (entry_price, quantity, position["tp_price"], position["sl_price"],
             liquidation_price, entry_fee, utc_now(), position["position_id"]),
        )

    async def _funding_cost(
        self, symbol: str, entry_time: int, exit_time: int, exposure: float
    ) -> tuple[float, str | None]:
        try:
            cached = self._funding_cache.get(symbol)
            if cached is None or time.time() - cached[0] > 600:
                lookback = int(time.time() * 1000) - 40 * 24 * 60 * 60 * 1000
                rates = await self.client.funding_rates(
                    symbol, min(lookback, entry_time), int(time.time() * 1000)
                )
                self._funding_cache[symbol] = (time.time(), rates)
            else:
                rates = cached[1]
            total_rate = sum(
                float(item["fundingRate"])
                for item in rates
                if entry_time <= int(item["fundingTime"]) <= exit_time
            )
            return exposure * total_rate, None
        except Exception as exc:
            return 0.0, f"{type(exc).__name__}: {exc}"

    async def _close_leverage_paper(
        self, position: dict[str, Any], exit_time: int, exit_price: float, reason: str
    ) -> None:
        mirror = self.db.query(
            "SELECT * FROM leverage_positions WHERE position_id=? AND status='OPEN'",
            (position["position_id"],),
        )
        if not mirror:
            return
        row = mirror[0]
        quantity = float(row["quantity"])
        exposure = float(row["exposure_usdt"])
        gross = quantity * (exit_price - float(row["entry_price"]))
        exit_fee = quantity * exit_price * self.settings.leverage_paper_fee_rate
        funding, funding_error = await self._funding_cost(
            row["symbol"], int(row["entry_time"]), exit_time, exposure
        )
        net = gross - float(row["entry_fee"]) - exit_fee - funding
        return_pct = 100 * net / float(row["margin_usdt"])
        self.db.execute(
            """UPDATE leverage_positions SET status='CLOSED',exit_time=?,exit_price=?,
            exit_reason=?,gross_pnl=?,exit_fee=?,funding=?,net_pnl=?,return_pct=?,
            funding_error=?,updated_at=? WHERE position_id=? AND status='OPEN'""",
            (exit_time, exit_price, reason, gross, exit_fee, funding, net, return_pct,
             funding_error, utc_now(), position["position_id"]),
        )

    async def _open_spot_demo(self, position: dict[str, Any]) -> None:
        if position["scenario_id"] not in self.spot_scenario_ids:
            return
        now = utc_now()
        spot_symbol = self.spot_client.spot_symbol(position["symbol"])
        if spot_symbol and self.spot_demo_connected:
            venue, routed_symbol, status, error = "SPOT", spot_symbol, "OPENING", None
        elif self.futures_demo_connected and self.futures_client.supports(position["symbol"]):
            venue, routed_symbol, status, error = "FUTURES_1X", position["symbol"], "OPENING", None
        else:
            venue, routed_symbol, status, error = "UNSUPPORTED", None, "SKIPPED", "symbol_not_available_on_spot_or_futures_demo"
        try:
            self.db.execute(
                """INSERT INTO spot_demo_orders(position_id,scenario_id,paper_symbol,spot_symbol,venue,status,quote_amount,error,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (position["position_id"], position["scenario_id"], position["symbol"], routed_symbol, venue, status,
                 self.settings.spot_demo_quote_usdt, error, now, now),
            )
        except Exception:
            return
        if status != "OPENING":
            return
        fallback_note = None
        try:
            if venue == "SPOT":
                try:
                    order = await self.spot_client.market_buy(
                        routed_symbol, self.settings.spot_demo_quote_usdt, f"ptb{position['position_id']}"
                    )
                    quantity = self.spot_client.net_base_quantity(order)
                    buy_quote = order.get("cummulativeQuoteQty")
                except Exception as spot_exc:
                    spot_error = f"{type(spot_exc).__name__}: {spot_exc}"
                    can_fallback = (
                        "-2010" in spot_error
                        and "insufficient balance" in spot_error.lower()
                        and self.futures_demo_connected
                        and self.futures_client.supports(position["symbol"])
                    )
                    if not can_fallback:
                        raise
                    venue, routed_symbol = "FUTURES_1X", position["symbol"]
                    fallback_note = "spot_insufficient_balance_fallback"
                    order = await self.futures_client.market_buy_notional(
                        routed_symbol, self.settings.futures_demo_quote_usdt, f"ptfb{position['position_id']}"
                    )
                    quantity = Decimal(order.get("executedQty") or order["requestedQuantity"])
                    buy_quote = str(quantity * Decimal(order.get("avgPrice") or "0"))
            else:
                order = await self.futures_client.market_buy_notional(
                    routed_symbol, self.settings.futures_demo_quote_usdt, f"ptfb{position['position_id']}"
                )
                quantity = Decimal(order.get("executedQty") or order["requestedQuantity"])
                buy_quote = str(quantity * Decimal(order.get("avgPrice") or "0"))
            self.db.execute(
                """UPDATE spot_demo_orders SET status='OPEN',venue=?,spot_symbol=?,base_quantity=?,buy_order_id=?,buy_quote=?,error=?,updated_at=?
                WHERE position_id=?""",
                (venue, routed_symbol, format(quantity, "f"), str(order["orderId"]), buy_quote,
                 fallback_note, utc_now(), position["position_id"]),
            )
            if fallback_note:
                self.db.event(
                    "warning", "futures_demo",
                    f"Spot balance fallback opened {position['symbol']} on Futures 1x",
                    {"position_id": position["position_id"], "scenario_id": position["scenario_id"]},
                )
        except Exception as exc:
            self.db.execute(
                "UPDATE spot_demo_orders SET status='ERROR',error=?,updated_at=? WHERE position_id=?",
                (f"{type(exc).__name__}: {exc}", utc_now(), position["position_id"]),
            )

    async def _close_spot_demo(self, position: dict[str, Any]) -> None:
        mirror = self.db.query(
            "SELECT * FROM spot_demo_orders WHERE position_id=? AND status='OPEN'", (position["position_id"],)
        )
        if not mirror:
            return
        row = mirror[0]
        try:
            if row["venue"] == "FUTURES_1X":
                order = await self.futures_client.market_close(
                    row["spot_symbol"], Decimal(row["base_quantity"]), f"ptfs{position['position_id']}"
                )
                sell_quote = str(Decimal(order.get("executedQty") or row["base_quantity"]) * Decimal(order.get("avgPrice") or "0"))
            else:
                order = await self.spot_client.market_sell(
                    row["spot_symbol"], Decimal(row["base_quantity"]), f"pts{position['position_id']}"
                )
                sell_quote = order.get("cummulativeQuoteQty")
            self.db.execute(
                """UPDATE spot_demo_orders SET status='CLOSED',sell_order_id=?,sell_quote=?,updated_at=?
                WHERE position_id=?""",
                (str(order["orderId"]), sell_quote, utc_now(), position["position_id"]),
            )
        except Exception as exc:
            self.db.execute(
                "UPDATE spot_demo_orders SET status='CLOSE_ERROR',error=?,updated_at=? WHERE position_id=?",
                (f"{type(exc).__name__}: {exc}", utc_now(), position["position_id"]),
            )

    def status(self) -> dict[str, Any]:
        enabled = sum(spec.enabled for spec in self.scenarios)
        open_positions = int(
            self.db.scalar("SELECT COUNT(*) FROM positions WHERE status IN ('PENDING','OPEN')") or 0
        )
        return {
            "state": self.state,
            "uptime_seconds": 0 if self.started_at is None else int(time.time() - self.started_at),
            "live_enabled": self.settings.live_enabled,
            "feature_contract": "portable-exact-v1",
            "symbols": len(self.registry.symbols),
            "models": len(self.registry.models),
            "model_load_seconds": round(self.registry.load_seconds, 3),
            "scenarios_total": len(self.scenarios),
            "scenarios_enabled": enabled,
            "scenarios_blocked": len(self.scenarios) - enabled,
            "open_positions": open_positions,
            "coverage": self.coverage,
            "last_close": self.last_close,
            "last_cycle_at": self.last_cycle_at,
            "last_feature_ms": self.last_feature_ms,
            "last_inference_ms": self.last_inference_ms,
            "feature_missing": self.feature_missing,
            "approximate_feature_count": 0,
            "rest": asdict(self.client.stats),
            "last_error": self.last_error,
            "incremental_shadow_5m": self.incremental_shadow.status(),
            "spot_demo": {
                "configured": self.spot_client.configured,
                "connected": self.spot_demo_connected,
                "selected_scenarios": len(self.spot_scenario_ids),
                "error": self.spot_demo_error,
                "futures_connected": self.futures_demo_connected,
                "futures_error": self.futures_demo_error,
            },
        }



