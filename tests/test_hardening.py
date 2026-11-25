"""Zip extraction limits, MCP argument validation and health/review agreement."""
from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for _path in (ROOT, HERE):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from support import enter_isolated_env, make_workspace  # noqa: E402

from caprouter import archive  # noqa: E402
from caprouter.health import _state  # noqa: E402
from caprouter.mcp_server import _handle  # noqa: E402


class ArchiveLimitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="caprouter-archive-"))
        self.zip_path = self.tmp / "payload.zip"
        with zipfile.ZipFile(self.zip_path, "w", zipfile.ZIP_DEFLATED) as archive_file:
            for index in range(3):
                archive_file.writestr(f"dir/file{index}.txt", "x" * 600)
        self.destination = self.tmp / "out"

    def test_declared_total_over_budget_is_rejected(self) -> None:
        with mock.patch.object(archive, "MAX_ZIP_BYTES", 512):
            with self.assertRaises(ValueError) as ctx:
                archive._safe_extract(self.zip_path, self.destination)
        self.assertIn("too many bytes", str(ctx.exception))

    def test_actual_written_total_over_budget_is_rejected(self) -> None:
        # A zip whose headers lie about the uncompressed size must still be
        # stopped by the bytes actually written: here the declared total is
        # forced to 0 while the archive really expands to ~1800 bytes.
        with mock.patch.object(archive, "_declared_total", lambda infos: 0), \
                mock.patch.object(archive, "MAX_ZIP_BYTES", 512):
            with self.assertRaises(ValueError) as ctx:
                archive._safe_extract(self.zip_path, self.destination)
        self.assertIn("actual", str(ctx.exception))

    def test_a_normal_archive_extracts(self) -> None:
        archive._safe_extract(self.zip_path, self.destination)
        self.assertEqual(len(list(self.destination.rglob("*.txt"))), 3)


class McpArgumentValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="caprouter-mcp-args-"))
        enter_isolated_env(self, self.tmp / "home")
        self.workspace = make_workspace(self.tmp)

    def _call(self, name: str, arguments: dict) -> dict:
        response = _handle(
            self.workspace,
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": name, "arguments": arguments}},
            host="unknown",
            profile="core",
        )
        return response["result"]

    def test_unknown_argument_is_rejected(self) -> None:
        # Regression: a typo such as ``top_k`` was silently dropped, so the tool
        # answered with the default instead of failing loudly.
        result = self._call("capability_search", {"task": "convert a pdf", "top_k": 3})
        self.assertTrue(result["isError"])
        text = result["content"][0]["text"]
        self.assertIn("unknown argument", text)
        self.assertIn("top_k", text)

    def test_known_arguments_still_work(self) -> None:
        result = self._call("capability_search", {"task": "convert a pdf", "top": 3})
        self.assertFalse(result["isError"], result["content"][0]["text"][:200])

    def test_unknown_tool_is_rejected(self) -> None:
        result = self._call("capability_nope", {})
        self.assertTrue(result["isError"])
        self.assertIn("unknown tool", result["content"][0]["text"])


class HealthReviewAgreementTests(unittest.TestCase):
    def test_review_failure_downgrades_active_to_probation(self) -> None:
        probe = {"status": "pass"}
        card = {"review": {"status": "needs_review"}}
        self.assertEqual(_state({}, probe, card), "probation")

    def test_passed_review_stays_active(self) -> None:
        probe = {"status": "pass"}
        for status in ("applied", "approved", "pass"):
            with self.subTest(review=status):
                self.assertEqual(_state({}, probe, {"review": {"status": status}}), "active")

    def test_failed_probe_still_degrades(self) -> None:
        self.assertEqual(_state({}, {"status": "fail"}, {"review": {"status": "applied"}}), "degraded")


if __name__ == "__main__":
    unittest.main()
