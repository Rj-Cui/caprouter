from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from caprouter.capabilities import overlay_path, record_outcome
from caprouter.duplicates import find_duplicates, prefer_duplicate
from caprouter.health import capability_health
from caprouter.probes import record_probe
from caprouter.util import write_json_atomic
from caprouter.workspace import create_workspace


class DuplicatesHealthTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root_a = base / "roots" / "a"
        self.root_b = base / "roots" / "b"
        for root in (self.root_a, self.root_b):
            skill = root / "demo"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\nname: demo\ndescription: Demo skill\n---\n# Demo\n",
                encoding="utf-8",
            )
        self.ws = base / "ws"
        create_workspace(
            self.ws,
            include_agents=False,
            include_default_roots=False,
            extra_skill_roots=[self.root_a, self.root_b],
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _health(self, capability_id: str) -> dict:
        return next(
            item
            for item in capability_health(self.ws)["capabilities"]
            if item["id"] == capability_id
        )

    def test_duplicates_group_same_content(self) -> None:
        result = find_duplicates(self.ws)
        self.assertEqual(result["count"], 1)
        group = result["groups"][0]
        self.assertEqual(group["name"], "demo")
        self.assertEqual(group["count"], 2)
        self.assertTrue(group["same_content"])
        self.assertEqual(len({item["path"] for item in group["items"]}), 2)

    def test_prefer_duplicate_reorders_roots(self) -> None:
        result = prefer_duplicate(self.ws, self.root_b / "demo")
        self.assertEqual(result["status"], "preferred")
        registry = json.loads(
            (self.ws / ".caprouter" / "registry.json").read_text(encoding="utf-8")
        )
        entry = next(item for item in registry["entries"] if item["id"] == "skill:demo")
        self.assertEqual(Path(entry["path"]).resolve(), (self.root_b / "demo").resolve())

    def test_duplicates_detect_different_content(self) -> None:
        (self.root_b / "demo" / "SKILL.md").write_text(
            "---\nname: demo\ndescription: Different body\n---\n# Different\n",
            encoding="utf-8",
        )
        result = find_duplicates(self.ws)
        self.assertEqual(result["count"], 1)
        self.assertFalse(result["groups"][0]["same_content"])

    def test_health_draft_probation_active_degraded_retired(self) -> None:
        self.assertEqual(self._health("skill:demo")["state"], "draft")

        write_json_atomic(overlay_path(self.ws), {"cards": {"skill:demo": {"description": "reviewed"}}})
        self.assertEqual(self._health("skill:demo")["state"], "probation")

        record_probe(self.ws, "skill:demo", task="host probe", status="pass")
        self.assertEqual(self._health("skill:demo")["state"], "active")

        record_outcome(self.ws, "skill:demo", success=False)
        record_outcome(self.ws, "skill:demo", success=False)
        self.assertEqual(self._health("skill:demo")["state"], "degraded")

        write_json_atomic(
            overlay_path(self.ws),
            {"cards": {"skill:demo": {"description": "reviewed", "lifecycle": "retired"}}},
        )
        self.assertEqual(self._health("skill:demo")["state"], "retired")

    def test_health_active_from_successful_outcomes(self) -> None:
        record_outcome(self.ws, "skill:demo", success=True)
        record_outcome(self.ws, "skill:demo", success=True)
        row = self._health("skill:demo")
        self.assertEqual(row["state"], "active")
        self.assertEqual(row["success_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
