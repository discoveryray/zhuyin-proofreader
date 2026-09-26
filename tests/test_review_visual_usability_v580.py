from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch, MagicMock

import fitz

import actual_review
import occurrence_ledger as ol
import review_display as display
import review_gui as gui
import standalone_gui as standalone
import standalone_proofread as sp
from tests.review_save_test_support import wait_for_save
from test_manual_review_usability_v580 import make_manifest, REGRESSION


def create_visual_fixture(folder: Path):
    """Synthetic GUI interaction fixture; no real textbook/decoder claim.

    The explicit coordinate roster is also usable for Windows capture. The PDF
    includes repeated characters, and each target's annotation is in its bbox.
    """
    folder.mkdir(parents=True, exist_ok=True)
    pdf = folder / "同頁目標定位與逐筆確認隔離樣本.pdf"
    roster = [("我", 50, 90), ("你", 125, 90), ("我", 200, 90),
              ("我", 50, 220), ("你", 125, 220), ("我", 200, 220)]
    with fitz.open() as doc:
        page = doc.new_page(width=400, height=330)
        page.insert_text((15, 35), "介面測試樣本（非教材）", fontname="china-t", fontsize=15)
        for n, (char, x, y) in enumerate(roster, 1):
            page.insert_text((x, y + 22), char, fontname="china-t", fontsize=22)
            page.insert_text((x + 24, y + 12), "ㄨ", fontname="china-t", fontsize=7)
            page.insert_text((x + 24, y + 21), "ㄛ", fontname="china-t", fontsize=7)
            page.insert_text((x + 30, y + 15), "ˇ", fontname="china-t", fontsize=7)
            page.insert_text((x, y + 45), f"位置 {n}", fontname="china-t", fontsize=10)
        doc.save(pdf)
    sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
    entries = []
    for n, (char, x, y) in enumerate(roster, 1):
        source = {"pdf": str(pdf), "pdf_name": pdf.name, "pdf_sha256": sha,
                  "實體頁碼": 1, "課本頁": "1", "字元": char, "穩定注音鍵": f"visual-{n}",
                  "font": "isolated", "font_xref": 1, "glyph_id_字形索引": n,
                  "x0": x, "y0": y, "x1": x + 37, "y1": y + 27,
                  "source_row_number": n, "所在行": f"各自保存的不同語境 {n}", "局部詞境": f"詞語 {n}"}
        ol.prepare_occurrence_rows(sha, [source])
        classified = {"actual": "ㄨㄛˇ" if n <= 3 else "", "actual_evidence": "explicit isolated actual" if n <= 3 else "",
                      "expected_set": [] if n <= 3 else ["ㄨㄛˇ"],
                      "expected_evidence": "" if n <= 3 else "explicit isolated expected",
                      "context_evidence": f"各自保存的不同語境 {n}", "state": "REVIEW_PENDING"}
        classified["state"] = ol.derive_authoritative_state(classified)
        entries.extend(ol.build_occurrence_ledger([source], {source["occurrence_id"]: classified}))
    manifest = make_manifest(entries)
    sp.json_save(folder / "校對工作階段.json", manifest)
    sp.json_save(folder / "人工判定資料庫.json", sp.normalize_db({}))
    gate = ol.completion_gate(entries, {"ok": True}, REGRESSION)
    sp.json_save(folder / "pipeline_status.json", {"status": gate["status"], "completion_gate": gate})
    return manifest


class PreviewGeometryTests(unittest.TestCase):
    def test_rotated_crop_rounding_and_raw_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            # Independent oracle for [100,80,130,110] on a 400x300 page:
            # rotated bounds at 90 are [190,100,220,130], etc.
            targets = {0: (100, 80, 130, 110), 90: (190, 100, 220, 130),
                       180: (270, 190, 300, 220), 270: (80, 270, 110, 300)}
            crops = {0: (90, 60), 90: (170, 90), 180: (260, 170), 270: (60, 260)}
            for rotation in targets:
                path = folder / f"rot{rotation}.pdf"
                with fitz.open() as doc:
                    page = doc.new_page(width=400, height=300)
                    page.draw_rect(fitz.Rect(100, 80, 130, 110), fill=(0, 0, 0))
                    page.draw_rect(fitz.Rect(220, 80, 250, 110), fill=(0, 0, 0))
                    page.set_rotation(rotation)
                    doc.save(path)
                entry = {"pdf": str(path), "physical_page": 1, "x0": 100, "y0": 80, "x1": 130, "y1": 110,
                         "occurrence_id": "fixed-id", "review_id": "fixed-review", "char": "我"}
                before = path.read_bytes(), copy.deepcopy(entry)
                pix, box, _ = display.occurrence_preview(entry, padding=(10, 20), scale=1.5)
                cx, cy = crops[rotation]
                tx0, ty0, tx1, ty1 = targets[rotation]
                self.assertEqual((pix.x, pix.y), (cx * 1.5, cy * 1.5))
                self.assertEqual(box, ((tx0-cx)*1.5, (ty0-cy)*1.5, (tx1-cx)*1.5, (ty1-cy)*1.5))
                # Unmarked output remains pixel-identical to direct PDF rendering.
                with fitz.open(path) as doc:
                    expected = doc[0].get_pixmap(matrix=fitz.Matrix(1.5, 1.5),
                                                clip=fitz.Rect(cx, cy, cx + (50 if rotation in (0, 180) else 70), cy + (70 if rotation in (0, 180) else 50)), alpha=False)
                    self.assertEqual(pix.samples, expected.samples)
                self.assertEqual((path.read_bytes(), entry), before)

    def test_page_edges_missing_nonfinite_and_nonzero_cropbox(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "crop.pdf"
            with fitz.open() as doc:
                page = doc.new_page(width=500, height=400)
                page.set_cropbox(fitz.Rect(50, 40, 450, 340))
                doc.save(path)
            entry = {"pdf": str(path), "physical_page": 1, "x0": -5, "y0": -3, "x1": 20, "y1": 30}
            _, box, notice = display.occurrence_preview(entry, padding=(10, 10), scale=2)
            self.assertEqual(box, (0, 0, 40, 60))
            self.assertIn("頁邊", notice)
            for bad in ({"x0": None}, {"x0": float("nan")}, {"x1": 0, "x0": 0}, {"x0": 600, "x1": 620}):
                _, box, notice = display.occurrence_preview({**entry, **bad})
                self.assertIsNone(box)
                self.assertIn("無法標示", notice)


class GroupOrderTests(unittest.TestCase):
    def test_fixed_char_order_both_lanes_defer_stage_and_revisit(self):
        def item(n, char, lane):
            return {"occurrence_id": str(n), "char": char, "source_row_number": n,
                    "state": "EXPECTED_UNRESOLVED" if lane == "expected" else "ACTUAL_UNRESOLVED",
                    "expected_status": "UNRESOLVED" if lane == "expected" else "RESOLVED",
                    "actual_status": "RESOLVED" if lane == "expected" else "UNRESOLVED",
                    "context_evidence": str(n), "expected_set": ["ㄅ"] if n % 2 else ["ㄆ"]}
        ledger = [item(1, "我", "expected"), item(2, "你", "expected"), item(3, "我", "expected"),
                  item(4, "我", "actual"), item(5, "你", "actual"), item(6, "我", "actual")]
        app = gui.ReviewApp.__new__(gui.ReviewApp)
        app.manifest, app.records, app.staged_checked_occurrence_ids = {}, [], set()
        app.index = 0
        app._set_actionable_records_from_ledger(ledger)
        self.assertEqual([e["occurrence_id"] for e in app.records], ["1", "3", "2", "4", "6", "5"])
        # Saving the last visible 我 still visits another unresolved 我 before 你.
        app.index = 1
        ledger[2]["state"] = "PASS"
        app._set_actionable_records_from_ledger(ledger)
        self.assertEqual(app.current()["occurrence_id"], "1")
        ledger[2]["state"] = "EXPECTED_UNRESOLVED"
        app._set_actionable_records_from_ledger(ledger)
        ledger[0]["state"] = "PASS"
        app._set_actionable_records_from_ledger(ledger)
        self.assertEqual(app.current()["occurrence_id"], "3")
        self.assertEqual([e["occurrence_id"] for e in app.records], ["3", "2", "4", "6", "5"])
        app.deferred_items = {("expected", "3")}
        app._set_actionable_records_from_ledger(ledger)
        self.assertEqual(app.current()["occurrence_id"], "2")
        app.staged_checked_occurrence_ids = {"3", "4"}
        app.deferred_items = set()
        app.records = []
        app._set_actionable_records_from_ledger(ledger)
        self.assertEqual([e["occurrence_id"] for e in app.records], ["3", "2", "6", "5"])
        self.assertEqual(len({e["occurrence_id"] for e in app.records}), 4)
        # Reopening with a full source roster preserves the first group's order.
        reopened = gui.ReviewApp.__new__(gui.ReviewApp)
        reopened.manifest, reopened.records, reopened.staged_checked_occurrence_ids = {}, [], set()
        reopened.index = 0
        reopened._set_actionable_records_from_ledger(ledger)
        self.assertEqual([e["occurrence_id"] for e in reopened.records][:2], ["3", "2"])


class ProgressPresentationTests(unittest.TestCase):
    def test_overlapping_causes_count_rows_once_and_hide_internal_diagnostics(self):
        payload = {"status": "PROCESSING_FINISHED", "status_text": "RAW_EXCEPTION_SECRET", "completion_gate": {
            "state_counts": {"PASS": 9, "ACTUAL_UNRESOLVED": 2, "EXPECTED_AMBIGUOUS": 3},
            "failed_gates": ["actual_coverage_100", "decode_error_zero", "pending_zero", "all_in_scope_terminal", "expected_coverage_100", "expected_ambiguous_zero"]}}
        text = standalone.summarize_project_status(payload)
        self.assertIn("待處理 5 筆", text)
        self.assertEqual(text.count("目前注音尚未辨識"), 1)
        self.assertEqual(text.count("應標注音尚待確認"), 1)
        self.assertNotIn("RAW_EXCEPTION", text)
        self.assertNotIn("pending_zero", text)

    def test_gate_complete_and_unknown_missing_or_failed_data(self):
        entry = {"state": "PASS", "actual": "ㄅ", "actual_evidence": "independent actual",
                 "expected_set": ["ㄅ"], "expected_evidence": "independent expected", "context_evidence": "context"}
        gate = ol.completion_gate([entry], {"ok": True}, REGRESSION)
        payload = {"status": gate["status"], "completion_gate": gate}
        self.assertIn("全冊校對完成", standalone.summarize_project_status(payload, has_session=True))
        for bad in (None, {}, {"status": "FUTURE"}, {"status": "PROOFREAD_COMPLETE"},
                    {**payload, "completion_gate": {**gate, "complete": False}},
                    {**payload, "completion_gate": {**gate, "state_counts": {"UNKNOWN": 1}}},
                    {**payload, "completion_gate": {**gate, "state_counts": {"PASS": True}}},
                    {**payload, "completion_gate": {**gate, "state_counts": {"PASS": 0}}},
                    {**payload, "completion_gate": {**gate, "state_counts": {"PASS": 2, "REVIEW_PENDING": 1}}},
                    {**payload, "completion_gate": {**gate, "state_counts": {"EXCLUDED_OUT_OF_SCOPE": 3}}},
                    {**payload, "completion_gate": {**gate, "in_scope_total": None}},
                    {**payload, "completion_gate": {**gate, "expected_covered": 0}},
                    {**payload, "completion_gate": {**gate, "hard_gates": {}}}):
            self.assertIn("目前無法確認進度", standalone.summarize_project_status(bad, has_session=True))
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "pipeline_status.json").write_text("{broken raw detail", encoding="utf-8")
            summary, detail = standalone.project_status_details(folder)
            self.assertIn("目前無法確認進度", summary)
            self.assertNotIn("broken", summary)
            self.assertIn("broken", detail)


class VisualGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
        except tk.TclError as exc:
            raise unittest.SkipTest(f"Tk display unavailable: {exc}")

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.manifest = create_visual_fixture(self.folder)
        self.window = tk.Toplevel(self.root)

    def tearDown(self):
        self.window.destroy()
        self.temp.cleanup()

    def test_main_occurrence_switch_failure_resize_and_source_invariance(self):
        before = {path: path.read_bytes() for path in self.folder.iterdir() if path.is_file()}
        entries_before = copy.deepcopy(self.manifest["records"])
        evidence = self.folder / "raw_evidence.png"
        actual_review.render_occurrence_png(self.manifest["records"][0], evidence, context=True)
        raw_before = evidence.read_bytes()
        app = gui.ReviewApp(self.window, self.folder)
        self.window.geometry("760x600")
        self.window.update()
        canvas = app.image.canvas
        first_box = canvas.coords(canvas.find_withtag("target")[-1])
        self.assertEqual(app.current()["source_row_number"], 1)
        # Independent PDF crop [0,0,237,232], target [50,90,87,117].
        # Width-fit can rerender at a new resolution; integer raster rounding
        # accounts for at most a pixel here, independent of viewport height.
        sw, sh = app.image.photo.width(), app.image.photo.height()
        self.assertLessEqual(abs(sw - (canvas.winfo_width() - 16)), 1)
        self.assertAlmostEqual(sh / sw, 232 / 237, delta=.005)
        x, y = (canvas.winfo_width() - sw) / 2, 8
        for actual, expected in zip(first_box, (x+50*sw/237-3, y+90*sh/232-3, x+87*sw/237+3, y+117*sh/232+3)):
            self.assertAlmostEqual(actual, expected, delta=1)
        app.next()
        self.window.update()
        self.assertEqual(app.current()["source_row_number"], 3)
        self.assertNotEqual(first_box, canvas.coords(canvas.find_withtag("target")[-1]))
        app.render({**app.current(), "pdf": str(self.folder / "missing.pdf")})
        self.window.update()
        self.assertEqual(canvas.find_withtag("target"), ())
        self.assertEqual(canvas.find_withtag("page"), ())
        self.assertIsNone(app._rendered_review_id)
        self.assertEqual(app.primary.cget("state"), "disabled")
        app.render({**app.current(), "x0": None})
        self.window.update()
        self.assertEqual(canvas.find_withtag("target"), ())
        self.assertEqual(len(canvas.find_withtag("page")), 1)
        self.assertIsNone(app._rendered_review_id)
        self.assertEqual(app.primary.cget("state"), "disabled")
        app.show()
        self.window.geometry("950x650")
        self.window.update()
        self.assertEqual(len(canvas.find_withtag("target")), 2)
        self.assertEqual(evidence.read_bytes(), raw_before)
        self.assertEqual({path: path.read_bytes() for path in before}, before)
        self.assertEqual(self.manifest["records"], entries_before)

    def test_sample_positions_are_individual_failed_sample_cannot_be_checked(self):
        entries = self.manifest["records"]
        group = {"members": [entries[0], entries[2]], "kind": "OCCURRENCE", "group_id": "ui-only-fixture"}
        dialog = gui.ActualReadingDialog(self.window, entries[0], group, self.folder, wait=False)
        try:
            dialog.update()
            self.assertEqual(len(dialog.previews), 2)
            self.assertNotEqual(dialog.previews[0].target, dialog.previews[1].target)
            self.assertTrue(all(p.canvas.find_withtag("target") for p in dialog.previews))
        finally:
            dialog.destroy()
        bad = {**entries[0], "pdf": str(self.folder / "absent.pdf"), "pdf_name": "absent.pdf"}
        dialog = gui.ActualReadingDialog(self.window, bad, {"members": [bad]}, self.folder, wait=False)
        try:
            self.assertEqual(dialog.sample_available, [False])
            dialog.checked_vars[0].set(True)
            dialog.reading.set("ㄅ")
            with patch.object(gui.messagebox, "showerror"):
                dialog.submit()
            self.assertIsNone(dialog.result)
            self.assertEqual(dialog.previews[0].canvas.find_withtag("target"), ())
        finally:
            dialog.destroy()
        invalid = {**entries[0], "x0": None}
        dialog = gui.ActualReadingDialog(self.window, invalid, {"members": [invalid]}, self.folder, wait=False)
        try:
            self.assertEqual(dialog.sample_available, [False])
            self.assertIsNone(dialog.previews[0].target)
        finally:
            dialog.destroy()

    def test_page_change_and_fractional_resize_clear_previous_frame(self):
        path = self.folder / "two-pages.pdf"
        with fitz.open() as doc:
            for color in ((1, 0, 0), (0, 0, 1)):
                page = doc.new_page(width=200, height=200)
                page.draw_rect(fitz.Rect(70, 70, 100, 100), fill=color, color=color)
            doc.save(path)
        preview = display.OccurrencePreview(self.window)
        preview.pack(fill="both", expand=True)
        entry = {"pdf": str(path), "physical_page": 1, "x0": 70, "y0": 70, "x1": 100, "y1": 100}
        self.window.geometry("333x277")
        self.assertTrue(preview.load(entry))
        self.window.update()
        # The render resolution now follows the viewport. The colored square
        # occupies PDF coordinates 70..100 on this unrotated 200-point page.
        self.assertEqual(preview.pixmap.pixel(int(preview.pixmap.width * .425), int(preview.pixmap.height * .425)), (255, 0, 0))
        self.assertTrue(preview.load({**entry, "physical_page": 2}))
        self.window.update()
        self.assertEqual(preview.pixmap.pixel(int(preview.pixmap.width * .425), int(preview.pixmap.height * .425)), (0, 0, 255))
        self.assertEqual(len(preview.canvas.find_withtag("target")), 2)
        self.assertFalse(preview.load({**entry, "physical_page": 3}))
        self.window.geometry("415x303")
        self.window.update()
        self.assertEqual(preview.canvas.find_all(), ())

    def test_real_save_only_current_row_and_same_character_navigation(self):
        app = gui.ReviewApp(self.window, self.folder)
        self.window.update()
        first, third = app.records[:2]
        app.primary.invoke()
        wait_for_save(app)
        self.assertEqual(set(app.db["events"]), {first["review_id"]})
        self.assertEqual(app.current()["review_id"], third["review_id"])
        event = sp.build_manual_expected_event(app.current(), operation="ENTER_EXPECTED", expected_set="ㄨㄛˋ", rationale="")
        app.save_event(app.current(), event)
        wait_for_save(app)
        self.assertEqual(len(app.db["events"]), 2)
        self.assertNotEqual(app.db["events"][first["review_id"]]["expected_set"], app.db["events"][third["review_id"]]["expected_set"])

    def test_responsive_buttons_wrapped_labels_and_complete_path_copy(self):
        original_scale = self.root.tk.call("tk", "scaling")
        try:
            # These are Tk scaling simulations, NOT Windows display setting runs.
            for scale in (96/72, 120/72, 144/72):
                self.root.tk.call("tk", "scaling", scale)
                container = tk.Toplevel(self.root)
                container.geometry("360x650")
                rows = display.ActionRows(container)
                rows.pack(fill="x")
                buttons = [tk.Button(rows, text=text) for text in ("確認目前注音就是應標注音", "輸入其他應標注音", "稍後處理", "儲存本筆應標判定")]
                rows.set_items(buttons)
                label = display.WrappedLabel(container, text="這是需要完整顯示的重要操作說明。" * 5)
                label.pack(fill="x")
                value = tk.StringVar(value="C:/" + "很長的教材檔名/" * 40 + "book.pdf")
                field = display.scrollable_entry(container, value)
                field.pack(fill="x")
                container.update()
                self.assertGreater(len({button.winfo_y() for button in buttons}), 1)
                for button in buttons:
                    self.assertLessEqual(button.winfo_x() + button.winfo_width(), rows.winfo_width())
                self.assertGreater(label.winfo_height(), 30)
                self.assertLessEqual(int(label.cget("wraplength")), label.winfo_width())
                field.entry.xview_moveto(1)
                field.entry.selection_range(0, "end")
                field.entry.event_generate("<<Copy>>")
                self.assertEqual(container.clipboard_get(), value.get())
                container.destroy()
        finally:
            self.root.tk.call("tk", "scaling", original_scale)


if __name__ == "__main__":
    unittest.main()
