"""Run real export supervision and result reads with finite UI doubles; no Tk."""
import queue
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from openpyxl import Workbook
import standalone_gui as gui


class ExportFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.app = gui.App.__new__(gui.App)
        self.app.output = SimpleNamespace(get=lambda: str(self.folder))
        self.app.run_status = Mock()
        self.app.log = Mock()
        self.app.root = SimpleNamespace(after=Mock())
        self.app.q = queue.Queue()
        self.app.refresh_project_status = Mock()
        self.info = self.enterContext(patch.object(gui.messagebox, "showinfo"))
        self.error = self.enterContext(patch.object(gui.messagebox, "showerror"))
        import _tkinter
        self.enterContext(patch.object(_tkinter, "create", side_effect=AssertionError("native Tk forbidden")))

    def run_export(self, kind, script):
        self.info.reset_mock()
        self.error.reset_mock()
        self.app.proof_cmd = Mock(return_value=[sys.executable, "-c", script])
        original = threading.Thread
        workers = []
        def thread(*args, **kwargs):
            worker = original(*args, **kwargs)
            workers.append(worker)
            return worker
        with patch.object(gui.threading, "Thread", side_effect=thread):
            (self.app.export_gpt if kind == "expected" else self.app.export_actual_gpt)()
        self.app.proof_cmd.assert_called_once_with([
            "--export-gpt" if kind == "expected" else "--export-actual-gpt", "-o", str(self.folder)])
        self.assertIn("正在匯出", self.app.run_status.config.call_args.kwargs["text"])
        for worker in workers:
            worker.join(15)
            self.assertFalse(worker.is_alive(), "synthetic child worker did not finish")
        self.app.q.put("匯出完成通知之後的紀錄\n")
        self.app.poll()
        self.app.log.insert.assert_called_with("end", "匯出完成通知之後的紀錄\n")
        self.app.root.after.assert_called_with(100, self.app.poll)

    def workbook_script(self, count):
        path = self.folder / "待判定候選_給GPT.xlsx"
        return ("from openpyxl import Workbook; from pathlib import Path; "
                f"p=Path({str(path)!r}); w=Workbook(); s=w.active; s.title='匯入中繼資料'; "
                f"s.append(['exported_review_count', {count!r}]); w.save(p); w.close(); print(p)")

    def test_blank_folder_reports_both_commands_without_starting(self):
        self.app.output = SimpleNamespace(get=lambda: "   ")
        with patch.object(self.app, "start") as start:
            self.app.export_gpt()
            self.app.export_actual_gpt()
        start.assert_not_called()
        self.assertEqual(self.error.call_count, 2)
        self.assertIn("未選擇", self.app.run_status.config.call_args.kwargs["text"])

    def test_both_successes_show_this_child_output_path(self):
        actual = self.folder / "actual待判定_GPT包.zip"
        for kind, script, path in (
            ("expected", self.workbook_script(2), self.folder / "待判定候選_給GPT.xlsx"),
            ("actual", f"from pathlib import Path; p=Path({str(actual)!r}); p.write_bytes(b'new'); print(p)", actual),
        ):
            with self.subTest(kind=kind):
                self.run_export(kind, script)
                self.error.assert_not_called()
                self.assertEqual(self.info.call_args.args[0], "GPT 匯出完成")
                self.assertIn(str(path), self.info.call_args.args[1])
                self.assertIn(str(path), self.app.run_status.config.call_args.kwargs["text"])

    def test_actual_zero_pending_never_uses_old_zip(self):
        old = self.folder / "actual待判定_GPT包.zip"
        old.write_bytes(b'old')
        message = "目前沒有 ACTUAL_DECODE_ERROR／ACTUAL_UNRESOLVED 可匯出；actual 待判定為 0，無須建立 GPT 判定包。"
        self.run_export("actual", f"print({message!r})")
        self.assertEqual(self.info.call_args.args[0], "沒有 actual 待判定項目")
        self.assertNotIn("匯出完成", self.app.run_status.config.call_args.kwargs["text"])
        self.assertEqual(old.read_bytes(), b'old')

    def test_expected_zero_pending_is_not_reported_as_export_success(self):
        self.run_export("expected", self.workbook_script(0))
        self.error.assert_not_called()
        self.assertEqual(self.info.call_args.args[0], "沒有 expected／差異待判定項目")
        self.assertIn("本次空白證據表", self.app.run_status.config.call_args.kwargs["text"])
        self.assertNotIn("匯出完成", self.app.run_status.config.call_args.kwargs["text"])

    def test_failed_child_and_spawn_failure_show_reason_and_log_hint(self):
        self.run_export("expected", "import sys; print('來源檔案失效'); sys.exit(2)")
        self.info.assert_not_called()
        self.assertIn("來源檔案失效", self.error.call_args.args[1])
        self.assertIn("更多…", self.app.run_status.config.call_args.kwargs["text"])
        with patch.object(gui.subprocess, "Popen", side_effect=OSError("無法啟動子程序")):
            self.run_export("actual", "print('unused')")
        self.info.assert_not_called()
        self.assertIn("無法啟動子程序", self.error.call_args.args[1])

    def test_missing_or_unrecognized_output_is_not_success(self):
        for output in (str(self.folder / "actual待判定_GPT包.zip"), "", "沒有可確認的結果"):
            with self.subTest(output=output):
                self.run_export("actual", f"print({output!r})")
                self.info.assert_not_called()
                self.assertEqual(self.error.call_args.args[0], "GPT 匯出結果無法確認")
                self.assertIn("更多…", self.error.call_args.args[1])

    def test_unreadable_or_invalid_path_keeps_queue_polling(self):
        path = self.folder / "actual待判定_GPT包.zip"
        path.write_bytes(b'new')
        for operation, exception in (("is_file", OSError("無法檢查路徑")),
                                     ("open", PermissionError("無法讀取輸出"))):
            with self.subTest(operation=operation), patch.object(Path, operation, side_effect=exception):
                result = gui.gpt_export_result("actual", 0, str(path))
            self.app.q.put(("__DONE__", None, 0, result))
            self.app.q.put("completion後的紀錄\n")
            self.app.poll()
            self.assertIn(str(exception), self.error.call_args.args[1])
            self.app.log.insert.assert_called_with("end", "completion後的紀錄\n")
        result = gui.gpt_export_result("actual", 0, "\0/actual待判定_GPT包.zip")
        self.assertTrue(result[2])
        self.app.q.put(("__DONE__", None, 0, result))
        self.app.poll()
        self.app.root.after.assert_called_with(100, self.app.poll)

    def test_unreadable_expected_metadata_does_not_claim_success(self):
        path = self.folder / "待判定候選_給GPT.xlsx"
        for rows in ([], [["exported_review_count", -1]], [["exported_review_count", "unknown"]],
                     [["exported_review_count", 1], ["exported_review_count", 0]]):
            with self.subTest(rows=rows):
                workbook = Workbook()
                workbook.active.title = "匯入中繼資料"
                for row in rows:
                    workbook.active.append(row)
                workbook.save(path)
                workbook.close()
                result = gui.gpt_export_result("expected", 0, str(path))
                self.assertTrue(result[2])
                self.assertEqual(result[0], "GPT 匯出結果無法確認")
        path.write_bytes(b'not an xlsx')
        self.assertTrue(gui.gpt_export_result("expected", 0, str(path))[2])

    def test_non_export_shared_worker_keeps_environment_button_and_status(self):
        original = threading.Thread
        workers = []
        def thread(*args, **kwargs):
            worker = original(*args, **kwargs)
            workers.append(worker)
            return worker
        for code in (0, 2):
            button = Mock()
            script = f"import os,sys; print(os.environ['EXPORT_TEST_ENV']); sys.exit({code})"
            with patch.object(gui.threading, "Thread", side_effect=thread):
                self.app.start([sys.executable, "-c", script], button, {"EXPORT_TEST_ENV": "保留一般命令"})
            self.assertEqual(button.config.call_args.kwargs["state"], "disabled")
            workers[-1].join(15)
            self.assertFalse(workers[-1].is_alive())
            self.app.poll()
            button.config.assert_called_with(state="normal")
            text = self.app.run_status.config.call_args.kwargs["text"]
            self.assertIn("處理已結束" if code == 0 else "操作失敗（代碼 2）", text)
        self.app.refresh_project_status.assert_called()
        self.app.log.insert.assert_any_call("end", "保留一般命令\n")
        self.info.assert_not_called()
        self.error.assert_not_called()
