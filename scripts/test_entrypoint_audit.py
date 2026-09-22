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


def compare_collections(unittest_ids, pytest_ids):
    missing = sorted(set(unittest_ids) - set(pytest_ids))
    duplicates = {name: count for name, count in Counter(pytest_ids).items() if count != 1}
    if missing or duplicates or not unittest_ids or not pytest_ids:
        raise ValueError(f"collection mismatch: missing={missing}, duplicates={duplicates}")
    return {"unittest_ids": unittest_ids, "pytest_ids": pytest_ids,
            "pytest_only": sorted(set(pytest_ids) - set(unittest_ids)),
            "same_common_order": unittest_ids == [name for name in pytest_ids if name in set(unittest_ids)]}


def gui_classes(tests):
    """Discover classes constructing real Tk roots, including inherited cases."""
    result = []
    for path in sorted(tests.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "load_tests":
                raise ValueError(f"custom unittest load_tests needs explicit runner review: {path}")
            if isinstance(node, ast.ClassDef) and any(
                    isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "Tk" for call in ast.walk(node)):
                result.append(f"{path.stem}.{node.name}.")
    if not result:
        raise ValueError("no real Tk classes discovered")
    return result


def verify_gui(report, inventory):
    required = inventory["gui_ids"]
    if not required:
        raise ValueError("missing required GUI inventory")
    cases = {}
    for case in ET.parse(report).iter("testcase"):
        name = case.get("classname", "").removeprefix("tests.") + "." + case.get("name", "")
        cases.setdefault(name, []).append(case)
    for name in required:
        entries = cases.get(name, [])
        if len(entries) != 1 or any(entries[0].find(tag) is not None for tag in ("skipped", "failure", "error")):
            raise ValueError(f"required GUI case did not execute successfully exactly once: {name}")
    return len(required)


def collect(runner, output):
    sys.path.insert(0, str(ROOT))
    if runner == "unittest":
        loader = unittest.TestLoader()
        suite = loader.discover(str(ROOT / "tests"), pattern="test_*.py")

        def flatten(item):
            if isinstance(item, unittest.TestSuite):
                for child in item:
                    yield from flatten(child)
            else:
                yield item.id()

        ids = list(flatten(suite))
        if loader.errors:
            raise ValueError("unittest discovery failed: " + "\n".join(loader.errors))
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
    output.write_text(json.dumps(ids, ensure_ascii=False, indent=2), encoding="utf-8")


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
            result = compare_collections(*(json.loads(path.read_text(encoding="utf-8")) for path in paths))
        prefixes = gui_classes(ROOT / "tests")
        result["gui_ids"] = [name for name in result["unittest_ids"] if any(name.startswith(p) for p in prefixes)]
        if not result["gui_ids"] or any(not any(name.startswith(p) for name in result["gui_ids"]) for p in prefixes):
            raise ValueError("Tk class has no collected tests")
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
