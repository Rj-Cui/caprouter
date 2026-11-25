#!/usr/bin/env python3
"""CapRouter CLI entrypoint. No packaging required."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# ``sys.path[0]`` is this script's directory (``bin/``), which also contains a
# ``caprouter.py``; that file would shadow the ``caprouter`` package whenever the
# repository root is already importable (``PYTHONPATH``, a ``.pth`` file, or an
# installed copy). Always put the repository root first instead of only when it
# is missing.
_root = str(ROOT)
while _root in sys.path:
    sys.path.remove(_root)
sys.path.insert(0, _root)

from caprouter.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
