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

from caprouter.evidence import collect_evidence, summarize_evidence


class EvidenceTests(unittest.TestCase):
    def test_collects_package_scripts_entrypoints_and_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "demo"
            root.mkdir()
            (root / "package.json").write_text(
                json.dumps(
                    {
                        "name": "demo-tool",
                        "description": "Convert and export documents",
                        "keywords": ["convert"],
                        "bin": {"demo": "cli.js"},
                        "scripts": {"build": "vite build", "convert": "node convert.js"},
                        "dependencies": {"axios": "^1.0.0"},
                    }
                ),
                encoding="utf-8",
            )
            (root / "README.md").write_text(
                "# Demo\n\n## Usage\n\n```bash\nnpm run build\nnode convert.js\n```\n",
                encoding="utf-8",
            )
            evidence = collect_evidence(root, "project")
        self.assertEqual(evidence["metadata"]["name"], "demo-tool")
        self.assertIn("demo", evidence["entrypoints"])
        self.assertEqual({item["name"] for item in evidence["scripts"]}, {"build", "convert"})
        self.assertIn("npm run build", evidence["commands"])
        self.assertTrue(evidence["signals"]["has_cli"])
        self.assertTrue(evidence["signals"]["has_shell"])
        self.assertTrue(evidence["signals"]["has_network"])

    def test_pyproject_entrypoints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "demo"
            root.mkdir()
            (root / "pyproject.toml").write_text(
                '[project]\nname = "demo-py"\ndescription = "Analyze market data"\n'
                'dependencies = ["httpx>=0.27"]\n\n[project.scripts]\ndemo-py = "demo.cli:main"\n',
                encoding="utf-8",
            )
            evidence = collect_evidence(root, "project")
        self.assertEqual(evidence["metadata"]["name"], "demo-py")
        self.assertIn("demo-py", evidence["entrypoints"])
        self.assertIn("httpx", evidence["dependencies"]["runtime"])

    def test_skill_frontmatter_and_no_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skill"
            root.mkdir()
            (root / "SKILL.md").write_text(
                "---\nname: demo-skill\ndescription: Review local documents\nkeywords: [review]\n---\n"
                "# Demo\n\n## Checklist\n- Check the title\n- Check the references\n",
                encoding="utf-8",
            )
            with mock.patch("subprocess.run", side_effect=AssertionError("must not execute")):
                evidence = collect_evidence(root, "skill")
        self.assertEqual(evidence["metadata"]["name"], "demo-skill")
        self.assertIn("SKILL.md", evidence["artifacts"])
        self.assertEqual(evidence["warnings"], [])

    def test_readme_commands_are_capped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "demo"
            root.mkdir()
            commands = "\n".join(f"python script_{index}.py" for index in range(40))
            (root / "README.md").write_text(f"## Usage\n\n```bash\n{commands}\n```\n", encoding="utf-8")
            evidence = collect_evidence(root, "project")
        self.assertLessEqual(len(evidence["commands"]), 20)

    def test_summary_omits_full_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "demo"
            root.mkdir()
            (root / "README.md").write_text("# Demo\n\nA project that converts files.\n", encoding="utf-8")
            evidence = collect_evidence(root, "project")
            summary = summarize_evidence(evidence)
        self.assertNotIn("summary_text", summary)
        self.assertIn("artifacts", summary)

    def test_gradle_and_maven_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gradle = Path(tmp) / "gradle-demo"
            gradle.mkdir()
            (gradle / "settings.gradle").write_text("rootProject.name = 'gradle-demo'\n", encoding="utf-8")
            (gradle / "build.gradle").write_text("description = 'Analyze build reports'\n", encoding="utf-8")
            gradle_evidence = collect_evidence(gradle, "project")

            maven = Path(tmp) / "maven-demo"
            maven.mkdir()
            (maven / "pom.xml").write_text(
                "<project><name>maven-demo</name><description>Extract table data</description></project>",
                encoding="utf-8",
            )
            maven_evidence = collect_evidence(maven, "project")
        self.assertTrue(gradle_evidence["signals"]["has_build"])
        self.assertEqual(gradle_evidence["metadata"]["description"], "Analyze build reports")
        self.assertEqual(maven_evidence["metadata"]["description"], "Extract table data")


if __name__ == "__main__":
    unittest.main()
