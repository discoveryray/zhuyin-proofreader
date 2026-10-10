"""Explicit human shortcut and real save boundaries, without Tcl/Tk objects."""
import copy

import pytest

import pdf_portability as p
import review_gui as gui
import standalone_proofread as sp
from review_save_service import ReviewSaveService, StaleReviewProjectError
from tests.test_expected_business_import import batch, state
from tests.test_manual_review_usability_v580 import make_entry
from tests.test_staged_review_navigation_v580 import create_staged_navigation_fixture, headless_app
from tests.review_save_test_support import wait_for_save


class Button:
    def __init__(self):
        self.options = {"state": "normal", "text": ""}

    def config(self, **values):
        self.options.update(values)

    def cget(self, key):
        return self.options[key]

    def invoke(self):
        if self.options.get("state") != "disabled":
            return self.options["command"]()


def controls(app):
    app.primary, app.secondary = Button(), Button()
    app.more_button, app.later = Button(), Button()
    return app


@pytest.mark.parametrize("kind", ["unresolved", "rule_difference", "pending_pass", "pending_human"])
def test_eligible_current_reading_freezes_only_one_expected_snapshot(kind):
    row = make_entry(expected=["ㄎㄢ"] if kind != "unresolved" else [])
    if kind == "pending_pass":
        row = make_entry(expected=["ㄎㄢˋ"])
    if kind == "pending_human":
        row = sp._apply_review_event(row, sp.build_manual_expected_event(
            row, operation="ENTER_EXPECTED", expected_set="ㄎㄢ"))
    if kind.startswith("pending_"):
        row["expected_business_conflict_pending"] = True
    before = copy.deepcopy(row)
    assert gui.can_confirm_current_expected(row)
    event = sp.build_manual_expected_event(row, operation="CONFIRM_CURRENT_AS_EXPECTED")
    assert row == before
    assert event["expected_set"] == [row["actual"]]
    assert "actual" not in event["manual_expected_decision"]["target"]
    replayed = sp._apply_review_event({**row, "actual": "ㄎㄢˊ", "actual_evidence": "new actual"}, event)
    assert replayed["expected_set"] == ["ㄎㄢˋ"]
    assert replayed["manual_expected_decision"] == event["manual_expected_decision"]


@pytest.mark.parametrize("fault", ["source", "blocking", "missing_evidence", "invalid_actual", "unresolved",
                                  "completed", "excluded", "identity", "page_zero", "page_nan", "bbox_nan"])
def test_unavailable_reason_and_builder_both_reject(fault):
    row = make_entry(expected=["ㄎㄢ"])
    updates = {
        "source": {"state": "SOURCE_INVALID"}, "blocking": {"blocking_state": "DATA_INTEGRITY_ERROR"},
        "missing_evidence": {"actual_evidence": " "}, "invalid_actual": {"actual": "bad"},
        "unresolved": {"actual_status": "UNRESOLVED"}, "completed": {"state": "PASS"},
        "excluded": {"state": "EXCLUDED_OUT_OF_SCOPE"}, "identity": {"review_id": "foreign"},
        "page_zero": {"physical_page": 0}, "page_nan": {"physical_page": float("nan")},
        "bbox_nan": {"x0": float("nan")},
    }
    row.update(updates[fault])
    reason = sp.confirm_current_actual_unavailable_reason(row)
    assert reason and not gui.can_confirm_current_expected(row)
    if fault in {"source", "blocking"}:
        assert str(row.get("blocking_state") or row["state"]) in reason
    with pytest.raises(sp.InvalidTransitionError):
        sp.build_manual_expected_event(row, operation="CONFIRM_CURRENT_AS_EXPECTED")


def test_zero_width_location_is_preserved_and_completion_is_not_reopened():
    row = make_entry(expected=["ㄎㄢ"])
    row["x1"] = row["x0"]
    assert gui.can_confirm_current_expected(row)
    event = sp.build_manual_expected_event(row, operation="CONFIRM_CURRENT_AS_EXPECTED")
    assert event["manual_expected_decision"]["target"]["x0"] == event["manual_expected_decision"]["target"]["x1"]
    completed = make_entry(expected=["ㄎㄢˋ"])
    queue = gui.prepare_review_queue({}, [completed], [], 0, set(), set())
    assert queue["records"] == []
    assert not gui.can_confirm_current_expected(completed)


def test_difference_secondary_shortcut_render_failure_preserves_primary():
    app = controls(gui.ReviewApp.__new__(gui.ReviewApp))
    row = make_entry(expected=["ㄎㄢ"])
    app.current = lambda: row
    app.staged_checked_occurrence_ids = set()
    app.configure_actions(row)
    assert app.primary.cget("text") == "確認教材錯誤"
    assert app.secondary.cget("text") == "確認目前注音就是應標注音"
    app._preview_failed()
    assert app.secondary.cget("state") == "disabled"
    assert app.primary.cget("state") == "normal"


@pytest.mark.parametrize("reason", ["source", "staging", "staged_member", "not_rendered"])
def test_handler_explains_rejection_without_saving(monkeypatch, reason):
    app = gui.ReviewApp.__new__(gui.ReviewApp)
    row = make_entry(expected=["ㄎㄢ"])
    app.current = lambda: row
    app.root = object()
    app.staging_summary = {}
    app.staged_checked_occurrence_ids = set()
    app._rendered_review_id = row["review_id"]
    if reason == "source":
        app._resume_source_error = "SOURCE_INVALID"
    elif reason == "staging":
        app.staging_summary["staging_validation_pending"] = True
    elif reason == "staged_member":
        app.staged_checked_occurrence_ids.add(row["occurrence_id"])
    else:
        app._rendered_review_id = None
    messages = []
    monkeypatch.setattr(gui.messagebox, "showwarning", lambda title, message, **kw: messages.append(message))
    # This rejection must occur before any save call; the actual handler is exercised.
    app.save_event = lambda *a, **kw: pytest.fail("rejected action reached save")
    app.confirm_current_expected(row["review_id"])
    assert len(messages) == 1


def test_real_handler_saves_rule_and_human_conflicts_and_retry_cannot_revive(batch, monkeypatch):
    _, target, _, tm, original_db, filled = batch
    sp.import_gpt_decisions(target, filled)
    app = controls(headless_app(target))
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **kw: None)
    monkeypatch.setattr(gui.messagebox, "showwarning", lambda *a, **kw: None)
    receipt = (target / p.CONFLICT_FILE).read_bytes()
    for n in (2, 4):
        rid = tm["records"][n]["review_id"]
        app.index = next(i for i, item in enumerate(app.records) if item["review_id"] == rid)
        row = app.current()
        app._rendered_review_id = rid
        app._review_action_cooldown_until = app._shortcut_cooldown_until = 0
        app.configure_actions(row)
        assert app.primary.cget("text") == "確認目前注音就是應標注音"
        app.primary.invoke()
        wait_for_save(app)
        saved = sp.json_load_strict(target / "人工判定資料庫.json")["events"][rid]
        assert saved["manual_expected_decision"]["operation"] == "CONFIRM_CURRENT_AS_EXPECTED"
        assert saved["expected_set"] == [row["actual"]]
        assert "portability_conflict_resolution" in saved
        if n == 4:
            assert saved["undo_previous_event"] == original_db["events"][rid]
        drifted = sp._apply_review_event({**tm["records"][n], "actual": "ㄐㄩㄝˋ", "actual_evidence": "new actual"}, saved)
        assert drifted["expected_set"] == saved["expected_set"]
    db = sp.json_load_strict(target / "人工判定資料庫.json")
    assert p.validate_conflict_state(target, tm, db) == []
    retried = sp.import_gpt_decisions(target, filled)
    assert retried.status["duplicates"] == 5 and retried.status["pending_conflicts"] == 0
    assert sp.json_load_strict(target / "人工判定資料庫.json") == db
    assert (target / p.CONFLICT_FILE).read_bytes() == receipt
    assert ReviewSaveService(target).load_resume_snapshot().ledger == sp.materialize_ledger(tm, db)


@pytest.mark.parametrize("fault", ["reading", "evidence", "physical_source"])
def test_write_boundary_rejects_changed_selected_snapshot_or_source(tmp_path, monkeypatch, fault):
    monkeypatch.setattr(sp, "USER_MEMORY", tmp_path / "memory.json")
    monkeypatch.setattr(sp, "REUSABLE_EXPECTED_RULES", tmp_path / "rules.json")
    manifest = create_staged_navigation_fixture(tmp_path)
    row = manifest["records"][4]
    event = sp.build_manual_expected_event(row, operation="CONFIRM_CURRENT_AS_EXPECTED")
    service = ReviewSaveService(tmp_path)
    service.load_resume_snapshot()
    snapshot = sp.actual_confirmation_snapshot(row)
    if fault == "reading":
        snapshot["actual"] = "ㄎㄢ"
    elif fault == "evidence":
        snapshot["actual_evidence"] = "old display proof"
    else:
        from pathlib import Path
        Path(row["pdf"]).write_bytes(b"changed PDF bytes")
    before = (tmp_path / "人工判定資料庫.json").read_bytes()
    with pytest.raises(ValueError):
        service.save_event(row["review_id"], event, confirm_current_actual_snapshot=snapshot)
    assert (tmp_path / "人工判定資料庫.json").read_bytes() == before


def test_unapplied_actual_blocks_shortcut_but_enter_expected_remains_independent(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "USER_MEMORY", tmp_path / "memory.json")
    monkeypatch.setattr(sp, "REUSABLE_EXPECTED_RULES", tmp_path / "rules.json")
    manifest = create_staged_navigation_fixture(tmp_path)
    row = manifest["records"][4]
    event = sp.build_manual_expected_event(row, operation="CONFIRM_CURRENT_AS_EXPECTED")
    sp.stage_manual_actual_correction(tmp_path, row["review_id"], "ㄎㄢ")
    service = ReviewSaveService(tmp_path)
    before = (tmp_path / "人工判定資料庫.json").read_bytes()
    with pytest.raises(ValueError, match="尚未套用"):
        service.save_event(row["review_id"], event, confirm_current_actual_snapshot=sp.actual_confirmation_snapshot(row))
    assert (tmp_path / "人工判定資料庫.json").read_bytes() == before
    independent = sp.build_manual_expected_event(row, operation="ENTER_EXPECTED", expected_set="ㄎㄢ")
    saved = service.save_event(row["review_id"], independent)
    assert saved.resolved_entry["expected_set"] == ["ㄎㄢ"]
    assert saved.resolved_entry["actual"] == row["actual"]


@pytest.mark.parametrize("operation", ["clear", "undo"])
def test_real_clear_and_undo_none_event_preserve_actual_and_baseline(tmp_path, monkeypatch, operation):
    monkeypatch.setattr(sp, "USER_MEMORY", tmp_path / "memory.json")
    monkeypatch.setattr(sp, "REUSABLE_EXPECTED_RULES", tmp_path / "rules.json")
    manifest = create_staged_navigation_fixture(tmp_path)
    app = headless_app(tmp_path)
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *a, **kw: True)
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **kw: None)
    monkeypatch.setattr(gui.messagebox, "showwarning", lambda *a, **kw: None)
    original = manifest["records"][4]
    rid = original["review_id"]
    app.index = next(i for i, row in enumerate(app.records) if row["review_id"] == rid)
    row = app.current()
    event = sp.build_manual_expected_event(row, operation="ENTER_EXPECTED", expected_set="ㄎㄢˊ")
    assert app.save_event(row, app._remember_previous_expected_event(row, event))
    wait_for_save(app)
    app.index = next(i for i, item in enumerate(app.records) if item["review_id"] == rid)
    app._review_action_cooldown_until = app._shortcut_cooldown_until = 0
    if operation == "clear":
        app.clear()
    else:
        app.undo_last_expected()
    wait_for_save(app)
    db = sp.json_load_strict(tmp_path / "人工判定資料庫.json")
    assert rid not in db["events"]
    restored = next(row for row in sp.materialize_ledger(manifest, db) if row["review_id"] == rid)
    assert restored["expected_set"] == original["expected_set"]
    assert sp.actual_confirmation_snapshot(restored) == sp.actual_confirmation_snapshot(original)
