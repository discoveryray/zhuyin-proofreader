import tempfile
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import patch

from scripts.test_entrypoint_audit import (
    ReviewLoadTestsLoader, collect, compare_collections, gui_classes, verify_gui,
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
                    inventory = {"gui_ids": ["test_gui.RealTk.test_visible"]}
                    if not inner and count == 1:
                        self.assertEqual(verify_gui(report, inventory), 1)
                    else:
                        with self.assertRaises(ValueError):
                            verify_gui(report, inventory)

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


if __name__ == "__main__":
    unittest.main()
