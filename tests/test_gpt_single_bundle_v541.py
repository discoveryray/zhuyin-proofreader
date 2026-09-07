from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import standalone_proofread as sp  # noqa: E402


class GptSingleBundleV541Tests(unittest.TestCase):
    def _bundle(self, folder: Path) -> Path:
        path = folder / "bundle.zip"
        meta = {
            "bundle_schema_version": sp.GPT_DECISION_BUNDLE_SCHEMA_VERSION,
            "version": sp.VERSION,
            "session_id": "s1",
            "session_schema_version": sp.SESSION_SCHEMA_VERSION,
            "workbook_schema_version": sp.WORKBOOK_SCHEMA_VERSION,
            "review_id_schema_version": sp.REVIEW_ID_SCHEMA_VERSION,
            "expected_workbook": "",
        }
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("GPT判定包.json", json.dumps(meta, ensure_ascii=False))
            zf.writestr("actual_occurrence_decisions.csv", "occurrence_id\n")
        return path

    def test_zip_bundle_detected_and_ordered_first(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            bundle = self._bundle(folder)
            self.assertEqual(sp.detect_gpt_decision_path_kind(bundle), "bundle")
            planned = sp.plan_gpt_auto_imports([bundle])
            self.assertEqual(planned, [(bundle, "bundle")])

    def test_occurrence_actual_import_is_actual_only_and_refreshes_once(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            manifest = {
                "version": sp.VERSION,
                "session_id": "s1",
                "session_schema_version": sp.SESSION_SCHEMA_VERSION,
                "workbook_schema_version": sp.WORKBOOK_SCHEMA_VERSION,
                "review_id_schema_version": sp.REVIEW_ID_SCHEMA_VERSION,
                "pdfs": [],
                "records": [],
            }
            sp.seal_manifest(manifest)
            (folder / "校對工作階段.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            entry = {
                "occurrence_id": ("occ_" + hashlib.sha256(b"occ1").hexdigest()),
                "review_id": "rev1",
                "pdf_name": "a.pdf",
                "physical_page": 2,
                "char": "蛋",
                "actual": "ㄊㄢˊ",
                "actual_evidence": "glyph-evidence",
                "stable_key": "k1",
                "state": "DIFFERENCE_PENDING_CONFIRMATION",
                "x0": 1, "y0": 2, "x1": 3, "y1": 4,
            }
            csv_path = folder / "actual_occurrence_decisions.csv"
            fields = [
                "occurrence_id", "review_id", "pdf_name", "physical_page", "char",
                "exported_actual", "exported_actual_evidence_sha256", "verified_actual",
                "visual_confirmation", "note",
            ]
            with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
                w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerow({
                    "occurrence_id": ("occ_" + hashlib.sha256(b"occ1").hexdigest()), "review_id": "rev1", "pdf_name": "a.pdf",
                    "physical_page": "2", "char": "蛋", "exported_actual": "ㄊㄢˊ",
                    "exported_actual_evidence_sha256": hashlib.sha256(b"glyph-evidence").hexdigest(),
                    "verified_actual": "ㄉㄢˋ", "visual_confirmation": "Y", "note": "visual only",
                })
            meta = {
                "bundle_schema_version": sp.GPT_DECISION_BUNDLE_SCHEMA_VERSION,
                "version": sp.VERSION, "session_id": "s1",
                "session_schema_version": sp.SESSION_SCHEMA_VERSION,
                "workbook_schema_version": sp.WORKBOOK_SCHEMA_VERSION,
                "review_id_schema_version": sp.REVIEW_ID_SCHEMA_VERSION,
            }
            group = {"group_id": "agr_" + "1" * 24, "members": [entry], "kind": "OCCURRENCE_ONLY", "exact_key": ("occ_" + hashlib.sha256(b"occ1").hexdigest()), "occurrence_count": 1}
            with patch.object(sp, "load_or_initialize_db", return_value={}), \
                 patch.object(sp, "materialize_ledger", side_effect=[[entry], [entry], [dict(entry, actual="ㄉㄢˋ")]]), \
                 patch.object(sp, "build_actual_group_for_entry", return_value=group), \
                 patch.object(sp, "apply_verified_actual_group", return_value={"target_occurrence_ids": [entry["occurrence_id"]],
                                                                           "verified_occurrence_ids": [entry["occurrence_id"]], "reading": "ㄉㄢˋ"}) as apply_mock, \
                 patch.object(sp, "_clear_actual_dependent_events", return_value=0), \
                 patch.object(sp, "refresh_actual_project", return_value=folder / "report.xlsx") as refresh_mock:
                n, removed, report = sp.import_actual_occurrence_decisions(folder, csv_path, package_meta=meta)
            self.assertEqual(n, 1)
            self.assertEqual(removed, 0)
            self.assertEqual(report, folder / "report.xlsx")
            apply_mock.assert_called_once()
            refresh_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
