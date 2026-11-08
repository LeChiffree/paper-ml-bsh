from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


DB = Path(__file__).resolve().parent / "paper_trade_app" / "data" / "paper_trade.db"
ACTIVE = (17, 49, 111)


def main() -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = DB.with_name(f"paper_trade_before_scenarios_17_49_111_{stamp}.db")
    with sqlite3.connect(DB) as source, sqlite3.connect(backup) as target:
        source.backup(target)
    now = datetime.now(timezone.utc).isoformat()
    placeholders = ",".join("?" for _ in ACTIVE)
    with sqlite3.connect(DB) as conn:
        account_rows = conn.execute(
            f"""UPDATE execution_account_positions
                SET status='CANCELLED',exit_reason='scenario_set_replaced',updated_at=?
                WHERE status IN ('PENDING','OPEN') AND scenario_id NOT IN ({placeholders})""",
            (now, *ACTIVE),
        ).rowcount
        parents = conn.execute(
            f"""UPDATE positions SET status='CANCELLED',exit_reason='scenario_set_replaced',updated_at=?
                WHERE status IN ('PENDING','OPEN') AND scenario_id NOT IN ({placeholders})""",
            (now, *ACTIVE),
        ).rowcount
        mirrors = conn.execute(
            """UPDATE futures_demo_mirrors SET status='TRACKING_CANCELLED',
                error=COALESCE(error,'scenario_set_replaced'),updated_at=?
                WHERE status IN ('OPENING','OPEN')""",
            (now,),
        ).rowcount
        conn.commit()
    print(f"backup={backup}")
    print(f"paper_account_rows_cancelled={account_rows}")
    print(f"parent_positions_cancelled={parents}")
    print(f"demo_tracking_rows_cancelled={mirrors}")


if __name__ == "__main__":
    main()
