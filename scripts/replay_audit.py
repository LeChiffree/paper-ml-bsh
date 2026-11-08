"""Point-in-time replay audit for active paper-trading scenarios."""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "paper_trade_app"
GENERATOR = ROOT / "feature_generator_portable"
sys.path[:0] = [str(APP), str(GENERATOR)]

from binance_client import BinanceClient
from candle_history import CandleHistoryStore
from config import Settings
from exact_features import _to_portable_frame
from feature_engine.features import FEATURE_ORDER, build_macro, compute_features, safe_div
from model_registry import ModelRegistry, load_scenarios


def accepted(spec, cls, range_value, vol_value) -> bool:
    if cls["prediction"] != "TP":
        return False
    score = float(cls["tp_score"])
    if spec.threshold is not None and score < spec.threshold:
        return False
    vol_pass = vol_value is not None and spec.vol_low <= vol_value <= spec.vol_high
    return {
        "signal_only": True,
        "threshold": True,
        "range_full": range_value is not None and range_value >= spec.tp_pct,
        "range_half_volatility": range_value is not None and range_value >= spec.tp_pct / 2 and vol_pass,
        "range_full_volatility": range_value is not None and range_value >= spec.tp_pct and vol_pass,
        "volatility": vol_pass,
    }.get(spec.filter_name, False)


async def repair_in_memory(settings, registry, store, timeframes, end_ms):
    client = BinanceClient(settings.binance_rest_url, settings.binance_ws_url, settings.request_concurrency)
    frames = {}
    download_gate = asyncio.Semaphore(2)
    try:
        for tf in timeframes:
            async def load(symbol):
                cached = store.load(tf, symbol)
                step = pd.Timedelta(minutes={"15m": 15, "1h": 60, "4h": 240}[tf])
                expected = pd.date_range(cached.index[0], pd.Timestamp(end_ms, unit="ms", tz="UTC"), freq=step)
                missing = expected.difference(cached.index)
                if len(missing):
                    start_ms = int(missing[0].timestamp() * 1000)
                else:
                    start_ms = int(cached.index[-1].timestamp() * 1000) + 1
                last_error = None
                for attempt in range(5):
                    try:
                        async with download_gate:
                            fresh = await client.historical_klines(
                                symbol, tf, start_ms, end_time=end_ms
                            )
                            # Avoid a 106-symbol burst against Binance's IP
                            # weight limiter during outage recovery.
                            await asyncio.sleep(0.25)
                        break
                    except Exception as exc:
                        last_error = exc
                        await asyncio.sleep(min(30, 2 ** (attempt + 1)))
                else:
                    raise RuntimeError(
                        f"{symbol} {tf} recovery download failed"
                    ) from last_error
                combined = pd.concat([cached, fresh])
                return symbol, combined[~combined.index.duplicated(keep="last")].sort_index()
            results = await asyncio.gather(*(load(symbol) for symbol in registry.symbols))
            frames[tf] = dict(results)
    finally:
        await client.close()
    return frames


def feature_panel(raw_frames, start_ms, end_ms, timeframe, warmup_bars=1500):
    # Computing the full multi-year panel for 106 symbols can require tens of
    # gigabytes. Keep a generous pre-window so EMA/rolling features are stable,
    # then retain only the requested audit interval in the result.
    minutes = {"15m": 15, "1h": 60, "4h": 240}[timeframe]
    warmup_start = start_ms - warmup_bars * minutes * 60_000
    portable = {
        symbol: _to_portable_frame(
            frame.loc[pd.Timestamp(warmup_start, unit="ms", tz="UTC") :]
        )
        for symbol, frame in raw_frames.items()
    }
    macro = build_macro(portable)
    quote_table = pd.concat(
        [frame.set_index("open_time")["quote_volume"].rename(symbol) for symbol, frame in portable.items()],
        axis=1,
    )
    market_total = quote_table.sum(axis=1, min_count=1)
    selected = {}
    for symbol, frame in portable.items():
        share = safe_div(frame.set_index("open_time")["quote_volume"], market_total).reindex(frame["open_time"]).reset_index(drop=True)
        enriched = compute_features(frame, macro, share)
        selected[symbol] = enriched.loc[
            enriched["open_time"].between(start_ms, end_ms),
            ["open_time", *FEATURE_ORDER],
        ].copy()
    return selected


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    settings = Settings()
    registry = ModelRegistry(settings.package_dir)
    scenario_ids = settings.active_scenario_ids()
    specs = [s for s in load_scenarios(settings.package_dir, registry) if s.scenario_id in scenario_ids]
    timeframes = sorted({s.timeframe for s in specs})
    start_ms = int(pd.Timestamp(args.start).timestamp() * 1000)
    end_ms = int(pd.Timestamp(args.end).timestamp() * 1000)
    store = CandleHistoryStore(settings.history_db_path)
    try:
        frames = asyncio.run(repair_in_memory(settings, registry, store, timeframes, end_ms))
    finally:
        store.close()

    expected = set()
    expected_details = {}
    funnel = {}
    diagnostics = {}
    for tf in timeframes:
        begun = time.perf_counter()
        panel = feature_panel(frames[tf], start_ms, end_ms, tf)
        available_times = [
            set(frame["open_time"].astype("int64"))
            for frame in panel.values() if not frame.empty
        ]
        times = sorted(set.intersection(*available_times)) if available_times else []
        tf_specs = [s for s in specs if s.timeframe == tf]
        needed = set()
        for spec in tf_specs:
            needed.add(spec.target)
            if spec.filter_name in {"range_full", "range_half_volatility", "range_full_volatility"}:
                needed.add(f"future_range_{spec.horizon}mum_pct")
            if spec.filter_name in {"volatility", "range_half_volatility", "range_full_volatility"}:
                needed.add(f"realized_volatility_{spec.horizon}mum_pct")
        by_symbol = {symbol: frame.set_index("open_time") for symbol, frame in panel.items()}
        for signal_time in times:
            rows = []
            symbols = []
            for symbol, frame in by_symbol.items():
                if signal_time in frame.index:
                    row = frame.loc[signal_time, FEATURE_ORDER]
                    if isinstance(row, pd.DataFrame):
                        row = row.iloc[-1]
                    rows.append(row)
                    symbols.append(symbol)
            matrix = pd.DataFrame(rows, index=symbols, columns=FEATURE_ORDER)
            predictions = {target: registry.predict(tf, target, matrix) for target in needed}
            for spec in tf_specs:
                stats = funnel.setdefault(str(spec.scenario_id), {"timeframe": tf, "scans": 0, "expected": 0})
                stats["scans"] += 1
                classifier = predictions[spec.target]
                range_target = f"future_range_{spec.horizon}mum_pct"
                vol_target = f"realized_volatility_{spec.horizon}mum_pct"
                for symbol, cls in classifier.items():
                    rv = predictions.get(range_target, {}).get(symbol)
                    vv = predictions.get(vol_target, {}).get(symbol)
                    if accepted(spec, cls, rv, vv):
                        key = (spec.scenario_id, symbol, int(signal_time))
                        expected.add(key)
                        expected_details[key] = {
                            "tp_score": float(cls["tp_score"]),
                            "range_prediction": None if rv is None else float(rv),
                            "volatility_prediction": None if vv is None else float(vv),
                        }
                        stats["expected"] += 1
        diagnostics[tf] = {"times": len(times), "seconds": round(time.perf_counter() - begun, 2)}

    with sqlite3.connect(settings.db_path) as conn:
        placeholders = ",".join("?" for _ in scenario_ids)
        actual = set(conn.execute(
            f"SELECT scenario_id,symbol,signal_time FROM positions "
            f"WHERE signal_time BETWEEN ? AND ? AND scenario_id IN ({placeholders})",
            (start_ms, end_ms, *sorted(scenario_ids)),
        ).fetchall())
        status_rows = conn.execute(
            f"SELECT scenario_id,symbol,signal_time,status,exit_reason FROM positions "
            f"WHERE signal_time BETWEEN ? AND ? AND scenario_id IN ({placeholders})",
            (start_ms, end_ms, *sorted(scenario_ids)),
        ).fetchall()
    status = {(r[0], r[1], r[2]): {"status": r[3], "exit_reason": r[4]} for r in status_rows}
    missed = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    matched = expected & actual
    by_scenario = {}
    for spec in specs:
        sid = spec.scenario_id
        exp = {k for k in expected if k[0] == sid}
        act = {k for k in actual if k[0] == sid}
        mat = exp & act
        by_scenario[str(sid)] = {
            "timeframe": spec.timeframe,
            "expected": len(exp), "actual": len(act), "matched": len(mat),
            "missed": len(exp - act), "unexpected": len(act - exp),
            "open": sum(status[k]["status"] == "OPEN" for k in mat),
            "closed": sum(status[k]["status"] == "CLOSED" for k in mat),
        }
    output = {
        "period": {"start": args.start, "end": args.end},
        "totals": {"expected": len(expected), "actual": len(actual), "matched": len(matched), "missed": len(missed), "unexpected": len(unexpected)},
        "by_scenario": by_scenario,
        "diagnostics": diagnostics,
        "missed": [{
            "scenario_id": a, "symbol": b,
            "signal_time": pd.Timestamp(c, unit="ms", tz="UTC").isoformat(),
            **expected_details[(a, b, c)],
        } for a,b,c in missed],
        "unexpected": [{"scenario_id": a, "symbol": b, "signal_time": pd.Timestamp(c, unit="ms", tz="UTC").isoformat()} for a,b,c in unexpected],
    }
    Path(args.output).write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output["totals"], ensure_ascii=False))
    print(json.dumps(diagnostics, ensure_ascii=False))


if __name__ == "__main__":
    main()
