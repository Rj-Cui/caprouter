from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from caprouter.status import collect_status


class StatusTests(unittest.TestCase):
    def test_recent_runs_reads_executor_run_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            bus = workspace / ".caprouter"
            bus.mkdir()
            (bus / "config.json").write_text(
                json.dumps({"schema": "caprouter.config.v1"}), encoding="utf-8"
            )
            (bus / "registry.json").write_text(json.dumps({"entries": []}), encoding="utf-8")
            run_dir = bus / "runs" / "20260101-000000"
            run_dir.mkdir(parents=True)
            (run_dir / "run.json").write_text(
                json.dumps(
                    {
                        "schema": "caprouter.run.v1",
                        "task_id": "executor-task",
                        "status": "completed",
                        "dry_run": False,
                        "nodes": [{"id": "n1"}],
                        "created_at": "2026-01-01T00:00:00",
                    }
                ),
                encoding="utf-8",
            )
            status = collect_status(workspace, audit_limit=0)
        self.assertEqual(status["recent_runs"][0]["task_id"], "executor-task")
        self.assertEqual(status["recent_runs"][0]["status"], "completed")
        self.assertEqual(status["recent_runs"][0]["nodes"], 1)

    def test_recent_runs_falls_back_to_summary_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            bus = workspace / ".caprouter"
            bus.mkdir()
            (bus / "config.json").write_text(
                json.dumps({"schema": "caprouter.config.v1"}), encoding="utf-8"
            )
            (bus / "registry.json").write_text(json.dumps({"entries": []}), encoding="utf-8")
            run_dir = bus / "runs" / "20260101-000000"
            run_dir.mkdir(parents=True)
            (run_dir / "summary.json").write_text(
                json.dumps(
                    {
                        "schema": "caprouter.run.v1",
                        "task_id": "dry-run-task",
                        "dry_run": True,
                        "nodes": [],
                    }
                ),
                encoding="utf-8",
            )
            status = collect_status(workspace, audit_limit=0)
        self.assertEqual(status["recent_runs"][0]["task_id"], "dry-run-task")
        self.assertTrue(status["recent_runs"][0]["dry_run"])


if __name__ == "__main__":
    unittest.main()
