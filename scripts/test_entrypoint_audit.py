"""Compare actual collection identities; verify required GUI cases really ran.

Collection happens in separate interpreters so imports cannot mask discovery
differences. This is evidence of coverage, not a substitute for running tests.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import json
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


def collect(runner, output):
    sys.path.insert(0, str(ROOT))
    if runner == "unittest":
        loader = ReviewLoadTestsLoader()
        suite = loader.discover(str(ROOT / "tests"), pattern="test_*.py")

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
        gui_ids = gui_ids_from_cases(cases, gui_classes(ROOT / "tests"))
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
    collection = {"ids": ids, "gui_ids": gui_ids} if runner == "unittest" else ids
    output.write_text(json.dumps(collection, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("collect", "verify-gui", "_unittest", "_pytest"))
    parser.add_argument("output", type=Path)
    parser.add_argument("--junit", type=Path)
    args = parser.parse_args(argv)
    if args.command.startswith("_"):
        collect(args.command[1:], args.output)
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
