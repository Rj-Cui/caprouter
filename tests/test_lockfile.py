from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from caprouter.lockfile import check_drift, lock_path, write_lock
from caprouter.workspace import create_workspace, refresh_registry


class LockfileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "skills"
        self.skill = self.root / "demo"
        self.skill.mkdir(parents=True)
        (self.skill / "SKILL.md").write_text(
            "---\nname: demo\ndescription: Demo\n---\n# Demo\n", encoding="utf-8"
        )
        self.ws = base / "ws"
        create_workspace(
            self.ws,
            include_agents=False,
            include_default_roots=False,
            extra_skill_roots=[self.root],
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _refresh(self) -> None:
        config = json.loads((self.ws / ".caprouter" / "config.json").read_text(encoding="utf-8"))
        refresh_registry(self.ws, config)

    def test_lock_and_clean_drift(self) -> None:
        result = write_lock(self.ws)
        self.assertTrue(Path(result["lock_path"]).is_file())
        self.assertEqual(result["lock_path"], str(lock_path(self.ws)))
        drift = check_drift(self.ws)
        self.assertTrue(drift["clean"])
        self.assertEqual(drift["counts"]["changed"], 0)

    def test_live_drift_detects_changed_files(self) -> None:
        write_lock(self.ws)
        (self.skill / "SKILL.md").write_text(
            "---\nname: demo\ndescription: Changed\n---\n# Changed\n", encoding="utf-8"
        )
        self.assertTrue(check_drift(self.ws)["clean"])
        live = check_drift(self.ws, live=True)
        self.assertFalse(live["clean"])
        self.assertTrue(any(item["id"] == "skill:demo" for item in live["changed"]))

    def test_missing_and_new_after_rescan(self) -> None:
        write_lock(self.ws)
        other = self.root / "second"
        other.mkdir()
        (other / "SKILL.md").write_text(
            "---\nname: second\ndescription: Second\n---\n# Second\n", encoding="utf-8"
        )
        self._refresh()
        added = check_drift(self.ws)
        self.assertIn("skill:second", added["new"])

        import shutil

        shutil.rmtree(self.skill)
        self._refresh()
        removed = check_drift(self.ws)
        self.assertIn("skill:demo", removed["missing"])

    def test_invalid_lockfile_raises(self) -> None:
        with self.assertRaises(ValueError):
            check_drift(self.ws, lock=self.ws / "missing.json")


if __name__ == "__main__":
    unittest.main()
