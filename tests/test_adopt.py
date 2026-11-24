from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from caprouter.adopt import adopt_capability, backup_capability, list_unmanaged
from caprouter.lifecycle import uninstall_capability
from caprouter.workspace import create_workspace


class AdoptTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.external_root = base / "external"
        self.skill = self.external_root / "demo"
        self.skill.mkdir(parents=True)
        (self.skill / "SKILL.md").write_text(
            "---\nname: demo\ndescription: Demo skill for adoption\nversion: 1.2.3\n---\n# Demo\n",
            encoding="utf-8",
        )
        (self.skill / "scripts").mkdir()
        (self.skill / "scripts" / "run.py").write_text("print('demo')\n", encoding="utf-8")
        self.ws = base / "ws"
        create_workspace(
            self.ws,
            include_agents=False,
            include_default_roots=False,
            extra_skill_roots=[self.external_root],
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _registry_entry(self, capability_id: str) -> dict:
        data = json.loads((self.ws / ".caprouter" / "registry.json").read_text(encoding="utf-8"))
        for entry in data["entries"]:
            if entry["id"] == capability_id:
                return entry
        raise AssertionError(f"missing registry entry: {capability_id}")

    def test_lists_unmanaged(self) -> None:
        result = list_unmanaged(self.ws)
        ids = {item["id"] for item in result["entries"]}
        self.assertIn("skill:demo", ids)
        self.assertGreaterEqual(result["count"], 1)

    def test_preview_does_not_copy(self) -> None:
        result = adopt_capability(self.ws, "skill:demo")
        self.assertEqual(result["status"], "preview")
        self.assertTrue(Path(result["source"]).is_dir())
        self.assertFalse(Path(result["target"]).exists())

    def test_adopt_copies_and_registry_prefers_managed_copy(self) -> None:
        result = adopt_capability(self.ws, "skill:demo", dry_run=False)
        self.assertEqual(result["status"], "adopted")
        target = Path(result["target"])
        self.assertTrue((target / "SKILL.md").is_file())
        self.assertTrue((self.skill / "SKILL.md").is_file())
        manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["source"]["kind"], "adopted")
        self.assertEqual(Path(manifest["adopted_from"]).resolve(), self.skill.resolve())
        self.assertTrue(manifest["managed"])
        self.assertEqual(Path(self._registry_entry("skill:demo")["path"]).resolve(), target.resolve())
        config = json.loads((self.ws / ".caprouter" / "config.json").read_text(encoding="utf-8"))
        self.assertTrue(config["skill_roots"][0].endswith("installed\\skills") or config["skill_roots"][0].endswith("installed/skills"))

    def test_adopt_is_idempotent_after_managed(self) -> None:
        adopt_capability(self.ws, "skill:demo", dry_run=False)
        result = adopt_capability(self.ws, "skill:demo", dry_run=False)
        self.assertEqual(result["status"], "already_managed")

    def test_uninstall_adopted_copy_keeps_original(self) -> None:
        adopt_capability(self.ws, "skill:demo", dry_run=False)
        result = uninstall_capability(self.ws, "skill:demo", dry_run=False)
        self.assertEqual(result["status"], "uninstalled")
        self.assertFalse(Path(result["path"]).exists())
        self.assertTrue((self.skill / "SKILL.md").is_file())

    def test_backup_skill(self) -> None:
        result = backup_capability(self.ws, "skill:demo")
        backup = Path(result["backup"])
        self.assertTrue((backup / "SKILL.md").is_file())
        self.assertTrue((backup / "backup.json").is_file())
        self.assertTrue((self.skill / "SKILL.md").is_file())

    def test_adopt_rejects_non_skill(self) -> None:
        with self.assertRaises(ValueError):
            adopt_capability(self.ws, "project:demo", dry_run=False)


if __name__ == "__main__":
    unittest.main()
