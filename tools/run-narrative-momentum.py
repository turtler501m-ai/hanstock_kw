# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategy.narrative_momentum_runner import run_narrative_momentum_cycle  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run narrative momentum scan.")
    parser.add_argument("--history", default=str(ROOT / ".runtime" / "narrative_history.json"))
    parser.add_argument("--theme-map", default=str(ROOT / "config" / "theme_map.json"))
    parser.add_argument("--output", default=str(ROOT / ".runtime" / "narrative_momentum_latest.json"))
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--save-candidates", action="store_true")
    parser.add_argument("--auto-collect", action="store_true")
    args = parser.parse_args()

    payload = run_narrative_momentum_cycle(
        save_candidates=args.save_candidates,
        auto_collect=args.auto_collect,
        history_path=Path(args.history),
        theme_map_path=Path(args.theme_map),
        latest_path=Path(args.output),
    )
    print(json.dumps({"status": payload.get("status"), "total_scanned": payload.get("total_scanned"), "saved_count": payload.get("saved_count"), "collection": payload.get("collection"), "top": payload.get("signals", [])[: args.limit]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
