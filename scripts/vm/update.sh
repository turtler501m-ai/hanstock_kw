#!/usr/bin/env bash
set -euo pipefail

BRANCH="${1:-main}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT_DIR"

echo "[update] repo: $ROOT_DIR"
echo "[update] branch: $BRANCH"

if [ ! -f ".env" ]; then
    echo "[update] missing .env. Create it from .env.example and set VM secrets first." >&2
    exit 1
fi

git fetch origin "$BRANCH"
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"

if [ ! -x ".venv/bin/python" ]; then
    echo "[update] creating .venv"
    python3 -m venv .venv
fi

PYTHON="$ROOT_DIR/.venv/bin/python"

"$PYTHON" -c 'import sys; assert sys.version_info >= (3, 10), "Hanstock VM requires Python 3.10+"'

echo "[update] installing requirements"
"$PYTHON" -m pip install \
    --constraint constraints-deploy.txt \
    --requirement requirements-core.txt \
    --requirement requirements-integrations.txt

mkdir -p "$ROOT_DIR/logs" "$ROOT_DIR/.runtime"

echo "[update] syncing Kiwoom dashboard systemd unit"
sudo install -m 0644 \
    "$ROOT_DIR/scripts/vm/hanstock-kw.service" \
    /etc/systemd/system/hanstock-kw.service
sudo systemctl daemon-reload
sudo systemctl enable hanstock-kw.service

echo "[update] restarting dashboard"
bash "$ROOT_DIR/scripts/vm/server.sh" restart
bash "$ROOT_DIR/scripts/vm/server.sh" status

echo "[update] done"
