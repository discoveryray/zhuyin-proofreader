"""Read-only decisions for the repository's two-review development contract.

This validates coordinator-collected evidence, not its authenticity. It has no
network, credential, Git write, agent-launch, or merge capability.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile


SCHEMA = "zhuyin-pr-review-gate/3"
STAGED_SCHEMA = "zhuyin-pr-review-gate/4"
NO_WINDOW_POLICY = "validation-flow/no-real-tk/1"
REPOSITORY = "discoveryray/zhuyin-proofreader"
WORKFLOW = ".github/workflows/ci.yml"
PYTHONS = ("3.13",)
CORRECTION4_EXCEPTION = {
    "schema": "validation-flow-correction-exception/1",
    "task_id": "validation-flow-reduction",
    "baseline": "457707b4c4109c1b10a0da76d8f8a884aca10341",
    "starting_head": "a0e647952ae5d973ea30130264294eee4e6982fa",
    "head_branch": "chore/validation-flow-reduction",
    "pr_number": 41, "extra_rounds": 1, "limit": 4,
    "authorization_sha256": "29b2869d5e6584bab8efbf022f697c8ee7331b7e1241ef0c18df7f4a647ec7fc",
    "scope": "same-installation Tcl/Tk wiring, early hosted fd preflight, directly related tests/policy and task-bound gate exception",
}
CORRECTION5_EXCEPTION = {
    "schema": "validation-flow-correction-exception/2",
    "task_id": "validation-flow-reduction",
    "baseline": "457707b4c4109c1b10a0da76d8f8a884aca10341",
    "starting_head": "90b408794415509dd919a6d7a91c911f3724fd1f",
    "head_branch": "chore/validation-flow-reduction",
    "pr_number": 41, "extra_rounds": 1, "limit": 5,
    "authorization_sha256": "46b2855d3fe7cd873ce1c4aaf9a93a8afa320b4f67e17aa14405f74d06f296d0",
    "previous_authorization_sha256": "29b2869d5e6584bab8efbf022f697c8ee7331b7e1241ef0c18df7f4a647ec7fc",
    "scope": "independent early-preflight records, genuine small CLI regressions, directly related policy and task-bound fifth-round gate exception",
}
CORRECTION6_EXCEPTION = {
    "schema": "validation-flow-correction-exception/3",
    "task_id": "validation-flow-reduction",
    "baseline": "457707b4c4109c1b10a0da76d8f8a884aca10341",
    "starting_head": "811ff56f256370eb19cb5de0d76ce6a8c12d61be",
    "head_branch": "chore/validation-flow-reduction",
    "pr_number": 41, "extra_rounds": 1, "limit": 6,
    "authorization_sha256": "01df9c9193d00f675add80287feadc9f657b27b6c81fc9114d59448d3c9d9d4d",
    "previous_authorization_sha256": "46b2855d3fe7cd873ce1c4aaf9a93a8afa320b4f67e17aa14405f74d06f296d0",
    "fourth_authorization_sha256": "29b2869d5e6584bab8efbf022f697c8ee7331b7e1241ef0c18df7f4a647ec7fc",
    "scope": "ExpectedResolutionGuiTests owner-thread object retirement, directly necessary regressions/registration and task-bound sixth-round exception",
}
CORRECTION7_EXCEPTION = {
    "schema": "validation-flow-correction-exception/4",
    "task_id": "validation-flow-reduction",
    "baseline": "457707b4c4109c1b10a0da76d8f8a884aca10341",
    "starting_head": "adcee81cf844fcee51c16b94099fe8916458c4dc",
    "head_branch": "chore/validation-flow-reduction",
    "pr_number": 41, "extra_rounds": 1, "limit": 7,
    "authorization_sha256": "5f581ffa18d57db0114f23ed26f30fd7d09ba1d8b0cf5a97499b1392d341655a",
    "previous_authorization_sha256": "01df9c9193d00f675add80287feadc9f657b27b6c81fc9114d59448d3c9d9d4d",
    "fifth_authorization_sha256": "46b2855d3fe7cd873ce1c4aaf9a93a8afa320b4f67e17aa14405f74d06f296d0",
    "fourth_authorization_sha256": "29b2869d5e6584bab8efbf022f697c8ee7331b7e1241ef0c18df7f4a647ec7fc",
    "scope": "main push baseline full-validation routing and required summary, direct regressions/registration/policy and task-bound seventh-round exception",
}
CORRECTION8_EXCEPTION = {
    "schema": "validation-flow-correction-exception/5",
    "task_id": "validation-flow-reduction",
    "baseline": "457707b4c4109c1b10a0da76d8f8a884aca10341",
    "starting_head": "d974ec5c420feabaa64b449bd25d78aed10c02f0",
    "head_branch": "chore/validation-flow-reduction",
    "pr_number": 41, "extra_rounds": 1, "limit": 8,
    "authorization_sha256": "c156319030dc54f549e6479b369ef718fecdd494aee1817fee515e683032e05a",
    "previous_authorization_sha256": "5f581ffa18d57db0114f23ed26f30fd7d09ba1d8b0cf5a97499b1392d341655a",
    "sixth_authorization_sha256": "01df9c9193d00f675add80287feadc9f657b27b6c81fc9114d59448d3c9d9d4d",
    "fifth_authorization_sha256": "46b2855d3fe7cd873ce1c4aaf9a93a8afa320b4f67e17aa14405f74d06f296d0",
    "fourth_authorization_sha256": "29b2869d5e6584bab8efbf022f697c8ee7331b7e1241ef0c18df7f4a647ec7fc",
    "scope": "unittest step metadata ordering, unchanged historical workflow reader validation, exact source registration and task-bound eighth-round exception",
}
OPERATIONS = {"implement", "delegate", "test", "commit", "push", "pr", "merge"}
COMMON_STEPS = (
    "Check out repository", "Set up Python", "Show Python version",
    "Upgrade pip", "Install development dependencies",
    "Validate runtime asset integrity", "Audit test entrypoint coverage",
    "Run full pytest suite", "Verify GUI test execution", "Compile Python sources",
    "Check repository working tree",
)


class EvidenceError(ValueError):
    """Missing, unknown, malformed, or internally contradictory evidence."""


def _require(condition, message):
    if not condition:
        raise EvidenceError(message)


def _fields(value, names, where):
    _require(type(value) is dict, f"{where}: expected object")
    expected = set(names.split())
    _require(set(value) == expected, f"{where}: fields must be {sorted(expected)}")


def _text(value, where):
    _require(type(value) is str and bool(value.strip()), f"{where}: expected nonempty text")


def _sha(value, where):
    _require(type(value) is str and re.fullmatch(r"[0-9a-f]{40}", value) is not None
             and value != "0" * 40, f"{where}: expected full nonzero Git SHA")


def _integer(value, where, minimum=1):
    _require(type(value) is int and value >= minimum, f"{where}: invalid integer")


def _boolean(value, where):
    _require(type(value) is bool, f"{where}: expected boolean")


def _texts(value, where):
    _require(type(value) is list, f"{where}: expected list")
    for item in value:
        _text(item, where)
    _require(len(value) == len(set(value)), f"{where}: duplicate entries")


def _ci_schema(ci, where):
    if ci is None:
        return
    _fields(ci, "event branch workflow run_id attempt latest_run_id latest_attempt "
            "head_sha tested_sha parents tree status conclusion url jobs evidence_ref", where)
    for field in ("event", "branch", "workflow", "url", "evidence_ref"):
        _text(ci[field], f"{where}.{field}")
    for field in ("run_id", "attempt", "latest_run_id", "latest_attempt"):
        _integer(ci[field], f"{where}.{field}")
    for field in ("head_sha", "tested_sha", "tree"):
        _sha(ci[field], f"{where}.{field}")
    _require(type(ci["parents"]) is list, f"{where}.parents: expected list")
    for parent in ci["parents"]:
        _sha(parent, f"{where}.parents")
    _require(ci["status"] in ("queued", "in_progress", "completed"), f"{where}: invalid status")
    _require(ci["conclusion"] in (None, "success", "failure", "cancelled", "timed_out",
                                   "action_required", "neutral", "skipped", "stale"),
             f"{where}: invalid conclusion")
    _require(type(ci["jobs"]) is list, f"{where}.jobs: expected list")
    names = []
    for job in ci["jobs"]:
        _fields(job, "name runner python_version run_id attempt tested_sha conclusion steps evidence_ref", f"{where}.job")
        for field in ("name", "runner", "python_version", "evidence_ref"):
            _text(job[field], f"{where}.job.{field}")
        for field in ("run_id", "attempt"):
            _integer(job[field], f"{where}.job.{field}")
        _sha(job["tested_sha"], f"{where}.job.tested_sha")
        _require(job["conclusion"] is None or type(job["conclusion"]) is str,
                 f"{where}.job.conclusion: invalid value")
        _require(type(job["steps"]) is dict, f"{where}.job.steps: expected object")
        for step, conclusion in job["steps"].items():
            _text(step, f"{where}.job.step")
            _require(conclusion is None or type(conclusion) is str,
                     f"{where}.job.step: invalid conclusion")
        names.append(job["name"])
    _require(len(names) == len(set(names)), f"{where}: duplicate jobs")


def _correction5_limit(state):
    exception = state["correction_exception"]
    _fields(exception, " ".join((*CORRECTION5_EXCEPTION, "authorization_ref", "previous_authorization_ref")), "correction_exception")
    for name, expected in CORRECTION5_EXCEPTION.items():
        _require(type(exception[name]) is type(expected) and exception[name] == expected,
                 "invalid fifth-round task-bound exception: " + name)
    task, pr, corrections = state["task"], state["pr"], state["corrections"]
    _require((task["id"], task["baseline"], task["head_branch"]) ==
             (exception["task_id"], exception["baseline"], exception["head_branch"]),
             "fifth-round exception belongs to another task/baseline/branch")
    _require(type(pr) is dict and (pr.get("number"), pr.get("base_sha"), pr.get("head_branch")) ==
             (41, exception["baseline"], exception["head_branch"]),
             "fifth-round exception requires original PR41/base/branch")
    _require(pr.get("state") == "merged" or state["current"]["base"] == exception["baseline"],
             "fifth-round exception base changed")
    _require(type(corrections) is list and len(corrections) in (4, 5),
             "fifth-round exception must retain the first four rounds")
    _require(type(corrections[2]) is dict and corrections[2].get("to_head") == CORRECTION4_EXCEPTION["starting_head"]
             and type(corrections[3]) is dict
             and corrections[3].get("from_head") == CORRECTION4_EXCEPTION["starting_head"]
             and corrections[3].get("to_head") == exception["starting_head"],
             "fifth-round exception differs from retained third/fourth history")
    if len(corrections) == 5:
        _require(type(corrections[4]) is dict and corrections[4].get("from_head") == exception["starting_head"],
                 "fifth round must start at the authorized HEAD")
        _require(state["current"]["head"] == corrections[4].get("to_head"),
                 "current HEAD differs from retained fifth correction")
    else:
        _require(state["current"]["head"] == exception["starting_head"],
                 "fifth-round authorization must start at exact current HEAD")
    for ref, digest in (("authorization_ref", "authorization_sha256"),
                        ("previous_authorization_ref", "previous_authorization_sha256")):
        _text(exception[ref], "correction_exception." + ref)
        try:
            actual = hashlib.sha256(Path(exception[ref]).read_bytes()).hexdigest()
        except OSError as error:
            raise EvidenceError("saved fifth/fourth correction authorization unavailable") from error
        _require(actual == exception[digest], "saved fifth/fourth correction authorization hash differs")
    return 5


def _correction6_limit(state):
    exception = state["correction_exception"]
    _fields(exception, " ".join((*CORRECTION6_EXCEPTION, "authorization_ref",
                                "previous_authorization_ref", "fourth_authorization_ref")),
            "correction_exception")
    for name, expected in CORRECTION6_EXCEPTION.items():
        _require(type(exception[name]) is type(expected) and exception[name] == expected,
                 "invalid sixth-round task-bound exception: " + name)
    corrections = state["corrections"]
    _require(type(corrections) is list and len(corrections) in (5, 6),
             "sixth-round exception must retain the first five rounds")
    # Revalidate the original fourth/fifth contract and saved raw authorizations;
    # extending this one task must not rewrite either historical adapter.
    retained = {**state, "corrections": corrections[:5],
                "current": {**state["current"], "head": exception["starting_head"]},
                "correction_exception": {
                    **CORRECTION5_EXCEPTION,
                    "authorization_ref": exception["previous_authorization_ref"],
                    "previous_authorization_ref": exception["fourth_authorization_ref"],
                }}
    _correction5_limit(retained)
    _require(corrections[4].get("to_head") == exception["starting_head"],
             "sixth-round starting HEAD differs from retained fifth correction")
    if len(corrections) == 6:
        _require(type(corrections[5]) is dict and
                 corrections[5].get("from_head") == exception["starting_head"],
                 "sixth round must start at the authorized HEAD")
        _require(state["current"]["head"] == corrections[5].get("to_head"),
                 "current HEAD differs from retained sixth correction")
    else:
        _require(state["current"]["head"] == exception["starting_head"],
                 "sixth-round authorization must start at exact current HEAD")
    _text(exception["authorization_ref"], "correction_exception.authorization_ref")
    try:
        digest = hashlib.sha256(Path(exception["authorization_ref"]).read_bytes()).hexdigest()
    except OSError as error:
        raise EvidenceError("saved sixth correction authorization unavailable") from error
    _require(digest == exception["authorization_sha256"],
             "saved sixth correction authorization hash differs")
    return 6


def _correction7_limit(state):
    exception = state["correction_exception"]
    _fields(exception, " ".join((*CORRECTION7_EXCEPTION, "authorization_ref",
                                "previous_authorization_ref", "fifth_authorization_ref", "fourth_authorization_ref")),
            "correction_exception")
    for name, expected in CORRECTION7_EXCEPTION.items():
        _require(type(exception[name]) is type(expected) and exception[name] == expected,
                 "invalid seventh-round task-bound exception: " + name)
    corrections = state["corrections"]
    _require(type(corrections) is list and len(corrections) in (6, 7),
             "seventh-round exception must retain the first six rounds")
    retained = {**state, "corrections": corrections[:6],
                "current": {**state["current"], "head": exception["starting_head"]},
                "correction_exception": {
                    **CORRECTION6_EXCEPTION,
                    "authorization_ref": exception["previous_authorization_ref"],
                    "previous_authorization_ref": exception["fifth_authorization_ref"],
                    "fourth_authorization_ref": exception["fourth_authorization_ref"],
                }}
    _correction6_limit(retained)
    _require(corrections[5].get("to_head") == exception["starting_head"],
             "seventh-round starting HEAD differs from retained sixth correction")
    if len(corrections) == 7:
        _require(type(corrections[6]) is dict and
                 corrections[6].get("from_head") == exception["starting_head"],
                 "seventh round must start at the authorized HEAD")
        _require(state["current"]["head"] == corrections[6].get("to_head"),
                 "current HEAD differs from retained seventh correction")
    else:
        _require(state["current"]["head"] == exception["starting_head"],
                 "seventh-round authorization must start at exact current HEAD")
    _text(exception["authorization_ref"], "correction_exception.authorization_ref")
    try:
        digest = hashlib.sha256(Path(exception["authorization_ref"]).read_bytes()).hexdigest()
    except OSError as error:
        raise EvidenceError("saved seventh correction authorization unavailable") from error
    _require(digest == exception["authorization_sha256"],
             "saved seventh correction authorization hash differs")
    return 7


def _correction8_limit(state):
    exception = state["correction_exception"]
    _fields(exception, " ".join((*CORRECTION8_EXCEPTION, "authorization_ref", "previous_authorization_ref",
                                "sixth_authorization_ref", "fifth_authorization_ref", "fourth_authorization_ref")),
            "correction_exception")
    for name, expected in CORRECTION8_EXCEPTION.items():
        _require(type(exception[name]) is type(expected) and exception[name] == expected,
                 "invalid eighth-round task-bound exception: " + name)
    corrections = state["corrections"]
    _require(type(corrections) is list and len(corrections) in (7, 8),
             "eighth-round exception must retain the first seven rounds")
    retained = {**state, "corrections": corrections[:7],
                "current": {**state["current"], "head": exception["starting_head"]},
                "correction_exception": {
                    **CORRECTION7_EXCEPTION,
                    "authorization_ref": exception["previous_authorization_ref"],
                    "previous_authorization_ref": exception["sixth_authorization_ref"],
                    "fifth_authorization_ref": exception["fifth_authorization_ref"],
                    "fourth_authorization_ref": exception["fourth_authorization_ref"],
                }}
    _correction7_limit(retained)
    _require(corrections[6].get("to_head") == exception["starting_head"],
             "eighth-round starting HEAD differs from retained seventh correction")
    if len(corrections) == 8:
        _require(type(corrections[7]) is dict and
                 corrections[7].get("from_head") == exception["starting_head"],
                 "eighth round must start at the authorized HEAD")
        _require(state["current"]["head"] == corrections[7].get("to_head"),
                 "current HEAD differs from retained eighth correction")
    else:
        _require(state["current"]["head"] == exception["starting_head"],
                 "eighth-round authorization must start at exact current HEAD")
    _text(exception["authorization_ref"], "correction_exception.authorization_ref")
    try:
        digest = hashlib.sha256(Path(exception["authorization_ref"]).read_bytes()).hexdigest()
    except OSError as error:
        raise EvidenceError("saved eighth correction authorization unavailable") from error
    _require(digest == exception["authorization_sha256"],
             "saved eighth correction authorization hash differs")
    return 8


PR43_CORRECTION_SCHEMA = "pr43-correction-exception/1"
PR43_TASK = "actual-gpt-bbox-export"
PR43_BASELINE = "8eab9a1de34b59115658a2c7d3565adb347e59ab"
PR43_BRANCH = "codex/actual-gpt-bbox-export"
PR43_PREFIX = (
    ("569385728c9c6fa4c6172c0f38220b176941112b", "a95172c194642e059025171128289ca5ab4ae2ef"),
    ("f4b8962214f5a46c12cd18aa35d15b4e78c43a81", "d59358cd45cc65a73b1d4dcb0aa228ffe7233435"),
    ("ea944524a9001ee452e36fefd32df97d0b850456", "7b5e3a599d87e9ddd77719921fda190a2fe66499"),
    ("7b5e3a599d87e9ddd77719921fda190a2fe66499", "6ce3cd5c9c3b2b02f4ab690c717ce1c122871c82"),
)
# Original bytes, including the canonical noncontiguous intervals and reports.
PR43_SOURCES = {
    "canonical_ledger": "f2ce7c1fbdb12a1f05745b0b355be029c6b1f0bf02943f87914cef81cbe05376",
    "fourth_authorization": "0cda45fc1ab970b320d2cd763d598f782821a97a55537c641f3857fd5cb8bda8",
    "fourth_followup": "c96af1170207fbb6b1e4c2114722235471869f59aaa2609930f719d7b32a5e7c",
    "fourth_receipt": "e301da8f04951bf7a9018f0578d4ce01c4f341db3f64bd9839fee34bdd266290",
    "original_gate": "845360bea58979cbe69d8e01070bb670042f5d7feff25691f9fea24e34df85f2",
    "projection_proposal": "7a39493ce0031fa3edf487f799b317cb857650c592870bb7c34761e6087d9374",
    "projection_adoption": "b6c9adccf24ceb50f3a79bf9faa91f750bf552b1304b7c6bdf29e62968fc6d1c",
    "closed_gate_proposal": "1f878c8e9026e29cdb4c195c2f932a9e96781846024c1366750c27d414fba0be",
    "closed_gate_adoption": "5519099dd0d20aefe752bf7fec2cfa85998e0c70850617a8b7949a5a79219f10",
}
PR43_FIFTH_AUTHORIZATION = "7bf99a4fa2ef4446fb8c0490af60ab0ca8014fe35ddf1025329d3ca6fdc5624b"


def _pr43_saved_bytes(ref):
    _text(ref, "PR43 original evidence reference")
    try:
        return Path(ref).read_bytes()
    except OSError as error:
        raise EvidenceError("PR43 original evidence unavailable") from error


def _pr43_correction_limit(state):
    """Closed historical projection; never edits the canonical ledger or reviews."""
    ex = state["correction_exception"]
    _fields(ex, "schema limit sources integration_commits corrective_head", "PR43 correction exception")
    _require(type(ex["limit"]) is int and ex["limit"] in (4, 5), "PR43 permits no sixth round")
    task, pr, corrections = state["task"], state["pr"], state["corrections"]
    _require(task == {"id": PR43_TASK, "repository": REPOSITORY, "baseline": PR43_BASELINE,
                      "base_branch": "develop", "head_branch": PR43_BRANCH}, "PR43 identity differs")
    _require(type(pr) is dict and type(pr.get("number")) is int and pr["number"] == 43
             and pr.get("head_branch") == PR43_BRANCH and pr.get("base_branch") == "develop",
             "PR43 original PR/branch required")
    pins = dict(PR43_SOURCES)
    if ex["limit"] == 5:
        pins["fifth_authorization"] = PR43_FIFTH_AUTHORIZATION
    _fields(ex["sources"], " ".join(pins), "PR43 pinned sources")
    saved = {}
    for key, digest in pins.items():
        raw = _pr43_saved_bytes(ex["sources"][key])
        _require(hashlib.sha256(raw).hexdigest() == digest, "PR43 source hash differs: " + key)
        saved[key] = raw
    try:
        canonical = json.loads(saved["canonical_ledger"])["corrections"]
    except (ValueError, KeyError, TypeError) as error:
        raise EvidenceError("PR43 canonical ledger malformed") from error
    _require(type(corrections) is list and len(corrections) in (4, ex["limit"]),
             "PR43 must retain four canonical corrections")
    # Operational endpoints are cumulative checkpoints, NOT actual commit parents.
    for index, (start, end) in enumerate(PR43_PREFIX):
        original = canonical[index]
        _require((original["from_head"], original["to_head"]) == (start, end),
                 "PR43 canonical prefix differs")
        projected_start = PR43_PREFIX[index - 1][1] if index else start
        expected_ref = (ex["sources"]["projection_proposal"] if index == 1 else
                        ex["sources"]["closed_gate_proposal"] if index == 2 else original["evidence_ref"])
        _require(corrections[index] == {"number": index + 1, "from_head": projected_start,
                                       "to_head": end, "evidence_ref": expected_ref},
                 "PR43 operational prefix differs")
    records = ex["integration_commits"]
    _require(type(records) is list, "PR43 integration commits must be a list")
    if len(corrections) == 4:
        _require(ex["corrective_head"] is None and not records
                 and state["current"]["head"] == PR43_PREFIX[-1][1]
                 and pr.get("head_sha") == PR43_PREFIX[-1][1]
                 and pr.get("base_sha") == PR43_BASELINE
                 and state["current"]["base"] == PR43_BASELINE,
                 "PR43 retained fourth state must keep exact original scope")
        return ex["limit"]
    fifth = corrections[4]
    _require(type(fifth) is dict and fifth.get("number") == 5
             and fifth.get("from_head") == PR43_PREFIX[-1][1]
             and fifth.get("to_head") == state["current"]["head"] == pr.get("head_sha"),
             "PR43 fifth interval must bind the fixed candidate")
    _require(bool(records), "PR43 fifth candidate requires actual ordinary integration")
    _sha(ex["corrective_head"], "PR43 actual fifth corrective HEAD")
    previous, integration_base = PR43_PREFIX[-1][1], None
    correction_seen = False
    for record in records:
        _fields(record, "sha object_ref", "PR43 integration commit")
        _sha(record["sha"], "PR43 integration SHA")
        raw = _pr43_saved_bytes(record["object_ref"])
        digest = hashlib.sha1(b"commit " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()
        _require(digest == record["sha"], "PR43 commit object hash differs")
        headers = raw.split(b"\n\n", 1)[0].split(b"\n")
        try:
            parents = [line[7:].decode("ascii") for line in headers if line.startswith(b"parent ")]
        except UnicodeError as error:
            raise EvidenceError("PR43 malformed commit parents") from error
        for parent in parents:
            _sha(parent, "PR43 actual parent")
        _require(len(parents) in (1, 2) and parents[0] == previous
                 and len(set(parents)) == len(parents), "PR43 ordinary ordered parents differ")
        if len(parents) == 1:
            _require(record["sha"] == ex["corrective_head"] and not correction_seen
                     and integration_base is not None,
                     "PR43 only the fifth corrective commit may be single-parent after integration")
        else:
            integration_base = parents[1]
        if record["sha"] == ex["corrective_head"]:
            _require(not correction_seen, "PR43 duplicate corrective commit")
            correction_seen = True
        previous = record["sha"]
    _require(correction_seen, "PR43 actual fifth corrective commit is missing")
    _require(previous == fifth["to_head"] and previous != PR43_PREFIX[-1][1]
             and integration_base == pr.get("base_sha"), "PR43 candidate/integration base differs")
    _require(pr.get("state") == "merged" or state["current"]["base"] == integration_base,
             "PR43 current base differs from actual integration")
    _require(fifth.get("evidence_ref") == records[-1]["object_ref"],
             "PR43 fifth evidence must reference actual final commit bytes")
    return 5


def _correction_limit(state):
    if "correction_exception" not in state:
        return 3
    exception = state["correction_exception"]
    if type(exception) is dict and exception.get("schema") == PR43_CORRECTION_SCHEMA:
        return _pr43_correction_limit(state)
    if type(exception) is dict and exception.get("schema") == CORRECTION8_EXCEPTION["schema"]:
        return _correction8_limit(state)
    if type(exception) is dict and exception.get("schema") == CORRECTION7_EXCEPTION["schema"]:
        return _correction7_limit(state)
    if type(exception) is dict and exception.get("schema") == CORRECTION6_EXCEPTION["schema"]:
        return _correction6_limit(state)
    if type(exception) is dict and exception.get("schema") == CORRECTION5_EXCEPTION["schema"]:
        return _correction5_limit(state)
    _fields(exception, " ".join((*CORRECTION4_EXCEPTION, "authorization_ref")), "correction_exception")
    for name, expected in CORRECTION4_EXCEPTION.items():
        _require(type(exception[name]) is type(expected) and exception[name] == expected,
                 "invalid task-bound correction exception: " + name)
    task = state["task"]
    _require((task["id"], task["baseline"], task["head_branch"]) ==
             (exception["task_id"], exception["baseline"], exception["head_branch"]),
             "correction exception belongs to another task/baseline/branch")
    pr = state["pr"]
    _require(type(pr) is dict and (pr.get("number"), pr.get("base_sha"), pr.get("head_branch")) ==
             (41, exception["baseline"], exception["head_branch"]),
             "correction exception requires the original PR41/base/branch")
    _require(pr.get("state") == "merged" or state["current"]["base"] == exception["baseline"],
             "correction exception base changed")
    corrections = state["corrections"]
    _require(type(corrections) is list and len(corrections) in (3, 4),
             "correction exception must retain the first three rounds")
    _require(type(corrections[2]) is dict and corrections[2].get("to_head") == exception["starting_head"],
             "correction exception starting HEAD differs from retained round 3")
    if len(corrections) == 4:
        _require(type(corrections[3]) is dict and corrections[3].get("from_head") == exception["starting_head"],
                 "fourth round must start at the authorized HEAD")
        _require(state["current"]["head"] == corrections[3].get("to_head"),
                 "current HEAD differs from the retained fourth correction")
    else:
        _require(state["current"]["head"] == exception["starting_head"],
                 "fourth-round authorization must start at the exact current HEAD")
    _text(exception["authorization_ref"], "correction_exception.authorization_ref")
    try:
        digest = hashlib.sha256(Path(exception["authorization_ref"]).read_bytes()).hexdigest()
    except OSError as error:
        raise EvidenceError("saved correction authorization unavailable") from error
    _require(digest == exception["authorization_sha256"], "saved correction authorization hash differs")
    return 4


def _correction_exhausted(state):
    return {3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight"}[_correction_limit(state)] + " corrective rounds exhausted"


def validate_state(state):
    """Validate the closed input schema. This does not grant permission."""
    staged = type(state) is dict and state.get("schema") == STAGED_SCHEMA
    fields = "schema task authorization current implementers corrections reviews unavailable_review_rounds pr pr_ci merge push_ci handoffs"
    _fields(state, fields + (" execution coverage short_validation" if staged else "")
            + (" correction_exception" if type(state) is dict and "correction_exception" in state else "")
            + (" validation_policy" if type(state) is dict and "validation_policy" in state else ""), "state")
    if "validation_policy" in state:
        _require(state["validation_policy"] == NO_WINDOW_POLICY, "unknown validation policy")
    _require(state["schema"] in (SCHEMA, STAGED_SCHEMA), "unsupported schema")
    if staged:
        _fields(state["execution"], "mode validation_authorization_ref", "execution")
        _require(state["execution"]["mode"] in ("development", "validation"), "unknown execution mode")
        if state["execution"]["mode"] == "validation":
            _text(state["execution"]["validation_authorization_ref"], "explicit start-validation authorization")
        else:
            _require(state["execution"]["validation_authorization_ref"] is None, "development cannot claim validation authorization")
        for field in ("coverage", "short_validation"):
            if state[field] is not None:
                _text(state[field], field)
    task = state["task"]
    _fields(task, "id repository baseline base_branch head_branch", "task")
    for field in ("id", "repository", "base_branch", "head_branch"):
        _text(task[field], f"task.{field}")
    _sha(task["baseline"], "task.baseline")
    _require(task["repository"] == REPOSITORY and task["base_branch"] == "develop",
             "only this repository's develop task workflow is supported")
    _require(task["head_branch"] not in ("main", "develop") and
             re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", task["head_branch"]) is not None,
             "invalid feature branch")
    auth = state["authorization"]
    _fields(auth, "task_id active operations source_ref", "authorization")
    _text(auth["source_ref"], "authorization.source_ref")
    _boolean(auth["active"], "authorization.active")
    _texts(auth["operations"], "authorization.operations")
    _require(set(auth["operations"]) <= OPERATIONS, "unknown authorized operation")
    _require(auth["task_id"] == task["id"], "authorization belongs to another task")
    current = state["current"]
    _fields(current, "base head working_tree_clean evidence_ref", "current")
    for field in ("base", "head"):
        _sha(current[field], f"current.{field}")
    _boolean(current["working_tree_clean"], "current.working_tree_clean")
    _text(current["evidence_ref"], "current.evidence_ref")
    _texts(state["implementers"], "implementers")
    _require(bool(state["implementers"]), "all code-writing actors must be listed")
    _require(type(state["unavailable_review_rounds"]) is list and
             all(type(n) is int and n in (1, 2) for n in state["unavailable_review_rounds"]),
             "invalid unavailable review rounds")
    _require(type(state["corrections"]) is list and len(state["corrections"]) <= _correction_limit(state),
             "correction rounds exceed the applicable task limit")
    previous = None
    for number, correction in enumerate(state["corrections"], 1):
        _fields(correction, "number from_head to_head evidence_ref", "correction")
        _require(type(correction["number"]) is int and correction["number"] == number,
                 "correction rounds must be contiguous")
        for field in ("from_head", "to_head"):
            _sha(correction[field], f"correction.{field}")
        _text(correction["evidence_ref"], "correction.evidence_ref")
        _require(correction["from_head"] != correction["to_head"], "correction needs a new commit")
        if previous is not None:
            _require(previous == correction["from_head"], "correction history is discontinuous")
        previous = correction["to_head"]
    _require(type(state["reviews"]) is list, "reviews: expected list")
    for review in state["reviews"]:
        _fields(review, "round reviewer baseline base head scope verdict independent "
                "full_diff_reviewed findings report_ref ci blocker_kind "
                "supersedes_report_ref resolution_evidence_ref review_mode" + (" coverage_sha256 finalizes_report_ref" if staged else ""), "review")
        _require(review["review_mode"] in ("full_diff", "evidence_gap"), "unknown review mode")
        _require(review["full_diff_reviewed"] == (review["review_mode"] == "full_diff"),
                 "review mode must accurately describe the work performed")
        _require(type(review["round"]) is int and review["round"] in (1, 2), "invalid review round")
        for field in ("reviewer", "scope", "report_ref"):
            _text(review[field], f"review.{field}")
        for field in ("baseline", "base", "head"):
            _sha(review[field], f"review.{field}")
        for field in ("independent", "full_diff_reviewed"):
            _boolean(review[field], f"review.{field}")
        _texts(review["findings"], "review.findings")
        _require(review["verdict"] in (("PASS", "BLOCKED", "CODE_REVIEWED") if staged else ("PASS", "BLOCKED")), "unknown review verdict")
        if staged:
            digest = review["coverage_sha256"]
            _require(digest is None or (type(digest) is str and re.fullmatch(r"[0-9a-f]{64}", digest)), "invalid review coverage digest")
            _require((review["verdict"] == "PASS") == (digest is not None), "only formal PASS binds verified functional coverage")
        if review["verdict"] in ("PASS", "CODE_REVIEWED"):
            _require(review["blocker_kind"] is None and not review["findings"],
                     "PASS cannot contain a blocker")
        else:
            _require(review["blocker_kind"] in ("code", "evidence", "capability", "contract")
                     and bool(review["findings"]), "BLOCKED needs an explicit kind and findings")
        for field in (("supersedes_report_ref", "resolution_evidence_ref", "finalizes_report_ref") if staged else ("supersedes_report_ref", "resolution_evidence_ref")):
            if review[field] is not None:
                _text(review[field], f"review.{field}")
        if review["round"] == 1:
            _require(review["ci"] is None, "round 1 does not attest PR CI")
        elif review["ci"] is not None:
            _fields(review["ci"], "run_id attempt tested_sha", "review.ci")
            _integer(review["ci"]["run_id"], "review.ci.run_id")
            _integer(review["ci"]["attempt"], "review.ci.attempt")
            _sha(review["ci"]["tested_sha"], "review.ci.tested_sha")
        else:
            _require((staged and review["verdict"] == "CODE_REVIEWED") or (review["verdict"] == "BLOCKED" and review["blocker_kind"] != "code"),
                     "round 2 without CI metadata must remain a non-code BLOCKED")
    _validate_review_history(state)
    pr = state["pr"]
    if pr is not None:
        _fields(pr, "number url state base_branch head_branch base_sha head_sha mergeable "
                "protection_satisfied findings_checked new_blockers evidence_ref", "pr")
        _integer(pr["number"], "pr.number")
        for field in ("url", "base_branch", "head_branch", "evidence_ref"):
            _text(pr[field], f"pr.{field}")
        for field in ("base_sha", "head_sha"):
            _sha(pr[field], f"pr.{field}")
        _require(pr["state"] in ("open", "closed", "merged"), "invalid PR state")
        for field in ("mergeable", "protection_satisfied", "findings_checked"):
            _boolean(pr[field], f"pr.{field}")
        _texts(pr["new_blockers"], "pr.new_blockers")
    _ci_schema(state["pr_ci"], "pr_ci")
    _ci_schema(state["push_ci"], "push_ci")
    merge = state["merge"]
    if merge is not None:
        _fields(merge, "sha parents tree develop_head develop_contains_merge evidence_ref", "merge")
        for field in ("sha", "tree", "develop_head"):
            _sha(merge[field], f"merge.{field}")
        _require(type(merge["parents"]) is list, "merge.parents: expected list")
        for parent in merge["parents"]:
            _sha(parent, "merge.parent")
        _boolean(merge["develop_contains_merge"], "merge.develop_contains_merge")
        _text(merge["evidence_ref"], "merge.evidence_ref")
    _require(type(state["handoffs"]) is list, "handoffs: expected list")
    for handoff in state["handoffs"]:
        _fields(handoff, "from to head evidence_ref", "handoff")
        for field in ("from", "to", "evidence_ref"):
            _text(handoff[field], f"handoff.{field}")
        _sha(handoff["head"], "handoff.head")


def _decision(state, action, reason, **details):
    needed = {
        "REQUEST_REVIEW_1": {"delegate"}, "REQUEST_REVIEW_2": {"delegate"},
        "ENSURE_PR": {"pr"}, "ENSURE_DRAFT_PR": {"pr"}, "MERGE_PROPOSAL": {"merge"},
        "CORRECT_IMPLEMENTATION": {"implement", "test", "commit", "push", "delegate"},
    }.get(action, set())
    if (action == "CORRECT_IMPLEMENTATION" and state["schema"] == STAGED_SCHEMA
            and state["execution"]["mode"] == "development"):
        needed = needed - {"push"}
        details["delivery"] = "local_commit_only"
    missing = needed - set(state["authorization"]["operations"])
    if missing:
        action, reason, details = "STOP", f"next action is not authorized: {sorted(missing)}", {}
    identity = {"task": state["task"], "base": state["current"]["base"],
                "head": state["current"]["head"], "action": action}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest()
    return {"action": action, "reason": reason, "idempotency_key": key, **details}


def _correct(state, reason):
    count = len(state["corrections"])
    if count >= _correction_limit(state):
        return _decision(state, "STOP", _correction_exhausted(state) + ": " + reason)
    return _decision(state, "CORRECT_IMPLEMENTATION", reason, correction_round=count + 1)


def _post_merge_blocked(state, reason, *, code=True):
    if code and len(state["corrections"]) >= _correction_limit(state):
        reason += "; " + _correction_exhausted(state)
    return _decision(
        state, "STOP", "post-merge blocker: " + reason,
        handoff="retain this task ledger and correction limit; do not reset the task; "
                "do not re-merge, push develop, or revert",
        task_id=state["task"]["id"], baseline=state["task"]["baseline"],
        merge_sha=state["merge"]["sha"] if state["merge"] is not None else None,
        corrections_used=len(state["corrections"]), correction_limit=_correction_limit(state),
    )


def _review_scope(review):
    return tuple(review[field] for field in ("round", "baseline", "base", "head", "scope"))


def _validate_review_history(state):
    """Resolve only explicit, append-only supplements backed by retained evidence."""
    reports, superseded = {}, set()
    for review in state["reviews"]:
        ref, prior = review["report_ref"], review["supersedes_report_ref"]
        finalizes = review.get("finalizes_report_ref")
        _require(not (prior and finalizes), "finalization and supersession are distinct relations")
        prior = prior or finalizes
        _require(ref not in reports, "report_ref must uniquely identify an original report")
        if prior is None:
            _require(review["resolution_evidence_ref"] is None, "resolution needs a supersession target")
            _require(review["review_mode"] == "full_diff", "gap review needs a retained full-review chain")
        else:
            _require(prior in reports and prior not in superseded,
                     "supersession target must be earlier, retained, and not already superseded")
            old = reports[prior]
            if finalizes:
                _require(old["verdict"] == "CODE_REVIEWED", "finalization must target a code-review intermediate report")
            else:
                _require(old["verdict"] == "BLOCKED" and old["blocker_kind"] != "code",
                         "only non-code BLOCKED reports may be supplemented at unchanged HEAD")
            _require(_review_scope(review) == _review_scope(old), "supersession must retain the exact code scope")
            _text(review["resolution_evidence_ref"], "supersession resolution evidence")
            if review["review_mode"] == "evidence_gap":
                _require(review["reviewer"] == old["reviewer"],
                         "only the original reviewer may perform a gap-only supplement")
            for candidate in (old, review):
                problem = _review_problem(state, candidate, candidate["round"])
                _require(problem is None, "invalid supersession review: " + str(problem))
            superseded.add(prior)
        reports[ref] = review
    seen = set()
    for ref, review in reports.items():
        if ref not in superseded:
            key = (*_review_scope(review), json.dumps(review["ci"], sort_keys=True))
            if state["schema"] == STAGED_SCHEMA:
                key += (review["coverage_sha256"],)
            _require(key not in seen, "ambiguous unlinked review for the same scope")
            seen.add(key)


def _review(state, number, base, head):
    superseded = {r["supersedes_report_ref"] or r.get("finalizes_report_ref") for r in state["reviews"]}
    matches = [r for r in state["reviews"] if r["report_ref"] not in superseded and r["round"] == number and
               (r["baseline"], r["base"], r["head"]) == (state["task"]["baseline"], base, head)]
    # Unresolved blockers survive CI reruns and unrelated PASS records.
    blockers = [r for r in matches if r["verdict"] == "BLOCKED"]
    if blockers:
        return min(blockers, key=lambda r: r["blocker_kind"] != "code")
    if state["schema"] == STAGED_SCHEMA and state["coverage"] is not None:
        try:
            digest = hashlib.sha256(Path(state["coverage"]).read_bytes()).hexdigest()
        except OSError:
            digest = None
        applicable = [r for r in matches if r["coverage_sha256"] == digest]
        if applicable:
            matches = applicable
    if number == 2 and state["pr_ci"] is not None and state["schema"] == SCHEMA:
        ci = state["pr_ci"]
        expected = {field: ci[field] for field in ("run_id", "attempt", "tested_sha")}
        matches = [r for r in matches if r["ci"] == expected]
    return matches[0] if len(matches) == 1 else None


def _review_problem(state, review, number):
    if not review["independent"] or review["reviewer"] in state["implementers"]:
        return "reviewer must be independent of every implementation actor"
    if not review["full_diff_reviewed"] and review["review_mode"] != "evidence_gap":
        return "full applicable diff has not been reviewed"
    scope = "baseline_to_head" if number == 1 else "cumulative_and_full_pr_with_ci"
    if review["scope"] != scope:
        return "review scope is incomplete"
    if number == 2 and any(
            other["round"] == 1 and (other["baseline"], other["base"], other["head"]) ==
            (review["baseline"], review["base"], review["head"]) and other["reviewer"] == review["reviewer"]
            for other in state["reviews"]):
        return "round two reviewer participated in round one for this code scope"
    if review["verdict"] == "PASS" and review["findings"]:
        return "PASS contains unresolved blocking findings"
    return None


def _ci_problem(ci, event, branch, head, parents, *, no_window=False):
    if ci["workflow"] != WORKFLOW or ci["event"] != event or ci["branch"] != branch:
        return "REFRESH_EVIDENCE", "wrong CI workflow, event, or branch"
    if ci["head_sha"] != head or ci["parents"] != parents:
        return "REFRESH_EVIDENCE", "CI SHA or tested commit parents differ from the reviewed scope"
    if event == "push" and ci["tested_sha"] != head:
        return "REFRESH_EVIDENCE", "post-merge checkout must be the actual merge SHA"
    if ci["tested_sha"] in parents:
        return "REFRESH_EVIDENCE", "tested merge commit cannot be one of its own parents"
    if (ci["run_id"], ci["attempt"]) != (ci["latest_run_id"], ci["latest_attempt"]):
        return "REFRESH_EVIDENCE", "CI is not the latest applicable run attempt"
    if ci["status"] != "completed" or ci["conclusion"] != "success":
        return "INVESTIGATE_CI", "CI is not completed successfully"
    jobs = {job["name"]: job for job in ci["jobs"]}
    for version in PYTHONS:
        name = f"Python {version}"
        if name not in jobs:
            return "REFRESH_EVIDENCE", f"missing required job: {name}"
        job = jobs[name]
        if not job["runner"].startswith("windows-"):
            return "REFRESH_EVIDENCE", f"required Windows runner is missing: {name}"
        if job["conclusion"] != "success":
            return "INVESTIGATE_CI", f"required Windows job did not succeed: {name}"
        if job["python_version"] != "3.13.0":
            return "REFRESH_EVIDENCE", f"wrong actual Python version: {name}"
        if (job["run_id"], job["attempt"], job["tested_sha"]) != (
                ci["run_id"], ci["attempt"], ci["tested_sha"]):
            return "REFRESH_EVIDENCE", f"job is from another run attempt or checkout: {name}"
        required = (*(step for step in COMMON_STEPS if not no_window or step != "Verify GUI test execution"),
                    *(('Verify active test execution',) if no_window else ()),
                    f"Check committed whitespace ({'pull request' if event == 'pull_request' else 'push'})")
        if any(step not in job["steps"] for step in required):
            return "REFRESH_EVIDENCE", f"required step evidence is missing: {name}"
        if any(job["steps"].get(step) != "success" for step in required):
            return "INVESTIGATE_CI", f"required step skipped or unsuccessful: {name}"
    if any(job["conclusion"] != "success" for job in jobs.values()):
        return "INVESTIGATE_CI", "another workflow job has not succeeded"
    return None


def next_action(state):
    """Return an advisory action; never perform or acknowledge external writes."""
    try:
        validate_state(state)
    except (EvidenceError, TypeError, KeyError) as exc:
        return {"action": "STOP", "reason": f"invalid evidence: {exc}"}
    if state["schema"] == STAGED_SCHEMA:
        return _next_action_staged(state)
    if not state["authorization"]["active"]:
        return _decision(state, "STOP", "task authorization is absent or revoked")
    if not state["current"]["working_tree_clean"]:
        return _decision(state, "STOP", "working tree is not clean")
    pr, merge = state["pr"], state["merge"]
    merged = pr is not None and pr["state"] == "merged"
    if merge is not None and not merged:
        return _decision(state, "STOP", "merge evidence contradicts PR state")
    base, head = (pr["base_sha"], pr["head_sha"]) if merged else (
        state["current"]["base"], state["current"]["head"])
    if pr is not None:
        if (pr["base_branch"], pr["head_branch"]) != (
                state["task"]["base_branch"], state["task"]["head_branch"]):
            return _decision(state, "STOP", "existing PR belongs to a different branch pair")
        if pr["state"] == "closed":
            return _decision(state, "STOP", "existing PR is closed without merge; do not duplicate it")
        if not merged and (pr["base_sha"], pr["head_sha"]) != (base, head):
            return _decision(state, "REFRESH_EVIDENCE", "remote PR base/head changed; invalidate stale reviews")
        if not pr["findings_checked"]:
            return _decision(state, "REFRESH_EVIDENCE", "check the latest findings for this PR scope")
        if pr["new_blockers"]:
            return _post_merge_blocked(state, "unresolved PR findings") if merged else _correct(
                state, "confirmed PR blockers require correction and both reviews")
    for number in (1, 2):
        # A valid confirmed blocker remains actionable while CI is failed/pending.
        # Successful CI is required for advancement, not for reaching correction.
        review = _review(state, number, base, head)
        if review is not None:
            problem = _review_problem(state, review, number)
            if problem:
                return _decision(state, "STOP", problem)
            if number == 2:
                first = _review(state, 1, base, head)
                if review["reviewer"] == first["reviewer"] or review["report_ref"] == first["report_ref"]:
                    return _decision(state, "STOP", "two separately produced independent reviews are required")
            if review["verdict"] == "BLOCKED":
                kind = review["blocker_kind"]
                reason = f"round {number} is BLOCKED ({kind})"
                if kind == "code":
                    return _post_merge_blocked(state, reason) if merged else _correct(
                        state, reason + "; re-review both full scopes after correction")
                if kind == "evidence":
                    return _decision(state, "REFRESH_EVIDENCE", reason + "; retain report and obtain a supplement",
                                     blocked_report_ref=review["report_ref"])
                return _post_merge_blocked(state, reason, code=False) if merged else _decision(
                    state, "STOP", reason + "; resolve the non-code limitation and obtain a supplement")
        if number == 2:
            if pr is None:
                return _decision(state, "ENSURE_PR", "round one passed; look up the branch pair before creation",
                                 base_branch="develop", head_branch=state["task"]["head_branch"])
            ci = state["pr_ci"]
            if ci is None or ci["status"] in ("queued", "in_progress"):
                return _decision(state, "WAIT_PR_CI", "matching completed PR CI evidence is required")
            problem = _ci_problem(ci, "pull_request", state["task"]["head_branch"], head, [base, head], no_window=state.get("validation_policy") == NO_WINDOW_POLICY)
            if problem:
                return _decision(state, "STOP", problem[1]) if merged else _decision(state, *problem)
        if review is None:
            if merged or number in state["unavailable_review_rounds"]:
                return _decision(state, "STOP", f"round {number} review evidence is unavailable")
            return _decision(state, f"REQUEST_REVIEW_{number}", "no review for the exact current scope")
    if merged:
        if merge is None:
            return _decision(state, "VERIFY_MERGE", "obtain the actual merge commit; never merge again")
        if merge["parents"] != [base, head] or merge["tree"] != state["pr_ci"]["tree"]:
            return _decision(state, "STOP", "actual merge parents/tree differ from reviewed integration")
        if merge["develop_head"] != state["current"]["base"] or not merge["develop_contains_merge"]:
            return _decision(state, "STOP", "fetched develop does not prove the actual merge is retained")
        ci = state["push_ci"]
        if ci is None or ci["status"] in ("queued", "in_progress"):
            return _decision(state, "WAIT_PUSH_CI", "actual merge SHA requires its own develop push CI")
        problem = _ci_problem(ci, "push", "develop", merge["sha"], [base, head], no_window=state.get("validation_policy") == NO_WINDOW_POLICY)
        if problem or ci["tree"] != merge["tree"]:
            return _decision(state, "STOP", problem[1] if problem else "push CI tree does not match the actual merge")
        return _decision(state, "COMPLETE", "both reviews, PR CI, actual merge and post-merge CI verified",
                         merge_sha=merge["sha"], develop_head=merge["develop_head"])
    if not pr["mergeable"] or not pr["protection_satisfied"]:
        return _decision(state, "STOP", "mergeability or repository protection requirements are not satisfied")
    return _decision(state, "MERGE_PROPOSAL", "recheck remote base/head and protection immediately before write",
                     pr_number=pr["number"], merge_method="merge", expected_head_sha=head,
                     expected_base_sha=base)


def _staged_ci_problem(ci, event, branch, head, parents, *, no_window=False):
    """v4 checks the required summary plus file-backed group/short evidence."""
    if ci["workflow"] != WORKFLOW or ci["event"] != event or ci["branch"] != branch:
        return "wrong CI workflow, event, or branch"
    if ci["head_sha"] != head or ci["parents"] != parents or ci["tested_sha"] in parents:
        return "CI checkout or ordered parents differ from the reviewed scope"
    if event == "push" and ci["tested_sha"] != head:
        return "push CI must check out the actual merge SHA"
    if (ci["run_id"], ci["attempt"]) != (ci["latest_run_id"], ci["latest_attempt"]):
        return "a newer applicable CI attempt is unresolved"
    if ci["status"] != "completed" or ci["conclusion"] != "success":
        return "required CI is not successful"
    jobs = {job["name"]: job for job in ci["jobs"]}
    summary = jobs.get("Python 3.13")
    if summary is None:
        return "missing Python 3.13 required check"
    required_name = "Grouped validation" if event == "pull_request" else "Merge short validation"
    if required_name not in jobs:
        return "missing applicable validation job"
    for job in jobs.values():
        if job["name"] in {"Grouped validation", "Merge short validation"} - {required_name} and job["conclusion"] == "skipped":
            continue
        if job["conclusion"] != "success":
            return "a required CI job is unsuccessful"
        if (job["run_id"], job["attempt"], job["tested_sha"]) != (ci["run_id"], ci["attempt"], ci["tested_sha"]):
            return "job metadata belongs to a different run/attempt/checkout"
    if not summary["runner"].startswith("windows-") or summary["python_version"] != "3.13.0":
        return "summary lacks the supported Windows Python environment"
    required_steps = [] if no_window else ["Configure same-installation Tcl/Tk"]
    if event == "pull_request" and not no_window:
        required_steps.append("Early hosted Tk preflight")
    for required_step in required_steps:
        if jobs[required_name]["steps"].get(required_step) != "success":
            return "same-installation wiring or early preflight is missing or unsuccessful"
    step = "Verify required validation results"
    if summary["steps"].get(step) != "success":
        return "required evidence verifier step is missing or unsuccessful"
    return None


def _next_action_staged(state):
    if not state["authorization"]["active"] or not state["current"]["working_tree_clean"]:
        return _decision(state, "STOP", "active authorization and clean working tree required")
    pr, merge = state["pr"], state["merge"]
    merged = pr is not None and pr["state"] == "merged"
    if merge is not None and not merged:
        return _decision(state, "STOP", "merge contradicts PR state")
    base, head = (pr["base_sha"], pr["head_sha"]) if merged else (state["current"]["base"], state["current"]["head"])
    if pr is not None:
        if (pr["base_branch"], pr["head_branch"]) != (state["task"]["base_branch"], state["task"]["head_branch"]):
            return _decision(state, "STOP", "PR branch pair differs from task")
        if pr["state"] == "closed":
            return _decision(state, "STOP", "closed unmerged PR must not be duplicated")
        if not merged and (pr["base_sha"], pr["head_sha"]) != (base, head):
            return _decision(state, "REFRESH_EVIDENCE", "PR scope changed")
        if not pr["findings_checked"]:
            return _decision(state, "REFRESH_EVIDENCE", "latest findings have not been checked")
        if pr["new_blockers"]:
            return _post_merge_blocked(state, "confirmed PR finding") if merged else _correct(state, "confirmed PR finding")
    reviews = [_review(state, number, base, head) for number in (1, 2)]
    # Confirmed blockers are handled before any wait or test requirement.
    for number, review in enumerate(reviews, 1):
        if review is None:
            continue
        problem = _review_problem(state, review, number)
        if problem:
            return _decision(state, "STOP", problem)
        if review["verdict"] == "BLOCKED":
            kind = review["blocker_kind"]
            if kind == "code":
                return _post_merge_blocked(state, "confirmed review finding") if merged else _correct(state, "confirmed review finding; both full scopes need review at new HEAD")
            return _decision(state, "REFRESH_EVIDENCE" if kind == "evidence" else "STOP",
                             f"round {number} BLOCKED ({kind}); retain original report",
                             blocked_report_ref=review["report_ref"])
    if reviews[0] is None:
        return _decision(state, "STOP" if merged or 1 in state["unavailable_review_rounds"] else "REQUEST_REVIEW_1", "full cumulative code review required")
    if state["execution"]["mode"] == "development":
        return _decision(state, "WAIT_VALIDATION_AUTHORIZATION", "code review is intermediate; wait for explicit start-validation instruction")
    if pr is None:
        return _decision(state, "ENSURE_DRAFT_PR", "code reviewed without a confirmed blocker; find unique PR before draft creation",
                         base_branch="develop", head_branch=state["task"]["head_branch"])
    if reviews[1] is None:
        return _decision(state, "STOP" if merged or 2 in state["unavailable_review_rounds"] else "REQUEST_REVIEW_2", "full PR code review may overlap CI")
    ci = state["pr_ci"]
    if ci is None or ci["status"] in ("queued", "in_progress"):
        return _decision(state, "WAIT_PR_CI", "formal functional coverage is pending")
    no_window = state.get("validation_policy") == NO_WINDOW_POLICY
    problem = _staged_ci_problem(ci, "pull_request", state["task"]["head_branch"], head, [base, head], no_window=no_window)
    if problem:
        return _decision(state, "STOP" if merged else "REFRESH_EVIDENCE", problem)
    try:
        try:
            from scripts.validation_evidence import verify_coverage, verify_reuse, sha256_file
        except ModuleNotFoundError:
            from validation_evidence import verify_coverage, verify_reuse, sha256_file
        if state["coverage"] is None:
            raise ValueError("missing PR coverage bundle")
        coverage = verify_coverage(Path(state["coverage"]))
        if no_window != (coverage.get("policy") == NO_WINDOW_POLICY):
            raise ValueError("coverage policy differs from adopted task policy")
        if coverage["candidate"] != {"head": ci["tested_sha"], "tree": ci["tree"], "parents": [base, head]}:
            raise ValueError("coverage candidate differs from current PR integration")
        coverage_ci = coverage["ci"]
        if any(coverage_ci[k] != ci[k] for k in ("run_id", "attempt")) or coverage_ci["event"] != "pull_request":
            raise ValueError("coverage is not bound to this PR run/attempt")
        digest = sha256_file(Path(state["coverage"]))
    except (OSError, ValueError, TypeError, KeyError, zipfile.BadZipFile) as exc:
        return _decision(state, "STOP" if merged else "REFRESH_EVIDENCE", f"functional evidence invalid: {exc}")
    for number, review in enumerate(reviews, 1):
        if review["verdict"] != "PASS" or review["coverage_sha256"] != digest:
            return _decision(state, "REFRESH_EVIDENCE", f"round {number} must append formal evidence supplement",
                             blocked_report_ref=review["report_ref"])
    if reviews[1]["ci"] != {key: ci[key] for key in ("run_id", "attempt", "tested_sha")}:
        return _decision(state, "REFRESH_EVIDENCE", "round two formal attestation must bind latest PR CI")
    if merged:
        if merge is None:
            return _decision(state, "VERIFY_MERGE", "retrieve actual merge; never repeat merge")
        if merge["parents"] != [base, head] or merge["tree"] != ci["tree"]:
            return _post_merge_blocked(state, "actual merge ordered parents/tree differ", code=False)
        if merge["develop_head"] != state["current"]["base"] or not merge["develop_contains_merge"]:
            return _post_merge_blocked(state, "develop ancestry has not been established", code=False)
        push = state["push_ci"]
        if push is None or push["status"] in ("queued", "in_progress"):
            return _decision(state, "WAIT_PUSH_CI", "actual merge requires its own short push validation")
        problem = _staged_ci_problem(push, "push", "develop", merge["sha"], [base, head], no_window=no_window)
        if problem or push["tree"] != merge["tree"]:
            return _post_merge_blocked(state, problem or "push tree mismatch", code=False)
        try:
            short = verify_reuse(Path(state["coverage"]), Path(state["short_validation"]))
            if short["candidate"] != {"head": merge["sha"], "parents": [base, head], "tree": merge["tree"]}:
                raise ValueError("short evidence is not for the actual merge")
            if any(short["ci"][key] != push[key] for key in ("run_id", "attempt")):
                raise ValueError("short evidence belongs to another push attempt")
        except (OSError, ValueError, TypeError, KeyError, zipfile.BadZipFile) as exc:
            return _post_merge_blocked(state, f"short validation cannot reuse PR coverage: {exc}", code=False)
        return _decision(state, "COMPLETE", "formal independent reviews, PR functional coverage and actual merge short checks verified",
                         merge_sha=merge["sha"], develop_head=merge["develop_head"])
    if not pr["mergeable"] or not pr["protection_satisfied"]:
        return _decision(state, "STOP", "mergeability or protection requirements are unsatisfied")
    return _decision(state, "MERGE_PROPOSAL", "recheck remote scope/findings/protection immediately before merge",
                     pr_number=pr["number"], merge_method="merge", expected_head_sha=head, expected_base_sha=base)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "next-action"))
    parser.add_argument("evidence", type=Path)
    args = parser.parse_args(argv)
    try:
        state = json.loads(args.evidence.read_text(encoding="utf-8-sig"),
                           object_pairs_hook=_unique_object,
                           parse_constant=lambda value: (_ for _ in ()).throw(EvidenceError(value)))
        if args.command == "validate":
            validate_state(state)
            result = {"schema_valid": True, "authenticity_verified": False}
        else:
            result = next_action(state)
    except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
        result = {"action": "STOP", "reason": f"invalid evidence: {exc}"}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 2 if result.get("action") == "STOP" else 0


if __name__ == "__main__":
    sys.exit(main())
