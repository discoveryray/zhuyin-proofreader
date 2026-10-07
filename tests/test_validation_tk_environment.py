"""Same-installation wiring and early probe regressions; no real Tk is created."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import validation_tk_environment as wiring
from scripts import validation_runner as runner


class TkEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root / "Python313"
        (self.base / "DLLs").mkdir(parents=True)
        for name in ("_tkinter.pyd", "tcl86t.dll", "tk86t.dll"):
            (self.base / "DLLs" / name).write_bytes(b"fixture, not a runtime DLL")
        for kind, file, version in (("tcl", "init.tcl", "Tcl"), ("tk", "tk.tcl", "Tk")):
            folder = self.base / "tcl" / (kind + "8.6")
            folder.mkdir(parents=True)
            # Actual CPython resources have two spaces before the Tk patchlevel.
            (folder / file).write_text("package require -exact " + version + "  8.6.14\n")
        self.loaded = {name: str(self.base / "DLLs" / name) for name in ("tcl86t.dll", "tk86t.dll")}

    def select(self, extension=None, loaded=None):
        return wiring.select_resources(self.base, extension or self.base / "DLLs/_tkinter.pyd",
                                       "8.6", "8.6", loaded or self.loaded)

    def test_exact_installation_resources_accept_venv_base_prefix_and_patchlevels(self):
        result = self.select()
        self.assertEqual(result["tcl_library"], str((self.base / "tcl/tcl8.6").resolve()))
        self.assertEqual(result["tk_library"], str((self.base / "tcl/tk8.6").resolve()))
        self.assertEqual((result["tcl_version"], result["tk_version"]), ("8.6.14", "8.6.14"))
        self.assertEqual(result["loaded_dlls"], self.loaded)

    def test_missing_resource_and_incompatible_or_ambiguous_versions_fail_closed(self):
        resource = self.base / "tcl/tk8.6/tk.tcl"
        for payload in ("package require -exact Tk 8.5.14\n", "no declared Tk version\n",
                        "package require -exact Tk 8.6.14\npackage require -exact Tk 8.6.14\n"):
            resource.write_text(payload)
            with self.assertRaises(ValueError):
                self.select()
        resource.unlink()
        with self.assertRaises(FileNotFoundError):
            self.select()

    def test_foreign_extension_and_loaded_dll_are_rejected_without_disk_search(self):
        foreign = self.root / "foreign.pyd"
        foreign.write_bytes(b"foreign")
        with self.assertRaisesRegex(ValueError, "_tkinter"):
            self.select(extension=foreign)
        with self.assertRaisesRegex(ValueError, "loaded Tcl/Tk DLL"):
            self.select(loaded={**self.loaded, "tcl86t.dll": str(foreign)})
        with patch.object(Path, "rglob", side_effect=AssertionError("no filesystem scanning")):
            self.select()

    def test_retired_configuration_preflight_and_probe_never_launch(self):
        from scripts import gui_preflight
        from scripts import benchmark_review_confirm
        with patch.object(runner, "run_process", side_effect=AssertionError("subprocess launched")):
            with self.assertRaisesRegex(ValueError, "取消"):
                wiring.configuration()
            with self.assertRaisesRegex(ValueError, "取消"):
                wiring.early_preflight(self.root)
            with self.assertRaisesRegex(RuntimeError, "取消"):
                gui_preflight.test_gui_environment()
            self.assertEqual(wiring.main(["configure"]), 2)
            self.assertEqual(wiring.main(["preflight"]), 2)
            with self.assertRaisesRegex(SystemExit, "取消"):
                benchmark_review_confirm.main()
            with self.assertRaisesRegex(RuntimeError, "取消"):
                benchmark_review_confirm.run_dialog_cycles(None, None, None, None, None, None, None)
        self.assertFalse(list(self.root.glob("tk-*")))


if __name__ == "__main__":
    unittest.main()
