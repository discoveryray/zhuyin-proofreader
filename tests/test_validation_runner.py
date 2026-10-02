"""Small isolated runner regressions; these fixtures never create a Tk window."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from scripts import test_entrypoint_audit as audit
from scripts import validation_runner as runner

ROOT = Path(__file__).resolve().parents[1]


class GroupedCollectionTests(unittest.TestCase):
    def test_partition_rejects_missing_duplicate_unknown_and_overlap(self):
        good = {"schema": "zhuyin-test-groups/1", "pytest_ids": ["m.a", "m.b"],
                "core_ids": ["m.a"], "gui_ids": ["m.b"],
                "records": [{"identity": "m.a", "nodeid": "test_m.py::a", "group": "core"},
                            {"identity": "m.b", "nodeid": "test_m.py::b", "group": "gui"}]}
        audit.validate_groups(good)
        for field, value in (("core_ids", []), ("core_ids", ["m.a", "m.a"]),
                             ("gui_ids", ["m.a", "m.b"]), ("pytest_ids", ["m.a", "m.z"])):
            changed = copy.deepcopy(good)
            changed[field] = value
            with self.assertRaises(ValueError):
                audit.validate_groups(changed)

    def test_imported_helper_inherited_setup_and_pure_logic(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as temporary:
            folder = Path(temporary)
            (folder / "helper_probe.py").write_text("import tkinter as tk\ndef make_root(): return tk.Tk()\n")
            (folder / "source_probe.py").write_text(
                "from helper_probe import make_root\n"
                "class Parent:\n    def setUp(self): self.root = make_root()\n"
                "class Child(Parent):\n    def test_inherited(self): pass\n"
                "def test_function(): return make_root()\n"
                "def test_logic(): return 1 + 2\n")
            sys.path.insert(0, str(folder))
            try:
                spec = importlib.util.spec_from_file_location("source_probe", folder / "source_probe.py")
                source_probe = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(source_probe)
                self.assertTrue(audit.callable_uses_tk(source_probe.Child.setUp, source_probe.Child))
                self.assertTrue(audit.callable_uses_tk(source_probe.test_function))
                self.assertFalse(audit.callable_uses_tk(source_probe.test_logic))
            finally:
                sys.path.remove(str(folder))
                sys.modules.pop("source_probe", None)
                sys.modules.pop("helper_probe", None)

    def test_package_alias_requires_the_exact_already_loaded_helper_source(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as temporary:
            root = Path(temporary)
            (root / "tests").mkdir()
            helper = root / "tests" / "aliased_helper.py"
            helper.write_text("import tkinter as tk\ndef make_root(): return tk.Tk()\n")
            probe = root / "probe.py"
            probe.write_text("def probe():\n    from tests.aliased_helper import make_root\n    return make_root()\n")
            def load(name, path):
                spec = importlib.util.spec_from_file_location(name, path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                return module
            provider = load("aliased_helper", helper)
            consumer = load("alias_consumer", probe)
            with patch.object(audit, "ROOT", root), patch.dict(sys.modules, {"aliased_helper": provider}):
                self.assertTrue(audit.callable_uses_tk(consumer.probe))
                provider.__file__ = str(root / "different_source.py")
                with self.assertRaisesRegex(ValueError, "unresolved local imported helper"):
                    audit.callable_uses_tk(consumer.probe)

    def test_script_entrypoint_alias_requires_exact_loaded_sibling(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as temporary:
            root = Path(temporary)
            scripts = root / "scripts"
            scripts.mkdir()
            helper = scripts / "entry_helper.py"
            helper.write_text("import tkinter as tk\ndef make_root(): return tk.Tk()\n")
            probe = scripts / "entry_probe.py"
            probe.write_text("def probe():\n    from entry_helper import make_root\n    return make_root()\n")
            def load(name, path):
                spec = importlib.util.spec_from_file_location(name, path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                return module
            provider = load("scripts.entry_helper", helper)
            consumer = load("scripts.entry_probe", probe)
            with patch.object(audit, "ROOT", root), patch.dict(sys.modules, {"scripts.entry_helper": provider}):
                self.assertTrue(audit.callable_uses_tk(consumer.probe))
                provider.__file__ = str(root / "different_source.py")
                with self.assertRaisesRegex(ValueError, "unresolved local imported helper"):
                    audit.callable_uses_tk(consumer.probe)

    def test_local_imports_cannot_silently_become_core(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as temporary:
            path = Path(temporary) / "local_import_probe.py"
            path.write_text("def tk_test():\n    from tkinter import Tk\n    return Tk()\ndef unknown_helper():\n    from uncatalogued_helper import make_root\n    return make_root()\n")
            spec = importlib.util.spec_from_file_location("local_import_probe", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.assertTrue(audit.callable_uses_tk(module.tk_test))
            with self.assertRaisesRegex(ValueError, "unresolved local imported helper"):
                audit.callable_uses_tk(module.unknown_helper)



class RunnerCliTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "tmp").mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT / "tmp")
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.execution = self.folder / "execution"
        self.execution.mkdir()
        (self.execution / "temp").mkdir()
        self.test_path = self.folder / "test_isolated.py"

    def execute(self, body):
        self.test_path.write_text(body, encoding="utf-8")
        relative = self.test_path.relative_to(ROOT).as_posix()
        inventory = {"schema": "zhuyin-test-groups/1", "pytest_ids": ["test_isolated.Case.test_probe"],
                     "core_ids": ["test_isolated.Case.test_probe"], "gui_ids": [],
                     "records": [{"identity": "test_isolated.Case.test_probe", "nodeid": relative + "::Case::test_probe", "group": "core"}]}
        runner.write_json(self.execution / "inventory.json", inventory)
        result = subprocess.run([sys.executable, str(ROOT / "scripts/validation_runner.py"), "_pytest",
                                 str(self.execution), "core"], cwd=ROOT, capture_output=True, timeout=30)
        return result, inventory

    def test_cli_success_counts_top_level_separately_from_subtests(self):
        result, inventory = self.execute("import unittest\nclass Case(unittest.TestCase):\n    def test_probe(self):\n        for i in range(2):\n            with self.subTest(i=i): self.assertLess(i, 2)\n")
        self.assertEqual(result.returncode, 0, result.stdout.decode())
        self.assertEqual(audit.verify_group(self.execution / "junit.xml", inventory, "core"), 1)
        self.assertEqual(runner.verify_events(self.execution / "events.jsonl", inventory, "core"), {"top_level": 1, "subtests": 2, "error": None})
        runner.junit_binding(self.execution / "junit.xml", "execution", runner.digest(self.execution / "inventory.json"))
        with self.assertRaisesRegex(ValueError, "stale"):
            runner.junit_binding(self.execution / "junit.xml", "other", runner.digest(self.execution / "inventory.json"))

    def test_cli_subtest_failure_is_retained_and_rejected(self):
        result, inventory = self.execute("import unittest\nclass Case(unittest.TestCase):\n    def test_probe(self):\n        with self.subTest(i=1): self.assertEqual(1, 2)\n")
        self.assertNotEqual(result.returncode, 0)
        events = (self.execution / "events.jsonl").read_text(encoding="utf-8")
        self.assertIn('"outcome": "failed"', events)
        self.assertIn("AssertionError", events)
        with self.assertRaises(ValueError):
            runner.verify_events(self.execution / "events.jsonl", inventory, "core")
        with self.assertRaises(ValueError):
            audit.verify_group(self.execution / "junit.xml", inventory, "core")

    def test_cli_skip_is_not_success(self):
        result, inventory = self.execute("import unittest\nclass Case(unittest.TestCase):\n    @unittest.skip('fixture')\n    def test_probe(self): pass\n")
        self.assertEqual(result.returncode, 0)
        with self.assertRaises(ValueError):
            audit.verify_group(self.execution / "junit.xml", inventory, "core")

    def test_cli_setup_error_and_failure_leave_raw_events(self):
        result, inventory = self.execute("import unittest\nclass Case(unittest.TestCase):\n    def setUp(self): raise RuntimeError('fixture setup failure')\n    def test_probe(self): pass\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("fixture setup failure", (self.execution / "events.jsonl").read_text())
        with self.assertRaises(ValueError):
            audit.verify_group(self.execution / "junit.xml", inventory, "core")

    def test_missing_partial_or_wrong_junit_is_rejected(self):
        result, inventory = self.execute("import unittest\nclass Case(unittest.TestCase):\n    def test_probe(self): pass\n")
        self.assertEqual(result.returncode, 0)
        report = self.execution / "junit.xml"
        report.write_text("<testsuites><testsuite><testcase")
        with self.assertRaises(ET.ParseError):
            audit.verify_group(report, inventory, "core")
        report.unlink()
        with self.assertRaises(FileNotFoundError):
            runner.junit_binding(report, "execution", "missing")

    def test_interrupt_keeps_reported_failure_even_without_junit(self):
        result, inventory = self.execute("import os\nimport unittest\nclass Case(unittest.TestCase):\n    def test_probe(self):\n        with self.subTest(i=1): self.assertEqual(1, 2)\n        os._exit(7)\n")
        self.assertEqual(result.returncode, 7)
        self.assertFalse((self.execution / "junit.xml").exists())
        self.assertIn("AssertionError", (self.execution / "events.jsonl").read_text())

    def test_write_and_process_log_are_exclusive(self):
        runner.write_json(self.execution / "once.json", {"original": True})
        with self.assertRaises(FileExistsError):
            runner.write_json(self.execution / "once.json", {})
        (self.execution / "run.log").write_text("original")
        with self.assertRaises(FileExistsError):
            runner.run_process([sys.executable, "-c", "print('changed')"], self.execution, "run.log", 1)
        self.assertEqual((self.execution / "run.log").read_text(), "original")

    def test_gui_retry_requires_original_link_and_only_one_retry(self):
        first = {"retry_key": "key", "group": "gui", "started_at": "1", "retry_of": None,
                 "execution_id": "first", "retry_eligible": True, "code_blockers": False, "outcome": "blocked"}
        second = {**first, "started_at": "2", "retry_of": "first", "execution_id": "second", "outcome": "success"}
        runner.retry_history([(Path("one"), first), (Path("two"), second)], "key")
        for values in ([second], [first, second, {**second, "execution_id": "third"}],
                       [{**first, "retry_eligible": False}, second], [{**first, "code_blockers": True}, second]):
            with self.assertRaises(ValueError):
                runner.retry_history([(Path(str(i)), value) for i, value in enumerate(values)], "key")

    def test_only_specific_initialization_resource_failure_is_retryable(self):
        runner.write_json(self.execution / "preflight.json", {"stage": "tk_initialization"})
        report = self.execution / "preflight.xml"
        preflight = {"outcome": "failed"}
        for message, expected in (("_tkinter.TclError: couldn't read file \"C:/Tk/combobox.tcl\": permission denied", True),
                                  ("_tkinter.TclError: invalid command name", False),
                                  ("AssertionError: bad save", False)):
            root = ET.Element("testsuite")
            case = ET.SubElement(root, "testcase")
            ET.SubElement(case, "failure", message=message)
            ET.ElementTree(root).write(report)
            self.assertEqual(runner.retry_eligible(self.execution, {}, preflight), expected)

    def test_development_cli_cannot_start_formal_group(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/validation_runner.py"), "run", "--group", "core", "--mode", "development", "--evidence-root", str(self.execution)], cwd=ROOT, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"explicit --mode validation", result.stderr)


if __name__ == "__main__":
    unittest.main()

class RunnerOrchestrationTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "tmp").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "tmp")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.evidence = self.folder / "evidence"
        test_path = self.folder / "test_pair.py"
        test_path.write_text("import unittest\nclass Case(unittest.TestCase):\n    def test_core(self): self.assertTrue(True)\n    def test_gui(self): self.assertTrue(True)\n")
        relative = test_path.relative_to(ROOT).as_posix()
        self.inventory = {"schema": "zhuyin-test-groups/1", "pytest_ids": ["test_pair.Case.test_core", "test_pair.Case.test_gui"],
                          "core_ids": ["test_pair.Case.test_core"], "gui_ids": ["test_pair.Case.test_gui"],
                          "records": [{"identity": "test_pair.Case.test_" + group, "nodeid": relative + "::Case::test_" + group, "group": group} for group in ("core", "gui")]}
        self.candidate = {"head": "a" * 40, "tree": "b" * 40, "parents": ["c" * 40, "d" * 40]}
        self.real_run = runner.run_process
        self.preflight_success = False
        self.calls = []
        self.patches = [patch.dict("os.environ", {"GITHUB_ACTIONS": "true"}),
                        patch.object(runner, "validate_history_context", return_value={"task_id": "fixture-task"}),
                        patch.object(runner, "snapshot", return_value=self.candidate),
                        patch.object(runner, "git", return_value=""),
                        patch.object(runner, "collect_inventory", side_effect=self.collect),
                        patch.object(runner, "run_process", side_effect=self.process)]
        for context in self.patches:
            context.start()
            self.addCleanup(context.stop)

    def collect(self, folder, tests=None):
        runner.write_json(folder / "inventory.json", self.inventory)
        (folder / "collection.log").write_text("isolated fixture inventory\n")
        return self.inventory

    def process(self, command, folder, name, timeout, env=None):
        self.calls.append(name)
        if name != "preflight.log":
            return self.real_run(command, folder, name, timeout, env)
        (folder / name).write_text("isolated preflight injection, no Tk created\n")
        runner.write_json(folder / "preflight.json", {"stage": "complete" if self.preflight_success else "tk_initialization"})
        root = ET.Element("testsuite")
        case = ET.SubElement(root, "testcase", name="test_gui_environment")
        if not self.preflight_success:
            ET.SubElement(case, "failure", message="_tkinter.TclError: couldn't read file \"C:/Tk/combobox.tcl\": permission denied")
        ET.ElementTree(root).write(folder / "preflight.xml")
        return {"exit_code": 0 if self.preflight_success else 1,
                "outcome": "success" if self.preflight_success else "failed",
                "started_at": runner.utc_now(), "finished_at": runner.utc_now(), "runner_start_error": None}

    def test_preflight_failure_retains_core_and_retry_uses_full_gui_only(self):
        core = runner.run_group("core", self.evidence, mode="validation")
        core_digest = runner.digest(core)
        self.assertEqual(runner.read_verified_manifest(core)["outcome"], "success")
        gui1 = runner.run_group("gui", self.evidence, mode="validation")
        self.assertEqual(runner.read_verified_manifest(gui1)["outcome"], "blocked")
        self.assertFalse((gui1.parent / "junit.xml").exists())
        self.assertEqual(self.calls.count("raw.log"), 1)
        with self.assertRaises(ValueError):
            runner.aggregate(self.evidence)
        self.assertEqual(runner.run_group("core", self.evidence, mode="validation"), core)
        self.assertEqual(self.calls.count("raw.log"), 1)
        self.preflight_success = True
        gui2 = runner.run_group("gui", self.evidence, mode="validation")
        self.assertEqual(runner.read_verified_manifest(gui2)["retry_of"], gui1.parent.name)
        self.assertEqual(runner.digest(core), core_digest)
        combined = runner.aggregate(self.evidence)
        self.assertEqual(combined["status"], "success")
        self.assertEqual(len(combined["history_manifests"]), 3)
        output = self.evidence / "coverage.json"
        self.assertEqual(runner.main(["aggregate", "--evidence-root", str(self.evidence), "--output", str(output)]), 0)
        self.assertEqual(runner.main(["aggregate", "--evidence-root", str(self.evidence), "--output", str(output)]), 2)

    def test_again_failed_gui_does_not_allow_third_attempt(self):
        runner.run_group("core", self.evidence, mode="validation")
        runner.run_group("gui", self.evidence, mode="validation")
        runner.run_group("gui", self.evidence, mode="validation")
        with self.assertRaisesRegex(ValueError, "retry"):
            runner.run_group("gui", self.evidence, mode="validation")
        self.assertEqual(self.calls.count("raw.log"), 1)
        self.assertEqual(self.calls.count("preflight.log"), 2)

    def test_core_failure_cannot_be_cleared_by_same_candidate_rerun(self):
        original = self.real_run
        def fail(command, folder, name, timeout, env=None):
            if name == "raw.log":
                (folder / "raw.log").write_text("AssertionError: isolated code failure")
                return {"exit_code": 1, "outcome": "failed", "started_at": runner.utc_now(),
                        "finished_at": runner.utc_now(), "runner_start_error": None}
            return original(command, folder, name, timeout, env)
        self.real_run = fail
        failed = runner.run_group("core", self.evidence, mode="validation")
        self.assertEqual(runner.read_verified_manifest(failed)["outcome"], "failed")
        self.real_run = original
        with self.assertRaisesRegex(ValueError, "core failure remains unresolved"):
            runner.run_group("core", self.evidence, mode="validation")

    def test_incomplete_history_and_corrupt_artifact_fail_closed(self):
        core = runner.run_group("core", self.evidence, mode="validation")
        (core.parent / "raw.log").write_text("changed")
        with self.assertRaisesRegex(ValueError, "corrupt"):
            runner.history(self.evidence)
        incomplete = self.evidence / "unfinished"
        incomplete.mkdir()
        runner.write_json(incomplete / "started.json", {})
        with self.assertRaisesRegex(ValueError, "unfinished"):
            runner.history(self.evidence)

    def test_mixed_candidate_and_later_failure_reject_aggregate(self):
        runner.run_group("core", self.evidence, mode="validation")
        self.preflight_success = True
        self.candidate["head"] = "e" * 40
        gui = runner.run_group("gui", self.evidence, mode="validation")
        with self.assertRaisesRegex(ValueError, "mixed"):
            runner.aggregate(self.evidence)
        self.assertEqual(runner.read_verified_manifest(gui)["outcome"], "success")


class HistoryRetrievalTests(unittest.TestCase):
    def test_prior_attempt_missing_artifact_fails_cli_without_reset(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            event = folder / "event.json"
            runner.write_json(event, {"pull_request": {"number": 99, "base": {"sha": "b" * 40}, "head": {"sha": "a" * 40, "ref": "task-branch"}}})
            responses = [{"workflow_runs": [{"id": 9, "status": "in_progress", "run_attempt": 2, "head_sha": "a" * 40,
                                             "head_repository": {"full_name": "owner/repo"}, "pull_requests": [{"number": 99}]}], "total_count": 1},
                         {"artifacts": [], "total_count": 0}]
            with patch.object(runner, "import_local_handoff", return_value={"task_id": "fixture-task", "archive_sha256": "f" * 64}), patch.object(runner, "snapshot", return_value={"head": "c" * 40, "tree": "d" * 40, "parents": ["b" * 40, "a" * 40]}), patch.dict("os.environ", {"GITHUB_EVENT_PATH": str(event), "GITHUB_REPOSITORY": "owner/repo",
                                            "GITHUB_RUN_ID": "9", "GITHUB_RUN_ATTEMPT": "2"}), patch.object(runner.subprocess, "check_output", side_effect=[json.dumps(value) for value in responses]) as query:
                self.assertEqual(runner.main(["restore-history", "--evidence-root", str(folder / "history")]), 2)
                self.assertIn("branch=task-branch", query.call_args_list[0].args[0][-1])
                self.assertNotIn("head_sha=", query.call_args_list[0].args[0][-1])

    def test_retry_suite_requires_initializer_trace_not_arbitrary_tclerror(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            message = '_tkinter.TclError: couldn\'t read file "C:/Tk/fonts.tcl": permission denied'
            root = ET.Element("testsuite")
            case = ET.SubElement(root, "testcase")
            ET.SubElement(case, "failure", message=message)
            ET.ElementTree(root).write(folder / "junit.xml")
            event = {"outcome": "failed", "when": "call", "subtest": False, "traceback": "setUp\n self.tk = _tkinter.create()\n" + message}
            (folder / "events.jsonl").write_text(json.dumps(event) + "\n")
            self.assertTrue(runner.retry_eligible(folder, {}, {"outcome": "success"}))
            event["traceback"] = "save_data\n" + message
            (folder / "events.jsonl").write_text(json.dumps(event) + "\n")
            self.assertFalse(runner.retry_eligible(folder, {}, {"outcome": "success"}))
