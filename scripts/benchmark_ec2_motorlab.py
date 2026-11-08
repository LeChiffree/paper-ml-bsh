from __future__ import annotations

import argparse
import json
import os
import sqlite3
import statistics
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path


def proc_sample(pid: int) -> tuple[float, int]:
    stat = Path(f"/proc/{pid}/stat").read_text().split()
    cpu_ticks = float(stat[13]) + float(stat[14])
    rss_kb = 0
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            rss_kb = int(line.split()[1])
            break
    return cpu_ticks, rss_kb


def call_cycle(base_url: str, timeframe: str, pid: int, db_path: Path) -> dict:
    before = time.perf_counter()
    start_ticks, start_rss = proc_sample(pid)
    peak_rss = start_rss
    stop = threading.Event()

    def monitor() -> None:
        nonlocal peak_rss
        while not stop.wait(0.2):
            try:
                _, rss = proc_sample(pid)
                peak_rss = max(peak_rss, rss)
            except FileNotFoundError:
                return

    watcher = threading.Thread(target=monitor, daemon=True)
    watcher.start()
    request = urllib.request.Request(
        f"{base_url}/api/control/cycle/{timeframe}", method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            body = json.loads(response.read())
    finally:
        stop.set()
        watcher.join()
    elapsed = time.perf_counter() - before
    end_ticks, end_rss = proc_sample(pid)
    clock_ticks = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
    cpu_pct = 100 * ((end_ticks - start_ticks) / clock_ticks) / elapsed

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """SELECT payload,created_at FROM events
               WHERE category='cycle' AND message=?
               ORDER BY event_id DESC LIMIT 1""",
            (f"{timeframe} cycle completed",),
        ).fetchone()
    payload = json.loads(row[0]) if row and row[0] else {}
    return {
        "timeframe": timeframe,
        "http_ok": bool(body.get("ok")),
        "wall_ms": round(elapsed * 1000, 1),
        "refresh_ms": payload.get("refresh_ms"),
        "feature_ms": payload.get("feature_ms"),
        "inference_ms": payload.get("inference_ms"),
        "symbols": payload.get("symbols"),
        "missing_features": payload.get("missing_features"),
        "entry_eligible": payload.get("entry_eligible"),
        "time_window_eligible": payload.get("time_window_eligible"),
        "cpu_pct_one_core": round(cpu_pct, 1),
        "rss_start_mb": round(start_rss / 1024, 1),
        "rss_peak_mb": round(peak_rss / 1024, 1),
        "rss_end_mb": round(end_rss / 1024, 1),
        "event_at": row[1] if row else None,
    }


def summarize(rows: list[dict]) -> dict:
    summary = {}
    for timeframe in sorted({row["timeframe"] for row in rows}):
        subset = [row for row in rows if row["timeframe"] == timeframe]
        summary[timeframe] = {
            "runs": len(subset),
            "wall_ms_median": round(statistics.median(row["wall_ms"] for row in subset), 1),
            "wall_ms_max": max(row["wall_ms"] for row in subset),
            "refresh_ms_median": round(statistics.median(row["refresh_ms"] for row in subset), 1),
            "feature_ms_median": round(statistics.median(row["feature_ms"] for row in subset), 1),
            "inference_ms_median": round(statistics.median(row["inference_ms"] for row in subset), 1),
            "cpu_pct_max": max(row["cpu_pct_one_core"] for row in subset),
            "rss_peak_mb": max(row["rss_peak_mb"] for row in subset),
            "symbols_min": min(row["symbols"] for row in subset),
            "missing_features_max": max(row["missing_features"] for row in subset),
        }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--parallel", action="store_true")
    parser.add_argument("--timeframes", default="1h,4h")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    if now.minute < 3:
        raise SystemExit("Refusing benchmark within the live entry window after an hourly close")

    rows = []
    timeframes = [item.strip() for item in args.timeframes.split(",") if item.strip()]
    if args.parallel:
        with ThreadPoolExecutor(max_workers=len(timeframes)) as pool:
            futures = [
                pool.submit(call_cycle, args.base_url, timeframe, args.pid, args.db)
                for timeframe in timeframes
            ]
            rows.extend(future.result() for future in futures)
    else:
        for timeframe in timeframes:
            for _ in range(args.runs):
                rows.append(call_cycle(args.base_url, timeframe, args.pid, args.db))

    print(json.dumps({"runs": rows, "summary": summarize(rows)}, indent=2))


if __name__ == "__main__":
    main()
