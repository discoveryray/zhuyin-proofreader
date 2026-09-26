"""Real pipeline orchestration with a synthetic, deterministic actual decoder.

The PDF rendering, candidate resolver, artifact seals, GUI preview, save
service, and portability adapter are real. Only the glyph decoder's output is
injected: this is synthetic acceptance, not real textbook acceptance.
"""

import hashlib
import json
import sys
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
from tests.test_pdf_portability import pdf as small_pdf, project as small_project


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


def _synthetic_unresolved_decode(pdf, output, *args, global_snapshot=None):
    _synthetic_decode(pdf, output, *args, global_snapshot=global_snapshot)
    override_rows = ar._read_csv(sp.project_actual_evidence_root(Path(output).parent.parent)
                                 / ar.OCCURRENCE_OVERRIDE_FILE, ar.OVERRIDE_HEADERS)
    workbook = load_workbook(output)
    sheet = workbook["實際注音"]
    headers = [cell.value for cell in sheet[1]]
    for row in range(2, sheet.max_row + 1):
        stable_key = str(sheet.cell(row, headers.index("穩定注音鍵") + 1).value or "")
        matched = next((item for item in override_rows if item["pdf_contains"] == Path(pdf).stem
                        and item["stable_key"] == stable_key), None)
        sheet.cell(row, headers.index("實際注音") + 1,
                   matched["actual_reading"] if matched else "")
        sheet.cell(row, headers.index("解碼狀態") + 1,
                   "已映射" if matched else "未辨識")
        sheet.cell(row, headers.index("解碼依據") + 1, "synthetic unresolved glyph")
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
        pending_workbook = load_workbook(b / "待判定候選_給GPT.xlsx", read_only=True)
        try:
            assert portability.EXCEL_PROOF_SHEET in pending_workbook.sheetnames
        finally:
            pending_workbook.close()
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


def test_partial_portable_publication_blocks_report_and_repair_until_new_success(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-localappdata"))
    first, second = _pdfs(tmp_path)
    source, partial, completed, partial_proof = [tmp_path / name for name in
                                                  ("source", "partial", "completed", "partial-proof")]
    with patch.object(sp, "decode", side_effect=_synthetic_decode), \
         patch.object(sp, "actual_workbook_global_exact_dependencies", return_value=()), \
         patch("check_pronunciation_candidates.actual_workbook_global_exact_dependencies", return_value=()):
        sp.run_pipeline_pdfs([first], source, defer_excel_reports=True)
        source_manifest = sp.json_load_strict(source / "校對工作階段.json")
        ReviewSaveService(source).save_event(source_manifest["records"][0]["review_id"], {
            "action": "確認非校對範圍", "exclusion_reason": "source visual",
            "exclusion_evidence": "source PDF page 1",
        })
        portability.prepare_portable_project(source)
        with patch.object(portability, "_publish_portable_outputs", side_effect=OSError("disk full")):
            with pytest.raises(OSError, match="disk full"):
                portability.continue_project(source, second, partial)
        marker = partial / portability.INCOMPLETE_FILE
        assert marker.is_file()
        watched = [marker, partial / "校對工作階段.json", partial / "人工判定資料庫.json",
                   partial / "待人工確認.json", partial / "pipeline_status.json",
                   partial / "注音校對_最終報告.xlsx"]
        before = {path: path.read_bytes() if path.exists() else None for path in watched}
        for operation in ("--report-only", "--repair-project"):
            with patch.object(sys, "argv", ["standalone_proofread.py", "-o", str(partial), operation]):
                with pytest.raises(ValueError, match="接續未完成"):
                    sp.main()
            assert {path: path.read_bytes() if path.exists() else None for path in watched} == before
        assert marker.is_file()

        source_db_before = (source / "人工判定資料庫.json").read_bytes()
        original_writer = portability.write_excel_content_proof
        def fail_final_excel(*args, **kwargs):
            if kwargs.get("allow_internal_incomplete"):
                raise OSError("final proof disk full")
            return original_writer(*args, **kwargs)
        with patch.object(portability, "write_excel_content_proof", side_effect=fail_final_excel):
            with pytest.raises(OSError, match="final proof disk full"):
                portability.continue_project(source, second, partial_proof)
        assert (partial_proof / portability.INCOMPLETE_FILE).exists()
        assert (source / "人工判定資料庫.json").read_bytes() == source_db_before
        with patch.object(sys, "argv", ["standalone_proofread.py", "-o", str(partial_proof), "--report-only"]):
            with pytest.raises(ValueError, match="接續未完成"):
                sp.main()

        portability.continue_project(source, second, completed)
        assert not (completed / portability.INCOMPLETE_FILE).exists()
        with patch.object(sys, "argv", ["standalone_proofread.py", "-o", str(completed), "--report-only"]):
            assert sp.main() == 0
        assert (completed / "注音校對_最終報告.xlsx").is_file()
        assert sp.json_load_strict(completed / "pipeline_status.json")["user_report"] == str(
            completed / "注音校對_最終報告.xlsx")
    assert not list((tmp_path / "isolated-localappdata").rglob("*.sqlite*"))


@pytest.mark.parametrize("sha_mode", ["same", "different"])
def test_direct_expected_excel_import_maps_different_sha_session(tmp_path, monkeypatch, sha_mode):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-localappdata"))
    first, different = _pdfs(tmp_path)
    second = first if sha_mode == "same" else different
    a, b = tmp_path / "project-a", tmp_path / "project-b"
    with patch.object(sp, "decode", side_effect=_synthetic_decode), \
         patch.object(sp, "actual_workbook_global_exact_dependencies", return_value=()), \
         patch("check_pronunciation_candidates.actual_workbook_global_exact_dependencies", return_value=()):
        sp.run_pipeline_pdfs([first], a, defer_excel_reports=True)
        sp.run_pipeline_pdfs([second], b, defer_excel_reports=True)
        a_manifest = sp.json_load_strict(a / "校對工作階段.json")
        b_manifest = sp.json_load_strict(b / "校對工作階段.json")
        assert a_manifest["session_id"] != b_manifest["session_id"]
        assert (a_manifest["pdfs"][0]["pdf_sha256"] == b_manifest["pdfs"][0]["pdf_sha256"]) == (sha_mode == "same")
        workbook_path = sp.export_pending_for_gpt(a)
        workbook = load_workbook(workbook_path)
        assert workbook[portability.EXCEL_PROOF_SHEET].sheet_state == "veryHidden"
        sheet = workbook["待判定候選"]
        headers = [cell.value for cell in sheet[1]]
        sheet.cell(3, headers.index("action") + 1, "確認非校對範圍")
        sheet.cell(3, headers.index("exclusion_reason") + 1, "source visual")
        sheet.cell(3, headers.index("exclusion_evidence") + 1, "PDF page 1")
        renamed = tmp_path / "expected與差異判定_匯入.xlsx"
        workbook.save(renamed)
        workbook.close()
        assert sp.detect_gpt_workbook_kind(renamed) == "expected"
        count, _, report = sp.import_gpt_decisions(b, renamed)
        assert count == 1 and report.is_file()
        b_db = sp.json_load_strict(b / "人工判定資料庫.json")
        event = b_db["events"][b_manifest["records"][1]["review_id"]]
        assert event["portability_source"]["pdf_sha256"] == a_manifest["pdfs"][0]["pdf_sha256"]
        assert event["portability_source"]["target_pdf_sha256"] == b_manifest["pdfs"][0]["pdf_sha256"]
        assert sp.import_gpt_decisions(b, renamed)[0] == 0
        assert len(sp.json_load_strict(b / "待人工確認.json")["pending"]) == 1
        assert b_manifest["records"][0]["review_id"] not in b_db["events"]
        assert portability._load_project(b)[0]["session_id"] == b_manifest["session_id"]
    assert not list((tmp_path / "isolated-localappdata").rglob("*.sqlite*"))


@pytest.mark.parametrize("sha_mode", ["same", "different"])
def test_direct_actual_excel_import_is_local_only_across_different_sha(tmp_path, monkeypatch, sha_mode):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-localappdata"))
    first, different = _pdfs(tmp_path)
    second = first if sha_mode == "same" else different
    a, b = tmp_path / "source", tmp_path / "target"
    with patch.object(sp, "decode", side_effect=_synthetic_unresolved_decode), \
         patch.object(sp, "actual_workbook_global_exact_dependencies", return_value=()), \
         patch("check_pronunciation_candidates.actual_workbook_global_exact_dependencies", return_value=()):
        sp.run_pipeline_pdfs([first], a, defer_excel_reports=True)
        sp.run_pipeline_pdfs([second], b, defer_excel_reports=True)
        a_manifest = sp.json_load_strict(a / "校對工作階段.json")
        b_manifest = sp.json_load_strict(b / "校對工作階段.json")
        assert a_manifest["session_id"] != b_manifest["session_id"]
        assert (a_manifest["pdfs"][0]["pdf_sha256"] == b_manifest["pdfs"][0]["pdf_sha256"]) == (sha_mode == "same")
        exported = sp.export_actual_pending_for_gpt(a)
        assert exported is not None
        workbook_path = a / "actual待判定_GPT包" / "actual待判定_給GPT.xlsx"
        workbook = load_workbook(workbook_path)
        assert workbook[portability.EXCEL_PROOF_SHEET].sheet_state == "veryHidden"
        sheet = workbook["actual待判定"]
        headers = [cell.value for cell in sheet[1]]
        for key, value in {"decision": "VERIFIED", "actual_reading": "ㄐㄩㄝˊ",
                           "confidence": "高", "sample_a_checked": "Y", "note": "source page 1"}.items():
            sheet.cell(2, headers.index(key) + 1, value)
        renamed = tmp_path / "actual視覺判定_匯入.xlsx"
        workbook.save(renamed)
        workbook.close()
        assert sp.detect_gpt_workbook_kind(renamed) == "actual"
        result = sp.import_actual_gpt_decisions(b, renamed)
        assert result[0] == 1
        assert result.status["global_promotion_delivery"]["status"] == "NO_PENDING"
        assert not (b / portability.INCOMPLETE_FILE).exists()
        refreshed = sp.json_load_strict(b / "校對工作階段.json")
        assert refreshed["session_id"] == b_manifest["session_id"]
        assert portability._load_project(b)[0]["session_id"] == b_manifest["session_id"]
        assert sp.import_actual_gpt_decisions(b, renamed)[0] == 0
    assert not list((tmp_path / "isolated-localappdata").rglob("*.sqlite*"))


def test_old_filled_expected_excel_imported_in_a_can_gain_present_proof(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-localappdata"))
    first, second = _pdfs(tmp_path)
    a, b = tmp_path / "source", tmp_path / "target"
    with patch.object(sp, "decode", side_effect=_synthetic_decode), \
         patch.object(sp, "actual_workbook_global_exact_dependencies", return_value=()), \
         patch("check_pronunciation_candidates.actual_workbook_global_exact_dependencies", return_value=()):
        sp.run_pipeline_pdfs([first], a, defer_excel_reports=True)
        sp.run_pipeline_pdfs([second], b, defer_excel_reports=True)
        old = tmp_path / "已完成的舊版判定.xlsx"
        workbook = load_workbook(sp.export_pending_for_gpt(a))
        del workbook[portability.EXCEL_PROOF_SHEET]
        sheet = workbook["待判定候選"]
        headers = [cell.value for cell in sheet[1]]
        for key, value in {"action": "確認非校對範圍", "exclusion_reason": "原頁範圍",
                           "exclusion_evidence": "A 原頁檢查"}.items():
            sheet.cell(2, headers.index(key) + 1, value)
        workbook.save(old)
        workbook.close()
        assert sp.import_gpt_decisions(a, old)[0] == 1
        before = old.read_bytes()
        portable = portability.attach_filled_excel_proof(a, old)
        assert old.read_bytes() == before and portable.is_file()
        assert sp.import_gpt_decisions(b, portable)[0] == 1
        entry = sp.json_load_strict(b / "校對工作階段.json")["records"][0]
        event = sp.json_load_strict(b / "人工判定資料庫.json")["events"][entry["review_id"]]
        assert event["portability_source"]["source_excel_decision"]["action"] == "確認非校對範圍"
        assert sp.import_gpt_decisions(b, portable)[0] == 0
    assert not list((tmp_path / "isolated-localappdata").rglob("*.sqlite*"))


def test_old_filled_actual_excel_imported_in_a_can_gain_present_proof(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-localappdata"))
    first, second = _pdfs(tmp_path)
    a, b = tmp_path / "source", tmp_path / "target"
    with patch.object(sp, "decode", side_effect=_synthetic_unresolved_decode), \
         patch.object(sp, "actual_workbook_global_exact_dependencies", return_value=()), \
         patch("check_pronunciation_candidates.actual_workbook_global_exact_dependencies", return_value=()):
        sp.run_pipeline_pdfs([first], a, defer_excel_reports=True)
        sp.run_pipeline_pdfs([second], b, defer_excel_reports=True)
        sp.export_actual_pending_for_gpt(a)
        old = tmp_path / "已完成的舊版actual.xlsx"
        workbook = load_workbook(a / "actual待判定_GPT包" / "actual待判定_給GPT.xlsx")
        del workbook[portability.EXCEL_PROOF_SHEET]
        sheet = workbook["actual待判定"]
        headers = [cell.value for cell in sheet[1]]
        for key, value in {"decision": "VERIFIED", "actual_reading": "ㄐㄩㄝˊ",
                           "confidence": "高", "sample_a_checked": "Y", "note": "A 原頁"}.items():
            sheet.cell(2, headers.index(key) + 1, value)
        workbook.save(old)
        workbook.close()
        assert sp.import_actual_gpt_decisions(a, old)[0] == 1
        before = old.read_bytes()
        portable = portability.attach_filled_excel_proof(a, old)
        assert old.read_bytes() == before and portable.is_file()
        assert sp.import_actual_gpt_decisions(b, portable)[0] == 1
        assert sp.import_actual_gpt_decisions(b, portable)[0] == 0
    assert not list((tmp_path / "isolated-localappdata").rglob("*.sqlite*"))


def test_excel_expected_conflict_and_same_decision_keep_sources(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-localappdata"))
    first, second = _pdfs(tmp_path)
    a, b = tmp_path / "source", tmp_path / "target"
    with patch.object(sp, "decode", side_effect=_synthetic_decode), \
         patch.object(sp, "actual_workbook_global_exact_dependencies", return_value=()), \
         patch("check_pronunciation_candidates.actual_workbook_global_exact_dependencies", return_value=()):
        sp.run_pipeline_pdfs([first], a, defer_excel_reports=True)
        sp.run_pipeline_pdfs([second], b, defer_excel_reports=True)
        exported = sp.export_pending_for_gpt(a)
        first_xlsx, same_xlsx, conflict_xlsx = [tmp_path / name for name in
                                               ("first.xlsx", "same.xlsx", "conflict.xlsx")]
        workbook = load_workbook(exported)
        sheet = workbook["待判定候選"]
        headers = [cell.value for cell in sheet[1]]
        sheet.cell(2, headers.index("action") + 1, "確認非校對範圍")
        sheet.cell(2, headers.index("exclusion_reason") + 1, "same judgment")
        sheet.cell(2, headers.index("exclusion_evidence") + 1, "source page")
        workbook.save(first_xlsx)
        workbook.properties.title = "same decision from another saved workbook"
        workbook.save(same_xlsx)
        sheet.cell(2, headers.index("exclusion_reason") + 1, "different judgment")
        workbook.save(conflict_xlsx)
        workbook.close()
        assert sp.import_gpt_decisions(b, first_xlsx)[0] == 1
        assert sp.import_gpt_decisions(b, same_xlsx)[0] == 0
        # A second, byte-distinct Excel with the same decision is provenance,
        # not a second active verdict.
        target_id = sp.json_load_strict(b / "校對工作階段.json")["records"][0]["review_id"]
        assert len(sp.json_load_strict(b / "人工判定資料庫.json")["events"][target_id]
                   .get("portability_duplicate_sources", [])) == 1
        db_path = b / "人工判定資料庫.json"
        marker_path = b / portability.INCOMPLETE_FILE
        receipt_path = b / portability.CONFLICT_FILE
        before = {path: path.read_bytes() if path.exists() else None
                  for path in (db_path, marker_path, receipt_path)}
        original_save = sp.json_save
        def fail_target_db(path, *args, **kwargs):
            if Path(path) == db_path:
                raise OSError("disk full before DB commit")
            return original_save(path, *args, **kwargs)
        with patch.object(sp, "json_save", side_effect=fail_target_db):
            with pytest.raises(OSError, match="before DB commit"):
                sp.import_gpt_decisions(b, conflict_xlsx)
        assert {path: path.read_bytes() if path.exists() else None
                for path in (db_path, marker_path, receipt_path)} == before
        watched = [db_path, marker_path, receipt_path, b / "待人工確認.json",
                   b / "注音校對_最終報告.xlsx", b / "pipeline_status.json"]
        before_publish = {path: path.read_bytes() if path.exists() else None for path in watched}
        with patch.object(portability, "_publish_portable_outputs", side_effect=OSError("report disk full")):
            with pytest.raises(OSError, match="report disk full"):
                sp.import_gpt_decisions(b, conflict_xlsx)
        assert {path: path.read_bytes() if path.exists() else None for path in watched} == before_publish
        with pytest.raises(ValueError, match="不同判定"):
            sp.import_gpt_decisions(b, conflict_xlsx)
        receipt = sp.json_load_strict(b / portability.CONFLICT_FILE)
        assert len(receipt["conflicts"]) == 1
        assert receipt["conflicts"][0]["source_event"]["exclusion_reason"] == "different judgment"
        assert receipt["conflicts"][0]["target_event"]["exclusion_reason"] == "same judgment"
        assert target_id not in sp.json_load_strict(b / "人工判定資料庫.json")["events"]
    assert not list((tmp_path / "isolated-localappdata").rglob("*.sqlite*"))


@pytest.mark.parametrize("kind", ["expected", "actual"])
def test_old_proofless_excel_only_maps_exact_same_sha_and_snapshot(tmp_path, monkeypatch, kind):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-localappdata"))
    first, different = _pdfs(tmp_path)
    a, b, c = [tmp_path / name for name in ("source", "exact-target", "different-target")]
    decoder = _synthetic_decode if kind == "expected" else _synthetic_unresolved_decode
    with patch.object(sp, "decode", side_effect=decoder), \
         patch.object(sp, "actual_workbook_global_exact_dependencies", return_value=()), \
         patch("check_pronunciation_candidates.actual_workbook_global_exact_dependencies", return_value=()):
        sp.run_pipeline_pdfs([first], a, defer_excel_reports=True)
        sp.run_pipeline_pdfs([first], b, defer_excel_reports=True)
        sp.run_pipeline_pdfs([different], c, defer_excel_reports=True)
        if kind == "expected":
            exported = sp.export_pending_for_gpt(a)
            sheet_name, filled = "待判定候選", {
                "action": "確認非校對範圍", "exclusion_reason": "source",
                "exclusion_evidence": "page 1"}
        else:
            sp.export_actual_pending_for_gpt(a)
            exported = a / "actual待判定_GPT包" / "actual待判定_給GPT.xlsx"
            sheet_name, filled = "actual待判定", {
                "decision": "VERIFIED", "actual_reading": "ㄐㄩㄝˊ",
                "confidence": "高", "sample_a_checked": "Y"}
        workbook = load_workbook(exported)
        del workbook[portability.EXCEL_PROOF_SHEET]
        sheet = workbook[sheet_name]
        headers = [cell.value for cell in sheet[1]]
        for key, value in filled.items():
            sheet.cell(2, headers.index(key) + 1, value)
        old = tmp_path / f"old-{kind}.xlsx"
        workbook.save(old)
        workbook.close()
        importer = sp.import_gpt_decisions if kind == "expected" else sp.import_actual_gpt_decisions
        with pytest.raises(ValueError, match="沒有全頁證據|不同 SHA"):
            importer(c, old)
        assert importer(b, old)[0] == 1
        assert importer(b, old)[0] == 0
        assert portability._load_project(b)[0]["session_id"] != sp.json_load_strict(a / "校對工作階段.json")["session_id"]
    assert not list((tmp_path / "isolated-localappdata").rglob("*.sqlite*"))


@pytest.mark.parametrize("change", ["text", "zhuyin", "image"])
def test_excel_full_page_proof_rejects_same_filename_content_changes(tmp_path, monkeypatch, change):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-localappdata"))
    first, _ = _pdfs(tmp_path)
    altered_dir = tmp_path / "other-download"
    altered_dir.mkdir()
    altered = altered_dir / first.name
    with fitz.open(first) as document:
        page = document[0]
        if change == "text":
            page.insert_text((200, 110), "教材新文字", fontname="china-t", fontsize=14)
        elif change == "zhuyin":
            page.insert_text((200, 70), "ㄅㄆㄇ", fontname="china-t", fontsize=12)
        else:
            page.draw_rect(fitz.Rect(200, 130, 245, 170), color=(1, 0, 0), fill=(1, 0, 0))
        document.save(altered)
    assert sp.sha256_file(first) != sp.sha256_file(altered)
    a, b = tmp_path / "source", tmp_path / "target"
    with patch.object(sp, "decode", side_effect=_synthetic_decode), \
         patch.object(sp, "actual_workbook_global_exact_dependencies", return_value=()), \
         patch("check_pronunciation_candidates.actual_workbook_global_exact_dependencies", return_value=()):
        sp.run_pipeline_pdfs([first], a, defer_excel_reports=True)
        sp.run_pipeline_pdfs([altered], b, defer_excel_reports=True)
        workbook = load_workbook(sp.export_pending_for_gpt(a))
        sheet = workbook["待判定候選"]
        headers = [cell.value for cell in sheet[1]]
        for key, value in {"action": "確認非校對範圍", "exclusion_reason": "source",
                           "exclusion_evidence": "page 1"}.items():
            sheet.cell(2, headers.index(key) + 1, value)
        filled = tmp_path / "filled.xlsx"
        workbook.save(filled)
        workbook.close()
        db_path = b / "人工判定資料庫.json"
        before = db_path.read_bytes()
        with pytest.raises(ValueError, match="頁面內容|無法唯一對應"):
            sp.import_gpt_decisions(b, filled)
        assert db_path.read_bytes() == before
    assert not list((tmp_path / "isolated-localappdata").rglob("*.sqlite*"))


def test_actual_excel_conflict_and_precommit_write_failure_preserve_target(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-localappdata"))
    first, second = _pdfs(tmp_path)
    a, b, c = [tmp_path / name for name in ("source", "conflict-target", "write-fail-target")]
    with patch.object(sp, "decode", side_effect=_synthetic_unresolved_decode), \
         patch.object(sp, "actual_workbook_global_exact_dependencies", return_value=()), \
         patch("check_pronunciation_candidates.actual_workbook_global_exact_dependencies", return_value=()):
        sp.run_pipeline_pdfs([first], a, defer_excel_reports=True)
        sp.run_pipeline_pdfs([second], b, defer_excel_reports=True)
        sp.run_pipeline_pdfs([second], c, defer_excel_reports=True)
        sp.export_actual_pending_for_gpt(a)
        workbook = load_workbook(a / "actual待判定_GPT包" / "actual待判定_給GPT.xlsx")
        sheet = workbook["actual待判定"]
        headers = [cell.value for cell in sheet[1]]
        for key, value in {"decision": "VERIFIED", "actual_reading": "ㄐㄩㄝˊ",
                           "confidence": "高", "sample_a_checked": "Y"}.items():
            sheet.cell(2, headers.index(key) + 1, value)
        filled = tmp_path / "first-actual.xlsx"
        workbook.save(filled)
        sheet.cell(2, headers.index("actual_reading") + 1, "ㄐㄩㄝˇ")
        different = tmp_path / "different-actual.xlsx"
        workbook.save(different)
        sheet.cell(2, headers.index("actual_reading") + 1, "ㄐㄩㄝˋ")
        third = tmp_path / "third-actual.xlsx"
        workbook.save(third)
        workbook.close()
        root_c = sp.project_actual_evidence_root(c)
        original_c = (root_c / ar.OCCURRENCE_OVERRIDE_FILE).read_bytes()
        manifest_c = (c / "校對工作階段.json").read_bytes()
        with patch.object(ar, "_write_csv", side_effect=OSError("disk full")):
            with pytest.raises(OSError, match="disk full"):
                sp.import_actual_gpt_decisions(c, filled)
        assert (root_c / ar.OCCURRENCE_OVERRIDE_FILE).read_bytes() == original_c
        assert (c / "校對工作階段.json").read_bytes() == manifest_c
        assert not (c / portability.INCOMPLETE_FILE).exists()
        assert sp.import_actual_gpt_decisions(b, filled)[0] == 1
        root_b = sp.project_actual_evidence_root(b)
        original_b = (root_b / ar.OCCURRENCE_OVERRIDE_FILE).read_bytes()
        with pytest.raises(ValueError, match="不同判定"):
            sp.import_actual_gpt_decisions(b, different)
        with pytest.raises(ValueError, match="不同判定"):
            sp.import_actual_gpt_decisions(b, different)
        with pytest.raises(ValueError, match="不同判定"):
            sp.import_actual_gpt_decisions(b, third)
        assert (root_b / ar.OCCURRENCE_OVERRIDE_FILE).read_bytes() == original_b
        receipt = sp.json_load_strict(b / "跨Excel_actual衝突.json")
        assert receipt["conflicts"][0]["source_reading"] == "ㄐㄩㄝˇ"
        assert receipt["conflicts"][0]["target_record"]["actual_reading"] == "ㄐㄩㄝˊ"
        assert [item["source_reading"] for item in receipt["conflicts"]] == ["ㄐㄩㄝˇ", "ㄐㄩㄝˋ"]
    assert not list((tmp_path / "isolated-localappdata").rglob("*.sqlite*"))


def test_actual_excel_prepared_and_post_ack_crash_windows_can_resume(tmp_path, monkeypatch):
    import global_glyph_promotion as promotion

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-localappdata"))
    first, second = _pdfs(tmp_path)
    a, before_commit, after_refresh = [tmp_path / name for name in
                                       ("source", "before-commit", "after-refresh")]
    with patch.object(sp, "decode", side_effect=_synthetic_unresolved_decode), \
         patch.object(sp, "actual_workbook_global_exact_dependencies", return_value=()), \
         patch("check_pronunciation_candidates.actual_workbook_global_exact_dependencies", return_value=()):
        sp.run_pipeline_pdfs([first], a, defer_excel_reports=True)
        sp.run_pipeline_pdfs([second], before_commit, defer_excel_reports=True)
        sp.run_pipeline_pdfs([second], after_refresh, defer_excel_reports=True)
        sp.export_actual_pending_for_gpt(a)
        workbook = load_workbook(a / "actual待判定_GPT包" / "actual待判定_給GPT.xlsx")
        sheet = workbook["actual待判定"]
        headers = [cell.value for cell in sheet[1]]
        for key, value in {"decision": "VERIFIED", "actual_reading": "ㄐㄩㄝˊ",
                           "confidence": "高", "sample_a_checked": "Y"}.items():
            sheet.cell(2, headers.index(key) + 1, value)
        filled = tmp_path / "filled-actual.xlsx"
        workbook.save(filled)
        workbook.close()

        original_before = portability._actual_excel_file_snapshot(before_commit)
        with patch.object(ar, "_write_csv", side_effect=SystemExit("power loss before commit")):
            with pytest.raises(SystemExit, match="power loss"):
                sp.import_actual_gpt_decisions(before_commit, filled)
        assert (before_commit / portability.INCOMPLETE_FILE).exists()
        assert portability.resume_actual_excel_project(before_commit)["project_actual_commit"] == "ROLLED_BACK"
        assert not (before_commit / portability.INCOMPLETE_FILE).exists()
        assert portability._actual_excel_file_snapshot(before_commit) == original_before

        with patch.object(promotion, "acknowledge_project_refresh", side_effect=SystemExit("power loss after refresh")):
            with pytest.raises(SystemExit, match="power loss"):
                sp.import_actual_gpt_decisions(after_refresh, filled)
        marker = sp.json_load_strict(after_refresh / portability.INCOMPLETE_FILE)
        assert marker["status"] == "ACTUAL_EXCEL_RECOVERED"
        assert promotion.committed_project_recovery(sp.project_actual_evidence_root(after_refresh)) is not None
        resumed = portability.resume_actual_excel_project(after_refresh)
        assert resumed["project_actual_commit"] == "COMMITTED"
        assert not (after_refresh / portability.INCOMPLETE_FILE).exists()
        assert promotion.committed_project_recovery(sp.project_actual_evidence_root(after_refresh)) is None
        assert portability._load_project(after_refresh)[0]["session_id"] == sp.json_load_strict(
            after_refresh / "校對工作階段.json")["session_id"]
        assert sp.import_actual_gpt_decisions(after_refresh, filled)[0] == 0
    assert not list((tmp_path / "isolated-localappdata").rglob("*.sqlite*"))


def test_existing_target_event_cannot_bypass_six_gate_actual_evidence_match(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-localappdata"))
    first, second = tmp_path / "A.pdf", tmp_path / "B.pdf"
    small_pdf(first, title="A")
    small_pdf(second, title="B")
    a, b = tmp_path / "source", tmp_path / "target"
    a_manifest, _ = small_project(a, first, session="A")
    b_manifest, b_db = small_project(b, second, session="B")
    for root, manifest in ((a, a_manifest), (b, b_manifest)):
        record = manifest["records"][0]
        record.update(state="DIFFERENCE_PENDING_CONFIRMATION",
                      expected_set=["ㄐㄧㄠˇ"], expected_evidence="independent rule",
                      context_evidence="角@1")
        if root == b:
            record["actual_evidence"] = "different B glyph evidence"
        sp.seal_manifest(manifest)
        sp.json_save(root / "校對工作階段.json", manifest)
    b_id = b_manifest["records"][0]["review_id"]
    b_db["events"][b_id] = {"action": "確認非校對範圍",
                             "exclusion_reason": "B source scope", "exclusion_evidence": "B page"}
    sp.json_save(b / "人工判定資料庫.json", b_db)
    assert not any(row.get("review_event_replay_status") for row in sp.materialize_ledger(b_manifest, b_db))
    exported = sp.export_pending_for_gpt(a)
    workbook = load_workbook(exported)
    sheet = workbook["待判定候選"]
    headers = [cell.value for cell in sheet[1]]
    sheet.cell(2, headers.index("action") + 1, "確認現版差異")
    for gate in sp.CONFIRMATION_GATES:
        sheet.cell(2, headers.index(gate) + 1, "Y")
    filled = tmp_path / "confirm.xlsx"
    workbook.save(filled)
    workbook.close()
    before = (b / "人工判定資料庫.json").read_bytes()
    with pytest.raises(ValueError, match="actual/expected 證據|當前 actual"):
        sp.import_gpt_decisions(b, filled)
    assert (b / "人工判定資料庫.json").read_bytes() == before
    assert not (b / portability.INCOMPLETE_FILE).exists()


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
