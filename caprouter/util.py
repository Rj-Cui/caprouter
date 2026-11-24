from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    ".mypy_cache",
    ".pytest_cache",
}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_json(path: Path | str, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json_atomic(path: Path | str, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def read_jsonl(path: Path | str) -> list[dict[str, Any]]:
    """Read a JSONL file, skipping malformed or non-object rows."""
    rows: list[dict[str, Any]] = []
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return rows
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def append_jsonl(path: Path | str, row: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def safe_name(value: str, fallback: str = "unnamed") -> str:
    """Turn a human name into a portable directory/file name.

    Unicode letters are kept so Chinese skill names do not collapse to
    ``unnamed``; path separators and shell-sensitive characters are replaced.
    """
    cleaned = re.sub(r"[^\w.-]+", "-", str(value or "").strip(), flags=re.UNICODE)
    cleaned = re.sub(r"[-_.]{2,}", "-", cleaned).strip("-._")
    reserved = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
    if cleaned.lower() in reserved:
        cleaned = f"{cleaned}-item"
    return cleaned[:80] or fallback


def first_prose_line(text: str) -> str:
    """Return the first README-like prose line, skipping badges and nav bars."""
    for line in (text or "").splitlines():
        value = line.strip()
        if not value or len(value) < 12:
            continue
        if value.startswith(("#", "!", "[", "<", "---", "```", "=", "{", "}", '"', "'", "|")):
            continue
        plain = re.sub(r"!?\[([^\]]*)\]\([^)]+\)", r"\1", value).strip()
        lowered = plain.lower()
        if len(plain) < 12:
            continue
        if lowered.startswith(("english", "中文", "简体", "繁體", "language", "read this")):
            continue
        if plain.count("|") >= 2:
            continue
        if len(plain) <= 200:
            return plain
        cut = max(plain.rfind(mark, 0, 200) for mark in ".!?。！？")
        if cut >= 60:
            return plain[: cut + 1]
        return plain[:200].rsplit(" ", 1)[0].strip()
    return ""


def split_keywords(value: Any) -> list[str]:
    """Normalize a frontmatter/JSON keyword field into a clean string list."""
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        cleaned = value.replace("，", ",").replace("、", ",")
        return [part.strip() for part in cleaned.split(",") if part.strip()]
    return []


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def hash_tree(root: Path | str) -> str:
    """Hash a file or a directory tree, including relative paths."""
    root = Path(root)
    digest = hashlib.sha256()
    if root.is_file():
        return sha256_text(root.read_text(encoding="utf-8", errors="replace"))
    if not root.is_dir():
        return sha256_text("")
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if any(part in SKIP_DIRS for part in relative.parts[:-1]):
            continue
        if path.suffix.lower() in {".pyc", ".pyo"}:
            continue
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"<unreadable>")
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return ""
    if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
        return value[1:-1]
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(part) for part in inner.split(",")]
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    return value


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse the small YAML subset used by SKILL.md frontmatter.

    Supported: scalar values, inline lists, quoted strings, and simple
    ``|`` / ``>`` block scalars. No third-party YAML dependency.
    """
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    end: int | None = None
    for index in range(1, len(lines)):
        if lines[index].strip() in {"---", "..."}:
            end = index
            break
    if end is None:
        return {}, text

    data: dict[str, Any] = {}
    key: str | None = None
    block: list[str] = []
    block_mode: str | None = None

    def flush() -> None:
        nonlocal block, block_mode
        if key and block:
            data[key] = ("\n" if block_mode == "|" else " ").join(block).strip()
        block = []
        block_mode = None

    for raw in lines[1:end]:
        if not raw.strip():
            if block:
                block.append("")
            continue
        if raw[:1] in {" ", "\t"} and key:
            block.append(raw.strip())
            continue
        flush()
        if ":" not in raw:
            continue
        name, value = raw.split(":", 1)
        key = name.strip()
        value = value.strip()
        if value in {"|", ">"}:
            block_mode = value
            data[key] = ""
        else:
            data[key] = _parse_scalar(value)
    flush()
    return data, "\n".join(lines[end + 1 :])
