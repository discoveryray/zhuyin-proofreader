"""Synthetic project and fake UI regressions. Never initialize Tcl/Tk."""
import contextlib
import os
import queue
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import review_gui as gui
import standalone_gui as launcher
import standalone_proofread as sp
from review_save_service import ReviewSaveService
from tests.review_save_test_support import QueuedRoot, wait_for_save
from tests.test_staged_review_navigation_v580 import create_staged_navigation_fixture, headless_app


class ResponsiveReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)
        self.manifest = create_staged_navigation_fixture(self.output)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        # Any accidental native entry in this test is an error, not a skip.
        self.stack.enter_context(patch.object(gui.tk, "Tk", side_effect=AssertionError("real Tk forbidden")))
        self.stack.enter_context(patch.object(gui.tk, "Toplevel", side_effect=AssertionError("real Tk forbidden")))
        for name in ("showinfo", "showerror", "showwarning"):
            self.stack.enter_context(patch.object(gui.messagebox, name))

    def fake_shell(self):
        root = QueuedRoot()
        root.protocol, root.title, root.destroy = Mock(), Mock(), Mock()
        def widget(*args, **kwargs):
            item = Mock()
            item.cget.return_value = "normal"
            item.winfo_children.return_value = []
            return item
        for name in ("Frame", "Button", "LabelFrame", "Text", "Menubutton", "Menu"):
            self.stack.enter_context(patch.object(gui.tk, name, side_effect=widget))
        for name in ("WrappedLabel", "ActionRows", "OccurrencePreview"):
            self.stack.enter_context(patch.object(gui, name, side_effect=widget))
        self.stack.enter_context(patch.object(gui, "apply_screen_safe_geometry"))
        self.stack.enter_context(patch.object(gui, "create_scrollable_body", return_value=(widget(), widget())))
        return root

    def pump_loading(self, app):
        deadline = time.monotonic() + 10
        while app._startup_in_progress:
            self.assertLess(time.monotonic(), deadline)
            app.root.update()
            time.sleep(.001)
        app._startup_worker.join(10)
        app.root.update()

    def test_constructor_returns_while_loading_and_publishes_only_on_ui_thread(self):
        root = self.fake_shell()
        entered, release = threading.Event(), threading.Event()
        worker_ids, ui_ids = [], []
        original = gui.load_review_project
        def held(output, progress):
            worker_ids.append(threading.get_ident())
            progress("隔離測試核對階段")
            entered.set()
            if not release.wait(10):
                raise RuntimeError("worker not released")
            return original(output, progress)
        with patch.object(gui, "load_review_project", side_effect=held):
            app = gui.ReviewApp(root, self.output)
            app.show = Mock(side_effect=lambda: ui_ids.append(threading.get_ident()))
            try:
                self.assertTrue(entered.wait(5))
                self.assertTrue(app._save_busy())
                self.assertEqual(app.manifest, {})
                ticks = []
                root.after(0, lambda: ticks.append(True))
                root.update()
                self.assertEqual(ticks, [True])
                self.assertIn("隔離測試核對階段", app.status.config.call_args.kwargs["text"])
                app.next()
                app.report()
                self.assertEqual(app.records, [])
            finally:
                release.set()
                self.pump_loading(app)
        self.assertEqual(app.manifest, self.manifest)
        self.assertEqual(ui_ids, [threading.get_ident()])
        self.assertNotEqual(worker_ids[0], threading.get_ident())
        self.assertFalse(app._save_busy())
        self.assertEqual(app._async_after_ids, set())

    def test_loader_replays_bound_expected_and_preserves_actual(self):
        row = self.manifest["records"][0]
        db = sp.normalize_db({})
        db["events"][row["review_id"]] = sp.build_manual_expected_event(
            row, operation="CONFIRM_CURRENT_AS_EXPECTED")
        sp.json_save(self.output / "人工判定資料庫.json", db)
        phases = []
        result = gui.load_review_project(self.output, phases.append)
        self.assertEqual(result["prepared"]["records"], headless_app(self.output).records)
        self.assertEqual(result["db"], db)
        self.assertEqual(result["manifest"], self.manifest)
        self.assertGreaterEqual(len(phases), 5)

    def test_corrupt_artifact_fails_closed_then_retry_loads(self):
        artifact = Path(self.manifest["pdfs"][0]["actual_workbook"])
        original = artifact.read_bytes()
        artifact.write_bytes(b"synthetic corrupt artifact")
        app = gui.ReviewApp(self.fake_shell(), self.output)
        app.show = Mock()
        self.pump_loading(app)
        self.assertTrue(app._startup_failed)
        self.assertTrue(app._save_busy())
        self.assertEqual(app.records, [])
        self.assertEqual(app.exit_code, 1)
        app.retry_startup_button.pack.assert_called()
        artifact.write_bytes(original)
        app._start_review_loading()
        self.pump_loading(app)
        self.assertFalse(app._startup_failed)
        self.assertEqual(app.exit_code, 0)
        self.assertEqual(app.manifest, self.manifest)

    def test_close_loading_cancels_owned_poll_and_ignores_late_result(self):
        entered, release = threading.Event(), threading.Event()
        result = gui.load_review_project(self.output, lambda _: None)
        def held(*args):
            entered.set()
            release.wait(10)
            return result
        with patch.object(gui, "load_review_project", side_effect=held):
            app = gui.ReviewApp(self.fake_shell(), self.output)
            try:
                self.assertTrue(entered.wait(5))
                app.close()
                app.root.destroy.assert_called_once()
                request = app._startup_request
                app._dispose_async(SimpleNamespace(widget=app.root))
                messages = queue.Queue()
                messages.put(("result", result))
                app._poll_review_loading(request, messages)
                self.assertEqual(app.manifest, {})
                self.assertTrue(app._startup_cancelled.is_set())
                self.assertEqual(app._async_after_ids, set())
            finally:
                release.set()
                app._startup_worker.join(10)
        self.assertFalse(app._startup_worker.is_alive())

    def test_thread_start_failure_is_visible_and_retryable(self):
        with patch.object(gui.threading.Thread, "start", side_effect=RuntimeError("synthetic no thread")):
            app = gui.ReviewApp(self.fake_shell(), self.output)
        self.assertTrue(app._startup_failed)
        self.assertIn("synthetic no thread", app.status.config.call_args.kwargs["text"])
        app.root.update()
        self.assertEqual(app._async_after_ids, set())
        app.show = Mock()
        app._start_review_loading()
        self.pump_loading(app)
        self.assertFalse(app._startup_failed)

    def test_save_progress_commit_boundary_and_observer_failure(self):
        service = ReviewSaveService(self.output)
        row = self.manifest["records"][0]
        event = sp.build_manual_expected_event(row, operation="CONFIRM_CURRENT_AS_EXPECTED")
        phases = []
        original = sp.json_save
        def save(path, db, **kwargs):
            self.assertEqual(phases[-1], "正在原子寫入人工判定")
            return original(path, db, **kwargs)
        def progress(phase):
            phases.append(phase)
            if phase == "人工判定已寫入":
                self.assertIn(row["review_id"], sp.load_or_initialize_db(self.output)["events"])
                raise RuntimeError("synthetic observer failure after durable write")
        with patch.object(sp, "json_save", side_effect=save):
            result = service.save_event(row["review_id"], event, progress=progress)
        self.assertEqual(phases[-1], "人工判定已寫入")
        self.assertIn("寫入前再次完整核對依賴（尚未寫入）", phases)
        self.assertEqual(result.db, sp.load_or_initialize_db(self.output))

    def test_failed_write_never_reports_durable_save(self):
        row = self.manifest["records"][0]
        event = sp.build_manual_expected_event(row, operation="CONFIRM_CURRENT_AS_EXPECTED")
        phases = []
        with patch.object(sp, "json_save", side_effect=OSError("synthetic disk failure")):
            with self.assertRaises(OSError):
                ReviewSaveService(self.output).save_event(row["review_id"], event, progress=phases.append)
        self.assertNotIn("人工判定已寫入", phases)
        self.assertEqual(sp.load_or_initialize_db(self.output)["events"], {})

    def test_progress_is_published_by_save_ui_poll(self):
        app = headless_app(self.output)
        app.status = Mock()
        app.status.cget.return_value = "ready"
        row = app.current()
        entered, release = threading.Event(), threading.Event()
        save = app._save_service.save_event
        def held(*args, **kwargs):
            kwargs["progress"]("隔離測試：尚未寫入")
            entered.set()
            release.wait(10)
            return save(*args, **kwargs)
        with patch.object(app._save_service, "save_event", side_effect=held):
            app.save_event(row, sp.build_manual_expected_event(row, operation="CONFIRM_CURRENT_AS_EXPECTED"))
            try:
                self.assertTrue(entered.wait(5))
                app.root.update()
                self.assertIn("尚未寫入", app.status.config.call_args.kwargs["text"])
                self.assertEqual(app.db["events"], {})
            finally:
                release.set()
                wait_for_save(app)
        self.assertTrue(app._last_event_saved)

    def launcher_app(self):
        app = launcher.App.__new__(launcher.App)
        app.output = Mock()
        app.output.get.return_value = str(self.output)
        app.root = Mock()
        app.q = queue.Queue()
        app.review_button = Mock()
        app.run_status = Mock()
        app.log = Mock()
        return app

    def test_launcher_supervisor_captures_synthetic_child_stderr_and_exit(self):
        messages, token = queue.Queue(), object()
        # This child only prints diagnostics; it never imports a GUI.
        cmd = [sys.executable, "-c", "import sys; print('synthetic ready', flush=True); "
               "print('synthetic traceback', file=sys.stderr, flush=True); sys.exit(7)"]
        launcher.review_child_worker(cmd, os.environ.copy(), messages, token)
        items = []
        while not messages.empty():
            items.append(messages.get_nowait())
        self.assertEqual(items[-1][:3], ("__REVIEW_DONE__", token, 7))
        self.assertIn("synthetic traceback", items[-1][3])
        app = self.launcher_app()
        app._review_child_token = token
        app._review_child_message(items[-1])
        self.assertIsNone(app._review_child_token)
        self.assertIn("代碼 7", app.run_status.config.call_args.kwargs["text"])
        app.review_button.config.assert_called_with(state="normal")

    def test_launcher_duplicate_and_normal_close_do_not_claim_completion(self):
        app = self.launcher_app()
        with patch.object(launcher.threading, "Thread") as thread:
            app.review()
            token = app._review_child_token
            app.review()
        self.assertEqual(thread.call_count, 1)
        app._review_child_message(("__REVIEW_DONE__", object(), 0, "obsolete"))
        self.assertIs(app._review_child_token, token)
        app._review_child_message(("__REVIEW_DONE__", token, 0, ""))
        self.assertIsNone(app._review_child_token)
        self.assertIn("視窗已關閉", app.run_status.config.call_args.kwargs["text"])
        self.assertNotIn("全冊校對完成", app.run_status.config.call_args.kwargs["text"])

    def test_launcher_spawn_error_restores_retry(self):
        app = self.launcher_app()
        with patch.object(launcher.threading.Thread, "start", side_effect=RuntimeError("synthetic thread failure")):
            app.review()
        self.assertIsNone(app._review_child_token)
        app.review_button.config.assert_called_with(state="normal")
