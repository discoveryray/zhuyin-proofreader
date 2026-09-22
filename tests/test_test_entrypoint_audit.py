import tempfile
from pathlib import Path
import unittest

from scripts.test_entrypoint_audit import compare_collections, gui_classes, verify_gui


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


if __name__ == "__main__":
    unittest.main()
