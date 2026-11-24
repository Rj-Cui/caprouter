from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from .paths import runs_dir
from .route import route
from .util import now_iso, write_json_atomic


def _safe_name(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "task")).strip("-")
    return text[:80] or "task"


def assign(
    plan: dict[str, Any],
    registry: dict[str, Any],
    routing_policy: dict[str, Any] | None = None,
    enabled_agents: list[str] | None = None,
) -> dict[str, Any]:
    policy = routing_policy if isinstance(routing_policy, dict) else {}
    enabled = [str(item) for item in (enabled_agents or [])]
    entries = [entry for entry in (registry.get("entries") or []) if isinstance(entry, dict)]
    assignments: list[dict[str, Any]] = []
    for node in plan.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        need_parts = [
            str(node.get("title") or ""),
            str(node.get("brief") or ""),
            str(node.get("goal") or ""),
            " ".join(str(item) for item in (node.get("skill_requirements") or [])),
        ]
        need = " ".join(part for part in need_parts if part).strip()
        top_k = node.get("top_k")
        top_k = top_k if isinstance(top_k, int) and 1 <= top_k <= 10 else 5
        capability = node.get("capability")
        capabilities = [capability] if isinstance(capability, str) and capability else None
        domain = node.get("domain")
        domains = [domain] if isinstance(domain, str) and domain else None

        skill_result = route(
            entries,
            need,
            top_k=top_k,
            types=["skill"],
            capabilities=capabilities,
            domains=domains,
            min_score=float(policy.get("min_score") or 0.0),
            exclude_path_contains=policy.get("exclude_path_contains") or (),
            exclude_id_prefixes=policy.get("exclude_id_prefixes") or (),
        )
        skills = skill_result.get("results") or []

        def agent_allowed(entry: dict[str, Any]) -> bool:
            if entry.get("type") != "cli-agent" or entry.get("status") != "verified":
                return False
            entry_caps = {str(item) for item in (entry.get("capabilities") or [])}
            if capabilities and not (set(capabilities) & entry_caps):
                return False
            canonical = str(entry.get("canonical") or entry.get("name"))
            if enabled and entry.get("name") not in enabled and canonical not in enabled:
                return False
            path = Path(str(entry.get("path") or ""))
            if not (path.exists() or shutil.which(str(entry.get("name")))):
                return False
            return True

        agent_pool = [entry for entry in entries if agent_allowed(entry)]
        deduped_pool: list[dict[str, Any]] = []
        seen_agents: set[str] = set()
        for entry in agent_pool:
            canonical = str(entry.get("canonical") or entry.get("name"))
            if canonical in seen_agents:
                continue
            seen_agents.add(canonical)
            deduped_pool.append(entry)
        agent_pool = deduped_pool
        def enabled_rank(entry: dict[str, Any]) -> int:
            name = str(entry.get("name"))
            canonical = str(entry.get("canonical") or name)
            if name in enabled:
                return enabled.index(name)
            if canonical in enabled:
                return enabled.index(canonical)
            return 999

        agent_pool.sort(key=lambda entry: (enabled_rank(entry), str(entry.get("name"))))
        agent_candidates = [
            {
                "id": entry.get("id"),
                "type": "cli-agent",
                "name": entry.get("name"),
                "path": entry.get("path"),
                "description": entry.get("description"),
                "domain": entry.get("domain") or "general",
                "capabilities": entry.get("capabilities") or [],
                "status": entry.get("status"),
                "available": True,
                "score": None,
                "why": ["capability match"],
            }
            for entry in agent_pool[:2]
        ]

        warnings: list[str] = []
        if not skills:
            warnings.append("no skill matched; the executor agent will use its base capabilities")
        if not agent_candidates:
            warnings.append(f"no enabled CLI agent covers capability {capability or 'any'}")

        assignments.append(
            {
                "node_id": node.get("id"),
                "need": need,
                "capability": capability,
                "domain": domain,
                "skill": skills[0] if skills else None,
                "agent": agent_candidates[0] if agent_candidates else None,
                "fallback_agent": agent_candidates[1] if len(agent_candidates) > 1 else None,
                "candidates": skills + agent_candidates,
                "warnings": warnings,
            }
        )
    return {
        "schema": "caprouter.dispatch.v1",
        "generated_at": now_iso(),
        "nodes": assignments,
    }


def run_plan(
    plan: dict[str, Any],
    registry: dict[str, Any],
    workspace: Path,
    *,
    dry_run: bool = True,
    routing_policy: dict[str, Any] | None = None,
    enabled_agents: list[str] | None = None,
) -> dict[str, Any]:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = runs_dir(workspace) / f"{_safe_name(plan.get('task_id'))}-{stamp}"
    run_dir.mkdir(parents=True, exist_ok=False)
    write_json_atomic(run_dir / "plan.json", plan)
    dispatch_plan = assign(plan, registry, routing_policy, enabled_agents)
    write_json_atomic(run_dir / "dispatch.json", dispatch_plan)
    (run_dir / "agents").mkdir(exist_ok=True)
    (run_dir / "output").mkdir(exist_ok=True)

    summary = {
        "schema": "caprouter.run.v1",
        "run_dir": str(run_dir),
        "task_id": plan.get("task_id"),
        "dry_run": dry_run,
        "nodes": [
            {
                "node_id": item.get("node_id"),
                "skill": (item.get("skill") or {}).get("id"),
                "agent": (item.get("agent") or {}).get("id"),
                "fallback_agent": (item.get("fallback_agent") or {}).get("id"),
            }
            for item in dispatch_plan["nodes"]
        ],
    }
    write_json_atomic(run_dir / "summary.json", summary)

    if dry_run:
        return summary
    raise NotImplementedError(
        "Phase 1 has not been enabled: CLI adapter dispatch, transcript capture and "
        "claim-level verification are not implemented yet. Use --dry-run."
    )
