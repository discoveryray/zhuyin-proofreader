from __future__ import annotations

import copy
import tempfile
import threading
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import review_gui as gui
import standalone_proofread as sp
from tests.review_save_test_support import wait_for_save
from tests.test_staged_review_navigation_v580 import (
    create_staged_navigation_fixture, headless_app, controlled_refresh,
)


class AsyncReviewSaveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)
        create_staged_navigation_fixture(self.output)
        self.app = headless_app(self.output)
        self.row = self.app.current()
        self.event = sp.build_manual_expected_event(self.row, operation="CONFIRM_CURRENT_AS_EXPECTED")

    def test_worker_keeps_ui_unlocked_but_guards_all_mutating_actions_and_debounce(self):
        app = self.app
        app._rendered_review_id = self.row["review_id"]
        entered, release = threading.Event(), threading.Event()
        save = app._save_service.save_event
        worker_ids, ui_ids = [], []

        def slow_save(*args, **kwargs):
            worker_ids.append(threading.get_ident())
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test did not release worker")
            return save(*args, **kwargs)

        app.show.side_effect = lambda: ui_ids.append(threading.get_ident())
        with patch.object(app._save_service, "save_event", side_effect=slow_save) as saves, \
                patch.object(gui.messagebox, "showinfo"), patch.object(gui.messagebox, "showwarning"), \
                patch.object(gui, "ExpectedDialog") as dialog, patch.object(gui, "regenerate_report") as report:
            app.confirm_current_expected(self.row["review_id"])
            self.assertTrue(entered.wait(5))
            try:
                self.assertTrue(app._save_in_progress)
                self.assertEqual(app.db["events"], {})
                for operation in (app.prev, app.next, app.defer_current, app.revisit_deferred,
                                  app.resolve_expected, app.clear, app.report, app.apply_staged_actuals,
                                  app.correct_actual, app.create_reusable_expected_rule):
                    operation()
                app.confirm_current_expected(self.row["review_id"])
                # The main-loop pump stays usable while the worker is blocked.
                ticks = []
                app.root.after(0, lambda: ticks.append(True))
                app.root.update()
                self.assertEqual(ticks, [True])
                self.assertEqual(app.current(), self.row)
                dialog.assert_not_called()
                report.assert_not_called()
            finally:
                release.set()
            wait_for_save(app)
            app._rendered_review_id = app.current()["review_id"]
            app.confirm_current_expected(app.current()["review_id"])
            saves.assert_called_once()
        self.assertEqual(set(app.db["events"]), {self.row["review_id"]})
        self.assertNotEqual(worker_ids, [threading.get_ident()])
        self.assertEqual(ui_ids, [threading.get_ident()])
        self.assertEqual(app.last_save_timings["cooldown_seconds"], 0.5)

    def test_write_failure_leaves_current_memory_disk_and_reopen_unchanged(self):
        before = copy.deepcopy(self.app.db)
        raw = (self.output / "人工判定資料庫.json").read_bytes()
        with patch.object(sp, "json_save", side_effect=OSError("disk full")), \
                patch.object(gui.messagebox, "showerror") as error:
            self.app.save_event(self.row, self.event)
            wait_for_save(self.app)
        error.assert_called_once()
        self.assertFalse(self.app._last_event_saved)
        self.assertEqual(self.app.db, before)
        self.assertEqual(self.app.current(), self.row)
        self.assertEqual((self.output / "人工判定資料庫.json").read_bytes(), raw)
        self.assertEqual(headless_app(self.output).db, before)

    def test_post_save_queue_failure_is_saved_and_recoverable_after_reopen(self):
        with patch.object(gui, "prepare_review_queue", side_effect=RuntimeError("queue failure")), \
                patch.object(gui.messagebox, "showwarning") as warning:
            self.app.save_event(self.row, self.event)
            wait_for_save(self.app)
        warning.assert_called_once()
        self.assertIn("已保存", warning.call_args.args[0])
        self.assertTrue(self.app._save_recovery_required)
        self.assertTrue(self.app._last_event_saved)
        self.assertFalse(self.app._last_event_refreshed)
        self.app.next()
        self.assertEqual(self.app.current(), self.row)
        reopened = headless_app(self.output)
        self.assertEqual(reopened.db["events"][self.row["review_id"]], self.event)
        self.assertNotIn(self.row["review_id"], [item["review_id"] for item in reopened.records])

    def test_project_generation_and_review_id_reject_stale_saved_result(self):
        for change in ("generation", "review_id"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as td:
                output = Path(td)
                create_staged_navigation_fixture(output)
                app = headless_app(output)
                row = app.current()
                event = sp.build_manual_expected_event(row, operation="CONFIRM_CURRENT_AS_EXPECTED")
                before = app.db
                app.save_event(row, event)
                app._save_worker.join(timeout=10)
                self.assertFalse(app._save_worker.is_alive())
                if change == "generation":
                    app._project_generation += 1
                else:
                    app.index += 1
                selected = app.current()
                with patch.object(gui.messagebox, "showwarning") as warning:
                    wait_for_save(app)
                warning.assert_called_once()
                self.assertIs(app.db, before)
                self.assertIs(app.current(), selected)
                app.show.assert_not_called()
                self.assertTrue(app._save_recovery_required)
                self.assertEqual(headless_app(output).db["events"][row["review_id"]], event)

    def test_missing_or_wrong_preview_never_starts_shortcut(self):
        for rendered in (None, "different-review-id"):
            self.app._rendered_review_id = rendered
            with patch.object(self.app, "save_event") as save:
                self.app.confirm_current_expected(self.row["review_id"])
            save.assert_not_called()
        self.app._rendered_review_id = self.row["review_id"]
        self.app.primary = Mock()
        self.app._preview_failed()
        with patch.object(self.app, "save_event") as save:
            self.app.confirm_current_expected(self.row["review_id"])
        save.assert_not_called()

    def test_async_undo_uses_saved_state_and_reopens_without_the_event(self):
        event = sp.build_manual_expected_event(self.row, operation="ENTER_EXPECTED", expected_set="ㄎㄢ")
        self.app.save_event(self.row, event)
        wait_for_save(self.app)
        self.app.index = next(i for i, row in enumerate(self.app.records)
                              if row["review_id"] == self.row["review_id"])
        self.app._review_action_cooldown_until = 0
        with patch.object(gui.messagebox, "askyesno", return_value=True):
            self.app.clear()
            wait_for_save(self.app)
        self.assertNotIn(self.row["review_id"], self.app.db["events"])
        reopened = headless_app(self.output)
        self.assertNotIn(self.row["review_id"], reopened.db["events"])
        restored = next(row for row in reopened.records if row["review_id"] == self.row["review_id"])
        self.assertEqual(restored, self.row)

    def test_evidence_rejection_survives_retry_reload_and_defer_revisit(self):
        for kind in ("actual", "rules"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as td:
                output = Path(td)
                create_staged_navigation_fixture(output)
                rules = output / "isolated-rules.json"
                sp.json_save(rules, {"schema_version": "1.0", "rules": []})
                with patch.object(sp, "REUSABLE_EXPECTED_RULES", rules):
                    app = headless_app(output)
                    row = app.current()
                    app.save_event(row, sp.build_manual_expected_event(row, operation="CONFIRM_CURRENT_AS_EXPECTED"))
                    wait_for_save(app)
                    target = app.current()
                    event = sp.build_manual_expected_event(target, operation="ENTER_EXPECTED", expected_set="ㄎㄢˋ")
                    anchor = app._save_service.evidence_anchor
                    self.assertIsNotNone(anchor)
                    path = rules if kind == "rules" else sp.project_actual_evidence_root(output) / sp.GLYPH_CONFLICT_FILE
                    old = path.read_bytes() if path.exists() else None
                    path.write_bytes((old or b"") + (b"\n" if kind == "rules" else b"\nexternal change"))
                    changed = path.read_bytes()
                    db_before = (output / "人工判定資料庫.json").read_bytes()
                    session_before = (output / "校對工作階段.json").read_bytes()
                    for route in ("first_rejection", "retry", "reload", "defer_revisit"):
                        with self.subTest(route=route):
                            if route == "reload":
                                app.reload_records()
                            elif route == "defer_revisit":
                                app.defer_current()
                                app.revisit_deferred()
                            app.index = next(i for i, row in enumerate(app.records)
                                             if row["review_id"] == target["review_id"])
                            selected = app.current()
                            app.show.reset_mock()
                            with patch.object(gui.messagebox, "showerror") as error:
                                app.save_event(selected, event)
                                wait_for_save(app)
                            error.assert_called_once()
                            self.assertIn("actual 證據或應標規則", error.call_args.args[1])
                            self.assertFalse(app._last_event_saved)
                            self.assertEqual(app.current(), selected)
                            app.show.assert_not_called()
                            self.assertIs(app._save_service.evidence_anchor, anchor)
                            self.assertEqual((output / "人工判定資料庫.json").read_bytes(), db_before)
                            self.assertEqual((output / "校對工作階段.json").read_bytes(), session_before)
                            self.assertEqual(path.read_bytes(), changed)
                    if old is None:
                        path.unlink()
                    else:
                        path.write_bytes(old)
                    app.reload_records()
                    app.index = next(i for i, row in enumerate(app.records)
                                     if row["review_id"] == target["review_id"])
                    app.save_event(app.current(), event)
                    wait_for_save(app)
                    self.assertEqual(app.db["events"][target["review_id"]], event)

    def test_ordinary_write_failure_can_retry_with_same_evidence_anchor(self):
        self.app.save_event(self.row, self.event)
        wait_for_save(self.app)
        anchor = self.app._save_service.evidence_anchor
        row = self.app.current()
        event = sp.build_manual_expected_event(row, operation="CONFIRM_CURRENT_AS_EXPECTED")
        before = (self.output / "人工判定資料庫.json").read_bytes()
        with patch.object(sp.os, "fsync", side_effect=OSError("isolated disk failure")), \
                patch.object(gui.messagebox, "showerror") as error:
            self.app.save_event(row, event)
            wait_for_save(self.app)
        error.assert_called_once()
        self.assertEqual((self.output / "人工判定資料庫.json").read_bytes(), before)
        self.assertEqual(self.app._save_service.evidence_anchor, anchor)
        self.app.save_event(row, event)
        wait_for_save(self.app)
        self.assertEqual(self.app.db["events"][row["review_id"]], event)

    def test_real_actual_transaction_refresh_allows_verified_new_snapshot(self):
        self.app.save_event(self.row, self.event)
        wait_for_save(self.app)
        old_anchor = self.app._save_service.evidence_anchor
        row = self.app.current()
        sp.stage_manual_actual_correction(self.output, row["review_id"], "ㄎㄢ")
        with patch.object(sp, "refresh_actual_project", side_effect=controlled_refresh):
            sp.apply_staged_manual_actual_corrections(self.output)
        self.app.manifest = sp.json_load_strict(self.output / "校對工作階段.json")
        self.app.db = sp.load_or_initialize_db(self.output)
        self.app.reload_records()
        self.assertIs(self.app._save_service.evidence_anchor, old_anchor)
        self.app.index = next(i for i, item in enumerate(self.app.records)
                              if item["review_id"] == row["review_id"])
        refreshed = self.app.current()
        self.assertEqual(refreshed["actual"], "ㄎㄢ")
        event = sp.build_manual_expected_event(refreshed, operation="ENTER_EXPECTED", expected_set="ㄎㄢ")
        self.app.save_event(refreshed, event)
        wait_for_save(self.app)
        self.assertTrue(self.app._last_event_saved)
        self.assertNotEqual(self.app._save_service.evidence_anchor, old_anchor)
        self.assertEqual(self.app.db["events"][self.row["review_id"]], self.event)


class AsyncOwnerTkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.output = Path(self.temp.name)
        create_staged_navigation_fixture(self.output)
        self.window = tk.Toplevel(self.root)
        self.app = gui.ReviewApp(self.window, self.output)
        self.window.update()
        self.interpreter = self.root.tk
        self.old_bgerror = self.interpreter.call("info", "procs", "bgerror")
        if self.old_bgerror:
            self.interpreter.call("rename", "bgerror", "_async_saved_bgerror")
        self.interpreter.eval("set ::async_bgerrors {}; proc bgerror {message} {lappend ::async_bgerrors $message}")

    def tearDown(self):
        if self.window.winfo_exists():
            self.window.destroy()
        self.root.update()
        errors = self.interpreter.splitlist(self.interpreter.getvar("async_bgerrors"))
        self.interpreter.call("rename", "bgerror", "")
        if self.old_bgerror:
            self.interpreter.call("rename", "_async_saved_bgerror", "bgerror")
        self.temp.cleanup()
        self.assertEqual(errors, ())

    def test_disposed_save_owner_cancels_only_owned_poll_and_saved_work_reopens(self):
        app = self.app
        row = app.current()
        entered, release = threading.Event(), threading.Event()
        save = app._save_service.save_event

        def held_save(*args, **kwargs):
            entered.set()
            if not release.wait(10):
                raise RuntimeError("test did not release worker")
            return save(*args, **kwargs)

        with patch.object(app._save_service, "save_event", side_effect=held_save), \
                patch.object(gui.messagebox, "showwarning") as warning, \
                patch.object(gui.messagebox, "showerror") as error:
            app.primary.invoke()
            self.assertTrue(entered.wait(5))
            timers = set(app._async_after_ids)
            self.assertTrue(timers)
            seen = []
            unrelated = self.root.after_idle(lambda: seen.append("unrelated"))
            try:
                self.window.destroy()
                pending = set(self.interpreter.splitlist(self.interpreter.call("after", "info")))
                self.assertFalse(timers & pending)
                self.assertIn(unrelated, pending)
                self.assertTrue(app._async_disposed)
                self.assertIsNone(app._save_request)
            finally:
                release.set()
                app._save_worker.join(timeout=15)
            self.assertFalse(app._save_worker.is_alive())
            self.root.update()
            self.assertEqual(seen, ["unrelated"])
            warning.assert_not_called()
            error.assert_not_called()
        reopened = headless_app(self.output)
        self.assertIn(row["review_id"], reopened.db["events"])
        self.assertNotIn(row["review_id"], [item["review_id"] for item in reopened.records])

    def test_actual_poll_owner_disposal_cancels_pending_timer(self):
        app = self.app
        # A held worker exercises native owner destruction without altering
        # transaction truth. The existing transaction suite covers persistence.
        with patch.object(gui.threading, "Thread"):
            app._start_staged_actual_apply(1)
        timers = set(app._async_after_ids)
        self.assertTrue(timers)
        self.window.destroy()
        self.assertFalse(timers & set(self.interpreter.splitlist(self.interpreter.call("after", "info"))))
        self.root.update()

    def test_actual_success_and_failure_release_owned_poll(self):
        app = self.app
        for failure in (False, True):
            with self.subTest(failure=failure), \
                    patch.object(gui, "apply_staged_manual_actual_corrections", return_value={},
                                 side_effect=OSError("isolated actual failure") if failure else None), \
                    patch.object(gui.messagebox, "showerror") as error, \
                    patch.object(gui.messagebox, "showinfo") as info:
                app._apply_in_progress = True
                app._start_staged_actual_apply(0)
                deadline = time.monotonic() + 10
                while app._apply_in_progress:
                    self.assertLess(time.monotonic(), deadline)
                    self.root.update()
                    time.sleep(.001)
                self.assertFalse(app._async_after_ids)
                self.assertEqual(error.call_count, int(failure))
                self.assertEqual(info.call_count, int(not failure))

    def test_save_completion_and_failure_release_owned_poll(self):
        app = self.app
        row = app.current()
        app.save_event(row, sp.build_manual_expected_event(row, operation="CONFIRM_CURRENT_AS_EXPECTED"))
        wait_for_save(app)
        self.assertFalse(app._async_after_ids)
        row = app.current()
        with patch.object(sp.os, "fsync", side_effect=OSError("isolated disk failure")), \
                patch.object(gui.messagebox, "showerror") as error:
            app.save_event(row, sp.build_manual_expected_event(row, operation="CONFIRM_CURRENT_AS_EXPECTED"))
            wait_for_save(app)
        error.assert_called_once()
        self.assertFalse(app._async_after_ids)


if __name__ == "__main__":
    unittest.main()
