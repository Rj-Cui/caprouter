"""Read-only inventory of MCP server definitions on this machine.

Only configuration files are read. Server processes are never started.
Environment values and secret-looking arguments are redacted; only env keys
are kept in the registry.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Iterable

from .util import now_iso, safe_name, sha256_text

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    tomllib = None  # type: ignore[assignment]

MCP_SCHEMA = "caprouter.mcp-inventory.v1"

_SECRET_KEY = re.compile(
    r"(api[-_]?key|token|secret|password|passwd|authorization|credential)", re.I
)
_SECRET_VALUE = re.compile(
    r"(sk-[A-Za-z0-9_\-]{8,}|ghp_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9\-]{10,}|Bearer\s+[A-Za-z0-9._\-]+)"
)
_SECRET_FLAG = re.compile(r"^--?(api[-_]?key|token|secret|password|authorization)$", re.I)
_SECRET_FLAG_EQ = re.compile(
    r"^(--?(?:api[-_]?key|token|secret|password|authorization)=).+$", re.I
)
_QUERY_SECRET = re.compile(
    r"([?&](?:key|token|api_key|apikey|access_token)=)[^&]+", re.I
)


def _redact_text(value: str) -> str:
    text = _SECRET_VALUE.sub("***", str(value))
    return _QUERY_SECRET.sub(r"\1***", text)


def _redact_args(args: Iterable[Any]) -> list[Any]:
    out: list[Any] = []
    redact_next = False
    for arg in args:
        if redact_next:
            out.append("***")
            redact_next = False
            continue
        if isinstance(arg, str):
            if _SECRET_FLAG.match(arg):
                out.append(arg)
                redact_next = True
                continue
            match = _SECRET_FLAG_EQ.match(arg)
            if match:
                out.append(match.group(1) + "***")
                continue
            out.append(_redact_text(arg))
        else:
            out.append(arg)
    return out


def _env_keys(env: Any) -> list[str]:
    if not isinstance(env, dict):
        return []
    return sorted(str(key) for key in env)


def candidate_configs(workspace: Path | str | None = None) -> list[tuple[str, Path]]:
    """Known host config files, filtered to those that exist."""
    home = Path.home()
    appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
    candidates: list[tuple[str, Path]] = [
        ("claude-code", home / ".claude.json"),
        ("claude-code", home / ".claude" / "settings.json"),
        (
            "claude-code",
            appdata / "CherryStudio" / "Data" / "Agents" / ".claude" / ".claude.json",
        ),
        ("claude-desktop", appdata / "Claude" / "claude_desktop_config.json"),
        (
            "claude-desktop",
            home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
        ),
        ("claude-desktop", home / ".config" / "Claude" / "claude_desktop_config.json"),
        ("cherry", appdata / "CherryStudio" / "Data" / "mcp.json"),
        ("cherry", appdata / "CherryStudio" / "Data" / "config" / "mcp.json"),
        ("cherry", home / "Library" / "Application Support" / "CherryStudio" / "Data" / "mcp.json"),
        ("cherry", home / ".config" / "CherryStudio" / "Data" / "mcp.json"),
        ("cursor", home / ".cursor" / "mcp.json"),
        ("codex", home / ".codex" / "config.toml"),
        ("gemini", home / ".gemini" / "settings.json"),
        ("vscode", appdata / "Code" / "User" / "mcp.json"),
        ("vscode", home / "Library" / "Application Support" / "Code" / "User" / "mcp.json"),
        ("vscode", home / ".config" / "Code" / "User" / "mcp.json"),
    ]
    if workspace:
        ws = Path(workspace)
        candidates.extend(
            [
                ("workspace", ws / ".mcp.json"),
                ("cursor-project", ws / ".cursor" / "mcp.json"),
                ("vscode-project", ws / ".vscode" / "mcp.json"),
                ("codex-project", ws / ".codex" / "config.toml"),
            ]
        )
    seen: set[str] = set()
    found: list[tuple[str, Path]] = []
    for host, path in candidates:
        key = str(path).lower()
        if key in seen or not path.is_file():
            continue
        seen.add(key)
        found.append((host, path))
    return found


def read_config(path: Path | str) -> Any:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if path.suffix.lower() == ".toml":
        if tomllib is None:
            return None
        try:
            return tomllib.loads(text)
        except (ValueError, TypeError):
            return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _iter_servers(
    data: Any, scope: str | None = None
) -> Iterable[tuple[str, dict[str, Any], str | None]]:
    if not isinstance(data, dict):
        return
    for key in ("mcpServers", "servers"):
        block = data.get(key)
        if isinstance(block, dict):
            for name, config in block.items():
                if isinstance(config, dict):
                    yield str(name), config, scope
    # Codex config.toml uses [mcp_servers.<name>].
    codex = data.get("mcp_servers")
    if isinstance(codex, dict):
        for name, config in codex.items():
            if isinstance(config, dict):
                yield str(name), config, scope
    # Claude Code ~/.claude.json stores per-project servers under "projects".
    projects = data.get("projects")
    if isinstance(projects, dict):
        for project_path, project_data in projects.items():
            if isinstance(project_data, dict):
                yield from _iter_servers(project_data, scope=str(project_path))


def _entry(
    host: str,
    config_path: Path,
    name: str,
    config: dict[str, Any],
    scope: str | None = None,
) -> dict[str, Any]:
    raw_args = config.get("args")
    args = _redact_args(raw_args if isinstance(raw_args, list) else [])
    command = config.get("command")
    url = config.get("url")
    transport = str(config.get("type") or "").strip().lower()
    if not transport:
        transport = "stdio" if command else "http" if url else "unknown"
    if transport in {"streamable-http", "streamablehttp"}:
        transport = "http"
    invocation = ""
    if command:
        invocation = " ".join([str(command), *[str(item) for item in args]])
    elif url:
        invocation = _redact_text(str(url))

    signature_data = {
        "command": str(command or ""),
        "args": [str(item) for item in args],
        "url": _redact_text(str(url or "")),
        "transport": transport,
    }
    signature = sha256_text(
        json.dumps(signature_data, sort_keys=True, ensure_ascii=False)
    )
    text = " ".join(
        [name, str(command or ""), " ".join(str(item) for item in args), str(url or "")]
    ).lower()
    capabilities = {"invoke"}
    for token, capability in (
        ("search", "retrieve"),
        ("fetch", "retrieve"),
        ("read", "retrieve"),
        ("sql", "retrieve"),
        ("db", "retrieve"),
        ("browser", "operate_gui"),
        ("git", "transform"),
        ("write", "transform"),
        ("code", "generate"),
    ):
        if token in text:
            capabilities.add(capability)
    keywords = sorted(
        {name.lower()} | {word for word in re.split(r"[^a-z0-9_\-]+", text) if len(word) >= 2}
    )[:20]
    return {
        "id": f"mcp:{safe_name(name)}",
        "type": "mcp",
        "name": name,
        "path": str(config_path),
        "root": str(config_path.parent),
        "host": host,
        "hosts": [host],
        "paths": [str(config_path)],
        "scope": scope or "",
        "domain": "general",
        "description": (
            f"MCP server {name} ({transport}) from {host}"
            + (f" [{scope}]" if scope else "")
        ),
        "version": "",
        "keywords": keywords,
        "capabilities": sorted(capabilities),
        "invocation": invocation[:300],
        "hash": signature,
        "verified_at": now_iso(),
        "status": "verified" if (command or url) else "unverified",
        "transport": transport,
        "env_keys": _env_keys(config.get("env")),
        "managed": False,
        "redacted": True,
    }


def scan_mcp_servers(
    workspace: Path | str | None = None,
    extra_configs: Iterable[Any] | None = None,
    *,
    include_defaults: bool = True,
) -> list[dict[str, Any]]:
    """Return one entry per distinct MCP server definition.

    The same server defined in multiple hosts is merged into one entry with
    ``hosts`` and ``paths`` lists. Same name with different command/url gets a
    hash suffix to keep ids unique.
    """
    configs = list(candidate_configs(workspace)) if include_defaults else []
    for item in extra_configs or []:
        if isinstance(item, (tuple, list)) and len(item) == 2:
            configs.append((str(item[0]), Path(item[1])))
        else:
            configs.append(("explicit", Path(str(item))))

    rows: list[dict[str, Any]] = []
    for host, path in configs:
        if not Path(path).is_file():
            continue
        data = read_config(path)
        if data is None:
            continue
        for name, config, scope in _iter_servers(data):
            rows.append(_entry(host, Path(path), name, config, scope))

    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (str(row["id"]), str(row["hash"]))
        if key not in merged:
            merged[key] = row
            continue
        keeper = merged[key]
        keeper["hosts"] = sorted(set(keeper.get("hosts") or []) | set(row.get("hosts") or []))
        keeper["paths"] = sorted(set(keeper.get("paths") or []) | set(row.get("paths") or []))
        if not keeper.get("scope") and row.get("scope"):
            keeper["scope"] = row["scope"]

    result = sorted(merged.values(), key=lambda item: (item["name"].lower(), item["hash"]))
    by_id: dict[str, list[dict[str, Any]]] = {}
    for row in result:
        by_id.setdefault(str(row["id"]), []).append(row)
    for identifier, group in by_id.items():
        if len(group) < 2:
            continue
        for row in group:
            digest = str(row["hash"]).split(":", 1)[-1][:8]
            row["id"] = f"{identifier}-{digest}"
    return result


def inventory_summary(
    workspace: Path | str | None = None,
    extra_configs: Iterable[Any] | None = None,
    *,
    include_defaults: bool = True,
) -> dict[str, Any]:
    entries = scan_mcp_servers(
        workspace, extra_configs=extra_configs, include_defaults=include_defaults
    )
    return {
        "schema": MCP_SCHEMA,
        "count": len(entries),
        "hosts": sorted({host for entry in entries for host in entry.get("hosts") or []}),
        "entries": entries,
    }
