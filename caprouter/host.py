from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .executor import ExecuteOptions, execute_plan


def build_host_plan(prompt: str, task_id: str | None = None) -> dict[str, Any]:
    task = task_id or f"host-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    return {
        "task_id": task,
        "goal": prompt.strip(),
        "quality_gate": {"max_attempts": 1, "on_limit": "pause_and_report"},
        "nodes": [
            {
                "id": "n1",
                "title": "host prompt",
                "capability": "generate",
                "domain": "general",
                "brief": prompt.strip(),
                "output_contract": {"type": "text", "format": "markdown"},
                "acceptance": [
                    "Directly answer the user",
                    "Do not ask for permission unless the task is blocked",
                ],
                "top_k": 3,
            }
        ],
    }


def run_host(
    prompt: str,
    registry: dict[str, Any],
    workspace: Path,
    *,
    config: dict[str, Any] | None = None,
    adapter: str | None = None,
    timeout: int = 300,
    max_turns: int = 8,
) -> dict[str, Any]:
    plan = build_host_plan(prompt)
    return execute_plan(
        plan,
        registry,
        workspace,
        config=config,
        options=ExecuteOptions(adapter=adapter, timeout=timeout, max_turns=max_turns, max_rework=0),
    )
