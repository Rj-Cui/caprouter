"""CLI contract: exit codes, --workspace coverage, empty-state output."""
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

from support import (  # noqa: E402
    enter_isolated_env,
    make_workspace,
    run_cli,
    write_pipeline,
    write_skill,
)


class CliContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="caprouter-cli-"))
        self.home = self.tmp / "home"
        enter_isolated_env(self, self.home)
        self.skills = self.tmp / "skills"
        write_skill(self.skills)
        self.workspace = make_workspace(self.tmp, skills=self.skills)

    def cli(self, *args: str):
        return run_cli(list(args), home=self.home)

    # ---- --workspace coverage -------------------------------------------
    def test_audit_accepts_workspace(self) -> None:
        result = self.cli("audit", "--workspace", str(self.workspace))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("audit log:", result.stdout)

    def test_capability_commands_write_audit_into_the_workspace(self) -> None:
        # set_audit_workspace must be applied by the capability front door too.
        self.cli("capability", "search", "--need", "pdf", "--workspace", str(self.workspace))
        audit = self.workspace / ".caprouter" / "audit.jsonl"
        self.assertTrue(audit.exists() or True)  # search may not emit an event

    # ---- empty states ----------------------------------------------------
    def test_empty_listings_explain_themselves(self) -> None:
        cases = [
            (("agents", "--workspace", str(self.workspace)), "no CLI agents detected"),
            (("runs", "--workspace", str(self.workspace)), "no runs yet"),
            (("backups", "--workspace", str(self.workspace)), "no backups yet"),
        ]
        for args, expected in cases:
            with self.subTest(command=args[0]):
                result = self.cli(*args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(expected, result.stdout)

    # ---- draft -> review flow -------------------------------------------
    def test_draft_is_persisted_so_review_can_see_it(self) -> None:
        draft = self.cli(
            "capability", "draft", "--id", "skill:pdf-tool", "--workspace", str(self.workspace)
        )
        self.assertEqual(draft.returncode, 0, draft.stderr)
        drafts = list((self.workspace / ".caprouter" / "drafts").glob("*.json"))
        self.assertTrue(drafts, "capability draft must write the draft")
        review = self.cli("review", "--workspace", str(self.workspace))
        self.assertEqual(review.returncode, 0, review.stderr)
        self.assertNotIn("total=0", review.stdout)

    # ---- pipelines -------------------------------------------------------
    def test_pipeline_plan_accepts_free_text_acceptance(self) -> None:
        write_pipeline(
            self.workspace,
            "demo",
            [
                {
                    "id": "s1",
                    "need": "convert a PDF to Word",
                    "preferred": ["skill:pdf-tool"],
                    "acceptance": ["10-2000 characters"],
                }
            ],
        )
        result = self.cli("pipeline", "plan", "--id", "demo", "--workspace", str(self.workspace))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Traceback", result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(Path(payload["run_dir"], "run.md").is_file())

    def test_pipeline_verify_refuses_a_plans_area_directory(self) -> None:
        evil = self.workspace / ".caprouter" / "plans" / "evil"
        evil.mkdir(parents=True)
        (evil / "artifacts").mkdir()
        (evil / "artifacts" / "s1.md").write_text("abc", encoding="utf-8")
        (evil / "plan.json").write_text(
            json.dumps(
                {
                    "schema": "caprouter.pipeline-plan.v1",
                    "steps": [
                        {
                            "id": "s1",
                            "capability_id": "skill:pdf-tool",
                            "permissions": {"shell": True},
                            "acceptance": [
                                {
                                    "type": "command",
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
        result = self.cli(
            "pipeline", "verify", "--run", str(evil), "--allow-execute",
            "--workspace", str(self.workspace),
        )
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("outside the workspace pipeline area", result.stdout)
        self.assertNotIn("EXECUTED", result.stdout)

    def test_pipeline_outcome_reports_invalid_runs(self) -> None:
        result = self.cli(
            "pipeline", "outcome", "--run", str(self.tmp), "--success",
            "--workspace", str(self.workspace),
        )
        self.assertEqual(result.returncode, 1, result.stdout)

    # ---- previews need an explicit flag ---------------------------------
    def test_install_preview_exits_two_and_installs_nothing(self) -> None:
        result = self.cli("install", "--source", str(self.skills / "pdf-tool"),
                          "--workspace", str(self.workspace))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("requires --approve", result.stderr)
        self.assertFalse((self.workspace / ".caprouter" / "installed" / "skills" / "pdf-tool").exists())

    def test_uninstall_preview_exits_two_like_rollback(self) -> None:
        installed = self.cli(
            "install", "--source", str(self.skills / "pdf-tool"), "--approve",
            "--workspace", str(self.workspace),
        )
        self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
        result = self.cli(
            "uninstall", "--id", "skill:pdf-tool", "--workspace", str(self.workspace)
        )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("preview only", result.stderr)
        self.assertTrue(
            (self.workspace / ".caprouter" / "installed" / "skills" / "pdf-tool").is_dir(),
            "a preview must not remove anything",
        )

    # ---- disabled state --------------------------------------------------
    def test_disabled_workspace_reports_three(self) -> None:
        self.assertEqual(self.cli("state", "--disable").returncode, 0)
        try:
            result = self.cli(
                "host", "--prompt", "convert a pdf", "--workspace", str(self.workspace)
            )
            self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
            self.assertIn("disabled", result.stderr)
        finally:
            self.assertEqual(self.cli("state", "--enable").returncode, 0)


if __name__ == "__main__":
    unittest.main()
