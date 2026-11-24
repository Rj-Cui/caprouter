from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from caprouter.setup_hosts import build_payload, default_config_path, setup_host


class SetupHostsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name) / "ws"
        self.ws.mkdir()
        self.config = Path(self.tmp.name) / "mcp.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_preview_cursor_payload(self) -> None:
        result = setup_host("cursor", self.ws, config_path=self.config)
        self.assertFalse(result["written"])
        self.assertFalse(self.config.exists())
        entry = result["payload"]["mcpServers"]["caprouter"]
        self.assertEqual(entry["command"], "python")
        self.assertIn("mcp", entry["args"])
        self.assertIn(str(self.ws.resolve()), entry["args"])
        self.assertIn("--tools", entry["args"])
        self.assertEqual(entry["args"][entry["args"].index("--host") + 1], "cursor")

    def test_write_merges_and_backs_up(self) -> None:
        self.config.write_text(
            json.dumps({"mcpServers": {"other": {"command": "other"}}}), encoding="utf-8"
        )
        result = setup_host("cursor", self.ws, config_path=self.config, write=True)
        self.assertTrue(result["written"])
        data = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertIn("other", data["mcpServers"])
        self.assertIn("caprouter", data["mcpServers"])
        self.assertTrue(Path(result["backup"]).is_file())

    def test_vscode_uses_servers_key(self) -> None:
        payload = build_payload("vscode", self.ws)
        self.assertIn("servers", payload)
        self.assertEqual(payload["servers"]["caprouter"]["type"], "stdio")

    def test_cherry_write_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            setup_host("cherry", self.ws, config_path=self.config, write=True)

    def test_claude_code_default_path_is_workspace_mcp_json(self) -> None:
        path = default_config_path("claude-code", self.ws)
        self.assertEqual(path, self.ws.resolve() / ".mcp.json")


if __name__ == "__main__":
    unittest.main()
