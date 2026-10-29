"""Learning UI command/data checks only; no Tcl/Tk initialization."""
import base64
import hashlib
import threading
import time
from types import MethodType
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import review_gui as gui
from review_operation_worker import ReviewOperationState, ReviewOperationError, validate_payload


class Value:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


def dialog_data():
    entry = dict(occurrence_id="o1", review_id="r1", pdf_sha256="a" * 64, actual="ㄅ")
    peer = dict(occurrence_id="o2", review_id="r2", pdf_sha256="b" * 64, actual="ㄅ")
    return SimpleNamespace(entry=entry, samples=[entry, peer], reading=Value("ㄅ"),
        checked_vars=[Value(True), Value(False)], sample_available=[True, True],
        _viewed_target=[True, False], previously_checked=[False, False],
        _learning_images={0: dict(png=b"viewed first image", padding=(95, 60), display_width=400),
                          1: dict(png=b"unseen peer image", padding=(95, 60), display_width=400)})


def test_only_viewed_checked_samples_enter_confirmation():
    dialog = dialog_data()
    inputs = gui.learning_confirmation_inputs(dialog)
    assert inputs["checked_occurrence_ids"] == ["o1"]
    assert inputs["images"] == [dict(occurrence_id="o1", review_id="r1", source_sha256="a" * 64,
        png_base64=base64.b64encode(b"viewed first image").decode("ascii"), padding=[95, 60], display_width=400)]
    dialog.checked_vars[1].set(True)
    with pytest.raises(ValueError, match="目視"):
        gui.learning_confirmation_inputs(dialog)


def test_image_failure_or_unchecked_target_prevents_confirmation():
    dialog = dialog_data()
    del dialog._learning_images[0]
    with pytest.raises(ValueError, match="圖片已失效"):
        gui.learning_confirmation_inputs(dialog)
    dialog = dialog_data()
    dialog.sample_available[0] = False
    with pytest.raises(ValueError, match="目視"):
        gui.learning_confirmation_inputs(dialog)
    dialog = dialog_data()
    dialog.checked_vars[0].set(False)
    with pytest.raises(ValueError, match="目前位置"):
        gui.learning_confirmation_inputs(dialog)


def test_reading_or_image_change_invalidates_the_fixed_plan(monkeypatch):
    monkeypatch.setattr(gui.messagebox, "showerror", Mock())
    for mutation in (lambda dialog: dialog.reading.set("ㄆ"),
                     lambda dialog: dialog._learning_images[0].update(png=b"replacement image")):
        dialog = dialog_data()
        original = gui.learning_inputs_key(gui.learning_confirmation_inputs(dialog))
        dialog._learning_prepared = (original, "server-token")
        dialog._learning_busy = False
        dialog._learning_changed = Mock()
        dialog._learning_job = Mock()
        mutation(dialog)
        gui.LearningReadingDialog.submit_learning(dialog, correct=True)
        dialog._learning_changed.assert_called_once()
        dialog._learning_job.assert_not_called()


def test_final_click_sends_only_fixed_worker_token_and_busy_click_is_ignored(monkeypatch):
    monkeypatch.setattr(gui.messagebox, "showerror", Mock())
    dialog = dialog_data()
    key = gui.learning_inputs_key(gui.learning_confirmation_inputs(dialog))
    dialog._learning_prepared = (key, "server-token")
    dialog._learning_busy = False
    dialog.learning_status = Mock()
    dialog._learning_job = Mock()
    gui.LearningReadingDialog.submit_learning(dialog, correct=True)
    assert dialog._learning_job.call_args.args[:2] == ("confirm_learning", {"plan_token": "server-token"})
    dialog._learning_busy = True
    gui.LearningReadingDialog.submit_learning(dialog, correct=True)
    assert dialog._learning_job.call_count == 1


def test_worker_rejects_unissued_token_and_arbitrary_plan(tmp_path):
    with pytest.raises(ValueError, match="payload fields"):
        validate_payload("confirm_learning", {"plan_token": "x", "plan": {"reading": "ㄅ"}})
    state = ReviewOperationState(tmp_path, service=SimpleNamespace())
    with pytest.raises(ReviewOperationError, match="計畫已失效") as error:
        state.dispatch("confirm_learning", {"plan_token": "unissued"}, lambda text: None, lambda: False)
    assert error.value.commit_state == "NOT_WRITTEN"
    assert not list(tmp_path.iterdir())


def test_learning_defer_does_not_clear_textbook_pending():
    textbook = [{"occurrence_id": "o1", "state": "ACTUAL_UNRESOLVED"}]
    deferred = set()
    tree = SimpleNamespace(selection=lambda: ("g1",), delete=Mock())
    dialog = SimpleNamespace(items=tree, targets={"g1": {"group": {"group_id": "exact-1"}}},
                             deferred=deferred)
    gui.LearningItemsDialog.later(dialog)
    assert deferred == {"exact-1"}
    assert textbook == [{"occurrence_id": "o1", "state": "ACTUAL_UNRESOLVED"}]
    tree.delete.assert_called_once_with("g1")


def test_save_failure_stays_on_current_snapshot(monkeypatch):
    monkeypatch.setattr(gui.messagebox, "showerror", Mock())
    app = SimpleNamespace(root=object(), manifest={"version": "displayed"},
                          db={"events": {}}, index=4, records=[{"review_id": "current"}])
    gui.ReviewApp._publish_learning_save(app, {"result": {"local_status": "NOT_SAVED", "error": "locked"}})
    assert app.index == 4
    assert app.manifest == {"version": "displayed"}
    assert app.records == [{"review_id": "current"}]
    gui.messagebox.showerror.assert_called_once()


def test_local_saved_global_pending_is_reported_separately(monkeypatch):
    monkeypatch.setattr(gui.messagebox, "showinfo", Mock())
    app = SimpleNamespace(root=object(), status=Mock(), _publish_staging_summary=Mock(),
                          _set_actionable_records_from_ledger=Mock(), show=Mock())
    value = dict(result=dict(local_status="SAVED", status="GLOBAL_PENDING", global_status="APPROVAL_PENDING_RETRY"),
                 snapshot=SimpleNamespace(manifest={"v": 2}, db={"events": {}}), summary={}, review_view=[])
    gui.ReviewApp._publish_learning_save(app, value)
    text = gui.messagebox.showinfo.call_args.args[1]
    assert "本地已保存" in text and "尚未完成" in text
    assert "已可供其他教材重用" not in text
    assert app.manifest == {"v": 2}


def test_all_pass_project_can_open_learning_without_current_review(monkeypatch):
    seen = []
    service = SimpleNamespace(invoke=lambda operation, payload, **kw: seen.append((operation, payload)))
    app = SimpleNamespace(_save_service=service, manifest={"records": [{"state": "PASS"}]}, db={},
                          _save_busy=lambda: False, _review_action_in_progress=False,
                          _start_owned_operation=lambda title, work, publish: work(lambda text: None))
    gui.ReviewApp.open_learning.__wrapped__(app) if hasattr(gui.ReviewApp.open_learning, "__wrapped__") else gui.ReviewApp.open_learning(app)
    assert seen == [("load_learning", {"expected_manifest": app.manifest, "expected_db": {}})]


def learning_worker_fixture(tmp_path, monkeypatch):
    from tests.test_glyph_learning_service import _project
    from review_save_service import ReviewSaveService
    root = short_root(tmp_path)
    monkeypatch.setenv("LOCALAPPDATA", str(root / "u"))
    output = _project(root, monkeypatch)
    snapshot = ReviewSaveService(output).load_resume_snapshot()
    state = ReviewOperationState(output)
    displayed = dict(expected_manifest=snapshot.manifest, expected_db=snapshot.db)
    value = state.dispatch("load_learning", displayed, lambda text: None, lambda: False)
    group = value["candidates"]["groups"][0]
    row = group["candidate_members"][0]
    return output, snapshot, state, displayed, group, row


def prepared_worker(tmp_path, monkeypatch):
    from review_operation_worker import preview_png
    output, snapshot, state, displayed, group, row = learning_worker_fixture(tmp_path, monkeypatch)
    pixels = preview_png(row, padding=(95, 60), output_dir=output, display_width=400)
    assert pixels["target"] is not None
    images = [dict(occurrence_id=row["occurrence_id"], review_id=row["review_id"],
                   source_sha256=row["pdf_sha256"], png_base64=base64.b64encode(pixels["png"]).decode("ascii"),
                   padding=[95, 60], display_width=400)]
    plan = state.dispatch("prepare_learning", dict(displayed, group_id=group["group_id"], reading="ㄅ",
        checked_occurrence_ids=[row["occurrence_id"]], images=images), lambda text: None, lambda: False)
    return output, snapshot, state, plan


def short_root(tmp_path):
    root = tmp_path.parent / ("g" + hashlib.sha256(tmp_path.name.encode()).hexdigest()[:6])
    root.mkdir()
    return root


def test_real_worker_prepare_confirm_and_reopened_retry_count_once(tmp_path, monkeypatch):
    import global_exact_glyph_library as library
    from review_save_service import ReviewSaveService
    output, before, state, prepared = prepared_worker(tmp_path, monkeypatch)
    result = state.dispatch("confirm_learning", prepared_token(prepared), lambda text: None, lambda: False)
    assert result["result"]["local_status"] == "SAVED"
    assert result["result"]["status"] == "LOCAL_SAVED_WAITING_SOURCE"
    inspection = library.GlobalExactGlyphRepository.resolved().load_inspection_snapshot()
    assert sum(len(record.source_evidence) for record in inspection.records) == 1
    reopened = ReviewOperationState(output)
    replay = reopened.dispatch("retry_learning", {"operation_id": result["result"]["operation_id"]}, lambda text: None, lambda: False)
    assert replay["result"]["local_status"] == "SAVED"
    after = library.GlobalExactGlyphRepository.resolved().load_inspection_snapshot()
    assert sum(len(record.source_evidence) for record in after.records) == 1
    assert ReviewSaveService(output).load_resume_snapshot().db == before.db


def prepared_token(prepared):
    return {"plan_token": prepared["plan_token"]}


def test_real_worker_changed_project_cannot_commit_prepared_plan(tmp_path, monkeypatch):
    import standalone_proofread as sp
    import global_exact_glyph_library as library
    output, snapshot, state, prepared = prepared_worker(tmp_path, monkeypatch)
    changed = dict(snapshot.db, events=dict(snapshot.db.get("events", {})))
    row = snapshot.ledger[0]
    changed["events"][row["review_id"]] = sp.build_manual_expected_event(row,
        operation="ENTER_EXPECTED", expected_set="ㄆ")
    sp.json_save(output / "人工判定資料庫.json", changed)
    with pytest.raises(ValueError, match="changed|變動|變更"):
        state.dispatch("confirm_learning", prepared_token(prepared), lambda text: None, lambda: False)
    assert not library.GlobalExactGlyphRepository.resolved().path.exists()
    assert sp.json_load_strict(output / "人工判定資料庫.json") == changed


def test_surface_learning_applies_only_current_position_and_never_global(tmp_path, monkeypatch):
    import standalone_proofread as sp
    import manual_surface_actual as surface
    import global_exact_glyph_library as library
    from review_save_service import ReviewSaveService
    from tests.test_glyph_learning_service import _project
    root = short_root(tmp_path)
    monkeypatch.setenv("LOCALAPPDATA", str(root / "u"))
    output = _project(root, monkeypatch)
    snapshot = ReviewSaveService(output).load_resume_snapshot()
    row = snapshot.ledger[0]
    proof = sp.prepare_surface_actual_correction(output, row["review_id"])["proof"]
    request = dict(review_id=row["review_id"], reading="ㄆ", proof=proof,
        actor="isolated worker fixture; no human interface acceptance", whole_unit_readable=True,
        target_character_matches=True, reading_from_original=True, enable_format=True,
        expected_manifest=snapshot.manifest, expected_db=snapshot.db)
    state = ReviewOperationState(output)
    saved = state.dispatch("save_learning_surface", request, lambda text: None, lambda: False)
    assert saved["result"]["global_status"] == "LOCAL_ONLY"
    updated = ReviewSaveService(output).load_resume_snapshot()
    assert updated.ledger[0]["actual"] == "ㄆ"
    assert updated.ledger[1]["actual"] == "ㄅ"
    assert updated.db == snapshot.db
    document = surface.load(sp.project_actual_evidence_root(output), updated.manifest)
    assert len(document["records"]) == 1 and document["pending"] == []
    assert not library.GlobalExactGlyphRepository.resolved().path.exists()


def test_started_learning_worker_keeps_owner_after_thread_start_exception(monkeypatch):
    from tests.review_save_test_support import QueuedRoot
    root = QueuedRoot()
    entered, release = threading.Event(), threading.Event()
    invocations, published = [], []
    original_thread = threading.Thread
    workers = []
    class StartedThenRaises(original_thread):
        def start(self):
            super().start()
            workers.append(self)
            assert entered.wait(2)
            raise RuntimeError("isolated start report failure after writer entry")
    def invoke(operation, payload):
        invocations.append((operation, payload))
        entered.set()
        assert release.wait(3)
        return {"saved": True}
    dialog = SimpleNamespace(_learning_busy=False, _learning_service=SimpleNamespace(invoke=invoke),
        prepare_button=Mock(), defer_button=Mock(), remember_button=Mock(), correct_button=Mock(),
        learning_status=Mock(), after=root.after)
    dialog._learning_controls = MethodType(gui.LearningReadingDialog._learning_controls, dialog)
    monkeypatch.setattr(gui.threading, "Thread", StartedThenRaises)
    monkeypatch.setattr(gui.messagebox, "showerror", Mock())
    gui.LearningReadingDialog._learning_job(dialog, "confirm_learning", {"plan_token": "fixed"}, published.append)
    try:
        assert dialog._learning_busy
        gui.LearningReadingDialog._learning_job(dialog, "confirm_learning", {"plan_token": "fixed"}, published.append)
        assert len(invocations) == 1
    finally:
        release.set()
        for worker in workers:
            worker.join(3)
    deadline = time.monotonic() + 3
    while dialog._learning_busy and time.monotonic() < deadline:
        root.update()
        time.sleep(.001)
    assert not dialog._learning_busy and published == [{"saved": True}]


def test_main_close_waits_for_learning_writer():
    app = SimpleNamespace(_learning_dialog=SimpleNamespace(_learning_busy=True), root=Mock(), status=Mock())
    gui.ReviewApp.close(app)
    app.root.destroy.assert_not_called()
    assert "完成後即可關閉" in app.status.config.call_args.kwargs["text"]


def test_worker_rejects_wrong_png_even_with_matching_source_labels(tmp_path, monkeypatch):
    import fitz
    from glyph_learning_images import LearningImageStore
    import global_exact_glyph_library as library
    output, snapshot, state, displayed, group, row = learning_worker_fixture(tmp_path, monkeypatch)
    blank = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 10, 10), False)
    blank.clear_with(255)
    image = dict(occurrence_id=row["occurrence_id"], review_id=row["review_id"], source_sha256=row["pdf_sha256"],
                 png_base64=base64.b64encode(blank.tobytes("png")).decode("ascii"), padding=[95, 60], display_width=400)
    payload = dict(displayed, group_id=group["group_id"], reading="ㄅ",
                   checked_occurrence_ids=[row["occurrence_id"]], images=[image])
    state.learning_plans["retired-previous-preview"] = {"retired": True}
    with pytest.raises(ValueError, match="圖片像素"):
        state.dispatch("prepare_learning", payload, lambda text: None, lambda: False)
    assert state.learning_plans == {}
    assert not (LearningImageStore().root / "evidence").exists()
    assert not library.GlobalExactGlyphRepository.resolved().path.exists()


def test_worker_rejects_another_occurrence_png_under_target_labels(tmp_path, monkeypatch):
    from glyph_learning_images import LearningImageStore
    from review_operation_worker import preview_png
    output, snapshot, state, displayed, group, row = learning_worker_fixture(tmp_path, monkeypatch)
    other = next(item for item in snapshot.ledger if item["occurrence_id"] != row["occurrence_id"])
    expected_image = preview_png(row, padding=(95, 60), output_dir=output, display_width=400)
    other_image = preview_png(other, padding=(95, 60), output_dir=output, display_width=400)
    assert expected_image["png"] != other_image["png"]
    image = dict(occurrence_id=row["occurrence_id"], review_id=row["review_id"], source_sha256=row["pdf_sha256"],
                 png_base64=base64.b64encode(other_image["png"]).decode("ascii"), padding=[95, 60], display_width=400)
    with pytest.raises(ValueError, match="圖片像素"):
        state.dispatch("prepare_learning", dict(displayed, group_id=group["group_id"], reading="ㄅ",
            checked_occurrence_ids=[row["occurrence_id"]], images=[image]), lambda text: None, lambda: False)
    assert state.learning_plans == {}
    assert not (LearningImageStore().root / "evidence").exists()


def test_actual_pending_uses_same_learning_confirmation_entry(monkeypatch):
    row = dict(review_id="pending-review", occurrence_id="pending-occurrence", state="ACTUAL_UNRESOLVED")
    group = dict(group_id="exact-group", members=[row], candidate_members=[row])
    response = dict(group=group, learning_group=group, ledger=[row], summary={}, surface=None)
    service = SimpleNamespace(invoke=Mock(return_value=response))
    app = SimpleNamespace(_save_service=service, manifest={"session_id": "bound-session"}, db={"events": {}},
        current=lambda: row, _save_busy=lambda: False, _publish_staging_summary=Mock(),
        _set_actionable_records_from_ledger=Mock(), show=Mock(), _show_learning_target=Mock(),
        _start_owned_operation=lambda title, work, publish: publish(work(lambda text: None)))
    legacy = Mock(side_effect=AssertionError("duplicate legacy confirmation must not open"))
    monkeypatch.setattr(gui, "ActualReadingDialog", legacy)
    gui.ReviewApp.correct_actual(app)
    service.invoke.assert_called_once()
    assert service.invoke.call_args.args == ("prepare_actual", dict(expected_manifest=app.manifest,
        expected_db=app.db, review_id=row["review_id"]))
    app._show_learning_target.assert_called_once_with(dict(entry=row, group=group, surface=None))
    legacy.assert_not_called()


def test_recovery_entry_survives_reload_failure_and_blocks_busy_actions(monkeypatch):
    monkeypatch.setattr(gui.messagebox, "showinfo", Mock())
    pending = dict(operation_id="fixed-operation", status="REFRESH_PENDING", local_status="SAVED")
    refreshed = dict(result=dict(pending, status="LOCAL_SAVED_WAITING_SOURCE"),
                     snapshot=SimpleNamespace(manifest={"fresh": True}, db={}), summary={}, review_view=[])
    for restarted in (False, True):
        app = gui.ReviewApp.__new__(gui.ReviewApp)
        app.root, app.status, app.learning_recovery_button = object(), Mock(), Mock()
        app.manifest, app.db = {}, {}
        app._startup_failed = restarted
        app._start_review_loading = Mock()
        app.current = lambda: None
        app._start_owned_operation = lambda title, work, publish: publish(work(lambda text: None))
        app._save_service = SimpleNamespace(invoke=Mock(side_effect=[ [pending], refreshed ]))
        selected = Mock(return_value=SimpleNamespace(result={"operation_id": "fixed-operation"}))
        monkeypatch.setattr(gui, "LearningItemsDialog", selected)
        gui.ReviewApp._publish_learning_save(app, dict(result=pending, reload_error="committed refresh interrupted"))
        assert app._save_recovery_required and app.learning_recovery_button.config.call_args.kwargs["state"] == "normal"
        assert app._save_busy()
        for flag in ("_save_in_progress", "_operation_in_progress", "_startup_in_progress", "_apply_in_progress",
                     "_review_action_in_progress", "_async_disposed"):
            setattr(app, flag, True)
            app.open_learning_recovery()
            app._save_service.invoke.assert_not_called()
            setattr(app, flag, False)
        app.open_learning_recovery()
        assert [call.args for call in app._save_service.invoke.call_args_list] == [
            ("list_learning_recovery", {}), ("retry_learning", {"operation_id": "fixed-operation"})]
        assert selected.call_args.args[1:3] == ([], [pending])
        app._start_review_loading.assert_called_once()
        assert app.manifest == {}  # Full startup, never a partial old-view repair.


def test_real_worker_refresh_pending_recovery_lists_without_resume_and_retries_once(tmp_path, monkeypatch):
    import global_exact_glyph_library as library
    import standalone_proofread as sp
    from review_save_service import ReviewSaveService
    output, before, state, prepared = prepared_worker(tmp_path, monkeypatch)
    with monkeypatch.context() as failure:
        failure.setattr(sp, "_finish_direct_actual_commit", Mock(side_effect=OSError("isolated refresh interruption")))
        saved = state.dispatch("confirm_learning", prepared_token(prepared), lambda text: None, lambda: False)
    result = saved["result"]
    assert result["local_status"] == "SAVED" and result["status"] == "REFRESH_PENDING"
    assert saved.get("reload_error")
    reopened = ReviewOperationState(output)
    with monkeypatch.context() as guard:
        guard.setattr(reopened.service, "load_resume_snapshot", Mock(side_effect=AssertionError("recovery list cannot accept a view")))
        history = reopened.dispatch("list_learning_recovery", {}, lambda text: None, lambda: False)
    assert any(row["operation_id"] == result["operation_id"] and row["status"] == "REFRESH_PENDING" for row in history)
    recovered = reopened.dispatch("retry_learning", {"operation_id": result["operation_id"]}, lambda text: None, lambda: False)
    assert recovered["result"]["status"] == "LOCAL_SAVED_WAITING_SOURCE"
    assert "snapshot" in recovered and "reload_error" not in recovered
    inspection = library.GlobalExactGlyphRepository.resolved().load_inspection_snapshot()
    assert sum(len(record.source_evidence) for record in inspection.records) == 1
    assert ReviewSaveService(output).load_resume_snapshot().db == before.db


def surface_request(output):
    import standalone_proofread as sp
    from review_save_service import ReviewSaveService
    snapshot = ReviewSaveService(output).load_resume_snapshot()
    row = snapshot.ledger[0]
    prepared = sp.prepare_surface_actual_correction(output, row["review_id"])
    return snapshot, dict(review_id=row["review_id"], reading="ㄆ", proof=prepared["proof"],
        actor="isolated recovery fixture; no human interface acceptance", whole_unit_readable=True,
        target_character_matches=True, reading_from_original=True, enable_format=True,
        expected_manifest=snapshot.manifest, expected_db=snapshot.db)


def test_surface_learning_staged_failure_reopens_existing_apply_queue(tmp_path, monkeypatch):
    import standalone_proofread as sp
    import global_exact_glyph_library as library
    from review_save_service import ReviewSaveService
    from tests.test_glyph_learning_service import _project
    root = short_root(tmp_path)
    monkeypatch.setenv("LOCALAPPDATA", str(root / "u"))
    output = _project(root, monkeypatch)
    before, request = surface_request(output)
    state = ReviewOperationState(output)
    with monkeypatch.context() as failure:
        failure.setattr(sp, "apply_staged_surface_actual_corrections", Mock(side_effect=OSError("isolated pre-apply interruption")))
        with pytest.raises(ReviewOperationError) as error:
            state.dispatch("save_learning_surface", request, lambda text: None, lambda: False)
    assert error.value.commit_state == "STAGED_REFRESH_PENDING"
    service = ReviewSaveService(output)
    snapshot = service.load_resume_snapshot()
    summary = service.validate_resume_staging(snapshot)
    assert summary["surface_pending_count"] == 1
    reopened = ReviewOperationState(output)
    value = reopened.dispatch("apply_actual", dict(expected_manifest=snapshot.manifest,
        expected_db=snapshot.db), lambda text: None, lambda: False)
    assert value["snapshot"].ledger[0]["actual"] == "ㄆ"
    assert value["snapshot"].ledger[1]["actual"] == "ㄅ"
    assert value["snapshot"].db == before.db
    assert not library.GlobalExactGlyphRepository.resolved().path.exists()


def test_surface_learning_committed_failure_keeps_existing_fixed_recovery(tmp_path, monkeypatch):
    import standalone_proofread as sp
    import global_exact_glyph_library as library
    from tests.test_glyph_learning_service import _project
    root = short_root(tmp_path)
    monkeypatch.setenv("LOCALAPPDATA", str(root / "u"))
    output = _project(root, monkeypatch)
    before, request = surface_request(output)
    state = ReviewOperationState(output)
    original_refresh = sp._surface_projection_refresh
    def interrupt_committed_refresh(*args, **kwargs):
        if kwargs.get("enable") is True:
            return original_refresh(*args, **kwargs)
        raise OSError("isolated committed refresh interruption")
    with monkeypatch.context() as failure:
        failure.setattr(sp, "_surface_projection_refresh", interrupt_committed_refresh)
        with pytest.raises(ReviewOperationError) as error:
            state.dispatch("save_learning_surface", request, lambda text: None, lambda: False)
    assert error.value.commit_state == "COMMITTED_REFRESH_PENDING"
    reopened = ReviewOperationState(output)
    summary = reopened.dispatch("actual_summary", {"recovery_only": True}, lambda text: None, lambda: False)
    assert summary["actual_recovery_pending"]
    value = reopened.dispatch("apply_actual", dict(recovery_only=True,
        expected_recovery=summary["actual_recovery_identity"]), lambda text: None, lambda: False)
    assert value["snapshot"].ledger[0]["actual"] == "ㄆ"
    assert value["snapshot"].ledger[1]["actual"] == "ㄅ"
    assert value["snapshot"].db == before.db
    assert not library.GlobalExactGlyphRepository.resolved().path.exists()
