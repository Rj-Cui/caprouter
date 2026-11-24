"""Deterministic capability probes.

A probe is evidence that a capability works, not a second LLM judgment. The
runner executes only checks that can be decided mechanically:

* built-in static checks: path, hash, SKILL.md lint, plugin manifest, invoke
  content, CLI availability;
* overlay checks: ``file_exists`` / ``skill_lint`` / ``plugin_manifest`` /
  ``invoke_content``, deterministic acceptance checks applied to a text source,
  and optional ``command`` checks (triple permission);
* ``host`` checks are not executed. They are returned as pending tasks so the
  host can run the task and record the result with :func:`record_probe`.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Iterable

from .scan import lint_skill
from .util import append_jsonl, hash_tree, now_iso, read_json, read_jsonl, safe_name

PROBE_SCHEMA = "caprouter.capability-probe.v1"
PROBE_RUN_SCHEMA = "caprouter.capability-probe-run.v1"
PROBE_REPORT_SCHEMA = "caprouter.capability-probe-report.v1"
DEFAULT_MIN_PASS_RATE = 0.8
MAX_CHECKS = 50
MAX_OUTPUT_CHARS = 20000
MAX_SOURCE_CHARS = 12000

STATIC_TYPES = {
    "path_exists",
    "file_exists",
    "hash_match",
    "skill_lint",
    "plugin_manifest",
    "invoke_content",
    "cli_available",
}
BEHAVIORAL_TYPES = {"command", "host"}
KNOWN_STATUSES = {"pass", "fail", "pending", "unknown", "unverifiable", "skipped", "error"}


def probes_path(workspace: Path | str) -> Path:
    return Path(workspace) / ".caprouter" / "probes.jsonl"


def load_probe_rows(workspace: Path | str) -> list[dict[str, Any]]:
    return read_jsonl(probes_path(workspace))


def _result(
    check_type: str,
    status: str,
    *,
    check_id: str | None = None,
    required: bool = True,
    expected: Any = None,
    actual: Any = None,
    reason: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": check_id or check_type,
        "type": check_type,
        "status": status if status in KNOWN_STATUSES else "error",
        "required": bool(required),
        "expected": expected,
        "actual": actual,
        "reason": reason,
    }
    payload.update(extra)
    return payload


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def _content_path(card: dict[str, Any]) -> Path | None:
    raw = (card.get("invoke") or {}).get("content_path")
    return Path(str(raw)) if raw else None


def _plugin_manifest(root: Path) -> Path | None:
    for candidate in (root / ".claude-plugin" / "plugin.json", root / "plugin.json"):
        if candidate.is_file():
            return candidate
    return None


def _primary_content(card: dict[str, Any]) -> Path | None:
    path = Path(str(card.get("path") or ""))
    content_path = _content_path(card)
    candidates = []
    if content_path:
        candidates.append(content_path)
    candidates.extend([path / "SKILL.md", path / "README.md"])
    if path.is_file():
        candidates.insert(0, path)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _read_source(check: dict[str, Any], card: dict[str, Any], workspace: Path) -> tuple[str | None, str | None]:
    """Return (text, error) for a custom check's text source."""
    raw_path = check.get("path")
    capability_path = Path(str(card.get("path") or ""))
    if raw_path:
        candidate = Path(str(raw_path))
        if not candidate.is_absolute():
            local = capability_path / candidate
            candidate = local if local.exists() else Path(workspace) / candidate
        if not candidate.exists():
            return None, f"source not found: {candidate}"
        if not (_inside(candidate, capability_path) or _inside(candidate, workspace)):
            return None, f"source is outside the capability and workspace: {candidate}"
        if not candidate.is_file():
            return None, f"source is not a file: {candidate}"
        return candidate.read_text(encoding="utf-8", errors="replace")[:MAX_SOURCE_CHARS], None
    if isinstance(check.get("text"), str):
        return str(check["text"])[:MAX_SOURCE_CHARS], None
    primary = _primary_content(card)
    if primary is None:
        return None, "no SKILL.md, README.md or invoke content to inspect"
    return primary.read_text(encoding="utf-8", errors="replace")[:MAX_SOURCE_CHARS], None


def _resolve_cwd(check: dict[str, Any], card: dict[str, Any], workspace: Path) -> tuple[Path | None, str | None]:
    capability_path = Path(str(card.get("path") or ""))
    raw = check.get("cwd")
    if raw:
        text = str(raw)
        if text == "capability":
            target = capability_path
        elif text == "workspace":
            target = Path(workspace)
        else:
            candidate = Path(text)
            target = candidate if candidate.is_absolute() else capability_path / candidate
        if not (_inside(target, capability_path) or _inside(target, workspace)):
            return None, f"probe cwd is outside the capability and workspace: {target}"
        if not target.is_dir():
            return None, f"probe cwd does not exist: {target}"
        return target, None
    target = Path(workspace) / ".caprouter" / "scratch" / "probes" / safe_name(str(card.get("id") or "capability"))
    target.mkdir(parents=True, exist_ok=True)
    return target, None


def _static_checks(card: dict[str, Any], *, check_hash: bool = False) -> list[dict[str, Any]]:
    """Built-in checks that do not need overlay definitions."""
    checks: list[dict[str, Any]] = []
    capability_id = str(card.get("id") or "")
    path = Path(str(card.get("path") or ""))
    entry_type = str(card.get("type") or "")

    checks.append(
        _result(
            "path_exists", "pass" if path.exists() else "fail",
            check_id="static:path", expected=str(path), actual=path.exists(),
            reason=None if path.exists() else "capability path is missing",
        )
    )

    if entry_type == "skill":
        skill_file = path / "SKILL.md"
        if skill_file.is_file():
            lint = lint_skill(skill_file)
            checks.append(
                _result(
                    "skill_lint", "pass" if lint.get("ok") else "fail",
                    check_id="static:skill_lint",
                    expected="valid SKILL.md frontmatter",
                    actual={"errors": lint.get("errors") or [], "warnings": lint.get("warnings") or []},
                    reason="; ".join(lint.get("errors") or []) or None,
                )
            )
        else:
            checks.append(
                _result(
                    "skill_lint", "fail", check_id="static:skill_lint",
                    expected="SKILL.md", actual=False, reason="SKILL.md is missing",
                )
            )

    if entry_type == "plugin":
        manifest = _plugin_manifest(path)
        if manifest is None:
            checks.append(
                _result(
                    "plugin_manifest", "fail", check_id="static:plugin_manifest",
                    expected=".claude-plugin/plugin.json or plugin.json",
                    actual=False, reason="plugin manifest is missing",
                )
            )
        else:
            data = read_json(manifest)
            ok = isinstance(data, dict) and bool(str(data.get("name") or "").strip())
            checks.append(
                _result(
                    "plugin_manifest", "pass" if ok else "fail",
                    check_id="static:plugin_manifest",
                    expected="manifest with name",
                    actual={"path": str(manifest), "name": data.get("name") if isinstance(data, dict) else None},
                    reason=None if ok else "plugin manifest is invalid or has no name",
                )
            )

    if entry_type == "cli-agent":
        name = str(card.get("name") or "")
        available = bool(shutil.which(name))
        checks.append(
            _result(
                "cli_available", "pass" if available else "fail",
                check_id="static:cli_available", expected=f"{name} on PATH", actual=available,
                reason=None if available else f"{name} is not on PATH",
            )
        )

    invoke = card.get("invoke") or {}
    if str(invoke.get("mode") or "none") == "prompt":
        content_path = _content_path(card)
        exists = bool(content_path and content_path.exists())
        checks.append(
            _result(
                "invoke_content", "pass" if exists else "fail",
                check_id="static:invoke_content", expected=str(content_path or ""), actual=exists,
                reason=None if exists else "prompt-mode content_path is missing",
            )
        )

    if check_hash:
        expected = str(card.get("hash") or "")
        if expected and path.exists():
            actual = hash_tree(path)
            checks.append(
                _result(
                    "hash_match", "pass" if actual == expected else "fail",
                    check_id="static:hash", expected=expected, actual=actual,
                    reason=None if actual == expected else "content changed since the registry scan; rerun caprouter scan",
                )
            )
        else:
            checks.append(
                _result(
                    "hash_match", "skipped", check_id="static:hash",
                    expected=expected or "stored hash", actual=None,
                    reason="no stored hash to compare",
                )
            )
    return checks


def runnable_definitions(overlay_probe: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Filter overlay probe entries to things the runner can actually run."""
    if not isinstance(overlay_probe, dict):
        return []
    raw = overlay_probe.get("checks")
    if not isinstance(raw, list):
        return []
    definitions: list[dict[str, Any]] = []
    for item in raw[:MAX_CHECKS]:
        if not isinstance(item, dict):
            continue
        check_type = str(item.get("type") or "").strip()
        if not check_type:
            continue
        # Legacy/manual result rows look like {"task": "...", "status": "pass"}.
        if "status" in item and "task" in item and check_type not in BEHAVIORAL_TYPES | STATIC_TYPES:
            continue
        definitions.append(dict(item))
    return definitions


def infer_evidence(checks: Iterable[dict[str, Any]]) -> str:
    types = {str(check.get("type") or "") for check in checks if isinstance(check, dict)}
    behavioral = bool(types & BEHAVIORAL_TYPES) or any(
        isinstance(check, dict) and check.get("task") and check.get("status")
        for check in checks
    )
    static = bool(types & STATIC_TYPES)
    if behavioral and static:
        return "mixed"
    if behavioral:
        return "behavioral"
    if static:
        return "static"
    return "unknown"


def _run_command(
    check: dict[str, Any],
    card: dict[str, Any],
    workspace: Path,
    *,
    allow_execute: bool,
    timeout: int,
) -> dict[str, Any]:
    from .capabilities import bus_config, verify_artifact

    check_id = str(check.get("id") or "command")
    required = bool(check.get("required", True))
    command = check.get("command")
    if not isinstance(command, list) or not command:
        return _result("command", "fail", check_id=check_id, required=required,
                       reason="command must be a non-empty argv list (no shell)")
    from .state import is_enabled
    if not is_enabled():
        return _result("command", "skipped", check_id=check_id, required=required,
                       reason="CapRouter is disabled")
    permissions = card.get("permissions") if isinstance(card.get("permissions"), dict) else {}
    if permissions.get("shell") is not True:
        return _result("command", "skipped", check_id=check_id, required=required,
                       reason="command probe requires Card permissions.shell=true")
    if not bool(check.get("allow")):
        return _result("command", "skipped", check_id=check_id, required=required,
                       reason="command probe requires check.allow=true")
    if not allow_execute:
        return _result("command", "skipped", check_id=check_id, required=required,
                       reason="command probe requires caller allow_execute=true")
    if not bool(bus_config(workspace).get("allow_execute")):
        return _result("command", "skipped", check_id=check_id, required=required,
                       reason="command probe requires config bus.allow_execute=true")

    cwd, error = _resolve_cwd(check, card, workspace)
    if error or cwd is None:
        return _result("command", "error", check_id=check_id, required=required, reason=error)

    argv = [str(value) for value in command]
    stdin_text = str(check.get("input") or "")
    if any("{input}" in value for value in argv):
        argv = [value.replace("{input}", stdin_text) for value in argv]
        stdin_text = ""
    env = os.environ.copy()
    extra_env = check.get("env")
    if isinstance(extra_env, dict):
        env.update({str(key): str(value) for key, value in extra_env.items()})

    limit = max(1, min(int(check.get("timeout") or timeout or 60), 600))
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd),
            input=stdin_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=limit,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return _result("command", "fail", check_id=check_id, required=required,
                       reason=f"timeout after {limit}s", duration_ms=round((time.perf_counter() - started) * 1000, 1))
    except OSError as exc:
        return _result("command", "error", check_id=check_id, required=required, reason=str(exc))

    duration_ms = round((time.perf_counter() - started) * 1000, 1)
    stdout = (proc.stdout or "")[:MAX_OUTPUT_CHARS]
    stderr = (proc.stderr or "")[:MAX_OUTPUT_CHARS]
    expected_returncode = check.get("expect_returncode")
    if expected_returncode is None:
        expected_returncode = 0
    acceptance = check.get("acceptance")
    if isinstance(acceptance, list) and acceptance:
        artifact = check.get("artifact")
        if artifact:
            artifact_path = Path(str(artifact))
            if not artifact_path.is_absolute():
                artifact_path = cwd / artifact_path
            if not artifact_path.is_file():
                return _result("command", "fail", check_id=check_id, required=required,
                               reason=f"probe artifact not found: {artifact_path}",
                               returncode=proc.returncode, duration_ms=duration_ms,
                               stdout_tail=stdout[-2000:], stderr_tail=stderr[-2000:])
            text = artifact_path.read_text(encoding="utf-8", errors="replace")
        else:
            text = stdout + ("\n" + stderr if stderr else "")
        verdict = verify_artifact(workspace, text=text, acceptance=acceptance)
        status = verdict.get("verdict") if verdict.get("verdict") in {"pass", "fail", "unverifiable"} else "unverifiable"
        if status == "pass" and proc.returncode != expected_returncode:
            status = "fail"
            reason = f"returncode {proc.returncode} != {expected_returncode}"
        else:
            reason = None if status == "pass" else "acceptance checks failed"
        return _result("command", status, check_id=check_id, required=required,
                       expected=acceptance, actual=verdict.get("checks"),
                       reason=reason, returncode=proc.returncode, duration_ms=duration_ms,
                       stdout_tail=stdout[-2000:], stderr_tail=stderr[-2000:])

    status = "pass" if proc.returncode == expected_returncode else "fail"
    return _result("command", status, check_id=check_id, required=required,
                   expected=f"returncode {expected_returncode}", actual=proc.returncode,
                   reason=None if status == "pass" else "command returned a non-zero exit code",
                   duration_ms=duration_ms, stdout_tail=stdout[-2000:], stderr_tail=stderr[-2000:])


def _run_custom(
    check: dict[str, Any],
    card: dict[str, Any],
    workspace: Path,
    *,
    allow_execute: bool,
    timeout: int,
) -> dict[str, Any]:
    from .capabilities import verify_artifact

    check_type = str(check.get("type") or "").strip()
    check_id = str(check.get("id") or check_type or "custom")
    required = bool(check.get("required", True))

    if check_type == "host":
        task = str(check.get("task") or "").strip()
        return _result("host", "pending", check_id=check_id, required=required,
                       expected=task or "host probe task", actual=None,
                       reason="host must run this task and record the result",
                       task=task, acceptance=check.get("acceptance") or [])

    if check_type == "command":
        return _run_command(check, card, workspace, allow_execute=allow_execute, timeout=timeout)

    if check_type in {"path_exists", "file_exists"}:
        raw = check.get("path")
        if not raw:
            return _result(check_type, "fail", check_id=check_id, required=required, reason="path is required")
        capability_path = Path(str(card.get("path") or ""))
        target = Path(str(raw))
        if not target.is_absolute():
            local = capability_path / target
            target = local if local.exists() else Path(workspace) / target
        exists = target.exists()
        if exists and not (_inside(target, capability_path) or _inside(target, workspace)):
            return _result(check_type, "fail", check_id=check_id, required=required,
                           reason=f"path is outside the capability and workspace: {target}")
        return _result(check_type, "pass" if exists else "fail", check_id=check_id,
                       required=required, expected=str(target), actual=exists,
                       reason=None if exists else "path is missing")

    if check_type == "skill_lint":
        skill_file = Path(str(card.get("path") or "")) / "SKILL.md"
        if not skill_file.is_file():
            return _result("skill_lint", "fail", check_id=check_id, required=required,
                           expected="SKILL.md", actual=False, reason="SKILL.md is missing")
        lint = lint_skill(skill_file)
        return _result("skill_lint", "pass" if lint.get("ok") else "fail", check_id=check_id,
                       required=required, expected="valid SKILL.md frontmatter",
                       actual={"errors": lint.get("errors") or [], "warnings": lint.get("warnings") or []},
                       reason="; ".join(lint.get("errors") or []) or None)

    if check_type == "plugin_manifest":
        manifest = _plugin_manifest(Path(str(card.get("path") or "")))
        if manifest is None:
            return _result("plugin_manifest", "fail", check_id=check_id, required=required,
                           expected="plugin manifest", actual=False, reason="plugin manifest is missing")
        data = read_json(manifest)
        ok = isinstance(data, dict) and bool(str(data.get("name") or "").strip())
        return _result("plugin_manifest", "pass" if ok else "fail", check_id=check_id,
                       required=required, expected="manifest with name",
                       actual={"path": str(manifest)}, reason=None if ok else "invalid plugin manifest")

    if check_type == "invoke_content":
        content_path = _content_path(card)
        exists = bool(content_path and content_path.exists())
        return _result("invoke_content", "pass" if exists else "fail", check_id=check_id,
                       required=required, expected=str(content_path or ""), actual=exists,
                       reason=None if exists else "prompt-mode content_path is missing")

    # Everything else is a deterministic acceptance check applied to a text source.
    text, error = _read_source(check, card, workspace)
    if error or text is None:
        return _result(check_type or "criterion", "error", check_id=check_id,
                       required=required, reason=error)
    acceptance = check.get("acceptance")
    if not isinstance(acceptance, list) or not acceptance:
        acceptance = [check]
    verdict = verify_artifact(workspace, text=text, acceptance=acceptance)
    status = verdict.get("verdict") if verdict.get("verdict") in {"pass", "fail", "unverifiable"} else "unverifiable"
    return _result(check_type or "criterion", status, check_id=check_id, required=required,
                   expected=acceptance, actual=verdict.get("checks"),
                   reason=None if status == "pass" else "acceptance checks did not pass")


def _aggregate(checks: list[dict[str, Any]]) -> dict[str, Any]:
    required = [check for check in checks if check.get("required", True)]
    counts = {
        "pass": sum(1 for check in checks if check.get("status") == "pass"),
        "fail": sum(1 for check in checks if check.get("status") == "fail"),
        "pending": sum(1 for check in checks if check.get("status") == "pending"),
        "skipped": sum(1 for check in checks if check.get("status") == "skipped"),
        "unverifiable": sum(1 for check in checks if check.get("status") == "unverifiable"),
        "error": sum(1 for check in checks if check.get("status") == "error"),
    }
    considered = counts["pass"] + counts["fail"]
    pass_rate = round(counts["pass"] / considered, 4) if considered else None
    if any(check.get("status") == "fail" for check in required):
        status = "fail"
    elif any(check.get("status") == "pending" for check in checks):
        # Host tasks are optional for pass/fail purposes but they are still
        # unverified behavioral claims; do not call the capability proven.
        status = "pending"
    elif any(check.get("status") in {"unverifiable", "skipped", "error"} for check in required):
        status = "unknown"
    elif required and all(check.get("status") == "pass" for check in required):
        status = "pass"
    else:
        status = "unknown"
    return {
        "status": status,
        "pass": counts["pass"],
        "fail": counts["fail"],
        "pending": counts["pending"],
        "skipped": counts["skipped"],
        "unverifiable": counts["unverifiable"],
        "error": counts["error"],
        "pass_rate": pass_rate,
        "evidence": infer_evidence(checks),
    }


def run_probes(
    workspace: Path | str,
    capability_id: str,
    *,
    allow_execute: bool = False,
    check_hash: bool = False,
    timeout: int = 60,
) -> dict[str, Any]:
    """Run deterministic probes for one capability and append one history row."""
    from .capabilities import get_capability

    card = get_capability(workspace, capability_id)
    if not card:
        return {"schema": PROBE_SCHEMA, "status": "not_found", "id": capability_id}
    started = time.perf_counter()
    overlay_probe = card.get("probe") or {}
    checks: list[dict[str, Any]] = _static_checks(card, check_hash=check_hash)
    # Use the overlay definitions, not the last run's result checks that the
    # Card summary exposes under ``checks``.
    definitions = overlay_probe.get("definitions")
    if not isinstance(definitions, list):
        definitions = runnable_definitions(overlay_probe)
    for check in definitions:
        if not isinstance(check, dict):
            continue
        checks.append(
            _run_custom(check, card, Path(workspace), allow_execute=allow_execute, timeout=timeout)
        )
    duration_ms = round((time.perf_counter() - started) * 1000, 1)
    aggregate = _aggregate(checks)
    result = {
        "schema": PROBE_SCHEMA,
        "id": capability_id,
        "status": aggregate["status"],
        "evidence": aggregate["evidence"],
        "duration_ms": duration_ms,
        "checks": checks,
        "pass": aggregate["pass"],
        "fail": aggregate["fail"],
        "pending": aggregate["pending"],
        "skipped": aggregate["skipped"],
        "unverifiable": aggregate["unverifiable"],
        "error": aggregate["error"],
        "pass_rate": aggregate["pass_rate"],
        "started_at": now_iso(),
    }
    append_jsonl(
        probes_path(workspace),
        {
            "schema": PROBE_RUN_SCHEMA,
            "ts": now_iso(),
            "capability_id": capability_id,
            "status": result["status"],
            "evidence": result["evidence"],
            "duration_ms": duration_ms,
            "pass": result["pass"],
            "fail": result["fail"],
            "pending": result["pending"],
            "pass_rate": result["pass_rate"],
            "checks": checks,
        },
    )
    return result


def run_all_probes(
    workspace: Path | str,
    *,
    allow_execute: bool = False,
    check_hash: bool = False,
    timeout: int = 60,
) -> dict[str, Any]:
    """Run probes for every registry entry. Command checks stay gated."""
    from .capabilities import load_entries

    entries = load_entries(workspace)
    results: list[dict[str, Any]] = []
    counts = {"pass": 0, "fail": 0, "pending": 0, "unknown": 0, "not_found": 0}
    started = time.perf_counter()
    for entry in entries:
        capability_id = str(entry.get("id") or "")
        if not capability_id:
            continue
        result = run_probes(
            workspace,
            capability_id,
            allow_execute=allow_execute,
            check_hash=check_hash,
            timeout=timeout,
        )
        status = str(result.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
        results.append(
            {
                "id": capability_id,
                "status": status,
                "evidence": result.get("evidence"),
                "pass": result.get("pass", 0),
                "fail": result.get("fail", 0),
                "pending": result.get("pending", 0),
                "duration_ms": result.get("duration_ms"),
            }
        )
    return {
        "schema": "caprouter.capability-probe-batch.v1",
        "total": len(results),
        "counts": counts,
        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        "results": results,
        "checked_at": now_iso(),
    }


def record_probe(
    workspace: Path | str,
    capability_id: str,
    *,
    task: str = "",
    status: str = "pass",
    duration_ms: float | None = None,
    notes: str = "",
    checks: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Record a host-executed probe result (the host owns prompt-mode tasks)."""
    normalized = str(status or "").strip().lower()
    if normalized not in {"pass", "fail", "pending"}:
        raise ValueError("status must be pass, fail or pending")
    details = [dict(item) for item in (checks or []) if isinstance(item, dict)]
    if not details:
        details = [
            _result(
                "host",
                normalized,
                check_id="host:recorded",
                expected=task or "host probe task",
                actual=normalized,
                reason=notes or None,
                task=task,
            )
        ]
    row = {
        "schema": PROBE_RUN_SCHEMA,
        "ts": now_iso(),
        "capability_id": capability_id,
        "status": normalized,
        "evidence": "behavioral",
        "source": "host",
        "task": task,
        "duration_ms": duration_ms,
        "notes": notes,
        "pass": sum(1 for item in details if item.get("status") == "pass"),
        "fail": sum(1 for item in details if item.get("status") == "fail"),
        "pending": sum(1 for item in details if item.get("status") == "pending"),
        "pass_rate": None,
        "checks": details,
    }
    append_jsonl(probes_path(workspace), row)
    return row


def _recent(rows: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    return rows[-max(1, int(limit)):]


def _seed_evidence(overlay: dict[str, Any], seed_status: str,
                   seed_checks: Iterable[dict[str, Any]]) -> str:
    """Definitions are plans, not evidence. Only recorded results count."""
    result_checks = [
        check for check in seed_checks
        if isinstance(check, dict) and "status" in check
    ]
    if seed_status in {"pass", "fail", "pending"}:
        return str(overlay.get("evidence") or infer_evidence(result_checks))
    return infer_evidence(result_checks)


def summarize_probe(
    capability_id: str,
    rows: Iterable[dict[str, Any]],
    overlay_probe: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fold probe history + overlay seed into the summary used by Cards/search."""
    overlay = overlay_probe if isinstance(overlay_probe, dict) else {}
    definitions = runnable_definitions(overlay)
    min_pass_rate = float(overlay.get("min_pass_rate") or DEFAULT_MIN_PASS_RATE)
    history = [row for row in rows if str(row.get("capability_id") or "") == str(capability_id)]
    if not history:
        seed_status = str(overlay.get("status") or "unknown").lower()
        if seed_status not in {"pass", "fail", "pending", "unknown"}:
            seed_status = "unknown"
        seed_checks = overlay.get("checks") if isinstance(overlay.get("checks"), list) else []
        return {
            "status": seed_status,
            "source": "overlay" if overlay else "none",
            "evidence": _seed_evidence(overlay, seed_status, seed_checks),
            "runs": 0,
            "pass": 0,
            "fail": 0,
            "pass_rate": None,
            "last_run_at": overlay.get("last_run_at"),
            "latency_ms": overlay.get("latency_ms"),
            "checks": seed_checks,
            "seed_checks": seed_checks,
            "failures": overlay.get("failures") or [],
            "min_pass_rate": min_pass_rate,
            "definitions": definitions,
        }

    last = history[-1]
    recent = _recent(history)
    behavioral_rows = [
        row for row in recent
        if str(row.get("evidence") or infer_evidence(row.get("checks") or [])) in {"behavioral", "mixed"}
        or str(row.get("source") or "") == "host"
    ]
    # When behavioral evidence exists, static passes must not wash out a
    # behavioral failure. Rate only over behavioral rows in that case.
    rate_rows = behavioral_rows or recent
    passed = sum(1 for row in rate_rows if row.get("status") == "pass")
    failed = sum(1 for row in rate_rows if row.get("status") == "fail")
    considered = passed + failed
    pass_rate = round(passed / considered, 4) if considered else None
    last_status = str(last.get("status") or "unknown")
    last_checks = last.get("checks") if isinstance(last.get("checks"), list) else []
    seed_status = str(overlay.get("status") or "unknown").lower()
    if seed_status not in {"pass", "fail", "pending", "unknown"}:
        seed_status = "unknown"
    seed_checks = overlay.get("checks") if isinstance(overlay.get("checks"), list) else []
    seed_evidence = _seed_evidence(overlay, seed_status, seed_checks)
    history_evidence = str(last.get("evidence") or infer_evidence(last_checks))
    behavioral_recent = bool(behavioral_rows)

    if behavioral_recent:
        behavioral_last = behavioral_rows[-1]
        behavioral_status = str(behavioral_last.get("status") or "unknown")
        behavioral_evidence = str(
            behavioral_last.get("evidence")
            or infer_evidence(behavioral_last.get("checks") or [])
        )
        if last_status == "fail" or behavioral_status == "fail":
            status = "fail"
        elif behavioral_status == "pass" and (pass_rate is None or pass_rate >= min_pass_rate):
            status = "pass"
        elif behavioral_status == "pending":
            status = "pending"
        else:
            status = "unknown"
        evidence = "mixed" if any(row not in behavioral_rows for row in recent) else behavioral_evidence
    else:
        # Static checks prove structure, not behavior. They must not erase a
        # manual behavioral seed or a recorded failure.
        if last_status == "fail" or seed_status == "fail":
            status = "fail"
        elif seed_status == "pending":
            status = "pending"
        elif last_status == "pass":
            status = "pass"
        else:
            status = "unknown"
        if last_status == "pass" and seed_evidence in {"behavioral", "mixed"}:
            evidence = "mixed"
        elif history_evidence != "unknown":
            evidence = history_evidence
        else:
            evidence = seed_evidence

    failures = [
        {
            "id": check.get("id"),
            "type": check.get("type"),
            "reason": check.get("reason"),
        }
        for check in last_checks
        if isinstance(check, dict) and check.get("status") in {"fail", "error"}
    ]
    return {
        "status": status,
        "source": last.get("source") or "runner",
        "evidence": evidence,
        "runs": len(history),
        "pass": sum(1 for row in history if row.get("status") == "pass"),
        "fail": sum(1 for row in history if row.get("status") == "fail"),
        "pass_rate": pass_rate,
        "last_run_at": last.get("ts"),
        "latency_ms": last.get("duration_ms"),
        "checks": last_checks,
        "seed_checks": seed_checks,
        "failures": failures,
        "min_pass_rate": min_pass_rate,
        "definitions": definitions,
    }


def probe_report(
    workspace: Path | str,
    *,
    capability_id: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    from .capabilities import load_overlay

    rows = load_probe_rows(workspace)
    cards = load_overlay(workspace)["cards"]

    def overlay_probe(item: str) -> dict[str, Any] | None:
        card = cards.get(item)
        return card.get("probe") if isinstance(card, dict) else None

    if capability_id:
        selected = [row for row in rows if str(row.get("capability_id") or "") == capability_id]
        recent = selected[-max(1, int(limit)):]
        return {
            "schema": PROBE_REPORT_SCHEMA,
            "id": capability_id,
            "runs": len(selected),
            "summary": summarize_probe(capability_id, rows, overlay_probe(capability_id)),
            "history": recent,
        }
    ids = sorted({str(row.get("capability_id") or "") for row in rows if row.get("capability_id")})
    summaries = []
    for item in ids:
        summary = summarize_probe(item, rows, overlay_probe(item))
        summaries.append({
            "id": item,
            "status": summary["status"],
            "evidence": summary["evidence"],
            "runs": summary["runs"],
            "pass_rate": summary["pass_rate"],
            "last_run_at": summary["last_run_at"],
            "failures": summary.get("failures") or [],
        })
    failures = [
        {"id": item["id"], "failures": item["failures"]}
        for item in summaries
        if item["status"] == "fail"
    ]
    return {
        "schema": PROBE_REPORT_SCHEMA,
        "count": len(summaries),
        "summaries": summaries,
        "failures": failures,
        "recent": rows[-max(1, int(limit)):],
    }
