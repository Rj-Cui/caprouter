from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .route import route
from .util import now_iso, read_json

DEFAULT_CASES = Path(__file__).resolve().parent.parent / "eval" / "routing_cases.json"


def load_cases(path: Path | str | None = None) -> list[dict[str, Any]]:
    data = read_json(Path(path or DEFAULT_CASES), [])
    if isinstance(data, dict):
        data = data.get("cases") or []
    return [item for item in data if isinstance(item, dict)]


def _entry_chars(entry: dict[str, Any]) -> int:
    parts = [
        str(entry.get("name") or ""),
        str(entry.get("description") or ""),
        " ".join(str(item) for item in (entry.get("keywords") or [])),
        " ".join(str(item) for item in (entry.get("capabilities") or [])),
    ]
    return sum(len(part) for part in parts)


def evaluate(
    entries: list[dict[str, Any]],
    cases: list[dict[str, Any]],
    routing_policy: dict[str, Any] | None = None,
    top_k: int = 3,
) -> dict[str, Any]:
    policy = routing_policy if isinstance(routing_policy, dict) else {}
    details: list[dict[str, Any]] = []
    topk_chars_total = 0
    latencies_ms: list[float] = []
    for case in cases:
        started = time.perf_counter()
        capabilities = case.get("capabilities")
        if capabilities is None and case.get("capability"):
            capabilities = [case["capability"]]
        domains = case.get("domains")
        if domains is None and case.get("domain"):
            domains = [case["domain"]]
        result = route(
            entries,
            str(case.get("need") or ""),
            top_k=top_k,
            types=case.get("types"),
            capabilities=capabilities,
            domains=domains,
            min_score=float(policy.get("min_score") or 0.0),
            exclude_path_contains=policy.get("exclude_path_contains") or (),
            exclude_id_prefixes=policy.get("exclude_id_prefixes") or (),
        )
        ids = [str(item.get("id")) for item in (result.get("results") or [])]
        expect_any = {str(item) for item in (case.get("expect_any") or [])}
        expect_none = bool(case.get("expect_none"))
        hit1 = bool(ids) and (ids[0] in expect_any)
        hit3 = bool(set(ids[:top_k]) & expect_any)
        if expect_none:
            hit1 = not ids
            hit3 = not ids
        topk_chars_total += sum(
            _entry_chars(entry)
            for entry in entries
            if str(entry.get("id")) in set(ids)
        )
        latencies_ms.append(round((time.perf_counter() - started) * 1000, 3))
        details.append(
            {
                "id": case.get("id"),
                "need": case.get("need"),
                "expect_any": sorted(expect_any),
                "expect_none": expect_none,
                "got": ids,
                "hit1": hit1,
                "hit_at_k": hit3,
                "latency_ms": latencies_ms[-1],
            }
        )

    total = len(details)
    full_chars = sum(_entry_chars(entry) for entry in entries)
    avg_topk_chars = round(topk_chars_total / total) if total else 0
    reduction = round(1 - (avg_topk_chars / full_chars), 4) if full_chars else 0.0
    ordered = sorted(latencies_ms)
    p95 = ordered[min(len(ordered) - 1, int(0.95 * (len(ordered) - 1)))] if ordered else 0.0
    return {
        "schema": "caprouter.eval.v1",
        "generated_at": now_iso(),
        "cases": total,
        "top_k": top_k,
        "top1": round(sum(1 for item in details if item["hit1"]) / total, 4) if total else 0.0,
        "topk": round(sum(1 for item in details if item["hit_at_k"]) / total, 4) if total else 0.0,
        "context": {
            "full_chars": full_chars,
            "avg_topk_chars": avg_topk_chars,
            "reduction": reduction,
            "rough_tokens_full": round(full_chars / 2),
            "rough_tokens_avg_topk": round(avg_topk_chars / 2),
        },
        "latency_ms": {
            "avg": round(sum(ordered) / len(ordered), 3) if ordered else 0.0,
            "p95": p95,
            "max": max(ordered) if ordered else 0.0,
        },
        "details": details,
    }
