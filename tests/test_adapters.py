from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from caprouter.adapters import MockAdapter, parse_output


class AdapterTests(unittest.TestCase):
    def test_codebuddy_events(self) -> None:
        events = [
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "OK"}]},
            {"type": "result", "result": "OK", "is_error": False, "num_turns": 1, "usage": {"input_tokens": 3}},
        ]
        parsed = parse_output(json.dumps(events))
        self.assertEqual(parsed["text"], "OK")
        self.assertEqual(parsed["meta"]["num_turns"], 1)

    def test_qwen_events(self) -> None:
        events = [
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "1+1=2"}]}},
            {"type": "result", "result": "1+1=2", "is_error": False},
        ]
        parsed = parse_output(json.dumps(events))
        self.assertEqual(parsed["text"], "1+1=2")

    def test_plain_text_fallback(self) -> None:
        parsed = parse_output("plain response")
        self.assertEqual(parsed["text"], "plain response")
        self.assertEqual(parsed["format"], "text")

    def test_mock_verifier_detected(self) -> None:
        adapter = MockAdapter()
        result = adapter.run("verify the following draft", max_turns=8, system_prompt=None)
        payload = json.loads(result.parsed["text"])
        self.assertTrue(payload["ok"])


if __name__ == "__main__":
    unittest.main()
