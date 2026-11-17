#!/usr/bin/env python3
"""Print a compact summary of the paper-ml-bsh Singular-Backtest."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path,
                    default=Path(__file__).resolve().parent / "results" /
                    "singular_results.json")
    args = ap.parse_args()
    data = json.loads(args.json.read_text(encoding="utf-8"))
    t = data["totals"]
    n, s = t["normal"], t["singular"]
    print(f"scope      : {data['scope']} ({data['scope_label']})")
    print(f"coins      : {data['data']['symbols']}")
    print(f"scenarios  : {data['data']['scenarios']} "
          f"({data['data']['scenario_names']} isim, "
          f"{data['data']['strategies']} strateji ailesi)")
    print(f"period     : {data['data']['start']} -> {data['data']['end']}")
    print(f"normal     : {n['trades']:,} trades | win {n['win_rate']:.2%} | "
          f"retAll {n['retAll']:+.2f}% | avg {n['avg_ret']:+.4f}%")
    print(f"singular   : {s['trades']:,} trades | win {s['win_rate']:.2%} | "
          f"retAll {s['retAll']:+.2f}% | avg {s['avg_ret']:+.4f}%")
    blocked = t["blocked"]
    print(f"blocked    : {blocked:,} ({blocked / n['trades']:.2%})")

    rows = [r for r in data["scenarios"] if r["n_trades"] >= 100]
    rows.sort(key=lambda r: r["delta_retAll"], reverse=True)
    print(f"\nEn iyi 10 senaryo (retAll farkı, en az 100 işlem)")
    print(f"{'sid':>4} {'tf':>3} {'normal':>9} {'singular':>9} {'delta':>8} "
          f"{'blocked':>7}")
    for r in rows[:10]:
        print(f"{r['sid']:>4} {r['tf']:>3} {r['n_retAll']:>8.2f}% "
              f"{r['s_retAll']:>8.2f}% {r['delta_retAll']:>+8.2f} "
              f"{r['blocked_pct']:>6.1f}%")

    improved = sum(1 for r in rows if r["delta_retAll"] > 0)
    print(f"\niyileşen: {improved}/{len(rows)} (retAll, >=100 işlem)")


if __name__ == "__main__":
    main()
