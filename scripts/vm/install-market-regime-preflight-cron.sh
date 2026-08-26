#!/bin/bash
set -euo pipefail

TIME_SPEC="${1:-43 8 * * 1-5}"
CRON_TZ_VALUE="${HANSTOCK_CRON_TZ:-Asia/Seoul}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
JOB="$TIME_SPEC cd $ROOT_DIR && bash $ROOT_DIR/scripts/vm/market-regime-preflight.sh"

existing="$(mktemp)"
trap 'rm -f "$existing"' EXIT
crontab -l 2>/dev/null | awk '
    /# hanstock-kw-market-regime-preflight begin/ { skip = 1; next }
    /# hanstock-kw-market-regime-preflight end/ { skip = 0; next }
    skip != 1 { print }
' > "$existing" || true
{
    cat "$existing"
    echo "# hanstock-kw-market-regime-preflight begin"
    echo "CRON_TZ=$CRON_TZ_VALUE"
    echo "$JOB"
    echo "# hanstock-kw-market-regime-preflight end"
} | crontab -

echo "[cron] installed: CRON_TZ=$CRON_TZ_VALUE $JOB"
echo "[cron] current matching entries:"
crontab -l | awk '
    /# hanstock-kw-market-regime-preflight begin/ { show = 1 }
    show == 1 { print }
    /# hanstock-kw-market-regime-preflight end/ { show = 0 }
'
