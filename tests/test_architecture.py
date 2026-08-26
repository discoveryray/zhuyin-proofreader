from __future__ import annotations

import ast
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[1]

import sys
sys.path.insert(0, str(ROOT))

from occurrence_ledger import (  # noqa: E402
    CONFIRMATION_GATES,
    LEDGER_SCHEMA_VERSION, SESSION_SCHEMA_VERSION, WORKBOOK_SCHEMA_VERSION,
    PIPELINE_BLOCKED,
    PROCESSING_FINISHED,
    PROOFREAD_COMPLETE,
    DuplicateIdError,
    InvalidTransitionError,
    assert_unique_ids,
    build_occurrence_ledger,
    completion_gate,
    make_occurrence_id,
    make_review_id,
    prepare_occurrence_rows,
    reconcile_ledger,
    transition_state,
)
from runtime_regression_gate import run_mandatory_regressions, validate_regression_execution  # noqa: E402
from runtime_source_validation import (  # noqa: E402
    ASSET_MANIFEST_SCHEMA_VERSION, TOOL_VERSION,
    compute_actual_asset_fingerprint,
    compute_expected_asset_fingerprint,
    validate_asset_manifest,
    validate_csv_schema,
    validate_xlsx_schema,
    write_pipeline_blocked,
)
from export_zhuyin_readings import ACTUAL_DECODER_SOURCE_FILES as EXPORT_ACTUAL_SOURCE_FILES  # noqa: E402
from standalone_proofread import (  # noqa: E402
    ACTUAL_DECODER_SOURCE_FILES as PIPELINE_ACTUAL_SOURCE_FILES,
    VERSION as CONTROLLER_VERSION,
    _apply_review_event,
    build_reusable_rule,
    materialize_ledger,
    output_is_reusable,
    reusable_rules_sha256,
    save_reusable_expected_rule,
    seal_manifest,
)


PDF_HASH = "a" * 64


def source_row(char="角", actual="ㄐㄩㄝˊ", source_row_number=1):
    return {
        "pdf_sha256": PDF_HASH,
        "pdf": "book.pdf",
        "pdf_name": "book.pdf",
        "實體頁碼": 1,
        "課本頁": 1,
        "字元": char,
        "實際注音": actual,
        "解碼依據": "PDF glyph exact evidence",
        "穩定注音鍵": "Font#12",
        "font": "Font",
        "font_xref": 9,
        "glyph_id_字形索引": 23,
        "注音元件ID": 12,
        "x0": 10.125,
        "y0": 20.25,
        "x1": 30.5,
        "y1": 40.75,
        "source_row_number": source_row_number,
    }


def ledger_for(state="PASS", actual="ㄐㄩㄝˊ", expected=("ㄐㄩㄝˊ",)):
    row = source_row(actual=actual)
    prepare_occurrence_rows(PDF_HASH, [row])
    classification = {
        row["occurrence_id"]: {
            "state": state,
            "actual": actual,
            "actual_evidence": "PDF glyph exact evidence",
            "expected_set": list(expected),
            "expected_evidence": "現版手冊完整詞條",
            "context_evidence": "角色/角@1",
            "comparison_result": "MATCH" if actual in expected else "MISMATCH",
            "source_view": "單音一致" if state == "PASS" else "差異候選",
            "source_record": row,
        }
    }
    return build_occurrence_ledger([row], classification)


class OccurrenceLedgerTests(unittest.TestCase):
    def test_occurrence_id_stable_across_excel_row_reorder(self):
        first = source_row(source_row_number=2)
        second = source_row(source_row_number=999)
        first_id = make_occurrence_id(PDF_HASH, first, fallback_row_number=2)[0]
        second_id = make_occurrence_id(PDF_HASH, second, fallback_row_number=999)[0]
        self.assertEqual(first_id, second_id)

    def test_pdf_change_changes_occurrence_id(self):
        row = source_row()
        first = make_occurrence_id("a" * 64, row, fallback_row_number=1)[0]
        second = make_occurrence_id("b" * 64, row, fallback_row_number=1)[0]
        self.assertNotEqual(first, second)

    def test_row_fallback_is_explicit(self):
        row = {"source_row_number": 7}
        occurrence_id, confidence, fallback = make_occurrence_id(PDF_HASH, row, fallback_row_number=7)
        self.assertTrue(occurrence_id.startswith("occ_"))
        self.assertEqual(confidence, "FALLBACK_SOURCE_ROW")
        self.assertTrue(fallback)

    def test_duplicate_id_detection(self):
        with self.assertRaises(DuplicateIdError):
            assert_unique_ids([{"occurrence_id": "x"}, {"occurrence_id": "x"}], "occurrence_id")

    def test_review_id_independent_of_category(self):
        occurrence_id = "occ_123"
        self.assertEqual(make_review_id(occurrence_id), make_review_id(occurrence_id))

    def test_expected_set_membership_passes(self):
        ledger = ledger_for(expected=("ㄐㄩㄝˊ", "ㄐㄧㄠˇ"))
        self.assertEqual(ledger[0]["state"], "PASS")

    def test_pass_rejects_actual_outside_expected_set(self):
        with self.assertRaises(ValueError):
            ledger_for(expected=("ㄐㄧㄠˇ",))

    def test_nonindependent_layer_requires_canonical_occurrence(self):
        rows = [source_row(source_row_number=1), source_row(source_row_number=2)]
        prepare_occurrence_rows(PDF_HASH, rows)
        classifications = {
            rows[0]["occurrence_id"]: {
                "state": "PASS", "actual": "ㄐㄩㄝˊ", "actual_evidence": "glyph",
                "expected_set": ["ㄐㄩㄝˊ"], "expected_evidence": "rule", "context_evidence": "角色", "comparison_result": "MATCH",
            },
            rows[1]["occurrence_id"]: {
                "state": "EXCLUDED_NONINDEPENDENT_LAYER",
                "canonical_occurrence_id": rows[0]["occurrence_id"], "exclusion_reason": "hidden overlap",
            },
        }
        ledger = build_occurrence_ledger(rows, classifications)
        self.assertEqual(len(ledger), 2)
        self.assertEqual(ledger[1]["state"], "EXCLUDED_NONINDEPENDENT_LAYER")

    def test_reconciliation_detects_missing_source(self):
        ledger = ledger_for()
        result = reconcile_ledger(ledger, {ledger[0]["occurrence_id"], "occ_missing"})
        self.assertFalse(result.ok)
        self.assertIn("occ_missing", result.details["actual_only"])

    def test_state_transition_requires_six_gates(self):
        entry = ledger_for(state="DIFFERENCE_PENDING_CONFIRMATION", actual="ㄐㄧㄠˇ", expected=("ㄐㄩㄝˊ",))[0]
        with self.assertRaises(InvalidTransitionError):
            transition_state(entry, "TEXTBOOK_ERROR_CONFIRMED", {"confirmation_gates": {}})
        gates = {gate: True for gate in CONFIRMATION_GATES}
        transitioned = transition_state(entry, "TEXTBOOK_ERROR_CONFIRMED", {"confirmation_gates": gates})
        self.assertEqual(transitioned["state"], "TEXTBOOK_ERROR_CONFIRMED")

    def test_invalid_manual_conclusion_rejected(self):
        entry = ledger_for(state="EXPECTED_UNRESOLVED", expected=())[0]
        with self.assertRaises(InvalidTransitionError):
            _apply_review_event(entry, {"action": "課本正確"})

    def test_expected_evidence_update_does_not_mutate_actual(self):
        entry = ledger_for(state="EXPECTED_UNRESOLVED", expected=())[0]
        before = (entry["actual"], entry["actual_evidence"])
        updated = _apply_review_event(entry, {
            "action": "補建expected證據",
            "expected_set": "ㄐㄩㄝˊ",
            "expected_evidence": "現版手冊",
            "context_evidence": "角色/角@1",
        })
        self.assertEqual(before, (updated["actual"], updated["actual_evidence"]))
        self.assertEqual(updated["state"], "PASS")

    def test_rule_conflict_can_be_resolved_by_independent_expected_evidence(self):
        entry = ledger_for(state="RULE_CONFLICT", expected=())[0]
        before = (entry["actual"], entry["actual_evidence"])
        updated = _apply_review_event(entry, {
            "action": "解決expected證據",
            "expected_set": "ㄐㄩㄝˊ",
            "expected_evidence": "公司現行規定：完整詞角色",
            "context_evidence": "角色/角@0",
            "resolution_reason": "排除另一個不同義項",
        })
        self.assertEqual(before, (updated["actual"], updated["actual_evidence"]))
        self.assertEqual(updated["state"], "PASS")
        self.assertEqual(updated["comparison_result"], "MATCH")

    def test_existing_difference_can_be_closed_as_pass_only_by_rebuilding_expected_evidence(self):
        entry = ledger_for(state="DIFFERENCE_PENDING_CONFIRMATION", actual="ㄐㄩㄝˊ", expected=("ㄐㄧㄠˇ",))[0]
        updated = _apply_review_event(entry, {
            "action": "解決expected證據",
            "expected_set": "ㄐㄩㄝˊ",
            "expected_evidence": "公司較高優先規定",
            "context_evidence": "角色/角@0",
        })
        self.assertEqual(updated["actual"], "ㄐㄩㄝˊ")
        self.assertEqual(updated["state"], "PASS")
        self.assertEqual(updated["previous_expected_set"], ["ㄐㄧㄠˇ"])

    def test_reusable_rule_builds_only_exact_phrase_and_target_position(self):
        entry = ledger_for(state="RULE_CONFLICT", expected=())[0]
        entry["source_record"]["所在行"] = "角色任務"
        entry["source_record"]["行內字元位置"] = 0
        rule = build_reusable_rule(
            entry,
            phrase="角色",
            expected_set="ㄐㄩㄝˊ",
            evidence="公司現行規定：角色",
        )
        self.assertEqual(rule["phrase"], "角色")
        self.assertEqual(rule["target_char"], "角")
        self.assertEqual(rule["target_index"], 0)
        self.assertEqual(rule["expected_set"], ["ㄐㄩㄝˊ"])
        self.assertEqual(rule["scope"], "same_phrase_position")

    def test_reusable_rule_rejects_ambiguous_target_position_without_coordinate_context(self):
        entry = ledger_for(state="RULE_CONFLICT", expected=())[0]
        entry["char"] = "看"
        entry["source_record"]["所在行"] = ""
        entry["source_record"]["行內字元位置"] = ""
        with self.assertRaises(ValueError):
            build_reusable_rule(
                entry,
                phrase="看看",
                expected_set="ㄎㄢˋ",
                evidence="人工確認義項",
            )

    def test_materialize_applies_session_snapshot_reusable_rule_without_using_actual_as_rule_input(self):
        entry = ledger_for(state="RULE_CONFLICT", actual="ㄐㄩㄝˊ", expected=())[0]
        entry["source_record"]["所在行"] = "角色任務"
        entry["source_record"]["行內字元位置"] = 0
        rule = build_reusable_rule(
            entry,
            phrase="角色",
            expected_set="ㄐㄩㄝˊ",
            evidence="公司現行規定：角色",
        )
        rules_doc = {"schema_version": "1.0", "rules": [rule]}
        manifest = {
            "records": [entry],
            "reusable_expected_rules": rules_doc,
            "reusable_expected_rules_sha256": reusable_rules_sha256(rules_doc),
        }
        materialized = materialize_ledger(manifest, {})
        self.assertEqual(materialized[0]["state"], "PASS")
        self.assertEqual(materialized[0]["actual"], "ㄐㄩㄝˊ")
        self.assertEqual(materialized[0]["expected_set"], ["ㄐㄩㄝˊ"])
        self.assertEqual(materialized[0]["source_view"], "人工核准可重用規則")

    def test_existing_session_snapshot_is_immutable_when_global_rule_file_changes(self):
        entry = ledger_for(state="RULE_CONFLICT", actual="ㄐㄩㄝˊ", expected=())[0]
        entry["source_record"]["所在行"] = "角色任務"
        entry["source_record"]["行內字元位置"] = 0
        empty_rules = {"schema_version": "1.0", "rules": []}
        manifest = {
            "records": [entry],
            "reusable_expected_rules": empty_rules,
            "reusable_expected_rules_sha256": reusable_rules_sha256(empty_rules),
        }
        rule = build_reusable_rule(
            entry,
            phrase="角色",
            expected_set="ㄐㄩㄝˊ",
            evidence="公司現行規定：角色",
        )
        with tempfile.TemporaryDirectory() as directory:
            save_reusable_expected_rule(rule, Path(directory) / "rules.json")
            materialized = materialize_ledger(manifest, {})
        self.assertEqual(materialized[0]["state"], "RULE_CONFLICT")

    def test_reusable_rule_conflicting_same_condition_fails_closed(self):
        base = {
            "rule_id": "R1",
            "enabled": True,
            "phrase": "角色",
            "target_char": "角",
            "target_index": 0,
            "expected_set": ["ㄐㄩㄝˊ"],
            "evidence": "來源一",
            "context_contains": "",
            "scope": "same_phrase_position",
        }
        conflict = dict(base)
        conflict["rule_id"] = "R2"
        conflict["expected_set"] = ["ㄐㄧㄠˇ"]
        with self.assertRaises(ValueError):
            reusable_rules_sha256({"schema_version": "1.0", "rules": [base, conflict]})


class CompletionGateTests(unittest.TestCase):
    def regression_ok(self):
        return {"ok": True, "required": 2, "executed": 2, "passed": 2, "failed": 0, "not_executed": 0, "duplicate_case_id": 0}

    def test_proofread_complete_only_when_all_gates_pass(self):
        ledger = ledger_for()
        reconciliation = reconcile_ledger(ledger, {ledger[0]["occurrence_id"]})
        gate = completion_gate(ledger, reconciliation, self.regression_ok())
        self.assertEqual(gate["status"], PROOFREAD_COMPLETE)

    def test_processing_finished_when_pending(self):
        ledger = ledger_for(state="EXPECTED_UNRESOLVED", expected=())
        reconciliation = reconcile_ledger(ledger, {ledger[0]["occurrence_id"]})
        gate = completion_gate(ledger, reconciliation, self.regression_ok())
        self.assertEqual(gate["status"], PROCESSING_FINISHED)
        self.assertFalse(gate["complete"])

    def test_zero_executed_regression_never_passes(self):
        ledger = ledger_for()
        reconciliation = reconcile_ledger(ledger, {ledger[0]["occurrence_id"]})
        regression = {"ok": True, "required": 0, "executed": 0, "passed": 0, "failed": 0, "not_executed": 0, "duplicate_case_id": 0}
        gate = completion_gate(ledger, reconciliation, regression)
        self.assertEqual(gate["status"], PROCESSING_FINISHED)
        self.assertFalse(gate["hard_gates"]["regression_gate"])

    def test_source_invalid_blocks_pipeline(self):
        ledger = ledger_for()
        reconciliation = reconcile_ledger(ledger, {ledger[0]["occurrence_id"]})
        gate = completion_gate(ledger, reconciliation, self.regression_ok(), source_validation_ok=False)
        self.assertEqual(gate["status"], PIPELINE_BLOCKED)


class SourceAndFingerprintTests(unittest.TestCase):
    def test_current_asset_manifest_validates(self):
        report = validate_asset_manifest(ROOT)
        self.assertTrue(report["ok"], report.get("errors"))
        self.assertGreaterEqual(len(report["assets"]), 20)

    def test_csv_schema_missing_column_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "x.csv"
            path.write_text("id,value\n1,a\n", encoding="utf-8")
            report = validate_csv_schema(path, {"required_columns": ["id", "missing"], "min_rows": 1})
            self.assertFalse(report["ok"])

    def test_csv_blank_unique_key_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "x.csv"
            path.write_text("id,value\n,a\n", encoding="utf-8")
            report = validate_csv_schema(path, {"required_columns": ["id", "value"], "unique_key": ["id"]})
            self.assertFalse(report["ok"])
            self.assertTrue(any("唯一鍵全空白" in error for error in report["errors"]))

    def test_csv_duplicate_header_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "x.csv"
            path.write_text("id,id\n1,2\n", encoding="utf-8")
            report = validate_csv_schema(path, {"required_columns": ["id"]})
            self.assertFalse(report["ok"])

    def test_corrupt_xlsx_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "x.xlsx"
            path.write_bytes(b"not an xlsx")
            report = validate_xlsx_schema(path, {"required_columns": ["id"]})
            self.assertFalse(report["ok"])

    def test_asset_manifest_hash_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "asset.csv").write_text("id,value\n1,a\n", encoding="utf-8")
            manifest = {
                "schema_version": "2.5.0",
                "tool_version": "5.2.0",
                "assets": [{
                    "name": "asset", "chain": "expected", "path": "asset.csv", "kind": "csv",
                    "sha256": "0" * 64, "required_columns": ["id", "value"], "unique_key": ["id"],
                }],
            }
            manifest_path = root / "runtime_asset_manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            report = validate_asset_manifest(root, manifest_path)
            self.assertFalse(report["ok"])
            self.assertTrue(any("SHA-256 不符" in error for error in report["errors"]))

    def test_asset_manifest_duplicate_json_key_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = root / "runtime_asset_manifest.json"
            manifest_path.write_text(
                '{"schema_version":"2.5.0","schema_version":"2.5.0",'
                '"tool_version":"5.2.0","assets":[]}',
                encoding="utf-8",
            )
            report = validate_asset_manifest(root, manifest_path)
            self.assertFalse(report["ok"])
            self.assertTrue(any("損壞" in error for error in report["errors"]))

    def test_blocked_source_overwrites_current_pipeline_status(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "pipeline_status.json").write_text('{"status":"PROOFREAD_COMPLETE"}', encoding="utf-8")
            write_pipeline_blocked(output, {"ok": False, "errors": ["bad hash"]}, "SOURCE_INVALID")
            status = json.loads((output / "pipeline_status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["status"], PIPELINE_BLOCKED)

    def test_actual_fingerprint_excludes_expected_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "decoder.py").write_text("print('same')", encoding="utf-8")
            pdf = root / "book.pdf"
            pdf.write_bytes(b"pdf-current")
            base = {"ok": True, "manifest_schema_version": ASSET_MANIFEST_SCHEMA_VERSION, "assets": [{"name": "map", "chain": "actual", "ok": True, "sha256": "1" * 64}]}
            changed_expected = deepcopy(base)
            changed_expected["assets"].append({"name": "rules", "chain": "expected", "ok": True, "sha256": "2" * 64})
            first = compute_actual_asset_fingerprint(root, pdf, base, decoder_version="5.2.0", source_files=["decoder.py"])
            second = compute_actual_asset_fingerprint(root, pdf, changed_expected, decoder_version="5.2.0", source_files=["decoder.py"])
            self.assertEqual(first["fingerprint"], second["fingerprint"])

    def test_invalid_expected_chain_does_not_block_actual_fingerprint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "decoder.py").write_text("print('same')", encoding="utf-8")
            pdf = root / "book.pdf"
            pdf.write_bytes(b"pdf-current")
            report = {
                "ok": False,
                "manifest_schema_version": ASSET_MANIFEST_SCHEMA_VERSION,
                "assets": [
                    {"name": "map", "chain": "actual", "ok": True, "sha256": "1" * 64},
                    {"name": "rules", "chain": "expected", "ok": False, "sha256": "2" * 64},
                ],
            }
            result = compute_actual_asset_fingerprint(root, pdf, report, decoder_version="5.2.0", source_files=["decoder.py"])
            self.assertTrue(result["fingerprint"])

    def test_actual_asset_change_invalidates_fingerprint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "decoder.py").write_text("print('same')", encoding="utf-8")
            pdf = root / "book.pdf"; pdf.write_bytes(b"pdf-current")
            first_report = {"ok": True, "manifest_schema_version": ASSET_MANIFEST_SCHEMA_VERSION, "assets": [{"name": "map", "chain": "actual", "ok": True, "sha256": "1" * 64}]}
            second_report = {"ok": True, "manifest_schema_version": ASSET_MANIFEST_SCHEMA_VERSION, "assets": [{"name": "map", "chain": "actual", "ok": True, "sha256": "2" * 64}]}
            first = compute_actual_asset_fingerprint(root, pdf, first_report, decoder_version="5.2.0", source_files=["decoder.py"])
            second = compute_actual_asset_fingerprint(root, pdf, second_report, decoder_version="5.2.0", source_files=["decoder.py"])
            self.assertNotEqual(first["fingerprint"], second["fingerprint"])

    def test_expected_fingerprint_excludes_actual_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "resolver.py").write_text("print('same')", encoding="utf-8")
            base = {"ok": True, "manifest_schema_version": ASSET_MANIFEST_SCHEMA_VERSION, "assets": [{"name": "rules", "chain": "expected", "ok": True, "sha256": "1" * 64}]}
            changed_actual = deepcopy(base)
            changed_actual["assets"].append({"name": "map", "chain": "actual", "ok": True, "sha256": "2" * 64})
            first = compute_expected_asset_fingerprint(root, base, resolver_version="5.2.0", source_files=["resolver.py"])
            second = compute_expected_asset_fingerprint(root, changed_actual, resolver_version="5.2.0", source_files=["resolver.py"])
            self.assertEqual(first["fingerprint"], second["fingerprint"])

    def test_expected_asset_change_invalidates_candidate_fingerprint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "resolver.py").write_text("print('same')", encoding="utf-8")
            first_report = {"ok": True, "manifest_schema_version": ASSET_MANIFEST_SCHEMA_VERSION, "assets": [{"name": "rules", "chain": "expected", "ok": True, "sha256": "1" * 64}]}
            second_report = {"ok": True, "manifest_schema_version": ASSET_MANIFEST_SCHEMA_VERSION, "assets": [{"name": "rules", "chain": "expected", "ok": True, "sha256": "2" * 64}]}
            first = compute_expected_asset_fingerprint(root, first_report, resolver_version="5.2.0", source_files=["resolver.py"])
            second = compute_expected_asset_fingerprint(root, second_report, resolver_version="5.2.0", source_files=["resolver.py"])
            self.assertNotEqual(first["fingerprint"], second["fingerprint"])

    def test_cache_requires_matching_actual_fingerprint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf = root / "book.pdf"; pdf.write_bytes(b"current pdf")
            actual = root / "actual.xlsx"
            wb = Workbook(); ws = wb.active; ws.title = "v5.2中繼資料"; ws.append(["項目", "內容"])
            import hashlib
            pdf_hash = hashlib.sha256(b"current pdf").hexdigest()
            for item in [
                ("workbook_schema_version", WORKBOOK_SCHEMA_VERSION), ("ledger_schema_version", LEDGER_SCHEMA_VERSION),
                ("actual_asset_fingerprint", "fp1"), ("pdf_sha256", pdf_hash),
            ]:
                ws.append(item)
            wb.save(actual)
            manifest = {
                "version": CONTROLLER_VERSION, "session_schema_version": SESSION_SCHEMA_VERSION, "ledger_schema_version": LEDGER_SCHEMA_VERSION,
                "pdfs": [{
                    "pdf_name": "book.pdf", "pdf_sha256": pdf_hash, "actual_asset_fingerprint": "fp1",
                    "actual_workbook": str(actual),
                    "actual_workbook_sha256": hashlib.sha256(actual.read_bytes()).hexdigest(),
                }],
            }
            (root / "校對工作階段.json").write_text(json.dumps(seal_manifest(manifest)), encoding="utf-8")
            self.assertTrue(output_is_reusable(root, pdf, actual, {"fingerprint": "fp1"}))
            self.assertFalse(output_is_reusable(root, pdf, actual, {"fingerprint": "fp2"}))

            # A controller-only / expected-only patch must not invalidate the
            # actual artifact when schema and the independent actual fingerprint
            # are unchanged.
            manifest["version"] = CONTROLLER_VERSION
            (root / "校對工作階段.json").write_text(json.dumps(seal_manifest(manifest)), encoding="utf-8")
            self.assertTrue(output_is_reusable(root, pdf, actual, {"fingerprint": "fp1"}))


class MandatoryRegressionTests(unittest.TestCase):
    def test_execution_count_and_all_case_ids_required(self):
        cases = [
            {"case_id": "a", "context": "角色", "target_char": "角", "target_index": "0", "expected_resolution": "RESOLVED", "expected_set": "ㄐㄩㄝˊ", "required_matched_phrase": "角色", "forbidden_matched_phrase": "", "control_type": "positive", "note": ""},
            {"case_id": "b", "context": "一龘", "target_char": "一", "target_index": "0", "expected_resolution": "UNRESOLVED", "expected_set": "", "required_matched_phrase": "", "forbidden_matched_phrase": "", "control_type": "negative", "note": ""},
        ]

        class Decision:
            expected_reading = "ㄐㄩㄝˊ"
            matched_phrase = "角色"

        def resolver(char, context, index):
            return (Decision(), []) if char == "角" else (None, [])

        results = run_mandatory_regressions(cases, resolver)
        report = validate_regression_execution(cases, results)
        self.assertTrue(report["ok"])
        self.assertEqual(report["required"], 2)
        self.assertEqual(report["executed"], 2)

    def test_not_executed_is_not_success(self):
        cases = [{"case_id": "a", "context": "角色", "target_char": "角", "target_index": "1", "expected_resolution": "RESOLVED", "expected_set": "ㄐㄩㄝˊ", "required_matched_phrase": "", "forbidden_matched_phrase": "", "control_type": "positive", "note": ""}]
        results = run_mandatory_regressions(cases, lambda *_: (_ for _ in ()).throw(RuntimeError("boom")))
        report = validate_regression_execution(cases, results)
        self.assertFalse(report["ok"])
        self.assertEqual(report["not_executed"], 1)


class IndependenceStaticTests(unittest.TestCase):
    def test_actual_fingerprint_source_lists_do_not_drift(self):
        self.assertEqual(EXPORT_ACTUAL_SOURCE_FILES, PIPELINE_ACTUAL_SOURCE_FILES)

    def test_actual_decoder_does_not_import_expected_modules(self):
        tree = ast.parse((ROOT / "export_zhuyin_readings.py").read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        forbidden = {"check_pronunciation_candidates", "concise_expected_resolver", "pronunciation_rule_engine"}
        self.assertFalse(imported & forbidden)

    def test_expected_decider_does_not_load_actual_variable(self):
        tree = ast.parse((ROOT / "check_pronunciation_candidates.py").read_text(encoding="utf-8"))
        decider = next(node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "decide_expected")
        loaded_names = {node.id for node in ast.walk(decider) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}
        self.assertNotIn("actual", loaded_names)


if __name__ == "__main__":
    unittest.main()
