from __future__ import annotations

import copy
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import review_gui as gui
import standalone_proofread as sp
from tests.review_save_test_support import wait_for_save
from tests.test_staged_review_navigation_v580 import create_staged_navigation_fixture, headless_app


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


if __name__ == "__main__":
    unittest.main()
