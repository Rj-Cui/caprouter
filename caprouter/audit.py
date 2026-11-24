from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .state import state_path
from .util import now_iso

_ACTIVE_WORKSPACE: Path | None = None


def set_audit_workspace(workspace: Path | str | None) -> None:
    """Scope audit writes to one workspace for the current process.

    Without this, every scanned workspace and every test run appends to the
    same ``~/.caprouter/audit.jsonl``, which both pollutes the user's global log
    and mixes unrelated work. Explicit ``CAPROUTER_AUDIT`` still wins.
    """
    global _ACTIVE_WORKSPACE
    _ACTIVE_WORKSPACE = Path(workspace).expanduser() if workspace else None


def audit_path() -> Path:
    override = os.environ.get("CAPROUTER_AUDIT")
    if override:
        return Path(override).expanduser()
    if _ACTIVE_WORKSPACE is not None:
        return _ACTIVE_WORKSPACE / ".caprouter" / "audit.jsonl"
    return state_path().parent / "audit.jsonl"


def append_audit(event: dict[str, Any]) -> None:
    path = audit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"ts": now_iso(), **event}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
