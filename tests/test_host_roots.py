from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from caprouter.paths import default_plugin_roots, default_skill_roots, host_skill_roots
from caprouter.workspace import create_workspace


class HostRootsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name) / "ws"
        self.ws.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_workspace_host_skill_roots_are_included(self) -> None:
        expected = [
            self.ws / ".claude" / "skills",
            self.ws / ".agents" / "skills",
            self.ws / ".cursor" / "skills",
            self.ws / ".codex" / "skills",
            self.ws / ".gemini" / "skills",
            self.ws / ".github" / "skills",
        ]
        for path in expected:
            path.mkdir(parents=True)
        roots = {path.resolve() for path in default_skill_roots(self.ws)}
        for path in expected:
            self.assertIn(path.resolve(), roots)
        self.assertIn(self.ws / ".agents" / "skills", host_skill_roots(self.ws))

    def test_installed_roots_are_included(self) -> None:
        skills = self.ws / ".caprouter" / "installed" / "skills"
        plugins = self.ws / ".caprouter" / "installed" / "plugins"
        skills.mkdir(parents=True)
        plugins.mkdir(parents=True)
        self.assertIn(skills.resolve(), {path.resolve() for path in default_skill_roots(self.ws)})
        self.assertIn(plugins.resolve(), {path.resolve() for path in default_plugin_roots(self.ws)})

    def test_create_workspace_persists_extra_plugin_root(self) -> None:
        plugin_root = Path(self.tmp.name) / "plugins"
        plugin_root.mkdir()
        create_workspace(
            self.ws,
            include_agents=False,
            include_default_roots=False,
            extra_plugin_roots=[plugin_root],
        )
        config = json.loads((self.ws / ".caprouter" / "config.json").read_text(encoding="utf-8"))
        self.assertIn(str(plugin_root.resolve()), config["plugin_roots"])
        self.assertEqual(config["skill_roots"], [])


if __name__ == "__main__":
    unittest.main()
