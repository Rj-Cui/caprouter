from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from caprouter.mcp_inventory import (
    candidate_configs,
    inventory_summary,
    read_config,
    scan_mcp_servers,
)


class McpInventoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write(self, name: str, data: object) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_scans_stdio_http_and_env_without_leaking_secrets(self) -> None:
        path = self._write(
            "config.json",
            {
                "mcpServers": {
                    "demo": {
                        "command": "python",
                        "args": ["-m", "demo", "--token", "supersecret"],
                    },
                    "web": {
                        "type": "http",
                        "url": "https://example.com/mcp?key=secret123",
                    },
                    "envs": {
                        "command": "node",
                        "args": ["server.js"],
                        "env": {"API_KEY": "sk-abcdefghijk", "HOME": "/tmp"},
                    },
                }
            },
        )
        entries = {entry["name"]: entry for entry in scan_mcp_servers(include_defaults=False, extra_configs=[("test", path)])}
        self.assertEqual(set(entries), {"demo", "web", "envs"})
        self.assertEqual(entries["demo"]["transport"], "stdio")
        self.assertIn("***", entries["demo"]["invocation"])
        self.assertNotIn("supersecret", entries["demo"]["invocation"])
        self.assertEqual(entries["web"]["transport"], "http")
        self.assertNotIn("secret123", entries["web"]["invocation"])
        self.assertEqual(entries["envs"]["env_keys"], ["API_KEY", "HOME"])
        dump = json.dumps(entries, ensure_ascii=False)
        self.assertNotIn("sk-abcdefghijk", dump)
        self.assertNotIn("supersecret", dump)
        self.assertTrue(all(entry["redacted"] for entry in entries.values()))

    def test_codex_toml_config(self) -> None:
        path = self.root / "config.toml"
        path.write_text(
            '[mcp_servers.toml_demo]\ncommand = "uvx"\nargs = ["demo", "--token", "hidden"]\n',
            encoding="utf-8",
        )
        data = read_config(path)
        self.assertIsInstance(data, dict)
        entries = scan_mcp_servers(include_defaults=False, extra_configs=[("codex", path)])
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["name"], "toml_demo")
        self.assertNotIn("hidden", entries[0]["invocation"])

    def test_claude_projects_scope(self) -> None:
        path = self._write(
            "claude.json",
            {
                "mcpServers": {},
                "projects": {
                    "/tmp/project": {
                        "mcpServers": {"proj": {"command": "node", "args": ["x.js"]}}
                    }
                },
            },
        )
        entries = scan_mcp_servers(include_defaults=False, extra_configs=[("claude-code", path)])
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["scope"], "/tmp/project")

    def test_same_server_across_hosts_is_merged(self) -> None:
        a = self._write("a.json", {"mcpServers": {"shared": {"command": "node", "args": ["s.js"]}}})
        b = self._write("b.json", {"mcpServers": {"shared": {"command": "node", "args": ["s.js"]}}})
        entries = scan_mcp_servers(include_defaults=False, extra_configs=[("a", a), ("b", b)])
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["hosts"], ["a", "b"])
        self.assertEqual(len(entries[0]["paths"]), 2)

    def test_same_name_different_command_gets_unique_id(self) -> None:
        a = self._write("a.json", {"mcpServers": {"same": {"command": "node", "args": ["a.js"]}}})
        b = self._write("b.json", {"mcpServers": {"same": {"command": "python", "args": ["b.py"]}}})
        entries = scan_mcp_servers(include_defaults=False, extra_configs=[("a", a), ("b", b)])
        self.assertEqual(len(entries), 2)
        self.assertEqual(len({entry["id"] for entry in entries}), 2)

    def test_workspace_config_is_discovered(self) -> None:
        ws = self.root / "ws"
        ws.mkdir()
        (ws / ".mcp.json").write_text(
            json.dumps({"mcpServers": {"local": {"command": "node", "args": ["l.js"]}}}),
            encoding="utf-8",
        )
        hosts = {host for host, path in candidate_configs(ws)}
        self.assertIn("workspace", hosts)
        entries = scan_mcp_servers(
            include_defaults=False,
            extra_configs=[("workspace", ws / ".mcp.json")],
        )
        self.assertTrue(any(entry["name"] == "local" for entry in entries))

    def test_inventory_summary_counts(self) -> None:
        path = self._write("config.json", {"mcpServers": {"one": {"command": "a"}}})
        summary = inventory_summary(include_defaults=False, extra_configs=[("test", path)])
        self.assertEqual(summary["count"], 1)
        self.assertEqual(summary["schema"], "caprouter.mcp-inventory.v1")


if __name__ == "__main__":
    unittest.main()
