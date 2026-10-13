"""Real Windows spawn/PDF/service regressions without a Tcl interpreter."""
import hashlib
import os
import json
import sys
import threading
import time
from unittest.mock import patch

import pytest
import standalone_proofread as sp
import review_display as display
import review_gui as gui
from review_operation_worker import (ReviewProcessService, ReviewOperationError, PreviewJob,
                                    validate_payload, validate_preview_png)
from tests.test_staged_review_navigation_v580 import create_staged_navigation_fixture
from tests.no_window_review_support import Preview, Surface, InlineReviewService
from tests.test_excel_text_storage import isolated_pipeline, exported, fill


def fixture(tmp_path):
    manifest = create_staged_navigation_fixture(tmp_path)
    return manifest, manifest["records"][0]


def wait_preview(job):
    deadline = time.monotonic() + 15
    while True:
        result = job.poll()
        if result is not None:
            return result
        assert time.monotonic() < deadline
        time.sleep(.01)


def test_spawn_save_keeps_service_anchor_and_rejects_source_drift(tmp_path):
    manifest, row = fixture(tmp_path)
    service = ReviewProcessService(tmp_path)
    try:
        snapshot = service.load_resume_snapshot()
        assert service.worker_pid != os.getpid()
        event = sp.build_manual_expected_event(row, operation="ENTER_EXPECTED", expected_set="ㄎㄢ")
        result = service.save_event(row["review_id"], event, expected_manifest=manifest, expected_db=snapshot.db)
        assert sp.load_or_initialize_db(tmp_path)["events"][row["review_id"]] == result.db["events"][row["review_id"]]
        assert result.ledger[0]["actual"] == row["actual"]
        anchor = service.evidence_anchor
        service.with_invalidated_cache()
        service.load_resume_snapshot(expected_manifest=manifest, expected_db=result.db)
        assert service.evidence_anchor == anchor
        pdf = tmp_path / row["pdf_name"]
        pdf.write_bytes(pdf.read_bytes() + b"\nchanged")
        before = (tmp_path / "人工判定資料庫.json").read_bytes()
        with pytest.raises(ReviewOperationError, match="SOURCE_INVALID"):
            service.save_event(row["review_id"], None, expected_manifest=manifest, expected_db=result.db)
        assert (tmp_path / "人工判定資料庫.json").read_bytes() == before
    finally:
        service.close()
    assert not service._process.is_alive()


def test_writer_close_waits_for_result_and_never_terminates(tmp_path):
    manifest, row = fixture(tmp_path)
    service = ReviewProcessService(tmp_path)
    snapshot = service.load_resume_snapshot()
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    outcome = []
    def progress(phase):
        if phase == "正在原子寫入人工判定":
            entered.set()
            assert release.wait(10)
    def write():
        event = sp.build_manual_expected_event(row, operation="ENTER_EXPECTED", expected_set="ㄎㄢ")
        outcome.append(service.save_event(row["review_id"], event, expected_manifest=manifest,
                                         expected_db=snapshot.db, progress=progress))
    worker = threading.Thread(target=write)
    worker.start()
    assert entered.wait(10)
    closer = threading.Thread(target=lambda: (service.close(), closed.set()))
    closer.start()
    assert not closed.wait(.05)
    release.set()
    worker.join(10); closer.join(10)
    assert closed.is_set() and outcome
    assert row["review_id"] in sp.load_or_initialize_db(tmp_path)["events"]


def test_real_spawn_actual_staging_preserves_expected_and_rejects_drift(tmp_path):
    manifest, row = fixture(tmp_path)
    service = ReviewProcessService(tmp_path)
    try:
        snapshot = service.load_resume_snapshot()
        request = dict(expected_manifest=manifest, expected_db=snapshot.db, review_id=row["review_id"])
        prepared = service.invoke("prepare_actual", request)
        assert row["occurrence_id"] in {item["occurrence_id"] for item in prepared["group"]["members"]}
        before = (tmp_path / "人工判定資料庫.json").read_bytes()
        request.update(reading="ㄎㄢ", checked_occurrence_ids=[row["occurrence_id"]])
        staged = service.invoke("stage_actual", request)
        assert staged["summary"]["staged_group_count"] == 1
        assert row["occurrence_id"] in staged["summary"]["staged_checked_occurrence_ids"]
        assert (tmp_path / "人工判定資料庫.json").read_bytes() == before
        import actual_review as ar
        path = ar.manual_actual_staging_path(sp.project_actual_evidence_root(tmp_path))
        retained = path.read_bytes()
        pdf = tmp_path / row["pdf_name"]
        pdf.write_bytes(pdf.read_bytes() + b"\nchanged after preparation")
        with pytest.raises(ReviewOperationError) as error:
            service.invoke("stage_actual", request)
        assert error.value.commit_state == "NOT_WRITTEN"
        assert path.read_bytes() == retained
    finally:
        service.close()


def test_preview_spawn_has_same_pixels_and_geometry(tmp_path):
    _, row = fixture(tmp_path)
    pixels, target, notice = display.occurrence_preview(row, display_width=984)
    job = PreviewJob(dict(entry=row, padding=(150, 115), output_dir=None, display_width=984))
    value = wait_preview(job)
    assert value["pid"] != os.getpid()
    assert value["target"] == target and value["notice"] == notice
    assert hashlib.sha256(value["png"]).digest() == hashlib.sha256(pixels.tobytes("png")).digest()
    with pytest.raises(ValueError):
        validate_preview_png(dict(value, width=value["width"] + 1))
    with pytest.raises(ValueError):
        validate_preview_png(dict(value, png=value["png"][:-4]))
    job.cancel()
    job._cleanup.join(10)


def test_payload_is_finite_plain_and_child_owned_snapshot(tmp_path):
    _, row = fixture(tmp_path)
    service = ReviewProcessService(tmp_path)
    for operation, payload in (("arbitrary", {}), ("save", {"review_id": "x", "event": lambda: None}),
                               ("resume", {"unknown": 1}), ("apply_actual", {"recovery_only": "yes"})):
        with pytest.raises(ValueError):
            validate_payload(operation, payload)
    copied = []
    class Unsafe:
        def __deepcopy__(self, memo):
            copied.append(True)
            return self
    with pytest.raises(ValueError):
        PreviewJob._payload(dict(entry={"nested": Unsafe()}, padding=(1, 1), output_dir=tmp_path, display_width=100))
    assert copied == []
    assert PreviewJob._payload(dict(entry=row, padding=(1, 1), output_dir=tmp_path, display_width=100))["output_dir"] == str(tmp_path)
    try:
        with pytest.raises(ReviewOperationError, match="快照已過期"):
            service.invoke("validate_staging", {"snapshot_key": "0" * 64})
    finally:
        service.close()


def test_preview_coalesces_latest_and_clear_discards_old_result(tmp_path):
    _, row = fixture(tmp_path)
    preview = Preview(Surface())
    starts = []
    preview._entry, preview._output_dir, preview._padding = row, None, (150, 115)
    class Job:
        def cancel(self):
            starts.append("cancel")
    def start(token, payload):
        starts.append(payload["display_width"])
        preview._preview_token, preview._preview_job = token, Job()
    with patch.object(preview, "_start_preview", side_effect=start):
        preview._request_preview(800)
        preview._request_preview(900)
        preview._request_preview(1000)
        assert starts == [800]
        assert preview._preview_pending[1]["display_width"] == 1000
        assert preview.photo is None and preview.target is None
        preview.clear()
    assert starts == [800]
    assert preview._preview_pending is None and preview._entry is None
    preview._disposed = True
    preview._cancel_preview()
    assert starts == [800, "cancel"]


def test_owned_operation_ignores_other_project_and_reports_publish_failure(tmp_path):
    manifest, _ = fixture(tmp_path)
    app = gui.ReviewApp.__new__(gui.ReviewApp)
    app.root, app.output_dir = Surface(), tmp_path
    app.manifest, app.db, app.records = manifest, sp.load_or_initialize_db(tmp_path), []
    app._save_service = InlineReviewService(tmp_path)
    app.status = Surface(app.root)
    app._project_generation = 0
    app._disable_save_controls = lambda: None
    app._restore_save_controls = lambda: None
    published = []
    entered, release = threading.Event(), threading.Event()
    def work(progress):
        entered.set(); assert release.wait(10)
        return app._save_service.load_resume_snapshot()
    app._start_owned_operation("test", work, published.append)
    assert entered.wait(5)
    assert not app._start_owned_operation("duplicate", work, published.append)
    app.output_dir = tmp_path / "other"
    release.set(); app._operation_worker.join(10); app.root.update()
    assert not published
    assert app._save_recovery_required


def test_real_cli_multi_file_retains_first_commit_and_reports_partial(isolated_pipeline, monkeypatch, tmp_path, capsys):
    import tests.test_excel_text_storage as inputs
    monkeypatch.setattr(inputs, "TEXTS", inputs.TEXTS[:2])
    root = isolated_pipeline("expected")
    path, sheet = exported(root, "expected")
    good, bad = tmp_path / "first.xlsx", tmp_path / "second.xlsx"
    wb, ws, columns = fill(path, sheet, "expected")
    wb.save(good)
    ws.cell(2, columns["review_id"], "modified protected identity")
    wb.save(bad); wb.close()
    monkeypatch.setenv("ZHUYIN_OPERATION_ID", "isolated-cli-operation")
    monkeypatch.setenv("ZHUYIN_OPERATION_PROJECT", str(root.resolve()))
    monkeypatch.setattr(sys, "argv", ["standalone_proofread.py", "-o", str(root), "--import-gpt-auto", str(good), str(bad)])
    with pytest.raises(ValueError):
        sp.main()
    lines = capsys.readouterr().out.splitlines()
    events = [json.loads(line[len("[ZHUYIN_OPERATION] "):]) for line in lines if line.startswith("[ZHUYIN_OPERATION] ")]
    assert events[-1]["completed_files"] == 1 and events[-1]["total_files"] == 2
    assert events[-1]["commit_state"] == "UNKNOWN"
    db = sp.load_or_initialize_db(root)
    import pdf_portability as pp
    assert len(db["events"]) == 2
    # Raw imported events retain the filled-cell string; the canonical binding
    # separately stores the normalized pronunciation list.
    assert all(event["expected_set"] == "ㄐㄩㄝˊ" for event in db["events"].values())
    assert all(event["expected_resolution_binding"]["resolution"]["expected_set"] == ["ㄐㄩㄝˊ"]
               for event in db["events"].values())
    assert all(event["portability_source"]["source_excel_sha256"] == pp._sha(good)
               for event in db["events"].values())


def test_launcher_reservation_stale_completion_and_close_are_owned(tmp_path, monkeypatch):
    import queue
    import standalone_gui as launcher
    from tests.no_window_review_support import Value
    from unittest.mock import Mock
    app = launcher.App.__new__(launcher.App)
    app.output = Value(value=str(tmp_path))
    app.root, app.run_status, app.log = Surface(), Surface(), Mock()
    app.q = queue.Queue()
    app.refresh_project_status = Mock()
    original, workers = threading.Thread, []
    def thread(*args, **kwargs):
        value = original(*args, **kwargs); workers.append(value); return value
    monkeypatch.setattr(launcher.threading, "Thread", thread)
    button = Surface()
    command = [sys.executable, "-c", "import time;time.sleep(.15);print('isolated diagnostic')"]
    assert app.start(command, button)
    assert not app.start(command)
    app.root.destroy = Mock()
    app.close(); app.root.destroy.assert_not_called()
    app.output.set(str(tmp_path / "different"))
    app.run_status.config(text="new owner status")
    workers[0].join(10)
    assert not workers[0].is_alive()
    app.poll()
    assert app.run_status.cget("text") == "new owner status"
    assert button.cget("state") == "normal"
    assert not app._project_operations
    app.close(); app.root.destroy.assert_called_once()


def test_project_status_uses_isolated_process_and_updates_current_content(tmp_path):
    fixture(tmp_path)
    service = ReviewProcessService(tmp_path)
    try:
        sp.json_save(tmp_path / "pipeline_status.json", {"status": "PROCESSING", "phase": "1/3"})
        first, _ = service.invoke("project_status", {})
        assert "讀取課本目前注音" in first and service.worker_pid != os.getpid()
        sp.json_save(tmp_path / "pipeline_status.json", {"status": "PROCESSING", "phase": "2/3"})
        second, _ = service.invoke("project_status", {})
        assert "核對語境與應標注音" in second
        assert "全冊校對完成" not in second
    finally:
        service.close()


def test_post_success_refresh_boundaries_preserve_known_operation_state(tmp_path, monkeypatch):
    """Unit transport states after a declared successful backend return.

    Backend boundaries below do not test transaction success. Real durable
    writes, spawn ownership and drift refusal have their own isolated cases.
    """
    import review_save_service as services
    from review_operation_worker import ReviewOperationState
    manifest, row = fixture(tmp_path)
    monkeypatch.setattr(sp, "stage_manual_actual_correction", lambda *a, **k: None)
    monkeypatch.setattr(sp, "regenerate_report", lambda *a, **k: "saved.xlsx")
    monkeypatch.setattr(sp, "apply_staged_manual_actual_corrections", lambda *a, **k: {})
    real_factory = services.ReviewSaveService
    def fail(*a, **k):
        raise OSError("post-success preparation failure")
    for operation, status in (("stage_actual", "STAGED_REFRESH_PENDING"),
                              ("report", "REPORT_SAVED_REFRESH_PENDING"),
                              ("apply_actual", "COMMITTED_REFRESH_PENDING")):
        for boundary in ("service", "view"):
            service = real_factory(tmp_path)
            snapshot = service.load_resume_snapshot()
            payload = dict(expected_manifest=manifest, expected_db=snapshot.db)
            if operation == "stage_actual":
                payload.update(review_id=row["review_id"], reading="ㄎㄢ",
                               checked_occurrence_ids=[row["occurrence_id"]])
            state = ReviewOperationState(tmp_path, service=service)
            with monkeypatch.context() as faults:
                if boundary == "service":
                    if operation == "stage_actual":
                        faults.setattr(service, "with_invalidated_cache", fail)
                    else:
                        faults.setattr(services, "ReviewSaveService", fail)
                else:
                    replacement = real_factory(tmp_path)
                    replacement.load_resume_snapshot()
                    faults.setattr(replacement._conflicts, "review_view", fail)
                    if operation == "stage_actual":
                        faults.setattr(service, "with_invalidated_cache", lambda: replacement)
                    else:
                        faults.setattr(services, "ReviewSaveService", lambda *a: replacement)
                with pytest.raises(ReviewOperationError, match="post-success") as error:
                    state.dispatch(operation, payload, lambda phase: None, lambda: False)
                assert error.value.commit_state == status


def _status_controller(tmp_path, monkeypatch):
    """Finite read-only transport/scheduler; real controller and JSON parser.

    This controls reply arrival without a Tk interpreter or domain writes. The
    separate real-spawn status case proves isolated reader execution.
    """
    import queue
    from pathlib import Path
    import standalone_gui as launcher
    from tests.no_window_review_support import Value
    from unittest.mock import Mock
    app = launcher.App.__new__(launcher.App)
    app.output = Value(value=str(tmp_path))
    app.root, app.project_status, app.run_status = Surface(), Surface(), Surface()
    app.log, app.q, app._project_operations = Mock(), queue.Queue(), {}
    app.output.trace_add("write", app._status_selection_changed)
    jobs = []
    class DeferredThread:
        def __init__(self, *, target, daemon):
            assert daemon is False
            self.target = target
        def start(self):
            jobs.append(self)
        def run(self):
            self.target()
    class ReadStatusService:
        def __init__(self, project):
            self.output_dir = Path(project)
        def invoke(self, operation, payload):
            assert operation == "project_status" and payload == {}
            return launcher.project_status_details(self.output_dir)
        def close(self):
            pass
    monkeypatch.setattr(launcher.threading, "Thread", DeferredThread)
    monkeypatch.setattr(launcher, "ReviewProcessService", ReadStatusService)
    tmp_path.mkdir(exist_ok=True)
    sp.json_save(tmp_path / "校對工作階段.json", {})
    sp.json_save(tmp_path / "pipeline_status.json", dict(status="PROOFREAD_COMPLETE", completion_gate=dict(
        status="PROOFREAD_COMPLETE", complete=True, failed_gates=[],
        hard_gates={key: True for key in launcher._KNOWN_GATES},
        state_counts={"PASS": 1}, in_scope_total=1, actual_covered=1, expected_covered=1)))
    assert "全冊校對完成" in launcher.project_status_details(tmp_path)[0]
    return app, jobs


def test_status_old_complete_cannot_publish_during_same_project_operation(tmp_path, monkeypatch):
    app, jobs = _status_controller(tmp_path, monkeypatch)
    app.refresh_project_status()
    old = app._status_request
    jobs.pop(0).run()  # Valid completion is computed, but not yet polled.
    assert app._reserve_operation(tmp_path, "import")
    sp.json_save(tmp_path / "pipeline_status.json", {"status": "PROCESSING", "phase": "2/3"})
    app.refresh_project_status(new_request=False)
    latest = app._status_request
    assert latest != old
    app.poll()
    assert app._status_request == latest
    assert app._status_reader_request is None
    assert jobs == []  # Active mutation cannot start another status reader.
    assert app.project_status.cget("text") == "正在驗證目前專案狀態…"
    assert "PROOFREAD_COMPLETE" not in app.project_diagnostics
    assert len(app._project_operations) == 1


def test_status_late_reply_after_completion_cannot_clear_new_request(tmp_path, monkeypatch):
    app, jobs = _status_controller(tmp_path, monkeypatch)
    app.refresh_project_status()
    jobs.pop(0).run()
    old_reply = app.q.get_nowait()
    token = app._reserve_operation(tmp_path, "import")
    sp.json_save(tmp_path / "pipeline_status.json", {"status": "PROCESSING", "phase": "2/3"})
    app.q.put(("__OP_DONE__", token, str(tmp_path), 0, None, None))
    app.poll()
    latest = app._status_request
    assert latest != old_reply[1] and not app._project_operations
    app.q.put(old_reply)
    app.poll()
    assert app._status_request == app._status_reader_request == latest
    assert len(jobs) == 1 and "全冊校對完成" not in app.project_status.cget("text")
    app.q.put(old_reply)  # A duplicate old reply cannot retire the new reader.
    app.poll()
    assert app._status_reader_request == latest and len(jobs) == 1
    app.poll_project_status()  # Ensure does not invalidate a slow latest reader.
    assert app._status_request == app._status_reader_request == latest and len(jobs) == 1
    jobs.pop(0).run()
    app.poll()
    assert app._status_request is None and app._status_reader_request is None
    assert "核對語境與應標注音" in app.project_status.cget("text")
    assert '"phase": "2/3"' in app.project_diagnostics


def test_status_selection_roundtrip_reselection_and_latest_request_are_owned(tmp_path, monkeypatch):
    app, jobs = _status_controller(tmp_path, monkeypatch)
    app.refresh_project_status()
    old = app._status_request
    jobs.pop(0).run()
    other = tmp_path / "other"
    app.output.set(str(other))
    app.refresh_project_status(new_request=False)
    app.output.set(str(tmp_path))
    before_reselection = app._status_generation
    app.output.set(str(tmp_path))  # Same-value selection still retires old work.
    assert app._status_generation > before_reselection
    app.refresh_project_status(new_request=False)
    latest = app._status_request
    assert latest[0] != old[0] and latest[1] > old[1]
    app.poll()
    assert app._status_request == app._status_reader_request == latest and len(jobs) == 1
    sp.json_save(tmp_path / "pipeline_status.json", {"status": "PROCESSING", "phase": "1/3"})
    jobs.pop(0).run()
    earlier = app.q.get_nowait()
    app.refresh_project_status()  # A newer explicit same-project request.
    newest = app._status_request
    assert newest[0] != latest[0] and newest[1] > latest[1]
    app.q.put(earlier)
    app.poll()
    assert app._status_request == app._status_reader_request == newest and len(jobs) == 1
    assert "全冊校對完成" not in app.project_status.cget("text")
    sp.json_save(tmp_path / "pipeline_status.json", {"status": "PROCESSING", "phase": "2/3"})
    jobs.pop(0).run()
    app.poll()
    assert app._status_request is None and app._status_reader_request is None
    assert "核對語境與應標注音" in app.project_status.cget("text")
    assert '"phase": "2/3"' in app.project_diagnostics
