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

import fitz

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import standalone_proofread as sp  # noqa: E402
from export_pdf_text_diagnostics import TrueTypeGlyphInspector, normalize_basefont  # noqa: E402


def _page_two_egg_source(pdf: Path) -> dict:
    """Give this controller fixture a genuine, sealed native source on page 2."""
    from tests.actual_source_test_support import register_fixture_glyph, _font, _GLYPHS

    digest = register_fixture_glyph("single-bundle-egg")
    with fitz.open() as document:
        document.new_page(width=200, height=200)
        page = document.new_page(width=200, height=200)
        page.insert_font(fontname="BundleEggFixture", fontbuffer=_font(_GLYPHS[digest], "蛋"))
        page.insert_text((20, 36), "蛋", fontname="BundleEggFixture", fontsize=20)
        document.save(pdf)
    with fitz.open(pdf) as document:
        page = document[1]
        [(span, char)] = [(span, char) for span in page.get_texttrace() for char in span["chars"]]
        [(xref, *_rest)] = [font for font in page.get_fonts(full=True)
                           if normalize_basefont(font[3]) == normalize_basefont(span["font"])]
        raw_sha = TrueTypeGlyphInspector(document.extract_font(xref)[3]).glyph_sha256(char[1])
        assert chr(char[0]) == "蛋" and raw_sha == digest
        bounds = dict(zip(("x0", "y0", "x1", "y1"), char[3]))
        return {"font_xref": xref, "glyph_id": char[1], "zhuyin_component_id": char[1],
                **bounds, "source_record": {
                    "pdf": str(pdf), "pdf_name": pdf.name, "pdf_sha256": sp.sha256_file(pdf),
                    "實體頁碼": 2, "字元": "蛋", "font_xref": xref,
                    "glyph_id_字形索引": char[1], "注音元件ID": char[1],
                    "TTF字形SHA256": raw_sha, **bounds}}


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
                "ledger_schema_version": sp.LEDGER_SCHEMA_VERSION,
                "workbook_schema_version": sp.WORKBOOK_SCHEMA_VERSION,
                "review_id_schema_version": sp.REVIEW_ID_SCHEMA_VERSION,
                "pdfs": [],
                "records": [],
            }
            sp.seal_manifest(manifest)
            (folder / "校對工作階段.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            # Keep the source guard real: this orchestration fixture has its
            # own finite PDF, not a source-less mocked READABLE result.
            pdf = folder / "a.pdf"
            source = _page_two_egg_source(pdf)
            info = {"pdf": str(pdf), "pdf_name": pdf.name, "pdf_sha256": sp.sha256_file(pdf)}
            for kind, directory in [("actual", "01_實際注音"), ("candidate", "02_候選報告")]:
                path = folder / directory / (kind + ".xlsx")
                path.parent.mkdir()
                path.write_bytes(("sealed-controller-" + kind).encode())
                info[kind + "_workbook"] = str(path)
                info[kind + "_workbook_sha256"] = sp.sha256_file(path)
            manifest["pdfs"] = [info]
            sp.json_save(folder / "校對工作階段.json", sp.seal_manifest(manifest))
            entry = {
                "occurrence_id": ("occ_" + hashlib.sha256(b"occ1").hexdigest()),
                "review_id": "rev1",
                "pdf_name": "a.pdf",
                "pdf": str(pdf),
                "pdf_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
                "physical_page": 2,
                "char": "蛋",
                "actual": "ㄊㄢˊ",
                "actual_evidence": "glyph-evidence",
                "stable_key": "k1",
                "state": "DIFFERENCE_PENDING_CONFIRMATION",
                **source,
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
            group = sp.build_actual_group_for_entry([entry],entry)
            # The shared prewrite guard rereads the durable DB and ledger.
            # Keep this orchestration fixture stable across those reads, then
            # expose the changed actual only after the mocked apply has run.
            db = sp.normalize_db({})
            sp.json_save(folder / "人工判定資料庫.json", db)
            # Bind the actual source to the same immutable occurrence inventory.
            manifest['records']=[dict(entry)]
            sp.json_save(folder / '校對工作階段.json',sp.seal_manifest(manifest))
            original_db = (folder / "人工判定資料庫.json").read_bytes()
            with patch.object(sp, "load_or_initialize_db", return_value=db), \
                 patch.object(sp, "materialize_ledger", side_effect=lambda *_args:
                              [dict(entry, actual="ㄉㄢˋ") if apply_mock.called else entry]), \
                 patch.object(sp, "build_actual_group_for_entry", return_value=group), \
                 patch.object(sp, "apply_verified_actual_group", return_value={"target_occurrence_ids": [entry["occurrence_id"]],
                                                                           "verified_occurrence_ids": [entry["occurrence_id"]], "reading": "ㄉㄢˋ"}) as apply_mock, \
                 patch.object(sp, "_clear_actual_dependent_events", return_value=0), \
                 patch.object(sp, "refresh_actual_project", side_effect=lambda *a, **kw: (kw["_refresh_status"].update(cleared_actual_dependent_event_count=0), folder / "report.xlsx")[1]) as refresh_mock:
                n, removed, report = sp.import_actual_occurrence_decisions(folder, csv_path, package_meta=meta)
            self.assertEqual(n, 1)
            self.assertEqual(removed, 0)
            self.assertEqual(report, folder / "report.xlsx")
            apply_mock.assert_called_once()
            refresh_mock.assert_called_once()
            self.assertEqual((folder / "人工判定資料庫.json").read_bytes(), original_db)


if __name__ == "__main__":
    unittest.main()
