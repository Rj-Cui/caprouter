#!/usr/bin/env python3
"""Claude Code UserPromptSubmit hook entrypoint for CapRouter auto-use."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from caprouter.prompt_hook import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
