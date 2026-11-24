from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from caprouter.hostaware import (  # noqa: E402
    HOST_LABELS,
    annotate_card,
    annotate_search_result,
    classify_path,
    host_roots,
    link_managed_skill,
    normalize_host,
    prefer_host_loadable,
    unlink_managed_skill,
)


def make_skill(root: Path, name: str, description: str) -> Path:
    skill = root / name
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\nbody\n",
        encoding="utf-8",
    )
    return skill


class HostAwareTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ws = self.root / "workspace"
        self.ws.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_normalize_host_aliases(self) -> None:
        self.assertEqual(normalize_host("DSH"), "deepseek-harness")
        self.assertEqual(normalize_host("DeepSeek Harness"), "deepseek-harness")
        self.assertEqual(normalize_host("Cherry Studio"), "cherry")
        self.assertEqual(normalize_host(None), "unknown")
        self.assertEqual(normalize_host("nonexistent"), "unknown")

    def test_own_host_path_is_loadable(self) -> None:
        skill = make_skill(self.ws / ".dsh" / "skills", "local", "write reports")
        info = classify_path(skill, host="deepseek-harness", workspace=self.ws)
        self.assertTrue(info["loadable"])
        self.assertEqual(info["owner_host"], "deepseek-harness")
        self.assertEqual(info["reason"], "under_host_root")

    def test_other_host_path_is_not_loadable(self) -> None:
        foreign = self.root / "CherryStudio" / "Data" / "Skills"
        skill = make_skill(foreign, "foreign", "write reports")
        info = classify_path(skill, host="deepseek-harness", workspace=self.ws)
        self.assertFalse(info["loadable"])
        self.assertEqual(info["owner_host"], "cherry")
        self.assertEqual(info["reason"], "other_host")
        self.assertIn(HOST_LABELS["cherry"], info["hint"])
        self.assertIn("deepseek-harness", info["hint"])

    def test_managed_path_explains_linking(self) -> None:
        skill = make_skill(
            self.ws / ".caprouter" / "installed" / "skills", "managed", "write reports"
        )
        empty_root = self.root / "empty-host-skills"
        with patch.dict(os.environ, {"CAPROUTER_HOST_SKILLS": str(empty_root)}):
            info = classify_path(skill, host="deepseek-harness", workspace=self.ws)
        self.assertFalse(info["loadable"])
        self.assertEqual(info["reason"], "managed_not_linked")
        self.assertIn("--link-skills", info["hint"])

    def test_managed_path_linked_to_host_is_loadable(self) -> None:
        skill = make_skill(
            self.ws / ".caprouter" / "installed" / "skills", "managed", "write reports"
        )
        host_root = self.root / "dsh-skills"
        make_skill(host_root, "managed", "write reports")
        with patch.dict(os.environ, {"CAPROUTER_HOST_SKILLS": str(host_root)}):
            info = classify_path(skill, host="deepseek-harness", workspace=self.ws)
        self.assertTrue(info["loadable"])
        self.assertEqual(info["reason"], "managed_linked")
        self.assertEqual(info["linked_path"], str(host_root / "managed"))

    def test_unknown_host_and_missing_path(self) -> None:
        skill = make_skill(self.ws / ".dsh" / "skills", "local", "x")
        self.assertIsNone(classify_path(skill, host=None)["loadable"])
        missing = classify_path(self.root / "nope", host="deepseek-harness")
        self.assertFalse(missing["loadable"])
        self.assertEqual(missing["reason"], "path_missing")

    def test_annotate_search_result(self) -> None:
        local = make_skill(self.ws / ".dsh" / "skills", "local", "写报告")
        foreign = make_skill(self.root / "CherryStudio" / "Data" / "Skills", "foreign", "写报告")
        result = {
            "selected": {"id": "skill:local", "path": str(local), "health": {"available": True}},
            "candidates": [
                {"id": "skill:local", "path": str(local), "health": {"available": True}},
                {"id": "skill:foreign", "path": str(foreign), "health": {"available": True}},
            ],
        }
        annotated = annotate_search_result(result, host="deepseek-harness", workspace=self.ws)
        self.assertEqual(annotated["current_host"], "deepseek-harness")
        self.assertTrue(annotated["selected"]["health"]["loadable_by_host"])
        self.assertNotIn("host_warning", annotated)
        rows = {row["id"]: row for row in annotated["candidates"]}
        self.assertFalse(rows["skill:foreign"]["host"]["loadable"])
        self.assertIn("host_hint", rows["skill:foreign"]["health"])
        self.assertNotIn("host_hint", rows["skill:local"]["health"])

    def test_search_warns_when_selection_is_other_host(self) -> None:
        foreign = make_skill(self.root / "CherryStudio" / "Data" / "Skills", "foreign", "写报告")
        result = annotate_search_result(
            {"selected": {"id": "skill:foreign", "path": str(foreign)}, "candidates": []},
            host="deepseek-harness",
            workspace=self.ws,
        )
        self.assertIn("host_warning", result)
        self.assertIn("Cherry Studio", result["host_warning"])

    def test_annotate_card(self) -> None:
        card = {"id": "skill:x", "path": str(self.root / "nope")}
        annotate_card(card, host="deepseek-harness")
        self.assertEqual(card["host"]["current_host"], "deepseek-harness")
        self.assertFalse(card["health"]["loadable_by_host"])

    def test_link_managed_skill_copies_to_host_root(self) -> None:
        installed = make_skill(
            self.ws / ".caprouter" / "installed" / "skills", "demo", "write reports"
        )
        self.assertTrue(installed.is_dir())
        target_root = self.root / "dsh-skills"
        with patch.dict(os.environ, {"CAPROUTER_HOST_SKILLS": str(target_root)}):
            first = link_managed_skill(self.ws, "skill:demo", host="deepseek-harness")
            self.assertEqual(first["linked"], str(target_root / "demo"))
            self.assertTrue((target_root / "demo" / "SKILL.md").is_file())
            second = link_managed_skill(self.ws, "skill:demo", host="deepseek-harness")
            self.assertIsNone(second["linked"])
            self.assertIn("Already exists", second["skipped"])
            forced = link_managed_skill(self.ws, "skill:demo", host="deepseek-harness", force=True)
            self.assertEqual(forced["linked"], str(target_root / "demo"))

    def test_link_managed_skill_rejects_non_skills(self) -> None:
        (self.ws / ".caprouter" / "installed" / "plugins" / "p").mkdir(parents=True)
        with patch.dict(os.environ, {"CAPROUTER_HOST_SKILLS": str(self.root / "roots")}):
            result = link_managed_skill(self.ws, "plugin:p", host="deepseek-harness")
            self.assertIsNone(result["linked"])
            self.assertIn("skill", result["skipped"])

    def test_host_roots_override(self) -> None:
        with patch.dict(os.environ, {"CAPROUTER_HOST_SKILLS": str(self.root / "override")}):
            self.assertEqual(host_roots("deepseek-harness"), [self.root / "override"])

    def test_prefer_host_loadable_switches_selection(self) -> None:
        local = make_skill(self.ws / ".dsh" / "skills", "local", "写报告")
        foreign = make_skill(self.root / "CherryStudio" / "Data" / "Skills", "foreign", "写报告")
        result = {
            "decision": "select",
            "confidence": 0.9,
            "selected": {"id": "skill:foreign", "path": str(foreign), "score": 0.9},
            "candidates": [
                {"id": "skill:foreign", "path": str(foreign), "score": 0.9},
                {"id": "skill:local", "path": str(local), "score": 0.5},
            ],
        }
        preferred = prefer_host_loadable(result, host="deepseek-harness", workspace=self.ws)
        self.assertEqual(preferred["selected"]["id"], "skill:local")
        self.assertEqual(preferred["selected_original"]["id"], "skill:foreign")
        self.assertIn("selection_adjusted", preferred)
        self.assertEqual(preferred["confidence"], 0.5)
        self.assertNotIn("host_warning", preferred)
        self.assertEqual([row["id"] for row in preferred["candidates"]][0], "skill:local")

    def test_prefer_host_loadable_keeps_selection_when_nothing_loadable(self) -> None:
        foreign = make_skill(self.root / "CherryStudio" / "Data" / "Skills", "foreign", "写报告")
        result = {
            "decision": "select",
            "selected": {"id": "skill:foreign", "path": str(foreign)},
            "candidates": [{"id": "skill:foreign", "path": str(foreign)}],
        }
        preferred = prefer_host_loadable(result, host="deepseek-harness", workspace=self.ws)
        self.assertEqual(preferred["selected"]["id"], "skill:foreign")
        self.assertNotIn("selection_adjusted", preferred)
        self.assertIn("host_warning", preferred)

    def test_unlink_managed_skill_removes_matching_copy(self) -> None:
        source = make_skill(
            self.ws / ".caprouter" / "installed" / "skills", "demo", "write reports"
        )
        target_root = self.root / "dsh-skills"
        with patch.dict(os.environ, {"CAPROUTER_HOST_SKILLS": str(target_root)}):
            link_managed_skill(self.ws, "skill:demo", host="deepseek-harness")
            self.assertTrue((target_root / "demo").is_dir())
            result = unlink_managed_skill(
                self.ws, "skill:demo", host="deepseek-harness", removed_path=source
            )
            self.assertTrue(result["removed"])
            self.assertFalse((target_root / "demo").exists())

    def test_unlink_keeps_locally_modified_copy(self) -> None:
        source = make_skill(
            self.ws / ".caprouter" / "installed" / "skills", "demo", "write reports"
        )
        target_root = self.root / "dsh-skills"
        with patch.dict(os.environ, {"CAPROUTER_HOST_SKILLS": str(target_root)}):
            link_managed_skill(self.ws, "skill:demo", host="deepseek-harness")
            (target_root / "demo" / "SKILL.md").write_text("locally edited", encoding="utf-8")
            blocked = unlink_managed_skill(
                self.ws, "skill:demo", host="deepseek-harness", removed_path=source
            )
            self.assertFalse(blocked["removed"])
            self.assertIn("differs", blocked["skipped"])
            self.assertTrue((target_root / "demo" / "SKILL.md").is_file())
            forced = unlink_managed_skill(
                self.ws, "skill:demo", host="deepseek-harness", removed_path=source, force=True
            )
            self.assertTrue(forced["removed"])

    def test_unlink_uses_prerecorded_hash(self) -> None:
        source = make_skill(
            self.ws / ".caprouter" / "installed" / "skills", "demo", "write reports"
        )
        from caprouter.util import hash_tree

        expected = hash_tree(source)
        target_root = self.root / "dsh-skills"
        with patch.dict(os.environ, {"CAPROUTER_HOST_SKILLS": str(target_root)}):
            link_managed_skill(self.ws, "skill:demo", host="deepseek-harness")
            # A modified host copy must not be deleted on the strength of a
            # recorded hash.
            (target_root / "demo" / "SKILL.md").write_text("edited", encoding="utf-8")
            blocked = unlink_managed_skill(
                self.ws, "skill:demo", host="deepseek-harness", removed_hash=expected
            )
            self.assertFalse(blocked["removed"])
            self.assertIn("differs", blocked["skipped"])
            forced = unlink_managed_skill(
                self.ws,
                "skill:demo",
                host="deepseek-harness",
                removed_hash=expected,
                force=True,
            )
            self.assertTrue(forced["removed"])

    def test_unlink_reports_missing_copy(self) -> None:
        target_root = self.root / "empty-skills"
        with patch.dict(os.environ, {"CAPROUTER_HOST_SKILLS": str(target_root)}):
            result = unlink_managed_skill(self.ws, "skill:absent", host="deepseek-harness")
            self.assertFalse(result["removed"])
            self.assertIn("No copy", result["skipped"])


class McpHostProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ws = self.root / "workspace"
        bus = self.ws / ".caprouter"
        bus.mkdir(parents=True)
        self.local = make_skill(self.ws / ".dsh" / "skills", "local", "写报告与生成文案")
        self.foreign = make_skill(
            self.root / "CherryStudio" / "Data" / "Skills", "foreign", "写报告与生成文案"
        )
        entries = []
        for skill, name in ((self.local, "local"), (self.foreign, "foreign")):
            entries.append({
                "id": f"skill:{name}",
                "type": "skill",
                "name": name,
                "path": str(skill),
                "description": "写报告与生成文案",
                "keywords": ["写", "报告", "文案"],
                "capabilities": ["generate"],
                "domain": "writing",
                "status": "verified",
            })
        (bus / "registry.json").write_text(
            json.dumps({"entries": entries}, ensure_ascii=False), encoding="utf-8"
        )
        (bus / "config.json").write_text(
            json.dumps({"schema": "caprouter.config.v1", "routing": {}}, ensure_ascii=False),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run(self, extra_args: list[str]) -> list[dict]:
        proc = subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "bin" / "caprouter.py"),
                "mcp",
                "--workspace",
                str(self.ws),
                "--host",
                "deepseek-harness",
                *extra_args,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "test", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "capability_search", "arguments": {"task": "写报告", "top": 5}}},
        ]
        payload = "\n".join(json.dumps(item, ensure_ascii=False) for item in requests) + "\n"
        out, _err = proc.communicate(payload, timeout=60)
        return [json.loads(line) for line in out.splitlines() if line.strip()]

    def test_core_profile_exposes_eight_tools(self) -> None:
        responses = self._run(["--tools", "core"])
        listing = next(item for item in responses if item.get("id") == 2)
        names = sorted(tool["name"] for tool in listing["result"]["tools"])
        self.assertEqual(len(names), 8)
        self.assertIn("capability_search", names)
        self.assertNotIn("capability_probe", names)

    def test_full_profile_exposes_twenty_tools(self) -> None:
        responses = self._run(["--tools", "full"])
        listing = next(item for item in responses if item.get("id") == 2)
        self.assertEqual(len(listing["result"]["tools"]), 20)

    def test_search_marks_cross_host_skill_unloadable(self) -> None:
        responses = self._run(["--tools", "core"])
        call = next(item for item in responses if item.get("id") == 3)
        payload = json.loads(call["result"]["content"][0]["text"])
        self.assertEqual(payload["current_host"], "deepseek-harness")
        rows = payload["candidates"]
        by_id = {row["id"]: row for row in rows}
        self.assertIn("skill:local", by_id)
        self.assertIn("skill:foreign", by_id)
        self.assertTrue(by_id["skill:local"]["health"]["loadable_by_host"])
        self.assertFalse(by_id["skill:foreign"]["health"]["loadable_by_host"])
        self.assertIn("host_hint", by_id["skill:foreign"]["health"])


if __name__ == "__main__":
    unittest.main()
