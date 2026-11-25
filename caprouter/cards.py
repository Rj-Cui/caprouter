"""Deterministic Capability Card draft generation.

The generator reads a local skill, plugin or project and produces a draft Card
that a human can review. It never calls a model, never writes to the registry,
and never executes the capability. ``apply_draft`` merges a reviewed draft into
the overlay and keeps existing non-empty fields unless ``force=True``.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .capabilities import default_invoke, load_entries
from .scan import (
    detect_source_type,
    infer_capabilities,
    infer_domain,
    infer_domain_from_text,
)
from .util import (
    first_prose_line,
    hash_tree,
    now_iso,
    parse_frontmatter,
    read_json,
    safe_name,
    sha256_text,
    split_keywords,
    write_json_atomic,
)

DRAFT_SCHEMA = "caprouter.capability-card-draft.v1"
SECTION_HINTS = (
    "example",
    "examples",
    "usage",
    "quick start",
    "示例",
    "用例",
    "使用示例",
    "快速开始",
)
PROJECT_SECTION_HINTS = (
    "example",
    "examples",
    "features",
    "what it does",
    "示例",
    "用例",
    "使用示例",
    "功能",
)
BOILERPLATE_RE = re.compile(
    r"(?i)^(only need|the user wants|use when|when to use|do not|don't|when not)",
)
FEATURE_BULLET_RE = re.compile(
    r"^(?:[\U0001F300-\U0001FAFF☀-➿]\s*)?\*\*[^*]+\*\*\s*[:\-]"
    r"|^(?:support(?:s|ed)? for|one-click|features?|benefits?|highlights?)\b",
    re.I,
)
COMMAND_LIKE_RE = re.compile(
    r"(?i)^(pip|npm|npx|pnpm|yarn|uv|uvx|python|node|bash|sh|curl|git|docker)\b",
)
SETUP_LINE_RE = re.compile(
    r"(?i)^(first|then|next|install|run|clone|fork|import|configure|set up|set |add |open |visit|go to|download|deploy|copy |部署|安装|运行|配置)"
)
WRITE_HINTS = ("write", "save", "create", "export", "edit", "写", "保存", "创建", "导出", "编辑", "生成文件")
NET_HINTS = ("http://", "https://", "web search", "websearch", "fetch", "browse", "浏览器", "联网", "网络请求", "抓取", "下载")
SHELL_HINTS = ("bash", "shell command", "subprocess", "命令行", "终端命令", "执行脚本", "powershell")
BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.、)])\s+(.*\S)\s*$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


def drafts_dir(workspace: Path | str) -> Path:
    return Path(workspace) / ".caprouter" / "drafts"


def draft_path(workspace: Path | str, capability_id: str) -> Path:
    return drafts_dir(workspace) / f"{safe_name(capability_id.replace(':', '-'))}.json"


def _read_text(path: Path | None, limit: int = 20_000) -> str:
    if path and path.is_file():
        try:
            with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
                return handle.read(limit)
        except OSError:
            return ""
    return ""


def _body_text(root: Path, entry_type: str) -> str:
    if entry_type == "skill":
        text = _read_text(root / "SKILL.md")
        _meta, body = parse_frontmatter(text)
        return body
    for name in ("README.md", "readme.md", "README.txt"):
        text = _read_text(root / name)
        if text:
            return text
    if entry_type == "plugin":
        return _read_text(root / "plugin.json") or _read_text(root / ".claude-plugin" / "plugin.json")
    return ""


def _truncate_text(value: str, limit: int = 200) -> str:
    """Trim to ``limit`` without cutting a word or sentence in half."""
    if len(value) <= limit:
        return value
    window = value[:limit]
    cut = max(window.rfind(mark) for mark in ".!?。！？")
    if cut >= int(limit * 0.6):
        return window[: cut + 1].strip()
    cut = window.rfind(" ")
    if cut >= int(limit * 0.6):
        return window[:cut].strip()
    return window.strip()


def _clean_example(text: str) -> str:
    value = re.sub(r"^\s*(?:[-*+]|\d+[.、)])\s*", "", text).strip()
    value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    value = value.strip("`*_ ")
    return _truncate_text(value, 200)


def _section_examples(
    body: str,
    hints: tuple[str, ...] = SECTION_HINTS,
    *,
    skip_setup: bool = False,
) -> list[str]:
    lines = body.splitlines()
    examples: list[str] = []
    active_level = 0
    for line in lines:
        heading = HEADING_RE.match(line)
        if heading:
            level = len(heading.group(1))
            title = heading.group(2).lower()
            if active_level and level <= active_level:
                active_level = 0
            if not active_level and any(hint in title for hint in hints):
                active_level = level
            continue
        if not active_level:
            continue
        bullet = BULLET_RE.match(line)
        if bullet:
            value = _clean_example(bullet.group(1))
            if not (6 <= len(value) <= 200):
                continue
            if (
                BOILERPLATE_RE.match(value)
                or COMMAND_LIKE_RE.match(value)
                or FEATURE_BULLET_RE.match(value)
                or "**" in value
            ):
                continue
            if skip_setup and SETUP_LINE_RE.match(value):
                continue
            examples.append(value)
    return examples


def extract_examples(
    body: str,
    description: str,
    limit: int = 5,
    *,
    section_hints: tuple[str, ...] = SECTION_HINTS,
    skip_setup: bool = False,
) -> list[str]:
    section = _section_examples(body, section_hints, skip_setup=skip_setup)
    if section:
        combined = section
    else:
        # A short description can stand in for one example; long prose and
        # "use when / only need" boilerplate cannot. Better to return fewer
        # examples than to fill the Card with noise.
        desc = _clean_example(description)
        usable = (
            8 <= len(desc) <= 160
            and not re.match(r"(?i)^(use when|only need|do not|don't|when not)", desc)
        )
        combined = [desc] if usable else []
    seen: set[str] = set()
    result: list[str] = []
    for value in combined:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
        if len(result) >= limit:
            break
    return result


def infer_permissions(text: str, root: Path, entry_type: str) -> dict[str, Any]:
    lowered = text.lower()
    fs = ["read"]
    if any(word in lowered for word in WRITE_HINTS):
        fs.append("write")
    net = any(word in lowered for word in NET_HINTS)
    shell = any(word in lowered for word in SHELL_HINTS)
    if entry_type in {"skill", "plugin"} and (root / "scripts").is_dir():
        shell = True
    return {"fs": fs, "net": net, "shell": shell}


PROJECT_HINT_FILES = (
    "package.json",
    "pyproject.toml",
    "Cargo.toml",
    "go.mod",
    "requirements.txt",
    "build.gradle",
    "build.gradle.kts",
    "settings.gradle",
    "settings.gradle.kts",
    "pom.xml",
    "CMakeLists.txt",
    "README.md",
    "readme.md",
    "README.txt",
)


def _metadata_hash(root: Path, names: tuple[str, ...]) -> str | None:
    """Hash review-relevant metadata without machine-specific path or mtime.

    Migration compares this hash across machines, so it must contain only
    relative file names and file contents. Path and mtime would make every
    identity look different on a new machine.
    """
    parts: list[str] = []
    for name in names:
        path = root / name
        if path.is_file():
            parts.append(name.replace("\\", "/"))
            parts.append(sha256_text(_read_text(path, 20_000)))
    if not parts:
        return None
    return sha256_text("\0".join(parts))


def entry_hash(root: Path, entry_type: str) -> str | None:
    if not root.exists():
        return None
    if entry_type == "skill":
        return hash_tree(root)
    if entry_type == "plugin":
        return _metadata_hash(
            root,
            (".claude-plugin/plugin.json", "plugin.json", "README.md", "readme.md"),
        )
    return _metadata_hash(root, PROJECT_HINT_FILES)


def _project_metadata(root: Path, name: str) -> dict[str, Any]:
    """Infer a project description, keywords, capabilities and domain.

    This is deterministic and capped: a handful of small metadata files, never
    source code and never command execution.
    """
    readme = ""
    for item in ("README.md", "readme.md", "README.txt"):
        readme = _read_text(root / item, 8_000)
        if readme:
            break
    manifest = "\n".join(
        _read_text(root / item, 8_000)
        for item in (
            "package.json", "pyproject.toml", "Cargo.toml", "go.mod", "requirements.txt",
            "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts",
            "pom.xml", "CMakeLists.txt",
        )
    )
    package = read_json(root / "package.json", {})
    package = package if isinstance(package, dict) else {}
    description = str(package.get("description") or "").strip()
    keywords = split_keywords(package.get("keywords"))
    if not description:
        match = re.search(r'(?m)^\s*description\s*=\s*["\']([^"\']+)["\']', manifest)
        if match:
            description = match.group(1).strip()
    if not description:
        match = re.search(r"<description>\s*([^<]+?)\s*</description>", manifest, re.IGNORECASE | re.DOTALL)
        if match:
            description = re.sub(r"\s+", " ", match.group(1)).strip()
    if not description:
        description = first_prose_line(readme)
    if not description:
        description = f"Local project {name}"
    keywords = list(dict.fromkeys([name, *keywords]))
    feature_bullets = _section_examples(
        readme,
        PROJECT_SECTION_HINTS,
        skip_setup=True,
    )
    script_names: list[str] = []
    if isinstance(package, dict) and isinstance(package.get("scripts"), dict):
        script_names = [str(key) for key in package["scripts"]]
    capability_text = " ".join(
        [name, description, " ".join(keywords), " ".join(feature_bullets), " ".join(script_names)]
    )
    capabilities = sorted(set(infer_capabilities(capability_text)))
    domain = infer_domain_from_text(name, description, keywords, root.parts)
    has_readme = any((root / item).is_file() for item in ("README.md", "readme.md", "README.txt"))
    return {
        "description": description,
        "keywords": keywords,
        "capabilities": capabilities,
        "domain": domain,
        "has_readme": has_readme,
    }


def _entry_from_path(root: Path, entry_type: str, name_override: str | None = None) -> dict[str, Any]:
    root = Path(root)
    if entry_type == "skill":
        text = _read_text(root / "SKILL.md")
        meta, _body = parse_frontmatter(text)
        name = str(name_override or meta.get("name") or root.name).strip()
        description = str(meta.get("description") or "").strip()
        keywords = split_keywords(meta.get("keywords"))
        capabilities = split_keywords(meta.get("capabilities")) or sorted(
            set(infer_capabilities(name, description, keywords))
        )
        domain = infer_domain(root / "SKILL.md", root.parent, name, description, keywords)
        version = str(meta.get("version") or "")
        entry_id = f"skill:{name}"
    elif entry_type == "plugin":
        manifest = root / ".claude-plugin" / "plugin.json"
        if not manifest.is_file():
            manifest = root / "plugin.json"
        data = read_json(manifest, {})
        data = data if isinstance(data, dict) else {}
        name = str(name_override or data.get("name") or root.name).strip()
        description = str(data.get("description") or "").strip()
        keywords = split_keywords(data.get("keywords"))
        capabilities = split_keywords(data.get("capabilities")) or sorted(
            set(infer_capabilities(name, description, keywords))
        )
        domain = "general"
        version = str(data.get("version") or "")
        entry_id = f"plugin:{name}"
    else:
        name = str(name_override or root.name).strip()
        metadata = _project_metadata(root, name)
        description = metadata["description"]
        keywords = metadata["keywords"]
        capabilities = metadata["capabilities"]
        domain = metadata["domain"]
        version = ""
        entry_id = f"project:{name}"
    entry = {
        "id": entry_id,
        "type": entry_type,
        "name": name,
        "description": description,
        "domain": domain,
        "keywords": keywords,
        "capabilities": capabilities,
        "version": version,
        "path": str(root),
        "hash": entry_hash(root, entry_type),
    }
    if entry_type == "project":
        entry["has_readme"] = metadata["has_readme"]
    return entry


def _draft(entry: dict[str, Any]) -> dict[str, Any]:
    root = Path(str(entry.get("path") or ""))
    entry_type = str(entry.get("type") or "")
    body = _body_text(root, entry_type)
    description = str(entry.get("description") or "")
    if entry_type == "project":
        examples = extract_examples(
            body,
            description,
            section_hints=PROJECT_SECTION_HINTS,
            skip_setup=True,
        )
    else:
        examples = extract_examples(body, description)
    probe_checks = [
        {
            "id": f"host:example-{index}",
            "type": "host",
            "task": example,
            "acceptance": [],
            "required": False,
        }
        for index, example in enumerate(examples[:3], start=1)
    ]
    notes = [
        "deterministic draft; review examples, probe tasks and permissions before apply",
        "permissions are inferred conservatively; verify fs/net/shell before enabling execution",
    ]
    if not examples:
        notes.append("no examples found; add 3-5 concrete task examples manually")
    if not entry.get("capabilities"):
        notes.append("no capability tags found; add tags manually")
    if entry_type == "project" and not entry.get("has_readme"):
        notes.append("no README found; examples come from manifests only; review before apply")
    return {
        "schema": DRAFT_SCHEMA,
        "generated_at": now_iso(),
        "id": entry.get("id"),
        "type": entry_type,
        "name": entry.get("name"),
        "description": description,
        "domain": entry.get("domain") or "general",
        "domains": [entry.get("domain") or "general"],
        "capabilities": list(entry.get("capabilities") or []),
        "keywords": list(entry.get("keywords") or []),
        "path": str(root),
        "version": entry.get("version"),
        "examples": examples,
        "probe": {
            "status": "unknown",
            "evidence": "behavioral" if probe_checks else "unknown",
            "min_pass_rate": 0.8,
            "checks": probe_checks,
        },
        "input_schema": None,
        "output_schema": None,
        "permissions": infer_permissions(" ".join([body, description]), root, entry_type),
        "invoke": default_invoke(entry),
        "provenance": {
            "source": "draft-generator",
            "version": entry.get("version"),
            "hash": entry.get("hash") or entry_hash(root, entry_type),
        },
        "review": {"status": "draft", "notes": notes},
    }


def generate_draft(
    workspace: Path | str,
    *,
    capability_id: str | None = None,
    path: Path | str | None = None,
    type_hint: str | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    """Generate a draft from a registry id or a local path."""
    if capability_id:
        from .capabilities import load_entries

        for entry in load_entries(workspace):
            if str(entry.get("id")) == str(capability_id):
                return _draft(entry)
        raise ValueError(f"capability not found: {capability_id}")
    if not path:
        raise ValueError("capability_id or path is required")
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"path is not a directory: {root}")
    entry_type = type_hint or detect_source_type(root)
    if entry_type not in {"skill", "plugin", "project"}:
        raise ValueError(f"cannot detect capability type for {root}; pass --type")
    entry = _entry_from_path(root, entry_type, name_override=name)
    return _draft(entry)


def write_draft(workspace: Path | str, draft: dict[str, Any], out: Path | str | None = None) -> Path:
    target = Path(out).expanduser() if out else draft_path(workspace, str(draft.get("id") or "unnamed"))
    if not target.is_absolute():
        target = Path(workspace) / target
    write_json_atomic(target, draft)
    return target


def _draft_signature(draft: dict[str, Any]) -> str:
    payload = {key: value for key, value in draft.items() if key != "generated_at"}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def generate_project_drafts(
    workspace: Path | str,
    projects: list[dict[str, Any]],
    *,
    types: set[str] | None = None,
    limit: int | None = None,
    force: bool = False,
    apply: bool = False,
) -> dict[str, Any]:
    """Write deterministic drafts for scan records; never auto-apply by default."""
    workspace = Path(workspace)
    out_root = drafts_dir(workspace) / "projects"
    registry_entries = load_entries(workspace)
    known_verified = {
        str(entry.get("id"))
        for entry in registry_entries
        if entry.get("status") == "verified"
    }
    by_path = {
        os.path.normcase(str(Path(str(entry.get("path") or "")).resolve())): entry
        for entry in registry_entries
        if entry.get("path")
    }
    drafted: list[dict[str, Any]] = []
    unchanged: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    applied: list[dict[str, Any]] = []
    seen_paths: set[str] = set()

    for item in projects:
        if limit is not None and len(drafted) + len(unchanged) >= limit:
            break
        path = Path(str(item.get("path") or ""))
        entry_type = str(item.get("type") or "project")
        if types and entry_type not in types:
            skipped.append({"path": str(path), "reason": "type_filter"})
            continue
        key = os.path.normcase(str(path.resolve())) if path.exists() else os.path.normcase(str(path))
        if key in seen_paths:
            skipped.append({"path": str(path), "reason": "duplicate"})
            continue
        seen_paths.add(key)
        if not path.is_dir():
            skipped.append({"path": str(path), "reason": "not_found"})
            continue
        try:
            draft = generate_draft(workspace, path=path, type_hint=entry_type)
        except (OSError, ValueError) as exc:
            errors.append({"path": str(path), "error": str(exc)})
            continue
        matched = by_path.get(key)
        if matched:
            # Registry ids are authoritative: a scanned plugin directory may
            # differ in case from the manifest name used by the registry.
            draft["id"] = matched.get("id") or draft.get("id")
            draft["name"] = matched.get("name") or draft.get("name")
            draft["type"] = matched.get("type") or draft.get("type")
        if str(draft.get("id")) in known_verified:
            skipped.append({"path": str(path), "reason": "already_registered"})
            continue
        base = safe_name(str(item.get("name") or path.name or "unnamed"))
        digest = sha256_text(key).split(":", 1)[-1][:8]
        target = out_root / f"{base}-{digest}.json"
        existing = read_json(target, {})
        unchanged_item = (
            not force
            and isinstance(existing, dict)
            and _draft_signature(existing) == _draft_signature(draft)
        )
        row = {
            "id": draft.get("id"),
            "type": draft.get("type") or entry_type,
            "name": draft.get("name"),
            "path": str(path),
            "draft_path": str(target),
            "needs_review": not draft.get("examples") or not draft.get("capabilities"),
        }
        if unchanged_item:
            unchanged.append(row)
            if apply:
                _append_apply_result(
                    workspace, existing, force=force, applied=applied, errors=errors
                )
            continue
        write_draft(workspace, draft, target)
        drafted.append(row)
        if apply:
            _append_apply_result(
                workspace, draft, force=force, applied=applied, errors=errors
            )

    return {
        "schema": "caprouter.capability-draft-batch.v1",
        "generated_at": now_iso(),
        "scanned": len(projects),
        "drafted": len(drafted),
        "unchanged": len(unchanged),
        "skipped": len(skipped),
        "needs_review": sum(1 for item in [*drafted, *unchanged] if item["needs_review"]),
        "applied": len(applied),
        "drafts": [*drafted, *unchanged],
        "skipped_items": skipped,
        "errors": errors,
        "applied_items": applied,
    }


def generate_registry_drafts(
    workspace: Path | str,
    entries: list[dict[str, Any]],
    *,
    types: set[str] | None = None,
    limit: int | None = None,
    force: bool = False,
    apply: bool = False,
) -> dict[str, Any]:
    """Draft Cards for registry entries (skills/plugins) in one pass.

    Unlike project drafts, a registry entry already existing is not a reason to
    skip: the overlay Card is what is missing. Existing drafts are skipped only
    when their content is unchanged.
    """
    workspace = Path(workspace)
    selected_types = types or {"skill", "plugin"}
    drafted: list[dict[str, Any]] = []
    unchanged: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    applied: list[dict[str, Any]] = []
    scanned = 0
    seen_ids: set[str] = set()

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        entry_type = str(entry.get("type") or "")
        if entry_type not in selected_types:
            continue
        scanned += 1
        if limit is not None and len(drafted) + len(unchanged) >= limit:
            break
        capability_id = str(entry.get("id") or "")
        if not capability_id or capability_id in seen_ids:
            skipped.append({"id": capability_id, "reason": "duplicate"})
            continue
        seen_ids.add(capability_id)
        path = Path(str(entry.get("path") or ""))
        if not path.is_dir():
            skipped.append({"id": capability_id, "path": str(path), "reason": "not_found"})
            continue
        try:
            draft = generate_draft(
                workspace,
                path=path,
                type_hint=entry_type,
                name=str(entry.get("name") or "") or None,
            )
        except (OSError, ValueError) as exc:
            errors.append({"id": capability_id, "path": str(path), "error": str(exc)})
            continue
        draft["id"] = capability_id
        draft["name"] = entry.get("name") or draft.get("name")
        draft["type"] = entry_type
        target = draft_path(workspace, capability_id)
        existing = read_json(target, {})
        unchanged_item = (
            not force
            and isinstance(existing, dict)
            and _draft_signature(existing) == _draft_signature(draft)
        )
        row = {
            "id": capability_id,
            "type": entry_type,
            "name": draft.get("name"),
            "path": str(path),
            "draft_path": str(target),
            "needs_review": not draft.get("examples") or not draft.get("capabilities"),
        }
        if unchanged_item:
            unchanged.append(row)
            if apply:
                _append_apply_result(
                    workspace, existing, force=force, applied=applied, errors=errors
                )
            continue
        write_draft(workspace, draft, target)
        drafted.append(row)
        if apply:
            _append_apply_result(
                workspace, draft, force=force, applied=applied, errors=errors
            )

    return {
        "schema": "caprouter.capability-draft-batch.v1",
        "generated_at": now_iso(),
        "scanned": scanned,
        "drafted": len(drafted),
        "unchanged": len(unchanged),
        "skipped": len(skipped),
        "needs_review": sum(1 for item in [*drafted, *unchanged] if item["needs_review"]),
        "applied": len(applied),
        "drafts": [*drafted, *unchanged],
        "skipped_items": skipped,
        "errors": errors,
        "applied_items": applied,
    }


def _append_apply_result(
    workspace: Path,
    draft: dict[str, Any],
    *,
    force: bool,
    applied: list[dict[str, Any]],
    errors: list[dict[str, Any]],
) -> None:
    result = apply_draft(workspace, draft, force=force)
    if result.get("status") == "applied":
        applied.append(result)
    else:
        errors.append({"id": result.get("id"), "error": result.get("reason") or result.get("status")})


def apply_draft(workspace: Path | str, draft: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
    """Merge a reviewed draft into the overlay without overwriting evidence by default.

    A Card is only visible through ``get_capability``/search when its id exists
    in the registry. Writing an overlay entry for an unknown id would report
    ``applied`` but leave the Card unreachable forever, so refuse it instead.
    """
    from .capabilities import load_entries, load_overlay, overlay_path

    capability_id = str(draft.get("id") or "").strip()
    if not capability_id:
        raise ValueError("draft has no id")
    if not any(str(entry.get("id")) == capability_id for entry in load_entries(workspace)):
        return {
            "schema": "caprouter.capability-card-apply.v1",
            "status": "unreachable",
            "id": capability_id,
            "overlay": str(overlay_path(workspace)),
            "reason": "capability id is not in the registry; run scan or install before applying the Card",
            "updated": [],
            "kept": [],
            "force": bool(force),
        }
    overlay = load_overlay(workspace)
    cards = overlay["cards"]
    existing = cards.get(capability_id)
    existing = dict(existing) if isinstance(existing, dict) else {}
    updated: list[str] = []
    kept: list[str] = []
    for key in (
        "description",
        "capabilities",
        "keywords",
        "domain",
        "domains",
        "examples",
        "probe",
        "input_schema",
        "output_schema",
        "permissions",
        "invoke",
    ):
        new_value = draft.get(key)
        if new_value in (None, [], {}):
            continue
        old_value = existing.get(key)
        if old_value not in (None, [], {}) and not force:
            kept.append(key)
            continue
        existing[key] = new_value
        updated.append(key)
    cards[capability_id] = existing
    defaults = overlay.get("defaults") or {}
    payload = {
        "schema": "caprouter.capability-cards.v1",
        "defaults": defaults,
        "cards": cards,
    }
    write_json_atomic(overlay_path(workspace), payload)
    return {
        "schema": "caprouter.capability-card-apply.v1",
        "status": "applied",
        "id": capability_id,
        "overlay": str(overlay_path(workspace)),
        "updated": updated,
        "kept": kept,
        "force": bool(force),
    }
