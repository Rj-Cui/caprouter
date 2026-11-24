from __future__ import annotations

from pathlib import Path
from typing import Any

from .adapters import AdapterResult
from .util import write_json_atomic


def write_attempt(
    run_dir: Path,
    node_id: str,
    attempt: int,
    prompt: str,
    result: AdapterResult,
) -> Path:
    base = Path(run_dir) / "agents" / node_id / f"attempt-{attempt}"
    base.mkdir(parents=True, exist_ok=True)
    (base / "prompt.txt").write_text(prompt, encoding="utf-8")
    (base / "stdout.txt").write_text(result.stdout, encoding="utf-8")
    (base / "stderr.txt").write_text(result.stderr, encoding="utf-8")

    meta = {
        "adapter": result.adapter,
        "command": result.command,
        "returncode": result.returncode,
        "duration_ms": result.duration_ms,
        "timed_out": result.timed_out,
        "error": result.error,
        "stdout_chars": len(result.stdout),
        "stderr_chars": len(result.stderr),
        "parsed_meta": result.parsed.get("meta") if isinstance(result.parsed, dict) else None,
    }
    write_json_atomic(base / "meta.json", meta)
    write_json_atomic(base / "result.json", result.parsed if isinstance(result.parsed, dict) else {"text": result.stdout})
    return base


def write_node_status(run_dir: Path, node_id: str, status: dict[str, Any]) -> None:
    base = Path(run_dir) / "agents" / node_id
    base.mkdir(parents=True, exist_ok=True)
    write_json_atomic(base / "status.json", status)
