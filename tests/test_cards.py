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

from caprouter.cards import (
    _clean_example,
    apply_draft,
    extract_examples,
    generate_draft,
    generate_project_drafts,
    write_draft,
)


class CardDraftTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)
        bus = self.ws / ".caprouter"
        bus.mkdir(parents=True)

        self.skill = self.ws / "skills" / "chart-maker"
        self.skill.mkdir(parents=True)
        (self.skill / "SKILL.md").write_text(
            "---\n"
            "name: chart-maker\n"
            "version: 2.0.0\n"
            "description: Turn spreadsheets into charts and save them\n"
            "keywords: [xlsx, chart]\n"
            "capabilities: [transform, format]\n"
            "---\n"
            "# Chart Maker\n\n"
            "## Examples\n"
            "- 把 xlsx 转成柱状图\n"
            "- 从表格生成折线图\n"
            "- 把 CSV 导出成 PNG 图表\n\n"
            "## Usage\n"
            "Run the script.\n",
            encoding="utf-8",
        )
        (bus / "registry.json").write_text(
            json.dumps({"entries": [{
                "id": "skill:chart-maker", "type": "skill", "name": "chart-maker",
                "path": str(self.skill), "description": "Turn spreadsheets into charts and save them",
                "keywords": ["xlsx", "chart"], "capabilities": ["transform", "format"],
                "domain": "office", "version": "2.0.0", "status": "verified",
            }]}, ensure_ascii=False),
            encoding="utf-8",
        )
        (bus / "config.json").write_text(
            json.dumps({"schema": "caprouter.config.v1", "routing": {}}),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_extract_examples(self) -> None:
        body = "## 示例\n- 把 PDF 转成 Word\n- 提取表格数据\n\n## 其他\n- 不应出现\n"
        examples = extract_examples(body, "描述")
        self.assertEqual(examples, ["把 PDF 转成 Word", "提取表格数据"])

    def test_example_truncation_keeps_word_boundary(self) -> None:
        value = "word " * 80
        cleaned = _clean_example(value)
        self.assertLessEqual(len(cleaned), 200)
        self.assertTrue(cleaned.endswith("word"))
        self.assertFalse(cleaned.endswith("wor"))

    def test_project_metadata_reads_gradle_description(self) -> None:
        project = self.ws / "gradle-tool"
        project.mkdir()
        (project / "build.gradle").write_text(
            "description = 'Convert spreadsheet reports'\n",
            encoding="utf-8",
        )
        draft = generate_draft(self.ws, path=project)
        self.assertEqual(draft["description"], "Convert spreadsheet reports")

    def test_generate_draft_from_path(self) -> None:
        draft = generate_draft(self.ws, path=self.skill)
        self.assertEqual(draft["schema"], "caprouter.capability-card-draft.v1")
        self.assertEqual(draft["id"], "skill:chart-maker")
        self.assertEqual(draft["type"], "skill")
        self.assertEqual(len(draft["examples"]), 3)
        self.assertTrue(any(check["type"] == "host" for check in draft["probe"]["checks"]))
        self.assertEqual(draft["invoke"]["mode"], "prompt")
        self.assertTrue(draft["invoke"]["content_path"].endswith("SKILL.md"))
        self.assertIn("write", draft["permissions"]["fs"])
        self.assertEqual(draft["review"]["status"], "draft")

    def test_generate_draft_from_id(self) -> None:
        draft = generate_draft(self.ws, capability_id="skill:chart-maker")
        self.assertEqual(draft["name"], "chart-maker")
        self.assertEqual(draft["version"], "2.0.0")
        self.assertEqual(draft["capabilities"], ["transform", "format"])

    def test_apply_draft_keeps_existing_fields(self) -> None:
        overlay = {
            "schema": "caprouter.capability-cards.v1",
            "cards": {"skill:chart-maker": {"examples": ["已有的示例"]}},
        }
        (self.ws / ".caprouter" / "capability_cards.json").write_text(
            json.dumps(overlay, ensure_ascii=False), encoding="utf-8"
        )
        draft = generate_draft(self.ws, capability_id="skill:chart-maker")
        result = apply_draft(self.ws, draft)
        self.assertIn("examples", result["kept"])
        saved = json.loads((self.ws / ".caprouter" / "capability_cards.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["cards"]["skill:chart-maker"]["examples"], ["已有的示例"])
        self.assertIn("probe", saved["cards"]["skill:chart-maker"])

    def test_apply_draft_refuses_unregistered_id(self) -> None:
        draft = generate_draft(self.ws, capability_id="skill:chart-maker")
        draft["id"] = "skill:not-registered"
        result = apply_draft(self.ws, draft)
        self.assertEqual(result["status"], "unreachable")
        self.assertFalse((self.ws / ".caprouter" / "capability_cards.json").exists())

    def test_apply_draft_force_overwrites(self) -> None:
        overlay = {
            "schema": "caprouter.capability-cards.v1",
            "cards": {"skill:chart-maker": {"examples": ["已有的示例"]}},
        }
        (self.ws / ".caprouter" / "capability_cards.json").write_text(
            json.dumps(overlay, ensure_ascii=False), encoding="utf-8"
        )
        draft = generate_draft(self.ws, capability_id="skill:chart-maker")
        result = apply_draft(self.ws, draft, force=True)
        self.assertIn("examples", result["updated"])
        saved = json.loads((self.ws / ".caprouter" / "capability_cards.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["cards"]["skill:chart-maker"]["examples"], draft["examples"])

    def test_project_metadata_prefers_prose_and_skips_setup(self) -> None:
        project = self.ws / "prompt-tool"
        project.mkdir()
        (project / "package.json").write_text(
            json.dumps({"name": "prompt-tool"}),
            encoding="utf-8",
        )
        (project / "README.md").write_text(
            "# Prompt Tool\n\n"
            "English | [中文](README.zh-CN.md)\n\n"
            "A local tool that optimizes prompts.\n\n"
            "## Usage\n"
            "- First fork the project\n"
            "- Then import the project to Vercel\n\n"
            "## Features\n"
            "- Optimize a prompt\n"
            "- Compare prompt variants\n",
            encoding="utf-8",
        )
        draft = generate_draft(self.ws, path=project)
        self.assertEqual(draft["description"], "A local tool that optimizes prompts.")
        self.assertIn("Optimize a prompt", draft["examples"])
        self.assertFalse(any(example.startswith(("First", "Then")) for example in draft["examples"]))

    def test_project_draft_does_not_hash_the_whole_tree(self) -> None:
        project = self.ws / "large-project"
        project.mkdir()
        (project / "package.json").write_text(
            json.dumps({"name": "large-project", "description": "A large local project"}),
            encoding="utf-8",
        )
        (project / "README.md").write_text("# Large\n\n## Usage\n- Run the large project\n", encoding="utf-8")
        (project / "big.bin").write_bytes(b"x" * 1024)
        with mock.patch("caprouter.cards.hash_tree", side_effect=AssertionError("tree hash")):
            draft = generate_draft(self.ws, path=project)
        self.assertEqual(draft["type"], "project")
        self.assertTrue(str(draft["provenance"]["hash"]).startswith("sha256:"))

    def test_batch_uses_registry_id_for_scanned_path(self) -> None:
        plugin = self.ws / "plugins" / "Demo-Plugin"
        (plugin / ".claude-plugin").mkdir(parents=True)
        (plugin / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "demo-plugin", "description": "Demo bundle"}),
            encoding="utf-8",
        )
        (self.ws / ".caprouter" / "registry.json").write_text(
            json.dumps(
                {
                    "entries": [
                        {
                            "id": "plugin:Demo-Plugin",
                            "type": "plugin",
                            "name": "Demo-Plugin",
                            "path": str(plugin),
                            "description": "Demo bundle",
                            "status": "scanned",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        report = generate_project_drafts(self.ws, [{"path": str(plugin), "type": "plugin"}])
        self.assertEqual(report["drafted"], 1)
        draft_file = Path(report["drafts"][0]["draft_path"])
        draft = json.loads(draft_file.read_text(encoding="utf-8"))
        self.assertEqual(draft["id"], "plugin:Demo-Plugin")

    def test_draft_signature_detects_domain_change(self) -> None:
        from caprouter.cards import _draft_signature

        draft = {"id": "project:x", "domain": "general", "generated_at": "a"}
        changed = {**draft, "domain": "writing", "generated_at": "b"}
        self.assertNotEqual(_draft_signature(draft), _draft_signature(changed))

    def test_batch_skips_verified_registry_entries(self) -> None:
        report = generate_project_drafts(
            self.ws,
            [{"path": str(self.skill), "type": "skill"}],
        )
        self.assertEqual(report["drafted"], 0)
        self.assertEqual(report["skipped"], 1)
        self.assertEqual(report["skipped_items"][0]["reason"], "already_registered")

    def test_write_draft(self) -> None:
        draft = generate_draft(self.ws, capability_id="skill:chart-maker")
        path = write_draft(self.ws, draft)
        self.assertTrue(path.is_file())
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["id"], "skill:chart-maker")

    def test_plugin_draft(self) -> None:
        plugin = self.ws / "plugins" / "demo-plugin"
        (plugin / ".claude-plugin").mkdir(parents=True)
        (plugin / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "demo-plugin", "version": "0.5.0", "description": "Bundle of demo commands"}),
            encoding="utf-8",
        )
        (plugin / "README.md").write_text("## Usage\n- 运行 demo 命令\n", encoding="utf-8")
        draft = generate_draft(self.ws, path=plugin)
        self.assertEqual(draft["type"], "plugin")
        self.assertEqual(draft["id"], "plugin:demo-plugin")
        self.assertEqual(draft["invoke"]["mode"], "prompt")
        self.assertTrue(draft["invoke"]["content_path"].endswith("demo-plugin"))


if __name__ == "__main__":
    unittest.main()
