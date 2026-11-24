"""Derived capability lifecycle state from probes and outcomes.

States are read-only derivations; nothing here changes the registry.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from .capabilities import _stats_for, load_entries, load_overlay
from .probes import probe_report

HEALTH_SCHEMA = "caprouter.health.v1"
STATE_ORDER = {"degraded": 0, "probation": 1, "draft": 2, "active": 3, "retired": 4}


def _state(
    stats: dict[str, Any],
    probe: dict[str, Any] | None,
    card: dict[str, Any] | None,
) -> str:
    if isinstance(card, dict) and str(card.get("lifecycle") or "") == "retired":
        return "retired"
    probe_status = str((probe or {}).get("status") or "").strip().lower()
    if probe_status == "fail":
        return "degraded"
    calls = int(stats.get("calls") or 0)
    rate = stats.get("success_rate")
    if calls >= 2 and isinstance(rate, (int, float)) and float(rate) < 0.5:
        return "degraded"
    if probe_status == "pass":
        return "active"
    if calls >= 1 and isinstance(rate, (int, float)) and float(rate) >= 0.5:
        return "active"
    if isinstance(card, dict):
        return "probation"
    return "draft"


def capability_health(
    workspace: Path | str,
    *,
    capability_id: str | None = None,
    run_probes: bool = False,
) -> dict[str, Any]:
    workspace = Path(workspace)
    if run_probes:
        from .probes import run_all_probes

        run_all_probes(workspace)
    stats = _stats_for(workspace)
    overlay = load_overlay(workspace)["cards"]
    summaries = {
        str(item.get("id")): item
        for item in (probe_report(workspace).get("summaries") or [])
        if item.get("id")
    }
    rows: list[dict[str, Any]] = []
    for entry in load_entries(workspace):
        identifier = str(entry.get("id") or "")
        if not identifier or (capability_id and identifier != capability_id):
            continue
        item_stats = stats.get(identifier, {})
        probe = summaries.get(identifier)
        card = overlay.get(identifier)
        probe_status = (probe or {}).get("status")
        if not probe_status and isinstance(card, dict):
            probe_status = (card.get("probe") or {}).get("status")
        rows.append(
            {
                "id": identifier,
                "type": entry.get("type"),
                "name": entry.get("name"),
                "path": entry.get("path"),
                "state": _state(item_stats, probe, card),
                "probe_status": probe_status,
                "probe_evidence": (probe or {}).get("evidence"),
                "success_rate": item_stats.get("success_rate"),
                "calls": item_stats.get("calls"),
                "avg_human_edit_minutes": item_stats.get("avg_human_edit_minutes"),
            }
        )
    rows.sort(key=lambda item: (STATE_ORDER.get(str(item["state"]), 9), str(item["id"])))
    return {
        "schema": HEALTH_SCHEMA,
        "count": len(rows),
        "counts": dict(Counter(str(item["state"]) for item in rows)),
        "capabilities": rows,
    }
