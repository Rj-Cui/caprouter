from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from caprouter.claims import sha256_file, verify_claims


class ClaimTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.snapshot = self.root / "snapshot.md"
        self.snapshot.write_text(
            "# Source\n\n**重要** 结论是 [A](https://example.com) 正确。\n",
            encoding="utf-8",
        )
        self.hash = sha256_file(self.snapshot)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_ledger(self, name: str, quote: str, snapshot_hash: str) -> Path:
        path = self.root / name
        path.write_text(
            json.dumps(
                {
                    "claims": [
                        {
                            "claim_id": "c1",
                            "source_id": "s1",
                            "quote": quote,
                            "snapshot_path": "snapshot.md",
                            "snapshot_hash": snapshot_hash,
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return path

    def test_markdown_normalized_quote_is_supported(self) -> None:
        ledger = self.write_ledger("ok.json", "重要 结论是 A 正确", self.hash)
        result = verify_claims(ledger)
        self.assertEqual(result["counts"]["supported"], 1)

    def test_hash_mismatch(self) -> None:
        ledger = self.write_ledger("bad.json", "重要 结论是 A 正确", self.hash)
        self.snapshot.write_text("changed", encoding="utf-8")
        result = verify_claims(ledger)
        self.assertEqual(result["counts"]["hash_mismatch"], 1)

    def test_quote_not_found(self) -> None:
        ledger = self.write_ledger("missing.json", "不存在的句子", self.hash)
        result = verify_claims(ledger)
        self.assertEqual(result["counts"]["not_found"], 1)

    def test_missing_hash(self) -> None:
        ledger = self.write_ledger("nohash.json", "重要 结论是 A 正确", "")
        result = verify_claims(ledger)
        self.assertEqual(result["counts"]["missing_hash"], 1)


if __name__ == "__main__":
    unittest.main()
