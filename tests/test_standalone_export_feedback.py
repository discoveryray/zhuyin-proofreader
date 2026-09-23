"""The menu exports must report their outcome without opening the advanced log."""

import queue
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import standalone_gui


class ExportFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.app = standalone_gui.App.__new__(standalone_gui.App)
        self.app.output = SimpleNamespace(get=lambda: str(self.folder))
        self.app.run_status = MagicMock()
        self.app.log = MagicMock()
        self.app.root = SimpleNamespace(after=lambda *_args: None)
        self.app.q = queue.Queue()
        self.app.refresh_project_status = lambda: None

    def run_export(self, kind, script):
        self.app.proof_cmd = lambda _args: [sys.executable, "-c", script]
        # Run the worker to completion, then consume its queue on the caller
        # thread, as Tk's event loop does. No display or real project needed.
        with (patch.object(standalone_gui.threading, "Thread", side_effect=lambda target, daemon:
                           SimpleNamespace(start=target)),
              patch.object(standalone_gui.messagebox, "showinfo") as info,
              patch.object(standalone_gui.messagebox, "showerror") as error):
            if kind == "expected":
                self.app.export_gpt()
            else:
                self.app.export_actual_gpt()
            self.app.poll()
            return info.call_args, error.call_args

    def test_blank_output_selection_is_reported_for_both_exports(self):
        self.app.output = SimpleNamespace(get=lambda: "   ")
        with (patch.object(standalone_gui.messagebox, "showerror") as error,
              patch.object(self.app, "start") as start):
            self.app.export_gpt()
            self.app.export_actual_gpt()
        self.assertEqual(error.call_count, 2)
        start.assert_not_called()

    def test_expected_success_shows_saved_workbook(self):
        target = self.folder / "待判定候選_給GPT.xlsx"
        script = "from pathlib import Path; p=Path(%r); p.write_bytes(b'xlsx'); print(p)" % str(target)
        info, error = self.run_export("expected", script)
        self.assertIn(str(target), info.args[1])
        self.assertIsNone(error)

    def test_actual_zero_pending_does_not_report_stale_zip_as_new(self):
        (self.folder / "actual待判定_GPT包.zip").write_bytes(b'old')
        script = "print('目前沒有 ACTUAL_DECODE_ERROR／ACTUAL_UNRESOLVED 可匯出；actual 待判定為 0，無須建立 GPT 判定包。')"
        info, error = self.run_export("actual", script)
        self.assertIn("待判定為 0", info.args[1])
        self.assertIsNone(error)

    def test_actual_success_shows_saved_zip(self):
        target = self.folder / "actual待判定_GPT包.zip"
        script = "from pathlib import Path; p=Path(%r); p.write_bytes(b'zip'); print(p)" % str(target)
        info, error = self.run_export("actual", script)
        self.assertIn(str(target), info.args[1])
        self.assertIsNone(error)

    def test_error_and_missing_file_are_not_reported_as_success(self):
        info, error = self.run_export("expected", "import sys; print('來源檔案失效'); sys.exit(2)")
        self.assertIsNone(info)
        self.assertIn("來源檔案失效", error.args[1])
        info, error = self.run_export("actual", "print('actual待判定_GPT包.zip')")
        self.assertIsNone(info)
        self.assertIn("無法確認", error.args[0])

    def test_unreadable_output_path_does_not_break_tk_poll(self):
        target = self.folder / "actual待判定_GPT包.zip"
        with (patch.object(standalone_gui.Path, "is_file", side_effect=OSError("invalid path")),
              patch.object(standalone_gui.messagebox, "showinfo") as info,
              patch.object(standalone_gui.messagebox, "showerror") as error):
            self.app.q.put(("__DONE__", None, 0, "actual", str(target)))
            self.app.poll()
            info.assert_not_called()
            self.assertIn("無法確認", error.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
