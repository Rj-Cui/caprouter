"""Safe ZIP extraction shared by the installer and remote quarantine.

The installer and the remote fetcher both unpack untrusted archives, but they
must not import each other just to reuse these checks (that created an import
cycle). This module depends only on ``scan``.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

from .scan import detect_source_type

MAX_ZIP_FILES = 5000
MAX_ZIP_BYTES = 200 * 1024 * 1024
MAX_ZIP_FILE_BYTES = 50 * 1024 * 1024


def _declared_total(infos: list[zipfile.ZipInfo]) -> int:
    """Sum of the sizes declared in the archive headers (attacker-controlled)."""
    return sum(info.file_size for info in infos)


def _safe_extract(zip_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_ZIP_FILES:
            raise ValueError(f"zip contains too many files: {len(infos)} > {MAX_ZIP_FILES}")
        total = _declared_total(infos)
        if total > MAX_ZIP_BYTES:
            raise ValueError(f"zip expands to too many bytes: {total} > {MAX_ZIP_BYTES}")
        # The declared sizes above are attacker-controlled; also count the bytes
        # actually written so a zip bomb cannot exceed the budget by lying.
        written_total = 0
        for info in infos:
            name = info.filename.replace("\\", "/")
            if info.flag_bits & 0x1:
                raise ValueError(f"encrypted zip member is not supported: {name}")
            if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
                raise ValueError(f"absolute zip member: {name}")
            parts = [part for part in name.split("/") if part not in {"", "."}]
            if any(part == ".." for part in parts):
                raise ValueError(f"path traversal in zip: {name}")
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError(f"symlink in zip is not supported: {name}")
            if not parts:
                continue
            target = destination.joinpath(*parts)
            try:
                target.resolve().relative_to(destination.resolve())
            except (ValueError, OSError) as exc:
                raise ValueError(f"zip member escapes destination: {name}") from exc
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if info.file_size > MAX_ZIP_FILE_BYTES:
                raise ValueError(f"zip member too large: {name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as output:
                copied = 0
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    copied += len(chunk)
                    written_total += len(chunk)
                    if copied > MAX_ZIP_FILE_BYTES:
                        raise ValueError(f"zip member exceeds the size limit: {name}")
                    if written_total > MAX_ZIP_BYTES:
                        raise ValueError(
                            f"zip expands to too many bytes (actual): {written_total} > {MAX_ZIP_BYTES}"
                        )
                    output.write(chunk)


def _zip_root(extracted: Path) -> Path:
    """Find the single capability root inside an extracted archive."""
    if detect_source_type(extracted):
        return extracted
    skill_dirs = sorted({path.parent for path in extracted.rglob("SKILL.md")})
    if len(skill_dirs) == 1:
        return skill_dirs[0]
    plugin_dirs = sorted({
        path.parent.parent if path.parent.name == ".claude-plugin" else path.parent
        for path in extracted.rglob("plugin.json")
    })
    if len(plugin_dirs) == 1:
        return plugin_dirs[0]
    children = [child for child in extracted.iterdir()]
    if len(children) == 1 and children[0].is_dir():
        return children[0]
    return extracted
