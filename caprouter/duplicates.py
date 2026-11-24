"""Read-only duplicate detection across skill/plugin roots."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .paths import (
    config_path,
    default_plugin_roots,
    default_skill_roots,
    installed_root,
    registry_path,
)
from .scan import scan_plugin_root, scan_skill_root
from .util import now_iso, read_json, write_json_atomic
from .workspace import refresh_registry

DUPLICATES_SCHEMA = "caprouter.duplicates.v1"


def _managed(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def find_duplicates(workspace: Path | str) -> dict[str, Any]:
    workspace = Path(workspace)
    root = installed_root(workspace)
    config = read_json(config_path(workspace), {})
    config = config if isinstance(config, dict) else {}
    skill_roots = config.get("skill_roots")
    plugin_roots = config.get("plugin_roots")
    skills = (
        [Path(item) for item in skill_roots]
        if isinstance(skill_roots, list)
        else [Path(item) for item in default_skill_roots(workspace)]
    )
    plugins = (
        [Path(item) for item in plugin_roots]
        if isinstance(plugin_roots, list)
        else [Path(item) for item in default_plugin_roots(workspace)]
    )
    installed_skills = root / "skills"
    installed_plugins = root / "plugins"
    if installed_skills.is_dir() and installed_skills not in skills:
        skills.insert(0, installed_skills)
    if installed_plugins.is_dir() and installed_plugins not in plugins:
        plugins.insert(0, installed_plugins)

    raw_entries: list[dict[str, Any]] = []
    seen_roots: set[str] = set()
    for skill_root in skills:
        key = str(skill_root).lower()
        if key in seen_roots:
            continue
        seen_roots.add(key)
        raw_entries.extend(scan_skill_root(skill_root))
    for plugin_root in plugins:
        key = str(plugin_root).lower()
        if key in seen_roots:
            continue
        seen_roots.add(key)
        raw_entries.extend(scan_plugin_root(plugin_root))

    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for entry in raw_entries:
        entry_type = str(entry.get("type") or "")
        if entry_type not in {"skill", "plugin"}:
            continue
        name = str(entry.get("name") or "").strip().lower()
        if not name:
            continue
        groups.setdefault((entry_type, name), []).append(entry)

    rows: list[dict[str, Any]] = []
    for (entry_type, name), items in sorted(groups.items()):
        if len(items) < 2:
            continue
        enriched: list[dict[str, Any]] = []
        for entry in items:
            path = Path(str(entry.get("path") or ""))
            enriched.append(
                {
                    "id": entry.get("id"),
                    "name": entry.get("name"),
                    "path": str(path),
                    "hash": entry.get("hash"),
                    "status": entry.get("status"),
                    "managed": _managed(path, root),
                }
            )
        winner = next((item for item in enriched if item["managed"]), enriched[0])
        rows.append(
            {
                "type": entry_type,
                "name": name,
                "count": len(enriched),
                "same_content": len({item["hash"] for item in enriched}) == 1,
                "winner": winner["path"],
                "items": enriched,
            }
        )

    registry = read_json(registry_path(workspace), {})
    dropped = registry.get("duplicates") if isinstance(registry, dict) else []
    return {
        "schema": DUPLICATES_SCHEMA,
        "count": len(rows),
        "groups": rows,
        "dropped": dropped or [],
    }


def prefer_duplicate(workspace: Path | str, path: Path | str) -> dict[str, Any]:
    """Move the root containing *path* to the front of the configured roots.

    This changes root priority only; it never deletes or moves a skill.
    """
    workspace = Path(workspace)
    target = Path(path).expanduser().resolve()
    if not target.exists():
        raise ValueError(f"path does not exist: {target}")
    config = read_json(config_path(workspace), {})
    if not isinstance(config, dict):
        raise ValueError(f"workspace is not initialized: {workspace}")
    for key in ("skill_roots", "plugin_roots"):
        roots = [str(item) for item in (config.get(key) or [])]
        match: str | None = None
        for root in roots:
            try:
                target.relative_to(Path(root).expanduser().resolve())
                match = root
                break
            except (ValueError, OSError):
                continue
        if match is None:
            continue
        roots.remove(match)
        roots.insert(0, match)
        config[key] = roots
        config["updated_at"] = now_iso()
        write_json_atomic(config_path(workspace), config)
        refresh_registry(workspace, config)
        return {
            "schema": "caprouter.duplicates-prefer.v1",
            "status": "preferred",
            "path": str(target),
            "root": match,
            "config_key": key,
        }
    raise ValueError(f"path is not under a configured skill/plugin root: {target}")
