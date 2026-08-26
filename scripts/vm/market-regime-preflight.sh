#!/bin/bash
set -euo pipefail

# Read-only Kiwoom market-data preflight. This intentionally uses its own lock:
# it never runs an order cycle, while the Python provider shares the normal
# Kiwoom query throttle with other REST requests.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT_DIR"

RUNTIME_DIR="$ROOT_DIR/.runtime"
LOG_DIR="$ROOT_DIR/logs"
mkdir -p "$RUNTIME_DIR" "$LOG_DIR"

find_python() {
    if [ -n "${PYTHON:-}" ]; then
        echo "$PYTHON"
        return 0
    fi
    if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
        echo "$ROOT_DIR/.venv/bin/python"
        return 0
    fi
    if [ -x "$ROOT_DIR/venv/bin/python" ]; then
        echo "$ROOT_DIR/venv/bin/python"
        return 0
    fi
    command -v python3 || command -v python
}

PYTHON_BIN="$(find_python)"
LOG_FILE="$LOG_DIR/market-regime-preflight.log"
LOCK_FILE="$RUNTIME_DIR/market-regime-preflight.lock"

acquire_lock() {
    if command -v flock >/dev/null 2>&1; then
        exec 9>"$LOCK_FILE"
        if ! flock -n 9; then
            echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] market regime preflight already running; skipped"
            exit 0
        fi
        return 0
    fi

    if ! mkdir "$LOCK_FILE.dir" 2>/dev/null; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] market regime preflight already running; skipped"
        exit 0
    fi
    trap 'rmdir "$LOCK_FILE.dir" 2>/dev/null || true' EXIT
}

{
    started_epoch="$(date +%s)"
    echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] market regime preflight start"
    acquire_lock
    set +e
    "$PYTHON_BIN" -m src.market_regime preflight --market KR
    status=$?
    set -e
    elapsed_seconds=$(( $(date +%s) - started_epoch ))
    if [ "$status" -ne 0 ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] market regime preflight failed status=$status duration_seconds=$elapsed_seconds"
        exit "$status"
    fi
    echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] market regime preflight done duration_seconds=$elapsed_seconds"
} >> "$LOG_FILE" 2>&1
