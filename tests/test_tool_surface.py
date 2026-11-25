"""The tool surface a host receives must match what the bundled skill tells it to call."""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for _path in (ROOT, HERE):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from caprouter.mcp_server import CORE_TOOL_NAMES, TOOLS, tools_for  # noqa: E402
from caprouter.setup_hosts import server_entry  # noqa: E402

SKILL = ROOT / "skills" / "caprouter" / "SKILL.md"
PLUGIN_MCP = ROOT / ".mcp.json"


class ToolSurfaceTests(unittest.TestCase):
    def test_core_is_a_subset_of_full(self) -> None:
        full = {tool["name"] for tool in TOOLS}
        core = {tool["name"] for tool in tools_for("core")}
        self.assertTrue(core, "core profile must not be empty")
        self.assertTrue(core <= full, core - full)
        self.assertEqual(core, set(CORE_TOOL_NAMES))

    def test_full_profile_has_every_tool(self) -> None:
        self.assertEqual(len(tools_for("full")), len(TOOLS))
        self.assertEqual(len(TOOLS), 20)

    def test_unknown_profile_falls_back_to_core(self) -> None:
        self.assertEqual(
            {tool["name"] for tool in tools_for("nonsense")},
            {tool["name"] for tool in tools_for("core")},
        )

    def test_every_tool_the_skill_calls_is_exposed_by_core(self) -> None:
        text = SKILL.read_text(encoding="utf-8")
        called = set(re.findall(r"capability_[a-z_]+", text))
        missing = sorted(called - set(CORE_TOOL_NAMES))
        # Regression: the skill told the agent to call probe/probe_record/
        # record_outcome/list_gaps while `setup` only exposed eight other tools.
        self.assertEqual(missing, [])

    def test_plugin_mcp_config_requests_the_core_profile(self) -> None:
        data = json.loads(PLUGIN_MCP.read_text(encoding="utf-8"))
        args = data["mcpServers"]["caprouter"]["args"]
        self.assertIn("--tools", args)
        self.assertEqual(args[args.index("--tools") + 1], "core")

    def test_host_setup_writes_core_profile_and_host_id(self) -> None:
        entry = server_entry(Path("C:/ws"), host="claude-code")
        self.assertIn("--tools", entry["args"])
        self.assertEqual(entry["args"][entry["args"].index("--tools") + 1], "core")
        self.assertIn("--host", entry["args"])

    def test_skill_documents_the_real_core_count(self) -> None:
        text = SKILL.read_text(encoding="utf-8")
        self.assertIn("ten", text.lower())
        self.assertNotIn("eight core", text.lower())


if __name__ == "__main__":
    unittest.main()
