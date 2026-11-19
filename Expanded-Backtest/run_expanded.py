#!/usr/bin/env python3
"""Orchestrate the full-history, all-coin expanded paper-ML backtest."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
import sys
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from pathlib import Path

import duckdb
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "paper_trade_app"))

from data import (
    CANDIDATES,
    CACHE,
    INITIAL_TL,
    PACKAGE,
    RESULTS,
    STAKE_TL,
    TIMEFRAMES,
    ensure_dirs,
    list_symbols,
)
from model_registry import ModelRegistry, load_scenarios
from pipeline import init_worker, process_symbol
from fast_admit import admit_portfolio, apply_singular
from report import aggregate, write_payload
from trade_sim import add_pnl
from data import MAX_OPEN


def run_workers(workers: int, symbols: list[str], force: bool) -> None:
    ensure_dirs()
    if force:
        for symbol in symbols:
            for path in CANDIDATES.glob(f"{symbol}__*.parquet"):
                path.unlink()
            marker = CANDIDATES / f"{symbol}.done"
            if marker.exists():
                marker.unlink()
    started = time.time()
    results = []
    with ProcessPoolExecutor(max_workers=workers, initializer=init_worker) as pool:
        futures = {pool.submit(process_symbol, symbol): symbol for symbol in symbols}
        for index, future in enumerate(as_completed(futures), 1):
            result = future.result()
            results.append(result)
            elapsed = time.time() - started
            status = result.get("status")
            if status == "error":
                print(f"[{index}/{len(symbols)}] ERROR {result}", flush=True)
            else:
                print(f"[{index}/{len(symbols)}] {result['symbol']} {status} "
                      f"{result.get('seconds', 0)}s elapsed={elapsed / 60:.1f}m",
                      flush=True)
    summary = {
        "symbols": len(symbols),
        "ok": sum(r.get("status") == "ok" for r in results),
        "cached": sum(r.get("status") == "cached" for r in results),
        "short": sum(r.get("status") == "short" for r in results),
        "errors": [r for r in results if r.get("status") == "error"],
        "seconds": round(time.time() - started, 1),
    }
    (RESULTS / "run_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


def build_scenario_partitions(force: bool) -> Path:
    target = CACHE / "by_scenario"
    if target.exists() and not force:
        return target
    if target.exists():
        shutil.rmtree(target)
    pattern = str(CANDIDATES / "*__*.parquet").replace("\\", "/")
    target_sql = str(target).replace("\\", "/")
    print("building scenario partitions", flush=True)
    started = time.time()
    duckdb.sql(f"""
        COPY (
            SELECT * FROM read_parquet('{pattern}')
        ) TO '{target_sql}'
        (FORMAT PARQUET, PARTITION_BY (scenario_id),
         OVERWRITE_OR_IGNORE TRUE, FILENAME_PATTERN 'part_{{i}}')
    """)
    print(f"partitions ready sec={time.time() - started:.1f}", flush=True)
    return target


def load_specs() -> list:
    registry = ModelRegistry(PACKAGE)
    return load_scenarios(PACKAGE, registry)


def merge(force: bool = False) -> None:
    ensure_dirs()
    partitions = build_scenario_partitions(force)
    specs = load_specs()
    by_sid = {int(s.scenario_id): s for s in specs}
    normal_parts: list[pd.DataFrame] = []
    started = time.time()
    total_raw = 0
    total_accepted = 0
    for index, sid in enumerate(sorted(by_sid), 1):
        folder = partitions / f"scenario_id={sid}"
        files = sorted(folder.glob("*.parquet"))
        if not files:
            continue
        frame = pd.read_parquet(files)
        total_raw += len(frame)
        frame = add_pnl(frame) if "net_pnl_tl" not in frame.columns else frame
        admitted = admit_portfolio(frame, MAX_OPEN) if len(frame) else frame
        total_accepted += len(admitted)
        if not admitted.empty:
            normal_parts.append(admitted)
        if index % 20 == 0:
            print(f"  scenario {index}/{len(by_sid)} raw={total_raw:,} "
                  f"accepted={total_accepted:,} sec={time.time() - started:.1f}",
                  flush=True)
    normal = pd.concat(normal_parts, ignore_index=True) if normal_parts else pd.DataFrame()
    del normal_parts
    if normal.empty:
        raise RuntimeError("No accepted trades")
    spec_cols = {
        int(s.scenario_id): (str(s.scenario), str(s.strategy), str(s.filter_name),
                              None if s.threshold is None else float(s.threshold))
        for s in specs
    }
    normal["scenario"] = normal["scenario_id"].map(
        {sid: value[0] for sid, value in spec_cols.items()})
    normal["strategy"] = normal["scenario_id"].map(
        {sid: value[1] for sid, value in spec_cols.items()})
    normal["filter"] = normal["scenario_id"].map(
        {sid: value[2] for sid, value in spec_cols.items()})
    normal["threshold"] = normal["scenario_id"].map(
        {sid: value[3] for sid, value in spec_cols.items()})
    for column in ("signal_time", "entry_time", "exit_time"):
        normal[column] = (
            pd.to_datetime(normal[column], unit="ms", utc=True)
            .dt.tz_convert("Europe/Istanbul").dt.tz_localize(None)
        )
    normal = normal.sort_values(["scenario_id", "signal_time", "score"],
                                ascending=[True, True, False], kind="mergesort")
    print("applying singular rule", flush=True)
    singular = apply_singular(normal)
    normal.to_parquet(RESULTS / "normal_trades.parquet", index=False,
                      compression="zstd")
    singular.to_parquet(RESULTS / "singular_trades.parquet", index=False,
                        compression="zstd")
    meta = {
        "symbols": int(normal["symbol"].nunique()),
        "scenarios": int(normal["scenario_id"].nunique()),
        "scenario_names": int(normal["scenario"].nunique()),
        "strategies": int(normal["strategy"].nunique()),
        "start": str(normal["signal_time"].min()),
        "end": str(normal["exit_time"].max()),
        "timeframes": sorted(normal["timeframe"].unique().tolist()),
        "source": "D:/1mdatabinance/1m (526 coin 1m arşivi; 58 coin CSV+parquet)",
        "stake_tl": STAKE_TL,
        "initial_tl": INITIAL_TL,
        "timezone": "Türkiye saati (UTC+3, kaynak dosyadaki gibi)",
        "warmup_bars": 200,
        "max_concurrent_positions": 10,
        "admission": "Kronolojik sinyal; senaryo başına en fazla 10 eşzamanlı "
                     "10.000 TL pozisyon; aynı coin üst üste binebilir.",
        "funding": "4h: 8 saatte %0,0108 sabit funding; 5m/15m/1h: funding yok.",
        "unseen_symbol_handling": "Model symbol_map dışındaki coinler NaN "
                                  "kategori ile skorlanır.",
        "raw_candidates": int(total_raw),
        "accepted_normal": int(len(normal)),
        "accepted_singular": int(len(singular)),
    }
    payload = aggregate(normal, singular, specs, meta)
    write_payload(payload, RESULTS)
    (RESULTS / "run_manifest.json").write_text(
        json.dumps({
            "generated_at": payload["generated_at"],
            "scope": "scenario",
            "source_rows": int(len(normal)),
            "accepted_rows": int(len(singular)),
            "blocked_rows": int(len(normal) - len(singular)),
            "meta": meta,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    t = payload["totals"]
    print(f"normal={t['normal']['trades']:,} singular={t['singular']['trades']:,} "
          f"blocked={t['blocked']:,} ({t['blocked'] / max(t['normal']['trades'], 1) * 100:.2f}%)",
          flush=True)
    print(f"normal retAll={t['normal']['retAll']:+.2f}% "
          f"singular retAll={t['singular']['retAll']:+.2f}%", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["run", "merge", "all"])
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--symbols", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    symbols = list_symbols()
    if args.symbols:
        wanted = {s.strip() for s in args.symbols.split(",") if s.strip()}
        symbols = [s for s in symbols if s in wanted]
    if args.limit:
        symbols = symbols[:args.limit]
    if args.command in {"run", "all"}:
        run_workers(args.workers, symbols, args.force)
    if args.command in {"merge", "all"}:
        merge(force=args.force)


if __name__ == "__main__":
    main()
