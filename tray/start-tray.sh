#!/usr/bin/env sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
exec uv run --with pystray --with pillow python "$ROOT/caprouter_tray.py"
