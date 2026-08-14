#!/bin/bash
# Install systemd service for Hanstock
set -e

SERVICE_FILE="/etc/systemd/system/hanstock-kw.service"
SRC_FILE="$(dirname "$0")/hanstock-kw.service"

if [ "$EUID" -ne 0 ]; then
  echo "Please run as root (sudo)"
  exit 1
fi

echo "Copying service file..."
cp "$SRC_FILE" "$SERVICE_FILE"

echo "Reloading systemd daemon..."
systemctl daemon-reload

echo "Enabling hanstock-kw service..."
systemctl enable hanstock-kw

echo "Starting hanstock-kw service..."
systemctl start hanstock-kw

echo "Status:"
systemctl status hanstock-kw --no-pager
