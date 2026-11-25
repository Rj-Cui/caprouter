"""Project-nested skills must reach the registry and the search index."""
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

from support import (  # noqa: E402
    enter_isolated_env,
    make_workspace,
    read_config,
    registry_ids,
    write_config,
    write_skill,
)

from caprouter.capabilities import search_capabilities  # noqa: E402
from caprouter.workspace import refresh_registry  # noqa: E402


def build_project(root: Path) -> Path:
    project = root / "demoproj"
    (project / "skills").mkdir(parents=True, exist_ok=True)
    write_skill(project / "skills", name="pdf-tool")
    (project / "package.json").write_text(
        json.dumps({"name": "demoproj", "version": "1.0.0"}), encoding="utf-8"
    )
    (project / "requirements.txt").write_text("pypdf==4.2.0\n", encoding="utf-8")
    return project


class ProjectSkillIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="caprouter-project-"))
        enter_isolated_env(self, self.tmp / "home")
        self.project = build_project(self.tmp)
        self.workspace = make_workspace(self.tmp)

    def _refresh_with_project(self) -> dict:
        config = read_config(self.workspace)
        config.setdefault("scan", {})
        config["scan"]["project_roots"] = [str(self.project)]
        write_config(self.workspace, config)
        return refresh_registry(self.workspace, config)

    def test_nested_skill_is_indexed(self) -> None:
        registry = self._refresh_with_project()
        entries = {entry["id"]: entry for entry in registry["entries"]}
        self.assertIn("skill:pdf-tool", entries, sorted(entries))
        entry = entries["skill:pdf-tool"]
        self.assertEqual(entry["type"], "skill")
        self.assertIn("pdf", entry["description"].lower())
        self.assertIn("pdf", " ".join(entry["keywords"]).lower())
        # Provenance: which project container the skill came from.
        self.assertEqual(entry.get("container"), "project:demoproj")
        # The project itself is still indexed.
        self.assertIn("project:demoproj", entries)

    def test_search_finds_the_nested_skill(self) -> None:
        self._refresh_with_project()
        # Regression: this used to abstain with "no lexical candidates" because
        # only the project directory name was indexed.
        result = search_capabilities(self.workspace, "convert a PDF to Word", top=5)
        self.assertEqual(result.get("decision"), "select", result.get("abstain_reason"))
        self.assertEqual((result.get("selected") or {}).get("id"), "skill:pdf-tool")

    def test_nested_skill_does_not_duplicate_configured_roots(self) -> None:
        registry = self._refresh_with_project()
        ids = [entry["id"] for entry in registry["entries"]]
        self.assertEqual(ids.count("skill:pdf-tool"), 1, ids)

    def test_host_skill_dirs_inside_a_project_are_found(self) -> None:
        from caprouter.scan import scan_project_skills

        hidden = self.project / ".claude" / "skills" / "hidden-skill"
        hidden.mkdir(parents=True, exist_ok=True)
        (hidden / "SKILL.md").write_text(
            "---\nname: hidden-skill\ndescription: a project-local host skill\n---\nbody\n",
            encoding="utf-8",
        )
        names = {entry["name"] for entry in scan_project_skills(self.project)}
        self.assertIn("pdf-tool", names)
        # `.claude/skills/<name>/SKILL.md` is project-local and loadable by the
        # host, so it is indexed too (only the skill *file* dir is inspected).
        self.assertIn("hidden-skill", names)


if __name__ == "__main__":
    unittest.main()
