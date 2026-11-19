#!/usr/bin/env python3
"""Compare the expanded candidate engine with the packaged workbook subset."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "paper_trade_app"))

from data import CACHE, MAX_OPEN, PACKAGE, WORKBOOK
from fast_admit import admit_portfolio


def trt_ms(values: pd.Series) -> pd.Series:
    return (
        pd.to_datetime(values).dt.tz_localize("Europe/Istanbul")
        .dt.tz_convert("UTC").astype("datetime64[ms, UTC]").astype("int64")
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--partitions", type=Path, default=CACHE / "by_scenario")
    ap.add_argument("--out", type=Path, default=HERE / "results" / "validation.json")
    args = ap.parse_args()

    book = pd.read_excel(WORKBOOK, sheet_name="Detayli_Islemler")
    book["signal_ms"] = trt_ms(book["signal_time"])
    symbols = sorted(book["symbol"].unique().tolist())
    start = int(book["signal_ms"].min())
    end = int(book["signal_ms"].max())
    print(f"workbook {len(book):,} trades symbols={len(symbols)} "
          f"{pd.to_datetime(start, unit='ms', utc=True)} -> "
          f"{pd.to_datetime(end, unit='ms', utc=True)}", flush=True)

    rows = []
    total_raw_hit = 0
    total_book = 0
    total_admitted = 0
    total_matched = 0
    for sid, book_group in book.groupby("scenario_id", sort=True):
        folder = args.partitions / f"scenario_id={sid}"
        files = sorted(folder.glob("*.parquet"))
        if not files:
            cand = pd.DataFrame()
        else:
            cand = pd.read_parquet(files)
            cand = cand[
                cand["symbol"].isin(symbols)
                & cand["signal_time"].between(start, end)
            ].copy()
        admitted = admit_portfolio(cand, MAX_OPEN) if len(cand) else cand
        book_keys = set(zip(book_group["symbol"], book_group["signal_ms"]))
        cand_keys = set(zip(cand["symbol"], cand["signal_time"])) if len(cand) else set()
        admitted_keys = set(zip(admitted["symbol"], admitted["signal_time"])) if len(admitted) else set()
        matched = len(book_keys & admitted_keys)
        rows.append({
            "sid": int(sid),
            "book": len(book_group),
            "raw_candidates": int(len(cand)),
            "raw_hit": int(len(book_keys & cand_keys)),
            "admitted": int(len(admitted)),
            "matched": int(matched),
        })
        total_book += len(book_group)
        total_raw_hit += len(book_keys & cand_keys)
        total_admitted += len(admitted)
        total_matched += matched
        if sid % 20 == 0:
            print(f"  sid={sid} book={total_book:,} raw_hit={total_raw_hit:,} "
                  f"matched={total_matched:,}", flush=True)

    frame = pd.DataFrame(rows)
    summary = {
        "book_trades": int(total_book),
        "book_raw_coverage": float(total_raw_hit / total_book) if total_book else 0.0,
        "expanded_admitted": int(total_admitted),
        "matched_admitted": int(total_matched),
        "matched_pct_of_book": float(total_matched / total_book) if total_book else 0.0,
        "per_scenario": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "per_scenario"},
                     ensure_ascii=False, indent=2), flush=True)
    print(frame.sort_values("matched").head(15).to_string(index=False), flush=True)


if __name__ == "__main__":
    main()
