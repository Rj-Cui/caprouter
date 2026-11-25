"""Deterministic Capability Card review and risk-graded auto-apply.

The reviewer reads drafts, the registry and bounded project evidence. It never
executes project code and never calls a model. A semantic second pass can be
injected by the caller (see ``semantic.py``) but can only upgrade medium risk
cards, never high risk ones.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from .capabilities import load_entries, merge_entry_overlay
from .cards import (
    BOILERPLATE_RE,
    COMMAND_LIKE_RE,
    FEATURE_BULLET_RE,
    SETUP_LINE_RE,
    apply_draft,
    drafts_dir,
)
from .evidence import collect_evidence, summarize_evidence
from .route import route
from .scan import CAPABILITY_KEYWORDS
from .util import append_jsonl, now_iso, read_json, write_json_atomic
from .workspace import DEFAULT_REVIEW_CONFIG

REVIEW_SCHEMA = "caprouter.capability-review.v1"
REVIEW_BATCH_SCHEMA = "caprouter.capability-review-batch.v1"
REVIEWS_DIR = "reviews"


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def review_config(workspace: Path | str) -> dict[str, Any]:
    config = read_json(Path(workspace) / ".caprouter" / "config.json", {})
    config = config if isinstance(config, dict) else {}
    block = config.get("review") if isinstance(config.get("review"), dict) else {}
    return _deep_merge(
        {
            **DEFAULT_REVIEW_CONFIG,
            "retrieval": dict(DEFAULT_REVIEW_CONFIG["retrieval"]),
            "semantic": dict(DEFAULT_REVIEW_CONFIG["semantic"]),
        },
        block,
    )


def _check(checks: list[dict[str, Any]], check_id: str, status: str, message: str = "") -> None:
    checks.append({"id": check_id, "status": status, "message": message})


def _norm(path: Any) -> str:
    try:
        return os.path.normcase(os.path.abspath(str(path)))
    except (OSError, ValueError):
        return os.path.normcase(str(path))


def _example_issues(examples: list[Any]) -> tuple[list[str], list[str]]:
    good: list[str] = []
    issues: list[str] = []
    seen: set[str] = set()
    for value in examples:
        text = str(value).strip()
        if not (8 <= len(text) <= 200):
            issues.append(f"example length outside 8-200: {text[:60]}")
            continue
        if SETUP_LINE_RE.match(text) or COMMAND_LIKE_RE.match(text) or BOILERPLATE_RE.match(text):
            issues.append(f"example looks like setup/command/boilerplate: {text[:60]}")
            continue
        if FEATURE_BULLET_RE.match(text) or "**" in text:
            issues.append(f"example looks like a feature bullet: {text[:60]}")
            continue
        key = text.lower()
        if key in seen:
            issues.append(f"duplicate example: {text[:60]}")
            continue
        seen.add(key)
        good.append(text)
    return good, issues


def _retrieval_replay(
    entries: list[dict[str, Any]],
    draft: dict[str, Any],
    *,
    top_k: int,
) -> dict[str, Any]:
    capability_id = str(draft.get("id") or "")
    enriched = [
        merge_entry_overlay(entry, draft) if str(entry.get("id")) == capability_id else entry
        for entry in entries
    ]
    examples = [str(item) for item in (draft.get("examples") or []) if str(item).strip()]
    queries = examples[:3]
    name = str(draft.get("name") or "")
    description = str(draft.get("description") or "")
    if name or description:
        queries.append(f"{name} {description}".strip())
    rows: list[dict[str, Any]] = []
    top1 = 0
    top3 = 0
    for query in queries:
        result = route(enriched, query, top_k=max(1, top_k))
        ids = [str(item.get("id")) for item in result.get("results") or []]
        if capability_id in ids:
            rank = ids.index(capability_id) + 1
        else:
            rank = None
        top1 += 1 if rank == 1 else 0
        top3 += 1 if rank and rank <= top_k else 0
        rows.append({"query": query[:160], "rank": rank})
    total = len(queries)
    return {
        "queries": rows,
        "count": total,
        "top1_rate": round(top1 / total, 3) if total else 0.0,
        "top3_rate": round(top3 / total, 3) if total else 0.0,
    }


def review_draft(
    workspace: Path | str,
    draft: dict[str, Any],
    *,
    entries: list[dict[str, Any]] | None = None,
    evidence: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    semantic: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Review one draft and return a report. Does not write files."""
    workspace = Path(workspace)
    config = config or review_config(workspace)
    entries = entries if entries is not None else load_entries(workspace)
    old_review = draft.get("review") if isinstance(draft.get("review"), dict) else {}
    carried_semantic = None
    previous_semantic_unresolved = False
    if semantic is None and isinstance(old_review.get("semantic"), dict):
        carried_semantic = old_review["semantic"]
        previous_verdict = str(carried_semantic.get("verdict") or "uncertain")
        try:
            previous_confidence = float(carried_semantic.get("confidence") or 0.0)
        except (TypeError, ValueError):
            previous_confidence = 0.0
        semantic_floor = float(
            (config.get("semantic") or {}).get("min_confidence") or 0.75
        ) if isinstance(config.get("semantic"), dict) else 0.75
        previous_semantic_unresolved = (
            previous_verdict != "pass" or previous_confidence < semantic_floor
        )
    checks: list[dict[str, Any]] = []
    capability_id = str(draft.get("id") or "").strip()
    path = Path(str(draft.get("path") or ""))
    entry = next((item for item in entries if str(item.get("id")) == capability_id), None)

    if not capability_id:
        _check(checks, "identity.id", "fail", "draft has no id")
    elif entry is None:
        _check(checks, "identity.id", "fail", "id is not in the registry; run scan/bootstrap first")
    elif _norm(entry.get("path")) != _norm(path):
        _check(checks, "identity.path", "fail", "registry path differs from draft path")
    else:
        _check(checks, "identity", "pass", "")

    if not path.is_dir():
        _check(checks, "path.exists", "fail", f"path is missing: {path}")
    else:
        _check(checks, "path.exists", "pass", "")

    description = str(draft.get("description") or "").strip()
    if len(description) < 20:
        _check(checks, "description.length", "fail", "description is shorter than 20 chars")
    elif description.lower().startswith("local project ") or description.startswith(("{", '"')):
        _check(checks, "description.meaningful", "fail", "description is a placeholder or raw JSON")
    elif "�" in description:
        _check(checks, "description.encoding", "fail", "description contains replacement characters")
    elif len(description) > 500:
        _check(checks, "description.length", "warn", "description is longer than 500 chars")
    else:
        _check(checks, "description", "pass", "")

    examples = [str(item) for item in (draft.get("examples") or [])]
    usable, example_issues = _example_issues(examples)
    min_examples = int(config.get("min_examples") or 3)
    if not usable:
        _check(checks, "examples", "fail", "no usable examples")
    elif len(usable) < min_examples:
        _check(checks, "examples.count", "warn", f"{len(usable)} usable examples; review target is {min_examples}")
    else:
        _check(checks, "examples", "pass", "")
    for issue in example_issues[:5]:
        _check(checks, "examples.quality", "warn", issue)

    capabilities = [str(item) for item in (draft.get("capabilities") or [])]
    min_capabilities = int(config.get("min_capabilities") or 1)
    known = set(CAPABILITY_KEYWORDS)
    unknown = [item for item in capabilities if item not in known]
    if len(capabilities) < min_capabilities:
        _check(checks, "capabilities", "fail", "no capability tags")
    else:
        _check(checks, "capabilities", "pass", "")
    if unknown:
        _check(checks, "capabilities.known", "warn", "unknown tags: " + ", ".join(unknown[:8]))

    evidence = evidence if evidence is not None else collect_evidence(path, str(draft.get("type") or "project"))
    summary = summarize_evidence(evidence)
    signals = evidence.get("signals") if isinstance(evidence.get("signals"), dict) else {}
    hints = set(evidence.get("capability_hints") or [])
    if capabilities and hints:
        overlap = hints & set(capabilities)
        if overlap:
            _check(checks, "evidence.capabilities", "pass", "supported by evidence: " + ", ".join(sorted(overlap)))
        else:
            _check(
                checks,
                "evidence.capabilities",
                "warn",
                "card tags do not overlap evidence hints: " + ", ".join(sorted(hints)[:8]),
            )
    elif not evidence.get("artifacts"):
        _check(checks, "evidence", "warn", "no manifest or README evidence found")

    permissions = draft.get("permissions") if isinstance(draft.get("permissions"), dict) else {}
    fs = permissions.get("fs") if isinstance(permissions.get("fs"), list) else []
    net = bool(permissions.get("net"))
    shell = bool(permissions.get("shell"))
    invoke = draft.get("invoke") if isinstance(draft.get("invoke"), dict) else {}
    invoke_mode = str(invoke.get("mode") or "none")
    allow = bool(invoke.get("allow"))

    permission_mismatch = False
    if shell and not signals.get("has_shell"):
        _check(checks, "permissions.shell", "warn", "shell=true but evidence has no scripts/commands")
        permission_mismatch = True
    if net and not signals.get("has_network"):
        _check(checks, "permissions.net", "warn", "net=true but evidence has no network hint")
        permission_mismatch = True
    if "write" in fs and not signals.get("has_write"):
        _check(checks, "permissions.write", "warn", "fs includes write but evidence has no write hint")
        permission_mismatch = True
    invoke_content_missing = False
    if invoke_mode == "prompt":
        content = Path(str(invoke.get("content_path") or ""))
        if content.exists():
            _check(checks, "invoke.content", "pass", "")
        else:
            _check(checks, "invoke.content", "fail", f"prompt content_path is missing: {content}")
            invoke_content_missing = True
    if invoke_mode == "command" or allow:
        _check(checks, "invoke.command", "fail", "command invoke/allow requires human review")
        shell = True

    # Risk here means "safe to write this Card into the overlay", not "the
    # capability never touches files or the network". Applying a Card only
    # writes metadata; command/allow is the only execution path. Declared
    # permissions supported by evidence therefore stay low risk, while
    # unsupported permission claims remain medium.
    risk = "low"
    if invoke_mode == "command" or allow:
        risk = "high"
    elif (
        permission_mismatch
        or invoke_content_missing
        or len(usable) < min_examples
        or len(capabilities) < min_capabilities
        or unknown
    ):
        risk = "medium"
    if any(item["status"] == "fail" for item in checks if item["id"].startswith(("identity", "path."))):
        risk = "high"

    retrieval: dict[str, Any] = {"enabled": False}
    retrieval_cfg = config.get("retrieval") if isinstance(config.get("retrieval"), dict) else {}
    if bool(retrieval_cfg.get("enabled", True)) and entry is not None and path.is_dir():
        top_k = max(1, int(retrieval_cfg.get("top_k") or 3))
        retrieval = _retrieval_replay(entries, draft, top_k=top_k)
        retrieval["enabled"] = True
        min_top1 = float(retrieval_cfg.get("min_top1_rate") or 0.3)
        min_top3 = float(retrieval_cfg.get("min_top3_rate") or 0.6)
        if retrieval["top3_rate"] < min_top3 or retrieval["top1_rate"] < min_top1:
            _check(
                checks,
                "retrieval",
                "warn",
                f"replay top1={retrieval['top1_rate']} top{top_k}={retrieval['top3_rate']}",
            )
            if risk == "low":
                risk = "medium"
        else:
            _check(checks, "retrieval", "pass", "")

    failures = [item for item in checks if item["status"] == "fail"]
    warnings = [item for item in checks if item["status"] == "warn"]
    score = round(max(0.0, 1.0 - 0.2 * len(failures) - 0.03 * len(warnings)), 3)
    rejected = any(
        item["id"] in {"identity.id", "identity.path", "path.exists", "invoke.command"}
        for item in failures
    )
    status = "rejected" if rejected else "needs_review"

    semantic_cfg = config.get("semantic") if isinstance(config.get("semantic"), dict) else {}
    if semantic:
        verdict = str(semantic.get("verdict") or "uncertain")
        confidence = float(semantic.get("confidence") or 0.0)
        issues = semantic.get("issues") if isinstance(semantic.get("issues"), list) else []
        if any(isinstance(item, dict) and str(item.get("severity")) == "high" for item in issues):
            risk = "high"
        if verdict == "fail":
            _check(checks, "semantic", "fail", "semantic reviewer rejected the card")
            status = "needs_review"
        elif verdict == "pass" and confidence >= float(semantic_cfg.get("min_confidence") or 0.75):
            _check(checks, "semantic", "pass", f"confidence={confidence}")
            if not rejected and risk == "low":
                status = "auto_approved"
            elif not rejected and risk == "medium" and bool(config.get("allow_medium")):
                status = "auto_approved"
        else:
            _check(checks, "semantic", "warn", f"semantic verdict={verdict} confidence={confidence}")
            status = "needs_review"

    if previous_semantic_unresolved:
        _check(
            checks,
            "semantic.previous",
            "warn",
            "a previous semantic review did not pass; rerun review with --semantic",
        )

    if not semantic and not rejected:
        auto_ok = (
            risk == "low"
            and not failures
            and not previous_semantic_unresolved
            and len(usable) >= min_examples
            and len(capabilities) >= min_capabilities
            and retrieval.get("top3_rate", 1.0) >= float(retrieval_cfg.get("min_top3_rate") or 0.6)
            and retrieval.get("top1_rate", 1.0) >= float(retrieval_cfg.get("min_top1_rate") or 0.3)
            and score >= 0.8
        )
        if auto_ok:
            status = "auto_approved"

    report = {
        "schema": REVIEW_SCHEMA,
        "id": capability_id,
        "status": status,
        "risk": risk,
        "score": score,
        "checks": checks,
        "retrieval": retrieval,
        "evidence": summary,
        "semantic": semantic if semantic is not None else carried_semantic,
        "reviewed_at": now_iso(),
        "reviewer": "semantic" if semantic else "deterministic",
        "notes": old_review.get("notes") or [],
    }
    return report


def _draft_files(workspace: Path) -> list[Path]:
    """Collect every reviewable draft.

    ``install`` writes drafts directly under ``.caprouter/drafts/``; project
    drafts live under ``drafts/projects/``. Reviewing only the latter silently
    hid installed capabilities from ``review``.
    """
    base = drafts_dir(workspace)
    files: list[Path] = []
    if base.is_dir():
        files.extend(path for path in base.glob("*.json") if path.is_file())
        projects = base / "projects"
        if projects.is_dir():
            files.extend(path for path in projects.glob("*.json") if path.is_file())
    return sorted(files)


def review_drafts(
    workspace: Path | str,
    *,
    ids: list[str] | None = None,
    semantic: bool = False,
    semantic_adapter: str | None = None,
    auto_apply: bool | None = None,
    dry_run: bool = False,
    force: bool = False,
    limit: int | None = None,
    repair: bool = False,
    semantic_runner: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Review all project drafts; optionally run semantic review and auto-apply.

    ``repair=True`` asks the semantic reviewer for a bounded rewrite of
    description/examples/capabilities/keywords, validates it, re-reviews the
    repaired draft, and only then applies it. A repair never changes
    permissions or invoke mode.
    """
    workspace = Path(workspace)
    config = review_config(workspace)
    if semantic_adapter:
        semantic_cfg = config.get("semantic") if isinstance(config.get("semantic"), dict) else {}
        config["semantic"] = {**semantic_cfg, "adapter": semantic_adapter}
    semantic_cfg = config.get("semantic") if isinstance(config.get("semantic"), dict) else {}
    if repair or force:
        semantic_cfg = dict(semantic_cfg)
        if repair:
            semantic_cfg["repair"] = True
        if force:
            semantic_cfg["force"] = True
        config["semantic"] = semantic_cfg
    semantic = semantic or bool(semantic_cfg.get("enabled"))
    entries = load_entries(workspace)
    target_ids = {str(item) for item in (ids or [])}
    applied_rows: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    applied = 0
    skipped = 0
    repaired = 0
    semantic_calls = 0
    semantic_skipped_high = 0
    apply_risks = {"low", "medium"} if bool(config.get("allow_medium")) else {"low"}

    runner = semantic_runner
    if semantic and runner is None:
        from .semantic import run_semantic_review

        runner = run_semantic_review

    def _run_semantic(
        draft: dict[str, Any],
        evidence: dict[str, Any],
        deterministic: dict[str, Any],
    ) -> dict[str, Any]:
        nonlocal semantic_calls
        semantic_calls += 1
        try:
            return runner(workspace, draft, evidence, deterministic, config)
        except Exception as exc:  # semantic review must never break the batch
            return {
                "verdict": "uncertain",
                "confidence": 0.0,
                "issues": [],
                "suggested_fixes": [],
                "error": str(exc),
            }

    for path in _draft_files(workspace):
        if limit is not None and len(items) >= limit:
            break
        draft = read_json(path, {})
        if not isinstance(draft, dict):
            skipped += 1
            continue
        capability_id = str(draft.get("id") or "")
        if target_ids and capability_id not in target_ids:
            continue
        old_review = draft.get("review") if isinstance(draft.get("review"), dict) else {}
        if str(old_review.get("status") or "").startswith("migrated"):
            skipped += 1
            continue
        if not force and old_review.get("status") == "auto_approved" and old_review.get("applied"):
            skipped += 1
            continue
        evidence = collect_evidence(
            draft.get("path") or workspace,
            str(draft.get("type") or "project"),
        )
        report = review_draft(workspace, draft, entries=entries, evidence=evidence, config=config)
        repair_info: dict[str, Any] | None = None
        if semantic and str(report["status"]) != "auto_approved":
            if str(report.get("risk")) == "high":
                # Semantic review cannot upgrade high risk; skip the cost.
                semantic_skipped_high += 1
            else:
                semantic_result = _run_semantic(draft, evidence, report)
                if repair and isinstance(semantic_result.get("repaired"), dict):
                    from .repair import apply_repair, validate_repaired

                    accepted, repair_notes = validate_repaired(
                        semantic_result["repaired"],
                        draft,
                        evidence_hints=evidence.get("capability_hints") or [],
                    )
                    repaired_draft, changes = apply_repair(draft, accepted)
                    if changes:
                        repaired_report = review_draft(
                            workspace,
                            repaired_draft,
                            entries=entries,
                            evidence=evidence,
                            config=config,
                        )
                        # The first verdict was about the old Card; review the
                        # repaired Card again before it can be auto-applied.
                        repaired_semantic = _run_semantic(repaired_draft, evidence, repaired_report)
                        draft = repaired_draft
                        report = review_draft(
                            workspace,
                            draft,
                            entries=entries,
                            evidence=evidence,
                            config=config,
                            semantic=repaired_semantic,
                        )
                        semantic_result = repaired_semantic
                        repaired += 1
                        repair_info = {
                            "fields": sorted(changes),
                            "notes": repair_notes,
                            "semantic_retry": True,
                        }
                    else:
                        repair_info = {
                            "fields": [],
                            "notes": repair_notes or ["semantic reviewer proposed no valid changes"],
                        }
                report = review_draft(
                    workspace,
                    draft,
                    entries=entries,
                    evidence=evidence,
                    config=config,
                    semantic=semantic_result,
                )
                if repair_info is not None:
                    report["repair"] = repair_info
        draft["review"] = report
        if not dry_run:
            write_json_atomic(path, draft)

        should_apply = (
            auto_apply
            if auto_apply is not None
            else bool(config.get("auto_apply"))
        )
        semantic_verdict = (
            str((report.get("semantic") or {}).get("verdict") or "")
            if isinstance(report.get("semantic"), dict)
            else ""
        )
        row = {
            "id": capability_id,
            "status": report["status"],
            "risk": report["risk"],
            "score": report["score"],
            "draft_path": str(path),
            "issues": [item for item in report["checks"] if item["status"] in {"warn", "fail"}][:8],
        }
        if repair_info is not None:
            row["repair"] = repair_info
        apply_allowed = (
            should_apply
            and not dry_run
            and report["status"] == "auto_approved"
            and report["risk"] in apply_risks
            and (report["risk"] == "low" or semantic_verdict == "pass")
        )
        if apply_allowed:
            try:
                result = apply_draft(workspace, draft)
                if result.get("status") != "applied":
                    # The Card id is not in the registry; do not claim success.
                    row["applied"] = False
                    row["apply_error"] = result.get("reason") or result.get("status")
                else:
                    report["applied"] = True
                    report["applied_at"] = now_iso()
                    draft["review"] = report
                    write_json_atomic(path, draft)
                    row["applied"] = True
                    row["applied_fields"] = result.get("updated") or []
                    applied += 1
                    applied_rows.append(
                        {
                            "ts": now_iso(),
                            "id": capability_id,
                            "draft_path": str(path),
                            "updated": result.get("updated") or [],
                        }
                    )
            except Exception as exc:
                row["applied"] = False
                row["apply_error"] = str(exc)
        elif should_apply and not dry_run and report["status"] == "auto_approved":
            row["apply_blocked_reason"] = (
                "medium risk requires allow_medium and a semantic pass"
                if report["risk"] == "medium"
                else "risk is not eligible for auto-apply"
            )
        items.append(row)

    if applied_rows and not dry_run:
        log_path = workspace / ".caprouter" / REVIEWS_DIR / "applied.jsonl"
        for row in applied_rows:
            append_jsonl(log_path, row)

    counts: dict[str, int] = {}
    risks: dict[str, int] = {}
    for item in items:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
        risks[item["risk"]] = risks.get(item["risk"], 0) + 1
    report = {
        "schema": REVIEW_BATCH_SCHEMA,
        "generated_at": now_iso(),
        "total": len(items),
        "counts": counts,
        "by_risk": risks,
        "applied": applied,
        "skipped": skipped,
        "repaired": repaired,
        "semantic_calls": semantic_calls,
        "semantic_skipped_high": semantic_skipped_high,
        "dry_run": bool(dry_run),
        "items": items,
    }
    if not dry_run:
        out = workspace / ".caprouter" / REVIEWS_DIR / "latest.json"
        write_json_atomic(out, report)
        report["report_path"] = str(out)
    return report
