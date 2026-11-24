from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from caprouter.route import route, tokenize


ENTRIES = [
    {
        "id": "skill:demo-frontend",
        "type": "skill",
        "name": "demo-frontend",
        "path": "D:/skills/frontend",
        "description": "网页 UI 设计、响应式布局、可访问性检查",
        "keywords": ["网页", "设计", "ui", "响应式"],
        "capabilities": ["design", "verify"],
        "status": "verified",
    },
    {
        "id": "skill:market-research",
        "type": "skill",
        "name": "market-research",
        "path": "D:/skills/research",
        "description": "市场调研、竞品分析与来源整理",
        "keywords": ["调研", "市场", "竞品"],
        "capabilities": ["retrieve", "analyze"],
        "status": "verified",
    },
    {
        "id": "cli-agent:codebuddy",
        "type": "cli-agent",
        "name": "codebuddy",
        "path": "C:/bin/codebuddy",
        "description": "Local CLI agent for generation and verification",
        "keywords": ["codebuddy"],
        "capabilities": ["generate", "verify"],
        "status": "verified",
    },
]


class RouteTests(unittest.TestCase):
    def test_cjk_tokenization(self) -> None:
        tokens = tokenize("网页设计")
        self.assertIn("网页", tokens)
        self.assertIn("页设", tokens)
        self.assertIn("设计", tokens)

    def test_routes_design_task_to_ui_skill(self) -> None:
        result = route(ENTRIES, "润色网页并做响应式检查", top_k=3)
        self.assertGreater(result["count"], 0)
        self.assertEqual(result["results"][0]["id"], "skill:demo-frontend")

    def test_routes_research_task_to_research_skill(self) -> None:
        result = route(ENTRIES, "调研西幻网文市场并整理竞品", top_k=3)
        self.assertEqual(result["results"][0]["id"], "skill:market-research")

    def test_type_filter(self) -> None:
        result = route(ENTRIES, "生成并检查一段代码", top_k=5, types=["cli-agent"])
        self.assertTrue(all(item["type"] == "cli-agent" for item in result["results"]))

    def test_prompt_alias_routes_to_prompt_tool(self) -> None:
        entries = [
            {
                "id": "project:prompt-optimizer",
                "type": "project",
                "name": "prompt-optimizer",
                "path": "D:/projects/prompt-optimizer",
                "description": "AI prompt optimization and comparison tool",
                "keywords": ["prompt", "optimization"],
                "capabilities": ["generate", "transform"],
                "status": "scanned",
            },
            {
                "id": "skill:market-research",
                "type": "skill",
                "name": "market-research",
                "path": "D:/skills/research",
                "description": "市场调研",
                "keywords": ["调研"],
                "capabilities": ["retrieve"],
                "status": "verified",
            },
        ]
        result = route(entries, "优化提示词", top_k=2)
        self.assertEqual(result["results"][0]["id"], "project:prompt-optimizer")

    def test_no_match_returns_empty(self) -> None:
        result = route(ENTRIES, "zzzz-nonexistent-token", top_k=3)
        self.assertEqual(result["count"], 0)


if __name__ == "__main__":
    unittest.main()
