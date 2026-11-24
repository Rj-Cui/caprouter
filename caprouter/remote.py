"""Online skill discovery and quarantine downloads.

Discovery is read-only against the public skills.sh catalog. Fetch writes only
to ``.caprouter/quarantine/`` and never installs or executes anything. Vendor
produces a six-step deconstruction report and a Card draft in quarantine;
installation still goes through ``install --approve`` and ``review``.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from .audit import append_audit
from .cards import generate_draft
from .state import is_enabled
from .util import append_jsonl, hash_tree, now_iso, read_json, safe_name, write_json_atomic

SKILLS_SH_BASE = "https://skills.sh"
DISCOVERY_SCHEMA = "caprouter.discovery.v1"
FETCH_SCHEMA = "caprouter.fetch.v1"
VENDOR_SCHEMA = "caprouter.vendor.v1"
QUARANTINE_SCHEMA = "caprouter.quarantine.v1"

ALLOWED_HOSTS = {"skills.sh", "www.skills.sh"}
FETCH_URL_HOSTS = {
    "skills.sh",
    "www.skills.sh",
    "github.com",
    "codeload.github.com",
    "raw.githubusercontent.com",
    "objects.githubusercontent.com",
}
FETCH_URL_SCHEMA = "caprouter.fetch-url.v1"
MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
USER_AGENT = "CapRouter/0.7.0"
MAX_FILES = 500
MAX_BYTES = 20 * 1024 * 1024
MAX_RESPONSE_BYTES = 25 * 1024 * 1024
MAX_SEARCH_RESULTS = 100
MIN_REQUEST_INTERVAL = 0.5
RETRY_STATUS = {429, 500, 502, 503, 504}
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
_LAST_REQUEST_AT = 0.0


def _remote_root(workspace: Path | str) -> Path:
    return Path(workspace) / ".caprouter"


def quarantine_dir(workspace: Path | str) -> Path:
    return _remote_root(workspace) / "quarantine"


def discovery_path(workspace: Path | str) -> Path:
    return _remote_root(workspace) / "discovery.jsonl"


def discovery_cache_path(workspace: Path | str) -> Path:
    return _remote_root(workspace) / "discovery-cache.json"


def _cache_key(query: str, source: str) -> str:
    return f"{source}:{str(query).strip().lower()}"


def _read_cache(workspace: Path | str) -> dict[str, Any]:
    data = read_json(discovery_cache_path(workspace), {})
    return data if isinstance(data, dict) else {}


def _write_cache(workspace: Path | str, key: str, result: dict[str, Any]) -> None:
    cache = _read_cache(workspace)
    cache[key] = {**result, "cached_at": now_iso()}
    if len(cache) > 50:
        ordered = sorted(
            cache.items(),
            key=lambda item: str((item[1] or {}).get("cached_at") or ""),
        )
        cache = dict(ordered[-50:])
    write_json_atomic(discovery_cache_path(workspace), cache)


def _audit(event: str, **fields: Any) -> None:
    try:
        append_audit({"event": event, **fields})
    except OSError:
        pass


def _require_enabled(operation: str) -> None:
    if not is_enabled():
        raise ValueError(f"CapRouter is disabled; cannot {operation}")


def _require_network(allow_network: bool) -> None:
    if not allow_network:
        raise ValueError("remote access requires --allow-network")


def _throttle() -> None:
    global _LAST_REQUEST_AT
    wait = MIN_REQUEST_INTERVAL - (time.monotonic() - _LAST_REQUEST_AT)
    if wait > 0:
        time.sleep(wait)
    _LAST_REQUEST_AT = time.monotonic()


def _http_json(url: str, timeout: int = 30, retries: int = 1) -> dict[str, Any]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError(f"blocked remote host: {parsed.hostname}")
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    attempts = max(0, int(retries)) + 1
    last_error: str = "remote request failed"
    for attempt in range(attempts):
        _throttle()
        try:
            with urllib.request.urlopen(request, timeout=max(1, int(timeout))) as response:
                final = urllib.parse.urlparse(response.geturl())
                if final.scheme != "https" or final.hostname not in ALLOWED_HOSTS:
                    raise ValueError(f"redirected to blocked host: {final.hostname}")
                length = response.headers.get("Content-Length")
                if length and int(length) > MAX_RESPONSE_BYTES:
                    raise ValueError("remote response too large")
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise ValueError("remote skill not found") from exc
            last_error = f"remote HTTP {exc.code}"
            if exc.code in RETRY_STATUS and attempt + 1 < attempts:
                time.sleep(0.5 * (2 ** attempt))
                continue
            if exc.code == 429:
                raise ValueError("remote rate limited; retry later") from exc
            raise ValueError(last_error) from exc
        except urllib.error.URLError as exc:
            last_error = f"remote request failed: {exc.reason}"
            if attempt + 1 < attempts:
                time.sleep(0.5 * (2 ** attempt))
                continue
            raise ValueError(last_error) from exc
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError("remote response too large")
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("remote response is not JSON") from exc
        if not isinstance(data, dict):
            raise ValueError("remote response must be an object")
        return data
    raise ValueError(last_error)


def _split_candidate_id(candidate_id: str) -> tuple[str, str]:
    parts = str(candidate_id or "").strip().split("/")
    if len(parts) != 3 or any(not part or part in {".", ".."} for part in parts):
        raise ValueError("candidate must look like owner/repository/skill")
    source = f"{parts[0]}/{parts[1]}"
    skill = parts[2]
    return source, skill


def discover_skills(
    workspace: Path | str,
    query: str,
    *,
    source: str = "skills.sh",
    limit: int = 10,
    offset: int = 0,
    allow_network: bool = False,
    offline: bool = False,
    timeout: int = 30,
) -> dict[str, Any]:
    _require_enabled("discover")
    if source != "skills.sh":
        raise ValueError(f"unsupported discovery source: {source}")
    query = str(query or "").strip()
    if not query:
        raise ValueError("query is required")
    limit = max(1, min(int(limit or 10), 50))
    offset = max(0, int(offset or 0))
    if offset >= MAX_SEARCH_RESULTS:
        raise ValueError(f"offset must be less than {MAX_SEARCH_RESULTS}")
    key = _cache_key(query, source)
    if offline:
        cached = _read_cache(workspace).get(key)
        if not isinstance(cached, dict):
            raise ValueError(
                f"no cached discovery result for '{query}'; run discover online first"
            )
        result = dict(cached)
        result.update({"status": "cached", "offline": True})
        result.pop("cached_at", None)
        return result
    _require_network(allow_network)
    # skills.sh has no offset/page parameter; request up to 100 and slice locally.
    request_limit = min(MAX_SEARCH_RESULTS, offset + limit)
    url = f"{SKILLS_SH_BASE}/api/search?q={urllib.parse.quote(query)}&limit={request_limit}"
    data = _http_json(url, timeout=timeout)
    raw_candidates: list[dict[str, Any]] = []
    for item in data.get("skills") or []:
        if not isinstance(item, dict):
            continue
        candidate_id = str(item.get("id") or "").strip()
        if not candidate_id:
            continue
        license_value = item.get("license")
        if isinstance(license_value, dict):
            license_value = license_value.get("name") or license_value.get("spdx")
        raw_candidates.append(
            {
                "id": candidate_id,
                "source": item.get("source"),
                "skill_id": item.get("skillId"),
                "name": item.get("name"),
                "description": item.get("description") or item.get("summary") or "",
                "author": item.get("author") or item.get("owner") or "",
                "license": license_value,
                "installs": item.get("installs"),
                "updated_at": item.get("updatedAt") or item.get("updated_at"),
                "detail_url": f"{SKILLS_SH_BASE}/{candidate_id}",
                "download_url": f"{SKILLS_SH_BASE}/api/download/{item.get('source')}/{item.get('skillId')}",
            }
        )
    candidates = raw_candidates[offset : offset + limit]
    next_offset = offset + len(candidates)
    result = {
        "schema": DISCOVERY_SCHEMA,
        "status": "ok",
        "query": query,
        "source": source,
        "offset": offset,
        "limit": limit,
        "count": len(candidates),
        "next_offset": next_offset if len(candidates) == limit and next_offset < MAX_SEARCH_RESULTS else None,
        "available": len(raw_candidates),
        "candidates": candidates,
        "checked_at": now_iso(),
    }
    _write_cache(workspace, key, result)
    append_jsonl(discovery_path(workspace), result)
    _audit("remote_discover", source=source, query=query, count=len(candidates))
    return result


def _license_info(files: list[tuple[Path, str]]) -> dict[str, Any]:
    """Read a LICENSE/COPYING file and identify common licenses.

    This is a filename/text heuristic, not legal advice. Unknown text is
    reported as ``unknown`` instead of guessing.
    """
    candidate = None
    for relative, contents in files:
        name = relative.name.upper()
        if name.startswith(("LICENSE", "COPYING", "NOTICE")):
            candidate = (relative, contents)
            break
    if candidate is None:
        return {"name": "unknown", "file": None, "detected": False}
    relative, contents = candidate
    text = contents[:12000].lower()
    patterns = [
        ("AGPL-3.0", ("gnu affero general public license",)),
        ("GPL-3.0", ("gnu general public license", "version 3")),
        ("GPL-2.0", ("gnu general public license", "version 2")),
        ("Apache-2.0", ("apache license", "version 2.0")),
        ("MPL-2.0", ("mozilla public license",)),
        ("CC-BY-4.0", ("creative commons attribution 4.0",)),
        ("BSD-3-Clause", ("bsd 3-clause", "neither the name")),
        ("BSD-2-Clause", ("bsd 2-clause",)),
        ("Unlicense", ("free and unencumbered software released into the public domain",)),
        ("MIT", ("mit license", "permission is hereby granted, free of charge")),
    ]
    for label, needles in patterns:
        if all(needle in text for needle in needles):
            return {"name": label, "file": relative.as_posix(), "detected": True}
    return {"name": "unknown", "file": relative.as_posix(), "detected": False}


def _validate_files(files: Any) -> list[tuple[Path, str]]:
    if not isinstance(files, list) or not files:
        raise ValueError("remote download has no files")
    if len(files) > MAX_FILES:
        raise ValueError(f"remote download exceeds {MAX_FILES} files")
    normalized: list[tuple[Path, str]] = []
    seen: set[str] = set()
    total = 0
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("remote file entry must be an object")
        raw_path = str(item.get("path") or "").strip()
        contents = item.get("contents")
        if not raw_path or not isinstance(contents, str):
            raise ValueError("remote file entry needs path and contents")
        relative = Path(raw_path)
        if relative.is_absolute() or relative.drive or ".." in relative.parts:
            raise ValueError(f"unsafe remote path: {raw_path}")
        if any(part.lower() in _RESERVED for part in relative.parts):
            raise ValueError(f"reserved remote path: {raw_path}")
        key = relative.as_posix()
        if key in seen:
            raise ValueError(f"duplicate remote path: {raw_path}")
        seen.add(key)
        total += len(contents.encode("utf-8"))
        if total > MAX_BYTES:
            raise ValueError(f"remote download exceeds {MAX_BYTES} bytes")
        normalized.append((relative, contents))
    return normalized


def _allowed_url_hosts(workspace: Path | str) -> set[str]:
    hosts = set(FETCH_URL_HOSTS)
    config = read_json(Path(workspace) / ".caprouter" / "config.json", {})
    remote = config.get("remote") if isinstance(config, dict) else None
    extra = remote.get("allowed_hosts") if isinstance(remote, dict) else None
    if isinstance(extra, list):
        hosts.update(str(item).strip().lower() for item in extra if str(item).strip())
    return hosts


def _check_url(url: str, allowed: set[str]) -> urllib.parse.ParseResult:
    parsed = urllib.parse.urlparse(str(url))
    host = str(parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in allowed:
        raise ValueError(f"blocked remote host: {parsed.hostname}")
    return parsed


def _http_bytes(
    url: str,
    *,
    allowed: set[str],
    timeout: int = 60,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
) -> tuple[bytes, str]:
    _check_url(url, allowed)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    _throttle()
    try:
        with urllib.request.urlopen(request, timeout=max(1, int(timeout))) as response:
            final = str(response.geturl())
            _check_url(final, allowed)
            length = response.headers.get("Content-Length")
            if length and int(length) > max_bytes:
                raise ValueError("remote download too large")
            raw = response.read(max_bytes + 1)
    except urllib.error.HTTPError as exc:
        raise ValueError(f"remote HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"remote request failed: {exc.reason}") from exc
    if len(raw) > max_bytes:
        raise ValueError("remote download too large")
    return raw, final


def _quarantine_url_dir(workspace: Path | str, host: str, digest: str) -> Path:
    short = digest.split(":", 1)[-1][:12]
    return quarantine_dir(workspace) / "url" / safe_name(f"{host}-{short}")


def fetch_url(
    workspace: Path | str,
    url: str,
    *,
    allow_network: bool = False,
    force: bool = False,
    timeout: int = 60,
) -> dict[str, Any]:
    """Download a ZIP from an allowlisted host into quarantine.

    Nothing is installed or executed. The extracted tree is hashed and written
    with provenance; use ``vendor --dir`` then ``install --approve``.
    """
    _require_enabled("fetch-url")
    _require_network(allow_network)
    workspace = Path(workspace)
    allowed = _allowed_url_hosts(workspace)
    parsed = _check_url(url, allowed)
    raw, final = _http_bytes(url, allowed=allowed, timeout=timeout)
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    dest = _quarantine_url_dir(workspace, str(parsed.hostname or ""), digest)
    root = quarantine_dir(workspace).resolve()
    try:
        dest.resolve().relative_to(root)
    except (ValueError, OSError) as exc:
        raise ValueError("quarantine target escapes workspace") from exc
    if dest.exists() and not force:
        raise ValueError(f"already fetched: {dest}; pass --force to replace")
    staging = quarantine_dir(workspace) / "url" / f".tmp-{int(time.time() * 1000)}"
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    try:
        zip_path = staging / "download.zip"
        zip_path.write_bytes(raw)
        from .install import _safe_extract, _zip_root

        extracted = staging / "extracted"
        extracted.mkdir()
        _safe_extract(zip_path, extracted)
        source_root = _zip_root(extracted)
        files: list[dict[str, Any]] = []
        total = 0
        for path in sorted(source_root.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            size = path.stat().st_size
            if len(files) + 1 > MAX_FILES:
                raise ValueError(f"remote download exceeds {MAX_FILES} files")
            total += size
            if total > MAX_BYTES:
                raise ValueError(f"remote download exceeds {MAX_BYTES} bytes")
            files.append({"path": path.relative_to(source_root).as_posix(), "bytes": size})
        if not files:
            raise ValueError("download has no files")
        content_hash = hash_tree(source_root)
        license_files: list[tuple[Path, str]] = []
        for row in files:
            name = Path(str(row["path"])).name.upper()
            if name.startswith(("LICENSE", "COPYING", "NOTICE")):
                license_files.append(
                    (
                        Path(str(row["path"])),
                        (source_root / str(row["path"])).read_text(
                            encoding="utf-8", errors="replace"
                        ),
                    )
                )
        license_info = (
            _license_info(license_files)
            if license_files
            else {"name": "unknown", "file": None, "detected": False}
        )
        candidate_id = f"url:{parsed.hostname}/{digest.split(':', 1)[-1][:12]}"
        provenance = {
            "schema": QUARANTINE_SCHEMA,
            "candidate_id": candidate_id,
            "source": "url",
            "url": final,
            "content_hash": content_hash,
            "remote_hash": digest,
            "file_count": len(files),
            "total_bytes": total,
            "files": files,
            "license": license_info,
            "fetched_at": now_iso(),
            "status": "quarantined",
            "install_hint": f'caprouter install --workspace <ws> --source "{dest}" --approve',
        }
        write_json_atomic(source_root / "provenance.json", provenance)
        if dest.exists():
            shutil.rmtree(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source_root), str(dest))
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    _audit(
        "remote_fetch_url",
        url=final,
        decision="quarantined",
        files=len(files),
        bytes=total,
    )
    return {
        "schema": FETCH_URL_SCHEMA,
        "status": "quarantined",
        "url": final,
        "candidate_id": candidate_id,
        "quarantine_dir": str(dest),
        "provenance_path": str(dest / "provenance.json"),
        "file_count": len(files),
        "total_bytes": total,
        "content_hash": content_hash,
        "license": license_info,
        "install_hint": provenance["install_hint"],
    }


def fetch_skill(
    workspace: Path | str,
    candidate_id: str,
    *,
    allow_network: bool = False,
    force: bool = False,
    timeout: int = 60,
) -> dict[str, Any]:
    _require_enabled("fetch")
    _require_network(allow_network)
    source, skill = _split_candidate_id(candidate_id)
    url = f"{SKILLS_SH_BASE}/api/download/{urllib.parse.quote(source, safe='/')}/{urllib.parse.quote(skill, safe='')}"
    data = _http_json(url, timeout=timeout)
    files = _validate_files(data.get("files"))
    dest = quarantine_dir(workspace) / safe_name(source.replace("/", "-")) / safe_name(skill)
    root = quarantine_dir(workspace).resolve()
    try:
        dest.resolve().relative_to(root)
    except (ValueError, OSError) as exc:
        raise ValueError(f"quarantine target escapes workspace: {dest}") from exc
    if dest.exists() and not force:
        raise ValueError(f"already fetched: {dest}; pass --force to replace")

    parent = dest.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = parent / f".{dest.name}.tmp-{int(time.time() * 1000)}"
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    try:
        file_rows: list[dict[str, Any]] = []
        total = 0
        for relative, contents in files:
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(contents, encoding="utf-8", newline="\n")
            size = len(contents.encode("utf-8"))
            total += size
            file_rows.append({"path": relative.as_posix(), "bytes": size})
        digest = hash_tree(staging)
        provenance = {
            "schema": QUARANTINE_SCHEMA,
            "candidate_id": candidate_id,
            "source": source,
            "skill": skill,
            "download_url": url,
            "remote_hash": data.get("hash"),
            "content_hash": digest,
            "file_count": len(file_rows),
            "total_bytes": total,
            "files": file_rows,
            "license": _license_info(files),
            "fetched_at": now_iso(),
            "status": "quarantined",
            "install_hint": f"caprouter install --workspace <ws> --source \"{dest}\" --approve",
        }
        write_json_atomic(staging / "provenance.json", provenance)
        if dest.exists():
            shutil.rmtree(dest)
        staging.replace(dest)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    _audit("remote_fetch", candidate_id=candidate_id, decision="quarantined", files=len(files), bytes=total)
    return {
        "schema": FETCH_SCHEMA,
        "status": "quarantined",
        "candidate_id": candidate_id,
        "quarantine_dir": str(dest),
        "provenance_path": str(dest / "provenance.json"),
        "file_count": len(files),
        "total_bytes": total,
        "content_hash": digest,
        "install_hint": provenance["install_hint"],
    }


def _extract_steps(body: str, limit: int = 12) -> list[str]:
    """Extract steps only from an explicit workflow/steps section.

    A bare bullet list elsewhere in SKILL.md is usually references or tips, not
    an ordered workflow; treating those as steps produces a misleading report.
    """
    lines = body.splitlines()
    start: int | None = None
    for index, line in enumerate(lines):
        if re.match(r"^#{1,6}\s+.*(workflow|steps|process|procedure|流程|步骤|工作流)", line, re.I):
            start = index + 1
            break
    if start is None:
        return []
    steps: list[str] = []
    for line in lines[start:]:
        if re.match(r"^#{1,6}\s+", line):
            break
        stripped = line.strip()
        if re.match(r"^(?:\d+[.)]|[-*+])\s+\S", stripped) and len(stripped) > 4:
            steps.append(stripped[:180])
        if len(steps) >= limit:
            break
    return steps


def _extract_constraints(body: str, limit: int = 12) -> list[str]:
    rules: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if any(word in stripped for word in ("必须", "不要", "只能", "禁止", "失败", "停止", "MUST", "NEVER", "must not")):
            if len(stripped) > 4:
                rules.append(stripped[:220])
        if len(rules) >= limit:
            break
    return rules


def _deconstruction_report(draft: dict[str, Any], dest: Path, provenance: dict[str, Any]) -> str:
    skill_md = dest / "SKILL.md"
    body = skill_md.read_text(encoding="utf-8", errors="replace") if skill_md.is_file() else ""
    files = sorted(
        path.relative_to(dest).as_posix()
        for path in dest.rglob("*")
        if path.is_file() and path.name not in {"provenance.json", "card_draft.json", "deconstruction.md"}
    )
    scripts = [name for name in files if name.startswith("scripts/")]
    steps = _extract_steps(body)
    constraints = _extract_constraints(body)
    permissions = draft.get("permissions") or {}
    invoke = draft.get("invoke") or {}
    lines = [
        f"# Skill deconstruction: {provenance.get('candidate_id')}",
        "",
        "> Generated by `caprouter vendor` from the quarantined SKILL.md. This is a",
        "> deterministic first pass following the `skill-deconstruction` six-step",
        "> method; the host agent must complete the semantic judgments.",
        "",
        "## Step 1 — Delivery",
        "",
        f"- Input: from the skill's declared trigger and examples.",
        f"- Deliverable: {draft.get('description') or '(missing description)'}",
        f"- Complete when: the acceptance checks below pass, not merely when the model says it is done.",
        "",
        "## Step 2 — Actions",
        "",
        f"- Files: {', '.join(files[:20]) if files else '(none)'}",
        f"- Scripts: {', '.join(scripts[:10]) if scripts else '(none — prompt-only skill)'}",
        f"- Declared capabilities: {', '.join(draft.get('capabilities') or []) or '(none)'}",
    ]
    if steps:
        lines.extend(["", "Observed ordered steps:", *[f"- {step}" for step in steps]])
    else:
        lines.extend(
            [
                "",
                "No explicit ordered workflow heading found. Inspect SKILL.md and",
                "references manually; do not treat unrelated bullets as steps.",
            ]
        )
    lines.extend(
        [
            "",
            "## Step 3 — Order",
            "",
            "> Host to complete: for each adjacent pair above, state the problem the",
            "> ordering prevents. Do not invent steps that are not in SKILL.md.",
            "",
            "## Step 4 — Division of labor",
            "",
            f"- Invoke mode: {invoke.get('mode')} (allow={invoke.get('allow')})",
            f"- Declared permissions: fs={permissions.get('fs')} net={permissions.get('net')} shell={permissions.get('shell')}",
            f"- Script boundary: {'scripts present; must be reviewed before enabling shell=true' if scripts else 'none'}",
            f"- Human checkpoint: {'yes — review/approve before install' if not invoke.get('allow') else 'verify invoke.allow and Card permissions'}",
            "",
            "## Step 5 — Constraints",
            "",
        ]
    )
    if constraints:
        lines.extend(f"- {rule}" for rule in constraints)
    else:
        lines.append("- (no explicit must/never rules found; inspect references manually)")
    lines.extend(
        [
            "",
            "## Step 6 — Principle",
            "",
            f"> Host to complete: when <situation>, do <action>, because it avoids <risk>;",
            f"> this holds only if <premise>. Starting point: {draft.get('description') or '(missing)'}",
            "",
            "## CapRouter mapping",
            "",
            f"- Card id: {draft.get('id')}",
            f"- Draft file: {dest / 'card_draft.json'}",
            f"- Quarantine: {dest}",
            f"- Install: {provenance.get('install_hint')}",
            "",
            "## License",
            "",
            f"- Detected: {(provenance.get('license') or {}).get('name') or 'unknown'}",
            f"- File: {(provenance.get('license') or {}).get('file') or '(none)'}",
            "- This is a filename/text heuristic, not legal advice; read the license before redistribution.",
            "",
            "## Safety",
            "",
            "- Downloaded content was not executed.",
            "- Installation still requires `install --approve`; command execution still requires a reviewed Card with `permissions.shell=true`.",
        ]
    )
    return "\n".join(lines) + "\n"


def _age_days(path: Path, provenance: dict[str, Any]) -> float:
    raw = str(provenance.get("fetched_at") or "").strip()
    if raw:
        try:
            then = datetime.fromisoformat(raw)
            if then.tzinfo is None:
                then = then.astimezone()
            return max(0.0, (datetime.now().astimezone() - then).total_seconds() / 86400)
        except ValueError:
            pass
    try:
        return max(0.0, (time.time() - path.stat().st_mtime) / 86400)
    except OSError:
        return 0.0


def list_quarantine(workspace: Path | str) -> dict[str, Any]:
    root = quarantine_dir(workspace)
    items: list[dict[str, Any]] = []
    if root.is_dir():
        for source_dir in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
            for skill_dir in sorted(p for p in source_dir.iterdir() if p.is_dir() and not p.name.startswith(".")):
                provenance = read_json(skill_dir / "provenance.json", {})
                provenance = provenance if isinstance(provenance, dict) else {}
                items.append(
                    {
                        "candidate_id": provenance.get("candidate_id")
                        or f"{source_dir.name}/{skill_dir.name}",
                        "quarantine_dir": str(skill_dir),
                        "file_count": provenance.get("file_count"),
                        "total_bytes": provenance.get("total_bytes"),
                        "fetched_at": provenance.get("fetched_at"),
                        "age_days": round(_age_days(skill_dir, provenance), 2),
                        "status": provenance.get("status") or "unknown",
                        "license": provenance.get("license") or {"name": "unknown", "file": None},
                        "has_deconstruction": (skill_dir / "deconstruction.md").is_file(),
                        "has_card_draft": (skill_dir / "card_draft.json").is_file(),
                    }
                )
    return {"schema": "caprouter.quarantine-list.v1", "count": len(items), "items": items}


def clean_quarantine(
    workspace: Path | str,
    *,
    candidate: str | None = None,
    older_than_days: float | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    root = quarantine_dir(workspace).resolve()
    targets: list[Path] = []
    if candidate:
        source, skill = _split_candidate_id(candidate)
        target = root / safe_name(source.replace("/", "-")) / safe_name(skill)
        try:
            target.resolve().relative_to(root)
        except (ValueError, OSError) as exc:
            raise ValueError(f"quarantine target escapes root: {target}") from exc
        if not target.is_dir():
            raise ValueError(f"not found in quarantine: {candidate}")
        targets.append(target)
    elif older_than_days is not None:
        threshold = max(0.0, float(older_than_days))
        for item in list_quarantine(workspace)["items"]:
            if float(item.get("age_days") or 0) >= threshold:
                target = Path(str(item.get("quarantine_dir") or ""))
                try:
                    target.resolve().relative_to(root)
                except (ValueError, OSError):
                    continue
                if target.is_dir():
                    targets.append(target)
    else:
        raise ValueError("candidate or older_than_days is required")

    removed: list[str] = []
    for target in targets:
        removed.append(str(target))
        if dry_run:
            continue
        shutil.rmtree(target)
        parent = target.parent
        if parent != root and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
    result = {
        "schema": "caprouter.quarantine-clean.v1",
        "status": "preview" if dry_run else "cleaned",
        "dry_run": bool(dry_run),
        "count": len(removed),
        "removed": removed,
        "checked_at": now_iso(),
    }
    if not dry_run:
        _audit("quarantine_clean", count=len(removed), candidates=removed[:20])
    return result


def vendor_skill(
    workspace: Path | str,
    candidate_id: str = "",
    *,
    method: str = "skill-deconstruction",
    quarantine_path: Path | str | None = None,
) -> dict[str, Any]:
    _require_enabled("vendor")
    if method != "skill-deconstruction":
        raise ValueError(f"unsupported vendor method: {method}")
    if quarantine_path:
        dest = Path(quarantine_path).expanduser().resolve()
        root = quarantine_dir(workspace).resolve()
        try:
            dest.relative_to(root)
        except (ValueError, OSError) as exc:
            raise ValueError(f"quarantine path escapes workspace: {dest}") from exc
        if not dest.is_dir():
            raise ValueError(f"not a quarantine directory: {dest}")
        provenance = read_json(dest / "provenance.json", {})
        provenance = provenance if isinstance(provenance, dict) else {}
        candidate_id = candidate_id or str(provenance.get("candidate_id") or f"url:{dest.name}")
        skill = dest.name
        source_label = str(provenance.get("source") or "url")
    else:
        source, skill = _split_candidate_id(candidate_id)
        dest = quarantine_dir(workspace) / safe_name(source.replace("/", "-")) / safe_name(skill)
        if not dest.is_dir():
            raise ValueError(f"not fetched: {dest}")
        provenance = read_json(dest / "provenance.json", {})
        provenance = provenance if isinstance(provenance, dict) else {}
        source_label = "skills.sh"
    skill_md = dest / "SKILL.md"
    if not skill_md.is_file():
        raise ValueError(f"SKILL.md is missing: {skill_md}")
    draft = generate_draft(
        workspace,
        path=dest,
        type_hint="skill",
        name=None if quarantine_path else skill,
    )
    draft["provenance"] = {
        **(draft.get("provenance") if isinstance(draft.get("provenance"), dict) else {}),
        "source": source_label,
        "candidate_id": candidate_id,
        "quarantine_dir": str(dest),
        "remote_hash": provenance.get("remote_hash"),
        "content_hash": provenance.get("content_hash"),
        "license": provenance.get("license") or {"name": "unknown", "file": None},
    }
    draft.setdefault("review", {})
    if isinstance(draft["review"], dict):
        notes = draft["review"].get("notes") if isinstance(draft["review"].get("notes"), list) else []
        notes.append("remote quarantine draft; install --approve before review/apply")
        license_info = provenance.get("license") or {}
        notes.append(
            f"license: {license_info.get('name') or 'unknown'} ({license_info.get('file') or 'no LICENSE file'})"
        )
        draft["review"]["notes"] = notes
    card_path = dest / "card_draft.json"
    write_json_atomic(card_path, draft)
    report_path = dest / "deconstruction.md"
    report_path.write_text(_deconstruction_report(draft, dest, provenance), encoding="utf-8")
    _audit("remote_vendor", candidate_id=candidate_id, decision="vendored", draft=str(card_path))
    return {
        "schema": VENDOR_SCHEMA,
        "status": "vendored",
        "candidate_id": candidate_id,
        "method": method,
        "quarantine_dir": str(dest),
        "deconstruction_path": str(report_path),
        "card_draft_path": str(card_path),
        "card_id": draft.get("id"),
        "install_hint": provenance.get("install_hint")
        or f'caprouter install --workspace <ws> --source "{dest}" --approve',
        "next": [
            f'caprouter install --workspace <ws> --source "{dest}" --approve',
            "caprouter review --workspace <ws> --id <installed-id>",
            "caprouter capability get --workspace <ws> --id <installed-id>",
        ],
    }
