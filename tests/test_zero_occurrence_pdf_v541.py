from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from check_pronunciation_candidates import _build_candidate_ledger  # noqa: E402
from standalone_proofread import (  # noqa: E402
    _canonical_rule_doc,
    _parse_ledger_rows,
    materialize_ledger,
    normalize_db,
    reusable_rules_sha256,
)
from occurrence_ledger import completion_gate, reconcile_ledger, PROCESSING_FINISHED  # noqa: E402


class ZeroOccurrencePdfV541Tests(unittest.TestCase):
    def test_candidate_builder_accepts_truly_empty_pdf(self):
        self.assertEqual(_build_candidate_ledger([], {}), [])

    def test_candidate_builder_does_not_hide_orphan_classification(self):
        with self.assertRaisesRegex(ValueError, "classification 非空"):
            _build_candidate_ledger([], {"occ_orphan": {"state": "PASS"}})

    def test_candidate_parser_accepts_header_only_ledger_sheet(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "empty_candidate.xlsx"
            wb = Workbook()
            ws = wb.active
            ws.title = "Occurrence Ledger"
            ws.append([
                "ledger_schema_version", "review_id_schema_version", "occurrence_id", "review_id",
                "active_review", "state", "actual_status", "expected_status", "comparison_result",
                "blocking_state", "exclusion_state", "pdf_sha256", "expected_set_json",
                "confirmation_gates_json", "source_record_json",
            ])
            wb.save(path)
            wb.close()
            self.assertEqual(_parse_ledger_rows(path), [])

    def test_all_empty_batch_materializes_without_fatal_error(self):
        rules_doc = _canonical_rule_doc({})
        manifest = {
            "records": [],
            "reusable_expected_rules": rules_doc,
            "reusable_expected_rules_sha256": reusable_rules_sha256(rules_doc),
        }
        self.assertEqual(materialize_ledger(manifest, normalize_db({})), [])

    def test_empty_batch_is_not_declared_complete(self):
        reconciliation = reconcile_ledger([], [])
        regression = {
            "ok": True, "required": 1, "executed": 1, "failed": 0,
            "not_executed": 0, "duplicate_case_id": 0,
        }
        gate = completion_gate([], reconciliation, regression)
        self.assertEqual(gate["status"], PROCESSING_FINISHED)
        self.assertFalse(gate["complete"])
        self.assertIn("in_scope_occurrence_count_positive", gate["failed_gates"])


if __name__ == "__main__":
    unittest.main()
