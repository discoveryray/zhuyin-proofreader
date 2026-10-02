"""One hosted, default-capture diagnostic of three unchanged tests at fixed M.

This helper is copied from definition D into RUNNER_TEMP and doubles as the
pytest observation plugin. It never creates an additional Tcl/Tk interpreter.
Only allowlisted technical records are uploaded; fixture directories are not.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

SOURCE_HEAD = "b8733e85300139748079130eb9d10c5d9685f22f"
SOURCE_TREE = "42816795ae93083146a7438a6383511a7a43dac5"
SOURCE_COUNT = 196
BRANCH = "refs/heads/codex/tk-b873-bounded-diagnostic"
REPOSITORY = "discoveryray/zhuyin-proofreader"
NODES = (
    "tests/test_manual_review_usability_v580.py::ManualReviewGuiTests::test_undo_reentry_keeps_difference_pending_in_queue",
    "tests/test_pdf_portability_integration.py::test_full_pipeline_different_sha_continues_and_returns",
    "tests/test_pdf_portability_integration.py::test_actual_excel_conflict_requires_fresh_local_visual_adjudication",
)
DEPENDENCIES = {"PyMuPDF": "1.26.7", "openpyxl": "3.1.5", "fonttools": "4.63.0",
                "python-docx": "1.2.0", "pytest": "9.1.1"}
ENV_KEYS = (
    "RUNNER_OS", "RUNNER_ARCH", "RUNNER_NAME", "RUNNER_TEMP", "ImageOS", "ImageVersion",
    "GITHUB_EVENT_NAME", "GITHUB_REPOSITORY", "GITHUB_SHA", "GITHUB_REF", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_JOB",
    "TEMP", "TMP", "TMPDIR", "LOCALAPPDATA", "PYTHONUTF8", "PYTHONIOENCODING",
    "PYTHONDONTWRITEBYTECODE", "PYTEST_ADDOPTS", "PYTHONHOME", "PYTHONPATH", "TCL_LIBRARY", "TK_LIBRARY",
)
OUTPUT_NAMES = ("environment.json", "summary.json", "process.json", "pytest.log", "junit.xml",
                "events.json", "source-before.json", "source-after.json")


def utc():
    return datetime.now(timezone.utc).isoformat()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def environment_record():
    versions = {}
    for name in DEPENDENCIES:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    helper = Path(__file__).resolve()
    return {"utc": utc(), "python": sys.version, "executable": sys.executable,
            "platform": platform.platform(), "dependencies": versions,
            "environment_allowlist": {key: os.environ.get(key) for key in ENV_KEYS},
            "helper_path": str(helper), "helper_sha256": hashlib.sha256(helper.read_bytes()).hexdigest()}


def require_hosted_context(definition_head):
    if (os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("GITHUB_EVENT_NAME") != "push"
            or os.environ.get("GITHUB_REPOSITORY") != REPOSITORY
            or os.environ.get("GITHUB_REF") != BRANCH
            or os.environ.get("GITHUB_RUN_ATTEMPT") != "1"
            or not re.fullmatch(r"[0-9a-f]{40}", definition_head)
            or os.environ.get("GITHUB_SHA") != definition_head):
        raise ValueError("Unsupported hosted event/ref/attempt/definition SHA")
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    restricted_event = {"created": event.get("created"), "before": event.get("before"),
                        "after": event.get("after"), "repository_full_name": event.get("repository", {}).get("full_name")}
    if (restricted_event["created"] is not True or restricted_event["before"] != "0" * 40
            or restricted_event["after"] != definition_head or restricted_event["repository_full_name"] != REPOSITORY):
        raise ValueError("Requires the initial branch-created push in the expected repository")
    if sys.platform != "win32" or sys.version_info[:3] != (3, 13, 0):
        raise ValueError("Requires Windows Python 3.13.0")
    return restricted_event


def bounded_paths(source, output):
    runner_temp = Path(os.environ["RUNNER_TEMP"]).resolve()
    source, output = source.resolve(), output.resolve()
    isolation = runner_temp / "tk-b873-isolation"
    helper = Path(__file__).resolve()
    for path in (output, isolation, helper):
        if path == runner_temp or not path.is_relative_to(runner_temp) or path.is_relative_to(source):
            raise ValueError("Helper, evidence and isolation must be outside source and within RUNNER_TEMP")
    if source.is_relative_to(output) or source.is_relative_to(isolation):
        raise ValueError("Source overlaps diagnostic output/isolation")
    if output != runner_temp / "tk-b873-diagnostic":
        raise ValueError("Unsupported evidence directory")
    return source, output, isolation


def git(source, *args):
    env = dict(os.environ)
    env["GIT_OPTIONAL_LOCKS"] = "0"
    command = ["git", "-C", str(source), *args]
    result = subprocess.run(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    if result.returncode:
        raise ValueError("Read-only Git query failed: " + result.stderr.decode("utf-8", "replace"))
    return result.stdout


def source_manifest(source):
    head = git(source, "rev-parse", "HEAD").decode().strip()
    tree = git(source, "rev-parse", "HEAD^{tree}").decode().strip()
    status = git(source, "status", "--porcelain=v1", "--untracked-files=all", "--ignored").decode("utf-8")
    entries = git(source, "ls-tree", "-rz", "--full-tree", "HEAD").split(b"\0")
    files = []
    for entry in entries:
        if not entry:
            continue
        metadata, raw_path = entry.split(b"\t", 1)
        mode, kind, blob = metadata.decode("ascii").split()
        name = raw_path.decode("utf-8")
        path = source / name
        if kind != "blob" or mode not in ("100644", "100755") or path.is_symlink() or not path.is_file():
            raise ValueError("Unsupported or missing tracked file: " + name)
        data = path.read_bytes()
        files.append({"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "git_blob": blob})
    result = {"utc": utc(), "source_root": str(source), "head": head, "tree": tree,
              "status_all_ignored_untracked": status, "tracked_count": len(files), "files": files,
              "physical_seal": hashlib.sha256(json.dumps(files, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()}
    result["fixed_source_valid"] = head == SOURCE_HEAD and tree == SOURCE_TREE and len(files) == SOURCE_COUNT and not status
    return result


def expected_junit_ids():
    result = []
    for node in NODES:
        parts = node.split("::")
        classname = parts[0][:-3].replace("/", ".")
        if len(parts) == 3:
            classname += "." + parts[1]
        result.append((classname, parts[-1]))
    return result


def validate_junit(root):
    cases = root.findall(".//testcase")
    expected = expected_junit_ids()
    actual = [(case.get("classname"), case.get("name")) for case in cases]
    findings = []
    if actual != expected:
        findings.append("JUnit identity/order differs from exact three nodes")
    outcomes = []
    for case in cases:
        kinds = [tag for tag in ("failure", "error", "skipped") if case.findall(tag)]
        outcomes.append({"classname": case.get("classname"), "name": case.get("name"), "non_success": kinds})
        if kinds:
            findings.append("JUnit contains non-success outcome")
    return {"valid": not findings, "findings": findings, "testcases": outcomes}


def validate_events(events):
    findings = []
    collections = [e["nodeids"] for e in events if e["kind"] == "collection"]
    if collections != [list(NODES)]:
        findings.append("Observed collection differs from exact three nodes/order")
    reports = [e for e in events if e["kind"] == "report"]
    expected = [(node, phase) for node in NODES for phase in ("setup", "call", "teardown")]
    if [(e["nodeid"], e["when"]) for e in reports] != expected or any(e["outcome"] != "passed" for e in reports):
        findings.append("Missing, extra or unsuccessful setup/call/teardown report")
    for phase in ("setup", "call", "teardown"):
        for edge in ("before", "after"):
            if [e["nodeid"] for e in events if e["kind"] == phase + "." + edge] != list(NODES):
                findings.append("Missing/extra phase observation: " + phase + "." + edge)
    configure = [e for e in events if e["kind"] == "configure"]
    if len(configure) != 1 or configure[0].get("capture") != "fd":
        findings.append("Capture was not original default fd")
    successes = [e for e in events if e["kind"] == "_tkinter.create.success"]
    attempts = [e for e in events if e["kind"] == "_tkinter.create.before"]
    if len(attempts) != 2 or len(successes) != 2 or any(e["kind"] in ("observer.error", "_tkinter.create.exception") for e in events):
        findings.append("Incomplete real Tk/observer evidence")
    for event in successes:
        if any(not isinstance(event.get("tcl", {}).get(key), str) or not event["tcl"][key]
               for key in ("info_library", "info_patchlevel", "package_Tk")):
            findings.append("Incomplete Tcl/Tk library/version evidence")
        loaded = event.get("loaded_tcl_tk_dlls")
        if (not isinstance(loaded, list) or len(loaded) != 2
                or {Path(d.get("path", "")).name.lower() for d in loaded} != {"tcl86t.dll", "tk86t.dll"}
                or any(not re.fullmatch(r"[0-9a-f]{64}", d.get("sha256", "")) for d in loaded)):
            findings.append("Incomplete loaded DLL paths/hashes")
    return {"valid": not findings, "findings": findings, "setup_call_teardown_reports": len(reports),
            "tk_attempts": len(attempts), "tk_successes": len(successes)}


def prepare(source, output, definition_head):
    hosted_push_contract = require_hosted_context(definition_head)
    source, output, isolation = bounded_paths(source, output)
    if output.exists() or isolation.exists():
        raise ValueError("Evidence/isolation directories already exist; no retry")
    output.mkdir()
    isolation.mkdir()
    for name in ("temp", "localappdata", "cache"):
        (isolation / name).mkdir()
    save(output / "environment.json", {"prepare": environment_record(), "hosted_push_contract": hosted_push_contract})
    save(output / "summary.json", {"prepared": True, "state": "PREPARED", "diagnostic_success": False,
          "definition_head": definition_head, "hosted_push_contract": hosted_push_contract,
          "source_head": SOURCE_HEAD, "source_tree": SOURCE_TREE,
          "source_root": str(source), "pytest_process_count": 0, "nodes": list(NODES),
          "capture": "default fd", "process_timeout_seconds": 300, "diagnostic_only": True,
          "formal_status": "MERGED_POSTMERGE_CI_BLOCKED", "corrective_count": "6/6",
          "allowlisted_outputs": list(OUTPUT_NAMES), "isolation_root": str(isolation)})


def run_once(source, output, definition_head):
    hosted_push_contract = require_hosted_context(definition_head)
    source, output, isolation = bounded_paths(source, output)
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    if (summary.get("state") != "PREPARED" or summary.get("definition_head") != definition_head
            or summary.get("hosted_push_contract") != hosted_push_contract
            or summary.get("source_root") != str(source) or summary.get("pytest_process_count") != 0
            or (output / "process.json").exists()):
        raise ValueError("Prepared context mismatch or prior execution; no retry")
    findings = []
    process_record = None
    before = None
    summary.update({"state": "STARTING", "started_utc": utc()})
    save(output / "summary.json", summary)
    try:
        metadata = environment_record()
        if metadata["dependencies"] != DEPENDENCIES:
            raise ValueError("Installed dependency versions differ from fixed requirements-ci")
        if any((isolation / "localappdata").iterdir()):
            raise ValueError("Isolated LOCALAPPDATA is not initially empty")
        env = dict(os.environ)
        env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTEST_ADDOPTS": "", "PYTHONPATH": str(Path(__file__).resolve().parent),
                    "TEMP": str(isolation / "temp"), "TMP": str(isolation / "temp"),
                    "TMPDIR": str(isolation / "temp"), "LOCALAPPDATA": str(isolation / "localappdata"),
                    "TK_DIAG_EVENTS": str(output / "events.json")})
        metadata["pytest_environment_allowlist"] = {key: env.get(key) for key in ENV_KEYS}
        prepared_env = json.loads((output / "environment.json").read_text(encoding="utf-8"))
        prepared_env["execution"] = metadata
        save(output / "environment.json", prepared_env)
        before = source_manifest(source)
        save(output / "source-before.json", before)
        if not before["fixed_source_valid"]:
            raise ValueError("Fixed M source HEAD/tree/count/clean validation failed")
        basetemp = isolation / "temp" / "pytest"
        if basetemp.exists():
            raise ValueError("Fresh basetemp already exists")
        command = [sys.executable, "-m", "pytest", *NODES, "-q", "-rs", "-p", "tk_postmerge_diagnostic",
                   "-o", "cache_dir=" + str(isolation / "cache"), "--basetemp=" + str(basetemp),
                   "--junitxml=" + str(output / "junit.xml")]
        process_record = {"command_argv": command, "cwd": str(source), "started_utc": utc(),
                          "timeout_seconds": 300, "capture_flag": None, "default_capture": "fd",
                          "environment_allowlist": {key: env.get(key) for key in ENV_KEYS},
                          "stdio_launch": "stdin DEVNULL; stdout binary pytest.log; stderr STDOUT"}
        save(output / "process.json", process_record)
        start = time.monotonic()
        with (output / "pytest.log").open("wb") as logfile:
            child = subprocess.Popen(command, cwd=source, env=env, stdin=subprocess.DEVNULL,
                                     stdout=logfile, stderr=subprocess.STDOUT)
            process_record["native_process_id"] = child.pid
            summary["pytest_process_count"] = 1
            try:
                save(output / "summary.json", summary)
                save(output / "process.json", process_record)
                process_record["exit_code"] = child.wait(timeout=300)
                process_record["timeout"] = False
            except subprocess.TimeoutExpired:
                process_record["timeout"] = True
            finally:
                # Keep the pytest parent alive until taskkill targets its tree.
                # This also cleans up if a post-spawn evidence write fails.
                if child.poll() is None:
                    stop = subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"],
                                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=20)
                    process_record["process_tree_stop"] = {"exit_code": stop.returncode,
                                                           "output": stop.stdout.decode("utf-8", "replace")}
                    process_record["exit_code"] = child.wait(timeout=15)
        process_record.update({"ended_utc": utc(), "elapsed_seconds": time.monotonic() - start})
        save(output / "process.json", process_record)
        if process_record["timeout"] or process_record["exit_code"] != 0:
            findings.append("Original pytest failed or timed out; no retry")
        junit = validate_junit(ET.parse(output / "junit.xml").getroot())
        summary["junit_validation"] = junit
        findings.extend(junit["findings"])
        observations = validate_events(json.loads((output / "events.json").read_text(encoding="utf-8")))
        summary["observation_validation"] = observations
        findings.extend(observations["findings"])
    except Exception as exc:
        findings.append(type(exc).__name__ + ": " + str(exc))
        if process_record is not None:
            process_record.setdefault("ended_utc", utc())
            process_record["helper_exception"] = type(exc).__name__ + ": " + str(exc)
            save(output / "process.json", process_record)
    finally:
        try:
            after = source_manifest(source)
            save(output / "source-after.json", after)
            summary["source_after_valid"] = after["fixed_source_valid"]
            summary["source_physical_seals_equal"] = before is not None and before["physical_seal"] == after["physical_seal"]
            if not after["fixed_source_valid"] or not summary["source_physical_seals_equal"]:
                findings.append("Source HEAD/tree/status or physical bytes changed")
        except Exception as exc:
            findings.append("After source evidence unavailable: " + type(exc).__name__ + ": " + str(exc))
        summary.update({"state": "FINISHED", "ended_utc": utc(), "findings": findings,
                        "diagnostic_success": not findings and summary["pytest_process_count"] == 1,
                        "limitations": ["Observer may affect timing", "Bounded sequence only; no full suite",
                                        "Definition D is distinct from tested M", "Not original workflow post-merge gate",
                                        "No capture/HANDLE/Tcl causality or environment remedy established"]})
        save(output / "summary.json", summary)
    print(json.dumps({"diagnostic_success": summary["diagnostic_success"], "findings": findings,
                      "source_head": SOURCE_HEAD, "pytest_process_count": summary["pytest_process_count"]}, indent=2))
    return 0 if summary["diagnostic_success"] else 1


def self_check():
    def make_xml():
        root = ET.Element("testsuites")
        suite = ET.SubElement(root, "testsuite")
        for classname, name in expected_junit_ids():
            ET.SubElement(suite, "testcase", classname=classname, name=name)
        return root
    assert validate_junit(make_xml())["valid"]
    for tag in ("failure", "error", "skipped"):
        root = make_xml()
        ET.SubElement(root.find(".//testcase"), tag)
        assert not validate_junit(root)["valid"]
    root = make_xml()
    suite = root.find("testsuite")
    suite.append(suite[0])
    assert not validate_junit(root)["valid"]
    root = make_xml()
    suite = root.find("testsuite")
    suite.remove(suite[0])
    assert not validate_junit(root)["valid"]
    root = make_xml()
    suite = root.find("testsuite")
    first = suite[0]
    suite.remove(first)
    suite.append(first)
    assert not validate_junit(root)["valid"]
    root = make_xml()
    root.find(".//testcase").set("classname", "unknown")
    assert not validate_junit(root)["valid"]
    print("Synthetic JUnit exact identity/order/non-success checks passed; no pytest or Tk executed.")
    return 0


def export_records(output, definition_head):
    """Expose only this approved closed file list for byte-exact log recovery."""
    require_hosted_context(definition_head)
    root = Path(os.environ["RUNNER_TEMP"]).resolve() / "tk-b873-diagnostic"
    if output.resolve() != root or not root.is_dir() or root.is_symlink():
        raise ValueError("Unsupported or missing export directory")
    print("TK_DIAG_EXPORT_BEGIN " + json.dumps({"definition_head": definition_head, "source_head": SOURCE_HEAD}))
    for name in OUTPUT_NAMES:
        path = root / name
        if not path.exists():
            print("TK_DIAG_FILE_MISSING " + json.dumps({"path": name}))
            continue
        if path.is_symlink() or not path.is_file() or path.resolve().parent != root:
            raise ValueError("Unsupported export entry: " + name)
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        print("TK_DIAG_FILE_BEGIN " + json.dumps({"path": name, "bytes": len(data), "sha256": digest, "encoding": "base64"}))
        encoded = base64.b64encode(data).decode("ascii")
        chunks = [encoded[offset:offset + 2900] for offset in range(0, len(encoded), 2900)]
        for number, chunk in enumerate(chunks):
            print("TK_DIAG_FILE_CHUNK " + str(number) + " " + chunk)
        print("TK_DIAG_FILE_END " + json.dumps({"path": name, "chunks": len(chunks), "bytes": len(data), "sha256": digest}))
    print("TK_DIAG_EXPORT_END " + json.dumps({"definition_head": definition_head, "allowlist": list(OUTPUT_NAMES)}))
    return 0


def install_observation_plugin():
    import ctypes
    import threading
    from ctypes import wintypes
    import _tkinter
    import pytest

    events = []
    original_create = _tkinter.create
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel.GetStdHandle.restype = wintypes.HANDLE
    kernel.GetFileType.argtypes = [wintypes.HANDLE]
    kernel.GetFileType.restype = wintypes.DWORD

    def emit(kind, **detail):
        events.append({"kind": kind, "utc": utc(), "monotonic_ns": time.monotonic_ns(),
                       "pid": os.getpid(), "native_thread_id": threading.get_native_id(), **detail})

    def state():
        streams = {}
        import msvcrt
        for name in ("stdin", "stdout", "stderr", "__stdin__", "__stdout__", "__stderr__"):
            stream = getattr(sys, name, None)
            value = {"type": type(stream).__name__}
            try:
                value["fd"] = stream.fileno()
                value["os_handle"] = msvcrt.get_osfhandle(value["fd"])
            except Exception as exc:
                value["fd_error"] = type(exc).__name__ + ": " + str(exc)
            streams[name] = value
        handles = {}
        for name, number in (("stdin", -10), ("stdout", -11), ("stderr", -12)):
            ctypes.set_last_error(0)
            handle = kernel.GetStdHandle(number & 0xffffffff)
            handle_error = ctypes.get_last_error()
            ctypes.set_last_error(0)
            kind = kernel.GetFileType(handle)
            handles[name] = {"handle": handle, "get_handle_error": handle_error,
                             "file_type": kind, "file_type_error": ctypes.get_last_error()}
        return {"environment_allowlist": {key: os.environ.get(key) for key in ENV_KEYS},
                "stdio": streams, "win32_standard_handles": handles}

    def observe(kind, **detail):
        try:
            emit(kind, state=state(), **detail)
        except Exception as exc:
            emit("observer.error", original_kind=kind, error=type(exc).__name__ + ": " + str(exc))

    def dlls():
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        process = kernel.GetCurrentProcess()
        modules = (wintypes.HMODULE * 1024)()
        needed = wintypes.DWORD()
        psapi.EnumProcessModules.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.HMODULE), wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        psapi.GetModuleFileNameExW.argtypes = [wintypes.HANDLE, wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
        if not psapi.EnumProcessModules(process, modules, ctypes.sizeof(modules), ctypes.byref(needed)):
            raise OSError(ctypes.get_last_error(), "EnumProcessModules")
        if needed.value > ctypes.sizeof(modules):
            raise ValueError("Loaded module inventory truncated")
        found = []
        for handle in modules[:needed.value // ctypes.sizeof(wintypes.HMODULE)]:
            buffer = ctypes.create_unicode_buffer(32768)
            if not psapi.GetModuleFileNameExW(process, handle, buffer, len(buffer)):
                raise OSError(ctypes.get_last_error(), "GetModuleFileNameExW")
            path = Path(buffer.value)
            if path.name.lower() in ("tcl86t.dll", "tk86t.dll"):
                data = path.read_bytes()
                found.append({"path": str(path), "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        return found

    def observed_create(*args, **kwargs):
        observe("_tkinter.create.before")
        try:
            app = original_create(*args, **kwargs)
        except BaseException as exc:
            observe("_tkinter.create.exception", exception_type=type(exc).__name__, exception=str(exc))
            raise
        info = {}
        for key, command in (("info_library", ("info", "library")), ("info_patchlevel", ("info", "patchlevel")),
                             ("package_Tk", ("package", "provide", "Tk"))):
            try:
                info[key] = str(app.call(*command))
            except Exception as exc:
                info[key] = {"error": type(exc).__name__ + ": " + str(exc)}
        try:
            loaded = dlls()
        except Exception as exc:
            loaded = {"error": type(exc).__name__ + ": " + str(exc)}
        observe("_tkinter.create.success", tcl=info, loaded_tcl_tk_dlls=loaded)
        return app

    def pytest_configure(config):
        _tkinter.create = observed_create
        observe("configure", capture=config.getoption("capture"), argv=sys.argv,
                environment=environment_record(), observer_timing_effect_possible=True)

    def pytest_collection_finish(session):
        emit("collection", nodeids=[item.nodeid for item in session.items])

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_setup(item):
        observe("setup.before", nodeid=item.nodeid)
        yield
        observe("setup.after", nodeid=item.nodeid)

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_call(item):
        observe("call.before", nodeid=item.nodeid)
        yield
        observe("call.after", nodeid=item.nodeid)

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_teardown(item):
        observe("teardown.before", nodeid=item.nodeid)
        yield
        observe("teardown.after", nodeid=item.nodeid)

    def pytest_runtest_logreport(report):
        emit("report", nodeid=report.nodeid, when=report.when, outcome=report.outcome,
             duration=report.duration, longrepr=str(report.longrepr) if report.failed or report.skipped else None)

    def pytest_sessionfinish(session, exitstatus):
        observe("sessionfinish", exitstatus=int(exitstatus))
        _tkinter.create = original_create
        save(Path(os.environ["TK_DIAG_EVENTS"]), events)

    for name, function in tuple(locals().items()):
        if name.startswith("pytest_"):
            globals()[name] = function


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="operation", required=True)
    subparsers.add_parser("self-check", help="Validate only tiny synthetic JUnit data; no pytest/Tk")
    for operation in ("prepare", "run"):
        child = subparsers.add_parser(operation)
        child.add_argument("--source", type=Path, required=True)
        child.add_argument("--output", type=Path, required=True)
        child.add_argument("--definition-head", required=True)
    child = subparsers.add_parser("export", help="Export only the eight approved technical records; no pytest/Tk")
    child.add_argument("--output", type=Path, required=True)
    child.add_argument("--definition-head", required=True)
    args = parser.parse_args()
    if args.operation == "self-check":
        return self_check()
    if args.operation == "export":
        return export_records(args.output, args.definition_head)
    if args.operation == "prepare":
        prepare(args.source, args.output, args.definition_head)
        return 0
    return run_once(args.source, args.output, args.definition_head)


if __name__ == "__main__":
    raise SystemExit(main())
else:
    install_observation_plugin()
