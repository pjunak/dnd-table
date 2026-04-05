#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/dnd-table"
MEDIA_DIR="/media/dnd_media"
USER="dnd"

echo "==> Installing DnD Table..."

# Install dependencies
echo "==> Installing dependencies..."
sudo apt-get install -y python3-flask mpv avahi-daemon rsync iptables

# Create install directory and copy files
echo "==> Copying files to $INSTALL_DIR..."
sudo mkdir -p "$INSTALL_DIR"
sudo rsync -a --exclude='.git' --exclude='__pycache__' --exclude='.vscode' --exclude='.claude' \
    "$SCRIPT_DIR/" "$INSTALL_DIR/"
sudo chown -R "$USER:$USER" "$INSTALL_DIR"
sudo chmod +x "$INSTALL_DIR/setup-display.sh"

# Create media directories
echo "==> Creating media directories..."
sudo mkdir -p "$MEDIA_DIR/Maps" "$MEDIA_DIR/Videos" "$MEDIA_DIR/Ambient" "$MEDIA_DIR/SFX"
sudo chown -R "$USER:$USER" "$MEDIA_DIR"

# Remove old split service if present
sudo systemctl stop dnd-display-setup.service 2>/dev/null || true
sudo systemctl disable dnd-display-setup.service 2>/dev/null || true
sudo rm -f /etc/systemd/system/dnd-display-setup.service

# Install and enable service
echo "==> Installing systemd service..."
sudo cp "$INSTALL_DIR/dnd-table.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable avahi-daemon
sudo systemctl enable dnd-table.service
sudo systemctl start dnd-table.service || true

echo ""
echo "Done. Service status:"
sudo systemctl status dnd-table.service --no-pager || true
