from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "paper_trade_app" / "data" / "paper_trade.db"


def main() -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = DB_PATH.with_name(f"paper_trade_before_legacy_cancel_{stamp}.db")
    now = datetime.now(timezone.utc).isoformat()

    with sqlite3.connect(DB_PATH) as source, sqlite3.connect(backup_path) as backup:
        source.backup(backup)

    with sqlite3.connect(DB_PATH) as conn:
        legacy_positions = conn.execute(
            """UPDATE positions
               SET status='CANCELLED', exit_reason='legacy_system_cancelled', updated_at=?
               WHERE status IN ('PENDING','OPEN')
                 AND NOT EXISTS (
                   SELECT 1 FROM execution_account_positions e
                   WHERE e.position_id=positions.position_id
                 )""",
            (now,),
        ).rowcount
        demo_orders = conn.execute(
            """UPDATE spot_demo_orders
               SET status='TRACKING_CANCELLED',
                   error=COALESCE(error, 'legacy_system_cancelled'), updated_at=?
               WHERE status IN ('OPEN','OPENING','PENDING')""",
            (now,),
        ).rowcount
        leverage_positions = conn.execute(
            """UPDATE leverage_positions
               SET status='CANCELLED', exit_reason='legacy_system_cancelled',
                   funding_error=COALESCE(funding_error, 'legacy_system_cancelled'), updated_at=?
               WHERE status IN ('PENDING','OPEN')""",
            (now,),
        ).rowcount
        motorlab_positions = conn.execute(
            "SELECT COUNT(*) FROM execution_account_positions WHERE status IN ('PENDING','OPEN')"
        ).fetchone()[0]
        conn.commit()

    print(f"backup={backup_path}")
    print(f"legacy_positions_cancelled={legacy_positions}")
    print(f"demo_orders_tracking_cancelled={demo_orders}")
    print(f"leverage_positions_cancelled={leverage_positions}")
    print(f"motorlab_positions_preserved={motorlab_positions}")


if __name__ == "__main__":
    main()
