from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from caprouter.capabilities import load_overlay
from caprouter.cards import entry_hash
from caprouter.migrate import export_bundle, import_bundle
from caprouter.util import write_json_atomic


def _workspace(tmp: Path, name: str, project_id: str, project_name: str, package_name: str, card: bool = True) -> tuple[Path, Path]:
    workspace = tmp / name
    bus = workspace / ".caprouter"
    bus.mkdir(parents=True)
    project = tmp / f"{name}-project" / project_name
    project.mkdir(parents=True)
    (project / "package.json").write_text(
        json.dumps({"name": package_name, "description": "Convert documents"}),
        encoding="utf-8",
    )
    entry = {
        "id": project_id,
        "type": "project",
        "name": project_name,
        "path": str(project),
        "description": "Convert documents",
        "domain": "office",
        "capabilities": ["transform"],
        "keywords": ["convert"],
        "hash": entry_hash(project, "project"),
        "status": "scanned",
    }
    write_json_atomic(
        bus / "config.json",
        {
            "schema": "caprouter.config.v1",
            "skill_roots": [],
            "plugin_roots": [],
            "scan_agents": False,
            "scan": {"project_roots": []},
            "routing": {},
        },
    )
    write_json_atomic(bus / "registry.json", {"entries": [entry]})
    if card:
        write_json_atomic(
            bus / "capability_cards.json",
            {
                "schema": "caprouter.capability-cards.v1",
                "defaults": {},
                "cards": {
                    project_id: {
                        "description": "Convert documents",
                        "capabilities": ["transform"],
                        "examples": ["Convert a PDF to Word"],
                        "permissions": {"fs": ["read"], "net": False, "shell": False},
                        "invoke": {"mode": "none", "allow": False},
                    }
                },
            },
        )
        (bus / "probes.jsonl").write_text(
            json.dumps({"capability_id": project_id, "status": "pass", "ts": "2026-01-01T00:00:00+08:00"}) + "\n",
            encoding="utf-8",
        )
    return workspace, project


class MigrationTests(unittest.TestCase):
    def test_export_import_matches_by_manifest_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, _source_project = _workspace(root, "source", "project:demo", "demo", "demo-project")
            target, _target_project = _workspace(root, "target", "project:demo-copy", "demo-copy", "demo-project", card=False)
            bundle = root / "bundle.zip"
            exported = export_bundle(source, bundle)
            self.assertTrue(bundle.is_file())
            self.assertEqual(exported["counts"]["cards"], 1)

            report = import_bundle(target, bundle, scan=False)
            overlay = load_overlay(target)
        self.assertEqual(report["matched"], 1)
        self.assertEqual(report["applied"], 1)
        self.assertIn("project:demo-copy", overlay["cards"])
        self.assertEqual(report["transferred"]["probes"], 1)

    def test_unmatched_card_becomes_migrated_draft(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, _source_project = _workspace(root, "source", "project:demo", "demo", "demo-project")
            target, _target_project = _workspace(root, "target", "project:other", "other", "other-project", card=False)
            bundle = root / "bundle.zip"
            export_bundle(source, bundle)
            report = import_bundle(target, bundle, scan=False)
            overlay = load_overlay(target)
            drafts = list((target / ".caprouter" / "drafts" / "migrated").glob("*.json"))
        self.assertEqual(report["unmatched"], 1)
        self.assertEqual(len(drafts), 1)
        self.assertNotIn("project:demo", overlay["cards"])

    def test_no_apply_writes_migrated_ready_draft(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, _source_project = _workspace(root, "source", "project:demo", "demo", "demo-project")
            target, _target_project = _workspace(root, "target", "project:demo-copy", "demo-copy", "demo-project", card=False)
            bundle = root / "bundle.zip"
            export_bundle(source, bundle)
            report = import_bundle(target, bundle, scan=False, apply_matched=False)
            overlay = load_overlay(target)
            drafts = list((target / ".caprouter" / "drafts" / "migrated").glob("*.json"))
            status = json.loads(drafts[0].read_text(encoding="utf-8"))["review"]["status"]
        self.assertEqual(report["applied"], 0)
        self.assertNotIn("project:demo-copy", overlay["cards"])
        self.assertEqual(status, "migrated_ready")

    def test_import_is_idempotent_for_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, _source_project = _workspace(root, "source", "project:demo", "demo", "demo-project")
            target, _target_project = _workspace(root, "target", "project:demo", "demo", "demo-project", card=False)
            bundle = root / "bundle.zip"
            export_bundle(source, bundle)
            first = import_bundle(target, bundle, scan=False)
            second = import_bundle(target, bundle, scan=False)
            rows = (target / ".caprouter" / "probes.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(first["transferred"]["probes"], 1)
        self.assertTrue(second["already_imported"])
        self.assertEqual(len([row for row in rows if row.strip()]), 1)

    def test_dry_run_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, _source_project = _workspace(root, "source", "project:demo", "demo", "demo-project")
            target, _target_project = _workspace(root, "target", "project:demo", "demo", "demo-project", card=False)
            bundle = root / "bundle.zip"
            export_bundle(source, bundle)
            before = (target / ".caprouter" / "registry.json").read_text(encoding="utf-8")
            report = import_bundle(target, bundle, scan=False, dry_run=True)
            after = (target / ".caprouter" / "registry.json").read_text(encoding="utf-8")
            overlay_exists = (target / ".caprouter" / "capability_cards.json").exists()
        self.assertTrue(report["dry_run"])
        self.assertEqual(before, after)
        self.assertFalse(overlay_exists)

    def test_rejects_path_traversal_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target, _project = _workspace(root, "target", "project:demo", "demo", "demo-project", card=False)
            bundle = root / "evil.zip"
            with zipfile.ZipFile(bundle, "w") as archive:
                archive.writestr("../evil.txt", "x")
            with self.assertRaises(ValueError):
                import_bundle(target, bundle, scan=False)

    def test_metadata_hash_is_path_independent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "one" / "demo"
            second = root / "two" / "demo"
            for project in (first, second):
                project.mkdir(parents=True)
                (project / "package.json").write_text(
                    json.dumps({"name": "demo-project", "description": "Convert documents"}),
                    encoding="utf-8",
                )
            self.assertEqual(entry_hash(first, "project"), entry_hash(second, "project"))

    def test_id_collision_does_not_apply_wrong_card(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, _source_project = _workspace(root, "source", "project:demo", "demo", "demo-project")
            target, _target_project = _workspace(root, "target", "project:demo", "other", "other-project", card=False)
            bundle = root / "bundle.zip"
            export_bundle(source, bundle)
            report = import_bundle(target, bundle, scan=False)
            overlay = load_overlay(target)
        self.assertEqual(report["unmatched"], 1)
        self.assertEqual(report["applied"], 0)
        self.assertEqual(overlay["cards"], {})

    def test_unsafe_command_card_is_not_applied_from_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, _source_project = _workspace(root, "source", "project:demo", "demo", "demo-project")
            overlay_path = source / ".caprouter" / "capability_cards.json"
            overlay = json.loads(overlay_path.read_text(encoding="utf-8"))
            overlay["cards"]["project:demo"]["invoke"] = {
                "mode": "command",
                "allow": True,
                "command": ["echo", "x"],
            }
            write_json_atomic(overlay_path, overlay)
            target, _target_project = _workspace(root, "target", "project:demo-copy", "demo-copy", "demo-project", card=False)
            bundle = root / "bundle.zip"
            export_bundle(source, bundle)
            report = import_bundle(target, bundle, scan=False)
            target_overlay = load_overlay(target)
            drafts = list((target / ".caprouter" / "drafts" / "migrated").glob("*.json"))
            status = json.loads(drafts[0].read_text(encoding="utf-8"))["review"]["status"]
        self.assertEqual(report["applied"], 0)
        self.assertEqual(report["unsafe_not_applied"], 1)
        self.assertEqual(target_overlay["cards"], {})
        self.assertEqual(status, "migrated_unsafe")


if __name__ == "__main__":
    unittest.main()
