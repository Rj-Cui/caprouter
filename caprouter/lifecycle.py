"""Install lifecycle: uninstall and rollback.

Uninstall moves an installed capability into
``.caprouter/installed/.trash/`` and removes its registry/overlay entry.
Rollback restores the latest uninstalled copy and its Card. Both default to
preview; the CLI requires ``--yes`` to modify anything.
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any

from .capabilities import load_overlay, overlay_path
from .paths import installed_root
from .util import (
    append_jsonl,
    hash_tree,
    now_iso,
    read_json,
    read_jsonl,
    safe_name,
    write_json_atomic,
)
from .workspace import refresh_registry

UNINSTALL_SCHEMA = "caprouter.uninstall.v1"
ROLLBACK_SCHEMA = "caprouter.rollback.v1"


def _trash_stamp() -> str:
    """Sortable millisecond stamp: zero-padded so name order equals time order."""
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{int(time.time() * 1000) % 1000:03d}"

def _bus_dir(workspace: Path | str) -> Path:
    return Path(workspace) / ".caprouter"


def uninstalled_path(workspace: Path | str) -> Path:
    return _bus_dir(workspace) / "uninstalled.jsonl"


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def _registry_entry(workspace: Path | str, capability_id: str) -> dict[str, Any] | None:
    data = read_json(_bus_dir(workspace) / "registry.json", {})
    entries = data.get("entries") if isinstance(data, dict) else None
    for entry in entries or []:
        if isinstance(entry, dict) and str(entry.get("id")) == capability_id:
            return entry
    return None


def _card_for(workspace: Path | str, capability_id: str) -> dict[str, Any] | None:
    card = load_overlay(workspace)["cards"].get(capability_id)
    return dict(card) if isinstance(card, dict) else None


def _write_overlay_card(workspace: Path | str, capability_id: str, card: dict[str, Any] | None) -> None:
    overlay = load_overlay(workspace)
    cards = overlay["cards"]
    if card is None:
        cards.pop(capability_id, None)
    else:
        cards[capability_id] = card
    payload = {
        "schema": "caprouter.capability-cards.v1",
        "defaults": overlay.get("defaults") or {},
        "cards": cards,
    }
    write_json_atomic(overlay_path(workspace), payload)


def uninstall_capability(
    workspace: Path | str,
    capability_id: str,
    *,
    dry_run: bool = True,
) -> dict[str, Any]:
    workspace = Path(workspace)
    capability_id = str(capability_id or "").strip()
    if not capability_id:
        raise ValueError("capability id is required")
    entry = _registry_entry(workspace, capability_id)
    if not entry:
        raise ValueError(f"capability is not installed: {capability_id}")
    target = Path(str(entry.get("path") or "")).expanduser()
    root = installed_root(workspace)
    if not target.is_dir() or not _inside(target, root):
        raise ValueError(f"refusing to uninstall outside installed/: {target}")
    trash_root = root / ".trash"
    stamp = _trash_stamp()
    trash = trash_root / f"{safe_name(capability_id)}-{stamp}"
    # Hash before moving: the trash copy later gains uninstall.json, which must
    # not make a host-side copy look "locally modified".
    content_hash = hash_tree(target)
    record = {
        "action": "uninstall",
        "id": capability_id,
        "original_path": str(target.resolve()),
        "trash_path": str(trash),
        "content_hash": content_hash,
        "entry": entry,
        "card": _card_for(workspace, capability_id),
        "uninstalled_at": now_iso(),
    }
    if dry_run:
        return {
            "schema": UNINSTALL_SCHEMA,
            "status": "preview",
            "dry_run": True,
            "id": capability_id,
            "path": str(target),
            "trash_path": str(trash),
            "content_hash": content_hash,
        }

    trash_root.mkdir(parents=True, exist_ok=True)
    shutil.move(str(target), str(trash))
    write_json_atomic(trash / "uninstall.json", record)
    _write_overlay_card(workspace, capability_id, None)
    config = read_json(_bus_dir(workspace) / "config.json", {})
    refresh_registry(workspace, config if isinstance(config, dict) else None)
    append_jsonl(uninstalled_path(workspace), record)
    return {
        "schema": UNINSTALL_SCHEMA,
        "status": "uninstalled",
        "dry_run": False,
        "id": capability_id,
        "path": str(target),
        "trash_path": str(trash),
        "content_hash": content_hash,
    }


def _latest_uninstall(workspace: Path | str, capability_id: str) -> dict[str, Any] | None:
    rows = read_jsonl(uninstalled_path(workspace))
    rolled = {
        str(row.get("ref") or "")
        for row in rows
        if row.get("action") == "rollback"
    }
    for row in reversed(rows):
        if (
            row.get("action") == "uninstall"
            and str(row.get("id")) == capability_id
            and str(row.get("uninstalled_at")) not in rolled
        ):
            return row
    return None


def rollback_capability(
    workspace: Path | str,
    capability_id: str,
    *,
    dry_run: bool = True,
    force: bool = False,
) -> dict[str, Any]:
    workspace = Path(workspace)
    capability_id = str(capability_id or "").strip()
    if not capability_id:
        raise ValueError("capability id is required")
    record = _latest_uninstall(workspace, capability_id)
    if not record:
        raise ValueError(f"no uninstall record for: {capability_id}")
    trash = Path(str(record.get("trash_path") or ""))
    target = Path(str(record.get("original_path") or ""))
    root = installed_root(workspace)
    if not _inside(trash, root / ".trash"):
        # uninstalled.jsonl is a data file; a tampered row must not point the
        # restore at an arbitrary directory.
        raise ValueError(f"refusing to restore from outside installed/.trash: {trash}")
    if not trash.is_dir():
        raise ValueError(f"uninstall trash is missing: {trash}")
    if not _inside(target, root):
        raise ValueError(f"rollback target is outside installed/: {target}")
    if target.exists() and not force:
        raise ValueError(f"rollback target already exists: {target}; pass --force")

    stamp = _trash_stamp()
    displaced = root / ".trash" / f"{safe_name(capability_id)}-pre-rollback-{stamp}"
    result = {
        "schema": ROLLBACK_SCHEMA,
        "status": "preview",
        "dry_run": True,
        "id": capability_id,
        "path": str(target),
        "trash_path": str(trash),
        "displaced_path": str(displaced) if target.exists() else None,
    }
    if dry_run:
        return result

    if target.exists():
        displaced.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(target), str(displaced))
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(trash), str(target))
    (target / "uninstall.json").unlink(missing_ok=True)
    card = record.get("card") if isinstance(record.get("card"), dict) else None
    if card is not None:
        _write_overlay_card(workspace, capability_id, card)
    config = read_json(_bus_dir(workspace) / "config.json", {})
    refresh_registry(workspace, config if isinstance(config, dict) else None)
    append_jsonl(
        uninstalled_path(workspace),
        {
            "action": "rollback",
            "id": capability_id,
            "ref": record.get("uninstalled_at"),
            "path": str(target),
            "rolled_back_at": now_iso(),
        },
    )
    result.update(
        {
            "status": "rolled_back",
            "dry_run": False,
            "displaced_path": str(displaced) if displaced.exists() else None,
        }
    )
    return result


VERSIONS_SCHEMA = "caprouter.versions.v1"
UPDATE_SCHEMA = "caprouter.update.v1"


def updates_path(workspace: Path | str) -> Path:
    return _bus_dir(workspace) / "updates.jsonl"


def _manifest_for(path: Path) -> dict[str, Any]:
    data = read_json(path / "manifest.json", {})
    return data if isinstance(data, dict) else {}


def list_versions(workspace: Path | str, capability_id: str) -> dict[str, Any]:
    workspace = Path(workspace)
    entry = _registry_entry(workspace, capability_id)
    if entry is None:
        raise ValueError(f"capability is not installed: {capability_id}")
    target = Path(str(entry.get("path") or "")).expanduser()
    root = installed_root(workspace)
    if not target.is_dir() or not _inside(target, root):
        raise ValueError(f"capability is not managed: {capability_id}")
    current = _manifest_for(target)
    rows: list[dict[str, Any]] = [
        {
            "version": current.get("version"),
            "hash": current.get("hash"),
            "source_hash": current.get("source_hash"),
            "installed_at": current.get("installed_at"),
            "path": str(target),
            "trash_path": None,
            "current": True,
        }
    ]
    trash_root = root / ".trash"
    if trash_root.is_dir():
        for candidate in sorted(trash_root.iterdir()):
            if not candidate.is_dir():
                continue
            manifest = _manifest_for(candidate)
            if str(manifest.get("id") or "") != capability_id:
                continue
            rows.append(
                {
                    "version": manifest.get("version"),
                    "hash": manifest.get("hash"),
                    "source_hash": manifest.get("source_hash"),
                    "installed_at": manifest.get("installed_at"),
                    "path": str(candidate),
                    "trash_path": str(candidate),
                    "current": False,
                }
            )
    rows.sort(key=lambda item: (bool(item["current"]), str(item.get("installed_at") or "")), reverse=True)
    return {
        "schema": VERSIONS_SCHEMA,
        "id": capability_id,
        "current": current,
        "versions": rows,
    }


def rollback_to_version(
    workspace: Path | str,
    capability_id: str,
    *,
    version: str | None = None,
    hash_value: str | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    if not version and not hash_value:
        raise ValueError("version or hash is required")
    workspace = Path(workspace)
    entry = _registry_entry(workspace, capability_id)
    if entry is None:
        raise ValueError(f"capability is not installed: {capability_id}")
    target = Path(str(entry.get("path") or "")).expanduser()
    root = installed_root(workspace)
    if not _inside(target, root):
        raise ValueError(f"refusing to rollback outside installed/: {target}")
    candidates: list[tuple[float, str, Path, dict[str, Any]]] = []
    trash_root = root / ".trash"
    if trash_root.is_dir():
        for candidate in trash_root.iterdir():
            if not candidate.is_dir():
                continue
            manifest = _manifest_for(candidate)
            if str(manifest.get("id") or "") != capability_id:
                continue
            if version and str(manifest.get("version")) != str(version):
                continue
            if hash_value and str(manifest.get("hash")) != str(hash_value):
                continue
            try:
                # Order by when the copy entered the trash, not by its name:
                # older releases and pre-rollback names do not sort chronologically.
                stamp = candidate.stat().st_mtime
            except OSError:
                stamp = 0.0
            candidates.append((stamp, candidate.name, candidate, manifest))
    if not candidates:
        raise ValueError(
            f"no matching version in trash for {capability_id} "
            f"(version={version or '*'}, hash={hash_value or '*'})"
        )
    candidate, manifest = sorted(candidates, key=lambda item: (item[0], item[1]))[-1][2:]
    stamp = _trash_stamp()
    displaced = trash_root / f"{safe_name(capability_id)}-pre-rollback-{stamp}"
    result = {
        "schema": ROLLBACK_SCHEMA,
        "status": "preview",
        "dry_run": True,
        "id": capability_id,
        "path": str(target),
        "restore_from": str(candidate),
        "version": manifest.get("version"),
        "hash": manifest.get("hash"),
        "displaced_path": str(displaced) if target.exists() else None,
    }
    if dry_run:
        return result
    if target.exists():
        displaced.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(target), str(displaced))
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(candidate), str(target))
    (target / "uninstall.json").unlink(missing_ok=True)
    config = read_json(_bus_dir(workspace) / "config.json", {})
    refresh_registry(workspace, config if isinstance(config, dict) else None)
    append_jsonl(
        updates_path(workspace),
        {
            "action": "rollback_version",
            "id": capability_id,
            "version": manifest.get("version"),
            "hash": manifest.get("hash"),
            "path": str(target),
            "rolled_back_at": now_iso(),
        },
    )
    result.update(
        {
            "status": "rolled_back",
            "dry_run": False,
            "displaced_path": str(displaced) if displaced.exists() else None,
        }
    )
    return result


def update_source(
    workspace: Path | str,
    capability_id: str,
    source: Path | str,
    *,
    approve: bool = False,
    generate_card: bool = True,
    **install_options: Any,
) -> dict[str, Any]:
    """Preview or apply an update for an already-managed capability."""
    from .install import install_source, preview_install

    workspace = Path(workspace)
    entry = _registry_entry(workspace, capability_id)
    if entry is None:
        raise ValueError(f"capability is not installed: {capability_id}")
    target = Path(str(entry.get("path") or "")).expanduser()
    root = installed_root(workspace)
    if not _inside(target, root):
        raise ValueError(f"capability is not managed; adopt it first: {capability_id}")
    preview = preview_install(workspace, source)
    expected_id = f"{preview.get('type')}:{preview.get('name')}"
    if expected_id != capability_id:
        raise ValueError(f"source id mismatch: {expected_id} != {capability_id}")
    current = _manifest_for(target)
    result: dict[str, Any] = {
        "schema": UPDATE_SCHEMA,
        "status": "preview",
        "id": capability_id,
        "current": current,
        "preview": preview,
    }
    if not approve:
        return result
    if preview.get("source_hash") and current.get("source_hash") == preview.get("source_hash"):
        result["status"] = "up_to_date"
        return result
    installed = install_source(
        workspace,
        source,
        approve=True,
        force=True,
        generate_card=generate_card,
        **install_options,
    )
    append_jsonl(
        updates_path(workspace),
        {
            "action": "update",
            "id": capability_id,
            "from_version": current.get("version"),
            "to_version": preview.get("version"),
            "from_hash": current.get("hash"),
            "to_hash": installed.get("manifest", {}).get("hash"),
            "status": installed.get("status"),
            "updated_at": now_iso(),
        },
    )
    if installed.get("status") != "installed":
        return installed
    return {
        **installed,
        "schema": UPDATE_SCHEMA,
        "status": "updated",
        "previous": current,
    }
