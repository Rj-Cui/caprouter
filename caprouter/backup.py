from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from .paths import config_path, plans_dir, registry_path
from .state import state_path
from .util import now_iso, read_json, safe_name, write_json_atomic


def backups_dir(workspace: Path | str) -> Path:
    return Path(workspace) / "backups"


def create_backup(workspace: Path | str, label: str | None = None) -> dict[str, Any]:
    workspace = Path(workspace)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"{stamp}-{safe_name(label, fallback='backup')}" if label else stamp
    dest = backups_dir(workspace) / name
    dest.mkdir(parents=True, exist_ok=False)

    manifest: dict[str, Any] = {
        "schema": "caprouter.backup.v1",
        "created_at": now_iso(),
        "workspace": str(workspace),
        "files": [],
    }

    sources = {
        "config": config_path(workspace),
        "registry": registry_path(workspace),
        "state": state_path(),
    }
    for key, source in sources.items():
        if not source.exists():
            continue
        target = dest / f"{key}{source.suffix}"
        shutil.copy2(source, target)
        manifest["files"].append({"name": key, "source": str(source), "file": target.name})

    source_plans = plans_dir(workspace)
    if source_plans.is_dir():
        shutil.copytree(source_plans, dest / "plans")
        manifest["files"].append({"name": "plans", "source": str(source_plans), "file": "plans"})

    write_json_atomic(dest / "manifest.json", manifest)
    return {
        "schema": "caprouter.backup.v1",
        "backup_dir": str(dest),
        "files": [item["file"] for item in manifest["files"]],
        "created_at": manifest["created_at"],
    }


def list_backups(workspace: Path | str) -> list[dict[str, Any]]:
    root = backups_dir(workspace)
    if not root.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(root.iterdir(), reverse=True):
        if not path.is_dir():
            continue
        manifest = read_json(path / "manifest.json", {})
        items.append(
            {
                "backup_dir": str(path),
                "created_at": manifest.get("created_at"),
                "files": [item.get("file") for item in (manifest.get("files") or []) if isinstance(item, dict)],
            }
        )
    return items


def restore_backup(workspace: Path | str, backup_dir: Path | str, *, force: bool = False) -> dict[str, Any]:
    workspace = Path(workspace)
    backup = Path(backup_dir)
    manifest = read_json(backup / "manifest.json")
    if not isinstance(manifest, dict):
        raise ValueError(f"invalid backup manifest: {backup / 'manifest.json'}")
    if not force:
        raise ValueError("restore is destructive; pass --force to confirm")

    restored: list[str] = []
    for item in manifest.get("files") or []:
        if not isinstance(item, dict):
            continue
        key = item.get("name")
        if key == "config":
            shutil.copy2(backup / item["file"], config_path(workspace))
            restored.append("config")
        elif key == "registry":
            shutil.copy2(backup / item["file"], registry_path(workspace))
            restored.append("registry")
        elif key == "state":
            target = state_path()
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup / item["file"], target)
            restored.append("state")
        elif key == "plans":
            target = plans_dir(workspace)
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(backup / item["file"], target)
            restored.append("plans")

    return {
        "schema": "caprouter.backup.v1",
        "restored": restored,
        "backup_dir": str(backup),
        "workspace": str(workspace),
    }
