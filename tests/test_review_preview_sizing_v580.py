from __future__ import annotations

import copy
from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch

import fitz

import review_display as display
import review_gui as gui
from test_review_visual_usability_v580 import create_visual_fixture
from test_review_visual_corrective_v580 import descendants


def settle(window):
    """Let the real resize debounce run, including any resulting geometry events."""
    until = time.monotonic() + 5
    while time.monotonic() < until:
        window.update()
        previews = [widget for widget in descendants(window) if isinstance(widget, display.OccurrencePreview)]
        if not any(widget._draw_pending or widget._render_pending or widget._locate_pending for widget in previews):
            return
        time.sleep(.01)
    raise AssertionError("preview layout/render callbacks did not settle")


def make_page(folder, *, rotation=0):
    path = folder / f"sizing-{rotation}.pdf"
    with fitz.open() as doc:
        page = doc.new_page(width=300, height=600)
        page.draw_rect(fitz.Rect(80, 290, 110, 320), color=(0, 0, 0), fill=(0, 0, 0))
        # Fine, separated strokes reveal whether the original PDF is rerendered.
        for x in (122, 123, 124, 125):
            page.draw_line((x, 292), (x, 318), color=(0, 0, 0), width=.2)
        page.set_rotation(rotation)
        doc.save(path)
    return {"pdf": str(path), "pdf_name": path.name, "physical_page": 1,
            "x0": 80, "y0": 290, "x1": 130, "y1": 320,
            "occurrence_id": "sizing-A", "review_id": "review-A"}


class WidthRenderTests(unittest.TestCase):
    def test_width_render_preserves_crop_rotation_pixels_and_input(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            # Known rotated rectangles for a 300x600 page and crop
            # [50,230,160,380]. These do not call the production transform.
            crops = {0: (50, 230, 160, 380), 90: (220, 50, 370, 160),
                     180: (140, 220, 250, 370), 270: (230, 140, 380, 250)}
            boxes = {0: (80, 290, 130, 320), 90: (280, 80, 310, 130),
                     180: (170, 280, 220, 310), 270: (290, 170, 320, 220)}
            for rotation in crops:
                entry = make_page(folder, rotation=rotation)
                before = copy.deepcopy(entry), Path(entry["pdf"]).read_bytes()
                for width in (247, 1193):
                    pix, target, _ = display.occurrence_preview(entry, padding=(30, 60), display_width=width)
                    cx0, cy0, cx1, cy1 = crops[rotation]
                    scale = width / (cx1 - cx0)
                    self.assertLessEqual(abs(pix.width - width), 2)
                    self.assertAlmostEqual(pix.height / pix.width, (cy1 - cy0) / (cx1 - cx0), delta=.012)
                    expected_target = tuple(value * scale - origin for value, origin in
                                            zip(boxes[rotation], (pix.x, pix.y, pix.x, pix.y)))
                    for actual, expected in zip(target, expected_target):
                        self.assertAlmostEqual(actual, expected, places=4)
                    with fitz.open(entry["pdf"]) as doc:
                        raw = doc[0].get_pixmap(matrix=fitz.Matrix(scale, scale), clip=fitz.Rect(crops[rotation]), alpha=False)
                    self.assertEqual(pix.samples, raw.samples)
                self.assertEqual((entry, Path(entry["pdf"]).read_bytes()), before)

    def test_invalid_resolution_and_resource_limit_fail_before_allocation(self):
        with tempfile.TemporaryDirectory() as directory:
            entry = make_page(Path(directory))
            with patch.object(fitz.Page, "get_pixmap", side_effect=AssertionError("must not allocate")):
                for width in (0, -10, float("nan"), float("inf"), 10000):
                    with self.subTest(width=width), self.assertRaises(ValueError):
                        display.occurrence_preview(entry, display_width=width)


class PreviewSizingTkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.folder_context = tempfile.TemporaryDirectory()
        self.folder = Path(self.folder_context.name)
        self.window = tk.Toplevel(self.root)
        self.window.geometry("1180x800+0+0")
        self.errors = []
        self.previous_handler = self.root.report_callback_exception
        self.root.report_callback_exception = lambda *args: self.errors.append(args)

    def tearDown(self):
        self.window.destroy()
        self.root.update()
        self.root.report_callback_exception = self.previous_handler
        self.folder_context.cleanup()
        self.assertEqual(self.errors, [])

    def assert_target_visible(self, preview, outer=None):
        box = preview.canvas.coords(preview.canvas.find_withtag("target")[-1])
        top = preview.canvas.winfo_rooty() + box[1] - preview.canvas.canvasy(0)
        bottom = preview.canvas.winfo_rooty() + box[3] - preview.canvas.canvasy(0)
        viewport = outer or preview.canvas
        self.assertGreaterEqual(top, viewport.winfo_rooty() - 1)
        self.assertLessEqual(bottom, viewport.winfo_rooty() + viewport.winfo_height() + 1)

    def test_main_fills_remaining_space_width_and_switch_reveals_target(self):
        create_visual_fixture(self.folder)
        app = gui.ReviewApp(self.window, self.folder)
        self.window.geometry("1440x960+0+0")
        settle(self.window)
        self.assertGreater(app.image.canvas.winfo_height(), 260)
        self.assertGreater(app.image.photo.width(), self.window.winfo_width() * .9)
        self.assertLessEqual(abs(app.image.photo.width() - (app.image.canvas.winfo_width() - 16)), 1)
        # The preview uses the rest of the body, ending just above the footer.
        body_bottom = app.body_canvas.winfo_rooty() + app.body_canvas.winfo_height()
        self.assertAlmostEqual(app.image.winfo_rooty() + app.image.winfo_height(), body_bottom - 6, delta=2)
        self.assert_target_visible(app.image, app.body_canvas)
        for size in ("760x520", "1180x800", "1440x960"):
            self.window.geometry(size)
            settle(self.window)
            app.image.canvas.yview_moveto(1)
            app.next()
            settle(self.window)
            self.assert_target_visible(app.image, app.body_canvas)
            self.assertLessEqual(app.primary.winfo_rooty() + app.primary.winfo_height(),
                                 self.window.winfo_rooty() + self.window.winfo_height())

    def test_pdf_rerenders_after_resize_manual_scroll_stays_and_events_converge(self):
        preview = display.OccurrencePreview(self.window)
        preview.pack(fill="both", expand=True)
        entry = make_page(self.folder)
        self.window.geometry("650x300")
        self.window.update()
        with patch.object(display, "occurrence_preview", wraps=display.occurrence_preview) as render:
            self.assertTrue(preview.load(entry, padding=(30, 180)))
            settle(self.window)
            self.assert_target_visible(preview)
            initial_width = preview.pixmap.width
            preview.canvas.yview_moveto(.12)
            first = preview.canvas.yview()[0]
            for width in (700, 750, 850, 980, 1100):
                self.window.geometry(f"{width}x300")
                self.window.update()
            settle(self.window)
            self.assertLessEqual(render.call_count, 3)
            self.assertGreater(preview.pixmap.width, initial_width * 1.5)
            self.assertLessEqual(abs(preview.pixmap.width - preview.photo.width()), 2)
            self.assertAlmostEqual(preview.canvas.yview()[0], first, delta=.025)
            stable = (preview.winfo_height(), preview.canvas.yview(), render.call_count)
            settle(self.window)
            self.assertEqual(stable, (preview.winfo_height(), preview.canvas.yview(), render.call_count))
            # Subsequent rerenders use the immutable display snapshot.
            entry["physical_page"] = 99
            self.window.geometry("1040x300")
            settle(self.window)
            self.assertIsNotNone(preview.pixmap)

    def test_long_summary_can_scroll_then_returns_remaining_space_to_preview(self):
        create_visual_fixture(self.folder)
        app = gui.ReviewApp(self.window, self.folder)
        self.window.geometry("760x520")
        settle(self.window)
        source = app.current()["source_record"]
        original = source["所在行"]
        source["所在行"] = "這是隔離測試的長句，所有內容都必須可以查看。" * 30
        app.show()
        settle(self.window)
        self.assertGreater(app.body_canvas.bbox("all")[3], app.body_canvas.winfo_height())
        self.assert_target_visible(app.image, app.body_canvas)
        app.body_canvas.yview_moveto(0)
        self.window.update()
        self.assertGreaterEqual(app.summary.winfo_rooty(), app.body_canvas.winfo_rooty())
        source["所在行"] = original
        self.window.geometry("1440x960")
        app.show()
        settle(self.window)
        self.assertGreater(app.image.canvas.winfo_height(), 260)
        self.assertEqual(app.body_canvas.yview(), (0.0, 1.0))

    def test_inner_wheel_consumes_once_and_boundary_hands_off_to_long_body(self):
        body, outer = gui.create_scrollable_body(self.window)
        tk.Frame(body, height=300).pack(fill="x")
        preview = display.OccurrencePreview(body, height=180)
        preview.pack(fill="x")
        tk.Frame(body, height=900).pack(fill="x")
        preview.load(make_page(self.folder), padding=(30, 180))
        self.window.geometry("800x500")
        settle(self.window)
        outer.yview_moveto(.1)
        preview.canvas.yview_moveto(.1)
        before_outer, before_inner = outer.yview(), preview.canvas.yview()
        preview.canvas.event_generate("<MouseWheel>", delta=-120)
        self.window.update()
        self.assertEqual(outer.yview(), before_outer)
        self.assertGreater(preview.canvas.yview()[0], before_inner[0])
        preview.canvas.yview_moveto(1)
        preview.canvas.event_generate("<MouseWheel>", delta=-120)
        self.window.update()
        self.assertGreater(outer.yview()[0], before_outer[0])

    def test_manual_scroll_cancels_pending_initial_target_placement(self):
        preview = display.OccurrencePreview(self.window)
        preview.pack(fill="both", expand=True)
        self.window.geometry("800x300")
        self.window.update()
        for use_scrollbar in (False, True):
            preview.load(make_page(self.folder), padding=(30, 180))
            self.assertIsNotNone(preview._locate_pending)
            if use_scrollbar:
                # Execute the actual registered scrollbar callback.
                preview.tk.call(preview.scrollbar.cget("command"), "moveto", .1)
            else:
                preview.canvas.event_generate("<MouseWheel>", delta=-120)
            self.assertIsNone(preview._locate_pending)
            fraction = preview.canvas.yview()[0]
            settle(self.window)
            self.assertAlmostEqual(preview.canvas.yview()[0], fraction, delta=.01)

    def test_actual_expanded_samples_first_target_and_form_reachable(self):
        first = make_page(self.folder)
        second = {**first, "occurrence_id": "sizing-B", "review_id": "review-B", "y0": 410, "y1": 440}
        dialog = gui.ActualReadingDialog(self.window, first, {"members": [first, second]}, self.folder, wait=False)
        try:
            dialog.geometry("1180x800")
            settle(dialog)
            self.assert_target_visible(dialog.previews[0], dialog.body_canvas)
            for preview in dialog.previews:
                self.assertGreater(preview.photo.width(), 1000)
                self.assertGreater(preview.canvas.winfo_height(), 220)
                self.assertEqual(preview.canvas.yview(), (0.0, 1.0))
            first_scroll = dialog.body_canvas.yview()
            dialog.previews[0].canvas.event_generate("<MouseWheel>", delta=-120)
            dialog.update()
            self.assertGreater(dialog.body_canvas.yview()[0], first_scroll[0])
            # Every sample's complete image can be reached through the one form.
            preview = dialog.previews[1]
            box = preview.canvas.coords(preview.canvas.find_withtag("target")[-1])
            dialog.body_canvas.reveal(preview.canvas, box[1], box[3])
            dialog.update()
            self.assert_target_visible(preview, dialog.body_canvas)
            for size in ("720x520", "1180x800"):
                dialog.geometry(size)
                settle(dialog)
                dialog.body_canvas.yview_moveto(1)
                dialog.update()
                note = next(widget for widget in descendants(dialog)
                            if isinstance(widget, tk.Entry) and widget.cget("textvariable") == str(dialog.note))
                self.assertGreaterEqual(note.winfo_rooty(), dialog.body_canvas.winfo_rooty())
                self.assertLessEqual(note.winfo_rooty() + note.winfo_height(),
                                     dialog.body_canvas.winfo_rooty() + dialog.body_canvas.winfo_height())
                before = dialog.body_canvas.yview()[0]
                note.event_generate("<MouseWheel>", delta=120)
                dialog.update()
                self.assertLess(dialog.body_canvas.yview()[0], before)
        finally:
            dialog.destroy()

    def test_resize_read_failure_clears_and_revokes_confirmation(self):
        manifest = create_visual_fixture(self.folder)
        app = gui.ReviewApp(self.window, self.folder)
        settle(self.window)
        with patch.object(display, "occurrence_preview", side_effect=OSError("isolated resize failure")):
            self.window.geometry("1000x700")
            settle(self.window)
        self.assertIsNone(app._rendered_review_id)
        self.assertIsNone(app.image.photo)
        self.assertEqual(app.image.canvas.find_all(), ())
        self.assertEqual(app.primary.cget("state"), "disabled")
        entry = manifest["records"][0]
        dialog = gui.ActualReadingDialog(self.window, entry, {"members": [entry]}, self.folder, wait=False)
        try:
            settle(dialog)
            with patch.object(display, "occurrence_preview", side_effect=OSError("isolated sample failure")):
                dialog.geometry("800x600")
                settle(dialog)
            self.assertEqual(dialog.sample_available, [False])
            self.assertFalse(dialog.checked_vars[0].get())
            dialog.checked_vars[0].set(True)
            dialog.reading.set("ㄅ")
            with patch.object(gui.messagebox, "showerror"):
                dialog.submit()
            self.assertIsNone(dialog.result)
        finally:
            dialog.destroy()

    def test_all_owned_resize_and_locate_callbacks_cancel_on_destruction(self):
        for target in ("frame", "canvas", "parent"):
            parent = tk.Frame(self.window)
            parent.pack(fill="both", expand=True)
            preview = display.OccurrencePreview(parent)
            preview.pack(fill="both", expand=True)
            self.window.update()
            preview.load(make_page(self.folder))
            preview._render_width = None
            preview._draw()
            preview._schedule_draw()
            owned = [preview._draw_pending, preview._render_pending, preview._locate_pending]
            self.assertTrue(all(owned))
            {"frame": preview, "canvas": preview.canvas, "parent": parent}[target].destroy()
            pending = self.root.tk.splitlist(self.root.tk.call("after", "info"))
            self.assertFalse(set(owned) & set(pending))
            settle(self.window)
            if parent.winfo_exists():
                parent.destroy()


if __name__ == "__main__":
    unittest.main()
