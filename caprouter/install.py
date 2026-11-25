"""Local capability installer.

Installation is deliberately boring and auditable:

* the source must be a local directory or ZIP (no network download);
* installation requires explicit approval and never executes a script;
* files land in ``<workspace>/.caprouter/installed/<type>/<name>/``;
* a manifest records source, version, hash, permissions and warnings;
* the installer refreshes the registry and writes a Card draft to
  ``.caprouter/drafts/`` for human review. It does not enable command execution.
"""
from __future__ import annotations

import hashlib
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    tomllib = None  # type: ignore[assignment]

from .archive import _safe_extract, _zip_root
from .cards import generate_draft, infer_permissions, write_draft
from .licenses import _license_info
from .paths import config_path, default_plugin_roots, default_skill_roots, installed_root, registry_path
from .scan import build_registry, detect_source_type
from .util import append_jsonl, hash_tree, now_iso, read_json, safe_name, sha256_text, write_json_atomic

INSTALL_SCHEMA = "caprouter.installed.v1"
PREVIEW_SCHEMA = "caprouter.install-preview.v1"
EXECUTABLE_SUFFIXES = {
    ".exe", ".dll", ".bat", ".cmd", ".ps1", ".com", ".msi",
    ".py", ".js", ".mjs", ".cjs", ".sh", ".bash", ".zsh", ".fish",
}
LIFECYCLE_KEYS = {"preinstall", "install", "postinstall", "prepare", "prepublish"}
IGNORE_PATTERNS = (
    ".git", ".hg", ".svn", "__pycache__", "*.pyc", "*.pyo",
    ".venv", "venv", ".mypy_cache", ".pytest_cache",
)
IGNORE = shutil.ignore_patterns(*IGNORE_PATTERNS)
VENDOR_ARTIFACTS = ("provenance.json", "deconstruction.md", "card_draft.json")
PREVIEW_SUBDIR = "installs/previews"
JOURNAL_SUBDIR = "installs/journal.jsonl"
MINIMUM_CAPROUTER_VERSION = "0.6.3"
TYPE_DIRS = {"skill": "skills", "plugin": "plugins", "project": "projects"}


def _trash_stamp() -> str:
    """Sortable millisecond stamp: zero-padded so name order equals time order."""
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{int(time.time() * 1000) % 1000:03d}"

def _ignore_for(payload_only: bool):
    if not payload_only:
        return IGNORE
    return shutil.ignore_patterns(*IGNORE_PATTERNS, *VENDOR_ARTIFACTS)


def previews_dir(workspace: Path | str) -> Path:
    return Path(workspace) / ".caprouter" / PREVIEW_SUBDIR


def _preview_key(source_path: Path | str) -> str:
    return sha256_text(str(Path(source_path).expanduser().resolve())).split(":", 1)[-1]


def _write_preview(workspace: Path, preview: dict[str, Any]) -> Path:
    path = previews_dir(workspace) / f"{_preview_key(preview['source']['path'])}.json"
    record = {
        "schema": "caprouter.install-preview-record.v1",
        "source": preview["source"]["path"],
        "source_hash": preview.get("source_hash"),
        "target": preview.get("target"),
        "created_at": now_iso(),
        "preview": preview,
    }
    write_json_atomic(path, record)
    return path


def _read_preview(workspace: Path, source_path: Path | str) -> dict[str, Any] | None:
    path = previews_dir(workspace) / f"{_preview_key(source_path)}.json"
    data = read_json(path)
    return data if isinstance(data, dict) else None


def _hash_listed_files(root: Path, rows: list[Any]) -> str | None:
    digest = hashlib.sha256()
    normalized: list[tuple[str, Path]] = []
    for row in rows:
        if not isinstance(row, dict):
            return None
        relative = str(row.get("path") or "").strip()
        if not relative:
            return None
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            return None
        normalized.append((path.as_posix(), root / path))
    for relative, path in sorted(normalized, key=lambda item: item[0]):
        if not path.is_file():
            return None
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        try:
            digest.update(path.read_bytes())
        except OSError:
            return None
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _verify_provenance(root: Path) -> dict[str, Any]:
    data = read_json(root / "provenance.json")
    if not isinstance(data, dict):
        return {"status": "absent", "verified": False}
    rows = data.get("files")
    expected = str(data.get("content_hash") or "")
    if not isinstance(rows, list) or not expected:
        return {"status": "invalid", "verified": False}
    actual = _hash_listed_files(root, rows)
    if actual != expected:
        return {
            "status": "mismatch",
            "verified": False,
            "expected": expected,
            "actual": actual,
        }
    return {
        "status": "verified",
        "verified": True,
        "content_hash": expected,
        "remote_hash": data.get("remote_hash"),
        "candidate_id": data.get("candidate_id"),
        "source": data.get("source"),
        "license": data.get("license"),
    }


def _extract_dependencies(root: Path) -> list[str]:
    found: list[str] = []
    package = read_json(root / "package.json", {})
    if isinstance(package, dict):
        for key in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
            block = package.get(key)
            if isinstance(block, dict):
                found.extend(str(name) for name in block)
    pyproject = root / "pyproject.toml"
    if pyproject.is_file() and tomllib is not None:
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8-sig", errors="replace"))
            project = data.get("project") if isinstance(data.get("project"), dict) else {}
            for item in project.get("dependencies") or []:
                found.append(str(item))
        except (ValueError, TypeError):
            pass
    requirements = root / "requirements.txt"
    if requirements.is_file():
        for line in requirements.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and not line.startswith("-"):
                found.append(line)
    return sorted({item for item in found if item})[:100]


def _license_for(root: Path) -> dict[str, Any]:
    files: list[tuple[Path, str]] = []
    for name in ("LICENSE", "LICENSE.txt", "LICENSE.md", "COPYING", "NOTICE"):
        path = root / name
        if path.is_file():
            files.append((Path(name), path.read_text(encoding="utf-8-sig", errors="replace")))
    if not files:
        return {"name": "unknown", "file": None, "detected": False}
    return _license_info(files)


def _journal(workspace: Path, event: str, **fields: Any) -> None:
    append_jsonl(
        Path(workspace) / ".caprouter" / JOURNAL_SUBDIR,
        {"event": event, "at": now_iso(), **fields},
    )


def type_dir(workspace: Path | str, entry_type: str) -> Path:
    return installed_root(workspace) / TYPE_DIRS.get(entry_type, f"{entry_type}s")


def manifest_path(target: Path) -> Path:
    return Path(target) / "manifest.json"


def _resolve_source(source: Path | str) -> Path:
    path = Path(source).expanduser().resolve()
    if not path.exists():
        raise ValueError(f"source not found: {path}")
    if path.is_file() and path.suffix.lower() != ".zip":
        raise ValueError("source must be a directory or a .zip file")
    return path


def _prepare_source(source: Path) -> tuple[Path, str, Path | None]:
    if source.is_dir():
        return source, "local-dir", None
    staging = Path(tempfile.mkdtemp(prefix=f"caprouter-install-{safe_name(source.stem)}-"))
    try:
        _safe_extract(source, staging)
        return _zip_root(staging), "local-zip", staging
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""


def _permission_text(root: Path) -> str:
    parts = []
    for name in ("SKILL.md", "README.md", ".claude-plugin/plugin.json", "plugin.json"):
        parts.append(_read_text(root / name))
    return "\n".join(parts)


def _inspect_tree(root: Path) -> dict[str, Any]:
    files = 0
    total_bytes = 0
    executables: list[str] = []
    symlinks: list[str] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            symlinks.append(path.relative_to(root).as_posix())
            continue
        if not path.is_file():
            continue
        files += 1
        try:
            total_bytes += path.stat().st_size
        except OSError:
            pass
        if path.suffix.lower() in EXECUTABLE_SUFFIXES:
            executables.append(path.relative_to(root).as_posix())
    warnings: list[str] = []
    if executables:
        warnings.append(f"{len(executables)} executable/script file(s); installation will not run them")
    if symlinks:
        warnings.append(f"{len(symlinks)} symlink(s); they are copied as-is and may behave differently")
    package_json = read_json(root / "package.json", {})
    if isinstance(package_json, dict):
        scripts = package_json.get("scripts")
        if isinstance(scripts, dict):
            lifecycle = sorted(key for key in scripts if str(key).lower() in LIFECYCLE_KEYS)
            if lifecycle:
                warnings.append("package.json lifecycle scripts present (" + ", ".join(lifecycle) + "); they will not run")
    return {
        "file_count": files,
        "total_bytes": total_bytes,
        "executables": executables[:50],
        "symlinks": symlinks[:50],
        "warnings": warnings,
    }


def _name_and_version(root: Path, entry_type: str, name_override: str | None = None) -> tuple[str, str]:
    name = str(name_override or "").strip()
    version = ""
    if entry_type == "skill":
        from .util import parse_frontmatter

        meta, _body = parse_frontmatter(_read_text(root / "SKILL.md"))
        name = name or str(meta.get("name") or root.name)
        version = str(meta.get("version") or "")
    elif entry_type == "plugin":
        manifest = root / ".claude-plugin" / "plugin.json"
        if not manifest.is_file():
            manifest = root / "plugin.json"
        data = read_json(manifest, {})
        data = data if isinstance(data, dict) else {}
        name = name or str(data.get("name") or root.name)
        version = str(data.get("version") or "")
    else:
        name = name or root.name
    return name.strip() or root.name, version.strip() or "0.0.0-local"


def _preview_from_root(
    workspace: Path,
    source_path: Path,
    root: Path,
    kind: str,
    type_hint: str | None,
    name: str | None,
) -> dict[str, Any]:
    entry_type = type_hint or detect_source_type(root)
    if entry_type not in TYPE_DIRS:
        raise ValueError(f"cannot detect capability type for {root}; pass --type")
    resolved_name, version = _name_and_version(root, entry_type, name)
    target = type_dir(workspace, entry_type) / safe_name(resolved_name)
    tree = _inspect_tree(root)
    permissions = infer_permissions(_permission_text(root), root, entry_type)
    return {
        "schema": PREVIEW_SCHEMA,
        "status": "preview",
        "source": {"kind": kind, "path": str(source_path)},
        "type": entry_type,
        "name": resolved_name,
        "version": version,
        "target": str(target),
        "permissions": permissions,
        "source_hash": hash_tree(root),
        "license": _license_for(root),
        "dependencies": _extract_dependencies(root),
        **tree,
    }


def preview_install(
    workspace: Path | str,
    source: Path | str,
    *,
    type_hint: str | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    workspace = Path(workspace)
    source_path = _resolve_source(source)
    root, kind, staging = _prepare_source(source_path)
    try:
        preview = _preview_from_root(workspace, source_path, root, kind, type_hint, name)
        _write_preview(workspace, preview)
        return preview
    finally:
        if staging:
            shutil.rmtree(staging, ignore_errors=True)


def _ensure_config_roots(config: dict[str, Any], workspace: Path, entry_type: str) -> list[str]:
    changed: list[str] = []
    if entry_type not in {"skill", "plugin"}:
        return changed
    key = "skill_roots" if entry_type == "skill" else "plugin_roots"
    wanted = str(type_dir(workspace, entry_type))
    roots = [str(item) for item in (config.get(key) or [])]
    if wanted.lower() not in {root.lower() for root in roots}:
        # Managed copies must win duplicate-id resolution over external roots.
        roots.insert(0, wanted)
        config[key] = roots
        changed.append(key)
    return changed


def _refresh_registry(workspace: Path, config: dict[str, Any]) -> dict[str, Any]:
    skill_roots = [Path(str(item)) for item in (config.get("skill_roots") or [])]
    plugin_roots = [Path(str(item)) for item in (config.get("plugin_roots") or [])]
    scan_config = config.get("scan") if isinstance(config.get("scan"), dict) else {}
    registry = build_registry(
        workspace,
        skill_roots=skill_roots or default_skill_roots(workspace),
        plugin_roots=plugin_roots or default_plugin_roots(workspace),
        include_agents=bool(config.get("scan_agents", True)),
        include_mcp=bool(scan_config.get("mcp_hosts", True)),
    )
    write_json_atomic(registry_path(workspace), registry)
    return registry


def install_source(
    workspace: Path | str,
    source: Path | str,
    *,
    type_hint: str | None = None,
    name: str | None = None,
    approve: bool = False,
    force: bool = False,
    generate_card: bool = True,
    allow_symlinks: bool = False,
    payload_only: bool = False,
    skip_provenance: bool = False,
) -> dict[str, Any]:
    workspace = Path(workspace)
    config_file = config_path(workspace)
    if not config_file.is_file():
        return {"schema": INSTALL_SCHEMA, "status": "not_initialized",
                "reason": f"run caprouter init --workspace {workspace}"}
    config = read_json(config_file, {})
    if not isinstance(config, dict) or str(config.get("schema") or "") != "caprouter.config.v1":
        return {"schema": INSTALL_SCHEMA, "status": "not_initialized",
                "reason": f"run caprouter init --workspace {workspace}"}
    source_path = _resolve_source(source)
    root, kind, staging = _prepare_source(source_path)
    target: Path | None = None
    trash: Path | None = None
    copied = False
    try:
        preview = _preview_from_root(workspace, source_path, root, kind, type_hint, name)
        preview_path = previews_dir(workspace) / f"{_preview_key(source_path)}.json"
        if not approve:
            _write_preview(workspace, preview)
            return {
                "schema": INSTALL_SCHEMA,
                "status": "approval_required",
                "preview": preview,
                "preview_path": str(preview_path),
            }

        _journal(workspace, "install_approved", source=str(source_path), target=preview["target"])

        record = _read_preview(workspace, source_path)
        preview_verified = False
        if record:
            stored = str(record.get("source_hash") or "")
            current = str(preview.get("source_hash") or "")
            if stored and current and stored != current:
                if not force:
                    _journal(
                        workspace,
                        "source_changed",
                        source=str(source_path),
                        preview_hash=stored,
                        current_hash=current,
                    )
                    return {
                        "schema": INSTALL_SCHEMA,
                        "status": "source_changed",
                        "preview_hash": stored,
                        "current_hash": current,
                        "reason": "source changed between preview and approval; re-preview or pass --force",
                    }
            elif stored and current:
                preview_verified = True

        if preview["symlinks"] and not allow_symlinks:
            return {
                "schema": INSTALL_SCHEMA,
                "status": "unsafe_symlinks",
                "symlinks": preview["symlinks"],
                "reason": "source contains symlinks; pass --allow-symlinks to copy them as links",
            }

        provenance = _verify_provenance(root)
        if provenance.get("status") == "mismatch" and not skip_provenance:
            _journal(workspace, "provenance_mismatch", source=str(source_path), **{
                key: provenance.get(key) for key in ("expected", "actual")
            })
            return {
                "schema": INSTALL_SCHEMA,
                "status": "provenance_mismatch",
                "expected": provenance.get("expected"),
                "actual": provenance.get("actual"),
                "reason": "quarantine provenance content_hash does not match; pass --skip-provenance to override",
            }

        entry_type = str(preview["type"])
        resolved_name = str(preview["name"])
        version = str(preview["version"])
        target = Path(str(preview["target"]))
        if target.exists():
            if not force:
                return {
                    "schema": INSTALL_SCHEMA,
                    "status": "exists",
                    "target": str(target),
                    "reason": "already installed; pass --force to replace",
                }
            trash = installed_root(workspace) / ".trash" / f"{safe_name(resolved_name)}-{_trash_stamp()}"
            trash.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(target), str(trash))
        target.parent.mkdir(parents=True, exist_ok=True)
        _journal(workspace, "copy_started", target=str(target), payload_only=payload_only)
        try:
            shutil.copytree(
                root,
                target,
                ignore=_ignore_for(payload_only),
                symlinks=allow_symlinks,
            )
            copied = True
            _journal(workspace, "copied", target=str(target))
        except Exception as exc:
            _journal(workspace, "copy_failed", target=str(target), error=str(exc))
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            if trash is not None and trash.exists():
                shutil.move(str(trash), str(target))
            raise

        tree = _inspect_tree(target)
        digest = hash_tree(target)
        license_info = provenance.get("license") or preview.get("license") or {
            "name": "unknown",
            "file": None,
            "detected": False,
        }
        manifest = {
            "schema": INSTALL_SCHEMA,
            "id": f"{entry_type}:{resolved_name}",
            "type": entry_type,
            "name": resolved_name,
            "version": version,
            "source": {"kind": kind, "path": str(source_path)},
            "installed_at": now_iso(),
            "installed_by": "caprouter install",
            "approved": True,
            "hash": digest,
            "permissions": preview["permissions"],
            "license": license_info,
            "dependencies": preview.get("dependencies") or [],
            "minimum_caprouter_version": MINIMUM_CAPROUTER_VERSION,
            "provenance": {
                key: provenance.get(key)
                for key in ("status", "verified", "content_hash", "remote_hash", "candidate_id", "source")
            },
            "source_hash": preview.get("source_hash"),
            "preview_verified": preview_verified,
            "payload_only": payload_only,
            "file_count": tree["file_count"],
            "total_bytes": tree["total_bytes"],
            "executables": tree["executables"],
            "symlinks": tree["symlinks"],
            "warnings": tree["warnings"],
        }
        write_json_atomic(manifest_path(target), manifest)
        _journal(workspace, "manifest_written", target=str(target), hash=digest)

        changed = _ensure_config_roots(config, workspace, entry_type)
        if changed:
            config["updated_at"] = now_iso()
            write_json_atomic(config_path(workspace), config)
        registry = _refresh_registry(workspace, config)
        _journal(workspace, "registry_refreshed", target=str(target))
        entry = next(
            (item for item in registry.get("entries") or []
             if str(item.get("id")) == manifest["id"]),
            None,
        )
        draft_file = None
        if generate_card:
            draft = generate_draft(
                workspace,
                path=target,
                type_hint=entry_type,
                name=resolved_name,
            )
            draft_file = str(write_draft(workspace, draft))
            _journal(workspace, "draft_written", target=str(target), draft=draft_file)
        return {
            "schema": INSTALL_SCHEMA,
            "status": "installed",
            "manifest": manifest,
            "manifest_path": str(manifest_path(target)),
            "target": str(target),
            "trash_path": str(trash) if trash is not None else None,
            "registry_entry": entry,
            "draft_path": draft_file,
            "config_roots_changed": changed,
            "preview_verified": preview_verified,
            "provenance": manifest["provenance"],
            "journal_path": str(Path(workspace) / ".caprouter" / JOURNAL_SUBDIR),
        }
    except Exception as exc:
        _journal(workspace, "install_failed", error=str(exc))
        if copied and target is not None and target.exists():
            shutil.rmtree(target, ignore_errors=True)
        if target is not None and trash is not None and trash.exists() and not target.exists():
            shutil.move(str(trash), str(target))
        raise
    finally:
        if staging:
            shutil.rmtree(staging, ignore_errors=True)


def list_installed(workspace: Path | str) -> list[dict[str, Any]]:
    root = installed_root(workspace)
    if not root.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(root.rglob("manifest.json")):
        if ".trash" in path.relative_to(root).parts:
            continue
        data = read_json(path, {})
        if not isinstance(data, dict) or data.get("schema") != INSTALL_SCHEMA:
            continue
        data = dict(data)
        data["manifest_path"] = str(path)
        items.append(data)
    return items
