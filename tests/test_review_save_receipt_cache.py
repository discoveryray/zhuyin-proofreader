"""Operation-owned receipt reuse and byte-bound save gates; no Tk."""
import copy
import os

import pytest

import pdf_portability as p
import review_save_service as service_module
import standalone_proofread as sp
from review_save_service import ReviewSaveService, StaleReviewProjectError
from tests.test_expected_business_import import batch


def imported(batch):
    _, target, _, manifest, _, filled = batch
    sp.import_gpt_decisions(target, filled)
    db = sp.normalize_db(sp.json_load_strict(target / "人工判定資料庫.json"))
    return target, manifest, db


def shortcut(target, manifest, db, n):
    ledger = sp.materialize_ledger(manifest, db)
    row = p.expected_conflict_review_view(target, manifest, db, ledger)[n]
    return row["review_id"], sp.build_manual_expected_event(row, operation="CONFIRM_CURRENT_AS_EXPECTED")


def test_receipt_once_then_reuse_and_returned_views_cannot_tamper_cache(batch, monkeypatch):
    target, manifest, db = imported(batch)
    selections = [shortcut(target, manifest, db, n) for n in (2, 4)]
    service = ReviewSaveService(target)
    calls = []
    original = p._validated_expected_receipt
    def validate(*args):
        calls.append(1)
        return original(*args)
    monkeypatch.setattr(p, "_validated_expected_receipt", validate)
    first = service.save_event(*selections[0], expected_manifest=manifest, expected_db=db)
    assert len(calls) == 1
    assert first.timings["conflict_receipt_cache_hit"] == 0
    assert first.review_view == p.expected_conflict_review_view(target, manifest, first.db, first.ledger)
    calls.clear()
    # This GUI-owned view is mutable, but it is never the private receipt.
    pending_row = next(row for row in first.review_view if row["review_id"] == selections[1][0])
    pending_row["expected_business_conflicts"][0]["source_event"]["expected_set"] = ["ㄧ"]
    second = service.save_event(*selections[1], expected_manifest=manifest, expected_db=first.db)
    assert calls == []
    assert second.timings["ledger_full_rebuild"] == 0
    assert second.timings["conflict_receipt_cache_hit"] == 1
    assert second.review_view == p.expected_conflict_review_view(target, manifest, second.db, second.ledger)
    saved = sp.json_load_strict(target / "人工判定資料庫.json")
    assert saved == second.db
    assert saved["events"][selections[1][0]]["portability_conflict_resolution"]["original_conflicts"][0]["source_event"]["expected_set"] != ["ㄧ"]
    assert p.validate_conflict_state(target, manifest, saved) == []


def test_equal_size_mtime_receipt_drift_invalidates_cache_before_save(batch):
    target, manifest, db = imported(batch)
    rid, event = shortcut(target, manifest, db, 2)
    service = ReviewSaveService(target)
    service.load_resume_snapshot(expected_manifest=manifest, expected_db=db)
    path = target / p.CONFLICT_FILE
    original = path.read_bytes()
    stat = path.stat()
    changed = original.replace(b"source-session", b"source-changed", 1)
    assert len(changed) == len(original) and changed != original
    path.write_bytes(changed)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    before = (target / "人工判定資料庫.json").read_bytes()
    with pytest.raises(StaleReviewProjectError, match="衝突來源已變更"):
        service.save_event(rid, event, expected_manifest=manifest, expected_db=db)
    assert (target / "人工判定資料庫.json").read_bytes() == before


def test_receipt_bytes_are_bound_to_capture_before_parsing(batch, monkeypatch):
    target, manifest, db = imported(batch)
    rid, event = shortcut(target, manifest, db, 2)
    path = target / p.CONFLICT_FILE
    original_read = service_module._read
    def changed_read(candidate):
        raw = original_read(candidate)
        return raw + b" " if candidate == path else raw
    monkeypatch.setattr(service_module, "_read", changed_read)
    before = (target / "人工判定資料庫.json").read_bytes()
    with pytest.raises(StaleReviewProjectError, match="核對期間衝突來源已變更"):
        ReviewSaveService(target).save_event(rid, event, expected_manifest=manifest, expected_db=db)
    assert (target / "人工判定資料庫.json").read_bytes() == before


def test_cached_receipt_drift_at_final_boundary_keeps_original_database(batch, monkeypatch):
    target, manifest, db = imported(batch)
    rid, event = shortcut(target, manifest, db, 2)
    service = ReviewSaveService(target)
    service.load_resume_snapshot(expected_manifest=manifest, expected_db=db)
    path = target / p.CONFLICT_FILE
    before = (target / "人工判定資料庫.json").read_bytes()
    original = service._conflicts.resolution
    def drift(*args):
        result = original(*args)
        path.write_bytes(path.read_bytes() + b" ")
        return result
    monkeypatch.setattr(service._conflicts, "resolution", drift)
    with pytest.raises(StaleReviewProjectError, match="驗證期間專案來源"):
        service.save_event(rid, event, expected_manifest=manifest, expected_db=db)
    assert (target / "人工判定資料庫.json").read_bytes() == before
