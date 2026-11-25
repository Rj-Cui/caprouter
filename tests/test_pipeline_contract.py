"""Pipeline planning / verification / run-directory trust boundary."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for _path in (ROOT, HERE):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from support import enter_isolated_env, make_workspace, write_pipeline, write_skill  # noqa: E402

from caprouter.pipelines import (  # noqa: E402
    _describe_check,
    pipeline_runs_dir,
    plan_pipeline,
    record_pipeline_outcome,
    run_pipeline,
    verify_pipeline,
)


class PipelinePlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="caprouter-pipeline-"))
        self.skills = self.tmp / "skills"
        write_skill(self.skills)
        enter_isolated_env(self, self.tmp / "home")
        self.workspace = make_workspace(self.tmp, skills=self.skills)

    def _plan(self, acceptance: list[object]) -> dict:
        write_pipeline(
            self.workspace,
            "demo",
            [
                {
                    "id": "s1",
                    "need": "convert a PDF to Word",
                    "preferred": ["skill:pdf-tool"],
                    "acceptance": acceptance,
                }
            ],
        )
        return plan_pipeline(self.workspace, "demo", task="convert it")

    def test_free_text_acceptance_does_not_crash(self) -> None:
        # Regression: _render_run_md assumed every check was a dict, so the
        # documented `--criterion "150-200 characters"` shape raised AttributeError.
        plan = self._plan(["10-2000 characters"])
        self.assertEqual(plan.get("status"), "ready")
        run_dir = Path(plan["run_dir"])
        self.assertTrue((run_dir / "plan.json").is_file())
        self.assertTrue((run_dir / "run.md").is_file())
        self.assertIn("10-2000 characters", (run_dir / "run.md").read_text(encoding="utf-8"))

    def test_structured_acceptance_still_renders(self) -> None:
        plan = self._plan([{"type": "chars", "min": 10, "max": 20}])
        run_md = (Path(plan["run_dir"]) / "run.md").read_text(encoding="utf-8")
        self.assertIn('"type": "chars"', run_md)

    def test_describe_check_handles_both_shapes(self) -> None:
        self.assertEqual(_describe_check("hello"), "hello")
        self.assertIn("chars", _describe_check({"type": "chars", "min": 1}))
        self.assertTrue(_describe_check({"type": "command", "argv": ["a", "b"]}).startswith("command"))

    def test_no_half_initialized_run_directory(self) -> None:
        self._plan(["10-2000 characters"])
        runs = pipeline_runs_dir(self.workspace)
        incomplete = [
            path.name
            for path in runs.iterdir()
            if path.is_dir() and not (path / "run.md").is_file()
        ]
        self.assertEqual(incomplete, [])

    def test_verify_and_outcome_on_a_real_run(self) -> None:
        plan = self._plan(["10-2000 characters"])
        run_dir = Path(plan["run_dir"])
        artifact = run_dir / "artifacts" / "s1.md"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("x" * 200, encoding="utf-8")
        verdict = verify_pipeline(self.workspace, run_dir)
        self.assertEqual(verdict.get("status"), "pass")
        outcome = record_pipeline_outcome(self.workspace, run_dir, success=True)
        self.assertEqual(outcome.get("status"), "recorded")
        ran = run_pipeline(self.workspace, run_dir)
        self.assertEqual(ran.get("status"), "awaiting_host")


class PipelineTrustBoundaryTests(unittest.TestCase):
    """`pipeline verify --allow-execute` runs argv taken from plan.json."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="caprouter-pipeline-trust-"))
        self.skills = self.tmp / "skills"
        write_skill(self.skills)
        enter_isolated_env(self, self.tmp / "home")
        self.workspace = make_workspace(self.tmp, skills=self.skills)

    def _hostile_plan(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "artifacts").mkdir(exist_ok=True)
        (directory / "artifacts" / "s1.md").write_text("abc", encoding="utf-8")
        (directory / "plan.json").write_text(
            json.dumps(
                {
                    "schema": "caprouter.pipeline-plan.v1",
                    "status": "ready",
                    "pipeline": "handwritten",
                    "steps": [
                        {
                            "id": "s1",
                            "capability_id": "skill:pdf-tool",
                            "skill_path": str(self.skills / "pdf-tool"),
                            "output": "artifacts/s1.md",
                            "permissions": {"shell": True},
                            "acceptance": [
                                {
                                    "type": "command",
                                    "label": "hostile",
                                    "allow": True,
                                    "argv": [sys.executable, "-c", "print('EXECUTED')"],
                                }
                            ],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return directory

    def test_verify_refuses_the_agent_writable_plans_area(self) -> None:
        hostile = self._hostile_plan(self.workspace / ".caprouter" / "plans" / "evil")
        result = verify_pipeline(self.workspace, hostile, allow_execute=True)
        self.assertEqual(result.get("status"), "invalid")
        self.assertIn("outside the workspace pipeline area", str(result.get("reason")))

    def test_verify_refuses_an_absolute_outside_directory(self) -> None:
        hostile = self._hostile_plan(self.tmp / "elsewhere")
        result = verify_pipeline(self.workspace, hostile, allow_execute=True)
        self.assertEqual(result.get("status"), "invalid")

    def test_run_and_outcome_refuse_the_same_paths(self) -> None:
        hostile = self._hostile_plan(self.tmp / "elsewhere2")
        self.assertEqual(run_pipeline(self.workspace, hostile, allow_execute=True).get("status"), "invalid")
        self.assertEqual(record_pipeline_outcome(self.workspace, hostile).get("status"), "invalid")

    def test_a_real_run_dir_still_executes_when_allowed(self) -> None:
        write_pipeline(
            self.workspace,
            "cmd",
            [
                {
                    "id": "s1",
                    "need": "convert a PDF to Word",
                    "preferred": ["skill:pdf-tool"],
                    "permissions": {"shell": True},
                    "acceptance": [
                        {
                            "type": "command",
                            "label": "echo",
                            "allow": True,
                            "argv": [sys.executable, "-c", "print('ok')"],
                            "expect_returncode": 0,
                        }
                    ],
                }
            ],
        )
        plan = plan_pipeline(self.workspace, "cmd")
        run_dir = Path(plan["run_dir"])
        (run_dir / "artifacts").mkdir(exist_ok=True)
        (run_dir / "artifacts" / "s1.md").write_text("artifact text", encoding="utf-8")
        verdict = verify_pipeline(self.workspace, run_dir, allow_execute=True)
        self.assertEqual(verdict.get("status"), "pass", verdict)
        stdout = json.dumps(verdict)
        self.assertIn("ok", stdout)


if __name__ == "__main__":
    unittest.main()
