from __future__ import annotations

import tkinter as tk
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from review_gui import (  # noqa: E402
    ConfirmationDialog,
    ExpectedDialog,
    review_action_labels,
    screen_safe_window_size,
)


def _find_button(widget, text: str):
    for child in widget.winfo_children():
        if isinstance(child, tk.Button) and str(child.cget("text")) == text:
            return child
        found = _find_button(child, text)
        if found is not None:
            return found
    return None


def _wait_for_dialog_layout(dialog):
    dialog.wait_visibility()
    dialog.update_idletasks()


ENTRY = {
    "state": "RULE_CONFLICT",
    "printed_page": 155,
    "char": "看",
    "actual": "ㄎㄢˋ",
    "actual_evidence": "CFF glyph",
    "expected_set": [],
    "expected_evidence": "",
    "context_evidence": "來檢視看看吧！",
    "source_record": {
        "局部詞境": "來檢視看看吧！",
        "所在行": "來檢視看看吧！",
        "完整詞語規則": "看看",
    },
}


class ReviewGuiPureLayoutTests(unittest.TestCase):
    def test_screen_safe_size_never_exceeds_available_screen(self):
        width, height = screen_safe_window_size(1024, 720, 760, 660)
        self.assertLessEqual(width, 944)
        self.assertLessEqual(height, 600)
        self.assertGreaterEqual(width, 520)
        self.assertGreaterEqual(height, 420)

    def test_no_visible_action_label_is_blank(self):
        states = [
            "DIFFERENCE_PENDING_CONFIRMATION",
            "RULE_CONFLICT",
            "EXPECTED_AMBIGUOUS",
            "EXPECTED_UNRESOLVED",
            "REVIEW_PENDING",
            "ACTUAL_DECODE_ERROR",
            "ACTUAL_UNRESOLVED",
            "SOURCE_INVALID",
        ]
        for state in states:
            primary, secondary = review_action_labels(state)
            self.assertTrue(primary.strip(), state)
            if secondary is not None:
                self.assertTrue(secondary.strip(), state)
        self.assertIsNone(review_action_labels("RULE_CONFLICT")[1])
        self.assertIsNone(review_action_labels("EXPECTED_AMBIGUOUS")[1])


class ReviewGuiVisibleFooterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
            cls.root.geometry("900x650+0+0")
            cls.root.update_idletasks()
        except tk.TclError as exc:
            raise unittest.SkipTest(f"Tk display unavailable: {exc}")

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "root", None) is not None:
            cls.root.destroy()

    def _assert_button_inside_dialog(self, dialog, label: str):
        button = _find_button(dialog, label)
        self.assertIsNotNone(button, label)
        self.assertTrue(dialog.winfo_ismapped(), label)
        self.assertTrue(dialog.winfo_viewable(), label)
        self.assertTrue(button.winfo_ismapped(), label)
        self.assertTrue(button.winfo_viewable(), label)
        dialog_bottom = dialog.winfo_rooty() + dialog.winfo_height()
        button_bottom = button.winfo_rooty() + button.winfo_height()
        self.assertLessEqual(button_bottom, dialog_bottom, label)
        self.assertGreater(button.winfo_height(), 1, label)

    def test_expected_dialog_submit_and_cancel_are_visible(self):
        dialog = ExpectedDialog(self.root, dict(ENTRY), "選擇／補充正確讀音", wait=False)
        try:
            _wait_for_dialog_layout(dialog)
            self._assert_button_inside_dialog(dialog, "儲存本筆應標判定")
            self._assert_button_inside_dialog(dialog, "取消")
        finally:
            dialog.grab_release()
            dialog.destroy()

    def test_confirmation_dialog_submit_and_cancel_are_visible(self):
        entry = dict(ENTRY)
        entry.update({
            "state": "DIFFERENCE_PENDING_CONFIRMATION",
            "expected_set": ["ㄎㄢ"],
            "expected_evidence": "公司規定",
        })
        dialog = ConfirmationDialog(self.root, entry, wait=False)
        try:
            _wait_for_dialog_layout(dialog)
            self._assert_button_inside_dialog(dialog, "確認為教材錯誤")
            self._assert_button_inside_dialog(dialog, "取消")
        finally:
            dialog.grab_release()
            dialog.destroy()


if __name__ == "__main__":
    unittest.main()
