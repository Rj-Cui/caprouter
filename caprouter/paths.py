from __future__ import annotations

import os
from pathlib import Path

CONFIG_DIR = ".caprouter"


def find_workspace(start: Path | str) -> Path | None:
    """Walk up from *start* until a CapRouter workspace is found."""
    current = Path(start).expanduser()
    try:
        current = current.resolve()
    except OSError:
        pass
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / CONFIG_DIR / "config.json").is_file():
            return candidate
    return None


def find_workspace_or_plugin(start: Path | str) -> Path | None:
    """Find a workspace above *start*, then fall back to the plugin's own workspace."""
    found = find_workspace(start)
    if found is not None:
        return found
    plugin_workspace = Path(__file__).resolve().parent.parent / "workspace"
    if (plugin_workspace / CONFIG_DIR / "config.json").is_file():
        return plugin_workspace.resolve()
    return None


def find_workspace_near_plugin(start: Path | str) -> Path | None:
    """Like find_workspace, but only fall back when *start* is inside the plugin repo."""
    found = find_workspace(start)
    if found is not None:
        return found
    plugin_root = Path(__file__).resolve().parent.parent
    try:
        Path(start).expanduser().resolve().relative_to(plugin_root.resolve())
    except (OSError, ValueError):
        return None
    plugin_workspace = plugin_root / "workspace"
    if (plugin_workspace / CONFIG_DIR / "config.json").is_file():
        return plugin_workspace.resolve()
    return None


def config_path(workspace: Path | str) -> Path:
    return Path(workspace) / CONFIG_DIR / "config.json"


def registry_path(workspace: Path | str) -> Path:
    return Path(workspace) / CONFIG_DIR / "registry.json"


def plans_dir(workspace: Path | str) -> Path:
    return Path(workspace) / CONFIG_DIR / "plans"


def runs_dir(workspace: Path | str) -> Path:
    return Path(workspace) / CONFIG_DIR / "runs"


def scratch_dir(workspace: Path | str) -> Path:
    return Path(workspace) / CONFIG_DIR / "scratch"


def _existing(candidates: list[Path | None]) -> list[Path]:
    seen: set[str] = set()
    found: list[Path] = []
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            path = Path(candidate).expanduser()
        except (TypeError, ValueError):
            continue
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        if path.is_dir():
            found.append(path)
    return found


def installed_root(workspace: Path | str) -> Path:
    return Path(workspace) / CONFIG_DIR / "installed"


def builtin_skill_root() -> Path | None:
    """Skills shipped with CapRouter itself; present for every new user."""
    root = Path(__file__).resolve().parent.parent / "skills"
    return root if root.is_dir() else None


def cherry_skill_roots() -> list[Path]:
    home = Path.home()
    appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
    return [
        appdata / "CherryStudio" / "Data" / "Skills",
        home / "Library" / "Application Support" / "CherryStudio" / "Data" / "Skills",
        home / ".config" / "CherryStudio" / "Data" / "Skills",
    ]


def host_skill_roots(workspace: Path | str | None = None) -> list[Path]:
    """Common per-host skill directories. Missing paths are filtered later."""
    home = Path.home()
    roots: list[Path] = []
    if workspace:
        ws = Path(workspace)
        roots.extend(
            [
                ws / ".claude" / "skills",
                ws / ".agents" / "skills",
                ws / ".cursor" / "skills",
                ws / ".codex" / "skills",
                ws / ".gemini" / "skills",
                ws / ".github" / "skills",
            ]
        )
    roots.extend(
        [
            home / ".claude" / "skills",
            home / ".agents" / "skills",
            home / ".codex" / "skills",
            home / ".gemini" / "skills",
            home / ".cursor" / "skills",
            home / ".copilot" / "skills",
        ]
    )
    roots.extend(cherry_skill_roots())
    return roots


def default_skill_roots(workspace: Path | str | None = None) -> list[Path]:
    """Portable default roots. Extra machine-specific roots belong in config."""
    candidates: list[Path | None] = [
        installed_root(workspace) / "skills" if workspace else None,
        builtin_skill_root(),
    ]
    candidates.extend(host_skill_roots(workspace))
    return _existing(candidates)


def default_plugin_roots(workspace: Path | str | None = None) -> list[Path]:
    """Portable default roots. Extra machine-specific roots belong in config."""
    home = Path.home()
    candidates: list[Path | None] = [
        installed_root(workspace) / "plugins" if workspace else None,
        home / ".claude" / "plugins",
        home / ".codex" / "plugins",
    ]
    return _existing(candidates)
