#!/usr/bin/env bash
set -euo pipefail
# Music has its own release and service; never rerun the display/kiosk installer.
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
exec sudo python3 "$script_dir/music_install.py" "$@"
