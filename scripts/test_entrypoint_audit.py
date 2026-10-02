"""Compare actual collection identities; verify required GUI cases really ran.

Collection happens in separate interpreters so imports cannot mask discovery
differences. This is evidence of coverage, not a substitute for running tests.
"""
from __future__ import annotations

import argparse
import ast
import builtins
import contextlib
from collections import Counter
import json
import inspect
import dataclasses
import functools
import hashlib
import textwrap
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
_MISSING = object()


class ReviewLoadTestsLoader(unittest.TestLoader):
    """Stop discovery before a module's custom unittest hook can run."""

    def loadTestsFromModule(self, module, *, pattern=None):
        if getattr(module, "load_tests", _MISSING) is not _MISSING:
            raise ValueError(
                f"custom unittest load_tests needs explicit runner review: {module.__name__}"
            )
        return super().loadTestsFromModule(module, pattern=pattern)


def compare_collections(unittest_ids, pytest_ids):
    missing = sorted(set(unittest_ids) - set(pytest_ids))
    duplicates = {name: count for name, count in Counter(pytest_ids).items() if count != 1}
    if missing or duplicates or not unittest_ids or not pytest_ids:
        raise ValueError(f"collection mismatch: missing={missing}, duplicates={duplicates}")
    return {"unittest_ids": unittest_ids, "pytest_ids": pytest_ids,
            "pytest_only": sorted(set(pytest_ids) - set(unittest_ids)),
            "same_common_order": unittest_ids == [name for name in pytest_ids if name in set(unittest_ids)]}


def gui_classes(tests):
    """Find classes directly constructing real Tk roots."""
    result = []
    for path in sorted(tests.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        tk_names = tk_constructor_names(tree)
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "load_tests":
                raise ValueError(f"custom unittest load_tests needs explicit runner review: {path}")
            if isinstance(node, ast.ClassDef) and calls_tk(node, tk_names):
                result.append(f"{path.stem}.{node.name}.")
    if not result:
        raise ValueError("no real Tk classes discovered")
    return result


def tk_constructor_names(tree):
    """Resolve direct tkinter Tk imports and simple module-level aliases."""
    if any(
        isinstance(node, ast.ImportFrom) and node.module == "tkinter"
        and any(alias.name == "*" for alias in node.names)
        for node in tree.body
    ):
        raise ValueError("wildcard tkinter import needs explicit GUI inventory review")
    names = {
        alias.asname or alias.name
        for node in tree.body if isinstance(node, ast.ImportFrom) and node.module == "tkinter"
        for alias in node.names if alias.name == "Tk"
    }
    aliases = [node for node in tree.body if isinstance(node, (ast.Assign, ast.AnnAssign))]
    while True:
        previous = len(names)
        for node in aliases:
            value = node.value
            if value is None:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                names.update(tk_aliases(target, value, names))
        if len(names) == previous:
            return names


def tk_aliases(target, value, names):
    if isinstance(target, ast.Name) and (
        isinstance(value, ast.Name) and value.id in names
        or isinstance(value, ast.Attribute) and value.attr == "Tk"
    ):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)) and isinstance(value, (ast.Tuple, ast.List)):
        if len(target.elts) == len(value.elts):
            return set().union(*(
                tk_aliases(child, source, names)
                for child, source in zip(target.elts, value.elts)
            ))
    return set()


def calls_tk(node, tk_names):
    names = set(tk_names)
    assignments = [item for item in ast.walk(node) if isinstance(item, (ast.Assign, ast.AnnAssign))]
    while True:
        previous = len(names)
        for item in assignments:
            if item.value is None:
                continue
            targets = item.targets if isinstance(item, ast.Assign) else [item.target]
            for target in targets:
                names.update(tk_aliases(target, item.value, names))
        if len(names) == previous:
            break
    return any(
        isinstance(call, ast.Call) and (
            isinstance(call.func, ast.Attribute) and call.func.attr == "Tk"
            or isinstance(call.func, ast.Name) and call.func.id in names
        )
        for call in ast.walk(node)
    )


def gui_functions(tests):
    """Find pytest test functions with direct or local-helper Tk construction.

    Imported helper behavior is outside this bounded source scan and needs
    explicit runner-contract review before use in a new GUI test.
    """
    result = []
    for path in sorted(tests.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        tk_names = tk_constructor_names(tree)
        functions = {
            node.name: node for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

        def uses_tk(name, seen):
            if name in seen:
                return False
            node = functions[name]
            if calls_tk(node, tk_names):
                return True
            seen = seen | {name}
            called = {
                call.func.id for call in ast.walk(node)
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                and call.func.id in functions
            }
            fixtures = {
                arg.arg for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
                if arg.arg in functions
            }
            return any(uses_tk(other, seen) for other in called | fixtures)

        result.extend(
            f"{path.stem}.{name}" for name in functions
            if name.startswith("test_") and uses_tk(name, set())
        )
    return result


def gui_ids_from_cases(cases, direct_prefixes):
    """Include collected subclasses whose MRO reaches a direct Tk class."""
    direct_classes = {prefix.removesuffix(".") for prefix in direct_prefixes}
    gui_ids = [case.id() for case in cases if any(
        f"{base.__module__}.{base.__name__}" in direct_classes
        for base in type(case).__mro__
    )]
    if not gui_ids or any(
        not any(case.id().startswith(prefix) for case in cases)
        for prefix in direct_prefixes
    ):
        raise ValueError("Tk class has no collected tests")
    return gui_ids


def inventory_from_collections(unittest_collection, pytest_ids, function_prefixes):
    result = compare_collections(unittest_collection["ids"], pytest_ids)
    pytest_only = set(result["pytest_only"])
    function_ids = [name for name in pytest_ids if name in pytest_only and any(
        name == prefix or name.startswith(prefix + "[") for prefix in function_prefixes
    )]
    if any(not any(
        name == prefix or name.startswith(prefix + "[") for name in function_ids
    ) for prefix in function_prefixes):
        raise ValueError("Tk test function has no collected pytest case")
    result["gui_ids"] = unittest_collection["gui_ids"] + function_ids
    if len(result["gui_ids"]) != len(set(result["gui_ids"])):
        raise ValueError("duplicate required GUI identity")
    return result


def verify_gui(report, inventory):
    required = inventory["gui_ids"]
    if not required:
        raise ValueError("missing required GUI inventory")
    collected = inventory.get("pytest_ids")
    if not isinstance(collected, list) or not collected or any(
        count != 1 for count in Counter(collected).values()
    ):
        raise ValueError("missing or duplicate collected pytest case identity")
    if any(name not in collected for name in required):
        raise ValueError("required GUI case absent from pytest collection")
    cases = {}
    for case in ET.parse(report).iter("testcase"):
        name = case.get("classname", "").removeprefix("tests.") + "." + case.get("name", "")
        cases.setdefault(name, []).append(case)
    if Counter(collected) != Counter({name: len(entries) for name, entries in cases.items()}):
        raise ValueError("collected pytest case identity mismatch with JUnit")
    for name in required:
        entries = cases.get(name, [])
        if len(entries) != 1 or any(entries[0].find(tag) is not None for tag in ("skipped", "failure", "error")):
            raise ValueError(f"required GUI case did not execute successfully exactly once: {name}")
    for name in collected:
        entry = cases[name][0]
        if any(entry.find(tag) is not None for tag in ("skipped", "failure", "error")):
            raise ValueError(f"collected pytest case did not execute successfully: {name}")
    return len(required)


def collect(runner, output, tests=None):
    sys.path.insert(0, str(ROOT))
    if runner in {"unittest", "identities"}:
        loader = ReviewLoadTestsLoader()
        suite = loader.discover(str(tests or ROOT / "tests"), pattern="test_*.py")

        def flatten(item):
            if isinstance(item, unittest.TestSuite):
                for child in item:
                    yield from flatten(child)
            else:
                yield item

        cases = list(flatten(suite))
        ids = [case.id() for case in cases]
        if loader.errors:
            raise ValueError("unittest discovery failed: " + "\n".join(loader.errors))
        gui_ids = gui_ids_from_cases(cases, gui_classes(ROOT / "tests")) if runner == "unittest" else []
    else:
        import pytest

        class Inventory:
            ids = []

            def pytest_collection_finish(self, session):
                for item in session.items:
                    parts = item.nodeid.split("::")
                    self.ids.append(".".join([Path(parts[0]).stem, *parts[1:]]))

        plugin = Inventory()
        result = pytest.main([str(ROOT / "tests"), "--collect-only", "-q"], plugins=[plugin])
        if result != 0:
            raise ValueError(f"pytest discovery failed: {result}")
        ids = plugin.ids
    collection = {"ids": ids, "gui_ids": gui_ids} if runner in {"unittest", "identities"} else ids
    output.write_text(json.dumps(collection, ensure_ascii=False, indent=2), encoding="utf-8")


def loaded_source_module(name, caller_source=None):
    """Resolve pytest's package/unpackaged alias only by exact repository file.

    Collection may import test_x while a local helper names tests.test_x. Never
    import new code or resolve a same-name module from a different source file.
    """
    direct = sys.modules.get(name)
    if direct is not None:
        return direct
    if not name or not all(part.isidentifier() for part in name.split(".")):
        return None
    roots = [ROOT.resolve()]
    if caller_source and Path(caller_source).resolve().is_relative_to(ROOT.resolve()):
        roots.append(Path(caller_source).resolve().parent)
    expected = {root.joinpath(*name.split(".")).with_suffix(".py").resolve() for root in roots}
    expected = {path for path in expected if path.is_relative_to(ROOT.resolve()) and path.is_file()}
    if not expected:
        return None
    candidates = {}
    for module in tuple(sys.modules.values()):
        source = vars(module).get("__file__") if hasattr(module, "__dict__") else None
        if source and Path(source).resolve() in expected:
            candidates[id(module)] = module
    if len(candidates) > 1:
        raise ValueError("ambiguous imported source alias requires inventory review: " + name)
    return next(iter(candidates.values()), None)


# Four reviewed opaque alias contracts only. Every source/callsite mismatch stops; these
# are not generic permissions for native APIs or dynamically assigned instances.
CALLABLE_ALIAS_SOURCE_HASHES = {'native_caller': 'ca39fdf2ceff04312cf16e1b048a428290d7110adf017f105580bfff3cf9527b',
 'instance_caller': '5f1cf2330aa35b416c377c0d0549b9caaa47083257c413290c9399952195c977',
 'instance_setup': 'bc87f5ee824dcc081f370fb845e58e041f1a7d8863e91997699ad0eea8ff0f24',
 'factory': '0270cc11b46bb0c7824bfa95bff87ec743717197ca4d19a9eae2fc12c33a606e',
 'repository_class': 'e69931a20e8ad852fc43183638b984fdf50d04c712d73b00547738e326d8bc6a',
 'target': '839947c76d4b989a5cd221e7fcc542bc854795cb393446b7a6b04a3382e063f2',
 'async_caller': 'c93e5659e2feb22afc8e0ac800efba13f3a2d3bb14ef1131393f983d27a3bffe',
 'async_setup': '64d70ea9db2ec6862854f53b21f0d2dc7c4aa0244368108c549aab09fde88e55',
 'headless': '59f7541024a3147f30e3db9410837e81fe321c08e691036e29fca21bb0ae0980',
 'queued_root': 'fbbf217b5b4a34eb2e4b02f5151580885cc01bb443526eba1e4a1fa273b45366',
 'reload_records': 'fb7c10376033a551c94d17cc1c7cf992793c0366b8c10726ab5819b3d2bf39f4',
 'invalidate_snapshot': '8f9870a09407990fd5f45900858b5d33e763eab626ae4ee70f9f0db90cd12ae8',
 'save_service': '7c0089733dda5bcbf2a0553fa70d7073e61c6c300368ef06a25e76ba2e7baebc',
 'save_target': '41994bdb97b4493121de535fe434e01b8dd09095f23b03f751e6dc3841e28dac',
 'busy_check': '46d43d74386671777daec8687b0b07715597037dc22e629df952a73f939ec519',
 'busy_wrapper': 'c9b4f1a5534e0df01ec9c6368eeadfa32483757ebde273d79c48121356b871ba',
 'forward_caller': '9078571b0ccb73b5a8666688583c73a05667ed6ac368e049ea6a683dcf2fb386',
 'forward_main': '1b9a7fd4ffdc49ce79058f63b1eda131e84074e308ee6b8a92baeb4a014f0e80',
 'forward_bundle': '40af18d46e5d59369918141cc9b8c54f43c49f056c4f073e5dc12ca6a1a9e872',
 'forward_import': '803829fc6b0af690de65de64af12dc18c9392172a05b16e06631a1071a7a6f00',
 'forward_serialized': 'e73e001986c6498b9fb326caca642a2db55d40d5ed743b7f5e7c026d313dc6aa',
 'forward_target': '8d47b0610fd8a51ec657c697edfe18956dbd5a15d22fc45a63e9180314245c67',
 'forward_validate': '053f8cb3814a1c9328bfa9a21a673595f617b2e3080c3be83e2682964f8a10e6',
 'forward_apply': '8d69d05347956efa0002bb49e41a78c8d189043406549285b3fde5fdc3ec3e7e',
 'forward_initialize': '8f651b3f20f16ba2d045d2b855e9ec34cfc24b2737fb7eb070b82d241fc7466e',
 'busy_entry_prev': '7640541e78a4598119455fa2ac7f8f47469aa361232eb9719526c03e13aa47a1',
 'busy_entry_next': '95be23136108f34cc13d3f0187544b1a3ba17f74b7eb370ed450a4613aa8df31',
 'busy_entry_defer_current': '445b87872682407fef4224fa0731797967733798980475df4875960e813984b2',
 'busy_entry_revisit_deferred': 'e8a4c9c9331dc215a8ddd7ad3e29707aa02df12689332336adcccd52777193b6',
 'busy_entry_resolve_expected': '4399e82654fcceacda3af50b466264be64f8806f67110dddb0ca599a11209d3f',
 'busy_body_resolve_expected': 'db7fd860eb409f54bc3dac746b2ee95ea07d1a87a9d02fec091a2c015dc75e30',
 'busy_entry_clear': '4399e82654fcceacda3af50b466264be64f8806f67110dddb0ca599a11209d3f',
 'busy_body_clear': '6371b0f366dc3d2629184d0959d6379dd07c46ba4ad772ac14f3e358e0b3d61b',
 'busy_entry_report': 'fedacac8841d433cf03e8029984d0a8355345255da661fe656e5aeb185fd3c26',
 'busy_entry_apply_staged_actuals': '71cbc747861fff2bae67a1dc313e4b974f15ccb58edfd60cc6fb2d30ae972d0f',
 'busy_entry_correct_actual': '4399e82654fcceacda3af50b466264be64f8806f67110dddb0ca599a11209d3f',
 'busy_body_correct_actual': 'df1ab2113b019145d61b2d5c586cc3ffc70c63c8827f9154abbb8d419536e06f',
 'busy_entry_create_reusable_expected_rule': '28b2bb81b72120e8560c8c8de91c39090a64b1184779e4129e6b0bf7c2429c6e'}
_NO_CALLABLE_ADAPTER = object()
_NATIVE_NON_TK_LEAF = object()
_BUSY_GUARDED_LEAF = object()
_MISSING_KEYWORD = object()


def _checked_callable_source(value, key, filename, qualname):
    source = inspect.getsourcefile(value) if inspect.isfunction(value) or inspect.ismethod(value) or inspect.isclass(value) else None
    if (not source or Path(source).resolve() != (ROOT / filename).resolve()
            or getattr(value, "__qualname__", None) != qualname
            or hashlib.sha256(inspect.getsource(value).encode()).hexdigest() != CALLABLE_ALIAS_SOURCE_HASHES[key]):
        raise ValueError("bounded callable adapter source changed: " + key)


def _bounded_callable_alias(function, owner, name):
    if (function.__qualname__, name) not in {
            ("fixture_path_alias", "get_short_path"),
            ("MigrationInspectionTests.test_one_plan_and_one_snapshot_with_post_snapshot_source_bracketing", "real"),
            ("AsyncReviewSaveTests.test_worker_keeps_ui_unlocked_but_guards_all_mutating_actions_and_debounce", "save"),
            ("AsyncReviewSaveTests.test_worker_keeps_ui_unlocked_but_guards_all_mutating_actions_and_debounce", "operation")}:
        return _NO_CALLABLE_ADAPTER
    source = inspect.getsourcefile(function)
    if not source or not Path(source).resolve().is_relative_to(ROOT.resolve()):
        return _NO_CALLABLE_ADAPTER
    relative = Path(source).resolve().relative_to(ROOT.resolve()).as_posix()
    identity = (relative, function.__qualname__, name)
    native = identity == ("tests/test_global_legacy_migration_v580.py", "fixture_path_alias", "get_short_path")
    instance = identity == ("tests/test_legacy_migration_inspector_v580.py", "MigrationInspectionTests.test_one_plan_and_one_snapshot_with_post_snapshot_source_bracketing", "real")
    async_save = (relative == "tests/test_review_async_save.py" and function.__qualname__ == "AsyncReviewSaveTests.test_worker_keeps_ui_unlocked_but_guards_all_mutating_actions_and_debounce" and name in {"save", "operation"})
    if not (native or instance or async_save):
        return _NO_CALLABLE_ADAPTER

    checked = _checked_callable_source

    checked(function, "native_caller" if native else "instance_caller" if instance else "async_caller", relative, function.__qualname__)
    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    values = [node.value for node in ast.walk(tree) if isinstance(node, ast.Assign)
              and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)]
    expression = ('ctypes.WinDLL("kernel32", use_last_error=True).GetShortPathNameW' if native
                  else 'self.repo.load_snapshot' if instance else 'app._save_service.save_event')
    busy_methods = ("prev", "next", "defer_current", "revisit_deferred", "resolve_expected", "clear", "report", "apply_staged_actuals", "correct_actual", "create_reusable_expected_rule")
    if async_save and name == "operation":
        values = [node.iter for node in ast.walk(tree) if isinstance(node, ast.For)
                  and isinstance(node.target, ast.Name) and node.target.id == "operation"]
        expression = "(" + ", ".join("app." + method for method in busy_methods) + ")"
    expected = ast.dump(ast.parse(expression, mode="eval").body)
    if len(values) != 1 or ast.dump(values[0]) != expected:
        raise ValueError("bounded callable adapter callsite changed")
    if native:
        return _NATIVE_NON_TK_LEAF
    owner = owner if inspect.isclass(owner) else type(owner)
    if async_save:
        if owner.__qualname__ != "AsyncReviewSaveTests":
            raise ValueError("bounded callable adapter owner changed")
        checked(owner.setUp, "async_setup", relative, "AsyncReviewSaveTests.setUp")
        headless = function.__globals__.get("headless_app")
        checked(headless, "headless", "tests/test_staged_review_navigation_v580.py", "headless_app")
        checked(headless.__globals__.get("QueuedRoot"), "queued_root", "tests/review_save_test_support.py", "QueuedRoot")
        gui = function.__globals__.get("gui")
        if gui is None or Path(gui.__file__).resolve() != (ROOT / "review_gui.py").resolve():
            raise ValueError("bounded callable adapter target module changed")
        app = inspect.getattr_static(gui, "ReviewApp")
        checked(app.reload_records, "reload_records", "review_gui.py", "ReviewApp.reload_records")
        checked(app._invalidate_save_snapshot, "invalidate_snapshot", "review_gui.py", "ReviewApp._invalidate_save_snapshot")
        service = inspect.getattr_static(gui, "ReviewSaveService")
        checked(service, "save_service", "review_save_service.py", "ReviewSaveService")
        target = inspect.getattr_static(service, "save_event")
        checked(target, "save_target", "review_save_service.py", "ReviewSaveService.save_event")
        if name == "operation":
            # This exact fixture holds its save worker until after the loop;
            # every reviewed method first rejects _save_busy(). Source drift of
            # the assertion, hold/release, loop, guard or closure invalidates it.
            checked(app._save_busy, "busy_check", "review_gui.py", "ReviewApp._save_busy")
            checked(gui.guarded_review_action, "busy_wrapper", "review_gui.py", "guarded_review_action")
            for method in busy_methods:
                entry = inspect.getattr_static(app, method)
                decorated = method in {"resolve_expected", "clear", "correct_actual"}
                qualname = "guarded_review_action.<locals>.run" if decorated else "ReviewApp." + method
                checked(entry, "busy_entry_" + method, "review_gui.py", qualname)
                if decorated:
                    body = inspect.getclosurevars(entry).nonlocals.get("method")
                    checked(body, "busy_body_" + method, "review_gui.py", "ReviewApp." + method)
            return _BUSY_GUARDED_LEAF
        # Bind only the proven analysis owner; do not instantiate a service or GUI.
        return target.__get__(service, service)
    if owner.__qualname__ != "MigrationInspectionTests":
        raise ValueError("bounded callable adapter owner changed")
    checked(owner.setUp, "instance_setup", relative, "MigrationInspectionTests.setUp")
    module = function.__globals__.get("lib")
    if module is None or Path(module.__file__).resolve() != (ROOT / "global_exact_glyph_library.py").resolve():
        raise ValueError("bounded callable adapter target module changed")
    repository = inspect.getattr_static(module, "GlobalExactGlyphRepository")
    filename = "global_exact_glyph_library.py"
    checked(repository, "repository_class", filename, "GlobalExactGlyphRepository")
    checked(repository.resolved, "factory", filename, "GlobalExactGlyphRepository.resolved")
    target = inspect.getattr_static(repository, "load_snapshot")
    checked(target, "target", filename, "GlobalExactGlyphRepository.load_snapshot")
    return target  # Caller still recursively checks this method and its helpers.


def _bounded_forward_bindings(function, node, target):
    """One reviewed mock forwarding fixture; callback identities remain real."""
    if (function.__qualname__ != "test_bundle_actual_late_marker_prevents_commit"
            or not isinstance(node.func, ast.Name) or node.func.id != "original"):
        return {}
    checked = _checked_callable_source
    checked(function, "forward_caller", "tests/test_pdf_portability_binding.py", function.__qualname__)
    sp = function.__globals__.get("sp")
    if sp is None or Path(sp.__file__).resolve() != (ROOT / "standalone_proofread.py").resolve():
        raise ValueError("bounded forwarding adapter module changed")
    entries = {"main": ("forward_main", "main"), "import_gpt_decision_bundle": ("forward_bundle", "import_gpt_decision_bundle"),
               "import_actual_occurrence_decisions": ("forward_import", "import_actual_occurrence_decisions"),
               "_serialized_user_project_entry": ("forward_serialized", "_serialized_user_project_entry"),
               "_validate_actual_import_target": ("forward_validate", "_validate_actual_import_target"),
               "initialize_project_actual_evidence": ("forward_initialize", "initialize_project_actual_evidence")}
    for name, (key, qualname) in entries.items():
        checked(inspect.getattr_static(sp, name), key, "standalone_proofread.py", qualname)
    checked(target, "forward_target", "actual_review.py", "apply_direct_visual_actual_batch")
    if target is not inspect.getattr_static(sp, "apply_direct_visual_actual_batch"):
        raise ValueError("bounded forwarding adapter target changed")
    apply = inspect.getattr_static(sp, "apply_verified_actual_group")
    checked(apply, "forward_apply", "actual_review.py", "apply_verified_actual_group")
    # The hash-bound real callsite supplies these three callbacks; its omitted
    # acknowledge has the target's checked default None. Never default an
    # arbitrary unknown **kwargs call to a pure callback.
    return {"prewrite_guard": sp._validate_actual_import_target,
            "apply_function": apply, "initialize_evidence": sp.initialize_project_actual_evidence,
            "acknowledge": type(None)}


def _callable_binding_key(value):
    """Finite alternatives; real mappings/tuples preserve keys and positions."""
    def abstract(current, ancestors=frozenset(), depth=0):
        if depth > 64:
            raise ValueError("callable binding exceeds bounded container depth")
        if isinstance(current, list):
            # Lists are this analyzer's alternative sets, not argument tuples.
            pending, active, visited, leaves = [(current, False)], set(ancestors), set(), set()
            while pending:
                item, closing = pending.pop()
                if isinstance(item, list):
                    if closing:
                        active.remove(id(item))
                        visited.add(id(item))
                    elif id(item) in active:
                        raise ValueError("cyclic callable binding requires inventory review")
                    elif id(item) not in visited:
                        active.add(id(item))
                        pending.append((item, True))
                        pending.extend((child, False) for child in item)
                else:
                    leaves.update(abstract(item, frozenset(active), depth + 1))
            return tuple(sorted(leaves, key=repr))
        if isinstance(current, (dict, tuple)):
            if id(current) in ancestors:
                raise ValueError("cyclic callable binding requires inventory review")
            ancestors = ancestors | {id(current)}
            if isinstance(current, tuple):
                return (("tuple", tuple(abstract(item, ancestors, depth + 1) for item in current)),)
            entries = []
            for key, item in current.items():
                if key is not None and not isinstance(key, (str, int, float, bool, bytes)):
                    raise ValueError("unproven callable binding mapping key")
                entries.append(((type(key).__name__, repr(key)), abstract(item, ancestors, depth + 1)))
            return (("mapping", tuple(sorted(entries, key=repr))),)
        if inspect.ismethod(current):
            return (("method", id(current.__func__), id(current.__self__)),)
        if current is None:
            return (("unknown",),)
        if isinstance(current, (str, int, float, bool, bytes)):
            return (("literal", type(current).__name__, repr(current)),)
        return (("identity", id(current)),)
    return abstract(value)


def callable_uses_tk(function, owner=None, seen=None, bindings=None):
    """Trace collected functions, inherited setup, fixtures and imported helpers.

    Dynamic tkinter lookup is deliberately rejected. Native/library callables
    without Python source are leaves; tkinter constructors are recognized first.
    This is a conservative classification, never an omission permission.
    """
    seen = set() if seen is None else seen
    function = inspect.unwrap(function)
    if inspect.ismethod(function):
        owner = function.__self__
        function = function.__func__
    bindings = {} if bindings is None else bindings
    invocation = (id(function), id(owner), tuple(sorted((key, _callable_binding_key(value)) for key, value in bindings.items())))
    if invocation in seen:
        return False
    seen.add(invocation)
    if getattr(function, "__module__", "") == "tkinter" and getattr(function, "__name__", "") in {"Tk", "Toplevel"}:
        return True
    module_name = getattr(function, "__module__", "") or ""
    if module_name == "tkinter" or module_name.startswith("tkinter."):
        # Widgets, variables, styles and dialogs can request the default root.
        # Tcl(useTk=False) is the explicit non-window interpreter adapter.
        return getattr(function, "__name__", "") != "Tcl"
    if module_name.split(".")[0] in sys.stdlib_module_names:
        return False
    if inspect.isclass(function):
        initializer = function.__init__
        if dataclasses.is_dataclass(function) and getattr(getattr(initializer, "__code__", None), "co_filename", None) == "<string>":
            factories = [field.default_factory for field in dataclasses.fields(function)
                         if field.default_factory is not dataclasses.MISSING]
            if hasattr(function, "__post_init__"):
                factories.append(function.__post_init__)
            return any(callable_uses_tk(factory, function, seen) for factory in factories)
        return callable_uses_tk(initializer, function, seen)
    if not inspect.isfunction(function):
        return False
    source_file = inspect.getsourcefile(function)
    if source_file and ("site-packages" in Path(source_file).parts or Path(source_file).is_relative_to(Path(sys.base_prefix))):
        return False
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    except (OSError, TypeError, IndentationError, SyntaxError) as error:
        raise ValueError(f"unclassifiable Python callable: {function}") from error
    namespace = dict(function.__globals__)
    namespace.update(inspect.getclosurevars(function).nonlocals)
    # Imports inside a test/helper are not present in __globals__. Resolve only
    # already imported modules; never execute a new helper during collection.
    for imported in ast.walk(tree):
        if not isinstance(imported, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(imported, ast.ImportFrom):
            module_name = imported.module or ""
            if module_name == "tkinter":
                if any(alias.name == "*" for alias in imported.names):
                    raise ValueError("wildcard local tkinter import needs inventory review")
                if any(alias.name in {"Tk", "Toplevel"} for alias in imported.names):
                    return True
            module = loaded_source_module(module_name, source_file)
            for alias in imported.names:
                if module is not None and hasattr(module, alias.name):
                    namespace[alias.asname or alias.name] = inspect.getattr_static(module, alias.name)
                elif module_name.split(".")[0] not in sys.stdlib_module_names:
                    raise ValueError("unresolved local imported helper requires inventory review: " + module_name)
        else:
            for alias in imported.names:
                module = loaded_source_module(alias.name, source_file)
                if module is not None:
                    namespace[alias.asname or alias.name.split(".")[0]] = module
                elif alias.name.split(".")[0] not in sys.stdlib_module_names:
                    raise ValueError("unresolved local imported module requires inventory review: " + alias.name)
    names = {name for name, value in namespace.items()
             if getattr(value, "__module__", "") == "tkinter"
             and getattr(value, "__name__", "") in {"Tk", "Toplevel"}}
    if calls_tk(tree, names):
        return True

    # Track local callable aliases without executing test/helper code. Keep all
    # possible assignments: a branch or later rebinding cannot hide a Tk path.
    assignments = {}

    def bind_literal(target, value):
        if isinstance(target, ast.Name):
            assignments.setdefault(target.id, []).append(value)
        elif isinstance(target, (ast.Tuple, ast.List)):
            if isinstance(value, ast.IfExp):
                bind_literal(target, value.body)
                bind_literal(target, value.orelse)
            elif isinstance(value, (ast.Tuple, ast.List)) and len(target.elts) == len(value.elts):
                for part, element in zip(target.elts, value.elts):
                    bind_literal(part, element)
            else:
                for part in target.elts:
                    bind_literal(part, None)

    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                bind_literal(target, node.value)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.For, ast.comprehension)):
            continue
        if isinstance(node.target, ast.Name):
            assignments.setdefault(node.target.id, []).append(node.iter)
        elif isinstance(node.target, (ast.Tuple, ast.List)) and isinstance(node.iter, (ast.Tuple, ast.List)):
            for row in node.iter.elts:
                if not isinstance(row, (ast.Tuple, ast.List)) or len(row.elts) != len(node.target.elts):
                    for target in node.target.elts:
                        if isinstance(target, ast.Name):
                            assignments.setdefault(target.id, []).append(None)
                    continue
                for target, value in zip(node.target.elts, row.elts):
                    if isinstance(target, ast.Name):
                        assignments.setdefault(target.id, []).append(value)
    for inner in ast.walk(tree):
        if not isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef)) or inner.name == function.__name__:
            continue
        parameters = [*inner.args.posonlyargs, *inner.args.args]
        called = {node.func.id for node in ast.walk(inner) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        sites = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == inner.name]
        direct_names = {id(node.func) for node in sites}
        escapes = any(isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
                      and node.id == inner.name and id(node) not in direct_names for node in ast.walk(tree))
        for index, parameter in enumerate(parameters):
            if parameter.arg not in called:
                continue
            for site in sites:
                prefix = []
                for value in site.args:
                    if isinstance(value, ast.Starred):
                        break
                    prefix.append(value)
                explicit = {value.arg: value.value for value in site.keywords if value.arg}
                value = prefix[index] if index < len(prefix) else explicit.get(parameter.arg)
                assignments.setdefault(parameter.arg, []).append(value)
            if escapes or not sites:
                assignments.setdefault(parameter.arg, []).append(None)

    mapping_writes = {}
    for statement in ast.walk(tree):
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        for target in statement.targets if isinstance(statement, ast.Assign) else [statement.target]:
            if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
                mapping_writes.setdefault(target.value.id, []).append((target.slice, statement.value))
    # A fixed-key write through a simple alias affects every name for that
    # mapping. Preserve the original and new values as possible alternatives.
    mapping_aliases = {}
    for name, values in assignments.items():
        for value in values:
            if isinstance(value, ast.Name):
                mapping_aliases.setdefault(name, set()).add(value.id)
                mapping_aliases.setdefault(value.id, set()).add(name)
    original_writes = dict(mapping_writes)
    for name, writes in original_writes.items():
        pending, visited = [name], set()
        while pending:
            alias = pending.pop()
            if alias in visited:
                continue
            visited.add(alias)
            pending.extend(mapping_aliases.get(alias, set()))
            existing = mapping_writes.setdefault(alias, [])
            keys = {(id(key), id(value)) for key, value in existing}
            existing.extend((key, value) for key, value in writes if (id(key), id(value)) not in keys)
    changed_containers = {node.func.value.id for node in ast.walk(tree)
                          if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                          and isinstance(node.func.value, ast.Name)
                          and node.func.attr in {"append", "extend", "insert", "remove", "pop", "clear", "__setitem__", "update", "setdefault"}}
    changed_containers.update(name for name, writes in mapping_writes.items()
                              if any(not isinstance(key, ast.Constant) or not isinstance(key.value, str) for key, _ in writes))
    # Passing a mapping as a regular argument can let an opaque callee mutate it.
    escaped_containers = {value.id for call in ast.walk(tree) if isinstance(call, ast.Call)
                          for value in [*call.args, *(kw.value for kw in call.keywords if kw.arg)]
                          if isinstance(value, ast.Name)}
    # A mutation through a simple alias also makes the original container opaque.
    for _ in assignments:
        for name, values in assignments.items():
            aliases = {value.id for value in values if isinstance(value, ast.Name)}
            if name in changed_containers or aliases & changed_containers:
                changed_containers.update(aliases | {name})
            if name in escaped_containers or aliases & escaped_containers:
                escaped_containers.update(aliases | {name})
    nested = {node.name for node in ast.walk(tree)
              if ((isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name != function.__name__)
                  or (isinstance(node, ast.ClassDef) and not node.bases and not node.keywords and not node.decorator_list))}
    parameters = dict(inspect.signature(function).parameters)
    context_aliases = {}

    def resolve(node, trail=frozenset()):
        if isinstance(node, ast.Name):
            if node.id in {"self", "cls"}:
                return owner
            adapted = _bounded_callable_alias(function, owner, node.id)
            if adapted is not _NO_CALLABLE_ADAPTER:
                return adapted
            if node.id in changed_containers or node.id in mapping_writes:
                return None
            if node.id in context_aliases:
                return context_aliases[node.id]
            if node.id in assignments:
                if node.id in trail:
                    if node.id in parameters:
                        default = parameters[node.id].default
                        return bindings.get(node.id, type(None) if default is None else None)
                    return None
                values = [resolve(value, trail | {node.id}) for value in assignments[node.id]]
                return values[0] if len(values) == 1 else values
            if node.id in nested:
                return ast.FunctionDef  # Its body is already traversed below.
            if node.id in parameters:
                if node.id in bindings:
                    return bindings[node.id]
                default = parameters[node.id].default
                return (type(None) if default is None else default) if default is not inspect.Parameter.empty else None
            return namespace.get(node.id, getattr(builtins, node.id, None))
        if isinstance(node, ast.Attribute):
            base = resolve(node.value, trail)
            if base is not None:
                try:
                    member = inspect.getattr_static(base, node.attr)
                    if isinstance(member, classmethod) and inspect.isclass(base):
                        return member.__get__(None, base)
                    return member.__func__ if isinstance(member, staticmethod) else member
                except AttributeError:
                    pass
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "getattr" and len(node.args) == 2 and not node.keywords):
            base, attribute = resolve(node.args[0], trail), resolve(node.args[1], trail)
            if base is not None and isinstance(attribute, str):
                try:
                    return inspect.getattr_static(base, attribute)
                except AttributeError:
                    return None
            return None
        if isinstance(node, ast.Dict):
            if all(isinstance(key, ast.Constant) and isinstance(key.value, (str, int, float, bool, bytes, type(None))) for key in node.keys):
                return {key.value: resolve(value, trail) for key, value in zip(node.keys, node.values)}
            return None
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            base, key = resolve(node.value, trail), node.slice.value
            if isinstance(base, dict) and isinstance(key, (str, int, float, bool, bytes, type(None))):
                return base.get(key)
            if isinstance(base, tuple) and isinstance(key, int) and -len(base) <= key < len(base):
                return base[key]
            return None
        if isinstance(node, ast.Constant):
            return type(None) if node.value is None else node.value
        if isinstance(node, ast.Lambda):
            return ast.Lambda  # Its body is already traversed below.
        if isinstance(node, (ast.List, ast.Tuple)):
            return [resolve(value, trail) for value in node.elts]
        if isinstance(node, ast.BoolOp):
            return [resolve(value, trail) for value in node.values]
        if isinstance(node, ast.IfExp):
            return [resolve(node.body, trail), resolve(node.orelse, trail)]
        return None

    def literal_keywords(node, trail=frozenset()):
        # Only finite literal alternatives and fixed-key writes prove **kwargs.
        # Opaque expansion/escape must never silently apply callee defaults.
        if isinstance(node, ast.Name):
            if node.id in trail or node.id in changed_containers or node.id in escaped_containers:
                return None
            values = assignments.get(node.id, [])
            choices = []
            for value in values:
                found = literal_keywords(value, trail | {node.id})
                if found is None:
                    return None
                choices.extend(found)
            if not choices:
                return None
            for key, value in mapping_writes.get(node.id, []):
                for choice in choices:
                    choice[key.value] = [choice.get(key.value, _MISSING_KEYWORD), resolve(value)]
            return choices
        if isinstance(node, ast.IfExp):
            left, right = literal_keywords(node.body, trail), literal_keywords(node.orelse, trail)
            return left + right if left is not None and right is not None else None
        if not isinstance(node, ast.Dict):
            return None
        result = {}
        for key, value in zip(node.keys, node.values):
            if not isinstance(key, ast.Constant) or not isinstance(key.value, str) or key.value in result:
                return None
            result[key.value] = resolve(value)
        return [result]

    for statement in ast.walk(tree):
        if not isinstance(statement, ast.With):
            continue
        for item in statement.items:
            if not isinstance(item.optional_vars, ast.Name) or not isinstance(item.context_expr, ast.Call):
                continue
            provider = resolve(item.context_expr.func)
            if (not inspect.isfunction(provider) or not hasattr(provider, "__wrapped__")
                    or inspect.getsourcefile(provider) != inspect.getsourcefile(contextlib.contextmanager)):
                continue
            generator = inspect.unwrap(provider)
            if not inspect.isgeneratorfunction(generator):
                continue
            provider_tree = ast.parse(textwrap.dedent(inspect.getsource(generator)))
            yields = [node for node in ast.walk(provider_tree) if isinstance(node, (ast.Yield, ast.YieldFrom))]
            inner = {node.name for node in ast.walk(provider_tree)
                     if isinstance(node, ast.FunctionDef) and node.name != generator.__name__}
            if len(yields) == 1 and isinstance(yields[0], ast.Yield) and isinstance(yields[0].value, ast.Name) and yields[0].value.id in inner:
                # Analyze the generator and its nested closure body; never infer
                # purity merely because a callable came from a with statement.
                context_aliases[item.optional_vars.id] = provider

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id in {"eval", "exec"}:
            # Test code may exercise eval/exec of isolated fixture strings. A
            # dynamic constructor reference, however, cannot be assigned core.
            if any(isinstance(arg, ast.Name) and getattr(namespace.get(arg.id), "__name__", "") == "tkinter" for arg in node.args):
                raise ValueError("dynamic tkinter construction requires inventory review")
        if isinstance(node.func, ast.Name) and node.func.id == "getattr" and node.args:
            base = resolve(node.args[0])
            if getattr(base, "__name__", "") == "tkinter" and (len(node.args) != 2 or not isinstance(resolve(node.args[1]), str)):
                raise ValueError("dynamic tkinter lookup requires inventory review")
        target = resolve(node.func)
        pending = target[:] if isinstance(target, list) else [target]
        while pending:
            target = pending.pop()
            if isinstance(target, list):
                pending.extend(target)
                continue
            if target is None and isinstance(node.func, (ast.Name, ast.Subscript)):
                raise ValueError("unresolved indirect callable requires inventory review: " + function.__qualname__ + ":" + ast.unparse(node.func))
            if target in (_NATIVE_NON_TK_LEAF, _BUSY_GUARDED_LEAF, ast.FunctionDef, ast.Lambda):
                continue
            if isinstance(target, (staticmethod, classmethod)):
                target = target.__func__
            # Follow application/test helpers, not pytest/unittest internals.
            module = getattr(target, "__module__", "") or ""
            if target is not None and not module.startswith(("unittest", "pytest", "_pytest")):
                call_bindings = {}
                if inspect.isfunction(target) or inspect.ismethod(target):
                    signature = inspect.signature(target)
                    arguments = []
                    opaque_unpacking = False
                    for arg in node.args:
                        if isinstance(arg, ast.Starred):
                            opaque_unpacking = True
                            break
                        arguments.append(resolve(arg))
                    if (isinstance(node.func, ast.Attribute) and signature.parameters
                            and next(iter(signature.parameters)) in {"self", "cls"}):
                        arguments.insert(0, owner)
                    keywords = {}
                    for keyword in node.keywords:
                        choices = [{keyword.arg: resolve(keyword.value)}] if keyword.arg else literal_keywords(keyword.value)
                        if choices is None:
                            opaque_unpacking = True
                            continue
                        values = {}
                        for key in set().union(*(set(choice) for choice in choices)):
                            default = signature.parameters[key].default if key in signature.parameters else inspect.Parameter.empty
                            default = None if default is inspect.Parameter.empty else type(None) if default is None else default
                            pending = [choice.get(key, _MISSING_KEYWORD) for choice in choices]
                            alternatives = []
                            while pending:
                                value = pending.pop()
                                if isinstance(value, list):
                                    pending.extend(value)
                                else:
                                    alternatives.append(default if value is _MISSING_KEYWORD else value)
                            values[key] = alternatives[0] if len(alternatives) == 1 else alternatives
                        if set(values) & set(keywords):
                            opaque_unpacking = True
                        else:
                            keywords.update(values)
                    try:
                        call_bindings = dict(signature.bind_partial(*arguments, **keywords).arguments)
                        if opaque_unpacking:
                            # Explicit prefix/keyword bindings cannot be replaced
                            # in a valid Python call; duplicates raise TypeError.
                            call_bindings = {name: call_bindings.get(name) for name in signature.parameters}
                    except TypeError:
                        call_bindings = {name: None for name in signature.parameters}
                call_bindings.update(_bounded_forward_bindings(function, node, target))
                if callable_uses_tk(target, owner, seen, call_bindings):
                    return True
    return False


@functools.lru_cache(maxsize=None)
def _cached_uses_tk(function, owner):
    return callable_uses_tk(function, owner)


# These baseline fixtures write and execute literal source that creates real Tk.
# Source hashes make the adapter fail closed when either opaque path changes.
OPAQUE_GUI_ADAPTERS = {'test_test_entrypoint_audit.EntrypointAuditTests.test_pytest_only_tk_functions_are_mandatory_even_when_skipped': '702e123fba48168398e8555cdf66b46f49f7d63f158c91e18ce38a2cb7ed3b09', 'test_test_entrypoint_audit.EntrypointAuditTests.test_cross_module_tk_helper_skip_cannot_escape_junit_gate': 'd83588da65cfe10328a5d829beb32d7718b4aadfde985244bc473ea633b840cf'}

def classify_item(item):
    parts = item.nodeid.split("::")
    identity = ".".join([Path(parts[0]).stem, *parts[1:]])
    if identity in OPAQUE_GUI_ADAPTERS:
        source_hash = hashlib.sha256(inspect.getsource(item.obj).encode()).hexdigest()
        if source_hash != OPAQUE_GUI_ADAPTERS[identity]:
            raise ValueError("opaque GUI adapter source changed: " + identity)
        return "gui"
    owner = getattr(item, "cls", None)
    functions = [item.obj]
    if owner is not None:
        functions.extend(getattr(owner, name) for name in
                         ("setUp", "setUpClass", "setup_method", "setup_class")
                         if hasattr(owner, name))
    functions.extend(definition.func for definitions in
                     getattr(item, "_fixtureinfo", None).name2fixturedefs.values()
                     for definition in definitions)
    unresolved = []
    for function in functions:
        try:
            if _cached_uses_tk(function, owner):
                return "gui"  # A proved required-Tk path suffices for isolation.
        except (ValueError, RecursionError) as error:
            unresolved.append(str(error))
    if unresolved:
        raise ValueError("; ".join(unresolved))
    return "core"


def grouped_collection(output, tests=None):
    sys.path.insert(0, str(ROOT))
    import pytest
    tests = Path(tests or ROOT / "tests").resolve()

    class Groups:
        records = []

        def pytest_collection_finish(self, session):
            failures = []
            for item in session.items:
                parts = item.nodeid.split("::")
                try:
                    group = classify_item(item)
                except (ValueError, RecursionError) as error:
                    failure = item.nodeid + ": " + str(error)
                    failures.append(failure)
                    print("UNRESOLVED CLASSIFICATION " + failure, flush=True)
                    continue
                self.records.append({"identity": ".".join([Path(parts[0]).stem, *parts[1:]]),
                                     "nodeid": item.nodeid, "group": group})
            if failures:
                raise ValueError("unresolved classification: " + str(len(failures)) + " identities; see retained diagnostics")

    with tempfile.TemporaryDirectory() as temporary:
        identity_path = Path(temporary) / "unittest.json"
        subprocess.run([sys.executable, __file__, "_identities", str(identity_path), "--tests", str(tests)], cwd=ROOT, check=True)
        unittest_ids = json.loads(identity_path.read_text(encoding="utf-8"))["ids"]
    _cached_uses_tk.cache_clear()
    plugin = Groups()
    code = pytest.main([str(tests), "--collect-only", "-q"], plugins=[plugin])
    if code:
        raise ValueError(f"group collection failed: {code}")
    records = plugin.records
    result = {"schema": "zhuyin-test-groups/1", "records": records,
              "pytest_ids": [record["identity"] for record in records],
              "core_ids": [record["identity"] for record in records if record["group"] == "core"],
              "gui_ids": [record["identity"] for record in records if record["group"] == "gui"]}
    if unittest_ids:
        result.update(compare_collections(unittest_ids, result["pytest_ids"]))
    else:
        result.update(unittest_ids=[], pytest_only=result["pytest_ids"], same_common_order=True)
    validate_groups(result)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def validate_groups(inventory):
    if inventory.get("schema") != "zhuyin-test-groups/1":
        raise ValueError("unknown grouped inventory schema")
    all_ids, core, gui = (inventory[key] for key in ("pytest_ids", "core_ids", "gui_ids"))
    if not all_ids or len(all_ids) != len(set(all_ids)):
        raise ValueError("empty or duplicate inventory")
    if Counter(core + gui) != Counter(all_ids) or set(core) & set(gui):
        raise ValueError("group partition mismatch")
    records = inventory["records"]
    if Counter(record["identity"] for record in records) != Counter(all_ids):
        raise ValueError("record identity mismatch")
    if len({record["nodeid"] for record in records}) != len(records):
        raise ValueError("duplicate nodeid")
    for record in records:
        if record["group"] not in {"core", "gui"} or record["identity"] not in inventory[record["group"] + "_ids"]:
            raise ValueError("unknown or inconsistent group")
    return inventory


def verify_group(report, inventory, group):
    validate_groups(inventory)
    required = inventory[group + "_ids"]
    cases = list(ET.parse(report).iter("testcase"))
    mapping = {}
    for record in inventory["records"]:
        parts = record["nodeid"].split("::")
        class_name = ".".join([parts[0].replace("\\", "/").removesuffix(".py").replace("/", "."), *parts[1:-1]])
        mapping[(class_name, parts[-1])] = record["identity"]
    actual = [mapping.get((case.get("classname", ""), case.get("name", "")), "UNKNOWN") for case in cases]
    if Counter(actual) != Counter(required):
        raise ValueError("group JUnit unknown/missing/duplicate identity")
    for case in cases:
        if any(case.find(tag) is not None for tag in ("skipped", "failure", "error")):
            raise ValueError("group case failed/errored/skipped: " + case.get("name", ""))
    return len(required)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("collect", "verify-gui", "_unittest", "_pytest", "_identities", "collect-groups"))
    parser.add_argument("output", type=Path)
    parser.add_argument("--junit", type=Path)
    parser.add_argument("--tests", type=Path)
    args = parser.parse_args(argv)
    if args.command == "collect-groups":
        grouped_collection(args.output, args.tests)
    elif args.command.startswith("_"):
        collect(args.command[1:], args.output, args.tests)
    elif args.command == "collect":
        with tempfile.TemporaryDirectory() as temporary:
            paths = [Path(temporary) / f"{runner}.json" for runner in ("unittest", "pytest")]
            for runner, path in zip(("unittest", "pytest"), paths):
                subprocess.run([sys.executable, __file__, "_" + runner, str(path)], cwd=ROOT, check=True)
            unittest_collection, pytest_ids = (
                json.loads(path.read_text(encoding="utf-8")) for path in paths
            )
            result = inventory_from_collections(
                unittest_collection, pytest_ids, gui_functions(ROOT / "tests")
            )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"unittest": len(result["unittest_ids"]), "pytest": len(result["pytest_ids"]),
                          "pytest_only": result["pytest_only"], "gui": len(result["gui_ids"]),
                          "same_common_order": result["same_common_order"]}))
    else:
        if args.junit is None:
            parser.error("verify-gui requires --junit")
        result = verify_gui(args.junit, json.loads(args.output.read_text(encoding="utf-8")))
        print(f"Verified {result} required GUI cases executed successfully")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
