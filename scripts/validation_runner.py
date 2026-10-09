"""Immutable non-window pytest evidence; legacy GUI evidence remains read-only.

This runner never grants review/merge authorization. Formal invocations require
an explicitly supplied validation mode, a clean frozen source and fd capture.
"""
from __future__ import annotations

import argparse
import ast
import base64
from contextlib import contextmanager
import io
import zipfile
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import subprocess
import sys
import time
import uuid
from urllib.parse import quote
import xml.etree.ElementTree as ET

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import test_entrypoint_audit as audit

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "zhuyin-validation-run/2"
COVERAGE_SCHEMA = "zhuyin-validation-coverage/2"
LEGACY_SCHEMA = "zhuyin-validation-run/1"
POLICY = audit.POLICY
ENV_KEYS = ("PYTHONUTF8", "PYTHONIOENCODING", "PYTEST_ADDOPTS", "TCL_LIBRARY", "TK_LIBRARY")
DEFAULT_TIMEOUTS = {"core": 5400}

# Human-adopted finite contract: docs/PR43_GUI_SUPPLEMENT_CONTRACT.md.
# These pins authenticate the original adoption and immutable failed artifact;
# they do not change legacy retry_eligible or admit arbitrary skipped tests.
PR43_GUI_ONCE = {
    "repository": "discoveryray/zhuyin-proofreader", "pr": 43,
    "task_id": "actual-gpt-bbox-export",
    "baseline": "8eab9a1de34b59115658a2c7d3565adb347e59ab",
    "starting_head": "d59358cd45cc65a73b1d4dcb0aa228ffe7233435",
    "run_id": 37453889278, "attempt": 1, "artifact_id": 11412967278,
    "archive_sha256": "200ff76738c7cb565cfad1cc470be97fa0bc3a2197908a63391c1335f0d82235",
    "execution_id": "08e193d65afa44a6b9498f631561521b",
    "manifest_sha256": "0d9a0e94a3d6b52e0fbeb2930af31d0180d6756ec6dfe8c142a6bdaa5ffc4b0f",
    "setup_sha256": "c662bd9592094a5c03ead85a304a4e532b254f7c580a955fffc736b655bfb041",
    "authorization_sha256": "39da5b779290cdb2b95eff70c8859122366dd7c5ca44d0fa3cdd4d2aae8bb50b",
    "proposal_sha256": "9b0aba33f2cf34b5ec29c290f3388501899e9e577e5a123b55c9bf53212c364a",
    "human_reply_sha256": "f5a5e87d7770e46fcf9e0875dd3b08f05ff7bec298aa5a9df9df2700e185220c",
}


# Only the adopted seventh correction may reference the already published
# PR43 handoff. This is history retrieval, never a new ledger or a review PASS.
PR43_POSTMERGE_HISTORY = {
    "schema": "validation-postmerge-continuation/1",
    "repository": "discoveryray/zhuyin-proofreader",
    "task_id": "actual-gpt-bbox-export",
    "baseline": "8eab9a1de34b59115658a2c7d3565adb347e59ab",
    "original_pr": 43,
    "branch": "codex/actual-gpt-bbox-export-postmerge7",
    "authorization_sha256": "5777e88fdff4820b2bbebdf0b1b95f3084acf4d46607f4610be5513b5a71a552",
    "original_head": "50b46f9624700399fda2dc610575041c9e1c1fc2",
    "original_merge": "92ffeda22dcaa0bb34da6b4d26ed7593e0bce641",
    "original_merge_parents": ["4aeb4837da8d77b91c816930533042916cc638b5",
                               "50b46f9624700399fda2dc610575041c9e1c1fc2"],
    "original_merge_tree": "22c5aaf8e6f8afcc3465996fafeda5e2669ad3d5",
    "original_history_block_sha256": "6baf58dcf47f2726c1f4c83fb66e48afd5b4af29a78dbf9186a5fdcc7d184d88",
    "original_archive_sha256": "322ddf29015c63ed646733af9f6c937f0ae4807120f347d135649b6d8ae6660e",
    "original_source_ref": "tmp/pr-review-automation/actual-gpt-bbox-export/original-request.txt",
    "sixth_state_sha256": "21ff67454a3a9235c05e725c5c4781ac1adf4562bb144c77a5d29c1afd320120",
    "p1_report_sha256": "edfe0ac324d2dd16c4f322e3a16bcc01c144e16b1cbff46e2c6477da1fe2426c",
    "p1_thread": "PRRT_kwDOUDv6jM6qpB4j", "p1_comment": 4226509109,
    "corrective_count": 7,
}
PR43_ORIGINAL_BRANCH = "codex/actual-gpt-bbox-export"
PR43_ORIGINAL_FULL_RUN = 37873515033
PR43_ORIGINAL_PUSH_RUN = 37881982664
PR43_ORIGINAL_PUSH_ARTIFACT = 11595410764
PR43_ORIGINAL_PUSH_ARCHIVE_SHA256 = "7278108f68b31e4d45acc8fead8ef83af83682d70e012e4d920420da140ff884"
PR43_ORIGINAL_COVERAGE_SHA256 = "00ce13eb3a9946381542d52e5dc00f4abd4cfc72a09e4497d44418a6944d44b0"
PR43_ORIGINAL_SHORT_SHA256 = "2886b9e6895661222e206fa26c924c18bae9e2f24ac74c808724ff48e0b2c3f5"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def write_json(path, data):
    with Path(path).open("x", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())


def parse_json(text):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    def invalid_constant(value):
        raise ValueError("non-finite JSON constant: " + value)
    return json.loads(text, object_pairs_hook=unique, parse_constant=invalid_constant)


def read_json(path):
    return parse_json(Path(path).read_text(encoding="utf-8-sig"))


def git(*arguments):
    return subprocess.check_output(["git", *arguments], cwd=ROOT, text=True, encoding="utf-8").strip()


def snapshot():
    return {"head": git("rev-parse", "HEAD"), "tree": git("rev-parse", "HEAD^{tree}"),
            "parents": git("show", "-s", "--format=%P", "HEAD").split()}


def environment():
    dependencies = {}
    for distribution in importlib.metadata.distributions():
        name = re.sub(r"[-_.]+", "-", distribution.metadata["Name"]).lower()
        if name in dependencies:
            raise ValueError("duplicate installed distribution: " + name)
        dependencies[name] = distribution.version
    return {"python": platform.python_version(), "platform": platform.system(),
            "dependencies": dict(sorted(dependencies.items())), "capture": "fd",
            "environment": {name: os.environ.get(name, "") for name in ENV_KEYS}}


def validate_environment(value):
    if value["python"] != "3.13.0" or value["platform"] != "Windows":
        raise ValueError("formal validation requires Windows Python 3.13.0")
    if value["environment"].get("PYTEST_ADDOPTS") or value["capture"] != "fd":
        raise ValueError("formal grouped validation requires default fd capture and empty PYTEST_ADDOPTS")
    requirements = ROOT / "requirements-ci-lock.txt"
    for line in requirements.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        name, version = line.strip().split("==")
        name = re.sub(r"[-_.]+", "-", name).lower()
        if value["dependencies"].get(name) != version:
            raise ValueError("installed dependency differs from lock: " + name)


def run_process(command, folder, name, timeout, env=None):
    """Stream directly to disk; retain output on timeout/interruption/launch error."""
    path = folder / name
    started = utc_now()
    with path.open("xb") as output:
        try:
            process = subprocess.Popen(command, cwd=ROOT, stdout=output,
                                       stderr=subprocess.STDOUT, env=env)
        except OSError as error:
            output.write(str(error).encode("utf-8"))
            return {"exit_code": None, "outcome": "failed", "started_at": started,
                    "finished_at": utc_now(), "runner_start_error": {
                        "errno": error.errno, "winerror": getattr(error, "winerror", None),
                        "message": str(error)}}
        try:
            code = process.wait(timeout=timeout)
            outcome = "success" if code == 0 else "failed"
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
            # Only terminate the process we created. pytest executes serially.
            if os.name == "nt" and process.poll() is None:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=output, stderr=subprocess.STDOUT, check=False, timeout=10)
            elif process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
            code = process.returncode
            outcome = "timeout" if isinstance(error, subprocess.TimeoutExpired) else "interrupted"
        return {"exit_code": code, "outcome": outcome,
                "started_at": started, "finished_at": utc_now(), "runner_start_error": None}


def junit_binding(path, execution_id, inventory_hash):
    tree = ET.parse(path)
    properties = {node.get("name"): node.get("value") for node in tree.findall(".//testsuite/properties/property")}
    if properties.get("validation_execution_id") != execution_id or properties.get("validation_inventory_sha256") != inventory_hash:
        raise ValueError("stale or other-execution JUnit binding")
    return tree


def verify_events(path, inventory, group):
    rows = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()]
    selected = {record["nodeid"] for record in inventory["records"] if record["group"] == group}
    calls = Counter(row["nodeid"] for row in rows if row["when"] == "call" and not row["subtest"])
    if calls != Counter({node: 1 for node in selected}):
        raise ValueError("event top-level identity mismatch")
    if any(row["nodeid"] not in selected or row["outcome"] != "passed" for row in rows):
        raise ValueError("event unknown identity or unsuccessful phase/subtest")
    return {"top_level": len(selected), "subtests": sum(row["subtest"] for row in rows), "error": None}


def artifact_entry(path, folder):
    return {"path": path.relative_to(folder).as_posix(), "sha256": digest(path)}


# cff-legacy-failure-read/1: the sole retained producer-legal False record.
# Producer 4453a35dc7d66b9d9a2f0cca43f49087b4a4cdbc, runner blob
# 3d33f83055c2c1cff366a1cee415cb81ef609c8d (SHA256
# 30452368f477dfafb093b0c8d93f3b340ce57a3b80a785ae75f4caa53bab75e5).
# The manifest binds every original raw artifact, including its task/PR context.
# No historical source is executed and this never changes retry eligibility.
CFF_LEGACY_FAILURE_SHA256 = "9b0635cbcb149cf4c272c0c51d28478d66c07d355ffc57117cb0d90b32c0793a"
CFF_HISTORY_TASK = "cff-batch-fingerprint"


def _cff_legacy_failure(path, data, context, policy, task_id):
    return (policy == POLICY and task_id == CFF_HISTORY_TASK
            and context.get("task_id") == CFF_HISTORY_TASK
            and digest(path) == CFF_LEGACY_FAILURE_SHA256
            and data["schema"] == LEGACY_SCHEMA and data["group"] == "gui"
            and data["execution_id"] == "0a56d940d31c4b48865cbda948494a7a"
            and data["candidate"] == {
                "head": "4453a35dc7d66b9d9a2f0cca43f49087b4a4cdbc",
                "tree": "d216214e0ff371486e2a0e34cae8332b3fc2918c",
                "parents": ["8eab9a1de34b59115658a2c7d3565adb347e59ab",
                            "431b43be38d655b51e58a81ceaa2caff141672e7"]}
            and data["ci"] == {"run_id": 37293413665, "attempt": 1,
                               "job": "grouped", "event": "pull_request"}
            and data["outcome"] == "failed" and type(data["exit_code"]) is int
            and data["exit_code"] == 1 and data["retry_eligible"] is False
            and data["retry_of"] is None and data["code_blockers"] is False)


def read_verified_manifest(path, *, history_policy=None, task_id=None):
    if history_policy not in (None, POLICY):
        raise ValueError("unknown history read policy")
    path = Path(path).resolve()
    data = read_json(path)
    if data.get("schema") not in {SCHEMA, LEGACY_SCHEMA}:
        raise ValueError("unknown execution manifest")
    current = data["schema"] == SCHEMA
    required = {"schema", "execution_id", "group", "candidate", "command", "environment",
                "inventory_sha256", "started_at", "finished_at", "exit_code", "outcome",
                "verification", "ci", "retry_of", "artifacts", "retry_key", "retry_eligible",
                "preflight", "runner_start_error", "code_blockers"}
    if current:
        required.update(("policy", "window_validation"))
        if (data.get("policy") != POLICY or data.get("window_validation") != audit.WINDOW_NOTICE
                or data["group"] != "core" or data["preflight"] is not None
                or data["retry_of"] is not None or data["retry_eligible"] is not False):
            raise ValueError("invalid no-window execution policy")
    if set(data) != required or data["group"] not in {"core", "gui"}:
        raise ValueError("invalid execution manifest contract")
    allowed_artifacts = {"inventory": "inventory.json", "started": "started.json", "history_context": "history-context.json",
                         "collection_log": "collection.log", "raw_log": "raw.log", "junit": "junit.xml", "events": "events.jsonl",
                         "preflight_log": "preflight.log", "preflight_junit": "preflight.xml", "preflight_result": "preflight.json"}
    if not set(data["artifacts"]) <= allowed_artifacts.keys():
        raise ValueError("unknown execution artifact")
    if current and any(name.startswith("preflight") for name in data["artifacts"]):
        raise ValueError("no-window execution contains retired preflight artifacts")
    for name, item in data["artifacts"].items():
        if set(item) != {"path", "sha256"} or item["path"] != allowed_artifacts[name]:
            raise ValueError("unexpected execution artifact filename")
        target = (path.parent / item["path"]).resolve()
        if not target.is_relative_to(path.parent) or not target.is_file() or digest(target) != item["sha256"]:
            raise ValueError("missing or corrupt execution artifact")
    if datetime.fromisoformat(data["started_at"]) > datetime.fromisoformat(data["finished_at"]):
        raise ValueError("invalid execution time interval")
    command = data["command"]
    if len(command) != 5 or Path(command[1]).name != "validation_runner.py" or command[2] != "_pytest" or Path(command[3]).name != data["execution_id"] or command[4] != data["group"]:
        raise ValueError("unexpected formal pytest command")
    artifacts = data["artifacts"]
    if not {"inventory", "raw_log", "started", "history_context"} <= artifacts.keys():
        raise ValueError("execution raw evidence absent")
    context = read_json(path.parent / artifacts["history_context"]["path"])
    if not context.get("task_id"):
        raise ValueError("original task history scope missing")
    if data["ci"]["event"] == "local":
        if context.get("schema") != LOCAL_SCHEMA or not context.get("source_ref") or not context.get("baseline"):
            raise ValueError("local execution provenance missing")
    if data["retry_key"] != canonical_digest({"task": context["task_id"], "tree": data["candidate"]["tree"]}):
        raise ValueError("retry identity is not original task/candidate tree")
    inventory_path = path.parent / artifacts["inventory"]["path"]
    if digest(inventory_path) != data["inventory_sha256"]:
        raise ValueError("inventory digest mismatch")
    inventory = audit.validate_groups(read_json(inventory_path))
    if current and inventory["schema"] != "zhuyin-test-groups/2":
        raise ValueError("no-window execution requires current active inventory")
    started = read_json(path.parent / artifacts["started"]["path"])
    for key in ("execution_id", "candidate", "environment", "inventory_sha256", "retry_key", "retry_of", "ci", "command"):
        if started[key] != data[key]:
            raise ValueError("execution start/finish metadata mismatch: " + key)
    retained_cff = _cff_legacy_failure(path, data, context, history_policy, task_id)
    if (history_policy == POLICY
            and data["execution_id"] == "0a56d940d31c4b48865cbda948494a7a"
            and not retained_cff):
        raise ValueError("CFF retained failure differs from frozen original/context")
    if (not current and not retained_cff
            and data["retry_eligible"] != (data["group"] == "gui" and retry_eligible(
                path.parent, {"runner_start_error": data["runner_start_error"]}, data["preflight"]))):
        raise ValueError("retry eligibility differs from original raw evidence")
    if data["outcome"] == "success":
        if data["exit_code"] != 0 or data["code_blockers"]:
            raise ValueError("success masks failed exit/code blocker")
        if not {"junit", "events"} <= artifacts.keys():
            raise ValueError("successful execution missing JUnit/events")
        report = path.parent / artifacts["junit"]["path"]
        junit_binding(report, data["execution_id"], data["inventory_sha256"])
        count = audit.verify_group(report, inventory, data["group"])
        verification = verify_events(path.parent / artifacts["events"]["path"], inventory, data["group"])
        if verification != data["verification"] or count != verification["top_level"]:
            raise ValueError("manifest verification differs from raw events")
        if data["group"] == "gui":
            if data["preflight"] is None or data["preflight"]["outcome"] != "success" or "preflight_result" not in artifacts:
                raise ValueError("GUI passed without successful preflight")
            if not {"preflight_junit", "preflight_log"} <= artifacts.keys() or data["preflight"]["exit_code"] != 0:
                raise ValueError("preflight raw success evidence is missing")
            probe_cases = list(ET.parse(path.parent / artifacts["preflight_junit"]["path"]).iter("testcase"))
            if len(probe_cases) != 1 or any(probe_cases[0].find(tag) is not None for tag in ("skipped", "failure", "error")):
                raise ValueError("preflight did not run successfully exactly once")
            if read_json(path.parent / artifacts["preflight_result"]["path"]).get("stage") != "complete":
                raise ValueError("incomplete Tk probe")
    elif data["outcome"] not in {"failed", "blocked", "timeout", "interrupted"}:
        raise ValueError("unknown execution outcome")
    return data


_TCL_RESOURCE_READ = r'(?:couldn.t read file|error reading) "[^"\r\n]+\.tcl": (?:permission denied|no such file or directory|no error|invalid argument)'


def _initializer_read_message(message):
    """Recognize a direct read error or the retained Tk8.6 SourceLibFile wrapper."""
    if re.fullmatch(r'_tkinter\.TclError: ' + _TCL_RESOURCE_READ, message, re.IGNORECASE):
        return True
    # Match the complete known initializer wrapper, including the repeated
    # inner error and source chain. A bare tk.tcl lookup failure is not proof.
    wrapper = (
        r"_tkinter\.TclError: Can't find a usable tk\.tcl in the following directories:[ \t]*\n"
        r"[ \t]+[^\r\n]+\n\n"
        r'(?P<library>[^"\r\n]+)[/\\]tk\.tcl: '
        r'(?P<inner>(?:couldn.t read file|error reading) "(?P=library)[/\\](?P<resource>[A-Za-z][A-Za-z0-9_]*)\.tcl": '
        r'(?:permission denied|no such file or directory|no error|invalid argument))\n'
        r'(?P=inner)\n    while executing\n'
        r'"source -encoding utf-8 (?P=library)[/\\](?P=resource)\.tcl"\n'
        r'    \(in namespace eval "::" script line 1\)\n    invoked from within\n'
        r'"namespace eval :: \[list source -encoding utf-8 \[file join \$::tk_library \$file\.tcl\]\]"\n'
        r'    \(procedure "SourceLibFile" line 2\)\n    invoked from within\n'
        r'"SourceLibFile (?P=resource)"\n'
        r'    \(in namespace eval "::tk" script line 3\)\n    invoked from within\n'
        r'"namespace eval ::tk \{\n\tSourceLibFile icons\n\tSourceLibFile button\n'
        r'\tSourceLibFile entry\n\tSourceLibFile listbox\n\tSourceLibFile menu\n\tSourceLibFile panedw\.\.\."\n'
        r'    \(file "(?P=library)[/\\]tk\.tcl" line 506\)\n    invoked from within\n'
        r'"source (?P=library)[/\\]tk\.tcl"\n'
        r'    \("uplevel" body line 1\)\n    invoked from within\n'
        r'"uplevel #0 \[list source \$file\]"\n\n\n'
        r"This probably means that tk wasn't installed properly\."
    )
    return bool(re.fullmatch(wrapper, message.replace("\r\n", "\n"), re.IGNORECASE))


def _matching_initializer_trace(traceback, message):
    # pytest adds E prefixes to exception lines; remove only that known display
    # prefix, never normalize error paths or infer the inner cause from keywords.
    text = re.sub(r"(?m)^E {7}", "", traceback)
    # A later initializer error cannot erase an earlier exception in the same
    # failure. Match Python/pytest exception structure, not exception keywords
    # that can also occur in ordinary frame source or local variable values.
    if re.search(
            r"(?m)^(?:During handling of the above exception, another exception occurred:|"
            r"The above exception was the direct cause of the following exception:)$", text):
        return False
    exception_name = r"(?:[A-Za-z_]\w*\.)*[A-Za-z_]\w*"
    if (len(re.findall(r"(?m)^" + exception_name + r":(?:[ \t]|$)", text)) != 1
            or re.search(r"(?m)^E {7}" + exception_name + r"[ \t]*$", traceback)):
        return False
    errors = list(re.finditer(r"(?m)^_tkinter\.TclError: ", text))
    if len(errors) != 1 or not re.search(
            r"(?m)^[> \t]*self\.tk[ \t]*=[ \t]*_tkinter\.create\(", text[:errors[0].start()]):
        return False
    error = text[errors[0].start():].rstrip()
    error = re.sub(r'\n[^\r\n]+[/\\]tkinter[/\\]__init__\.py:\d+: TclError$', "", error).rstrip()
    return error == message.replace("\r\n", "\n")


def retry_eligible(folder, result, preflight=None):
    """Only narrow, recorded initializer read errors or OS resource launch errors."""
    launch = result.get("runner_start_error")
    if launch:
        return launch.get("winerror") in {8, 1450} or launch.get("errno") in {11, 12}
    if preflight is None:
        return False
    if preflight.get("outcome") == "success":
        report = folder / "junit.xml"
        events_path = folder / "events.jsonl"
        if not report.is_file() or not events_path.is_file():
            return False
        try:
            junit = ET.parse(report)
            failures = [(case, failure) for case in junit.iter("testcase")
                        for tag in ("failure", "error") for failure in case.findall(tag)]
            events = [parse_json(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
        except (ET.ParseError, ValueError):
            return False
        event_fields = {"nodeid", "outcome", "when", "subtest", "traceback"}
        if any(not isinstance(event, dict) or set(event) != event_fields
               or not isinstance(event["nodeid"], str) or not event["nodeid"]
               or not isinstance(event["outcome"], str) or event["outcome"] not in {"passed", "failed"}
               or not isinstance(event["when"], str) or event["when"] not in {"setup", "call", "teardown"}
               or type(event["subtest"]) is not bool
               or (event["traceback"] is not None and not isinstance(event["traceback"], str))
               for event in events):
            return False
        unsuccessful = [event for event in events if event["outcome"] != "passed"]
        if (not failures or len(failures) != len(unsuccessful) or list(junit.iter("skipped"))
                or len(failures) != len(list(junit.iter("failure"))) + len(list(junit.iter("error")))):
            return False
        by_case = {}
        for event in unsuccessful:
            parts = event["nodeid"].split("::")
            if len(parts) < 2 or event["subtest"] or event["when"] not in {"setup", "call"}:
                return False
            classname = ".".join([parts[0].replace("\\", "/").removesuffix(".py").replace("/", "."), *parts[1:-1]])
            key = (classname, parts[-1])
            if key in by_case:
                return False
            by_case[key] = event
        for case, failure in failures:
            event = by_case.pop((case.get("classname", ""), case.get("name", "")), None)
            message = failure.get("message", "")
            if (event is None or not _initializer_read_message(message)
                    or not isinstance(failure.text, str)
                    or failure.text.replace("\r\n", "\n") != (event["traceback"] or "").replace("\r\n", "\n")
                    or not _matching_initializer_trace(event["traceback"] or "", message)):
                return False
        return not by_case
    if preflight.get("outcome") != "failed":
        return False
    probe = folder / "preflight.json"
    report = folder / "preflight.xml"
    if not probe.is_file() or not report.is_file() or read_json(probe).get("stage") != "tk_initialization":
        return False
    try:
        failures = list(ET.parse(report).iter("failure")) + list(ET.parse(report).iter("error"))
    except ET.ParseError:
        return False
    if len(failures) != 1:
        return False
    message = failures[0].get("message", "")
    # No arbitrary TclError/assertion/skip or unknown cause is eligible.
    return bool(re.fullmatch(r'_tkinter\.TclError: (?:couldn.t read file|error reading) "[^"\r\n]+\.tcl": (?:permission denied|no such file or directory|no error|invalid argument)', message, re.IGNORECASE))


def history(root, *, policy=None, task_id=None):
    root = Path(root)
    if not root.is_dir():
        raise ValueError("execution history store is missing")
    for ledger in root.rglob("ledger.json"):
        data = read_json(ledger)
        read_local_ledger(ledger.parent, data.get("task_id"))
    if policy not in (None, POLICY):
        raise ValueError("unknown history read policy")
    paths = sorted(root.rglob("manifest.json"))
    # A /2 aggregate may recover the task from bound contexts. A borrowed CFF
    # record mixed into another task must not receive this read exception.
    tasks = {read_json(path.parent / "history-context.json").get("task_id") for path in paths}
    if task_id is None and len(tasks) == 1:
        task_id = next(iter(tasks))
    if policy == POLICY and CFF_HISTORY_TASK in tasks and tasks != {task_id}:
        raise ValueError("CFF retained history task scope mismatch")
    values = []
    for started in Path(root).rglob("started.json"):
        if not (started.parent / "manifest.json").is_file():
            raise ValueError("unfinished execution history; retain and resolve before restarting")
    for path in paths:
        values.append((path, read_verified_manifest(path, history_policy=policy, task_id=task_id)))
    unique = {}
    for path, value in values:
        identity = value["execution_id"]
        if identity in unique and unique[identity][1] != value:
            raise ValueError("conflicting duplicate execution history")
        unique.setdefault(identity, (path, value))
    # The same retained artifact may be downloaded from multiple later bundles.
    # It is one execution, and all original files remain unchanged.
    return list(unique.values())


def pr43_gui_block(head, authorization, proposal, human_reply):
    """Encode the exact adopted bytes; this does not create authorization."""
    data = {"schema": "pr43-gui-once/1", **PR43_GUI_ONCE, "new_head": head}
    for name, path in (("authorization", authorization), ("proposal", proposal), ("human_reply", human_reply)):
        data[name + "_base64"] = base64.b64encode(Path(path).read_bytes()).decode()
    _pr43_declaration(data)
    return "<!-- pr43-gui-once\n" + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n-->"


def _pr43_declaration(data):
    fields = {"schema", "new_head", "authorization_base64", "proposal_base64", "human_reply_base64", *PR43_GUI_ONCE}
    if set(data) != fields or data["schema"] != "pr43-gui-once/1":
        raise ValueError("unknown PR43 GUI supplement declaration")
    if any(type(data[key]) is not type(value) or data[key] != value for key, value in PR43_GUI_ONCE.items()):
        raise ValueError("PR43 GUI supplement belongs to another adopted contract")
    if not re.fullmatch(r"[0-9a-f]{40}", data["new_head"] or "") or data["new_head"] in {data["baseline"], data["starting_head"]}:
        raise ValueError("PR43 GUI supplement requires the new frozen feature head")
    payloads = {}
    for name in ("authorization", "proposal", "human_reply"):
        raw = base64.b64decode(data[name + "_base64"], validate=True)
        if hashlib.sha256(raw).hexdigest() != data[name + "_sha256"]:
            raise ValueError("PR43 original adopted bytes differ: " + name)
        payloads[name] = raw
    authorization = parse_json(payloads["authorization"].decode("utf-8"))
    proposal = parse_json(payloads["proposal"].decode("utf-8"))
    if (authorization["human_message_exact"] != "核准"
            or payloads["human_reply"].decode("utf-8").strip() != "核准"
            or authorization["proposal_sha256"] != data["proposal_sha256"]
            or proposal["task"] != data["task_id"] or proposal["repository"] != data["repository"]
            or proposal["pr"] != data["pr"] or proposal["starting_head"] != data["starting_head"]
            or proposal["baseline"] != data["baseline"]
            or proposal["original_gui_execution"] != data["execution_id"]):
        raise ValueError("PR43 adopted literal/identity differs")
    return data


def _pr43_ref(root, reference):
    if set(reference) != {"path", "sha256"}:
        raise ValueError("malformed PR43 side-evidence reference")
    relative = Path(reference["path"])
    target = (root / relative).resolve()
    if relative.as_posix() not in {"pr43-gui-once/declaration.json", "pr43-gui-once/claim.json"}:
        raise ValueError("unknown PR43 side-evidence path")
    if not target.exists():
        # Restored bundles retain their original root-relative references.
        # Resolve only this exact namespace and digest, never rewrite bytes.
        copies = [path for path in root.rglob(relative.name)
                  if path.parent.name == "pr43-gui-once" and digest(path) == reference["sha256"]]
        if copies:
            target = copies[0].resolve()
    if (not target.is_relative_to(root.resolve()) or not target.is_file()
            or digest(target) != reference["sha256"]):
        raise ValueError("missing/corrupt PR43 side evidence")
    return target


def _pr43_setup_source():
    # Historical evidence only: never import/execute the retired Tk initializer.
    # The full immutable blob pin precedes AST selection; no current-source or
    # missing-object fallback. The original archive/adoption/skip checks remain.
    raw = subprocess.check_output([
        "git", "show",
        "d59358cd45cc65a73b1d4dcb0aa228ffe7233435:tests/test_manual_actual_gui_batch_v570.py",
    ], cwd=ROOT)
    if hashlib.sha256(raw).hexdigest() != "1373c00997ca98db7562a39b1b0370b818bc9302c1e2c3a9ef7bc915ade416da":
        raise ValueError("PR43 archived initializer full source changed")
    source = raw.decode("utf-8")
    cls = next(node for node in ast.parse(source).body if isinstance(node, ast.ClassDef)
               and node.name == "ManualActualGuiVisibleLayoutTests")
    setup = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "setUpClass")
    first = min([setup.lineno, *[node.lineno for node in setup.decorator_list]])
    text = "".join(source.splitlines(keepends=True)[first - 1:setup.end_lineno])
    if hashlib.sha256(text.encode()).hexdigest() != PR43_GUI_ONCE["setup_sha256"]:
        raise ValueError("PR43 actual Tk initializer source changed")


def _pr43_skips(folder, value):
    """Only the source-bound setUpClass wrapper around actual Tk init reads."""
    _pr43_setup_source()
    tree = junit_binding(folder / "junit.xml", value["execution_id"], value["inventory_sha256"])
    if list(tree.iter("failure")) or list(tree.iter("error")):
        raise ValueError("PR43 mixed assertion/error failure is not an initializer skip")
    events = [parse_json(line) for line in (folder / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    bad = [event for event in events if event["outcome"] != "passed"]
    cases = [case for case in tree.iter("testcase") if case.find("skipped") is not None]
    prefix = "tests/test_manual_actual_gui_batch_v570.py::ManualActualGuiVisibleLayoutTests::"
    inventory = read_json(folder / "inventory.json")
    expected = {row["nodeid"] for row in inventory["records"] if row["group"] == "gui" and row["nodeid"].startswith(prefix)}
    if (len(cases) != 11 or len(expected) != 11 or len(bad) != 11
            or Counter(event["nodeid"] for event in bad) != Counter(expected)
            or any(event["when"] != "setup" or event["subtest"] or event["outcome"] != "skipped" for event in bad)):
        raise ValueError("PR43 original skip identities/phases differ")
    raw = (folder / "raw.log").read_text(encoding="utf-8")
    # The outer Tcl init discovery wrapper and inner exact init.tcl read failure
    # are both mandatory; no generic TclError or post-initialization skip.
    pattern = re.compile(
        r"Tk display unavailable: Can't find a usable init\.tcl in the following directories: \n[^\r\n]+\n\n"
        r'(?P<file>[^"\r\n]+/init\.tcl): couldn\'t read file "(?P=file)": (?P<reason>No error|permission denied|no such file or directory|invalid argument)\n'
        r'couldn\'t read file "(?P=file)": (?P=reason)\n    while executing\n"source (?P=file)"\n'
        r'    \("uplevel" body line 1\)\n    invoked from within\n"uplevel #0 \[list source \$tclfile\]"\n\n\n'
        r"This probably means that Tcl wasn't installed properly\.")
    messages = {}
    for case in cases:
        if case.get("classname") != "tests.test_manual_actual_gui_batch_v570.ManualActualGuiVisibleLayoutTests":
            raise ValueError("PR43 unknown skip wrapper owner")
        message = case.find("skipped").get("message", "")
        match = pattern.fullmatch(message)
        if not match or match["file"] != value["environment"]["environment"]["TCL_LIBRARY"].replace("\\", "/") + "/init.tcl":
            raise ValueError("PR43 unknown/non-initialization resource wrapper")
        if message not in raw:
            raise ValueError("PR43 raw/JUnit skip reason differs")
        messages[prefix + case.get("name", "")] = message
    if set(messages) != expected or len(set(messages.values())) != 1:
        raise ValueError("PR43 original JUnit identities/reasons differ")
    for event in bad:
        trace = ast.literal_eval(event["traceback"])
        if (not isinstance(trace, tuple) or len(trace) != 3 or trace[1] != 523
                or not trace[0].replace("\\", "/").endswith("/_pytest/unittest.py")
                or trace[2] != "Skipped: " + messages[event["nodeid"]]):
            raise ValueError("PR43 raw/JUnit/events skip wrapper differs")
    selected = {row["nodeid"] for row in inventory["records"] if row["group"] == "gui"}
    calls = Counter(event["nodeid"] for event in events if event["when"] == "call" and not event["subtest"])
    if calls != Counter(selected - expected) or any(event["nodeid"] not in selected for event in events):
        raise ValueError("PR43 original other GUI execution is incomplete")


def validate_pr43_gui_once(root, context, candidate, *, env=None, inventory=None, require_claim=False):
    """The sole admission validator, also consumed by aggregate/coverage."""
    root = Path(root).resolve()
    binding = context.get("pr43_gui_once")
    original_paths = [path for path in root.rglob("manifest.json")
                      if read_json(path).get("execution_id") == PR43_GUI_ONCE["execution_id"]]
    if binding is None:
        if context.get("task_id") == PR43_GUI_ONCE["task_id"] and (
                original_paths or candidate.get("parents", [None])[:1] == [PR43_GUI_ONCE["baseline"]]):
            raise ValueError("PR43 adopted supplement evidence missing; candidate change cannot reset budget")
        return None
    if set(binding) not in ({"declaration"}, {"declaration", "claim"}):
        raise ValueError("unknown PR43 context binding")
    declaration_path = _pr43_ref(root, binding["declaration"])
    data = _pr43_declaration(read_json(declaration_path))
    if (context.get("task_id") != data["task_id"] or candidate["parents"] != [data["baseline"], data["new_head"]]
            or not original_paths or candidate["head"] in {data["starting_head"], data["new_head"]}):
        raise ValueError("PR43 candidate/task/base/head admission differs")
    if git("show", "-s", "--format=%P", data["new_head"]).split() != [data["starting_head"]]:
        raise ValueError("PR43 frozen feature must directly preserve the adopted starting head")
    original_path = original_paths[0]
    original = read_verified_manifest(original_path)
    if (digest(original_path) != data["manifest_sha256"]
            or original["ci"] != {"run_id": data["run_id"], "attempt": data["attempt"], "job": "grouped", "event": "pull_request"}
            or original["candidate"]["parents"] != [data["baseline"], data["starting_head"]]
            or original["retry_eligible"] or original["retry_of"] is not None
            or original["code_blockers"] or original["outcome"] != "failed"):
        raise ValueError("PR43 original immutable execution differs")
    archive = declaration_path.parent / "pr-artifact.zip"
    if not archive.is_file() or digest(archive) != data["archive_sha256"]:
        raise ValueError("PR43 original binary artifact missing/corrupt")
    with zipfile.ZipFile(archive) as zipped:
        names = zipped.namelist()
        if len(names) != len(set(names)):
            raise ValueError("PR43 original artifact duplicate member")
        for info in zipped.infolist():
            path = Path(info.filename)
            target = (original_path.parent.parent / path).resolve()
            if (path.is_absolute() or ".." in path.parts or "\\" in info.filename
                    or not target.is_relative_to(original_path.parent.parent.resolve())
                    or ((info.external_attr >> 16) & 0o170000) == 0o120000):
                raise ValueError("unsafe PR43 artifact member")
            if not info.is_dir() and (not target.is_file() or target.read_bytes() != zipped.read(info)):
                raise ValueError("PR43 artifact original extracted bytes differ")
    _pr43_skips(original_path.parent, original)
    if env is not None and env != original["environment"]:
        raise ValueError("PR43 supplement actual environment changed")
    if inventory is not None:
        previous = read_json(original_path.parent / "inventory.json")
        if set(previous["gui_ids"]) != set(inventory["gui_ids"]):
            raise ValueError("PR43 required GUI identities changed")
    claims = {}
    for path in root.rglob("pr43-gui-once/claim.json"):
        claim = read_json(path)
        if (set(claim) != {"execution_id", "candidate", "environment", "declaration_sha256", "declared_at"}
                or claim["declaration_sha256"] != digest(declaration_path)
                or claim["candidate"] != candidate or claim["environment"] != original["environment"]
                or not re.fullmatch(r"[0-9a-f]{32}", claim["execution_id"])
                or not claim["declared_at"]):
            raise ValueError("PR43 supplement claim malformed/cross-head")
        if claim["execution_id"] in claims and claims[claim["execution_id"]][1] != claim:
            raise ValueError("PR43 conflicting duplicate supplement claim")
        claimed_context = path.parent.parent / claim["execution_id"] / "history-context.json"
        if not claimed_context.is_file():
            raise ValueError("PR43 durable claim context missing; budget remains consumed")
        claimed_binding = read_json(claimed_context).get("pr43_gui_once", {})
        if (set(claimed_binding) != {"declaration", "claim"}
                or claimed_binding["declaration"]["sha256"] != digest(declaration_path)
                or claimed_binding["claim"] != artifact_entry(path, path.parent.parent)):
            raise ValueError("PR43 durable claim context binding differs")
        claims.setdefault(claim["execution_id"], (path, claim))
    linked = [read_json(path) for path in root.rglob("manifest.json")
              if read_json(path).get("retry_of") == data["execution_id"]]
    if any(value["execution_id"] not in claims for value in linked):
        raise ValueError("PR43 retained supplement has missing durable claim")
    if len(claims) > 1:
        raise ValueError("PR43 original plus supplement GUI budget exceeded")
    if "claim" in binding:
        bound = _pr43_ref(root, binding["claim"])
        if not claims or read_json(bound) != next(iter(claims.values()))[1]:
            raise ValueError("PR43 supplement claim reference differs")
    elif require_claim:
        raise ValueError("PR43 supplement claim reference missing")
    return {"original": (original_path, original), "claims": claims, "data": data,
            "binding": binding, "declaration_path": declaration_path}


def import_pr43_gui_once(body, root, context, candidate, api, repository, pr_number):
    blocks = re.findall(r"<!-- pr43-gui-once\s+(.*?)\s*-->", body or "", re.DOTALL)
    if not blocks:
        validate_pr43_gui_once(root, context, candidate)
        return
    if len(blocks) != 1 or len(blocks[0].encode()) > 12000:
        raise ValueError("exactly one bounded PR43 supplement block is required")
    data = _pr43_declaration(parse_json(blocks[0]))
    if repository != data["repository"] or pr_number != data["pr"]:
        raise ValueError("PR43 supplement repository/PR mismatch")
    metadata = api(f"repos/{repository}/actions/artifacts/{data['artifact_id']}")
    if (metadata["id"] != data["artifact_id"] or metadata["digest"] != "sha256:" + data["archive_sha256"]
            or metadata["workflow_run"]["id"] != data["run_id"] or metadata["expired"]):
        raise ValueError("PR43 original artifact identity/digest differs")
    folder = Path(root) / "pr43-gui-once"
    folder.mkdir()
    payload = subprocess.check_output(["gh", "api", f"repos/{repository}/actions/artifacts/{data['artifact_id']}/zip"])
    if hashlib.sha256(payload).hexdigest() != data["archive_sha256"]:
        raise ValueError("PR43 original artifact downloaded bytes differ")
    with (folder / "pr-artifact.zip").open("xb") as output:
        output.write(payload)
    write_json(folder / "declaration.json", data)
    context["pr43_gui_once"] = {"declaration": artifact_entry(folder / "declaration.json", Path(root))}
    validate_pr43_gui_once(root, context, candidate)

def retry_history(values, key, supplement=None):
    if supplement is not None:
        original_path, original = supplement["original"]
        current = [(path, value) for path, value in values if value["group"] == "gui" and value["retry_key"] == key]
        claims = supplement["claims"]
        if len(current) > 1:
            raise ValueError("PR43 original plus supplement GUI budget exceeded")
        if current:
            value = current[0][1]
            if (value["retry_of"] != original["execution_id"] or value["execution_id"] not in claims
                    or value["candidate"] != claims[value["execution_id"]][1]["candidate"]):
                raise ValueError("PR43 supplement retry/claim relation differs")
        if claims and not current:
            raise ValueError("unfinished PR43 supplement declaration consumes final GUI budget")
        return [(original_path, original), *current]
    values = [(path, value) for path, value in values if value["retry_key"] == key and value["group"] == "gui"]
    values.sort(key=lambda pair: pair[1]["started_at"])
    if len(values) > 2:
        raise ValueError("GUI retry budget exceeded")
    if values:
        first = values[0][1]
        if first["retry_of"] is not None:
            raise ValueError("missing original GUI attempt")
        if len(values) == 2:
            second = values[1][1]
            if second["retry_of"] != first["execution_id"] or not first["retry_eligible"] or first["code_blockers"] or first["outcome"] == "success":
                raise ValueError("illegal GUI retry relation")
    return values


def collect_inventory(folder, tests=None):
    command = [sys.executable, str(Path(audit.__file__)), "collect-groups", str(folder / "inventory.json")]
    if tests:
        command += ["--tests", str(tests)]
    result = run_process(command, folder, "collection.log", 120)
    if result["outcome"] != "success":
        raise ValueError("collection failed; see " + str(folder / "collection.log"))
    return audit.validate_groups(read_json(folder / "inventory.json"))


LOCAL_SCHEMA = "zhuyin-local-history/2"
HANDOFF_SCHEMA = "zhuyin-local-history-handoff/2"
HANDOFF_LIMIT = 40000
REPOSITORY = "discoveryray/zhuyin-proofreader"


def local_ledger(task_id):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", task_id or ""):
        raise ValueError("invalid original task ID")
    common = Path(git("rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = ROOT / common
    return common.resolve().parent / "tmp" / "validation-task-ledgers" / task_id


@contextmanager
def task_lock(folder):
    path = folder / ".task.lock"
    with path.open("x", encoding="utf-8") as handle:
        handle.write(str(os.getpid()))
    try:
        yield
    finally:
        path.unlink()


def bootstrap_local(task_id, source_ref, baseline):
    if not isinstance(source_ref, str) or not source_ref.strip():
        raise ValueError("original task authorization source_ref is required")
    if not re.fullmatch(r"[0-9a-f]{40}", baseline or "") or baseline == "0" * 40:
        raise ValueError("original task baseline must be a complete SHA")
    folder = local_ledger(task_id)
    folder.parent.mkdir(parents=True, exist_ok=True)
    folder.mkdir()  # Once only; an absent/corrupt ledger is never reinitialized.
    with task_lock(folder):
        store_id = uuid.uuid4().hex
        write_json(folder / "ledger.json", {"schema": LOCAL_SCHEMA, "task_id": task_id,
                   "source_ref": source_ref, "baseline": baseline, "repository": REPOSITORY,
                   "created_at": utc_now(), "store_id": store_id})
        for name in ("executions", "declarations"):
            store = folder / name
            store.mkdir()
            write_json(store / "store.json", {"schema": "zhuyin-local-store/1", "store_id": store_id})
    return folder


def read_local_ledger(folder, task_id):
    data = read_json(folder / "ledger.json")
    if set(data) != {"schema", "task_id", "source_ref", "baseline", "repository", "created_at", "store_id"} or data["schema"] != LOCAL_SCHEMA or data["task_id"] != task_id or data["repository"] != REPOSITORY or not data["source_ref"]:
        raise ValueError("invalid original task ledger")
    if not re.fullmatch(r"[0-9a-f]{40}", data["baseline"]) or data["baseline"] == "0" * 40:
        raise ValueError("invalid ledger baseline")
    if not re.fullmatch(r"[0-9a-f]{32}", data["store_id"]):
        raise ValueError("invalid local history store identity")
    for name in ("executions", "declarations"):
        store = folder / name
        if not store.is_dir() or not (store / "store.json").is_file():
            raise ValueError("local history store is missing: " + name)
        if read_json(store / "store.json") != {"schema": "zhuyin-local-store/1", "store_id": data["store_id"]}:
            raise ValueError("local history store identity mismatch")
    declared = {}
    for path in (folder / "declarations").iterdir():
        if path.name == "store.json":
            continue
        value = read_json(path)
        if (not re.fullmatch(r"[0-9a-f]{32}\.json", path.name)
                or set(value) != {"execution_id", "group", "candidate", "declared_at", "store_id"}
                or value["execution_id"] != path.stem or value["store_id"] != data["store_id"]
                or value["group"] not in {"core", "gui"}):
            raise ValueError("invalid durable execution declaration")
        declared[path.stem] = value
    executions = {path.name for path in (folder / "executions").iterdir() if path.is_dir()}
    if executions != set(declared):
        raise ValueError("declared execution history is missing or undeclared")
    for identity, declaration in declared.items():
        path = folder / "executions" / identity / "manifest.json"
        if not path.is_file():
            raise ValueError("unfinished declared execution; retain original history")
        value = read_verified_manifest(path)
        if any(value[key] != declaration[key] for key in ("execution_id", "group", "candidate")):
            raise ValueError("execution differs from durable declaration")
        context = read_json(path.parent / "history-context.json")
        if context != data:
            raise ValueError("execution differs from original local ledger")
    return data


def export_local_history(task_id, output):
    folder = local_ledger(task_id)
    with task_lock(folder):
        data = read_local_ledger(folder, task_id)
        if (folder / "sealed.json").exists():
            raise ValueError("local history already sealed; retain original handoff")
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(folder / "ledger.json", "ledger.json")
            archive.write(folder / "executions/store.json", "executions/store.json")
            for declaration in sorted((folder / "declarations").iterdir()):
                archive.write(declaration, "declarations/" + declaration.name)
            # Export only explicit evidence artifacts, never basetemp/materials.
            for manifest, value in history(folder / "executions"):
                paths = [manifest, *(manifest.parent / item["path"] for item in value["artifacts"].values())]
                for path in paths:
                    archive.write(path, "executions/" + path.relative_to(folder / "executions").as_posix())
        payload = raw.getvalue()
        result = {"schema": HANDOFF_SCHEMA, "task_id": task_id, "source_ref": data["source_ref"],
                  "archive_sha256": hashlib.sha256(payload).hexdigest(),
                  "archive_base64": base64.b64encode(payload).decode("ascii")}
        if len(json.dumps(result).encode()) > HANDOFF_LIMIT:
            raise ValueError("local handoff exceeds PR evidence block budget; preserve history and arrange explicit raw-artifact handoff")
        output = Path(output)
        if output.exists():
            raise FileExistsError(output)
        # Sealing is durable before publishing. Interruption never reopens runs.
        write_json(folder / "sealed.json", {"archive_sha256": result["archive_sha256"],
                                            "output": str(output.resolve()), "sealed_at": utc_now(), "handoff": result})
        write_json(output, result)
        return result


def import_local_handoff(body, evidence_root, task_id=None):
    blocks = re.findall(r"<!-- validation-local-history\s+(.*?)\s*-->", body or "", re.DOTALL)
    if len(blocks) != 1 or len(blocks[0].encode()) > HANDOFF_LIMIT:
        raise ValueError("exactly one bounded local-history handoff block is required")
    data = parse_json(blocks[0])
    if set(data) != {"schema", "task_id", "source_ref", "archive_sha256", "archive_base64"} or data["schema"] != HANDOFF_SCHEMA or not data["source_ref"]:
        raise ValueError("invalid local-history handoff contract")
    if task_id is not None and data["task_id"] != task_id:
        raise ValueError("local-history task mismatch")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", data["task_id"]):
        raise ValueError("invalid handoff task ID")
    payload = base64.b64decode(data["archive_base64"], validate=True)
    if hashlib.sha256(payload).hexdigest() != data["archive_sha256"]:
        raise ValueError("local-history archive digest mismatch")
    destination = Path(evidence_root) / "local-history"
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or "ledger.json" not in names:
            raise ValueError("duplicate or absent ledger archive member")
        if sum(info.file_size for info in archive.infolist()) > 16 * 1024 * 1024:
            raise ValueError("local history uncompressed size exceeds bounded handoff")
        for info in archive.infolist():
            path = Path(info.filename)
            if path.is_absolute() or ".." in path.parts or "\\" in info.filename or (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("unsafe local-history archive path")
            marker = info.filename in {"ledger.json", "executions/store.json", "declarations/store.json"}
            declaration = len(path.parts) == 2 and path.parts[0] == "declarations" and re.fullmatch(r"[0-9a-f]{32}\.json", path.name)
            execution = len(path.parts) == 3 and path.parts[0] == "executions" and path.suffix in {".json", ".jsonl", ".xml", ".log"}
            if not (marker or declaration or execution):
                raise ValueError("unexpected local-history archive content")
        destination.mkdir()  # Never overlay or overwrite retained history.
        for info in archive.infolist():
            target = destination / info.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as handle:
                handle.write(archive.read(info))
    original = read_local_ledger(destination, data["task_id"])
    if original["source_ref"] != data["source_ref"]:
        raise ValueError("local-history authorization source mismatch")
    write_json(destination / "handoff.json", data)
    return data


def validate_history_context(root, candidate):
    """Formal coverage starts only from the authoritative PR CI history scope.

    Local targeted fixtures do not claim formal coverage. No empty directory is
    accepted as evidence of a zero retry count.
    """
    root = Path(root).resolve()
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("GITHUB_EVENT_NAME") != "pull_request":
        raise ValueError("formal groups require PR CI authoritative history; use local targeted pytest for development")
    if root != (ROOT / "tmp" / "validation-evidence").resolve():
        raise ValueError("formal CI evidence root is fixed; changing directories cannot reset history")
    indexes = [read_json(path) for path in root.glob("history-index-*.json")]
    matching = [value for value in indexes if value.get("current_run") == int(os.environ["GITHUB_RUN_ID"])
                and value.get("current_attempt") == int(os.environ["GITHUB_RUN_ATTEMPT"])]
    if len(matching) != 1 or matching[0].get("candidate") != candidate:
        raise ValueError("missing or stale authoritative history scope index")
    current_ids = {value["execution_id"] for _, value in history(
        root, policy=POLICY, task_id=matching[0]["task_id"])}
    if not set(matching[0]["executions"]) <= current_ids:
        raise ValueError("retained authoritative execution history is missing")
    return matching[0]


def _run_group(group, evidence_root, *, timeout=None, tests=None, mode=None, code_blockers=False, local_context=None):
    if group != "core":
        raise ValueError(audit.WINDOW_NOTICE)
    if mode != "validation":
        raise ValueError("explicit --mode validation is required for formal grouped execution")
    if git("status", "--porcelain"):
        raise ValueError("frozen formal source must be clean")
    candidate = snapshot()
    env = environment()
    validate_environment(env)
    evidence_root = Path(evidence_root).resolve()
    evidence_root.mkdir(parents=True, exist_ok=True)
    context = local_context if local_context is not None else validate_history_context(evidence_root, candidate)
    lock = evidence_root / ".execution.lock"
    with lock.open("x", encoding="utf-8") as handle:
        handle.write(str(os.getpid()))
    try:
        previous = history(evidence_root, policy=POLICY if local_context is None else None,
                           task_id=context["task_id"])
        # Current core policy creates no new GUI-once obligation. Legacy
        # aggregate admission and immutable history readers remain unchanged.
        supplement = (None if POLICY == "validation-flow/no-real-tk/1" else
                      validate_pr43_gui_once(evidence_root, context, candidate, env=env))
        key = canonical_digest({"task": context["task_id"], "tree": candidate["tree"]})
        matching = [(path, value) for path, value in previous
                    if value["retry_key"] == key and value["group"] == group]
        if any(value["outcome"] != "success" for _, value in matching):
            raise ValueError("same-candidate core failure remains unresolved; no automatic core retry")
        if matching and matching[-1][1]["schema"] == SCHEMA and matching[-1][1]["outcome"] == "success" and matching[-1][1]["candidate"] == candidate and ((matching[-1][1]["ci"]["event"] == "local") == (local_context is not None)):
            if matching[-1][1]["environment"] != env:
                raise ValueError("successful execution environment changed")
            return matching[-1][0]
        retry_of = None
        if code_blockers:
            raise ValueError("confirmed code blocker prevents execution/retry")
        execution_id = uuid.uuid4().hex
        folder = evidence_root / execution_id
        if local_context is not None:
            write_json(evidence_root.parent / "declarations" / (execution_id + ".json"),
                       {"execution_id": execution_id, "group": group, "candidate": candidate,
                        "declared_at": utc_now(), "store_id": local_context["store_id"]})
        folder.mkdir()
        if group == "gui" and supplement is not None:
            claim = supplement["declaration_path"].parent / "claim.json"
            write_json(claim, {"execution_id": execution_id, "candidate": candidate, "environment": env,
                               "declaration_sha256": digest(supplement["declaration_path"]), "declared_at": utc_now()})
            context = {**context, "pr43_gui_once": {**context["pr43_gui_once"],
                       "claim": artifact_entry(claim, evidence_root)}}
            write_json(folder / "history-context.json", context)
        inventory = collect_inventory(folder, tests)
        inventory_hash = digest(folder / "inventory.json")
        if inventory["schema"] != "zhuyin-test-groups/2":
            raise ValueError("current execution requires no-window inventory")
        temporary = folder / "temp"
        temporary.mkdir()
        child_env = os.environ.copy()
        command = [sys.executable, str(Path(__file__).resolve()), "_pytest", str(folder), group]
        ci = {"run_id": int(os.environ["GITHUB_RUN_ID"]) if os.environ.get("GITHUB_RUN_ID") else None, "attempt": int(os.environ["GITHUB_RUN_ATTEMPT"]) if os.environ.get("GITHUB_RUN_ATTEMPT") else None,
              "job": os.environ.get("GITHUB_JOB"), "event": os.environ.get("GITHUB_EVENT_NAME")}
        if local_context is not None:
            ci = {"run_id": None, "attempt": None, "job": None, "event": "local"}
        data = {"schema": SCHEMA, "policy": POLICY, "window_validation": audit.WINDOW_NOTICE,
                "execution_id": execution_id, "group": group,
                "candidate": candidate, "command": command, "environment": env,
                "inventory_sha256": inventory_hash, "started_at": utc_now(), "finished_at": None,
                "exit_code": None, "outcome": "interrupted", "verification": {"top_level": 0, "subtests": 0, "error": "unfinished"},
                "ci": ci, "retry_of": retry_of, "artifacts": {}, "retry_key": key, "retry_eligible": False,
                "preflight": None, "runner_start_error": None, "code_blockers": False}
        if not (folder / "history-context.json").exists():
            write_json(folder / "history-context.json", context)
        write_json(folder / "started.json", data)
        try:
            result = run_process(command, folder, "raw.log", timeout or DEFAULT_TIMEOUTS[group], child_env)
            data.update(exit_code=result["exit_code"], outcome=result["outcome"], runner_start_error=result["runner_start_error"])
            if result["outcome"] == "success":
                if snapshot() != candidate or git("status", "--porcelain"):
                    raise ValueError("tested source changed during execution")
                junit_binding(folder / "junit.xml", execution_id, inventory_hash)
                audit.verify_group(folder / "junit.xml", inventory, group)
                data["verification"] = verify_events(folder / "events.jsonl", inventory, group)
            else:
                data["verification"]["error"] = result["outcome"]
        except BaseException as error:
            data["outcome"] = "interrupted" if isinstance(error, KeyboardInterrupt) else "failed"
            data["verification"]["error"] = str(error)
            if not (folder / "raw.log").exists():
                (folder / "raw.log").write_text(str(error), encoding="utf-8")
        finally:
            data["finished_at"] = utc_now()
            names = {"inventory": "inventory.json", "started": "started.json", "history_context": "history-context.json", "collection_log": "collection.log",
                     "raw_log": "raw.log", "junit": "junit.xml", "events": "events.jsonl",
                     "preflight_log": "preflight.log", "preflight_junit": "preflight.xml", "preflight_result": "preflight.json"}
            data["artifacts"] = {name: artifact_entry(folder / file, folder) for name, file in names.items() if (folder / file).is_file()}
            write_json(folder / "manifest.json", data)
        read_verified_manifest(folder / "manifest.json")
        return folder / "manifest.json"
    finally:
        lock.unlink()


def run_group(group, evidence_root, *, task_id=None, **kwargs):
    if group != "core":
        raise ValueError(audit.WINDOW_NOTICE)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return _run_group(group, evidence_root, **kwargs)
    if kwargs.get("mode") != "validation":
        raise ValueError("explicit --mode validation is required for formal grouped execution")
    if not task_id:
        raise ValueError("local grouped validation requires original --task-id and pre-existing canonical ledger")
    folder = local_ledger(task_id)
    with task_lock(folder):
        context = read_local_ledger(folder, task_id)
        if (folder / "sealed.json").exists():
            raise ValueError("local history was sealed for PR handoff; no later local attempts are allowed")
        result = _run_group(group, folder / "executions", local_context=context, **kwargs)
        output = Path(evidence_root)
        output.mkdir(parents=True, exist_ok=True)
        write_json(output / ("local-result-" + uuid.uuid4().hex + ".json"),
                   {"manifest": str(result), "sha256": digest(result), "task_id": task_id})
        return result


def pytest_child(folder, group):
    if group != "core":
        raise ValueError(audit.WINDOW_NOTICE)
    import pytest
    folder = Path(folder).resolve()
    inventory = audit.validate_groups(read_json(folder / "inventory.json"))
    if inventory["schema"] != "zhuyin-test-groups/2":
        raise ValueError("retired inventory cannot be executed by the current runner")
    selected = {record["nodeid"] for record in inventory["records"] if record["group"] == group}
    records = [record for record in inventory["records"] if record["group"] == "core"]
    paths = sorted({record["nodeid"].split("::")[0] for record in records})

    class Evidence:
        def pytest_collection_modifyitems(self, session, config, items):
            actual = {item.nodeid for item in items}
            expected = {record["nodeid"] for record in records}
            if actual != expected or len(items) != len(expected):
                raise pytest.UsageError("source collection drift since inventory")
            excluded = [item for item in items if item.nodeid not in selected]
            items[:] = [item for item in items if item.nodeid in selected]
            config.hook.pytest_deselected(items=excluded)

        def pytest_sessionstart(self, session):
            from _pytest.junitxml import xml_key
            xml = session.config.stash.get(xml_key, None)
            if xml is None:
                raise pytest.UsageError("JUnit plugin missing")
            xml.add_global_property("validation_execution_id", folder.name)
            xml.add_global_property("validation_inventory_sha256", digest(folder / "inventory.json"))

        def pytest_runtest_logreport(self, report):
            row = {"nodeid": report.nodeid, "when": report.when, "outcome": report.outcome,
                   "subtest": hasattr(report, "context"),
                   "traceback": str(report.longrepr) if report.failed or report.skipped else None}
            with (folder / "events.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())

    # JUnit/plugins bind raw results to this unique execution, not to timestamps.
    return pytest.main([*paths, "-q", "-rs", "--capture=fd", f"--junitxml={folder / 'junit.xml'}",
                        f"--basetemp={folder / 'temp' / 'pytest'}"], plugins=[Evidence()])


def aggregate(evidence_root, head=None, ci=None, *, legacy=False):
    root = Path(evidence_root).resolve()
    values = history(root, policy=None if legacy else POLICY)
    if not values:
        raise ValueError("no execution evidence")
    by_group = {}
    for group in (("core", "gui") if legacy else ("core",)):
        matching = [(path, value) for path, value in values if value["group"] == group
                    and value["ci"]["event"] != "local"
                    and (head is None or value["candidate"]["head"] == head)
                    and value["schema"] == (LEGACY_SCHEMA if legacy else SCHEMA)]
        if not matching:
            raise ValueError("missing " + group + " evidence")
        matching.sort(key=lambda pair: pair[1]["started_at"])
        path, latest = matching[-1]
        if latest["outcome"] != "success" or latest["code_blockers"]:
            raise ValueError("latest " + group + " attempt is unresolved")
        if group == "gui":
            context = read_json(path.parent / "history-context.json")
            supplement = validate_pr43_gui_once(root, context, latest["candidate"], env=latest["environment"],
                                               inventory=read_json(path.parent / "inventory.json"), require_claim=True)
            retry_history(values, latest["retry_key"], supplement)
            if supplement is not None:
                claim = supplement["claims"][latest["execution_id"]][1]
                if claim["candidate"] != latest["candidate"]:
                    raise ValueError("PR43 selected GUI claim candidate differs")
        by_group[group] = (path, latest)
    core = by_group["core"][1]
    gui = by_group["gui"][1] if legacy else core
    if any(value["retry_key"] == core["retry_key"] and value["code_blockers"] for _, value in values):
        raise ValueError("retained same-candidate code blocker remains unresolved")
    if any(value["group"] == "core" and value["retry_key"] == core["retry_key"]
           and value["outcome"] != "success" for _, value in values):
        raise ValueError("earlier same-candidate core failure remains unresolved")
    for key in ("candidate", "environment", "inventory_sha256", "retry_key"):
        if core[key] != gui[key]:
            raise ValueError("mixed group evidence: " + key)
    result = {"schema": "zhuyin-validation-coverage/1" if legacy else COVERAGE_SCHEMA, "candidate": core["candidate"], "environment": core["environment"],
            "inventory_sha256": core["inventory_sha256"], "core_manifest": by_group["core"][0].relative_to(root).as_posix(),
            "gui_manifest": by_group["gui"][0].relative_to(root).as_posix() if legacy else None,
            "history_manifests": [path.relative_to(root).as_posix() for path, _ in values],
            "ci": (ci if ci is not None else {"run_id": int(os.environ["GITHUB_RUN_ID"]), "attempt": int(os.environ["GITHUB_RUN_ATTEMPT"]),
                    "job": os.environ["GITHUB_JOB"], "event": os.environ["GITHUB_EVENT_NAME"]}
                   if ci is not None or os.environ.get("GITHUB_RUN_ID") else gui["ci"]), "status": "success"}
    if not legacy:
        result.pop("gui_manifest")
        result.update(policy=POLICY, window_validation=audit.WINDOW_NOTICE)
    return result


def pr43_postmerge_handoff(event, candidate, api, repository):
    """Validate the sole adopted post-merge reference before importing history.

    Source pins identify originals; they do not authenticate reviewers or turn
    the retained P1/code report into a PASS. No private archive is republished.
    """
    pr = event["pull_request"]
    body = pr.get("body") or ""
    marker = "<!-- validation-postmerge-continuation"
    selected = marker in body or pr["head"]["ref"] == PR43_POSTMERGE_HISTORY["branch"]
    if not selected:
        return None
    blocks = re.findall(r"<!-- validation-postmerge-continuation\s+(.*?)\s*-->", body, re.DOTALL)
    if len(blocks) != 1 or len(blocks[0].encode()) > HANDOFF_LIMIT or "<!-- validation-local-history" in body:
        raise ValueError("exactly one bounded PR43 post-merge reference without a new archive is required")
    value = parse_json(blocks[0])
    if not isinstance(value, dict) or set(value) != set(PR43_POSTMERGE_HISTORY) | {"current_pr", "head"}:
        raise ValueError("invalid PR43 post-merge continuation fields")
    if any(type(value[key]) is not type(expected) or value[key] != expected
           for key, expected in PR43_POSTMERGE_HISTORY.items()):
        raise ValueError("PR43 post-merge continuation identity or original source differs")
    # Creation cannot know the assigned PR number. Only this explicit strategy
    # derives it from the immutable opened event; never infer or default it.
    declared_pr = value["current_pr"]
    number = pr.get("number")
    if (type(number) is not int or number <= 43
            or not ((type(declared_pr) is int and declared_pr == number)
                    or (type(declared_pr) is str and declared_pr == "event.pull_request.number"))):
        raise ValueError("PR43 post-merge current PR declaration/event differs")
    value = {**value, "current_pr": number}
    if (repository != REPOSITORY
            or value["head"] != pr["head"]["sha"]
            or not re.fullmatch(r"[0-9a-f]{40}", value["head"])
            or value["head"] == "0" * 40 or value["head"] == value["original_merge"]
            or pr["head"]["ref"] != value["branch"]
            or pr["base"].get("ref") != "develop" or pr["base"]["sha"] != value["original_merge"]
            or any(pr[side].get("repo", {}).get("full_name") != repository for side in ("head", "base"))):
        raise ValueError("PR43 post-merge current PR/base/head/repository differs")
    if (git("show", "-s", "--format=%P", value["head"]).split() != [value["original_merge"]]
            or git("show", "-s", "--format=%T", value["head"]) != candidate["tree"]
            or candidate["parents"] != [value["original_merge"], value["head"]]
            or git("show", "-s", "--format=%P", value["original_merge"]).split() != value["original_merge_parents"]
            or git("show", "-s", "--format=%T", value["original_merge"]) != value["original_merge_tree"]
            or git("show", "-s", "--format=%T", value["original_head"]) != value["original_merge_tree"]):
        raise ValueError("PR43 post-merge actual commit parents/tree differs")
    original = api("repos/discoveryray/zhuyin-proofreader/pulls/43")
    if (original.get("number") != 43 or original.get("state") != "closed" or original.get("merged") is not True
            or original.get("merge_commit_sha") != value["original_merge"]
            or original["head"]["sha"] != value["original_head"]
            or original["head"]["ref"] != PR43_ORIGINAL_BRANCH
            or original["base"]["sha"] != value["original_merge_parents"][0]
            or original["base"].get("ref") != "develop"
            or any(original[side].get("repo", {}).get("full_name") != repository for side in ("head", "base"))):
        raise ValueError("original merged PR43 identity differs")
    old_body = original.get("body") or ""
    old_blocks = re.findall(r"<!-- validation-local-history\s+(.*?)\s*-->", old_body, re.DOTALL)
    if (len(old_blocks) != 1 or len(old_blocks[0].encode()) > HANDOFF_LIMIT
            or hashlib.sha256(old_blocks[0].encode()).hexdigest() != value["original_history_block_sha256"]):
        raise ValueError("original PR43 history block differs")
    handoff = parse_json(old_blocks[0])
    if (handoff.get("schema") != HANDOFF_SCHEMA or handoff.get("task_id") != value["task_id"]
            or handoff.get("source_ref") != value["original_source_ref"]
            or handoff.get("archive_sha256") != value["original_archive_sha256"]):
        raise ValueError("original PR43 handoff identity differs")
    return value, old_body


def restore_pr43_original_push(destination, artifact, root):
    """Retain the exact merge ZIP; materialize only its complete group history.

    The sole known merge-short started record is not a pytest declaration.
    Its original bytes and every short source/log remain in the original ZIP;
    any other started record without a manifest still blocks restoration.
    """
    if (artifact.get("id") != PR43_ORIGINAL_PUSH_ARTIFACT
            or artifact.get("digest") != "sha256:" + PR43_ORIGINAL_PUSH_ARCHIVE_SHA256):
        raise ValueError("original PR43 merge artifact identity differs")
    payload = subprocess.check_output(["gh", "api",
        "repos/discoveryray/zhuyin-proofreader/actions/artifacts/11595410764/zip"])
    if hashlib.sha256(payload).hexdigest() != PR43_ORIGINAL_PUSH_ARCHIVE_SHA256:
        raise ValueError("original PR43 merge archive digest differs")
    destination.mkdir(parents=True)
    raw_path = destination / "pr-artifact.zip"
    with raw_path.open("xb") as handle:
        handle.write(payload)
    try:
        original_archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as error:
        raise ValueError("corrupt original PR43 merge archive") from error
    with original_archive as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or archive.testzip() is not None:
            raise ValueError("duplicate or corrupt original PR43 merge archive")
        for info in archive.infolist():
            name = PurePosixPath(info.filename)
            if (name.is_absolute() or ".." in name.parts or "\\" in info.filename
                    or ":" in info.filename or (info.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError("unsafe original PR43 merge archive member")
        files = {item.filename for item in archive.infolist() if not item.is_dir()}
        short_names = [name for name in files if len(PurePosixPath(name).parts) == 2
                       and PurePosixPath(name).parts[0].startswith("short-") and name.endswith("/short.json")]
        if len(short_names) != 1:
            raise ValueError("original PR43 merge short receipt missing")
        short_name = short_names[0]
        short_bytes = archive.read(short_name)
        if hashlib.sha256(short_bytes).hexdigest() != PR43_ORIGINAL_SHORT_SHA256:
            raise ValueError("original PR43 merge short receipt corrupt")
        short = parse_json(short_bytes.decode("utf-8-sig"))
        spec = PR43_POSTMERGE_HISTORY
        from scripts import validation_evidence as evidence
        if (short.get("schema") != evidence.SHORT_SCHEMA or short.get("policy") != POLICY
                or short.get("window_validation") != audit.WINDOW_NOTICE or short.get("outcome") != "success"
                or short.get("candidate") != {"head": spec["original_merge"], "tree": spec["original_merge_tree"],
                                               "parents": spec["original_merge_parents"]}
                or short.get("ci") != {"run_id": PR43_ORIGINAL_PUSH_RUN, "attempt": 1, "job": "short", "event": "push"}
                or short.get("coverage_sha256") != PR43_ORIGINAL_COVERAGE_SHA256
                or set(short.get("source", {})) != {"archive", "artifact", "assets", "commit", "pr", "pr_assets", "run", "runs"}):
            raise ValueError("original PR43 merge short scope differs")
        validate_environment(short["environment"])
        expected = evidence.short_commands(spec["original_merge_parents"][0], spec["original_merge"])
        if ([item["name"] for item in short["checks"]] != [name for name, _ in expected]
                or any(type(item["exit_code"]) is not int or item["exit_code"] != 0
                       or evidence._canonical_command(item["command"]) != evidence._canonical_command(command)
                       for item, (_, command) in zip(short["checks"], expected))):
            raise ValueError("original PR43 merge short checks differ")
        parent = PurePosixPath(short_name).parent
        byte_refs = []
        for entry in [*short["source"].values(), *(item["log"] for item in short["checks"])]:
            member = parent / entry["path"]
            if (set(entry) != {"path", "sha256"} or PurePosixPath(entry["path"]).is_absolute()
                    or ".." in member.parts or "\\" in entry["path"] or str(member) not in files
                    or hashlib.sha256(archive.read(str(member))).hexdigest() != entry["sha256"]):
                raise ValueError("original PR43 merge short raw artifact missing or corrupt")
            byte_refs.append({"member": str(member), "sha256": entry["sha256"]})
        short_start = str(parent / "started.json")
        if short_start not in files:
            raise ValueError("original PR43 merge short start missing")
        byte_refs.append({"member": short_start, "sha256": hashlib.sha256(archive.read(short_start)).hexdigest()})
        selected = set()
        for name in files:
            member = PurePosixPath(name)
            if member.name == "started.json" and name != short_start and str(member.parent / "manifest.json") not in files:
                raise ValueError("unfinished execution in original PR43 merge history")
            if member.name == "manifest.json":
                value = parse_json(archive.read(name).decode("utf-8-sig"))
                selected.add(name)
                for item in value["artifacts"].values():
                    relative = PurePosixPath(item["path"])
                    linked = member.parent / relative
                    if (relative.is_absolute() or ".." in relative.parts or "\\" in item["path"]
                            or str(linked) not in files
                            or hashlib.sha256(archive.read(str(linked))).hexdigest() != item["sha256"]):
                        raise ValueError("original PR43 merge group closure missing or corrupt")
                    selected.add(str(linked))
            if member.name == "ledger.json":
                selected.update(value for value in files if PurePosixPath(value).is_relative_to(member.parent))
        if short_start in selected:
            raise ValueError("original PR43 short start conflicts with group history")
        if not any(PurePosixPath(name).name == "manifest.json" for name in selected):
            raise ValueError("original PR43 merge group history missing")
        for name in sorted(selected):
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as handle:
                handle.write(archive.read(name))
    return {"archive": artifact_entry(raw_path, root), "short": {"member": short_name, "sha256": PR43_ORIGINAL_SHORT_SHA256},
            "short_raw_bytes": byte_refs, "materialized_members": sorted(selected)}


def restore_history(root):
    """Retrieve every prior exact-head PR execution; missing history fails closed."""
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    event = read_json(os.environ["GITHUB_EVENT_PATH"])
    head = event["pull_request"]["head"]["sha"]
    branch = event["pull_request"]["head"]["ref"]
    pr_number = event["pull_request"]["number"]
    repository = os.environ["GITHUB_REPOSITORY"]
    current_run = int(os.environ["GITHUB_RUN_ID"])
    current_attempt = int(os.environ["GITHUB_RUN_ATTEMPT"])

    def api(endpoint):
        return json.loads(subprocess.check_output(["gh", "api", endpoint], text=True, encoding="utf-8"))

    def branch_runs(source_branch):
        values = []
        page = 1
        while True:
            result = api(f"repos/{repository}/actions/workflows/ci.yml/runs?event=pull_request&branch={quote(source_branch, safe="")}&per_page=100&page={page}")
            batch = result["workflow_runs"]
            values.extend(batch)
            if len(values) >= result["total_count"]:
                break
            if not batch:
                raise ValueError("workflow run pagination is incomplete")
            page += 1
        if len({row["id"] for row in values}) != len(values):
            raise ValueError("duplicate workflow run identity")
        return values

    runs = branch_runs(branch)
    current = [run for run in runs if run["id"] == current_run]
    if len(current) != 1 or current[0]["head_sha"] != head or current[0]["run_attempt"] != current_attempt:
        raise ValueError("current PR workflow run is absent or mismatched")
    if "<!-- pr43-gui-once" in (event["pull_request"].get("body") or ""):
        if (current[0].get("head_repository", {}).get("full_name") != repository
                or event["pull_request"]["head"].get("repo", {}).get("full_name") != repository
                or event["pull_request"]["base"].get("repo", {}).get("full_name") != repository):
            raise ValueError("PR43 supplement requires the exact head/base repository")
    candidate = snapshot()
    if candidate["parents"] != [event["pull_request"]["base"]["sha"], head]:
        raise ValueError("checkout is not the current PR integration commit")
    continuation = pr43_postmerge_handoff(event, candidate, api, repository)
    history_origins = {}
    if continuation is not None:
        reference, original_body = continuation
        original_runs = branch_runs(PR43_ORIGINAL_BRANCH)
        original_full = [row for row in original_runs if row["id"] == PR43_ORIGINAL_FULL_RUN]
        if (len(original_full) != 1 or original_full[0]["head_sha"] != reference["original_head"]
                or original_full[0]["run_attempt"] != 1 or original_full[0]["status"] != "completed"
                or original_full[0].get("conclusion") != "success"):
            raise ValueError("original PR43 fixed full CI history is missing or mismatched")
        push = api(f"repos/discoveryray/zhuyin-proofreader/actions/runs/{PR43_ORIGINAL_PUSH_RUN}")
        if (push.get("id") != PR43_ORIGINAL_PUSH_RUN or push.get("run_attempt") != 1
                or push.get("event") != "push" or push.get("head_sha") != reference["original_merge"]
                or push.get("head_branch") != "develop" or push.get("status") != "completed"
                or push.get("conclusion") != "success"
                or push.get("head_repository", {}).get("full_name") != repository):
            raise ValueError("original PR43 actual merge push history is missing or mismatched")
        for source_branch, source_pr, values in ((branch, pr_number, runs), (PR43_ORIGINAL_BRANCH, 43, original_runs)):
            for row in values:
                links = row.get("pull_requests")
                if (row.get("head_branch") != source_branch or row.get("event") != "pull_request"
                        or row.get("head_repository", {}).get("full_name") != repository
                        or not isinstance(links, list)
                        or (links and (len(links) != 1 or not isinstance(links[0], dict)
                            or type(links[0].get("number")) is not int or links[0]["number"] != source_pr))
                        or row["id"] in history_origins):
                    raise ValueError("PR43 continuation history run scope or duplicate differs")
                history_origins[row["id"]] = {"branch": source_branch, "pr": source_pr}
        if push["id"] in history_origins:
            raise ValueError("duplicate PR43 merge push history")
        history_origins[push["id"]] = {"branch": "develop", "pr": 43}
        runs = [*runs, *original_runs, push]
        handoff = import_local_handoff(original_body, root, task_id=reference["task_id"])
        ledger = read_local_ledger(root / "local-history", reference["task_id"])
        if ledger["baseline"] != reference["baseline"] or ledger["repository"] != repository:
            raise ValueError("original PR43 ledger baseline/repository differs")
    else:
        handoff = import_local_handoff(event["pull_request"].get("body"), root)
    # Only this exact PR/task caller may opt into the retained False record.
    history_policy = (POLICY if repository == REPOSITORY
                      and branch == "codex/cff-batch-fingerprint" and pr_number == 42
                      and handoff["task_id"] == CFF_HISTORY_TASK else None)
    retrieved = []
    for run in runs:
        if run.get("head_repository", {}).get("full_name") != repository:
            continue
        source_pr = history_origins[run["id"]]["pr"] if continuation is not None else pr_number
        if run.get("pull_requests") and not any(pr["number"] == source_pr for pr in run["pull_requests"]):
            continue
        if run["id"] == current_run and current_attempt == 1:
            continue
        if run["id"] != current_run and run["status"] != "completed":
            raise ValueError("another candidate run is unfinished; history not final")
        attempts = range(1, current_attempt) if run["id"] == current_run else range(1, run["run_attempt"] + 1)
        artifacts = []
        artifact_page = 1
        while True:
            page_data = api(f"repos/{repository}/actions/runs/{run['id']}/artifacts?per_page=100&page={artifact_page}")
            artifacts.extend(page_data["artifacts"])
            if len(artifacts) >= page_data["total_count"]:
                break
            artifact_page += 1
        for attempt in attempts:
            name = f"validation-evidence-{run['id']}-{attempt}"
            matches = [artifact for artifact in artifacts if artifact["name"] == name and not artifact["expired"]]
            if len(matches) != 1:
                raise ValueError("prior run/attempt history unavailable: " + name)
            destination = root / "history" / name
            if destination.exists():
                raise ValueError("history download would overwrite")
            if continuation is not None and run["id"] == PR43_ORIGINAL_PUSH_RUN:
                history_origins[run["id"]]["retained_short"] = restore_pr43_original_push(destination, matches[0], root)
            else:
                subprocess.run(["gh", "run", "download", str(run["id"]), "--repo", repository,
                                "--name", name, "--dir", str(destination)], check=True)
            if continuation is not None and run["id"] == PR43_ORIGINAL_FULL_RUN:
                original_coverage = destination / "coverage.json"
                if not original_coverage.is_file() or digest(original_coverage) != PR43_ORIGINAL_COVERAGE_SHA256:
                    raise ValueError("original PR43 fixed full coverage missing or corrupt")
                history_origins[run["id"]]["coverage"] = artifact_entry(original_coverage, root)
            # Prior bundles already contain earlier history. Keep one original
            # copy per execution, rejecting inconsistent duplicates.
            for manifest in destination.rglob("manifest.json"):
                value = read_verified_manifest(manifest, history_policy=history_policy,
                                               task_id=handoff["task_id"])
                known = {row["execution_id"]: row for row in retrieved}
                if value["execution_id"] in known and known[value["execution_id"]] != value:
                    raise ValueError("conflicting retained execution")
                retrieved.append(value)
    seen = {value["execution_id"]: path for path, value in history(
        root, policy=history_policy, task_id=handoff["task_id"])}

    context = {"head": head, "current_run": current_run, "current_attempt": current_attempt,
                "runs": [{"id": run["id"], "attempt": run["run_attempt"]} for run in runs],
                "executions": sorted(seen), "candidate": candidate, "raw_runs": runs, "task_id": handoff["task_id"], "local_handoff_sha256": handoff["archive_sha256"]}
    if continuation is not None:
        context["postmerge_continuation"] = {"reference": reference,
            "original_history_block_sha256": reference["original_history_block_sha256"],
            "origins": [{"run_id": key, **value} for key, value in sorted(history_origins.items())]}
    if POLICY != "validation-flow/no-real-tk/1":
        import_pr43_gui_once(event["pull_request"].get("body"), root, context, candidate, api, repository, pr_number)
    write_json(root / ("history-index-" + uuid.uuid4().hex + ".json"), context)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--group", choices=("core", "gui"), required=True)
    run.add_argument("--evidence-root", type=Path, required=True)
    run.add_argument("--mode", choices=("development", "validation"), required=True)
    run.add_argument("--confirmed-code-blocker", action="store_true")
    run.add_argument("--task-id")
    run.add_argument("--timeout", type=int)
    combine = sub.add_parser("aggregate")
    combine.add_argument("--evidence-root", type=Path, required=True)
    combine.add_argument("--head")
    combine.add_argument("--output", type=Path, required=True)
    restore = sub.add_parser("restore-history")
    restore.add_argument("--evidence-root", type=Path, required=True)
    bootstrap = sub.add_parser("bootstrap-local")
    bootstrap.add_argument("--task-id", required=True)
    bootstrap.add_argument("--source-ref", required=True)
    bootstrap.add_argument("--baseline", required=True)
    export = sub.add_parser("export-local-history")
    export.add_argument("--task-id", required=True)
    export.add_argument("--output", type=Path, required=True)
    pr43 = sub.add_parser("pr43-gui-block")
    pr43.add_argument("--head", required=True)
    pr43.add_argument("--authorization", type=Path, required=True)
    pr43.add_argument("--proposal", type=Path, required=True)
    pr43.add_argument("--human-reply", type=Path, required=True)
    pr43.add_argument("--output", type=Path, required=True)
    child = sub.add_parser("_pytest")
    child.add_argument("folder", type=Path)
    child.add_argument("group", choices=("core", "gui"))
    args = parser.parse_args(argv)
    try:
        if args.command == "pr43-gui-block":
            text = pr43_gui_block(args.head, args.authorization, args.proposal, args.human_reply)
            with args.output.open("x", encoding="utf-8") as output:
                output.write(text + "\n")
            return 0
        if args.command == "bootstrap-local":
            print(bootstrap_local(args.task_id, args.source_ref, args.baseline))
            return 0
        if args.command == "export-local-history":
            export_local_history(args.task_id, args.output)
            return 0
        if args.command == "_pytest":
            return pytest_child(args.folder, args.group)
        if args.command == "restore-history":
            restore_history(args.evidence_root)
            return 0
        if args.command == "aggregate":
            result = aggregate(args.evidence_root, args.head)
            write_json(args.output, result)
            print(json.dumps(result))
            return 0
        path = run_group(args.group, args.evidence_root, timeout=args.timeout, mode=args.mode,
                         code_blockers=args.confirmed_code_blocker, task_id=args.task_id)
        result = read_verified_manifest(path)
        print(json.dumps({"manifest": str(path), "outcome": result["outcome"]}))
        return 0 if result["outcome"] == "success" else 2
    except (ValueError, OSError, ET.ParseError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
