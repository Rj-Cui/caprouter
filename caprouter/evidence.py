"""Read-only capability evidence extraction.

The collector reads manifests, README command examples and entrypoint names.
It never imports project code, never executes a command, and never writes into
the project directory. Evidence is deliberately small: strings are capped,
dependencies are names only, and full file contents are not stored.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .scan import infer_capabilities, infer_domain_from_text
from .util import first_prose_line, parse_frontmatter, read_json, sha256_text, split_keywords

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - fallback for very old runtimes
    tomllib = None  # type: ignore[assignment]

EVIDENCE_SCHEMA = "caprouter.capability-evidence.v1"
MAX_FILE_CHARS = 12_000
MAX_COMMANDS = 20
MAX_DEPS = 40
MAX_SUMMARY_CHARS = 4_000
README_NAMES = ("README.md", "readme.md", "README.txt")
COMMAND_START_RE = re.compile(
    r"^(?:pip|pip3|npm|npx|pnpm|yarn|uv|uvx|python|python3|node|bash|sh|pwsh|powershell|"
    r"cargo|go|dotnet|java|docker|make|cmake|git|curl|wget)\b",
    re.IGNORECASE,
)
FENCE_RE = re.compile(r"```[a-zA-Z0-9_-]*\s*\n(.*?)```", re.DOTALL)
PROMPT_RE = re.compile(r"^\s*(?:\$|>)\s+(.+)$")
BADGE_RE = re.compile(r"\[!\[[^\]]*\]\([^)]*\)\]\([^)]*\)")
URL_RE = re.compile(r"https?://[^\s)\]>\"']+")
NETWORK_DEPS = {
    "requests", "httpx", "aiohttp", "urllib3", "axios", "node-fetch", "got",
    "openai", "anthropic", "google-genai", "google-generativeai", "boto3",
}
WRITE_DEPS = {
    "openpyxl", "python-docx", "docx", "xlsxwriter", "fs-extra", "python-pptx",
    "pptx", "reportlab", "pillow", "sharp",
}
GUI_DEPS = {"electron", "tauri", "@tauri-apps/api", "pyqt5", "pyqt6", "pyside6", "tkinter"}


def _read(path: Path, limit: int = MAX_FILE_CHARS) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return handle.read(limit)
    except OSError:
        return ""


def _toml(path: Path) -> dict[str, Any]:
    if tomllib is None or not path.is_file():
        return {}
    text = _read(path)
    if not text:
        return {}
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _package_names(value: Any) -> list[str]:
    names: list[str] = []
    if isinstance(value, list):
        for item in value:
            text = str(item).strip()
            if not text:
                continue
            name = re.split(r"[\s<>=!~\[]", text, maxsplit=1)[0].strip()
            if name:
                names.append(name)
    elif isinstance(value, dict):
        names.extend(str(key).strip() for key in value if str(key).strip())
    return list(dict.fromkeys(names))


def _commands_from_block(block: str) -> list[str]:
    commands: list[str] = []
    for raw in block.splitlines():
        line = raw.strip()
        prompt = PROMPT_RE.match(line)
        if prompt:
            line = prompt.group(1).strip()
        if not line or line.startswith("#"):
            continue
        if COMMAND_START_RE.match(line):
            commands.append(line[:300])
    return commands


def _readme_commands(text: str) -> list[str]:
    commands: list[str] = []
    for block in FENCE_RE.findall(text):
        commands.extend(_commands_from_block(block))
    commands.extend(_commands_from_block(text))
    return list(dict.fromkeys(commands))[:MAX_COMMANDS]


def _has_non_badge_url(text: str) -> bool:
    cleaned = BADGE_RE.sub("", text)
    return bool(URL_RE.search(cleaned))


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        value = line.strip()
        if value and not value.startswith(("#", "[", "!", "<")):
            return value[:200]
    return ""


def collect_evidence(root: Path | str, entry_type: str = "project") -> dict[str, Any]:
    """Collect bounded metadata evidence for a local capability."""
    root = Path(root).expanduser().resolve()
    artifacts: list[str] = []
    warnings: list[str] = []
    metadata: dict[str, Any] = {"name": root.name, "description": "", "keywords": [], "version": ""}
    scripts: list[dict[str, str]] = []
    entrypoints: list[str] = []
    dependencies: dict[str, list[str]] = {"runtime": [], "dev": []}
    commands: list[str] = []
    manifest_hashes: dict[str, str] = {}

    package_text = _read(root / "package.json")
    if package_text:
        artifacts.append("package.json")
        manifest_hashes["package.json"] = sha256_text(package_text)
        try:
            package = json.loads(package_text)
        except json.JSONDecodeError:
            package = None
        if isinstance(package, dict):
            metadata["name"] = str(package.get("name") or metadata["name"])
            metadata["description"] = str(package.get("description") or "")
            metadata["version"] = str(package.get("version") or "")
            metadata["keywords"] = split_keywords(package.get("keywords"))
            raw_scripts = package.get("scripts")
            if isinstance(raw_scripts, dict):
                for name, value in raw_scripts.items():
                    scripts.append({"name": str(name), "command": str(value)[:300]})
            raw_bin = package.get("bin")
            if isinstance(raw_bin, str):
                entrypoints.append(metadata["name"])
            elif isinstance(raw_bin, dict):
                entrypoints.extend(str(key) for key in raw_bin)
            dependencies["runtime"].extend(_package_names(package.get("dependencies")))
            dependencies["dev"].extend(_package_names(package.get("devDependencies")))
        else:
            warnings.append("package.json is not a JSON object")

    pyproject_text = _read(root / "pyproject.toml")
    if pyproject_text:
        artifacts.append("pyproject.toml")
        manifest_hashes["pyproject.toml"] = sha256_text(pyproject_text)
        pyproject = _toml(root / "pyproject.toml")
        project = pyproject.get("project") if isinstance(pyproject.get("project"), dict) else {}
        if project:
            metadata["name"] = str(project.get("name") or metadata["name"])
            metadata["description"] = str(project.get("description") or metadata["description"])
            metadata["version"] = str(project.get("version") or metadata["version"])
            metadata["keywords"] = metadata["keywords"] or split_keywords(project.get("keywords"))
            for key in ("scripts", "gui-scripts"):
                values = project.get(key)
                if isinstance(values, dict):
                    entrypoints.extend(str(name) for name in values)
            dependencies["runtime"].extend(_package_names(project.get("dependencies")))
            optional = project.get("optional-dependencies")
            if isinstance(optional, dict):
                for values in optional.values():
                    dependencies["dev"].extend(_package_names(values))
        tool = pyproject.get("tool")
        poetry = tool.get("poetry") if isinstance(tool, dict) else None
        if isinstance(poetry, dict):
            metadata["name"] = str(poetry.get("name") or metadata["name"])
            metadata["description"] = str(poetry.get("description") or metadata["description"])
            values = poetry.get("scripts")
            if isinstance(values, dict):
                entrypoints.extend(str(name) for name in values)
            dependencies["runtime"].extend(_package_names(poetry.get("dependencies")))

    cargo_text = _read(root / "Cargo.toml")
    if cargo_text:
        artifacts.append("Cargo.toml")
        manifest_hashes["Cargo.toml"] = sha256_text(cargo_text)
        cargo = _toml(root / "Cargo.toml")
        package = cargo.get("package") if isinstance(cargo.get("package"), dict) else {}
        if package:
            metadata["name"] = str(package.get("name") or metadata["name"])
            metadata["description"] = str(package.get("description") or metadata["description"])
            metadata["version"] = str(package.get("version") or metadata["version"])
        bins = cargo.get("bin")
        if isinstance(bins, list):
            for item in bins:
                if isinstance(item, dict) and item.get("name"):
                    entrypoints.append(str(item["name"]))
        dependencies["runtime"].extend(_package_names(cargo.get("dependencies")))

    if (root / "go.mod").is_file():
        artifacts.append("go.mod")
        go_text = _read(root / "go.mod")
        manifest_hashes["go.mod"] = sha256_text(go_text)
        match = re.search(r"(?m)^\s*module\s+(\S+)", go_text)
        if match:
            metadata["name"] = metadata["name"] or match.group(1).rsplit("/", 1)[-1]

    for gradle_name in ("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"):
        gradle_file = root / gradle_name
        if not gradle_file.is_file():
            continue
        gradle_text = _read(gradle_file)
        artifacts.append(gradle_name)
        manifest_hashes[gradle_name] = sha256_text(gradle_text)
        if not metadata["description"]:
            match = re.search(r"(?m)^\s*description\s*=\s*[\"']([^\"']+)[\"']", gradle_text)
            if match:
                metadata["description"] = match.group(1).strip()
        match = re.search(r"rootProject\.name\s*=\s*[\"']([^\"']+)[\"']", gradle_text)
        if match and metadata["name"] == root.name:
            metadata["name"] = match.group(1).strip()

    pom_file = root / "pom.xml"
    if pom_file.is_file():
        pom_text = _read(pom_file)
        artifacts.append("pom.xml")
        manifest_hashes["pom.xml"] = sha256_text(pom_text)
        if not metadata["description"]:
            match = re.search(r"<description>\s*([^<]+?)\s*</description>", pom_text, re.IGNORECASE | re.DOTALL)
            if match:
                metadata["description"] = re.sub(r"\s+", " ", match.group(1)).strip()
        match = re.search(r"<name>\s*([^<]+?)\s*</name>", pom_text, re.IGNORECASE | re.DOTALL)
        if match and metadata["name"] == root.name:
            metadata["name"] = re.sub(r"\s+", " ", match.group(1)).strip()

    cmake_file = root / "CMakeLists.txt"
    if cmake_file.is_file():
        cmake_text = _read(cmake_file)
        artifacts.append("CMakeLists.txt")
        manifest_hashes["CMakeLists.txt"] = sha256_text(cmake_text)
        match = re.search(r"(?im)^\s*project\s*\(\s*([A-Za-z0-9_.+-]+)", cmake_text)
        if match and metadata["name"] == root.name:
            metadata["name"] = match.group(1).strip()

    if entry_type == "skill" and (root / "SKILL.md").is_file():
        artifacts.append("SKILL.md")
        skill_text = _read(root / "SKILL.md")
        manifest_hashes["SKILL.md"] = sha256_text(skill_text)
        front, _body = parse_frontmatter(skill_text)
        metadata["name"] = str(front.get("name") or metadata["name"])
        metadata["description"] = str(front.get("description") or metadata["description"])
        metadata["version"] = str(front.get("version") or metadata["version"])
        metadata["keywords"] = metadata["keywords"] or split_keywords(front.get("keywords"))

    plugin_manifest = root / ".claude-plugin" / "plugin.json"
    if not plugin_manifest.is_file():
        plugin_manifest = root / "plugin.json"
    if plugin_manifest.is_file():
        artifacts.append(plugin_manifest.relative_to(root).as_posix())
        plugin_text = _read(plugin_manifest)
        manifest_hashes[plugin_manifest.name] = sha256_text(plugin_text)
        data = read_json(plugin_manifest, {})
        if isinstance(data, dict):
            metadata["name"] = str(data.get("name") or metadata["name"])
            metadata["description"] = str(data.get("description") or metadata["description"])
            metadata["version"] = str(data.get("version") or metadata["version"])
            metadata["keywords"] = metadata["keywords"] or split_keywords(data.get("keywords"))

    readme_text = ""
    for name in README_NAMES:
        readme_text = _read(root / name)
        if readme_text:
            artifacts.append(name)
            break
    if readme_text:
        commands.extend(_readme_commands(readme_text))
        if not metadata["description"]:
            metadata["description"] = first_prose_line(readme_text)
        if not metadata["description"]:
            metadata["description"] = _first_line(readme_text)

    for folder in ("scripts", "bin"):
        path = root / folder
        if not path.is_dir():
            continue
        try:
            names = sorted(
                child.name
                for child in path.iterdir()
                if child.is_file() and child.suffix.lower() in {".py", ".sh", ".ps1", ".js", ".mjs", ".ts", ".bat", ".cmd"}
            )
        except OSError:
            names = []
        if names:
            artifacts.append(f"{folder}/")
            if folder == "bin":
                entrypoints.extend(names)

    commands = list(dict.fromkeys(commands))[:MAX_COMMANDS]
    entrypoints = list(dict.fromkeys(entrypoints))
    dependencies["runtime"] = list(dict.fromkeys(dependencies["runtime"]))[:MAX_DEPS]
    dependencies["dev"] = list(dict.fromkeys(dependencies["dev"]))[:MAX_DEPS]
    all_deps = set(dependencies["runtime"]) | set(dependencies["dev"])
    scripts_dir = (root / "scripts").is_dir()
    bin_dir = (root / "bin").is_dir()
    signals = {
        "has_readme": bool(readme_text),
        "has_build": any(
            (root / name).is_file()
            for name in (
                "build.gradle", "build.gradle.kts", "settings.gradle",
                "settings.gradle.kts", "pom.xml", "CMakeLists.txt",
            )
        ),
        "has_scripts_dir": scripts_dir,
        "has_bin_dir": bin_dir,
        "has_cli": bool(entrypoints),
        "has_scripts": bool(scripts),
        "has_commands": bool(commands),
        "has_shell": bool(scripts or scripts_dir or bin_dir or commands),
        "has_network": bool(all_deps & NETWORK_DEPS) or _has_non_badge_url(readme_text),
        "has_write": bool(all_deps & WRITE_DEPS)
        or any(word in readme_text.lower() for word in ("write", "save", "export", "create", "写", "保存", "导出")),
        "has_gui": bool(all_deps & GUI_DEPS)
        or any(word in readme_text.lower() for word in ("gui", "desktop", "桌面", "界面")),
    }
    summary_parts = [
        metadata["name"],
        metadata["description"],
        " ".join(metadata["keywords"]),
        " ".join(entrypoints),
        " ".join(item["name"] for item in scripts),
        " ".join(dependencies["runtime"]),
        readme_text[:2_000],
    ]
    summary_text = " ".join(part for part in summary_parts if part)[:MAX_SUMMARY_CHARS]
    return {
        "schema": EVIDENCE_SCHEMA,
        "root": str(root),
        "type": entry_type,
        "artifacts": list(dict.fromkeys(artifacts)),
        "manifest_hashes": manifest_hashes,
        "metadata": metadata,
        "entrypoints": entrypoints[:30],
        "scripts": scripts[:30],
        "commands": commands,
        "dependencies": dependencies,
        "signals": signals,
        "capability_hints": sorted(set(infer_capabilities(summary_text))),
        "domain_hint": infer_domain_from_text(
            metadata["name"],
            metadata["description"],
            metadata["keywords"],
            root.parts,
        ),
        "summary_text": summary_text,
        "warnings": warnings,
    }


def summarize_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    """Small evidence view suitable for a prompt or CLI output."""
    return {
        "root": evidence.get("root"),
        "type": evidence.get("type"),
        "metadata": evidence.get("metadata") or {},
        "artifacts": evidence.get("artifacts") or [],
        "entrypoints": evidence.get("entrypoints") or [],
        "scripts": [item.get("name") for item in (evidence.get("scripts") or []) if isinstance(item, dict)],
        "commands": evidence.get("commands") or [],
        "dependencies": evidence.get("dependencies") or {},
        "signals": evidence.get("signals") or {},
        "capability_hints": evidence.get("capability_hints") or [],
        "domain_hint": evidence.get("domain_hint"),
        "warnings": evidence.get("warnings") or [],
    }
