from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from .audit import append_audit
from .paths import config_path, find_workspace_near_plugin
from .state import is_enabled
from .util import read_json

TASK_VERBS = (
    "调研", "搜索", "检索", "写", "生成", "分析", "检查", "核查", "整理", "转换",
    "设计", "实现", "开发", "修复", "部署", "测试", "报告", "方案", "对比", "提取",
    "审查", "翻译", "续写", "海报", "幻灯片", "表格", "代码", "文档", "总结", "评估",
    "规划", "research", "search", "write", "generate", "analyze", "check", "convert",
    "design", "implement", "fix", "deploy", "test", "report", "compare", "extract",
    "review", "translate", "summarize", "plan", "audit", "refactor", "debug",
)
MIN_PROMPT_LENGTH = 12
CONTEXT = (
    "CapRouter is enabled. For multi-step or local-capability tasks, use the caprouter skill: "
    "write a plan to .caprouter/plans/, run `caprouter plan check`, then `caprouter run`. "
    "Do not call WebSearch/WebFetch/native subagents directly. If CapRouter is not applicable, proceed normally."
)
BUS_CONTEXT = (
    "CapRouter capability bus is enabled. For multi-step or local-capability tasks, use the caprouter skill: "
    "call capability_search before improvising, use the selected capability or record a gap if nothing fits, "
    "and call capability_verify before declaring the deliverable done. If CapRouter is not applicable, proceed normally."
)


def _mode(payload: dict[str, Any]) -> str:
    cwd_text = str(payload.get("cwd") or ".")
    workspace = find_workspace_near_plugin(Path(cwd_text))
    if workspace is None:
        return "orchestrator"
    config = read_json(config_path(workspace), {})
    if isinstance(config, dict):
        return str(config.get("mode") or "orchestrator")
    return "orchestrator"


def should_inject(prompt: Any, enabled: bool | None = None) -> bool:
    enabled = is_enabled() if enabled is None else enabled
    if not enabled:
        return False
    text = str(prompt or "").strip()
    if len(text) < MIN_PROMPT_LENGTH:
        return False
    if text.startswith("/"):
        return False
    lowered = text.lower()
    if "caprouter" in lowered or "不要使用" in text:
        return False
    return any(verb in lowered for verb in TASK_VERBS)


def decide(payload: dict[str, Any]) -> dict[str, Any] | None:
    prompt = payload.get("prompt")
    if not should_inject(prompt):
        return None
    context = BUS_CONTEXT if _mode(payload) == "bus" else CONTEXT
    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": context,
        }
    }


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
    try:
        result = decide(payload)
    except Exception:
        return 0
    if result is None:
        return 0
    try:
        append_audit(
            {
                "event": "user_prompt_submit",
                "decision": "inject",
                "prompt_chars": len(str(payload.get("prompt") or "")),
                "cwd": payload.get("cwd"),
                "session_id": payload.get("session_id"),
            }
        )
    except OSError:
        pass
    print(json.dumps(result, ensure_ascii=False))
    return 0
