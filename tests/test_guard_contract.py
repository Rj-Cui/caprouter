"""PreToolUse guard: write roots, executable trust and fail-closed behaviour."""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for _path in (ROOT, HERE):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from support import enter_isolated_env, make_workspace, read_config, write_config  # noqa: E402

from caprouter import guard  # noqa: E402


class GuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="caprouter-guard-"))
        enter_isolated_env(self, self.tmp / "home")
        self.workspace = make_workspace(self.tmp)
        config = read_config(self.workspace)
        config["mode"] = "orchestrator"
        config["guard"] = {
            "allow_native_subagents": False,
            "allow_web": False,
            "write_allow": [".caprouter/plans", ".caprouter/scratch"],
        }
        write_config(self.workspace, config)

    def decide(self, tool: str, payload: dict) -> dict:
        return guard.decide(
            {"tool_name": tool, "tool_input": payload, "cwd": str(self.workspace)},
            plugin_root=ROOT,
        )

    # ---- write roots -----------------------------------------------------
    def test_write_inside_a_configured_root_is_allowed(self) -> None:
        result = self.decide("Write", {"file_path": str(self.workspace / ".caprouter" / "plans" / "p.md")})
        self.assertEqual(result["decision"], "allow")

    def test_write_outside_the_roots_is_denied(self) -> None:
        result = self.decide("Write", {"file_path": str(self.workspace / "src" / "x.py")})
        self.assertEqual(result["decision"], "deny")

    def test_write_allow_is_honoured_from_config(self) -> None:
        config = read_config(self.workspace)
        config["guard"]["write_allow"] = [".caprouter/plans"]
        write_config(self.workspace, config)
        allowed = self.decide("Write", {"file_path": str(self.workspace / ".caprouter" / "plans" / "a.md")})
        denied = self.decide("Write", {"file_path": str(self.workspace / ".caprouter" / "scratch" / "a.md")})
        self.assertEqual(allowed["decision"], "allow")
        # Regression: write_allow was written by create_workspace but never read,
        # so scratch/ was always allowed no matter what the config said.
        self.assertEqual(denied["decision"], "deny")

    def test_write_allow_outside_the_workspace_is_ignored(self) -> None:
        outside = self.tmp / "outside"
        outside.mkdir()
        config = read_config(self.workspace)
        config["guard"]["write_allow"] = [str(outside)]
        write_config(self.workspace, config)
        result = self.decide("Write", {"file_path": str(outside / "x.txt")})
        self.assertEqual(result["decision"], "deny")

    # ---- executable trust ------------------------------------------------
    def test_caprouter_shim_inside_the_writable_area_is_rejected(self) -> None:
        plans = self.workspace / ".caprouter" / "plans"
        plans.mkdir(parents=True, exist_ok=True)
        shim = plans / "caprouter.cmd"
        shim.write_text("@echo off\n", encoding="utf-8")
        result = self.decide("Bash", {"command": f'"{shim}" --version'})
        self.assertEqual(result["decision"], "deny")

    def test_shell_is_limited_to_caprouter_commands(self) -> None:
        self.assertEqual(self.decide("Bash", {"command": "curl http://example.com"})["decision"], "deny")
        self.assertEqual(self.decide("Bash", {"command": "rm -rf /"})["decision"], "deny")

    def test_unsafe_shell_syntax_is_rejected(self) -> None:
        for command in ("caprouter doctor > out.txt", "caprouter $(whoami)", "caprouter `whoami`"):
            with self.subTest(command=command):
                self.assertEqual(self.decide("Bash", {"command": command})["decision"], "deny")

    def test_read_only_tools_are_not_gated(self) -> None:
        self.assertEqual(self.decide("Read", {"file_path": str(self.tmp / "x")})["decision"], "allow")

    # ---- bus mode --------------------------------------------------------
    def test_bus_mode_allows_the_host_unless_enforced(self) -> None:
        config = read_config(self.workspace)
        config["mode"] = "bus"
        write_config(self.workspace, config)
        self.assertEqual(self.decide("Bash", {"command": "rm -rf /"})["decision"], "allow")

        config["guard"]["enforce_in_bus_mode"] = True
        write_config(self.workspace, config)
        # Opt-in escape hatch: the host-wide guard can be kept active in bus mode.
        self.assertEqual(self.decide("Bash", {"command": "rm -rf /"})["decision"], "deny")

    def test_disabled_state_allows_everything(self) -> None:
        from caprouter.state import write_state

        write_state(False, updated_by="test")
        self.assertEqual(self.decide("Bash", {"command": "rm -rf /"})["decision"], "allow")


class GuardFailClosedTests(unittest.TestCase):
    def test_guard_failure_denies_execution_capable_tools(self) -> None:
        payload = json.dumps(
            {"tool_name": "Bash", "tool_input": {"command": "caprouter --version"}, "cwd": "."}
        )
        buffer = io.StringIO()
        with mock.patch.object(guard, "decide", side_effect=RuntimeError("boom")), \
                mock.patch.object(sys, "stdin", io.StringIO(payload)), \
                mock.patch.object(guard, "append_audit", lambda event: None), \
                redirect_stdout(buffer):
            code = guard.main()
        self.assertEqual(code, 0)
        output = buffer.getvalue().strip()
        self.assertTrue(output, "a denial must be reported to the host")
        parsed = json.loads(output)
        self.assertEqual(
            parsed["hookSpecificOutput"]["permissionDecision"], "deny"
        )

    def test_guard_failure_does_not_block_reads(self) -> None:
        payload = json.dumps({"tool_name": "Read", "tool_input": {}, "cwd": "."})
        buffer = io.StringIO()
        with mock.patch.object(guard, "decide", side_effect=RuntimeError("boom")), \
                mock.patch.object(sys, "stdin", io.StringIO(payload)), \
                mock.patch.object(guard, "append_audit", lambda event: None), \
                redirect_stdout(buffer):
            code = guard.main()
        self.assertEqual(code, 0)
        self.assertEqual(buffer.getvalue().strip(), "")


if __name__ == "__main__":
    unittest.main()
