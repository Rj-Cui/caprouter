from __future__ import annotations

import json
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent


class McpServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)
        bus = self.ws / ".caprouter"
        bus.mkdir(parents=True)
        skill = self.ws / "skills" / "writer"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: writer\ndescription: 写报告与生成文案\n---\nbody\n",
            encoding="utf-8",
        )
        (bus / "registry.json").write_text(
            json.dumps({"entries": [{
                "id": "skill:writer", "type": "skill", "name": "writer",
                "path": str(skill), "description": "写报告与生成文案",
                "keywords": ["写", "报告"], "capabilities": ["generate"],
                "domain": "writing", "status": "verified",
            }]}, ensure_ascii=False),
            encoding="utf-8",
        )
        (bus / "config.json").write_text(
            json.dumps({"schema": "caprouter.config.v1", "routing": {}}, ensure_ascii=False),
            encoding="utf-8",
        )
        self.proc = subprocess.Popen(
            [sys.executable, str(ROOT / "bin" / "caprouter.py"), "mcp", "--workspace", str(self.ws)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self.queue: queue.Queue[str] = queue.Queue()
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self.queue.put(line)

    def send(self, payload: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def recv(self, timeout: float = 10.0) -> dict:
        return json.loads(self.queue.get(timeout=timeout))

    def tearDown(self) -> None:
        if self.proc.poll() is None:
            if self.proc.stdin:
                self.proc.stdin.close()
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        for stream in (self.proc.stdout, self.proc.stderr):
            if stream:
                try:
                    stream.close()
                except OSError:
                    pass
        self.tmp.cleanup()

    def initialize(self) -> dict:
        self.send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                   "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                              "clientInfo": {"name": "test", "version": "0"}}})
        return self.recv()

    def test_initialize_and_tools_list(self) -> None:
        response = self.initialize()
        self.assertEqual(response["result"]["serverInfo"]["name"], "caprouter")
        self.assertIn("tools", response["result"]["capabilities"])
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        self.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        response = self.recv()
        names = [tool["name"] for tool in response["result"]["tools"]]
        self.assertIn("capability_search", names)
        self.assertIn("capability_verify", names)
        self.assertIn("capability_probe", names)
        self.assertIn("capability_probe_record", names)
        self.assertIn("capability_card_draft", names)
        self.assertIn("capability_discover", names)

    def test_search_and_verify(self) -> None:
        self.initialize()
        self.send({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
            "name": "capability_search", "arguments": {"task": "写一份报告", "top": 3}}})
        response = self.recv()
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(payload["decision"], "select")
        self.assertEqual(payload["selected"]["id"], "skill:writer")

        self.send({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {
            "name": "capability_verify",
            "arguments": {"text": "1. 甲\n2. 乙\n3. 丙", "acceptance": [{"type": "exact_items", "value": 3, "pattern": "list"}]}}})
        response = self.recv()
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(payload["verdict"], "pass")

    def test_probe_and_record_tools(self) -> None:
        self.initialize()
        self.send({"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {
            "name": "capability_probe", "arguments": {"id": "skill:writer"}}})
        response = self.recv()
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(payload["status"], "pass")
        self.assertEqual(payload["evidence"], "static")

        self.send({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {
            "name": "capability_probe_record",
            "arguments": {"id": "skill:writer", "task": "写一份报告", "status": "pass", "duration_ms": 10}}})
        response = self.recv()
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(payload["status"], "pass")
        self.assertEqual(payload["evidence"], "behavioral")

    def test_card_draft_tool(self) -> None:
        self.initialize()
        self.send({"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {
            "name": "capability_card_draft", "arguments": {"id": "skill:writer"}}})
        response = self.recv()
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(payload["id"], "skill:writer")
        self.assertEqual(payload["schema"], "caprouter.capability-card-draft.v1")

    def test_discover_tool_requires_network(self) -> None:
        self.initialize()
        self.send({"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {
            "name": "capability_discover", "arguments": {"query": "pdf"}}})
        response = self.recv()
        self.assertTrue(response["result"]["isError"])
        self.assertIn("allow-network", response["result"]["content"][0]["text"])

    def test_dispatch_discover_with_mock(self) -> None:
        from caprouter.mcp_server import _dispatch

        fake = {
            "skills": [
                {
                    "id": "anthropics/skills/pdf",
                    "source": "anthropics/skills",
                    "skillId": "pdf",
                    "name": "pdf",
                    "installs": 1,
                }
            ]
        }
        with patch("caprouter.remote._http_json", return_value=fake):
            result = _dispatch(
                self.ws, "capability_discover", {"query": "pdf", "allow_network": True}
            )
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["candidates"][0]["id"], "anthropics/skills/pdf")

    def test_unknown_tool_is_error(self) -> None:
        self.initialize()
        self.send({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {
            "name": "no_such_tool", "arguments": {}}})
        response = self.recv()
        self.assertTrue(response["result"]["isError"])

    def test_resolve_workspace_plugin_fallback(self) -> None:
        from caprouter.mcp_server import resolve_workspace

        plugin_workspace = ROOT / "workspace"
        if not (plugin_workspace / ".caprouter" / "config.json").is_file():
            self.skipTest("plugin workspace is not initialized")
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(Path, "cwd", return_value=Path(tmp)):
                resolved = resolve_workspace(None)
        self.assertEqual(resolved, plugin_workspace.resolve())


if __name__ == "__main__":
    unittest.main()
