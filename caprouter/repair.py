"""Validated Card repairs suggested by the semantic reviewer.

The semantic layer is allowed to propose a rewrite of ``description``,
``examples``, ``capabilities`` and ``keywords``. This module is the gate: it
only accepts bounded, well-formed values, keeps capability tags supported by
the draft or evidence, and records the before/after values in provenance.
It never writes files and never executes project code.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from .cards import BOILERPLATE_RE, COMMAND_LIKE_RE, SETUP_LINE_RE
from .scan import CAPABILITY_KEYWORDS
from .util import now_iso

MAX_EXAMPLES = 5
MAX_CAPABILITIES = 8
MAX_KEYWORDS = 12
MAX_HISTORY = 5


def _clean_text(value: Any, limit: int = 500) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit].strip()


def _valid_example(value: Any) -> str | None:
    text = _clean_text(value, 200)
    if not (8 <= len(text) <= 200):
        return None
    if "�" in text:
        return None
    if SETUP_LINE_RE.match(text) or COMMAND_LIKE_RE.match(text) or BOILERPLATE_RE.match(text):
        return None
    return text


def validate_repaired(
    raw: Any,
    draft: dict[str, Any],
    *,
    evidence_hints: Iterable[str] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Return accepted fields and validation notes.

    Invalid fields are dropped instead of leaking a model rewrite into routing.
    """
    notes: list[str] = []
    if not isinstance(raw, dict):
        return {}, ["repaired payload is not an object"]
    accepted: dict[str, Any] = {}

    description = _clean_text(raw.get("description"))
    if description:
        if not (20 <= len(description) <= 500):
            notes.append("repaired description length outside 20-500")
        elif description.lower().startswith("local project ") or description.startswith(("{", '"')):
            notes.append("repaired description is a placeholder or raw JSON")
        elif "�" in description:
            notes.append("repaired description contains replacement characters")
        else:
            accepted["description"] = description

    raw_examples = raw.get("examples")
    if isinstance(raw_examples, list):
        examples: list[str] = []
        seen: set[str] = set()
        for item in raw_examples:
            text = _valid_example(item)
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            examples.append(text)
            if len(examples) >= MAX_EXAMPLES:
                break
        if examples:
            accepted["examples"] = examples
        else:
            notes.append("no usable repaired examples")
    elif raw_examples is not None:
        notes.append("repaired examples is not a list")

    raw_capabilities = raw.get("capabilities")
    if isinstance(raw_capabilities, list):
        supported = {str(item) for item in (draft.get("capabilities") or [])}
        supported.update(str(item) for item in (evidence_hints or []))
        allowed = set(CAPABILITY_KEYWORDS)
        capabilities: list[str] = []
        for item in raw_capabilities:
            value = str(item).strip()
            if value in allowed and value in supported and value not in capabilities:
                capabilities.append(value)
            elif value:
                notes.append(f"dropped unsupported capability tag: {value[:40]}")
            if len(capabilities) >= MAX_CAPABILITIES:
                break
        if capabilities:
            accepted["capabilities"] = sorted(capabilities)
    elif raw_capabilities is not None:
        notes.append("repaired capabilities is not a list")

    raw_keywords = raw.get("keywords")
    if isinstance(raw_keywords, list):
        keywords: list[str] = []
        for item in raw_keywords:
            value = _clean_text(item, 64)
            if value and value not in keywords:
                keywords.append(value)
            if len(keywords) >= MAX_KEYWORDS:
                break
        if keywords:
            accepted["keywords"] = keywords
    elif raw_keywords is not None:
        notes.append("repaired keywords is not a list")

    return accepted, notes


def apply_repair(draft: dict[str, Any], accepted: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Merge accepted fields into a copy of ``draft`` and record provenance."""
    updated = dict(draft)
    changes: dict[str, Any] = {}
    before: dict[str, Any] = {}
    for key in ("description", "examples", "capabilities", "keywords"):
        if key not in accepted:
            continue
        if accepted[key] == draft.get(key):
            continue
        changes[key] = accepted[key]
        old = draft.get(key)
        before[key] = old[:500] if isinstance(old, str) else old
    if not changes:
        return updated, {}
    updated.update(changes)
    provenance = draft.get("provenance") if isinstance(draft.get("provenance"), dict) else {}
    history = provenance.get("repairs") if isinstance(provenance.get("repairs"), list) else []
    history = list(history[-MAX_HISTORY:])
    history.append(
        {
            "at": now_iso(),
            "source": "semantic",
            "fields": sorted(changes),
            "before": before,
        }
    )
    updated["provenance"] = {**provenance, "repairs": history}
    return updated, changes
