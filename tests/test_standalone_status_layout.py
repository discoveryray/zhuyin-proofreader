"""Launcher refresh and natural tab sizing, without a Tcl interpreter.

Only transport arrival and finite widget geometry are substituted. The status
controller, filesystem metadata checks, JSON parser and layout handlers run.
These checks are not visual GUI acceptance.
"""
from types import SimpleNamespace
from unittest.mock import Mock

import standalone_gui as launcher
import standalone_proofread as sp
from tests.no_window_review_support import Surface
from tests.test_review_process_operations import _status_controller


def finish_status(app, jobs):
    assert len(jobs) == 1
    jobs.pop().run()
    app.poll()


def test_unchanged_timer_keeps_summary_and_does_not_read_or_reset_label(tmp_path, monkeypatch):
    app, jobs = _status_controller(tmp_path, monkeypatch)
    app.refresh_project_status()
    finish_status(app, jobs)
    summary = app.project_status.cget("text")
    app.project_status.config = Mock(wraps=app.project_status.config)
    for _ in range(8):
        app.poll_project_status()
    assert jobs == [] and app._status_request is None
    assert app.project_status.cget("text") == summary
    app.project_status.config.assert_not_called()


def test_background_change_retains_summary_and_equal_result_does_not_reset_label(tmp_path, monkeypatch):
    app, jobs = _status_controller(tmp_path, monkeypatch)
    app.refresh_project_status()
    finish_status(app, jobs)
    summary = app.project_status.cget("text")
    app.project_status.config = Mock(wraps=app.project_status.config)
    # This input is relevant to actual conflict inspection; touching it causes
    # a read, even when the status summary itself has not changed.
    sp.json_save(tmp_path / "人工判定資料庫.json", {"isolated_test": True})
    app.poll_project_status()
    assert len(jobs) == 1 and app.project_status.cget("text") == summary
    finish_status(app, jobs)
    app.project_status.config.assert_not_called()
    sp.json_save(tmp_path / "pipeline_status.json", {"status": "PROCESSING", "phase": "2/3"})
    app.poll_project_status()
    finish_status(app, jobs)
    assert "核對語境與應標注音" in app.project_status.cget("text")
    app.refresh_project_status()  # Explicit retry always reads unchanged data.
    finish_status(app, jobs)


def test_change_after_slow_reply_is_computed_cannot_publish_old_completion(tmp_path, monkeypatch):
    app, jobs = _status_controller(tmp_path, monkeypatch)
    app.refresh_project_status()
    jobs.pop().run()
    sp.json_save(tmp_path / "pipeline_status.json", {"status": "PROCESSING", "phase": "1/3"})
    app.poll()
    assert "全冊校對完成" not in app.project_status.cget("text")
    assert app._status_revision is None
    app.poll_project_status()
    finish_status(app, jobs)
    assert "讀取課本目前注音" in app.project_status.cget("text")


def test_failed_read_is_uncertain_until_change_or_manual_retry(tmp_path, monkeypatch):
    app, jobs = _status_controller(tmp_path, monkeypatch)
    app.refresh_project_status()
    finish_status(app, jobs)
    service = app._status_service
    invoke = service.invoke
    def fail_read(*_args):
        raise OSError("isolated transport failure")
    monkeypatch.setattr(service, "invoke", fail_read)
    app.refresh_project_status()
    finish_status(app, jobs)
    assert app.project_status.cget("text") == launcher._UNKNOWN_PROGRESS
    assert "isolated transport failure" in app.project_diagnostics
    for _ in range(4):
        app.poll_project_status()
    assert jobs == []
    monkeypatch.setattr(service, "invoke", invoke)
    app.refresh_project_status()
    finish_status(app, jobs)
    assert "全冊校對完成" in app.project_status.cget("text")
    assert app._status_service is not service  # A failed child is not reused.


def test_status_revision_tracks_all_reader_inputs_without_reading_contents(tmp_path):
    initial = launcher.project_status_revision(tmp_path)
    for name, *_ in initial:
        path = tmp_path / name
        path.write_text("isolated input", encoding="utf-8")
        assert launcher.project_status_revision(tmp_path) != initial
        path.unlink()
        assert launcher.project_status_revision(tmp_path) == initial
    # The nested Excel conflict reader also depends on occurrence proof and
    # its strict project transaction. Each independently invalidates the hint.
    sp.json_save(tmp_path / "跨Excel_actual衝突.json", {})
    conflict_revision = launcher.project_status_revision(tmp_path)
    assert len(conflict_revision) == len(initial) + 3
    for name, *_ in conflict_revision[len(initial):]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("isolated proof", encoding="utf-8")
        assert launcher.project_status_revision(tmp_path) != conflict_revision
        path.unlink()
        assert launcher.project_status_revision(tmp_path) == conflict_revision


class Geometry(Surface):
    def __init__(self, master=None, *, width=600, requested_height=100, **options):
        super().__init__(master, **options)
        self.width, self.requested_height = width, requested_height

    def winfo_width(self):
        return self.width

    def winfo_reqheight(self):
        return self.requested_height


def test_notebook_tracks_only_selected_natural_content_and_cancels_pending_layout():
    app = launcher.App.__new__(launcher.App)
    app.notebook = Geometry(height=0)
    proof = Geometry(app.notebook, requested_height=310)
    diff = Geometry(app.notebook, requested_height=9000)
    child = Geometry(proof)
    selected = ["proof"]
    app.notebook.select = lambda: selected[0]
    app.notebook.nametowidget = lambda name: {"proof": proof, "diff": diff}[name]
    app.body_canvas = SimpleNamespace(request_layout=Mock())
    app._install_notebook_layout()
    app.notebook.update()
    assert app.notebook.cget("height") == 310
    # Real handler response to wrapping/width changes follows natural content;
    # there is no viewport cap which could hide the last field or button.
    proof.requested_height = 720
    child.event_generate("<Configure>")
    app.notebook.update()
    assert app.notebook.cget("height") == 720
    diff.requested_height = 245
    selected[0] = "diff"
    app.notebook.event_generate("<<NotebookTabChanged>>")
    app.notebook.update()
    assert app.notebook.cget("height") == 245
    app.notebook.width = 1
    diff.requested_height = 9999
    app.notebook.event_generate("<Configure>")
    app.notebook.update()
    assert app.notebook.cget("height") == 245
    app.notebook.width = 600
    diff.requested_height = 380
    app.notebook.event_generate("<Configure>")
    app.notebook.update()
    assert app.notebook.cget("height") == 380
    app.notebook.event_generate("<Configure>")
    pending = app._notebook_layout_pending
    app.notebook.destroy()
    assert pending not in app.notebook.callbacks
    assert app._notebook_layout_pending is None


def test_toggle_log_requests_scroll_content_relayout():
    app = launcher.App.__new__(launcher.App)
    app.log_visible = False
    app.log_frame = Surface()
    app.log_frame.pack = Mock()
    app.log_frame.pack_forget = Mock()
    app.body_canvas = SimpleNamespace(request_layout=Mock())
    app.toggle_log()
    assert app.log_visible
    app.log_frame.pack.assert_called_once()
    app.toggle_log()
    assert not app.log_visible
    app.log_frame.pack_forget.assert_called_once()
    assert app.body_canvas.request_layout.call_count == 2
