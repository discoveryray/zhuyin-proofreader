from __future__ import annotations

import json
from pathlib import Path
import tempfile
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
import unittest
from unittest.mock import MagicMock, patch

import occurrence_ledger as ol
import review_display as display
import review_gui as gui
import standalone_gui as standalone
from test_manual_review_usability_v580 import REGRESSION
from test_review_visual_usability_v580 import create_visual_fixture


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def open_progress_demo(root, count=10):
    """Production constructor, held worker; UI-only demo, no actual service call."""
    app = gui.ReviewApp.__new__(gui.ReviewApp)
    app.root = root
    app.output_dir = Path("ui-only-held-progress-project")
    app.index = 0
    app.reload_staging_summary = MagicMock(return_value={"staged_group_count": count})
    with (patch.object(gui.messagebox, "askyesno", return_value=True),
          patch.object(gui.messagebox, "showerror") as error,
          patch.object(gui.threading, "Thread") as thread,
          patch.object(gui, "apply_staged_manual_actual_corrections") as service):
        app.apply_staged_actuals()
        error.assert_not_called()
        thread.return_value.start.assert_called_once()
        service.assert_not_called()
    progress = next(widget for widget in root.winfo_children()
                    if isinstance(widget, tk.Toplevel) and widget.title() == "批次套用 actual")
    bar = next(widget for widget in descendants(progress) if isinstance(widget, ttk.Progressbar))
    return progress, bar


class ProgressFilesystemRegressionTests(unittest.TestCase):
    def test_retained_complete_snapshot_requires_session_file_and_keeps_raw_gate(self):
        complete_row = {"state": "PASS", "actual": "ㄅ", "actual_evidence": "independent actual",
                        "expected_set": ["ㄅ"], "expected_evidence": "independent expected", "context_evidence": "context"}
        gate = ol.completion_gate([complete_row], {"ok": True}, REGRESSION)
        self.assertTrue(gate["complete"])
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            status = folder / "pipeline_status.json"
            session = folder / "校對工作階段.json"
            original = json.dumps({"status": gate["status"], "completion_gate": gate}, ensure_ascii=False).encode("utf-8")
            status.write_bytes(original)
            summary, diagnostics = standalone.project_status_details(folder)
            self.assertIn("目前無法確認進度", summary)
            self.assertNotIn("全冊校對完成", summary)
            self.assertIn("缺少工作階段檔案", diagnostics)
            self.assertIn(str(session), diagnostics)
            self.assertIn('"complete": true', diagnostics)
            self.assertEqual(status.read_bytes(), original)
            self.assertFalse(session.exists())
            # An existing directory with the session filename is not a file.
            session.mkdir()
            self.assertIn("目前無法確認進度", standalone.project_status_text(folder))
            session.rmdir()
            # Presence is the presentation precondition; manifest validation
            # remains the existing project service's responsibility.
            session.write_text("{}", encoding="utf-8")
            self.assertIn("全冊校對完成", standalone.project_status_text(folder))
            status.unlink()
            self.assertIn("目前無法確認進度", standalone.project_status_text(folder))
            session.unlink()
            self.assertIn("新的校對專案資料夾", standalone.project_status_text(folder))
            for payload, expected in (({"status": "PROCESSING", "phase": "2/3"}, "首次分析中"),
                                      ({"status": "PIPELINE_BLOCKED", "reason": "raw diagnostic"}, "處理被阻擋")):
                status.write_text(json.dumps(payload), encoding="utf-8")
                self.assertIn(expected, standalone.project_status_text(folder))


class CorrectiveTkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.callback_errors = []
        self._previous_callback_handler = self.root.report_callback_exception
        self.root.report_callback_exception = lambda *args: self.callback_errors.append(args)
        self.window = tk.Toplevel(self.root)

    def tearDown(self):
        self.window.destroy()
        self.root.report_callback_exception = self._previous_callback_handler
        self.assertEqual(self.callback_errors, [])

    def assert_converged(self, dialog, labels):
        events = []
        for label in labels:
            label.bind("<Configure>", lambda event: events.append((event.width, event.height)), add="+")
        dialog.update()
        self.assertLess(len(events), 50, "layout must converge without repeated shrink passes")
        shape = [(label.winfo_width(), label.winfo_height(), str(label.cget("wraplength"))) for label in labels]
        settled_count = len(events)
        for _ in range(3):
            dialog.update_idletasks()
        self.assertEqual(shape, [(label.winfo_width(), label.winfo_height(), str(label.cget("wraplength"))) for label in labels])
        self.assertEqual(len(events), settled_count)

    def assert_short_label_readable(self, label, *, max_lines=2):
        font = tkfont.Font(font=label.cget("font"))
        # Independent text metrics, not the wrapping helper's own calculation.
        self.assertGreaterEqual(label.winfo_width(), font.measure("應標注音") + 6)
        self.assertLessEqual(label.winfo_height(), font.metrics("linespace") * max_lines + 6)

    def test_real_expected_and_actual_form_labels_converge_at_narrow_and_wide_sizes(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            manifest = create_visual_fixture(folder)
            entry = manifest["records"][0]
            expected = gui.ExpectedDialog(self.window, entry, "應標判定", wait=False)
            try:
                labels = [widget for widget in descendants(expected)
                          if isinstance(widget, display.WrappedLabel) and widget.cget("text") in {"應標注音", "依據（選填）", "詞語與位置", "補充說明（可空白）"}]
                self.assertEqual(len(labels), 4)
                for size in ("600x440", "760x660", "1180x800", "600x440"):
                    expected.geometry(size)
                    self.assert_converged(expected, labels)
                    for label in labels:
                        self.assert_short_label_readable(label)
                expected.body_canvas.yview_moveto(1)
                expected.update()
                note = next(widget for widget in descendants(expected)
                            if isinstance(widget, tk.Entry) and widget.cget("textvariable") == str(expected.reason))
                self.assertGreaterEqual(note.winfo_rooty(), expected.body_canvas.winfo_rooty())
                self.assertLessEqual(note.winfo_rooty()+note.winfo_height(), expected.body_canvas.winfo_rooty()+expected.body_canvas.winfo_height())
            finally:
                expected.destroy()
            actual = gui.ActualReadingDialog(self.window, entry, {"members": [entry]}, folder, wait=False)
            try:
                labels = [widget for widget in descendants(actual)
                          if isinstance(widget, display.WrappedLabel) and
                          (str(widget.cget("text")).startswith("程式目前 actual：") or widget.cget("text") in {"原頁真正 actual：", "備註（可空白）："})]
                self.assertEqual(len(labels), 3)
                for size in ("720x520", "980x760", "1180x800", "720x520"):
                    actual.geometry(size)
                    self.assert_converged(actual, labels)
                    for label in labels:
                        self.assert_short_label_readable(label)
                body_canvas = next(widget for widget in descendants(actual) if isinstance(widget, tk.Canvas)
                                   and any(widget.type(item) == "window" for item in widget.find_all()))
                body_canvas.yview_moveto(1)
                actual.update()
                note = next(widget for widget in descendants(actual)
                            if isinstance(widget, tk.Entry) and widget.cget("textvariable") == str(actual.note))
                self.assertGreaterEqual(note.winfo_rooty(), body_canvas.winfo_rooty())
                self.assertLessEqual(note.winfo_rooty()+note.winfo_height(), body_canvas.winfo_rooty()+body_canvas.winfo_height())
            finally:
                actual.destroy()

    def test_actual_apply_progress_constructor_keeps_bar_visible_and_text_readable(self):
        progress, bar = open_progress_demo(self.window)
        try:
            labels = [widget for widget in descendants(progress) if isinstance(widget, display.WrappedLabel)]
            self.assertEqual(len(labels), 2)
            for size in ("560x210", "440x170", "800x260", "560x210"):
                progress.geometry(size)
                self.assert_converged(progress, labels)
                for label in labels:
                    self.assert_short_label_readable(label)
                    self.assertGreaterEqual(label.winfo_rooty(), progress.winfo_rooty())
                    self.assertLessEqual(label.winfo_rooty()+label.winfo_height(), bar.winfo_rooty())
                self.assertTrue(bar.winfo_viewable())
                self.assertGreater(bar.winfo_width(), 100)
                self.assertLessEqual(bar.winfo_rooty()+bar.winfo_height(), progress.winfo_rooty()+progress.winfo_height())
        finally:
            bar.stop()
            progress.destroy()

    def test_unallocated_wrapped_label_does_not_erode_its_own_width(self):
        label = display.WrappedLabel(self.window, text="應標注音")
        label.pack()
        self.assert_converged(self.window, [label])
        self.assert_short_label_readable(label, max_lines=1)

    def test_owned_idle_callbacks_cancel_on_child_parent_and_canvas_destruction(self):
        interpreter = self.root.tk
        old_bgerror = interpreter.call("info", "procs", "bgerror")
        if old_bgerror:
            interpreter.call("rename", "bgerror", "_visual_saved_bgerror")
        interpreter.eval("set ::visual_bgerrors {}; proc bgerror {message} {lappend ::visual_bgerrors $message}")
        surviving_calls = []
        try:
            for destroy_target in ("child", "parent", "canvas"):
                parent = tk.Frame(self.window)
                rows = display.ActionRows(parent)
                rows.set_items([tk.Button(rows, text="仍可操作")])
                preview = display.OccurrencePreview(parent)
                preview._schedule_draw()
                owned = [rows._scheduled, preview._draw_pending]
                self.assertTrue(all(owned))
                # A synchronous draw must cancel the previously queued ID,
                # rather than only clearing the Python reference to it.
                preview._draw()
                self.assertNotIn(owned[1], interpreter.splitlist(interpreter.call("after", "info")))
                preview._schedule_draw()
                owned.append(preview._draw_pending)
                unrelated = self.root.after_idle(lambda: surviving_calls.append(destroy_target))
                if destroy_target == "child":
                    rows.destroy()
                    preview.destroy()
                elif destroy_target == "parent":
                    parent.destroy()
                else:
                    rows.destroy()
                    preview.canvas.destroy()
                pending = interpreter.splitlist(interpreter.call("after", "info"))
                self.assertFalse(set(owned) & set(pending))
                self.assertIn(unrelated, pending)
                self.root.update()
                self.assertEqual(interpreter.splitlist(interpreter.getvar("visual_bgerrors")), ())
                self.assertEqual(surviving_calls[-1], destroy_target)
                if parent.winfo_exists():
                    parent.destroy()
            button = tk.Button(self.window, text="其他操作", command=lambda: surviving_calls.append("invoked"))
            button.invoke()
            self.assertEqual(surviving_calls[-1], "invoked")
        finally:
            interpreter.call("rename", "bgerror", "")
            if old_bgerror:
                interpreter.call("rename", "_visual_saved_bgerror", "bgerror")
