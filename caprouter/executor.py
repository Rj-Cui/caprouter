from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .adapters import AdapterResult, run_adapter
from .dispatch import _safe_name, assign
from .paths import runs_dir
from .plan import validate_plan
from .transcript import write_attempt, write_node_status
from .util import now_iso, write_json_atomic
from .verifier import VERIFIER_SYSTEM, build_verify_prompt, parse_verdict


@dataclass
class ExecuteOptions:
    adapter: str | None = None
    timeout: int = 300
    max_turns: int = 8
    max_rework: int = 2
    system_prompt: str | None = None
    dry_run: bool = False


def _prompt_for_node(plan: dict[str, Any], node: dict[str, Any], outputs: dict[str, str], feedback: str | None = None) -> str:
    input_blocks: list[str] = []
    for input_id in node.get("inputs") or []:
        if input_id in outputs:
            input_blocks.append(f"### {input_id}\n{outputs[input_id][:12000]}")
    acceptance = "\n".join(f"- {item}" for item in (node.get("acceptance") or [])) or "- (none)"
    contract = json.dumps(node.get("output_contract") or {}, ensure_ascii=False)
    prompt = (
        "You are executing one node of a CapRouter plan. Do not execute other nodes.\n"
        f"Task: {plan.get('goal')}\n"
        f"Node: {node.get('id')} - {node.get('title') or ''}\n"
        f"Capability: {node.get('capability') or ''}\n"
        f"Brief: {node.get('brief') or ''}\n"
        f"Acceptance criteria:\n{acceptance}\n"
        f"Output contract: {contract}\n"
        "Inputs:\n"
        f"{chr(10).join(input_blocks) or '(none)'}\n"
        "Return only the required output."
    )
    if feedback:
        prompt += f"\n\nPrevious verification failed. Fix these issues:\n{feedback}\nRewrite the output."
    return prompt


class PlanExecutor:
    def __init__(
        self,
        plan: dict[str, Any],
        registry: dict[str, Any],
        workspace: Path,
        *,
        config: dict[str, Any] | None = None,
        options: ExecuteOptions | None = None,
    ) -> None:
        self.plan = plan
        self.registry = registry
        self.workspace = Path(workspace)
        self.config = config or {}
        self.options = options or ExecuteOptions()
        check = validate_plan(plan)
        if not check["ok"]:
            raise ValueError("plan check failed: " + "; ".join(check["errors"]))

    def _agent_for(self, assignment: dict[str, Any]) -> tuple[str | None, str | None]:
        agent = self.options.adapter
        fallback = None
        if not agent:
            primary = assignment.get("agent") or {}
            agent = primary.get("name") if isinstance(primary, dict) else None
            fallback_obj = assignment.get("fallback_agent") or {}
            fallback = fallback_obj.get("name") if isinstance(fallback_obj, dict) else None
        return agent, fallback

    def _invoke(
        self,
        node: dict[str, Any],
        agent: str,
        fallback: str | None,
        prompt: str,
        system_prompt: str | None,
        attempts: dict[str, int],
        run_dir: Path,
    ) -> AdapterResult:
        attempts[node["id"]] = attempts.get(node["id"], 0) + 1
        attempt = attempts[node["id"]]
        max_turns = node.get("max_turns")
        max_turns = max_turns if isinstance(max_turns, int) and max_turns > 0 else self.options.max_turns
        result = run_adapter(
            agent,
            prompt,
            cwd=self.workspace,
            timeout=self.options.timeout,
            max_turns=max_turns,
            system_prompt=system_prompt,
        )
        write_attempt(run_dir, node["id"], attempt, prompt, result)
        if (result.error or result.returncode != 0) and fallback and fallback != agent:
            attempts[node["id"]] += 1
            fallback_result = run_adapter(
                fallback,
                prompt,
                cwd=self.workspace,
                timeout=self.options.timeout,
                max_turns=max_turns,
                system_prompt=system_prompt,
            )
            write_attempt(run_dir, node["id"], attempts[node["id"]], prompt, fallback_result)
            if not fallback_result.error and fallback_result.returncode == 0:
                return fallback_result
        return result

    def run(self) -> dict[str, Any]:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        run_dir = runs_dir(self.workspace) / f"{_safe_name(self.plan.get('task_id'))}-{stamp}"
        run_dir.mkdir(parents=True, exist_ok=False)
        write_json_atomic(run_dir / "plan.json", self.plan)

        routing_policy = self.config.get("routing") if isinstance(self.config, dict) else None
        enabled_agents = self.config.get("enabled_agents") if isinstance(self.config, dict) else None
        assignments = assign(self.plan, self.registry, routing_policy, enabled_agents)
        by_node = {item.get("node_id"): item for item in assignments.get("nodes") or []}
        nodes = [node for node in (self.plan.get("nodes") or []) if isinstance(node, dict)]
        node_by_id = {node.get("id"): node for node in nodes}
        outputs: dict[str, str] = {}
        attempts: dict[str, int] = {}
        statuses: list[dict[str, Any]] = []

        budget = self.config.get("budgets") if isinstance(self.config.get("budgets"), dict) else {}
        max_seconds = float(budget.get("max_task_seconds") or 600)
        deadline = time.time() + max_seconds
        final_id: str | None = None
        previous_id: str | None = None
        failed = False

        for node in nodes:
            if time.time() > deadline:
                statuses.append({"node_id": node.get("id"), "status": "paused", "error": "task budget exceeded"})
                failed = True
                break

            assignment = by_node.get(node.get("id")) or {}
            agent, fallback = self._agent_for(assignment)
            if not agent:
                statuses.append({"node_id": node.get("id"), "status": "paused", "error": "no available CLI agent"})
                failed = True
                break

            capability = str(node.get("capability") or "")
            is_verify = capability == "verify" or str((node.get("output_contract") or {}).get("type") or "") == "claim_ledger"

            if is_verify:
                target_id = next((item for item in (node.get("inputs") or []) if item in outputs), previous_id)
                if not target_id or target_id not in outputs:
                    statuses.append(
                        {"node_id": node.get("id"), "status": "paused", "error": "verifier has no target output"}
                    )
                    failed = True
                    break

                rework = 0
                verdict: dict[str, Any] = {"ok": False, "issues": [{"problem": "not run"}]}
                while True:
                    sources = "\n\n".join(
                        outputs[item] for item in (node.get("inputs") or []) if item in outputs and item != target_id
                    )
                    prompt = build_verify_prompt(outputs[target_id], node.get("acceptance") or [], sources)
                    result = self._invoke(
                        node,
                        agent,
                        fallback,
                        prompt,
                        VERIFIER_SYSTEM,
                        attempts,
                        run_dir,
                    )
                    if result.error or result.returncode != 0:
                        statuses.append(
                            {
                                "node_id": node.get("id"),
                                "status": "paused",
                                "error": result.error or result.stderr[:500],
                                "adapter": result.adapter,
                            }
                        )
                        failed = True
                        break
                    verdict = parse_verdict(str((result.parsed or {}).get("text") or result.stdout))
                    if verdict["ok"] or rework >= self.options.max_rework:
                        break
                    target_node = node_by_id.get(target_id)
                    if not target_node:
                        break
                    rework += 1
                    target_assignment = by_node.get(target_id) or {}
                    target_agent, target_fallback = self._agent_for(target_assignment)
                    if not target_agent:
                        break
                    feedback = json.dumps(verdict["issues"], ensure_ascii=False, indent=2)
                    target_prompt = _prompt_for_node(self.plan, target_node, outputs, feedback)
                    target_result = self._invoke(
                        target_node,
                        target_agent,
                        target_fallback,
                        target_prompt,
                        self.options.system_prompt,
                        attempts,
                        run_dir,
                    )
                    if target_result.error or target_result.returncode != 0:
                        break
                    outputs[target_id] = str((target_result.parsed or {}).get("text") or target_result.stdout)

                if failed:
                    break
                outputs[node.get("id")] = json.dumps(verdict, ensure_ascii=False)
                statuses.append(
                    {
                        "node_id": node.get("id"),
                        "status": "passed" if verdict["ok"] else "accepted_with_issues",
                        "capability": capability,
                        "adapter": agent,
                        "verdict": verdict["ok"],
                        "issues": verdict["issues"],
                        "rework": rework,
                    }
                )
                previous_id = node.get("id")
                continue

            prompt = _prompt_for_node(self.plan, node, outputs)
            result = self._invoke(
                node,
                agent,
                fallback,
                prompt,
                self.options.system_prompt,
                attempts,
                run_dir,
            )
            if result.error or result.returncode != 0:
                statuses.append(
                    {
                        "node_id": node.get("id"),
                        "status": "paused",
                        "capability": capability,
                        "adapter": result.adapter,
                        "error": result.error or result.stderr[:500],
                    }
                )
                failed = True
                break

            text = str((result.parsed or {}).get("text") or result.stdout).strip()
            if not text:
                statuses.append({"node_id": node.get("id"), "status": "paused", "error": "empty output"})
                failed = True
                break
            outputs[node.get("id")] = text
            final_id = node.get("id")
            previous_id = node.get("id")
            statuses.append(
                {
                    "node_id": node.get("id"),
                    "status": "passed",
                    "capability": capability,
                    "adapter": result.adapter,
                    "duration_ms": result.duration_ms,
                }
            )

        for status in statuses:
            write_node_status(run_dir, str(status.get("node_id")), status)

        delivery = Path(str((self.config.get("delivery") or ""))) if self.config.get("delivery") else None
        final_text = outputs.get(final_id) if final_id else None
        if not failed and final_text:
            (run_dir / "final.md").write_text(final_text, encoding="utf-8")
            if delivery:
                delivery.mkdir(parents=True, exist_ok=True)
                (delivery / f"{_safe_name(self.plan.get('task_id'))}.md").write_text(final_text, encoding="utf-8")

        summary = {
            "schema": "caprouter.run.v1",
            "run_dir": str(run_dir),
            "task_id": self.plan.get("task_id"),
            "status": "paused" if failed else "completed",
            "dry_run": False,
            "nodes": statuses,
            "final_chars": len(final_text or ""),
            "final_path": str(run_dir / "final.md") if final_text and not failed else None,
            "created_at": now_iso(),
        }
        write_json_atomic(run_dir / "run.json", summary)
        return summary


def execute_plan(
    plan: dict[str, Any],
    registry: dict[str, Any],
    workspace: Path,
    *,
    config: dict[str, Any] | None = None,
    options: ExecuteOptions | None = None,
) -> dict[str, Any]:
    return PlanExecutor(plan, registry, workspace, config=config, options=options).run()
