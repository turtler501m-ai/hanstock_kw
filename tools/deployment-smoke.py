#!/usr/bin/env python3
"""Fail deployment when the restarted dashboard is unreachable or malformed."""

from __future__ import annotations

import argparse
import json
import time
from urllib.error import URLError
from urllib.request import Request, urlopen


def fetch_json(url: str, timeout: float) -> dict:
    request = Request(url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}: {url}")
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise RuntimeError(f"JSON object required: {url}")
    return payload


def verify(base_url: str, timeout: float) -> dict:
    health = fetch_json(f"{base_url}/api/health", timeout)
    operations = fetch_json(f"{base_url}/api/operations/health", timeout)
    missing = {
        key for key in ("state", "operational_status", "new_risk_allowed", "blockers", "schema")
        if key not in operations
    }
    if missing:
        raise RuntimeError(f"operations health missing fields: {', '.join(sorted(missing))}")
    if not isinstance(operations["blockers"], list):
        raise RuntimeError("operations health blockers must be a list")
    if not isinstance(operations["schema"], dict) or "ready" not in operations["schema"]:
        raise RuntimeError("operations health schema readiness is missing")
    return {"dashboard": health, "operations": operations}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--attempts", type=int, default=12)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--timeout", type=float, default=4.0)
    args = parser.parse_args()
    error: Exception | None = None
    for attempt in range(1, max(1, args.attempts) + 1):
        try:
            result = verify(args.base_url.rstrip("/"), args.timeout)
            operations = result["operations"]
            print(json.dumps({
                "smoke": "ok", "attempt": attempt,
                "operational_status": operations["operational_status"],
                "state": operations["state"],
                "new_risk_allowed": operations["new_risk_allowed"],
                "blockers": operations["blockers"],
                "warnings": operations.get("warnings", []),
            }, ensure_ascii=False))
            return 0
        except (OSError, URLError, ValueError, RuntimeError) as exc:
            error = exc
            if attempt < args.attempts:
                time.sleep(max(0, args.interval))
    print(json.dumps({"smoke": "failed", "error": str(error)}, ensure_ascii=False))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
