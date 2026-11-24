from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from unittest import mock

from caprouter.adapters import AdapterResult
from caprouter.capabilities import load_overlay, record_gap, record_outcome
from caprouter.scan import lint_skill
from caprouter.synthesize import (
    approve_synthesis,
    improve_synthesis,
    list_syntheses,
    propose_synthesis,
    refine_candidate,
)
from caprouter.util import write_json_atomic


def _workspace(tmp: str) -> Path:
    workspace = Path(tmp) / "ws"
    bus = workspace / ".caprouter"
    bus.mkdir(parents=True)
    skill_root = Path(tmp) / "skills"
    skill_root.mkdir()
    write_json_atomic(
        bus / "config.json",
        {
            "schema": "caprouter.config.v1",
            "skill_roots": [str(skill_root)],
            "plugin_roots": [],
            "scan_agents": False,
            "routing": {},
            "review": {},
        },
    )
    write_json_atomic(bus / "registry.json", {"entries": []})
    return workspace


class SynthesisTests(unittest.TestCase):
    def test_requires_a_recorded_gap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = _workspace(tmp)
            report = propose_synthesis(workspace, task="Do a repeatable task")
        self.assertEqual(report["status"], "no_gap")

    def test_propose_creates_candidate_without_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = _workspace(tmp)
            record_gap(workspace, need="No local game design tool", reason="registry search aborted")
            report = propose_synthesis(
                workspace,
                task="Design a rule-driven text adventure",
                name="demo-game-designer",
                kind="script",
                trace={
                    "goal": "Design a rule-driven text adventure",
                    "inputs": ["worldview", "company loop"],
                    "steps": [{"action": "extract loop", "tool": "notes", "result": "loop"}],
                    "outputs": ["rulebook"],
                    "acceptance": ["no cards", "one example"],
                },
            )
            registry = json.loads((workspace / ".caprouter" / "registry.json").read_text(encoding="utf-8"))
            candidate = json.loads(Path(report["candidate_dir"]).joinpath("candidate.json").read_text(encoding="utf-8"))
            skill_exists = (Path(report["skill_dir"]) / "SKILL.md").is_file()
            script_exists = (Path(report["skill_dir"]) / "scripts" / "tool.py").is_file()
        self.assertEqual(report["status"], "proposed")
        self.assertTrue(skill_exists)
        self.assertTrue(script_exists)
        self.assertEqual(registry["entries"], [])
        self.assertEqual(candidate["status"], "proposed")

    def test_approve_installs_and_reviews_without_auto_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = _workspace(tmp)
            record_gap(workspace, need="No local game design tool")
            proposed = propose_synthesis(
                workspace,
                task="Design a rule-driven text adventure",
                name="demo-game-designer",
                trace={"goal": "Design a rule-driven text adventure", "examples": []},
            )
            approved = approve_synthesis(workspace, proposed["candidate_dir"])
            registry = json.loads((workspace / ".caprouter" / "registry.json").read_text(encoding="utf-8"))
            overlay = load_overlay(workspace)
        self.assertEqual(approved["status"], "approved")
        self.assertEqual(approved["review"]["applied"], 0)
        self.assertTrue(any(entry["id"] == approved["capability_id"] for entry in registry["entries"]))
        self.assertEqual(overlay["cards"], {})

    def test_auto_approve_stops_before_install_when_review_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = _workspace(tmp)
            record_gap(workspace, need="No local game design tool")
            proposed = propose_synthesis(
                workspace,
                task="Design a rule-driven text adventure",
                name="demo-game-designer",
            )
            with mock.patch(
                "caprouter.synthesize.review_drafts",
                return_value={
                    "counts": {"needs_review": 1},
                    "applied": 0,
                    "items": [
                        {
                            "id": proposed["candidate"]["id"],
                            "status": "needs_review",
                            "risk": "medium",
                            "score": 0.9,
                            "issues": [],
                        }
                    ],
                },
            ):
                report = approve_synthesis(workspace, proposed["candidate_dir"], auto=True)
            registry = json.loads((workspace / ".caprouter" / "registry.json").read_text(encoding="utf-8"))
            candidate = json.loads(
                Path(proposed["candidate_dir"]).joinpath("candidate.json").read_text(encoding="utf-8")
            )
            installed = (workspace / ".caprouter" / "installed" / "skills" / "demo-game-designer").exists()
            draft = (workspace / ".caprouter" / "drafts" / "projects" / "skill-demo-game-designer.json").exists()
        self.assertEqual(report["status"], "review_failed")
        self.assertFalse(any(entry["id"] == proposed["candidate"]["id"] for entry in registry["entries"]))
        self.assertEqual(candidate["status"], "proposed")
        self.assertFalse(installed)
        self.assertFalse(draft)

    def test_auto_approve_installs_when_review_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = _workspace(tmp)
            record_gap(workspace, need="No local game design tool")
            proposed = propose_synthesis(
                workspace,
                task="Design a rule-driven text adventure",
                name="demo-game-designer",
            )
            with mock.patch(
                "caprouter.synthesize.review_drafts",
                return_value={
                    "counts": {"auto_approved": 1},
                    "applied": 0,
                    "items": [
                        {
                            "id": proposed["candidate"]["id"],
                            "status": "auto_approved",
                            "risk": "low",
                            "score": 0.95,
                            "issues": [],
                        }
                    ],
                },
            ):
                report = approve_synthesis(workspace, proposed["candidate_dir"], auto=True)
            registry = json.loads((workspace / ".caprouter" / "registry.json").read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "approved")
        self.assertTrue(any(entry["id"] == report["capability_id"] for entry in registry["entries"]))

    def test_improve_creates_patch_without_replacing_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = _workspace(tmp)
            record_gap(workspace, need="No local game design tool")
            proposed = propose_synthesis(
                workspace,
                task="Design a rule-driven text adventure",
                name="demo-game-designer",
            )
            approved = approve_synthesis(workspace, proposed["candidate_dir"])
            installed_skill = Path(approved["installed_path"]) / "SKILL.md"
            before = installed_skill.read_text(encoding="utf-8")
            record_outcome(
                workspace,
                approved["capability_id"],
                task="second similar task",
                success=False,
                human_edit_minutes=20,
                notes="forgot the acceptance checks",
            )
            improved = improve_synthesis(
                workspace,
                approved["capability_id"],
                notes="add acceptance checks to workflow",
            )
            after = installed_skill.read_text(encoding="utf-8")
            patch = json.loads(Path(improved["patch_path"]).read_text(encoding="utf-8"))
        self.assertEqual(improved["status"], "improvement_proposed")
        self.assertEqual(before, after)
        self.assertEqual(patch["from_version"], "0.1.0")
        self.assertEqual(patch["to_version"], "0.1.1")

    def test_builtin_refine_needs_no_external_tool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = _workspace(tmp)
            record_gap(workspace, need="No local game design tool")
            proposed = propose_synthesis(
                workspace,
                task="Design a rule-driven text adventure",
                name="demo-game-designer",
            )
            refined = refine_candidate(workspace, proposed["candidate_dir"], method="builtin")
            text = Path(refined["skill_path"]).read_text(encoding="utf-8")
            backups = list(Path(refined["refinement_dir"]).glob("*"))
        self.assertEqual(refined["status"], "refined_builtin")
        self.assertEqual(refined["method"], "builtin")
        self.assertIn("## Workflow", text)
        self.assertIn("## Checks", text)
        self.assertTrue(any(path.name.endswith(".before") for path in backups))

    def test_refine_twice_in_same_second_does_not_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = _workspace(tmp)
            record_gap(workspace, need="No local game design tool")
            proposed = propose_synthesis(
                workspace,
                task="Design a rule-driven text adventure",
                name="demo-game-designer",
            )
            with mock.patch("caprouter.synthesize._refine_stamp", return_value="samestamp"):
                first = refine_candidate(workspace, proposed["candidate_dir"], method="builtin")
                second = refine_candidate(workspace, proposed["candidate_dir"], method="builtin")
            dirs = {first["refinement_dir"], second["refinement_dir"]}
        self.assertEqual(len(dirs), 2)

    def test_adapter_refine_uses_json_and_preserves_proposed_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = _workspace(tmp)
            record_gap(workspace, need="No local game design tool")
            proposed = propose_synthesis(
                workspace,
                task="Design a rule-driven text adventure",
                name="demo-game-designer",
            )
            before_card = json.loads(
                Path(proposed["candidate_dir"]).joinpath("card_draft.json").read_text(encoding="utf-8")
            )
            payload = json.dumps(
                {
                    "description": "Design a rule-driven text adventure with a company loop and no cards.",
                    "when_to_use": "Use when a user wants an AI-run rulebook instead of a card game.",
                    "inputs": ["worldview"],
                    "workflow": ["Extract the loop", "Write the rules", "Run one example"],
                    "outputs": ["rulebook"],
                    "checks": ["no cards", "one example"],
                    "failure_handling": ["record gap and patch"],
                    "examples": ["Design a rule-driven game"],
                    "warnings": [],
                }
            )
            with mock.patch("caprouter.synthesize._available_refine_adapter", return_value="fake"), \
                    mock.patch(
                        "caprouter.synthesize.run_adapter",
                        return_value=AdapterResult(
                            adapter="fake",
                            command=[],
                            returncode=0,
                            stdout=payload,
                            stderr="",
                            duration_ms=10,
                            timed_out=False,
                            parsed={"text": payload},
                        ),
                    ):
                refined = refine_candidate(workspace, proposed["candidate_dir"], method="adapter", adapter="fake")
            text = Path(refined["skill_path"]).read_text(encoding="utf-8")
            after_card = json.loads(
                Path(proposed["candidate_dir"]).joinpath("card_draft.json").read_text(encoding="utf-8")
            )
            candidate = json.loads(
                Path(proposed["candidate_dir"]).joinpath("candidate.json").read_text(encoding="utf-8")
            )
        self.assertEqual(refined["status"], "refined")
        self.assertEqual(refined["method"], "adapter")
        self.assertIn("company loop and no cards", text)
        self.assertEqual(after_card["permissions"], before_card["permissions"])
        self.assertEqual(after_card["invoke"], before_card["invoke"])
        self.assertEqual(candidate["status"], "proposed")

    def test_auto_refine_uses_adapter_without_skill_creator(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = _workspace(tmp)
            record_gap(workspace, need="No local game design tool")
            proposed = propose_synthesis(
                workspace,
                task="Design a rule-driven text adventure",
                name="demo-game-designer",
            )
            payload = json.dumps(
                {
                    "description": "Design a rule-driven text adventure with a company loop and no cards.",
                    "workflow": ["Extract the loop", "Write the rules", "Run one example"],
                    "checks": ["no cards"],
                    "examples": ["Design a rule-driven game"],
                }
            )
            with mock.patch("caprouter.synthesize._skill_creator_guidance", return_value=("", "missing")), \
                    mock.patch("caprouter.synthesize._available_refine_adapter", return_value="fake"), \
                    mock.patch(
                        "caprouter.synthesize.run_adapter",
                        return_value=AdapterResult(
                            adapter="fake",
                            command=[],
                            returncode=0,
                            stdout=payload,
                            stderr="",
                            duration_ms=10,
                            timed_out=False,
                            parsed={"text": payload},
                        ),
                    ) as run:
                refined = refine_candidate(workspace, proposed["candidate_dir"], method="auto")
        self.assertEqual(refined["status"], "refined")
        self.assertEqual(refined["method"], "adapter")
        self.assertTrue(run.called)

    def test_invalid_adapter_output_falls_back_to_builtin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = _workspace(tmp)
            record_gap(workspace, need="No local game design tool")
            proposed = propose_synthesis(workspace, task="Design a rule-driven text adventure")
            with mock.patch("caprouter.synthesize._available_refine_adapter", return_value="fake"), \
                    mock.patch(
                        "caprouter.synthesize.run_adapter",
                        return_value=AdapterResult(
                            adapter="fake",
                            command=[],
                            returncode=0,
                            stdout="not json",
                            stderr="",
                            duration_ms=10,
                            timed_out=False,
                            parsed={"text": "not json"},
                        ),
                    ):
                refined = refine_candidate(workspace, proposed["candidate_dir"], method="adapter", adapter="fake")
        self.assertEqual(refined["status"], "refined_builtin")
        self.assertTrue(any("did not return JSON" in item for item in refined["warnings"]))

    def test_bundled_authoring_skill_is_valid(self) -> None:
        skill = Path(__file__).resolve().parent.parent / "skills" / "capability-author" / "SKILL.md"
        result = lint_skill(skill)
        self.assertTrue(result["ok"], result)

    def test_list_and_slug_are_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = _workspace(tmp)
            record_gap(workspace, need="No local tool")
            report = propose_synthesis(workspace, task="Task", name="../../evil")
            listing = list_syntheses(workspace)
            root = (workspace / ".caprouter" / "synthesized").resolve()
            candidate = Path(report["candidate_dir"]).resolve()
        self.assertEqual(report["status"], "proposed")
        self.assertEqual(listing["count"], 1)
        self.assertTrue(str(candidate).startswith(str(root)))


if __name__ == "__main__":
    unittest.main()
