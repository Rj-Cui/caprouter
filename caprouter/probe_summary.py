"""Probe history folding shared by the capability bus and the probe runner.

This module only depends on the standard library. Moving the summary logic here
removes the previous ``capabilities`` <-> ``probes`` import cycle: the bus reads
summaries from this module and the runner imports it too, so neither side has to
import the other for Card/search rendering.
"""
from __future__ import annotations

from typing import Any, Iterable

DEFAULT_MIN_PASS_RATE = 0.8
MAX_CHECKS = 50

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
