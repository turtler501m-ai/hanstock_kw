from __future__ import annotations

import argparse
import json
from typing import Sequence

from src.broker import create_domestic_stock_broker

from .service import MarketRegimeService


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect and inspect the Kiwoom KR market regime")
    parser.add_argument("command", choices=("refresh", "preflight", "current", "history", "diagnostics"), nargs="?", default="refresh")
    parser.add_argument("--market", default="KR")
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args(argv)
    try:
        service = MarketRegimeService(create_domestic_stock_broker(order_submission_enabled=False))
        if args.command in {"refresh", "preflight"}:
            result = service.refresh(args.market)
        elif args.command == "current":
            result = service.current()
        elif args.command == "history":
            result = service.history(args.days)
        else:
            result = service.diagnostics()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.command == "preflight" and (not result or result.get("quality") == "insufficient"):
            return 1
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
