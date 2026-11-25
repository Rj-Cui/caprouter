"""Shared helpers for the CapRouter test suite (stdlib only)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for _path in (ROOT, HERE):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

ENV_KEYS = ("USERPROFILE", "HOME", "APPDATA", "CAPROUTER_STATE", "CAPROUTER_AUDIT")


@contextmanager
def isolated_env(home: Path) -> Iterator[dict[str, str]]:
    """Point every user-level path at *home* so tests never touch the real profile."""
    home = Path(home)
    (home / "AppData" / "Roaming").mkdir(parents=True, exist_ok=True)
    values = {
        "USERPROFILE": str(home),
        "HOME": str(home),
        "APPDATA": str(home / "AppData" / "Roaming"),
        "CAPROUTER_STATE": str(home / "state.json"),
        "CAPROUTER_AUDIT": str(home / "audit.jsonl"),
    }
    saved = {key: os.environ.get(key) for key in ENV_KEYS}
    os.environ.update(values)
    try:
        yield values
    finally:
        for key, previous in saved.items():
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous


def enter_isolated_env(case: Any, home: Path) -> None:
    """Activate :func:`isolated_env` for a ``unittest.TestCase`` lifetime."""
    ctx = isolated_env(Path(home))
    ctx.__enter__()
    case.addCleanup(ctx.__exit__, None, None, None)


def write_skill(
    root: Path,
    name: str = "pdf-tool",
    description: str = "Convert PDF files to Word documents with layout retention.",
    keywords: str = "pdf, convert, word",
    capabilities: str = "transform",
) -> Path:
    directory = Path(root) / name
    directory.mkdir(parents=True, exist_ok=True)
    body = (
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        f"keywords: [{keywords}]\n"
        f"capabilities: [{capabilities}]\n"
        "---\n"
        f"# {name}\n\n1. read the input\n2. write the artifact\n"
    )
    (directory / "SKILL.md").write_text(body, encoding="utf-8")
    return directory


def make_workspace(
    tmp: Path,
    *,
    skills: Path | None = None,
    plugins: Path | None = None,
) -> Path:
    """Create a workspace without scanning the machine or calling any binary."""
    from caprouter.workspace import create_workspace

    workspace = Path(tmp) / "ws"
    extra_skills = [skills] if skills else []
    extra_plugins = [plugins] if plugins else []
    create_workspace(
        workspace,
        extra_skill_roots=extra_skills,
        extra_plugin_roots=extra_plugins,
        include_default_roots=False,
        include_agents=False,
    )
    return workspace


def read_config(workspace: Path) -> dict[str, Any]:
    return json.loads((Path(workspace) / ".caprouter" / "config.json").read_text(encoding="utf-8"))


def write_config(workspace: Path, config: dict[str, Any]) -> None:
    (Path(workspace) / ".caprouter" / "config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def write_pipeline(workspace: Path, pipeline_id: str, steps: list[dict[str, Any]], goal: str = "") -> Path:
    from caprouter.pipelines import pipelines_dir

    directory = pipelines_dir(workspace)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{pipeline_id}.json"
    path.write_text(
        json.dumps(
            {
                "schema": "caprouter.pipeline.v1",
                "id": pipeline_id,
                "goal": goal or pipeline_id,
                "steps": steps,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def registry_ids(workspace: Path) -> set[str]:
    data = json.loads((Path(workspace) / ".caprouter" / "registry.json").read_text(encoding="utf-8"))
    return {str(entry.get("id")) for entry in data.get("entries") or []}


def run_cli(args: list[str], *, home: Path, cwd: Path | None = None, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    """Run the CLI as a subprocess exactly like a user would."""
    env = dict(os.environ)
    env.update(
        {
            "USERPROFILE": str(home),
            "HOME": str(home),
            "APPDATA": str(home / "AppData" / "Roaming"),
            "CAPROUTER_STATE": str(home / "state.json"),
            "CAPROUTER_AUDIT": str(home / "audit.jsonl"),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        }
    )
    return subprocess.run(
        [sys.executable, "-m", "caprouter", *args],
        cwd=str(cwd or ROOT),
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
