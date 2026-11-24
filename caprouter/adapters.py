from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


@dataclass
class AdapterResult:
    adapter: str
    command: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool
    parsed: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


def _text_parts(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list):
        return []
    parts: list[str] = []
    for item in value:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict) and item.get("type") in {"text", "output_text"}:
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
    return parts


def parse_output(stdout: str) -> dict[str, Any]:
    """Parse CodeBuddy/Qwen JSON event streams, or fall back to text."""
    text = (stdout or "").strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {"text": text, "meta": {}, "format": "text"}

    events = data if isinstance(data, list) else [data]
    assistant_parts: list[str] = []
    result_event: dict[str, Any] | None = None
    for event in events:
        if not isinstance(event, dict):
            continue
        if event.get("type") == "result":
            result_event = event
        if event.get("type") == "assistant":
            message = event.get("message") if isinstance(event.get("message"), dict) else event
            assistant_parts.extend(_text_parts(message.get("content")))
        if event.get("type") == "message" and event.get("role") == "assistant":
            assistant_parts.extend(_text_parts(event.get("content")))

    final_text = ""
    if result_event and isinstance(result_event.get("result"), str):
        final_text = result_event["result"]
    elif assistant_parts:
        final_text = "\n".join(part for part in assistant_parts if part).strip()

    meta: dict[str, Any] = {}
    if result_event:
        meta = {
            key: result_event.get(key)
            for key in ("is_error", "num_turns", "duration_ms", "total_cost_usd", "usage", "session_id")
            if key in result_event
        }
    error = None
    if result_event and result_event.get("is_error"):
        error = "adapter reported is_error"
    return {"text": final_text, "meta": meta, "format": "json", "error": error, "events": len(events)}


class Adapter:
    name = ""
    binary = ""
    prompt_via_stdin = False
    supports_tool_isolation = True

    def __init__(self) -> None:
        self.binary = shutil.which(self.name) or self.name

    def available(self) -> bool:
        return bool(shutil.which(self.name))

    def build(
        self,
        prompt: str,
        *,
        max_turns: int,
        system_prompt: str | None,
        disable_tools: bool = False,
    ) -> tuple[list[str], list[str]]:
        raise NotImplementedError

    def probe(self) -> dict[str, Any]:
        if not self.available():
            return {"name": self.name, "available": False, "version": None}
        try:
            proc = subprocess.run(
                [self.binary, "--version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                creationflags=CREATE_NO_WINDOW,
            )
            version = (proc.stdout or proc.stderr or "").strip().splitlines()
            return {"name": self.name, "available": True, "version": version[0][:120] if version else None}
        except (OSError, subprocess.SubprocessError):
            return {"name": self.name, "available": True, "version": None}


class CodeBuddyAdapter(Adapter):
    name = "codebuddy"
    prompt_via_stdin = True

    def build(
        self,
        prompt: str,
        *,
        max_turns: int,
        system_prompt: str | None,
        disable_tools: bool = False,
    ) -> tuple[list[str], list[str]]:
        cmd = [self.binary, "-p", "--output-format", "json", "--max-turns", str(max_turns)]
        display = [self.binary, "-p", "<stdin>", "--output-format", "json", "--max-turns", str(max_turns)]
        if disable_tools:
            cmd.extend(["--tools", ""])
            display.extend(["--tools", "<none>"])
        if system_prompt:
            cmd.extend(["--system-prompt", system_prompt])
            display.extend(["--system-prompt", "<system>"])
        return cmd, display


class QwenAdapter(Adapter):
    name = "qwen"
    prompt_via_stdin = True

    def build(
        self,
        prompt: str,
        *,
        max_turns: int,
        system_prompt: str | None,
        disable_tools: bool = False,
    ) -> tuple[list[str], list[str]]:
        cmd = [self.binary, "-o", "json", "--max-session-turns", str(max_turns)]
        display = [self.binary, "-o", "json", "<stdin>", "--max-session-turns", str(max_turns)]
        if disable_tools:
            # Plan mode analyzes only: no edits and no command execution.
            cmd.extend(["--approval-mode", "plan"])
            display.extend(["--approval-mode", "plan"])
        if system_prompt:
            cmd.extend(["--system-prompt", system_prompt])
            display.extend(["--system-prompt", "<system>"])
        return cmd, display


class KimiAdapter(Adapter):
    name = "kimi"
    # No verified CLI flag disables tools for this adapter; the semantic
    # reviewer therefore refuses it unless the user explicitly opts in.
    supports_tool_isolation = False

    def build(
        self,
        prompt: str,
        *,
        max_turns: int,
        system_prompt: str | None,
        disable_tools: bool = False,
    ) -> tuple[list[str], list[str]]:
        full_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
        # mise shims on Windows reject multi-line argv; flatten before passing.
        flat_prompt = " ".join(full_prompt.split())
        cmd = [self.binary, "-p", flat_prompt, "--output-format", "text"]
        display = [self.binary, "-p", "<prompt>", "--output-format", "text"]
        return cmd, display


class MockAdapter(Adapter):
    name = "mock"
    verify_calls = 0

    def available(self) -> bool:
        return True

    def build(
        self,
        prompt: str,
        *,
        max_turns: int,
        system_prompt: str | None,
        disable_tools: bool = False,
    ) -> tuple[list[str], list[str]]:
        return ["mock"], ["mock"]

    def run(self, prompt: str, *, max_turns: int, system_prompt: str | None) -> AdapterResult:
        started = time.perf_counter()
        is_verifier = (system_prompt and "verifier" in system_prompt.lower()) or (
            "verify the following draft" in prompt.lower()
        )
        if is_verifier:
            fail_once = os.environ.get("CAPROUTER_MOCK_VERIFY_FAIL") == "1"
            if fail_once and MockAdapter.verify_calls == 0:
                text = json.dumps(
                    {"ok": False, "issues": [{"severity": "high", "problem": "mock verification failure"}], "claims": []},
                    ensure_ascii=False,
                )
            else:
                text = json.dumps({"ok": True, "issues": [], "claims": []}, ensure_ascii=False)
            MockAdapter.verify_calls += 1
        else:
            text = "MOCK: " + prompt[:200]
        return AdapterResult(
            adapter="mock",
            command=["mock"],
            returncode=0,
            stdout=text,
            stderr="",
            duration_ms=round((time.perf_counter() - started) * 1000, 3),
            timed_out=False,
            parsed={"text": text, "meta": {}, "format": "text"},
        )


ADAPTERS: dict[str, type[Adapter]] = {
    "codebuddy": CodeBuddyAdapter,
    "qwen": QwenAdapter,
    "kimi": KimiAdapter,
    "mock": MockAdapter,
}


def get_adapter(name: str) -> Adapter:
    cls = ADAPTERS.get(name)
    if cls is None:
        raise ValueError(f"unknown adapter: {name}")
    return cls()


def list_adapters() -> list[dict[str, Any]]:
    return [get_adapter(name).probe() for name in ADAPTERS]


def run_adapter(
    name: str,
    prompt: str,
    *,
    cwd: Path,
    timeout: int = 300,
    max_turns: int = 8,
    system_prompt: str | None = None,
    disable_tools: bool = False,
    allow_unisolated: bool = False,
) -> AdapterResult:
    adapter = get_adapter(name)
    if isinstance(adapter, MockAdapter):
        return adapter.run(prompt, max_turns=max_turns, system_prompt=system_prompt)
    from .state import is_enabled
    if not is_enabled():
        return AdapterResult(
            adapter=name,
            command=[],
            returncode=3,
            stdout="",
            stderr="CapRouter is disabled",
            duration_ms=0,
            timed_out=False,
            error="CapRouter is disabled; run: caprouter state --enable",
        )
    if not adapter.available():
        return AdapterResult(
            adapter=name,
            command=[],
            returncode=127,
            stdout="",
            stderr=f"{name} not found on PATH",
            duration_ms=0,
            timed_out=False,
            error=f"{name} not found on PATH",
        )
    if disable_tools and not adapter.supports_tool_isolation and not allow_unisolated:
        return AdapterResult(
            adapter=name,
            command=[],
            returncode=126,
            stdout="",
            stderr=f"{name} has no verified tool-isolation flag",
            duration_ms=0,
            timed_out=False,
            error=(
                f"{name} has no verified tool-isolation flag; refusing to run an "
                "untrusted Card review. Set semantic.allow_unisolated=true to override."
            ),
        )
    cmd, display = adapter.build(
        prompt,
        max_turns=max_turns,
        system_prompt=system_prompt,
        disable_tools=disable_tools,
    )
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            input=prompt if adapter.prompt_via_stdin else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
        duration = round((time.perf_counter() - started) * 1000, 3)
        parsed = parse_output(proc.stdout or "")
        error = None
        if proc.returncode != 0:
            error = (proc.stderr or "").strip()[:1000] or f"exit code {proc.returncode}"
        elif parsed.get("error"):
            error = parsed["error"]
        return AdapterResult(
            adapter=name,
            command=display,
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            duration_ms=duration,
            timed_out=False,
            parsed=parsed,
            error=error,
        )
    except subprocess.TimeoutExpired as exc:
        duration = round((time.perf_counter() - started) * 1000, 3)
        stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return AdapterResult(
            adapter=name,
            command=display,
            returncode=-1,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration,
            timed_out=True,
            error=f"timeout after {timeout}s",
        )
