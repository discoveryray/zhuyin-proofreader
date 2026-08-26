from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from actual_review import (
    USER_GLYF_FILE,
    USER_CFF_FILE,
    OCCURRENCE_OVERRIDE_FILE,
    USER_GLYF_HEADERS,
    USER_CFF_HEADERS,
    OVERRIDE_HEADERS,
    build_actual_review_groups,
    dynamic_actual_hashes,
    ensure_user_evidence_files,
)
from export_zhuyin_readings import match_actual_occurrence_override


SHA_A = "a" * 64
SHA_B = "b" * 64


def write_csv(path: Path, headers, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=headers)
        w.writeheader()
        w.writerows(rows)


def ledger_entry(oid: str, state: str, pdf_name: str, sha: str):
    return {
        "occurrence_id": oid,
        "review_id": "r_" + oid,
        "state": state,
        "pdf_name": pdf_name,
        "physical_page": 1,
        "printed_page": "1",
        "char": "字",
        "stable_key": "key-" + oid,
        "x0": 10.0,
        "y0": 20.0,
        "actual": "",
        "actual_evidence": "test",
        "source_record": {"TTF字形SHA256": sha, "穩定注音鍵": "key-" + oid},
    }


class IncrementalActualV541Tests(unittest.TestCase):
    def test_scoped_dynamic_hash_ignores_unrelated_pdf_and_glyph_truth(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ensure_user_evidence_files(root)
            pdf_a = root / "bookA.pdf"
            pdf_b = root / "bookB.pdf"
            pdf_a.write_bytes(b"A")
            pdf_b.write_bytes(b"B")
            write_csv(root / OCCURRENCE_OVERRIDE_FILE, OVERRIDE_HEADERS, [
                {"pdf_contains":"bookA","pdf_excludes":"","page":"1","target_char":"甲","stable_key":"kA","x0":"1","y0":"2","actual_reading":"ㄅ","source":"t","note":""},
                {"pdf_contains":"bookB","pdf_excludes":"","page":"1","target_char":"乙","stable_key":"kB","x0":"3","y0":"4","actual_reading":"ㄆ","source":"t","note":""},
            ])
            write_csv(root / USER_GLYF_FILE, USER_GLYF_HEADERS, [
                {"glyph_sha256":SHA_A,"bopomofo":"ㄅ","verification_level":"VERIFIED_EXACT_GLYPH","source_count":"2","source_examples":"a1|a2","updated_at":"x","notes":""},
                {"glyph_sha256":SHA_B,"bopomofo":"ㄆ","verification_level":"VERIFIED_EXACT_GLYPH","source_count":"2","source_examples":"b1|b2","updated_at":"x","notes":""},
            ])
            deps_a = {"ttf_glyph_sha256":[SHA_A], "cff_glyph_keys":[]}
            first = dynamic_actual_hashes(root, pdf_path=pdf_a, dependencies=deps_a)

            # Change only bookB / SHA_B evidence.  bookA's scoped fingerprint
            # must remain identical even though global dynamic evidence changes.
            write_csv(root / OCCURRENCE_OVERRIDE_FILE, OVERRIDE_HEADERS, [
                {"pdf_contains":"bookA","pdf_excludes":"","page":"1","target_char":"甲","stable_key":"kA","x0":"1","y0":"2","actual_reading":"ㄅ","source":"t","note":""},
                {"pdf_contains":"bookB","pdf_excludes":"","page":"1","target_char":"乙","stable_key":"kB","x0":"3","y0":"4","actual_reading":"ㄇ","source":"t2","note":"changed"},
            ])
            write_csv(root / USER_GLYF_FILE, USER_GLYF_HEADERS, [
                {"glyph_sha256":SHA_A,"bopomofo":"ㄅ","verification_level":"VERIFIED_EXACT_GLYPH","source_count":"2","source_examples":"a1|a2","updated_at":"x","notes":""},
                {"glyph_sha256":SHA_B,"bopomofo":"ㄇ","verification_level":"VERIFIED_EXACT_GLYPH","source_count":"2","source_examples":"b1|b2","updated_at":"y","notes":"changed"},
            ])
            second = dynamic_actual_hashes(root, pdf_path=pdf_a, dependencies=deps_a)
            self.assertEqual(first, second)

    def test_pending_seed_expands_to_nonpending_same_exact_glyph(self):
        a = ledger_entry("o1", "ACTUAL_DECODE_ERROR", "a.pdf", SHA_A)
        b = ledger_entry("o2", "PASS", "b.pdf", SHA_A)
        groups = build_actual_review_groups([a, b])
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["occurrence_count"], 2)
        self.assertEqual({m["occurrence_id"] for m in groups[0]["members"]}, {"o1", "o2"})

    def test_occurrence_override_accepts_physical_page_and_tight_key_drift(self):
        row = {
            "課本頁": "136",
            "實體頁碼": 5,
            "字元": "西",
            "穩定注音鍵": "new-key",
            "x0": 496.134,
            "y0": 179.295,
        }
        ov = {
            "page": "5",  # legacy/manual data may have stored physical page
            "target_char": "西",
            "stable_key": "old-key",  # decoder upgrade changed the key
            "x0": 496.13,
            "y0": 179.30,
            "actual_reading": "˙ㄒㄧ",
        }
        self.assertIs(match_actual_occurrence_override(row, [ov]), ov)

    def test_occurrence_override_does_not_relax_key_when_coordinates_are_not_exact(self):
        row = {
            "課本頁": "136", "實體頁碼": 5, "字元": "西",
            "穩定注音鍵": "new-key", "x0": 496.134, "y0": 179.295,
        }
        ov = {
            "page": "5", "target_char": "西", "stable_key": "old-key",
            "x0": 496.60, "y0": 179.60, "actual_reading": "˙ㄒㄧ",
        }
        self.assertIsNone(match_actual_occurrence_override(row, [ov]))


if __name__ == "__main__":
    unittest.main()
