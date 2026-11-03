"""Locked source groups, real PDF bytes and bounded legacy adapters; no Tk."""
from pathlib import Path
from unittest.mock import patch

import pytest
from openpyxl import load_workbook

import actual_review as ar
import pdf_portability as portable
from tests.test_actual_source_visual_safety import make_visual_source
from tests.test_actual_export_usage_20261103 import META
from tests.test_actual_sample_profile import edit_row, seal_content_proof


def exported(tmp_path, *, painting="path", count=2):
    rows = make_visual_source(tmp_path / "source", painting=painting, count=count)
    output = tmp_path / "out"
    ar.export_actual_review_package(output, rows, **META)
    return rows, output, output / "actual待判定_GPT包" / "actual待判定_給GPT.xlsx"


def import_workbook(tmp_path, rows, output, xlsx, **kwargs):
    return ar.import_actual_review_workbook(tmp_path / "actual", xlsx, ar.build_actual_review_groups(rows),
                                            expected_metadata=META, source_output_dir=output, **kwargs)


def test_mixed_group_keeps_every_sample_and_locks_whole_group(tmp_path):
    rows, output, xlsx = exported(tmp_path)
    _, contents = ar._load_sheet_rows(xlsx, "actual待判定")
    assert len(contents) == 1
    item = contents[0]
    assert {item["sample_a_occurrence_id"], item["sample_b_occurrence_id"]} == {r["occurrence_id"] for r in rows}
    assert {item["sample_a_availability"], item["sample_b_availability"]} == {"READABLE", "SOURCE_OVERPAINT_CONFIRMED"}
    assert item["decision"] == "UNRESOLVED"
    assert item["sample_a_checked"] == item["sample_b_checked"] == "N"
    assert not item["actual_reading"] and not item["confidence"]
    assert len(list((xlsx.parent / "images").glob("*.png"))) == 2
    for label in ("a", "b"):
        assert bool(item[f"sample_{label}_image"]) is (item[f"sample_{label}_availability"] == "READABLE")
    book = load_workbook(xlsx)
    sheet = book["actual待判定"]
    assert sheet.protection.sheet and all(cell.protection.locked for cell in sheet[2])
    book.close()
    assert ar.validate_actual_sample_profile(xlsx, ar.build_actual_review_groups(rows), output)


def test_all_unreadable_still_exports_complete_plan_without_images(tmp_path):
    rows, output, xlsx = exported(tmp_path, count=1)
    _, contents = ar._load_sheet_rows(xlsx, "actual待判定")
    assert len(contents) == 1 and contents[0]["sample_a_occurrence_id"] == rows[0]["occurrence_id"]
    assert contents[0]["decision"] == "UNRESOLVED"
    assert not list((xlsx.parent / "images").glob("*.png"))
    assert ar.validate_actual_sample_profile(xlsx, ar.build_actual_review_groups(rows), output)


def test_mixed_group_forgery_rejected_before_any_transaction(tmp_path):
    rows, output, xlsx = exported(tmp_path)
    original = xlsx.read_bytes()
    changes = [dict(decision="VERIFIED", actual_reading="ㄅㄨˋ", confidence="高", sample_a_checked="Y", sample_b_checked="Y"),
               dict(actual_reading="ㄅㄨˋ"), dict(confidence="高"), dict(sample_a_checked="Y"),
               dict(sample_b_checked="Y"), dict(source_availability_reason=""),
               dict(sample_a_availability="READABLE"), dict(sample_a_image="forged.png"),
               dict(sample_b_occurrence_id=rows[0]["occurrence_id"])]
    for values in changes:
        xlsx.write_bytes(original)
        book = load_workbook(xlsx); edit_row(book, values); book.save(xlsx); book.close()
        with patch.object(ar, "apply_direct_visual_actual_batch") as apply:
            with pytest.raises(ValueError):
                import_workbook(tmp_path, rows, output, xlsx)
            apply.assert_not_called()
    assert not (tmp_path / "actual").exists()


def test_self_sealed_profile_and_unknown_version_cannot_create_readability(tmp_path):
    rows, output, xlsx = exported(tmp_path)
    original = xlsx.read_bytes()
    for version in ("1.3", "1.2", "999"):
        xlsx.write_bytes(original)
        book = load_workbook(xlsx)
        profile = ar._load_actual_sample_profile(book)
        if version == "1.2":
            # A v1 profile cannot carry the new blocked-source contract.
            profile["schema"] = "actual-gpt-sample-profile/1"
        else:
            for sample in profile["groups"][0]["samples"].values():
                sample.update(availability="READABLE", reason="")
        del book[ar.ACTUAL_SAMPLE_PROFILE_SHEET]
        meta = book["匯入中繼資料"]
        for index in range(meta.max_row, 0, -1):
            if meta.cell(index, 1).value in ar.ACTUAL_SAMPLE_PROFILE_META:
                meta.delete_rows(index)
            elif meta.cell(index, 1).value == "actual_review_schema_version":
                meta.cell(index, 2).value = version
        ar._write_actual_sample_profile(book, profile)
        edit_row(book, dict(sample_a_availability="READABLE", source_availability_reason=""))
        book.save(xlsx); book.close()
        with patch.object(ar, "apply_direct_visual_actual_batch") as apply:
            with pytest.raises(ValueError):
                import_workbook(tmp_path, rows, output, xlsx)
            apply.assert_not_called()


def test_readable_normal_and_explicit_12_profile_adapter(tmp_path):
    rows, output, xlsx = exported(tmp_path, painting=None, count=1)
    book = load_workbook(xlsx)
    profile = ar._load_actual_sample_profile(book)
    assert profile["schema"] == "actual-gpt-sample-profile/2"
    profile["schema"] = "actual-gpt-sample-profile/1"
    del book[ar.ACTUAL_SAMPLE_PROFILE_SHEET]
    meta = book["匯入中繼資料"]
    for index in range(meta.max_row, 0, -1):
        if meta.cell(index, 1).value in ar.ACTUAL_SAMPLE_PROFILE_META:
            meta.delete_rows(index)
        elif meta.cell(index, 1).value == "actual_review_schema_version":
            meta.cell(index, 2).value = "1.2"
    ar._write_actual_sample_profile(book, profile)
    edit_row(book, dict(decision="VERIFIED", actual_reading="ㄅㄨˋ", confidence="高", sample_a_checked="Y"))
    book.save(xlsx); book.close()
    with patch.object(ar, "apply_direct_visual_actual_batch", return_value={"group_results": [dict(ok=True)]}) as apply:
        result = import_workbook(tmp_path, rows, output, xlsx)
        assert result["imported_groups"] == 1
        apply.call_args.kwargs["prewrite_guard"]()


def test_prewrite_rechecks_blocked_sample_source_and_profile(tmp_path):
    rows, output, xlsx = exported(tmp_path)
    # Capture the real prewrite guard even though this no-op package has no votes.
    with patch.object(ar, "apply_direct_visual_actual_batch", return_value={"group_results": []}) as apply:
        import_workbook(tmp_path, rows, output, xlsx)
        guard = apply.call_args.kwargs["prewrite_guard"]
    pdf = Path(rows[0]["pdf"])
    original = pdf.read_bytes()
    pdf.write_bytes(original + b"\n% changed\n")
    with pytest.raises(ValueError): guard()
    pdf.unlink()
    with pytest.raises((ValueError, FileNotFoundError)): guard()
    pdf.write_bytes(original)
    book = load_workbook(xlsx); edit_row(book, dict(actual_reading="ㄅㄨˋ")); book.save(xlsx); book.close()
    with pytest.raises(ValueError): guard()


def test_portable_profile_binding_and_locked_decision_reject_tampering(tmp_path):
    rows, output, xlsx = exported(tmp_path)
    pdf = Path(rows[0]["pdf"])
    sha, pages = portable._pdf_content_snapshot(pdf)
    manifest = dict(session_id=META["session_id"], manifest_integrity_sha256="fixture-seal",
                    pdfs=[dict(pdf=str(pdf), pdf_name=pdf.name, pdf_sha256=sha)], records=rows)
    book = load_workbook(xlsx); seal_content_proof(book, manifest, pages); book.save(xlsx); book.close()
    assert portable._guard_actual_excel_sources(xlsx, rows, output, manifest, {sha: sha})
    book = load_workbook(xlsx); edit_row(book, dict(decision="VERIFIED", actual_reading="ㄅㄨˋ", confidence="高", sample_a_checked="Y", sample_b_checked="Y")); book.save(xlsx); book.close()
    with pytest.raises(ValueError):
        portable._guard_actual_excel_sources(xlsx, rows, output, manifest, {sha: sha})
