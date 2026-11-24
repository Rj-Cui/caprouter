from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from caprouter.eval import evaluate


ENTRIES = [
    {
        "id": "skill:web-research",
        "type": "skill",
        "name": "web-research",
        "description": "调研、检索公开来源与资料",
        "keywords": ["调研", "检索", "来源"],
        "capabilities": ["retrieve"],
        "domain": "research",
        "status": "verified",
    },
    {
        "id": "skill:poster-design",
        "type": "skill",
        "name": "poster-design",
        "description": "设计海报与视觉方案",
        "keywords": ["海报", "设计"],
        "capabilities": ["design"],
        "domain": "design",
        "status": "verified",
    },
]


class EvalTests(unittest.TestCase):
    def test_hit_and_none(self) -> None:
        cases = [
            {
                "id": "research",
                "need": "调研市场并检索来源",
                "capability": "retrieve",
                "domain": "research",
                "expect_any": ["skill:web-research"],
            },
            {
                "id": "none",
                "need": "撰写一份市场报告",
                "capability": "generate",
                "domain": "research",
                "expect_none": True,
            },
        ]
        result = evaluate(ENTRIES, cases, {"min_score": 0.0}, top_k=3)
        self.assertEqual(result["cases"], 2)
        self.assertEqual(result["top1"], 1.0)
        self.assertEqual(result["topk"], 1.0)
        self.assertGreater(result["context"]["reduction"], 0)
        self.assertEqual(result["details"][1]["got"], [])
        self.assertIn("latency_ms", result)
        self.assertGreaterEqual(result["latency_ms"]["avg"], 0)

    def test_miss_is_reported(self) -> None:
        cases = [
            {
                "id": "miss",
                "need": "设计一张海报",
                "capability": "retrieve",
                "domain": "design",
                "expect_any": ["skill:poster-design"],
            }
        ]
        result = evaluate(ENTRIES, cases, {"min_score": 0.0}, top_k=3)
        self.assertFalse(result["details"][0]["hit_at_k"])


if __name__ == "__main__":
    unittest.main()
