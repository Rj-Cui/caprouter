"""Workspace initialization shared by ``init`` and ``bootstrap``."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from .paths import (
    config_path,
    default_plugin_roots,
    default_skill_roots,
    plans_dir,
    registry_path,
    scratch_dir,
)
from .route import route
from .scan import (
    DEFAULT_SCAN_DEPTH,
    DEFAULT_SCAN_DIRS,
    DEFAULT_SCAN_PROJECTS,
    DEFAULT_SCAN_SECONDS,
    build_registry,
    scan_agents,
)
from .util import now_iso, write_json_atomic

DEFAULT_SCAN_CONFIG = {
    "max_depth": DEFAULT_SCAN_DEPTH,
    "max_dirs": DEFAULT_SCAN_DIRS,
    "time_budget_seconds": DEFAULT_SCAN_SECONDS,
    "max_projects": DEFAULT_SCAN_PROJECTS,
    "include_removable": False,
    "project_roots": [],
}
DEFAULT_REVIEW_CONFIG = {
    "auto_apply": False,
    "min_examples": 3,
    "min_capabilities": 1,
    "allow_medium": False,
    "retrieval": {
        "enabled": True,
        "top_k": 3,
        "min_top1_rate": 0.3,
        "min_top3_rate": 0.6,
    },
    "semantic": {
        "enabled": False,
        "adapter": "qwen",
        "capability_id": "",
        "timeout": 120,
        "max_turns": 1,
        "min_confidence": 0.75,
        "cache": True,
        "allow_unisolated": False,
    },
}


def _verified_agents() -> list[str]:
    enabled: list[str] = []
    seen: set[str] = set()
    for entry in scan_agents():
        if entry.get("status") != "verified":
            continue
        canonical = str(entry.get("canonical") or entry.get("name"))
        if canonical in seen:
            continue
        seen.add(canonical)
        enabled.append(str(entry.get("name")))
    return enabled


def refresh_registry(
    workspace: Path | str,
    config: dict[str, Any] | None = None,
    *,
    scanned_projects: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Rebuild and persist the registry from a workspace config."""
    workspace = Path(workspace)
    if config is None:
        from .util import read_json

        config = read_json(config_path(workspace), {})
    config = config if isinstance(config, dict) else {}
    skill_config = config.get("skill_roots")
    plugin_config = config.get("plugin_roots")
    skill_roots = (
        [Path(item) for item in skill_config]
        if isinstance(skill_config, list)
        else [Path(item) for item in default_skill_roots(workspace)]
    )
    plugin_roots = (
        [Path(item) for item in plugin_config]
        if isinstance(plugin_config, list)
        else [Path(item) for item in default_plugin_roots(workspace)]
    )
    scan_config = config.get("scan") if isinstance(config.get("scan"), dict) else {}
    project_roots = [Path(item) for item in (scan_config.get("project_roots") or [])]
    registry = build_registry(
        workspace,
        skill_roots=skill_roots,
        plugin_roots=plugin_roots,
        include_agents=bool(config.get("scan_agents", True)),
        project_roots=project_roots,
        scanned_projects=scanned_projects,
        scan_budget=scan_config,
        include_mcp=bool(scan_config.get("mcp_hosts", True)),
    )
    write_json_atomic(registry_path(workspace), registry)
    return registry


def create_workspace(
    workspace: Path | str,
    *,
    delivery: Path | str | None = None,
    extra_skill_roots: Iterable[Path | str] = (),
    extra_plugin_roots: Iterable[Path | str] = (),
    enabled_agents: Iterable[str] | None = None,
    include_agents: bool = True,
    include_default_roots: bool = True,
    mode: str = "bus",
) -> dict[str, Any]:
    """Create or reinitialize a workspace and build its registry."""
    workspace = Path(workspace).expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    delivery_path = Path(delivery).expanduser() if delivery else workspace / "deliverables"
    if not delivery_path.is_absolute():
        delivery_path = workspace / delivery_path
    delivery_path = delivery_path.resolve()
    delivery_path.mkdir(parents=True, exist_ok=True)

    skill_roots = (
        [Path(item).expanduser().resolve() for item in default_skill_roots(workspace)]
        if include_default_roots
        else []
    )
    for raw in extra_skill_roots:
        root = Path(raw).expanduser().resolve()
        if root.is_dir() and root not in skill_roots:
            skill_roots.append(root)
    plugin_roots = (
        [Path(item).expanduser().resolve() for item in default_plugin_roots(workspace)]
        if include_default_roots
        else []
    )
    for raw in extra_plugin_roots:
        root = Path(raw).expanduser().resolve()
        if root.is_dir() and root not in plugin_roots:
            plugin_roots.append(root)

    if enabled_agents is not None:
        agents = list(enabled_agents)
    elif include_agents:
        agents = _verified_agents()
    else:
        agents = []
    config = {
        "schema": "caprouter.config.v1",
        "mode": mode,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "workspace": str(workspace),
        "delivery": str(delivery_path),
        "skill_roots": [str(item) for item in skill_roots],
        "plugin_roots": [str(item) for item in plugin_roots],
        "enabled_agents": agents,
        "scan_agents": include_agents,
        "budgets": {
            "max_plan_calls": 5,
            "max_task_seconds": 600,
            "max_rework": 2,
        },
        "bus": {
            "allow_execute": False,
            "min_confidence": 0.18,
            "min_margin": 0.02,
            "min_coverage": 0.12,
        },
        "routing": {
            "min_score": 0.05,
            "exclude_path_contains": ["/meta/_bundles/"],
            "exclude_id_prefixes": [
                "skill:demo-recommend-",
                "skill:demo-installer",
                "skill:demo-superagent-",
            ],
        },
        "guard": {
            "allow_native_subagents": False,
            "allow_web": False,
            "write_allow": [".caprouter/plans", ".caprouter/scratch"],
        },
        "scan": {**DEFAULT_SCAN_CONFIG, "mcp_hosts": bool(include_default_roots)},
        "review": {
            **DEFAULT_REVIEW_CONFIG,
            "retrieval": dict(DEFAULT_REVIEW_CONFIG["retrieval"]),
            "semantic": dict(DEFAULT_REVIEW_CONFIG["semantic"]),
        },
    }
    write_json_atomic(config_path(workspace), config)
    for directory in (plans_dir(workspace), scratch_dir(workspace)):
        directory.mkdir(parents=True, exist_ok=True)

    registry = refresh_registry(workspace, config)

    routing = config["routing"]
    smoke = route(
        registry.get("entries") or [],
        "Research a market report and split it into retrieval and writing steps",
        top_k=3,
        capabilities=["retrieve"],
        domains=["research"],
        min_score=float(routing.get("min_score") or 0.0),
        exclude_path_contains=routing.get("exclude_path_contains") or (),
        exclude_id_prefixes=routing.get("exclude_id_prefixes") or (),
    )
    return {
        "workspace": str(workspace),
        "delivery": str(delivery_path),
        "config": str(config_path(workspace)),
        "registry": str(registry_path(workspace)),
        "stats": registry.get("stats"),
        "skill_roots": [str(item) for item in skill_roots],
        "plugin_roots": [str(item) for item in plugin_roots],
        "enabled_agents": agents,
        "smoke_route": smoke,
    }
