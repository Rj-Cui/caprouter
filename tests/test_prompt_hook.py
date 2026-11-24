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

from caprouter.prompt_hook import decide, should_inject


class PromptHookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "state.json"
        self.state.write_text(json.dumps({"enabled": True}), encoding="utf-8")
        self.env = patch.dict(os.environ, {"CAPROUTER_STATE": str(self.state)})
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def test_injects_for_multistep_task(self) -> None:
        self.assertTrue(should_inject("调研西幻网文市场并写一份报告"))

    def test_skips_short_or_slash_prompts(self) -> None:
        self.assertFalse(should_inject("你好"))
        self.assertFalse(should_inject("/help"))
        self.assertFalse(should_inject("调研并写报告", enabled=False))

    def test_decide_returns_context(self) -> None:
        result = decide({"prompt": "调研西幻网文市场并写一份报告", "cwd": str(self.tmp.name)})
        self.assertIsNotNone(result)
        output = result["hookSpecificOutput"]
        self.assertEqual(output["hookEventName"], "UserPromptSubmit")
        self.assertIn("caprouter", output["additionalContext"].lower())

    def test_decide_skips_trivial(self) -> None:
        self.assertIsNone(decide({"prompt": "你好", "cwd": str(self.tmp.name)}))

    def test_bus_mode_injects_bus_context(self) -> None:
        workspace = Path(self.tmp.name)
        (workspace / ".caprouter").mkdir()
        (workspace / ".caprouter" / "config.json").write_text(
            json.dumps({"schema": "caprouter.config.v1", "mode": "bus"}),
            encoding="utf-8",
        )
        result = decide({"prompt": "调研西幻网文市场并写一份报告", "cwd": str(workspace)})
        self.assertIsNotNone(result)
        context = result["hookSpecificOutput"]["additionalContext"]
        self.assertIn("capability_search", context)
        self.assertNotIn("caprouter run", context)


if __name__ == "__main__":
    unittest.main()
