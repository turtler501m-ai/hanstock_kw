"""Fail deployment when Kiwoom databases escape or overlap the repository."""
from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import dotenv_values


def verify(root: Path) -> dict[str, Path]:
    root = root.resolve()
    values = dotenv_values(root / ".env")
    configured = {
        "TRADE_DB_PATH": values.get("TRADE_DB_PATH") or ".runtime/trades.sqlite",
        "MISTOCK_TRADE_DB_PATH": (
            values.get("MISTOCK_TRADE_DB_PATH") or ".runtime/mistock/trades.sqlite"
        ),
    }
    resolved: dict[str, Path] = {}
    for key, raw_path in configured.items():
        path = Path(str(raw_path))
        path = (root / path).resolve() if not path.is_absolute() else path.resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"{key} must stay inside {root}: {path}") from exc
        resolved[key] = path
    if len(set(resolved.values())) != len(resolved):
        raise ValueError("domestic and US trading databases must use different paths")
    return resolved


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    for key, path in verify(args.root).items():
        print(f"[isolation] {key}={path}")


if __name__ == "__main__":
    main()
