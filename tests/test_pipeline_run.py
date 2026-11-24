from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from caprouter.capabilities import bus_dir, overlay_path
from caprouter.dashboard import build_dashboard
from caprouter.maintenance import clean_trash, prune_logs, rotate_jsonl
from caprouter.pipelines import run_pipeline
from caprouter.util import read_jsonl, write_json_atomic
from caprouter.workspace import create_workspace


class PipelineRunTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "skills"
        self.skill = self.root / "cmd"
        self.skill.mkdir(parents=True)
        (self.skill / "SKILL.md").write_text(
            "---\nname: cmd\ndescription: Command skill\n---\n# Cmd\n", encoding="utf-8"
        )
        self.ws = base / "ws"
        create_workspace(
            self.ws,
            include_agents=False,
            include_default_roots=False,
            extra_skill_roots=[self.root],
        )
        config_path = self.ws / ".caprouter" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["bus"]["allow_execute"] = True
        write_json_atomic(config_path, config)
        self.run_dir = self.ws / ".caprouter" / "pipeline-runs" / "test-run"
        self.run_dir.mkdir(parents=True)
        self.plan = {
            "schema": "caprouter.pipeline-plan.v1",
            "status": "ready",
            "pipeline": "test",
            "task": "run",
            "run_dir": str(self.run_dir),
            "steps": [
                {
                    "id": "s1",
                    "capability_id": "skill:cmd",
                    "output": "artifacts/s1.md",
                    "acceptance": [],
                    "permissions": {"shell": True},
                }
            ],
        }
        write_json_atomic(self.run_dir / "plan.json", self.plan)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _set_invoke(self, invoke: dict) -> None:
        write_json_atomic(
            overlay_path(self.ws),
            {
                "cards": {
                    "skill:cmd": {
                        "invoke": invoke,
                        "permissions": {"shell": True, "fs": ["read"]},
                    }
                }
            },
        )

    def test_command_step_requires_approval_then_runs(self) -> None:
        self._set_invoke(
            {
                "mode": "command",
                "allow": True,
                "command": [sys.executable, "-c", "print('ok')"],
            }
        )
        blocked = run_pipeline(self.ws, self.run_dir, allow_execute=False)
        self.assertEqual(blocked["status"], "approval_required")
        ran = run_pipeline(self.ws, self.run_dir, allow_execute=True)
        self.assertEqual(ran["status"], "executed")
        transcript = Path(ran["steps"][0]["transcript"])
        self.assertTrue(transcript.is_file())
        self.assertEqual(ran["steps"][0]["status"], "ok")

    def test_prompt_step_awaits_host(self) -> None:
        self._set_invoke({"mode": "prompt", "content_path": str(self.skill / "SKILL.md")})
        result = run_pipeline(self.ws, self.run_dir, allow_execute=True)
        self.assertEqual(result["status"], "awaiting_host")
        self.assertEqual(result["steps"][0]["status"], "awaiting_host")


class DashboardMaintenanceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name) / "ws"
        create_workspace(self.ws, include_agents=False, include_default_roots=False)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_dashboard_is_written(self) -> None:
        result = build_dashboard(self.ws)
        path = Path(result["path"])
        self.assertTrue(path.is_file())
        text = path.read_text(encoding="utf-8")
        self.assertIn("CapRouter Dashboard", text)
        self.assertIn("Registry", text)

    def test_rotate_jsonl_keeps_tail(self) -> None:
        path = bus_dir(self.ws) / "probes.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(json.dumps({"n": index}) + "\n" for index in range(5)),
            encoding="utf-8",
        )
        result = rotate_jsonl(path, keep=2)
        self.assertEqual(result["status"], "rotated")
        rows = read_jsonl(path)
        self.assertEqual([row["n"] for row in rows], [3, 4])
        self.assertTrue(Path(result["backup"]).is_file())
        pruned = prune_logs(self.ws, kind="probes", keep=1, dry_run=False)
        self.assertEqual(pruned["after"], 1)

    def test_clean_trash_older_than(self) -> None:
        old = self.ws / ".caprouter" / "installed" / ".trash" / "old"
        old.mkdir(parents=True)
        (old / "manifest.json").write_text("{}", encoding="utf-8")
        past = time.time() - 10 * 86400
        os.utime(old, (past, past))
        preview = clean_trash(self.ws, older_than_days=1)
        self.assertEqual(preview["count"], 1)
        self.assertTrue(old.exists())
        cleaned = clean_trash(self.ws, older_than_days=1, dry_run=False)
        self.assertEqual(cleaned["status"], "cleaned")
        self.assertFalse(old.exists())


if __name__ == "__main__":
    unittest.main()
