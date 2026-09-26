"""Real pipeline orchestration with a synthetic, deterministic actual decoder.

The PDF rendering, candidate resolver, artifact seals, GUI preview, save
service, and portability adapter are real. Only the glyph decoder's output is
injected: this is synthetic acceptance, not real textbook acceptance.
"""

import hashlib
import json
import tkinter as tk
from pathlib import Path
from unittest.mock import patch

import fitz
import pytest
from openpyxl import Workbook, load_workbook

import pdf_portability as portability
import review_gui
import standalone_proofread as sp
import occurrence_ledger as ol
import actual_review as ar
from review_display import occurrence_preview
from review_save_service import ReviewSaveService
from tests.test_project_repair_integration_v562 import ACTUAL_COLUMNS, EXCLUDED_COLUMNS


def _pdfs(folder):
    first, second = folder / "download-a.pdf", folder / "download-b.pdf"
    with fitz.open() as document:
        page = document.new_page(width=400, height=220)
        page.insert_text((50, 90), "角角", fontname="china-t", fontsize=20)
        page.insert_text((80, 76), "ㄐㄩㄝˊ", fontname="china-t", fontsize=8)
        page.insert_text((114, 76), "ㄐㄩㄝˊ", fontname="china-t", fontsize=8)
        document.set_metadata({"title": "download A"})
        document.save(first, deflate=False, garbage=0)
        document.set_metadata({"title": "download B"})
        document.save(second, deflate=True, garbage=4)
    assert hashlib.sha256(first.read_bytes()).digest() != hashlib.sha256(second.read_bytes()).digest()
    assert portability._page_signatures(first) == portability._page_signatures(second)
    return first, second


def _synthetic_decode(pdf, output, *args, global_snapshot=None):
    """Emit actual rows from visible PDF char boxes, without expected input."""
    fingerprint = args[-2]
    digest = sp.sha256_file(pdf)
    with fitz.open(pdf) as document:
        chars = [char for block in document[0].get_text("rawdict")["blocks"]
                 for line in block.get("lines", []) for span in line.get("spans", [])
                 for char in span.get("chars", []) if char.get("c") == "角"]
    assert len(chars) == 2
    rows = []
    for index, char in enumerate(chars, 1):
        x0, y0, x1, y1 = char["bbox"]
        rows.append({
            "實體頁碼": 1, "課本頁": 1, "頁面標籤": "1", "字元": "角",
            "實際注音": "ㄐㄩㄝˊ", "解碼狀態": "已映射",
            "解碼依據": "synthetic independent PDF glyph fixture",
            "穩定注音鍵": f"fixture-{index}", "群組注音鍵": f"fixture-{index}",
            "font": "synthetic-font", "font_xref": 1, "注音元件ID": index,
            "glyph_id_字形索引": index, "x0": x0, "y0": y0, "x1": x1, "y1": y1,
            "source_row_number": index + 1,
            "ledger_schema_version": ol.LEDGER_SCHEMA_VERSION,
            "workbook_schema_version": ol.WORKBOOK_SCHEMA_VERSION,
        })
    ol.prepare_occurrence_rows(digest, rows)
    workbook = Workbook()
    ws = workbook.active
    ws.title = "實際注音"
    columns = [*ACTUAL_COLUMNS, "注音結構偵測來源", "CFF符號簽名", "CFF聲調"]
    ws.append(columns)
    for row in rows:
        ws.append([row.get(column, "") for column in columns])
    excluded = workbook.create_sheet("結構偵測排除")
    excluded.append(EXCLUDED_COLUMNS)
    metadata = workbook.create_sheet("v5.2中繼資料")
    metadata.append(["項目", "內容"])
    metadata.append(["workbook_schema_version", ol.WORKBOOK_SCHEMA_VERSION])
    metadata.append(["ledger_schema_version", ol.LEDGER_SCHEMA_VERSION])
    metadata.append(["pdf_sha256", digest])
    metadata.append(["actual_asset_fingerprint", fingerprint["fingerprint"]])
    metadata.append(["actual_asset_fingerprint_components", json.dumps(fingerprint["components"], ensure_ascii=False, sort_keys=True)])
    summary = workbook.create_sheet("摘要")
    summary.append(["項目", "內容"])
    summary.append(["偵測到注音字形筆數", len(rows)])
    workbook.save(output)
    workbook.close()


def test_full_pipeline_different_sha_continues_and_returns(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-localappdata"))
    first, second = _pdfs(tmp_path)
    a, b = tmp_path / "project-a", tmp_path / "project-b"
    with patch.object(sp, "decode", side_effect=_synthetic_decode), \
         patch.object(sp, "actual_workbook_global_exact_dependencies", return_value=()), \
         patch("check_pronunciation_candidates.actual_workbook_global_exact_dependencies", return_value=()):
        sp.run_pipeline_pdfs([first], a, defer_excel_reports=True)
        a_manifest = sp.json_load_strict(a / "校對工作階段.json")
        assert len(a_manifest["records"]) == 2
        first_event = {"action": "確認非校對範圍", "exclusion_reason": "synthetic fixture",
                       "exclusion_evidence": "PDF page 1 visual position 1"}
        ReviewSaveService(a).save_event(a_manifest["records"][0]["review_id"], first_event)
        portability.prepare_portable_project(a)
        first.rename(tmp_path / "source-pdf-not-on-b.pdf")
        result = portability.continue_project(a, second, b)
        assert result["imported"] == 1
        b_manifest = sp.json_load_strict(b / "校對工作階段.json")
        assert len(b_manifest["records"]) == 2
        assert len(sp.json_load_strict(b / "待人工確認.json")["pending"]) == 1
        b_status = sp.json_load_strict(b / "pipeline_status.json")
        assert b_status["user_report"] == str(b / "注音校對_最終報告.xlsx")
        workbook = load_workbook(b / "注音校對_最終報告.xlsx", read_only=True)
        try:
            assert any(row[0] == "人工判定" for row in workbook["人工與公司規則"].values)
        finally:
            workbook.close()
        pixels, box, _ = occurrence_preview(b_manifest["records"][1], output_dir=b)
        assert pixels.width > 0 and box is not None
        second_event = {"action": "確認非校對範圍", "exclusion_reason": "synthetic fixture",
                        "exclusion_evidence": "PDF page 1 visual position 2"}
        ReviewSaveService(b).save_event(b_manifest["records"][1]["review_id"], second_event)
        portability.prepare_portable_project(b)
        (tmp_path / "source-pdf-not-on-b.pdf").rename(first)
        second.rename(tmp_path / "target-pdf-not-on-a.pdf")
        back = portability.import_project_decisions(b, a)
        assert back["imported"] == 1 and back["duplicates"] == 1
        assert len(sp.json_load_strict(a / "人工判定資料庫.json")["events"]) == 2
        assert sp.json_load_strict(a / "待人工確認.json")["pending"] == []
        assert (a / "注音校對_最終報告.xlsx").is_file()
        assert sp.json_load_strict(a / "pipeline_status.json")["user_report"] == str(a / "注音校對_最終報告.xlsx")
        assert len(sp.materialize_ledger(a_manifest, sp.json_load_strict(a / "人工判定資料庫.json"))) == 2
    assert not list((tmp_path / "isolated-localappdata").rglob("*.sqlite*"))


def test_real_pipeline_actual_override_refresh_publishes_current_manifest(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-localappdata"))
    first, second = _pdfs(tmp_path)
    a, b = tmp_path / "source", tmp_path / "continued"
    with patch.object(sp, "decode", side_effect=_synthetic_decode), \
         patch.object(sp, "actual_workbook_global_exact_dependencies", return_value=()), \
         patch("check_pronunciation_candidates.actual_workbook_global_exact_dependencies", return_value=()):
        sp.run_pipeline_pdfs([first], a, defer_excel_reports=True)
        original = sp.json_load_strict(a / "校對工作階段.json")
        entry = original["records"][0]
        key = ar._override_key_from_entry(entry)
        fields = ("pdf_contains", "pdf_excludes", "page", "target_char", "stable_key", "x0", "y0")
        override = dict(zip(fields, key)) | {
            "actual_reading": "ㄐㄩㄝˊ", "source": "source direct visual", "note": "verified page 1",
        }
        ar._write_csv(sp.project_actual_evidence_root(a) / ar.OCCURRENCE_OVERRIDE_FILE,
                      ar.OVERRIDE_HEADERS, [override])
        sp.refresh_actual_project(a)
        refreshed = sp.json_load_strict(a / "校對工作階段.json")
        assert refreshed["session_id"] == original["session_id"]
        portability.prepare_portable_project(a)
        first.rename(tmp_path / "source-away.pdf")
        result = portability.continue_project(a, second, b)
        assert result["matched_actual_overrides"] == 1
        assert not (b / portability.INCOMPLETE_FILE).exists()
        b_manifest = sp.json_load_strict(b / "校對工作階段.json")
        sp.validate_manifest_integrity(b_manifest)
        assert b_manifest["pdfs"][0]["actual_workbook_sha256"] == sp.sha256_file(
            b / "01_實際注音" / Path(b_manifest["pdfs"][0]["actual_workbook"]).name)
        assert (b / "待人工確認.json").is_file()
        assert (b / "注音校對_最終報告.xlsx").is_file()
        target_overrides = ar._read_csv(sp.project_actual_evidence_root(b) / ar.OCCURRENCE_OVERRIDE_FILE,
                                        ar.OVERRIDE_HEADERS)
        assert len([row for row in target_overrides if row["source"] ==
                    "portable occurrence-local actual (no Global quorum)"]) == 1
        assert portability._load_project(b)[0]["session_id"] == b_manifest["session_id"]
    assert not list((tmp_path / "isolated-localappdata").rglob("*.sqlite*"))


def test_review_app_reopens_continued_project(tmp_path, monkeypatch):
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk display unavailable: {exc}")
    root.withdraw()
    try:
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-localappdata"))
        first, second = _pdfs(tmp_path)
        a, b = tmp_path / "project-a", tmp_path / "project-b"
        with patch.object(sp, "decode", side_effect=_synthetic_decode), \
             patch.object(sp, "actual_workbook_global_exact_dependencies", return_value=()), \
             patch("check_pronunciation_candidates.actual_workbook_global_exact_dependencies", return_value=()):
            sp.run_pipeline_pdfs([first], a, defer_excel_reports=True)
            manifest = sp.json_load_strict(a / "校對工作階段.json")
            ReviewSaveService(a).save_event(
                manifest["records"][0]["review_id"],
                {"action": "確認非校對範圍", "exclusion_reason": "synthetic fixture",
                 "exclusion_evidence": "PDF page 1 visual position 1"},
            )
            portability.prepare_portable_project(a)
            first.rename(tmp_path / "pdf-unavailable-on-b.pdf")
            portability.continue_project(a, second, b)
            window = tk.Toplevel(root)
            try:
                app = review_gui.ReviewApp(window, b)
                window.update()
                assert len(app.manifest["records"]) == 2
                assert len(app.db["events"]) == 1
                assert app.image.photo is not None
                assert app.current()["source_row_number"] == 3
            finally:
                window.destroy()
    finally:
        root.destroy()


def test_real_pipeline_conflict_stays_pending_until_new_local_adjudication(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-localappdata"))
    first, second = _pdfs(tmp_path)
    third = tmp_path / "download-c.pdf"
    with fitz.open(first) as document:
        document.set_metadata({"title": "download C"})
        document.save(third, deflate=True, garbage=4)
    assert portability._page_signatures(first) == portability._page_signatures(third)
    a, b, merged = [tmp_path / name for name in ("project-a", "project-b", "merged")]
    with patch.object(sp, "decode", side_effect=_synthetic_decode), \
         patch.object(sp, "actual_workbook_global_exact_dependencies", return_value=()), \
         patch("check_pronunciation_candidates.actual_workbook_global_exact_dependencies", return_value=()):
        sp.run_pipeline_pdfs([first], a, defer_excel_reports=True)
        sp.run_pipeline_pdfs([second], b, defer_excel_reports=True)
        a_manifest = sp.json_load_strict(a / "校對工作階段.json")
        b_manifest = sp.json_load_strict(b / "校對工作階段.json")
        ReviewSaveService(a).save_event(
            a_manifest["records"][0]["review_id"],
            {"action": "確認非校對範圍", "exclusion_reason": "A local visual decision",
             "exclusion_evidence": "A PDF page 1"},
        )
        ReviewSaveService(b).save_event(
            b_manifest["records"][0]["review_id"],
            {"action": "確認非校對範圍", "exclusion_reason": "B different decision",
             "exclusion_evidence": "B PDF page 1"},
        )
        result = portability.merge_projects([a, b], third, merged)
        assert len(result["conflicts"]) == 1
        manifest = sp.json_load_strict(merged / "校對工作階段.json")
        db = sp.json_load_strict(merged / "人工判定資料庫.json")
        conflicted_id = manifest["records"][0]["review_id"]
        assert conflicted_id not in db["events"]
        assert sp.materialize_ledger(manifest, db)[0]["active_review"] is True
        assert len(sp.json_load_strict(merged / "待人工確認.json")["pending"]) == 2
        assert sp.json_load_strict(merged / "pipeline_status.json")["status"] != "PROOFREAD_COMPLETE"
        workbook = load_workbook(merged / "注音校對_最終報告.xlsx", read_only=True)
        try:
            assert not any(row[0] == "人工判定" for row in workbook["人工與公司規則"].values)
        finally:
            workbook.close()
        receipt = sp.json_load_strict(merged / portability.CONFLICT_FILE)
        ReviewSaveService(merged).save_event(
            conflicted_id,
            {"action": "確認非校對範圍", "exclusion_reason": "new local check",
             "exclusion_evidence": "C PDF page 1 direct visual"},
        )
        resolved = sp.json_load_strict(merged / "人工判定資料庫.json")
        assert resolved["events"][conflicted_id]["portability_conflict_resolution"]["original_conflicts"] == receipt["conflicts"]
        sp.save_pending_json(merged, manifest, resolved)
        sp.generate_report(merged, manifest, resolved)
        assert len(sp.json_load_strict(merged / "待人工確認.json")["pending"]) == 1
        assert sp.json_load_strict(merged / portability.CONFLICT_FILE) == receipt
    assert not list((tmp_path / "isolated-localappdata").rglob("*.sqlite*"))
