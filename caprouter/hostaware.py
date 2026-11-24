"""Host awareness for discovered capabilities.

CapRouter scans the whole machine, so a workspace routinely lists skills that
belong to a *different* host (Cherry Studio, Claude Code, ...). A host asking
``capability_search`` must not be told that such a skill is usable: the path
exists, but the host will never load it.

This module turns a discovered path into a small contract:

``{"current_host", "owner_host", "loadable", "reason", "hint"}``

``loadable`` is ``True`` when the path sits under the current host's own skill
root, ``False`` when it provably belongs to another host or is only managed by
CapRouter, and ``None`` when ownership cannot be inferred (CLI use, or an
unrecognized directory).
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from .util import hash_tree

HOST_LABELS = {
    "deepseek-harness": "DeepSeek Harness",
    "claude-code": "Claude Code",
    "claude-desktop": "Claude Desktop",
    "cherry": "Cherry Studio",
    "cursor": "Cursor",
    "codex": "Codex",
    "gemini": "Gemini CLI",
    "vscode": "VS Code",
}

MANAGED = "caprouter-managed"

_TYPE_DIRS = {"skill": "skills", "plugin": "plugins", "project": "projects"}

_ALIASES = {
    "dsh": "deepseek-harness",
    "deepseek": "deepseek-harness",
    "deepseek-harness": "deepseek-harness",
    "deepseek harness": "deepseek-harness",
    "claude": "claude-code",
    "claude-code": "claude-code",
    "claude code": "claude-code",
    "claude-desktop": "claude-desktop",
    "cherry": "cherry",
    "cherrystudio": "cherry",
    "cherry studio": "cherry",
    "cursor": "cursor",
    "codex": "codex",
    "gemini": "gemini",
    "gemini-cli": "gemini",
    "vscode": "vscode",
    "vs code": "vscode",
}

# Order matters: the managed directory is checked before generic host markers.
_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (MANAGED, ("/.caprouter/installed/",)),
    ("deepseek-harness", ("/.dsh/skills", "/.agents/skills")),
    ("claude-code", ("/.claude/skills", "/.claude-plugin/", "/.claude/")),
    ("claude-desktop", ("/claude/claude_desktop_config.json",)),
    ("cherry", ("/cherrystudio/",)),
    ("cursor", ("/.cursor/",)),
    ("codex", ("/.codex/",)),
    ("gemini", ("/.gemini/",)),
    ("vscode", ("/appdata/roaming/code/user/",)),
)


def _norm(value: Any) -> str:
    return str(value).replace("\\", "/").lower()


def normalize_host(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return "unknown"
    if text in _ALIASES:
        return _ALIASES[text]
    return text if text in HOST_LABELS else "unknown"


def host_roots(
    host: Any,
    *,
    workspace: Path | str | None = None,
    home: Path | str | None = None,
) -> list[Path]:
    """Skill roots the given host scans, in priority order.

    ``CAPROUTER_HOST_SKILLS`` overrides everything (used by tests and by users
    who keep skills somewhere unusual).
    """
    override = os.environ.get("CAPROUTER_HOST_SKILLS")
    if override:
        return [Path(override).expanduser()]
    host = normalize_host(host)
    home_path = Path(home).expanduser() if home else Path.home()
    ws = Path(workspace).expanduser() if workspace else None
    roots: list[Path] = []
    if host == "deepseek-harness":
        dsh_home = Path(os.environ.get("DSH_HOME") or (home_path / ".dsh")).expanduser()
        roots.append(dsh_home / "skills")
        if ws:
            roots.extend([ws / ".dsh" / "skills", ws / ".agents" / "skills"])
    elif host == "claude-code":
        roots.append(home_path / ".claude" / "skills")
        if ws:
            roots.append(ws / ".claude" / "skills")
    elif host == "cherry":
        appdata = Path(os.environ.get("APPDATA") or (home_path / "AppData" / "Roaming"))
        roots.append(appdata / "CherryStudio" / "Data" / "Skills")
    elif host == "cursor":
        roots.append(home_path / ".cursor" / "skills")
    elif host == "codex":
        roots.append(home_path / ".codex" / "skills")
    elif host == "gemini":
        roots.append(home_path / ".gemini" / "skills")
    elif host == "vscode":
        roots.append(home_path / ".vscode" / "skills")
    return roots


def guess_owner_host(path: Any) -> str | None:
    text = _norm(path)
    for owner, markers in _MARKERS:
        if any(marker in text for marker in markers):
            return owner
    return None


def classify_path(
    path: Any,
    *,
    host: Any,
    workspace: Path | str | None = None,
) -> dict[str, Any]:
    current = normalize_host(host)
    info: dict[str, Any] = {
        "current_host": current,
        "owner_host": None,
        "loadable": None,
        "reason": "unknown",
        "hint": None,
    }
    if not path:
        info["reason"] = "no_path"
        return info
    candidate = Path(str(path))
    owner = guess_owner_host(candidate)
    info["owner_host"] = owner
    if not candidate.exists():
        info["loadable"] = False
        info["reason"] = "path_missing"
        return info
    label = HOST_LABELS.get(current, current)
    if owner == MANAGED:
        # Managed by CapRouter: loadable only if the same skill name also exists
        # under the current host's skill root (the link_host / --link-skills copy).
        for candidate_root in host_roots(current, workspace=workspace):
            linked = candidate_root / candidate.name
            if linked.is_dir() and (linked / "SKILL.md").is_file():
                info["loadable"] = True
                info["reason"] = "managed_linked"
                info["linked_path"] = str(linked)
                return info
        info["loadable"] = False
        info["reason"] = "managed_not_linked"
        info["hint"] = (
            f"Managed by CapRouter but not linked into {label} yet; run "
            f"caprouter setup --host {current} --link-skills, or keep link_host enabled in capability_install"
        )
        return info
    if current == "unknown":
        info["reason"] = "unknown_host"
        return info
    if owner is None:
        info["reason"] = "unrecognized_root"
        return info
    if owner == current:
        info["loadable"] = True
        info["reason"] = "under_host_root"
        return info
    owner_label = HOST_LABELS.get(owner, owner)
    info["loadable"] = False
    info["reason"] = "other_host"
    info["hint"] = (
        f"This capability belongs to {owner_label}, which {label} cannot load directly; run "
        f"caprouter setup --host {current} --link-skills to migrate it, or use a copy for this host"
    )
    return info


def annotate_record(
    record: Any,
    *,
    host: Any,
    workspace: Path | str | None = None,
) -> Any:
    if not isinstance(record, dict):
        return record
    info = classify_path(record.get("path"), host=host, workspace=workspace)
    record["host"] = info
    health = record.get("health")
    if not isinstance(health, dict):
        health = {}
        record["health"] = health
    health["current_host"] = info["current_host"]
    health["loadable_by_host"] = info["loadable"]
    if info.get("hint"):
        health["host_hint"] = info["hint"]
    return record


def annotate_search_result(
    result: Any,
    *,
    host: Any,
    workspace: Path | str | None = None,
) -> Any:
    if not isinstance(result, dict):
        return result
    result["current_host"] = normalize_host(host)
    selected = result.get("selected")
    if isinstance(selected, dict):
        annotate_record(selected, host=host, workspace=workspace)
        host_info = selected.get("host") or {}
        if host_info.get("loadable") is False:
            result["host_warning"] = (
                f"The selected capability belongs to {HOST_LABELS.get(host_info.get('owner_host'), host_info.get('owner_host'))}, "
                f"which the current host cannot load; prefer candidates with health.loadable_by_host=true, "
                f"or run caprouter setup --host {normalize_host(host)} --link-skills to migrate it"
            )
    candidates = result.get("candidates")
    if isinstance(candidates, list):
        for item in candidates:
            annotate_record(item, host=host, workspace=workspace)
    return result


def annotate_card(card: Any, *, host: Any, workspace: Path | str | None = None) -> Any:
    return annotate_record(card, host=host, workspace=workspace)


def link_managed_skill(
    workspace: Path | str,
    capability_id: str,
    *,
    host: Any,
    force: bool = False,
    root: Path | str | None = None,
) -> dict[str, Any]:
    """Copy a CapRouter-managed skill into the current host's skill root.

    This is what makes "installed with CapRouter" actually loadable by the host
    that asked. Only skills have a portable host representation; plugins and
    projects are reported as skipped instead of being copied blindly.
    """
    current = normalize_host(host)
    result: dict[str, Any] = {"host": current, "linked": None, "root": None, "skipped": None}
    if current == "unknown":
        result["skipped"] = "unknown host"
        return result
    entry_type, _, name = str(capability_id or "").partition(":")
    if not name:
        result["skipped"] = "capability id must look like skill:<name>"
        return result
    if entry_type != "skill":
        result["skipped"] = "Only skills can be linked into a host skill root (plugins/projects are not copied)"
        return result
    source = Path(workspace).expanduser().resolve() / ".caprouter" / "installed" / _TYPE_DIRS["skill"] / name
    if not source.is_dir():
        result["skipped"] = f"Not installed: {source}"
        return result
    if not (source / "SKILL.md").is_file():
        result["skipped"] = "SKILL.md is missing, so the host will not load it"
        return result
    roots = [Path(root).expanduser()] if root else host_roots(current, workspace=workspace)
    if not roots:
        result["skipped"] = f"Unknown host {current} skill root"
        return result
    root_path = roots[0]
    root_path.mkdir(parents=True, exist_ok=True)
    target = root_path / name
    if target.exists():
        if not force:
            result["skipped"] = f"Already exists: {target} (force=True to replace)"
            return result
        shutil.rmtree(target)
    shutil.copytree(source, target)
    result["linked"] = str(target)
    result["root"] = str(root_path)
    return result


def unlink_managed_skill(
    workspace: Path | str,
    capability_id: str,
    *,
    host: Any,
    removed_path: Path | str | None = None,
    removed_hash: str | None = None,
    force: bool = False,
    root: Path | str | None = None,
) -> dict[str, Any]:
    """Remove the host-side copy after an uninstall.

    Deleting is only safe when the host copy still matches the uninstalled copy
    (``removed_hash`` preferred, otherwise the hash of ``removed_path``); a
    modified copy is kept and reported, so an uninstall never silently discards
    someone's edits. ``force=True`` overrides that guard.
    """
    current = normalize_host(host)
    result: dict[str, Any] = {
        "host": current,
        "path": None,
        "removed": False,
        "skipped": None,
    }
    if current == "unknown":
        result["skipped"] = "unknown host"
        return result
    entry_type, _, name = str(capability_id or "").partition(":")
    if entry_type != "skill" or not name:
        result["skipped"] = "Only skills are synced to a host skill root"
        return result
    roots = [Path(root).expanduser()] if root else host_roots(current, workspace=workspace)
    target: Path | None = None
    for candidate_root in roots:
        candidate = candidate_root / name
        if candidate.exists():
            target = candidate
            result["root"] = str(candidate_root)
            break
    if target is None:
        result["skipped"] = "No copy of this skill in the host skill root"
        return result
    result["path"] = str(target)
    expected = removed_hash
    if expected is None and removed_path:
        source = Path(removed_path)
        if source.is_dir():
            try:
                expected = hash_tree(source)
            except OSError as exc:
                result["skipped"] = f"Could not compare the host copy: {exc}"
                return result
    if not force and expected:
        try:
            if hash_tree(target) != expected:
                result["skipped"] = (
                    "Host copy differs from the installed copy (edited locally?); not deleted; pass force to delete"
                )
                return result
        except OSError as exc:
            result["skipped"] = f"Could not compare the host copy: {exc}"
            return result
    shutil.rmtree(target)
    result["removed"] = True
    return result


def prefer_host_loadable(
    result: Any,
    *,
    host: Any,
    workspace: Path | str | None = None,
) -> Any:
    """Annotate a search result, then rank loadable candidates first.

    When the top match belongs to another host but a loadable candidate exists,
    the selection is switched and the original choice is retained under
    ``selected_original`` so a reviewer can see what changed. When nothing is
    loadable the original selection stays and ``host_warning`` explains why.
    """
    if not isinstance(result, dict):
        return result
    annotate_search_result(result, host=host, workspace=workspace)
    candidates = result.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return result
    indexed = list(enumerate(candidates))

    def rank(pair: tuple[int, Any]) -> tuple[int, int]:
        index, item = pair
        loadable = (item.get("host") or {}).get("loadable") if isinstance(item, dict) else None
        if loadable is True:
            band = 0
        elif loadable is None:
            band = 1
        else:
            band = 2
        return band, index

    ordered = [item for _, item in sorted(indexed, key=rank)]
    result["candidates"] = ordered
    selected = result.get("selected")
    if not isinstance(selected, dict):
        return result
    if (selected.get("host") or {}).get("loadable") is True:
        return result
    replacement = next(
        (
            item
            for item in ordered
            if isinstance(item, dict) and (item.get("host") or {}).get("loadable") is True
        ),
        None,
    )
    if replacement is None:
        return result
    result["selected_original"] = {
        key: selected.get(key) for key in ("id", "name", "path", "score")
    }
    result["selected"] = replacement
    result["selection_adjusted"] = f"Switched to a host-loadable candidate: {replacement.get('id')}"
    if isinstance(replacement.get("score"), (int, float)):
        result["confidence_original"] = result.get("confidence")
        result["confidence"] = replacement["score"]
    result.pop("host_warning", None)
    return result
