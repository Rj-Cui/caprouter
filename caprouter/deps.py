"""Read-only dependency planning and checking.

This module never executes pip/npm/pnpm/yarn/uv/poetry. It reads manifests and
lockfiles, produces exact commands for a human or host to run, and checks what
is already present in the current interpreter / capability-local node_modules.
"""
from __future__ import annotations

import re
import shutil
from importlib import metadata
from pathlib import Path
from typing import Any

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    tomllib = None  # type: ignore[assignment]

from .capabilities import get_capability
from .util import read_json, safe_name

DEPS_PLAN_SCHEMA = "caprouter.deps-plan.v1"
DEPS_CHECK_SCHEMA = "caprouter.deps-check.v1"
MAX_TEXT = 200_000
NODE_LOCKFILES = ("package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lockb")
PY_LOCKFILES = ("uv.lock", "poetry.lock", "Pipfile.lock")
REQUIREMENT_FILES = ("requirements.lock", "requirements.txt", "requirements-dev.txt")
RISK_SCRIPTS = {"preinstall", "install", "postinstall", "prepare"}


def _read_text(path: Path) -> str:
    try:
        if path.stat().st_size > MAX_TEXT:
            return ""
        return path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""


def _capability_root(workspace: Path | str, capability_id: str) -> tuple[dict[str, Any], Path]:
    card = get_capability(workspace, capability_id)
    if not card:
        raise ValueError(f"capability not found: {capability_id}")
    root = Path(str(card.get("path") or "")).expanduser()
    if not root.is_dir():
        raise ValueError(f"capability path is not a directory: {root}")
    return card, root


def _normalize_name(value: str) -> str:
    name = re.split(r"[<>=!~;\[\s@]", str(value).strip(), 1)[0]
    return name.lower().replace("_", "-")


def _package_json(root: Path) -> tuple[list[dict[str, Any]], set[str], set[str]]:
    data = read_json(root / "package.json", {})
    declared: list[dict[str, Any]] = []
    risks: set[str] = set()
    warnings: set[str] = set()
    if not isinstance(data, dict):
        return declared, risks, warnings
    for key in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        block = data.get(key)
        if not isinstance(block, dict):
            continue
        for name, spec in block.items():
            declared.append(
                {
                    "ecosystem": "node",
                    "name": str(name),
                    "spec": str(spec),
                    "source": f"package.json:{key}",
                }
            )
    scripts = data.get("scripts")
    if isinstance(scripts, dict):
        found = sorted(str(key) for key in scripts if str(key).lower() in RISK_SCRIPTS)
        if found:
            risks.add("postinstall")
            warnings.add("package.json lifecycle scripts present: " + ", ".join(found))
    return declared, risks, warnings


def _pyproject(root: Path) -> tuple[list[dict[str, Any]], set[str], set[str]]:
    declared: list[dict[str, Any]] = []
    risks: set[str] = set()
    warnings: set[str] = set()
    path = root / "pyproject.toml"
    if not path.is_file() or tomllib is None:
        return declared, risks, warnings
    try:
        data = tomllib.loads(_read_text(path))
    except (ValueError, TypeError):
        warnings.add("pyproject.toml could not be parsed")
        return declared, risks, warnings
    project = data.get("project") if isinstance(data.get("project"), dict) else {}
    for spec in project.get("dependencies") or []:
        declared.append(
            {
                "ecosystem": "python",
                "name": _normalize_name(str(spec)),
                "spec": str(spec),
                "source": "pyproject.toml:project.dependencies",
            }
        )
    optional = project.get("optional-dependencies")
    if isinstance(optional, dict):
        for group, specs in optional.items():
            for spec in specs or []:
                declared.append(
                    {
                        "ecosystem": "python",
                        "name": _normalize_name(str(spec)),
                        "spec": str(spec),
                        "source": f"pyproject.toml:optional-dependencies.{group}",
                    }
                )
    build_system = data.get("build-system")
    if isinstance(build_system, dict) and build_system.get("build-backend"):
        risks.add("build_backend")
        warnings.add(
            "pyproject build-backend is present; installing from source may execute build code"
        )
    return declared, risks, warnings


def _requirements(root: Path) -> tuple[list[dict[str, Any]], set[str], set[str], dict[str, bool]]:
    declared: list[dict[str, Any]] = []
    risks: set[str] = set()
    warnings: set[str] = set()
    hashes: dict[str, bool] = {}
    for name in REQUIREMENT_FILES:
        path = root / name
        if not path.is_file():
            continue
        lines = [
            line.strip()
            for line in _read_text(path).splitlines()
            if line.strip() and not line.strip().startswith(("#", "-"))
        ]
        if not lines:
            continue
        hashed = all("--hash=" in line for line in lines)
        hashes[name] = hashed
        for line in lines:
            if line.startswith(("git+", "http://", "https://", "file:")):
                risks.add("non_registry_source")
                declared.append(
                    {
                        "ecosystem": "python",
                        "name": "",
                        "spec": line,
                        "source": name,
                    }
                )
                continue
            if "==" not in line and "===" not in line:
                risks.add("unpinned")
            declared.append(
                {
                    "ecosystem": "python",
                    "name": _normalize_name(line),
                    "spec": line,
                    "source": name,
                }
            )
        if not hashed:
            risks.add("no_hash")
            warnings.add(f"{name} does not pin hashes for every requirement")
    return declared, risks, warnings, hashes


def _node_lock(root: Path) -> tuple[list[str], set[str], set[str]]:
    lockfiles: list[str] = []
    risks: set[str] = set()
    warnings: set[str] = set()
    for name in NODE_LOCKFILES:
        if (root / name).is_file():
            lockfiles.append(name)
    package_lock = root / "package-lock.json"
    if package_lock.is_file():
        data = read_json(package_lock, {})
        if isinstance(data, dict):
            packages = data.get("packages")
            if isinstance(packages, dict):
                install_scripts = [
                    key
                    for key, value in packages.items()
                    if isinstance(value, dict) and value.get("hasInstallScript")
                ]
                if install_scripts:
                    risks.add("postinstall")
                    warnings.add(
                        f"package-lock.json marks {len(install_scripts)} package(s) with install scripts; "
                        "use npm ci --ignore-scripts"
                    )
    return lockfiles, risks, warnings


def _python_lock(root: Path) -> list[str]:
    return [name for name in PY_LOCKFILES if (root / name).is_file()]


def _commands(
    root: Path,
    *,
    has_package_json: bool,
    has_pyproject: bool,
    node_lockfiles: list[str],
    py_lockfiles: list[str],
    requirement_hashes: dict[str, bool],
) -> list[dict[str, Any]]:
    commands: list[dict[str, Any]] = []
    if has_package_json:
        if "package-lock.json" in node_lockfiles:
            argv = ["npm", "ci", "--ignore-scripts"]
        elif "pnpm-lock.yaml" in node_lockfiles:
            argv = ["pnpm", "install", "--frozen-lockfile", "--ignore-scripts"]
        elif "yarn.lock" in node_lockfiles:
            argv = ["yarn", "install", "--frozen-lockfile", "--ignore-scripts"]
        elif "bun.lockb" in node_lockfiles:
            argv = ["bun", "install", "--frozen-lockfile", "--ignore-scripts"]
        else:
            argv = ["npm", "install", "--ignore-scripts"]
        commands.append(
            {
                "ecosystem": "node",
                "argv": argv,
                "cwd": str(root),
                "mutates": "capability-local",
                "requires_lockfile": "package-lock.json" not in node_lockfiles,
            }
        )
    if "uv.lock" in py_lockfiles:
        commands.append(
            {
                "ecosystem": "python",
                "argv": ["uv", "sync", "--frozen"],
                "cwd": str(root),
                "mutates": "capability-local",
                "requires_lockfile": False,
            }
        )
    elif "poetry.lock" in py_lockfiles:
        commands.append(
            {
                "ecosystem": "python",
                "argv": ["poetry", "install", "--no-root", "--no-interaction"],
                "cwd": str(root),
                "mutates": "capability-local",
                "requires_lockfile": False,
            }
        )
    elif "Pipfile.lock" in py_lockfiles:
        commands.append(
            {
                "ecosystem": "python",
                "argv": ["pipenv", "sync"],
                "cwd": str(root),
                "mutates": "capability-local",
                "requires_lockfile": False,
            }
        )
    else:
        for name in ("requirements.lock", "requirements.txt", "requirements-dev.txt"):
            if not (root / name).is_file():
                continue
            argv = ["python", "-m", "pip", "install", "-r", name]
            if requirement_hashes.get(name):
                argv = ["python", "-m", "pip", "install", "--require-hashes", "-r", name]
            commands.append(
                {
                    "ecosystem": "python",
                    "argv": argv,
                    "cwd": str(root),
                    "mutates": "capability-local",
                    "requires_lockfile": not requirement_hashes.get(name, False),
                }
            )
            break
        if not commands and has_pyproject:
            commands.append(
                {
                    "ecosystem": "python",
                    "argv": ["python", "-m", "pip", "install", "."],
                    "cwd": str(root),
                    "mutates": "capability-local",
                    "requires_lockfile": False,
                }
            )
    return commands


def _network_hosts(declared: list[dict[str, Any]]) -> list[str]:
    ecosystems = {item["ecosystem"] for item in declared}
    hosts: set[str] = set()
    if "python" in ecosystems:
        hosts.update({"pypi.org", "files.pythonhosted.org"})
    if "node" in ecosystems:
        hosts.add("registry.npmjs.org")
    if any(
        str(item.get("spec") or "").startswith(("git+", "http://", "https://"))
        for item in declared
    ):
        hosts.update({"github.com", "codeload.github.com", "raw.githubusercontent.com"})
    return sorted(hosts)


def _tools(commands: list[dict[str, Any]]) -> dict[str, bool]:
    names = {str(item["argv"][0]) for item in commands if item.get("argv")}
    return {name: bool(shutil.which(name)) for name in sorted(names)}


def plan_dependencies(workspace: Path | str, capability_id: str) -> dict[str, Any]:
    card, root = _capability_root(workspace, capability_id)
    package_declared, package_risks, package_warnings = _package_json(root)
    py_declared, py_risks, py_warnings = _pyproject(root)
    req_declared, req_risks, req_warnings, req_hashes = _requirements(root)
    node_lockfiles, lock_risks, lock_warnings = _node_lock(root)
    py_lockfiles = _python_lock(root)
    declared = [*package_declared, *py_declared, *req_declared]
    risks = package_risks | py_risks | req_risks | lock_risks
    warnings = set(package_warnings) | set(py_warnings) | set(req_warnings) | set(lock_warnings)
    has_package_json = (root / "package.json").is_file()
    has_pyproject = (root / "pyproject.toml").is_file()
    if (has_package_json or has_pyproject or req_declared) and not (
        node_lockfiles or py_lockfiles or req_hashes
    ):
        risks.add("no_lockfile")
        warnings.add("dependencies are declared but no lockfile was found")
    commands = _commands(
        root,
        has_package_json=has_package_json,
        has_pyproject=has_pyproject,
        node_lockfiles=node_lockfiles,
        py_lockfiles=py_lockfiles,
        requirement_hashes=req_hashes,
    )
    manifest = read_json(root / "manifest.json", {})
    manifest_deps = manifest.get("dependencies") if isinstance(manifest, dict) else None
    if isinstance(manifest_deps, list):
        for name in manifest_deps:
            declared.append(
                {
                    "ecosystem": "unknown",
                    "name": str(name),
                    "spec": str(name),
                    "source": "manifest.json:dependencies",
                }
            )
    return {
        "schema": DEPS_PLAN_SCHEMA,
        "id": capability_id,
        "path": str(root),
        "declared": declared,
        "lockfiles": [*node_lockfiles, *py_lockfiles, *sorted(req_hashes)],
        "risk_flags": sorted(risks),
        "warnings": sorted(warnings),
        "commands": commands,
        "network_hosts": _network_hosts(declared),
        "tools": _tools(commands),
        "manifest_dependencies": manifest_deps if isinstance(manifest_deps, list) else [],
        "note": "plan only; CapRouter does not execute these commands",
    }


def check_dependencies(workspace: Path | str, capability_id: str) -> dict[str, Any]:
    plan = plan_dependencies(workspace, capability_id)
    root = Path(plan["path"])
    satisfied: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in plan["declared"]:
        ecosystem = str(item.get("ecosystem") or "unknown")
        name = str(item.get("name") or "").strip()
        key = (ecosystem, name.lower())
        if key in seen:
            continue
        seen.add(key)
        if not name:
            unknown.append(item)
            continue
        if ecosystem == "python":
            try:
                version = metadata.version(name)
            except metadata.PackageNotFoundError:
                missing.append({**item, "checked": "current-interpreter"})
            except (ValueError, OSError):
                unknown.append(item)
            else:
                satisfied.append({**item, "version": version, "checked": "current-interpreter"})
        elif ecosystem == "node":
            local = root / "node_modules" / name
            workspace_env = (
                Path(workspace) / ".caprouter" / "envs" / safe_name(capability_id)
                / "node_modules" / name
            )
            if local.is_dir() or workspace_env.is_dir():
                satisfied.append(
                    {
                        **item,
                        "checked": str(local if local.is_dir() else workspace_env),
                    }
                )
            else:
                missing.append({**item, "checked": "capability node_modules"})
        else:
            unknown.append(item)
    return {
        "schema": DEPS_CHECK_SCHEMA,
        "id": capability_id,
        "path": str(root),
        "satisfied": satisfied,
        "missing": missing,
        "unknown": unknown,
        "tools": plan["tools"],
        "risk_flags": plan["risk_flags"],
        "note": "checked current interpreter and capability-local node_modules; nothing was executed",
    }
