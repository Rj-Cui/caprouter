from __future__ import annotations

import json
import shutil
from collections import deque
from pathlib import Path
from typing import Any

from .audit import audit_path
from .paths import config_path, registry_path, runs_dir
from .state import read_state, state_path
from .util import read_json


def tail_audit(limit: int = 20) -> list[dict[str, Any]]:
    path = audit_path()
    if not path.exists() or limit <= 0:
        return []
    lines: deque[str] = deque(maxlen=limit)
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                lines.append(line)
    except OSError:
        return []
    events: list[dict[str, Any]] = []
    for line in lines:
        try:
            data = json.loads(line)
        except Exception:
            continue
        if isinstance(data, dict):
            events.append(data)
    return events


def _agent_status(entry: dict[str, Any]) -> dict[str, Any]:
    name = str(entry.get("name") or "")
    path = Path(str(entry.get("path") or ""))
    available = path.exists() or bool(shutil.which(name))
    return {
        "id": entry.get("id"),
        "name": name,
        "canonical": entry.get("canonical") or name,
        "path": str(path),
        "available": available,
        "capabilities": entry.get("capabilities") or [],
        "status": entry.get("status"),
    }


def collect_status(workspace: Path | str, *, audit_limit: int = 10) -> dict[str, Any]:
    workspace = Path(workspace)
    config = read_json(config_path(workspace), {})
    registry = read_json(registry_path(workspace), {})
    entries = [item for item in (registry.get("entries") or []) if isinstance(item, dict)]
    agents = [_agent_status(item) for item in entries if item.get("type") == "cli-agent"]

    run_items: list[dict[str, Any]] = []
    root = runs_dir(workspace)
    if root.is_dir():
        for path in sorted(root.iterdir(), reverse=True)[:5]:
            if not path.is_dir():
                continue
            # execute_plan writes run.json; dispatch/dry-run writes summary.json.
            summary = read_json(path / "run.json", {})
            if not isinstance(summary, dict) or not summary:
                summary = read_json(path / "summary.json", {})
            if not isinstance(summary, dict):
                summary = {}
            run_items.append(
                {
                    "run_dir": str(path),
                    "task_id": summary.get("task_id"),
                    "status": summary.get("status"),
                    "dry_run": summary.get("dry_run"),
                    "nodes": len(summary.get("nodes") or []),
                    "created_at": summary.get("created_at"),
                }
            )

    state = read_state()
    return {
        "schema": "caprouter.status.v1",
        "workspace": str(workspace),
        "state": {"enabled": state.get("enabled", True), "path": str(state_path()), "updated_by": state.get("updated_by")},
        "delivery": config.get("delivery") if isinstance(config, dict) else None,
        "registry": {
            "entries": len(entries),
            "stats": registry.get("stats") if isinstance(registry, dict) else {},
            "stale": None,
        },
        "agents": agents,
        "recent_runs": run_items,
        "audit": {"path": str(audit_path()), "tail": tail_audit(audit_limit)},
    }
