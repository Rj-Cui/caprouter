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

from caprouter.backup import create_backup, list_backups, restore_backup


class BackupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)
        state_dir = self.workspace / ".caprouter"
        state_dir.mkdir()
        (state_dir / "plans").mkdir()
        (state_dir / "config.json").write_text(
            json.dumps({"workspace": str(self.workspace)}), encoding="utf-8"
        )
        (state_dir / "registry.json").write_text(json.dumps({"entries": []}), encoding="utf-8")
        (state_dir / "plans" / "p.json").write_text("{}", encoding="utf-8")
        self.state = self.workspace / "state.json"
        self.state.write_text(json.dumps({"enabled": True}), encoding="utf-8")
        self.env = patch.dict(os.environ, {"CAPROUTER_STATE": str(self.state)})
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def test_backup_and_restore(self) -> None:
        result = create_backup(self.workspace, "test")
        backup = Path(result["backup_dir"])
        self.assertTrue((backup / "config.json").exists())
        self.assertTrue((backup / "registry.json").exists())
        self.assertTrue((backup / "state.json").exists())
        self.assertTrue((backup / "plans" / "p.json").exists())

        config = self.workspace / ".caprouter" / "config.json"
        config.write_text(json.dumps({"workspace": "changed"}), encoding="utf-8")
        with self.assertRaises(ValueError):
            restore_backup(self.workspace, backup)
        restore_backup(self.workspace, backup, force=True)
        self.assertEqual(json.loads(config.read_text(encoding="utf-8"))["workspace"], str(self.workspace))

    def test_list_backups(self) -> None:
        create_backup(self.workspace)
        items = list_backups(self.workspace)
        self.assertEqual(len(items), 1)
        self.assertTrue(Path(items[0]["backup_dir"]).is_dir())

    def test_backup_label_cannot_escape_workspace(self) -> None:
        result = create_backup(self.workspace, "../../escape")
        backup = Path(result["backup_dir"]).resolve()
        root = (self.workspace / "backups").resolve()
        self.assertTrue(backup.is_relative_to(root))


if __name__ == "__main__":
    unittest.main()
