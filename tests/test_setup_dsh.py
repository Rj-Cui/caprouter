from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from caprouter.setup_hosts import (  # noqa: E402
    DSH_HOST,
    HOSTS,
    link_installed_skills,
    setup_host,
)


def write_skill(root: Path, name: str) -> Path:
    skill = root / name
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test skill\n---\n\nbody\n",
        encoding="utf-8",
    )
    return skill


class DshSetupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ws = self.root / "workspace"
        self.ws.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_deepseek_harness_is_a_known_host(self) -> None:
        self.assertIn(DSH_HOST, HOSTS)
        self.assertEqual(DSH_HOST, "deepseek-harness")

    def test_print_only_creates_nothing(self) -> None:
        result = setup_host(DSH_HOST, self.ws)
        self.assertFalse(result["written"])
        self.assertFalse((self.ws / "caprouter-dsh-bundle").exists())
        self.assertIn("serverName: caprouter", result["patch_text"])
        self.assertIn("dsh-mcp-client", result["patch_text"])
        self.assertEqual(result["linked_skills"], [])

    def test_write_bundle_layout(self) -> None:
        bundle = self.root / "bundle"
        result = setup_host(DSH_HOST, self.ws, config_path=bundle, write=True)
        self.assertTrue(result["written"])
        self.assertTrue((bundle / "cordis.patch.yml").is_file())
        manifest = json.loads((bundle / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["dsh"]["bundle"]["patch"], "./cordis.patch.yml")
        self.assertIn("./locale/*.json", manifest["exports"])
        for locale in ("en", "zh"):
            payload = json.loads((bundle / "locale" / f"{locale}.json").read_text(encoding="utf-8"))
            self.assertIn("title", payload["meta"])
        patch = (bundle / "cordis.patch.yml").read_text(encoding="utf-8")
        self.assertIn("--workspace", patch)
        self.assertIn(str(self.ws.resolve()), patch)
        self.assertIn("--tools", patch)
        self.assertIn("--host", patch)
        self.assertIn("deepseek-harness", patch)
        self.assertIn("toolCallTimeoutMs: 120000", patch)

    def test_link_skills_copies_and_skips(self) -> None:
        source = self.ws / ".caprouter" / "installed" / "skills"
        write_skill(source, "demo-skill")
        (source / "broken").mkdir()
        skills_root = self.root / "dsh-skills"
        first = link_installed_skills(self.ws, skills_root)
        self.assertEqual(first["linked"], ["demo-skill"])
        self.assertTrue((skills_root / "demo-skill" / "SKILL.md").is_file())
        self.assertTrue(any("no SKILL.md" in item for item in first["skipped"]))
        second = link_installed_skills(self.ws, skills_root)
        self.assertEqual(second["linked"], [])
        self.assertTrue(any("already exists" in item for item in second["skipped"]))

    def test_link_skills_force_replaces(self) -> None:
        source = self.ws / ".caprouter" / "installed" / "skills"
        write_skill(source, "demo-skill")
        skills_root = self.root / "dsh-skills"
        link_installed_skills(self.ws, skills_root)
        (skills_root / "demo-skill" / "SKILL.md").write_text("stale", encoding="utf-8")
        forced = link_installed_skills(self.ws, skills_root, force=True)
        self.assertEqual(forced["linked"], ["demo-skill"])
        self.assertIn("test skill", (skills_root / "demo-skill" / "SKILL.md").read_text(encoding="utf-8"))

    def test_link_skills_missing_source_reports_error(self) -> None:
        result = link_installed_skills(self.ws, self.root / "dsh-skills")
        self.assertEqual(result["linked"], [])
        self.assertTrue(result["errors"])

    def test_link_skills_rejected_for_other_hosts(self) -> None:
        with self.assertRaises(ValueError):
            setup_host("cursor", self.ws, link_skills=True)

    def test_setup_integrates_link_flag(self) -> None:
        source = self.ws / ".caprouter" / "installed" / "skills"
        write_skill(source, "demo-skill")
        skills_root = self.root / "dsh-skills"
        result = setup_host(
            DSH_HOST,
            self.ws,
            config_path=self.root / "bundle",
            write=True,
            link_skills=True,
            skills_root=skills_root,
        )
        self.assertEqual(result["linked_skills"], ["demo-skill"])
        self.assertEqual(result["skills_root"], str(skills_root))


if __name__ == "__main__":
    unittest.main()
