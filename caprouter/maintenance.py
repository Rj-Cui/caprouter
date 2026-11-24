"""Log rotation and installed/.trash cleanup."""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from .capabilities import bus_dir
from .paths import installed_root
from .util import now_iso, read_jsonl

PRUNE_SCHEMA = "caprouter.prune.v1"
TRASH_SCHEMA = "caprouter.trash.v1"


def rotate_jsonl(
    path: Path | str,
    *,
    keep: int = 1000,
    dry_run: bool = False,
) -> dict[str, Any]:
    path = Path(path)
    keep = max(1, int(keep))
    if not path.is_file():
        return {"path": str(path), "status": "missing", "before": 0, "after": 0}
    rows = read_jsonl(path)
    before = len(rows)
    if before <= keep:
        return {"path": str(path), "status": "unchanged", "before": before, "after": before}
    kept = rows[-keep:]
    backup = None
    if not dry_run:
        backup_path = path.with_suffix(path.suffix + f".{time.strftime('%Y%m%d-%H%M%S')}.bak")
        shutil.copy2(path, backup_path)
        path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in kept),
            encoding="utf-8",
        )
        backup = str(backup_path)
    return {
        "path": str(path),
        "status": "rotated" if not dry_run else "would_rotate",
        "before": before,
        "after": len(kept),
        "backup": backup,
    }


def prune_logs(
    workspace: Path | str,
    *,
    kind: str,
    keep: int = 1000,
    dry_run: bool = False,
) -> dict[str, Any]:
    workspace = Path(workspace)
    names = {
        "probes": "probes.jsonl",
        "outcomes": "outcomes.jsonl",
        "discovery": "discovery.jsonl",
        "gaps": "gaps.jsonl",
        "uninstalled": "uninstalled.jsonl",
        "updates": "updates.jsonl",
    }
    if kind == "audit":
        from .audit import audit_path

        path = audit_path()
    elif kind in names:
        path = bus_dir(workspace) / names[kind]
    else:
        raise ValueError(f"unknown log kind: {kind}")
    result = rotate_jsonl(path, keep=keep, dry_run=dry_run)
    result["schema"] = PRUNE_SCHEMA
    result["kind"] = kind
    result["dry_run"] = dry_run
    result["pruned_at"] = now_iso()
    return result


def trash_entries(workspace: Path | str) -> list[dict[str, Any]]:
    root = installed_root(workspace) / ".trash"
    rows: list[dict[str, Any]] = []
    if not root.is_dir():
        return rows
    now = time.time()
    for path in sorted(root.iterdir()):
        if not path.is_dir():
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        rows.append(
            {
                "name": path.name,
                "path": str(path),
                "mtime": mtime,
                "age_days": round((now - mtime) / 86400, 1),
            }
        )
    return rows


def clean_trash(
    workspace: Path | str,
    *,
    older_than_days: float = 30,
    dry_run: bool = True,
) -> dict[str, Any]:
    rows = trash_entries(workspace)
    selected = [row for row in rows if float(row["age_days"]) >= float(older_than_days)]
    if not dry_run:
        for row in selected:
            shutil.rmtree(row["path"], ignore_errors=True)
    return {
        "schema": TRASH_SCHEMA,
        "status": "preview" if dry_run else "cleaned",
        "dry_run": dry_run,
        "older_than_days": older_than_days,
        "count": len(selected),
        "total": len(rows),
        "items": selected,
        "checked_at": now_iso(),
    }
