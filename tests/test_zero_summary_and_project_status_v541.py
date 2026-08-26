from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from standalone_proofread import (  # noqa: E402
    _summary_int,
    _has_complete_pre_session_outputs,
    actual_workbook_path,
    candidate_workbook_path,
)
from standalone_gui import project_status_text  # noqa: E402


class ZeroSummaryAndProjectStatusV541Tests(unittest.TestCase):
    def test_zero_summary_value_is_not_treated_as_missing(self):
        self.assertEqual(_summary_int({"count": 0}, "count"), 0)
        self.assertEqual(_summary_int({"count": "0"}, "count"), 0)
        self.assertEqual(_summary_int({}, "count"), -1)
        self.assertEqual(_summary_int({"count": ""}, "count"), -1)


    def test_pre_session_recovery_only_triggers_when_all_stage_outputs_exist(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            actual_dir = folder / "01_實際注音"
            cand_dir = folder / "02_候選報告"
            actual_dir.mkdir()
            cand_dir.mkdir()
            pdfs = [folder / "a.pdf", folder / "b.pdf"]
            for pdf in pdfs:
                pdf.write_bytes(b"%PDF-test")
            self.assertFalse(_has_complete_pre_session_outputs(pdfs, actual_dir, cand_dir))
            for pdf in pdfs:
                actual_workbook_path(actual_dir, pdf).touch()
                candidate_workbook_path(cand_dir, pdf).touch()
            self.assertTrue(_has_complete_pre_session_outputs(pdfs, actual_dir, cand_dir))

    def test_blocked_pipeline_is_visible_without_session_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            (folder / "pipeline_status.json").write_text(json.dumps({
                "status": "PIPELINE_BLOCKED",
                "reason": "RUNTIME_FATAL_OR_DATA_INTEGRITY_ERROR",
                "source_validation": {"errors": ["runtime fatal error: zero occurrence summary"]},
            }, ensure_ascii=False), encoding="utf-8")
            text = project_status_text(folder)
            self.assertIn("處理被阻擋", text)
            self.assertIn("zero occurrence summary", text)
            self.assertIn("工作階段尚未建立", text)

    def test_processing_phase_is_visible_before_session_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            (folder / "pipeline_status.json").write_text(json.dumps({
                "status": "PROCESSING",
                "phase": "2/3",
                "status_text": "[2/3] 詞境／字典校對候選",
            }, ensure_ascii=False), encoding="utf-8")
            text = project_status_text(folder)
            self.assertIn("[2/3]", text)
            self.assertIn("首次分析中", text)

    def test_brand_new_folder_still_reports_new_project(self):
        with tempfile.TemporaryDirectory() as td:
            text = project_status_text(Path(td))
            self.assertIn("新的校對專案資料夾", text)


if __name__ == "__main__":
    unittest.main()
