#!/usr/bin/env bash
# Continuous polling daemon - runs every 5 minutes
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT_DIR" || exit 1

INTERVAL=300
LOG_FILE=".runtime/polld.log"

exec >> "$LOG_FILE" 2>&1

echo "$(date) polld started"

while true; do
    echo "$(date) polling..."
    POLLING_FORCE_RUN=1 "$SCRIPT_DIR/poll.sh"
    echo "$(date) done"
    sleep $INTERVAL
done
