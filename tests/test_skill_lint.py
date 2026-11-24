from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from caprouter.scan import lint_skill


class SkillLintTests(unittest.TestCase):
    def make_skill(self, root: Path, name: str, description: str) -> Path:
        skill_dir = root / "demo"
        (skill_dir / "references").mkdir(parents=True)
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(
            f"---\nname: {name}\ndescription: {description}\n---\nbody\n",
            encoding="utf-8",
        )
        return skill_file

    def test_valid_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skill = self.make_skill(Path(tmp), "demo-skill", "A valid skill")
            result = lint_skill(skill)
            self.assertTrue(result["ok"], result)

    def test_invalid_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skill = self.make_skill(Path(tmp), "Bad_Name", "A valid skill")
            result = lint_skill(skill)
            self.assertFalse(result["ok"])
            self.assertTrue(any("lowercase" in error for error in result["errors"]))

    def test_missing_references_warns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = root / "demo"
            skill_dir.mkdir()
            skill = skill_dir / "SKILL.md"
            skill.write_text("---\nname: demo\ndescription: A skill\n---\n", encoding="utf-8")
            result = lint_skill(skill)
            self.assertTrue(result["ok"])
            self.assertTrue(any("references" in warning for warning in result["warnings"]))


if __name__ == "__main__":
    unittest.main()
