"""Minimal MCP stdio server for the CapRouter capability bus.

The server speaks newline-delimited JSON-RPC 2.0 on stdin/stdout. It has no
third-party dependency; the goal is to make the bus callable from Claude Code,
Cherry, or any other MCP host without pulling in an SDK.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .audit import set_audit_workspace
from .capabilities import (
    get_capability,
    invoke_capability,
    list_gaps,
    record_gap,
    record_outcome,
    search_capabilities,
    verify_artifact,
)
from .cards import generate_draft
from .deps import check_dependencies, plan_dependencies
from .duplicates import find_duplicates
from .health import capability_health
from .hostaware import (
    HOST_LABELS,
    annotate_card,
    annotate_search_result,
    link_managed_skill,
    normalize_host,
    prefer_host_loadable,
    unlink_managed_skill,
)
from .install import install_source, preview_install
from .lifecycle import list_versions, uninstall_capability
from .lockfile import check_drift, write_lock
from .paths import find_workspace_or_plugin
from .probes import probe_report, record_probe, run_all_probes, run_probes
from .remote import discover_skills

PROTOCOL_VERSION = "2025-06-18"

TOOLS: list[dict[str, Any]] = [
    {
        "name": "capability_search",
        "description": "Find local capabilities (skills, plugins, projects, scripts, CLI agents) for a task. Returns top candidates, confidence, and an abstain decision when nothing fits.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "The subtask or need, in natural language."},
                "top": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
                "types": {"type": "array", "items": {"type": "string"}, "description": "Filter by capability type."},
                "capabilities": {"type": "array", "items": {"type": "string"}, "description": "Required capability tags, e.g. retrieve or verify."},
                "domains": {"type": "array", "items": {"type": "string"}, "description": "Filter by domain, e.g. research or office."},
                "min_confidence": {"type": "number", "description": "Override the abstain threshold."},
                "min_margin": {"type": "number", "description": "Override the top-1/top-2 margin threshold."},
                "min_coverage": {"type": "number", "description": "Minimum query-token coverage for the top candidate."},
                "include_unavailable": {"type": "boolean", "default": False},
                "record_gap": {"type": "boolean", "default": False},
                "prefer_host": {
                    "type": "boolean",
                    "default": True,
                    "description": "Rank candidates loadable by the current host first and switch the selection when the top match belongs to another host.",
                }
            },
            "required": ["task"]
        }
    },
    {
        "name": "capability_get",
        "description": "Get the full Capability Card (manifest, examples, probe, stats, invoke contract) for one capability.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"]
        }
    },
    {
        "name": "capability_invoke",
        "description": "Invoke a capability. Prompt-mode capabilities return their content path and instructions; command-mode capabilities require explicit execution permissions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "input": {"type": "string", "description": "Prompt or input passed to the capability."},
                "cwd": {"type": "string"},
                "timeout": {"type": "integer", "minimum": 1, "maximum": 600, "default": 60},
                "allow_execute": {"type": "boolean", "default": False}
            },
            "required": ["id"]
        }
    },
    {
        "name": "capability_verify",
        "description": "Run deterministic acceptance checks on an artifact (length, item counts, format, JSON, claim ledger, hash). No LLM is called.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "artifact_path": {"type": "string"},
                "text": {"type": "string"},
                "acceptance": {
                    "description": "A list of criteria. Each item can be a string or a structured check object.",
                    "anyOf": [
                        {"type": "array", "items": {}},
                        {"type": "object"},
                        {"type": "string"}
                    ]
                },
                "evidence": {"type": "array", "items": {"type": "string"}}
            }
        }
    },
    {
        "name": "capability_record_outcome",
        "description": "Record the outcome of a capability invocation so future routing can rank by empirical success and human-edit cost.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "task": {"type": "string"},
                "success": {"type": "boolean"},
                "duration_ms": {"type": "number"},
                "human_edit_minutes": {"type": "number"},
                "rework": {"type": "integer", "default": 0},
                "notes": {"type": "string"}
            },
            "required": ["id"]
        }
    },
    {
        "name": "capability_record_gap",
        "description": "Record a missing capability so it can be added to the roadmap instead of being improvised.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "need": {"type": "string"},
                "reason": {"type": "string"},
                "tried": {"type": "array", "items": {"type": "string"}},
                "workaround": {"type": "string"}
            },
            "required": ["need"]
        }
    },
    {
        "name": "capability_list_gaps",
        "description": "List recorded capability gaps.",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50}}
        }
    },
    {
        "name": "capability_probe",
        "description": "Run deterministic probes for one capability or the whole registry. Static checks (path, SKILL.md lint, plugin manifest, invoke content) run without permission; command probes need check.allow plus config and caller permission. Host tasks are returned as pending.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Capability id; omit when all=true."},
                "all": {"type": "boolean", "default": False},
                "allow_execute": {"type": "boolean", "default": False},
                "check_hash": {"type": "boolean", "default": False},
                "timeout": {"type": "integer", "minimum": 1, "maximum": 600, "default": 60}
            }
        }
    },
    {
        "name": "capability_probe_record",
        "description": "Record a host-executed probe result for a capability. Use this after running a pending host task; the result becomes behavioral evidence.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "task": {"type": "string"},
                "status": {"type": "string", "enum": ["pass", "fail", "pending"]},
                "duration_ms": {"type": "number"},
                "notes": {"type": "string"}
            },
            "required": ["id", "status"]
        }
    },
    {
        "name": "capability_probe_report",
        "description": "Show probe history and aggregated probe status, optionally for one capability.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50}
            }
        }
    },
    {
        "name": "capability_card_draft",
        "description": "Generate a deterministic Capability Card draft from a registry id or a local path. The draft is returned for review; it is not written to the overlay.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "path": {"type": "string"},
                "type": {"type": "string", "enum": ["skill", "plugin", "project"]},
                "name": {"type": "string"}
            }
        }
    },
    {
        "name": "capability_discover",
        "description": "Search the public skills.sh catalog read-only and return remote candidates. It does NOT download or install; use the CLI fetch/vendor/install --approve flow after the user approves. Requires allow_network=true and explicit user consent.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "source": {"type": "string", "enum": ["skills.sh"], "default": "skills.sh"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                "offset": {"type": "integer", "minimum": 0, "maximum": 99, "default": 0},
                "allow_network": {"type": "boolean", "default": False}
            },
            "required": ["query"]
        }
    },
    {
        "name": "capability_install",
        "description": "Install a LOCAL skill/plugin/project from a directory or ZIP. Returns a preview unless approve=true. To approve, call once with approve=false, show the preview to the user, then call again with approve=true and approve_hash set to the returned source_hash. Remote fetch/install stays CLI-only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "type": {"type": "string", "enum": ["skill", "plugin", "project"]},
                "name": {"type": "string"},
                "approve": {"type": "boolean", "default": False},
                "approve_hash": {"type": "string", "description": "source_hash from the preview call"},
                "force": {"type": "boolean", "default": False},
                "payload_only": {"type": "boolean", "default": False},
                "link_host": {
                    "type": "boolean",
                    "default": True,
                    "description": "After a successful install, copy the skill into the current host's skill root so the host can load it immediately. Only skills are linked.",
                },
                "host_root": {
                    "type": "string",
                    "description": "Override the host skill root used for linking.",
                }
            },
            "required": ["source"]
        }
    },
    {
        "name": "capability_uninstall",
        "description": "Preview removing a managed capability by default. Set yes=true only after explicit user confirmation; the copy moves to installed/.trash and the original external path is untouched. With unlink_host (default true) the host-side copy is removed too, unless it was modified locally.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "yes": {"type": "boolean", "default": False},
                "unlink_host": {
                    "type": "boolean",
                    "default": True,
                    "description": "Also remove the copy in the current host's skill root.",
                },
                "force": {
                    "type": "boolean",
                    "default": False,
                    "description": "Delete the host copy even when it differs from the installed copy.",
                },
                "host_root": {
                    "type": "string",
                    "description": "Override the host skill root used for unlinking.",
                }
            },
            "required": ["id"]
        }
    },
    {
        "name": "capability_versions",
        "description": "List the current installed version and versions preserved in installed/.trash for one capability.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"]
        }
    },
    {
        "name": "capability_health",
        "description": "Derive lifecycle state (draft/probation/active/degraded/retired) from probes and outcomes. Read-only.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string"}}
        }
    },
    {
        "name": "capability_duplicates",
        "description": "List duplicate skill/plugin copies across configured roots, with content equality and the currently winning path. Read-only.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "capability_lock",
        "description": "Write .caprouter/caprouter.lock.json for the current registry, optionally rehashing live skill/plugin directories.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "out": {"type": "string"},
                "live": {"type": "boolean", "default": False}
            }
        }
    },
    {
        "name": "capability_drift",
        "description": "Compare the current registry with the lockfile and report missing/new/changed capabilities.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "lock": {"type": "string"},
                "live": {"type": "boolean", "default": False}
            }
        }
    },
    {
        "name": "capability_deps",
        "description": "Read-only dependency plan/check for one capability. Returns exact install commands and risk flags; never executes pip/npm. Use action=plan for the host to run after user approval, action=check for what is already installed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "action": {"type": "string", "enum": ["plan", "check"], "default": "plan"}
            },
            "required": ["id"]
        }
    }
]


CORE_TOOL_NAMES = (
    "capability_search",
    "capability_get",
    "capability_invoke",
    "capability_verify",
    "capability_install",
    "capability_uninstall",
    "capability_health",
    "capability_duplicates",
)


def tools_for(profile: str) -> list[dict[str, Any]]:
    """Return the tool list for a profile.

    ``core`` (default for new host installs) keeps the model-facing surface at
    eight tools; ``full`` exposes all twenty for debugging and power users.
    """
    if str(profile).strip().lower() == "full":
        return TOOLS
    wanted = set(CORE_TOOL_NAMES)
    return [tool for tool in TOOLS if tool.get("name") in wanted]


def resolve_workspace(value: str | None) -> Path:
    if value:
        return Path(value).expanduser().resolve()
    env = os.environ.get("CAPROUTER_WORKSPACE")
    if env:
        return Path(env).expanduser().resolve()
    found = find_workspace_or_plugin(Path.cwd())
    return found if found else Path.cwd().resolve()


def _as_list(value: Any) -> list[Any] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return list(value)
    return None


def _normalize_acceptance(value: Any) -> list[Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        checks = value.get("checks")
        return list(checks) if isinstance(checks, list) else [value]
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _dispatch(
    workspace: Path,
    name: str,
    args: dict[str, Any],
    host: str = "unknown",
) -> dict[str, Any]:
    if name == "capability_search":
        task = str(args.get("task") or "").strip()
        if not task:
            raise ValueError("task is required")
        result = search_capabilities(
            workspace,
            task,
            top=int(args.get("top") or 5),
            types=_as_list(args.get("types")),
            capabilities=_as_list(args.get("capabilities")),
            domains=_as_list(args.get("domains")),
            min_confidence=args.get("min_confidence"),
            min_margin=args.get("min_margin"),
            min_coverage=args.get("min_coverage"),
            include_unavailable=bool(args.get("include_unavailable")),
            record_gap=bool(args.get("record_gap")),
        )
        if bool(args.get("prefer_host", True)):
            return prefer_host_loadable(result, host=host, workspace=workspace)
        return annotate_search_result(result, host=host, workspace=workspace)
    if name == "capability_discover":
        query = str(args.get("query") or "").strip()
        if not query:
            raise ValueError("query is required")
        return discover_skills(
            workspace,
            query,
            source=str(args.get("source") or "skills.sh"),
            limit=int(args.get("limit") or 10),
            offset=int(args.get("offset") or 0),
            allow_network=bool(args.get("allow_network")),
        )
    if name == "capability_get":
        capability_id = str(args.get("id") or "").strip()
        if not capability_id:
            raise ValueError("id is required")
        card = get_capability(workspace, capability_id)
        if not card:
            return {"status": "not_found", "id": capability_id}
        return annotate_card(card, host=host, workspace=workspace)
    if name == "capability_invoke":
        capability_id = str(args.get("id") or "").strip()
        if not capability_id:
            raise ValueError("id is required")
        return invoke_capability(
            workspace,
            capability_id,
            input_text=str(args.get("input") or ""),
            cwd=args.get("cwd"),
            timeout=int(args.get("timeout") or 60),
            allow_execute=bool(args.get("allow_execute")),
        )
    if name == "capability_verify":
        return verify_artifact(
            workspace,
            artifact_path=args.get("artifact_path"),
            text=args.get("text"),
            acceptance=_normalize_acceptance(args.get("acceptance")),
            evidence=_as_list(args.get("evidence")),
        )
    if name == "capability_record_outcome":
        capability_id = str(args.get("id") or "").strip()
        if not capability_id:
            raise ValueError("id is required")
        return record_outcome(
            workspace,
            capability_id,
            task=str(args.get("task") or ""),
            success=args.get("success") if isinstance(args.get("success"), bool) else None,
            duration_ms=args.get("duration_ms"),
            human_edit_minutes=args.get("human_edit_minutes"),
            rework=int(args.get("rework") or 0),
            notes=str(args.get("notes") or ""),
        )
    if name == "capability_record_gap":
        need = str(args.get("need") or "").strip()
        if not need:
            raise ValueError("need is required")
        return record_gap(
            workspace,
            need=need,
            reason=str(args.get("reason") or ""),
            tried=_as_list(args.get("tried")),
            workaround=str(args.get("workaround") or ""),
        )
    if name == "capability_list_gaps":
        return list_gaps(workspace, limit=int(args.get("limit") or 50))
    if name == "capability_probe":
        capability_id = str(args.get("id") or "").strip()
        if bool(args.get("all")):
            return run_all_probes(
                workspace,
                allow_execute=bool(args.get("allow_execute")),
                check_hash=bool(args.get("check_hash")),
                timeout=int(args.get("timeout") or 60),
            )
        if not capability_id:
            raise ValueError("id is required unless all=true")
        return run_probes(
            workspace,
            capability_id,
            allow_execute=bool(args.get("allow_execute")),
            check_hash=bool(args.get("check_hash")),
            timeout=int(args.get("timeout") or 60),
        )
    if name == "capability_probe_record":
        capability_id = str(args.get("id") or "").strip()
        if not capability_id:
            raise ValueError("id is required")
        return record_probe(
            workspace,
            capability_id,
            task=str(args.get("task") or ""),
            status=str(args.get("status") or "pass"),
            duration_ms=args.get("duration_ms"),
            notes=str(args.get("notes") or ""),
        )
    if name == "capability_probe_report":
        return probe_report(
            workspace,
            capability_id=args.get("id"),
            limit=int(args.get("limit") or 50),
        )
    if name == "capability_card_draft":
        capability_id = str(args.get("id") or "").strip() or None
        path = args.get("path")
        if not capability_id and not path:
            raise ValueError("id or path is required")
        return generate_draft(
            workspace,
            capability_id=capability_id,
            path=path,
            type_hint=args.get("type"),
            name=args.get("name"),
        )
    if name == "capability_install":
        source = str(args.get("source") or "").strip()
        if not source:
            raise ValueError("source is required")
        type_hint = args.get("type")
        install_name = args.get("name")
        preview = preview_install(workspace, source, type_hint=type_hint, name=install_name)
        if not bool(args.get("approve")):
            return preview
        expected = str(args.get("approve_hash") or "").strip()
        if not expected:
            return {
                "status": "approval_required",
                "reason": "show the preview to the user, then pass approve_hash=preview.source_hash",
                "preview": preview,
            }
        if expected != str(preview.get("source_hash") or ""):
            return {
                "status": "source_changed",
                "preview_hash": expected,
                "current_hash": preview.get("source_hash"),
            }
        result = install_source(
            workspace,
            source,
            type_hint=type_hint,
            name=install_name,
            approve=True,
            force=bool(args.get("force")),
            payload_only=bool(args.get("payload_only")),
        )
        current_host = normalize_host(host)
        if (
            isinstance(result, dict)
            and result.get("status") == "installed"
            and current_host != "unknown"
            and bool(args.get("link_host", True))
        ):
            identifier = str((result.get("manifest") or {}).get("id") or "")
            if identifier:
                result["host_link"] = link_managed_skill(
                    workspace,
                    identifier,
                    host=current_host,
                    force=bool(args.get("force")),
                    root=args.get("host_root"),
                )
        return result
    if name == "capability_uninstall":
        capability_id = str(args.get("id") or "").strip()
        if not capability_id:
            raise ValueError("id is required")
        result = uninstall_capability(
            workspace, capability_id, dry_run=not bool(args.get("yes"))
        )
        current_host = normalize_host(host)
        if current_host != "unknown" and bool(args.get("unlink_host", True)):
            result["host_unlink"] = unlink_managed_skill(
                workspace,
                capability_id,
                host=current_host,
                removed_hash=result.get("content_hash"),
                force=bool(args.get("force")),
                root=args.get("host_root"),
            )
        return result
    if name == "capability_versions":
        capability_id = str(args.get("id") or "").strip()
        if not capability_id:
            raise ValueError("id is required")
        return list_versions(workspace, capability_id)
    if name == "capability_health":
        return capability_health(workspace, capability_id=args.get("id"))
    if name == "capability_duplicates":
        return find_duplicates(workspace)
    if name == "capability_lock":
        return write_lock(workspace, out=args.get("out"), live=bool(args.get("live")))
    if name == "capability_drift":
        return check_drift(workspace, lock=args.get("lock"), live=bool(args.get("live")))
    if name == "capability_deps":
        capability_id = str(args.get("id") or "").strip()
        if not capability_id:
            raise ValueError("id is required")
        action = str(args.get("action") or "plan").strip().lower()
        if action == "plan":
            return plan_dependencies(workspace, capability_id)
        if action == "check":
            return check_dependencies(workspace, capability_id)
        raise ValueError("action must be plan or check")
    raise ValueError(f"unknown tool: {name}")


def _error(request_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _tool_result(payload: Any) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
        "isError": False,
    }


def _handle(
    workspace: Path,
    request: dict[str, Any],
    *,
    host: str = "unknown",
    profile: str = "core",
) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")
    params = request.get("params") if isinstance(request.get("params"), dict) else {}

    # Notifications have no id and must not receive a response.
    if request_id is None and method:
        return None

    if method == "initialize":
        client = params.get("clientInfo") if isinstance(params.get("clientInfo"), dict) else {}
        client_host = normalize_host(client.get("name"))
        if host == "unknown" and client_host != "unknown":
            host = client_host
        return _result(request_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "caprouter", "version": __version__},
            "instructions": (
                "Local capability bus. Search before improvising; verify before declaring done. "
                f"Host-aware: results carry host.loadable for {HOST_LABELS.get(host, host)}."
            ),
        })
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": tools_for(profile)})
    if method == "tools/call":
        name = str(params.get("name") or "")
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        try:
            payload = _dispatch(workspace, name, arguments, host=host)
        except (ValueError, KeyError, TypeError) as exc:
            return _result(request_id, {
                "content": [{"type": "text", "text": json.dumps({"error": str(exc)}, ensure_ascii=False)}],
                "isError": True,
            })
        except Exception as exc:  # noqa: BLE001 - keep the server alive
            return _result(request_id, {
                "content": [{"type": "text", "text": json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False)}],
                "isError": True,
            })
        return _result(request_id, _tool_result(payload))
    if method == "resources/list":
        return _result(request_id, {"resources": []})
    if method == "prompts/list":
        return _result(request_id, {"prompts": []})
    if method == "logging/setLevel":
        return _result(request_id, {})
    return _error(request_id, -32601, f"method not found: {method}")


def serve(workspace: Path, *, host: str = "unknown", profile: str = "full") -> int:
    if normalize_host(host) == "unknown":
        host = normalize_host(os.environ.get("CAPROUTER_HOST"))
    set_audit_workspace(workspace)
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(
        f"[caprouter-mcp] workspace={workspace} host={host} tools={profile}",
        file=sys.stderr,
        flush=True,
    )
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            response = _error(None, -32700, f"parse error: {exc}")
        else:
            if not isinstance(request, dict):
                response = _error(None, -32600, "invalid request")
            else:
                response = _handle(workspace, request, host=host, profile=profile)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="caprouter mcp", description="CapRouter MCP stdio server")
    parser.add_argument("--workspace", help="CapRouter workspace (default: discover from cwd)")
    parser.add_argument(
        "--tools",
        choices=("core", "full"),
        default="full",
        help="core exposes 8 tools for hosts; full exposes all 20 (default: full)",
    )
    parser.add_argument(
        "--host",
        default=None,
        help="Current host id (deepseek-harness, claude-code, cherry, ...); "
        "also read from CAPROUTER_HOST",
    )
    args = parser.parse_args(argv)
    host = normalize_host(args.host or os.environ.get("CAPROUTER_HOST") or "unknown")
    return serve(resolve_workspace(args.workspace), host=host, profile=args.tools)


if __name__ == "__main__":
    raise SystemExit(main())
