"""Install lifecycle integrity: sortable trash stamps and rollback selection."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for _path in (ROOT, HERE):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from support import enter_isolated_env, make_workspace  # noqa: E402

from caprouter.lifecycle import (  # noqa: E402
    rollback_capability,
    rollback_to_version,
    _trash_stamp as lifecycle_stamp,
)
from caprouter.install import _trash_stamp as install_stamp  # noqa: E402


class TrashStampTests(unittest.TestCase):
    def test_stamps_are_zero_padded_and_sortable(self) -> None:
        for factory in (lifecycle_stamp, install_stamp):
            with self.subTest(factory=factory.__module__):
                stamp = factory()
                # ``YYYYmmdd-HHMMSS-mmm``: a single-digit millisecond used to
                # produce names that sort *after* later ones ("5" > "100").
                self.assertRegex(stamp, r"^\d{8}-\d{6}-\d{3}$")
                self.assertEqual(len(stamp), 19)


class RollbackSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="caprouter-lifecycle-"))
        enter_isolated_env(self, self.tmp / "home")
        self.workspace = make_workspace(self.tmp)
        self.installed = self.workspace / ".caprouter" / "installed"
        self.trash = self.installed / ".trash"
        self.trash.mkdir(parents=True, exist_ok=True)
        self.target = self.installed / "skills" / "demo"
        self.target.mkdir(parents=True, exist_ok=True)
        (self.target / "SKILL.md").write_text("---\nname: demo\ndescription: d\n---\nbody\n", encoding="utf-8")
        self._register()

    def _register(self) -> None:
        registry_path = self.workspace / ".caprouter" / "registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry.setdefault("entries", []).append(
            {
                "id": "skill:demo",
                "type": "skill",
                "name": "demo",
                "path": str(self.target),
                "status": "verified",
                "description": "demo",
                "keywords": [],
                "capabilities": [],
            }
        )
        registry["stats"] = {"total": len(registry["entries"]), "skill": len(registry["entries"])}
        registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")

    def _trash_copy(self, name: str, version: str, *, mtime: float) -> Path:
        directory = self.trash / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "SKILL.md").write_text("---\nname: demo\ndescription: old\n---\nold\n", encoding="utf-8")
        (directory / "manifest.json").write_text(
            json.dumps({"schema": "caprouter.install.v1", "id": "skill:demo", "version": version, "hash": "sha256:x"}),
            encoding="utf-8",
        )
        os.utime(directory, (mtime, mtime))
        return directory

    def test_newest_candidate_is_chosen_by_time_not_name(self) -> None:
        # The lexicographically "newest" name carries the oldest mtime.
        old_by_name = self._trash_copy("demo-20990101-000000-000", "1.0.0", mtime=time.time() - 5000)
        new_by_time = self._trash_copy("demo-20000101-000000-000", "1.0.0", mtime=time.time())
        result = rollback_to_version(self.workspace, "skill:demo", version="1.0.0", dry_run=True)
        self.assertEqual(Path(result["restore_from"]), new_by_time)
        self.assertNotEqual(Path(result["restore_from"]), old_by_name)

    def test_rollback_refuses_a_trash_path_outside_dot_trash(self) -> None:
        outside = self.tmp / "planted"
        outside.mkdir()
        (outside / "SKILL.md").write_text("x", encoding="utf-8")
        log = self.workspace / ".caprouter" / "uninstalled.jsonl"
        log.write_text(
            json.dumps(
                {
                    "action": "uninstall",
                    "id": "skill:demo",
                    "trash_path": str(outside),
                    "original_path": str(self.target),
                    "uninstalled_at": "2026-11-25T00:00:00+08:00",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        with self.assertRaises(ValueError) as ctx:
            rollback_capability(self.workspace, "skill:demo", dry_run=True)
        self.assertIn("outside installed/.trash", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
