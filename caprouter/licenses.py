"""Small license-file heuristics shared by the installer and remote fetch.

Kept dependency-free so neither caller has to import the other.
"""
from __future__ import annotations

from pathlib import Path


def _license_info(files: list[tuple[Path, str]]) -> dict[str, object]:
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
