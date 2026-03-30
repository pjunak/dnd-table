#!/bin/bash
# Detects the connected display output and configures it.
# Falls back to the display's preferred mode if 1920x1080 is not available.

DISPLAY_ENV="${DISPLAY:-:0}"
PREFERRED_MODE="1920x1080"
PREFERRED_RATE="60"

# Wait for Xauthority cookie file to exist and X to be available (up to 60 seconds)
for i in $(seq 1 60); do
    # Find the actual Xauthority file for this display session
    XAUTH_FILE=$(find /run/user /tmp -name ".Xauthority" -newer /proc/1 2>/dev/null | head -1)
    if [ -z "$XAUTH_FILE" ] && [ -f "/home/dnd/.Xauthority" ]; then
        XAUTH_FILE="/home/dnd/.Xauthority"
    fi
    if [ -n "$XAUTH_FILE" ]; then
        export XAUTHORITY="$XAUTH_FILE"
        if xrandr --display "$DISPLAY_ENV" &>/dev/null; then
            echo "X ready after ${i}s, using XAUTHORITY=$XAUTH_FILE"
            break
        fi
    fi
    echo "Waiting for X display $DISPLAY_ENV... ($i/60)"
    sleep 1
done

if ! xrandr --display "$DISPLAY_ENV" &>/dev/null; then
    echo "ERROR: X display $DISPLAY_ENV not available after 60 seconds." >&2
    exit 1
fi

# Find the first connected output
OUTPUT=$(xrandr --display "$DISPLAY_ENV" | awk '/ connected/{print $1; exit}')

if [ -z "$OUTPUT" ]; then
    echo "ERROR: No connected display output found." >&2
    exit 1
fi

echo "Detected output: $OUTPUT"

# Check if preferred mode is available on this output
if xrandr --display "$DISPLAY_ENV" | grep -A 50 "^$OUTPUT " | grep -q "$PREFERRED_MODE"; then
    echo "Setting ${PREFERRED_MODE}@${PREFERRED_RATE}Hz on $OUTPUT..."
    xrandr --display "$DISPLAY_ENV" --output "$OUTPUT" --mode "$PREFERRED_MODE" --rate "$PREFERRED_RATE"
else
    echo "Mode $PREFERRED_MODE not available, using preferred mode..."
    xrandr --display "$DISPLAY_ENV" --output "$OUTPUT" --auto
fi

# Disable screensaver and power management
xset -display "$DISPLAY_ENV" -dpms
xset -display "$DISPLAY_ENV" s off
xset -display "$DISPLAY_ENV" s noblank

echo "Display setup complete."
