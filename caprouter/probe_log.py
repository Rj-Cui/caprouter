"""Append-only probe history access, shared by the bus and the probe runner.

Keeping this module free of imports from ``probes``/``capabilities`` breaks the
import cycle: both sides depend downward on it instead of on each other.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .util import read_jsonl


def probes_path(workspace: Path | str) -> Path:
    return Path(workspace) / ".caprouter" / "probes.jsonl"


def load_probe_rows(workspace: Path | str) -> list[dict[str, Any]]:
    return read_jsonl(probes_path(workspace))
