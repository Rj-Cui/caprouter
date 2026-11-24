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

from caprouter.adapters import MockAdapter
from caprouter.executor import ExecuteOptions, execute_plan


PLAN = {
    "task_id": "executor-test",
    "goal": "generate and verify a sentence",
    "quality_gate": {"max_attempts": 1},
    "nodes": [
        {
            "id": "n1",
            "title": "generate",
            "capability": "generate",
            "brief": "write a sentence",
            "output_contract": {"type": "text"},
            "acceptance": ["sentence exists"],
        },
        {
            "id": "n2",
            "title": "verify",
            "capability": "verify",
            "brief": "verify n1",
            "inputs": ["n1"],
            "output_contract": {"type": "claim_ledger"},
            "acceptance": ["n1 is supported"],
        },
    ],
}


class ExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)
        self.config = {"delivery": str(self.workspace / "deliverables")}
        MockAdapter.verify_calls = 0

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_execute_and_verify(self) -> None:
        summary = execute_plan(
            PLAN,
            {"entries": []},
            self.workspace,
            config=self.config,
            options=ExecuteOptions(adapter="mock"),
        )
        self.assertEqual(summary["status"], "completed")
        self.assertTrue(Path(summary["final_path"]).exists())
        run_dir = Path(summary["run_dir"])
        self.assertTrue((run_dir / "agents" / "n1" / "attempt-1" / "prompt.txt").exists())
        self.assertTrue((run_dir / "agents" / "n2" / "attempt-1" / "stdout.txt").exists())
        self.assertEqual(summary["nodes"][1]["verdict"], True)

    def test_rework_when_verifier_fails_once(self) -> None:
        with patch.dict(os.environ, {"CAPROUTER_MOCK_VERIFY_FAIL": "1"}):
            summary = execute_plan(
                PLAN,
                {"entries": []},
                self.workspace,
                config=self.config,
                options=ExecuteOptions(adapter="mock", max_rework=1),
            )
        self.assertEqual(summary["status"], "completed")
        verify = summary["nodes"][1]
        self.assertEqual(verify["rework"], 1)
        self.assertTrue(verify["verdict"])
        run_dir = Path(summary["run_dir"])
        self.assertTrue((run_dir / "agents" / "n1" / "attempt-2").exists())


if __name__ == "__main__":
    unittest.main()
