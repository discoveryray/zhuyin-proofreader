"""Isolated XLSX round trips, not manual Excel or real textbook acceptance."""
import copy
import json
from pathlib import Path
from unittest.mock import patch

import fitz
import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.worksheet.datavalidation import DataValidation

import actual_review as ar
import check_pronunciation_candidates as candidates
import export_zhuyin_readings as decoder
import occurrence_ledger as ol
import pdf_portability as portable
import standalone_proofread as sp
from excel_text import append_text_row, write_text_value
from tests.test_global_exact_glyph_read_reuse_v580 import (
    _DecodeDocument, _DecodeTrueTypeInspector, _absent_snapshot,
)
from tests.test_pdf_portability_integration import _pdfs, _synthetic_decode
from tests.test_import_transaction import make_manifest
from tests.test_actual_review_v540 import entry, make_pdf


# Literal, independent oracles; production has no symbol/prefix list.
TEXTS = (
    "=15,15個一換成1個十和5", "=6,表示6個一。", "=1+2", "+1", "-1", "@1", "=",
    "001", "1/2", "1E3", "#N/A", "＝15", "×", "÷", "−", "≤", "≥",
    "中文×3÷2−1≤5≥0", " 前後空白 ", "第一行\n=第二行", "'原本的單引號", "\t空白\r\n末行 ",
)
ROOT = Path(__file__).resolve().parents[1]


def assert_text(path, sheet_name, field, expected, *, identity_field=None):
    for data_only in (False, True):
        wb = load_workbook(path, data_only=data_only)
        try:
            sheet = wb[sheet_name]
            columns = {cell.value: cell.column for cell in sheet[1]}
            cells = [sheet.cell(row, columns[field]) for row in range(2, sheet.max_row + 1)]
            if identity_field is None:
                assert [cell.value for cell in cells] == list(expected)
            else:
                identities = [sheet.cell(row, columns[identity_field]).value
                              for row in range(2, sheet.max_row + 1)]
                assert len(identities) == len(set(identities)) == len(expected)
                assert dict(zip(identities, (cell.value for cell in cells))) == expected
            assert all(cell.data_type == "s" for cell in cells)
        finally:
            wb.close()


def project_bytes(root):
    # Byte-level atomicity: include all project artifacts and dynamic evidence.
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*")
            if p.is_file() and p.suffix != ".lock"}


@pytest.fixture
def isolated_pipeline(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    pdf, _ = _pdfs(tmp_path)

    def controlled_decode(pdf, output, *args, global_snapshot=None):
        # Only glyph acquisition is synthetic. The real candidate analyzer,
        # seals, both exporters/importers, actual transaction and refresh run.
        _synthetic_decode(pdf, output, *args, global_snapshot=global_snapshot)
        wb = load_workbook(output)
        sheet = wb["實際注音"]
        columns = {cell.value: cell.column for cell in sheet[1]}
        base = {key: sheet.cell(2, col).value for key, col in columns.items()}
        sheet.delete_rows(2, sheet.max_row)
        overrides = ar._read_csv(args[13] / ar.OCCURRENCE_OVERRIDE_FILE, ar.OVERRIDE_HEADERS)
        records = []
        for index, text in enumerate(TEXTS):
            stable = f"text-fixture-{index}"
            matched = next((item for item in overrides if item["stable_key"] == stable), None)
            rec = {**base, "課本頁": text, "頁面標籤": text, "穩定注音鍵": stable,
                   "群組注音鍵": stable, "注音元件ID": index + 1,
                   "glyph_id_字形索引": index + 1, "x0": 50 + index, "x1": 65 + index,
                   "source_row_number": index + 1,
                   "實際注音": matched["actual_reading"] if matched else ("" if unresolved[0] else "ㄐㄩㄝˇ"),
                   "解碼狀態": "已映射" if matched or not unresolved[0] else "未辨識",
                   "解碼依據": text}
            for field in ("occurrence_id", "review_id"):
                rec.pop(field, None)
            records.append(rec)
        ol.prepare_occurrence_rows(sp.sha256_file(pdf), records)
        for rec in records:
            append_text_row(sheet, [rec.get(key, "") for key in columns])
        # The reused two-glyph acquisition surface originally reports two rows;
        # this expanded fixture must report its independently declared roster.
        for row in wb["摘要"]:
            if row[0].value == "偵測到注音字形筆數":
                row[1].value = len(TEXTS)
        wb.save(output)
        wb.close()

    def context(rec, _index):
        text = TEXTS[int(str(rec["穩定注音鍵"]).rsplit("-", 1)[1])]
        return text, text, "", "", "", None, text

    unresolved = [False]
    monkeypatch.setattr(sp, "decode", controlled_decode)
    monkeypatch.setattr(sp, "actual_workbook_global_exact_dependencies", lambda *_: ())
    monkeypatch.setattr(candidates, "actual_workbook_global_exact_dependencies", lambda *_: ())
    monkeypatch.setattr(candidates, "locate_line_context", context)

    def create(lane):
        unresolved[0] = lane == "actual"
        root = tmp_path / lane
        sp.run_pipeline_pdfs([pdf], root, defer_excel_reports=True)
        return root
    yield create
    assert not list((tmp_path / "localappdata").rglob("*.sqlite*"))


def exported(root, lane):
    if lane == "expected":
        return sp.export_pending_for_gpt(root), "待判定候選"
    assert sp.export_actual_pending_for_gpt(root) is not None
    return root / "actual待判定_GPT包" / "actual待判定_給GPT.xlsx", "actual待判定"


def fill(path, sheet_name, lane):
    wb = load_workbook(path)
    sheet = wb[sheet_name]
    cols = {cell.value: cell.column for cell in sheet[1]}
    answers = ({"action": "解決expected證據", "proposed_expected_set": "ㄐㄩㄝˊ",
                "proposed_expected_evidence": "獨立的正式應標證據",
                "proposed_context_evidence": "獨立的本列語境"} if lane == "expected" else
               {"decision": "VERIFIED", "actual_reading": "ㄐㄩㄝˊ", "confidence": "高",
                "sample_a_checked": "Y", "sample_b_checked": "Y"})
    for row in range(2, sheet.max_row + 1):
        for key, value in answers.items():
            sheet.cell(row, cols[key], value)
    return wb, sheet, cols


@pytest.mark.parametrize("lane", ["expected", "actual"])
def test_formal_export_save_reopen_fill_import_preserves_literal_text(isolated_pipeline, lane):
    root = isolated_pipeline(lane)
    manifest = sp.json_load_strict(root / "校對工作階段.json")
    before = copy.deepcopy(manifest["records"])
    candidate = Path(manifest["pdfs"][0]["candidate_workbook"])
    assert_text(candidate, "Occurrence Ledger", "printed_page", TEXTS)
    for data_only in (False, True):
        wb = load_workbook(candidate, data_only=data_only)
        try:
            ws = wb["Occurrence Ledger"]
            cols = {cell.value: cell.column for cell in ws[1]}
            sources = [json.loads(ws.cell(row, cols["source_record_json"]).value)
                       for row in range(2, ws.max_row + 1)]
            assert [source["所在行"] for source in sources] == list(TEXTS)
        finally:
            wb.close()
    path, sheet_name = exported(root, lane)
    fields = ("課本頁", "所在行", "局部詞境", "actual_evidence") if lane == "expected" else ("sample_a_page", "actual_evidence")
    # Actual groups have their own deterministic order. Bind literal oracles to
    # original occurrence IDs rather than imposing expected-lane row ordering.
    text_oracle = {entry["occurrence_id"]: text for entry, text in zip(before, TEXTS)}
    kwargs = {} if lane == "expected" else {"identity_field": "sample_a_occurrence_id"}
    for field in fields:
        assert_text(path, sheet_name, field, TEXTS if lane == "expected" else text_oracle, **kwargs)
    wb = load_workbook(path)
    try:
        assert portable.EXCEL_PROOF_SHEET in wb.sheetnames
        ws = wb[sheet_name]
        assert len(ws.data_validations.dataValidation) > 0
        cols = {cell.value: cell.column for cell in ws[1]}
        assert ws.cell(2, cols["編號"]).value == 1 and ws.cell(2, cols["編號"]).data_type == "n"
        assert ws.cell(2, cols["action" if lane == "expected" else "decision"]).value is None
        if lane == "expected":
            assert ws.cell(2, cols["實體頁碼"]).value == 1
    finally:
        wb.close()
    wb, _, _ = fill(path, sheet_name, lane)
    wb.save(path)
    wb.close()
    for field in fields:
        assert_text(path, sheet_name, field, TEXTS if lane == "expected" else text_oracle, **kwargs)
    importer = sp.import_gpt_decisions if lane == "expected" else sp.import_actual_gpt_decisions
    assert importer(root, path)[0] == len(TEXTS)
    db = sp.json_load_strict(root / "人工判定資料庫.json")
    latest = sp.json_load_strict(root / "校對工作階段.json")
    ledger = sp.materialize_ledger(latest, db)
    if lane == "expected":
        assert [entry["actual"] for entry in ledger] == [entry["actual"] for entry in before]
        assert [entry["actual_evidence"] for entry in ledger] == [entry["actual_evidence"] for entry in before]
    else:
        assert [entry["expected_set"] for entry in ledger] == [entry["expected_set"] for entry in before]
        assert [entry["expected_evidence"] for entry in ledger] == [entry["expected_evidence"] for entry in before]
        assert [entry["actual"] for entry in ledger] == ["ㄐㄩㄝˊ"] * len(TEXTS)


@pytest.mark.parametrize("lane", ["expected", "actual"])
@pytest.mark.parametrize("tamper", ["source", "identity", "seal"])
def test_protected_tamper_rejects_entire_batch_without_project_writes(isolated_pipeline, lane, tamper):
    root = isolated_pipeline(lane)
    path, sheet_name = exported(root, lane)
    wb, sheet, cols = fill(path, sheet_name, lane)
    if tamper == "seal":
        wb[portable.EXCEL_PROOF_SHEET].cell(2, 2, "0" * 64)
    else:
        field = ("所在行" if lane == "expected" else "sample_a_page") if tamper == "source" else ("review_id" if lane == "expected" else "sample_a_occurrence_id")
        write_text_value(sheet, sheet.max_row, cols[field], "modified protected source")
    wb.save(path)
    wb.close()
    before = project_bytes(root)
    importer = sp.import_gpt_decisions if lane == "expected" else sp.import_actual_gpt_decisions
    with pytest.raises(ValueError):
        importer(root, path)
    assert project_bytes(root) == before


def test_real_first_actual_writer_preserves_each_label_and_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    source = tmp_path / "source.pdf"
    with fitz.open() as real:
        real.new_page()
        real.save(source)
    document = _DecodeDocument()
    pages = []
    for text in TEXTS:
        page = copy.copy(document.page)
        page.literal = text
        page.get_label = lambda value=text: value
        pages.append(page)
    document.page_count = len(pages)
    # A local acquisition adapter; production decode and worksheet writes run.
    class Document:
        page_count = len(pages)
        def __iter__(self): return iter(pages)
        def extract_font(self, xref): return document.extract_font(xref)
        def close(self): pass
    output = tmp_path / "actual.xlsx"
    with patch.object(decoder.fitz, "open", return_value=Document()), \
         patch.object(decoder, "TrueTypeGlyphInspector", _DecodeTrueTypeInspector), \
         patch.object(decoder, "infer_printed_page_number", side_effect=lambda page, _: page.literal):
        decoder.decode(source, output, tmp_path / "absent-map.csv", tmp_path / "absent-groups.csv",
                       dynamic_evidence_root=tmp_path / "dynamic", global_snapshot=_absent_snapshot())
    assert_text(output, "實際注音", "課本頁", TEXTS)
    assert_text(output, "實際注音", "頁面標籤", TEXTS)
    wb = load_workbook(output, data_only=True)
    try:
        assert all(type(row[0]) is int for row in wb["實際注音"].iter_rows(min_row=2, values_only=True))
    finally:
        wb.close()


@pytest.mark.parametrize("bad,reason", [("x" * 32768, "32767"), ("😀" * 16384, "UTF-16"),
                                       ("bad\x00text", "U+0000"), ("\ud800", "U+D800"), ("\uffff", "U+FFFF")],
                         ids=["too-long-ascii", "too-long-utf16", "nul", "surrogate", "noncharacter"])
def test_unrepresentable_text_names_sheet_field_and_fails_before_mutation(bad, reason):
    wb = Workbook()
    ws = wb.active
    ws.title = "教材"
    ws.append(["原文"])
    with pytest.raises(ValueError, match=reason.replace("+", r"\+")) as exc:
        append_text_row(ws, [bad])
    assert "教材/原文 (A2)" in str(exc.value)
    assert ws.max_row == 1
    write_text_value(ws, 2, 1, "original")
    with pytest.raises(ValueError):
        write_text_value(ws, 2, 1, bad)
    assert ws.cell(2, 1).value == "original"


def test_native_scalars_required_formula_and_validation_are_preserved(tmp_path):
    wb = Workbook()
    ws = wb.active
    append_text_row(ws, ["text", "integer", "float", "boolean", "empty", "formula"])
    append_text_row(ws, ["#N/A", 15, 1.25, True, None, None])
    ws.cell(2, 6, "=B2+C2")  # An actual formula writer is deliberately separate.
    dv = DataValidation(type="list", formula1='"Y,N"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add("D2")
    path = tmp_path / "native.xlsx"
    wb.save(path)
    loaded = load_workbook(path)
    try:
        assert [cell.value for cell in loaded.active[2]] == ["#N/A", 15, 1.25, True, None, "=B2+C2"]
        assert [cell.data_type for cell in loaded.active[2]][:4] == ["s", "n", "n", "b"]
        assert loaded.active.cell(2, 6).data_type == "f"
        assert len(loaded.active.data_validations.dataValidation) == 1
    finally:
        loaded.close()


def test_exact_capacity_boundary_round_trip(tmp_path):
    wb = Workbook()
    ws = wb.active
    append_text_row(ws, ["原文"])
    append_text_row(ws, ["a" * 32767])
    append_text_row(ws, ["😀" * 16383 + "a"])
    path = tmp_path / "boundary.xlsx"
    wb.save(path)
    assert_text(path, ws.title, "原文", ["a" * 32767, "😀" * 16383 + "a"])


@pytest.mark.parametrize("lane", ["expected", "actual"])
def test_formal_export_limit_refusal_preserves_previous_output(tmp_path, monkeypatch, lane):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    root = tmp_path / "project"
    root.mkdir()
    if lane == "expected":
        # A new, isolated source fixture: sealed once with its declared input,
        # never an importer adapter that reseals a modified carried workbook.
        source = make_manifest()
        source["records"][0]["source_record"]["所在行"] = "x" * 32768
        sp.json_save(root / "校對工作階段.json", sp.seal_manifest(source))
        sp.json_save(root / "人工判定資料庫.json", sp.normalize_db({}))
        path = root / "待判定候選_給GPT.xlsx"
        path.write_bytes(b"previous publication")
        before = path.read_bytes()
        with pytest.raises(ValueError, match="待判定候選/所在行.*32767"):
            sp.export_pending_for_gpt(root)
        assert path.read_bytes() == before
    else:
        pdf = tmp_path / "source.pdf"
        make_pdf(pdf)
        row = entry("occ_" + "a" * 64, "rev_" + "b" * 64, pdf, 60)
        meta = dict(version=sp.VERSION, session_id="isolated",
                    session_schema_version=ol.SESSION_SCHEMA_VERSION,
                    workbook_schema_version=ol.WORKBOOK_SCHEMA_VERSION,
                    review_id_schema_version=ol.REVIEW_ID_SCHEMA_VERSION)
        ar.export_actual_review_package(root, [row], **meta)
        before = project_bytes(root)
        row["printed_page"] = "x" * 32768
        with pytest.raises(ValueError, match="actual待判定/sample_a_page.*32767"):
            ar.export_actual_review_package(root, [row], **meta)
        assert project_bytes(root) == before
