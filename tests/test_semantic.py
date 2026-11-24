from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from caprouter.adapters import AdapterResult, CodeBuddyAdapter, KimiAdapter, QwenAdapter
from caprouter.semantic import (
    _extract_json,
    build_prompt,
    load_rubric,
    normalize_result,
    run_semantic_review,
)


class SemanticTests(unittest.TestCase):
    def test_builtin_rubric_without_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rubric = load_rubric(Path(tmp), "")
        self.assertTrue(rubric["rubric"])
        self.assertEqual(rubric["source"], "builtin")

    def test_extract_json_from_fence(self) -> None:
        data = _extract_json('```json\n{"verdict":"pass","confidence":0.8}\n```')
        self.assertEqual(data["verdict"], "pass")

    def test_normalize_result_clamps_values(self) -> None:
        result = normalize_result(
            {
                "verdict": "PASS",
                "confidence": 2,
                "issues": [{"field": "examples", "severity": "high", "message": "x" * 800}],
                "suggested_fixes": ["fix it"],
            },
            adapter="qwen",
        )
        self.assertEqual(result["verdict"], "pass")
        self.assertEqual(result["confidence"], 1.0)
        self.assertLessEqual(len(result["issues"][0]["message"]), 500)

    def test_run_semantic_review_parses_adapter_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            draft = {"id": "project:x", "type": "project", "name": "x", "description": "A tool", "examples": []}
            evidence = {"artifacts": [], "signals": {}, "capability_hints": []}
            deterministic = {"status": "needs_review", "risk": "medium", "score": 0.6, "checks": []}
            config = {"semantic": {"adapter": "fake", "capability_id": "", "min_confidence": 0.75}}
            payload = '{"verdict":"pass","confidence":0.9,"issues":[],"suggested_fixes":[]}'
            with mock.patch(
                "caprouter.semantic.run_adapter",
                return_value=AdapterResult(
                    adapter="fake",
                    command=[],
                    returncode=0,
                    stdout=payload,
                    stderr="",
                    duration_ms=12,
                    timed_out=False,
                    parsed={"text": payload},
                ),
            ):
                result = run_semantic_review(workspace, draft, evidence, deterministic, config)
        self.assertEqual(result["verdict"], "pass")
        self.assertEqual(result["confidence"], 0.9)
        self.assertIsNone(result["error"])

    def test_prompt_contains_builtin_rubric(self) -> None:
        prompt = build_prompt(
            {"id": "project:x", "name": "x", "description": "d", "examples": []},
            {"artifacts": [], "signals": {}},
            {"status": "needs_review", "risk": "medium", "checks": []},
            load_rubric(Path("."), ""),
        )
        self.assertIn("concrete user requests", prompt)
        self.assertIn("untrusted data", prompt)

    def test_repair_request_and_normalization(self) -> None:
        prompt = build_prompt(
            {"id": "project:x", "name": "x", "description": "d", "examples": []},
            {"artifacts": [], "signals": {}},
            {"status": "needs_review", "risk": "medium", "checks": []},
            load_rubric(Path("."), ""),
            request_repair=True,
        )
        self.assertIn('"repaired"', prompt)
        result = normalize_result(
            {
                "verdict": "pass",
                "confidence": 0.8,
                "repaired": {
                    "description": "Convert PDFs to Word",
                    "examples": ["Convert this PDF to Word"],
                    "capabilities": ["transform"],
                    "keywords": ["pdf"],
                },
            },
            adapter="mock",
        )
        self.assertEqual(result["repaired"]["examples"], ["Convert this PDF to Word"])

    def test_tool_isolation_flags(self) -> None:
        codebuddy_cmd, _ = CodeBuddyAdapter().build(
            "x", max_turns=1, system_prompt=None, disable_tools=True
        )
        qwen_cmd, _ = QwenAdapter().build(
            "x", max_turns=1, system_prompt=None, disable_tools=True
        )
        self.assertIn("--tools", codebuddy_cmd)
        self.assertIn("", codebuddy_cmd)
        self.assertIn("--approval-mode", qwen_cmd)
        self.assertIn("plan", qwen_cmd)
        self.assertFalse(KimiAdapter.supports_tool_isolation)

    def test_semantic_cache_reuses_unchanged_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            draft = {"id": "project:x", "type": "project", "name": "x", "description": "A tool"}
            evidence = {"artifacts": [], "signals": {}, "capability_hints": []}
            deterministic = {"status": "needs_review", "risk": "medium", "score": 0.6, "checks": []}
            config = {"semantic": {"adapter": "fake", "capability_id": "", "cache": True}}
            payload = '{"verdict":"pass","confidence":0.9,"issues":[]}'
            with mock.patch(
                "caprouter.semantic.run_adapter",
                return_value=AdapterResult(
                    adapter="fake",
                    command=[],
                    returncode=0,
                    stdout=payload,
                    stderr="",
                    duration_ms=12,
                    timed_out=False,
                    parsed={"text": payload},
                ),
            ) as fake_run:
                first = run_semantic_review(workspace, draft, evidence, deterministic, config)
                second = run_semantic_review(workspace, draft, evidence, deterministic, config)
        self.assertFalse(first.get("cached"))
        self.assertTrue(second.get("cached"))
        self.assertEqual(fake_run.call_count, 1)


if __name__ == "__main__":
    unittest.main()
