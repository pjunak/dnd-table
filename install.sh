#!/bin/bash
# DnD Table installer — Debian 13 (Trixie) / x86_64 / Wayland / native display
# Idempotent: safe to re-run.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/dnd-table"
MEDIA_DIR="/media/dnd_media"
USER_NAME="dndtable"
USER_HOME="/home/${USER_NAME}"
GREETD_BACKUP="/etc/greetd/config.toml.orig"

if [ "${USER_NAME}" != "$(id -un)" ] && [ "${SUDO_USER:-}" != "${USER_NAME}" ]; then
    echo "WARNING: run this as the ${USER_NAME} user (currently $(id -un))."
fi

echo "==> Installing DnD Table (Debian 13 / x86 / Wayland / native display)..."

# ── 1. Enable contrib + non-free for VA-API driver ───────────────
if ! grep -q 'non-free[^-]' /etc/apt/sources.list 2>/dev/null; then
    echo "==> Enabling contrib + non-free in /etc/apt/sources.list..."
    sudo sed -i 's/main non-free-firmware/main contrib non-free non-free-firmware/g' /etc/apt/sources.list
fi

# ── 2. Install system packages ───────────────────────────────────
echo "==> Updating apt and installing packages..."
sudo apt-get update
sudo apt-get install -y \
    python3-flask \
    python3-gi gir1.2-gst-plugins-base-1.0 \
    gstreamer1.0-tools \
    gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly \
    gstreamer1.0-vaapi gstreamer1.0-gl \
    python3-venv python3-pip \
    python3-requests python3-pil \
    mesa-va-drivers i965-va-driver-shaders vainfo \
    firmware-misc-nonfree \
    libglvnd-dev \
    pipewire pipewire-pulse wireplumber \
    mpv ffmpeg \
    libmpv2 python3-mpv python3-websocket \
    iptables avahi-daemon \
    git rsync curl \
    greetd cage xwayland \
    fonts-noto fonts-noto-color-emoji \
    wlr-randr wayland-utils grim \
    unattended-upgrades

# ── 3. User groups (video/render/input for KMS + libinput, audio for music output) ─
echo "==> Adding ${USER_NAME} to video, render, input, audio groups..."
sudo usermod -aG video,render,input,audio "${USER_NAME}"

# ── 4. Sudoers (NOPASSWD for the specific commands the app calls) ─
echo "==> Configuring sudoers for ${USER_NAME}..."
sudo tee /etc/sudoers.d/dnd-table > /dev/null <<EOF
${USER_NAME} ALL=(ALL) NOPASSWD: /usr/bin/systemctl, /sbin/reboot, /usr/sbin/reboot, /sbin/shutdown, /usr/sbin/shutdown, /usr/sbin/iptables, /usr/bin/rsync, /usr/bin/chown, /usr/bin/chmod, /usr/bin/cp
EOF
sudo chmod 0440 /etc/sudoers.d/dnd-table
sudo visudo -c -f /etc/sudoers.d/dnd-table > /dev/null

# ── 5. Deploy code to /opt/dnd-table ─────────────────────────────
echo "==> Deploying code to ${INSTALL_DIR}..."
sudo mkdir -p "${INSTALL_DIR}"
sudo rsync -a --delete \
    --exclude='.git' --exclude='__pycache__' --exclude='.vscode' \
    --exclude='.venv' --exclude='*.png' --exclude='settings.json' \
    "${SCRIPT_DIR}/" "${INSTALL_DIR}/"
sudo chown -R "${USER_NAME}:${USER_NAME}" "${INSTALL_DIR}"
sudo chmod +x "${INSTALL_DIR}/kiosk.sh"

# ── 6. Python venv with system-site-packages (for python3-gi / gst) ─
echo "==> Creating Python venv at ${INSTALL_DIR}/.venv..."
sudo -u "${USER_NAME}" python3 -m venv "${INSTALL_DIR}/.venv" --system-site-packages
sudo -u "${USER_NAME}" "${INSTALL_DIR}/.venv/bin/pip" install --upgrade --quiet pip
if [ -f "${INSTALL_DIR}/requirements.txt" ]; then
    sudo -u "${USER_NAME}" "${INSTALL_DIR}/.venv/bin/pip" install --quiet -r "${INSTALL_DIR}/requirements.txt"
fi

# ── 7. Media directories ─────────────────────────────────────────
echo "==> Creating media directories..."
sudo mkdir -p "${MEDIA_DIR}/Maps" "${MEDIA_DIR}/Videos"
sudo chown -R "${USER_NAME}:${USER_NAME}" "${MEDIA_DIR}"

# ── 8. Persistent settings dir ───────────────────────────────────
sudo -u "${USER_NAME}" mkdir -p "${USER_HOME}/dnd-display"

# ── 9. Headless music output (pjunak/music) ──────────────────────
# The table acts as a remote audio output of music.junak.eu: a small
# guest client follows the server's playback over a WebSocket and plays
# it through mpv, exposing a localhost control surface the Flask panel
# proxies.  Downloaded from the upstream repo so it tracks future fixes.
echo "==> Installing headless music output client..."
sudo mkdir -p /opt/music-output
sudo curl -fsSL \
    https://raw.githubusercontent.com/pjunak/music/main/clients/headless/music_output.py \
    -o /opt/music-output/music_output.py
sudo chown -R "${USER_NAME}:${USER_NAME}" /opt/music-output
# Config — written only if absent so a hand-edited server URL survives
# re-runs.  XDG_RUNTIME_DIR lets the system service reach the dndtable
# session's PipeWire socket (audio group is the ALSA fallback).
if [ ! -f /etc/music-output.env ]; then
    sudo tee /etc/music-output.env > /dev/null <<EOF
MUSIC_SERVER_URL=https://music.junak.eu
MUSIC_OUTPUT_NAME=DnD Table
MUSIC_CONTROL_PORT=8731
XDG_RUNTIME_DIR=/run/user/$(id -u "${USER_NAME}")
EOF
fi
sudo cp "${INSTALL_DIR}/system/music-output.service" /etc/systemd/system/

# ── 10. greetd config — autologin into cage ────────────────────
echo "==> Configuring greetd..."
if [ ! -f "${GREETD_BACKUP}" ] && [ -f /etc/greetd/config.toml ]; then
    sudo cp /etc/greetd/config.toml "${GREETD_BACKUP}"
fi
sudo install -m 644 "${INSTALL_DIR}/system/greetd-config.toml" /etc/greetd/config.toml

# ── 11. Disable getty@tty1 so greetd owns vt1 ────────────────────
sudo systemctl disable --now getty@tty1.service 2>/dev/null || true

# ── 12. Install + enable systemd services ────────────────────────
echo "==> Installing dnd-table.service + music-output.service..."
sudo cp "${INSTALL_DIR}/dnd-table.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable avahi-daemon greetd dnd-table.service music-output.service
sudo systemctl restart dnd-table.service || true
sudo systemctl restart music-output.service || true

echo ""
echo "==> Done."
echo "    Flask:  systemctl status dnd-table.service"
echo "    Kiosk:  systemctl status greetd.service"
echo "    Reboot to land in kiosk mode from a clean boot."
