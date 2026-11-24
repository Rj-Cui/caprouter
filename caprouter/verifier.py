from __future__ import annotations

import json
import re
from typing import Any

VERIFIER_SYSTEM = (
    "You are an independent verifier. You do not trust the writer's reasoning. "
    "Check the draft against the acceptance criteria and the provided sources. "
    "Return ONLY JSON: {\"ok\": true|false, \"issues\": [{\"severity\": \"high|medium|low\", "
    "\"problem\": \"...\", \"fix\": \"...\"}], \"claims\": []}."
)

_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def build_verify_prompt(draft: str, acceptance: list[str], sources: str = "") -> str:
    criteria = "\n".join(f"- {item}" for item in acceptance) or "- (no explicit criteria)"
    source_block = sources.strip() or "(no source pack provided)"
    return (
        "Verify the following draft. Do not rewrite it unless an issue needs a fix.\n\n"
        "## Acceptance criteria\n"
        f"{criteria}\n\n"
        "## Source pack\n"
        f"{source_block[:12000]}\n\n"
        "## Draft\n"
        f"{draft[:24000]}\n\n"
        "Return only the JSON verdict described in the system prompt."
    )


def _extract_json(text: str) -> Any:
    match = _FENCE.search(text or "")
    candidates = [match.group(1)] if match else []
    stripped = (text or "").strip()
    if stripped.startswith("{") or stripped.startswith("["):
        candidates.append(stripped)
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end > start:
        candidates.append(stripped[start : end + 1])
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


def parse_verdict(text: str) -> dict[str, Any]:
    data = _extract_json(text)
    if isinstance(data, dict) and isinstance(data.get("ok"), bool):
        issues = data.get("issues") if isinstance(data.get("issues"), list) else []
        claims = data.get("claims") if isinstance(data.get("claims"), list) else []
        return {
            "ok": data["ok"],
            "issues": issues,
            "claims": claims,
            "raw": (text or "")[:2000],
            "parsed": True,
        }
    lowered = (text or "").lower()
    ok = any(token in lowered for token in ("pass", "ok", "supported")) and not any(
        token in lowered for token in ("fail", "not found", "unsupported")
    )
    return {
        "ok": ok,
        "issues": [] if ok else [{"severity": "medium", "problem": (text or "verifier returned no parseable JSON")[:500]}],
        "claims": [],
        "raw": (text or "")[:2000],
        "parsed": False,
    }
