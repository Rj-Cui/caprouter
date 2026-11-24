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

from caprouter.state import is_enabled, read_state, state_path, write_state


class StateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "state.json"
        self.env = patch.dict(os.environ, {"CAPROUTER_STATE": str(self.path)})
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def test_default_is_enabled(self) -> None:
        self.assertEqual(state_path(), self.path)
        self.assertTrue(is_enabled())

    def test_disable_and_enable(self) -> None:
        data = write_state(False, "test")
        self.assertFalse(data["enabled"])
        self.assertFalse(is_enabled())
        self.assertEqual(read_state()["updated_by"], "test")

        write_state(True, "test")
        self.assertTrue(is_enabled())

    def test_file_is_valid_json(self) -> None:
        write_state(False, "test")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["schema"], "caprouter.state.v1")
        self.assertFalse(data["enabled"])


if __name__ == "__main__":
    unittest.main()
