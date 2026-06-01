#!/bin/bash
# DnD Table uninstaller — undoes everything install.sh set up, in reverse
# order, idempotently.  Media files in /media/dnd_media are left alone.

set -e

INSTALL_DIR="/opt/dnd-table"
GREETD_BACKUP="/etc/greetd/config.toml.orig"

echo "==> Uninstalling DnD Table..."

# ── 1. Flask service ──────────────────────────────────────────────
echo "==> Stopping Flask service..."
sudo systemctl stop dnd-table.service 2>/dev/null || true
sudo systemctl disable dnd-table.service 2>/dev/null || true
sudo rm -f /etc/systemd/system/dnd-table.service

# ── 2. Music output service ───────────────────────────────────────
echo "==> Removing music output client..."
sudo systemctl stop music-output.service 2>/dev/null || true
sudo systemctl disable music-output.service 2>/dev/null || true
sudo rm -f /etc/systemd/system/music-output.service
sudo rm -rf /opt/music-output
sudo rm -f /etc/music-output.env

# ── 3. Restore greetd config ──────────────────────────────────────
if [ -f "${GREETD_BACKUP}" ]; then
    echo "==> Restoring original greetd config..."
    sudo install -m 644 "${GREETD_BACKUP}" /etc/greetd/config.toml
    sudo rm -f "${GREETD_BACKUP}"
else
    echo "==> No greetd backup found; leaving /etc/greetd/config.toml as-is."
fi
sudo systemctl restart greetd.service 2>/dev/null || true

# ── 4. Re-enable getty on tty1 (the install disables it for greetd) ─
echo "==> Re-enabling getty@tty1.service..."
sudo systemctl enable getty@tty1.service 2>/dev/null || true

# ── 5. Sudoers ─────────────────────────────────────────────────────
echo "==> Removing sudoers rule..."
sudo rm -f /etc/sudoers.d/dnd-table

# ── 6. iptables port-80 redirect ──────────────────────────────────
echo "==> Clearing port-80 redirect..."
sudo /usr/sbin/iptables -t nat -D PREROUTING -p tcp --dport 80 -j REDIRECT --to-port 5000 2>/dev/null || true
sudo /usr/sbin/iptables -t nat -D OUTPUT -p tcp -d 127.0.0.1 --dport 80 -j REDIRECT --to-port 5000 2>/dev/null || true

# ── 7. Install directory ──────────────────────────────────────────
echo "==> Removing ${INSTALL_DIR}..."
sudo rm -rf "${INSTALL_DIR}"

# ── 8. Reload systemd state so removed units don't linger ─────────
sudo systemctl daemon-reload
sudo systemctl reset-failed 2>/dev/null || true

echo ""
echo "Done.  Media files in /media/dnd_media were left intact."
echo "User account 'dndtable' and any installed apt packages are also untouched —"
echo "remove those by hand if you want a fully clean slate."
