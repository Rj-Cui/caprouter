"""Static HTML dashboard: health, duplicates, drift and registry counts."""
from __future__ import annotations

import html
from pathlib import Path
from typing import Any, Iterable

from .capabilities import load_entries
from .duplicates import find_duplicates
from .health import capability_health
from .lockfile import check_drift, lock_path
from .paths import config_path
from .util import now_iso, read_json

DASHBOARD_SCHEMA = "caprouter.dashboard.v1"


def dashboard_path(workspace: Path | str) -> Path:
    workspace = Path(workspace)
    config = read_json(config_path(workspace), {})
    delivery = Path(str(config.get("delivery") or "")) if isinstance(config, dict) else Path("")
    if not str(delivery) or not delivery.is_absolute():
        delivery = workspace / "deliverables"
    return delivery / "caprouter-dashboard.html"


def _table(headers: Iterable[str], rows: Iterable[Iterable[Any]]) -> str:
    head = "".join(f"<th>{html.escape(str(item))}</th>" for item in headers)
    body = []
    for row in rows:
        body.append(
            "<tr>" + "".join(f"<td>{html.escape(str(item))}</td>" for item in row) + "</tr>"
        )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def build_dashboard(
    workspace: Path | str,
    *,
    out: Path | str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    workspace = Path(workspace)
    health = capability_health(workspace)
    duplicates = find_duplicates(workspace)
    drift = None
    if lock_path(workspace).is_file():
        try:
            drift = check_drift(workspace)
        except ValueError:
            drift = None
    entries = load_entries(workspace)
    stats: dict[str, int] = {}
    for entry in entries:
        key = str(entry.get("type") or "unknown")
        stats[key] = stats.get(key, 0) + 1

    health_rows = [
        (
            item.get("state"),
            item.get("id"),
            item.get("probe_status") or "-",
            item.get("success_rate"),
            item.get("calls"),
            item.get("path"),
        )
        for item in health.get("capabilities", [])[:limit]
    ]
    duplicate_rows = [
        (group.get("type"), group.get("name"), group.get("count"), group.get("same_content"), group.get("winner"))
        for group in duplicates.get("groups", [])
    ]
    drift_rows = []
    if drift:
        drift_rows = [
            ("changed", item.get("id"), item.get("from_hash"), item.get("to_hash"))
            for item in drift.get("changed", [])
        ] + [("missing", item, "", "") for item in drift.get("missing", [])] + [
            ("new", item, "", "") for item in drift.get("new", [])
        ]

    duplicate_html = (
        _table(["type", "name", "copies", "same_content", "winner"], duplicate_rows)
        if duplicate_rows
        else '<p class="muted">No duplicate groups.</p>'
    )
    drift_html = (
        _table(["kind", "id", "from", "to"], drift_rows)
        if drift_rows
        else '<p class="muted">No lockfile or no drift.</p>'
    )
    content = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>CapRouter Dashboard</title>
<style>
body {{ font-family: ui-sans-serif, system-ui, sans-serif; margin: 2rem; color: #1f2328; }}
h1 {{ margin-bottom: .25rem; }}
.muted {{ color: #57606a; }}
table {{ border-collapse: collapse; width: 100%; margin: .75rem 0 1.5rem; }}
th, td {{ border-bottom: 1px solid #d0d7de; padding: .35rem .5rem; text-align: left; font-size: .9rem; }}
th {{ background: #f6f8fa; }}
code {{ background: #f6f8fa; padding: .1rem .25rem; }}
</style>
</head>
<body>
<h1>CapRouter Dashboard</h1>
<p class="muted">Generated {html.escape(now_iso())} · workspace <code>{html.escape(str(workspace))}</code></p>
<h2>Registry ({len(entries)})</h2>
{_table(["type", "count"], sorted(stats.items()))}
<h2>Lifecycle</h2>
<p>{_table(["state", "count"], sorted(health.get("counts", {}).items()))}</p>
{_table(["state", "id", "probe", "success_rate", "calls", "path"], health_rows)}
<h2>Duplicates ({duplicates.get("count", 0)})</h2>
{duplicate_html}
<h2>Drift</h2>
{drift_html}
</body>
</html>
"""
    path = Path(out).expanduser() if out else dashboard_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return {
        "schema": DASHBOARD_SCHEMA,
        "path": str(path),
        "generated_at": now_iso(),
        "registry": len(entries),
        "stats": stats,
        "health": health.get("counts"),
        "duplicates": duplicates.get("count"),
        "drift": drift.get("counts") if drift else None,
    }
