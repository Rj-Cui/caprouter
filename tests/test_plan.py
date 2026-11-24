from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from caprouter.plan import validate_plan


def plan_with(node: dict) -> dict:
    return {
        "task_id": "demo",
        "goal": "a long enough goal",
        "nodes": [{"id": "n1", **node}],
    }


class PlanTests(unittest.TestCase):
    def test_valid_plan(self) -> None:
        result = validate_plan(plan_with({
            "brief": "检索公开来源，只返回 URL",
            "output_contract": {"type": "url_list"},
            "acceptance": ["至少 20 条"],
        }))
        self.assertTrue(result["ok"], result)

    def test_report_url_conflict(self) -> None:
        result = validate_plan(plan_with({
            "brief": "撰写一份 1500 字市场报告",
            "output_contract": {"type": "url_list"},
            "acceptance": ["报告完整"],
        }))
        self.assertFalse(result["ok"])
        self.assertTrue(any("URL-only" in error for error in result["errors"]))

    def test_url_only_brief_with_report_contract(self) -> None:
        result = validate_plan(plan_with({
            "brief": "只返回 URL 列表，不要抓取正文",
            "output_contract": {"type": "report"},
            "acceptance": ["URL 列表"],
        }))
        self.assertFalse(result["ok"])

    def test_duplicate_ids(self) -> None:
        plan = {
            "task_id": "demo",
            "goal": "a long enough goal",
            "nodes": [
                {"id": "n1", "brief": "a", "output_contract": {"type": "url_list"}, "acceptance": ["x"]},
                {"id": "n1", "brief": "b", "output_contract": {"type": "url_list"}, "acceptance": ["x"]},
            ],
        }
        result = validate_plan(plan)
        self.assertFalse(result["ok"])
        self.assertTrue(any("duplicate node id" in error for error in result["errors"]))

    def test_missing_nodes(self) -> None:
        result = validate_plan({"task_id": "demo", "goal": "a long enough goal"})
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
