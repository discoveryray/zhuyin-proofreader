"""Source safety executes against genuine isolated PDF/font bytes; no Tk."""
import copy
import csv
import hashlib
import json
from pathlib import Path

import pytest
import actual_review as actual
import global_glyph_promotion as promotion
import review_display as display
import standalone_proofread as pipeline
from tests.test_actual_source_visual_safety import make_visual_source


def _relocate_rows(rows, destination):
    """Retain the producer's original path claims in the archived source row."""
    source = Path(rows[0]["pdf"])
    destination.mkdir(parents=True, exist_ok=True)
    moved = source.rename(destination / source.name)
    for row in rows:
        row["source_record"].update(pdf=str(source), pdf_name=row["pdf_name"],
                                    pdf_sha256=row["pdf_sha256"])
        row["pdf"] = str(moved)
    return source, moved


def test_relocated_and_returned_paths_require_sealed_content_and_all_claims(tmp_path):
    from actual_source_visual_safety import source_visual_safety
    rows = rows_at(tmp_path / "old")
    assert actual.actual_visual_source_safety(rows[0])["donor_eligible"]
    original, moved = _relocate_rows(rows, tmp_path / "new")
    row = rows[0]
    source_before = copy.deepcopy(row["source_record"])
    assert not original.exists()
    assert source_visual_safety(row)["donor_eligible"]
    assert actual.actual_sample_availability(row) == "READABLE"
    assert display.occurrence_preview(row, display_width=200)[1] is not None
    raw = moved.read_bytes()
    original.write_bytes(raw)
    moved.write_bytes(raw + b"\n% different current PDF with the same name\n")
    # An existing incorrect current file must not fall back to the intact old
    # source just to obtain a matching SHA.
    assert actual.actual_visual_source_safety(row)["reason"] == "SOURCE_PDF_SHA_MISMATCH"
    root = tmp_path / "existing-evidence"
    actual.ensure_user_evidence_files(root)
    retained = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
    with pytest.raises(ValueError, match="SOURCE_PDF_SHA_MISMATCH"):
        actual.stage_manual_actual_group(root, actual.build_actual_group_for_entry(rows, row),
            "ㄅ", checked_occurrence_ids=[row["occurrence_id"]], source="visual")
    assert {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()} == retained
    moved.write_bytes(raw); original.unlink()
    moved.rename(original)
    assert not moved.exists()  # persisted outer=new, inner=old, PDF back at old
    source_only = copy.deepcopy(row); source_only.pop("pdf")
    assert source_visual_safety(source_only)["donor_eligible"]
    assert source_visual_safety(row)["donor_eligible"]
    assert actual.actual_sample_availability(row) == "READABLE"
    assert display.occurrence_preview(row, display_width=200)[1] is not None
    for key, value in (("pdf_sha256", "0" * 64), ("pdf_name", "renamed.pdf"),
                       ("實體頁碼", 2), ("x0", row["x0"] + 1),
                       ("glyph_id_字形索引", 999), ("occurrence_id", "other"),
                       ("review_id", "other")):
        bad = copy.deepcopy(row); bad["source_record"][key] = value
        assert source_visual_safety(bad)["reason"].startswith("SOURCE_TUPLE_CONTRADICTION:")
    for seal in (None, "invalid", "0" * 63):
        bad = copy.deepcopy(row); bad["pdf_sha256"] = seal
        bad["source_record"]["pdf_sha256"] = seal
        assert source_visual_safety(bad)["reason"] == "SOURCE_PDF_SHA_MISSING"
    # Same name alone cannot substitute for the retained content seal.
    original.write_bytes(original.read_bytes() + b"\n% changed content\n")
    proof = actual.actual_visual_source_safety(row)
    assert not proof["location_readable"] and not proof["donor_eligible"]
    assert row["source_record"] == source_before


def test_relocated_hidden_or_overpainted_source_and_ambiguous_lookup_stay_closed(tmp_path):
    from tests.test_actual_source_visual_safety import clipped_visual_source
    candidates = [rows_at(tmp_path / "covered", painting="replacement")[0],
        clipped_visual_source(tmp_path / "hidden", b"q\n0 0 1 1 re W n\n")]
    for index, row in enumerate(candidates):
        original, moved = _relocate_rows([row], tmp_path / f"moved-{index}")
        assert hashlib.sha256(moved.read_bytes()).hexdigest() == row["pdf_sha256"]
        before = copy.deepcopy(row)
        assert not actual.actual_visual_source_safety(row)["location_readable"]
        assert display.occurrence_preview(row, display_width=200)[1] is None
        root = tmp_path / f"must-not-write-{index}"
        with pytest.raises(ValueError):
            actual.stage_manual_actual_group(root, actual.build_actual_group_for_entry([row], row),
                "ㄅ", checked_occurrence_ids=[row["occurrence_id"]], source="visual")
        assert not root.exists() and row == before
    row = rows_at(tmp_path / "ambiguous")[0]
    original, moved = _relocate_rows([row], tmp_path / "away")
    raw = moved.read_bytes(); moved.unlink()
    project = tmp_path / "lookup" / "output"; project.mkdir(parents=True)
    for name in ("a", "b"):
        child = project / name; child.mkdir(); (child / row["pdf_name"]).write_bytes(raw)
    proof = actual.actual_visual_source_safety(row, project)
    assert not proof["location_readable"]
    assert "找不到現版 PDF" in proof["reason"]


def test_relocated_source_replacement_is_rejected_before_first_staging_write(tmp_path, monkeypatch):
    rows = rows_at(tmp_path / "old")
    original, moved = _relocate_rows(rows, tmp_path / "new")
    root = tmp_path / "actual"
    group = actual.build_actual_group_for_entry(rows, rows[0])
    # Preserve already stored decisions and evidence on refusal, not just an
    # absent directory. Only inject the adversarial file replacement timing.
    actual.ensure_user_evidence_files(root)
    before = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
    load = actual.load_manual_actual_staging
    def replace_after_proof(path):
        result = load(path)
        moved.write_bytes(moved.read_bytes() + b"\n% source replaced before write\n")
        return result
    monkeypatch.setattr(actual, "load_manual_actual_staging", replace_after_proof)
    with pytest.raises(ValueError, match="SOURCE_FILE_CHANGED"):
        actual.stage_manual_actual_group(root, group, "ㄅ",
            checked_occurrence_ids=[rows[0]["occurrence_id"]], source="visual")
    assert {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()} == before


def test_relocated_geometry_adapter_receives_resolved_path_without_authorizing(tmp_path, monkeypatch):
    """Failure-only dispatch probe, never a mock geometry/source acceptance."""
    import actual_render_geometry as geometry
    from actual_source_visual_safety import source_visual_safety
    row = rows_at(tmp_path / "old", count=1)[0]
    original, moved = _relocate_rows([row], tmp_path / "new")
    raw = moved.read_bytes(); moved.rename(original)
    row["x1"] = row["x0"]
    # Reach only the adapter handoff with a real sealed PDF. The injected
    # adapter always raises: no glyph visibility, geometry or donor success
    # can be supplied by this probe, and no production allowlist is changed.
    monkeypatch.setattr(geometry, "EXACT_VERTICAL_CFF_PDF", row["pdf_sha256"])
    received = []
    def refuse(path, entry, *, digest):
        received.append(Path(path))
        assert Path(path).read_bytes() == raw and digest == row["pdf_sha256"]
        raise ValueError("SOURCE_GEOMETRY_DISPATCH_PROBE_REFUSAL")
    monkeypatch.setattr(geometry, "exact_vertical_cff_location", refuse)
    for options in ({}, {"pdf": raw}):
        proof = source_visual_safety(row, **options)
        assert proof["reason"] == "SOURCE_GEOMETRY_DISPATCH_PROBE_REFUSAL"
        assert not proof["location_readable"] and not proof["donor_eligible"]
    assert received == [original, original]


@pytest.mark.parametrize("returned", [False, True])
def test_real_producer_relocated_records_preview_stage_save_reopen(tmp_path, monkeypatch, returned):
    """Only decoder output is synthetic; source bytes and all public gates run."""
    from unittest.mock import patch
    from tests.test_pdf_portability_integration import _pdfs, _synthetic_unresolved_decode
    from review_save_service import ReviewSaveService
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-user"))
    original, _ = _pdfs(tmp_path)
    output = tmp_path / "project"
    with patch.object(pipeline, "decode", side_effect=_synthetic_unresolved_decode), \
         patch.object(pipeline, "actual_workbook_global_exact_dependencies", return_value=()), \
         patch("check_pronunciation_candidates.actual_workbook_global_exact_dependencies", return_value=()):
        pipeline.run_pipeline_pdfs([original], output, defer_excel_reports=True)
        initial = pipeline.json_load_strict(output / "校對工作階段.json")
        evidence_root = pipeline.project_actual_evidence_root(output)
        seed_overrides = csv_rows(evidence_root, actual.OCCURRENCE_OVERRIDE_FILE)
        seed_reusable = {name: csv_rows(evidence_root, name)
                         for name in (actual.USER_GLYF_FILE, actual.USER_CFF_FILE)}
        old_source = copy.deepcopy(initial["records"][0]["source_record"])
        assert old_source["pdf"] == str(original)
        moved = tmp_path / "moved" / original.name; moved.parent.mkdir()
        original.rename(moved)
        info = initial["pdfs"][0]
        metadata = pipeline.workbook_metadata(Path(info["actual_workbook"]))
        # The actual workbooks/candidate ledger still contain the original path;
        # exercise the real collect_manifest producer rather than fabricating
        # an entry missing source_record.pdf.
        manifest = pipeline.collect_manifest([moved], output / "01_實際注音", output / "02_候選報告",
            actual_fingerprints={moved.name: {"fingerprint": metadata["actual_asset_fingerprint"],
                "components": json.loads(metadata["actual_asset_fingerprint_components"])}},
            source_validation=initial["source_validation"])
        entry, peer = manifest["records"]
        assert entry["pdf"] == str(moved) and entry["source_record"] == old_source
        pipeline.json_save(output / "校對工作階段.json", manifest)
        if returned:
            moved.rename(original)
            # No manifest or historical row rewrite when returning to old path.
        assert actual.actual_sample_availability(entry, output) == "READABLE"
        assert display.occurrence_preview(entry, output_dir=output, display_width=200)[1] is not None
        service = ReviewSaveService(output)
        event = pipeline.build_manual_expected_event(peer, operation="ENTER_EXPECTED", expected_set="ㄐㄩㄝˊ")
        saved = service.save_event(peer["review_id"], event)
        retained_event = copy.deepcopy(saved.db["events"][peer["review_id"]])
        before = pipeline.materialize_ledger(manifest, saved.db)
        pipeline.stage_manual_actual_correction(output, entry["review_id"], "ㄐㄩㄝˊ")
        staged = actual.load_manual_actual_staging(pipeline.project_actual_evidence_root(output))["staged_groups"]
        assert len(staged) == 1 and staged[0]["checked_occurrence_ids"] == [entry["occurrence_id"]]
        result = pipeline.apply_staged_manual_actual_corrections(output)
        assert result["applied_group_count"] == 1
        reopened = ReviewSaveService(output).load_resume_snapshot()
        current = pipeline.json_load_strict(output / "校對工作階段.json")
        db = pipeline.json_load_strict(output / "人工判定資料庫.json")
        after = pipeline.materialize_ledger(current, db)
        assert after[0]["actual"] == "ㄐㄩㄝˊ"
        assert not before[1]["actual"] and after[1]["actual"] == before[1]["actual"]
        assert reopened.ledger == after
        assert db["events"][peer["review_id"]] == retained_event
        assert [(r["expected_set"], r["expected_evidence"]) for r in after] == [
            (r["expected_set"], r["expected_evidence"]) for r in before]
        overrides = csv_rows(pipeline.project_actual_evidence_root(output), actual.OCCURRENCE_OVERRIDE_FILE)
        fields = ("pdf_contains", "pdf_excludes", "page", "target_char", "stable_key", "x0", "y0")
        key = actual._override_key_from_entry(entry)
        new = [r for r in overrides if tuple(r[field] for field in fields) == key]
        retained = [r for r in overrides if tuple(r[field] for field in fields) != key]
        assert len(new) == 1 and new[0]["actual_reading"] == "ㄐㄩㄝˊ"
        # The existing writer canonically sorts CSV rows. Preserve every seed
        # field and its multiplicity; physical input order is not identity.
        canonical = lambda rows: sorted(json.dumps(r, sort_keys=True) for r in rows)
        assert canonical(retained) == canonical(seed_overrides)
        for name, seed in seed_reusable.items():
            assert canonical(csv_rows(pipeline.project_actual_evidence_root(output), name)) == canonical(seed)
        assert actual.load_manual_actual_staging(pipeline.project_actual_evidence_root(output))["staged_groups"] == []


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


def test_erased_outside_tone_never_learns_or_delivers_raw_identity(tmp_path):
    import hashlib
    import global_exact_glyph_library as library
    from tests.test_actual_source_visual_safety import later_ink_visual_source
    for painting in ("path", "image", "text"):
        row = later_ink_visual_source(tmp_path / painting, painting)
        row.update(expected_set=["ㄆ"],expected_evidence="independent expected")
        before = copy.deepcopy(row)
        group = actual.build_actual_group_for_entry([row],row)
        root = tmp_path / painting / "actual"
        assert display.occurrence_preview(row,display_width=200)[1] is None
        for write in (actual.stage_manual_actual_group,actual.apply_verified_actual_group):
            with pytest.raises(ValueError):
                write(root,group,"ㄅ",checked_occurrence_ids=[row["occurrence_id"]],source="visual")
            assert not root.exists()
        with pytest.raises(ValueError):
            actual.apply_direct_visual_actual_batch(root,[{"group":group,"reading":"ㄅ",
                "checked_occurrence_ids":[row["occurrence_id"]],"source":"visual"}],source_context=context_for([row]))
        assert not root.exists()  # no override, learning, provenance or project transaction
        intents,admissions=promotion.direct_visual_intents(group,"ㄅ",[row["occurrence_id"]],context_for([row]))
        assert not intents and admissions[0]["status"] == "NON_GLOBAL_ELIGIBLE"
        assert row == before
        normal = copy.deepcopy(row)
        normal_pdf = tmp_path / painting / "normal/book.pdf"
        normal.update(pdf=str(normal_pdf),pdf_name=normal_pdf.name,
            pdf_sha256=hashlib.sha256(normal_pdf.read_bytes()).hexdigest())
        normal_group = actual.build_actual_group_for_entry([normal],normal)
        genuine,_=promotion.direct_visual_intents(normal_group,"ㄅ",[normal["occurrence_id"]],context_for([normal]))
        assert len(genuine)==1
        identity=genuine[0]["payload"]["identity"]
        evidence=genuine[0]["payload"]["evidence"]
        frozen={"session_id":evidence["source_project_id"],"pdf_sha256":row["pdf_sha256"],
            "font_program_sha256":evidence["source_font_program_sha256"],"occurrence_id":row["occurrence_id"],
            "review_id":row["review_id"],"identity":identity,"reading":"ㄅ","confirmation_channel":library.PDF_PAGE_VISUAL}
        # A real canonical retained legacy envelope is not current-source proof.
        retained=library.make_direct_evidence_intent(identity,reading="ㄅ",
            source_project_id=evidence["source_project_id"],source_pdf_sha256=row["pdf_sha256"],
            source_font_program_sha256=evidence["source_font_program_sha256"],
            source_occurrence_id=row["occurrence_id"],source_review_id=row["review_id"],
            decision_snapshot_sha256=library._canonical_sha256(frozen))
        promotion.enqueue_promotion_intents(root,[retained])
        outbox=root/promotion.GLOBAL_PROMOTION_OUTBOX_FILE
        original=outbox.read_bytes()
        repo=library.GlobalExactGlyphRepository.resolved(tmp_path/painting/"global")
        result=promotion.deliver_pending_promotion_outbox(root,repository=repo,
            source_context=context_for([row]),source_entries=[row])
        assert result["status"]=="PENDING_RETRY"
        assert outbox.read_bytes()==original and not repo.path.exists()
        assert not (root/actual.USER_GLYF_FILE).exists()
