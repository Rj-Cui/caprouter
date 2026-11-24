from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .mcp_inventory import scan_mcp_servers
from .paths import CONFIG_DIR, builtin_skill_root, default_plugin_roots, default_skill_roots, installed_root
from .util import hash_tree, now_iso, parse_frontmatter, read_json, sha256_text, split_keywords, write_json_atomic

CAPABILITY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "retrieve": ("search", "research", "source", "fetch", "browse", "调研", "搜索", "资料", "检索", "抓取"),
    "analyze": ("analyze", "analyse", "compare", "classify", "extract", "分析", "比较", "评估"),
    "generate": ("write", "generate", "create", "draft", "content", "写作", "生成", "创作", "文案"),
    "transform": ("convert", "rewrite", "summarize", "translate", "transform", "改写", "转换", "翻译", "总结"),
    "verify": ("review", "check", "validate", "test", "audit", "检查", "校验", "评审", "核查"),
    "format": ("format", "ppt", "docx", "xlsx", "pdf", "office", "排版", "表格", "幻灯片"),
    "design": ("design", "ui design", "ux", "visual", "brand", "设计", "视觉"),
    "operate_gui": ("gui", "desktop", "browser", "click", "screen", "桌面", "浏览器"),
    "decide": ("decide", "choose", "route", "rank", "决策", "选择", "路由"),
    "orchestrate": ("orchestrate", "plan", "workflow", "agent", "编排", "工作流"),
}

KNOWN_DOMAINS = {"design", "engineering", "literature", "media", "meta", "office", "research", "writing"}
DOMAIN_PRIORITY = ["office", "design", "engineering", "media", "literature", "research", "writing", "meta"]
DOMAIN_DEFAULT_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "literature": ("generate",),
    "writing": ("generate", "transform"),
    "office": ("format", "transform"),
    "design": ("design",),
    "engineering": ("generate", "verify"),
    "research": ("retrieve", "analyze"),
    "media": ("generate", "transform"),
    "meta": ("orchestrate", "decide"),
}
DOMAIN_HINTS: dict[str, tuple[str, ...]] = {
    "literature": ("小说", "网文", "西幻", "奇幻", "fiction", "novel", "story", "narrative", "literature"),
    "research": ("调研", "研究", "市场", "竞品", "research", "survey", "source", "检索"),
    "office": ("ppt", "docx", "xlsx", "pdf", "office", "表格", "幻灯片", "文档"),
    "design": ("网页", "设计", "ui", "ux", "视觉", "design"),
    "engineering": ("代码", "编程", "开发", "工程", "code", "programming", "api"),
    "media": ("视频", "音频", "图片", "图像", "video", "audio", "image"),
    "writing": ("写作", "文案", "润色", "改写", "copywriting", "rewrite", "prompt", "提示词"),
    "meta": ("安装 skill", "推荐专家", "marketplace", "install skill", "recommend"),
}

KNOWN_CLI_AGENTS = [
    "codebuddy",
    "cbc",
    "qwen",
    "kimi",
    "claude",
    "codex",
    "gemini",
    "opencode",
    "aider",
    "droid",
    "cursor-agent",
    "trae",
]

AGENT_ALIASES: dict[str, str] = {
    "cbc": "codebuddy",
}

AGENT_CAPABILITY_HINTS: dict[str, list[str]] = {
    "codebuddy": ["generate", "retrieve", "transform", "verify", "orchestrate"],
    "cbc": ["generate", "retrieve", "transform", "verify", "orchestrate"],
    "qwen": ["generate", "retrieve", "transform", "verify"],
    "kimi": ["generate", "retrieve", "transform"],
    "claude": ["generate", "analyze", "transform", "verify", "orchestrate"],
    "codex": ["generate", "transform", "verify"],
    "gemini": ["generate", "retrieve", "analyze"],
    "opencode": ["generate", "transform"],
    "aider": ["generate", "transform"],
    "droid": ["generate", "transform"],
    "cursor-agent": ["generate", "transform", "verify"],
    "trae": ["generate", "transform"],
}

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
MAX_SCAN_DEPTH = 6


def infer_capabilities(*values: Any) -> list[str]:
    blob = " ".join(str(value) for value in values if value).lower()
    found: list[str] = []
    for capability, words in CAPABILITY_KEYWORDS.items():
        if any(word.lower() in blob for word in words):
            found.append(capability)
    return found


NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# A directory is a project when one primary marker is present. Auxiliary
# markers only enrich the metadata; they never make a directory a project.
PRIMARY_PROJECT_MARKERS = (
    "pyproject.toml",
    "package.json",
    ".git",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "settings.gradle",
    "requirements.txt",
    "Pipfile",
    "composer.json",
    "Gemfile",
    "mix.exs",
    "pubspec.yaml",
    "CMakeLists.txt",
)
PROJECT_GLOB_MARKERS = (".sln", ".csproj")
AUXILIARY_PROJECT_MARKERS = (
    "README.md",
    "readme.md",
    "README.txt",
    "Dockerfile",
    "Makefile",
    "deno.json",
    "bun.lockb",
    "uv.lock",
    "poetry.lock",
)
# Directories that never contain a user project worth routing to. Names are
# matched case-insensitively at any depth. The explicit scan root itself is
# always inspected, so a project literally named "build" can still be passed
# with --project-root.
SYSTEM_DIR_NAMES = frozenset(
    {
        "windows",
        "windows.old",
        "program files",
        "program files (x86)",
        "programdata",
        "appdata",
        "windowsapps",
        "$recycle.bin",
        "system volume information",
        "recovery",
        "boot",
        "efi",
    }
)
SOFTWARE_DIR_NAMES = frozenset(
    {
        "steamlibrary",
        "epic games",
        "gog games",
        "battle.net",
        "riot games",
    }
)
CACHE_DIR_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".idea",
        ".caprouter",
        ".vscode",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        "node_modules",
        "dist",
        "build",
        "target",
        "vendor",
        "site-packages",
        ".mypy_cache",
        ".pytest_cache",
        ".next",
        ".nuxt",
        ".cache",
        ".uv-cache",
        ".superagent",
        ".pnpm-store",
        ".yarn",
        ".turbo",
        ".nx",
        ".parcel-cache",
        ".tox",
        ".nox",
        "__pypackages__",
        "bower_components",
        "coverage",
        "logs",
    }
)
SKIP_DIR_NAMES = SYSTEM_DIR_NAMES | SOFTWARE_DIR_NAMES | CACHE_DIR_NAMES

DRIVE_TYPES = {
    0: "unknown",
    1: "no_root",
    2: "removable",
    3: "fixed",
    4: "remote",
    5: "cdrom",
    6: "ramdisk",
}
FILE_ATTRIBUTE_HIDDEN = 0x2
FILE_ATTRIBUTE_SYSTEM = 0x4
FILE_ATTRIBUTE_REPARSE_POINT = 0x400

DEFAULT_SCAN_DEPTH = 4
DEFAULT_SCAN_DIRS = 50_000
DEFAULT_SCAN_SECONDS = 30.0
DEFAULT_SCAN_PROJECTS = 500
SCAN_SUMMARY_BYTES = 4096
PROJECTS_SCAN_SCHEMA = "caprouter.projects-scan.v1"


def _norm_path(path: Path | str) -> str:
    try:
        return os.path.normcase(os.path.abspath(str(path)))
    except (OSError, ValueError):
        return os.path.normcase(str(path))


def _system_drive() -> str:
    value = (os.environ.get("SystemDrive") or "C:").strip().upper()
    return value[:1]


def _enumerate_windows_drives() -> list[tuple[str, int]]:
    """Return [(root, GetDriveTypeW value)] for every logical drive."""
    if os.name != "nt":
        return []
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        mask = int(kernel32.GetLogicalDrives())
    except (AttributeError, OSError, ValueError):
        return []
    found: list[tuple[str, int]] = []
    for index in range(26):
        if not mask & (1 << index):
            continue
        letter = chr(ord("A") + index)
        root = f"{letter}:\\"
        try:
            kind = int(kernel32.GetDriveTypeW(ctypes.c_wchar_p(root)))
        except (AttributeError, OSError, ValueError):
            kind = 0
        found.append((root, kind))
    return found


def classify_drives() -> list[dict[str, Any]]:
    """Classify local drives without touching their contents."""
    system = _system_drive()
    items: list[dict[str, Any]] = []
    for root, kind in _enumerate_windows_drives():
        letter = root[:1].upper()
        type_name = DRIVE_TYPES.get(kind, "unknown")
        is_system = letter == system
        items.append(
            {
                "drive": f"{letter}:",
                "root": root,
                "type": type_name,
                "system": is_system,
                "scan_default": type_name == "fixed" and not is_system,
            }
        )
    return sorted(items, key=lambda item: item["drive"])


def default_scan_roots(include_removable: bool = False) -> list[Path]:
    """Non-system fixed drives; removable drives are opt-in, network never."""
    roots: list[Path] = []
    for item in classify_drives():
        if item["scan_default"] or (include_removable and item["type"] == "removable"):
            roots.append(Path(item["root"]))
    return roots


def _env_skip_paths() -> set[str]:
    paths: set[str] = set()
    for name in ("SystemRoot", "windir", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
        value = os.environ.get(name)
        if value:
            paths.add(_norm_path(value))
    for name in ("APPDATA", "LOCALAPPDATA"):
        value = os.environ.get(name)
        if value:
            paths.add(_norm_path(value))
    system = _system_drive()
    for name in ("$Recycle.Bin", "System Volume Information", "Recovery", "Boot", "EFI", "Windows.old", "Windows"):
        paths.add(_norm_path(f"{system}:\\{name}"))
    return paths


def _normalize_drive(value: str) -> str:
    drive = str(value).strip().upper().rstrip("\\/")
    if not drive.endswith(":"):
        drive += ":"
    if len(drive) != 2 or not drive[0].isalpha():
        raise ValueError(f"invalid drive: {value}")
    return drive


def resolve_scan_roots(
    *,
    drives: Iterable[str] | None = None,
    project_roots: Iterable[Path | str] | None = None,
    include_removable: bool = False,
) -> list[dict[str, Any]]:
    """Resolve CLI selectors into roots.

    Explicit drives override the default drive set. Explicit project roots are
    always added. When only project roots are given, no drive is scanned.
    """
    drives = [str(item) for item in (drives or [])]
    project_roots = [Path(item).expanduser().resolve() for item in (project_roots or [])]
    drive_items = classify_drives()
    by_drive = {item["drive"].upper(): item for item in drive_items}
    selected: list[dict[str, Any]] = []

    if drives:
        for raw in drives:
            drive = _normalize_drive(raw)
            info = by_drive.get(drive)
            if info:
                selected.append({**info, "kind": "drive", "explicit": True})
            else:
                selected.append(
                    {
                        "drive": drive,
                        "root": f"{drive}\\",
                        "type": "unknown",
                        "system": drive[:1] == _system_drive(),
                        "scan_default": False,
                        "kind": "drive",
                        "explicit": True,
                    }
                )
    elif not project_roots:
        selected = [
            {**item, "kind": "drive", "explicit": False}
            for item in drive_items
            if item["scan_default"] or (include_removable and item["type"] == "removable")
        ]

    for root in project_roots:
        selected.append(
            {
                "drive": root.drive or "",
                "root": str(root),
                "type": "project_root",
                "system": False,
                "scan_default": False,
                "kind": "project",
                "explicit": True,
            }
        )

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in selected:
        key = _norm_path(item["root"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _names_for(directory: Path) -> set[str]:
    try:
        return {child.name.lower() for child in directory.iterdir()}
    except OSError:
        return set()


def _primary_markers(names: Iterable[str]) -> list[str]:
    lowered = {str(name).lower() for name in names}
    found = [marker for marker in PRIMARY_PROJECT_MARKERS if marker.lower() in lowered]
    for name in sorted(lowered):
        if any(name.endswith(suffix) for suffix in PROJECT_GLOB_MARKERS):
            found.append(name)
    return found


def _auxiliary_markers(names: Iterable[str]) -> list[str]:
    lowered = {str(name).lower() for name in names}
    return [marker for marker in AUXILIARY_PROJECT_MARKERS if marker.lower() in lowered]


def _project_plausible(root: Path, names: Iterable[str], markers: Iterable[str]) -> tuple[bool, str]:
    """Filter build-system files that belong to vendored tools, not projects.

    This is intentionally metadata-only: it checks directory entries, never
    reads file contents. A lone ``settings.gradle`` inside ``tools/ghidra`` is
    not a user project; a Gradle project with ``src/`` or a README is.
    """
    lowered = {str(name).lower() for name in names}
    marker_set = {str(marker).lower() for marker in markers}
    if ".git" in lowered:
        return True, "git"
    strong_manifests = {
        "package.json", "pyproject.toml", "cargo.toml", "go.mod", "pom.xml",
        "composer.json", "gemfile", "pubspec.yaml", "mix.exs", "pipfile",
    }
    if marker_set & strong_manifests:
        return True, "manifest"
    source_dirs = ("src", "app", "lib", "include", "source", "sources", "packages")
    has_source = any((root / name).is_dir() for name in source_dirs)
    has_readme = any(name.startswith("readme") for name in lowered)
    build_markers = {
        "build.gradle", "build.gradle.kts", "settings.gradle",
        "settings.gradle.kts", "cmakelists.txt",
    }
    if marker_set & build_markers:
        if has_source or has_readme:
            return True, "build+source"
        return False, "build_without_source"
    if "requirements.txt" in marker_set:
        if has_source or has_readme:
            return True, "requirements+source"
        return False, "requirements_without_source"
    return True, "marker"


def detect_source_type(path: Path | str) -> str | None:
    """Classify a local directory as skill, plugin or project."""
    root = Path(path)
    if not root.is_dir():
        return None
    names = _names_for(root)
    if "skill.md" in names:
        return "skill"
    if "plugin.json" in names or ".claude-plugin" in names:
        if (root / "plugin.json").is_file() or (root / ".claude-plugin" / "plugin.json").is_file():
            return "plugin"
    if _primary_markers(names):
        return "project"
    return None


def lint_skill(skill_file: Path) -> dict[str, Any]:
    """Validate the portable parts of an Agent Skill entry."""
    errors: list[str] = []
    warnings: list[str] = []
    try:
        text = skill_file.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"ok": False, "errors": [str(exc)], "warnings": []}
    meta, _body = parse_frontmatter(text)
    name = str(meta.get("name") or "")
    description = str(meta.get("description") or "")
    if not NAME_RE.match(name) or len(name) > 64:
        errors.append("name must be lowercase letters, digits and hyphens, max 64 chars")
    if not description:
        errors.append("description is required")
    elif len(description) > 1024:
        errors.append("description must be <= 1024 chars")
    size = skill_file.stat().st_size
    if size > 2048:
        warnings.append(f"SKILL.md is {size} bytes; the entry contract should stay <= 2048")
    if not (skill_file.parent / "references").is_dir():
        warnings.append("references/ directory is missing; move long details out of SKILL.md")
    return {"ok": not errors, "errors": errors, "warnings": warnings, "bytes": size, "name": name}


def _hint_match(text: str, word: str) -> bool:
    """ASCII hints match whole words; Chinese hints match substrings."""
    if word.isascii():
        return re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", text) is not None
    return word in text


def infer_domain_from_text(
    name: str,
    description: str,
    keywords: list[str],
    path_parts: Iterable[str] = (),
) -> str:
    """Infer a domain from metadata without reading project files."""
    for part in path_parts:
        if part in KNOWN_DOMAINS:
            return part
    name_text = name.lower()
    for domain in DOMAIN_PRIORITY:
        if any(_hint_match(name_text, word.lower()) for word in DOMAIN_HINTS.get(domain, ())):
            return domain
    blob = " ".join([name, description, " ".join(keywords)]).lower()
    for domain in DOMAIN_PRIORITY:
        if any(_hint_match(blob, word.lower()) for word in DOMAIN_HINTS.get(domain, ())):
            return domain
    return "general"


def infer_domain(skill_file: Path, root: Path, name: str, description: str, keywords: list[str]) -> str:
    try:
        path_parts = skill_file.parent.relative_to(root).parts
    except ValueError:
        path_parts = ()
    return infer_domain_from_text(name, description, keywords, path_parts)


def _walk_files(root: Path, target_name: str, max_depth: int = MAX_SCAN_DEPTH) -> Iterable[Path]:
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        try:
            depth = len(current.relative_to(root).parts)
        except ValueError:
            depth = 0
        if depth >= max_depth:
            dirnames[:] = []
        dirnames[:] = [
            name
            for name in dirnames
            if (
                name.lower() not in SKIP_DIR_NAMES
                and (not name.startswith(".") or name == ".claude-plugin")
            )
        ]
        if target_name in filenames:
            yield current / target_name


def _skill_entry(skill_file: Path, root: Path) -> dict[str, Any] | None:
    try:
        text = skill_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    meta, _body = parse_frontmatter(text)
    name = str(meta.get("name") or skill_file.parent.name).strip()
    if not name:
        return None
    description = str(meta.get("description") or "").strip()
    keywords = split_keywords(meta.get("keywords"))
    domain = infer_domain(skill_file, root, name, description, keywords)
    declared = split_keywords(meta.get("capabilities"))
    if declared:
        capabilities = sorted(set(declared))
    else:
        capabilities = sorted(
            set(infer_capabilities(name, description, keywords))
            | set(DOMAIN_DEFAULT_CAPABILITIES.get(domain, ()))
        )
    try:
        relative = skill_file.parent.relative_to(root).as_posix()
    except ValueError:
        relative = skill_file.parent.name
    status = "verified" if description else "unverified"
    return {
        "id": f"skill:{name}",
        "type": "skill",
        "name": name,
        "path": str(skill_file.parent),
        "root": str(root),
        "relative_path": relative,
        "domain": domain,
        "description": description,
        "version": str(meta.get("version") or ""),
        "keywords": keywords,
        "capabilities": capabilities,
        "invocation": f"read {skill_file}",
        "hash": hash_tree(skill_file.parent),
        "verified_at": now_iso(),
        "status": status,
    }


def scan_skill_root(root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    root = Path(root)
    if not root.is_dir():
        return entries
    for skill_file in _walk_files(root, "SKILL.md"):
        entry = _skill_entry(skill_file, root)
        if entry:
            entries.append(entry)
    return entries


def _plugin_entry(manifest: Path) -> dict[str, Any] | None:
    data = read_json(manifest)
    if not isinstance(data, dict):
        return None
    root = manifest.parent.parent if manifest.parent.name == ".claude-plugin" else manifest.parent
    name = str(data.get("name") or root.name).strip()
    if not name:
        return None
    description = str(data.get("description") or "").strip()
    keywords = split_keywords(data.get("keywords"))
    capabilities = split_keywords(data.get("capabilities")) or infer_capabilities(name, description, keywords)
    return {
        "id": f"plugin:{name}",
        "type": "plugin",
        "name": name,
        "path": str(root),
        "root": str(root),
        "domain": "general",
        "description": description,
        "version": str(data.get("version") or ""),
        "keywords": keywords,
        "capabilities": capabilities,
        "invocation": f"plugin root {root}",
        "hash": hash_tree(root),
        "verified_at": now_iso(),
        "status": "verified" if description else "unverified",
    }


def scan_plugin_root(root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    root = Path(root)
    if not root.is_dir():
        return entries
    for manifest in _walk_files(root, "plugin.json", max_depth=4):
        entry = _plugin_entry(manifest)
        if entry:
            entries.append(entry)
    return entries


def _probe_version(path: str) -> tuple[str | None, bool]:
    try:
        proc = subprocess.run(
            [path, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            creationflags=CREATE_NO_WINDOW,
        )
        text = (proc.stdout or proc.stderr or "").strip()
        line = text.splitlines()[0][:200] if text else None
        return line, proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return None, False


def scan_agents() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    seen_canonical: set[str] = set()
    for name in KNOWN_CLI_AGENTS:
        path = shutil.which(name)
        if not path:
            continue
        resolved = str(Path(path).resolve()).lower()
        if resolved in seen:
            continue
        canonical = AGENT_ALIASES.get(name, name)
        if canonical in seen_canonical:
            continue
        seen.add(resolved)
        seen_canonical.add(canonical)
        version, ok = _probe_version(path)
        entries.append(
            {
                "id": f"cli-agent:{name}",
                "type": "cli-agent",
                "name": name,
                "canonical": AGENT_ALIASES.get(name, name),
                "path": path,
                "root": str(Path(path).parent),
                "domain": "general",
                "description": f"Local CLI agent {name} ({version or 'version unknown'})",
                "version": version,
                "keywords": [name],
                "capabilities": AGENT_CAPABILITY_HINTS.get(name, ["generate"]),
                "invocation": f"{path} -p <prompt>",
                "hash": sha256_text(path + "|" + str(version)),
                "verified_at": now_iso(),
                "status": "verified" if ok else "unverified",
            }
        )
    return entries


def _project_mtime(directory: Path, markers: Iterable[str] = ()) -> str:
    stamps: list[float] = []
    try:
        stamps.append(directory.stat().st_mtime)
    except OSError:
        pass
    for marker in markers:
        try:
            stamps.append((directory / marker).stat().st_mtime)
        except OSError:
            continue
    if not stamps:
        return ""
    return datetime.fromtimestamp(max(stamps)).astimezone().isoformat(timespec="seconds")


def project_entry(project: dict[str, Any], root: str | None = None) -> dict[str, Any]:
    """Convert one scan record into a lightweight registry entry.

    The scan record only contains metadata, so this deliberately avoids
    hashing or reading the project tree.
    """
    path_text = str(project.get("path") or "")
    name = str(project.get("name") or Path(path_text).name or "project")
    markers = [str(item) for item in (project.get("markers") or [])]
    entry_type = str(project.get("type") or "project")
    mtime = str(project.get("mtime") or "")
    entry = {
        "id": f"{entry_type}:{name}",
        "type": entry_type,
        "name": name,
        "path": path_text,
        "root": str(root or project.get("root") or Path(path_text).parent),
        "domain": "general",
        "description": "Local project (" + ", ".join(markers) + ")" if markers else "Local project",
        "keywords": [name, *markers],
        "capabilities": [],
        "invocation": f"open {path_text}" if entry_type == "project" else (
            f"read {Path(path_text) / 'SKILL.md'}" if entry_type == "skill" else f"plugin root {path_text}"
        ),
        "hash": sha256_text(f"{path_text}|{mtime}"),
        "verified_at": now_iso(),
        "status": "scanned",
        "source": "scan",
        "markers": markers,
        "mtime": mtime,
        "drive": str(project.get("drive") or ""),
        "has_readme": bool(project.get("has_readme")),
    }
    return entry


def scan_projects(workspace: Path, limit: int = 50) -> list[dict[str, Any]]:
    """Scan the workspace root and its direct children (cheap registry path)."""
    workspace = Path(workspace)
    entries: list[dict[str, Any]] = []
    candidates = [workspace]
    try:
        candidates.extend(sorted(child for child in workspace.iterdir() if child.is_dir()))
    except OSError:
        pass
    for candidate in candidates[:limit]:
        if candidate.name in {".caprouter", ".git"}:
            continue
        if candidate.name.lower() in SKIP_DIR_NAMES:
            continue
        names = _names_for(candidate)
        present = _primary_markers(names)
        if not present:
            continue
        plausible, _why = _project_plausible(candidate, names, present)
        if not plausible:
            continue
        aux = _auxiliary_markers(names)
        record = {
            "path": str(candidate),
            "name": candidate.name,
            "drive": candidate.drive,
            "markers": present,
            "auxiliary_markers": aux,
            "type": "project",
            "mtime": _project_mtime(candidate, present),
            "has_readme": any(item.lower().startswith("readme") for item in aux),
        }
        entries.append(project_entry(record, root=str(workspace)))
    return entries


# ---------------------------------------------------------------------------
# project-root scan: drives and explicit roots, metadata only


class _ScanBudget:
    def __init__(self, *, max_dirs: int, max_projects: int, seconds: float) -> None:
        self.max_dirs = max(0, int(max_dirs))
        self.max_projects = max(0, int(max_projects))
        self.seconds = max(0.0, float(seconds))
        self.started = time.monotonic()
        self.dirs = 0
        self.projects = 0

    def elapsed(self) -> float:
        return round(time.monotonic() - self.started, 3)

    def exhausted(self) -> str | None:
        if self.dirs >= self.max_dirs:
            return "max_dirs"
        if self.projects >= self.max_projects:
            return "max_projects"
        if self.elapsed() >= self.seconds:
            return "time_budget"
        return None


def _dir_identity(entry: os.DirEntry[str], env_paths: set[str]) -> str | None:
    """Return a skip reason, or None when the directory may be scanned."""
    lowered = entry.name.lower()
    if lowered in SKIP_DIR_NAMES:
        return lowered
    if _norm_path(entry.path) in env_paths:
        return "system_path"
    try:
        stat_result = entry.stat(follow_symlinks=False)
    except OSError:
        return "stat_error"
    attributes = int(getattr(stat_result, "st_file_attributes", 0) or 0)
    if attributes & (FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM):
        return "hidden_or_system"
    if attributes & FILE_ATTRIBUTE_REPARSE_POINT or entry.is_symlink():
        return "reparse_point"
    return None


def _scan_one_root(
    root_info: dict[str, Any],
    *,
    max_depth: int,
    max_dirs: int,
    time_budget_seconds: float,
    max_projects: int,
) -> dict[str, Any]:
    root = Path(str(root_info.get("root") or ""))
    budget = _ScanBudget(max_dirs=max_dirs, max_projects=max_projects, seconds=time_budget_seconds)
    env_paths = _env_skip_paths()
    skipped: Counter[str] = Counter()
    projects: list[dict[str, Any]] = []
    errors = 0
    depth_limited = 0
    truncated_reason: str | None = None
    root_answer = {
        "root": str(root),
        "drive": str(root_info.get("drive") or (root.drive or "")),
        "kind": str(root_info.get("kind") or "project"),
        "type": str(root_info.get("type") or "unknown"),
        "system": bool(root_info.get("system")),
        "explicit": bool(root_info.get("explicit")),
    }
    if not root.is_dir():
        return {
            **root_answer,
            "dirs_scanned": 0,
            "projects": 0,
            "elapsed_seconds": 0.0,
            "truncated": False,
            "reason": "not_found",
            "errors": 1,
            "skipped": {},
        }

    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        reason = budget.exhausted()
        if reason:
            truncated_reason = reason
            break
        current, depth = stack.pop()
        budget.dirs += 1
        try:
            with os.scandir(current) as iterator:
                entries = list(iterator)
        except OSError:
            errors += 1
            continue

        names = {entry.name.lower() for entry in entries}
        markers = _primary_markers(names)
        auxiliary = _auxiliary_markers(names)
        entry_type: str | None = None
        weak_boundary = False
        # A drive root is always a container. Some drives have a stray .git or
        # package.json at the root; that must not stop the whole-drive walk.
        if depth > 0 or root_answer["kind"] != "drive":
            if "skill.md" in names:
                entry_type = "skill"
            elif (
                "plugin.json" in names
                and (current / "plugin.json").is_file()
            ) or (
                ".claude-plugin" in names
                and (current / ".claude-plugin" / "plugin.json").is_file()
            ):
                entry_type = "plugin"
            elif markers:
                explicit_root = depth == 0 and bool(root_answer.get("explicit"))
                plausible, why = _project_plausible(current, names, markers)
                if explicit_root or plausible:
                    entry_type = "project"
                else:
                    skipped[why] += 1
                    # Do not descend into a vendored tool tree; its support
                    # directories would produce more false projects.
                    weak_boundary = True

        if entry_type:
            name = current.name or (current.drive.rstrip(":\\") + ":" if current.drive else str(current))
            projects.append(
                {
                    "path": str(current),
                    "name": name,
                    "drive": current.drive,
                    "root": str(root),
                    "markers": markers,
                    "auxiliary_markers": auxiliary,
                    "type": entry_type,
                    "mtime": _project_mtime(current, markers),
                    "has_readme": any(item.startswith("readme") for item in names),
                }
            )
            budget.projects += 1
            # A project boundary ends the descent: source subdirectories are
            # part of the project, not separate projects.
            continue

        if weak_boundary:
            continue

        if depth >= max_depth:
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        depth_limited += 1
                except OSError:
                    continue
            continue
        for entry in entries:
            try:
                if not entry.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                skipped["stat_error"] += 1
                continue
            skip = _dir_identity(entry, env_paths)
            if skip:
                skipped[skip] += 1
                continue
            stack.append((Path(entry.path), depth + 1))

    return {
        **root_answer,
        "dirs_scanned": budget.dirs,
        "projects": len(projects),
        "elapsed_seconds": budget.elapsed(),
        "truncated": bool(truncated_reason),
        "reason": truncated_reason,
        "errors": errors,
        "depth_limited_dirs": depth_limited,
        "skipped": dict(skipped),
        "_projects": projects,
    }


def _root_info_for(raw: Path | str) -> dict[str, Any]:
    path = Path(raw).expanduser().resolve()
    text = str(path)
    is_drive_root = path.parent == path
    drive = (path.drive or "")[:2].upper()
    info = next((item for item in classify_drives() if item["drive"].upper() == drive), None)
    return {
        "drive": drive,
        "root": text,
        "kind": "drive" if is_drive_root else "project",
        "type": (info or {}).get("type", "project_root" if not is_drive_root else "unknown"),
        "system": bool((info or {}).get("system", drive[:1] == _system_drive())),
        "scan_default": bool((info or {}).get("scan_default")),
        "explicit": True,
    }


def scan_project_roots(
    roots: Iterable[Path | str] | None = None,
    *,
    drives: Iterable[str] | None = None,
    project_roots: Iterable[Path | str] | None = None,
    include_removable: bool = False,
    max_depth: int = DEFAULT_SCAN_DEPTH,
    max_dirs: int = DEFAULT_SCAN_DIRS,
    time_budget_seconds: float = DEFAULT_SCAN_SECONDS,
    max_projects: int = DEFAULT_SCAN_PROJECTS,
) -> dict[str, Any]:
    """Scan roots for project metadata only.

    Explicit project roots are scanned before drives, so the user-asserted
    path wins when both selectors overlap. Nothing inside a project is read,
    executed or written.
    """
    started = time.monotonic()
    if roots is not None:
        root_infos = [_root_info_for(item) for item in roots]
    else:
        root_infos = resolve_scan_roots(
            drives=drives,
            project_roots=project_roots,
            include_removable=include_removable,
        )
        root_infos.sort(key=lambda item: (item.get("kind") != "project", item.get("root", "")))

    root_reports: list[dict[str, Any]] = []
    projects: list[dict[str, Any]] = []
    warnings: list[str] = []
    if not root_infos:
        warnings.append("no scan roots resolved; pass --drive or --project-root")
    for info in root_infos:
        report = _scan_one_root(
            info,
            max_depth=max_depth,
            max_dirs=max_dirs,
            time_budget_seconds=time_budget_seconds,
            max_projects=max_projects,
        )
        projects.extend(report.pop("_projects", []))
        root_reports.append(report)
        if report.get("reason") == "not_found":
            warnings.append(f"scan root not found: {info.get('root')}")

    deduped: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    seen: dict[str, dict[str, Any]] = {}
    for item in projects:
        key = _norm_path(item["path"])
        if key in seen:
            duplicates.append(
                {
                    "path": item["path"],
                    "kept": seen[key]["path"],
                    "reason": "duplicate_path",
                }
            )
            continue
        seen[key] = item
        deduped.append(item)
    deduped.sort(key=lambda item: (str(item.get("type") or ""), _norm_path(item.get("path") or "")))

    by_drive: Counter[str] = Counter()
    by_type: Counter[str] = Counter()
    skipped: Counter[str] = Counter()
    for item in deduped:
        by_drive[str(item.get("drive") or "?")] += 1
        by_type[str(item.get("type") or "unknown")] += 1
    for report in root_reports:
        skipped.update(report.get("skipped") or {})

    elapsed = round(time.monotonic() - started, 3)
    truncated = [
        {
            "root": report["root"],
            "kind": report["kind"],
            "reason": report["reason"],
            "dirs_scanned": report["dirs_scanned"],
            "projects": report["projects"],
        }
        for report in root_reports
        if report.get("truncated")
    ]
    return {
        "schema": PROJECTS_SCAN_SCHEMA,
        "generated_at": now_iso(),
        "roots": root_reports,
        "projects": deduped,
        "duplicates": duplicates,
        "truncated": truncated,
        "skipped": dict(skipped),
        "warnings": warnings,
        "stats": {
            "roots": len(root_reports),
            "dirs_scanned": sum(int(report.get("dirs_scanned") or 0) for report in root_reports),
            "projects": len(deduped),
            "duplicates": len(duplicates),
            "errors": sum(int(report.get("errors") or 0) for report in root_reports),
            "skipped_dirs": sum(skipped.values()),
            "depth_limited_dirs": sum(int(report.get("depth_limited_dirs") or 0) for report in root_reports),
            "truncated_roots": len(truncated),
            "elapsed_seconds": elapsed,
            "by_drive": dict(sorted(by_drive.items())),
            "by_type": dict(sorted(by_type.items())),
        },
    }


def projects_scan_path(workspace: Path | str) -> Path:
    return Path(workspace) / CONFIG_DIR / "projects_scan.json"


def write_projects_scan(workspace: Path | str, result: dict[str, Any]) -> Path:
    path = projects_scan_path(workspace)
    write_json_atomic(path, result)
    return path


def load_projects_scan(workspace: Path | str) -> dict[str, Any]:
    data = read_json(projects_scan_path(workspace), {})
    return data if isinstance(data, dict) else {}


def summarize_scan(
    result: dict[str, Any],
    *,
    recent: int = 10,
    limit_bytes: int = SCAN_SUMMARY_BYTES,
) -> dict[str, Any]:
    """Build a small prompt-safe summary; the full list stays on disk."""
    projects = result.get("projects") or []
    ordered = sorted(projects, key=lambda item: str(item.get("mtime") or ""), reverse=True)
    recent_rows = [
        {
            "name": item.get("name"),
            "path": item.get("path"),
            "type": item.get("type"),
            "mtime": item.get("mtime"),
        }
        for item in ordered
    ]
    summary: dict[str, Any] = {
        "schema": "caprouter.projects-scan-summary.v1",
        "generated_at": result.get("generated_at"),
        "stats": result.get("stats") or {},
        "roots": [
            {
                "root": item.get("root"),
                "drive": item.get("drive"),
                "kind": item.get("kind"),
                "type": item.get("type"),
                "system": item.get("system"),
                "reason": item.get("reason"),
                "depth_limited_dirs": item.get("depth_limited_dirs"),
            }
            for item in (result.get("roots") or [])[:16]
        ],
        "truncated": (result.get("truncated") or [])[:16],
        "top_skipped": [
            {"reason": key, "count": value}
            for key, value in sorted(
                (result.get("skipped") or {}).items(),
                key=lambda item: (-int(item[1]), str(item[0])),
            )[:10]
        ],
        "recent_projects": recent_rows[: max(0, int(recent))],
        "warnings": (result.get("warnings") or [])[:8],
    }

    def encoded() -> bytes:
        return json.dumps(summary, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    while summary["recent_projects"] and len(encoded()) > limit_bytes:
        summary["recent_projects"].pop()
    while summary["truncated"] and len(encoded()) > limit_bytes:
        summary["truncated"].pop()
    while len(summary["top_skipped"]) > 5 and len(encoded()) > limit_bytes:
        summary["top_skipped"].pop()
    while len(summary["roots"]) > 1 and len(encoded()) > limit_bytes:
        summary["roots"].pop()
    while summary["warnings"] and len(encoded()) > limit_bytes:
        summary["warnings"].pop()
    while summary["top_skipped"] and len(encoded()) > limit_bytes:
        summary["top_skipped"].pop()
    return summary


def summary_size(summary: dict[str, Any]) -> int:
    return len(json.dumps(summary, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _merge(entries: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    merged: dict[str, dict[str, Any]] = {}
    paths: dict[str, str] = {}
    duplicates: list[dict[str, Any]] = []
    for entry in entries:
        key = entry["id"]
        path_key = _norm_path(entry.get("path") or "") if entry.get("path") else ""
        if path_key and path_key in paths and entry.get("type") != "mcp":
            keeper = merged[paths[path_key]]
            duplicates.append(
                {
                    "id": key,
                    "kept": keeper.get("path"),
                    "dropped": entry.get("path"),
                    "reason": "duplicate_path",
                }
            )
            continue
        if key not in merged:
            merged[key] = entry
            if path_key:
                paths[path_key] = key
            continue
        keeper = merged[key]
        duplicates.append(
            {
                "id": key,
                "kept": keeper.get("path"),
                "dropped": entry.get("path"),
                "reason": "duplicate_id",
            }
        )
        if keeper.get("status") != "verified" and entry.get("status") == "verified":
            merged[key] = entry
            if path_key:
                paths[path_key] = key
    return sorted(merged.values(), key=lambda item: (item["type"], item["id"])), duplicates


def build_registry(
    workspace: Path,
    skill_roots: list[Path] | None = None,
    plugin_roots: list[Path] | None = None,
    include_agents: bool = True,
    project_roots: list[Path] | None = None,
    scanned_projects: list[dict[str, Any]] | None = None,
    scan_budget: dict[str, Any] | None = None,
    include_mcp: bool = True,
) -> dict[str, Any]:
    workspace = Path(workspace)
    if skill_roots is None:
        skill_roots = [Path(item) for item in default_skill_roots(workspace)]
    else:
        skill_roots = [Path(item) for item in skill_roots]
    builtin = builtin_skill_root()
    if skill_roots and builtin is not None and builtin not in skill_roots:
        skill_roots.append(builtin)
    if plugin_roots is None:
        plugin_roots = [Path(item) for item in default_plugin_roots(workspace)]
    else:
        plugin_roots = [Path(item) for item in plugin_roots]
    project_roots = [Path(item) for item in (project_roots or [])]

    entries: list[dict[str, Any]] = []
    for root in skill_roots:
        entries.extend(scan_skill_root(root))
    for root in plugin_roots:
        entries.extend(scan_plugin_root(root))
    if include_agents:
        entries.extend(scan_agents())
    if include_mcp:
        entries.extend(scan_mcp_servers(workspace))
    entries.extend(scan_projects(workspace))
    installed_projects = installed_root(workspace) / "projects"
    if installed_projects.is_dir():
        entries.extend(scan_projects(installed_projects))

    budget = scan_budget if isinstance(scan_budget, dict) else {}
    scan_options = {
        "max_depth": int(budget.get("max_depth") or DEFAULT_SCAN_DEPTH),
        "max_dirs": int(budget.get("max_dirs") or DEFAULT_SCAN_DIRS),
        "time_budget_seconds": float(budget.get("time_budget_seconds") or DEFAULT_SCAN_SECONDS),
        "max_projects": int(budget.get("max_projects") or DEFAULT_SCAN_PROJECTS),
    }
    scanned_rows: list[dict[str, Any]] = []
    for root in project_roots:
        if not root.is_dir():
            continue
        result = scan_project_roots([root], **scan_options)
        for item in result.get("projects") or []:
            scanned_rows.append({**item, "_root": str(root)})
    if scanned_projects is None:
        saved = load_projects_scan(workspace)
        rows = saved.get("projects") if isinstance(saved, dict) else None
        scanned_projects = [
            item
            for item in (rows or [])
            if isinstance(item, dict) and Path(str(item.get("path") or "")).is_dir()
        ]
    for item in scanned_projects or []:
        if isinstance(item, dict) and item.get("path"):
            scanned_rows.append(item)

    existing_ids = {str(entry.get("id")) for entry in entries}
    existing_paths = {
        _norm_path(entry.get("path") or "")
        for entry in entries
        if entry.get("path")
    }
    for item in scanned_rows:
        path_key = _norm_path(item.get("path") or "")
        if not path_key or path_key in existing_paths:
            continue
        entry = project_entry(item, root=str(item.get("_root") or item.get("root") or ""))
        # A saved drive scan re-discovers skills/plugins that are already
        # indexed from configured roots; do not duplicate them in the report.
        if entry["type"] in {"skill", "plugin"} and entry["id"] in existing_ids:
            continue
        if entry["id"] in existing_ids:
            # Project directory names are not unique. Keep the first readable
            # id and disambiguate the rest by path instead of dropping them.
            digest = sha256_text(path_key).split(":", 1)[-1][:8]
            entry["id"] = f"{entry['id']}-{digest}"
            while entry["id"] in existing_ids:
                entry["id"] += "x"
        entries.append(entry)
        existing_ids.add(entry["id"])
        existing_paths.add(path_key)

    merged, duplicates = _merge(entries)
    stats: dict[str, int] = {}
    for entry in merged:
        stats[entry["type"]] = stats.get(entry["type"], 0) + 1
    return {
        "schema": "caprouter.registry.v1",
        "generated_at": now_iso(),
        "workspace": str(workspace),
        "roots": {
            "skills": [str(item) for item in skill_roots],
            "plugins": [str(item) for item in plugin_roots],
            "projects": [str(item) for item in project_roots],
        },
        "stats": {"total": len(merged), **stats},
        "entries": merged,
        "duplicates": duplicates,
    }
