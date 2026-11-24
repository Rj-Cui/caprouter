from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from caprouter.deps import check_dependencies, plan_dependencies
from caprouter.mcp_server import _dispatch
from caprouter.workspace import create_workspace


class DepsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "skills"

        node = self.root / "node-demo"
        node.mkdir(parents=True)
        (node / "SKILL.md").write_text(
            "---\nname: node-demo\ndescription: Node demo\n---\n# Node\n", encoding="utf-8"
        )
        (node / "package.json").write_text(
            json.dumps(
                {
                    "dependencies": {"left-pad": "1.3.0"},
                    "scripts": {"postinstall": "node evil.js"},
                }
            ),
            encoding="utf-8",
        )
        (node / "package-lock.json").write_text(
            json.dumps(
                {
                    "lockfileVersion": 3,
                    "packages": {
                        "": {},
                        "node_modules/left-pad": {
                            "version": "1.3.0",
                            "hasInstallScript": True,
                        },
                    },
                }
            ),
            encoding="utf-8",
        )
        self.node = node

        py = self.root / "py-demo"
        py.mkdir(parents=True)
        (py / "SKILL.md").write_text(
            "---\nname: py-demo\ndescription: Python demo\n---\n# Py\n", encoding="utf-8"
        )
        (py / "pyproject.toml").write_text(
            "\n".join(
                [
                    "[build-system]",
                    'requires = []',
                    'build-backend = "setuptools.build_meta"',
                    "",
                    "[project]",
                    'name = "py-demo"',
                    'version = "0.1.0"',
                    'dependencies = ["definitely-missing-package-xyz==1.0"]',
                ]
            ),
            encoding="utf-8",
        )
        (py / "requirements.lock").write_text(
            "definitely-missing-package-xyz==1.0 --hash=sha256:" + "0" * 64 + "\n",
            encoding="utf-8",
        )
        self.py = py

        self.ws = base / "ws"
        create_workspace(
            self.ws,
            include_agents=False,
            include_default_roots=False,
            extra_skill_roots=[self.root],
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_node_plan_flags_postinstall_and_uses_ignore_scripts(self) -> None:
        plan = plan_dependencies(self.ws, "skill:node-demo")
        self.assertIn("postinstall", plan["risk_flags"])
        self.assertIn("package-lock.json", plan["lockfiles"])
        commands = [item["argv"] for item in plan["commands"]]
        self.assertIn(["npm", "ci", "--ignore-scripts"], commands)
        self.assertIn("registry.npmjs.org", plan["network_hosts"])
        self.assertIn("does not execute", plan["note"])

    def test_python_plan_uses_hashes_and_flags_build_backend(self) -> None:
        plan = plan_dependencies(self.ws, "skill:py-demo")
        self.assertIn("build_backend", plan["risk_flags"])
        commands = [item["argv"] for item in plan["commands"]]
        self.assertIn(
            ["python", "-m", "pip", "install", "--require-hashes", "-r", "requirements.lock"],
            commands,
        )
        self.assertIn("pypi.org", plan["network_hosts"])

    def test_check_reports_missing_python_and_node(self) -> None:
        py = check_dependencies(self.ws, "skill:py-demo")
        self.assertTrue(
            any(item["name"] == "definitely-missing-package-xyz" for item in py["missing"])
        )
        node = check_dependencies(self.ws, "skill:node-demo")
        self.assertTrue(any(item["name"] == "left-pad" for item in node["missing"]))

        (self.node / "node_modules" / "left-pad").mkdir(parents=True)
        satisfied = check_dependencies(self.ws, "skill:node-demo")
        self.assertTrue(any(item["name"] == "left-pad" for item in satisfied["satisfied"]))

    def test_no_lockfile_is_flagged(self) -> None:
        package = self.node / "package.json"
        data = json.loads(package.read_text(encoding="utf-8"))
        data["scripts"] = {}
        package.write_text(json.dumps(data), encoding="utf-8")
        (self.node / "package-lock.json").unlink()
        plan = plan_dependencies(self.ws, "skill:node-demo")
        self.assertIn("no_lockfile", plan["risk_flags"])
        self.assertTrue(any(item["requires_lockfile"] for item in plan["commands"]))

    def test_mcp_tool_plan(self) -> None:
        result = _dispatch(
            self.ws, "capability_deps", {"id": "skill:node-demo", "action": "plan"}
        )
        self.assertEqual(result["schema"], "caprouter.deps-plan.v1")
        check = _dispatch(
            self.ws, "capability_deps", {"id": "skill:py-demo", "action": "check"}
        )
        self.assertEqual(check["schema"], "caprouter.deps-check.v1")


if __name__ == "__main__":
    unittest.main()
