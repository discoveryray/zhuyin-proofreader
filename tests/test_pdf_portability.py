"""Isolated end-to-end contracts for same-content, different-byte PDF transfer."""

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import fitz
import pytest
import actual_review as ar
from openpyxl import Workbook, load_workbook

import pdf_portability as portable
import standalone_proofread as sp
from review_display import occurrence_preview
from review_save_service import ReviewSaveService
from occurrence_ledger import (
    LEDGER_SCHEMA_VERSION, REVIEW_ID_SCHEMA_VERSION, SESSION_SCHEMA_VERSION,
    WORKBOOK_SCHEMA_VERSION, build_occurrence_ledger, prepare_occurrence_rows,
)


def pdf(path: Path, *, title: str, text: str = "角角ㄐㄩㄝˊ", picture=(0, 0, 1), tone="rising"):
    document = fitz.open()
    page = document.new_page(width=300, height=200)
    page.insert_text((20, 40), text, fontsize=16)
    page.draw_rect(fitz.Rect(210, 100, 245, 135), color=picture, fill=picture)
    # A vector tone mark stands in for the embedded textbook glyph program;
    # the default PDF font cannot encode Bopomofo on every CI host.
    page.draw_line((70, 100), (85, 88 if tone == "rising" else 112), color=(0, 0, 0), width=2)
    document.set_metadata({"title": title})
    document.save(path)
    document.close()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def project(root: Path, source_pdf: Path, *, session: str, event_positions=()):
    root.mkdir(exist_ok=True)
    actual = root / "01_實際注音"
    candidate = root / "02_候選報告"
    actual.mkdir()
    candidate.mkdir()
    actual_file = actual / "actual.xlsx"
    candidate_file = candidate / "candidate.xlsx"
    evidence_root = sp.project_actual_evidence_root(root)
    ar.ensure_user_evidence_files(evidence_root)
    dynamic = ar.dynamic_actual_hashes(evidence_root, pdf_path=source_pdf, dependencies=None, read_only=True)
    workbook = Workbook()
    workbook.active.title = "實際注音"
    workbook.active.append(["occurrence_id"])
    metadata = workbook.create_sheet("v5.2中繼資料")
    metadata.append(["項目", "內容"])
    metadata.append(["pdf_sha256", sha(source_pdf)])
    metadata.append(["actual_asset_fingerprint_components", json.dumps({"dynamic_actual_evidence_hashes": dynamic})])
    workbook.save(actual_file)
    workbook.close()
    candidate_file.write_bytes(b"sealed candidate test artifact")
    digest = sha(source_pdf)
    rows = []
    for index, x0 in enumerate((20, 90), 1):
        rows.append({
            "pdf_sha256": digest, "pdf": str(source_pdf), "pdf_name": source_pdf.name,
            "實體頁碼": 1, "課本頁": 1, "字元": "角", "實際注音": "ㄐㄩㄝˊ",
            "解碼依據": "glyph evidence", "穩定注音鍵": f"F#{index}", "font": "F", "font_xref": 1,
            "TTF字形SHA256": "a" * 64,
            "glyph_id_字形索引": index, "注音元件ID": index,
            "x0": x0, "y0": 20, "x1": x0 + 20, "y1": 45, "source_row_number": index,
        })
    prepare_occurrence_rows(digest, rows)
    ledger = build_occurrence_ledger(rows, {
        row["occurrence_id"]: {
            "state": "EXPECTED_UNRESOLVED", "actual": "ㄐㄩㄝˊ", "actual_evidence": "glyph evidence",
            "context_evidence": f"角色/角@{index}", "source_view": "未建立預期音", "source_record": row,
        }
        for index, row in enumerate(rows, 1)
    })
    manifest = sp.seal_manifest({
        "version": sp.VERSION,
        "session_schema_version": SESSION_SCHEMA_VERSION,
        "workbook_schema_version": WORKBOOK_SCHEMA_VERSION,
        "ledger_schema_version": LEDGER_SCHEMA_VERSION,
        "review_id_schema_version": REVIEW_ID_SCHEMA_VERSION,
        "session_id": session,
        "pdfs": [{"pdf": str(source_pdf), "pdf_name": source_pdf.name,
                  "pdf_sha256": digest, "actual_workbook": str(actual_file),
                  "actual_workbook_sha256": sha(actual_file), "candidate_workbook": str(candidate_file),
                  "candidate_workbook_sha256": sha(candidate_file)}],
        "records": ledger,
        "actual_source_ids": [row["occurrence_id"] for row in rows],
        "regression_gate": {"ok": False, "required": 1, "executed": 1,
                            "passed": 0, "failed": 1, "not_executed": 0, "duplicate_case_id": 0},
    })
    sp.json_save(root / "校對工作階段.json", manifest)
    events = {}
    for index in event_positions:
        events[rows[index]["review_id"]] = {
            "action": "補建expected證據", "expected_set": ["ㄐㄩㄝˊ"],
            "expected_evidence": "現版手冊", "context_evidence": f"角色/角@{index + 1}",
        }
    db = sp.normalize_db({})
    db["events"] = events
    sp.json_save(root / "人工判定資料庫.json", db)
    return manifest, db


def reseal_actual_dynamic(root: Path, source_pdf: Path):
    """Test producer: bind a changed project-local CSV to its sealed workbook."""
    evidence_root = sp.project_actual_evidence_root(root)
    dynamic = ar.dynamic_actual_hashes(evidence_root, pdf_path=source_pdf, dependencies=None, read_only=True)
    manifest = sp.json_load_strict(root / "校對工作階段.json")
    actual_file = root / "01_實際注音" / "actual.xlsx"
    workbook = load_workbook(actual_file)
    metadata = workbook["v5.2中繼資料"]
    for row in metadata.iter_rows(min_row=2, max_col=2):
        if row[0].value == "actual_asset_fingerprint_components":
            row[1].value = json.dumps({"dynamic_actual_evidence_hashes": dynamic})
    workbook.save(actual_file)
    workbook.close()
    manifest["pdfs"][0]["actual_workbook_sha256"] = sha(actual_file)
    sp.seal_manifest(manifest)
    sp.json_save(root / "校對工作階段.json", manifest)


def add_override(root: Path, entry, reading="ㄐㄩㄝˊ"):
    key = ar._override_key_from_entry(entry)
    fields = ("pdf_contains", "pdf_excludes", "page", "target_char", "stable_key", "x0", "y0")
    row = dict(zip(fields, key)) | {
        "actual_reading": reading, "source": "original direct visual",
        "note": "source page checked",
    }
    ar._write_csv(sp.project_actual_evidence_root(root) / ar.OCCURRENCE_OVERRIDE_FILE,
                  ar.OVERRIDE_HEADERS, [row])
    return row


def test_different_sha_same_pages_a_to_b_to_a_with_reopen(tmp_path):
    a_pdf, b_pdf = tmp_path / "A.pdf", tmp_path / "B.pdf"
    pdf(a_pdf, title="download A")
    pdf(b_pdf, title="download B")
    assert sha(a_pdf) != sha(b_pdf)
    a, b = tmp_path / "A_project", tmp_path / "B_project"
    a_manifest, _ = project(a, a_pdf, session="A", event_positions=(0,))
    b_manifest, _ = project(b, b_pdf, session="B")
    portable.prepare_portable_project(a)
    a_pdf.rename(tmp_path / "A-away.pdf")
    result = portable.import_project_decisions(a, b)
    assert result["imported"] == 1 and not result["conflicts"]
    assert result["pdf_sha_map"] == {a_manifest["pdfs"][0]["pdf_sha256"]: b_manifest["pdfs"][0]["pdf_sha256"]}
    b_db = sp.json_load_strict(b / "人工判定資料庫.json")
    target_event = b_db["events"][b_manifest["records"][0]["review_id"]]
    assert target_event["portability_source"]["review_id"] == a_manifest["records"][0]["review_id"]
    assert target_event["portability_source"]["pdf_sha256"] != target_event["portability_source"]["target_pdf_sha256"]
    assert sp.materialize_ledger(b_manifest, b_db)[0]["expected_set"] == ["ㄐㄩㄝˊ"]
    assert portable.import_project_decisions(a, b)["duplicates"] == 1
    b_db["events"][b_manifest["records"][1]["review_id"]] = {
        "action": "補建expected證據", "expected_set": ["ㄐㄩㄝˊ"],
        "expected_evidence": "現版手冊", "context_evidence": "角色/角@2",
    }
    sp.json_save(b / "人工判定資料庫.json", b_db)
    portable.prepare_portable_project(b)
    (tmp_path / "A-away.pdf").rename(a_pdf)
    back = portable.import_project_decisions(b, a)
    assert back["duplicates"] == 1 and back["imported"] == 1
    reopened = sp.json_load_strict(a / "人工判定資料庫.json")
    assert len(reopened["events"]) == 2
    assert len(sp.materialize_ledger(a_manifest, reopened)) == 2


@pytest.mark.parametrize("change", ["text", "picture", "zhuyin"])
def test_same_filename_different_content_rejected(tmp_path, change):
    left, right = tmp_path / "left", tmp_path / "right"
    left.mkdir(); right.mkdir()
    first, second = left / "book.pdf", right / "book.pdf"
    pdf(first, title="A")
    kwargs = {"title": "B"}
    if change == "text":
        kwargs["text"] = "不一樣ㄐㄩㄝˊ"
    elif change == "picture":
        kwargs["picture"] = (1, 0, 0)
    else:
        kwargs["tone"] = "falling"
    pdf(second, **kwargs)
    source, target = tmp_path / "source", tmp_path / "target"
    project(source, first, session="source", event_positions=(0,))
    project(target, second, session="target")
    before = (target / "人工判定資料庫.json").read_bytes()
    with pytest.raises(ValueError, match="頁面內容"):
        portable.import_project_decisions(source, target)
    assert (target / "人工判定資料庫.json").read_bytes() == before


def test_conflict_preserves_both_and_write_failure_keeps_target(tmp_path):
    a_pdf, b_pdf = tmp_path / "A.pdf", tmp_path / "B.pdf"
    pdf(a_pdf, title="A")
    pdf(b_pdf, title="B")
    source, target = tmp_path / "source", tmp_path / "target"
    project(source, a_pdf, session="source", event_positions=(0,))
    manifest, db = project(target, b_pdf, session="target")
    rid = manifest["records"][0]["review_id"]
    db["events"][rid] = {"action": "保留待人工"}
    sp.json_save(target / "人工判定資料庫.json", db)
    before = (target / "人工判定資料庫.json").read_bytes()
    result = portable.import_project_decisions(source, target)
    assert result["conflicts"] == [{"source_review_id": sp.json_load_strict(source / "校對工作階段.json")["records"][0]["review_id"], "target_review_id": rid}]
    assert (target / "人工判定資料庫.json").read_bytes() == before
    db["events"].clear()
    sp.json_save(target / "人工判定資料庫.json", db)
    before = (target / "人工判定資料庫.json").read_bytes()
    with patch("standalone_proofread.json_save", side_effect=OSError("disk full")):
        with pytest.raises(OSError, match="disk full"):
            portable.import_project_decisions(source, target)
    assert (target / "人工判定資料庫.json").read_bytes() == before


def test_repeated_character_requires_its_own_position(tmp_path):
    a_pdf, b_pdf = tmp_path / "A.pdf", tmp_path / "B.pdf"
    pdf(a_pdf, title="A")
    pdf(b_pdf, title="B")
    source, target = tmp_path / "source", tmp_path / "target"
    project(source, a_pdf, session="source", event_positions=(1,))
    target_manifest, _ = project(target, b_pdf, session="target")
    portable.import_project_decisions(source, target)
    events = sp.json_load_strict(target / "人工判定資料庫.json")["events"]
    assert list(events) == [target_manifest["records"][1]["review_id"]]


def test_different_pdf_object_storage_and_identity_mapping(tmp_path):
    first, second = tmp_path / "uncompressed.pdf", tmp_path / "compressed.pdf"
    document = fitz.open()
    page = document.new_page(width=300, height=200)
    page.insert_text((20, 40), "same complete page", fontsize=16)
    page.draw_rect(fitz.Rect(210, 100, 245, 135), color=(0, 0, 1), fill=(0, 0, 1))
    document.save(first, deflate=False, garbage=0)
    document.save(second, deflate=True, garbage=4)
    document.close()
    assert sha(first) != sha(second)
    assert portable._page_signatures(first) == portable._page_signatures(second)
    source, target = tmp_path / "source", tmp_path / "target"
    source_manifest, _ = project(source, first, session="source", event_positions=(0,))
    target_manifest, _ = project(target, second, session="target")
    # The PDF's font/xref/glyph identity may differ even though the page and
    # occurrence position are equal. Original identities remain distinct.
    assert source_manifest["records"][0]["review_id"] != target_manifest["records"][0]["review_id"]
    result = portable.import_project_decisions(source, target)
    assert result["imported"] == 1


def test_missing_source_pdf_requires_prior_proof_and_moved_artifact_is_checked(tmp_path):
    source_pdf, target_pdf = tmp_path / "source.pdf", tmp_path / "target.pdf"
    pdf(source_pdf, title="A")
    pdf(target_pdf, title="B")
    source, target = tmp_path / "source", tmp_path / "target"
    project(source, source_pdf, session="source", event_positions=(0,))
    project(target, target_pdf, session="target")
    before = (target / "人工判定資料庫.json").read_bytes()
    source_pdf.rename(tmp_path / "absent.pdf")
    with pytest.raises(FileNotFoundError, match="原 PDF"):
        portable.import_project_decisions(source, target)
    assert (target / "人工判定資料庫.json").read_bytes() == before
    (tmp_path / "absent.pdf").rename(source_pdf)
    portable.prepare_portable_project(source)
    source_pdf.rename(tmp_path / "absent.pdf")
    # An intact workbook at the copied project-relative path is accepted even
    # though the sealed manifest retains the first computer's absolute path.
    assert portable.import_project_decisions(source, target)["imported"] == 1
    (source / "01_實際注音" / "actual.xlsx").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="來源工作簿"):
        portable.import_project_decisions(source, target)


def test_pending_actual_transaction_rejects_transfer_without_touching_target(tmp_path):
    source_pdf, target_pdf = tmp_path / "source.pdf", tmp_path / "target.pdf"
    pdf(source_pdf, title="A")
    pdf(target_pdf, title="B")
    source, target = tmp_path / "source", tmp_path / "target"
    project(source, source_pdf, session="source", event_positions=(0,))
    project(target, target_pdf, session="target")
    root = sp.project_actual_evidence_root(source)
    root.mkdir(parents=True, exist_ok=True)
    (root / "global_exact_glyph_project_transaction.json").write_text("pending", encoding="utf-8")
    before = (target / "人工判定資料庫.json").read_bytes()
    with pytest.raises(ValueError, match="交易尚待原專案恢復"):
        portable.import_project_decisions(source, target)
    assert (target / "人工判定資料庫.json").read_bytes() == before


def test_multi_hop_provenance_retains_first_source(tmp_path):
    files = [tmp_path / f"{letter}.pdf" for letter in "ABC"]
    for letter, path in zip("ABC", files):
        pdf(path, title=letter)
    dirs = [tmp_path / letter for letter in "ABC"]
    manifests = [project(folder, path, session=letter, event_positions=(0,) if letter == "A" else ())[0]
                 for letter, folder, path in zip("ABC", dirs, files)]
    portable.import_project_decisions(dirs[0], dirs[1])
    portable.import_project_decisions(dirs[1], dirs[2])
    event = sp.json_load_strict(dirs[2] / "人工判定資料庫.json")["events"][manifests[2]["records"][0]["review_id"]]
    assert event["portability_source"]["session_id"] == "B"
    assert event["portability_source"]["prior"]["session_id"] == "A"
    assert event["portability_source"]["prior"]["review_id"] == manifests[0]["records"][0]["review_id"]


def test_imported_project_can_show_local_page_save_and_reopen(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-localappdata"))
    source_pdf, local_pdf = tmp_path / "download-a.pdf", tmp_path / "download-b.pdf"
    pdf(source_pdf, title="computer A")
    pdf(local_pdf, title="computer B")
    source, target = tmp_path / "source", tmp_path / "target"
    project(source, source_pdf, session="A", event_positions=(0,))
    target_manifest, _ = project(target, local_pdf, session="B")
    portable.prepare_portable_project(source)
    source_pdf.rename(tmp_path / "not-on-B.pdf")
    portable.import_project_decisions(source, target)
    row = target_manifest["records"][1]
    pixels, rectangle, _ = occurrence_preview(row, output_dir=target)
    assert pixels.width > 0 and pixels.height > 0 and rectangle is not None
    event = {"action": "補建expected證據", "expected_set": ["ㄐㄩㄝˊ"],
             "expected_evidence": "現版手冊", "context_evidence": "角色/角@2"}
    result = ReviewSaveService(target).save_event(row["review_id"], event)
    assert result.resolved_entry["expected_set"] == ["ㄐㄩㄝˊ"]
    reopened = sp.json_load_strict(target / "人工判定資料庫.json")
    assert len(reopened["events"]) == 2
    assert sp.materialize_ledger(target_manifest, reopened)[1]["expected_set"] == ["ㄐㄩㄝˊ"]


def test_source_actual_staging_is_preserved_as_recheck_not_new_global_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-localappdata"))
    source_pdf, target_pdf = tmp_path / "source.pdf", tmp_path / "target.pdf"
    pdf(source_pdf, title="A")
    pdf(target_pdf, title="B")
    source, target = tmp_path / "source", tmp_path / "target"
    manifest, _ = project(source, source_pdf, session="source", event_positions=(0,))
    project(target, target_pdf, session="target")
    group = ar.build_actual_group_for_entry(manifest["records"], manifest["records"][0])
    root = sp.project_actual_evidence_root(source)
    ar.stage_manual_actual_group(
        root, group, "ㄐㄩㄝˊ", checked_occurrence_ids=[manifest["records"][0]["occurrence_id"]],
        source="manual visual actual confirmation",
    )
    source_staging = ar.load_manual_actual_staging(root)
    result = portable.import_project_decisions(source, target)
    assert result["actual_staging"] == "RECHECK_LOCAL_PDF_REQUIRED"
    receipt = sp.json_load_strict(target / portable.PENDING_ACTUAL_FILE)
    assert receipt["staging"] == source_staging
    assert receipt["source_session_id"] == "source"
    assert ar.load_manual_actual_staging(sp.project_actual_evidence_root(target))["staged_groups"] == []
    from standalone_gui import project_status_details
    assert "重新核對" in project_status_details(target)[0]
    assert not (tmp_path / "isolated-localappdata").exists()


def test_continue_operation_uses_local_pdf_and_only_clears_success_marker(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-localappdata"))
    first, local = tmp_path / "first.pdf", tmp_path / "local.pdf"
    pdf(first, title="A")
    pdf(local, title="B")
    source, target = tmp_path / "source", tmp_path / "continued"
    project(source, first, session="source", event_positions=(0,))
    portable.prepare_portable_project(source)
    first.rename(tmp_path / "first-away.pdf")

    def build_target(paths, output_dir):
        assert paths == [local.resolve()]
        project(output_dir, local, session="continued")

    with patch.object(sp, "run_pipeline_pdfs", side_effect=build_target):
        result = portable.continue_project(source, local, target)
    assert result["imported"] == 1
    assert not (target / portable.INCOMPLETE_FILE).exists()
    assert sp.json_load_strict(target / "校對工作階段.json")["pdfs"][0]["pdf_sha256"] == sha(local)
    assert len(sp.json_load_strict(target / "人工判定資料庫.json")["events"]) == 1


def test_failed_continue_keeps_source_and_marks_target_incomplete(tmp_path):
    first, local = tmp_path / "first.pdf", tmp_path / "local.pdf"
    pdf(first, title="A")
    pdf(local, title="B")
    source, target = tmp_path / "source", tmp_path / "continued"
    project(source, first, session="source", event_positions=(0,))
    portable.prepare_portable_project(source)
    source_db = (source / "人工判定資料庫.json").read_bytes()
    with patch.object(sp, "run_pipeline_pdfs", side_effect=OSError("write failed")):
        with pytest.raises(OSError, match="write failed"):
            portable.continue_project(source, local, target)
    assert (source / "人工判定資料庫.json").read_bytes() == source_db
    assert (target / portable.INCOMPLETE_FILE).is_file()
    from standalone_gui import project_status_details
    assert "未完成" in project_status_details(target)[0]
    with pytest.raises(ValueError, match="接續未完成"):
        ReviewSaveService(target).save_event("any", {"action": "保留待人工"})


def test_tampered_source_actual_override_cannot_be_imported(tmp_path):
    first, second = tmp_path / "first.pdf", tmp_path / "second.pdf"
    pdf(first, title="A")
    pdf(second, title="B")
    source, target = tmp_path / "source", tmp_path / "target"
    source_manifest, _ = project(source, first, session="source", event_positions=(0,))
    project(target, second, session="target")
    add_override(source, source_manifest["records"][0])
    before = (target / "人工判定資料庫.json").read_bytes()
    with pytest.raises(ValueError, match="fingerprint 不符"):
        portable.import_project_decisions(source, target)
    assert (target / "人工判定資料庫.json").read_bytes() == before


def test_saved_actual_override_maps_without_global_promotion(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-localappdata"))
    first, local = tmp_path / "first.pdf", tmp_path / "local.pdf"
    pdf(first, title="A")
    pdf(local, title="B")
    source, continued = tmp_path / "source", tmp_path / "continued"
    source_manifest, _ = project(source, first, session="A", event_positions=(0,))
    add_override(source, source_manifest["records"][0])
    reseal_actual_dynamic(source, first)
    portable.prepare_portable_project(source)
    first.rename(tmp_path / "first-away.pdf")

    def build_target(paths, output_dir):
        project(output_dir, local, session="B")

    def refresh_target(output_dir):
        reseal_actual_dynamic(output_dir, local)

    with patch.object(sp, "run_pipeline_pdfs", side_effect=build_target), \
         patch.object(sp, "refresh_actual_project", side_effect=refresh_target):
        result = portable.continue_project(source, local, continued)
    assert result["matched_actual_overrides"] == 1
    target_rows = ar._read_csv(sp.project_actual_evidence_root(continued) / ar.OCCURRENCE_OVERRIDE_FILE,
                               ar.OVERRIDE_HEADERS)
    assert len(target_rows) == 1
    assert target_rows[0]["pdf_contains"] == local.stem
    assert target_rows[0]["actual_reading"] == "ㄐㄩㄝˊ"
    assert source_manifest["records"][0]["occurrence_id"] in target_rows[0]["note"]
    portable.prepare_portable_project(continued)
    (tmp_path / "first-away.pdf").rename(first)
    back = portable.import_project_decisions(continued, source)
    assert back["matched_actual_overrides"] == 1
    assert back["duplicates"] == 1
    assert ar._read_csv(sp.project_actual_evidence_root(source) / ar.OCCURRENCE_OVERRIDE_FILE,
                        ar.OVERRIDE_HEADERS)[0]["actual_reading"] == "ㄐㄩㄝˊ"
    assert not (tmp_path / "isolated-localappdata").exists()


def test_existing_target_rejects_unmapped_actual_without_changing_either_project(tmp_path):
    first, second = tmp_path / "first.pdf", tmp_path / "second.pdf"
    pdf(first, title="A")
    pdf(second, title="B")
    source, target = tmp_path / "source", tmp_path / "target"
    source_manifest, _ = project(source, first, session="A", event_positions=(0,))
    project(target, second, session="B")
    add_override(source, source_manifest["records"][0])
    reseal_actual_dynamic(source, first)
    before_source = (source / "人工判定資料庫.json").read_bytes()
    before_target = (target / "人工判定資料庫.json").read_bytes()
    with pytest.raises(ValueError, match="缺少 1 筆"):
        portable.import_project_decisions(source, target)
    assert (source / "人工判定資料庫.json").read_bytes() == before_source
    assert (target / "人工判定資料庫.json").read_bytes() == before_target


def test_two_independent_projects_merge_in_new_project_and_preserve_sources(tmp_path):
    files = [tmp_path / f"{letter}.pdf" for letter in "ABC"]
    for letter, path in zip("ABC", files):
        pdf(path, title=letter)
    first, second, merged = [tmp_path / name for name in ("first", "second", "merged")]
    project(first, files[0], session="A", event_positions=(0,))
    project(second, files[1], session="B", event_positions=(1,))
    original_a = (first / "人工判定資料庫.json").read_bytes()
    original_b = (second / "人工判定資料庫.json").read_bytes()

    def build_target(paths, output_dir):
        project(output_dir, files[2], session="C")

    with patch.object(sp, "run_pipeline_pdfs", side_effect=build_target):
        result = portable.merge_projects([first, second], files[2], merged)
    assert result["imported"] == 2 and result["conflicts"] == []
    assert len(sp.json_load_strict(merged / "人工判定資料庫.json")["events"]) == 2
    assert (first / "人工判定資料庫.json").read_bytes() == original_a
    assert (second / "人工判定資料庫.json").read_bytes() == original_b
    assert not (merged / portable.INCOMPLETE_FILE).exists()


def test_two_project_conflicting_decisions_keep_both_sources(tmp_path):
    files = [tmp_path / f"{letter}.pdf" for letter in "ABC"]
    for letter, path in zip("ABC", files):
        pdf(path, title=letter)
    first, second, merged = [tmp_path / name for name in ("first", "second", "merged")]
    project(first, files[0], session="A", event_positions=(0,))
    second_manifest, db = project(second, files[1], session="B", event_positions=(0,))
    db["events"][second_manifest["records"][0]["review_id"]]["expected_evidence"] = "另一來源"
    sp.json_save(second / "人工判定資料庫.json", db)
    before_a = (first / "人工判定資料庫.json").read_bytes()
    before_b = (second / "人工判定資料庫.json").read_bytes()

    def build_target(paths, output_dir):
        project(output_dir, files[2], session="C")

    with patch.object(sp, "run_pipeline_pdfs", side_effect=build_target):
        result = portable.merge_projects([first, second], files[2], merged)
    assert len(result["conflicts"]) == 1
    receipt = sp.json_load_strict(merged / portable.CONFLICT_FILE)
    assert receipt["conflicts"][0]["source_event"]["expected_evidence"] == "另一來源"
    assert receipt["conflicts"][0]["target_event"]["expected_evidence"] == "現版手冊"
    assert (first / "人工判定資料庫.json").read_bytes() == before_a
    assert (second / "人工判定資料庫.json").read_bytes() == before_b
    from standalone_gui import project_status_details
    assert "衝突" in project_status_details(merged)[0]
