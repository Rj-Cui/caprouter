from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .util import now_iso, read_json, write_json_atomic

SCHEMA = "caprouter.state.v1"
DEFAULT_ENABLED = True


def state_path() -> Path:
    override = os.environ.get("CAPROUTER_STATE")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".caprouter" / "state.json"


def read_state() -> dict[str, Any]:
    data = read_json(state_path())
    if not isinstance(data, dict):
        return {
            "schema": SCHEMA,
            "enabled": DEFAULT_ENABLED,
            "updated_at": None,
            "updated_by": "default",
        }
    enabled = data.get("enabled")
    return {
        "schema": SCHEMA,
        "enabled": enabled if isinstance(enabled, bool) else DEFAULT_ENABLED,
        "updated_at": data.get("updated_at"),
        "updated_by": data.get("updated_by") or "unknown",
    }


def write_state(enabled: bool, updated_by: str = "cli") -> dict[str, Any]:
    data = {
        "schema": SCHEMA,
        "enabled": bool(enabled),
        "updated_at": now_iso(),
        "updated_by": updated_by,
    }
    write_json_atomic(state_path(), data)
    return data


def is_enabled() -> bool:
    return bool(read_state().get("enabled", DEFAULT_ENABLED))
