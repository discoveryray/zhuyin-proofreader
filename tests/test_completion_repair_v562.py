from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import standalone_proofread as proof  # noqa: E402
from check_pronunciation_candidates import (  # noqa: E402
    DEFAULT_RULES,
    historical_regression_applicability,
)
from pronunciation_rule_engine import load_rules, resolve_rules  # noqa: E402
from standalone_gui import project_status_text  # noqa: E402


class CompletionRepairV562Tests(unittest.TestCase):
    def test_three_up_zhuandong_uses_user_confirmed_zhuan_third_tone(self):
        rules = load_rules(DEFAULT_RULES, "07-115國小健體3上課本-L06-0225")
        decision, conflicts = resolve_rules(rules, "轉", "轉動身體重心", 0)
        self.assertIsNotNone(decision)
        self.assertFalse(conflicts and len({item.expected_reading for item in conflicts}) > 1)
        self.assertEqual(decision.expected_reading, "ㄓㄨㄢˇ")
        self.assertEqual(decision.matched_phrase, "轉動")

    def test_reference_only_and_sha_mismatch_are_not_applicable(self):
        applicable, note = historical_regression_applicability(
            {"case_id": "OLD", "applicability_mode": "reference_only"},
            "a" * 64,
        )
        self.assertFalse(applicable)
        self.assertIn("不列入", note)

        applicable, note = historical_regression_applicability(
            {"case_id": "BOUND", "applicability_mode": "hard_gate", "source_pdf_sha256": "b" * 64},
            "a" * 64,
        )
        self.assertFalse(applicable)
        self.assertIn("SHA-256", note)

    def test_export_actual_zero_is_normal_exit_without_traceback(self):
        stdout = io.StringIO()
        argv = ["standalone_proofread.py", "--export-actual-gpt", "-o", "project"]
        with (
            patch.object(sys, "argv", argv),
            patch.object(proof, "resolve_existing_project_dir", return_value=Path("project")),
            patch.object(proof, "export_actual_pending_for_gpt", return_value=None),
            redirect_stdout(stdout),
        ):
            self.assertEqual(proof.main(), 0)
        self.assertIn("actual 待判定為 0", stdout.getvalue())
        self.assertNotIn("Traceback", stdout.getvalue())

    def test_repair_project_reuses_pipeline_with_same_session(self):
        manifest = {
            "session_id": "session-562",
            "session_schema_version": proof.SESSION_SCHEMA_VERSION,
            "ledger_schema_version": proof.LEDGER_SCHEMA_VERSION,
            "review_id_schema_version": proof.REVIEW_ID_SCHEMA_VERSION,
        }
        pdfs = [Path("book.pdf")]
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            expected_report = output_dir / "注音校對_最終報告.xlsx"
            with (
                patch.object(proof, "json_load_strict", return_value=manifest),
                patch.object(proof, "validate_manifest_integrity"),
                patch.object(proof, "validate_output_artifact_hashes"),
                patch.object(proof, "_resolve_session_pdfs", return_value=pdfs),
                patch.object(proof, "run_pipeline_pdfs", return_value=expected_report) as run,
            ):
                self.assertEqual(proof.repair_project_state(output_dir), expected_report)
            run.assert_called_once_with(
                pdfs,
                output_dir,
                session_id_override="session-562",
            )

    def test_gui_surfaces_failed_gate_when_pending_is_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = {
                "status": "PROCESSING_FINISHED",
                "status_text": "處理結束；校對尚未完成。",
                "completion_gate": {
                    "state_counts": {"PASS": 10},
                    "failed_gates": ["regression_gate"],
                },
            }
            (root / "pipeline_status.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            self.assertIn("regression_gate", project_status_text(root))


if __name__ == "__main__":
    unittest.main()
