from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any

from .audit import append_audit
from .paths import config_path, find_workspace_near_plugin, plans_dir, scratch_dir
from .state import is_enabled
from .util import read_json

DENY_TOOLS = {"WebSearch", "WebFetch", "Task", "Agent", "NotebookEdit", "MultiEdit"}
WRITE_TOOLS = {"Write", "Edit"}
SHELL_TOOLS = {"Bash", "PowerShell"}


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def _split_commands(command: str) -> list[str]:
    segments: list[str] = []
    buffer: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(command):
        char = command[index]
        if quote:
            buffer.append(char)
            if char == quote:
                quote = None
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            buffer.append(char)
            index += 1
            continue
        pair = command[index : index + 2]
        if pair in {"&&", "||"}:
            segments.append("".join(buffer))
            buffer = []
            index += 2
            continue
        if char in {";", "|", "\n"}:
            segments.append("".join(buffer))
            buffer = []
            index += 1
            continue
        buffer.append(char)
        index += 1
    segments.append("".join(buffer))
    return [segment.strip() for segment in segments if segment.strip()]


_CAPROUTER_EXECUTABLES = {"caprouter", "caprouter.exe", "caprouter.cmd", "caprouter.bat"}
_PYTHON_EXECUTABLES = {"python", "python.exe", "python3", "python3.exe"}


def _unsafe_shell_syntax(command: str) -> str | None:
    """Reject metacharacters that could turn an allowed command into another one.

    The allowlist only permits caprouter invocations (and ``cd``), so any
    redirection, command substitution, subshell or background separator is
    outside that contract. Quotes are respected: ``--need "a > b"`` is fine.
    """
    quote: str | None = None
    index = 0
    while index < len(command):
        char = command[index]
        if quote:
            if char == "\\" and quote == '"' and index + 1 < len(command):
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            index += 1
            continue
        if char in {">", "<", "`", "(", ")"}:
            return f"unsafe shell syntax: {char!r}"
        if char == "&":
            if command[index:index + 2] == "&&":
                index += 2
                continue
            return "unsafe shell syntax: '&'"
        if char == "$" and command[index:index + 2] in {"$(", "${"}:
            return "unsafe shell syntax: command substitution"
        index += 1
    if quote:
        return "unsafe shell syntax: unterminated quote"
    return None


def _is_plugin_caprouter_script(token: str, cwd: Path, plugin_root: Path) -> bool:
    """Only the plugin's own ``bin/caprouter.py`` may run through Python."""
    target = Path(token.strip('"').strip("'"))
    if not target.is_absolute():
        target = cwd / target
    try:
        expected = (Path(plugin_root) / "bin" / "caprouter.py").resolve()
        return target.resolve() == expected
    except OSError:
        return False


def _python_argv_is_caprouter(argv: list[str], cwd: Path, plugin_root: Path) -> bool:
    if len(argv) < 2:
        return False
    if argv[1] == "-m":
        return len(argv) >= 3 and argv[2] == "caprouter"
    if argv[1].startswith("-"):
        # -c, -, -E, -I, ... are arbitrary Python execution.
        return False
    return _is_plugin_caprouter_script(argv[1], cwd, plugin_root)


def _is_allowed_command(segment: str, cwd: Path, plugin_root: Path) -> bool:
    text = segment.strip()
    if not text:
        return False
    try:
        argv = shlex.split(text, posix=True)
    except ValueError:
        return False
    if not argv:
        return False
    executable = Path(argv[0].lower()).name
    if executable in _CAPROUTER_EXECUTABLES:
        return True
    if executable == "cd":
        return len(argv) <= 2 or (len(argv) == 3 and argv[1].lower() == "/d")
    if executable in _PYTHON_EXECUTABLES:
        return _python_argv_is_caprouter(argv, cwd, plugin_root)
    if executable in {"uv", "uv.exe"}:
        if len(argv) < 3 or argv[1] != "run":
            return False
        rest = argv[2:]
        runner = Path(rest[0].lower()).name
        if runner in _CAPROUTER_EXECUTABLES:
            return True
        if runner in _PYTHON_EXECUTABLES:
            return _python_argv_is_caprouter(rest, cwd, plugin_root)
        return False
    if executable in {"uvx", "uvx.exe"}:
        return len(argv) >= 2 and Path(argv[1].lower()).name in _CAPROUTER_EXECUTABLES
    return False


def _allowed_write(path_text: str, cwd: Path, workspace: Path) -> bool:
    if not path_text:
        return False
    target = Path(path_text)
    if not target.is_absolute():
        target = cwd / target
    return _inside(target, plans_dir(workspace)) or _inside(target, scratch_dir(workspace))


def decide(payload: dict[str, Any], plugin_root: Path | None = None) -> dict[str, str]:
    if not is_enabled():
        return {"decision": "allow", "reason": "CapRouter is disabled"}
    tool = str(payload.get("tool_name") or payload.get("tool") or "")
    tool_input = payload.get("tool_input") or payload.get("input") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}
    cwd_text = payload.get("cwd") or os.getcwd()
    cwd = Path(cwd_text)
    root = Path(plugin_root) if plugin_root is not None else Path(__file__).resolve().parent.parent
    workspace = find_workspace_near_plugin(cwd)
    if workspace is None:
        return {"decision": "allow", "reason": "not a CapRouter workspace"}

    config = read_json(config_path(workspace), {})
    guard_config = config.get("guard") if isinstance(config, dict) else {}
    if not isinstance(guard_config, dict):
        guard_config = {}
    mode = str(config.get("mode") or guard_config.get("mode") or "orchestrator")
    if mode == "bus":
        # In bus mode the host owns the loop; the MCP capability bus gates
        # command execution per card instead of the host-wide guard.
        return {"decision": "allow", "reason": "bus mode: host owns the loop; capability invocation is gated by the bus"}
    allow_native = bool(guard_config.get("allow_native_subagents", False))
    allow_web = bool(guard_config.get("allow_web", False))

    if tool in {"Task", "Agent"}:
        if allow_native:
            return {"decision": "allow", "reason": "native subagents allowed by config"}
        return {
            "decision": "deny",
            "reason": "CapRouter: native subagents are disabled; dispatch through caprouter run",
        }

    if tool in {"WebSearch", "WebFetch"}:
        if allow_web:
            return {"decision": "allow", "reason": "web access allowed by config"}
        return {
            "decision": "deny",
            "reason": "CapRouter: direct web access is disabled; retrieval must run in a dispatched node",
        }

    if tool in {"NotebookEdit", "MultiEdit"}:
        return {
            "decision": "deny",
            "reason": f"CapRouter: {tool} is not allowed; use the CLI and plan files",
        }

    if tool in WRITE_TOOLS:
        path_text = str(tool_input.get("file_path") or tool_input.get("path") or "")
        if _allowed_write(path_text, cwd, workspace):
            return {"decision": "allow", "reason": "write is inside the plan/scratch area"}
        return {
            "decision": "deny",
            "reason": "CapRouter: writes are limited to .caprouter/plans and .caprouter/scratch",
        }

    if tool in SHELL_TOOLS:
        command = str(tool_input.get("command") or "")
        if not command.strip():
            return {"decision": "deny", "reason": "CapRouter: empty shell command"}
        unsafe = _unsafe_shell_syntax(command)
        if unsafe:
            return {"decision": "deny", "reason": f"CapRouter: {unsafe}"}
        segments = _split_commands(command)
        if segments and all(_is_allowed_command(segment, cwd, root) for segment in segments):
            return {"decision": "allow", "reason": "command is a CapRouter command"}
        return {
            "decision": "deny",
            "reason": "CapRouter: shell is limited to caprouter commands (and cd); dispatch work through caprouter run",
        }

    return {"decision": "allow", "reason": "tool is not execution-capable"}


def _read_stdin() -> str:
    try:
        return sys.stdin.buffer.read().decode("utf-8", errors="replace")
    except AttributeError:
        return sys.stdin.read()


def main() -> int:
    raw = _read_stdin()
    if not raw.strip():
        return 0
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return 0
    if not isinstance(payload, dict):
        return 0
    plugin_root = Path(os.environ.get("CLAUDE_PLUGIN_ROOT") or Path(__file__).resolve().parent.parent)
    try:
        result = decide(payload, plugin_root)
    except Exception:
        # A broken guard must not brick the host; fail open, and report through doctor.
        return 0
    try:
        append_audit(
            {
                "event": "pre_tool_use",
                "tool": payload.get("tool_name") or payload.get("tool"),
                "decision": result.get("decision"),
                "reason": result.get("reason"),
                "cwd": payload.get("cwd"),
                "session_id": payload.get("session_id"),
            }
        )
    except OSError:
        pass
    if result.get("decision") == "deny":
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": result.get("reason") or "denied by CapRouter",
            }
        }
        print(json.dumps(output, ensure_ascii=False))
    return 0
