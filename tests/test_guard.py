from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from caprouter.guard import decide


class GuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)
        config_dir = self.workspace / ".caprouter"
        config_dir.mkdir()
        (config_dir / "config.json").write_text(
            json.dumps({
                "schema": "caprouter.config.v1",
                "workspace": str(self.workspace),
                "delivery": str(self.workspace / "deliverables"),
                "guard": {"allow_native_subagents": False, "allow_web": False},
            }),
            encoding="utf-8",
        )
        (config_dir / "plans").mkdir()
        self.state_file = self.workspace / "state.json"
        self.state_file.write_text(
            json.dumps({"schema": "caprouter.state.v1", "enabled": True}),
            encoding="utf-8",
        )
        self.state_patch = patch.dict(os.environ, {"CAPROUTER_STATE": str(self.state_file)})
        self.state_patch.start()

    def tearDown(self) -> None:
        self.state_patch.stop()
        self.tmp.cleanup()

    def payload(self, tool: str, **tool_input) -> dict:
        return {"tool_name": tool, "tool_input": tool_input, "cwd": str(self.workspace)}

    def test_denies_direct_web(self) -> None:
        self.assertEqual(decide(self.payload("WebSearch", query="x"))["decision"], "deny")

    def test_denies_native_subagent(self) -> None:
        self.assertEqual(decide(self.payload("Task", prompt="x"))["decision"], "deny")
        self.assertEqual(decide(self.payload("Agent", prompt="x"))["decision"], "deny")

    def test_denies_arbitrary_bash(self) -> None:
        self.assertEqual(decide(self.payload("Bash", command="curl https://example.com"))["decision"], "deny")

    def test_allows_caprouter_bash(self) -> None:
        command = f'python "{ROOT / "bin" / "caprouter.py"}" route --need "网页设计"'
        self.assertEqual(decide(self.payload("Bash", command=command))["decision"], "allow")

    def test_denies_compound_with_disallowed_part(self) -> None:
        command = 'caprouter route --need "x" && curl https://example.com'
        self.assertEqual(decide(self.payload("Bash", command=command))["decision"], "deny")

    def test_write_limited_to_plans(self) -> None:
        allowed = self.payload("Write", file_path=".caprouter/plans/task.json", content="{}")
        denied = self.payload("Write", file_path="src/main.py", content="x")
        self.assertEqual(decide(allowed)["decision"], "allow")
        self.assertEqual(decide(denied)["decision"], "deny")

    def test_read_is_allowed(self) -> None:
        self.assertEqual(decide(self.payload("Read", file_path="README.md"))["decision"], "allow")

    def test_bus_mode_allows_host_tools(self) -> None:
        config_path = self.workspace / ".caprouter" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["mode"] = "bus"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        self.assertEqual(decide(self.payload("WebSearch", query="x"))["decision"], "allow")
        self.assertEqual(decide(self.payload("Task", prompt="x"))["decision"], "allow")
        self.assertEqual(decide(self.payload("Bash", command="curl x"))["decision"], "allow")

    def test_disabled_allows_everything(self) -> None:
        self.state_file.write_text(
            json.dumps({"schema": "caprouter.state.v1", "enabled": False}),
            encoding="utf-8",
        )
        self.assertEqual(decide(self.payload("WebSearch", query="x"))["decision"], "allow")
        self.assertEqual(decide(self.payload("Task", prompt="x"))["decision"], "allow")
        self.assertEqual(decide(self.payload("Bash", command="curl x"))["decision"], "allow")

    def test_no_config_allows(self) -> None:
        with tempfile.TemporaryDirectory() as other:
            payload = {"tool_name": "Bash", "tool_input": {"command": "curl x"}, "cwd": other}
            self.assertEqual(decide(payload)["decision"], "allow")

    def test_denies_python_c_even_with_caprouter_comment(self) -> None:
        command = 'python -c "import os; os.system(\'echo x\')" # caprouter.py'
        self.assertEqual(decide(self.payload("Bash", command=command))["decision"], "deny")

    def test_denies_python_with_fake_script_name_argument(self) -> None:
        command = "python evil.py caprouter.py"
        self.assertEqual(decide(self.payload("Bash", command=command))["decision"], "deny")

    def test_denies_output_redirection(self) -> None:
        command = "caprouter route --need x > notes.txt"
        self.assertEqual(decide(self.payload("Bash", command=command))["decision"], "deny")

    def test_denies_single_ampersand(self) -> None:
        command = "caprouter status & curl https://example.com"
        self.assertEqual(decide(self.payload("Bash", command=command))["decision"], "deny")

    def test_denies_command_substitution(self) -> None:
        command = "caprouter status $(curl https://example.com)"
        self.assertEqual(decide(self.payload("Bash", command=command))["decision"], "deny")

    def test_denies_uv_run_with_fake_caprouter_argument(self) -> None:
        command = "uv run evil.py caprouter"
        self.assertEqual(decide(self.payload("Bash", command=command))["decision"], "deny")

    def test_denies_module_prefix_bypass(self) -> None:
        command = "python -m caprouter.evil"
        self.assertEqual(decide(self.payload("Bash", command=command))["decision"], "deny")

    def test_allows_python_m_caprouter(self) -> None:
        command = "python -m caprouter status"
        self.assertEqual(decide(self.payload("Bash", command=command))["decision"], "allow")

    def test_allows_quoted_argument_with_redirect_char(self) -> None:
        command = f'python "{ROOT / "bin" / "caprouter.py"}" route --need "a > b"'
        self.assertEqual(decide(self.payload("Bash", command=command))["decision"], "allow")


if __name__ == "__main__":
    unittest.main()
