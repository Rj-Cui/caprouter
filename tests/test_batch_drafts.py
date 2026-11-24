from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from caprouter.capabilities import load_entries
from caprouter.cards import generate_registry_drafts
from caprouter.review import review_drafts
from caprouter.workspace import create_workspace


class BatchRegistryDraftTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.external = base / "external"
        skill = self.external / "demo"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: demo\ndescription: Demo skill used for batch drafting\n"
            "capabilities: [transform]\n---\n"
            "# Demo\n\n## Examples\n\n- Convert a file\n- Summarize a document\n",
            encoding="utf-8",
        )
        self.ws = base / "ws"
        create_workspace(
            self.ws,
            include_agents=False,
            include_default_roots=False,
            extra_skill_roots=[self.external],
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _demo_entries(self) -> list[dict]:
        return [entry for entry in load_entries(self.ws) if entry["id"] == "skill:demo"]

    def test_drafts_skill_and_skips_unchanged(self) -> None:
        report = generate_registry_drafts(self.ws, self._demo_entries(), types={"skill"})
        self.assertEqual(report["scanned"], 1)
        self.assertEqual(report["drafted"], 1)
        draft_path = Path(report["drafts"][0]["draft_path"])
        self.assertTrue(draft_path.is_file())
        draft = json.loads(draft_path.read_text(encoding="utf-8"))
        self.assertEqual(draft["id"], "skill:demo")
        self.assertEqual(draft["type"], "skill")
        self.assertTrue(draft.get("examples"))

        second = generate_registry_drafts(self.ws, self._demo_entries(), types={"skill"})
        self.assertEqual(second["drafted"], 0)
        self.assertEqual(second["unchanged"], 1)

    def test_review_sees_registry_drafts(self) -> None:
        generate_registry_drafts(self.ws, self._demo_entries(), types={"skill"})
        report = review_drafts(self.ws)
        self.assertGreaterEqual(report.get("total", 0), 1)
        ids = {item.get("id") for item in report.get("reviews") or report.get("items") or []}
        self.assertIn("skill:demo", ids)

    def test_force_rewrites(self) -> None:
        generate_registry_drafts(self.ws, self._demo_entries(), types={"skill"})
        report = generate_registry_drafts(
            self.ws, self._demo_entries(), types={"skill"}, force=True
        )
        self.assertEqual(report["drafted"], 1)


if __name__ == "__main__":
    unittest.main()
