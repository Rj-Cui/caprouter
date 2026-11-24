"""Minimal pipeline runner for personal tool chains.

A pipeline does not call a model. Prompt-mode skills are executed by the host
agent; this module records which capabilities were selected, prepares a run
packet with the exact skill and artifact paths, runs deterministic command
acceptance checks, and records outcomes. CapRouter stays a local capability
integrator rather than a model/provider gateway.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from .capabilities import (
    bus_dir,
    get_capability,
    load_entries,
    record_gap,
    record_outcome,
    search_capabilities,
    verify_artifact,
)
from .state import is_enabled
from .util import now_iso, read_json, safe_name, write_json_atomic

PIPELINE_SCHEMA = "caprouter.pipeline.v1"
PLAN_SCHEMA = "caprouter.pipeline-plan.v1"
VERIFY_SCHEMA = "caprouter.pipeline-verify.v1"
OUTCOME_SCHEMA = "caprouter.pipeline-outcome.v1"


def pipelines_dir(workspace: Path | str) -> Path:
    return bus_dir(workspace) / "pipelines"


def pipeline_runs_dir(workspace: Path | str) -> Path:
    return bus_dir(workspace) / "pipeline-runs"


def list_pipelines(workspace: Path | str) -> dict[str, Any]:
    root = pipelines_dir(workspace)
    items: list[dict[str, Any]] = []
    if root.is_dir():
        for path in sorted(root.glob("*.json")):
            data = read_json(path, {})
            if not isinstance(data, dict):
                continue
            items.append(
                {
                    "id": data.get("id") or path.stem,
                    "goal": data.get("goal") or "",
                    "steps": len(data.get("steps") or []),
                    "path": str(path),
                }
            )
    return {"schema": "caprouter.pipeline-list.v1", "count": len(items), "pipelines": items}


def load_pipeline(workspace: Path | str, pipeline_id: str) -> dict[str, Any]:
    path = pipelines_dir(workspace) / f"{safe_name(pipeline_id)}.json"
    data = read_json(path, {})
    if not isinstance(data, dict) or not data:
        raise ValueError(f"pipeline not found: {pipeline_id} ({path})")
    return data


def validate_pipeline(pipeline: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if str(pipeline.get("schema") or "") != PIPELINE_SCHEMA:
        errors.append(f"schema must be {PIPELINE_SCHEMA}")
    if not str(pipeline.get("id") or "").strip():
        errors.append("id is required")
    steps = pipeline.get("steps")
    if not isinstance(steps, list) or not steps:
        errors.append("steps must be a non-empty list")
        return errors
    seen: set[str] = set()
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            errors.append(f"step[{index}] must be an object")
            continue
        step_id = str(step.get("id") or "").strip()
        if not step_id:
            errors.append(f"step[{index}] id is required")
        elif step_id in seen:
            errors.append(f"duplicate step id: {step_id}")
        seen.add(step_id)
        if not str(step.get("need") or "").strip():
            errors.append(f"step[{index}] need is required")
        for key in ("preferred", "fallback"):
            value = step.get(key)
            if value is not None and not isinstance(value, list):
                errors.append(f"step[{index}] {key} must be a list")
        acceptance = step.get("acceptance")
        if acceptance is not None and not isinstance(acceptance, list):
            errors.append(f"step[{index}] acceptance must be a list")
    return errors


def _available_ids(workspace: Path | str) -> set[str]:
    return {str(entry.get("id")) for entry in load_entries(workspace) if entry.get("id")}


def _select_step(workspace: Path | str, step: dict[str, Any]) -> dict[str, Any]:
    """Select one capability for a step: search first, then declared fallbacks."""
    preferred = [str(item) for item in (step.get("preferred") or [])]
    fallback = [str(item) for item in (step.get("fallback") or [])]
    search = search_capabilities(
        workspace,
        str(step.get("need") or ""),
        top=5,
        record_gap=False,
    )
    selected = search.get("selected") if isinstance(search.get("selected"), dict) else None
    selected_id = str(selected.get("id") or "") if selected else ""
    allowed = [*preferred, *fallback]
    if search.get("decision") == "select" and selected_id in allowed:
        return {
            "status": "selected",
            "capability_id": selected_id,
            "selection": "search",
            "confidence": search.get("confidence"),
            "search_decision": search.get("decision"),
        }
    available = _available_ids(workspace)
    for capability_id in allowed:
        if capability_id in available:
            return {
                "status": "selected",
                "capability_id": capability_id,
                "selection": "fallback",
                "confidence": search.get("confidence"),
                "search_decision": search.get("decision"),
            }
    return {
        "status": "abstain",
        "reason": (
            "no declared capability is registered; searched top candidate: "
            + (selected_id or "none")
        ),
        "search_decision": search.get("decision"),
        "search_abstain_reason": search.get("abstain_reason"),
    }


def _render_run_md(plan: dict[str, Any], run_dir: Path) -> str:
    lines = [
        f"# Pipeline run: {plan.get('pipeline')}",
        "",
        f"- Goal: {plan.get('goal') or ''}",
        f"- Task: {plan.get('task') or ''}",
        f"- Run dir: {run_dir}",
        f"- Created: {plan.get('created_at')}",
        "",
        "## Steps",
    ]
    for step in plan.get("steps") or []:
        lines.extend(
            [
                "",
                f"### {step.get('id')}",
                f"- Capability: {step.get('capability_id')}",
                f"- Selection: {step.get('selection')} (search decision: {step.get('search_decision')}, "
                f"confidence: {step.get('confidence')})",
                f"- Skill: {step.get('skill_path')}",
                f"- Input: {step.get('input') or '(passed by the host)'}",
                f"- Output artifact: {step.get('output')}",
                f"- Human confirm: {step.get('human_confirm')}",
            ]
        )
        if step.get("acceptance"):
            lines.append("- Acceptance:")
            for check in step["acceptance"]:
                if check.get("type") == "command":
                    lines.append(f"  - command {check.get('label')}: {' '.join(map(str, check.get('argv') or []))}")
                else:
                    lines.append(f"  - {json.dumps(check, ensure_ascii=False)}")
    lines.extend(
        [
            "",
            "## Host instructions",
            "",
            "1. Read the selected capability's SKILL.md and follow its workflow.",
            "2. Write the final artifact to the output path above.",
            "3. Keep the original input unchanged; write a new artifact.",
            "4. Run: `caprouter pipeline verify --workspace <workspace> --run "
            + str(run_dir)
            + " --allow-execute`.",
            "5. Record the result: `caprouter pipeline outcome --workspace <workspace> --run "
            + str(run_dir)
            + " --success --human-edit-minutes <n>`.",
        ]
    )
    return "\n".join(lines) + "\n"


def plan_pipeline(
    workspace: Path | str,
    pipeline_id: str,
    *,
    task: str = "",
    input_path: str | Path | None = None,
) -> dict[str, Any]:
    workspace = Path(workspace)
    pipeline = load_pipeline(workspace, pipeline_id)
    errors = validate_pipeline(pipeline)
    if errors:
        return {
            "schema": PLAN_SCHEMA,
            "status": "invalid",
            "pipeline": pipeline_id,
            "errors": errors,
        }

    selected_steps: list[dict[str, Any]] = []
    for step in pipeline["steps"]:
        choice = _select_step(workspace, step)
        if choice.get("status") != "selected":
            record_gap(
                workspace,
                need=str(step.get("need") or ""),
                reason=f"pipeline {pipeline_id} step {step.get('id')} has no candidate",
            )
            return {
                "schema": PLAN_SCHEMA,
                "status": "abstain",
                "pipeline": pipeline_id,
                "step": step.get("id"),
                "reason": choice.get("reason"),
                "search_decision": choice.get("search_decision"),
                "search_abstain_reason": choice.get("search_abstain_reason"),
            }
        capability_id = str(choice["capability_id"])
        card = get_capability(workspace, capability_id)
        if not card:
            return {
                "schema": PLAN_SCHEMA,
                "status": "abstain",
                "pipeline": pipeline_id,
                "step": step.get("id"),
                "reason": f"selected capability disappeared from registry: {capability_id}",
            }
        selected_steps.append(
            {
                "id": step.get("id"),
                "need": step.get("need"),
                "capability_id": capability_id,
                "selection": choice.get("selection"),
                "confidence": choice.get("confidence"),
                "search_decision": choice.get("search_decision"),
                "skill_path": card.get("path"),
                "input": str(input_path) if input_path else "",
                "output": str(step.get("output") or f"artifacts/{step.get('id')}.md"),
                "acceptance": step.get("acceptance") or [],
                "human_confirm": bool(step.get("human_confirm")),
                "permissions": step.get("permissions") or {},
                "fallback": step.get("fallback") or [],
            }
        )

    root = pipeline_runs_dir(workspace)
    root.mkdir(parents=True, exist_ok=True)
    prefix = f"{time.strftime('%Y%m%d-%H%M%S')}-{safe_name(pipeline_id)}-"
    run_dir = Path(tempfile.mkdtemp(prefix=prefix, dir=str(root)))
    (run_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    if input_path:
        source = Path(input_path).expanduser().resolve()
        if source.is_file():
            target = run_dir / "input" / source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
            for step in selected_steps:
                if step.get("input") in {"{input}", str(input_path)}:
                    step["input"] = str(target)

    plan = {
        "schema": PLAN_SCHEMA,
        "status": "ready",
        "pipeline": pipeline_id,
        "goal": pipeline.get("goal") or "",
        "task": task,
        "run_dir": str(run_dir),
        "created_at": now_iso(),
        "steps": selected_steps,
    }
    write_json_atomic(run_dir / "plan.json", plan)
    (run_dir / "run.md").write_text(_render_run_md(plan, run_dir), encoding="utf-8")
    plan["run_md"] = str(run_dir / "run.md")
    return plan


def _command_check(
    check: dict[str, Any],
    *,
    output: Path,
    step: dict[str, Any],
    run_dir: Path,
    workspace: Path,
    allow_execute: bool,
    timeout: int,
) -> dict[str, Any]:
    label = str(check.get("label") or "command")
    if not is_enabled():
        return {"type": "command", "label": label, "status": "skipped", "reason": "CapRouter is disabled"}
    if not allow_execute:
        return {
            "type": "command",
            "label": label,
            "status": "skipped",
            "reason": "command acceptance requires allow_execute=true",
        }
    if not bool(check.get("allow")):
        return {"type": "command", "label": label, "status": "skipped", "reason": "check.allow is false"}
    permissions = step.get("permissions") if isinstance(step.get("permissions"), dict) else {}
    if permissions.get("shell") is not True:
        return {
            "type": "command",
            "label": label,
            "status": "skipped",
            "reason": "step permissions.shell must be true",
        }
    argv = check.get("argv")
    if not isinstance(argv, list) or not argv:
        return {"type": "command", "label": label, "status": "fail", "reason": "argv must be a non-empty list"}
    skill_path = Path(str(step.get("skill_path") or "")).expanduser()
    cwd_name = str(check.get("cwd") or "capability")
    if cwd_name == "capability":
        cwd = skill_path
    elif cwd_name == "workspace":
        cwd = workspace
    elif cwd_name == "run":
        cwd = run_dir
    else:
        cwd = run_dir
    if not cwd.is_dir():
        return {"type": "command", "label": label, "status": "fail", "reason": f"cwd does not exist: {cwd}"}
    command = [str(value).replace("{output}", str(output)) for value in argv]
    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(1, int(timeout)),
        )
    except subprocess.TimeoutExpired:
        return {"type": "command", "label": label, "status": "fail", "reason": f"timeout after {timeout}s"}
    except OSError as exc:
        return {"type": "command", "label": label, "status": "fail", "reason": str(exc)}
    expected = int(check.get("expect_returncode", 0))
    ok = proc.returncode == expected
    return {
        "type": "command",
        "label": label,
        "status": "pass" if ok else "fail",
        "expected_returncode": expected,
        "returncode": proc.returncode,
        "stdout_tail": (proc.stdout or "")[-2000:],
        "stderr_tail": (proc.stderr or "")[-2000:],
    }


def verify_pipeline(
    workspace: Path | str,
    run_dir: Path | str,
    *,
    allow_execute: bool = False,
    timeout: int = 120,
) -> dict[str, Any]:
    workspace = Path(workspace)
    run_dir = Path(run_dir).expanduser().resolve()
    plan = read_json(run_dir / "plan.json", {})
    if not isinstance(plan, dict) or plan.get("schema") != PLAN_SCHEMA:
        return {"schema": VERIFY_SCHEMA, "status": "invalid", "reason": f"not a pipeline run: {run_dir}"}

    step_results: list[dict[str, Any]] = []
    for step in plan.get("steps") or []:
        if not isinstance(step, dict):
            continue
        output = Path(str(step.get("output") or "artifacts/missing.md"))
        if not output.is_absolute():
            output = run_dir / output
        if not output.is_file():
            step_results.append(
                {
                    "step": step.get("id"),
                    "capability_id": step.get("capability_id"),
                    "status": "not_run",
                    "reason": f"artifact not found: {output}",
                }
            )
            continue
        text = output.read_text(encoding="utf-8", errors="replace")
        checks: list[dict[str, Any]] = []
        for check in step.get("acceptance") or []:
            if not isinstance(check, dict):
                continue
            if check.get("type") == "command":
                checks.append(
                    _command_check(
                        check,
                        output=output,
                        step=step,
                        run_dir=run_dir,
                        workspace=workspace,
                        allow_execute=allow_execute,
                        timeout=timeout,
                    )
                )
            else:
                verdict = verify_artifact(workspace, text=text, acceptance=[check])
                checks.extend(verdict.get("checks") or [])
        if any(item.get("status") == "fail" for item in checks):
            status = "fail"
        elif any(item.get("status") == "skipped" for item in checks):
            status = "incomplete"
        else:
            status = "pass"
        step_results.append(
            {
                "step": step.get("id"),
                "capability_id": step.get("capability_id"),
                "status": status,
                "artifact": str(output),
                "checks": checks,
            }
        )

    if any(item["status"] == "fail" for item in step_results):
        overall = "fail"
    elif any(item["status"] in {"not_run", "incomplete"} for item in step_results):
        overall = "incomplete"
    else:
        overall = "pass"
    result = {
        "schema": VERIFY_SCHEMA,
        "status": overall,
        "pipeline": plan.get("pipeline"),
        "run_dir": str(run_dir),
        "steps": step_results,
        "verified_at": now_iso(),
    }
    write_json_atomic(run_dir / "verify.json", result)
    return result


def record_pipeline_outcome(
    workspace: Path | str,
    run_dir: Path | str,
    *,
    success: bool | None = None,
    duration_ms: float | None = None,
    human_edit_minutes: float | None = None,
    notes: str = "",
) -> dict[str, Any]:
    workspace = Path(workspace)
    run_dir = Path(run_dir).expanduser().resolve()
    plan = read_json(run_dir / "plan.json", {})
    if not isinstance(plan, dict) or plan.get("schema") != PLAN_SCHEMA:
        return {"schema": OUTCOME_SCHEMA, "status": "invalid", "reason": f"not a pipeline run: {run_dir}"}
    rows: list[dict[str, Any]] = []
    for step in plan.get("steps") or []:
        if not isinstance(step, dict):
            continue
        capability_id = str(step.get("capability_id") or "")
        if not capability_id:
            continue
        rows.append(
            record_outcome(
                workspace,
                capability_id,
                task=str(plan.get("task") or plan.get("goal") or ""),
                success=success,
                duration_ms=duration_ms,
                human_edit_minutes=human_edit_minutes,
                notes=notes,
            )
        )
    result = {
        "schema": OUTCOME_SCHEMA,
        "status": "recorded",
        "pipeline": plan.get("pipeline"),
        "run_dir": str(run_dir),
        "outcomes": rows,
        "recorded_at": now_iso(),
    }
    write_json_atomic(run_dir / "outcome.json", result)
    return result


RUN_SCHEMA = "caprouter.pipeline-run.v1"


def run_pipeline(
    workspace: Path | str,
    run_dir: Path | str,
    *,
    allow_execute: bool = False,
    timeout: int = 120,
) -> dict[str, Any]:
    """Execute command-mode steps deterministically; prompt steps stop the run.

    Prompt-mode capabilities need a host agent. This runner never calls a model
    and never invents an artifact; it records command transcripts and stops.
    """
    from .capabilities import invoke_capability

    workspace = Path(workspace)
    run_dir = Path(run_dir).expanduser().resolve()
    plan = read_json(run_dir / "plan.json", {})
    if not isinstance(plan, dict) or plan.get("schema") != PLAN_SCHEMA:
        return {
            "schema": RUN_SCHEMA,
            "status": "invalid",
            "reason": f"not a pipeline run: {run_dir}",
        }
    if not is_enabled():
        return {
            "schema": RUN_SCHEMA,
            "status": "blocked",
            "reason": "CapRouter is disabled; run: caprouter state --enable",
        }
    results: list[dict[str, Any]] = []
    for step in plan.get("steps") or []:
        if not isinstance(step, dict):
            continue
        step_id = str(step.get("id") or "")
        capability_id = str(step.get("capability_id") or "")
        card = get_capability(workspace, capability_id)
        if not card:
            results.append(
                {"step": step_id, "status": "not_found", "capability_id": capability_id}
            )
            return _finish_run(run_dir, plan, results, "failed")
        invoke = card.get("invoke") if isinstance(card.get("invoke"), dict) else {}
        mode = str(invoke.get("mode") or "none")
        if mode != "command":
            results.append(
                {
                    "step": step_id,
                    "status": "awaiting_host",
                    "capability_id": capability_id,
                    "mode": mode,
                    "skill_path": card.get("path"),
                    "output": step.get("output"),
                    "reason": "prompt-mode step; the host agent must execute it and write the artifact",
                }
            )
            return _finish_run(run_dir, plan, results, "awaiting_host")
        if not allow_execute:
            results.append(
                {
                    "step": step_id,
                    "status": "approval_required",
                    "capability_id": capability_id,
                    "reason": "command step requires allow_execute=true",
                }
            )
            return _finish_run(run_dir, plan, results, "approval_required")
        result = invoke_capability(
            workspace,
            capability_id,
            input_text=str(step.get("input") or plan.get("task") or ""),
            timeout=timeout,
            allow_execute=True,
        )
        transcript = run_dir / "artifacts" / f"{safe_name(step_id)}.command.json"
        transcript.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(transcript, result)
        results.append(
            {
                "step": step_id,
                "status": result.get("status"),
                "capability_id": capability_id,
                "transcript": str(transcript),
                "returncode": result.get("returncode"),
            }
        )
        if result.get("status") != "ok":
            return _finish_run(run_dir, plan, results, "failed")
    return _finish_run(run_dir, plan, results, "executed")


def _finish_run(
    run_dir: Path,
    plan: dict[str, Any],
    results: list[dict[str, Any]],
    status: str,
) -> dict[str, Any]:
    result = {
        "schema": RUN_SCHEMA,
        "status": status,
        "pipeline": plan.get("pipeline"),
        "run_dir": str(run_dir),
        "steps": results,
        "ran_at": now_iso(),
    }
    write_json_atomic(run_dir / "run.json", result)
    return result
