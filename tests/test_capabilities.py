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

from caprouter.capabilities import (
    get_capability,
    invoke_capability,
    list_gaps,
    record_gap,
    record_outcome,
    search_capabilities,
    verify_artifact,
)
from caprouter.claims import sha256_file


class CapabilityBusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)
        bus = self.ws / ".caprouter"
        bus.mkdir(parents=True)

        self.state_file = self.ws / "state.json"
        self.state_file.write_text(
            json.dumps({"schema": "caprouter.state.v1", "enabled": True}), encoding="utf-8"
        )
        self.audit_file = self.ws / "audit.jsonl"
        self.env = patch.dict(
            os.environ,
            {
                "CAPROUTER_STATE": str(self.state_file),
                "CAPROUTER_AUDIT": str(self.audit_file),
            },
        )
        self.env.start()

        self.research = self.ws / "skills" / "research-writer"
        self.research.mkdir(parents=True)
        (self.research / "SKILL.md").write_text(
            "---\nname: research-writer\ndescription: 调研检索并写报告\n---\nbody\n",
            encoding="utf-8",
        )
        self.verifier = self.ws / "skills" / "report-verifier"
        self.verifier.mkdir(parents=True)
        (self.verifier / "SKILL.md").write_text(
            "---\nname: report-verifier\ndescription: 核查报告与来源\n---\nbody\n",
            encoding="utf-8",
        )
        self.office = self.ws / "skills" / "demo-office-doc"
        self.office.mkdir(parents=True)
        (self.office / "SKILL.md").write_text(
            "---\nname: demo-office-doc\ndescription: Convert PDF and Office files into new formats\n---\nbody\n",
            encoding="utf-8",
        )
        self.pdf_reader = self.ws / "skills" / "demo-pdf-reader"
        self.pdf_reader.mkdir(parents=True)
        (self.pdf_reader / "SKILL.md").write_text(
            "---\nname: demo-pdf-reader\ndescription: Read PDFs. Reading only — creating or editing PDF files is not supported.\n---\nbody\n",
            encoding="utf-8",
        )

        entries = [
            {
                "id": "skill:research-writer", "type": "skill", "name": "research-writer",
                "path": str(self.research), "description": "调研检索并写报告",
                "keywords": ["调研", "报告"], "capabilities": ["retrieve", "generate"],
                "domain": "research", "status": "verified",
            },
            {
                "id": "skill:report-verifier", "type": "skill", "name": "report-verifier",
                "path": str(self.verifier), "description": "核查报告与来源",
                "keywords": ["核查"], "capabilities": ["verify"],
                "domain": "research", "status": "verified",
            },
            {
                "id": "skill:demo-office-doc", "type": "skill", "name": "demo-office-doc",
                "path": str(self.office), "description": "Convert PDF and Office files into new formats",
                "keywords": ["convert", "pdf", "docx"], "capabilities": ["transform", "format"],
                "domain": "office", "status": "verified",
            },
            {
                "id": "skill:demo-pdf-reader", "type": "skill", "name": "demo-pdf-reader",
                "path": str(self.pdf_reader),
                "description": "Read PDFs. Reading only — creating or editing PDF files is not supported.",
                "keywords": ["pdf"], "capabilities": ["retrieve", "transform", "format"],
                "domain": "office", "status": "verified",
            },
            {
                "id": "script:counter", "type": "script", "name": "counter",
                "path": str(self.ws / "counter.py"), "description": "Count words in text",
                "keywords": ["count"], "capabilities": ["analyze"],
                "domain": "general", "status": "verified",
            },
            {
                "id": "skill:missing", "type": "skill", "name": "missing",
                "path": str(self.ws / "does-not-exist"), "description": "Missing skill",
                "keywords": ["missing"], "capabilities": ["generate"],
                "domain": "general", "status": "verified",
            },
        ]
        (bus / "registry.json").write_text(json.dumps({"entries": entries}, ensure_ascii=False), encoding="utf-8")
        (bus / "config.json").write_text(
            json.dumps({"schema": "caprouter.config.v1", "routing": {}, "bus": {"allow_execute": True}}, ensure_ascii=False),
            encoding="utf-8",
        )
        overlay = {
            "schema": "caprouter.capability-cards.v1",
            "cards": {
                "skill:research-writer": {
                    "examples": ["调研市场并写报告"],
                    "probe": {"status": "pass", "checks": []},
                },
                "skill:report-verifier": {
                    "examples": ["核查报告来源"],
                    "probe": {"status": "pass", "checks": []},
                },
                "script:counter": {
                    "permissions": {"fs": ["read"], "net": False, "shell": True},
                    "invoke": {
                        "mode": "command",
                        "command": [sys.executable, "-c", "print('ok')"],
                        "allow": True,
                    },
                },
            },
        }
        (bus / "capability_cards.json").write_text(json.dumps(overlay, ensure_ascii=False), encoding="utf-8")

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def test_search_selects_research_writer(self) -> None:
        result = search_capabilities(self.ws, "调研市场并写报告", top=3)
        self.assertEqual(result["decision"], "select")
        self.assertEqual(result["selected"]["id"], "skill:research-writer")
        self.assertGreaterEqual(result["confidence"], result["thresholds"]["min_confidence"])

    def test_search_abstains_on_unrelated_task(self) -> None:
        result = search_capabilities(self.ws, "证明黎曼猜想的严格数学推导", top=3)
        self.assertEqual(result["decision"], "abstain")
        self.assertTrue(result["abstain_reason"])

    def test_negative_description_penalizes_reader_for_conversion(self) -> None:
        result = search_capabilities(self.ws, "把 PDF 转成 Word", top=3)
        self.assertEqual(result["decision"], "select")
        self.assertEqual(result["selected"]["id"], "skill:demo-office-doc")

    def test_missing_path_is_filtered_unless_requested(self) -> None:
        default = search_capabilities(self.ws, "missing skill", top=10)
        self.assertNotIn("skill:missing", [item["id"] for item in default["candidates"]])
        included = search_capabilities(self.ws, "missing skill", top=10, include_unavailable=True)
        self.assertIn("skill:missing", [item["id"] for item in included["candidates"]])

    def test_get_card(self) -> None:
        card = get_capability(self.ws, "skill:research-writer")
        self.assertIsNotNone(card)
        assert card is not None
        self.assertEqual(card["schema"], "caprouter.capability-card.v1")
        self.assertEqual(card["invoke"]["mode"], "prompt")
        self.assertEqual(card["examples"], ["调研市场并写报告"])
        self.assertEqual(card["probe"]["status"], "pass")

    def test_invoke_prompt_mode(self) -> None:
        result = invoke_capability(self.ws, "skill:research-writer")
        self.assertEqual(result["status"], "prompt")
        self.assertIn("research-writer", result["content"])

    def test_invoke_command_blocked_without_permission(self) -> None:
        result = invoke_capability(self.ws, "script:counter")
        self.assertEqual(result["status"], "blocked")

    def test_invoke_command_with_permission(self) -> None:
        result = invoke_capability(self.ws, "script:counter", allow_execute=True)
        self.assertEqual(result["status"], "ok")
        self.assertIn("ok", result["stdout"])

    def test_invoke_blocked_when_state_disabled(self) -> None:
        self.state_file.write_text(
            json.dumps({"schema": "caprouter.state.v1", "enabled": False}), encoding="utf-8"
        )
        prompt_result = invoke_capability(self.ws, "skill:research-writer")
        command_result = invoke_capability(self.ws, "script:counter", allow_execute=True)
        self.assertEqual(prompt_result["status"], "blocked")
        self.assertEqual(command_result["status"], "blocked")
        self.assertIn("disabled", command_result["reason"])
        audit_rows = self.audit_file.read_text(encoding="utf-8").splitlines()
        self.assertTrue(any('"decision": "blocked"' in row for row in audit_rows))

    def test_invoke_command_requires_card_shell_permission(self) -> None:
        overlay_path = self.ws / ".caprouter" / "capability_cards.json"
        overlay = json.loads(overlay_path.read_text(encoding="utf-8"))
        overlay["cards"]["script:counter"]["permissions"] = {
            "fs": ["read"], "net": False, "shell": False,
        }
        overlay_path.write_text(json.dumps(overlay), encoding="utf-8")
        result = invoke_capability(self.ws, "script:counter", allow_execute=True)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("shell=true", result["reason"])

    def test_invoke_command_requires_permissions_block(self) -> None:
        overlay_path = self.ws / ".caprouter" / "capability_cards.json"
        overlay = json.loads(overlay_path.read_text(encoding="utf-8"))
        del overlay["cards"]["script:counter"]["permissions"]
        overlay_path.write_text(json.dumps(overlay), encoding="utf-8")
        result = invoke_capability(self.ws, "script:counter", allow_execute=True)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("permissions", result["reason"])

    def test_invoke_cwd_outside_workspace_blocked(self) -> None:
        outside = Path(self.tmp.name).parent
        result = invoke_capability(self.ws, "script:counter", allow_execute=True, cwd=str(outside))
        self.assertEqual(result["status"], "blocked")
        self.assertIn("outside", result["reason"])

    def test_invoke_cwd_missing_is_not_created(self) -> None:
        missing = self.ws / "new-cwd"
        result = invoke_capability(self.ws, "script:counter", allow_execute=True, cwd=str(missing))
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(missing.exists())

    def test_invoke_cwd_inside_workspace_allowed(self) -> None:
        work = self.ws / "work"
        work.mkdir()
        result = invoke_capability(self.ws, "script:counter", allow_execute=True, cwd=str(work))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(Path(result["cwd"]).resolve(), work.resolve())

    def test_invoke_default_cwd_is_workspace_scratch(self) -> None:
        result = invoke_capability(self.ws, "script:counter", allow_execute=True)
        self.assertEqual(result["status"], "ok")
        scratch = (self.ws / ".caprouter" / "scratch").resolve()
        self.assertTrue(Path(result["cwd"]).resolve().is_relative_to(scratch))

    def test_verify_char_range(self) -> None:
        text = "汉" * 160
        result = verify_artifact(self.ws, text=text, acceptance=[{"type": "char_range", "min": 150, "max": 200, "unit": "han"}])
        self.assertEqual(result["verdict"], "pass")

    def test_verify_exact_items(self) -> None:
        text = "1. 甲\n2. 乙\n3. 丙\n"
        result = verify_artifact(self.ws, text=text, acceptance=[{"type": "exact_items", "value": 3, "pattern": "list"}])
        self.assertEqual(result["verdict"], "pass")

    def test_verify_json_required(self) -> None:
        result = verify_artifact(self.ws, text='{"a": 1}', acceptance=[{"type": "json_required", "value": ["a", "b"]}])
        self.assertEqual(result["verdict"], "fail")
        self.assertEqual(result["checks"][0]["actual"], {"missing": ["b"]})

    def test_verify_claim_ledger(self) -> None:
        snap = self.ws / "snapshot.md"
        snap.write_text("hello world", encoding="utf-8")
        ledger = self.ws / "ledger.json"
        ledger.write_text(
            json.dumps({"claims": [{
                "claim_id": "c1", "quote": "hello",
                "snapshot_path": "snapshot.md", "snapshot_hash": sha256_file(snap),
            }]}),
            encoding="utf-8",
        )
        result = verify_artifact(self.ws, text="report", acceptance=[{"type": "claim_ledger", "path": str(ledger)}])
        self.assertEqual(result["verdict"], "pass")

    def test_free_text_criteria(self) -> None:
        text = "1. 甲\n2. 乙\n3. 丙\n" + "汉" * 160 + "\nhttps://a.example https://b.example"
        result = verify_artifact(
            self.ws, text=text,
            acceptance=["150–200 个汉字", "正好 3 条", "至少 2 个来源"],
        )
        self.assertEqual(result["verdict"], "pass")

    def test_outcome_and_gap_logs(self) -> None:
        record_outcome(self.ws, "skill:research-writer", task="t", success=True, duration_ms=10, human_edit_minutes=2)
        card = get_capability(self.ws, "skill:research-writer")
        assert card is not None
        self.assertEqual(card["stats"]["calls"], 1)
        self.assertEqual(card["stats"]["success_rate"], 1.0)
        record_gap(self.ws, need="翻译", reason="no skill")
        result = list_gaps(self.ws)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["gaps"][0]["need"], "翻译")


if __name__ == "__main__":
    unittest.main()
