from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from caprouter.capabilities import load_overlay
from caprouter.review import review_draft, review_drafts
from caprouter.util import write_json_atomic


def _workspace(tmp: str, review: dict | None = None) -> tuple[Path, Path, dict]:
    workspace = Path(tmp) / "ws"
    bus = workspace / ".caprouter"
    bus.mkdir(parents=True)
    project = Path(tmp) / "convert-doc"
    project.mkdir()
    entry = {
        "id": "project:convert-doc",
        "type": "project",
        "name": "convert-doc",
        "path": str(project),
        "description": "Convert PDF documents to Word and export tables",
        "domain": "office",
        "capabilities": ["transform", "format"],
        "keywords": ["pdf", "docx"],
        "status": "scanned",
    }
    config = {
        "schema": "caprouter.config.v1",
        "routing": {},
        "review": review or {},
    }
    write_json_atomic(bus / "config.json", config)
    write_json_atomic(bus / "registry.json", {"entries": [entry]})
    return workspace, project, entry


def _draft(entry: dict, **overrides) -> dict:
    draft = {
        "schema": "caprouter.capability-card-draft.v1",
        "id": entry["id"],
        "type": entry["type"],
        "name": entry["name"],
        "path": entry["path"],
        "description": entry["description"],
        "domain": "office",
        "domains": ["office"],
        "capabilities": ["transform", "format"],
        "keywords": ["pdf", "docx"],
        "examples": [
            "Convert a PDF file to Word",
            "Export the converted document",
            "Extract a table from the PDF",
        ],
        "permissions": {"fs": ["read"], "net": False, "shell": False},
        "invoke": {"mode": "none", "allow": False},
        "review": {"status": "draft", "notes": []},
    }
    draft.update(overrides)
    return draft


def _write_draft(workspace: Path, draft: dict) -> Path:
    target = workspace / ".caprouter" / "drafts" / "projects" / "draft.json"
    write_json_atomic(target, draft)
    return target


class ReviewTests(unittest.TestCase):
    def test_good_low_risk_draft_is_auto_approved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, _project, entry = _workspace(tmp)
            report = review_draft(workspace, _draft(entry))
        self.assertEqual(report["status"], "auto_approved")
        self.assertEqual(report["risk"], "low")
        self.assertGreaterEqual(report["score"], 0.8)
        self.assertTrue(report["retrieval"]["enabled"])
        self.assertGreaterEqual(report["retrieval"]["top1_rate"], 0.3)

    def test_missing_registry_id_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, _project, entry = _workspace(tmp)
            report = review_draft(workspace, _draft(entry, id="project:missing"))
        self.assertEqual(report["status"], "rejected")
        self.assertEqual(report["risk"], "high")
        self.assertTrue(any(item["id"] == "identity.id" for item in report["checks"]))

    def test_net_permission_is_medium_risk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, _project, entry = _workspace(tmp)
            draft = _draft(entry, permissions={"fs": ["read"], "net": True, "shell": False})
            report = review_draft(workspace, draft)
        self.assertEqual(report["status"], "needs_review")
        self.assertEqual(report["risk"], "medium")

    def test_semantic_pass_can_upgrade_medium_when_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, _project, entry = _workspace(tmp, review={"allow_medium": True})
            draft = _draft(entry, permissions={"fs": ["read"], "net": True, "shell": False})
            deterministic = review_draft(workspace, draft)
            report = review_draft(
                workspace,
                draft,
                config={
                    "min_examples": 3,
                    "min_capabilities": 1,
                    "allow_medium": True,
                    "retrieval": {"enabled": True, "top_k": 3, "min_top1_rate": 0.3, "min_top3_rate": 0.6},
                    "semantic": {"min_confidence": 0.75},
                },
                semantic={"verdict": "pass", "confidence": 0.9, "issues": []},
            )
        self.assertEqual(deterministic["risk"], "medium")
        self.assertEqual(report["status"], "auto_approved")

    def test_semantic_fail_blocks_auto_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, _project, entry = _workspace(tmp, review={"allow_medium": True})
            draft = _draft(entry)
            report = review_draft(
                workspace,
                draft,
                semantic={
                    "verdict": "fail",
                    "confidence": 0.9,
                    "issues": [{"field": "examples", "severity": "high", "message": "not concrete"}],
                },
            )
        self.assertEqual(report["status"], "needs_review")
        self.assertEqual(report["risk"], "high")

    def test_review_includes_drafts_at_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, _project, entry = _workspace(tmp)
            write_json_atomic(
                workspace / ".caprouter" / "drafts" / "installed.json",
                _draft(entry),
            )
            report = review_drafts(workspace, ids=[entry["id"]], dry_run=True)
        self.assertEqual(report["total"], 1)
        self.assertEqual(report["items"][0]["id"], entry["id"])

    def test_unreachable_apply_is_not_counted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, _project, entry = _workspace(tmp)
            _write_draft(workspace, _draft(entry))
            with patch(
                "caprouter.review.apply_draft",
                return_value={"status": "unreachable", "reason": "not in registry"},
            ):
                report = review_drafts(workspace, ids=[entry["id"]], auto_apply=True)
        self.assertEqual(report["applied"], 0)
        self.assertEqual(report["items"][0]["apply_error"], "not in registry")

    def test_batch_auto_apply_writes_overlay_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, _project, entry = _workspace(tmp, review={"auto_apply": True})
            _write_draft(workspace, _draft(entry))
            first = review_drafts(workspace)
            overlay = load_overlay(workspace)
            second = review_drafts(workspace)
        self.assertEqual(first["applied"], 1)
        self.assertIn(entry["id"], overlay["cards"])
        self.assertEqual(second["applied"], 0)
        self.assertEqual(second["skipped"], 1)

    def test_dry_run_does_not_apply_or_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, _project, entry = _workspace(tmp, review={"auto_apply": True})
            path = _write_draft(workspace, _draft(entry))
            before = path.read_text(encoding="utf-8")
            report = review_drafts(workspace, dry_run=True)
            after = path.read_text(encoding="utf-8")
            overlay = load_overlay(workspace)
        self.assertEqual(report["applied"], 0)
        self.assertEqual(before, after)
        self.assertNotIn(entry["id"], overlay["cards"])

    def test_unrelated_examples_fail_retrieval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, _project, entry = _workspace(tmp)
            draft = _draft(entry, examples=["zzzz unrelated alpha", "qqqq beta gamma", "wwww delta"])
            report = review_draft(workspace, draft)
        self.assertEqual(report["status"], "needs_review")
        self.assertEqual(report["risk"], "medium")
        self.assertTrue(any(item["id"] == "retrieval" for item in report["checks"]))

    def test_previous_semantic_fail_blocks_deterministic_auto_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, _project, entry = _workspace(tmp)
            draft = _draft(
                entry,
                review={
                    "status": "needs_review",
                    "semantic": {"verdict": "fail", "confidence": 0.9, "issues": []},
                },
            )
            report = review_draft(workspace, draft)
        self.assertEqual(report["status"], "needs_review")
        self.assertTrue(any(item["id"] == "semantic.previous" for item in report["checks"]))

    def test_feature_bullets_are_not_usable_examples(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, _project, entry = _workspace(tmp)
            draft = _draft(
                entry,
                examples=[
                    "🎯 **Intelligent Optimization**: One-click prompt optimization",
                    "📝 **Dual Mode Optimization**: system and user prompts",
                    "Support for mainstream AI models including OpenAI and Gemini",
                ],
            )
            report = review_draft(workspace, draft)
        self.assertEqual(report["status"], "needs_review")
        self.assertTrue(any(item["id"] == "examples" for item in report["checks"]))
        self.assertTrue(any("feature bullet" in item["message"] for item in report["checks"]))

    def test_evidenced_permissions_stay_low_risk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, project, entry = _workspace(tmp)
            (project / "package.json").write_text(
                json.dumps({"name": "convert-doc", "scripts": {"build": "node build.js"}}),
                encoding="utf-8",
            )
            (project / "README.md").write_text(
                "Fetch https://example.com/data and save the converted document.",
                encoding="utf-8",
            )
            draft = _draft(
                entry,
                permissions={"fs": ["read", "write"], "net": True, "shell": True},
            )
            report = review_draft(workspace, draft)
        self.assertEqual(report["risk"], "low")
        self.assertEqual(report["status"], "auto_approved")

    def test_medium_semantic_pass_is_applied_when_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, _project, entry = _workspace(
                tmp, review={"auto_apply": True, "allow_medium": True}
            )
            draft = _draft(entry, permissions={"fs": ["read"], "net": True, "shell": False})
            _write_draft(workspace, draft)
            report = review_drafts(
                workspace,
                semantic=True,
                semantic_runner=lambda *args, **kwargs: {
                    "verdict": "pass",
                    "confidence": 0.9,
                    "issues": [],
                },
            )
            overlay = load_overlay(workspace)
        self.assertEqual(report["applied"], 1)
        self.assertIn(entry["id"], overlay["cards"])

    def test_repair_rewrites_examples_and_then_applies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, _project, entry = _workspace(
                tmp, review={"auto_apply": True, "allow_medium": True}
            )
            draft = _draft(entry, examples=["Convert a PDF file to Word"])
            path = _write_draft(workspace, draft)
            calls: list[int] = []

            def runner(ws, current, evidence, deterministic, config):
                calls.append(len(current.get("examples") or []))
                if len(calls) == 1:
                    return {
                        "verdict": "pass",
                        "confidence": 0.9,
                        "issues": [],
                        "repaired": {
                            "description": "Convert PDF documents to Word and export tables",
                            "examples": [
                                "Convert a PDF file to Word",
                                "Export the converted document",
                                "Extract a table from the PDF",
                            ],
                            "capabilities": ["transform", "format"],
                            "keywords": ["pdf", "docx"],
                        },
                    }
                return {"verdict": "pass", "confidence": 0.9, "issues": []}

            report = review_drafts(
                workspace,
                semantic=True,
                repair=True,
                semantic_runner=runner,
            )
            saved = json.loads(path.read_text(encoding="utf-8"))
            overlay = load_overlay(workspace)
        self.assertEqual(report["repaired"], 1)
        self.assertEqual(report["applied"], 1)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(saved["examples"]), 3)
        self.assertEqual(len(overlay["cards"][entry["id"]]["examples"]), 3)

    def test_high_risk_skips_semantic_cost(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, _project, entry = _workspace(tmp)
            draft = _draft(entry, invoke={"mode": "command", "allow": True, "command": ["echo", "x"]})
            _write_draft(workspace, draft)
            calls: list[int] = []

            def runner(*args, **kwargs):
                calls.append(1)
                return {"verdict": "pass", "confidence": 1.0, "issues": []}

            report = review_drafts(workspace, semantic=True, semantic_runner=runner)
        self.assertEqual(calls, [])
        self.assertEqual(report["semantic_skipped_high"], 1)


if __name__ == "__main__":
    unittest.main()
