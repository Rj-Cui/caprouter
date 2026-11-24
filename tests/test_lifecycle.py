from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from caprouter.capabilities import load_overlay, overlay_path
from caprouter.install import install_source
from caprouter.lifecycle import (
    rollback_capability,
    uninstall_capability,
    uninstalled_path,
)
from caprouter.util import write_json_atomic


class LifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name) / "ws"
        bus = self.ws / ".caprouter"
        bus.mkdir(parents=True)
        (bus / "config.json").write_text(
            json.dumps(
                {
                    "schema": "caprouter.config.v1",
                    "skill_roots": [],
                    "plugin_roots": [],
                    "scan_agents": False,
                    "routing": {},
                }
            ),
            encoding="utf-8",
        )
        (bus / "registry.json").write_text(json.dumps({"entries": []}), encoding="utf-8")
        source = Path(self.tmp.name) / "source" / "demo-skill"
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text(
            "---\nname: demo-skill\ndescription: Demo skill for lifecycle tests\n---\nbody\n",
            encoding="utf-8",
        )
        result = install_source(self.ws, source, approve=True)
        self.assertEqual(result["status"], "installed")
        self.target = Path(result["target"])
        self.capability_id = "skill:demo-skill"
        write_json_atomic(
            overlay_path(self.ws),
            {
                "schema": "caprouter.capability-cards.v1",
                "cards": {self.capability_id: {"description": "card to restore"}},
            },
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_uninstall_preview_does_not_change_anything(self) -> None:
        result = uninstall_capability(self.ws, self.capability_id, dry_run=True)
        self.assertEqual(result["status"], "preview")
        self.assertTrue(self.target.is_dir())
        self.assertIn(self.capability_id, [
            entry["id"]
            for entry in json.loads((self.ws / ".caprouter" / "registry.json").read_text(encoding="utf-8"))["entries"]
        ])
        self.assertIn(self.capability_id, load_overlay(self.ws)["cards"])

    def test_uninstall_then_rollback(self) -> None:
        removed = uninstall_capability(self.ws, self.capability_id, dry_run=False)
        self.assertEqual(removed["status"], "uninstalled")
        self.assertFalse(self.target.exists())
        self.assertTrue(Path(removed["trash_path"]).is_dir())
        registry = json.loads((self.ws / ".caprouter" / "registry.json").read_text(encoding="utf-8"))
        self.assertNotIn(self.capability_id, [entry["id"] for entry in registry["entries"]])
        self.assertNotIn(self.capability_id, load_overlay(self.ws)["cards"])

        restored = rollback_capability(self.ws, self.capability_id, dry_run=False)
        self.assertEqual(restored["status"], "rolled_back")
        self.assertTrue(self.target.is_dir())
        registry = json.loads((self.ws / ".caprouter" / "registry.json").read_text(encoding="utf-8"))
        self.assertIn(self.capability_id, [entry["id"] for entry in registry["entries"]])
        self.assertIn(self.capability_id, load_overlay(self.ws)["cards"])

        rows = [
            json.loads(line)
            for line in uninstalled_path(self.ws).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual([row["action"] for row in rows], ["uninstall", "rollback"])

    def test_uninstall_unknown_id(self) -> None:
        with self.assertRaises(ValueError):
            uninstall_capability(self.ws, "skill:missing")

    def test_uninstall_refuses_path_outside_installed(self) -> None:
        registry_path = self.ws / ".caprouter" / "registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry["entries"] = [
            {
                "id": "skill:outside",
                "type": "skill",
                "name": "outside",
                "path": str(Path(self.tmp.name) / "source" / "demo-skill"),
            }
        ]
        registry_path.write_text(json.dumps(registry), encoding="utf-8")
        with self.assertRaises(ValueError):
            uninstall_capability(self.ws, "skill:outside", dry_run=False)

    def test_rollback_without_record(self) -> None:
        with self.assertRaises(ValueError):
            rollback_capability(self.ws, self.capability_id)

    def test_rollback_refuses_existing_target_and_force_displaces(self) -> None:
        uninstall_capability(self.ws, self.capability_id, dry_run=False)
        self.target.mkdir(parents=True)
        (self.target / "new.txt").write_text("occupied", encoding="utf-8")
        with self.assertRaises(ValueError):
            rollback_capability(self.ws, self.capability_id, dry_run=False)
        result = rollback_capability(self.ws, self.capability_id, dry_run=False, force=True)
        self.assertEqual(result["status"], "rolled_back")
        self.assertTrue((self.target / "SKILL.md").is_file())
        self.assertTrue(Path(result["displaced_path"]).is_dir())


if __name__ == "__main__":
    unittest.main()
