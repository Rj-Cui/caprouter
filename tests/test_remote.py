from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from caprouter.remote import (
    _http_json,
    clean_quarantine,
    discover_skills,
    fetch_skill,
    list_quarantine,
    quarantine_dir,
    vendor_skill,
)

SEARCH_DATA = {
    "skills": [
        {
            "id": "anthropics/skills/pdf",
            "source": "anthropics/skills",
            "skillId": "pdf",
            "name": "pdf",
            "installs": 235455,
        }
    ]
}

DOWNLOAD_DATA = {
    "hash": "sha256:remote",
    "files": [
        {
            "path": "SKILL.md",
            "contents": (
                "---\nname: pdf\ndescription: Work with PDF files\n"
                "keywords: [pdf, form]\ncapabilities: [transform]\n---\n"
                "## Workflow\n1. Read the PDF\n2. Fill the form\n"
                "MUST preserve pages\n"
            ),
        },
        {"path": "scripts/tool.py", "contents": "print('not executed')\n"},
        {
            "path": "LICENSE.txt",
            "contents": "MIT License\n\nPermission is hereby granted, free of charge, to any person obtaining a copy.\n",
        },
    ],
}


class RemoteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)
        bus = self.ws / ".caprouter"
        bus.mkdir()
        (bus / "config.json").write_text(
            json.dumps({"schema": "caprouter.config.v1", "routing": {}}), encoding="utf-8"
        )
        (bus / "registry.json").write_text(json.dumps({"entries": []}), encoding="utf-8")
        self.state = self.ws / "state.json"
        self.state.write_text(
            json.dumps({"schema": "caprouter.state.v1", "enabled": True}), encoding="utf-8"
        )
        self.audit = self.ws / "audit.jsonl"
        self.env = patch.dict(
            os.environ,
            {"CAPROUTER_STATE": str(self.state), "CAPROUTER_AUDIT": str(self.audit)},
        )
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def _fake_http(self, data: dict):
        return patch("caprouter.remote._http_json", return_value=data)

    def test_discover_requires_network_opt_in(self) -> None:
        with self.assertRaises(ValueError):
            discover_skills(self.ws, "pdf")

    def test_discover_requires_enabled_state(self) -> None:
        self.state.write_text(
            json.dumps({"schema": "caprouter.state.v1", "enabled": False}), encoding="utf-8"
        )
        with self.assertRaises(ValueError):
            discover_skills(self.ws, "pdf", allow_network=True)

    def test_discover_writes_candidates(self) -> None:
        with self._fake_http(SEARCH_DATA):
            result = discover_skills(self.ws, "pdf", allow_network=True)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["candidates"][0]["id"], "anthropics/skills/pdf")
        log = (self.ws / ".caprouter" / "discovery.jsonl").read_text(encoding="utf-8")
        self.assertIn("anthropics/skills/pdf", log)

    def test_discover_offset_slices_locally(self) -> None:
        data = {
            "skills": [
                {"id": "a/one/s1", "source": "a/one", "skillId": "s1", "name": "s1", "installs": 3},
                {"id": "b/two/s2", "source": "b/two", "skillId": "s2", "name": "s2", "installs": 2},
                {"id": "c/three/s3", "source": "c/three", "skillId": "s3", "name": "s3", "installs": 1},
            ]
        }
        with self._fake_http(data):
            result = discover_skills(self.ws, "x", limit=1, offset=1, allow_network=True)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["candidates"][0]["id"], "b/two/s2")
        self.assertEqual(result["next_offset"], 2)

    def test_discover_rejects_offset_beyond_limit(self) -> None:
        with self.assertRaises(ValueError):
            discover_skills(self.ws, "x", offset=100, allow_network=True)

    def test_fetch_requires_network_opt_in(self) -> None:
        with self.assertRaises(ValueError):
            fetch_skill(self.ws, "anthropics/skills/pdf")

    def test_fetch_writes_quarantine_without_installing(self) -> None:
        with self._fake_http(DOWNLOAD_DATA):
            result = fetch_skill(self.ws, "anthropics/skills/pdf", allow_network=True)
        dest = Path(result["quarantine_dir"])
        self.assertTrue((dest / "SKILL.md").is_file())
        self.assertTrue((dest / "scripts" / "tool.py").is_file())
        provenance = json.loads((dest / "provenance.json").read_text(encoding="utf-8"))
        self.assertEqual(provenance["status"], "quarantined")
        self.assertEqual(provenance["candidate_id"], "anthropics/skills/pdf")
        self.assertEqual(provenance["license"]["name"], "MIT")
        self.assertEqual(result["file_count"], 3)
        self.assertFalse((self.ws / ".caprouter" / "installed" / "skills" / "pdf").exists())
        self.assertEqual(
            json.loads((self.ws / ".caprouter" / "registry.json").read_text(encoding="utf-8"))["entries"],
            [],
        )

    def test_fetch_rejects_path_traversal(self) -> None:
        bad = {
            "files": [
                {"path": "../evil.txt", "contents": "x"},
            ]
        }
        with self._fake_http(bad):
            with self.assertRaises(ValueError):
                fetch_skill(self.ws, "anthropics/skills/evil", allow_network=True)
        self.assertFalse((quarantine_dir(self.ws) / "anthropics-skills" / "evil").exists())

    def test_fetch_requires_force_to_replace(self) -> None:
        with self._fake_http(DOWNLOAD_DATA):
            fetch_skill(self.ws, "anthropics/skills/pdf", allow_network=True)
            with self.assertRaises(ValueError):
                fetch_skill(self.ws, "anthropics/skills/pdf", allow_network=True)
            forced = fetch_skill(self.ws, "anthropics/skills/pdf", allow_network=True, force=True)
        self.assertEqual(forced["status"], "quarantined")

    def test_list_quarantine_after_fetch(self) -> None:
        with self._fake_http(DOWNLOAD_DATA):
            fetch_skill(self.ws, "anthropics/skills/pdf", allow_network=True)
        data = list_quarantine(self.ws)
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["items"][0]["candidate_id"], "anthropics/skills/pdf")
        self.assertEqual(data["items"][0]["file_count"], 3)
        self.assertEqual(data["items"][0]["license"]["name"], "MIT")
        self.assertFalse(data["items"][0]["has_deconstruction"])

    def test_clean_preview_does_not_delete(self) -> None:
        with self._fake_http(DOWNLOAD_DATA):
            result = fetch_skill(self.ws, "anthropics/skills/pdf", allow_network=True)
        dest = Path(result["quarantine_dir"])
        preview = clean_quarantine(self.ws, candidate="anthropics/skills/pdf", dry_run=True)
        self.assertEqual(preview["status"], "preview")
        self.assertTrue(dest.is_dir())

    def test_clean_candidate_removes_only_target(self) -> None:
        with self._fake_http(DOWNLOAD_DATA):
            pdf = fetch_skill(self.ws, "anthropics/skills/pdf", allow_network=True)
            docx = fetch_skill(self.ws, "anthropics/skills/docx", allow_network=True)
        pdf_dir = Path(pdf["quarantine_dir"])
        docx_dir = Path(docx["quarantine_dir"])
        result = clean_quarantine(self.ws, candidate="anthropics/skills/pdf", dry_run=False)
        self.assertEqual(result["status"], "cleaned")
        self.assertFalse(pdf_dir.exists())
        self.assertTrue(docx_dir.is_dir())

    def test_clean_requires_selector(self) -> None:
        with self.assertRaises(ValueError):
            clean_quarantine(self.ws)

    def test_clean_rejects_path_traversal(self) -> None:
        with self.assertRaises(ValueError):
            clean_quarantine(self.ws, candidate="../x/y", dry_run=True)

    def test_vendor_requires_quarantine(self) -> None:
        with self.assertRaises(ValueError):
            vendor_skill(self.ws, "anthropics/skills/pdf")

    def test_vendor_writes_report_and_card_draft(self) -> None:
        with self._fake_http(DOWNLOAD_DATA):
            fetch_skill(self.ws, "anthropics/skills/pdf", allow_network=True)
        result = vendor_skill(self.ws, "anthropics/skills/pdf")
        dest = Path(result["quarantine_dir"])
        report = (dest / "deconstruction.md").read_text(encoding="utf-8")
        draft = json.loads((dest / "card_draft.json").read_text(encoding="utf-8"))
        self.assertIn("## Step 1 — Delivery", report)
        self.assertIn("## Step 6 — Principle", report)
        self.assertIn("## License", report)
        self.assertIn("MIT", report)
        self.assertIn("not executed", report.lower())
        self.assertEqual(draft["id"], "skill:pdf")
        self.assertEqual(draft["provenance"]["candidate_id"], "anthropics/skills/pdf")
        self.assertEqual(draft["provenance"]["license"]["name"], "MIT")
        self.assertEqual(draft["invoke"]["mode"], "prompt")
        self.assertFalse((self.ws / ".caprouter" / "drafts" / "skill-pdf.json").exists())


class HttpGuardTest(unittest.TestCase):
    def test_redirect_to_foreign_host_is_blocked(self) -> None:
        class FakeResponse:
            headers: dict = {}

            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, *args: object) -> bool:
                return False

            def geturl(self) -> str:
                return "https://evil.example/collect"

            def read(self, size: int = -1) -> bytes:
                return b"{}"

        with patch("caprouter.remote._throttle", return_value=None), patch(
            "caprouter.remote.urllib.request.urlopen", return_value=FakeResponse()
        ):
            with self.assertRaisesRegex(ValueError, "redirected to blocked host"):
                _http_json("https://skills.sh/api/search")


if __name__ == "__main__":
    unittest.main()
