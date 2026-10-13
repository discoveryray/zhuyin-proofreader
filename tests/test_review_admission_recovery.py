"""Owned controller admission failures without a Tcl interpreter or live data."""
import threading
import queue
from contextlib import ExitStack
from unittest.mock import patch

import pytest
import review_gui as gui
import standalone_proofread as sp
from review_operation_worker import ReviewOperationError, ReviewOperationState, validate_payload
from tests.no_window_review_support import Surface
from tests.test_manual_actual_gui_batch_v570 import FakeProgressWidget


def admission_app(tmp_path):
    app = gui.ReviewApp.__new__(gui.ReviewApp)
    app.root, app.output_dir = Surface(), tmp_path
    app.manifest, app.db, app.records, app.index = {}, {"events": {}}, [], 0
    app._project_generation = 0
    app._save_recovery_required = False
    app.status, app.primary = Surface(app.root), Surface(app.root)
    return app


def test_thread_constructor_failure_allows_retry_and_close(tmp_path):
    app = admission_app(tmp_path)
    marker, published = tmp_path / "operation.txt", []
    def work(progress):
        marker.write_text("owned result", encoding="utf-8")
        return marker.read_text(encoding="utf-8")
    with patch.object(gui.threading, "Thread", side_effect=RuntimeError("constructor injected")) as fault, \
            patch.object(gui.messagebox, "showerror") as errors:
        assert not app._start_owned_operation("測試操作", work, published.append)
    fault.assert_called_once()
    errors.assert_called_once()
    assert not marker.exists() and published == []
    assert app._operation_request is None and not app._operation_in_progress
    assert app.primary.cget("state") == "normal" and not app._save_busy()
    assert app._start_owned_operation("重試", work, published.append)
    app._operation_worker.join(5)
    assert not app._operation_worker.is_alive()
    app.root.update()
    assert published == ["owned result"] and not app._operation_in_progress
    app.close()
    assert not app.root.winfo_exists()


def test_thread_start_failure_retires_late_bootstrap(tmp_path):
    app = admission_app(tmp_path)
    marker, threads = tmp_path / "operation.txt", []
    real_start = threading.Thread.start
    def fail_start(thread):
        threads.append(thread)
        raise RuntimeError("start injected before bootstrap")
    with patch.object(gui.threading.Thread, "start", fail_start), patch.object(gui.messagebox, "showerror") as errors:
        assert not app._start_owned_operation("測試操作", lambda p: marker.write_text("unsafe"), lambda v: None)
    assert len(threads) == 1
    errors.assert_called_once()
    assert not app._save_busy() and app.primary.cget("state") == "normal"
    # A late native bootstrap must see retirement before it can enter work.
    real_start(threads[0])
    threads[0].join(5)
    assert not threads[0].is_alive() and not marker.exists()
    app.close()
    assert not app.root.winfo_exists()


def test_started_writer_start_error_drains_original_owner(tmp_path):
    app = admission_app(tmp_path)
    marker, published = tmp_path / "operation.txt", []
    entered, release = threading.Event(), threading.Event()
    real_start = threading.Thread.start
    def work(progress):
        entered.set()
        assert release.wait(5)
        marker.write_text("finished", encoding="utf-8")
        return "finished"
    def start_then_fail(thread):
        real_start(thread)
        assert entered.wait(5)
        raise RuntimeError("start return injected after writer entered")
    with patch.object(gui.threading.Thread, "start", start_then_fail) as fault, \
            patch.object(gui.messagebox, "showwarning") as warning:
        try:
            assert app._start_owned_operation("測試操作", work, published.append)
            assert entered.is_set() and app._operation_request is not None
            assert app._operation_in_progress and app.primary.cget("state") == "disabled"
            app.close()
            assert app.root.winfo_exists() and not marker.exists()
        finally:
            release.set()
            app._operation_worker.join(5)
    warning.assert_called_once()
    assert not app._operation_worker.is_alive()
    app.root.update()
    assert marker.read_text(encoding="utf-8") == "finished" and published == ["finished"]
    assert not app._operation_in_progress and app.primary.cget("state") == "normal"
    app.close()
    assert not app.root.winfo_exists()


def test_failed_admission_does_not_release_newer_owner(tmp_path):
    app = admission_app(tmp_path)
    newer, injected = {"newer": True}, []
    def changed_owner(thread):
        injected.append(True)
        app._operation_request = newer
        raise RuntimeError("start injected after owner changed")
    with patch.object(gui.threading.Thread, "start", changed_owner), patch.object(gui.messagebox, "showerror") as errors:
        assert not app._start_owned_operation("舊操作", lambda p: pytest.fail("writer entered"), lambda v: pytest.fail("published"))
    assert injected == [True]
    assert app._operation_request is newer and app._operation_in_progress
    assert app.primary.cget("state") == "disabled"
    errors.assert_not_called()


def test_staged_apply_admission_failure_closes_progress_without_writing(tmp_path):
    for boundary in ("constructor", "start"):
        app = admission_app(tmp_path)
        app._apply_in_progress = True
        class UnenteredService:
            def invoke(self, *args, **kwargs):
                pytest.fail("actual service entered before successful admission")
        app._save_service = UnenteredService()
        with ExitStack() as stack:
            for target, name in ((gui.tk, "Toplevel"), (gui.ttk, "Progressbar"), (gui, "WrappedLabel")):
                stack.enter_context(patch.object(target, name, FakeProgressWidget))
            stack.enter_context(patch.object(gui, "apply_screen_safe_geometry"))
            errors = stack.enter_context(patch.object(gui.messagebox, "showerror"))
            destroyed = stack.enter_context(patch.object(FakeProgressWidget, "destroy", autospec=True,
                side_effect=FakeProgressWidget.destroy))
            if boundary == "constructor":
                fault = stack.enter_context(patch.object(gui.threading, "Thread", side_effect=RuntimeError("constructor")))
            else:
                fault = stack.enter_context(patch.object(gui.threading.Thread, "start", side_effect=RuntimeError("start")))
            app._start_staged_actual_apply(1)
            fault.assert_called_once()
            errors.assert_called_once()
        assert not app._apply_in_progress and not app._operation_in_progress
        assert app._operation_request is None and app.primary.cget("state") == "normal"
        destroyed.assert_called_once_with(app._actual_progress_window)
        app.close()
        assert not app.root.winfo_exists()


def test_staged_apply_started_supervisor_keeps_owner_until_terminal(tmp_path):
    app = admission_app(tmp_path)
    app._apply_in_progress = True
    entered, release = threading.Event(), threading.Event()
    real_start = threading.Thread.start
    class FaultingService:
        # Admission/close unit boundary only: no transaction success is faked.
        def invoke(self, operation, payload, **kwargs):
            assert operation == "apply_actual"
            entered.set()
            assert release.wait(5)
            raise ReviewOperationError("injected terminal before write", commit_state="NOT_WRITTEN")
    app._save_service = FaultingService()
    def start_then_fail(thread):
        real_start(thread)
        assert entered.wait(5)
        raise RuntimeError("start return injected after supervisor entered")
    with ExitStack() as stack:
        for target, name in ((gui.tk, "Toplevel"), (gui.ttk, "Progressbar"), (gui, "WrappedLabel")):
            stack.enter_context(patch.object(target, name, FakeProgressWidget))
        stack.enter_context(patch.object(gui, "apply_screen_safe_geometry"))
        stack.enter_context(patch.object(gui.threading.Thread, "start", start_then_fail))
        warning = stack.enter_context(patch.object(gui.messagebox, "showwarning"))
        errors = stack.enter_context(patch.object(gui.messagebox, "showerror"))
        success = stack.enter_context(patch.object(gui.messagebox, "showinfo"))
        destroyed = stack.enter_context(patch.object(FakeProgressWidget, "destroy", autospec=True,
            side_effect=FakeProgressWidget.destroy))
        try:
            app._start_staged_actual_apply(1)
            assert entered.is_set() and app._apply_in_progress and app._operation_in_progress
            app.close()
            assert app.root.winfo_exists()
            destroyed.assert_not_called()
        finally:
            release.set()
            app._operation_worker.join(5)
        assert not app._operation_worker.is_alive()
        app.root.update()
        warning.assert_called_once()
        errors.assert_called_once()
        success.assert_not_called()
        destroyed.assert_called_once_with(app._actual_progress_window)
    assert not app._operation_in_progress and not app._apply_in_progress
    app.close()
    assert not app.root.winfo_exists()


def test_recovery_apply_requires_confirmed_identity(tmp_path):
    state = ReviewOperationState(tmp_path)
    for payload in ({"recovery_only": True}, {"recovery_only": True, "expected_recovery": None},
                    {"recovery_only": True, "expected_recovery": ["f" * 64, {}]},
                    {"recovery_only": True, "expected_recovery": ["wrong-token", {
                        "schema_version": "1.0", "affected_occurrence_ids": [], "checked_postconditions": []}]}):
        with patch.object(sp, "recover_committed_actual_project") as recover, \
                pytest.raises(ValueError, match="confirmed durable identity"):
            state.dispatch("apply_actual", payload, lambda phase: None, lambda: False)
        recover.assert_not_called()
    with pytest.raises(ValueError, match="only valid"):
        validate_payload("apply_actual", {"recovery_only": False, "expected_recovery": ["x", {}]})


def test_old_actual_terminal_cleans_only_old_progress_and_not_new_owner(tmp_path):
    app = admission_app(tmp_path)
    app._apply_in_progress = True
    entered, release = threading.Event(), threading.Event()
    class FaultingService:
        def invoke(self, operation, payload, **kwargs):
            entered.set()
            assert release.wait(5)
            raise ReviewOperationError("old operation terminal", commit_state="NOT_WRITTEN")
    app._save_service = FaultingService()
    with ExitStack() as stack:
        for target, name in ((gui.tk, "Toplevel"), (gui.ttk, "Progressbar"), (gui, "WrappedLabel")):
            stack.enter_context(patch.object(target, name, FakeProgressWidget))
        stack.enter_context(patch.object(gui, "apply_screen_safe_geometry"))
        errors = stack.enter_context(patch.object(gui.messagebox, "showerror"))
        success = stack.enter_context(patch.object(gui.messagebox, "showinfo"))
        destroyed = stack.enter_context(patch.object(FakeProgressWidget, "destroy", autospec=True,
            side_effect=FakeProgressWidget.destroy))
        try:
            app._start_staged_actual_apply(1)
            assert entered.wait(5)
            old_progress, old_worker = app._actual_progress_window, app._operation_worker
            newer, new_task = {"project": "new owner"}, object()
            app.output_dir = tmp_path / "new-project"
            app._project_generation += 1
            app._operation_request, app._actual_apply_task = newer, new_task
            app.status.config(text="new project operation")
            release.set()
            old_worker.join(5)
            assert not old_worker.is_alive()
            app.root.update()
            destroyed.assert_called_once_with(old_progress)
            assert app._operation_request is newer and app._actual_apply_task is new_task
            assert app._operation_in_progress and app._apply_in_progress
            assert app.status.cget("text") == "new project operation"
            assert app.primary.cget("state") == "disabled"
            assert not app._save_recovery_required
            errors.assert_not_called()
            success.assert_not_called()
        finally:
            release.set()
            app._operation_worker.join(5)


def test_disposed_operation_discards_result_and_runs_only_old_cleanup(tmp_path):
    app = admission_app(tmp_path)
    entered, release = threading.Event(), threading.Event()
    cleaned, published = [], []
    def work(progress):
        entered.set()
        assert release.wait(5)
        return "old result"
    try:
        assert app._start_owned_operation("舊操作", work, published.append,
            on_terminal=lambda: cleaned.append("old progress"))
        assert entered.wait(5)
        request = app._operation_request
        app._async_disposed = True
        newer = {"new owner": True}
        app._operation_request = newer
        app.status.config(text="disposed owner")
        release.set()
        app._operation_worker.join(5)
        gui.ReviewApp._poll_owned_operation(app, request, queue.Queue(),
            queue.Queue(), published.append, False)
        assert cleaned == ["old progress"] and published == []
        assert app._operation_request is newer and app._operation_in_progress
        assert app.primary.cget("state") == "disabled"
        assert app.status.cget("text") == "disposed owner"
    finally:
        release.set()
        app._operation_worker.join(5)
