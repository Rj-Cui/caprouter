from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from caprouter.host import run_host


class HostTests(unittest.TestCase):
    def test_host_prompt_runs_through_executor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            summary = run_host(
                "用一句话回答问题",
                {"entries": []},
                workspace,
                config={"delivery": str(workspace / "deliverables")},
                adapter="mock",
            )
            self.assertEqual(summary["status"], "completed")
            self.assertTrue(Path(summary["final_path"]).exists())
            self.assertIn("MOCK", Path(summary["final_path"]).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
