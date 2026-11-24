from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from caprouter.mcp_server import _dispatch
from caprouter.workspace import create_workspace


class NewMcpToolsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.ws = base / "ws"
        create_workspace(self.ws, include_agents=False, include_default_roots=False)
        self.source = base / "source" / "demo"
        self.source.mkdir(parents=True)
        (self.source / "SKILL.md").write_text(
            "---\nname: demo\ndescription: Demo\n---\n# Demo\n", encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_install_preview_approve_and_uninstall(self) -> None:
        preview = _dispatch(self.ws, "capability_install", {"source": str(self.source)})
        self.assertEqual(preview["status"], "preview")
        source_hash = preview["source_hash"]

        bad = _dispatch(
            self.ws,
            "capability_install",
            {"source": str(self.source), "approve": True, "approve_hash": "sha256:wrong"},
        )
        self.assertEqual(bad["status"], "source_changed")

        installed = _dispatch(
            self.ws,
            "capability_install",
            {
                "source": str(self.source),
                "approve": True,
                "approve_hash": source_hash,
            },
        )
        self.assertEqual(installed["status"], "installed")

        versions = _dispatch(self.ws, "capability_versions", {"id": "skill:demo"})
        self.assertEqual(versions["current"]["version"], "0.0.0-local")

        preview_remove = _dispatch(self.ws, "capability_uninstall", {"id": "skill:demo"})
        self.assertEqual(preview_remove["status"], "preview")
        removed = _dispatch(
            self.ws, "capability_uninstall", {"id": "skill:demo", "yes": True}
        )
        self.assertEqual(removed["status"], "uninstalled")

    def test_health_duplicates_lock_drift(self) -> None:
        health = _dispatch(self.ws, "capability_health", {})
        self.assertEqual(health["schema"], "caprouter.health.v1")
        duplicates = _dispatch(self.ws, "capability_duplicates", {})
        self.assertEqual(duplicates["schema"], "caprouter.duplicates.v1")
        locked = _dispatch(self.ws, "capability_lock", {})
        self.assertTrue(locked["lock_path"].endswith("caprouter.lock.json"))
        drift = _dispatch(self.ws, "capability_drift", {})
        self.assertTrue(drift["clean"])


if __name__ == "__main__":
    unittest.main()
