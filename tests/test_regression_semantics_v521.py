from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from check_pronunciation_candidates import evaluate_historical_regression  # noqa: E402
from standalone_proofread import _aggregate_pdf_regression_rows  # noqa: E402


class HistoricalRegressionSemanticsTests(unittest.TestCase):
    def test_former_error_corrected_now_passes(self):
        rec = {"實際注音": "˙ㄒㄧ", "預期注音": "˙ㄒㄧ"}
        rg = {"status": "確認錯誤", "expected_reading": "˙ㄒㄧ"}
        result, note = evaluate_historical_regression(rec, rg)
        self.assertEqual(result, "PASS")
        self.assertIn("已於現版修正", note)

    def test_former_error_still_wrong_but_currently_detected_passes(self):
        rec = {"實際注音": "ㄒㄧ", "預期注音": "˙ㄒㄧ"}
        rg = {"status": "確認錯誤", "expected_reading": "˙ㄒㄧ"}
        result, note = evaluate_historical_regression(rec, rg)
        self.assertEqual(result, "PASS")
        self.assertIn("獨立檢出差異", note)

    def test_former_error_hidden_by_wrong_current_expected_fails(self):
        rec = {"實際注音": "ㄒㄧ", "預期注音": "ㄒㄧ"}
        rg = {"status": "確認錯誤", "expected_reading": "˙ㄒㄧ"}
        result, _ = evaluate_historical_regression(rec, rg)
        self.assertEqual(result, "FAIL")

    def test_historical_correct_control_must_remain_currently_correct(self):
        rec = {"實際注音": "˙ㄒㄧ", "預期注音": "˙ㄒㄧ"}
        rg = {"status": "確認正確", "expected_reading": "˙ㄒㄧ"}
        result, _ = evaluate_historical_regression(rec, rg)
        self.assertEqual(result, "PASS")


class WholeBookRegressionAggregationTests(unittest.TestCase):
    def test_zero_applicable_historical_cases_is_neutral_pass(self):
        report = _aggregate_pdf_regression_rows([])
        self.assertTrue(report["ok"])
        self.assertEqual(report["required"], 0)
        self.assertEqual(report["executed"], 0)
        self.assertEqual(report["failed"], 0)
        self.assertEqual(report["not_executed"], 0)

    def test_split_pdfs_with_no_applicable_rows_is_neutral_pass(self):
        report = _aggregate_pdf_regression_rows([
            ("a.pdf", []),
            ("b.pdf", []),
        ])
        self.assertTrue(report["ok"])
        self.assertEqual(report["required"], 0)
        self.assertEqual(report["executed"], 0)

    @staticmethod
    def _row(result: str) -> dict[str, str]:
        return {
            "案例ID": "CONF-TEST",
            "課本頁": "27",
            "完整詞語": "東西",
            "字元": "西",
            "真值實際注音": "ㄒㄧ",
            "真值預期注音": "˙ㄒㄧ",
            "真值結論": "確認錯誤",
            "回歸結果": result,
            "執行說明": result,
            "來源": "fixture",
            "備註": "fixture",
        }

    def test_one_execution_plus_split_not_executed_counts_once(self):
        per_pdf = [(f"part-{i}.pdf", [self._row("PASS" if i == 4 else "NOT_EXECUTED")]) for i in range(9)]
        report = _aggregate_pdf_regression_rows(per_pdf)
        self.assertTrue(report["ok"])
        self.assertEqual(report["required"], 1)
        self.assertEqual(report["executed"], 1)
        self.assertEqual(report["passed"], 1)
        self.assertEqual(report["not_executed"], 0)
        self.assertEqual(report["duplicate_case_id"], 0)

    def test_no_execution_anywhere_is_one_not_executed(self):
        report = _aggregate_pdf_regression_rows([
            ("a.pdf", [self._row("NOT_EXECUTED")]),
            ("b.pdf", [self._row("NOT_EXECUTED")]),
        ])
        self.assertFalse(report["ok"])
        self.assertEqual(report["required"], 1)
        self.assertEqual(report["executed"], 0)
        self.assertEqual(report["not_executed"], 1)

    def test_reference_only_case_is_audited_but_not_required(self):
        row = self._row("NOT_APPLICABLE")
        row["適用性模式"] = "reference_only"
        report = _aggregate_pdf_regression_rows([
            ("a.pdf", [row]),
            ("b.pdf", [dict(row)]),
        ])
        self.assertTrue(report["ok"])
        self.assertEqual(report["required"], 0)
        self.assertEqual(report["executed"], 0)
        self.assertEqual(report["not_executed"], 0)
        self.assertEqual(report["not_applicable"], 1)
        self.assertEqual(report["not_applicable_case_ids"], ["CONF-TEST"])

    def test_multiple_executions_are_fail_closed(self):
        report = _aggregate_pdf_regression_rows([
            ("a.pdf", [self._row("PASS")]),
            ("b.pdf", [self._row("PASS")]),
        ])
        self.assertFalse(report["ok"])
        self.assertEqual(report["required"], 1)
        self.assertEqual(report["failed"], 1)
        self.assertEqual(report["duplicate_case_id"], 1)
        self.assertEqual(report["duplicate_execution_case_ids"], ["CONF-TEST"])

    def test_definition_drift_across_splits_is_integrity_error(self):
        a = self._row("PASS")
        b = self._row("NOT_EXECUTED")
        b["真值預期注音"] = "ㄒㄧ"
        with self.assertRaises(ValueError):
            _aggregate_pdf_regression_rows([("a.pdf", [a]), ("b.pdf", [b])])


if __name__ == "__main__":
    unittest.main()
