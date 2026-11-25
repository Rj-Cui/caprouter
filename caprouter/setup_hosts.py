"""Host setup helper: print or merge an MCP server entry for common hosts.

The default is print-only. ``write=True`` backs up an existing JSON config
before merging. Cherry Studio stores MCP configuration in SQLite; it is
print-only here.

``deepseek-harness`` is different from the JSON-config hosts: DSH installs
npm-style bundles, so this module generates a complete config-only bundle
directory the user adds once in DSH → Plugins → Add local plugin. That is
still one manual click; no profile file is touched from here.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .util import read_json, write_json_atomic

SETUP_SCHEMA = "caprouter.host-setup.v1"
DSH_HOST = "deepseek-harness"
DSH_BUNDLE_DIRNAME = "caprouter-dsh-bundle"
HOSTS = (
    "claude-code",
    "claude-desktop",
    "cursor",
    "vscode",
    "cherry",
    DSH_HOST,
)


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def server_entry(workspace: Path | str, host: str | None = None) -> dict[str, Any]:
    script = repo_root() / "bin" / "caprouter.py"
    args = [str(script), "mcp", "--workspace", str(Path(workspace).expanduser().resolve())]
    if host and host in HOSTS and host != DSH_HOST:
        # Every host gets the small core tool surface by default and tells the
        # server which host it is, so cross-host capabilities can be flagged.
        args.extend(["--tools", "core", "--host", host])
    return {
        "command": "python",
        "args": args,
        "env": {"PYTHONUTF8": "1"},
    }


def python_command() -> str:
    """Absolute interpreter for an external host to spawn.

    ``sys.executable`` beats a bare ``python`` because Windows ships a Store
    alias that resolves to a non-runner placeholder. Callers that pass an
    explicit interpreter override this.
    """
    return sys.executable or ("python" if os.name == "nt" else "python3")


def dsh_home() -> Path:
    env = os.environ.get("DSH_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".dsh"


def dsh_skills_root(home: Path | str | None = None) -> Path:
    return (Path(home).expanduser() if home else dsh_home()) / "skills"


def _yaml_str(value: Path | str) -> str:
    """Single-quoted YAML scalar; doubling quotes keeps paths literal."""
    return "'" + str(value).replace("'", "''") + "'"


def _portable_patch_text() -> str:
    """Patch for distribution: contains no path from the packaging machine.

    The interpreter is resolved when the row mounts (``CAPROUTER_PYTHON`` wins,
    then the platform default), the server starts as ``python -m caprouter`` so
    a pip-installed CapRouter works on any machine, and the workspace is left to
    CapRouter's own resolution (``CAPROUTER_WORKSPACE``, a workspace above the
    working directory, then ``~/CapRouter/workspace``).
    """
    return "\n".join(
        [
            "- insert:",
            "    - id: mcp-caprouter",
            "      name: '@deepseek-ai/dsh-mcp-client'",
            "      config:",
            "        serverName: caprouter",
            "        transport: stdio",
            "        command: !!js 'process.env.CAPROUTER_PYTHON || (process.platform === \"win32\" ? \"py\" : \"python3\")'",
            "        args:",
            "          - -m",
            "          - caprouter",
            "          - mcp",
            "          - --tools",
            "          - core",
            "          - --host",
            "          - deepseek-harness",
            "        startupTimeoutMs: 15000",
            "        toolCallTimeoutMs: 120000",
            "        failOnStartupError: true",
            "",
        ]
    )


def dsh_patch_text(
    workspace: Path | str,
    *,
    python_exe: str | None = None,
    portable: bool = False,
) -> str:
    if portable:
        return _portable_patch_text()
    workspace = Path(workspace).expanduser().resolve()
    script = (repo_root() / "bin" / "caprouter.py").resolve()
    python_exe = python_exe or python_command()
    lines = [
        "- insert:",
        "    - id: mcp-caprouter",
        "      name: '@deepseek-ai/dsh-mcp-client'",
        "      config:",
        "        serverName: caprouter",
        "        transport: stdio",
        f"        command: {_yaml_str(python_exe)}",
        "        args:",
        f"          - {_yaml_str(script)}",
        "          - mcp",
        "          - --workspace",
        f"          - {_yaml_str(workspace)}",
        "          - --tools",
        "          - core",
        "          - --host",
        "          - deepseek-harness",
        "        startupTimeoutMs: 15000",
        "        toolCallTimeoutMs: 120000",
        "        failOnStartupError: true",
        "",
    ]
    return "\n".join(lines)


def dsh_manifest(include_icon: bool) -> dict[str, Any]:
    exports = {"./locale/*.json": "./locale/*.json"}
    files = ["cordis.patch.yml", "locale/*.json", "docs", "README.md"]
    if include_icon:
        exports["./icon"] = "./icon.png"
        files.append("icon.png")
    return {
        "name": "@local/caprouter-mcp",
        "version": __version__,
        "private": True,
        "type": "module",
        "description": "Connects the local CapRouter capability bus to DSH as an MCP server.",
        "exports": exports,
        "files": files,
        "dsh": {"bundle": {"patch": "./cordis.patch.yml"}},
    }


def _dsh_detail(
    workspace: Path,
    python_exe: str,
    script: Path,
    *,
    portable: bool = False,
) -> str:
    """Detail page text shown in DSH → Plugins for this bundle."""
    if portable:
        runtime = (
            "## How it runs\n\n"
            "- interpreter: `CAPROUTER_PYTHON`, else `py` on Windows and `python3` elsewhere\n"
            "- command: `<python> -m caprouter mcp --tools core --host deepseek-harness`\n"
            "- transport: stdio\n\n"
            "Requires Python 3.11+ and `pip install caprouter`. The workspace is resolved at "
            "startup (`CAPROUTER_WORKSPACE`, a workspace above the session directory, then "
            "`~/CapRouter/workspace`); nothing from this machine is embedded.\n\n"
        )
    else:
        runtime = (
            "## How it runs\n\n"
            f"- command: `{python_exe}`\n"
            f"- args: `{script} mcp --workspace {workspace} --tools core --host deepseek-harness`\n"
            "- transport: stdio\n\n"
            "The server reads the workspace named in the patch. If you move either the CapRouter "
            "repository or the workspace, regenerate the bundle so the paths stay valid.\n\n"
        )
    return (
        "# CapRouter capability bus\n\n"
        "Adds the CapRouter MCP server to DSH. CapRouter is a local ledger for the skills, plugins, "
        "MCP servers and CLI agents already installed on this machine: it indexes them, routes a need "
        "to a candidate, runs deterministic probes and acceptance checks, and records outcomes.\n\n"
        "## What it adds\n\n"
        "The `core` tool profile exposes ten `mcp__caprouter__*` tools: `capability_search`, "
        "`capability_get`, `capability_invoke`, `capability_probe`, `capability_probe_record`, "
        "`capability_verify`, `capability_record_outcome`, `capability_list_gaps`, "
        "`capability_install` and `capability_uninstall`. Switch the patch to `--tools full` for all "
        "twenty, including discovery, card drafts, versions, health, duplicates, lock, drift and deps.\n\n"
        + runtime
        + "## Limits\n\n"
        "- CapRouter never installs or executes anything without an explicit approval: `install` copies "
        "files, and command execution needs a reviewed Card permission plus `allow_execute`.\n"
        "- Search abstains when it is not confident; an empty result is a supported answer.\n"
        "- Probe evidence marked `static` proves that a capability is present and invokable, not that "
        "it produces good output. Behavioral evidence must be recorded by the host.\n"
        "- The workspace is local state: its registry, probes and outcomes stay on this machine and are "
        "not published by this bundle.\n\n"
        "## Review status\n\n"
        "This is a local directory bundle installed for review; it is not published to a public "
        "registry. Regenerate it with `caprouter setup --host deepseek-harness --write --portable`.\n"
    )


def _dsh_readme(workspace: Path, python_exe: str, script: Path, *, portable: bool = False) -> str:
    if portable:
        return (
            "# CapRouter for DeepSeek Harness\n\n"
            "This directory is a config-only DSH bundle generated by "
            "`caprouter setup --host deepseek-harness --write --portable`. "
            "It contains no path from the machine that produced it.\n\n"
            "## Before installing\n\n"
            "1. Python 3.11+ on PATH. The row uses `py` on Windows and `python3` elsewhere; "
            "set `CAPROUTER_PYTHON` to an absolute interpreter to override it.\n"
            "2. CapRouter installed for that interpreter:\n\n"
            "   ```bash\n   pip install caprouter\n   ```\n\n"
            "   From a checkout, run `pip install .` in the repository root instead.\n\n"
            "## Install\n\n"
            "1. DSH → **Plugins** → **Add plugin** (local directory).\n"
            "2. Pick this folder.\n"
            "3. Start a new session; the ten core `mcp__caprouter__*` tools appear.\n\n"
            "## Workspace\n\n"
            "The server resolves the workspace in this order: `CAPROUTER_WORKSPACE`, a "
            "workspace found above the session directory, then the default "
            "`~/CapRouter/workspace` (`%USERPROFILE%\\CapRouter\\workspace` on Windows). "
            "Create it once with:\n\n"
            "```bash\ncaprouter init --workspace ~/CapRouter/workspace\n```\n\n"
            "All CapRouter state (registry, probes, outcomes) stays in that workspace on "
            "this machine; this bundle publishes none of it.\n"
        )
    return (
        "# CapRouter for DeepSeek Harness\n\n"
        "This directory is a config-only DSH bundle generated by "
        "`caprouter setup --host deepseek-harness --write`.\n\n"
        "Install it in DSH once:\n\n"
        "1. Open DSH → **Plugins** → **Add plugin** (local directory).\n"
        "2. Pick this folder.\n"
        "3. Start a new session; the ten core `mcp__caprouter__*` tools appear.\n\n"
        "The bundle passes `--tools core` (search/get/invoke/probe/probe_record/"
        "verify/record_outcome/list_gaps/install/uninstall). "
        "For all twenty tools, change that arg to `full` in cordis.patch.yml.\n\n"
        "It launches:\n\n"
        f"- command: `{python_exe}`\n"
        f"- args: `{script} mcp --workspace {workspace}`\n\n"
        "This form embeds this machine's paths. Regenerate it if you move the CapRouter "
        "repository. For a bundle you can hand to another machine, use `--portable`.\n"
    )


def write_dsh_bundle(
    workspace: Path | str,
    bundle_dir: Path | str,
    *,
    python_exe: str | None = None,
    portable: bool = False,
) -> dict[str, Any]:
    """Write a self-contained DSH config-only bundle. No profile file is touched.

    ``portable=True`` omits every path of this machine so the directory can be
    handed to another host: the interpreter is resolved when the row mounts and
    the server starts as ``python -m caprouter``.
    """
    workspace = Path(workspace).expanduser().resolve()
    bundle_dir = Path(bundle_dir).expanduser()
    python_exe = python_exe or python_command()
    script = (repo_root() / "bin" / "caprouter.py").resolve()
    icon_src = repo_root() / ".claude-plugin" / "icon.png"
    include_icon = icon_src.exists()
    files: dict[str, str] = {
        "package.json": json.dumps(dsh_manifest(include_icon), ensure_ascii=False, indent=2) + "\n",
        "cordis.patch.yml": dsh_patch_text(workspace, python_exe=python_exe, portable=portable),
        "locale/en.json": json.dumps(
            {
                "meta": {
                    "title": "CapRouter capability bus",
                    "description": (
                        "Connects the local CapRouter MCP server, adding capability_* tools that "
                        "search, inspect, probe, install and verify local skills, plugins and scripts "
                        "in the configured CapRouter workspace."
                    ),
                    "detail": "docs/DETAIL.md",
                }
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        "locale/zh.json": json.dumps(
            {
                "meta": {
                    "title": "CapRouter 能力总线",
                    "description": (
                        "连接本机 CapRouter MCP 服务，新增 capability_* 工具，用于在指定工作区内"
                        "搜索、查看、探测、安装与验证本机技能、插件和脚本。"
                    ),
                    "detail": "docs/DETAIL.md",
                }
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        "docs/DETAIL.md": _dsh_detail(workspace, python_exe, script, portable=portable),
        "README.md": _dsh_readme(workspace, python_exe, script, portable=portable),
    }
    bundle_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for rel, content in files.items():
        target = bundle_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(str(target))
    if include_icon:
        icon_target = bundle_dir / "icon.png"
        shutil.copy2(icon_src, icon_target)
        written.append(str(icon_target))
    return {
        "bundle_dir": str(bundle_dir),
        "files": written,
        "icon": include_icon,
        "portable": portable,
        "patch": str(bundle_dir / "cordis.patch.yml"),
    }


def link_installed_skills(
    workspace: Path | str,
    skills_root: Path | str,
    *,
    force: bool = False,
) -> dict[str, list[str]]:
    """Copy CapRouter-managed skills into a host skill root.

    DSH discovers ``<root>/<name>/SKILL.md``; CapRouter keeps managed skills in
    ``<workspace>/.caprouter/installed/skills/<name>``. This is the bridge that
    makes "installed with CapRouter" actually visible in DSH. It never edits
    the source copy, and skips existing targets unless ``force`` is set.
    """
    source = Path(workspace).expanduser().resolve() / ".caprouter" / "installed" / "skills"
    root = Path(skills_root).expanduser()
    linked: list[str] = []
    skipped: list[str] = []
    if not source.is_dir():
        return {
            "linked": linked,
            "skipped": skipped,
            "errors": [
                f"No CapRouter-managed skills yet ({source} does not exist); install one with caprouter install first"
            ],
        }
    root.mkdir(parents=True, exist_ok=True)
    for child in sorted(source.iterdir()):
        if not child.is_dir():
            continue
        if not (child / "SKILL.md").is_file():
            skipped.append(f"{child.name}: no SKILL.md")
            continue
        target = root / child.name
        if target.exists():
            if not force:
                skipped.append(f"{child.name}: already exists (use --force to replace)")
                continue
            shutil.rmtree(target)
        shutil.copytree(child, target)
        linked.append(child.name)
    return {"linked": linked, "skipped": skipped, "errors": []}


def default_config_path(host: str, workspace: Path | str | None = None) -> Path | None:
    home = Path.home()
    appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
    if host == "claude-desktop":
        candidates = [
            appdata / "Claude" / "claude_desktop_config.json",
            home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
            home / ".config" / "Claude" / "claude_desktop_config.json",
        ]
    elif host == "cursor":
        candidates = [home / ".cursor" / "mcp.json"]
    elif host == "vscode":
        candidates = [
            appdata / "Code" / "User" / "mcp.json",
            home / "Library" / "Application Support" / "Code" / "User" / "mcp.json",
            home / ".config" / "Code" / "User" / "mcp.json",
        ]
    elif host == "claude-code":
        candidates = [Path(workspace or ".").expanduser().resolve() / ".mcp.json"]
    else:
        return None
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def build_payload(host: str, workspace: Path | str) -> dict[str, Any]:
    entry = server_entry(workspace, host)
    if host == "vscode":
        entry = {"type": "stdio", **entry}
        return {"servers": {"caprouter": entry}}
    return {"mcpServers": {"caprouter": entry}}


def setup_host(
    host: str,
    workspace: Path | str,
    *,
    config_path: Path | str | None = None,
    write: bool = False,
    link_skills: bool = False,
    skills_root: Path | str | None = None,
    force: bool = False,
    portable: bool = False,
) -> dict[str, Any]:
    if host not in HOSTS:
        raise ValueError(f"unknown host: {host}; choose from {', '.join(HOSTS)}")
    if host == DSH_HOST:
        return _setup_dsh(
            workspace,
            bundle_dir=config_path,
            write=write,
            link_skills=link_skills,
            skills_root=skills_root,
            force=force,
            portable=portable,
        )
    if portable:
        raise ValueError("--portable is only supported for --host deepseek-harness")
    if link_skills:
        raise ValueError("--link-skills is only supported for --host deepseek-harness")
    path = Path(config_path).expanduser() if config_path else default_config_path(host, workspace)
    payload = build_payload(host, workspace)
    result: dict[str, Any] = {
        "schema": SETUP_SCHEMA,
        "host": host,
        "workspace": str(Path(workspace).expanduser().resolve()),
        "config_path": str(path) if path else None,
        "payload": payload,
        "written": False,
        "backup": None,
    }
    if not write:
        return result
    if host == "cherry":
        raise ValueError(
            "Cherry Studio stores MCP configuration in SQLite; paste the payload in "
            "Settings → MCP Servers instead of writing a JSON file"
        )
    if path is None:
        raise ValueError(f"no default config path for host: {host}")
    existing = read_json(path, {})
    existing = existing if isinstance(existing, dict) else {}
    key = next(iter(payload))
    block = existing.get(key) if isinstance(existing.get(key), dict) else {}
    block = dict(block)
    block["caprouter"] = payload[key]["caprouter"]
    existing[key] = block
    if path.exists():
        backup = path.with_suffix(path.suffix + ".caprouter.bak")
        shutil.copy2(path, backup)
        result["backup"] = str(backup)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, existing)
    result["written"] = True
    return result


def _setup_dsh(
    workspace: Path | str,
    *,
    bundle_dir: Path | str | None = None,
    write: bool = False,
    link_skills: bool = False,
    skills_root: Path | str | None = None,
    force: bool = False,
    portable: bool = False,
) -> dict[str, Any]:
    workspace = Path(workspace).expanduser().resolve()
    target = (
        Path(bundle_dir).expanduser()
        if bundle_dir
        else workspace / DSH_BUNDLE_DIRNAME
    )
    root = Path(skills_root).expanduser() if skills_root else dsh_skills_root()
    python_exe = python_command()
    result: dict[str, Any] = {
        "schema": SETUP_SCHEMA,
        "host": DSH_HOST,
        "workspace": str(workspace),
        "bundle_dir": str(target),
        "python": python_exe,
        "portable": portable,
        "patch_text": dsh_patch_text(workspace, python_exe=python_exe, portable=portable),
        "written": False,
        "files": [],
        "skills_root": str(root),
        "linked_skills": [],
        "skipped_skills": [],
        "link_errors": [],
        "next_steps": [
            "Open DeepSeek Harness -> Plugins -> Add local plugin directory and pick bundle_dir",
            "Start a new session and confirm the mcp__caprouter__* tools appear",
            "To make CapRouter-managed skills visible to DSH, rerun with --link-skills",
        ],
    }
    if write:
        bundle = write_dsh_bundle(workspace, target, python_exe=python_exe, portable=portable)
        result.update(
            {
                "written": True,
                "bundle_dir": bundle["bundle_dir"],
                "files": bundle["files"],
                "icon": bundle["icon"],
            }
        )
    if link_skills:
        linked = link_installed_skills(workspace, root, force=force)
        result["linked_skills"] = linked["linked"]
        result["skipped_skills"] = linked["skipped"]
        result["link_errors"] = linked["errors"]
    return result
