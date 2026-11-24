from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path
from typing import Any

from .util import now_iso, read_json

_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MD_EMPHASIS = re.compile(r"(\*\*|__|\*|_|`)")
_MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)
_MD_LIST = re.compile(r"^\s{0,3}[-*+]\s+", re.MULTILINE)
_WS = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    value = unicodedata.normalize("NFKC", str(text or ""))
    value = _MD_LINK.sub(r"\1", value)
    value = _MD_EMPHASIS.sub("", value)
    value = _MD_HEADING.sub("", value)
    value = _MD_LIST.sub("", value)
    value = _WS.sub(" ", value).strip()
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def verify_claims(ledger_path: Path | str) -> dict[str, Any]:
    ledger_path = Path(ledger_path)
    data = read_json(ledger_path)
    if isinstance(data, dict):
        claims = data.get("claims") or []
    elif isinstance(data, list):
        claims = data
    else:
        claims = []

    base = ledger_path.parent
    results: list[dict[str, Any]] = []
    counts = {"supported": 0, "not_found": 0, "hash_mismatch": 0, "missing_snapshot": 0, "missing_hash": 0, "invalid": 0}

    for index, claim in enumerate(claims):
        if not isinstance(claim, dict):
            counts["invalid"] += 1
            results.append({"claim_id": f"#{index}", "verdict": "invalid", "reason": "claim must be an object"})
            continue
        claim_id = str(claim.get("claim_id") or f"#{index}")
        quote = normalize_text(str(claim.get("quote") or ""))
        snapshot_path = claim.get("snapshot_path")
        snapshot_hash = claim.get("snapshot_hash")
        item: dict[str, Any] = {
            "claim_id": claim_id,
            "source_id": claim.get("source_id"),
            "quote": claim.get("quote"),
            "snapshot_path": snapshot_path,
            "verdict": "invalid",
            "reason": None,
        }

        if not quote:
            counts["invalid"] += 1
            item["reason"] = "quote is empty"
            results.append(item)
            continue
        if not snapshot_path:
            counts["missing_snapshot"] += 1
            item["verdict"] = "missing_snapshot"
            item["reason"] = "snapshot_path is empty"
            results.append(item)
            continue

        path = Path(str(snapshot_path))
        if not path.is_absolute():
            path = base / path
        if not path.exists():
            counts["missing_snapshot"] += 1
            item["verdict"] = "missing_snapshot"
            item["reason"] = f"snapshot not found: {path}"
            results.append(item)
            continue
        if not snapshot_hash:
            counts["missing_hash"] += 1
            item["verdict"] = "missing_hash"
            item["reason"] = "snapshot_hash is empty"
            results.append(item)
            continue

        actual_hash = sha256_file(path)
        if actual_hash != str(snapshot_hash):
            counts["hash_mismatch"] += 1
            item["verdict"] = "hash_mismatch"
            item["reason"] = f"expected {snapshot_hash}, got {actual_hash}"
            results.append(item)
            continue

        try:
            body = normalize_text(path.read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:
            counts["missing_snapshot"] += 1
            item["verdict"] = "missing_snapshot"
            item["reason"] = str(exc)
            results.append(item)
            continue

        if quote in body:
            counts["supported"] += 1
            item["verdict"] = "supported"
        else:
            counts["not_found"] += 1
            item["verdict"] = "not_found"
            item["reason"] = "quote not found in normalized snapshot"
        results.append(item)

    return {
        "schema": "caprouter.claim-ledger.v1",
        "verified_at": now_iso(),
        "ledger": str(ledger_path),
        "counts": counts,
        "claims": results,
    }
