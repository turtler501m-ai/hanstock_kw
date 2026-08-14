from __future__ import annotations

import os


class OnlineAccessBlockedError(RuntimeError):
    pass


def _env_blocked() -> bool:
    return os.environ.get("ONLINE_ACCESS_BLOCKED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def is_online_access_blocked() -> bool:
    try:
        from src.config import config

        return bool(getattr(config, "online_access_blocked", False))
    except Exception:
        return _env_blocked()


def require_online_access(operation: str = "online operation") -> None:
    if is_online_access_blocked():
        raise OnlineAccessBlockedError(
            f"{operation} is disabled because ONLINE_ACCESS_BLOCKED=true"
        )
