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
ORIGINAL_RUN_PROCESS = runner.run_process


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

    def test_real_collection_cli_tracks_aliases_and_rejects_unknown_callbacks(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as temporary:
            folder = Path(temporary)
            source = folder / "test_alias.py"
            (folder / "pure_source_helper.py").write_text("def dumps(value): return value\n")
            source.write_text("import tkinter as tk\ndef make_window(): return tk.Tk()\ndef pure(): return 3\ndef test_alias():\n    factory = make_window\n    nested_alias = factory\n    return nested_alias()\ndef test_pure():\n    factory = pure\n    return factory()\n")
            source.write_text(source.read_text() + "from contextlib import contextmanager\n@contextmanager\ndef manager():\n    def callback(): return tk.Tk()\n    yield callback\n@contextmanager\ndef pure_manager():\n    def callback(): return 2\n    yield callback\ndef invoke(callback): return callback()\ndef test_context():\n    with manager() as callback: return callback()\ndef test_pure_context():\n    with pure_manager() as callback: return callback()\ndef test_distinct_callbacks():\n    invoke(pure)\n    invoke(make_window)\n")
            source.write_text(source.read_text() + "def test_literal_pure():\n    for callback in [lambda: 3, pure]: callback()\ndef test_literal_tk():\n    for callback in (pure, make_window): callback()\n")
            source.write_text(source.read_text() + "def test_local_class():\n    class Value:\n        result = 3\n    return Value()\n")
            source.write_text(source.read_text() + "import pure_source_helper as json\nclass PureFactory:\n    @classmethod\n    def make(cls): return cls()\nclass TkFactory:\n    @classmethod\n    def make(cls): return tk.Tk()\ndef test_classmethod_pure():\n    factory = PureFactory.make\n    return factory()\ndef test_classmethod_tk(): return TkFactory.make()\ndef test_tuple_alias():\n    first, second = pure, make_window\n    first()\n    second()\ndef test_fixed_getattr_tk():\n    factory = getattr(tk, 'Tk')\n    return factory()\ndef test_fixed_getattr_pure():\n    factory = getattr(json, 'dumps')\n    return factory(1)\n")
            source.write_text(source.read_text() + "def use(callback=None): return callback()\ndef test_kwargs_tk():\n    kwargs = {'callback': make_window}\n    return use(**kwargs)\ndef test_kwargs_pure(): return use(**{'callback': pure})\n")
            source.write_text(source.read_text() + "def test_kwargs_conditional_tk(): return use(**({'callback': make_window} if globals()['flag'] else {}))\ndef test_kwargs_written_tk():\n    kwargs = {'callback': pure}\n    kwargs['callback'] = make_window\n    return use(**kwargs)\ndef test_kwargs_other_key_pure():\n    kwargs = {'callback': pure}\n    kwargs['unused'] = 1\n    return invoke_options(**kwargs)\ndef invoke_options(callback, **kwargs): return callback()\ndef test_known_prefix_tk(): return use(make_window, **globals()['kwargs'])\ndef test_nested_prefix_pure():\n    def inject(callback, *args, **kwargs): return callback(*args, **kwargs)\n    thunk = lambda *args, **kwargs: inject(pure, *args, **kwargs)\n    return thunk()\ndef test_nested_prefix_tk():\n    def inject(callback, *args, **kwargs): return callback(*args, **kwargs)\n    thunk = lambda *args, **kwargs: inject(make_window, *args, **kwargs)\n    return thunk()\n")
            source.write_text(source.read_text() + "def test_kwargs_alias_write_tk():\n    kwargs = {'callback': pure}\n    alias = kwargs\n    alias['callback'] = make_window\n    return use(**kwargs)\n")
            source.write_text(source.read_text() + "def invoke_kw(**kwargs): return kwargs['a']()\ndef invoke_pos(*args): return args[0]()\ndef test_key_order_tk():\n    invoke_kw(a=pure, b=make_window)\n    invoke_kw(a=make_window, b=pure)\ndef test_position_order_tk():\n    invoke_pos(pure, make_window)\n    invoke_pos(make_window, pure)\ndef test_unused_callback_pure():\n    invoke_kw(a=pure, b=make_window)\n    invoke_pos(pure, make_window)\n")
            output = folder / "inventory.json"
            command = [sys.executable, str(ROOT / "scripts/test_entrypoint_audit.py"), "collect-groups", str(output), "--tests", str(folder)]
            result = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            inventory = json.loads(output.read_text())
            self.assertEqual(set(inventory["gui_ids"]), {"test_alias.test_alias", "test_alias.test_context", "test_alias.test_distinct_callbacks", "test_alias.test_literal_tk", "test_alias.test_classmethod_tk", "test_alias.test_tuple_alias", "test_alias.test_fixed_getattr_tk", "test_alias.test_kwargs_tk", "test_alias.test_kwargs_conditional_tk", "test_alias.test_kwargs_written_tk", "test_alias.test_known_prefix_tk", "test_alias.test_nested_prefix_tk", "test_alias.test_kwargs_alias_write_tk", "test_alias.test_key_order_tk", "test_alias.test_position_order_tk"})
            self.assertEqual(set(inventory["core_ids"]), {"test_alias.test_pure", "test_alias.test_pure_context", "test_alias.test_literal_pure", "test_alias.test_local_class", "test_alias.test_classmethod_pure", "test_alias.test_fixed_getattr_pure", "test_alias.test_kwargs_pure", "test_alias.test_kwargs_other_key_pure", "test_alias.test_nested_prefix_pure", "test_alias.test_unused_callback_pure"})
            unknowns = ["def invoke(**kwargs): return kwargs['callback']()\ndef test_alias(): return invoke(**globals()['kwargs'])\n",
                        "def invoke(*args): return args[0]()\ndef test_alias(): return invoke(*globals()['args'])\n",
                        "def use(callback=None): return callback()\ndef test_alias():\n    kwargs = {'callback': lambda: 1}\n    globals()['mutate'](kwargs)\n    return use(**kwargs)\n",
                        "def use(callback=None): return callback()\ndef test_alias():\n    kwargs = {'callback': lambda: 1}\n    kwargs[globals()['key']] = lambda: 2\n    return use(**kwargs)\n",
                        "def test_alias():\n    def inject(callback, *args, **kwargs): return callback(*args, **kwargs)\n    return inject(**globals()['kwargs'])\n",
                        "def use(callback=None): return callback()\ndef test_alias(): return use(**globals()['kwargs'])\n",
                        "def use(callback=None): return callback()\ndef test_alias(): return use(**{'callback': globals()['callback']})\n",
                        "def use(callback=None): return callback()\ndef test_alias():\n    kwargs = {'callback': lambda: 1}\n    kwargs['callback'] = globals()['callback']\n    return use(**kwargs)\n",
                        "def use(callback=None): return callback()\ndef test_alias(): return use(*globals()['args'])\n",
                        "def test_alias():\n    factory = globals()['callback']\n    return factory()\n",
                        "def invoke(callback): return callback()\ndef test_alias(): return invoke(object())\n",
                        "from contextlib import contextmanager\n@contextmanager\ndef manager(): yield globals()['callback']\ndef test_alias():\n    with manager() as callback: return callback()\n",
                        "def test_alias():\n    first = second\n    second = first\n    return first()\n",
                        "def test_alias():\n    for callback in globals()['callbacks']: callback()\n",
                        "def test_alias():\n    for callback in [lambda: 1, globals()['callback']]: callback()\n",
                        "def test_alias():\n    callbacks = [lambda: 1]\n    alias = callbacks\n    alias.append(globals()['callback'])\n    for callback in callbacks: callback()\n",
                        "def test_alias():\n    for name, callback in [('known', lambda: 1), globals()['row']]: callback()\n"]
            for index, body in enumerate(unknowns):
                with self.subTest(index=index):
                    source.write_text(body)
                    result = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=30)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(b"unresolved indirect callable", result.stdout + result.stderr)

    def test_real_cli_instance_and_returned_callees_share_source_resolution(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as temporary:
            folder = Path(temporary)
            source = folder / "test_receiver.py"
            source.write_text("import tkinter as tk\nclass Helper:\n    def make_window(self): return tk.Tk()\n    def invoke(self, callback): return callback()\n    def pure(self): return 3\nclass Child(Helper): pass\ndef factory(): return tk.Tk\ndef pure(): return 4\ndef pure_factory(): return pure\ndef receiver_factory(): return Helper()\ndef test_instance():\n    helper = Helper()\n    return helper.make_window()\ndef test_method_alias():\n    helper = Helper()\n    factory = helper.make_window\n    return factory()\ndef test_bound_argument():\n    helper = Helper()\n    method = helper.invoke\n    return method(tk.Tk)\ndef test_returned(): return factory()()\ndef test_returned_alias():\n    callback = factory()\n    return callback()\ndef test_returned_receiver(): return receiver_factory().make_window()\ndef test_inherited(): return Child().make_window()\ndef test_pure_instance(): return Helper().pure()\ndef test_pure_return(): return pure_factory()()\ndef test_plain(): return 2 + 3\n")
            output = folder / "inventory.json"
            command = [sys.executable, str(ROOT / "scripts/test_entrypoint_audit.py"), "collect-groups", str(output), "--tests", str(folder)]
            result = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            inventory = json.loads(output.read_text())
            gui = {"test_instance", "test_method_alias", "test_bound_argument", "test_returned", "test_returned_alias", "test_returned_receiver", "test_inherited"}
            self.assertEqual(set(inventory["gui_ids"]), {"test_receiver." + name for name in gui})
            self.assertEqual(set(inventory["core_ids"]), {"test_receiver.test_pure_instance", "test_receiver.test_pure_return", "test_receiver.test_plain"})
            spec = importlib.util.spec_from_file_location("receiver_sentinel_probe", source)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            class ReachedTk(Exception): pass
            with patch.object(module.tk, "Tk", side_effect=ReachedTk("reached constructor sentinel")):
                for name in sorted(gui):
                    with self.subTest(name=name), self.assertRaises(ReachedTk):
                        getattr(module, name)()

    def test_real_cli_unknown_callable_shapes_and_protocols_fail_closed(self):
        bodies = [
            "def test_unknown(receiver): return receiver.method()\n",
            "def factory(): return globals()['callback']\ndef test_unknown(): return factory()()\n",
            "def test_unknown(): return globals()['factory']()()\n",
            "def test_unknown(): return getattr(globals()['receiver'], globals()['attribute'])()\n",
            "class Helper:\n    def pure(self): return 1\ndef test_unknown():\n    helper = Helper()\n    helper.pure = globals()['callback']\n    return helper.pure()\n",
            "class Meta(type):\n    def __call__(cls): return globals()['callback']()\nclass Helper(metaclass=Meta): pass\ndef test_unknown(): return Helper()\n",
            "class Helper:\n    def __new__(cls): return globals()['receiver']\ndef test_unknown(): return Helper().method()\n",
            "class Helper:\n    @property\n    def callback(self): return globals()['callback']\ndef test_unknown(): return Helper().callback()\n",
            "from pathlib import Path\ndef test_unknown(): return Path.cwd()\n",
            "exec(compile(\"\\n\" * 10000 + \"def generated(): return 3\\n\", __file__, \"exec\"))\ndef factory(): return generated()\ndef test_unknown(): return factory()()\n",
        ]
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as temporary:
            folder = Path(temporary)
            for index, body in enumerate(bodies):
                with self.subTest(index=index):
                    source = folder / "test_unknown.py"
                    source.write_text(body)
                    output = folder / (str(index) + ".json")
                    command = [sys.executable, str(ROOT / "scripts/test_entrypoint_audit.py"), "collect-groups", str(output), "--tests", str(folder)]
                    result = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=30)
                    self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertFalse(output.exists())
                    self.assertIn(b"unresolved", result.stdout + result.stderr)
                    self.assertIn(b"UNRESOLVED CLASSIFICATION", result.stdout + result.stderr)
                    if "def generated" in body:
                        self.assertIn(b"unresolved return source: test_unknown.generated: OSError", result.stdout + result.stderr)

    def test_callable_binding_union_is_finite_and_keeps_distinct_targets(self):
        def pure(): return 1
        def possible_tk(): return 2  # Identity must differ even before source tracing.
        self.assertEqual(audit._callable_binding_key(pure), audit._callable_binding_key([[[pure]], pure]))
        self.assertNotEqual(audit._callable_binding_key(pure), audit._callable_binding_key([pure, possible_tk]))
        self.assertNotEqual(audit._callable_binding_key(pure), audit._callable_binding_key([pure, None]))
        self.assertNotEqual(audit._callable_binding_key({"a": pure, "b": possible_tk}), audit._callable_binding_key({"a": possible_tk, "b": pure}))
        self.assertNotEqual(audit._callable_binding_key((pure, possible_tk)), audit._callable_binding_key((possible_tk, pure)))
        self.assertEqual(audit._callable_binding_key({"a": (pure,)}), audit._callable_binding_key({"a": (pure,)}))
        self.assertNotEqual(audit._callable_binding_key("Tk"), audit._callable_binding_key("Tcl"))
        cycle = []; cycle.append(cycle)
        with self.assertRaisesRegex(ValueError, "cyclic"):
            audit._callable_binding_key(cycle)
        deep = (pure,)
        for _ in range(65):
            deep = (deep,)
        with self.assertRaisesRegex(ValueError, "depth"):
            audit._callable_binding_key(deep)
        nested = [pure]
        for _ in range(1200):
            nested = [nested]
        self.assertEqual(audit._callable_binding_key(nested), audit._callable_binding_key(pure))

    def test_bounded_alias_adapters_bind_sources_and_still_follow_target(self):
        import inspect
        import hashlib
        import tkinter
        import test_global_legacy_migration_v580 as native
        import test_legacy_migration_inspector_v580 as instance
        import global_exact_glyph_library as library
        owner = instance.MigrationInspectionTests
        caller = owner.test_one_plan_and_one_snapshot_with_post_snapshot_source_bracketing
        self.assertIs(audit._bounded_callable_alias(native.fixture_path_alias, None, "get_short_path"), audit._NATIVE_NON_TK_LEAF)
        self.assertIs(audit._bounded_callable_alias(caller, owner, "real"), library.GlobalExactGlyphRepository.load_snapshot)
        self.assertIs(audit._bounded_callable_alias(caller, owner(), "real"), library.GlobalExactGlyphRepository.load_snapshot)
        with patch.dict(audit.CALLABLE_ALIAS_SOURCE_HASHES, {"native_caller": "0" * 64}), self.assertRaisesRegex(ValueError, "source changed"):
            audit._bounded_callable_alias(native.fixture_path_alias, None, "get_short_path")
        with patch.object(library.GlobalExactGlyphRepository, "load_snapshot", tkinter.Tk), self.assertRaises(ValueError):
            audit._bounded_callable_alias(caller, owner, "real")
        with patch.object(library, "_load_snapshot_from_path", tkinter.Tk):
            self.assertTrue(audit.callable_uses_tk(caller, owner))
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as temporary:
            root = Path(temporary)
            (root / "tests").mkdir()
            path = root / "tests/test_global_legacy_migration_v580.py"
            path.write_text(inspect.getsource(native.fixture_path_alias).replace("GetShortPathNameW", "CreateWindowExW"))
            spec = importlib.util.spec_from_file_location("isolated_native_alias", path)
            probe = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(probe)
            expected = hashlib.sha256(inspect.getsource(probe.fixture_path_alias).encode()).hexdigest()
            with patch.object(audit, "ROOT", root), patch.dict(audit.CALLABLE_ALIAS_SOURCE_HASHES, {"native_caller": expected}), self.assertRaisesRegex(ValueError, "callsite changed"):
                audit._bounded_callable_alias(probe.fixture_path_alias, None, "get_short_path")
            path = root / "tests/test_legacy_migration_inspector_v580.py"
            path.write_text("class MigrationInspectionTests:\n" + inspect.getsource(caller).replace("self.repo.load_snapshot", "self.repo.load_inspection_snapshot"))
            spec = importlib.util.spec_from_file_location("isolated_instance_alias", path)
            probe = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(probe)
            changed = probe.MigrationInspectionTests.test_one_plan_and_one_snapshot_with_post_snapshot_source_bracketing
            expected = hashlib.sha256(inspect.getsource(changed).encode()).hexdigest()
            with patch.object(audit, "ROOT", root), patch.dict(audit.CALLABLE_ALIAS_SOURCE_HASHES, {"instance_caller": expected}), self.assertRaisesRegex(ValueError, "callsite changed"):
                audit._bounded_callable_alias(changed, owner, "real")

    def test_async_save_adapter_binds_initialization_and_recurses_into_real_service(self):
        import tkinter
        import test_review_async_save as fixture
        import review_save_service as service
        owner = fixture.AsyncReviewSaveTests
        caller = owner.test_worker_keeps_ui_unlocked_but_guards_all_mutating_actions_and_debounce
        target = audit._bounded_callable_alias(caller, owner(), "save")
        self.assertIs(target.__func__, service.ReviewSaveService.save_event)
        self.assertIs(target.__self__, service.ReviewSaveService)
        for key in ("async_caller", "async_setup", "headless", "queued_root", "reload_records", "invalidate_snapshot", "save_service", "save_target"):
            with self.subTest(key=key), patch.dict(audit.CALLABLE_ALIAS_SOURCE_HASHES, {key: "0" * 64}), self.assertRaisesRegex(ValueError, "source changed"):
                audit._bounded_callable_alias(caller, owner, "save")
        with patch.object(service.ReviewSaveService, "save_event", tkinter.Tk), self.assertRaises(ValueError):
            audit._bounded_callable_alias(caller, owner, "save")
        with patch.object(service.ReviewSaveService, "_save", tkinter.Tk):
            self.assertTrue(audit.callable_uses_tk(caller, owner))

    def test_busy_operation_adapter_rejects_guard_caller_and_method_drift(self):
        import test_review_async_save as fixture
        import review_gui as gui
        owner = fixture.AsyncReviewSaveTests
        caller = owner.test_worker_keeps_ui_unlocked_but_guards_all_mutating_actions_and_debounce
        self.assertIs(audit._bounded_callable_alias(caller, owner, "operation"), audit._BUSY_GUARDED_LEAF)
        keys = [key for key in audit.CALLABLE_ALIAS_SOURCE_HASHES if key.startswith("busy_")] + ["async_caller"]
        for key in keys:
            with self.subTest(key=key), patch.dict(audit.CALLABLE_ALIAS_SOURCE_HASHES, {key: "0" * 64}), self.assertRaisesRegex(ValueError, "source changed"):
                audit._bounded_callable_alias(caller, owner, "operation")
        with patch.object(gui.ReviewApp, "_save_busy", lambda self: False), self.assertRaises(ValueError):
            audit._bounded_callable_alias(caller, owner, "operation")
        with patch.object(gui.ReviewApp, "prev", gui.ReviewApp.next), self.assertRaises(ValueError):
            audit._bounded_callable_alias(caller, owner, "operation")

    def test_exact_mock_forwarding_adapter_preserves_actual_callbacks(self):
        import ast
        import tkinter
        import test_pdf_portability_binding as fixture
        sp = fixture.sp
        caller = fixture.test_bundle_actual_late_marker_prevents_commit
        node = ast.parse("original(*args, **kwargs)", mode="eval").body
        bindings = audit._bounded_forward_bindings(caller, node, sp.apply_direct_visual_actual_batch)
        self.assertIs(bindings["prewrite_guard"], sp._validate_actual_import_target)
        self.assertIs(bindings["apply_function"], sp.apply_verified_actual_group)
        self.assertIs(bindings["initialize_evidence"], sp.initialize_project_actual_evidence)
        for key in [key for key in audit.CALLABLE_ALIAS_SOURCE_HASHES if key.startswith("forward_")]:
            with self.subTest(key=key), patch.dict(audit.CALLABLE_ALIAS_SOURCE_HASHES, {key: "0" * 64}), self.assertRaisesRegex(ValueError, "source changed"):
                audit._bounded_forward_bindings(caller, node, sp.apply_direct_visual_actual_batch)
        with patch.object(sp, "initialize_project_actual_evidence", tkinter.Tk), self.assertRaises(ValueError):
            audit._bounded_forward_bindings(caller, node, sp.apply_direct_visual_actual_batch)
        with patch.object(sp, "json_load_strict", tkinter.Tk):
            self.assertTrue(audit.callable_uses_tk(bindings["prewrite_guard"]))
        self.assertEqual(audit._bounded_forward_bindings(caller, ast.parse("other(*args, **kwargs)", mode="eval").body, sp.apply_direct_visual_actual_batch), {})

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
        self.real_run = ORIGINAL_RUN_PROCESS
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
        original = ORIGINAL_RUN_PROCESS
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
