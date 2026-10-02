"""Explicit registration regression fixtures; Tk is replaced by a sentinel."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts import test_entrypoint_audit as audit

ROOT = Path(__file__).resolve().parents[1]
F1_SOURCE = """import tkinter as tk
class Helper:
    def make_window(self):
        return tk.Tk()
def test_instance_method():
    helper = Helper()
    return helper.make_window()
def factory():
    return tk.Tk
def test_returned_callable():
    return factory()()
def test_plain_logic():
    return 2 + 3
"""


def declared_registry(folder, entries):
    return {"schema": "zhuyin-test-group-registry/1", "source_scope": "tests",
            "source_review": {"basis": "Explicit independently asserted fixture groups",
                              "normalized_source_sha256": {path.relative_to(folder).as_posix(): audit.normalized_source_sha256(path)
                                                           for path in sorted(folder.rglob("*.py"))}},
            "entries": entries}


class RegisteredGroupsTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "tmp").mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT / "tmp")
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)

    def cli(self, registry):
        declaration = self.folder / "registry.json"
        declaration.write_text(json.dumps(registry), encoding="utf-8")
        output = self.folder / "inventory.json"
        command = [sys.executable, str(ROOT / "scripts/test_entrypoint_audit.py"),
                   "collect-groups", str(output), "--tests", str(self.folder), "--registry", str(declaration)]
        return subprocess.run(command, cwd=ROOT, capture_output=True, timeout=30), output

    def test_original_f1_instance_and_returned_call_are_gui_with_independent_sentinel(self):
        source = self.folder / "test_f1.py"
        source.write_text(F1_SOURCE, encoding="utf-8")
        entries = [{"prefix": "test_f1", "gui": ["test_instance_method", "test_returned_callable"], "core": ["test_plain_logic"]}]
        result, output = self.cli(declared_registry(self.folder, entries))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        inventory = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(inventory["gui_ids"], ["test_f1.test_instance_method", "test_f1.test_returned_callable"])
        self.assertEqual(inventory["core_ids"], ["test_f1.test_plain_logic"])
        spec = importlib.util.spec_from_file_location("f1_original_semantics", source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        class ReachedTk(Exception):
            pass
        with patch.object(module.tk, "Tk", side_effect=ReachedTk("original repro reaches Tk without opening a window")):
            with self.assertRaises(ReachedTk):
                module.test_instance_method()
            with self.assertRaises(ReachedTk):
                module.test_returned_callable()
            self.assertEqual(module.test_plain_logic(), 5)

    def test_finite_class_members_reject_new_unknown_and_missing_cases(self):
        registry = {"schema": "zhuyin-test-group-registry/1", "source_review": {"basis": "fixture"},
                    "entries": [{"prefix": "probe.Child", "core": ["test_known"], "gui": []}]}
        registration = audit.expand_registry(registry)
        record = {"identity": "probe.Child.test_known", "nodeid": "probe.py::Child::test_known"}
        audit.reconcile_registered_cases([record], registration)
        for records, message in (([], "missing"), ([record, {"identity": "probe.Child.test_new", "nodeid": "probe.py::Child::test_new"}], "unregistered"), ([record, record], "duplicate")):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                audit.reconcile_registered_cases(records, registration)
        for members in (["test_known", "test_known"],):
            changed = copy.deepcopy(registry)
            changed["entries"][0]["core"] = members
            with self.assertRaisesRegex(ValueError, "duplicate"):
                audit.expand_registry(changed)
        changed = copy.deepcopy(registry)
        changed["entries"][0]["gui"] = ["test_known"]
        with self.assertRaisesRegex(ValueError, "conflicting"):
            audit.expand_registry(changed)
        changed = copy.deepcopy(registry)
        changed["entries"][0]["core"] = ["test_*"]
        with self.assertRaisesRegex(ValueError, "invalid"):
            audit.expand_registry(changed)

    def test_real_cli_rejects_unregistered_missing_duplicate_and_conflicting_registration(self):
        (self.folder / "test_cases.py").write_text("def test_one(): pass\ndef test_two(): pass\n", encoding="utf-8")
        entry = {"prefix": "test_cases", "core": ["test_one", "test_two"], "gui": []}
        variants = [({"prefix": "test_cases", "core": ["test_one"], "gui": []}, "unregistered"),
                    ({"prefix": "test_cases", "core": ["test_one", "test_two", "test_absent"], "gui": []}, "missing"),
                    ({"prefix": "test_cases", "core": ["test_one", "test_two", "test_one"], "gui": []}, "duplicate"),
                    ({"prefix": "test_cases", "core": ["test_one", "test_two"], "gui": ["test_one"]}, "conflicting")]
        for entry, expected in variants:
            with self.subTest(expected=expected):
                result, output = self.cli(declared_registry(self.folder, [entry]))
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(output.exists())
                self.assertIn(expected.encode(), result.stdout + result.stderr)

    def test_inherited_cases_and_fixture_requirements_reject_core_then_expand_gui(self):
        source = self.folder / "test_inherited.py"
        source.write_text("import unittest\nimport pytest\nimport tkinter as tk\nclass Base(unittest.TestCase):\n    def setUp(self): self.root = tk.Tk()\n    def test_inherited(self): pass\nclass Child(Base): pass\n@pytest.fixture\ndef window(): return tk.Tk()\ndef test_fixture(window): pass\ndef test_pure(): pass\n", encoding="utf-8")
        entries = [{"prefix": "test_inherited.Base", "core": ["test_inherited"], "gui": []},
                   {"prefix": "test_inherited.Child", "core": ["test_inherited"], "gui": []},
                   {"prefix": "test_inherited", "core": ["test_fixture", "test_pure"], "gui": []}]
        result, output = self.cli(declared_registry(self.folder, entries))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(output.exists())
        for identity in ("Base.test_inherited", "Child.test_inherited", "test_inherited.test_fixture"):
            self.assertIn(identity.encode(), result.stdout + result.stderr)
        entries[0].update(core=[], gui=["test_inherited"])
        entries[1].update(core=[], gui=["test_inherited"])
        entries[2].update(core=["test_pure"], gui=["test_fixture"])
        result, output = self.cli(declared_registry(self.folder, entries))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        inventory = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(set(inventory["gui_ids"]), {"test_inherited.Base.test_inherited", "test_inherited.Child.test_inherited", "test_inherited.test_fixture"})
        self.assertEqual(inventory["core_ids"], ["test_inherited.test_pure"])

    def test_source_drift_and_new_source_cannot_reuse_registration(self):
        source = self.folder / "test_source.py"
        source.write_text("def test_known(): return 1\n", encoding="utf-8")
        registry = declared_registry(self.folder, [{"prefix": "test_source", "core": ["test_known"], "gui": []}])
        audit.validate_registry_sources(registry, self.folder)
        # Newline transport equivalence is explicit, not a recomputed approval.
        source.write_bytes(source.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
        audit.validate_registry_sources(registry, self.folder)
        source.write_text("def test_known(): return 2\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "changed"):
            audit.validate_registry_sources(registry, self.folder)
        result, output = self.cli(registry)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(output.exists())
        self.assertIn(b"registry reviewed source changed", result.stdout + result.stderr)

    def test_formal_classifier_never_uses_generic_provider_analyzer(self):
        def pure():
            return Path.cwd()
        item = SimpleNamespace(nodeid="test_explicit.py::test_pure", obj=pure, cls=None, _fixtureinfo=None)
        with patch.object(audit, "callable_uses_tk", side_effect=AssertionError("generic analyzer must not run")):
            self.assertEqual(audit.classify_item(item, {"test_explicit.test_pure": "core"}), "core")
        with self.assertRaisesRegex(ValueError, "unregistered"):
            audit.classify_item(item, {})

    def test_bounded_positive_guard_keeps_existing_alias_loop_context_and_imported_helper_paths(self):
        helper = self.folder / "positive_helper.py"
        helper.write_text("import tkinter as tk\ndef make_window(): return tk.Tk()\n", encoding="utf-8")
        source = self.folder / "positive_guard.py"
        source.write_text("import tkinter as tk\nfrom contextlib import contextmanager\nfrom positive_helper import make_window\ndef pure(): return 3\ndef test_direct_alias():\n    create = tk.Tk\n    return create()\ndef test_tuple():\n    first, second = pure, make_window\n    first()\n    return second()\ndef test_literal_loop():\n    for callback in (pure, make_window): callback()\n@contextmanager\ndef manager():\n    def callback(): return tk.Tk()\n    yield callback\ndef test_context():\n    with manager() as callback: return callback()\ndef invoke(callback): return callback()\ndef test_callback(): return invoke(make_window)\ndef ignore(callback): return 1\ndef test_unused_callback(): return ignore(make_window)\n", encoding="utf-8")
        sys.path.insert(0, str(self.folder))
        try:
            spec = importlib.util.spec_from_file_location("positive_guard", source)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            for name in ("test_direct_alias", "test_tuple", "test_literal_loop", "test_context", "test_callback"):
                with self.subTest(name=name):
                    self.assertTrue(audit.bounded_tk_requirement(getattr(module, name)))
                    item = SimpleNamespace(nodeid="positive_guard.py::" + name, obj=getattr(module, name), cls=None, _fixtureinfo=None)
                    with self.assertRaisesRegex(ValueError, "conflicts"):
                        audit.classify_item(item, {"positive_guard." + name: "core"})
            self.assertFalse(audit.bounded_tk_requirement(module.test_unused_callback))
        finally:
            sys.path.remove(str(self.folder))
            sys.modules.pop("positive_helper", None)
