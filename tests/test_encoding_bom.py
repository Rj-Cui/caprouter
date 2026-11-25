"""UTF-8 BOM tolerance for files CapRouter reads from the machine."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for _path in (ROOT, HERE):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from support import enter_isolated_env, make_workspace, read_config, write_config  # noqa: E402

from caprouter.deps import plan_dependencies  # noqa: E402
from caprouter.evidence import collect_evidence  # noqa: E402
from caprouter.workspace import refresh_registry  # noqa: E402

BOM = "\ufeff"


class BomToleranceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="caprouter-bom-"))
        enter_isolated_env(self, self.tmp / "home")
        self.project = self.tmp / "demoproj"
        self.project.mkdir()
        # Windows tooling (PowerShell 5, Notepad, Visual Studio) writes a BOM
        # by default; parsing must not leak U+FEFF into names.
        (self.project / "package.json").write_text(
            BOM + json.dumps(
                {"name": "demoproj", "version": "1.0.0", "description": "PDF conversion demo"}
            ),
            encoding="utf-8",
        )
        (self.project / "requirements.txt").write_text(
            BOM + "pypdf==4.2.0\npdf2docx==0.5.8\n", encoding="utf-8"
        )
        self.workspace = make_workspace(self.tmp)
        config = read_config(self.workspace)
        config.setdefault("scan", {})
        config["scan"]["project_roots"] = [str(self.project)]
        write_config(self.workspace, config)
        refresh_registry(self.workspace, config)

    def test_requirements_with_bom_are_parsed_cleanly(self) -> None:
        plan = plan_dependencies(self.workspace, "project:demoproj")
        names = {item["name"] for item in plan.get("declared") or []}
        self.assertIn("pypdf", names)
        self.assertIn("pdf2docx", names)
        self.assertFalse([name for name in names if "\ufeff" in name], names)

    def test_package_json_with_bom_is_not_reported_as_broken(self) -> None:
        evidence = collect_evidence(self.project, "project")
        warnings = " ".join(evidence.get("warnings") or [])
        self.assertNotIn("package.json is not a JSON object", warnings)
        self.assertEqual(evidence.get("metadata", {}).get("description"), "PDF conversion demo")
        self.assertIn("package.json", evidence.get("artifacts") or [])

    def test_skill_md_with_bom_keeps_its_frontmatter(self) -> None:
        skill = self.project / "skills" / "pdf-tool"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            BOM
            + "---\nname: pdf-tool\ndescription: Convert PDF files to Word documents\n"
            "keywords: [pdf, convert]\n---\nbody\n",
            encoding="utf-8",
        )
        registry = refresh_registry(self.workspace, read_config(self.workspace))
        entry = next(
            (item for item in registry["entries"] if item["id"] == "skill:pdf-tool"), None
        )
        self.assertIsNotNone(entry)
        self.assertEqual(entry["description"], "Convert PDF files to Word documents")
        self.assertEqual(entry["status"], "verified")


if __name__ == "__main__":
    unittest.main()
