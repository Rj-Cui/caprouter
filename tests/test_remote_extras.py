from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from caprouter.remote import discover_skills, fetch_url, vendor_skill
from caprouter.workspace import create_workspace

SEARCH_DATA = {
    "skills": [
        {
            "id": "example/skills/demo",
            "source": "example/skills",
            "skillId": "demo",
            "name": "demo",
            "description": "A demo skill",
            "license": "MIT",
            "installs": 42,
        }
    ]
}


def make_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "SKILL.md",
            "---\nname: demo\ndescription: URL demo\n---\n# Demo\n",
        )
        archive.writestr(
            "LICENSE",
            "MIT License\n\nPermission is hereby granted, free of charge, to any person "
            "obtaining a copy of this software.\n",
        )
    return buffer.getvalue()


class RemoteExtrasTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.ws = base / "ws"
        create_workspace(self.ws, include_agents=False, include_default_roots=False)
        self.env = patch.dict(
            os.environ,
            {
                "CAPROUTER_STATE": str(base / "state.json"),
                "CAPROUTER_AUDIT": str(base / "audit.jsonl"),
            },
        )
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def test_discover_metadata_and_offline_cache(self) -> None:
        with patch("caprouter.remote._http_json", return_value=SEARCH_DATA) as http:
            online = discover_skills(self.ws, "demo", allow_network=True)
            self.assertEqual(http.call_count, 1)
        candidate = online["candidates"][0]
        self.assertEqual(candidate["description"], "A demo skill")
        self.assertEqual(candidate["license"], "MIT")

        with patch("caprouter.remote._http_json", side_effect=AssertionError("network used")):
            cached = discover_skills(self.ws, "demo", offline=True)
        self.assertEqual(cached["status"], "cached")
        self.assertTrue(cached["offline"])
        self.assertEqual(cached["candidates"][0]["id"], "example/skills/demo")

    def test_offline_without_cache_raises(self) -> None:
        with self.assertRaises(ValueError):
            discover_skills(self.ws, "never-seen", offline=True)

    def test_fetch_url_quarantine_and_vendor(self) -> None:
        payload = make_zip()
        with patch(
            "caprouter.remote._http_bytes",
            return_value=(payload, "https://github.com/example/repo/archive.zip"),
        ):
            result = fetch_url(
                self.ws,
                "https://github.com/example/repo/archive.zip",
                allow_network=True,
            )
        self.assertEqual(result["status"], "quarantined")
        self.assertEqual(result["license"]["name"], "MIT")
        quarantine = Path(result["quarantine_dir"])
        provenance = json.loads((quarantine / "provenance.json").read_text(encoding="utf-8"))
        self.assertEqual(provenance["source"], "url")
        self.assertTrue(provenance["content_hash"].startswith("sha256:"))

        vendored = vendor_skill(self.ws, quarantine_path=quarantine)
        self.assertEqual(vendored["status"], "vendored")
        self.assertTrue(Path(vendored["card_draft_path"]).is_file())
        self.assertEqual(vendored["card_id"], "skill:demo")

    def test_fetch_url_blocks_unknown_host(self) -> None:
        with patch("caprouter.remote._http_bytes", side_effect=AssertionError("network used")):
            with self.assertRaisesRegex(ValueError, "blocked remote host"):
                fetch_url(self.ws, "https://evil.example/x.zip", allow_network=True)

    def test_fetch_url_requires_network_flag(self) -> None:
        with self.assertRaises(ValueError):
            fetch_url(self.ws, "https://github.com/example/repo/archive.zip")


if __name__ == "__main__":
    unittest.main()
