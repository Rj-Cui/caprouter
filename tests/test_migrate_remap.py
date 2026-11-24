from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from caprouter.cards import drafts_dir
from caprouter.migrate import list_migrated, remap_migrated
from caprouter.util import write_json_atomic
from caprouter.workspace import create_workspace


class MigrateRemapTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.ws = base / "ws"
        create_workspace(self.ws, include_agents=False, include_default_roots=False)
        self.migrated = drafts_dir(self.ws) / "migrated" / "demo-1234.json"
        write_json_atomic(
            self.migrated,
            {
                "schema": "caprouter.capability-draft.v1",
                "id": "skill:demo",
                "type": "skill",
                "name": "demo",
                "description": "Migrated demo",
                "path": "",
                "provenance": {"source": "migration", "path_hint": ["/old/machine/demo"]},
                "review": {"status": "migrated_unmatched", "risk": "unknown", "notes": []},
            },
        )
        self.new_path = base / "local" / "demo"
        self.new_path.mkdir(parents=True)
        (self.new_path / "SKILL.md").write_text(
            "---\nname: demo\ndescription: Local demo\n---\n# Demo\n", encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_list_and_remap(self) -> None:
        listed = list_migrated(self.ws)
        self.assertEqual(listed["count"], 1)
        self.assertEqual(listed["entries"][0]["id"], "skill:demo")

        result = remap_migrated(self.ws, "skill:demo", self.new_path)
        self.assertEqual(result["status"], "remapped")
        draft_path = Path(result["draft_path"])
        self.assertTrue(draft_path.is_file())
        draft = json.loads(draft_path.read_text(encoding="utf-8"))
        self.assertEqual(draft["id"], "skill:demo")
        self.assertEqual(draft["path"], str(self.new_path.resolve()))
        self.assertFalse(self.migrated.exists())
        self.assertEqual(list_migrated(self.ws)["count"], 0)

    def test_remap_missing_draft_raises(self) -> None:
        with self.assertRaises(ValueError):
            remap_migrated(self.ws, "skill:missing", self.new_path)

    def test_remap_rejects_non_directory(self) -> None:
        with self.assertRaises(ValueError):
            remap_migrated(self.ws, "skill:demo", self.new_path / "SKILL.md")


if __name__ == "__main__":
    unittest.main()
