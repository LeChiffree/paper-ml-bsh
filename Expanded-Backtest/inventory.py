#!/usr/bin/env python3
"""Build a fast inventory of the 1m archive and native higher timeframes."""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path(r"D:\1mdatabinance")
OUT = Path(__file__).resolve().parent / "inventory.json"
MONTH_RE = re.compile(r"-1m-(\d{4})-(\d{2})")


def parquet_rows(path: Path) -> int:
    try:
        return int(pq.ParquetFile(path).metadata.num_rows)
    except Exception:
        return -1


def scan_symbol(folder: Path) -> dict:
    files = sorted(folder.glob("*.parquet"))
    rows = 0
    months = set()
    bad = 0
    for path in files:
        count = parquet_rows(path)
        if count < 0:
            bad += 1
            continue
        rows += count
        match = MONTH_RE.search(path.name)
        if match:
            months.add(f"{match.group(1)}-{match.group(2)}")
    return {
        "symbol": folder.name,
        "files": len(files),
        "rows": rows,
        "bad_files": bad,
        "months": sorted(months),
    }


def main() -> None:
    base = ROOT / "1m"
    symbols = sorted(p for p in base.iterdir() if p.is_dir())
    with ThreadPoolExecutor(max_workers=16) as pool:
        result = list(pool.map(scan_symbol, symbols))
    result.sort(key=lambda item: item["symbol"])
    total = sum(item["rows"] for item in result)
    OUT.write_text(
        json.dumps(
            {
                "root": str(ROOT),
                "symbols": len(result),
                "total_1m_rows": total,
                "per_symbol": result,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"symbols={len(result)} rows={total:,} -> {OUT}", flush=True)
    for item in result[:5]:
        print(item["symbol"], item["rows"], item["months"][:2], item["months"][-2:])


if __name__ == "__main__":
    main()
