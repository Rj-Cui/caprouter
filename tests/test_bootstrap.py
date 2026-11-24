from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from caprouter.capabilities import search_capabilities
from caprouter.cli import main


def _run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


def _project(root: Path, name: str) -> Path:
    project = root / name
    project.mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        f'[project]\nname="{name}"\ndescription="Analyze market reports and extract sources"\n',
        encoding="utf-8",
    )
    (project / "README.md").write_text(
        "# Demo\n\n## Usage\n- Analyze a market report\n- Extract sources from a report\n",
        encoding="utf-8",
    )
    return project


class BootstrapTests(unittest.TestCase):
    def test_dry_run_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            root = Path(tmp) / "projects"
            _project(root, "demo-app")
            code, out, err = _run(
                [
                    "bootstrap",
                    "--workspace",
                    str(workspace),
                    "--project-root",
                    str(root),
                    "--dry-run",
                    "--no-probe",
                    "--no-agents",
                    "--json",
                ]
            )
            self.assertEqual(code, 0, err)
            report = json.loads(out)
            self.assertTrue(report["dry_run"])
            self.assertFalse(workspace.exists())

    def test_bootstrap_drafts_but_never_applies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            root = Path(tmp) / "projects"
            _project(root, "demo-app")
            code, out, err = _run(
                [
                    "bootstrap",
                    "--workspace",
                    str(workspace),
                    "--project-root",
                    str(root),
                    "--no-probe",
                    "--no-agents",
                    "--json",
                ]
            )
            self.assertEqual(code, 0, err)
            report = json.loads(out)
            bus = workspace / ".caprouter"
            self.assertTrue((bus / "config.json").is_file())
            self.assertTrue((bus / "registry.json").is_file())
            self.assertTrue((bus / "projects_scan.json").is_file())
            self.assertTrue((bus / "drafts" / "projects").is_dir())
            self.assertFalse((bus / "capability_cards.json").exists())
            self.assertEqual(report["drafts"]["applied"], 0)
            self.assertEqual(report["drafts"]["drafted"], 1)

            config = json.loads((bus / "config.json").read_text(encoding="utf-8"))
            roots = [Path(item).resolve() for item in config["scan"]["project_roots"]]
            self.assertIn(root.resolve(), roots)
            registry = json.loads((bus / "registry.json").read_text(encoding="utf-8"))
            self.assertIn("project:demo-app", [entry["id"] for entry in registry["entries"]])

    def test_batch_apply_makes_project_searchable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            root = Path(tmp) / "projects"
            _project(root, "demo-app")
            code, out, err = _run(
                [
                    "bootstrap",
                    "--workspace",
                    str(workspace),
                    "--project-root",
                    str(root),
                    "--no-probe",
                    "--no-agents",
                    "--no-default-skills",
                    "--json",
                ]
            )
            self.assertEqual(code, 0, err)

            code, out, err = _run(
                [
                    "capability",
                    "draft",
                    "--all",
                    "--apply",
                    "--workspace",
                    str(workspace),
                    "--json",
                ]
            )
            self.assertEqual(code, 0, err)
            report = json.loads(out)
            self.assertEqual(report["applied"], 1)

            result = search_capabilities(workspace, "分析市场报告并提取来源", top=3)
            self.assertEqual(result["decision"], "select")
            self.assertEqual(result["candidates"][0]["id"], "project:demo-app")
            self.assertTrue(result["candidates"][0]["capabilities"])

    def test_no_default_skills_creates_clean_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            root = Path(tmp) / "projects"
            _project(root, "demo-app")
            code, out, err = _run(
                [
                    "bootstrap",
                    "--workspace",
                    str(workspace),
                    "--project-root",
                    str(root),
                    "--no-probe",
                    "--no-agents",
                    "--no-default-skills",
                    "--json",
                ]
            )
            self.assertEqual(code, 0, err)
            bus = workspace / ".caprouter"
            config = json.loads((bus / "config.json").read_text(encoding="utf-8"))
            registry = json.loads((bus / "registry.json").read_text(encoding="utf-8"))
        self.assertEqual(config["skill_roots"], [])
        self.assertEqual(config["plugin_roots"], [])
        self.assertEqual(
            [entry["id"] for entry in registry["entries"]],
            ["project:demo-app"],
        )

    def test_rescan_keeps_projects_from_saved_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            root = Path(tmp) / "projects"
            _project(root, "demo-app")
            code, _out, err = _run(
                [
                    "bootstrap",
                    "--workspace",
                    str(workspace),
                    "--project-root",
                    str(root),
                    "--no-probe",
                    "--no-agents",
                    "--json",
                ]
            )
            self.assertEqual(code, 0, err)
            config_file = workspace / ".caprouter" / "config.json"
            config = json.loads(config_file.read_text(encoding="utf-8"))
            config["scan"]["project_roots"] = []
            config_file.write_text(json.dumps(config), encoding="utf-8")

            code, out, err = _run(["scan", "--workspace", str(workspace), "--json"])
            self.assertEqual(code, 0, err)
            registry = json.loads((workspace / ".caprouter" / "registry.json").read_text(encoding="utf-8"))
            self.assertIn("project:demo-app", [entry["id"] for entry in registry["entries"]])

    def test_draft_all_requires_scan_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / ".caprouter").mkdir(parents=True)
            (workspace / ".caprouter" / "config.json").write_text("{}", encoding="utf-8")
            code, _out, err = _run(
                ["capability", "draft", "--all", "--workspace", str(workspace), "--json"]
            )
            self.assertEqual(code, 1)
            self.assertIn("projects_scan.json", err)

    def test_bootstrap_does_not_execute_project_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            root = Path(tmp) / "projects"
            project = _project(root, "demo-app")
            (project / "run.sh").write_text("echo should-not-run\n", encoding="utf-8")
            with mock.patch("caprouter.scan.subprocess.run", side_effect=AssertionError("exec")):
                code, out, err = _run(
                    [
                        "bootstrap",
                        "--workspace",
                        str(workspace),
                        "--project-root",
                        str(root),
                        "--no-probe",
                        "--no-agents",
                        "--json",
                    ]
                )
            self.assertEqual(code, 0, err)
            self.assertEqual(json.loads(out)["drafts"]["drafted"], 1)


if __name__ == "__main__":
    unittest.main()
