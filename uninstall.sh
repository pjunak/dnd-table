#!/bin/bash
set -e

INSTALL_DIR="/opt/dnd-table"

echo "==> Uninstalling DnD Table..."

echo "==> Stopping service..."
for SERVICE in dnd-table.service dnd-display-setup.service; do
    sudo systemctl stop "$SERVICE" 2>/dev/null || true
    sudo systemctl disable "$SERVICE" 2>/dev/null || true
    sudo rm -f "/etc/systemd/system/$SERVICE"
done
sudo systemctl daemon-reload
sudo systemctl reset-failed 2>/dev/null || true

echo "==> Removing files from $INSTALL_DIR..."
sudo rm -rf "$INSTALL_DIR"

echo ""
echo "Done. Media files in /media/dnd_media were left intact."
