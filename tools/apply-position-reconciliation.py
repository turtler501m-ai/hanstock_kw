"""Apply latest open broker-balance reconciliation issues to positions."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.application.orders.position_reconciliation import apply_latest_open_reconciliation_issues
from src.db.repository import connect_db, init_db


if __name__ == "__main__":
    init_db()
    result = apply_latest_open_reconciliation_issues(
        connect_db, actor="operations-cli",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
