"""Non-window regressions for the usage feedback presentation paths."""
import queue
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from review_context_display import pdf_sentence_at_target
from review_gui import ReviewApp, SurfaceActualDialog, review_context_text
from standalone_gui import App, child_window_options, fitted_launcher_geometry, review_child_worker


class UsageInterfaceTests(unittest.TestCase):
    def test_context_keeps_full_sentence_once_and_preserves_distinct_local_context(self):
        entry = {"source_record": {"局部詞境": "請", "所在行": "請把答案寫下來。"}}
        self.assertEqual(review_context_text(entry), "請把答案寫下來。")
        entry["source_record"]["局部詞境"] = "把答案寫下來"
        self.assertEqual(review_context_text(entry), "請把答案寫下來。")
        entry["source_record"]["所在行"] = "請"
        self.assertEqual(review_context_text(entry), "請（局部：把答案寫下來）")
        entry["_display_context"] = "請把答案寫下來。"
        entry["char"] = "請"
        self.assertEqual(review_context_text(entry), "請把答案寫下來。")

    def test_pdf_context_crosses_only_adjacent_matching_column_and_stops_at_sentence_end(self):
        def line(text, x, y):
            chars = [{"c": char, "bbox": (x + i * 10, y, x + (i + 1) * 10, y + 16)}
                     for i, char in enumerate(text)]
            return {"bbox": (x, y, x + len(text) * 10, y + 16), "spans": [{"chars": chars}]}
        first = line("前句。請", 68, 100)
        next_line = line("勾選愛家行動。", 68, 122)
        other_column = line("錯誤欄。", 320, 122)
        raw = {"blocks": [{"lines": [first]}, {"lines": [next_line]}, {"lines": [other_column]}]}
        target = first["spans"][0]["chars"][-1]["bbox"]
        self.assertEqual(pdf_sentence_at_target(raw, target, "請"), "請勾選愛家行動。")
        raw["blocks"][1]["lines"][0] = line("另一段", 68, 155)
        self.assertIsNone(pdf_sentence_at_target(raw, target, "請"))
        raw["blocks"][1]["lines"][0] = line("甲" * 500, 68, 122)
        self.assertIsNone(pdf_sentence_at_target(raw, target, "請"))

    def test_launcher_grows_for_content_and_log_without_shrinking_large_window(self):
        self.assertEqual(fitted_launcher_geometry(1080, 560, 700, 480, 1920, 1080, 100, 120),
                         (1080, 780, 100, 120))
        self.assertEqual(fitted_launcher_geometry(1080, 850, 600, 480, 1920, 1080, 100, 120),
                         (1080, 970, 100, 70))
        self.assertEqual(fitted_launcher_geometry(1080, 560, 1500, 480, 1280, 720, 100, 120),
                         (1080, 640, 100, 40))

    def test_child_output_and_failure_status_survive_console_suppression(self):
        seen = {}

        class Child:
            def __init__(self):
                self.stdout = iter(["diagnostic\n", "failure detail\n"])

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def wait(self):
                return 9

        def popen(*args, **kwargs):
            seen.update(kwargs)
            return Child()

        messages = queue.Queue()
        with patch("standalone_gui.subprocess.Popen", popen):
            review_child_worker(["python", "review_gui.py"], {}, messages, "token")
        self.assertEqual(seen["stderr"], subprocess.STDOUT)
        self.assertEqual(seen["stdout"], subprocess.PIPE)
        self.assertEqual({key: seen[key] for key in child_window_options()}, child_window_options())
        self.assertEqual(messages.get_nowait(), ("__REVIEW_LOG__", "token", "diagnostic\n"))
        self.assertEqual(messages.get_nowait(), ("__REVIEW_LOG__", "token", "failure detail\n"))
        self.assertEqual(messages.get_nowait(), ("__REVIEW_DONE__", "token", 9, "diagnostic\nfailure detail\n"))
        operation_messages = queue.Queue()
        app = SimpleNamespace(output=SimpleNamespace(get=lambda: "."),
                              _reserve_operation=lambda *_args: "operation",
                              run_status=SimpleNamespace(config=lambda **_kwargs: None),
                              q=operation_messages)
        with patch("standalone_gui.subprocess.Popen", popen), patch(
                "standalone_gui.threading.Thread",
                side_effect=lambda **kwargs: SimpleNamespace(start=kwargs["target"])):
            self.assertTrue(App.start(app, ["python", "operation.py"]))
        self.assertEqual({key: seen[key] for key in child_window_options()}, child_window_options())
        self.assertEqual(operation_messages.get_nowait()[0], "__OP_LOG__")
        self.assertEqual(operation_messages.get_nowait()[0], "__OP_LOG__")
        self.assertEqual(operation_messages.get_nowait()[0:1], ("__OP_DONE__",))

    def test_confirmed_overlay_routes_confirmation_to_manual_surface_first(self):
        entry = {"review_id": "r1", "state": "EXPECTED_UNRESOLVED"}
        changes = {}
        button = SimpleNamespace(config=lambda **kwargs: changes.update(kwargs))
        app = SimpleNamespace(current=lambda: entry, _save_busy=lambda: False,
                              _rendered_review_id="r1", primary=button,
                              _confirm_current_expected_button=button,
                              confirm_current_expected=lambda rid: rid,
                              correct_actual=lambda: None,
                              image=SimpleNamespace(_entry=entry, source_diagnostic={
                                  "reason": "SOURCE_OVERPAINT_CONFIRMED"}))
        with patch("review_gui.can_confirm_current_expected", return_value=True):
            ReviewApp._preview_failed(app)
        self.assertEqual(app._rendered_review_id, None)
        self.assertEqual(changes["state"], "normal")
        self.assertEqual(changes["command"], app.correct_actual)
        entry["manual_surface_actual"] = {"applied": True}
        app.image.photo = object()
        changes.clear()
        with patch("review_gui.can_confirm_current_expected", return_value=True):
            ReviewApp._preview_failed(app)
        self.assertEqual(app._rendered_review_id, "r1")
        self.assertEqual(changes["state"], "normal")
        self.assertEqual(changes["text"], "確認目前注音就是應標注音")
        self.assertEqual(changes["command"](), "r1")
        entry.pop('manual_surface_actual')
        for reason in ("SOURCE_PREFIX_BACKGROUND_UNPROVABLE", "SOURCE_COMPLETE_CONTRAST_UNPROVABLE",
                       "SOURCE_LATER_INK_INTERSECTION_UNPROVABLE"):
            app.image.source_diagnostic["reason"] = reason
            changes.clear()
            with patch("review_gui.can_confirm_current_expected", return_value=True):
                ReviewApp._preview_failed(app)
            self.assertIsNone(app._rendered_review_id)
            self.assertEqual(changes['command'], app.correct_actual)
            self.assertEqual(changes['text'], '目視確認本位置注音')
        for reason in ("SOURCE_ANNOTATION_INTERSECTION_UNPROVABLE", "SOURCE_SLOT_CONTRADICTION"):
            app.image.source_diagnostic["reason"] = reason
            changes.clear()
            with patch("review_gui.can_confirm_current_expected", return_value=True):
                ReviewApp._preview_failed(app)
            self.assertIsNone(app._rendered_review_id)
            self.assertEqual(changes, {"state": "disabled"})
        entry["char"] = "請"
        app._summary_context = "請"
        app.image.display_context = "請勾選已做到的愛家行動。"
        summary = {}
        app.summary_text = SimpleNamespace(get=lambda *_: "語境：請\n目前注音：待核對",
            configure=lambda **kwargs: summary.update(kwargs))
        with patch("review_gui.can_confirm_current_expected", return_value=True):
            ReviewApp._preview_failed(app)
        self.assertEqual(summary["text"], "語境：請勾選已做到的愛家行動。\n目前注音：待核對")
        self.assertIsNone(app._rendered_review_id)
        self.assertNotIn("_display_context", entry)

    def test_surface_button_uses_current_reading_and_explicit_conversion_consent(self):
        values = dict(actual='ㄆ', expected='ㄇ')
        dialog = SimpleNamespace(prepared={'warning':'舊版不支援回寫'},
            reading=SimpleNamespace(get=lambda:values['actual']),
            expected_reading=SimpleNamespace(get=lambda:values['expected']),
            actor=SimpleNamespace(get=lambda:'human'), result=None, destroy=Mock())
        def result(prepared, **kwargs):
            return dict(kwargs,enable_format=True)
        with patch('review_gui.surface_review_result',side_effect=result), patch(
                'review_gui.messagebox.askyesno',return_value=False) as consent:
            SurfaceActualDialog.submit_review(dialog,same=True)
        self.assertIsNone(dialog.result)
        dialog.destroy.assert_not_called()
        self.assertIn('舊版不支援回寫',consent.call_args.args[1])
        values['actual']='ㄈ'
        with patch('review_gui.surface_review_result',side_effect=result), patch(
                'review_gui.messagebox.askyesno',return_value=True):
            SurfaceActualDialog.submit_review(dialog,same=True)
        self.assertEqual(dialog.result['reading'],'ㄈ')
        self.assertEqual(dialog.result['user_expected_reading'],'ㄈ')
        with patch('review_gui.surface_review_result',side_effect=result), patch(
                'review_gui.messagebox.askyesno',return_value=True):
            SurfaceActualDialog.submit_review(dialog,same=False)
        self.assertEqual(dialog.result['reading'],'ㄈ')
        self.assertEqual(dialog.result['user_expected_reading'],'ㄇ')


if __name__ == "__main__":
    unittest.main()
