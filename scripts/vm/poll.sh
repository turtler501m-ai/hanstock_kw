#!/usr/bin/env bash
# Poll signal-tracking channels every 5 minutes (han2 version)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/../.."
PY=".venv/bin/python"
LOCK_FILE=".runtime/poll.lock"
mkdir -p .runtime

is_trading_time_kst() {
  local dow="$1"
  local hm="$2"

  case "$dow" in
    1) [ "$hm" -ge 700 ] ;;
    2|3|4|5) [ "$hm" -lt 600 ] || [ "$hm" -ge 700 ] ;;
    6) [ "$hm" -lt 600 ] ;;
    *) return 1 ;;
  esac
}

{
  exec 9>"$LOCK_FILE"
  if ! flock -n 9; then
    echo "polling already running; skip"
    exit 0
  fi

  NOW_DOW=$(TZ=Asia/Seoul date +%u)
  NOW_HM_RAW=$(TZ=Asia/Seoul date +%H%M)
  NOW_HM=$((10#$NOW_HM_RAW))

  if [ "${POLLING_FORCE_RUN:-0}" != "1" ] && ! is_trading_time_kst "$NOW_DOW" "$NOW_HM"; then
    echo "outside trading hours; skip (KST dow=$NOW_DOW time=$NOW_HM_RAW)"
    exit 0
  fi

  "$PY" -m src.futures_signals.poll
} 2>&1
