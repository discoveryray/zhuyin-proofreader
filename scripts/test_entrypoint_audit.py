"""Compare actual collection identities; verify required GUI cases really ran.

Collection happens in separate interpreters so imports cannot mask discovery
differences. This is evidence of coverage, not a substitute for running tests.
"""
from __future__ import annotations

import argparse
import ast
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


def callable_uses_tk(function, owner=None, seen=None):
    """Trace collected functions, inherited setup, fixtures and imported helpers.

    Dynamic tkinter lookup is deliberately rejected. Native/library callables
    without Python source are leaves; tkinter constructors are recognized first.
    This is a conservative classification, never an omission permission.
    """
    seen = set() if seen is None else seen
    function = inspect.unwrap(function)
    if inspect.ismethod(function):
        function = function.__func__
    if id(function) in seen:
        return False
    seen.add(id(function))
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

    def resolve(node):
        if isinstance(node, ast.Name):
            return owner if node.id in {"self", "cls"} else namespace.get(node.id)
        if isinstance(node, ast.Attribute):
            base = resolve(node.value)
            if base is not None:
                try:
                    return inspect.getattr_static(base, node.attr)
                except AttributeError:
                    pass
        return None

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
            if getattr(base, "__name__", "") == "tkinter":
                raise ValueError("dynamic tkinter lookup requires inventory review")
        target = resolve(node.func)
        if isinstance(target, (staticmethod, classmethod)):
            target = target.__func__
        # Follow application/test helpers, not pytest/unittest internals.
        module = getattr(target, "__module__", "") or ""
        if target is not None and not module.startswith(("unittest", "pytest", "_pytest")):
            if callable_uses_tk(target, owner, seen):
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
    return "gui" if any(_cached_uses_tk(function, owner) for function in functions) else "core"


def grouped_collection(output, tests=None):
    sys.path.insert(0, str(ROOT))
    import pytest
    tests = Path(tests or ROOT / "tests").resolve()

    class Groups:
        records = []

        def pytest_collection_finish(self, session):
            for item in session.items:
                parts = item.nodeid.split("::")
                self.records.append({"identity": ".".join([Path(parts[0]).stem, *parts[1:]]),
                                     "nodeid": item.nodeid, "group": classify_item(item)})

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
