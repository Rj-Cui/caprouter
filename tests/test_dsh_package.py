"""The repository root must stay installable as a DSH bundle (market requirement)."""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _path in (ROOT, Path(__file__).resolve().parent):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))


class RepoDshPackageTests(unittest.TestCase):
    """`dsh plugin add github:Rj-Cui/caprouter` reads the manifest at the repository root."""

    def setUp(self) -> None:
        manifest = ROOT / "package.json"
        self.assertTrue(manifest.is_file(), "package.json is required at the repository root")
        self.manifest = json.loads(manifest.read_text(encoding="utf-8"))

    def test_declares_a_bundle_patch(self) -> None:
        patch = (self.manifest.get("dsh") or {}).get("bundle", {}).get("patch")
        self.assertTrue(patch, "package.json must declare dsh.bundle.patch")
        self.assertTrue((ROOT / patch).is_file(), f"missing patch file: {patch}")

    def test_version_matches_pyproject(self) -> None:
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
        self.assertIsNotNone(match)
        self.assertEqual(self.manifest["version"], match.group(1))

    def test_locales_name_an_existing_detail_document(self) -> None:
        for locale in ("en", "zh"):
            data = json.loads((ROOT / "locale" / f"{locale}.json").read_text(encoding="utf-8"))
            meta = data.get("meta") or {}
            self.assertTrue(meta.get("title"))
            self.assertTrue(meta.get("description"))
            detail = meta.get("detail")
            if detail:
                self.assertTrue((ROOT / detail).is_file(), f"missing detail document: {detail}")

    def test_patch_is_portable(self) -> None:
        patch = (ROOT / self.manifest["dsh"]["bundle"]["patch"]).read_text(encoding="utf-8")
        self.assertIn("!!js", patch)
        self.assertIn("CAPROUTER_PYTHON", patch)
        self.assertIn("-m", patch)
        self.assertNotIn("--workspace", patch)
        for forbidden in ("C:" + chr(92), "D:" + chr(92), "python.exe"):
            self.assertNotIn(forbidden, patch)

    def test_manifest_and_locales_have_no_machine_paths(self) -> None:
        for relative in ("package.json", "cordis.patch.yml", "locale/en.json", "locale/zh.json",
                         "docs/DETAIL.md"):
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertNotIn("C:" + chr(92) + "Users", text, relative)
            self.assertNotIn("D:" + chr(92) + "CapRouter", text, relative)


if __name__ == "__main__":
    unittest.main()
