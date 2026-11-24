from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from caprouter.verifier import build_verify_prompt, parse_verdict


class VerifierTests(unittest.TestCase):
    def test_parse_fenced_json(self) -> None:
        verdict = parse_verdict('```json\n{"ok": false, "issues": [{"problem": "x"}], "claims": []}\n```')
        self.assertFalse(verdict["ok"])
        self.assertEqual(len(verdict["issues"]), 1)

    def test_parse_plain_json(self) -> None:
        verdict = parse_verdict('{"ok": true, "issues": [], "claims": []}')
        self.assertTrue(verdict["ok"])

    def test_parse_fallback(self) -> None:
        verdict = parse_verdict("FAIL: missing source")
        self.assertFalse(verdict["ok"])
        self.assertFalse(verdict["parsed"])

    def test_prompt_contains_criteria_and_draft(self) -> None:
        prompt = build_verify_prompt("draft text", ["criterion one"], "source pack")
        self.assertIn("criterion one", prompt)
        self.assertIn("draft text", prompt)
        self.assertIn("source pack", prompt)


if __name__ == "__main__":
    unittest.main()
