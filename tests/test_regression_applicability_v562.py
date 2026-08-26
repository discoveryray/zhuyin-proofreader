from __future__ import annotations

import csv
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from check_pronunciation_candidates import (  # noqa: E402
    execute_historical_regression,
    historical_regression_applicability,
    historical_regression_potential_match,
    historical_regression_routed_to_pdf,
    validate_historical_regression_definitions,
)
from occurrence_ledger import candidate_payload_sha256  # noqa: E402
from runtime_regression_gate import validate_regression_execution  # noqa: E402
from standalone_proofread import _aggregate_pdf_regression_rows  # noqa: E402


SHA_A = "a" * 64
SHA_B = "b" * 64


def occurrence(
    *,
    page: int = 10,
    line: str = "大家都說一級棒",
    phrase: str = "一級棒",
    target: str = "一",
    actual: str = "ㄧˋ",
    expected: str = "ㄧˋ",
) -> dict:
    return {
        "課本頁": str(page),
        "字元": target,
        "所在行": line,
        "局部詞境": line,
        "上下文": line,
        "行內字元位置": line.index(phrase) + phrase.index(target),
        "實際注音": actual,
        "預期注音": expected,
    }


def regression_case(
    mode: str,
    *,
    case_id: str = "CASE",
    page: int = 10,
    phrase: str = "一級棒",
    target: str = "一",
    source_sha: str = "",
    actual: str = "ㄧˋ",
    expected: str = "ㄧˋ",
    status: str = "確認正確",
) -> dict:
    return {
        "case_id": case_id,
        "page": str(page),
        "phrase": phrase,
        "target_char": target,
        "target_occurrence_index": "",
        "locator_context": "",
        "actual_reading": actual,
        "expected_reading": expected,
        "status": status,
        "applicability_mode": mode,
        "source_pdf_sha256": source_sha,
    }


def aggregate_row(
    result: str,
    *,
    case_id: str = "CASE",
    mode: str = "portable_gate",
    gate: str = "Y",
    locator_context: str = "",
    match_count: int = 0,
) -> dict:
    return {
        "案例ID": case_id,
        "課本頁": "10",
        "完整詞語": "一級棒",
        "字元": "一",
        "目標在詞內序號": "",
        "定位上下文": locator_context,
        "真值實際注音": "ㄧˋ",
        "真值預期注音": "ㄧˋ",
        "真值結論": "確認正確",
        "適用性模式": mode,
        "來源PDF SHA-256": "",
        "適用性狀態": "GATE_EXECUTED" if result in {"PASS", "FAIL"} else "GATE_NOT_EXECUTED",
        "計入完成門檻": gate,
        "定位命中數": match_count,
        "回歸結果": result,
        "執行說明": result,
        "來源": "fixture",
        "備註": "fixture",
    }


class HistoricalApplicabilityStateMachineTests(unittest.TestCase):
    def test_all_definitions_fail_closed_before_filename_routing(self):
        malformed = regression_case("unknown_mode", case_id="BAD-MODE")
        malformed["pdf_contains"] = "different-book"
        self.assertFalse(historical_regression_routed_to_pdf(malformed, "current-book"))
        with self.assertRaisesRegex(ValueError, "applicability_mode"):
            validate_historical_regression_definitions([malformed], SHA_A)

    def test_three_up_legacy_cases_are_individually_classified(self):
        with (ROOT / "pronunciation_regressions.csv").open(encoding="utf-8-sig", newline="") as handle:
            rows = {
                row["case_id"]: row
                for row in csv.DictReader(handle)
                if row["case_id"].startswith(("CONF-3UP-", "NEG-3UP-"))
            }
        self.assertEqual(len(rows), 15)
        self.assertEqual(rows["NEG-3UP-005"]["applicability_mode"], "portable_gate")
        self.assertEqual(rows["NEG-3UP-005"]["target_occurrence_index"], "0")
        self.assertEqual(rows["NEG-3UP-005"]["source_pdf_sha256"], "")
        for case_id, row in rows.items():
            if case_id == "NEG-3UP-005":
                continue
            self.assertEqual(row["applicability_mode"], "reference_only", case_id)
            self.assertEqual(row["source_pdf_sha256"], "", case_id)

    def test_exact_sha_hard_gate_matches_and_executes(self):
        case = regression_case("hard_gate", source_sha=SHA_A)
        applicability = historical_regression_applicability(case, SHA_A)
        self.assertTrue(applicability.should_locate)
        self.assertTrue(applicability.completion_gate)
        self.assertEqual(applicability.state, "HARD_GATE_EXACT_SHA")

        execution = execute_historical_regression(case, [occurrence()], SHA_A, {"10"})
        self.assertEqual(execution["result"], "PASS")
        self.assertTrue(execution["completion_gate"])
        self.assertEqual(execution["match_count"], 1)

    def test_exact_sha_hard_gate_routes_even_if_pdf_was_renamed(self):
        case = regression_case("hard_gate", source_sha=SHA_A)
        case["pdf_contains"] = "historical-name"
        self.assertTrue(historical_regression_routed_to_pdf(case, "renamed-current-file"))

        reference = regression_case("reference_only")
        reference["pdf_contains"] = "historical-name"
        self.assertFalse(historical_regression_routed_to_pdf(reference, "renamed-current-file"))

    def test_exact_sha_hard_gate_mismatch_is_not_applicable(self):
        case = regression_case("hard_gate", source_sha=SHA_A)
        execution = execute_historical_regression(case, [occurrence()], SHA_B, {"10"})
        self.assertEqual(execution["result"], "NOT_APPLICABLE")
        self.assertEqual(execution["applicability_state"], "SOURCE_NOT_APPLICABLE")
        self.assertFalse(execution["completion_gate"])
        self.assertEqual(execution["match_count"], 0)

    def test_reference_only_found_is_evaluated_but_non_gating(self):
        case = regression_case("reference_only")
        execution = execute_historical_regression(case, [occurrence()], SHA_A, {"10"})
        self.assertEqual(execution["result"], "PASS")
        self.assertEqual(execution["applicability_state"], "REFERENCE_MATCHED")
        self.assertFalse(execution["completion_gate"])
        self.assertEqual(execution["match_count"], 1)

    def test_reference_only_not_found_is_explicit(self):
        case = regression_case("reference_only")
        execution = execute_historical_regression(case, [], SHA_A, {"10"})
        self.assertEqual(execution["result"], "NOT_APPLICABLE")
        self.assertEqual(execution["applicability_state"], "REFERENCE_NOT_FOUND")
        self.assertIn("reference not found", execution["note"])
        self.assertFalse(execution["completion_gate"])

    def test_reference_only_ambiguous_match_is_visible_but_non_gating(self):
        case = regression_case("reference_only")
        execution = execute_historical_regression(
            case,
            [occurrence(line="甲說一級棒"), occurrence(line="乙說一級棒")],
            SHA_A,
            {"10"},
        )
        self.assertEqual(execution["result"], "FAIL")
        self.assertEqual(execution["applicability_state"], "REFERENCE_AMBIGUOUS")
        self.assertFalse(execution["completion_gate"])

    def test_portable_gate_unique_match_ignores_historical_page(self):
        case = regression_case("portable_gate", page=999)
        execution = execute_historical_regression(case, [occurrence(page=10)], SHA_A, {"10"})
        self.assertEqual(execution["result"], "PASS")
        self.assertTrue(execution["completion_gate"])
        self.assertEqual(execution["match_count"], 1)

    def test_portable_gate_zero_match_is_not_executed_fail_closed(self):
        case = regression_case("portable_gate")
        execution = execute_historical_regression(case, [], SHA_A, {"10"})
        self.assertEqual(execution["result"], "NOT_EXECUTED")
        self.assertEqual(execution["applicability_state"], "GATE_NOT_EXECUTED")
        self.assertTrue(execution["completion_gate"])

    def test_portable_gate_multiple_matches_fail_identity_ambiguity(self):
        case = regression_case("portable_gate")
        records = [
            occurrence(line="甲說一級棒"),
            occurrence(line="乙說一級棒"),
        ]
        execution = execute_historical_regression(case, records, SHA_A, {"10"})
        self.assertEqual(execution["result"], "FAIL")
        self.assertEqual(execution["applicability_state"], "IDENTITY_AMBIGUITY")
        self.assertEqual(execution["match_count"], 2)

    def test_portable_gate_context_can_make_locator_unique(self):
        case = regression_case("portable_gate")
        case["locator_context"] = "乙說一級棒"
        records = [
            occurrence(line="甲說一級棒"),
            occurrence(line="乙說一級棒"),
        ]
        execution = execute_historical_regression(case, records, SHA_A, {"10"})
        self.assertEqual(execution["result"], "PASS")
        self.assertEqual(execution["match_count"], 1)

    def test_historical_pronunciation_truth_does_not_change_locator(self):
        record = occurrence(page=10)
        first = regression_case("portable_gate", actual="ㄧ", expected="ㄧˋ")
        second = regression_case("portable_gate", actual="ㄧˊ", expected="ㄧ")
        self.assertTrue(historical_regression_potential_match(record, first, match_page=False))
        self.assertTrue(historical_regression_potential_match(record, second, match_page=False))


class WholeBookApplicabilityAggregationTests(unittest.TestCase):
    def test_one_portable_owner_executes_once_across_split_pdfs(self):
        report = _aggregate_pdf_regression_rows([
            ("part-a.pdf", [aggregate_row("NOT_EXECUTED")]),
            ("part-b.pdf", [aggregate_row("PASS", match_count=1)]),
            ("part-c.pdf", [aggregate_row("NOT_EXECUTED")]),
        ])
        self.assertTrue(report["ok"])
        self.assertEqual(report["required"], 1)
        self.assertEqual(report["executed"], 1)
        self.assertEqual(report["not_executed"], 0)

    def test_duplicate_portable_execution_across_splits_fails(self):
        report = _aggregate_pdf_regression_rows([
            ("part-a.pdf", [aggregate_row("PASS", match_count=1)]),
            ("part-b.pdf", [aggregate_row("PASS", match_count=1)]),
        ])
        self.assertFalse(report["ok"])
        self.assertEqual(report["failed"], 1)
        self.assertEqual(report["duplicate_case_id"], 1)

    def test_definition_drift_in_locator_context_is_rejected(self):
        first = aggregate_row("PASS", locator_context="大家都說一級棒", match_count=1)
        second = aggregate_row("NOT_EXECUTED", locator_context="他得到一級棒")
        with self.assertRaises(ValueError):
            _aggregate_pdf_regression_rows([
                ("part-a.pdf", [first]),
                ("part-b.pdf", [second]),
            ])

    def test_reference_failure_is_audited_without_entering_gate(self):
        reference = aggregate_row(
            "FAIL", mode="reference_only", gate="N", match_count=1,
        )
        report = _aggregate_pdf_regression_rows([("part-a.pdf", [reference])])
        self.assertTrue(report["ok"])
        self.assertEqual(report["required"], 0)
        self.assertEqual(report["failed"], 0)
        self.assertEqual(report["reference_audit_matched"], 1)
        self.assertEqual(report["reference_audit_failed"], 1)

    def test_reference_audit_cannot_relax_mandatory_regression(self):
        reference = aggregate_row(
            "PASS", mode="reference_only", gate="N", match_count=1,
        )
        reference_report = _aggregate_pdf_regression_rows([("part-a.pdf", [reference])])
        mandatory_report = validate_regression_execution([{"case_id": "MANDATORY"}], [])
        self.assertTrue(reference_report["ok"])
        self.assertFalse(mandatory_report["ok"])
        self.assertEqual(mandatory_report["required"], 1)
        self.assertEqual(mandatory_report["executed"], 0)

    def test_applicability_and_locator_fields_are_authoritative_payload(self):
        base = aggregate_row("PASS", match_count=1)
        baseline_hash = candidate_payload_sha256([], [], [base])
        for field, value in (
            ("定位上下文", "大家都說一級棒"),
            ("適用性狀態", "IDENTITY_AMBIGUITY"),
            ("計入完成門檻", "N"),
            ("定位命中數", 2),
        ):
            changed = dict(base)
            changed[field] = value
            self.assertNotEqual(
                baseline_hash,
                candidate_payload_sha256([], [], [changed]),
                field,
            )


if __name__ == "__main__":
    unittest.main()
