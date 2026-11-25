"""Capability Card and evidence migration between machines.

Paths are machine-specific, so the bundle carries identities (manifest name,
content hash, path hint) instead of relying on absolute paths. On import the
new machine rescans, matches identities against its own registry, applies
matched reviewed cards, and leaves unmatched cards in ``drafts/migrated/``.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from .capabilities import default_invoke, load_entries, load_overlay, overlay_path
from .cards import DRAFT_SCHEMA, draft_path, drafts_dir, entry_hash, generate_draft, write_draft
from .evidence import collect_evidence
from .paths import config_path
from .util import (
    append_jsonl,
    now_iso,
    read_json,
    read_jsonl,
    safe_name,
    sha256_text,
    write_json_atomic,
)
from .workspace import refresh_registry

MIGRATION_SCHEMA = "caprouter.migration-bundle.v1"
MIGRATION_IMPORT_SCHEMA = "caprouter.migration-import.v1"
MAX_BUNDLE_BYTES = 200 * 1024 * 1024
MAX_BUNDLE_MEMBERS = 10_000
MIGRATION_DIR = "migrations"


def _norm(path: Any) -> str:
    try:
        return os.path.normcase(os.path.abspath(str(path)))
    except (OSError, ValueError):
        return os.path.normcase(str(path))


def _manifest_name(path: Path, entry_type: str) -> str:
    if not path.is_dir():
        return ""
    try:
        return str(collect_evidence(path, entry_type).get("metadata", {}).get("name") or "")
    except Exception:
        return ""


def _identity(entry: dict[str, Any]) -> dict[str, Any]:
    path = Path(str(entry.get("path") or ""))
    entry_type = str(entry.get("type") or "project")
    parts = [part for part in path.parts if part not in {path.anchor, "\\", "/"}]
    return {
        "id": str(entry.get("id") or ""),
        "type": entry_type,
        "name": str(entry.get("name") or ""),
        "path_hint": parts[-3:],
        "registry_hash": str(entry.get("hash") or ""),
        "metadata_hash": entry_hash(path, entry_type) if path.exists() else None,
        "manifest_name": _manifest_name(path, entry_type),
        "markers": entry.get("markers") or [],
    }


def _filter_rows(rows: list[dict[str, Any]], ids: set[str]) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for row in rows:
        capability_id = str(row.get("capability_id") or row.get("id") or "")
        if capability_id in ids:
            kept.append(row)
    return kept


def export_bundle(workspace: Path | str, out: Path | str | None = None) -> dict[str, Any]:
    """Export overlay cards, identities and evidence rows to a ZIP bundle."""
    workspace = Path(workspace)
    overlay = load_overlay(workspace)
    cards = overlay.get("cards") or {}
    entries = {str(entry.get("id")): entry for entry in load_entries(workspace)}
    identities: list[dict[str, Any]] = []
    for capability_id in cards:
        entry = entries.get(capability_id)
        if entry:
            identities.append(_identity(entry))
        else:
            identities.append(
                {
                    "id": capability_id,
                    "type": "unknown",
                    "name": "",
                    "path_hint": [],
                    "registry_hash": "",
                    "metadata_hash": None,
                    "manifest_name": "",
                    "markers": [],
                    "missing_registry": True,
                }
            )

    ids = set(cards)
    bus = workspace / ".caprouter"
    outcomes = _filter_rows(read_jsonl(bus / "outcomes.jsonl"), ids)
    gaps = read_jsonl(bus / "gaps.jsonl")
    exported_at = now_iso()
    migration_id = sha256_text(f"{workspace}|{exported_at}|{len(cards)}").split(":", 1)[-1][:16]
    manifest = {
        "schema": MIGRATION_SCHEMA,
        "migration_id": migration_id,
        "exported_at": exported_at,
        "source_workspace": str(workspace),
        "counts": {
            "cards": len(cards),
            "identities": len(identities),
            "probes": len(_filter_rows(read_jsonl(bus / "probes.jsonl"), ids)),
            "outcomes": len(outcomes),
            "gaps": len(gaps),
        },
    }
    if out is None:
        directory = bus / MIGRATION_DIR
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"caprouter-migration-{migration_id}.zip"
    else:
        target = Path(out).expanduser()
        if target.is_dir() or target.suffix.lower() != ".zip":
            target.mkdir(parents=True, exist_ok=True)
            target = target / f"caprouter-migration-{migration_id}.zip"
    target.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "manifest.json": json.dumps(manifest, ensure_ascii=False, indent=2),
        "cards.json": json.dumps(
            {"schema": "caprouter.capability-cards.v1", "defaults": overlay.get("defaults") or {}, "cards": cards},
            ensure_ascii=False,
            indent=2,
        ),
        "identities.json": json.dumps(identities, ensure_ascii=False, indent=2),
        "probes.jsonl": "\n".join(
            json.dumps(row, ensure_ascii=False) for row in _filter_rows(read_jsonl(bus / "probes.jsonl"), ids)
        ),
        "outcomes.jsonl": "\n".join(
            json.dumps(row, ensure_ascii=False) for row in outcomes
        ),
        "gaps.jsonl": "\n".join(
            json.dumps(row, ensure_ascii=False) for row in gaps
        ),
    }
    insights = bus / "conversation_insights.json"
    if insights.is_file():
        payload["conversation_insights.json"] = insights.read_text(encoding="utf-8", errors="replace")

    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, text in payload.items():
            archive.writestr(name, text)
    return {
        "schema": "caprouter.migration-export.v1",
        "migration_id": migration_id,
        "bundle": str(target),
        "bytes": target.stat().st_size,
        "counts": manifest["counts"],
    }


def _safe_extract(bundle: Path) -> tempfile.TemporaryDirectory[str]:
    temp = tempfile.TemporaryDirectory(prefix="caprouter-migration-")
    dest = Path(temp.name)
    total = 0
    try:
        with zipfile.ZipFile(bundle) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_BUNDLE_MEMBERS:
                raise ValueError("bundle has too many members")
            for info in infos:
                name = info.filename.replace("\\", "/")
                parts = [part for part in name.split("/") if part not in {"", "."}]
                if not parts or name.startswith("/") or ".." in parts:
                    raise ValueError(f"unsafe bundle member: {info.filename}")
                if len(parts[0]) == 2 and parts[0][1] == ":":
                    raise ValueError(f"unsafe bundle member: {info.filename}")
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError(f"bundle contains a symlink: {info.filename}")
                if int(getattr(info, "flag_bits", 0)) & 0x1:
                    raise ValueError(f"encrypted bundle member: {info.filename}")
                total += int(info.file_size)
                if total > MAX_BUNDLE_BYTES:
                    raise ValueError("bundle is too large")
                archive.extract(info, dest)
    except Exception:
        temp.cleanup()
        raise
    return temp


def _path_hint_matches(entry: dict[str, Any], path_hint: list[str]) -> bool:
    if not path_hint:
        return False
    suffix = _norm("/".join(path_hint))
    return bool(suffix) and _norm(str(entry.get("path") or "")).endswith(suffix)


def _id_match_is_safe(
    identity: dict[str, Any],
    entry: dict[str, Any],
    *,
    by_manifest: dict[tuple[str, str], list[dict[str, Any]]],
) -> bool:
    """Project ids are directory-name based and can collide across machines."""
    entry_type = str(identity.get("type") or "")
    if str(entry.get("type") or "") != entry_type:
        return False
    if entry_type != "project":
        # Skill/plugin ids are derived from manifest names, so the id itself is
        # a stronger identity than a project directory name.
        return True
    manifest_name = str(identity.get("manifest_name") or "").lower()
    if manifest_name and entry in (by_manifest.get((entry_type, manifest_name)) or []):
        return True
    metadata_hash = str(identity.get("metadata_hash") or "")
    if metadata_hash and entry_hash(Path(str(entry.get("path") or "")), entry_type) == metadata_hash:
        return True
    return _path_hint_matches(entry, [str(item) for item in (identity.get("path_hint") or [])])


def _match_identity(
    identity: dict[str, Any],
    *,
    by_id: dict[str, dict[str, Any]],
    by_hash: dict[str, list[dict[str, Any]]],
    by_manifest: dict[tuple[str, str], list[dict[str, Any]]],
    by_name: dict[tuple[str, str], list[dict[str, Any]]],
    entries: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, str]:
    capability_id = str(identity.get("id") or "")
    entry_type = str(identity.get("type") or "")
    name = str(identity.get("name") or "")
    manifest_name = str(identity.get("manifest_name") or "").lower()
    path_hint = [str(item) for item in (identity.get("path_hint") or [])]

    metadata_hash = str(identity.get("metadata_hash") or "")

    entry = by_id.get(capability_id)
    if entry and _id_match_is_safe(identity, entry, by_manifest=by_manifest):
        return entry, "id"

    registry_hash = str(identity.get("registry_hash") or "")
    if registry_hash:
        candidates = by_hash.get(registry_hash) or []
        if len(candidates) == 1:
            return candidates[0], "registry_hash"

    candidates = by_manifest.get((entry_type, manifest_name)) or []
    if len(candidates) == 1:
        return candidates[0], "manifest_name"
    if metadata_hash and len(candidates) > 1:
        for candidate in candidates:
            path = Path(str(candidate.get("path") or ""))
            if entry_hash(path, entry_type) == metadata_hash:
                return candidate, "metadata_hash"

    candidates = by_name.get((entry_type, name.lower())) or []
    if len(candidates) == 1:
        return candidates[0], "name"
    if metadata_hash and len(candidates) > 1:
        for candidate in candidates:
            path = Path(str(candidate.get("path") or ""))
            if entry_hash(path, entry_type) == metadata_hash:
                return candidate, "metadata_hash"

    if path_hint:
        candidates = [item for item in entries if _path_hint_matches(item, path_hint)]
        if len(candidates) == 1:
            return candidates[0], "path_hint"
    return None, "ambiguous" if candidates else "unmatched"


def _rewrite_invoke_for_local(card: dict[str, Any], entry: dict[str, Any] | None) -> dict[str, Any]:
    """Never keep a bundle-supplied content_path; rebind to the local capability.

    A migration bundle is untrusted input: its Card may point at any file on this
    machine. Matched cards get the local default invoke; unmatched/unsafe cards
    lose the path and must be regenerated during review.
    """
    invoke = dict(card.get("invoke") or {})
    mode = str(invoke.get("mode") or "none")
    if entry is not None and mode == "prompt":
        local = default_invoke(entry)
        local["allow"] = False
        return local
    return {"mode": mode if mode in {"prompt", "command"} else "none", "allow": False}


def _card_import_unsafe(card: dict[str, Any]) -> bool:
    """A bundle is untrusted input; command execution is never auto-applied."""
    invoke = card.get("invoke") if isinstance(card.get("invoke"), dict) else {}
    return str(invoke.get("mode") or "") == "command" or bool(invoke.get("allow"))


def _write_migrated_draft(
    workspace: Path,
    capability_id: str,
    type_name: str,
    card: dict[str, Any],
    identity: dict[str, Any],
    status: str,
    entry: dict[str, Any] | None = None,
) -> Path:
    drafts = drafts_dir(workspace) / "migrated"
    digest = sha256_text(capability_id).split(":", 1)[-1][:8]
    target = drafts / f"{safe_name(str(identity.get('name') or capability_id).replace(':', '-'))}-{digest}.json"
    draft = {
        "schema": DRAFT_SCHEMA,
        "generated_at": now_iso(),
        "id": capability_id,
        "type": type_name,
        "name": identity.get("name") or card.get("name"),
        "description": card.get("description") or "",
        "domain": card.get("domain") or "general",
        "domains": card.get("domains") or [card.get("domain") or "general"],
        "capabilities": card.get("capabilities") or [],
        "keywords": card.get("keywords") or [],
        "path": "",
        "version": "",
        "examples": card.get("examples") or [],
        "probe": card.get("probe") or {"status": "unknown", "evidence": "unknown", "checks": []},
        "input_schema": card.get("input_schema"),
        "output_schema": card.get("output_schema"),
        "permissions": card.get("permissions") or {},
        "invoke": _rewrite_invoke_for_local(card, entry),
        "provenance": {
            **(card.get("provenance") if isinstance(card.get("provenance"), dict) else {}),
            "source": "migration",
            "migrated_from": capability_id,
            "migrated_at": now_iso(),
            "path_hint": identity.get("path_hint") or [],
        },
        "review": {
            "status": status,
            "risk": "unknown",
            "notes": [
                "migrated from another machine",
                "path is not remapped; review and regenerate before apply" if status == "migrated_unmatched" else "",
                "command/allow cards are not auto-applied from a bundle; review manually" if status == "migrated_unsafe" else "",
                "bundle content_path was dropped; regenerate local content before apply" if str((card.get("invoke") or {}).get("mode") or "") == "prompt" else "",
            ],
        },
    }
    draft["review"]["notes"] = [note for note in draft["review"]["notes"] if note]
    write_json_atomic(target, draft)
    return target


def import_bundle(
    workspace: Path | str,
    bundle: Path | str,
    *,
    apply_matched: bool = True,
    scan: bool = True,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Import a migration bundle into a workspace."""
    workspace = Path(workspace)
    bundle = Path(bundle).expanduser()
    if not bundle.is_file():
        raise ValueError(f"bundle not found: {bundle}")
    if not config_path(workspace).is_file():
        raise ValueError(f"workspace is not initialized: {workspace}")
    temp = _safe_extract(bundle)
    try:
        root = Path(temp.name)
        manifest = read_json(root / "manifest.json", {})
        if not isinstance(manifest, dict) or manifest.get("schema") != MIGRATION_SCHEMA:
            raise ValueError("invalid migration manifest")
        cards_payload = read_json(root / "cards.json", {})
        cards = cards_payload.get("cards") if isinstance(cards_payload, dict) else {}
        cards = cards if isinstance(cards, dict) else {}
        identities = read_json(root / "identities.json", [])
        identities = [item for item in identities if isinstance(item, dict)] if isinstance(identities, list) else []
        if not identities:
            identities = [
                {
                    "id": str(capability_id),
                    "type": str(card.get("type") or "unknown") if isinstance(card, dict) else "unknown",
                    "name": str(card.get("name") or str(capability_id).split(":", 1)[-1]) if isinstance(card, dict) else "",
                    "path_hint": [],
                    "registry_hash": "",
                    "metadata_hash": None,
                    "manifest_name": "",
                }
                for capability_id, card in cards.items()
            ]

        if scan and not dry_run:
            refresh_registry(workspace)
        entries = load_entries(workspace)
        by_id = {str(entry.get("id")): entry for entry in entries}
        by_hash: dict[str, list[dict[str, Any]]] = {}
        by_manifest: dict[tuple[str, str], list[dict[str, Any]]] = {}
        by_name: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for entry in entries:
            entry_type = str(entry.get("type") or "")
            entry_hash_value = str(entry.get("hash") or "")
            if entry_hash_value:
                by_hash.setdefault(entry_hash_value, []).append(entry)
            name = str(entry.get("name") or "")
            by_name.setdefault((entry_type, name.lower()), []).append(entry)
            path = Path(str(entry.get("path") or ""))
            manifest_name = _manifest_name(path, entry_type).lower()
            if manifest_name:
                by_manifest.setdefault((entry_type, manifest_name), []).append(entry)

        matched: list[dict[str, Any]] = []
        unmatched: list[dict[str, Any]] = []
        id_map: dict[str, str] = {}
        for identity in identities:
            entry, method = _match_identity(
                identity,
                by_id=by_id,
                by_hash=by_hash,
                by_manifest=by_manifest,
                by_name=by_name,
                entries=entries,
            )
            row = {
                "old_id": identity.get("id"),
                "new_id": entry.get("id") if entry else None,
                "type": identity.get("type"),
                "name": identity.get("name"),
                "method": method,
                "path": entry.get("path") if entry else None,
            }
            if entry:
                matched.append(row)
                id_map[str(identity.get("id"))] = str(entry.get("id"))
            else:
                unmatched.append(row)

        overlay = load_overlay(workspace)
        new_cards = dict(overlay.get("cards") or {})
        applied = 0
        kept = 0
        unsafe = 0
        drafts_written: list[str] = []
        for identity in identities:
            old_id = str(identity.get("id") or "")
            card = cards.get(old_id)
            if not isinstance(card, dict):
                continue
            new_id = id_map.get(old_id)
            card_unsafe = _card_import_unsafe(card)
            if new_id and apply_matched and not card_unsafe:
                if new_id in new_cards and not force:
                    kept += 1
                    continue
                migrated = dict(card)
                migrated["invoke"] = _rewrite_invoke_for_local(card, entry)
                migrated["provenance"] = {
                    **(card.get("provenance") if isinstance(card.get("provenance"), dict) else {}),
                    "source": "migration",
                    "migrated_from": old_id,
                    "migrated_at": now_iso(),
                    "migration_id": manifest.get("migration_id"),
                }
                new_cards[new_id] = migrated
                applied += 1
            else:
                if card_unsafe:
                    unsafe += 1
                status = (
                    "migrated_unsafe" if card_unsafe
                    else ("migrated_ready" if new_id else "migrated_unmatched")
                )
                if not dry_run:
                    drafts_written.append(
                        str(
                            _write_migrated_draft(
                                workspace,
                                old_id,
                                str(identity.get("type") or "unknown"),
                                card,
                                identity,
                                status,
                                entry,
                            )
                        )
                    )

        if not dry_run and applied:
            write_json_atomic(
                overlay_path(workspace),
                {"schema": "caprouter.capability-cards.v1", "defaults": overlay.get("defaults") or {}, "cards": new_cards},
            )

        transferred = {"probes": 0, "outcomes": 0, "gaps": 0}
        already_imported = False
        if not dry_run and id_map:
            log_path = workspace / ".caprouter" / "migrations.jsonl"
            migration_id = str(manifest.get("migration_id") or "")
            seen = {
                str(row.get("migration_id") or "")
                for row in read_jsonl(log_path)
                if str(row.get("migration_id") or "") == migration_id
            }
            already_imported = bool(migration_id and migration_id in seen)
            if not already_imported:
                bus = workspace / ".caprouter"
                for row in _filter_rows(read_jsonl(root / "probes.jsonl"), set(id_map)):
                    old_id = str(row.get("capability_id") or "")
                    new_id = id_map.get(old_id)
                    if not new_id:
                        continue
                    migrated_row = {**row, "capability_id": new_id, "migrated_from": old_id, "migrated_at": now_iso()}
                    append_jsonl(bus / "probes.jsonl", migrated_row)
                    transferred["probes"] += 1
                for row in read_jsonl(root / "outcomes.jsonl"):
                    old_id = str(row.get("capability_id") or "")
                    new_id = id_map.get(old_id)
                    if not new_id:
                        continue
                    migrated_row = {**row, "capability_id": new_id, "migrated_from": old_id, "migrated_at": now_iso()}
                    append_jsonl(bus / "outcomes.jsonl", migrated_row)
                    transferred["outcomes"] += 1
                for row in read_jsonl(root / "gaps.jsonl"):
                    append_jsonl(bus / "gaps.jsonl", {**row, "migrated_at": now_iso(), "migration_id": migration_id})
                    transferred["gaps"] += 1
                append_jsonl(
                    log_path,
                    {
                        "ts": now_iso(),
                        "migration_id": migration_id,
                        "bundle": str(bundle),
                        "matched": len(matched),
                        "applied": applied,
                    },
                )

        return {
            "schema": MIGRATION_IMPORT_SCHEMA,
            "bundle": str(bundle),
            "migration_id": manifest.get("migration_id"),
            "dry_run": bool(dry_run),
            "matched": len(matched),
            "unmatched": len(unmatched),
            "applied": applied,
            "kept_existing": kept,
            "unsafe_not_applied": unsafe,
            "already_imported": already_imported,
            "drafts_written": drafts_written,
            "transferred": transferred,
            "items": [*matched, *unmatched],
        }
    finally:
        temp.cleanup()


MIGRATED_DRAFTS_SCHEMA = "caprouter.migrated-drafts.v1"
MIGRATED_REMAP_SCHEMA = "caprouter.migrated-remap.v1"


def list_migrated(workspace: Path | str) -> dict[str, Any]:
    base = drafts_dir(workspace) / "migrated"
    rows: list[dict[str, Any]] = []
    if base.is_dir():
        for path in sorted(base.glob("*.json")):
            if not path.is_file():
                continue
            data = read_json(path, {})
            if not isinstance(data, dict):
                continue
            rows.append(
                {
                    "id": data.get("id"),
                    "type": data.get("type"),
                    "name": data.get("name"),
                    "path": data.get("path"),
                    "path_hint": (data.get("provenance") or {}).get("path_hint") or [],
                    "status": (data.get("review") or {}).get("status"),
                    "draft_path": str(path),
                }
            )
    return {"schema": MIGRATED_DRAFTS_SCHEMA, "count": len(rows), "entries": rows}


def _find_migrated(workspace: Path, capability_id: str) -> tuple[Path | None, dict[str, Any] | None]:
    base = drafts_dir(workspace) / "migrated"
    if not base.is_dir():
        return None, None
    for path in sorted(base.glob("*.json")):
        if not path.is_file():
            continue
        data = read_json(path, {})
        if isinstance(data, dict) and str(data.get("id") or "") == capability_id:
            return path, data
    return None, None


def remap_migrated(
    workspace: Path | str,
    capability_id: str,
    path: Path | str,
    *,
    name: str | None = None,
    apply: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Point an unmatched migrated draft at a local directory and re-draft it."""
    workspace = Path(workspace)
    target_path = Path(path).expanduser().resolve()
    if not target_path.is_dir():
        raise ValueError(f"path is not a directory: {target_path}")
    draft_file, old = _find_migrated(workspace, capability_id)
    if draft_file is None or old is None:
        raise ValueError(f"migrated draft not found: {capability_id}")
    fresh = generate_draft(
        workspace,
        path=target_path,
        type_hint=str(old.get("type") or "") or None,
        name=name or (str(old.get("name") or "") or None),
    )
    fresh["id"] = capability_id
    fresh["name"] = name or old.get("name") or fresh.get("name")
    fresh["type"] = old.get("type") or fresh.get("type")
    provenance = fresh.get("provenance") if isinstance(fresh.get("provenance"), dict) else {}
    provenance.update(
        {
            "remapped_from": str(draft_file),
            "remapped_at": now_iso(),
            "migrated_from": (old.get("provenance") or {}).get("migrated_from"),
            "path_hint": [str(target_path)],
        }
    )
    fresh["provenance"] = provenance
    fresh["review"] = {
        "status": "needs_review",
        "risk": "unknown",
        "notes": ["remapped from a migrated draft; review before apply"],
    }
    out = draft_path(workspace, capability_id)
    if out.exists() and not force:
        raise ValueError(f"draft already exists: {out}; pass --force")
    write_draft(workspace, fresh, out)
    archive = drafts_dir(workspace) / "migrated" / ".remapped"
    archive.mkdir(parents=True, exist_ok=True)
    shutil.move(str(draft_file), str(archive / draft_file.name))
    result: dict[str, Any] = {
        "schema": MIGRATED_REMAP_SCHEMA,
        "status": "remapped",
        "id": capability_id,
        "path": str(target_path),
        "draft_path": str(out),
    }
    if apply:
        from .review import review_drafts

        report = review_drafts(workspace, ids=[capability_id], auto_apply=True)
        applied = int(report.get("applied") or 0)
        result["review"] = {
            "total": report.get("total"),
            "counts": report.get("counts"),
            "applied": applied,
        }
        result["status"] = "applied" if applied else "needs_review"
    return result
