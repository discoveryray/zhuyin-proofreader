import json
import subprocess
import tempfile
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import patch

from scripts.test_entrypoint_audit import (
    ReviewLoadTestsLoader, collect, compare_collections, gui_classes, gui_functions,
    inventory_from_collections, verify_gui,
)


class EntrypointAuditTests(unittest.TestCase):
    def test_identity_comparison_catches_equal_count_substitution(self):
        with self.assertRaises(ValueError):
            compare_collections(["m.C.a", "m.C.b"], ["m.C.a", "m.C.c"])
        with self.assertRaises(ValueError):
            compare_collections(["m.C.a"], ["m.C.a", "m.C.a"])
        result = compare_collections(["m.C.a", "m.C.b"], ["m.C.b", "m.C.a", "m.extra"])
        self.assertFalse(result["same_common_order"])
        self.assertEqual(result["pytest_only"], ["m.extra"])

    def test_gui_missing_skipped_failed_error_duplicate_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary) / "junit.xml"
            case = '<testcase classname="tests.test_gui.RealTk" name="test_visible">{}</testcase>'
            for inner in ("", "<skipped/>", "<failure/>", "<error/>"):
                for count in (0, 1, 2):
                    report.write_text("<testsuite>" + case.format(inner) * count + "</testsuite>")
                    inventory = {"gui_ids": ["test_gui.RealTk.test_visible"],
                                 "pytest_ids": ["test_gui.RealTk.test_visible"]}
                    if not inner and count == 1:
                        self.assertEqual(verify_gui(report, inventory), 1)
                    else:
                        with self.assertRaises(ValueError):
                            verify_gui(report, inventory)

    def test_every_collected_case_must_execute_successfully_exactly_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary) / "junit.xml"
            gui = '<testcase classname="tests.test_gui.RealTk" name="test_visible"/>'
            other = '<testcase classname="tests.test_other" name="test_plain">{}</testcase>'
            unknown = '<testcase classname="tests.test_unknown" name="test_extra"/>'
            inventory = {"gui_ids": ["test_gui.RealTk.test_visible"],
                         "pytest_ids": ["test_gui.RealTk.test_visible", "test_other.test_plain"]}
            report.write_text("<testsuite>" + gui + other.format("") + "</testsuite>")
            self.assertEqual(verify_gui(report, inventory), 1)
            rejected = (
                gui,
                gui + other.format("<skipped/>"),
                gui + other.format("<failure/>"),
                gui + other.format("<error/>"),
                gui + other.format("") * 2,
                gui + other.format("") + unknown,
            )
            for cases in rejected:
                with self.subTest(cases=cases):
                    report.write_text("<testsuite>" + cases + "</testsuite>")
                    with self.assertRaises(ValueError):
                        verify_gui(report, inventory)
            report.write_text("<testsuite>" + gui + other.format("") + "</testsuite>")
            with self.assertRaisesRegex(ValueError, "missing or duplicate collected pytest"):
                verify_gui(report, {"gui_ids": inventory["gui_ids"]})
            with self.assertRaisesRegex(ValueError, "missing or duplicate collected pytest"):
                verify_gui(report, {"gui_ids": inventory["gui_ids"],
                                    "pytest_ids": inventory["pytest_ids"] * 2})
            with self.assertRaisesRegex(ValueError, "required GUI case absent"):
                verify_gui(report, {"gui_ids": inventory["gui_ids"],
                                    "pytest_ids": ["test_other.test_plain"]})

    def test_custom_loader_cannot_silently_bypass_entrypoint_review(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "test_example.py").write_text("def load_tests(loader, tests, pattern): return tests\n")
            with self.assertRaisesRegex(ValueError, "custom unittest load_tests"):
                gui_classes(folder)

    def test_module_load_tests_bindings_rejected_before_hook_even_with_same_ids(self):
        source = (
            "import unittest\n"
            "class Case(unittest.TestCase):\n"
            "    def test_kept(self): pass\n"
        )
        hook = lambda loader, tests, pattern: tests
        provider = ModuleType("entrypoint_hook_provider")
        provider.hook = provider.load_tests = hook
        bindings = (
            "def load_tests(loader, tests, pattern): return tests\n",
            "load_tests = lambda loader, tests, pattern: tests\n",
            "load_tests: object = lambda loader, tests, pattern: tests\n",
            "alias = load_tests = lambda loader, tests, pattern: tests\n",
            "load_tests, other = (lambda loader, tests, pattern: tests), None\n",
            "if True: load_tests = lambda loader, tests, pattern: tests\n",
            "from entrypoint_hook_provider import hook as load_tests\n",
            "from entrypoint_hook_provider import load_tests\n",
            "globals()['load_tests'] = lambda loader, tests, pattern: tests\n",
        )

        def identities(suite):
            result = []
            for case in suite:
                result.extend(identities(case) if isinstance(case, unittest.TestSuite) else [case.id()])
            return result

        with patch.dict(sys.modules, {provider.__name__: provider}):
            plain = ModuleType("test_same_ids")
            exec(source, plain.__dict__)
            expected = identities(ReviewLoadTestsLoader().loadTestsFromModule(plain))
            self.assertEqual(expected, ["test_same_ids.Case.test_kept"])
            for binding in bindings:
                with self.subTest(binding=binding):
                    module = ModuleType("test_same_ids")
                    exec(source + binding, module.__dict__)
                    # The hook returns the default suite, so identity comparison alone passes.
                    ordinary = unittest.TestLoader().loadTestsFromModule(module)
                    self.assertEqual(identities(ordinary), expected)
                    self.assertEqual(
                        compare_collections(identities(ordinary), identities(ordinary))["pytest_only"],
                        [],
                    )
                    with self.assertRaisesRegex(ValueError, "custom unittest load_tests"):
                        ReviewLoadTestsLoader().loadTestsFromModule(module)

    def test_collection_stops_on_imported_load_tests_hook(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tests = root / "tests"
            tests.mkdir()
            (tests / "hook_provider.py").write_text(
                "def hook(loader, tests, pattern): return tests\n", encoding="utf-8"
            )
            (tests / "test_hook_probe.py").write_text(
                "import unittest\n"
                "from hook_provider import hook as load_tests\n"
                "class Case(unittest.TestCase):\n"
                "    def test_kept(self): pass\n",
                encoding="utf-8",
            )
            original_path = sys.path[:]
            try:
                with patch("scripts.test_entrypoint_audit.ROOT", root):
                    with self.assertRaisesRegex(ValueError, "custom unittest load_tests"):
                        collect("unittest", root / "inventory.json")
            finally:
                sys.path[:] = original_path
                sys.modules.pop("test_hook_probe", None)
                sys.modules.pop("hook_provider", None)

    def test_inherited_gui_cases_are_mandatory_across_modules(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tests = root / "tests"
            tests.mkdir()
            (tests / "test_gui_parent.py").write_text(
                "import unittest\n"
                "import tkinter as tk\n"
                "class Parent(unittest.TestCase):\n"
                "    def setUp(self): self.root = tk.Tk()\n"
                "    def test_base(self): pass\n"
                "class LocalChild(Parent):\n"
                "    def test_local(self): pass\n",
                encoding="utf-8",
            )
            (tests / "test_gui_child.py").write_text(
                "import test_gui_parent\n"
                "class CrossChild(test_gui_parent.Parent):\n"
                "    def test_cross(self): pass\n",
                encoding="utf-8",
            )
            original_path = sys.path[:]
            try:
                with patch("scripts.test_entrypoint_audit.ROOT", root):
                    output = root / "unittest.json"
                    collect("unittest", output)
                    collection = json.loads(output.read_text(encoding="utf-8"))
            finally:
                sys.path[:] = original_path
                sys.modules.pop("test_gui_parent", None)
                sys.modules.pop("test_gui_child", None)

            ids = collection["ids"]
            inventory = compare_collections(ids, ids)
            inventory["gui_ids"] = collection["gui_ids"]
            self.assertEqual(set(inventory["gui_ids"]), set(ids))
            self.assertEqual(len(ids), 5)
            report = root / "junit.xml"
            entries = []
            for name in ids:
                module, class_name, method = name.rsplit(".", 2)
                content = "<skipped/>" if class_name == "CrossChild" else ""
                entries.append(
                    f'<testcase classname="tests.{module}.{class_name}" name="{method}">'
                    f"{content}</testcase>"
                )
            report.write_text("<testsuite>" + "".join(entries) + "</testsuite>", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "required GUI case did not execute"):
                verify_gui(report, inventory)

    def test_pytest_only_tk_functions_are_mandatory_even_when_skipped(self):
        repo = Path(__file__).resolve().parents[1]
        fixture_parent = repo / "tmp"
        fixture_parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=fixture_parent) as temporary:
            root = Path(temporary)
            tests = root / "tests"
            tests.mkdir()
            (tests / "test_gui_probe.py").write_text(
                "import unittest\n"
                "import tkinter as tk\n"
                "from tkinter import Tk as Root\n"
                "import pytest\n"
                "class Existing(unittest.TestCase):\n"
                "    def setUp(self):\n"
                "        self.root = tk.Tk()\n"
                "        self.addCleanup(self.root.destroy)\n"
                "    def test_existing(self): pass\n"
                "@pytest.mark.parametrize('case', [1, 2])\n"
                "@pytest.mark.skip(reason='prove GUI verifier rejects skipped functions')\n"
                "def test_new_gui(case):\n"
                "    root = tk.Tk()\n"
                "    root.destroy()\n"
                "def make_alias_root(): return Root()\n"
                "@pytest.mark.skip(reason='prove GUI verifier rejects skipped functions')\n"
                "def test_alias_gui():\n"
                "    root = make_alias_root()\n"
                "    root.destroy()\n"
                "TkRoot = tk.Tk\n"
                "@pytest.mark.skip(reason='prove GUI verifier rejects skipped functions')\n"
                "def test_module_alias_gui():\n"
                "    root = TkRoot()\n"
                "    root.destroy()\n"
                "@pytest.mark.skip(reason='prove GUI verifier rejects skipped functions')\n"
                "def test_local_alias_gui():\n"
                "    LocalRoot = tk.Tk\n"
                "    root = LocalRoot()\n"
                "    root.destroy()\n",
                encoding="utf-8",
            )
            original_path = sys.path[:]
            try:
                with patch("scripts.test_entrypoint_audit.ROOT", root):
                    unittest_output = root / "unittest.json"
                    collect("unittest", unittest_output)
            finally:
                sys.path[:] = original_path
                sys.modules.pop("test_gui_probe", None)

            pytest_output = root / "pytest.json"
            script = (
                "import sys; from pathlib import Path; "
                "from scripts import test_entrypoint_audit as audit; "
                "audit.ROOT = Path(sys.argv[1]); "
                "audit.collect('pytest', Path(sys.argv[2]))"
            )
            subprocess.run(
                [sys.executable, "-c", script, str(root), str(pytest_output)],
                cwd=repo, check=True, capture_output=True, text=True, timeout=30,
            )
            unittest_collection = json.loads(unittest_output.read_text(encoding="utf-8"))
            pytest_ids = json.loads(pytest_output.read_text(encoding="utf-8"))
            prefixes = gui_functions(tests)
            self.assertEqual(set(prefixes), {
                "test_gui_probe.test_new_gui", "test_gui_probe.test_alias_gui",
                "test_gui_probe.test_module_alias_gui",
                "test_gui_probe.test_local_alias_gui",
            })
            inventory = inventory_from_collections(unittest_collection, pytest_ids, prefixes)
            self.assertEqual(set(inventory["gui_ids"]), set(pytest_ids))
            self.assertEqual(len(pytest_ids), 6)
            report = root / "junit.xml"
            subprocess.run(
                [sys.executable, "-m", "pytest", str(tests), "-q", "-rs",
                 f"--junitxml={report}", f"--basetemp={root / 'pytest-temp'}",
                 f"--rootdir={root}"],
                cwd=repo, check=True, capture_output=True, text=True, timeout=30,
            )
            from xml.etree import ElementTree as ET
            junit_ids = {
                case.get("classname", "").removeprefix("tests.") + "." + case.get("name", "")
                for case in ET.parse(report).iter("testcase")
            }
            self.assertEqual(junit_ids, set(pytest_ids))
            with self.assertRaisesRegex(ValueError, "required GUI case did not execute"):
                verify_gui(report, inventory)
            with self.assertRaisesRegex(ValueError, "Tk test function has no collected pytest case"):
                inventory_from_collections(
                    unittest_collection, unittest_collection["ids"], prefixes
                )

    def test_wildcard_tk_import_requires_inventory_review(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "test_wildcard.py").write_text(
                "from tkinter import *\n"
                "def test_gui():\n"
                "    root = Tk()\n"
                "    root.destroy()\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "wildcard tkinter import"):
                gui_functions(folder)

    def test_cross_module_tk_helper_skip_cannot_escape_junit_gate(self):
        repo = Path(__file__).resolve().parents[1]
        fixture_parent = repo / "tmp"
        fixture_parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=fixture_parent) as temporary:
            root = Path(temporary)
            tests = root / "tests"
            tests.mkdir()
            (tests / "cross_gui_helper.py").write_text(
                "import tkinter as tk\n"
                "def create_root(): return tk.Tk()\n", encoding="utf-8"
            )
            (tests / "test_cross_module_existing.py").write_text(
                "import unittest\nimport tkinter as tk\n"
                "class Existing(unittest.TestCase):\n"
                "    def setUp(self):\n"
                "        self.root = tk.Tk()\n"
                "        self.addCleanup(self.root.destroy)\n"
                "    def test_ok(self): pass\n", encoding="utf-8"
            )
            (tests / "test_cross_module_imported.py").write_text(
                "import pytest\nfrom cross_gui_helper import create_root\n"
                "@pytest.mark.skip(reason='cross-module GUI bypass regression')\n"
                "def test_imported_gui():\n"
                "    root = create_root()\n"
                "    root.destroy()\n", encoding="utf-8"
            )
            original_path = sys.path[:]
            try:
                with patch("scripts.test_entrypoint_audit.ROOT", root):
                    unittest_output = root / "unittest.json"
                    collect("unittest", unittest_output)
            finally:
                sys.path[:] = original_path
                sys.modules.pop("test_cross_module_existing", None)
                sys.modules.pop("test_cross_module_imported", None)
                sys.modules.pop("cross_gui_helper", None)
            script = (
                "import sys; from pathlib import Path; "
                "from scripts import test_entrypoint_audit as audit; "
                "audit.ROOT = Path(sys.argv[1]); "
                "audit.collect('pytest', Path(sys.argv[2]))"
            )
            pytest_output = root / "pytest.json"
            subprocess.run(
                [sys.executable, "-c", script, str(root), str(pytest_output)],
                cwd=repo, check=True, capture_output=True, text=True, timeout=30,
            )
            report = root / "junit.xml"
            subprocess.run(
                [sys.executable, "-m", "pytest", str(tests), "-q", "-rs",
                 f"--junitxml={report}", f"--basetemp={root / 'pytest-temp'}",
                 f"--rootdir={root}"],
                cwd=repo, check=True, capture_output=True, text=True, timeout=30,
            )
            unittest_collection = json.loads(unittest_output.read_text(encoding="utf-8"))
            pytest_ids = json.loads(pytest_output.read_text(encoding="utf-8"))
            inventory = inventory_from_collections(
                unittest_collection, pytest_ids, gui_functions(tests)
            )
            self.assertEqual(inventory["pytest_only"], [
                "test_cross_module_imported.test_imported_gui"
            ])
            self.assertEqual(inventory["gui_ids"], [
                "test_cross_module_existing.Existing.test_ok"
            ])
            from xml.etree import ElementTree as ET
            actual_ids = {
                case.get("classname", "").removeprefix("tests.") + "." + case.get("name", "")
                for case in ET.parse(report).iter("testcase")
            }
            self.assertEqual(actual_ids, set(pytest_ids))
            with self.assertRaisesRegex(ValueError, "collected pytest case"):
                verify_gui(report, inventory)


if __name__ == "__main__":
    unittest.main()
