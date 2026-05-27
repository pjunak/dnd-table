#!/bin/bash
# DnD Table kiosk launcher — runs as cage's single fullscreen Wayland client.
#
# Prefers the native dnd_display app. Falls back to Chromium loading the
# bundled GPU probe page if the native app isn't available yet (useful
# during initial setup and for verifying HW acceleration).
set -e

# Force Wayland for pyglet (the native app expects it; we don't want
# silent fallback to XWayland which loses the KMS-plane benefit).
export PYGLET_BACKEND=wayland

# Apply persisted display mode before any client opens its surface, so
# pyglet's fullscreen window is created at the user-chosen resolution.
# Silently skipped if no preference is set or the mode isn't supported.
SETTINGS=/home/dndtable/dnd-display/settings.json
if [ -r "${SETTINGS}" ]; then
    DISPLAY_MODE=$(python3 -c "import json,sys
try:
  d=json.load(open('${SETTINGS}'))
  m=(d.get('display') or {}).get('mode')
  print(m or '')
except Exception:
  print('')" 2>/dev/null)
    if [ -n "${DISPLAY_MODE}" ]; then
        OUTPUT=$(wlr-randr 2>/dev/null | awk '/^[A-Za-z]/ {print $1; exit}')
        if [ -n "${OUTPUT}" ]; then
            # Try --mode first (validated against EDID); fall back to
            # --custom-mode for refresh rates the TV won't advertise
            # (e.g. 1080p@30 on bandwidth-limited HDMI links).  Without
            # this fallback, cage starts at the EDID-preferred resolution
            # and the display app's window is created at the wrong size.
            wlr-randr --output "${OUTPUT}" --mode "${DISPLAY_MODE}" 2>/dev/null \
              || wlr-randr --output "${OUTPUT}" --custom-mode "${DISPLAY_MODE}" 2>/dev/null \
              || true
        fi
    fi
fi

NATIVE_PY=/opt/dnd-table/.venv/bin/python
NATIVE_PKG=/opt/dnd-table/dnd_display

if [ -x "${NATIVE_PY}" ] && [ -d "${NATIVE_PKG}" ]; then
    cd /opt/dnd-table
    LOG=/tmp/dnd-display.log
    # Truncate so each launch starts fresh
    : > "${LOG}"
    exec "${NATIVE_PY}" -m dnd_display "$@" >> "${LOG}" 2>&1
fi

# ── Fallback: Chromium on the GPU probe page ─────────────────────
# Bypass the Debian chromium wrapper: it injects a malformed
# `--load-extension=` when /usr/share/chromium/extensions is empty,
# which swallows the next flag (e.g. --kiosk) as its value.

PROFILE="${HOME}/.config/chromium-kiosk"
mkdir -p "${PROFILE}"
URL="${KIOSK_URL:-file:///opt/dnd-table/system/gpu-probe.html}"

exec /usr/lib/chromium/chromium \
  --kiosk \
  --remote-debugging-port=9222 \
  --remote-allow-origins=* \
  --ozone-platform=wayland \
  --enable-features=VaapiVideoDecoder,VaapiVideoEncoder \
  --disable-features=UseChromeOSDirectVideoDecoder \
  --enable-accelerated-video-decode \
  --ignore-gpu-blocklist \
  --no-first-run \
  --no-default-browser-check \
  --disable-translate \
  --disable-infobars \
  --disable-suggestions-service \
  --disable-save-password-bubble \
  --noerrdialogs \
  --check-for-update-interval=31536000 \
  --autoplay-policy=no-user-gesture-required \
  --user-data-dir="${PROFILE}" \
  "${URL}"
