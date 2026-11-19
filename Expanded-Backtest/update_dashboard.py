#!/usr/bin/env python3
"""Publish expanded Paper ML results into the all-in-dashboard repository."""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DASH = Path(r"C:\Users\GIGABAYT\Projects\all-in-dashboard")
PAYLOAD = HERE / "results" / "singular_results.json"
MANIFEST = HERE / "results" / "run_manifest.json"
BOOK = REPO / "PAPER_TRADE_POSITIVE_MODELS_1M_EXECUTION" / "paper_trade_package" / "BACKTEST_POSITIVE_METHODS_1M_EXECUTION.xlsx"


def load_targets() -> dict[int, str]:
    summary = pd.read_excel(BOOK, sheet_name="Pozitif_Sonuclar",
                            usecols=["scenario_id", "target"])
    return {int(row.scenario_id): str(row.target) for row in summary.itertuples()}


def update_rows(rows: list[dict], payload: dict) -> tuple[list[dict], int]:
    by_key = {(int(s["sid"]), str(s["tf"])): s for s in payload["scenarios"]}
    targets = load_targets()
    changed = 0
    for row in rows:
        if row.get("source") != "Paper ML":
            continue
        match = re.search(r"scenario (\d+)", str(row.get("detail", "")))
        if not match:
            continue
        sid = int(match.group(1))
        scenario = by_key.get((sid, str(row["timeframe"])))
        if scenario is None:
            continue
        threshold = scenario.get("threshold")
        filter_name = str(scenario["filter"])
        row["strategy"] = str(scenario["strategy"])
        row["filter"] = (filter_name if threshold is None
                         else f"{filter_name} (t={float(threshold):g})")
        row["retAll"] = round(float(scenario["s_avg_ret"]), 6)
        row["ret2026"] = round(float(scenario["s_avg_ret2026"]), 6)
        row["trades"] = int(scenario["s_trades"])
        row["winRatio"] = round(float(scenario["s_winRatio"]), 4)
        row["winHoldH"] = round(float(scenario["s_winHoldH"]), 2)
        row["profitDays"] = int(scenario["s_profitDays"])
        row["lossDays"] = int(scenario["s_lossDays"])
        row["detail"] = f"paper-ml-bsh · scenario {sid} · {targets.get(sid, '')}"
        changed += 1
    return rows, changed


def replace_embedded(html: str, rows: list[dict], row_count: int) -> str:
    start = html.find("const ROWS = ")
    end = html.find("\nconst META", start)
    if start < 0 or end < 0:
        raise RuntimeError("ROWS block not found in index.html")
    payload = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    html = html[:start] + "const ROWS = " + payload + ";" + html[end:]
    meta_start = html.find("const META = ")
    meta_end = html.find(";", meta_start)
    sources = []
    for row in rows:
        if row["source"] not in sources:
            sources.append(row["source"])
    meta = {
        "generated": datetime.now().strftime("%d.%m.%Y %H:%M"),
        "row_count": row_count,
        "sources": sources,
    }
    html = html[:meta_start] + "const META = " + json.dumps(meta, ensure_ascii=False) + ";" + html[meta_end + 1:]
    return html


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-summary", action="store_true")
    args = ap.parse_args()

    with open(PAYLOAD, encoding="utf-8") as handle:
        payload = json.load(handle)
    all_path = DASH / "allinone.json"
    with open(all_path, encoding="utf-8") as handle:
        allinone = json.load(handle)

    rows, changed = update_rows(allinone["rows"], payload)
    allinone["generated"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    allinone["row_count"] = len(rows)
    with open(all_path, "w", encoding="utf-8") as handle:
        json.dump(allinone, handle, ensure_ascii=False, indent=2)
    print(f"allinone.json Paper ML rows updated={changed}")

    frame = pd.DataFrame(rows, columns=allinone["columns"])
    frame.to_csv(DASH / "allinone.csv", sep=";", index=False,
                 encoding="utf-8-sig")
    with pd.ExcelWriter(DASH / "Allinone.xlsx", engine="openpyxl") as writer:
        frame.to_excel(writer, sheet_name="Allinone", index=False)
    print("allinone.csv / Allinone.xlsx written")

    html_path = DASH / "index.html"
    html = html_path.read_text(encoding="utf-8")
    html = replace_embedded(html, rows, len(rows))
    html_path.write_text(html, encoding="utf-8")
    print("index.html ROWS/META updated")

    singular_dir = REPO / "Singular-Backtest"
    shutil.copy2(PAYLOAD, singular_dir / "singular_results.json")
    if MANIFEST.exists():
        shutil.copy2(MANIFEST, singular_dir / "run_manifest.json")
    print("paper-ml-bsh Singular-Backtest payload copied")

    if not args.skip_summary:
        subprocess.run([sys.executable, "singular/build_summary.py"],
                       cwd=DASH, check=True)


if __name__ == "__main__":
    main()
