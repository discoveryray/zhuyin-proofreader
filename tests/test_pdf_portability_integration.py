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
from openpyxl import Workbook

import pdf_portability as portability
import review_gui
import standalone_proofread as sp
import occurrence_ledger as ol
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
        assert len(sp.materialize_ledger(a_manifest, sp.json_load_strict(a / "人工判定資料庫.json"))) == 2
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
