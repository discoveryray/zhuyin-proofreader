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

    def test_configuration_writes_only_current_job_environment_and_retains_failure(self):
        envfile = self.root / "job-env"
        envfile.write_text("")
        selected = self.select()
        with patch.dict(os.environ, {"GITHUB_ENV": str(envfile)}), patch.object(wiring, "configuration", return_value=selected):
            self.assertEqual(wiring.main(["configure", "--evidence-root", str(self.root / "evidence"), "--github-env", str(envfile)]), 0)
            self.assertEqual(envfile.read_text().splitlines(), ["TCL_LIBRARY=" + selected["tcl_library"], "TK_LIBRARY=" + selected["tk_library"]])
            other = self.root / "unrelated-env"
            self.assertEqual(wiring.main(["configure", "--evidence-root", str(self.root / "evidence"), "--github-env", str(other)]), 2)
            self.assertFalse(other.exists())
        records = [json.loads(path.read_text()) for path in (self.root / "evidence").glob("tk-configure-*/configuration.json")]
        self.assertEqual(sorted(row["outcome"] for row in records), ["failed", "success"])
        self.assertTrue(all(row["started_at"] and row["finished_at"] and row["source_sha256"] for row in records))

    def test_early_probe_is_fd_same_interpreter_bounded_and_preserves_failed_raw_evidence(self):
        folder = self.root / "preflight"
        folder.mkdir()
        env = runner.environment()
        def failure(command, output, name, timeout, child_env):
            self.assertEqual(command[0], wiring.sys.executable)
            self.assertIn("--capture=fd", command)
            self.assertEqual(timeout, 60)
            self.assertEqual({name: child_env.get(name, "") for name in runner.ENV_KEYS}, env["environment"])
            (output / name).write_text("original initialization failure")
            return {"outcome": "failed", "exit_code": 1, "started_at": "start", "finished_at": "end", "runner_start_error": None}
        with patch.object(runner, "validate_environment"), patch.object(runner, "snapshot", return_value={"head": "a" * 40}), patch.object(runner, "run_process", side_effect=failure):
            self.assertFalse(wiring.early_preflight(folder))
        result = json.loads((folder / "result.json").read_text())
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual((folder / "preflight.log").read_text(), "original initialization failure")
        self.assertEqual(len(result["source_sha256"]), 2)
        self.assertFalse((folder / "manifest.json").exists())

    def test_early_probe_does_not_accept_incomplete_success(self):
        folder = self.root / "preflight"
        folder.mkdir()
        def incomplete(command, output, name, timeout, env):
            (output / name).write_text("incomplete synthetic probe")
            (output / "preflight.json").write_text('{"stage": "widgets"}')
            return {"outcome": "success", "exit_code": 0}
        with patch.object(runner, "validate_environment"), patch.object(runner, "snapshot", return_value={}), patch.object(runner, "run_process", side_effect=incomplete):
            self.assertFalse(wiring.early_preflight(folder))
        self.assertEqual(json.loads((folder / "result.json").read_text())["outcome"], "failed")
