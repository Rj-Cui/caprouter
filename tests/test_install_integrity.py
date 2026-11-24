from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from caprouter.install import install_source, preview_install
from caprouter.workspace import create_workspace

MIT_TEXT = """MIT License

Copyright (c) 2026 Example

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software.
"""


def hash_rows(root: Path, rows: list[dict]) -> str:
    digest = hashlib.sha256()
    for row in sorted(rows, key=lambda item: item["path"]):
        path = root / row["path"]
        digest.update(row["path"].encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


class InstallIntegrityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.ws = base / "ws"
        create_workspace(self.ws, include_agents=False, include_default_roots=False)
        self.source = base / "source" / "demo"
        self.source.mkdir(parents=True)
        (self.source / "SKILL.md").write_text(
            "---\nname: demo\ndescription: Demo skill\nversion: 1.0.0\n---\n# Demo\n",
            encoding="utf-8",
        )
        (self.source / "package.json").write_text(
            json.dumps({"dependencies": {"left-pad": "1.0.0"}}), encoding="utf-8"
        )
        (self.source / "LICENSE").write_text(MIT_TEXT, encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_preview_persists_hash_and_manifest_has_license_and_deps(self) -> None:
        preview = preview_install(self.ws, self.source)
        self.assertTrue(preview["source_hash"].startswith("sha256:"))
        self.assertEqual(preview["license"]["name"], "MIT")
        self.assertIn("left-pad", preview["dependencies"])
        previews = list((self.ws / ".caprouter" / "installs" / "previews").glob("*.json"))
        self.assertEqual(len(previews), 1)
        record = json.loads(previews[0].read_text(encoding="utf-8"))
        self.assertEqual(record["source_hash"], preview["source_hash"])

        result = install_source(self.ws, self.source, approve=True)
        self.assertEqual(result["status"], "installed")
        self.assertTrue(result["preview_verified"])
        manifest = result["manifest"]
        self.assertEqual(manifest["license"]["name"], "MIT")
        self.assertIn("left-pad", manifest["dependencies"])
        self.assertEqual(manifest["minimum_caprouter_version"], "0.6.3")
        self.assertEqual(manifest["provenance"]["status"], "absent")
        journal = (self.ws / ".caprouter" / "installs" / "journal.jsonl").read_text(encoding="utf-8")
        for event in ("install_approved", "copy_started", "copied", "manifest_written", "registry_refreshed"):
            self.assertIn(event, journal)

    def test_source_changed_between_preview_and_approve(self) -> None:
        preview_install(self.ws, self.source)
        (self.source / "SKILL.md").write_text(
            "---\nname: demo\ndescription: changed\n---\n# Changed\n", encoding="utf-8"
        )
        result = install_source(self.ws, self.source, approve=True)
        self.assertEqual(result["status"], "source_changed")
        self.assertFalse((self.ws / ".caprouter" / "installed" / "skills" / "demo").exists())
        forced = install_source(self.ws, self.source, approve=True, force=True)
        self.assertEqual(forced["status"], "installed")

    def test_provenance_hash_is_verified(self) -> None:
        rows = [
            {"path": "SKILL.md"},
            {"path": "package.json"},
        ]
        provenance = {
            "schema": "caprouter.quarantine.v1",
            "candidate_id": "example/demo",
            "source": "example",
            "files": rows,
            "content_hash": hash_rows(self.source, rows),
            "license": {"name": "MIT", "file": "LICENSE", "detected": True},
        }
        (self.source / "provenance.json").write_text(json.dumps(provenance), encoding="utf-8")
        result = install_source(self.ws, self.source, approve=True)
        self.assertEqual(result["status"], "installed")
        self.assertTrue(result["provenance"]["verified"])
        self.assertEqual(result["provenance"]["content_hash"], provenance["content_hash"])

    def test_provenance_mismatch_blocks_install(self) -> None:
        rows = [{"path": "SKILL.md"}]
        provenance = {
            "schema": "caprouter.quarantine.v1",
            "candidate_id": "example/demo",
            "files": rows,
            "content_hash": hash_rows(self.source, rows),
        }
        (self.source / "provenance.json").write_text(json.dumps(provenance), encoding="utf-8")
        (self.source / "SKILL.md").write_text("# tampered\n", encoding="utf-8")
        result = install_source(self.ws, self.source, approve=True, force=True)
        self.assertEqual(result["status"], "provenance_mismatch")
        overridden = install_source(self.ws, self.source, approve=True, skip_provenance=True)
        self.assertEqual(overridden["status"], "installed")

    def test_payload_only_excludes_vendor_artifacts(self) -> None:
        (self.source / "provenance.json").write_text("{}", encoding="utf-8")
        (self.source / "deconstruction.md").write_text("vendor\n", encoding="utf-8")
        (self.source / "card_draft.json").write_text("{}", encoding="utf-8")
        result = install_source(self.ws, self.source, approve=True, payload_only=True, skip_provenance=True)
        self.assertEqual(result["status"], "installed")
        target = Path(result["target"])
        self.assertTrue((target / "SKILL.md").is_file())
        self.assertFalse((target / "deconstruction.md").exists())
        self.assertFalse((target / "card_draft.json").exists())

    def test_symlink_requires_explicit_flag(self) -> None:
        outside = Path(self.tmp.name) / "outside.txt"
        outside.write_text("outside\n", encoding="utf-8")
        link = self.source / "link.txt"
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation is not permitted on this platform")
        preview = preview_install(self.ws, self.source)
        self.assertTrue(preview["symlinks"])
        result = install_source(self.ws, self.source, approve=True)
        self.assertEqual(result["status"], "unsafe_symlinks")
        allowed = install_source(self.ws, self.source, approve=True, allow_symlinks=True)
        self.assertEqual(allowed["status"], "installed")


if __name__ == "__main__":
    unittest.main()
