from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

import sys

sys.path.insert(0, str(ROOT))

from occurrence_ledger import build_occurrence_ledger, prepare_occurrence_rows  # noqa: E402
from standalone_proofread import (  # noqa: E402
    _apply_reusable_rules,
    load_reusable_expected_rules,
    save_reusable_expected_rule,
)


RULES_PATH = ROOT / "可重用expected規則.json"
PDF_SHA256 = "b" * 64
APPROVED = {
    ("額頭", 1, "頭"): {
        "rule_id": "USER-EXPECTED-8e23e6643db8887ccabf",
        "expected_set": ["˙ㄊㄡ"],
        "evidence": "簡編本",
        "approved_at": "2026-08-25T13:49:46",
    },
    ("OK繃", 2, "繃"): {
        "rule_id": "USER-EXPECTED-04c50c1c3bf9f7d609b1",
        "expected_set": ["ㄅㄥ"],
        "evidence": "簡編本",
        "approved_at": "2026-08-25T13:54:44",
    },
    ("一會兒", 2, "兒"): {
        "rule_id": "USER-EXPECTED-6b8ac667a7d5fdb257db",
        "expected_set": ["ㄦ"],
        "evidence": "簡編本",
        "approved_at": "2026-08-25T14:20:35",
    },
}


def _rule_map(doc):
    return {
        (rule["phrase"], rule["target_index"], rule["target_char"]): rule
        for rule in doc["rules"]
    }


def _unresolved_entry(*, phrase: str, target_index: int, target_char: str, actual: str):
    row = {
        "pdf_sha256": PDF_SHA256,
        "pdf": "approved-rules.pdf",
        "pdf_name": "approved-rules.pdf",
        "實體頁碼": 1,
        "課本頁": 1,
        "字元": target_char,
        "實際注音": actual,
        "解碼依據": "independent actual fixture",
        "穩定注音鍵": f"Font#{target_char}",
        "font": "Font",
        "font_xref": 9,
        "glyph_id_字形索引": 23,
        "注音元件ID": 12,
        "x0": 10.0,
        "y0": 20.0,
        "x1": 30.0,
        "y1": 40.0,
        "所在行": phrase,
        "局部詞境": phrase,
        "行內字元位置": target_index,
        "source_row_number": 1,
    }
    prepare_occurrence_rows(PDF_SHA256, [row])
    classification = {
        row["occurrence_id"]: {
            "state": "EXPECTED_UNRESOLVED",
            "actual": actual,
            "actual_status": "RESOLVED",
            "actual_evidence": "independent actual fixture",
            "expected_status": "UNRESOLVED",
            "expected_set": [],
            "expected_evidence": "",
            "context_evidence": phrase,
            "comparison_result": "",
            "source_view": "expected unresolved",
            "source_record": row,
        }
    }
    return build_occurrence_ledger([row], classification)[0]


class ReusableExpectedRulePreservationTests(unittest.TestCase):
    def setUp(self):
        self.rules_doc = load_reusable_expected_rules(RULES_PATH)
        self.rules = _rule_map(self.rules_doc)

    def _resolve(self, phrase, target_index, target_char, actual):
        entry = _unresolved_entry(
            phrase=phrase,
            target_index=target_index,
            target_char=target_char,
            actual=actual,
        )
        return _apply_reusable_rules([entry], self.rules_doc)[0]

    def test_v561_approved_seed_rules_load_with_original_provenance(self):
        self.assertEqual(set(self.rules), set(APPROVED))
        self.assertEqual(len(self.rules_doc["rules"]), 3)
        for key, expected in APPROVED.items():
            rule = self.rules[key]
            for field, value in expected.items():
                self.assertEqual(rule[field], value)

    def test_etou_head_resolves_to_neutral_tone(self):
        result = self._resolve("額頭", 1, "頭", "ㄊㄡˊ")
        self.assertEqual(result["expected_set"], ["˙ㄊㄡ"])
        self.assertEqual(result["reusable_rule_id"], APPROVED[("額頭", 1, "頭")]["rule_id"])
        self.assertIn("簡編本", result["expected_evidence"])

    def test_ok_band_beng_resolves_to_first_tone(self):
        result = self._resolve("OK繃", 2, "繃", "ㄆㄥ")
        self.assertEqual(result["expected_set"], ["ㄅㄥ"])
        self.assertEqual(result["reusable_rule_id"], APPROVED[("OK繃", 2, "繃")]["rule_id"])
        self.assertIn("簡編本", result["expected_evidence"])

    def test_yihuir_er_resolves_to_er(self):
        result = self._resolve("一會兒", 2, "兒", "˙ㄦ")
        self.assertEqual(result["expected_set"], ["ㄦ"])
        self.assertEqual(result["reusable_rule_id"], APPROVED[("一會兒", 2, "兒")]["rule_id"])
        self.assertIn("簡編本", result["expected_evidence"])

    def test_repeated_save_keeps_existing_id_timestamp_and_stronger_evidence(self):
        original = self.rules[("OK繃", 2, "繃")]
        duplicate = dict(original)
        duplicate.update({
            "rule_id": "USER-EXPECTED-NEW-UNAPPROVED",
            "evidence": "我",
            "approved_at": "2099-01-01T00:00:00",
        })
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rules.json"
            path.write_bytes(RULES_PATH.read_bytes())
            before = path.read_bytes()
            saved = save_reusable_expected_rule(duplicate, path)
            after = load_reusable_expected_rules(path)
            self.assertEqual(path.read_bytes(), before)
        self.assertEqual(saved, original)
        self.assertEqual(len(after["rules"]), 3)
        self.assertEqual(_rule_map(after)[("OK繃", 2, "繃")], original)

    def test_saving_new_rule_preserves_all_existing_approved_rules(self):
        new_rule = {
            "rule_id": "USER-EXPECTED-PRESERVATION-TEST",
            "enabled": True,
            "phrase": "角色",
            "target_char": "角",
            "target_index": 0,
            "expected_set": ["ㄐㄩㄝˊ"],
            "evidence": "公司現行規定：角色",
            "context_contains": "",
            "scope": "same_phrase_position",
            "source": "人工核准可重用 expected 規則",
            "note": "",
            "approved_at": "2026-08-26T00:00:00",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rules.json"
            path.write_bytes(RULES_PATH.read_bytes())
            save_reusable_expected_rule(new_rule, path)
            after = load_reusable_expected_rules(path)
        after_rules = _rule_map(after)
        self.assertEqual(len(after["rules"]), 4)
        for key in APPROVED:
            self.assertEqual(after_rules[key], self.rules[key])

    def test_expected_resolution_is_independent_of_actual_reading(self):
        matching = self._resolve("OK繃", 2, "繃", "ㄅㄥ")
        differing = self._resolve("OK繃", 2, "繃", "ㄆㄥ")
        self.assertEqual(matching["actual"], "ㄅㄥ")
        self.assertEqual(differing["actual"], "ㄆㄥ")
        self.assertEqual(matching["expected_set"], ["ㄅㄥ"])
        self.assertEqual(differing["expected_set"], ["ㄅㄥ"])
        self.assertEqual(matching["reusable_rule_id"], differing["reusable_rule_id"])
        self.assertEqual(matching["expected_evidence"], differing["expected_evidence"])


if __name__ == "__main__":
    unittest.main()
