"""Capability bus: capability cards, two-stage search, invoke and verification.

This module is the runtime behind the MCP server. It deliberately has no
third-party dependencies and no model call in the default path: routing is
lexical + contract scoring, and verification is deterministic. An LLM can be
added later as an optional reranker or semantic verifier, but it is not
required to use the bus.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Iterable

from .audit import append_audit
from .claims import verify_claims
from .probes import load_probe_rows, summarize_probe
from .route import expand_query, route, tokenize
from .state import is_enabled
from .util import append_jsonl, now_iso, read_json, read_jsonl, sha256_text

CARD_SCHEMA = "caprouter.capability-card.v1"
SEARCH_SCHEMA = "caprouter.capability-search.v1"
INVOKE_SCHEMA = "caprouter.capability-invoke.v1"
VERIFY_SCHEMA = "caprouter.capability-verify.v1"
OUTCOME_SCHEMA = "caprouter.capability-outcome.v1"

DEFAULT_MIN_CONFIDENCE = 0.18
DEFAULT_MIN_MARGIN = 0.02
DEFAULT_MIN_COVERAGE = 0.12

_HAN_RE = re.compile(r"[一-鿿]")
_URL_RE = re.compile(r"https?://[^\s)\]>\"']+")
_NUMBERED_RE = re.compile(r"^\s*\d+[.、)]\s+", re.MULTILINE)
_BULLET_RE = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE)
_HEADING_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_SOURCE_REF_RE = re.compile(r"\[(?:S|c)\d+\]")
_NEGATIVE_RE = re.compile(
    r"reading only|not supported|does not support|cannot (?:create|edit|convert)|"
    r"仅(?:支持)?读取|不支持(?:创建|编辑|转换|导出)",
    re.IGNORECASE,
)
_INTENT_HINTS: dict[str, tuple[str, ...]] = {
    "retrieve": ("搜索", "检索", "调研", "查", "找", "收集", "来源", "search", "research", "retrieve", "fetch"),
    "transform": ("转", "转换", "改写", "润色", "翻译", "总结", "整理", "convert", "transform", "rewrite", "translate", "summarize"),
    "generate": ("写", "生成", "创作", "报告", "文案", "draft", "write", "generate"),
    "verify": ("检查", "核查", "验证", "校验", "审查", "review", "verify", "check", "audit"),
    "analyze": ("分析", "比较", "对比", "评估", "analyze", "compare"),
    "format": ("格式", "排版", "ppt", "docx", "xlsx", "pdf", "表格", "幻灯片", "format"),
}
_CREATION_HINTS = ("转成", "转换", "导出", "生成文件", "创建", "编辑", "convert", "export", "create", "edit")
_RANGE_HAN_RE = re.compile(r"(\d+)\s*[-–—~至]\s*(\d+)\s*个?\s*汉字")
_RANGE_CHARS_RE = re.compile(r"(\d+)\s*[-–—~至]\s*(\d+)\s*个?\s*字")
_PLUS_MINUS_RE = re.compile(r"(\d+)\s*[±]\s*(\d+)\s*%")
_AT_LEAST_RE = re.compile(r"至少\s*(\d+)")
_EXACT_RE = re.compile(r"正好\s*(\d+)")


# ---------------------------------------------------------------------------
# paths and loading


def bus_dir(workspace: Path | str) -> Path:
    return Path(workspace) / ".caprouter"


def overlay_path(workspace: Path | str) -> Path:
    return bus_dir(workspace) / "capability_cards.json"


def outcomes_path(workspace: Path | str) -> Path:
    return bus_dir(workspace) / "outcomes.jsonl"


def gaps_path(workspace: Path | str) -> Path:
    return bus_dir(workspace) / "gaps.jsonl"


def load_entries(workspace: Path | str) -> list[dict[str, Any]]:
    data = read_json(bus_dir(workspace) / "registry.json", {})
    entries = data.get("entries") if isinstance(data, dict) else None
    return [item for item in (entries or []) if isinstance(item, dict)]


def load_overlay(workspace: Path | str) -> dict[str, Any]:
    data = read_json(overlay_path(workspace), {})
    if not isinstance(data, dict):
        return {"cards": {}, "defaults": {}}
    cards = data.get("cards")
    if isinstance(cards, dict):
        return {"cards": {str(k): v for k, v in cards.items() if isinstance(v, dict)},
                "defaults": data.get("defaults") if isinstance(data.get("defaults"), dict) else {}}
    # Allow a flat map as well: {"skill:foo": {...}}
    return {"cards": {str(k): v for k, v in data.items() if isinstance(v, dict) and not str(k).startswith("_")},
            "defaults": {}}


def bus_config(workspace: Path | str) -> dict[str, Any]:
    config = read_json(bus_dir(workspace) / "config.json", {})
    bus = config.get("bus") if isinstance(config, dict) else None
    return bus if isinstance(bus, dict) else {}


# ---------------------------------------------------------------------------
# cards


def _health(entry: dict[str, Any]) -> dict[str, Any]:
    if entry.get("type") == "cli-agent":
        name = str(entry.get("canonical") or entry.get("name") or "")
        available = bool(shutil.which(name))
    else:
        available = Path(str(entry.get("path") or "")).exists()
    return {"available": available, "checked_at": now_iso()}


def default_invoke(entry: dict[str, Any]) -> dict[str, Any]:
    entry_type = str(entry.get("type") or "")
    path = Path(str(entry.get("path") or ""))
    if entry_type == "skill":
        return {"mode": "prompt", "content_path": str(path / "SKILL.md"), "allow": False}
    if entry_type == "plugin":
        return {"mode": "prompt", "content_path": str(path), "allow": False}
    return {"mode": "none", "allow": False}


def _stats_for(workspace: Path | str) -> dict[str, dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(outcomes_path(workspace)):
        capability_id = str(row.get("capability_id") or "")
        if not capability_id:
            continue
        item = stats.setdefault(
            capability_id,
            {"calls": 0, "success": 0, "fail": 0, "durations": [], "edits": []},
        )
        item["calls"] += 1
        if row.get("success") is True:
            item["success"] += 1
        elif row.get("success") is False:
            item["fail"] += 1
        if isinstance(row.get("duration_ms"), (int, float)):
            item["durations"].append(float(row["duration_ms"]))
        if isinstance(row.get("human_edit_minutes"), (int, float)):
            item["edits"].append(float(row["human_edit_minutes"]))
    result: dict[str, dict[str, Any]] = {}
    for capability_id, item in stats.items():
        calls = item["calls"]
        result[capability_id] = {
            "calls": calls,
            "success": item["success"],
            "fail": item["fail"],
            "success_rate": round(item["success"] / calls, 3) if calls else None,
            "avg_duration_ms": round(sum(item["durations"]) / len(item["durations"]), 1) if item["durations"] else None,
            "avg_human_edit_minutes": round(sum(item["edits"]) / len(item["edits"]), 1) if item["edits"] else None,
        }
    return result


def _list_field(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def merge_entry_overlay(entry: dict[str, Any], overlay_card: dict[str, Any] | None) -> dict[str, Any]:
    """Apply reviewed Card metadata at read time so drafts route immediately."""
    if not isinstance(overlay_card, dict):
        return entry
    merged = dict(entry)
    for key in ("description", "capabilities", "keywords", "domain", "domains"):
        value = overlay_card.get(key)
        if value in (None, "", []):
            continue
        if key in {"capabilities", "keywords", "domains"}:
            merged[key] = _list_field(value)
        else:
            merged[key] = value
    return merged


def build_card(entry: dict[str, Any], overlay: dict[str, Any] | None = None,
               stats: dict[str, Any] | None = None,
               probe: dict[str, Any] | None = None) -> dict[str, Any]:
    overlay = overlay or {}
    entry_type = str(entry.get("type") or "")
    description = str(overlay.get("description") or entry.get("description") or "")
    capabilities = _list_field(overlay.get("capabilities")) or _list_field(entry.get("capabilities"))
    keywords = _list_field(overlay.get("keywords")) or _list_field(entry.get("keywords"))
    domain = str(overlay.get("domain") or entry.get("domain") or "general")
    domains = _list_field(overlay.get("domains")) or ([domain] if domain else [])
    invoke = overlay.get("invoke")
    if not isinstance(invoke, dict):
        invoke = default_invoke(entry)
    if not isinstance(probe, dict):
        probe = summarize_probe(str(entry.get("id") or ""), [], overlay.get("probe"))
    return {
        "schema": CARD_SCHEMA,
        "id": entry.get("id"),
        "type": entry_type,
        "name": entry.get("name"),
        "description": description,
        "domain": domain,
        "domains": domains,
        "capabilities": capabilities,
        "keywords": keywords,
        "path": entry.get("path"),
        "relative_path": entry.get("relative_path"),
        "version": entry.get("version"),
        "hash": entry.get("hash"),
        "status": entry.get("status"),
        "input_schema": overlay.get("input_schema"),
        "output_schema": overlay.get("output_schema"),
        "examples": overlay.get("examples") or [],
        "permissions": overlay.get("permissions") or {},
        "probe": probe,
        "invoke": invoke,
        "provenance": {
            "source": "registry",
            "version": entry.get("version"),
            "hash": entry.get("hash"),
        },
        "health": _health(entry),
        "stats": stats or {
            "calls": 0, "success": 0, "fail": 0,
            "success_rate": None, "avg_duration_ms": None, "avg_human_edit_minutes": None,
        },
    }


def get_capability(workspace: Path | str, capability_id: str) -> dict[str, Any] | None:
    overlay = load_overlay(workspace)
    stats = _stats_for(workspace)
    probe_rows = load_probe_rows(workspace)
    for entry in load_entries(workspace):
        if str(entry.get("id")) == str(capability_id):
            overlay_card = overlay["cards"].get(str(capability_id)) or {}
            probe = summarize_probe(str(capability_id), probe_rows, overlay_card.get("probe"))
            return build_card(entry, overlay_card, stats.get(str(capability_id)), probe=probe)
    return None


# ---------------------------------------------------------------------------
# search


def _query_intents(need: str) -> set[str]:
    lowered = str(need or "").lower()
    return {
        intent
        for intent, hints in _INTENT_HINTS.items()
        if any(str(hint).lower() in lowered for hint in hints)
    }


def _creation_intent(need: str) -> bool:
    lowered = str(need or "").lower()
    return any(str(hint).lower() in lowered for hint in _CREATION_HINTS)


def _candidate_summary(card: dict[str, Any], score: float, reasons: list[str],
                       why: list[str] | None = None) -> dict[str, Any]:
    return {
        "id": card.get("id"),
        "type": card.get("type"),
        "name": card.get("name"),
        "description": card.get("description"),
        "domain": card.get("domain"),
        "domains": card.get("domains"),
        "capabilities": card.get("capabilities"),
        "path": card.get("path"),
        "score": round(min(1.0, max(0.0, score)), 4),
        "reasons": list(dict.fromkeys(reasons)),
        "why": why or [],
        "health": card.get("health"),
        "probe_status": (card.get("probe") or {}).get("status"),
        "probe_evidence": (card.get("probe") or {}).get("evidence"),
        "probe_pass_rate": (card.get("probe") or {}).get("pass_rate"),
        "success_rate": (card.get("stats") or {}).get("success_rate"),
        "invoke_mode": (card.get("invoke") or {}).get("mode"),
    }


def search_capabilities(
    workspace: Path | str,
    need: str,
    *,
    top: int = 5,
    types: Iterable[str] | None = None,
    capabilities: Iterable[str] | None = None,
    domains: Iterable[str] | None = None,
    min_confidence: float | None = None,
    min_margin: float | None = None,
    min_coverage: float | None = None,
    include_unavailable: bool = False,
    record_gap: bool = False,
) -> dict[str, Any]:
    entries = load_entries(workspace)
    if not entries:
        return {
            "schema": SEARCH_SCHEMA,
            "need": need,
            "decision": "abstain",
            "selected": None,
            "candidates": [],
            "count": 0,
            "error": "registry is empty or missing; run caprouter scan",
            "checked_at": now_iso(),
        }

    config = read_json(bus_dir(workspace) / "config.json", {})
    policy = config.get("routing") if isinstance(config, dict) else None
    policy = policy if isinstance(policy, dict) else {}
    bus = bus_config(workspace)
    overlay = load_overlay(workspace)
    defaults = overlay.get("defaults") or {}
    stats = _stats_for(workspace)
    cards = overlay.get("cards") or {}
    entries = [
        merge_entry_overlay(entry, cards.get(str(entry.get("id"))))
        for entry in entries
    ]

    min_confidence = float(
        min_confidence if min_confidence is not None
        else bus.get("min_confidence", defaults.get("min_confidence", DEFAULT_MIN_CONFIDENCE))
    )
    min_margin = float(
        min_margin if min_margin is not None
        else bus.get("min_margin", defaults.get("min_margin", DEFAULT_MIN_MARGIN))
    )
    min_coverage = float(
        min_coverage if min_coverage is not None
        else bus.get("min_coverage", defaults.get("min_coverage", DEFAULT_MIN_COVERAGE))
    )

    pool_size = max(20, int(top) * 4)
    pool = route(
        entries,
        need,
        top_k=pool_size,
        types=types,
        capabilities=capabilities,
        domains=domains,
        min_score=0.0,
        exclude_path_contains=policy.get("exclude_path_contains") or (),
        exclude_id_prefixes=policy.get("exclude_id_prefixes") or (),
    )
    entry_by_id = {str(entry.get("id")): entry for entry in entries}
    query_tokens = set(expand_query(need))
    query_intents = _query_intents(need)
    creation_intent = _creation_intent(need)
    probe_rows = load_probe_rows(workspace)

    scored: list[tuple[float, dict[str, Any], list[str], list[str]]] = []
    for item in pool.get("results") or []:
        capability_id = str(item.get("id"))
        entry = entry_by_id.get(capability_id)
        if not entry:
            continue
        overlay_card = overlay["cards"].get(capability_id) or {}
        probe = summarize_probe(capability_id, probe_rows, overlay_card.get("probe"))
        card = build_card(entry, overlay_card, stats.get(capability_id), probe=probe)
        if not include_unavailable and not (card.get("health") or {}).get("available", False):
            continue
        score = float(item.get("score") or 0.0)
        reasons: list[str] = ["lexical"]
        why = [str(value) for value in (item.get("why") or [])]

        probe_status = str((card.get("probe") or {}).get("status") or "unknown")
        probe_evidence = str((card.get("probe") or {}).get("evidence") or "unknown")
        if probe_status == "pass":
            # Static checks prove the capability is installable, not that it
            # actually performed the task. Behavioral evidence earns more.
            if probe_evidence in {"behavioral", "mixed"}:
                score += 0.05
                reasons.append("probe:pass:behavioral")
            else:
                score += 0.01
                reasons.append("probe:pass:static")
        elif probe_status == "fail":
            score -= 0.15
            reasons.append("probe:fail")

        success_rate = (card.get("stats") or {}).get("success_rate")
        if isinstance(success_rate, (int, float)):
            score += 0.08 * (float(success_rate) - 0.5)
            reasons.append(f"success_rate:{float(success_rate):.2f}")

        examples = card.get("examples") or []
        example_text = " ".join(
            str(value.get("task") if isinstance(value, dict) else value) for value in examples
        )
        if example_text:
            overlap = query_tokens & set(tokenize(example_text))
            if overlap:
                score += min(0.06, 0.02 * len(overlap))
                reasons.append("example_overlap")

        card_capabilities = {str(value) for value in (card.get("capabilities") or [])}
        matched_intents = query_intents & card_capabilities
        if matched_intents:
            score += min(0.12, 0.06 * len(matched_intents))
            reasons.append("intent:" + ",".join(sorted(matched_intents)))
        elif query_intents:
            score -= 0.02
            reasons.append("intent_miss")

        if creation_intent and _NEGATIVE_RE.search(str(card.get("description") or "")):
            score -= 0.3
            reasons.append("negative_description")

        if (card.get("invoke") or {}).get("mode") == "none":
            score -= 0.03
            reasons.append("no_invoke")

        scored.append((score, card, reasons, why))

    scored.sort(key=lambda item: (-item[0], str(item[1].get("id"))))
    selected_items = scored[: max(1, int(top))]
    candidates = [
        _candidate_summary(card, score, reasons, why)
        for score, card, reasons, why in selected_items
    ]
    confidence = candidates[0]["score"] if candidates else 0.0
    if len(candidates) >= 2:
        margin = round(candidates[0]["score"] - candidates[1]["score"], 4)
    else:
        margin = confidence
    top_why = set(candidates[0].get("why") or []) if candidates else set()
    coverage = round(len(top_why) / max(1, len(query_tokens)), 3)

    decision = (
        "select"
        if candidates
        and confidence >= min_confidence
        and margin >= min_margin
        and (coverage >= min_coverage or confidence >= 0.5)
        else "abstain"
    )
    abstain_reason = None
    if decision == "abstain":
        if not candidates:
            abstain_reason = "no lexical candidates"
        elif confidence < min_confidence:
            abstain_reason = "confidence below threshold"
        elif margin < min_margin:
            abstain_reason = "top candidates too close"
        elif coverage < min_coverage and confidence < 0.5:
            abstain_reason = "query coverage too low"
        else:
            abstain_reason = "thresholds not met"
    result = {
        "schema": SEARCH_SCHEMA,
        "need": need,
        "decision": decision,
        "selected": candidates[0] if decision == "select" else None,
        "candidates": candidates,
        "count": len(candidates),
        "confidence": confidence,
        "margin": margin,
        "coverage": coverage,
        "abstain_reason": abstain_reason,
        "thresholds": {
            "min_confidence": min_confidence,
            "min_margin": min_margin,
            "min_coverage": min_coverage,
        },
        "checked_at": now_iso(),
    }
    if decision == "abstain" and record_gap:
        record_gap(
            workspace,
            need=need,
            reason="no capability above confidence/margin threshold",
            tried=[item.get("id") for item in candidates],
            workaround="",
        )
    return result


# ---------------------------------------------------------------------------
# invoke


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def _audit_invoke(workspace: Path | str, capability_id: str, decision: str, reason: str) -> None:
    """Record execution decisions without letting a bad audit path break invoke."""
    try:
        append_audit(
            {
                "event": "capability_invoke",
                "workspace": str(workspace),
                "capability_id": capability_id,
                "decision": decision,
                "reason": reason,
            }
        )
    except OSError:
        pass


def _invoke_permission_error(card: dict[str, Any]) -> str | None:
    """Return why the Card cannot execute a command, or None when it may.

    Execution is opt-in at Card level: a Card must declare a permissions
    block with ``shell=true``. Cards produced by scan have no permissions,
    so they can never execute until a reviewed draft writes one.
    """
    permissions = card.get("permissions")
    if not isinstance(permissions, dict) or not permissions:
        return "command execution requires an explicit Card permissions block with shell=true"
    fs = permissions.get("fs")
    if not isinstance(fs, list) or not all(isinstance(item, str) for item in fs):
        return "Card permissions.fs must be a list of strings"
    unsupported = sorted({item for item in fs if item not in {"read", "write"}})
    if unsupported:
        return "Card permissions.fs has unsupported entries: " + ", ".join(unsupported)
    if permissions.get("shell") is not True:
        return "command execution requires Card permissions.shell=true"
    return None


def _resolve_invoke_workdir(
    workspace: Path | str,
    card: dict[str, Any],
    cwd: str | None,
) -> tuple[Path | None, str | None]:
    """Resolve an existing work directory inside the workspace or the Card path."""
    workspace_path = Path(workspace).expanduser().resolve()
    card_path = Path(str(card.get("path") or "")).expanduser()
    try:
        card_path = card_path.resolve()
    except OSError:
        card_path = None
    if cwd:
        target = Path(str(cwd)).expanduser()
        if not target.is_absolute():
            target = workspace_path / target
        try:
            target = target.resolve()
        except OSError:
            return None, f"cwd cannot be resolved: {target}"
        allowed = _inside(target, workspace_path) or (
            card_path is not None and _inside(target, card_path)
        )
        if not allowed:
            return None, f"cwd is outside the workspace and capability: {target}"
        if not target.is_dir():
            return None, f"cwd does not exist: {target}"
        return target, None
    target = bus_dir(workspace_path) / "scratch"
    target.mkdir(parents=True, exist_ok=True)
    return target, None


def invoke_capability(
    workspace: Path | str,
    capability_id: str,
    *,
    input_text: str | None = None,
    cwd: str | None = None,
    timeout: int = 60,
    allow_execute: bool = False,
) -> dict[str, Any]:
    if not is_enabled():
        reason = "CapRouter is disabled; run: caprouter state --enable"
        _audit_invoke(workspace, capability_id, "blocked", reason)
        return {
            "schema": INVOKE_SCHEMA,
            "status": "blocked",
            "id": capability_id,
            "reason": reason,
        }
    card = get_capability(workspace, capability_id)
    if not card:
        return {"schema": INVOKE_SCHEMA, "status": "not_found", "id": capability_id}

    invoke = card.get("invoke") or {}
    mode = str(invoke.get("mode") or "none")
    if mode == "prompt":
        content_path = Path(str(invoke.get("content_path") or ""))
        if not content_path.exists():
            return {
                "schema": INVOKE_SCHEMA,
                "status": "missing",
                "id": capability_id,
                "reason": f"content_path not found: {content_path}",
            }
        if content_path.is_dir():
            files = [
                path.relative_to(content_path).as_posix()
                for path in sorted(content_path.rglob("*"))
                if path.is_file()
            ][:50]
            return {
                "schema": INVOKE_SCHEMA,
                "status": "prompt",
                "id": capability_id,
                "mode": "prompt",
                "content_path": str(content_path),
                "files": files,
                "instructions": "Read the plugin/skill files and apply them. The bus does not execute prompt-mode capabilities.",
            }
        content = content_path.read_text(encoding="utf-8", errors="replace")
        return {
            "schema": INVOKE_SCHEMA,
            "status": "prompt",
            "id": capability_id,
            "mode": "prompt",
            "content_path": str(content_path),
            "content": content[:12000],
            "truncated": len(content) > 12000,
            "instructions": "Read the skill content and apply it. The bus does not execute prompt-mode capabilities.",
        }

    if mode != "command":
        return {
            "schema": INVOKE_SCHEMA,
            "status": "unsupported",
            "id": capability_id,
            "reason": f"invoke mode is {mode!r}; only prompt and command are supported",
        }

    config = bus_config(workspace)
    if not (allow_execute and bool(invoke.get("allow")) and bool(config.get("allow_execute"))):
        reason = "command execution requires invoke.allow=true, bus.allow_execute=true and allow_execute=true"
        _audit_invoke(workspace, capability_id, "blocked", reason)
        return {
            "schema": INVOKE_SCHEMA,
            "status": "blocked",
            "id": capability_id,
            "reason": reason,
        }
    command = invoke.get("command")
    if not isinstance(command, list) or not command:
        return {
            "schema": INVOKE_SCHEMA,
            "status": "invalid",
            "id": capability_id,
            "reason": "invoke.command must be a non-empty list (no shell)",
        }
    permission_error = _invoke_permission_error(card)
    if permission_error:
        _audit_invoke(workspace, capability_id, "blocked", permission_error)
        return {
            "schema": INVOKE_SCHEMA,
            "status": "blocked",
            "id": capability_id,
            "reason": permission_error,
        }
    workdir, workdir_error = _resolve_invoke_workdir(workspace, card, cwd)
    if workdir_error:
        _audit_invoke(workspace, capability_id, "blocked", workdir_error)
        return {
            "schema": INVOKE_SCHEMA,
            "status": "blocked",
            "id": capability_id,
            "reason": workdir_error,
        }

    timeout = int(timeout or 60)
    argv = [str(value) for value in command]
    stdin_text = input_text or ""
    if any("{input}" in value for value in argv):
        argv = [value.replace("{input}", stdin_text) for value in argv]
        stdin_text = ""
    env = os.environ.copy()
    extra_env = invoke.get("env")
    if isinstance(extra_env, dict):
        env.update({str(k): str(v) for k, v in extra_env.items()})

    started = time.perf_counter()
    try:
        proc = subprocess.run(
            argv,
            cwd=str(workdir),
            input=stdin_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(1, min(int(timeout), 600)),
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "schema": INVOKE_SCHEMA,
            "status": "timeout",
            "id": capability_id,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            "reason": f"timeout after {timeout}s",
            "stdout": (exc.stdout or "")[:20000] if isinstance(exc.stdout, str) else "",
            "stderr": (exc.stderr or "")[:20000] if isinstance(exc.stderr, str) else "",
        }
    except OSError as exc:
        return {"schema": INVOKE_SCHEMA, "status": "error", "id": capability_id, "reason": str(exc)}

    status = "ok" if proc.returncode == 0 else "failed"
    _audit_invoke(workspace, capability_id, "executed" if status == "ok" else "failed", f"returncode={proc.returncode}")
    return {
        "schema": INVOKE_SCHEMA,
        "status": status,
        "id": capability_id,
        "mode": "command",
        "returncode": proc.returncode,
        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        "cwd": str(workdir),
        "permissions": card.get("permissions") or {},
        "permissions_enforcement": {
            "shell": "enforced",
            "fs": "cwd-scoped",
            "net": "advisory (no local sandbox)",
        },
        "stdout": (proc.stdout or "")[:20000],
        "stderr": (proc.stderr or "")[:20000],
    }


# ---------------------------------------------------------------------------
# deterministic verification


def _han_count(text: str) -> int:
    return len(_HAN_RE.findall(text or ""))


def _count_items(text: str, pattern: str = "list") -> int:
    pattern = str(pattern or "list").lower()
    if pattern == "url":
        return len(_URL_RE.findall(text or ""))
    if pattern == "table":
        return max(0, len(_TABLE_ROW_RE.findall(text or "")) - 2)
    if pattern == "source_ref":
        return len(_SOURCE_REF_RE.findall(text or ""))
    if pattern == "paragraph":
        return len([part for part in re.split(r"\n\s*\n", text or "") if part.strip()])
    if pattern == "heading":
        return len(_HEADING_RE.findall(text or ""))
    if pattern == "numbered":
        return len(_NUMBERED_RE.findall(text or ""))
    if pattern == "bullet":
        return len(_BULLET_RE.findall(text or ""))
    numbered = len(_NUMBERED_RE.findall(text or ""))
    if numbered:
        return numbered
    bullets = len(_BULLET_RE.findall(text or ""))
    if bullets:
        return bullets
    return len([part for part in re.split(r"\n\s*\n", text or "") if part.strip()])


def _check(status: str, check_type: str, expected: Any, actual: Any,
           reason: str | None = None, criterion: str | None = None) -> dict[str, Any]:
    return {
        "type": check_type,
        "status": status,
        "expected": expected,
        "actual": actual,
        "reason": reason,
        "criterion": criterion,
    }


def _run_check(check: dict[str, Any], text: str, workspace: Path | str,
               artifact_path: str | None) -> dict[str, Any]:
    check_type = str(check.get("type") or "").strip()
    criterion = check.get("criterion")

    if check_type == "file_exists":
        exists = bool(artifact_path and Path(artifact_path).exists())
        return _check("pass" if exists else "fail", check_type, True, exists, criterion=criterion)
    if check_type == "non_empty":
        non_empty = bool((text or "").strip())
        return _check("pass" if non_empty else "fail", check_type, True, non_empty, criterion=criterion)
    if check_type == "format":
        fmt = str(check.get("value") or "").lower()
        if fmt == "json":
            try:
                json.loads(text)
                return _check("pass", check_type, "valid JSON", "valid", criterion=criterion)
            except json.JSONDecodeError as exc:
                return _check("fail", check_type, "valid JSON", str(exc), criterion=criterion)
        if fmt in {"markdown_table", "table"}:
            rows = _TABLE_ROW_RE.findall(text or "")
            has_separator = bool(re.search(r"^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$", text or "", re.MULTILINE))
            ok = len(rows) >= 3 and has_separator
            return _check("pass" if ok else "fail", check_type, "Markdown table", {"rows": len(rows), "separator": has_separator}, criterion=criterion)
        if fmt == "markdown":
            ok = bool(_HEADING_RE.search(text or ""))
            return _check("pass" if ok else "fail", check_type, "Markdown heading", ok, criterion=criterion)
        return _check("unverifiable", check_type, fmt, "unknown format", criterion=criterion)
    if check_type == "min_chars":
        value = int(check.get("value") or 0)
        unit = str(check.get("unit") or "chars")
        actual = _han_count(text) if unit == "han" else len(text or "")
        return _check("pass" if actual >= value else "fail", check_type, f">= {value} {unit}", actual, criterion=criterion)
    if check_type == "max_chars":
        value = int(check.get("value") or 0)
        unit = str(check.get("unit") or "chars")
        actual = _han_count(text) if unit == "han" else len(text or "")
        return _check("pass" if actual <= value else "fail", check_type, f"<= {value} {unit}", actual, criterion=criterion)
    if check_type == "char_range":
        low = int(check.get("min") or 0)
        high = int(check.get("max") or 0)
        unit = str(check.get("unit") or "chars")
        actual = _han_count(text) if unit == "han" else len(text or "")
        ok = low <= actual <= high
        return _check("pass" if ok else "fail", check_type, f"{low}..{high} {unit}", actual, criterion=criterion)
    if check_type in {"min_items", "max_items", "exact_items"}:
        value = int(check.get("value") or 0)
        pattern = str(check.get("pattern") or "list")
        actual = _count_items(text, pattern)
        if check_type == "min_items":
            ok = actual >= value
            expected = f">= {value} {pattern}"
        elif check_type == "max_items":
            ok = actual <= value
            expected = f"<= {value} {pattern}"
        else:
            ok = actual == value
            expected = f"== {value} {pattern}"
        return _check("pass" if ok else "fail", check_type, expected, actual, criterion=criterion)
    if check_type == "contains":
        value = str(check.get("value") or "")
        ok = value in (text or "")
        return _check("pass" if ok else "fail", check_type, f"contains {value!r}", ok, criterion=criterion)
    if check_type == "not_contains":
        value = str(check.get("value") or "")
        ok = value not in (text or "")
        return _check("pass" if ok else "fail", check_type, f"not contains {value!r}", ok, criterion=criterion)
    if check_type == "regex":
        value = str(check.get("value") or "")
        try:
            ok = re.search(value, text or "") is not None
        except re.error as exc:
            return _check("unverifiable", check_type, value, str(exc), criterion=criterion)
        return _check("pass" if ok else "fail", check_type, f"regex {value!r}", ok, criterion=criterion)
    if check_type == "json_required":
        required = check.get("value") or []
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            return _check("fail", check_type, required, str(exc), criterion=criterion)
        if not isinstance(data, dict):
            return _check("fail", check_type, required, type(data).__name__, criterion=criterion)
        missing = [key for key in required if key not in data]
        return _check("pass" if not missing else "fail", check_type, required, {"missing": missing}, criterion=criterion)
    if check_type == "claim_ledger":
        ledger = check.get("path")
        if not ledger:
            return _check("unverifiable", check_type, "claim ledger path", None, "path is required", criterion=criterion)
        ledger_path = Path(str(ledger))
        if not ledger_path.is_absolute():
            ledger_path = Path(workspace) / ledger_path
        if not ledger_path.exists():
            return _check("fail", check_type, "claim ledger exists", str(ledger_path), "not found", criterion=criterion)
        try:
            result = verify_claims(ledger_path)
        except (OSError, ValueError) as exc:
            return _check("unverifiable", check_type, "claim ledger", str(exc), criterion=criterion)
        counts = result.get("counts") or {}
        bad = {key: value for key, value in counts.items() if key != "supported" and value}
        status = "pass" if not bad else "fail"
        return _check(status, check_type, "no unsupported/missing claims", counts, reason=json.dumps(bad, ensure_ascii=False) if bad else None, criterion=criterion)
    if check_type == "sha256":
        expected = str(check.get("value") or "")
        actual = sha256_text(text or "")
        return _check("pass" if actual == expected else "fail", check_type, expected, actual, criterion=criterion)

    return _check("unverifiable", check_type or "unknown", "known check type", check_type, "unknown check type", criterion=criterion)


def _parse_criterion(criterion: str) -> dict[str, Any]:
    text = criterion.strip()
    if "Markdown" in text and "表格" in text:
        return {"type": "format", "value": "markdown_table", "criterion": criterion}
    if "JSON" in text.upper():
        return {"type": "format", "value": "json", "criterion": criterion}

    match = _RANGE_HAN_RE.search(text)
    if match:
        return {"type": "char_range", "min": int(match.group(1)), "max": int(match.group(2)),
                "unit": "han", "criterion": criterion}
    match = _PLUS_MINUS_RE.search(text)
    if match:
        center, percent = int(match.group(1)), int(match.group(2))
        return {"type": "char_range", "min": int(center * (100 - percent) / 100),
                "max": int(center * (100 + percent) / 100),
                "unit": "han" if "汉字" in text else "chars", "criterion": criterion}
    match = _RANGE_CHARS_RE.search(text)
    if match:
        return {"type": "char_range", "min": int(match.group(1)), "max": int(match.group(2)),
                "unit": "han" if "汉字" in text else "chars", "criterion": criterion}
    match = _AT_LEAST_RE.search(text)
    if match:
        pattern = "url" if any(word in text for word in ("来源", "URL", "url", "链接")) else "list"
        return {"type": "min_items", "value": int(match.group(1)), "pattern": pattern, "criterion": criterion}
    match = _EXACT_RE.search(text)
    if match:
        return {"type": "exact_items", "value": int(match.group(1)), "pattern": "list", "criterion": criterion}
    if "来源编号" in text or "引用" in text:
        return {"type": "unverifiable", "reason": "needs a claim ledger to check citations", "criterion": criterion}
    if "数字必须与文件一致" in text or "计数准确" in text or "名称准确" in text:
        return {"type": "unverifiable", "reason": "needs the source file or a structured check", "criterion": criterion}
    if "没有编造" in text or "无编造" in text or "未经验证" in text:
        return {"type": "unverifiable", "reason": "needs source access to prove absence of fabrication", "criterion": criterion}
    return {"type": "unverifiable", "reason": "criterion is not machine-checkable", "criterion": criterion}


def verify_artifact(
    workspace: Path | str,
    *,
    artifact_path: str | None = None,
    text: str | None = None,
    acceptance: Iterable[Any] | None = None,
    evidence: Iterable[str] | None = None,
) -> dict[str, Any]:
    resolved_path: str | None = None
    if text is not None:
        content = str(text)
    elif artifact_path:
        path = Path(artifact_path)
        if not path.is_absolute():
            path = Path(workspace) / path
        resolved_path = str(path)
        if not path.exists():
            return {
                "schema": VERIFY_SCHEMA,
                "verdict": "fail",
                "ok": False,
                "artifact": resolved_path,
                "checks": [_check("fail", "file_exists", True, False, "artifact not found")],
                "checked_at": now_iso(),
            }
        content = path.read_text(encoding="utf-8", errors="replace")
    else:
        return {
            "schema": VERIFY_SCHEMA,
            "verdict": "unverifiable",
            "ok": False,
            "artifact": None,
            "checks": [_check("unverifiable", "input", "artifact_path or text", None, "no artifact supplied")],
            "checked_at": now_iso(),
        }

    checks: list[dict[str, Any]] = []
    items = list(acceptance or [])
    if not items:
        checks.append(_check("pass" if content.strip() else "fail", "non_empty", True, bool(content.strip())))
    for item in items:
        if isinstance(item, dict):
            if item.get("type") == "unverifiable":
                checks.append(_check("unverifiable", "criterion", "machine-checkable", item.get("reason"), item.get("reason"), criterion=item.get("criterion")))
            else:
                checks.append(_run_check(item, content, workspace, resolved_path))
        else:
            check = _parse_criterion(str(item))
            if check.get("type") == "unverifiable":
                checks.append(_check("unverifiable", "criterion", "machine-checkable", check.get("reason"), check.get("reason"), criterion=str(item)))
            else:
                checks.append(_run_check(check, content, workspace, resolved_path))

    if any(check["status"] == "fail" for check in checks):
        verdict = "fail"
    elif any(check["status"] == "unverifiable" for check in checks):
        verdict = "unverifiable"
    else:
        verdict = "pass"
    return {
        "schema": VERIFY_SCHEMA,
        "verdict": verdict,
        "ok": verdict == "pass",
        "artifact": resolved_path,
        "checks": checks,
        "evidence": list(evidence or []),
        "checked_at": now_iso(),
    }


# ---------------------------------------------------------------------------
# outcomes and gaps


def record_outcome(
    workspace: Path | str,
    capability_id: str,
    *,
    task: str = "",
    success: bool | None = None,
    duration_ms: float | None = None,
    human_edit_minutes: float | None = None,
    rework: int = 0,
    notes: str = "",
) -> dict[str, Any]:
    row = {
        "schema": OUTCOME_SCHEMA,
        "ts": now_iso(),
        "capability_id": capability_id,
        "task": task,
        "success": success,
        "duration_ms": duration_ms,
        "human_edit_minutes": human_edit_minutes,
        "rework": rework,
        "notes": notes,
    }
    append_jsonl(outcomes_path(workspace), row)
    return row


def record_gap(
    workspace: Path | str,
    *,
    need: str,
    reason: str = "",
    tried: Iterable[str] | None = None,
    workaround: str = "",
) -> dict[str, Any]:
    row = {
        "schema": "caprouter.capability-gap.v1",
        "ts": now_iso(),
        "need": need,
        "reason": reason,
        "tried": list(tried or []),
        "workaround": workaround,
    }
    append_jsonl(gaps_path(workspace), row)
    return row


def list_gaps(workspace: Path | str, limit: int = 50) -> dict[str, Any]:
    rows = read_jsonl(gaps_path(workspace))
    return {
        "schema": "caprouter.capability-gaps.v1",
        "count": len(rows),
        "gaps": rows[-max(1, int(limit)):],
    }
