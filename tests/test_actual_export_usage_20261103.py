"""Genuine PDF annotation appearance at the ACTUAL Excel export boundary."""
import hashlib
from pathlib import Path
from unittest.mock import patch

import fitz
import pytest
from openpyxl import load_workbook

import actual_review as ar
from actual_source_visual_safety import source_visual_safety
from tests.test_actual_source_visual_safety import make_visual_source


META = dict(version="5.8.0", session_id="isolated", session_schema_version="2.5.0",
            workbook_schema_version="2.5.0", review_id_schema_version="2.5.0")


def _annotated_source(tmp_path: Path, *, filled: bool):
    row = make_visual_source(tmp_path / "source", count=1)[0]
    pdf = Path(row["pdf"])
    with fitz.open(pdf) as doc:
        page = doc[0]
        # A large Square is deliberately related by its declared rectangle.
        # Its unfilled appearance paints only the distant frame, as in the
        # reported textbook's page-sized Square annotation.
        annotation = page.add_rect_annot(fitz.Rect(10, 10, 210, 230))
        annotation.set_border(width=2)
        annotation.set_colors(stroke=(1, 0, 0), **({"fill": (1, 0, 0)} if filled else {}))
        annotation.update()
        doc.saveIncr()
    row["pdf_sha256"] = hashlib.sha256(pdf.read_bytes()).hexdigest()
    target = fitz.Rect(*(row[key] for key in ("x0", "y0", "x1", "y1")))
    with fitz.open(pdf) as doc:
        page = doc[0]
        annotation = next(page.annots())
        assert annotation.type[1] == "Square" and annotation.rect.contains(target)
        with_annotations = page.get_pixmap(matrix=fitz.Matrix(4, 4), clip=target, annots=True)
        without_annotations = page.get_pixmap(matrix=fitz.Matrix(4, 4), clip=target, annots=False)
        assert (with_annotations.samples == without_annotations.samples) is (not filled)
    return row


def _edit_decision(xlsx, **changes):
    book = load_workbook(xlsx)
    sheet = book["actual待判定"]
    headers = [cell.value for cell in sheet[1]]
    for key, value in changes.items():
        sheet.cell(2, headers.index(key) + 1, value)
    book.save(xlsx)
    book.close()


def test_unfilled_page_square_keeps_real_source_and_export_import(tmp_path):
    row = _annotated_source(tmp_path, filled=False)
    assert source_visual_safety(row)["location_readable"]
    assert source_visual_safety(row)["donor_eligible"]
    output = tmp_path / "delivery"
    ar.export_actual_review_package(output, [row], **META)
    xlsx = output / "actual待判定_GPT包" / "actual待判定_給GPT.xlsx"
    groups = ar.build_actual_review_groups([row])
    meta, rows = ar._load_sheet_rows(xlsx, "actual待判定")
    assert meta["exported_group_count"] == meta["readable_sample_count"] == 1
    assert meta["blocked_sample_count"] == 0
    assert len(rows) == 1 and rows[0]["sample_a_occurrence_id"] == row["occurrence_id"]
    assert rows[0]["sample_a_availability"] == "READABLE"
    assert rows[0]["sample_a_image"] and len(list((xlsx.parent / "images").glob("*.png"))) == 2
    assert ar.validate_actual_sample_profile(xlsx, groups, output) == ar.load_actual_sample_profile(xlsx)

    _edit_decision(xlsx, decision="VERIFIED", actual_reading="ㄅㄨˋ", confidence="高", sample_a_checked="Y")
    with patch.object(ar, "apply_direct_visual_actual_batch", return_value={"group_results": [dict(ok=True)]}) as apply:
        result = ar.import_actual_review_workbook(tmp_path / "actual", xlsx, groups,
                                                  expected_metadata=META, source_output_dir=output)
        assert result["imported_groups"] == 1
        decisions = apply.call_args.args[1]
        assert len(decisions) == 1 and decisions[0]["checked_occurrence_ids"] == [row["occurrence_id"]]
        apply.call_args.kwargs["prewrite_guard"]()


def test_visible_square_cover_cannot_export_or_supply_exact_donor(tmp_path):
    row = _annotated_source(tmp_path, filled=True)
    safety = source_visual_safety(row)
    assert not safety["location_readable"] and not safety["donor_eligible"]
    output = tmp_path / "delivery"
    ar.export_actual_review_package(output, [row], **META)
    xlsx = output / "actual待判定_GPT包" / "actual待判定_給GPT.xlsx"
    _, rows = ar._load_sheet_rows(xlsx, "actual待判定")
    assert rows[0]["decision"] == "UNRESOLVED" and not rows[0]["sample_a_image"]
    assert not list((xlsx.parent / "images").glob("*.png"))
    _edit_decision(xlsx, decision="VERIFIED", actual_reading="ㄅㄨˋ", confidence="高", sample_a_checked="Y")
    with patch.object(ar, "apply_direct_visual_actual_batch") as apply:
        with pytest.raises(ValueError):
            ar.import_actual_review_workbook(tmp_path / "actual", xlsx, ar.build_actual_review_groups([row]),
                                             expected_metadata=META, source_output_dir=output)
        apply.assert_not_called()


def test_exported_frame_fails_closed_on_changed_or_missing_source_before_import(tmp_path):
    row = _annotated_source(tmp_path, filled=False)
    output = tmp_path / "delivery"
    ar.export_actual_review_package(output, [row], **META)
    xlsx = output / "actual待判定_GPT包" / "actual待判定_給GPT.xlsx"
    groups = ar.build_actual_review_groups([row])
    _edit_decision(xlsx, decision="VERIFIED", actual_reading="ㄅㄨˋ", confidence="高", sample_a_checked="Y")
    pdf = Path(row["pdf"])
    original = pdf.read_bytes()
    try:
        for changed in (True, False):
            if changed:
                pdf.write_bytes(original + b"\n% changed after export\n")
            else:
                pdf.unlink()
            with patch.object(ar, "apply_direct_visual_actual_batch") as apply:
                with pytest.raises((ValueError, FileNotFoundError)):
                    ar.import_actual_review_workbook(tmp_path / "actual", xlsx, groups,
                                                     expected_metadata=META, source_output_dir=output)
                apply.assert_not_called()
    finally:
        pdf.write_bytes(original)
