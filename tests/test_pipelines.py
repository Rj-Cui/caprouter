from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from caprouter.pipelines import (
    list_pipelines,
    plan_pipeline,
    record_pipeline_outcome,
    verify_pipeline,
)


def _pipeline_definition(check_script: str) -> dict:
    return {
        "schema": "caprouter.pipeline.v1",
        "id": "fiction-deslop",
        "goal": "fiction de-AI pass",
        "steps": [
            {
                "id": "deslop",
                "need": "网文 去 AI 味 改稿",
                "preferred": ["skill:demo-deslop"],
                "fallback": ["skill:demo-tone"],
                "input": "{input}",
                "output": "artifacts/deslop.md",
                "acceptance": [
                    {
                        "type": "command",
                        "label": "patterns",
                        "allow": True,
                        "cwd": "capability",
                        "argv": [sys.executable, "scripts/check.py", "{output}"],
                        "expect_returncode": 0,
                    },
                    {"type": "min_chars", "value": 500, "unit": "chars"},
                ],
                "human_confirm": True,
                "permissions": {"shell": True},
            }
        ],
    }


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)
        bus = self.ws / ".caprouter"
        bus.mkdir()
        (bus / "config.json").write_text(
            json.dumps({"schema": "caprouter.config.v1", "routing": {}}), encoding="utf-8"
        )
        self.skill = self.ws / "skills" / "demo-deslop"
        (self.skill / "scripts").mkdir(parents=True)
        (self.skill / "SKILL.md").write_text("---\nname: demo-deslop\ndescription: 网文 去 AI 味\n---\n", encoding="utf-8")
        (self.skill / "scripts" / "check.py").write_text(
            "import sys\nfrom pathlib import Path\n"
            "text = Path(sys.argv[1]).read_text(encoding='utf-8')\n"
            "sys.exit(1 if 'BLOCK' in text else 0)\n",
            encoding="utf-8",
        )
        self.fallback = self.ws / "skills" / "demo-tone"
        self.fallback.mkdir(parents=True)
        (self.fallback / "SKILL.md").write_text(
            "---\nname: demo-tone\ndescription: 非虚构 去 AI 味\n---\n", encoding="utf-8"
        )
        self.entries = [
            {
                "id": "skill:demo-deslop",
                "type": "skill",
                "name": "demo-deslop",
                "path": str(self.skill),
                "description": "网文 去 AI 味 改稿",
                "keywords": ["网文", "去AI味"],
                "capabilities": ["transform"],
                "domain": "writing",
                "status": "verified",
            },
            {
                "id": "skill:demo-tone",
                "type": "skill",
                "name": "demo-tone",
                "path": str(self.fallback),
                "description": "非虚构 去 AI 味 改稿",
                "keywords": ["去AI味"],
                "capabilities": ["transform"],
                "domain": "writing",
                "status": "verified",
            },
        ]
        (bus / "registry.json").write_text(json.dumps({"entries": self.entries}, ensure_ascii=False), encoding="utf-8")
        pipelines = bus / "pipelines"
        pipelines.mkdir()
        (pipelines / "fiction-deslop.json").write_text(
            json.dumps(_pipeline_definition("scripts/check.py"), ensure_ascii=False),
            encoding="utf-8",
        )
        self.state = self.ws / "state.json"
        self.state.write_text(
            json.dumps({"schema": "caprouter.state.v1", "enabled": True}), encoding="utf-8"
        )
        self.env = patch.dict(
            os.environ,
            {
                "CAPROUTER_STATE": str(self.state),
                "CAPROUTER_AUDIT": str(self.ws / "audit.jsonl"),
            },
        )
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def test_list_pipelines(self) -> None:
        data = list_pipelines(self.ws)
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["pipelines"][0]["id"], "fiction-deslop")

    def test_plan_selects_preferred_capability(self) -> None:
        plan = plan_pipeline(self.ws, "fiction-deslop", task="去味第一章")
        self.assertEqual(plan["status"], "ready")
        self.assertEqual(plan["steps"][0]["capability_id"], "skill:demo-deslop")
        self.assertTrue((Path(plan["run_dir"]) / "plan.json").is_file())
        self.assertTrue((Path(plan["run_dir"]) / "run.md").is_file())

    def test_plan_falls_back_when_preferred_is_missing(self) -> None:
        registry = self.ws / ".caprouter" / "registry.json"
        registry.write_text(json.dumps({"entries": [self.entries[1]]}, ensure_ascii=False), encoding="utf-8")
        plan = plan_pipeline(self.ws, "fiction-deslop", task="去味第一章")
        self.assertEqual(plan["status"], "ready")
        self.assertEqual(plan["steps"][0]["capability_id"], "skill:demo-tone")

    def test_plan_abstains_without_candidates(self) -> None:
        (self.ws / ".caprouter" / "registry.json").write_text(json.dumps({"entries": []}), encoding="utf-8")
        plan = plan_pipeline(self.ws, "fiction-deslop", task="去味第一章")
        self.assertEqual(plan["status"], "abstain")
        gaps = (self.ws / ".caprouter" / "gaps.jsonl").read_text(encoding="utf-8")
        self.assertIn("no candidate", gaps)

    def _make_plan(self, text: str) -> tuple[dict, Path]:
        plan = plan_pipeline(self.ws, "fiction-deslop", task="去味第一章")
        run_dir = Path(plan["run_dir"])
        artifact = run_dir / "artifacts" / "deslop.md"
        artifact.write_text(text, encoding="utf-8")
        return plan, artifact

    def test_verify_passes_when_commands_and_text_checks_pass(self) -> None:
        _plan, _artifact = self._make_plan("人" * 600)
        result = verify_pipeline(self.ws, _plan["run_dir"], allow_execute=True)
        self.assertEqual(result["status"], "pass")
        self.assertTrue((Path(_plan["run_dir"]) / "verify.json").is_file())

    def test_verify_fails_when_command_fails(self) -> None:
        _plan, _artifact = self._make_plan("BLOCK" + "人" * 600)
        result = verify_pipeline(self.ws, _plan["run_dir"], allow_execute=True)
        self.assertEqual(result["status"], "fail")
        checks = result["steps"][0]["checks"]
        self.assertTrue(any(item.get("label") == "patterns" and item["status"] == "fail" for item in checks))

    def test_verify_skips_commands_without_allow_execute(self) -> None:
        _plan, _artifact = self._make_plan("人" * 600)
        result = verify_pipeline(self.ws, _plan["run_dir"], allow_execute=False)
        self.assertEqual(result["status"], "incomplete")

    def test_verify_skips_commands_when_disabled(self) -> None:
        _plan, _artifact = self._make_plan("人" * 600)
        self.state.write_text(
            json.dumps({"schema": "caprouter.state.v1", "enabled": False}), encoding="utf-8"
        )
        result = verify_pipeline(self.ws, _plan["run_dir"], allow_execute=True)
        self.assertEqual(result["status"], "incomplete")
        self.assertTrue(any(item.get("status") == "skipped" for item in result["steps"][0]["checks"]))

    def test_verify_requires_step_shell_permission(self) -> None:
        path = self.ws / ".caprouter" / "pipelines" / "fiction-deslop.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["steps"][0]["permissions"] = {"shell": False}
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        _plan, _artifact = self._make_plan("人" * 600)
        result = verify_pipeline(self.ws, _plan["run_dir"], allow_execute=True)
        self.assertEqual(result["status"], "incomplete")

    def test_outcome_records_every_step(self) -> None:
        _plan, _artifact = self._make_plan("人" * 600)
        result = record_pipeline_outcome(
            self.ws, _plan["run_dir"], success=True, human_edit_minutes=12.5, notes="first run"
        )
        self.assertEqual(result["status"], "recorded")
        rows = (self.ws / ".caprouter" / "outcomes.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0])["capability_id"], "skill:demo-deslop")
        self.assertEqual(json.loads(rows[0])["human_edit_minutes"], 12.5)


if __name__ == "__main__":
    unittest.main()
