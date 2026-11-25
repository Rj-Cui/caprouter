from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPORT_WORDS = (
    "报告", "report", "撰写", "写一篇", "写一份", "1500字", "长文", "白皮书", "分析文章",
)
URL_ONLY_TYPES = {"url_list", "urls", "links", "link_list"}
URL_ONLY_WORDS = ("only return", "只返回", "仅返回", "url list", "链接列表", "不要抓取", "不抓取正文")
REPORT_TYPES = {"report", "markdown", "article", "doc", "document"}


def load_plan(path: Path | str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _text(value: Any) -> str:
    return str(value or "").strip()


def _node_text(node: dict[str, Any]) -> str:
    parts = [
        _text(node.get("title")),
        _text(node.get("brief")),
        _text(node.get("goal")),
        " ".join(str(item) for item in (node.get("requirements") or [])),
    ]
    return " ".join(part for part in parts if part)


def _contract_type(contract: Any) -> str:
    if not isinstance(contract, dict):
        return ""
    return _text(contract.get("type") or contract.get("format")).lower()


def _contract_conflict(brief: str, contract: Any) -> str | None:
    if not isinstance(contract, dict):
        return None
    lowered = brief.lower()
    ctype = _contract_type(contract)
    if ctype in URL_ONLY_TYPES and any(word.lower() in lowered for word in REPORT_WORDS):
        return "brief asks for a report but output_contract is URL-only; split into retrieve -> generate"
    if ctype in REPORT_TYPES and any(word.lower() in lowered for word in URL_ONLY_WORDS):
        return "brief says URL-only but output_contract expects a report; align contract or brief"
    if ctype == "url_list" and contract.get("fetch_body"):
        return "URL-only contract cannot also require fetching page bodies; split fetch into its own node"
    return None


def validate_plan(plan: Any) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(plan, dict):
        return {
            "schema": "caprouter.plan-check.v1",
            "ok": False,
            "errors": ["plan must be a JSON object"],
            "warnings": [],
        }

    for key in ("task_id", "goal", "nodes"):
        if key not in plan:
            errors.append(f"missing required field: {key}")
    nodes = plan.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        errors.append("nodes must be a non-empty array")
        nodes = []

    gate = plan.get("quality_gate")
    if gate is not None:
        if not isinstance(gate, dict):
            errors.append("quality_gate must be an object")
        else:
            attempts = gate.get("max_attempts")
            if not isinstance(attempts, int) or attempts < 1 or attempts > 3:
                errors.append("quality_gate.max_attempts must be an integer between 1 and 3")

    seen_ids: set[str] = set()
    for index, node in enumerate(nodes):
        label = f"nodes[{index}]"
        if not isinstance(node, dict):
            errors.append(f"{label} must be an object")
            continue
        node_id = _text(node.get("id"))
        if not node_id:
            errors.append(f"{label}.id is required")
        elif node_id in seen_ids:
            errors.append(f"duplicate node id: {node_id}")
        else:
            seen_ids.add(node_id)
            label = node_id

        brief = _node_text(node)
        if not brief:
            errors.append(f"{label}: brief/title/goal is empty")

        if not _text(node.get("capability")):
            warnings.append(f"{label}: capability is missing; routing will be lexical only")
        if not _text(node.get("domain")):
            warnings.append(f"{label}: domain is missing; domain filtering is disabled")

        contract = node.get("output_contract")
        if contract is None:
            warnings.append(f"{label}: output_contract is missing")
        elif not isinstance(contract, dict):
            errors.append(f"{label}: output_contract must be an object")
        else:
            conflict = _contract_conflict(brief, contract)
            if conflict:
                errors.append(f"{label}: {conflict}")

        acceptance = node.get("acceptance")
        if not isinstance(acceptance, list) or not acceptance:
            warnings.append(f"{label}: acceptance is empty; verification will be generic")

        max_turns = node.get("max_turns")
        if isinstance(max_turns, int) and max_turns > 12:
            warnings.append(f"{label}: max_turns={max_turns} is high; prefer deterministic tools")

        inputs = node.get("inputs")
        if inputs is not None and not isinstance(inputs, list):
            errors.append(f"{label}: inputs must be an array")

    return {
        "schema": "caprouter.plan-check.v1",
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
    }
