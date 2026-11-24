from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from caprouter.dispatch import assign


class DispatchTests(unittest.TestCase):
    def test_unavailable_primary_falls_back(self) -> None:
        registry = {
            "entries": [
                {
                    "id": "cli-agent:ghost",
                    "type": "cli-agent",
                    "name": "ghost-agent",
                    "path": "Z:/definitely-not-here/ghost.exe",
                    "capabilities": ["generate"],
                    "status": "verified",
                },
                {
                    "id": "cli-agent:python",
                    "type": "cli-agent",
                    "name": "python",
                    "path": sys.executable,
                    "capabilities": ["generate"],
                    "status": "verified",
                },
            ]
        }
        plan = {
            "task_id": "fallback",
            "goal": "test fallback",
            "nodes": [{"id": "n1", "brief": "generate content", "capability": "generate"}],
        }
        result = assign(plan, registry, {"min_score": 0}, ["ghost-agent", "python"])
        node = result["nodes"][0]
        self.assertEqual(node["agent"]["name"], "python")
        self.assertIsNone(node["fallback_agent"])


if __name__ == "__main__":
    unittest.main()
