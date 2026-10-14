"""Source safety executes against genuine isolated PDF/font bytes; no Tk."""
import copy
import csv
from pathlib import Path

import pytest
import actual_review as actual
import global_glyph_promotion as promotion
import review_display as display
import standalone_proofread as pipeline
from tests.test_actual_source_visual_safety import make_visual_source


def rows_at(root, *, painting=None, count=2):
    rows = make_visual_source(root, painting=painting, count=count)
    for row in rows:
        row.update(expected_set=["ㄆ"], expected_evidence="INDEPENDENT_EXPECTED", actual_evidence="raw fixture")
    return rows


def context_for(rows):
    return {"session_id": "isolated-source-safe", "pdfs": {
        row["pdf_name"]: {"pdf": row["pdf"], "pdf_sha256": row["pdf_sha256"]} for row in rows}}


def csv_rows(root, name):
    with (root / name).open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def test_covered_target_never_confirms_stages_or_writes(tmp_path):
    rows = rows_at(tmp_path / "source", painting="replacement")
    entry = rows[0]
    group = actual.build_actual_group_for_entry(rows, entry)
    root = tmp_path / "actual"
    _pix, box, _notice = display.occurrence_preview(entry, display_width=200)
    assert box is None
    with pytest.raises(ValueError):
        actual.actual_sample_availability(entry)
    with pytest.raises(ValueError):
        actual.stage_manual_actual_group(root, group, "ㄅ", checked_occurrence_ids=[entry["occurrence_id"]], source="visual")
    with pytest.raises(ValueError):
        actual.apply_verified_actual_group(root, group, "ㄅ", checked_occurrence_ids=[entry["occurrence_id"]], source="visual")
    with pytest.raises(ValueError):
        actual.apply_direct_visual_actual_batch(root, [{"group": group, "reading": "ㄅ",
            "checked_occurrence_ids": [entry["occurrence_id"]], "source": "visual"}], source_context=context_for(rows))
    with pytest.raises(ValueError):
        pipeline.require_current_actual_visual_source(entry, tmp_path)
    assert not root.exists()


def test_genuine_normal_stage_transaction_reopen_preserves_quorum_and_expected(tmp_path):
    rows = rows_at(tmp_path / "source", count=3)
    before = copy.deepcopy(rows)
    group = actual.build_actual_group_for_entry(rows, rows[0])
    root = tmp_path / "actual"
    checked = [row["occurrence_id"] for row in rows[:2]]
    actual.stage_manual_actual_group(root, group, "ㄅ", checked_occurrence_ids=checked, source="visual")
    result = actual.apply_staged_manual_actual_batch(root, [group], source_context=context_for(rows))
    item = result["group_results"][0]
    assert item["propagated_to_group"]
    assert item["learning_level"] == "VERIFIED_EXACT_GLYPH"
    assert set(item["target_occurrence_ids"]) == {row["occurrence_id"] for row in rows}
    assert len(csv_rows(root, actual.OCCURRENCE_OVERRIDE_FILE)) == 3
    assert actual.load_user_verified_glyf(root / actual.USER_GLYF_FILE)
    assert actual.load_manual_actual_staging(root)["staged_groups"] == []
    # A propagated peer cannot masquerade as directly viewed local evidence.
    assert not actual.has_direct_occurrence_actual(root, rows[2], "ㄅ")
    assert rows == before
    delivered = promotion.deliver_pending_promotion_outbox(root,
        source_context=context_for(rows), source_entries=rows,
        repository=__import__("global_exact_glyph_library").GlobalExactGlyphRepository.resolved(tmp_path / "global"))
    assert delivered["status"] == "DELIVERED" and delivered["delivered_count"] == 2


def test_location_only_writes_local_override_without_learning_or_donor(tmp_path):
    row = rows_at(tmp_path / "source", count=1)[0]
    del row["source_record"]["TTF字形SHA256"]
    group = actual.build_actual_group_for_entry([row], row)
    root = tmp_path / "actual"
    result = actual.apply_direct_visual_actual_batch(root, [{"group": group, "reading": "ㄅ",
        "checked_occurrence_ids": [row["occurrence_id"]], "source": "visual"}], source_context=context_for([row]))
    assert result["group_results"][0]["target_occurrence_ids"] == [row["occurrence_id"]]
    assert not result["group_results"][0]["propagated_to_group"]
    assert not csv_rows(root, actual.USER_GLYF_FILE)
    assert not csv_rows(root, actual.USER_CFF_FILE)
    assert promotion.load_promotion_outbox(root)["items"] == []
    assert len(csv_rows(root, actual.GLYPH_PROVENANCE_FILE)) == 1
    assert actual.has_direct_occurrence_actual(root, row, "ㄅ")
    provenance = root / actual.GLYPH_PROVENANCE_FILE
    records = csv_rows(root, actual.GLYPH_PROVENANCE_FILE)
    records[0]["event_id"] = "gpe_" + "0"*24
    with provenance.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=actual.GLYPH_PROVENANCE_HEADERS)
        writer.writeheader(); writer.writerows(records)
    assert not actual.has_direct_occurrence_actual(root, row, "ㄅ")


def test_missing_or_forged_source_cannot_bypass_low_level_writer(tmp_path):
    row = rows_at(tmp_path / "source", count=1)[0]
    group = actual.build_actual_group_for_entry([row], row)
    root = tmp_path / "actual"
    for changed in (dict(row, pdf=str(tmp_path / "missing.pdf")),
                    dict(row, pdf_sha256="0"*64), dict(row, glyph_id=999)):
        bad = {**group, "members": [changed]}
        with pytest.raises(ValueError):
            actual.apply_verified_actual_group(root, bad, "ㄅ", checked_occurrence_ids=[row["occurrence_id"]], source="visual")
        assert not root.exists()
    bad = {**group, "kind": "OCCURRENCE_ONLY", "exact_key": row["occurrence_id"]}
    with pytest.raises(ValueError, match="exact identity"):
        actual.apply_verified_actual_group(root, bad, "ㄅ", source="visual")
    assert not root.exists()


def test_retained_outbox_requires_current_source_and_preserves_pending_bytes(tmp_path):
    row = rows_at(tmp_path / "source", count=1)[0]
    group = actual.build_actual_group_for_entry([row], row)
    intents, admissions = promotion.direct_visual_intents(group, "ㄅ", [row["occurrence_id"]], context_for([row]))
    assert len(intents) == 1 and admissions[0]["status"] == "ELIGIBLE"
    root = tmp_path / "actual"
    promotion.enqueue_promotion_intents(root, intents)
    before = (root / promotion.GLOBAL_PROMOTION_OUTBOX_FILE).read_bytes()
    result = promotion.deliver_pending_promotion_outbox(root)
    assert result["status"] == "PENDING_RETRY"
    assert (root / promotion.GLOBAL_PROMOTION_OUTBOX_FILE).read_bytes() == before
    Path(row["pdf"]).write_bytes(Path(row["pdf"]).read_bytes() + b"\n% changed\n")
    result = promotion.deliver_pending_promotion_outbox(root, source_context=context_for([row]), source_entries=[row])
    assert result["status"] == "PENDING_RETRY"
    assert (root / promotion.GLOBAL_PROMOTION_OUTBOX_FILE).read_bytes() == before


def test_source_guard_at_save_keeps_expected_input_independent(tmp_path):
    from tests.test_staged_review_navigation_v580 import create_staged_navigation_fixture
    from review_save_service import ReviewSaveService
    output = tmp_path / "output"
    manifest = create_staged_navigation_fixture(output)
    db = pipeline.load_or_initialize_db(output)
    service = ReviewSaveService(output)
    row = manifest["records"][0]
    expected = pipeline.build_manual_expected_event(row, operation="ENTER_EXPECTED", expected_set="ㄎㄢ")
    result = service.save_event(row["review_id"], expected, expected_manifest=manifest, expected_db=db)
    reviewed = result.resolved_entry
    event = {**result.db["events"][row["review_id"]], "action": "確認現版差異",
             "gui_confirmation": pipeline.build_gui_confirmation(reviewed),
             "confirmation_actual_snapshot": pipeline.actual_confirmation_snapshot(reviewed)}
    # A genuine normal visible source can be confirmed at the durable gate.
    confirmation = service.save_event(row["review_id"], event, expected_manifest=manifest, expected_db=result.db)
    assert confirmation.resolved_entry["state"] == "TEXTBOOK_ERROR_CONFIRMED"
    before = (output / "人工判定資料庫.json").read_bytes()
    changed = copy.deepcopy(event)
    # Source checks cannot be bypassed by providing a new confirmation snapshot.
    source = Path(row["pdf"])
    source.write_bytes(source.read_bytes() + b"\n% externally changed source\n")
    with pytest.raises(ValueError):
        service.save_event(row["review_id"], changed, expected_manifest=manifest, expected_db=confirmation.db)
    assert (output / "人工判定資料庫.json").read_bytes() == before


def test_portable_actual_mapping_cannot_authorize_later_paint_target(tmp_path):
    from pdf_portability import _require_portable_actual_visual_pair
    source = rows_at(tmp_path / "source", count=1)[0]
    target = rows_at(tmp_path / "covered", painting="replacement", count=1)[0]
    assert actual.actual_visual_source_safety(source)["donor_eligible"]
    # Even an existing trusted page mapping and checked source cannot donate
    # raw glyph pronunciation to a different final painted target.
    with pytest.raises(ValueError):
        _require_portable_actual_visual_pair(source, target, tmp_path, directly_checked=True)
    assert not pipeline.project_actual_evidence_root(tmp_path).exists()


def test_clipped_source_is_refused_by_preview_writers_and_retained_delivery(tmp_path):
    import hashlib
    import global_exact_glyph_library as library
    from tests.test_actual_source_visual_safety import clipped_visual_source
    row = clipped_visual_source(tmp_path / "source", b"q\n0 0 1 1 re W n\n")
    row.update(actual="ㄅ", actual_status="RESOLVED", actual_evidence="independent raw fixture",
        expected_set=["ㄆ"], expected_status="RESOLVED", expected_evidence="independent expected",
        context_evidence="fixture original context", line="fixture original context")
    before = copy.deepcopy(row)
    group = actual.build_actual_group_for_entry([row], row)
    root = tmp_path / "actual"
    assert display.occurrence_preview(row, display_width=200)[1] is None
    with pytest.raises(ValueError):
        actual.actual_sample_availability(row)
    for write in (actual.stage_manual_actual_group, actual.apply_verified_actual_group):
        with pytest.raises(ValueError):
            write(root, group, "ㄅ", checked_occurrence_ids=[row["occurrence_id"]], source="visual")
        assert not root.exists()
    with pytest.raises(ValueError):
        actual.apply_direct_visual_actual_batch(root, [{"group":group,"reading":"ㄅ",
            "checked_occurrence_ids":[row["occurrence_id"]],"source":"visual"}],source_context=context_for([row]))
    assert not root.exists()
    intents, admissions = promotion.direct_visual_intents(group,"ㄅ",[row["occurrence_id"]],context_for([row]))
    assert intents == [] and admissions[0]["status"] == "NON_GLOBAL_ELIGIBLE"
    with pytest.raises(ValueError):
        pipeline.require_current_actual_visual_source(row, tmp_path)
    event = pipeline.build_manual_expected_event(row, operation="ENTER_EXPECTED", expected_set="ㄆ")
    assert event["manual_expected_decision"]["operation"] == "ENTER_EXPECTED"
    assert row == before

    # A correctly shaped legacy pending intent may predate the new clip gate.
    # Canonical library fixtures do not prove its source. Delivery must reprove
    # the *same clipped source seal*, not just notice an unrelated SHA change.
    normal = copy.deepcopy(row)
    normal_pdf = tmp_path / "source/normal/book.pdf"
    normal.update(pdf=str(normal_pdf),pdf_name=normal_pdf.name,
        pdf_sha256=hashlib.sha256(normal_pdf.read_bytes()).hexdigest())
    normal_group = actual.build_actual_group_for_entry([normal],normal)
    genuine,_ = promotion.direct_visual_intents(normal_group,"ㄅ",[normal["occurrence_id"]],context_for([normal]))
    assert len(genuine) == 1
    identity = genuine[0]["payload"]["identity"]
    evidence = genuine[0]["payload"]["evidence"]
    frozen = {"session_id":evidence["source_project_id"],"pdf_sha256":row["pdf_sha256"],
        "font_program_sha256":evidence["source_font_program_sha256"],"occurrence_id":row["occurrence_id"],
        "review_id":row["review_id"],"identity":identity,"reading":"ㄅ",
        "confirmation_channel":library.PDF_PAGE_VISUAL}
    retained = library.make_direct_evidence_intent(identity,reading="ㄅ",
        source_project_id=evidence["source_project_id"],source_pdf_sha256=row["pdf_sha256"],
        source_font_program_sha256=evidence["source_font_program_sha256"],
        source_occurrence_id=row["occurrence_id"],source_review_id=row["review_id"],
        decision_snapshot_sha256=library._canonical_sha256(frozen))
    promotion.enqueue_promotion_intents(root,[retained])
    outbox = root / promotion.GLOBAL_PROMOTION_OUTBOX_FILE
    pending_before = outbox.read_bytes()
    repo = library.GlobalExactGlyphRepository.resolved(tmp_path / "global")
    result = promotion.deliver_pending_promotion_outbox(root,repository=repo,
        source_context=context_for([row]),source_entries=[row])
    assert result["status"] == "PENDING_RETRY"
    assert not repo.path.exists() and outbox.read_bytes() == pending_before
    assert not (root/actual.OCCURRENCE_OVERRIDE_FILE).exists()
