"""Lockfile and drift detection for a workspace's capability set."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .capabilities import bus_dir, load_entries
from .util import hash_tree, now_iso, read_json, write_json_atomic

LOCK_SCHEMA = "caprouter.lock.v1"
DRIFT_SCHEMA = "caprouter.drift.v1"


def lock_path(workspace: Path | str) -> Path:
    return bus_dir(workspace) / "caprouter.lock.json"


def _row(entry: dict[str, Any], *, live: bool = False) -> dict[str, Any]:
    row = {
        "id": entry.get("id"),
        "type": entry.get("type"),
        "name": entry.get("name"),
        "version": entry.get("version"),
        "hash": entry.get("hash"),
        "status": entry.get("status"),
        "path": entry.get("path"),
    }
    if live and entry.get("type") in {"skill", "plugin"}:
        path = Path(str(entry.get("path") or ""))
        if path.exists():
            row["hash"] = hash_tree(path)
    return row


def write_lock(
    workspace: Path | str,
    *,
    out: Path | str | None = None,
    live: bool = False,
) -> dict[str, Any]:
    workspace = Path(workspace)
    entries = [_row(entry, live=live) for entry in load_entries(workspace)]
    entries.sort(key=lambda item: str(item.get("id")))
    data = {
        "schema": LOCK_SCHEMA,
        "generated_at": now_iso(),
        "workspace": str(workspace),
        "count": len(entries),
        "entries": entries,
    }
    target = Path(out).expanduser() if out else lock_path(workspace)
    write_json_atomic(target, data)
    data["lock_path"] = str(target)
    return data


def check_drift(
    workspace: Path | str,
    *,
    lock: Path | str | None = None,
    live: bool = False,
) -> dict[str, Any]:
    workspace = Path(workspace)
    lock_file = Path(lock).expanduser() if lock else lock_path(workspace)
    locked_data = read_json(lock_file, {})
    if not isinstance(locked_data, dict) or locked_data.get("schema") != LOCK_SCHEMA:
        raise ValueError(f"lockfile missing or invalid: {lock_file}")
    locked = {
        str(item.get("id")): item
        for item in (locked_data.get("entries") or [])
        if isinstance(item, dict) and item.get("id")
    }
    current = {
        str(item.get("id")): item
        for item in (_row(entry, live=live) for entry in load_entries(workspace))
        if item.get("id")
    }
    missing = sorted(set(locked) - set(current))
    new = sorted(set(current) - set(locked))
    changed: list[dict[str, Any]] = []
    for identifier in sorted(set(locked) & set(current)):
        before = locked[identifier]
        after = current[identifier]
        if str(before.get("hash")) != str(after.get("hash")):
            changed.append(
                {
                    "id": identifier,
                    "from_hash": before.get("hash"),
                    "to_hash": after.get("hash"),
                    "from_version": before.get("version"),
                    "to_version": after.get("version"),
                    "path": after.get("path"),
                }
            )
    result = {
        "schema": DRIFT_SCHEMA,
        "lock_path": str(lock_file),
        "clean": not (missing or new or changed),
        "counts": {
            "missing": len(missing),
            "new": len(new),
            "changed": len(changed),
            "locked": len(locked),
            "current": len(current),
        },
        "missing": missing,
        "new": new,
        "changed": changed,
    }
    return result
