"""Process entrypoint for continuous read-only condition monitoring."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import threading

from src.strategy.condition_monitor import run_forever
from src.utils.logger import logger


def _runtime_revision() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            check=False,
            text=True,
            timeout=2,
        )
        return result.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def main() -> None:
    stop_event = threading.Event()

    def stop(_signum, _frame) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    interval_seconds = float(os.environ.get("CONDITION_MONITOR_INTERVAL_SECONDS", "60"))
    logger.info(
        "[SERVER_LIFECYCLE] service=condition_monitor event=startup pid={} host={} "
        "revision={} interval_seconds={}",
        os.getpid(),
        socket.gethostname(),
        _runtime_revision(),
        interval_seconds,
    )
    try:
        run_forever(
            interval_seconds=interval_seconds,
            stop_requested=stop_event.is_set,
        )
    finally:
        logger.info(
            "[SERVER_LIFECYCLE] service=condition_monitor event=shutdown pid={} host={} revision={}",
            os.getpid(),
            socket.gethostname(),
            _runtime_revision(),
        )


if __name__ == "__main__":
    main()
