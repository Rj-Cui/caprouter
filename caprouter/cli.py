from __future__ import annotations

"""CapRouter command line interface.

Exit codes:
    0   success (a preview or an "already up to date" no-op counts as success)
    1   the operation failed, or the requested state is invalid
    2   usage error, or the operation needs an explicit flag to proceed
        (``--approve``, ``--yes``); nothing was changed
    3   CapRouter is disabled globally (``caprouter state --disable``)
"""

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Sequence

from . import __version__
from .adopt import adopt_capability, backup_capability, list_unmanaged
from .audit import audit_path, set_audit_workspace
from .backup import create_backup, list_backups, restore_backup
from .capabilities import (
    get_capability,
    invoke_capability,
    list_gaps,
    record_gap,
    record_outcome,
    search_capabilities,
    verify_artifact,
)
from .cards import (
    apply_draft,
    generate_draft,
    generate_project_drafts,
    generate_registry_drafts,
    write_draft,
)
from .claims import verify_claims
from .dashboard import build_dashboard
from .deps import check_dependencies, plan_dependencies
from .dispatch import run_plan
from .duplicates import find_duplicates, prefer_duplicate
from .eval import DEFAULT_CASES, evaluate, load_cases
from .health import capability_health
from .hostaware import normalize_host, unlink_managed_skill
from .lockfile import check_drift, write_lock
from .maintenance import clean_trash, prune_logs, trash_entries
from .setup_hosts import HOSTS, setup_host
from .executor import ExecuteOptions, execute_plan
from .host import run_host
from .install import install_source, list_installed, preview_install
from .lifecycle import (
    list_versions,
    rollback_capability,
    rollback_to_version,
    uninstall_capability,
    update_source,
)
from .mcp_inventory import inventory_summary
from .mcp_server import resolve_workspace, serve as serve_mcp
from .migrate import export_bundle, import_bundle, list_migrated, remap_migrated
from .pipelines import (
    list_pipelines,
    plan_pipeline,
    record_pipeline_outcome,
    run_pipeline,
    verify_pipeline,
)
from .probes import probe_report, record_probe, run_all_probes, run_probes
from .remote import (
    clean_quarantine,
    discover_skills,
    fetch_skill,
    fetch_url,
    list_quarantine,
    vendor_skill,
)
from .review import review_drafts
from .paths import config_path, registry_path
from .plan import load_plan, validate_plan
from .route import route
from .scan import (
    default_scan_roots,
    lint_skill,
    load_projects_scan,
    scan_project_roots,
    summarize_scan,
    summary_size,
    write_projects_scan,
)
from .state import is_enabled, read_state, state_path, write_state
from .status import collect_status, tail_audit
from .synthesize import (
    approve_synthesis,
    improve_synthesis,
    list_syntheses,
    propose_synthesis,
    refine_candidate,
)
from .util import hash_tree, now_iso, read_json, write_json_atomic
from .workspace import DEFAULT_SCAN_CONFIG, create_workspace, refresh_registry

# Single implementation lives in cli_common; these aliases keep the call sites
# in this module unchanged while removing the duplicate definitions.
from .cli_common import (  # noqa: E402  (imports below the stdlib/lib block on purpose)
    EXIT_DISABLED,
    EXIT_FAILED,
    EXIT_NEEDS_FLAG,
    EXIT_OK,
    configure_console,
)
from .cli_common import ask as _ask
from .cli_common import capability_workspace as _capability_workspace
from .cli_common import fail as _fail
from .cli_common import load_config as _load_config
from .cli_common import print_json as _print_json
from .cli_common import registry_entries as _registry_entries
from .cli_common import workspace_from_args as _workspace_from_args

configure_console(sys.stdout)
configure_console(sys.stderr)


def _write_registry(
    workspace: Path,
    config: dict[str, Any],
    *,
    scanned_projects: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return refresh_registry(workspace, config, scanned_projects=scanned_projects)


def cmd_init(args: argparse.Namespace) -> int:
    if args.workspace:
        workspace = Path(args.workspace).expanduser().resolve()
    elif args.yes or not sys.stdin.isatty():
        workspace = Path.cwd().resolve()
    else:
        workspace = Path(_ask("Workspace path", str(Path.cwd()), assume_yes=False)).expanduser().resolve()
    existing = read_json(config_path(workspace))
    if isinstance(existing, dict) and not args.force:
        print(f"CapRouter already initialized at {workspace}")
        print(f"config: {config_path(workspace)}")
        print("Run 'caprouter scan' to refresh, or add --force to reinitialize.")
        return 0

    default_delivery = str(workspace / "deliverables")
    delivery_text = _ask("Delivery directory", args.delivery or default_delivery, assume_yes=args.yes)
    delivery = Path(delivery_text).expanduser()
    if not delivery.is_absolute():
        delivery = workspace / delivery

    summary = create_workspace(
        workspace,
        delivery=delivery,
        extra_skill_roots=args.root or [],
        extra_plugin_roots=args.plugin_root or [],
        enabled_agents=args.agent or None,
        include_agents=not args.no_agents,
        mode=args.mode,
    )
    if args.mode == "orchestrator":
        print(
            "warning: mode=orchestrator is a compatibility path; new workspaces should use mode=bus.",
            file=sys.stderr,
        )
    if args.json:
        _print_json(summary)
    else:
        smoke = summary.get("smoke_route") or {}
        print(f"CapRouter initialized: {workspace}")
        print(f"  delivery: {summary.get('delivery')}")
        print(f"  registry: {summary.get('registry')}")
        print(f"  entries:  {summary.get('stats')}")
        print(f"  agents:   {', '.join(summary.get('enabled_agents') or []) or 'none detected'}")
        print("  smoke route:")
        for item in smoke.get("results") or []:
            print(f"    {item.get('score'):.3f}  {item.get('type'):9}  {item.get('id')}")
        if not smoke.get("results"):
            print("    (no matches; check skill roots)")
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    workspace = _workspace_from_args(args)
    config = _load_config(workspace)
    registry = _write_registry(workspace, config)
    if args.json:
        _print_json(registry.get("stats"))
    else:
        print(f"Scanned {workspace}")
        print(json.dumps(registry.get("stats"), ensure_ascii=False, indent=2))
        if registry.get("duplicates"):
            print(f"duplicates: {len(registry['duplicates'])} (kept first by root priority)")
    return 0


def _scan_settings(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    """CLI values override config; config overrides built-in defaults."""
    values = config.get("scan") if isinstance(config.get("scan"), dict) else {}

    def pick(attribute: str, key: str, default: Any) -> Any:
        value = getattr(args, attribute, None)
        return values.get(key, default) if value is None else value

    include_removable = getattr(args, "include_removable", None)
    return {
        "max_depth": int(pick("max_depth", "max_depth", DEFAULT_SCAN_CONFIG["max_depth"])),
        "max_dirs": int(pick("max_dirs", "max_dirs", DEFAULT_SCAN_CONFIG["max_dirs"])),
        "time_budget_seconds": float(
            pick("time_budget", "time_budget_seconds", DEFAULT_SCAN_CONFIG["time_budget_seconds"])
        ),
        "max_projects": int(pick("max_projects", "max_projects", DEFAULT_SCAN_CONFIG["max_projects"])),
        "include_removable": bool(
            include_removable
            if include_removable is not None
            else values.get("include_removable", DEFAULT_SCAN_CONFIG["include_removable"])
        ),
    }


def _save_project_roots(workspace: Path, config: dict[str, Any], roots: list[str]) -> dict[str, Any]:
    scan_config = config.get("scan") if isinstance(config.get("scan"), dict) else {}
    scan_config = {**DEFAULT_SCAN_CONFIG, **scan_config}
    existing = [str(Path(item)) for item in (scan_config.get("project_roots") or [])]
    for raw in roots:
        resolved = str(Path(raw).expanduser().resolve())
        if resolved not in existing and Path(resolved).is_dir():
            existing.append(resolved)
    scan_config["project_roots"] = existing
    config["scan"] = scan_config
    config["updated_at"] = now_iso()
    write_json_atomic(config_path(workspace), config)
    return config


def _save_extra_roots(
    workspace: Path,
    config: dict[str, Any],
    skill_roots: list[str],
    plugin_roots: list[str],
) -> dict[str, Any]:
    changed = False
    for key, roots in (("skill_roots", skill_roots), ("plugin_roots", plugin_roots)):
        existing = [str(Path(item)) for item in (config.get(key) or [])]
        for raw in roots:
            resolved = str(Path(raw).expanduser().resolve())
            if resolved not in existing and Path(resolved).is_dir():
                existing.append(resolved)
                changed = True
        config[key] = existing
    if changed:
        config["updated_at"] = now_iso()
        write_json_atomic(config_path(workspace), config)
    return config


def _print_scan_summary(summary: dict[str, Any], *, project_list: Path | None = None) -> None:
    stats = summary.get("stats") or {}
    print(
        f"scan: roots={stats.get('roots')} dirs={stats.get('dirs_scanned')} "
        f"projects={stats.get('projects')} skipped={stats.get('skipped_dirs')} "
        f"elapsed={stats.get('elapsed_seconds')}s"
    )
    for item in summary.get("roots") or []:
        reason = f"  truncated={item.get('reason')}" if item.get("reason") else ""
        print(
            f"  {item.get('drive') or '?':3} {item.get('kind'):7} {item.get('type'):12} "
            f"{item.get('root')}{reason}"
        )
    if stats.get("by_type"):
        print(f"by type: {stats.get('by_type')}")
    if stats.get("by_drive"):
        print(f"by drive: {stats.get('by_drive')}")
    if stats.get("depth_limited_dirs"):
        print(f"depth-limited dirs (not scanned below max_depth): {stats.get('depth_limited_dirs')}")
    for item in summary.get("truncated") or []:
        print(f"  TRUNCATED {item.get('root')} reason={item.get('reason')} dirs={item.get('dirs_scanned')}")
    for item in summary.get("warnings") or []:
        print(f"  WARN {item}")
    if project_list:
        print(f"list: {project_list}")
    print(f"summary: {summary_size(summary)} bytes")


def cmd_scan_projects(args: argparse.Namespace) -> int:
    workspace = _workspace_from_args(args)
    config = read_json(config_path(workspace), {})
    config = config if isinstance(config, dict) else {}
    settings = _scan_settings(args, config)
    result = scan_project_roots(
        None,
        drives=args.drive or None,
        project_roots=args.project_root or None,
        **settings,
    )
    project_list = None
    if not args.no_write:
        project_list = write_projects_scan(workspace, result)
    if args.save_roots and args.project_root and not args.no_write:
        _load_config(workspace)
        _save_project_roots(workspace, config, args.project_root)
    if args.merge_registry:
        config = _load_config(workspace)
        registry = _write_registry(workspace, config, scanned_projects=result.get("projects"))
        result["registry_stats"] = registry.get("stats")
    summary = summarize_scan(result, recent=args.recent)
    if args.json:
        payload = dict(summary)
        payload["project_list"] = str(project_list) if project_list else None
        payload["summary_bytes"] = summary_size(summary)
        payload["registry_stats"] = result.get("registry_stats")
        _print_json(payload)
    else:
        _print_scan_summary(summary, project_list=project_list)
        if result.get("registry_stats"):
            print(f"registry: {result['registry_stats']}")
        if summary.get("recent_projects"):
            print("recent:")
            for item in summary["recent_projects"]:
                print(f"  {item.get('type'):8} {item.get('name')}  {item.get('path')}")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    workspace = _capability_workspace(args)
    report = review_drafts(
        workspace,
        ids=args.id or None,
        semantic=bool(args.semantic or args.fix),
        semantic_adapter=args.agent,
        auto_apply=args.auto_apply,
        dry_run=bool(args.dry_run),
        force=bool(args.force),
        limit=args.limit,
        repair=bool(args.fix),
    )
    if args.json:
        _print_json(report)
    else:
        counts = report.get("counts") or {}
        print(
            f"review: total={report.get('total')} "
            + " ".join(f"{key}={value}" for key, value in counts.items())
            + f" applied={report.get('applied')} skipped={report.get('skipped')} "
            + f"repaired={report.get('repaired')} semantic_calls={report.get('semantic_calls')}"
        )
        for item in report.get("items") or []:
            print(f"  {item.get('status'):13} {item.get('risk'):6} {item.get('score'):.3f}  {item.get('id')}")
            for issue in item.get("issues") or []:
                print(f"       {issue.get('status')}: {issue.get('id')} {issue.get('message')}")
        if report.get("report_path"):
            print(f"report: {report['report_path']}")
    return 0


def cmd_migrate_export(args: argparse.Namespace) -> int:
    workspace = _capability_workspace(args)
    try:
        report = export_bundle(workspace, args.out)
    except ValueError as exc:
        return _fail(exc)
    if args.json:
        _print_json(report)
    else:
        print(f"migration bundle: {report.get('bundle')}")
        print(f"  id:     {report.get('migration_id')}")
        print(f"  bytes:  {report.get('bytes')}")
        print(f"  counts: {report.get('counts')}")
    return 0


def cmd_migrate_drafts(args: argparse.Namespace) -> int:
    workspace = _workspace_from_args(args)
    result = list_migrated(workspace)
    if args.json:
        _print_json(result)
        return 0
    print(f"migrated drafts: {result['count']}")
    for item in result["entries"]:
        print(
            f"  {str(item.get('id')):36} {str(item.get('type') or ''):7} "
            f"{item.get('status')} hints={item.get('path_hint')}"
        )
    return 0


def cmd_migrate_remap(args: argparse.Namespace) -> int:
    workspace = _workspace_from_args(args)
    try:
        result = remap_migrated(
            workspace,
            args.id,
            args.path,
            name=args.name,
            apply=args.apply,
            force=args.force,
        )
    except ValueError as exc:
        return _fail(exc)
    if args.json:
        _print_json(result)
    else:
        print(f"{result['status']}: {result['id']}")
        print(f"  draft: {result['draft_path']}")
    return 0 if result["status"] in {"remapped", "applied", "needs_review"} else 1


def cmd_migrate_import(args: argparse.Namespace) -> int:
    workspace = _capability_workspace(args)
    try:
        report = import_bundle(
            workspace,
            args.bundle,
            apply_matched=not args.no_apply,
            scan=not args.no_scan,
            dry_run=bool(args.dry_run),
            force=bool(args.force),
        )
    except ValueError as exc:
        return _fail(exc)
    if args.json:
        _print_json(report)
    else:
        print(
            f"migration import: matched={report.get('matched')} unmatched={report.get('unmatched')} "
            f"applied={report.get('applied')} kept={report.get('kept_existing')} "
            f"unsafe={report.get('unsafe_not_applied')} drafts={len(report.get('drafts_written') or [])}"
        )
        print(f"  transferred: {report.get('transferred')}")
        for item in report.get("items") or []:
            print(
                f"  {item.get('method'):13} {item.get('old_id')} -> {item.get('new_id') or 'UNMATCHED'}"
            )
    return 0


def cmd_bootstrap(args: argparse.Namespace) -> int:
    """Scan -> collect -> merge registry -> batch draft -> review -> static probe."""
    if args.dry_run and args.workspace:
        workspace = Path(args.workspace).expanduser().resolve()
    else:
        workspace = _workspace_from_args(args)
    if not args.dry_run:
        workspace.mkdir(parents=True, exist_ok=True)
    initialized = config_path(workspace).is_file()
    if not initialized and not args.dry_run:
        create_workspace(
            workspace,
            extra_skill_roots=getattr(args, "skill_root", None) or [],
            extra_plugin_roots=getattr(args, "plugin_root", None) or [],
            enabled_agents=args.agent or None,
            include_agents=not args.no_agents,
            include_default_roots=not args.no_default_skills,
            mode="bus",
        )
        initialized = True
    config = read_json(config_path(workspace), {})
    config = config if isinstance(config, dict) else {}
    if getattr(args, "skill_root", None) or getattr(args, "plugin_root", None):
        config = _save_extra_roots(
            workspace,
            config,
            getattr(args, "skill_root", None) or [],
            getattr(args, "plugin_root", None) or [],
        )
    settings = _scan_settings(args, config)
    if args.project_root and not args.dry_run and initialized:
        config = _save_project_roots(workspace, config, args.project_root)

    scan_block = config.get("scan") if isinstance(config.get("scan"), dict) else {}
    configured_roots = [
        str(Path(item).expanduser().resolve())
        for item in (scan_block.get("project_roots") or [])
        if str(item).strip()
    ]
    if args.project_root:
        # Targeted mode: scan exactly the roots the user named.
        project_roots = list(args.project_root)
        drives = args.drive or None
    else:
        project_roots = configured_roots
        drives = args.drive or [str(item) for item in default_scan_roots(settings["include_removable"])]
    if not project_roots and not drives:
        print(
            "warning: no default scan roots on this OS; pass --project-root <dir> "
            "(or --drive <letter> on Windows).",
            file=sys.stderr,
        )
    scan_result = scan_project_roots(
        None,
        drives=drives or None,
        project_roots=project_roots or None,
        **settings,
    )
    summary = summarize_scan(scan_result, recent=args.recent)
    scan_file = None
    registry_stats = None
    draft_report = None
    registry_draft_report = None
    review_report = None
    probe_result = None

    if not args.dry_run:
        scan_file = write_projects_scan(workspace, scan_result)
        if initialized:
            registry = _write_registry(workspace, config, scanned_projects=scan_result.get("projects"))
            registry_stats = registry.get("stats")
        draft_types = {args.type} if args.type else None
        draft_report = generate_project_drafts(
            workspace,
            scan_result.get("projects") or [],
            types=draft_types,
            force=args.force,
        )
        registry_draft_report = None
        if getattr(args, "draft_registry", False):
            registry_types = (
                {args.type} if args.type in {"skill", "plugin"} else {"skill", "plugin"}
            )
            registry_draft_report = generate_registry_drafts(
                workspace,
                _registry_entries(workspace),
                types=registry_types,
                force=args.force,
            )
        if not args.no_review:
            review_report = review_drafts(
                workspace,
                semantic=bool(args.review_semantic),
                auto_apply=args.auto_apply,
                limit=args.review_limit,
            )
        if not args.no_probe:
            probe_result = run_all_probes(workspace)

    report = {
        "schema": "caprouter.bootstrap.v1",
        "generated_at": now_iso(),
        "workspace": str(workspace),
        "dry_run": bool(args.dry_run),
        "initialized": initialized,
        "scan": summary,
        "project_list": str(scan_file) if scan_file else None,
        "summary_bytes": summary_size(summary),
        "registry": registry_stats,
        "drafts": (
            {
                key: draft_report.get(key)
                for key in ("scanned", "drafted", "unchanged", "skipped", "needs_review", "applied")
            }
            if draft_report
            else None
        ),
        "registry_drafts": (
            {
                key: registry_draft_report.get(key)
                for key in ("scanned", "drafted", "unchanged", "skipped", "needs_review", "applied")
            }
            if registry_draft_report
            else None
        ),
        "drafts_dir": str(Path(workspace) / ".caprouter" / "drafts" / "projects"),
        "review": (
            {
                "total": review_report.get("total"),
                "counts": review_report.get("counts"),
                "by_risk": review_report.get("by_risk"),
                "applied": review_report.get("applied"),
            }
            if review_report
            else None
        ),
        "probes": (probe_result or {}).get("counts") if probe_result else None,
        "next": [
            "review drafts, then apply with: caprouter capability draft --all --apply",
            "run a real task and record capability outcome --human-edit-minutes",
        ],
    }
    if args.json:
        _print_json(report)
    else:
        print(f"bootstrap: {workspace}  dry_run={bool(args.dry_run)}  initialized={initialized}")
        _print_scan_summary(summary, project_list=scan_file)
        if registry_stats:
            print(f"registry: {registry_stats}")
        if draft_report:
            print(
                "drafts: "
                f"drafted={draft_report.get('drafted')} unchanged={draft_report.get('unchanged')} "
                f"skipped={draft_report.get('skipped')} needs_review={draft_report.get('needs_review')} "
                f"applied=0"
            )
            print(f"drafts dir: {report['drafts_dir']}")
        if review_report:
            print(
                "review: "
                f"total={review_report.get('total')} counts={review_report.get('counts')} "
                f"risk={review_report.get('by_risk')} applied={review_report.get('applied')}"
            )
        if probe_result:
            print(f"probes: {probe_result.get('counts')}")
        if args.dry_run:
            print("dry-run: no files written, no drafts applied, no probes recorded")
        else:
            print("next: review drafts, then: caprouter capability draft --all --apply")
    return 0


def cmd_route(args: argparse.Namespace) -> int:
    workspace = _workspace_from_args(args)
    entries = _registry_entries(workspace)
    if not entries:
        raise SystemExit(f"registry is empty or missing in {workspace}; run: caprouter scan")
    config = read_json(config_path(workspace), {})
    policy = config.get("routing") if isinstance(config, dict) else {}
    policy = policy if isinstance(policy, dict) else {}
    result = route(
        entries,
        args.need,
        top_k=args.top,
        types=args.type,
        capabilities=args.capability,
        domains=args.domain,
        min_score=(
            args.min_score
            if args.min_score is not None
            else float(policy.get("min_score") or 0.0)
        ),
        exclude_path_contains=policy.get("exclude_path_contains") or (),
        exclude_id_prefixes=policy.get("exclude_id_prefixes") or (),
    )
    if args.json:
        _print_json(result)
    else:
        print(f"need: {args.need}")
        for item in result.get("results") or []:
            print(f"{item.get('score'):.3f}  {item.get('type'):9}  {item.get('id')}")
            print(f"       {item.get('path')}")
        if not result.get("results"):
            print("(no matches)")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    workspace = _workspace_from_args(args)
    problems: list[str] = []
    warnings: list[str] = []
    state = read_state()
    if not state.get("enabled", True):
        warnings.append("CapRouter is disabled (tray switch or caprouter state --disable)")
    config = read_json(config_path(workspace))
    if not isinstance(config, dict):
        _print_json({"ok": False, "problems": [f"not initialized: {config_path(workspace)}"]})
        return 1

    delivery_text = str(config.get("delivery") or "").strip()
    if not delivery_text:
        warnings.append("config has no delivery directory; run: caprouter init --workspace <ws>")
        delivery = workspace / "deliverables"
    else:
        delivery = Path(delivery_text)
        if not delivery.is_dir():
            problems.append(f"delivery directory missing: {delivery}")
    registry_file = registry_path(workspace)
    registry = read_json(registry_file, {})
    if not isinstance(registry, dict):
        problems.append(f"registry missing: {registry_file}")
        registry = {}
    entries = [item for item in (registry.get("entries") or []) if isinstance(item, dict)]
    if not entries:
        problems.append("registry has no entries")

    stale: list[str] = []
    missing: list[str] = []
    if args.hash:
        for entry in entries:
            path = Path(str(entry.get("path") or ""))
            if not path.exists():
                missing.append(str(entry.get("id")))
                continue
            if entry.get("type") in {"skill", "plugin"} and entry.get("status") == "verified":
                current = hash_tree(path)
                if current != entry.get("hash"):
                    stale.append(str(entry.get("id")))

    enabled = config.get("enabled_agents") or []
    unavailable = [name for name in enabled if not shutil.which(str(name))]

    if missing:
        problems.append(f"missing paths: {', '.join(missing[:10])}")
    if unavailable:
        problems.append(f"enabled agents not on PATH: {', '.join(unavailable)}")
    if stale:
        warnings.append(f"stale hashes (rerun scan): {', '.join(stale[:10])}")

    plugin_root = Path(__file__).resolve().parent.parent
    manifest = plugin_root / ".claude-plugin" / "plugin.json"
    plugin_mode = manifest.exists()
    plugin_info: dict[str, Any] = {
        "root": str(plugin_root),
        "mode": "plugin" if plugin_mode else "package",
        "manifest": "ok" if plugin_mode else "n/a",
        "hooks": "n/a",
        "mcp": "n/a",
        "skill_bytes": None,
    }
    if plugin_mode:
        if isinstance(read_json(manifest), dict):
            plugin_info["manifest"] = "ok"
        else:
            plugin_info["manifest"] = "invalid"
            problems.append(f"invalid plugin manifest: {manifest}")

        hooks_file = plugin_root / "hooks" / "hooks.json"
        if hooks_file.exists():
            if isinstance(read_json(hooks_file), dict):
                plugin_info["hooks"] = "ok"
            else:
                plugin_info["hooks"] = "invalid"
                problems.append(f"invalid hooks file: {hooks_file}")
        else:
            warnings.append(f"hooks file missing: {hooks_file}")

        mcp_file = plugin_root / ".mcp.json"
        if mcp_file.exists():
            mcp_data = read_json(mcp_file)
            if isinstance(mcp_data, dict) and isinstance(mcp_data.get("mcpServers"), dict):
                plugin_info["mcp"] = "ok"
            else:
                plugin_info["mcp"] = "invalid"
                problems.append(f"invalid MCP config: {mcp_file}")
        else:
            warnings.append(f"MCP config missing: {mcp_file}")

        skill_file = plugin_root / "skills" / "caprouter" / "SKILL.md"
        if skill_file.exists():
            plugin_info["skill_bytes"] = skill_file.stat().st_size
            skill_lint = lint_skill(skill_file)
            plugin_info["skill_lint"] = skill_lint
            problems.extend(f"skill lint: {item}" for item in skill_lint["errors"])
            warnings.extend(f"skill lint: {item}" for item in skill_lint["warnings"])
        else:
            warnings.append(f"skill entry missing: {skill_file}")

    result = {
        "schema": "caprouter.doctor.v1",
        "ok": not problems,
        "workspace": str(workspace),
        "mode": config.get("mode") or "orchestrator",
        "delivery": str(delivery),
        "entries": len(entries),
        "stats": registry.get("stats"),
        "state": {"enabled": state.get("enabled", True), "path": str(state_path())},
        "plugin": plugin_info,
        "audit": str(audit_path()),
        "duplicates": len(registry.get("duplicates") or []),
        "stale": stale,
        "missing": missing,
        "unavailable_agents": unavailable,
        "problems": problems,
        "warnings": warnings,
    }
    if args.json:
        _print_json(result)
    else:
        print(f"workspace: {workspace}")
        print(f"mode:      {result['mode']}")
        print(f"delivery:  {delivery}")
        print(f"entries:   {len(entries)}  {registry.get('stats') or {}}")
        skill_bytes = plugin_info.get("skill_bytes")
        skill_display = f"{skill_bytes}B" if skill_bytes else "n/a"
        print(
            f"plugin:    {plugin_info['mode']}  manifest={plugin_info['manifest']}"
            f"  hooks={plugin_info['hooks']}  mcp={plugin_info['mcp']}  skill={skill_display}"
        )
        print(f"audit:     {audit_path()}")
        print(f"stale:     {len(stale)}")
        print(f"problems:  {len(problems)}")
        for problem in problems:
            print(f"  FAIL  {problem}")
        for warning in warnings:
            print(f"  WARN  {warning}")
        print("OK" if not problems else "NOT OK")
    return 0 if not problems else 1


def cmd_dashboard(args: argparse.Namespace) -> int:
    workspace = _workspace_from_args(args)
    result = build_dashboard(workspace, out=args.out)
    if args.json:
        _print_json(result)
    else:
        print(f"dashboard: {result['path']}")
        print(f"  registry={result['registry']} health={result['health']} duplicates={result['duplicates']}")
    return 0


def cmd_prune(args: argparse.Namespace) -> int:
    workspace = _workspace_from_args(args)
    kinds = [
        kind
        for kind, enabled in (
            ("probes", args.probes),
            ("outcomes", args.outcomes),
            ("discovery", args.discovery),
            ("gaps", args.gaps),
            ("uninstalled", args.uninstalled),
            ("updates", args.updates),
            ("audit", args.audit),
        )
        if enabled
    ]
    if args.all:
        kinds = ["probes", "outcomes", "discovery", "gaps", "uninstalled", "updates", "audit"]
    if not kinds:
        print("select --all or at least one log kind", file=sys.stderr)
        return 2
    results = [
        prune_logs(workspace, kind=kind, keep=args.keep, dry_run=args.dry_run)
        for kind in kinds
    ]
    if args.json:
        _print_json({"schema": "caprouter.prune.v1", "results": results})
    else:
        for result in results:
            print(
                f"  {result['kind']:11} {result['status']:12} "
                f"{result['before']} -> {result['after']}  {result['path']}"
            )
    return 0


def cmd_trash(args: argparse.Namespace) -> int:
    workspace = _workspace_from_args(args)
    if args.clean:
        result = clean_trash(
            workspace,
            older_than_days=args.older_than_days,
            dry_run=not args.yes,
        )
    else:
        rows = trash_entries(workspace)
        result = {
            "schema": "caprouter.trash.v1",
            "status": "listed",
            "count": len(rows),
            "items": rows,
        }
    if args.json:
        _print_json(result)
        if args.clean and result.get("status") == "preview":
            print("preview only; pass --yes to delete", file=sys.stderr)
            return EXIT_NEEDS_FLAG
        return 0
    if args.clean:
        print(
            f"trash {result['status']}: {result['count']}/{result['total']} "
            f"older than {result['older_than_days']}d"
        )
        for item in result["items"]:
            print(f"  {item['name']}  age={item['age_days']}d")
        if result["status"] == "preview":
            print("preview only; pass --yes to delete", file=sys.stderr)
            return EXIT_NEEDS_FLAG
        return 0
    print(f"trash: {result['count']}")
    for item in result["items"]:
        print(f"  {item['name']}  age={item['age_days']}d")
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    workspace = _workspace_from_args(args)
    try:
        result = setup_host(
            args.host,
            workspace,
            config_path=args.config,
            write=args.write,
            link_skills=getattr(args, "link_skills", False),
            skills_root=getattr(args, "skills_root", None) or getattr(args, "host_root", None),
            force=getattr(args, "force", False),
            portable=getattr(args, "portable", False),
        )
    except ValueError as exc:
        return _fail(exc)
    if args.json:
        _print_json(result)
        return 0
    if result["host"] == "deepseek-harness":
        return _print_dsh_setup(result)
    print(f"host: {result['host']}")
    if result.get("config_path"):
        print(f"config: {result['config_path']}")
    print(json.dumps(result["payload"], ensure_ascii=False, indent=2))
    if result.get("written"):
        print(f"written: {result['config_path']}")
        if result.get("backup"):
            print(f"backup:  {result['backup']}")
    else:
        print("print-only; pass --write to merge (a .caprouter.bak is created)")
    return 0


def _print_dsh_setup(result: dict[str, Any]) -> int:
    print(f"host:      {result['host']}")
    print(f"workspace: {result['workspace']}")
    print(f"python:    {result['python']}")
    state = "written" if result.get("written") else "print-only (pass --write to create it)"
    print(f"bundle:    {result['bundle_dir']}  [{state}]")
    if result.get("written"):
        print(f"files:     {len(result['files'])} written")
    if result.get("linked_skills") or result.get("skipped_skills") or result.get("link_errors"):
        print(f"skills:    {result['skills_root']}")
        for name in result["linked_skills"]:
            print(f"  linked   {name}")
        for item in result["skipped_skills"]:
            print(f"  skipped  {item}")
        for item in result["link_errors"]:
            print(f"  error    {item}")
    print("next:")
    for index, step in enumerate(result["next_steps"], 1):
        print(f"  {index}. {step}")
    if not result.get("written"):
        print("\npatch:")
        print(result["patch_text"])
    return 0



def cmd_lock(args: argparse.Namespace) -> int:
    workspace = _workspace_from_args(args)
    result = write_lock(workspace, out=args.out, live=args.live)
    if args.json:
        _print_json(result)
    else:
        print(f"lock written: {result['lock_path']} ({result['count']} entries)")
    return 0


def cmd_drift(args: argparse.Namespace) -> int:
    workspace = _workspace_from_args(args)
    try:
        result = check_drift(workspace, lock=args.lock, live=args.live)
    except ValueError as exc:
        return _fail(exc)
    if args.json:
        _print_json(result)
    else:
        print(f"drift: {'clean' if result['clean'] else 'found'}  {result['counts']}")
        for identifier in result["missing"]:
            print(f"  MISSING {identifier}")
        for identifier in result["new"]:
            print(f"  NEW     {identifier}")
        for item in result["changed"]:
            print(
                f"  CHANGED {item['id']}  "
                f"{item.get('from_hash')} -> {item.get('to_hash')}"
            )
    if args.check and not result["clean"]:
        return 1
    return 0


def cmd_duplicates(args: argparse.Namespace) -> int:
    workspace = _workspace_from_args(args)
    if getattr(args, "prefer", None):
        try:
            result = prefer_duplicate(workspace, args.prefer)
        except ValueError as exc:
            return _fail(exc)
        _print_json(result)
        return 0
    result = find_duplicates(workspace)
    if args.json:
        _print_json(result)
        return 0
    print(f"duplicate groups: {result['count']}")
    for group in result["groups"]:
        print(
            f"  {group['type']:7} {group['name']} x{group['count']} "
            f"same_content={group['same_content']}"
        )
        for item in group["items"]:
            flag = "*" if item["path"] == group["winner"] else " "
            print(f"    {flag} {item['path']}  hash={item['hash']}")
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    workspace = _workspace_from_args(args)
    result = capability_health(
        workspace,
        capability_id=args.id,
        run_probes=getattr(args, "probe", False),
    )
    if args.state:
        result["capabilities"] = [
            item for item in result["capabilities"] if item["state"] == args.state
        ]
        result["count"] = len(result["capabilities"])
    if args.json:
        _print_json(result)
        return 0
    print(f"capabilities: {result['count']}  counts={result['counts']}")
    for item in result["capabilities"]:
        review = f" review={item['review_status']}" if item.get("review_status") else ""
        print(
            f"  {item['state']:9} {str(item['id']):38} "
            f"probe={item.get('probe_status') or '-'} success={item.get('success_rate')}{review}"
        )
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    workspace = _workspace_from_args(args)
    data = collect_status(workspace, audit_limit=args.tail)
    if args.json:
        _print_json(data)
        return 0
    print(f"workspace: {data['workspace']}")
    print(f"state:     {'enabled' if data['state']['enabled'] else 'disabled'}  {data['state']['path']}")
    print(f"registry:  {data['registry']['stats']}")
    print("agents:")
    for agent in data["agents"]:
        mark = "OK" if agent["available"] else "MISS"
        print(f"  {mark:4} {agent['name']:12} {', '.join(agent['capabilities'])}")
    print("recent runs:")
    for run in data["recent_runs"]:
        print(f"  {run['run_dir']}")
    print(f"audit:     {data['audit']['path']}")
    return 0


def cmd_agents(args: argparse.Namespace) -> int:
    workspace = _workspace_from_args(args)
    data = collect_status(workspace, audit_limit=0)
    if args.json:
        _print_json(data["agents"])
    else:
        if not data["agents"]:
            print("no CLI agents detected (looked for claude, codebuddy, qwen, kimi, ... on PATH)")
        for agent in data["agents"]:
            mark = "OK" if agent["available"] else "MISS"
            print(f"{mark:4} {agent['name']:12} {agent['path']}")
            print(f"     {', '.join(agent['capabilities'])}")
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    workspace = _workspace_from_args(args)
    events = tail_audit(args.tail)
    if args.json:
        _print_json(events)
    else:
        print(f"audit log: {audit_path()}")
        if not events:
            print("  (no events yet; hook events appear here once the host runs a tool)")
        for event in events:
            print(
                f"{event.get('ts')}  {event.get('event')}  "
                f"{event.get('tool') or ''}  {event.get('decision')}  {event.get('reason') or ''}"
            )
    return 0


def cmd_runs(args: argparse.Namespace) -> int:
    workspace = _workspace_from_args(args)
    data = collect_status(workspace, audit_limit=0)
    if args.json:
        _print_json(data["recent_runs"])
    else:
        if not data["recent_runs"]:
            print("no runs yet (run a plan with: caprouter run --plan <file>)")
        for run in data["recent_runs"]:
            print(f"{run['run_dir']}  task={run.get('task_id')}  nodes={run.get('nodes')}")
    return 0


def cmd_backups(args: argparse.Namespace) -> int:
    workspace = _workspace_from_args(args)
    items = list_backups(workspace)
    if args.json:
        _print_json(items)
    else:
        if not items:
            print("no backups yet (create one with: caprouter backup)")
        for item in items:
            print(f"{item['created_at']}  {item['backup_dir']}")
    return 0


def cmd_backup(args: argparse.Namespace) -> int:
    workspace = _workspace_from_args(args)
    _load_config(workspace)
    result = create_backup(workspace, args.label)
    _print_json(result)
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    workspace = _workspace_from_args(args)
    try:
        result = restore_backup(workspace, args.source, force=args.force)
    except ValueError as exc:
        return _fail(exc)
    _print_json(result)
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    try:
        result = verify_claims(args.claims)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.json:
        _print_json(result)
    else:
        counts = result["counts"]
        total = len(result["claims"])
        print(f"claims: {total}")
        for key, value in counts.items():
            if value:
                print(f"  {key}: {value}")
        for claim in result["claims"]:
            print(f"  {claim['verdict']:15} {claim['claim_id']}  {claim.get('reason') or ''}")
    if args.strict and any(claim["verdict"] != "supported" for claim in result["claims"]):
        return 1
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    workspace = _workspace_from_args(args)
    entries = _registry_entries(workspace)
    if not entries:
        raise SystemExit(f"registry is empty or missing in {workspace}; run: caprouter scan")
    config = read_json(config_path(workspace), {})
    policy = config.get("routing") if isinstance(config, dict) else None
    cases = load_cases(args.cases)
    if not cases:
        raise SystemExit(f"no cases found: {args.cases or DEFAULT_CASES}")
    result = evaluate(
        entries,
        cases,
        policy if isinstance(policy, dict) else None,
        top_k=args.top,
    )
    if args.json:
        _print_json(result)
    else:
        context = result["context"]
        print(
            f"cases: {result['cases']}  "
            f"top1: {result['top1']:.1%}  "
            f"top{result['top_k']}: {result['topk']:.1%}"
        )
        print(
            f"context: {context['full_chars']} chars -> avg {context['avg_topk_chars']} chars "
            f"({context['reduction']:.1%} reduction)"
        )
        for item in result["details"]:
            mark = "OK  " if item["hit_at_k"] else "FAIL"
            top1 = "Y" if item["hit1"] else "N"
            print(f"  {mark} top1={top1} {item['id']}: {item['got'][:3]}")
    if args.min_topk is not None and result["topk"] < args.min_topk:
        return 1
    return 0


def cmd_state(args: argparse.Namespace) -> int:
    if args.enable:
        data = write_state(True, "cli")
    elif args.disable:
        data = write_state(False, "cli")
    else:
        data = read_state()
    payload = {**data, "path": str(state_path())}
    if args.json:
        _print_json(payload)
    else:
        print(f"CapRouter is {'enabled' if data['enabled'] else 'disabled'}")
        print(f"  state:   {state_path()}")
        if data.get("updated_at"):
            print(f"  updated: {data['updated_at']} ({data.get('updated_by')})")
    return 0


def cmd_plan_check(args: argparse.Namespace) -> int:
    try:
        plan = load_plan(args.file)
    except (OSError, json.JSONDecodeError) as exc:
        _print_json({"schema": "caprouter.plan-check.v1", "ok": False, "errors": [str(exc)], "warnings": []})
        return 1
    result = validate_plan(plan)
    if args.json:
        _print_json(result)
    else:
        print("OK" if result["ok"] else "NOT OK")
        for error in result["errors"]:
            print(f"  ERROR  {error}")
        for warning in result["warnings"]:
            print(f"  WARN   {warning}")
    return 0 if result["ok"] else 1


def cmd_run(args: argparse.Namespace) -> int:
    workspace = _workspace_from_args(args)
    if args.execute and not is_enabled():
        print(
            "CapRouter is disabled; enable it from the tray or run: caprouter state --enable",
            file=sys.stderr,
        )
        return EXIT_DISABLED
    entries = _registry_entries(workspace)
    if not entries:
        raise SystemExit(f"registry is empty or missing in {workspace}; run: caprouter scan")
    plan = load_plan(args.plan)
    check = validate_plan(plan)
    if not check["ok"]:
        _print_json(check)
        return 1
    registry = {"entries": entries}
    config = read_json(config_path(workspace), {})
    if str((config if isinstance(config, dict) else {}).get("mode") or "orchestrator") != "bus":
        print(
            "warning: mode=orchestrator is a compatibility path; new work should use the capability bus "
            "(capability search/get/invoke/verify/probe).",
            file=sys.stderr,
        )

    if args.execute:
        summary = execute_plan(
            plan,
            registry,
            workspace,
            config=config if isinstance(config, dict) else None,
            options=ExecuteOptions(
                adapter=args.adapter,
                timeout=args.timeout,
                max_turns=args.max_turns,
                max_rework=args.max_rework,
            ),
        )
        _print_json(summary)
        return 0 if summary.get("status") == "completed" else 2

    routing_policy = config.get("routing") if isinstance(config, dict) else None
    summary = run_plan(
        plan,
        registry,
        workspace,
        dry_run=True,
        routing_policy=routing_policy if isinstance(routing_policy, dict) else None,
        enabled_agents=config.get("enabled_agents") if isinstance(config, dict) else None,
    )
    _print_json(summary)
    print("dry-run only; add --execute to run the plan", file=sys.stderr)
    return 0


def cmd_host(args: argparse.Namespace) -> int:
    workspace = _workspace_from_args(args)
    if not is_enabled():
        print(
            "CapRouter is disabled; enable it from the tray or run: caprouter state --enable",
            file=sys.stderr,
        )
        return EXIT_DISABLED
    config = _load_config(workspace)
    prompt = args.prompt
    if args.stdin:
        try:
            prompt = sys.stdin.buffer.read().decode("utf-8", errors="replace")
        except AttributeError:
            prompt = sys.stdin.read()
    if not prompt or not prompt.strip():
        print("host requires --prompt or --stdin", file=sys.stderr)
        return 1
    entries = _registry_entries(workspace)
    summary = run_host(
        prompt,
        {"entries": entries},
        workspace,
        config=config,
        adapter=args.adapter,
        timeout=args.timeout,
        max_turns=args.max_turns,
    )
    if args.json:
        _print_json(summary)
    else:
        final_path = summary.get("final_path")
        if final_path and Path(final_path).exists():
            print(Path(final_path).read_text(encoding="utf-8"))
        else:
            _print_json(summary)
    return 0 if summary.get("status") == "completed" else 2


def cmd_hook(args: argparse.Namespace) -> int:
    from .guard import main as guard_main

    return guard_main()


def _acceptance_from_args(args: argparse.Namespace) -> list[Any] | None:
    acceptance: list[Any] = []
    raw = getattr(args, "acceptance", None)
    if raw:
        path = Path(str(raw))
        try:
            data = read_json(path) if path.exists() else json.loads(str(raw))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"invalid --acceptance JSON: {exc}") from exc
        if isinstance(data, dict):
            checks = data.get("checks")
            acceptance.extend(checks if isinstance(checks, list) else [data])
        elif isinstance(data, list):
            acceptance.extend(data)
        else:
            acceptance.append(data)
    acceptance.extend(getattr(args, "criterion", None) or [])
    return acceptance or None


def _capability_workspace(args: argparse.Namespace) -> Path:
    workspace = resolve_workspace(getattr(args, "workspace", None))
    set_audit_workspace(workspace)
    return workspace


def cmd_capability_search(args: argparse.Namespace) -> int:
    workspace = _capability_workspace(args)
    result = search_capabilities(
        workspace,
        args.need,
        top=args.top,
        types=args.type,
        capabilities=args.capability,
        domains=args.domain,
        min_confidence=args.min_confidence,
        min_margin=args.min_margin,
        min_coverage=args.min_coverage,
        include_unavailable=args.include_unavailable,
        record_gap=args.record_gap,
    )
    if args.json:
        _print_json(result)
    else:
        print(f"need:      {args.need}")
        print(f"decision:  {result.get('decision')}  confidence={result.get('confidence')} margin={result.get('margin')}")
        if result.get("error"):
            print(f"error:     {result['error']}")
        for item in result.get("candidates") or []:
            print(f"{item.get('score'):.3f}  {item.get('type'):10}  {item.get('id')}")
            print(f"       {item.get('description') or ''}")
            if item.get("reasons"):
                print(f"       reasons: {', '.join(item['reasons'])}")
        if result.get("decision") == "abstain":
            reason = result.get("abstain_reason") or "thresholds not met"
            print(f"(abstained: {reason}; consider capability gaps / search / ask the user)")
    return 0


def cmd_capability_get(args: argparse.Namespace) -> int:
    workspace = _capability_workspace(args)
    card = get_capability(workspace, args.id)
    if not card:
        print(f"capability not found: {args.id}", file=sys.stderr)
        return 1
    _print_json(card)
    return 0


def cmd_capability_invoke(args: argparse.Namespace) -> int:
    workspace = _capability_workspace(args)
    input_text = args.input
    if args.stdin:
        input_text = sys.stdin.read()
    result = invoke_capability(
        workspace,
        args.id,
        input_text=input_text or "",
        cwd=args.cwd,
        timeout=args.timeout,
        allow_execute=args.allow_execute,
        allow_external=getattr(args, "allow_external_content", False),
    )
    _print_json(result)
    return 0 if result.get("status") in {"ok", "prompt"} else 1


def cmd_capability_verify(args: argparse.Namespace) -> int:
    workspace = _capability_workspace(args)
    result = verify_artifact(
        workspace,
        artifact_path=args.artifact,
        text=args.text,
        acceptance=_acceptance_from_args(args),
    )
    if args.json:
        _print_json(result)
    else:
        print(f"verdict: {result.get('verdict')}")
        for check in result.get("checks") or []:
            print(f"  {check.get('status'):12} {check.get('type'):16} expected={check.get('expected')} actual={check.get('actual')}")
            if check.get("reason"):
                print(f"       {check['reason']}")
    return 0 if result.get("verdict") == "pass" else 1


def cmd_capability_outcome(args: argparse.Namespace) -> int:
    workspace = _capability_workspace(args)
    success: bool | None = None
    if args.success:
        success = True
    if args.failure:
        success = False
    row = record_outcome(
        workspace,
        args.id,
        task=args.task or "",
        success=success,
        duration_ms=args.duration_ms,
        human_edit_minutes=args.human_edit_minutes,
        rework=args.rework,
        notes=args.notes or "",
    )
    _print_json(row)
    return 0


def cmd_pipeline_list(args: argparse.Namespace) -> int:
    workspace = _capability_workspace(args)
    data = list_pipelines(workspace)
    if args.json:
        _print_json(data)
    else:
        print(f"pipelines: {data['count']}")
        for item in data["pipelines"]:
            print(f"  {item['id']:24} steps={item['steps']}  {item['goal']}")
    return 0


def cmd_pipeline_plan(args: argparse.Namespace) -> int:
    workspace = _capability_workspace(args)
    try:
        result = plan_pipeline(workspace, args.id, task=args.task or "", input_path=args.input)
    except ValueError as exc:
        return _fail(exc)
    _print_json(result)
    return 0 if result.get("status") == "ready" else 2


def cmd_deps_plan(args: argparse.Namespace) -> int:
    workspace = _capability_workspace(args)
    try:
        result = plan_dependencies(workspace, args.id)
    except ValueError as exc:
        return _fail(exc)
    _print_json(result)
    return 0


def cmd_deps_check(args: argparse.Namespace) -> int:
    workspace = _capability_workspace(args)
    try:
        result = check_dependencies(workspace, args.id)
    except ValueError as exc:
        return _fail(exc)
    _print_json(result)
    return 0


def cmd_pipeline_run(args: argparse.Namespace) -> int:
    workspace = _capability_workspace(args)
    result = run_pipeline(
        workspace,
        args.run,
        allow_execute=args.allow_execute,
        timeout=args.timeout,
    )
    _print_json(result)
    return 0 if result.get("status") in {"executed", "awaiting_host"} else 1


def cmd_pipeline_verify(args: argparse.Namespace) -> int:
    workspace = _capability_workspace(args)
    result = verify_pipeline(
        workspace,
        args.run,
        allow_execute=args.allow_execute,
        timeout=args.timeout,
    )
    _print_json(result)
    return 0 if result.get("status") == "pass" else 1


def cmd_pipeline_outcome(args: argparse.Namespace) -> int:
    workspace = _capability_workspace(args)
    success: bool | None = None
    if args.success:
        success = True
    if args.failure:
        success = False
    result = record_pipeline_outcome(
        workspace,
        args.run,
        success=success,
        duration_ms=args.duration_ms,
        human_edit_minutes=args.human_edit_minutes,
        notes=args.notes or "",
    )
    _print_json(result)
    return EXIT_OK if result.get("status") == "recorded" else EXIT_FAILED


def cmd_capability_gap(args: argparse.Namespace) -> int:
    workspace = _capability_workspace(args)
    row = record_gap(
        workspace,
        need=args.need,
        reason=args.reason or "",
        tried=args.tried,
        workaround=args.workaround or "",
    )
    _print_json(row)
    return 0


def cmd_capability_gaps(args: argparse.Namespace) -> int:
    workspace = _capability_workspace(args)
    data = list_gaps(workspace, limit=args.limit)
    if args.json:
        _print_json(data)
    else:
        print(f"gaps: {data.get('count')}")
        for item in data.get("gaps") or []:
            print(f"  {item.get('ts')}  {item.get('need')}  {item.get('reason') or ''}")
    return 0


def cmd_capability_probe(args: argparse.Namespace) -> int:
    workspace = _capability_workspace(args)
    if args.all:
        result = run_all_probes(
            workspace,
            allow_execute=args.allow_execute,
            check_hash=args.hash,
            timeout=args.timeout,
        )
        if args.json:
            _print_json(result)
        else:
            counts = result.get("counts") or {}
            print(f"probes: {result.get('total')}  " + "  ".join(f"{key}={value}" for key, value in counts.items()))
            for item in result.get("results") or []:
                if item.get("status") != "pass":
                    print(f"  {item.get('status'):9} {item.get('id')}  {item.get('fail')} fail / {item.get('pending')} pending")
        return 0 if not (result.get("counts") or {}).get("fail") else 1
    if not args.id:
        print("capability probe requires --id or --all", file=sys.stderr)
        return 1
    result = run_probes(
        workspace,
        args.id,
        allow_execute=args.allow_execute,
        check_hash=args.hash,
        timeout=args.timeout,
    )
    if args.json:
        _print_json(result)
    else:
        print(f"id:        {result.get('id')}")
        print(f"status:    {result.get('status')}  evidence={result.get('evidence')}  pass_rate={result.get('pass_rate')}")
        for check in result.get("checks") or []:
            print(f"  {check.get('status'):12} {check.get('type'):18} {check.get('id')}  {check.get('reason') or ''}")
        pending = [check for check in (result.get("checks") or []) if check.get("status") == "pending"]
        if pending:
            print(f"(pending host tasks: {len(pending)}; run them and record with capability probe-record)")
    return 0 if result.get("status") in {"pass", "pending"} else 1


def cmd_capability_probe_record(args: argparse.Namespace) -> int:
    workspace = _capability_workspace(args)
    status = "pass" if args.passed else "fail" if args.failed else "pending"
    row = record_probe(
        workspace,
        args.id,
        task=args.task or "",
        status=status,
        duration_ms=args.duration_ms,
        notes=args.notes or "",
    )
    _print_json(row)
    return 0


def cmd_capability_probe_report(args: argparse.Namespace) -> int:
    workspace = _capability_workspace(args)
    data = probe_report(workspace, capability_id=args.id, limit=args.limit)
    if args.json:
        _print_json(data)
    else:
        if args.id:
            summary = data.get("summary") or {}
            print(f"id:       {args.id}")
            print(f"runs:     {data.get('runs')}  status={summary.get('status')} evidence={summary.get('evidence')}")
            for row in data.get("history") or []:
                print(f"  {row.get('ts')}  {row.get('status'):9} {row.get('evidence') or ''}  {row.get('task') or ''}")
        else:
            print(f"probed capabilities: {data.get('count')}")
            for item in data.get("summaries") or []:
                print(f"  {item.get('status'):9} runs={item.get('runs'):3} {item.get('id')}  {item.get('evidence') or ''}")
    return 0


def _combine_draft_reports(reports: list[dict[str, Any]]) -> dict[str, Any]:
    if not reports:
        return {
            "schema": "caprouter.capability-draft-batch.v1",
            "generated_at": now_iso(),
            "scanned": 0,
            "drafted": 0,
            "unchanged": 0,
            "skipped": 0,
            "needs_review": 0,
            "applied": 0,
            "drafts": [],
            "skipped_items": [],
            "errors": [],
            "applied_items": [],
        }
    if len(reports) == 1:
        return reports[0]
    combined: dict[str, Any] = {
        key: sum(int(report.get(key) or 0) for report in reports)
        for key in ("scanned", "drafted", "unchanged", "skipped", "needs_review", "applied")
    }
    combined["schema"] = "caprouter.capability-draft-batch.v1"
    combined["generated_at"] = now_iso()
    for key in ("drafts", "skipped_items", "errors", "applied_items"):
        combined[key] = [item for report in reports for item in (report.get(key) or [])]
    return combined


def cmd_capability_draft(args: argparse.Namespace) -> int:
    workspace = _capability_workspace(args)
    if args.all:
        if args.id or args.path:
            print("--all cannot be combined with --id or --path", file=sys.stderr)
            return 2
        if args.type == "mcp":
            print("mcp entries are metadata-only; no Card draft is generated", file=sys.stderr)
            return 1
        reports: list[dict[str, Any]] = []
        if args.type in {"skill", "plugin"}:
            reports.append(
                generate_registry_drafts(
                    workspace,
                    _registry_entries(workspace),
                    types={args.type},
                    limit=args.limit,
                    force=args.force,
                    apply=args.apply,
                )
            )
        else:
            projects = [
                item
                for item in (load_projects_scan(workspace).get("projects") or [])
                if isinstance(item, dict)
            ]
            if projects:
                reports.append(
                    generate_project_drafts(
                        workspace,
                        projects,
                        types={args.type} if args.type else None,
                        limit=args.limit,
                        force=args.force,
                        apply=args.apply,
                    )
                )
            if getattr(args, "registry", False):
                reports.append(
                    generate_registry_drafts(
                        workspace,
                        _registry_entries(workspace),
                        types={"skill", "plugin"},
                        limit=args.limit,
                        force=args.force,
                        apply=args.apply,
                    )
                )
            if not reports:
                print(
                    "no projects_scan.json; run: caprouter scan-projects or caprouter bootstrap",
                    file=sys.stderr,
                )
                return 1
        report = _combine_draft_reports(reports)
        if args.json:
            _print_json(report)
        else:
            print(
                f"drafts: scanned={report['scanned']} drafted={report['drafted']} "
                f"unchanged={report['unchanged']} skipped={report['skipped']} "
                f"needs_review={report['needs_review']} applied={report['applied']}"
            )
            for item in report["drafts"]:
                flag = "REVIEW" if item["needs_review"] else "ok    "
                print(f"  {flag} {item.get('type'):7} {item.get('name')} -> {item.get('draft_path')}")
            for item in report["errors"]:
                target = item.get("path") or item.get("id") or "?"
                print(f"  ERROR {target}: {item.get('error')}", file=sys.stderr)
        return 0
    try:
        draft = generate_draft(
            workspace,
            capability_id=args.id,
            path=args.path,
            type_hint=args.type,
            name=args.name,
        )
    except ValueError as exc:
        return _fail(exc)
    # Always persist the draft: `review` reads .caprouter/drafts/, so a draft
    # that only existed on stdout could never be reviewed (the documented
    # generate -> review -> apply flow silently skipped the middle step).
    written = write_draft(workspace, draft, args.out)
    if args.apply:
        result = apply_draft(workspace, draft, force=args.force)
        result["draft_path"] = str(written)
        _print_json(result)
        return 0 if result.get("status") == "applied" else 1
    if args.json:
        payload = dict(draft)
        payload["draft_path"] = str(written)
        _print_json(payload)
    else:
        print(f"draft written: {written}")
        print(f"next: caprouter review --workspace \"{workspace}\"  (or add --apply to merge it now)")
    return 0


def cmd_synthesize_propose(args: argparse.Namespace) -> int:
    workspace = _capability_workspace(args)
    report = propose_synthesis(
        workspace,
        task=args.task,
        trace=args.trace,
        gap_need=args.gap_need,
        gap_index=args.gap_index,
        name=args.name,
        kind=args.kind,
        notes=args.notes or "",
    )
    if args.json:
        _print_json(report)
    else:
        print(report.get("message"))
        if report.get("status") == "proposed":
            print(f"candidate: {report.get('candidate_dir')}")
            print(f"skill:     {report.get('skill_dir')}")
            print(f"card:      {report.get('card_draft_path')}")
            for command in report.get("next_commands") or []:
                print(f"next:      {command}")
    return 0 if report.get("status") == "proposed" else 2


def cmd_synthesize_list(args: argparse.Namespace) -> int:
    workspace = _capability_workspace(args)
    report = list_syntheses(workspace)
    if args.json:
        _print_json(report)
    else:
        print(f"synthesized candidates: {report.get('count')}")
        for item in report.get("candidates") or []:
            print(f"  {item.get('status'):12} {item.get('id'):40} {item.get('task')}")
    return 0


def cmd_synthesize_refine(args: argparse.Namespace) -> int:
    workspace = _capability_workspace(args)
    report = refine_candidate(
        workspace,
        args.candidate,
        method=args.method,
        adapter=args.agent,
        timeout=args.timeout,
    )
    if args.json:
        _print_json(report)
    else:
        print(report.get("message") or report.get("status"))
        if report.get("refinement_dir"):
            print(f"method:      {report.get('method')}  adapter={report.get('adapter')}")
            print(f"refinement:  {report.get('refinement_dir')}")
            print(f"skill:       {report.get('skill_path')}")
        for warning in report.get("warnings") or []:
            print(f"WARN {warning}")
    return 0 if str(report.get("status", "")).startswith("refined") else 2


def cmd_synthesize_approve(args: argparse.Namespace) -> int:
    workspace = _capability_workspace(args)
    report = approve_synthesis(
        workspace,
        args.candidate,
        apply=bool(args.apply),
        force=bool(args.force),
        auto=bool(args.auto),
    )
    if args.json:
        _print_json(report)
    else:
        print(report.get("message") or report.get("status"))
        review = report.get("review") or {}
        if report.get("status") == "review_failed":
            print(f"review:     {review.get('status')} risk={review.get('risk')} score={review.get('score')}")
            for check in review.get("checks") or []:
                if check.get("status") in {"fail", "warn"}:
                    print(f"  {check.get('status'):4} {check.get('id')}: {check.get('message')}")
        if report.get("capability_id"):
            print(f"capability: {report.get('capability_id')}")
            print(f"installed:  {report.get('installed_path')}")
            print(f"draft:      {report.get('draft_path')}")
            review = report.get("review") or {}
            print(f"review:     {review.get('counts')} applied={review.get('applied')}")
            for command in report.get("next_commands") or []:
                print(f"next:       {command}")
    return 0 if str(report.get("status", "")).startswith(("approved", "improvement")) else 2


def cmd_synthesize_improve(args: argparse.Namespace) -> int:
    workspace = _capability_workspace(args)
    report = improve_synthesis(
        workspace,
        args.id,
        notes=args.notes or "",
        apply=bool(args.apply),
    )
    if args.json:
        _print_json(report)
    else:
        print(report.get("message") or report.get("status"))
        if report.get("improvement_dir"):
            print(f"improvement: {report.get('improvement_dir')}")
            print(f"patch:       {report.get('patch_path')}")
        if report.get("installed_path"):
            print(f"installed:   {report.get('installed_path')}")
    return 0 if str(report.get("status", "")).startswith(("improvement", "approved")) else 2


def cmd_discover(args: argparse.Namespace) -> int:
    workspace = _workspace_from_args(args)
    try:
        result = discover_skills(
            workspace,
            args.query,
            source=args.source,
            limit=args.limit,
            offset=args.offset,
            allow_network=args.allow_network,
            offline=args.offline,
            timeout=args.timeout,
        )
    except ValueError as exc:
        return _fail(exc)
    if args.json:
        _print_json(result)
    else:
        print(f"discover: {result['count']} candidates for {result['query']!r}")
        for item in result["candidates"]:
            print(
                f"  {item.get('id'):48} installs={item.get('installs')} "
                f"license={item.get('license') or '-'} "
                f"{(item.get('description') or '')[:60]}"
            )
        print("next: caprouter fetch --candidate <id> --allow-network", file=sys.stderr)
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    workspace = _workspace_from_args(args)
    if bool(args.candidate) == bool(args.url):
        print("provide exactly one of --candidate or --url", file=sys.stderr)
        return 2
    try:
        if args.url:
            result = fetch_url(
                workspace,
                args.url,
                allow_network=args.allow_network,
                force=args.force,
                timeout=args.timeout,
            )
        else:
            result = fetch_skill(
                workspace,
                args.candidate,
                allow_network=args.allow_network,
                force=args.force,
                timeout=args.timeout,
            )
    except ValueError as exc:
        return _fail(exc)
    _print_json(result)
    return 0


def cmd_vendor(args: argparse.Namespace) -> int:
    workspace = _workspace_from_args(args)
    if bool(args.candidate) == bool(args.dir):
        print("provide exactly one of --candidate or --dir", file=sys.stderr)
        return 2
    try:
        result = vendor_skill(
            workspace,
            args.candidate or "",
            method=args.method,
            quarantine_path=args.dir,
        )
    except ValueError as exc:
        return _fail(exc)
    _print_json(result)
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    workspace = _workspace_from_args(args)
    try:
        result = uninstall_capability(workspace, args.id, dry_run=not args.yes)
    except ValueError as exc:
        return _fail(exc)
    host = normalize_host(getattr(args, "host", None))
    if host != "unknown" and getattr(args, "unlink_host", False):
        result["host_unlink"] = unlink_managed_skill(
            workspace,
            args.id,
            host=host,
            removed_hash=result.get("content_hash"),
            force=bool(getattr(args, "force", False)),
            root=getattr(args, "host_root", None),
        )
    _print_json(result)
    if result.get("status") == "preview":
        print("preview only; pass --yes to uninstall", file=sys.stderr)
        return EXIT_NEEDS_FLAG
    return 0


def cmd_rollback(args: argparse.Namespace) -> int:
    workspace = _workspace_from_args(args)
    try:
        if args.version or args.hash:
            result = rollback_to_version(
                workspace,
                args.id,
                version=args.version,
                hash_value=args.hash,
                dry_run=not args.yes,
            )
        else:
            result = rollback_capability(
                workspace,
                args.id,
                dry_run=not args.yes,
                force=args.force,
            )
    except ValueError as exc:
        return _fail(exc)
    _print_json(result)
    if result.get("status") == "preview":
        print("preview only; pass --yes to rollback", file=sys.stderr)
        return EXIT_NEEDS_FLAG
    return 0


def cmd_versions(args: argparse.Namespace) -> int:
    workspace = _workspace_from_args(args)
    try:
        result = list_versions(workspace, args.id)
    except ValueError as exc:
        return _fail(exc)
    if args.json:
        _print_json(result)
        return 0
    print(f"versions: {args.id}")
    for item in result["versions"]:
        flag = "current" if item.get("current") else "trash  "
        print(
            f"  {flag} version={item.get('version') or '?'} "
            f"hash={item.get('hash')} path={item.get('path')}"
        )
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    workspace = _workspace_from_args(args)
    try:
        result = update_source(
            workspace,
            args.id,
            args.source,
            approve=args.approve,
            generate_card=not args.no_draft,
            allow_symlinks=args.allow_symlinks,
            payload_only=args.payload_only,
            skip_provenance=args.skip_provenance,
        )
    except ValueError as exc:
        return _fail(exc)
    if args.json:
        _print_json(result)
    else:
        print(f"{result.get('status')}: {result.get('id')}")
        if result.get("current"):
            print(f"  current: {result['current'].get('version')} {result['current'].get('hash')}")
        if result.get("preview"):
            print(
                f"  candidate: {result['preview'].get('version')} "
                f"{result['preview'].get('source_hash')}"
            )
    if result.get("status") == "preview":
        print("preview only; pass --approve to update", file=sys.stderr)
        return 2
    return 0 if result.get("status") in {"updated", "up_to_date"} else 1


def cmd_quarantine_list(args: argparse.Namespace) -> int:
    workspace = _workspace_from_args(args)
    data = list_quarantine(workspace)
    if args.json:
        _print_json(data)
    else:
        print(f"quarantine: {data['count']}")
        for item in data["items"]:
            print(
                f"  {item.get('candidate_id'):48} age={item.get('age_days')}d "
                f"files={item.get('file_count')} status={item.get('status')}"
            )
    return 0


def cmd_quarantine_clean(args: argparse.Namespace) -> int:
    workspace = _workspace_from_args(args)
    try:
        result = clean_quarantine(
            workspace,
            candidate=args.candidate,
            older_than_days=args.older_than_days,
            dry_run=not args.yes,
        )
    except ValueError as exc:
        return _fail(exc)
    _print_json(result)
    if result.get("status") == "preview":
        print("preview only; pass --yes to delete", file=sys.stderr)
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    workspace = _workspace_from_args(args)
    if not args.approve:
        try:
            preview = preview_install(workspace, args.source, type_hint=args.type, name=args.name)
        except ValueError as exc:
            return _fail(exc)
        if args.json:
            _print_json(preview)
        else:
            print(f"source:      {preview['source']['kind']}  {preview['source']['path']}")
            print(f"type/name:   {preview['type']} / {preview['name']} ({preview['version']})")
            print(f"target:      {preview['target']}")
            print(f"files:       {preview['file_count']}  bytes={preview['total_bytes']}")
            print(f"permissions: {preview['permissions']}")
            print(f"license:     {(preview.get('license') or {}).get('name')}")
            print(f"deps:        {', '.join(preview.get('dependencies') or []) or 'none'}")
            print(f"source hash: {preview.get('source_hash')}")
            for warning in preview.get("warnings") or []:
                print(f"  WARN  {warning}")
        print("installation requires --approve (files are copied, never executed)", file=sys.stderr)
        print(
            "tip: pass --approve-hash <source hash> to pin exactly what you reviewed",
            file=sys.stderr,
        )
        return EXIT_NEEDS_FLAG
    expected_hash = str(getattr(args, "approve_hash", None) or "").strip()
    if expected_hash:
        # Pin the reviewed source: `--approve` alone approves whatever is on
        # disk at approval time, which is not the same as what was previewed.
        try:
            preview = preview_install(workspace, args.source, type_hint=args.type, name=args.name)
        except ValueError as exc:
            return _fail(exc)
        current = str(preview.get("source_hash") or "")
        if current != expected_hash:
            _print_json(
                {
                    "schema": "caprouter.install.v1",
                    "status": "source_changed",
                    "preview_hash": expected_hash,
                    "current_hash": current,
                    "reason": "source changed since the preview; re-preview and approve the new hash",
                }
            )
            return EXIT_FAILED
    result = install_source(
        workspace,
        args.source,
        type_hint=args.type,
        name=args.name,
        approve=True,
        force=args.force,
        generate_card=not args.no_draft,
        allow_symlinks=args.allow_symlinks,
        payload_only=args.payload_only,
        skip_provenance=args.skip_provenance,
    )
    _print_json(result)
    return 0 if result.get("status") == "installed" else 1


def cmd_installed(args: argparse.Namespace) -> int:
    workspace = _workspace_from_args(args)
    items = list_installed(workspace)
    if args.json:
        _print_json(items)
    else:
        print(f"installed: {len(items)}")
        for item in items:
            print(f"  {item.get('type'):8} {item.get('name')}  {item.get('version')}  {item.get('hash')}")
    return 0


def cmd_unmanaged(args: argparse.Namespace) -> int:
    workspace = _workspace_from_args(args)
    result = list_unmanaged(workspace)
    if args.json:
        _print_json(result)
        return 0
    print(f"unmanaged skills/plugins: {result['count']}")
    for item in result["entries"]:
        print(f"  {item['id']:36} {item['type']:7} {item['path']}")
    return 0


def cmd_adopt(args: argparse.Namespace) -> int:
    workspace = _workspace_from_args(args)
    try:
        result = adopt_capability(
            workspace,
            args.id,
            dry_run=not args.yes,
            name=args.name,
            force=args.force,
            allow_symlinks=args.allow_symlinks,
            generate_card=not args.no_draft,
        )
    except ValueError as exc:
        return _fail(exc)
    if args.json:
        _print_json(result)
    else:
        print(f"{result['status']}: {result.get('id')}")
        if result.get("source"):
            print(f"  source: {result['source']}")
        if result.get("target"):
            print(f"  target: {result['target']}")
        for warning in result.get("warnings") or result.get("preview", {}).get("warnings", []):
            print(f"  WARN  {warning}")
    if result["status"] == "preview":
        print("adoption requires --yes (the original path is copied, never moved)", file=sys.stderr)
        return 2
    return 0 if result["status"] in {"adopted", "already_managed"} else 1


def cmd_backup_skill(args: argparse.Namespace) -> int:
    workspace = _workspace_from_args(args)
    try:
        result = backup_capability(workspace, args.id, out=args.out)
    except ValueError as exc:
        return _fail(exc)
    if args.json:
        _print_json(result)
    else:
        print(f"backed_up: {result['id']}")
        print(f"  backup: {result['backup']}")
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    workspace = resolve_workspace(args.workspace)
    host = normalize_host(getattr(args, "host", None))
    return serve_mcp(workspace, host=host, profile=getattr(args, "tools", "full"))


def cmd_mcp_list(args: argparse.Namespace) -> int:
    workspace = _workspace_from_args(args)
    result = inventory_summary(workspace, extra_configs=args.config)
    if args.json:
        _print_json(result)
        return 0
    print(f"MCP servers: {result['count']}")
    for entry in result["entries"]:
        hosts = ", ".join(entry.get("hosts") or [])
        print(
            f"  {entry['id']:32} {entry.get('transport') or '?':8} "
            f"{hosts:20} {str(entry.get('invocation') or '')[:90]}"
        )
    if not result["entries"]:
        print("  (no MCP configs found; pass --config <path> to scan an explicit file)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="caprouter", description="Local capability router")
    parser.add_argument("--version", action="version", version=f"caprouter {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="initialize a workspace and build the registry")
    init.add_argument("--workspace", help="workspace root (default: cwd)")
    init.add_argument("--delivery", help="delivery directory (default: <workspace>/deliverables)")
    init.add_argument("--root", action="append", help="additional skill root (repeatable)")
    init.add_argument("--plugin-root", action="append", help="additional plugin root (repeatable)")
    init.add_argument("--agent", action="append", help="enabled CLI agent (repeatable)")
    init.add_argument("--no-agents", action="store_true", help="do not scan CLI agents")
    init.add_argument("--mode", choices=["bus", "orchestrator"], default="bus",
                      help="bus: host owns the loop (default); orchestrator: legacy plan runner")
    init.add_argument("--yes", action="store_true", help="use defaults without prompting")
    init.add_argument("--force", action="store_true", help="reinitialize an existing workspace")
    init.add_argument("--json", action="store_true", help="print JSON")
    init.set_defaults(func=cmd_init)

    scan = subparsers.add_parser("scan", help="rescan local capabilities")
    scan.add_argument("--workspace")
    scan.add_argument("--json", action="store_true")
    scan.set_defaults(func=cmd_scan)

    scan_projects = subparsers.add_parser(
        "scan-projects",
        help="scan non-system drives or explicit roots for projects (metadata only)",
    )
    scan_projects.add_argument("--workspace")
    scan_projects.add_argument("--drive", action="append", help="drive letter, e.g. D (repeatable)")
    scan_projects.add_argument("--project-root", action="append", help="explicit root to scan (repeatable)")
    scan_projects.add_argument("--include-removable", action="store_true", default=None)
    scan_projects.add_argument("--max-depth", type=int, default=None)
    scan_projects.add_argument("--max-dirs", type=int, default=None)
    scan_projects.add_argument("--time-budget", type=float, default=None, help="seconds per root")
    scan_projects.add_argument("--max-projects", type=int, default=None)
    scan_projects.add_argument("--recent", type=int, default=10)
    scan_projects.add_argument("--no-write", action="store_true", help="do not write projects_scan.json")
    scan_projects.add_argument("--merge-registry", action="store_true", help="add scanned projects to the registry")
    scan_projects.add_argument("--save-roots", action="store_true", help="persist --project-root into config")
    scan_projects.add_argument("--json", action="store_true")
    scan_projects.set_defaults(func=cmd_scan_projects)

    bootstrap = subparsers.add_parser(
        "bootstrap",
        help="init if needed, scan, merge, draft Cards, review and static-probe",
    )
    bootstrap.add_argument("--workspace")
    bootstrap.add_argument("--drive", action="append", help="drive letter, e.g. D (repeatable)")
    bootstrap.add_argument("--project-root", action="append", help="explicit root to scan (repeatable)")
    bootstrap.add_argument("--skill-root", action="append", help="additional skill root (repeatable)")
    bootstrap.add_argument("--plugin-root", action="append", help="additional plugin root (repeatable)")
    bootstrap.add_argument("--include-removable", action="store_true", default=None)
    bootstrap.add_argument("--max-depth", type=int, default=None)
    bootstrap.add_argument("--max-dirs", type=int, default=None)
    bootstrap.add_argument("--time-budget", type=float, default=None, help="seconds per root")
    bootstrap.add_argument("--max-projects", type=int, default=None)
    bootstrap.add_argument("--recent", type=int, default=10)
    bootstrap.add_argument("--type", choices=["skill", "plugin", "project"], help="draft only this type")
    bootstrap.add_argument("--dry-run", action="store_true", help="scan and report without writing anything")
    bootstrap.add_argument("--no-probe", action="store_true", help="skip static probes")
    bootstrap.add_argument(
        "--draft-registry",
        action="store_true",
        help="also draft Cards for registry skills/plugins (not just projects)",
    )
    bootstrap.add_argument("--no-review", action="store_true", help="skip deterministic Card review")
    bootstrap.add_argument("--review-semantic", action="store_true", help="run semantic review for non-approved drafts")
    bootstrap.add_argument("--review-limit", type=int, default=None)
    bootstrap.add_argument("--auto-apply", action="store_true", default=None, help="apply low-risk auto-approved cards")
    bootstrap.add_argument("--force", action="store_true", help="overwrite unchanged draft files")
    bootstrap.add_argument("--agent", action="append", help="enabled CLI agent when initializing")
    bootstrap.add_argument("--no-agents", action="store_true")
    bootstrap.add_argument(
        "--no-default-skills",
        action="store_true",
        help="do not scan default global skill/plugin roots (clean workspace)",
    )
    bootstrap.add_argument("--json", action="store_true")
    bootstrap.set_defaults(func=cmd_bootstrap)

    review_cmd = subparsers.add_parser("review", help="review Card drafts deterministically (optional semantic pass)")
    review_cmd.add_argument("--workspace")
    review_cmd.add_argument("--id", action="append", help="review only this capability id (repeatable)")
    review_cmd.add_argument("--semantic", action="store_true", help="run the configured semantic reviewer")
    review_cmd.add_argument(
        "--fix",
        action="store_true",
        help="ask the semantic reviewer for a validated rewrite, then re-review (implies --semantic)",
    )
    review_cmd.add_argument("--agent", help="override semantic adapter: qwen / codebuddy / kimi / mock")
    review_auto = review_cmd.add_mutually_exclusive_group()
    review_auto.add_argument("--auto-apply", dest="auto_apply", action="store_true")
    review_auto.add_argument("--no-auto-apply", dest="auto_apply", action="store_false")
    review_cmd.set_defaults(auto_apply=None)
    review_cmd.add_argument("--dry-run", action="store_true")
    review_cmd.add_argument("--force", action="store_true", help="re-review and re-apply even if already applied")
    review_cmd.add_argument("--limit", type=int, default=None)
    review_cmd.add_argument("--json", action="store_true")
    review_cmd.set_defaults(func=cmd_review)

    migrate = subparsers.add_parser("migrate", help="export/import reviewed Cards and evidence across machines")
    migrate_sub = migrate.add_subparsers(dest="migrate_command", required=True)
    migrate_export = migrate_sub.add_parser("export", help="export a migration bundle")
    migrate_export.add_argument("--workspace")
    migrate_export.add_argument("--out", help="output .zip path or directory")
    migrate_export.add_argument("--json", action="store_true")
    migrate_export.set_defaults(func=cmd_migrate_export)
    migrate_import = migrate_sub.add_parser("import", help="import a bundle and apply matched reviewed Cards")
    migrate_import.add_argument("--bundle", required=True)
    migrate_import.add_argument("--workspace")
    migrate_import.add_argument("--no-scan", action="store_true", help="do not refresh the registry before matching")
    migrate_import.add_argument("--no-apply", action="store_true", help="write drafts instead of applying matched cards")
    migrate_import.add_argument("--dry-run", action="store_true")
    migrate_import.add_argument("--force", action="store_true", help="overwrite existing overlay cards")
    migrate_import.add_argument("--json", action="store_true")
    migrate_import.set_defaults(func=cmd_migrate_import)
    migrate_drafts = migrate_sub.add_parser(
        "drafts", help="list unmatched migrated drafts"
    )
    migrate_drafts.add_argument("--workspace")
    migrate_drafts.add_argument("--json", action="store_true")
    migrate_drafts.set_defaults(func=cmd_migrate_drafts)
    migrate_remap = migrate_sub.add_parser(
        "remap", help="point a migrated draft at a local directory and re-draft it"
    )
    migrate_remap.add_argument("--id", required=True)
    migrate_remap.add_argument("--path", required=True)
    migrate_remap.add_argument("--name")
    migrate_remap.add_argument("--apply", action="store_true", help="review and auto-apply low-risk")
    migrate_remap.add_argument("--force", action="store_true", help="overwrite an existing draft")
    migrate_remap.add_argument("--workspace")
    migrate_remap.add_argument("--json", action="store_true")
    migrate_remap.set_defaults(func=cmd_migrate_remap)

    route_cmd = subparsers.add_parser("route", help="route a need to top-k local capabilities")
    route_cmd.add_argument("--need", required=True)
    route_cmd.add_argument("--top", type=int, default=5)
    route_cmd.add_argument("--type", action="append", choices=["skill", "plugin", "cli-agent", "project", "script", "mcp"])
    route_cmd.add_argument("--capability", action="append", help="required capability, e.g. retrieve / generate / verify")
    route_cmd.add_argument("--domain", action="append", help="required domain, e.g. research / literature / office")
    route_cmd.add_argument("--min-score", type=float, default=None, help="override the routing threshold")
    route_cmd.add_argument("--workspace")
    route_cmd.add_argument("--json", action="store_true")
    route_cmd.set_defaults(func=cmd_route)

    eval_cmd = subparsers.add_parser("eval", help="measure routing accuracy and context reduction")
    eval_cmd.add_argument("--workspace")
    eval_cmd.add_argument("--cases", help="case file (default: eval/routing_cases.json)")
    eval_cmd.add_argument("--top", type=int, default=3)
    eval_cmd.add_argument("--min-topk", type=float, default=None)
    eval_cmd.add_argument("--json", action="store_true")
    eval_cmd.set_defaults(func=cmd_eval)

    dashboard_cmd = subparsers.add_parser(
        "dashboard", help="write a static HTML dashboard of health/duplicates/drift"
    )
    dashboard_cmd.add_argument("--workspace")
    dashboard_cmd.add_argument("--out")
    dashboard_cmd.add_argument("--json", action="store_true")
    dashboard_cmd.set_defaults(func=cmd_dashboard)

    prune_cmd = subparsers.add_parser(
        "prune", help="rotate append-only JSONL logs (keeps the most recent rows)"
    )
    prune_cmd.add_argument("--workspace")
    prune_cmd.add_argument("--probes", action="store_true")
    prune_cmd.add_argument("--outcomes", action="store_true")
    prune_cmd.add_argument("--discovery", action="store_true")
    prune_cmd.add_argument("--gaps", action="store_true")
    prune_cmd.add_argument("--uninstalled", action="store_true")
    prune_cmd.add_argument("--updates", action="store_true")
    prune_cmd.add_argument("--audit", action="store_true")
    prune_cmd.add_argument("--all", action="store_true")
    prune_cmd.add_argument("--keep", type=int, default=1000)
    prune_cmd.add_argument("--dry-run", action="store_true")
    prune_cmd.add_argument("--json", action="store_true")
    prune_cmd.set_defaults(func=cmd_prune)

    trash_cmd = subparsers.add_parser(
        "trash", help="list or clean installed/.trash"
    )
    trash_cmd.add_argument("--workspace")
    trash_cmd.add_argument("--clean", action="store_true")
    trash_cmd.add_argument("--older-than-days", type=float, default=30)
    trash_cmd.add_argument("--yes", action="store_true")
    trash_cmd.add_argument("--json", action="store_true")
    trash_cmd.set_defaults(func=cmd_trash)

    setup_cmd = subparsers.add_parser(
        "setup", help="print or merge an MCP server entry for a host"
    )
    setup_cmd.add_argument("--host", required=True, choices=list(HOSTS))
    setup_cmd.add_argument(
        "--config", help="explicit config path (bundle directory for deepseek-harness)"
    )
    setup_cmd.add_argument("--write", action="store_true", help="write the host config or bundle")
    setup_cmd.add_argument(
        "--portable",
        action="store_true",
        help=(
            "deepseek-harness only: emit a bundle with no path from this machine "
            "(python -m caprouter, workspace resolved at startup) for distribution"
        ),
    )
    setup_cmd.add_argument(
        "--link-skills",
        action="store_true",
        help="copy installed skills into the host skill root (deepseek-harness only)",
    )
    setup_cmd.add_argument(
        "--skills-root", help="override the host skill root (default: <DSH_HOME>/skills)"
    )
    setup_cmd.add_argument(
        "--host-root", help="alias for --skills-root (host skill directory)"
    )
    setup_cmd.add_argument("--force", action="store_true", help="replace an existing bundle copy")
    setup_cmd.add_argument("--workspace")
    setup_cmd.add_argument("--json", action="store_true")
    setup_cmd.set_defaults(func=cmd_setup)

    lock_cmd = subparsers.add_parser(
        "lock", help="write .caprouter/caprouter.lock.json for the current registry"
    )
    lock_cmd.add_argument("--workspace")
    lock_cmd.add_argument("--out")
    lock_cmd.add_argument("--live", action="store_true", help="rehash skill/plugin paths")
    lock_cmd.add_argument("--json", action="store_true")
    lock_cmd.set_defaults(func=cmd_lock)

    drift_cmd = subparsers.add_parser(
        "drift", help="compare the current registry with the lockfile"
    )
    drift_cmd.add_argument("--workspace")
    drift_cmd.add_argument("--lock")
    drift_cmd.add_argument("--live", action="store_true", help="rehash skill/plugin paths")
    drift_cmd.add_argument("--check", action="store_true", help="exit 1 when drift is found")
    drift_cmd.add_argument("--json", action="store_true")
    drift_cmd.set_defaults(func=cmd_drift)

    duplicates_cmd = subparsers.add_parser(
        "duplicates", help="list duplicate skill/plugin copies (read-only)"
    )
    duplicates_cmd.add_argument("--workspace")
    duplicates_cmd.add_argument("--prefer", help="path whose root should win duplicate resolution")
    duplicates_cmd.add_argument("--json", action="store_true")
    duplicates_cmd.set_defaults(func=cmd_duplicates)

    health_cmd = subparsers.add_parser(
        "health", help="derive capability lifecycle state from probes/outcomes"
    )
    health_cmd.add_argument("--workspace")
    health_cmd.add_argument("--id")
    health_cmd.add_argument(
        "--state", choices=["draft", "probation", "active", "degraded", "retired"]
    )
    health_cmd.add_argument("--probe", action="store_true", help="run static probes first")
    health_cmd.add_argument("--json", action="store_true")
    health_cmd.set_defaults(func=cmd_health)

    status_cmd = subparsers.add_parser("status", help="show state, registry, agents, runs and audit")
    status_cmd.add_argument("--workspace")
    status_cmd.add_argument("--tail", type=int, default=10)
    status_cmd.add_argument("--json", action="store_true")
    status_cmd.set_defaults(func=cmd_status)

    agents_cmd = subparsers.add_parser("agents", help="list CLI agents and runtime availability")
    agents_cmd.add_argument("--workspace")
    agents_cmd.add_argument("--json", action="store_true")
    agents_cmd.set_defaults(func=cmd_agents)

    audit_cmd = subparsers.add_parser("audit", help="show recent hook audit events")
    audit_cmd.add_argument("--tail", type=int, default=20)
    audit_cmd.add_argument("--workspace")
    audit_cmd.add_argument("--json", action="store_true")
    audit_cmd.set_defaults(func=cmd_audit)

    runs_cmd = subparsers.add_parser("runs", help="show recent run folders")
    runs_cmd.add_argument("--workspace")
    runs_cmd.add_argument("--json", action="store_true")
    runs_cmd.set_defaults(func=cmd_runs)

    backups_cmd = subparsers.add_parser("backups", help="list configuration backups")
    backups_cmd.add_argument("--workspace")
    backups_cmd.add_argument("--json", action="store_true")
    backups_cmd.set_defaults(func=cmd_backups)

    backup_cmd = subparsers.add_parser("backup", help="back up config, registry, state and plans")
    backup_cmd.add_argument("--workspace")
    backup_cmd.add_argument("--label")
    backup_cmd.set_defaults(func=cmd_backup)

    restore_cmd = subparsers.add_parser("restore", help="restore a backup (destructive)")
    restore_cmd.add_argument("--workspace")
    restore_cmd.add_argument("--from", dest="source", required=True)
    restore_cmd.add_argument("--force", action="store_true")
    restore_cmd.set_defaults(func=cmd_restore)

    verify_cmd = subparsers.add_parser("verify", help="deterministic claim-ledger verification")
    verify_cmd.add_argument("--claims", required=True)
    verify_cmd.add_argument("--json", action="store_true")
    verify_cmd.add_argument("--strict", action="store_true")
    verify_cmd.set_defaults(func=cmd_verify)

    state_cmd = subparsers.add_parser("state", help="show or change the global on/off switch")
    state_group = state_cmd.add_mutually_exclusive_group()
    state_group.add_argument("--enable", action="store_true")
    state_group.add_argument("--disable", action="store_true")
    state_cmd.add_argument("--json", action="store_true")
    state_cmd.set_defaults(func=cmd_state)

    doctor = subparsers.add_parser("doctor", help="check config, registry, hashes and agents")
    doctor.add_argument("--workspace")
    doctor.add_argument("--no-hash", dest="hash", action="store_false", help="skip hash checks")
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(func=cmd_doctor, hash=True)

    run = subparsers.add_parser("run", help="dispatch a plan (dry-run by default)")
    run.add_argument("--plan", required=True)
    run.add_argument("--workspace")
    run.add_argument("--execute", action="store_true", help="execute the plan through CLI adapters")
    run.add_argument("--adapter", help="override adapter: codebuddy / qwen / kimi / mock")
    run.add_argument("--timeout", type=int, default=300)
    run.add_argument("--max-turns", type=int, default=8)
    run.add_argument("--max-rework", type=int, default=2)
    run.set_defaults(func=cmd_run)

    host_cmd = subparsers.add_parser("host", help="prompt wrapper: route one prompt through Phase 1")
    host_cmd.add_argument("--prompt")
    host_cmd.add_argument("--stdin", action="store_true")
    host_cmd.add_argument("--workspace")
    host_cmd.add_argument("--adapter", help="override adapter: codebuddy / qwen / kimi / mock")
    host_cmd.add_argument("--timeout", type=int, default=300)
    host_cmd.add_argument("--max-turns", type=int, default=8)
    host_cmd.add_argument("--json", action="store_true")
    host_cmd.set_defaults(func=cmd_host)

    plan_parser = subparsers.add_parser("plan", help="plan utilities")
    plan_sub = plan_parser.add_subparsers(dest="plan_command", required=True)
    check = plan_sub.add_parser("check", help="validate a plan and detect contract conflicts")
    check.add_argument("--file", required=True)
    check.add_argument("--json", action="store_true")
    check.set_defaults(func=cmd_plan_check)

    hook = subparsers.add_parser("hook", help="host hook entrypoints")
    hook_sub = hook.add_subparsers(dest="hook_command", required=True)
    pre = hook_sub.add_parser("pre-tool-use")
    pre.set_defaults(func=cmd_hook)

    capability = subparsers.add_parser("capability", help="capability bus: search, get, invoke, verify, outcomes")
    capability_sub = capability.add_subparsers(dest="capability_command", required=True)

    cap_search = capability_sub.add_parser("search", help="search local capabilities with confidence/abstain")
    cap_search.add_argument("--need", required=True)
    cap_search.add_argument("--top", type=int, default=5)
    cap_search.add_argument("--type", action="append", choices=["skill", "plugin", "cli-agent", "project", "script", "mcp"])
    cap_search.add_argument("--capability", action="append", help="required capability tag")
    cap_search.add_argument("--domain", action="append", help="required domain")
    cap_search.add_argument("--min-confidence", type=float, default=None)
    cap_search.add_argument("--min-margin", type=float, default=None)
    cap_search.add_argument("--min-coverage", type=float, default=None)
    cap_search.add_argument("--include-unavailable", action="store_true")
    cap_search.add_argument("--record-gap", action="store_true")
    cap_search.add_argument("--workspace")
    cap_search.add_argument("--json", action="store_true")
    cap_search.set_defaults(func=cmd_capability_search)

    cap_get = capability_sub.add_parser("get", help="get one Capability Card")
    cap_get.add_argument("--id", required=True)
    cap_get.add_argument("--workspace")
    cap_get.set_defaults(func=cmd_capability_get)

    cap_invoke = capability_sub.add_parser("invoke", help="invoke a capability")
    cap_invoke.add_argument("--id", required=True)
    cap_invoke.add_argument("--input")
    cap_invoke.add_argument("--stdin", action="store_true")
    cap_invoke.add_argument("--cwd")
    cap_invoke.add_argument("--timeout", type=int, default=60)
    cap_invoke.add_argument("--allow-execute", action="store_true")
    cap_invoke.add_argument(
        "--allow-external-content",
        action="store_true",
        help="explicitly allow a prompt-mode content_path outside the capability/workspace",
    )
    cap_invoke.add_argument("--workspace")
    cap_invoke.set_defaults(func=cmd_capability_invoke)

    cap_verify = capability_sub.add_parser("verify", help="deterministic acceptance checks")
    cap_verify.add_argument("--artifact")
    cap_verify.add_argument("--text")
    cap_verify.add_argument("--acceptance", help="JSON file or inline JSON")
    cap_verify.add_argument("--criterion", action="append", help="free-text or structured criterion (repeatable)")
    cap_verify.add_argument("--workspace")
    cap_verify.add_argument("--json", action="store_true")
    cap_verify.set_defaults(func=cmd_capability_verify)

    cap_outcome = capability_sub.add_parser("outcome", help="record an invocation outcome")
    cap_outcome.add_argument("--id", required=True)
    cap_outcome.add_argument("--task")
    cap_outcome.add_argument("--success", action="store_true")
    cap_outcome.add_argument("--failure", action="store_true")
    cap_outcome.add_argument("--duration-ms", type=float)
    cap_outcome.add_argument("--human-edit-minutes", type=float)
    cap_outcome.add_argument("--rework", type=int, default=0)
    cap_outcome.add_argument("--notes")
    cap_outcome.add_argument("--workspace")
    cap_outcome.set_defaults(func=cmd_capability_outcome)

    cap_gap = capability_sub.add_parser("gap", help="record a missing capability")
    cap_gap.add_argument("--need", required=True)
    cap_gap.add_argument("--reason")
    cap_gap.add_argument("--tried", action="append")
    cap_gap.add_argument("--workaround")
    cap_gap.add_argument("--workspace")
    cap_gap.set_defaults(func=cmd_capability_gap)

    cap_gaps = capability_sub.add_parser("gaps", help="list missing capabilities")
    cap_gaps.add_argument("--limit", type=int, default=50)
    cap_gaps.add_argument("--workspace")
    cap_gaps.add_argument("--json", action="store_true")
    cap_gaps.set_defaults(func=cmd_capability_gaps)

    cap_probe = capability_sub.add_parser("probe", help="run deterministic capability probes")
    cap_probe.add_argument("--id")
    cap_probe.add_argument("--all", action="store_true", help="probe every registry entry")
    cap_probe.add_argument("--allow-execute", action="store_true", help="allow command probes with check.allow=true")
    cap_probe.add_argument("--hash", action="store_true", help="also compare the stored content hash")
    cap_probe.add_argument("--timeout", type=int, default=60)
    cap_probe.add_argument("--workspace")
    cap_probe.add_argument("--json", action="store_true")
    cap_probe.set_defaults(func=cmd_capability_probe)

    cap_probe_record = capability_sub.add_parser("probe-record", help="record a host-executed probe result")
    cap_probe_record.add_argument("--id", required=True)
    cap_probe_record.add_argument("--task", default="")
    probe_status = cap_probe_record.add_mutually_exclusive_group()
    probe_status.add_argument("--pass", dest="passed", action="store_true")
    probe_status.add_argument("--fail", dest="failed", action="store_true")
    cap_probe_record.add_argument("--duration-ms", type=float)
    cap_probe_record.add_argument("--notes")
    cap_probe_record.add_argument("--workspace")
    cap_probe_record.set_defaults(func=cmd_capability_probe_record)

    cap_probe_report = capability_sub.add_parser("probe-report", help="show probe history and summaries")
    cap_probe_report.add_argument("--id")
    cap_probe_report.add_argument("--limit", type=int, default=50)
    cap_probe_report.add_argument("--workspace")
    cap_probe_report.add_argument("--json", action="store_true")
    cap_probe_report.set_defaults(func=cmd_capability_probe_report)

    cap_draft = capability_sub.add_parser("draft", help="generate a Card draft from a registry id or local path")
    cap_draft.add_argument("--id")
    cap_draft.add_argument("--path")
    cap_draft.add_argument(
        "--all",
        action="store_true",
        help="draft every project from projects_scan.json (or skills/plugins with --type)",
    )
    cap_draft.add_argument(
        "--registry",
        action="store_true",
        help="with --all: also draft registry skills/plugins",
    )
    cap_draft.add_argument("--limit", type=int, default=None, help="max drafts with --all")
    cap_draft.add_argument("--type", choices=["skill", "plugin", "project"])
    cap_draft.add_argument("--name")
    cap_draft.add_argument("--out")
    cap_draft.add_argument("--apply", action="store_true", help="merge the draft into the overlay")
    cap_draft.add_argument("--force", action="store_true", help="overwrite existing non-empty overlay fields")
    cap_draft.add_argument("--workspace")
    cap_draft.add_argument("--json", action="store_true")
    cap_draft.set_defaults(func=cmd_capability_draft)

    deps = subparsers.add_parser(
        "deps", help="read-only dependency planning and checking (never executes package managers)"
    )
    deps_sub = deps.add_subparsers(dest="deps_command", required=True)
    deps_plan = deps_sub.add_parser(
        "plan", help="produce exact install commands for a human or host to run"
    )
    deps_plan.add_argument("--id", required=True)
    deps_plan.add_argument("--workspace")
    deps_plan.add_argument("--json", action="store_true")
    deps_plan.set_defaults(func=cmd_deps_plan)
    deps_check = deps_sub.add_parser(
        "check", help="check declared dependencies against the current interpreter/node_modules"
    )
    deps_check.add_argument("--id", required=True)
    deps_check.add_argument("--workspace")
    deps_check.add_argument("--json", action="store_true")
    deps_check.set_defaults(func=cmd_deps_check)

    pipeline = subparsers.add_parser(
        "pipeline",
        help="plan, verify and record a local capability chain",
    )
    pipeline_sub = pipeline.add_subparsers(dest="pipeline_command", required=True)

    pipe_list = pipeline_sub.add_parser("list", help="list pipeline definitions")
    pipe_list.add_argument("--workspace")
    pipe_list.add_argument("--json", action="store_true")
    pipe_list.set_defaults(func=cmd_pipeline_list)

    pipe_plan = pipeline_sub.add_parser("plan", help="select capabilities and prepare a run packet")
    pipe_plan.add_argument("--id", required=True)
    pipe_plan.add_argument("--task", default="")
    pipe_plan.add_argument("--input", help="input file copied into the run packet")
    pipe_plan.add_argument("--workspace")
    pipe_plan.set_defaults(func=cmd_pipeline_plan)

    pipe_run = pipeline_sub.add_parser(
        "run", help="execute command-mode steps deterministically (prompt steps stop)"
    )
    pipe_run.add_argument("--run", required=True)
    pipe_run.add_argument("--allow-execute", action="store_true")
    pipe_run.add_argument("--timeout", type=int, default=120)
    pipe_run.add_argument("--workspace")
    pipe_run.add_argument("--json", action="store_true")
    pipe_run.set_defaults(func=cmd_pipeline_run)

    pipe_verify = pipeline_sub.add_parser("verify", help="run deterministic acceptance checks")
    pipe_verify.add_argument("--run", required=True, help="run directory returned by pipeline plan")
    pipe_verify.add_argument("--allow-execute", action="store_true", help="allow command acceptance")
    pipe_verify.add_argument("--timeout", type=int, default=120)
    pipe_verify.add_argument("--workspace")
    pipe_verify.set_defaults(func=cmd_pipeline_verify)

    pipe_outcome = pipeline_sub.add_parser("outcome", help="record outcomes for every step")
    pipe_outcome.add_argument("--run", required=True)
    pipe_outcome.add_argument("--success", action="store_true")
    pipe_outcome.add_argument("--failure", action="store_true")
    pipe_outcome.add_argument("--duration-ms", type=float)
    pipe_outcome.add_argument("--human-edit-minutes", type=float)
    pipe_outcome.add_argument("--notes")
    pipe_outcome.add_argument("--workspace")
    pipe_outcome.set_defaults(func=cmd_pipeline_outcome)

    synthesize = subparsers.add_parser(
        "synthesize",
        help="turn a recorded capability gap + task trace into a reviewable local skill/script",
    )
    synthesize_sub = synthesize.add_subparsers(dest="synthesize_command", required=True)

    syn_propose = synthesize_sub.add_parser("propose", help="generate a candidate skill/script and Card draft (no registration)")
    syn_propose.add_argument("--workspace")
    syn_propose.add_argument("--task", required=True, help="task that exposed the gap")
    syn_propose.add_argument("--trace", help="JSON task trace path, or inline notes")
    syn_propose.add_argument("--gap-need", help="match a recorded gap by need (default: latest gap)")
    syn_propose.add_argument("--gap-index", type=int, help="1-based gap index (default: latest)")
    syn_propose.add_argument("--name", help="candidate name; lowercase letters/digits/hyphens recommended")
    syn_propose.add_argument("--type", dest="kind", choices=["skill", "script"], default="skill")
    syn_propose.add_argument("--notes", help="additional notes for the candidate")
    syn_propose.add_argument("--json", action="store_true")
    syn_propose.set_defaults(func=cmd_synthesize_propose)

    syn_list = synthesize_sub.add_parser("list", help="list synthesized candidates")
    syn_list.add_argument("--workspace")
    syn_list.add_argument("--json", action="store_true")
    syn_list.set_defaults(func=cmd_synthesize_list)

    syn_refine = synthesize_sub.add_parser("refine", help="refine a candidate with builtin templates, skill-creator guidance or a CLI adapter")
    syn_refine.add_argument("--workspace")
    syn_refine.add_argument("--candidate", required=True, help="candidate slug, id, or directory")
    syn_refine.add_argument("--method", choices=["auto", "builtin", "skill-creator", "adapter"], default="auto")
    syn_refine.add_argument("--agent", help="adapter name for --method adapter/auto (qwen, codebuddy, kimi)")
    syn_refine.add_argument("--timeout", type=int, default=120)
    syn_refine.add_argument("--json", action="store_true")
    syn_refine.set_defaults(func=cmd_synthesize_refine)

    syn_approve = synthesize_sub.add_parser("approve", help="install a candidate, refresh registry, create/review its Card")
    syn_approve.add_argument("--workspace")
    syn_approve.add_argument("--candidate", required=True, help="candidate slug, id, or directory")
    syn_approve.add_argument("--apply", action="store_true", help="also apply the Card if it passes review")
    syn_approve.add_argument(
        "--auto",
        action="store_true",
        help="install only if the built-in deterministic review returns auto_approved",
    )
    syn_approve.add_argument("--force", action="store_true", help="replace an existing installed skill")
    syn_approve.add_argument("--json", action="store_true")
    syn_approve.set_defaults(func=cmd_synthesize_approve)

    syn_improve = synthesize_sub.add_parser("improve", help="propose (or apply) a versioned patch from outcomes")
    syn_improve.add_argument("--workspace")
    syn_improve.add_argument("--id", required=True, help="synthesized capability id")
    syn_improve.add_argument("--notes", help="review notes / failure summary")
    syn_improve.add_argument("--apply", action="store_true", help="install the improved skill after explicit approval")
    syn_improve.add_argument("--json", action="store_true")
    syn_improve.set_defaults(func=cmd_synthesize_improve)

    install_cmd = subparsers.add_parser("install", help="install a local skill/plugin/project (manual approval, no execution)")
    install_cmd.add_argument("--source", required=True, help="local directory or .zip")
    install_cmd.add_argument("--type", choices=["skill", "plugin", "project"])
    install_cmd.add_argument("--name", help="override the installed name")
    install_cmd.add_argument("--approve", action="store_true", help="required to copy files")
    install_cmd.add_argument(
        "--approve-hash",
        help="pin the source hash from the preview; refuses to install if the source changed",
    )
    install_cmd.add_argument("--force", action="store_true", help="replace an existing installation")
    install_cmd.add_argument("--allow-symlinks", action="store_true", help="copy symlinks as links")
    install_cmd.add_argument("--payload-only", action="store_true", help="do not copy provenance/vendor artifacts")
    install_cmd.add_argument("--skip-provenance", action="store_true", help="install even if provenance hash mismatches")
    install_cmd.add_argument("--no-draft", action="store_true", help="skip Card draft generation")
    install_cmd.add_argument("--workspace")
    install_cmd.add_argument("--json", action="store_true")
    install_cmd.set_defaults(func=cmd_install)

    discover_cmd = subparsers.add_parser(
        "discover",
        help="search the skills.sh catalog (read-only; requires --allow-network)",
    )
    discover_cmd.add_argument("--query", required=True)
    discover_cmd.add_argument("--source", choices=["skills.sh"], default="skills.sh")
    discover_cmd.add_argument("--limit", type=int, default=10)
    discover_cmd.add_argument("--offset", type=int, default=0, help="client-side offset (max 99)")
    discover_cmd.add_argument("--allow-network", action="store_true", help="required for remote access")
    discover_cmd.add_argument("--offline", action="store_true", help="read the local discovery cache")
    discover_cmd.add_argument("--timeout", type=int, default=30)
    discover_cmd.add_argument("--workspace")
    discover_cmd.add_argument("--json", action="store_true")
    discover_cmd.set_defaults(func=cmd_discover)

    fetch_cmd = subparsers.add_parser(
        "fetch",
        help="download a remote skill into quarantine (no install, no execution)",
    )
    fetch_cmd.add_argument("--candidate", help="owner/repository/skill")
    fetch_cmd.add_argument("--url", help="https ZIP URL from an allowlisted host")
    fetch_cmd.add_argument("--allow-network", action="store_true", help="required for remote access")
    fetch_cmd.add_argument("--force", action="store_true", help="replace an existing quarantine copy")
    fetch_cmd.add_argument("--timeout", type=int, default=60)
    fetch_cmd.add_argument("--workspace")
    fetch_cmd.set_defaults(func=cmd_fetch)

    vendor_cmd = subparsers.add_parser(
        "vendor",
        help="deconstruct a quarantined skill into a six-step report and Card draft",
    )
    vendor_cmd.add_argument("--candidate", help="owner/repository/skill")
    vendor_cmd.add_argument("--dir", help="explicit quarantine directory (e.g. a URL fetch)")
    vendor_cmd.add_argument(
        "--method",
        choices=["skill-deconstruction"],
        default="skill-deconstruction",
    )
    vendor_cmd.add_argument("--workspace")
    vendor_cmd.add_argument("--json", action="store_true")
    vendor_cmd.set_defaults(func=cmd_vendor)

    quarantine_cmd = subparsers.add_parser("quarantine", help="list or clean remote quarantine")
    quarantine_sub = quarantine_cmd.add_subparsers(dest="quarantine_command", required=True)

    quarantine_list = quarantine_sub.add_parser("list", help="list quarantined downloads")
    quarantine_list.add_argument("--workspace")
    quarantine_list.add_argument("--json", action="store_true")
    quarantine_list.set_defaults(func=cmd_quarantine_list)

    quarantine_clean = quarantine_sub.add_parser("clean", help="remove quarantined downloads")
    quarantine_clean.add_argument("--candidate", help="owner/repository/skill")
    quarantine_clean.add_argument("--older-than-days", type=float)
    quarantine_clean.add_argument("--yes", action="store_true", help="actually delete; default is preview")
    quarantine_clean.add_argument("--workspace")
    quarantine_clean.add_argument("--json", action="store_true")
    quarantine_clean.set_defaults(func=cmd_quarantine_clean)

    uninstall_cmd = subparsers.add_parser("uninstall", help="remove an installed capability (preview by default)")
    uninstall_cmd.add_argument("--id", required=True)
    uninstall_cmd.add_argument("--yes", action="store_true", help="actually uninstall; default is preview")
    uninstall_cmd.add_argument("--host", help="also remove the host-side copy (deepseek-harness, cherry, ...)")
    uninstall_cmd.add_argument(
        "--unlink-host",
        action="store_true",
        help="with --host: remove the copy in that host's skill root",
    )
    uninstall_cmd.add_argument("--host-root", help="override the host skill root for unlinking")
    uninstall_cmd.add_argument(
        "--force",
        action="store_true",
        help="remove the host copy even if it differs from the installed copy",
    )
    uninstall_cmd.add_argument("--workspace")
    uninstall_cmd.add_argument("--json", action="store_true")
    uninstall_cmd.set_defaults(func=cmd_uninstall)

    rollback_cmd = subparsers.add_parser("rollback", help="restore the latest uninstalled capability")
    rollback_cmd.add_argument("--id", required=True)
    rollback_cmd.add_argument("--yes", action="store_true", help="actually rollback; default is preview")
    rollback_cmd.add_argument("--force", action="store_true", help="displace an existing target first")
    rollback_cmd.add_argument("--version", help="restore a specific version from .trash")
    rollback_cmd.add_argument("--hash", help="restore a specific manifest hash from .trash")
    rollback_cmd.add_argument("--workspace")
    rollback_cmd.add_argument("--json", action="store_true")
    rollback_cmd.set_defaults(func=cmd_rollback)

    unmanaged_cmd = subparsers.add_parser(
        "unmanaged", help="list skills/plugins that exist outside installed/ (read-only)"
    )
    unmanaged_cmd.add_argument("--workspace")
    unmanaged_cmd.add_argument("--json", action="store_true")
    unmanaged_cmd.set_defaults(func=cmd_unmanaged)

    adopt_cmd = subparsers.add_parser(
        "adopt", help="copy an existing skill/plugin into installed/ (original untouched)"
    )
    adopt_cmd.add_argument("--id", required=True, help="registry id, e.g. skill:demo")
    adopt_cmd.add_argument("--name", help="override the managed copy name")
    adopt_cmd.add_argument("--yes", action="store_true", help="required to copy files")
    adopt_cmd.add_argument("--force", action="store_true", help="replace an existing managed copy")
    adopt_cmd.add_argument("--allow-symlinks", action="store_true", help="copy symlinks as links")
    adopt_cmd.add_argument("--no-draft", action="store_true", help="skip Card draft generation")
    adopt_cmd.add_argument("--workspace")
    adopt_cmd.add_argument("--json", action="store_true")
    adopt_cmd.set_defaults(func=cmd_adopt)

    backup_skill_cmd = subparsers.add_parser(
        "backup-skill", help="copy a skill/plugin to .caprouter/backups/skills/"
    )
    backup_skill_cmd.add_argument("--id", required=True)
    backup_skill_cmd.add_argument("--out", help="backup directory (default: workspace backups)")
    backup_skill_cmd.add_argument("--workspace")
    backup_skill_cmd.add_argument("--json", action="store_true")
    backup_skill_cmd.set_defaults(func=cmd_backup_skill)

    versions_cmd = subparsers.add_parser(
        "versions", help="list installed and trashed versions of a capability"
    )
    versions_cmd.add_argument("--id", required=True)
    versions_cmd.add_argument("--workspace")
    versions_cmd.add_argument("--json", action="store_true")
    versions_cmd.set_defaults(func=cmd_versions)

    update_cmd = subparsers.add_parser(
        "update", help="preview or apply an update for a managed capability"
    )
    update_cmd.add_argument("--id", required=True)
    update_cmd.add_argument("--source", required=True, help="local directory or .zip")
    update_cmd.add_argument("--approve", action="store_true", help="required to copy files")
    update_cmd.add_argument("--no-draft", action="store_true", help="skip Card draft generation")
    update_cmd.add_argument("--allow-symlinks", action="store_true")
    update_cmd.add_argument("--payload-only", action="store_true")
    update_cmd.add_argument("--skip-provenance", action="store_true")
    update_cmd.add_argument("--workspace")
    update_cmd.add_argument("--json", action="store_true")
    update_cmd.set_defaults(func=cmd_update)

    installed_cmd = subparsers.add_parser("installed", help="list locally installed capabilities")
    installed_cmd.add_argument("--workspace")
    installed_cmd.add_argument("--json", action="store_true")
    installed_cmd.set_defaults(func=cmd_installed)

    mcp_list = subparsers.add_parser(
        "mcp-list",
        help="list MCP servers from host configs (read-only, secrets redacted)",
    )
    mcp_list.add_argument("--workspace")
    mcp_list.add_argument("--config", action="append", help="extra config path (repeatable)")
    mcp_list.add_argument("--json", action="store_true")
    mcp_list.set_defaults(func=cmd_mcp_list)

    mcp = subparsers.add_parser("mcp", help="run the Capability Bus MCP stdio server")
    mcp.add_argument("--workspace")
    mcp.add_argument(
        "--tools",
        choices=("core", "full"),
        default="full",
        help="core exposes 8 host-facing tools; full exposes all 20 (default: full)",
    )
    mcp.add_argument(
        "--host",
        help="current host id (deepseek-harness, claude-code, cherry, ...)",
    )
    mcp.set_defaults(func=cmd_mcp)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
