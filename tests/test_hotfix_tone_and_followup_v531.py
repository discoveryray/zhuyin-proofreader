from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from occurrence_ledger import canonical_bopomofo, normalize_expected_set  # noqa: E402
from review_gui import ReviewApp  # noqa: E402


class TonePositionCanonicalizationTests(unittest.TestCase):
    def test_neutral_tone_front_and_back_are_equal(self):
        self.assertEqual(canonical_bopomofo("˙ㄒㄧ"), "˙ㄒㄧ")
        self.assertEqual(canonical_bopomofo("ㄒㄧ˙"), "˙ㄒㄧ")
        self.assertEqual(normalize_expected_set("˙ㄒㄧ|ㄒㄧ˙"), ("˙ㄒㄧ",))

    def test_non_neutral_tone_front_and_back_are_equal(self):
        pairs = [
            ("ˊㄒㄧ", "ㄒㄧˊ"),
            ("ˇㄒㄧ", "ㄒㄧˇ"),
            ("ˋㄒㄧ", "ㄒㄧˋ"),
        ]
        for front, canonical in pairs:
            self.assertEqual(canonical_bopomofo(front), canonical)
            self.assertEqual(canonical_bopomofo(canonical), canonical)

    def test_malformed_tone_placement_fails_closed(self):
        for value in ("ˊㄒㄧˋ", "ㄒˋㄧ", "˙ㄒㄧˇ"):
            self.assertEqual(canonical_bopomofo(value), "", value)

    def test_erhua_and_legacy_duplicate_tone_are_source_compatible(self):
        self.assertEqual(canonical_bopomofo("ㄉㄧㄢˇㄦ"), "ㄉㄧㄢˇㄦ")
        self.assertEqual(canonical_bopomofo("ˇㄉㄧㄢㄦ"), "ㄉㄧㄢˇㄦ")
        self.assertEqual(canonical_bopomofo("ㄉㄧㄢㄦˇ"), "ㄉㄧㄢˇㄦ")
        self.assertEqual(canonical_bopomofo("ㄌㄧㄤˋˋ"), "ㄌㄧㄤˋ")

    def test_tone_kind_still_matters(self):
        self.assertNotEqual(canonical_bopomofo("ㄒㄧ"), canonical_bopomofo("ㄒㄧ˙"))
        self.assertNotEqual(canonical_bopomofo("ㄒㄧˊ"), canonical_bopomofo("ㄒㄧˋ"))


class ExpectedResolutionFollowupTests(unittest.TestCase):
    def _make_app(self, updated_entry):
        app = object.__new__(ReviewApp)
        app.root = None
        original = {
            "review_id": "RID-1",
            "occurrence_id": "OID-1",
            "state": "DIFFERENCE_PENDING_CONFIRMATION",
            "char": "西",
            "actual": "ㄒㄧ",
            "source_record": {},
        }
        app.current = lambda: original
        app.records = [updated_entry] if updated_entry is not None else []

        def save_event(_entry, _event):
            app._last_event_saved = True
            app.records = [updated_entry] if updated_entry is not None else []

        app.save_event = save_event
        app._confirm_calls = []
        app._confirm_difference_entry = lambda entry: app._confirm_calls.append(entry)
        return app

    def test_expected_still_mismatch_warns_but_does_not_auto_open_six_gate(self):
        updated = {
            "review_id": "RID-1",
            "state": "DIFFERENCE_PENDING_CONFIRMATION",
            "actual": "ㄒㄧ",
            "expected_set": ["˙ㄒㄧ"],
        }
        app = self._make_app(updated)
        dialog_result = {
            "expected_set": "˙ㄒㄧ",
            "expected_evidence": "《國語辭典簡編本》完整詞條",
            "context_evidence": "東西／西",
            "resolution_reason": "",
            "promote_rule": False,
            "rule_phrase": "",
        }
        with patch("review_gui.ExpectedDialog") as dialog_cls, patch("review_gui.messagebox.showwarning") as warning:
            dialog_cls.return_value.result = dialog_result
            app.resolve_expected()
        self.assertEqual(app._confirm_calls, [])
        warning.assert_called_once()
        self.assertIn("ㄒㄧ", warning.call_args.args[1])
        self.assertIn("˙ㄒㄧ", warning.call_args.args[1])

    def test_expected_match_does_not_open_six_gate_followup(self):
        updated = None  # PASS rows disappear from the pending list after save_event().
        app = self._make_app(updated)
        dialog_result = {
            "expected_set": "ㄒㄧ",
            "expected_evidence": "獨立來源",
            "context_evidence": "西／西",
            "resolution_reason": "",
            "promote_rule": False,
            "rule_phrase": "",
        }
        with patch("review_gui.ExpectedDialog") as dialog_cls:
            dialog_cls.return_value.result = dialog_result
            app.resolve_expected()
        self.assertEqual(app._confirm_calls, [])


if __name__ == "__main__":
    unittest.main()
