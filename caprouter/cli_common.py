"""Shared CLI plumbing: one workspace resolver, one exit-code contract, one output path.

Before this module existed, ``cli.py`` carried three workspace resolvers, two
JSON printers and an implicit exit-code convention. Every command now goes
through the helpers here.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from .audit import set_audit_workspace
from .mcp_server import resolve_workspace
from .paths import config_path, find_workspace, registry_path
from .util import read_json

#: Success, including previews and "nothing to do" no-ops.
EXIT_OK = 0
#: The operation failed, or the requested state is invalid.
EXIT_FAILED = 1
#: Usage error, or the operation needs an explicit flag to proceed.
EXIT_NEEDS_FLAG = 2
#: CapRouter is disabled globally.
EXIT_DISABLED = 3


def configure_console(stream: Any) -> None:
    """Keep the console encoding when it already covers CJK.

    Forcing UTF-8 on a legacy ``cp936``/``cp950``/``cp932`` console turns every
    Chinese path into mojibake. UTF-8 is only forced when the current codec
    cannot represent CJK at all (``ascii``, ``cp1252``, ...).
    """
    if not hasattr(stream, "reconfigure"):
        return
    encoding = str(getattr(stream, "encoding", "") or "")
    try:
        "工作区".encode(encoding or "ascii")
    except (LookupError, UnicodeEncodeError):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass
        return
    try:
        stream.reconfigure(errors="replace")
    except (ValueError, OSError):
        pass


def workspace_from_args(args: Any) -> Path:
    """Resolve the workspace for a command: ``--workspace``, else walk up, else cwd."""
    if getattr(args, "workspace", None):
        workspace = Path(args.workspace).expanduser().resolve()
    else:
        found = find_workspace(Path.cwd())
        workspace = found if found else Path.cwd().resolve()
    set_audit_workspace(workspace)
    return workspace


def capability_workspace(args: Any) -> Path:
    """Resolve the workspace for capability-bus commands (env var aware)."""
    workspace = resolve_workspace(getattr(args, "workspace", None))
    set_audit_workspace(workspace)
    return workspace


def print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def fail(message: Any, code: int = EXIT_FAILED) -> int:
    """Print an error to stderr and return an exit code."""
    print(str(message), file=sys.stderr)
    return code


def ask(prompt: str, default: str, *, assume_yes: bool) -> str:
    if assume_yes or not sys.stdin.isatty():
        return default
    try:
        value = input(f"{prompt} [{default}]: ").strip()
    except EOFError:
        # Non-interactive consumers (CI, MCP hosts, piped stdin) may still report a TTY.
        return default
    return value or default


def load_config(workspace: Path) -> dict[str, Any]:
    data = read_json(config_path(workspace))
    if not isinstance(data, dict):
        raise SystemExit(
            f'CapRouter is not initialized in {workspace}; run: caprouter init --workspace "{workspace}"'
        )
    return data


def registry_entries(workspace: Path) -> list[dict[str, Any]]:
    data = read_json(registry_path(workspace), {})
    entries = data.get("entries") if isinstance(data, dict) else None
    return [item for item in (entries or []) if isinstance(item, dict)]
