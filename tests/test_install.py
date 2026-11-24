from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from caprouter.install import install_source, list_installed, preview_install


class InstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name) / "ws"
        self.ws.mkdir()
        bus = self.ws / ".caprouter"
        bus.mkdir()
        (bus / "config.json").write_text(
            json.dumps({
                "schema": "caprouter.config.v1", "mode": "bus",
                "skill_roots": [], "plugin_roots": [], "scan_agents": False,
                "routing": {},
            }),
            encoding="utf-8",
        )
        (bus / "registry.json").write_text(json.dumps({"entries": []}), encoding="utf-8")

        self.skill = Path(self.tmp.name) / "source" / "installed-skill"
        self.skill.mkdir(parents=True)
        (self.skill / "SKILL.md").write_text(
            "---\nname: installed-skill\nversion: 1.0.0\n"
            "description: Convert PDF pages into images\n---\n"
            "## Examples\n- 把 PDF 第 3 页转成 PNG\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_install_refuses_uninitialized_workspace(self) -> None:
        uninit = Path(self.tmp.name) / "uninitialized"
        uninit.mkdir()
        result = install_source(uninit, self.skill, approve=True)
        self.assertEqual(result["status"], "not_initialized")
        self.assertFalse((uninit / ".caprouter").exists())

    def test_preview_does_not_install(self) -> None:
        preview = preview_install(self.ws, self.skill)
        self.assertEqual(preview["type"], "skill")
        self.assertEqual(preview["name"], "installed-skill")
        self.assertEqual(preview["version"], "1.0.0")
        self.assertFalse((self.ws / ".caprouter" / "installed" / "skills" / "installed-skill").exists())

    def test_install_requires_approval(self) -> None:
        result = install_source(self.ws, self.skill)
        self.assertEqual(result["status"], "approval_required")
        self.assertFalse((self.ws / ".caprouter" / "installed" / "skills" / "installed-skill").exists())

    def test_install_skill_generates_manifest_registry_and_draft(self) -> None:
        result = install_source(self.ws, self.skill, approve=True)
        self.assertEqual(result["status"], "installed")
        target = Path(result["target"])
        self.assertTrue((target / "SKILL.md").is_file())
        self.assertTrue((target / "manifest.json").is_file())
        self.assertEqual(result["manifest"]["id"], "skill:installed-skill")
        self.assertTrue(Path(result["draft_path"]).is_file())
        self.assertEqual(result["config_roots_changed"], ["skill_roots"])

        registry = json.loads((self.ws / ".caprouter" / "registry.json").read_text(encoding="utf-8"))
        ids = {entry["id"] for entry in registry["entries"]}
        self.assertIn("skill:installed-skill", ids)
        config = json.loads((self.ws / ".caprouter" / "config.json").read_text(encoding="utf-8"))
        self.assertTrue(any("installed" in root for root in config["skill_roots"]))

    def test_install_existing_requires_force(self) -> None:
        first = install_source(self.ws, self.skill, approve=True)
        self.assertEqual(first["status"], "installed")
        second = install_source(self.ws, self.skill, approve=True)
        self.assertEqual(second["status"], "exists")
        forced = install_source(self.ws, self.skill, approve=True, force=True)
        self.assertEqual(forced["status"], "installed")
        self.assertTrue((self.ws / ".caprouter" / "installed" / ".trash").is_dir())

    def test_install_zip(self) -> None:
        archive = Path(self.tmp.name) / "skill.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            for path in self.skill.rglob("*"):
                if path.is_file():
                    zf.write(path, path.relative_to(self.skill.parent).as_posix())
        result = install_source(self.ws, archive, approve=True)
        self.assertEqual(result["status"], "installed")
        self.assertTrue((Path(result["target"]) / "SKILL.md").is_file())
        self.assertEqual(result["manifest"]["source"]["kind"], "local-zip")

    def test_zip_traversal_is_rejected(self) -> None:
        archive = Path(self.tmp.name) / "evil.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("../escape.txt", "nope")
        with self.assertRaises(ValueError):
            preview_install(self.ws, archive)

    def test_install_never_executes_scripts(self) -> None:
        marker = Path(self.tmp.name) / "executed.txt"
        scripts = self.skill / "scripts"
        scripts.mkdir()
        (scripts / "postinstall.py").write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n",
            encoding="utf-8",
        )
        result = install_source(self.ws, self.skill, approve=True)
        self.assertEqual(result["status"], "installed")
        self.assertFalse(marker.exists())

    def test_list_installed(self) -> None:
        install_source(self.ws, self.skill, approve=True)
        items = list_installed(self.ws)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], "skill:installed-skill")

    def test_install_plugin(self) -> None:
        plugin = Path(self.tmp.name) / "source" / "demo-plugin"
        (plugin / ".claude-plugin").mkdir(parents=True)
        (plugin / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "demo-plugin", "version": "0.5.0", "description": "Demo plugin"}),
            encoding="utf-8",
        )
        result = install_source(self.ws, plugin, approve=True)
        self.assertEqual(result["status"], "installed")
        self.assertEqual(result["manifest"]["id"], "plugin:demo-plugin")
        self.assertIn("plugin_roots", result["config_roots_changed"])


if __name__ == "__main__":
    unittest.main()
