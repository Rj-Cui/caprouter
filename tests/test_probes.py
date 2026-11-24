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

from caprouter.capabilities import get_capability, search_capabilities
from caprouter.probes import (
    load_probe_rows,
    probe_report,
    record_probe,
    run_all_probes,
    run_probes,
    summarize_probe,
)


class ProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)
        bus = self.ws / ".caprouter"
        bus.mkdir(parents=True)
        self.state_file = self.ws / "state.json"
        self.state_file.write_text(
            json.dumps({"schema": "caprouter.state.v1", "enabled": True}), encoding="utf-8"
        )
        self.env = patch.dict(os.environ, {"CAPROUTER_STATE": str(self.state_file)})
        self.env.start()

        self.skill = self.ws / "skills" / "probe-skill"
        self.skill.mkdir(parents=True)
        (self.skill / "SKILL.md").write_text(
            "---\nname: probe-skill\ndescription: Convert PDF pages into images\n---\nbody\n",
            encoding="utf-8",
        )
        self.broken = self.ws / "skills" / "broken-skill"
        self.broken.mkdir(parents=True)
        (self.broken / "SKILL.md").write_text("no frontmatter\n", encoding="utf-8")
        self.plain = self.ws / "skills" / "plain-skill"
        self.plain.mkdir(parents=True)
        (self.plain / "SKILL.md").write_text(
            "---\nname: plain-skill\ndescription: A plain valid skill\n---\nbody\n",
            encoding="utf-8",
        )
        self.missing = self.ws / "skills" / "missing-skill"
        self.script = self.ws / "counter.py"
        self.script.write_text("print('counter')\n", encoding="utf-8")

        entries = [
            {
                "id": "skill:probe-skill", "type": "skill", "name": "probe-skill",
                "path": str(self.skill), "description": "Convert PDF pages into images",
                "keywords": ["pdf"], "capabilities": ["transform"], "domain": "office",
                "status": "verified",
            },
            {
                "id": "skill:broken-skill", "type": "skill", "name": "broken-skill",
                "path": str(self.broken), "description": "Broken skill",
                "keywords": ["broken"], "capabilities": ["generate"], "domain": "general",
                "status": "verified",
            },
            {
                "id": "skill:plain-skill", "type": "skill", "name": "plain-skill",
                "path": str(self.plain), "description": "A plain valid skill",
                "keywords": ["plain"], "capabilities": ["generate"], "domain": "general",
                "status": "verified",
            },
            {
                "id": "skill:missing-skill", "type": "skill", "name": "missing-skill",
                "path": str(self.missing), "description": "Missing skill",
                "keywords": ["missing"], "capabilities": ["generate"], "domain": "general",
                "status": "verified",
            },
            {
                "id": "script:counter", "type": "script", "name": "counter",
                "path": str(self.script), "description": "Count words",
                "keywords": ["count"], "capabilities": ["analyze"], "domain": "general",
                "status": "verified",
            },
        ]
        (bus / "registry.json").write_text(json.dumps({"entries": entries}, ensure_ascii=False), encoding="utf-8")
        (bus / "config.json").write_text(
            json.dumps({"schema": "caprouter.config.v1", "routing": {}, "bus": {"allow_execute": True}}),
            encoding="utf-8",
        )
        overlay = {
            "schema": "caprouter.capability-cards.v1",
            "cards": {
                "skill:probe-skill": {
                    "probe": {
                        "status": "unknown",
                        "checks": [
                            {"id": "host-1", "type": "host", "task": "PDF page -> PNG"},
                        ],
                    },
                },
                "script:counter": {
                    "permissions": {"fs": ["read"], "net": False, "shell": True},
                    "probe": {
                        "status": "unknown",
                        "checks": [
                            {
                                "id": "cmd-ok", "type": "command",
                                "command": [sys.executable, "-c", "print('ok')"],
                                "allow": True,
                                "acceptance": [{"type": "contains", "value": "ok"}],
                            },
                            {
                                "id": "cmd-bad", "type": "command",
                                "command": [sys.executable, "-c", "print('bad')"],
                                "allow": True,
                                "acceptance": [{"type": "contains", "value": "ok"}],
                            },
                        ],
                    },
                },
            },
        }
        (bus / "capability_cards.json").write_text(json.dumps(overlay, ensure_ascii=False), encoding="utf-8")

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def test_broken_skill_lint_fails(self) -> None:
        result = run_probes(self.ws, "skill:broken-skill")
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["evidence"], "static")
        self.assertTrue(any(check["type"] == "skill_lint" and check["status"] == "fail" for check in result["checks"]))

    def test_missing_path_fails(self) -> None:
        result = run_probes(self.ws, "skill:missing-skill")
        self.assertEqual(result["status"], "fail")
        path_check = next(check for check in result["checks"] if check["id"] == "static:path")
        self.assertEqual(path_check["status"], "fail")

    def test_host_task_makes_probe_pending(self) -> None:
        result = run_probes(self.ws, "skill:probe-skill")
        self.assertEqual(result["status"], "pending")
        self.assertGreaterEqual(result["pending"], 1)
        self.assertEqual(result["evidence"], "mixed")
        pending = [check for check in result["checks"] if check["status"] == "pending"]
        self.assertEqual(pending[0]["task"], "PDF page -> PNG")

    def test_host_definitions_are_not_evidence(self) -> None:
        card = get_capability(self.ws, "skill:probe-skill")
        assert card is not None
        # The overlay has a host probe *definition*, but nobody ran it yet.
        self.assertEqual(card["probe"]["status"], "unknown")
        self.assertEqual(card["probe"]["evidence"], "unknown")

    def test_host_definitions_do_not_award_behavioral_bonus(self) -> None:
        result = search_capabilities(self.ws, "PDF page to PNG", top=5)
        candidate = next(item for item in result["candidates"] if item["id"] == "skill:probe-skill")
        self.assertNotIn("probe:pass:behavioral", candidate["reasons"])
        self.assertNotIn("probe:pass:static", candidate["reasons"])

    def test_record_probe_turns_summary_behavioral(self) -> None:
        record_probe(self.ws, "skill:probe-skill", task="PDF page -> PNG", status="pass", duration_ms=1200)
        card = get_capability(self.ws, "skill:probe-skill")
        assert card is not None
        self.assertEqual(card["probe"]["status"], "pass")
        self.assertEqual(card["probe"]["evidence"], "behavioral")
        self.assertEqual(card["probe"]["latency_ms"], 1200)
        self.assertEqual(card["probe"]["runs"], 1)

    def test_command_probe_blocked_without_caller_permission(self) -> None:
        result = run_probes(self.ws, "script:counter", allow_execute=False)
        skipped = [check for check in result["checks"] if check["status"] == "skipped"]
        self.assertTrue(skipped)
        self.assertEqual(result["status"], "unknown")

    def test_command_probe_with_permission(self) -> None:
        result = run_probes(self.ws, "script:counter", allow_execute=True)
        # cmd-bad fails, so the overall result must fail even though cmd-ok passes.
        self.assertEqual(result["status"], "fail")
        statuses = {check["id"]: check["status"] for check in result["checks"]}
        self.assertEqual(statuses["cmd-ok"], "pass")
        self.assertEqual(statuses["cmd-bad"], "fail")

    def test_command_probe_acceptance_failure(self) -> None:
        result = run_probes(self.ws, "script:counter", allow_execute=True)
        bad = next(check for check in result["checks"] if check["id"] == "cmd-bad")
        self.assertEqual(bad["status"], "fail")
        self.assertIn("bad", bad["stdout_tail"])

    def test_command_probe_skipped_when_state_disabled(self) -> None:
        self.state_file.write_text(
            json.dumps({"schema": "caprouter.state.v1", "enabled": False}), encoding="utf-8"
        )
        result = run_probes(self.ws, "script:counter", allow_execute=True)
        command_checks = [check for check in result["checks"] if check["type"] == "command"]
        self.assertTrue(command_checks)
        self.assertTrue(all(check["status"] == "skipped" for check in command_checks))
        self.assertEqual(result["status"], "unknown")

    def test_command_probe_requires_card_shell_permission(self) -> None:
        overlay_path = self.ws / ".caprouter" / "capability_cards.json"
        overlay = json.loads(overlay_path.read_text(encoding="utf-8"))
        overlay["cards"]["script:counter"]["permissions"] = {
            "fs": ["read"], "net": False, "shell": False,
        }
        overlay_path.write_text(json.dumps(overlay), encoding="utf-8")
        result = run_probes(self.ws, "script:counter", allow_execute=True)
        command_checks = [check for check in result["checks"] if check["type"] == "command"]
        self.assertTrue(command_checks)
        self.assertTrue(all(check["status"] == "skipped" for check in command_checks))
        self.assertEqual(result["status"], "unknown")

    def test_probe_fail_penalizes_search(self) -> None:
        run_probes(self.ws, "skill:broken-skill")
        result = search_capabilities(self.ws, "broken skill", top=5)
        candidate = next(item for item in result["candidates"] if item["id"] == "skill:broken-skill")
        self.assertIn("probe:fail", candidate["reasons"])
        self.assertEqual(candidate["probe_status"], "fail")

    def test_static_history_does_not_erase_behavioral_seed(self) -> None:
        overlay_path = self.ws / ".caprouter" / "capability_cards.json"
        overlay = json.loads(overlay_path.read_text(encoding="utf-8"))
        overlay["cards"]["skill:plain-skill"] = {
            "probe": {
                "status": "pass",
                "evidence": "behavioral",
                "checks": [{"task": "manual PDF task", "status": "pass"}],
            },
        }
        overlay_path.write_text(json.dumps(overlay, ensure_ascii=False), encoding="utf-8")
        run_probes(self.ws, "skill:plain-skill")
        card = get_capability(self.ws, "skill:plain-skill")
        assert card is not None
        self.assertEqual(card["probe"]["status"], "pass")
        self.assertEqual(card["probe"]["evidence"], "mixed")

    def test_static_failure_overrides_behavioral_seed(self) -> None:
        overlay_path = self.ws / ".caprouter" / "capability_cards.json"
        overlay = json.loads(overlay_path.read_text(encoding="utf-8"))
        overlay["cards"]["skill:broken-skill"] = {
            "probe": {"status": "pass", "evidence": "behavioral", "checks": []},
        }
        overlay_path.write_text(json.dumps(overlay, ensure_ascii=False), encoding="utf-8")
        run_probes(self.ws, "skill:broken-skill")
        card = get_capability(self.ws, "skill:broken-skill")
        assert card is not None
        self.assertEqual(card["probe"]["status"], "fail")

    def test_static_pass_does_not_wash_out_behavioral_failure(self) -> None:
        record_probe(self.ws, "skill:plain-skill", task="bad run", status="fail")
        run_probes(self.ws, "skill:plain-skill")
        card = get_capability(self.ws, "skill:plain-skill")
        assert card is not None
        self.assertEqual(card["probe"]["status"], "fail")

    def test_probe_history_and_report(self) -> None:
        run_probes(self.ws, "skill:probe-skill")
        record_probe(self.ws, "skill:probe-skill", task="PDF page -> PNG", status="pass")
        self.assertGreaterEqual(len(load_probe_rows(self.ws)), 2)
        report = probe_report(self.ws, capability_id="skill:probe-skill")
        self.assertEqual(report["runs"], 2)
        self.assertEqual(report["summary"]["status"], "pass")
        summary = summarize_probe("skill:probe-skill", load_probe_rows(self.ws))
        self.assertEqual(summary["evidence"], "behavioral")

    def test_run_all_probes_counts(self) -> None:
        result = run_all_probes(self.ws)
        self.assertEqual(result["total"], 5)
        self.assertEqual(result["counts"].get("fail"), 2)


if __name__ == "__main__":
    unittest.main()
