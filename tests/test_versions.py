from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from caprouter.install import install_source
from caprouter.lifecycle import list_versions, rollback_to_version, update_source
from caprouter.workspace import create_workspace


class VersionsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.ws = base / "ws"
        create_workspace(self.ws, include_agents=False, include_default_roots=False)
        self.source = base / "source" / "demo"
        self.source.mkdir(parents=True)
        self._write_version("1.0.0", "one")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_version(self, version: str, body: str) -> None:
        (self.source / "SKILL.md").write_text(
            f"---\nname: demo\ndescription: Demo {body}\nversion: {version}\n---\n# {body}\n",
            encoding="utf-8",
        )

    def test_update_then_rollback_to_version(self) -> None:
        first = install_source(self.ws, self.source, approve=True)
        self.assertEqual(first["status"], "installed")
        self._write_version("2.0.0", "two")
        updated = update_source(self.ws, "skill:demo", self.source, approve=True)
        self.assertEqual(updated["status"], "updated")
        self.assertEqual(updated["previous"]["version"], "1.0.0")
        self.assertEqual(updated["manifest"]["version"], "2.0.0")

        versions = list_versions(self.ws, "skill:demo")
        self.assertEqual(len(versions["versions"]), 2)
        self.assertEqual(versions["current"]["version"], "2.0.0")
        self.assertTrue(any(item["version"] == "1.0.0" and not item["current"] for item in versions["versions"]))

        preview = rollback_to_version(self.ws, "skill:demo", version="1.0.0")
        self.assertEqual(preview["status"], "preview")
        rolled = rollback_to_version(self.ws, "skill:demo", version="1.0.0", dry_run=False)
        self.assertEqual(rolled["status"], "rolled_back")
        current = list_versions(self.ws, "skill:demo")["current"]
        self.assertEqual(current["version"], "1.0.0")

    def test_update_is_up_to_date_for_same_source_hash(self) -> None:
        install_source(self.ws, self.source, approve=True)
        result = update_source(self.ws, "skill:demo", self.source, approve=True)
        self.assertEqual(result["status"], "up_to_date")

    def test_update_rejects_id_mismatch(self) -> None:
        install_source(self.ws, self.source, approve=True)
        other = Path(self.tmp.name) / "other"
        other.mkdir()
        (other / "SKILL.md").write_text(
            "---\nname: other\ndescription: Other\n---\n# Other\n", encoding="utf-8"
        )
        with self.assertRaises(ValueError):
            update_source(self.ws, "skill:demo", other)

    def test_rollback_without_match_raises(self) -> None:
        install_source(self.ws, self.source, approve=True)
        with self.assertRaises(ValueError):
            rollback_to_version(self.ws, "skill:demo", version="9.9.9")


if __name__ == "__main__":
    unittest.main()
