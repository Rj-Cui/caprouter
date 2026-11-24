from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from caprouter.scan import (
    _scan_one_root,
    build_registry,
    classify_drives,
    detect_source_type,
    infer_capabilities,
    project_entry,
    resolve_scan_roots,
    scan_project_roots,
    scan_skill_root,
    summarize_scan,
    summary_size,
)
from caprouter.util import parse_frontmatter


def _make_project(root: Path, name: str, marker: str = "package.json") -> Path:
    project = root / name
    project.mkdir(parents=True)
    (project / marker).write_text("{}", encoding="utf-8")
    return project


class ScanTests(unittest.TestCase):
    def test_parse_frontmatter_scalars_and_list(self) -> None:
        text = """---
name: demo-skill
description: 一个用于网页设计的技能
keywords: [网页, design, ui]
---
body
"""
        meta, body = parse_frontmatter(text)
        self.assertEqual(meta["name"], "demo-skill")
        self.assertEqual(meta["keywords"], ["网页", "design", "ui"])
        self.assertEqual(body.strip(), "body")

    def test_scan_skill_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            skill = root / "design" / "demo-skill"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\nname: demo-skill\nversion: 1.0.0\ndescription: 网页 UI 设计与响应式检查\nkeywords: [网页, 设计]\n---\n",
                encoding="utf-8",
            )
            entries = scan_skill_root(root)
            self.assertEqual(len(entries), 1)
            entry = entries[0]
            self.assertEqual(entry["id"], "skill:demo-skill")
            self.assertEqual(entry["type"], "skill")
            self.assertEqual(entry["version"], "1.0.0")
            self.assertIn("design", entry["capabilities"])
            self.assertEqual(entry["status"], "verified")
            self.assertTrue(entry["hash"].startswith("sha256:"))

    def test_infer_capabilities(self) -> None:
        caps = infer_capabilities("市场调研与竞品分析")
        self.assertIn("retrieve", caps)
        self.assertIn("analyze", caps)

    def test_detect_source_type_expanded_markers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(detect_source_type(_make_project(root, "py", "pyproject.toml")), "project")
            self.assertEqual(detect_source_type(_make_project(root, "req", "requirements.txt")), "project")
            skill = root / "skill"
            skill.mkdir()
            (skill / "SKILL.md").write_text("---\nname: s\n---\n", encoding="utf-8")
            (skill / "package.json").write_text("{}", encoding="utf-8")
            self.assertEqual(detect_source_type(skill), "skill")


class DriveClassificationTests(unittest.TestCase):
    def test_classify_drives_marks_system_and_defaults(self) -> None:
        drives = [("C:\\", 3), ("D:\\", 3), ("E:\\", 2), ("Z:\\", 4)]
        with mock.patch("caprouter.scan._enumerate_windows_drives", return_value=drives), \
                mock.patch.dict("os.environ", {"SystemDrive": "C:"}):
            items = {item["drive"]: item for item in classify_drives()}
        self.assertTrue(items["C:"]["system"])
        self.assertFalse(items["C:"]["scan_default"])
        self.assertTrue(items["D:"]["scan_default"])
        self.assertEqual(items["E:"]["type"], "removable")
        self.assertFalse(items["E:"]["scan_default"])
        self.assertEqual(items["Z:"]["type"], "remote")
        self.assertFalse(items["Z:"]["scan_default"])

    def test_resolve_roots_explicit_overrides_defaults(self) -> None:
        drives = [("C:\\", 3), ("D:\\", 3), ("E:\\", 2)]
        with mock.patch("caprouter.scan._enumerate_windows_drives", return_value=drives), \
                mock.patch.dict("os.environ", {"SystemDrive": "C:"}):
            default = resolve_scan_roots()
            explicit = resolve_scan_roots(drives=["E"])
        self.assertEqual([item["drive"] for item in default], ["D:"])
        self.assertEqual([item["drive"] for item in explicit], ["E:"])

    def test_project_root_only_skips_drive_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "projects"
            root.mkdir()
            with mock.patch("caprouter.scan._enumerate_windows_drives", return_value=[("D:\\", 3)]):
                selected = resolve_scan_roots(project_roots=[root])
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["kind"], "project")


class ProjectScanTests(unittest.TestCase):
    def test_scan_finds_projects_and_stops_at_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            project = _make_project(root, "alpha")
            nested = project / "packages" / "nested"
            nested.mkdir(parents=True)
            (nested / "package.json").write_text("{}", encoding="utf-8")
            skill = root / "skills" / "demo"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("---\nname: demo\n---\n", encoding="utf-8")
            (root / "empty").mkdir()

            result = scan_project_roots([root], time_budget_seconds=30)
            found = {item["name"]: item for item in result["projects"]}
            self.assertEqual(set(found), {"alpha", "demo"})
            self.assertEqual(found["alpha"]["type"], "project")
            self.assertEqual(found["demo"]["type"], "skill")
            self.assertEqual(result["stats"]["projects"], 2)
            self.assertEqual(result["duplicates"], [])

    def test_drive_root_is_never_a_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "drive"
            root.mkdir()
            (root / ".git").mkdir()
            real = _make_project(root, "real-project")
            report = _scan_one_root(
                {"root": str(root), "drive": "Z:", "kind": "drive", "type": "fixed"},
                max_depth=4,
                max_dirs=100,
                time_budget_seconds=30,
                max_projects=10,
            )
        names = [item["name"] for item in report["_projects"]]
        self.assertEqual(names, ["real-project"])
        self.assertEqual(Path(report["_projects"][0]["path"]).resolve(), real.resolve())

    def test_scan_skips_build_marker_without_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            legacy = root / "legacy-tool"
            legacy.mkdir(parents=True)
            (legacy / "settings.gradle").write_text("{}", encoding="utf-8")
            real = _make_project(root, "real-project")
            result = scan_project_roots([root], time_budget_seconds=30)
        paths = {Path(item["path"]).resolve() for item in result["projects"]}
        self.assertEqual(paths, {real.resolve()})
        self.assertIn("build_without_source", result["skipped"])

    def test_explicit_weak_project_root_is_kept(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "legacy-tool"
            root.mkdir(parents=True)
            (root / "settings.gradle").write_text("{}", encoding="utf-8")
            result = scan_project_roots([root], time_budget_seconds=30)
        self.assertEqual(result["stats"]["projects"], 1)
        self.assertEqual(Path(result["projects"][0]["path"]).resolve(), root.resolve())

    def test_scan_skips_system_and_cache_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            for name in ("node_modules", "Program Files", ".venv", "SteamLibrary", "build"):
                _make_project(root / name, "hidden-project")
            real = _make_project(root, "real-project")

            result = scan_project_roots([root], time_budget_seconds=30)
            paths = {Path(item["path"]).resolve() for item in result["projects"]}
            self.assertEqual(paths, {real.resolve()})
            self.assertIn("node_modules", result["skipped"])
            self.assertIn("program files", result["skipped"])
            self.assertIn(".venv", result["skipped"])
            self.assertIn("steamlibrary", result["skipped"])

    def test_scan_respects_dir_budget_and_reports_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            for index in range(10):
                (root / f"dir-{index}").mkdir(parents=True)
            result = scan_project_roots([root], max_dirs=2, time_budget_seconds=30)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["truncated"][0]["reason"], "max_dirs")
        self.assertLessEqual(result["stats"]["dirs_scanned"], 2)

    def test_depth_limit_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            deep = root / "a" / "b" / "c"
            _make_project(deep, "deep-project")
            result = scan_project_roots([root], max_depth=2, time_budget_seconds=30)
        self.assertEqual(result["stats"]["projects"], 0)
        self.assertGreaterEqual(result["stats"]["depth_limited_dirs"], 1)

    def test_scan_respects_project_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            _make_project(root, "one")
            _make_project(root, "two")
            result = scan_project_roots([root], max_projects=1, time_budget_seconds=30)
        self.assertEqual(result["stats"]["projects"], 1)
        self.assertEqual(result["truncated"][0]["reason"], "max_projects")

    def test_scan_respects_time_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            _make_project(root, "one")
            result = scan_project_roots([root], time_budget_seconds=0)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["truncated"][0]["reason"], "time_budget")

    def test_scan_never_reads_or_executes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            project = _make_project(root, "safe")
            (project / "README.md").write_text("should not be read", encoding="utf-8")
            with mock.patch("pathlib.Path.read_text", side_effect=AssertionError("read_text")), \
                    mock.patch("caprouter.scan.subprocess.run", side_effect=AssertionError("exec")):
                result = scan_project_roots([root], time_budget_seconds=30)
        self.assertEqual(result["stats"]["projects"], 1)

    def test_duplicate_roots_are_deduped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            _make_project(root, "one")
            result = scan_project_roots([root, root], time_budget_seconds=30)
        self.assertEqual(result["stats"]["projects"], 1)
        self.assertEqual(len(result["duplicates"]), 1)
        self.assertEqual(result["duplicates"][0]["reason"], "duplicate_path")

    def test_same_name_projects_get_distinct_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            _make_project(root / "a", "tools")
            _make_project(root / "b", "tools")
            workspace = Path(tmp) / "ws"
            scan = scan_project_roots([root], time_budget_seconds=30)
            registry = build_registry(
                workspace,
                skill_roots=[],
                plugin_roots=[],
                include_agents=False,
                scanned_projects=scan["projects"],
            )
        projects = [entry for entry in registry["entries"] if entry["type"] == "project"]
        self.assertEqual(len(projects), 2)
        self.assertEqual(len({entry["id"] for entry in projects}), 2)
        self.assertEqual(len({entry["path"] for entry in projects}), 2)

    def test_project_entry_is_lightweight(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            project = _make_project(root, "one")
            entry = project_entry(
                {
                    "path": str(project),
                    "name": "one",
                    "markers": ["package.json"],
                    "type": "project",
                    "mtime": "2026-01-01T00:00:00+08:00",
                }
            )
        self.assertEqual(entry["id"], "project:one")
        self.assertEqual(entry["status"], "scanned")
        self.assertTrue(entry["hash"].startswith("sha256:"))
        self.assertEqual(entry["source"], "scan")

    def test_summary_stays_small(self) -> None:
        projects = [
            {
                "name": f"project-{index}-" + "x" * 60,
                "path": "D:\\work\\" + "y" * 120 + f"\\{index}",
                "type": "project",
                "mtime": f"2026-01-{index % 28 + 1:02d}T00:00:00+08:00",
            }
            for index in range(200)
        ]
        result = {
            "generated_at": "2026-01-01T00:00:00+08:00",
            "stats": {"projects": 200},
            "roots": [
                {
                    "root": "D:\\work\\" + "z" * 150 + f"\\{index}",
                    "drive": "D:",
                    "kind": "project",
                    "type": "project_root",
                }
                for index in range(20)
            ],
            "truncated": [{"root": "D:\\", "reason": "max_projects", "dirs_scanned": 1, "projects": 500}],
            "skipped": {"node_modules": 99, "build": 40},
            "warnings": [],
            "projects": projects,
        }
        summary = summarize_scan(result, recent=10)
        self.assertLessEqual(summary_size(summary), 4096)
        self.assertLessEqual(len(summary["recent_projects"]), 10)


if __name__ == "__main__":
    unittest.main()
