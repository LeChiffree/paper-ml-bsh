"""Prune the ledger to selected scenarios and seed Aug 1 backtest trades."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "paper_trade_app"
sys.path.insert(0, str(APP))

from config import TIMEFRAME_MINUTES, Settings  # noqa: E402
from execution_accounts import EXECUTION_ACCOUNTS  # noqa: E402


SELECTED = {5, 9, 10, 15, 19, 49, 56, 64, 79, 108, 111}
START = pd.Timestamp("2026-08-01T00:00:00Z")
CUTOVER = pd.Timestamp("2026-09-07T00:00:00Z")


def ts(value) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def iso(value) -> str:
    return ts(value).isoformat()


def ms(value) -> int:
    return int(ts(value).timestamp() * 1000)


def recompute_scenarios(conn: sqlite3.Connection) -> None:
    for scenario_id in SELECTED:
        stats = conn.execute(
            """SELECT COUNT(*) trades,
            SUM(CASE WHEN exit_reason='take' THEN 1 ELSE 0 END) wins,
            SUM(CASE WHEN exit_reason='stop' THEN 1 ELSE 0 END) losses,
            SUM(CASE WHEN exit_reason='time' THEN 1 ELSE 0 END) times,
            COALESCE(SUM(net_pnl),0) pnl
            FROM positions WHERE scenario_id=? AND status='CLOSED'""",
            (scenario_id,),
        ).fetchone()
        initial = float(conn.execute(
            "SELECT initial_cash FROM scenarios WHERE scenario_id=?", (scenario_id,)
        ).fetchone()[0])
        conn.execute(
            """UPDATE scenarios SET trade_count=?,win_count=?,loss_count=?,time_count=?,
            realized_pnl=?,cash=?,enabled=1,blocked_reason=NULL WHERE scenario_id=?""",
            (
                int(stats[0] or 0), int(stats[1] or 0), int(stats[2] or 0),
                int(stats[3] or 0), float(stats[4] or 0), initial + float(stats[4] or 0),
                scenario_id,
            ),
        )
    placeholders = ",".join("?" for _ in SELECTED)
    conn.execute(
        f"UPDATE scenarios SET enabled=0,blocked_reason='inactive_by_allowlist' "
        f"WHERE scenario_id NOT IN ({placeholders})",
        sorted(SELECTED),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if not args.apply:
        raise SystemExit("Refusing mutation without --apply")
    os.environ["PT_ACTIVE_SCENARIO_IDS"] = ",".join(map(str, sorted(SELECTED)))
    os.environ["PT_MOTORLAB_DEMO_ENABLED"] = "false"
    os.environ["PT_LEGACY_DEMO_ENABLED"] = "false"
    settings = Settings()
    book = settings.package_dir / "BACKTEST_POSITIVE_METHODS_1M_EXECUTION.xlsx"
    trades = pd.read_excel(book, sheet_name="Detayli_Islemler")
    signal_ts = pd.to_datetime(trades["signal_time"], utc=True)
    trades = trades[
        trades["scenario_id"].isin(SELECTED)
        & (signal_ts >= START)
        & (signal_ts < CUTOVER)
    ].copy()
    trades["signal_time"] = signal_ts.loc[trades.index]

    inserted_positions = 0
    inserted_accounts = 0
    placeholders = ",".join("?" for _ in SELECTED)
    with sqlite3.connect(settings.db_path, timeout=60) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        # Remove every non-selected strategy outcome from the canonical and
        # four-account ledgers, while preserving events for operational audit.
        conn.execute(
            f"DELETE FROM futures_demo_mirrors WHERE position_id IN "
            f"(SELECT position_id FROM positions WHERE scenario_id NOT IN ({placeholders}))",
            sorted(SELECTED),
        )
        for table in ("spot_demo_orders", "leverage_positions", "execution_account_positions"):
            conn.execute(
                f"DELETE FROM {table} WHERE position_id IN "
                f"(SELECT position_id FROM positions WHERE scenario_id NOT IN ({placeholders}))",
                sorted(SELECTED),
            )
        conn.execute(
            f"DELETE FROM signals WHERE scenario_id NOT IN ({placeholders})", sorted(SELECTED)
        )
        conn.execute(
            f"DELETE FROM positions WHERE scenario_id NOT IN ({placeholders})", sorted(SELECTED)
        )
        conn.execute("DELETE FROM equity_snapshots")
        scenarios = {
            int(row["scenario_id"]): row
            for row in conn.execute(
                f"SELECT * FROM scenarios WHERE scenario_id IN ({placeholders})", sorted(SELECTED)
            )
        }

        for row in trades.itertuples(index=False):
            scenario_id = int(row.scenario_id)
            spec = scenarios[scenario_id]
            signal_time = ms(row.signal_time)
            entry_time = ms(row.entry_time)
            exit_time = ms(row.exit_time)
            deadline = entry_time + int(spec["horizon"]) * TIMEFRAME_MINUTES[spec["timeframe"]] * 60_000
            entry_price = float(row.entry_price)
            exit_price = float(row.exit_price)
            notional = settings.trade_notional_tl
            quantity = notional / entry_price
            reason = "time" if str(row.exit_reason).endswith("time_exit") else str(row.exit_reason)
            now_created = iso(row.signal_time)
            now_updated = iso(row.exit_time)
            conn.execute(
                """INSERT OR IGNORE INTO signals(
                scenario_id,symbol,timeframe,signal_time,decision,reason,tp_score,
                range_prediction,volatility_prediction,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    scenario_id, row.symbol, spec["timeframe"], signal_time,
                    "accepted", "backtest_seed_2026_08_01", float(row.tp_score),
                    None if pd.isna(row.range_prediction) else float(row.range_prediction),
                    None if pd.isna(row.volatility_prediction) else float(row.volatility_prediction),
                    now_created,
                ),
            )
            before = conn.total_changes
            cursor = conn.execute(
                """INSERT OR IGNORE INTO positions(
                scenario_id,symbol,timeframe,target,signal_time,entry_time,deadline,status,
                notional,entry_price,quantity,tp_price,sl_price,exit_time,exit_price,exit_reason,
                gross_pnl,fees,funding,net_pnl,tp_score,range_prediction,volatility_prediction,
                created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    scenario_id, row.symbol, spec["timeframe"], spec["target"], signal_time,
                    entry_time, deadline, "CLOSED", notional, entry_price, quantity,
                    entry_price * (1 + float(spec["tp_pct"]) / 100),
                    entry_price * (1 - float(spec["sl_pct"]) / 100),
                    exit_time, exit_price, reason, float(row.gross_pnl_tl),
                    float(row.fees_tl), float(row.funding_tl), float(row.net_pnl_tl),
                    float(row.tp_score),
                    None if pd.isna(row.range_prediction) else float(row.range_prediction),
                    None if pd.isna(row.volatility_prediction) else float(row.volatility_prediction),
                    now_created, now_updated,
                ),
            )
            changed = conn.total_changes - before
            inserted_positions += changed
            if not changed:
                continue
            position_id = int(cursor.lastrowid)
            funding_rate = float(row.funding_tl) / notional
            for account_id, account in EXECUTION_ACCOUNTS.items():
                margin = settings.execution_account_margin_usdt
                exposure = margin * account["leverage"]
                account_quantity = exposure / entry_price
                gross = account_quantity * (exit_price - entry_price)
                entry_fee = exposure * account["fee_rate"]
                exit_fee = account_quantity * exit_price * account["fee_rate"]
                funding = 0.0 if account["market"] == "SPOT" else exposure * funding_rate
                net = gross - entry_fee - exit_fee - funding
                liquidation = None
                if account["market"] == "FUTURES":
                    liquidation = entry_price * (
                        1 - 1 / account["leverage"] + settings.leverage_paper_maintenance_rate
                    )
                conn.execute(
                    """INSERT INTO execution_account_positions(
                    position_id,account_id,market_type,leverage,scenario_id,symbol,timeframe,
                    status,signal_time,entry_time,deadline,margin_usdt,exposure_usdt,entry_price,
                    quantity,tp_price,sl_price,liquidation_price,exit_time,exit_price,exit_reason,
                    gross_pnl,entry_fee,exit_fee,funding,net_pnl,return_pct,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        position_id, account_id, account["market"], account["leverage"],
                        scenario_id, row.symbol, spec["timeframe"], "CLOSED", signal_time,
                        entry_time, deadline, margin, exposure, entry_price, account_quantity,
                        entry_price * (1 + float(spec["tp_pct"]) / 100),
                        entry_price * (1 - float(spec["sl_pct"]) / 100), liquidation,
                        exit_time, exit_price, reason, gross, entry_fee, exit_fee, funding, net,
                        100 * net / margin, now_created, now_updated,
                    ),
                )
                inserted_accounts += 1
        recompute_scenarios(conn)
        conn.commit()
        counts = {
            row[0]: row[1] for row in conn.execute(
                "SELECT status,COUNT(*) FROM positions GROUP BY status"
            )
        }
    output = {
        "selected": sorted(SELECTED),
        "seed_period": {"start": START.isoformat(), "end_exclusive": CUTOVER.isoformat()},
        "source_rows": len(trades),
        "inserted_positions": inserted_positions,
        "inserted_account_positions": inserted_accounts,
        "position_counts_after_seed": counts,
    }
    Path(args.output).write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
