"""Adopt existing (unmanaged) skills/plugins into the managed installed/ tree.

Adoption is copy-only: the original path is never modified, moved or deleted.
Use ``uninstall`` on the adopted copy to release it; the original stays.
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any

from .cards import generate_draft, write_draft
from .install import (
    IGNORE,
    _ensure_config_roots,
    _inspect_tree,
    _refresh_registry,
    manifest_path,
    type_dir,
)
from .paths import config_path, installed_root, registry_path
from .util import hash_tree, now_iso, read_json, safe_name, write_json_atomic

ADOPT_SCHEMA = "caprouter.adopt.v1"
UNMANAGED_SCHEMA = "caprouter.unmanaged.v1"
BACKUP_SCHEMA = "caprouter.skill-backup.v1"


def _registry_entries(workspace: Path | str) -> list[dict[str, Any]]:
    data = read_json(registry_path(workspace), {})
    entries = data.get("entries") if isinstance(data, dict) else None
    return [item for item in (entries or []) if isinstance(item, dict)]


def _entry(workspace: Path | str, capability_id: str) -> dict[str, Any] | None:
    for entry in _registry_entries(workspace):
        if str(entry.get("id")) == capability_id:
            return entry
    return None


def _inside(path: Path, root: Path) -> bool:
    try:
        path.expanduser().resolve().relative_to(root.expanduser().resolve())
        return True
    except (ValueError, OSError):
        return False


def list_unmanaged(workspace: Path | str) -> dict[str, Any]:
    workspace = Path(workspace)
    root = installed_root(workspace)
    rows: list[dict[str, Any]] = []
    for entry in _registry_entries(workspace):
        if str(entry.get("type")) not in {"skill", "plugin"}:
            continue
        path = Path(str(entry.get("path") or "")).expanduser()
        if not path.is_dir() or _inside(path, root):
            continue
        rows.append(
            {
                "id": entry.get("id"),
                "type": entry.get("type"),
                "name": entry.get("name"),
                "path": str(path),
                "root": entry.get("root"),
                "status": entry.get("status"),
                "hash": entry.get("hash"),
                "description": entry.get("description"),
            }
        )
    rows.sort(key=lambda item: str(item.get("id")))
    return {"schema": UNMANAGED_SCHEMA, "count": len(rows), "entries": rows}


def adopt_capability(
    workspace: Path | str,
    capability_id: str,
    *,
    dry_run: bool = True,
    name: str | None = None,
    force: bool = False,
    allow_symlinks: bool = False,
    generate_card: bool = True,
) -> dict[str, Any]:
    workspace = Path(workspace)
    entry = _entry(workspace, capability_id)
    if entry is None:
        raise ValueError(f"capability not found: {capability_id}")
    entry_type = str(entry.get("type") or "")
    if entry_type not in {"skill", "plugin"}:
        raise ValueError(f"only skill/plugin can be adopted: {capability_id} ({entry_type})")
    source = Path(str(entry.get("path") or "")).expanduser()
    if not source.is_dir():
        raise ValueError(f"source path is missing: {source}")
    managed_root = installed_root(workspace)
    if _inside(source, managed_root):
        return {
            "schema": ADOPT_SCHEMA,
            "status": "already_managed",
            "id": capability_id,
            "path": str(source),
        }

    resolved_name = safe_name(name or str(entry.get("name") or source.name))
    target = type_dir(workspace, entry_type) / resolved_name
    tree = _inspect_tree(source)
    if tree["symlinks"] and not allow_symlinks:
        raise ValueError(
            f"source contains {len(tree['symlinks'])} symlink(s); "
            "pass --allow-symlinks to copy them as links"
        )
    digest = hash_tree(source)
    preview = {
        "schema": ADOPT_SCHEMA,
        "status": "preview" if dry_run else "adopting",
        "dry_run": dry_run,
        "id": capability_id,
        "type": entry_type,
        "name": resolved_name,
        "source": str(source),
        "target": str(target),
        "hash": digest,
        **{key: tree[key] for key in ("file_count", "total_bytes", "executables", "symlinks", "warnings")},
        "note": "the original path is copied, never moved or modified",
    }
    if dry_run:
        return preview
    if target.exists() and not force:
        return {
            "schema": ADOPT_SCHEMA,
            "status": "exists",
            "id": capability_id,
            "target": str(target),
            "reason": "already adopted; pass --force to replace the managed copy",
        }

    trash: Path | None = None
    if target.exists():
        trash = managed_root / ".trash" / f"adopt-{resolved_name}-{int(time.time())}"
        trash.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(target), str(trash))
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(source, target, ignore=IGNORE, symlinks=allow_symlinks)
        target_hash = hash_tree(target)
        if target_hash != digest:
            raise ValueError("adopted copy hash does not match the source")
        installed_tree = _inspect_tree(target)
        manifest = {
            "schema": "caprouter.installed.v1",
            "id": f"{entry_type}:{resolved_name}",
            "type": entry_type,
            "name": resolved_name,
            "version": str(entry.get("version") or ""),
            "source": {"kind": "adopted", "path": str(source)},
            "adopted_from": str(source),
            "original_hash": digest,
            "installed_at": now_iso(),
            "installed_by": "caprouter adopt",
            "approved": True,
            "managed": True,
            "hash": target_hash,
            "permissions": entry.get("permissions") or {},
            "file_count": installed_tree["file_count"],
            "total_bytes": installed_tree["total_bytes"],
            "executables": installed_tree["executables"],
            "symlinks": installed_tree["symlinks"],
            "warnings": installed_tree["warnings"],
        }
        write_json_atomic(manifest_path(target), manifest)

        config = read_json(config_path(workspace), {})
        changed: list[str] = []
        if isinstance(config, dict):
            changed = _ensure_config_roots(config, workspace, entry_type)
            if changed:
                config["updated_at"] = now_iso()
                write_json_atomic(config_path(workspace), config)
        _refresh_registry(workspace, config if isinstance(config, dict) else {})
        draft_file = None
        if generate_card:
            draft = generate_draft(
                workspace, path=target, type_hint=entry_type, name=resolved_name
            )
            draft_file = str(write_draft(workspace, draft))
        adopted = _entry(workspace, manifest["id"])
        return {
            "schema": ADOPT_SCHEMA,
            "status": "adopted",
            "dry_run": False,
            "id": manifest["id"],
            "source": str(source),
            "target": str(target),
            "manifest": manifest,
            "manifest_path": str(manifest_path(target)),
            "trash_path": str(trash) if trash else None,
            "registry_entry": adopted,
            "draft_path": draft_file,
            "config_roots_changed": changed,
        }
    except Exception:
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        if trash is not None and trash.exists():
            shutil.move(str(trash), str(target))
        raise


def backup_capability(
    workspace: Path | str,
    capability_id: str,
    *,
    out: Path | str | None = None,
) -> dict[str, Any]:
    workspace = Path(workspace)
    entry = _entry(workspace, capability_id)
    if entry is None:
        raise ValueError(f"capability not found: {capability_id}")
    source = Path(str(entry.get("path") or "")).expanduser()
    if not source.is_dir():
        raise ValueError(f"source path is missing: {source}")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    destination = (
        Path(out).expanduser()
        if out
        else workspace / ".caprouter" / "backups" / "skills" / f"{safe_name(str(entry.get('name') or source.name))}-{stamp}"
    )
    if destination.exists():
        raise ValueError(f"backup destination exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination, ignore=IGNORE, symlinks=True)
    manifest = {
        "schema": BACKUP_SCHEMA,
        "id": capability_id,
        "source": str(source),
        "backup": str(destination),
        "hash": hash_tree(source),
        "created_at": now_iso(),
    }
    write_json_atomic(destination / "backup.json", manifest)
    return {
        "schema": BACKUP_SCHEMA,
        "status": "backed_up",
        "id": capability_id,
        "source": str(source),
        "backup": str(destination),
        "hash": manifest["hash"],
    }
