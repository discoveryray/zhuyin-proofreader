"""Read-only PR29 fifth/sixth-round continuation; frozen v2 safety plus addenda.

Derived from 1593e7af65596d320b4427f1b15bb2bc0bdc949c:scripts/pr_review_gate.py.
The ordinary gate is unchanged. Historical gaps are never review evidence.

This validates coordinator-collected evidence, not its authenticity. It has no
network, credential, Git write, agent-launch, or merge capability.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys


SCHEMA = "zhuyin-pr29-continuation/1"
MERGE_SCHEMA = "zhuyin-pr29-merge-continuation/1"
BASELINE = "1593e7af65596d320b4427f1b15bb2bc0bdc949c"
START = "a38bdf1c84889c52e7281a0c23c9458e3762afa8"
DEVELOP = "502414b3b38e004a6d8d9cb693cf21b65148765a"
FIFTH_HEAD = "0249c44636857739a262d10bd7f4e7ed1f713d1c"
TASK = "review-confirm-responsive"
BRANCH = "codex/review-confirm-responsive"
PR_URL = "https://github.com/discoveryray/zhuyin-proofreader/pull/29"
AUTHORIZATION_REF = "pr29-round5/user-adopted-contract"
AUTHORIZATION_SHA256 = "53790b7433d64197d046dbaad0e61c6ebb0ad5775a1d5ad3ec36e142371a0b26"
MERGE_AUTHORIZATION_REF = "pr29-round6/user-adopted-contract"
MERGE_AUTHORIZATION_SHA256 = "1ab7e0e1e17dfa0a5ebddf0c0f697ca4056b7f090cc011ecc479b070a872a335"
FIFTH_STATE_SHA256 = "45b5d52d53385a89e8759022bb691ffbe55d049c56791d7ebda694bec4bffff8"
FIFTH_STOP_SHA256 = "33efa9af7012d50a88a4b1f21103a550b89c7a868328cfb6e95927ffed63f182"
FIFTH_REPORT_REFS = (
    "pr29-round5-r1-20260925T152241Z-0249c4463685",
    "review-confirm-responsive/round5/review2/0249c4463685/20260925-full-pr-01",
)
FIFTH_KNOWN_IMPLEMENTERS = (
    "/root", "/root/implement_round5", "/root/benchmark_round5", "/root/reconstruct_evidence",
)
MISSING_ORIGINALS = (
    "review1-e53f44d-report.md", "review1-89e0297-report.md", "review2-89e0297-report.md",
    "review1-bb4c136-report.md", "review2-bb4c136-report.md", "review1-bd6b478-report.md",
    "review2-bd6b478-report.md", "task.md", "correction-4-user-authorization.md",
    "original-four-correction-ledger-and-state", "original-gate-decision-and-command-log",
    "original-user-task-and-authorization-session", "original-local-full-suite-and-GUI-logs",
    "original-four-benchmark-raw-runs",
)
HISTORY = (
    "e53f44d2da29b46dfe0bd0f2c06af9919808b99f",
    "89e0297bd6e5b9b08d077b2216029fb6224976fc",
    "bb4c136fbc49c706fecfd48d1dacf81b4d97584e",
    "bd6b4789a646f8bed075c256447f54922a5c4f90",
    START,
)
LOCAL_CHECKS = ("unittest", "pytest", "gui", "runtime", "compile", "diff", "performance")
MERGE_LOCAL_CHECKS = ("unittest", "pytest", "gui", "runtime", "compile", "diff_baseline",
                      "diff_develop", "inventory", "clean_tree")
KNOWN_FINDING_IDS = {*(f"KF-{number:02d}" for number in range(1, 8)),
                     *(f"RC-{number:02d}" for number in range(1, 5))}
REPOSITORY = "discoveryray/zhuyin-proofreader"
WORKFLOW = ".github/workflows/ci.yml"
PYTHONS = ("3.12", "3.13")
OPERATIONS = {"implement", "delegate", "test", "commit", "push", "pr"}
MERGE_OPERATIONS = OPERATIONS | {"ready", "merge"}
COMMON_STEPS = (
    "Check out repository", "Set up Python", "Show Python version",
    "Upgrade pip", "Install development dependencies",
    "Validate runtime asset integrity", "Run full unittest suite",
    "Run full pytest suite", "Audit test entrypoint coverage",
    "Verify GUI test execution", "Compile Python sources",
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


def validate_state(state):
    """Validate the closed input schema. This does not grant permission."""
    if type(state) is dict and state.get("schema") == MERGE_SCHEMA:
        return validate_merge_state(state)
    _fields(state, "schema task authorization current implementers corrections reviews "
            "unavailable_review_rounds pr pr_ci handoffs continuation remote local_validation", "state")
    _require(state["schema"] == SCHEMA, "unsupported schema")
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
    _fields(auth, "task_id active operations source_ref source_sha256", "authorization")
    _text(auth["source_ref"], "authorization.source_ref")
    _boolean(auth["active"], "authorization.active")
    _texts(auth["operations"], "authorization.operations")
    _require(set(auth["operations"]) <= OPERATIONS, "unknown authorized operation")
    _require(auth["task_id"] == task["id"], "authorization belongs to another task")
    current = state["current"]
    _fields(current, "base head tree working_tree_clean evidence_ref", "current")
    for field in ("base", "head"):
        _sha(current[field], f"current.{field}")
    _boolean(current["working_tree_clean"], "current.working_tree_clean")
    _text(current["evidence_ref"], "current.evidence_ref")
    _texts(state["implementers"], "implementers")
    _require(bool(state["implementers"]), "all code-writing actors must be listed")
    _require(type(state["unavailable_review_rounds"]) is list and
             all(type(n) is int and n in (1, 2) for n in state["unavailable_review_rounds"]),
             "invalid unavailable review rounds")
    _require(type(state["corrections"]) is list and len(state["corrections"]) == 5,
             "this continuation retains exactly four prior rounds and the fifth candidate")
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
                "supersedes_report_ref resolution_evidence_ref", "review")
        _require(type(review["round"]) is int and review["round"] in (1, 2), "invalid review round")
        for field in ("reviewer", "scope", "report_ref"):
            _text(review[field], f"review.{field}")
        for field in ("baseline", "base", "head"):
            _sha(review[field], f"review.{field}")
        for field in ("independent", "full_diff_reviewed"):
            _boolean(review[field], f"review.{field}")
        _texts(review["findings"], "review.findings")
        _require(review["verdict"] in ("PASS", "BLOCKED"), "unknown review verdict")
        if review["verdict"] == "PASS":
            _require(review["blocker_kind"] is None and not review["findings"],
                     "PASS cannot contain a blocker")
        else:
            _require(review["blocker_kind"] in ("code", "evidence", "capability", "contract")
                     and bool(review["findings"]), "BLOCKED needs an explicit kind and findings")
        for field in ("supersedes_report_ref", "resolution_evidence_ref"):
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
            _require(review["verdict"] == "BLOCKED" and review["blocker_kind"] != "code",
                     "round 2 without CI metadata must remain a non-code BLOCKED")
    _validate_review_history(state)
    pr = state["pr"]
    if pr is not None:
        _fields(pr, "number url state base_branch head_branch base_sha head_sha mergeable "
                "protection_satisfied findings_checked new_blockers evidence_ref draft", "pr")
        _integer(pr["number"], "pr.number")
        for field in ("url", "base_branch", "head_branch", "evidence_ref"):
            _text(pr[field], f"pr.{field}")
        for field in ("base_sha", "head_sha"):
            _sha(pr[field], f"pr.{field}")
        _require(pr["state"] in ("open", "closed", "merged"), "invalid PR state")
        for field in ("mergeable", "protection_satisfied", "findings_checked", "draft"):
            _boolean(pr[field], f"pr.{field}")
        _texts(pr["new_blockers"], "pr.new_blockers")
    _ci_schema(state["pr_ci"], "pr_ci")
    _require(type(state["handoffs"]) is list, "handoffs: expected list")
    for handoff in state["handoffs"]:
        _fields(handoff, "from to head evidence_ref", "handoff")
        for field in ("from", "to", "evidence_ref"):
            _text(handoff[field], f"handoff.{field}")
        _sha(handoff["head"], "handoff.head")
    _continuation_schema(state)


def _decision(state, action, reason, **details):
    needed = {
        "REQUEST_REVIEW_1": {"delegate"}, "REQUEST_REVIEW_2": {"delegate"},
        "PUSH_CANDIDATE": {"push", "pr"},
    }.get(action, set())
    missing = needed - set(state["authorization"]["operations"])
    if missing:
        action, reason, details = "STOP", f"next action is not authorized: {sorted(missing)}", {}
    identity = {"task": state["task"], "base": state["current"]["base"],
                "head": state["current"]["head"], "action": action}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest()
    return {"action": action, "reason": reason, "idempotency_key": key, **details}


def _review_scope(review):
    return tuple(review[field] for field in ("round", "baseline", "base", "head", "scope"))


def _validate_review_history(state):
    """Resolve only explicit, append-only supplements backed by retained evidence."""
    reports, superseded = {}, set()
    for review in state["reviews"]:
        ref, prior = review["report_ref"], review["supersedes_report_ref"]
        _require(ref not in reports, "report_ref must uniquely identify an original report")
        if prior is None:
            _require(review["resolution_evidence_ref"] is None, "resolution needs a supersession target")
        else:
            _require(prior in reports and prior not in superseded,
                     "supersession target must be earlier, retained, and not already superseded")
            old = reports[prior]
            _require(old["verdict"] == "BLOCKED" and old["blocker_kind"] != "code",
                     "only non-code BLOCKED reports may be supplemented at unchanged HEAD")
            _require(_review_scope(review) == _review_scope(old), "supersession must retain the exact code scope")
            _text(review["resolution_evidence_ref"], "supersession resolution evidence")
            for candidate in (old, review):
                problem = _review_problem(state, candidate, candidate["round"])
                _require(problem is None, "invalid supersession review: " + str(problem))
            superseded.add(prior)
        reports[ref] = review
    seen = set()
    for ref, review in reports.items():
        if ref not in superseded:
            key = (*_review_scope(review), json.dumps(review["ci"], sort_keys=True))
            _require(key not in seen, "ambiguous unlinked review for the same scope")
            seen.add(key)


def _review(state, number, base, head):
    superseded = {r["supersedes_report_ref"] for r in state["reviews"]}
    matches = [r for r in state["reviews"] if r["report_ref"] not in superseded and r["round"] == number and
               (r["baseline"], r["base"], r["head"]) == (state["task"]["baseline"], base, head)]
    # Unresolved blockers survive CI reruns and unrelated PASS records.
    blockers = [r for r in matches if r["verdict"] == "BLOCKED"]
    if blockers:
        return min(blockers, key=lambda r: r["blocker_kind"] != "code")
    if number == 2 and state["pr_ci"] is not None:
        ci = state["pr_ci"]
        expected = {field: ci[field] for field in ("run_id", "attempt", "tested_sha")}
        matches = [r for r in matches if r["ci"] == expected]
    return matches[0] if len(matches) == 1 else None


def _review_problem(state, review, number):
    if not review["independent"] or review["reviewer"] in state["implementers"]:
        return "reviewer must be independent of every implementation actor"
    if not review["full_diff_reviewed"]:
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


def _ci_problem(ci, event, branch, head, parents):
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
        if not re.fullmatch(re.escape(version) + r"\.\d+", job["python_version"]):
            return "REFRESH_EVIDENCE", f"wrong actual Python version: {name}"
        if version == "3.13" and job["python_version"] != "3.13.0":
            return "REFRESH_EVIDENCE", "Python 3.13 must retain the current pinned 3.13.0 environment"
        if (job["run_id"], job["attempt"], job["tested_sha"]) != (
                ci["run_id"], ci["attempt"], ci["tested_sha"]):
            return "REFRESH_EVIDENCE", f"job is from another run attempt or checkout: {name}"
        required = (*COMMON_STEPS, f"Check committed whitespace ({'pull request' if event == 'pull_request' else 'push'})")
        if any(step not in job["steps"] for step in required):
            return "REFRESH_EVIDENCE", f"required step evidence is missing: {name}"
        if any(job["steps"].get(step) != "success" for step in required):
            return "INVESTIGATE_CI", f"required step skipped or unsuccessful: {name}"
        event_whitespace = f"Check committed whitespace ({'pull request' if event == 'pull_request' else 'push'})"
        optional_skips = {"Check committed whitespace (pull request)", "Check committed whitespace (push)",
                          "Check committed whitespace (workflow dispatch)"} - {event_whitespace}
        if any(result != "success" and not (result == "skipped" and step in optional_skips)
               for step, result in job["steps"].items()):
            return "INVESTIGATE_CI", f"another workflow step did not succeed: {name}"
    if any(job["conclusion"] != "success" for job in jobs.values()):
        return "INVESTIGATE_CI", "another workflow job has not succeeded"
    return None


def _continuation_schema(state):
    """Only the adopted fixed PR29 continuation, never a general round override."""
    task, auth, current = state["task"], state["authorization"], state["current"]
    _require(task == {"id": TASK, "repository": REPOSITORY, "baseline": BASELINE,
                      "base_branch": "develop", "head_branch": BRANCH}, "wrong fixed task")
    _require(auth["source_ref"] == AUTHORIZATION_REF and
             auth["source_sha256"] == AUTHORIZATION_SHA256, "wrong adopted authorization source")
    _require(current["base"] == DEVELOP, "develop target drifted")
    _sha(current["tree"], "current.tree")
    _require(current["head"] not in (*HISTORY, BASELINE, DEVELOP), "new candidate required")
    chain = (*HISTORY, current["head"])
    for index, correction in enumerate(state["corrections"]):
        _require((correction["from_head"], correction["to_head"]) == chain[index:index + 2],
                 "immutable four-round history or fifth candidate changed")
    continuation = state["continuation"]
    _fields(continuation, "created_at reconstructed history_ref missing_originals known_findings "
            "finding_inventory_ref original_stop_ref candidate_head integration_ref contains_start "
            "contains_develop", "continuation")
    _require(continuation["reconstructed"] is True, "must label reconstructed history")
    for field in ("created_at", "history_ref", "finding_inventory_ref", "original_stop_ref", "integration_ref"):
        _text(continuation[field], "continuation." + field)
    _texts(continuation["missing_originals"], "missing_originals")
    _require(continuation["candidate_head"] == current["head"], "frozen candidate mismatch")
    for field in ("contains_start", "contains_develop"):
        _require(continuation[field] is True, "integration ancestry evidence required")
    _require(type(continuation["known_findings"]) is list, "known_findings must be a list")
    identifiers = []
    for finding in continuation["known_findings"]:
        _fields(finding, "id source_ref description status verification_ref", "known_finding")
        for field in ("id", "source_ref", "description"):
            _text(finding[field], "finding." + field)
        _require(finding["status"] in ("verified_on_candidate", "open", "unknown"), "unknown finding status")
        if finding["status"] == "verified_on_candidate":
            _text(finding["verification_ref"], "finding.verification_ref")
        else:
            _require(finding["verification_ref"] is None, "unresolved finding cannot claim verification")
        identifiers.append(finding["id"])
    _require(len(identifiers) == len(set(identifiers)), "duplicate historical finding")
    _require(KNOWN_FINDING_IDS <= set(identifiers), "known finding inventory is incomplete")
    remote = state["remote"]
    _fields(remote, "base head evidence_ref", "remote")
    _sha(remote["base"], "remote.base")
    _sha(remote["head"], "remote.head")
    _text(remote["evidence_ref"], "remote.evidence_ref")
    _require(remote["base"] == DEVELOP and remote["head"] in (START, current["head"]),
             "unexpected external remote drift")
    checks = state["local_validation"]
    _fields(checks, "head platform python_version evidence_ref checks", "local_validation")
    _require(checks["head"] == current["head"] and checks["platform"] == "Windows"
             and checks["python_version"] == "3.13.0", "local validation scope/runtime mismatch")
    _text(checks["evidence_ref"], "local_validation.evidence_ref")
    _fields(checks["checks"], " ".join(LOCAL_CHECKS), "local_validation.checks")
    for name, check in checks["checks"].items():
        _fields(check, "status evidence_ref", "local_validation." + name)
        _require(check["status"] in ("success", "failure", "missing"), "unknown local check status")
        _text(check["evidence_ref"], "local check evidence_ref")
    pr = state["pr"]
    _require(pr is not None and type(pr["number"]) is int and pr["number"] == 29
             and pr["url"] == PR_URL, "the original PR29 must be retained")
    _require((pr["base_branch"], pr["head_branch"]) == ("develop", BRANCH), "wrong PR branch pair")
    _require(pr["draft"] is True, "original PR must remain draft")
    # Historical reports remain in the retained reconstruction, not in current
    # review records. No fabricated role/session is needed to model a gap.
    for review in state["reviews"]:
        _require((review["baseline"], review["base"], review["head"]) ==
                 (BASELINE, DEVELOP, current["head"]), "review is not for this candidate")


def next_action(state):
    """Advise review/push/evidence only. Never authorize merge or completion."""
    if type(state) is dict and state.get("schema") == MERGE_SCHEMA:
        return next_merge_action(state)
    try:
        validate_state(state)
    except (EvidenceError, TypeError, KeyError) as exc:
        return {"action": "STOP", "reason": f"invalid evidence: {exc}"}
    if not state["authorization"]["active"]:
        return _decision(state, "STOP", "task authorization is absent or revoked")
    if not state["current"]["working_tree_clean"]:
        return _decision(state, "STOP", "working tree is not clean")
    if state["pr"]["state"] != "open":
        return _decision(state, "STOP", "original PR must remain open and unmerged")
    if any(f["status"] != "verified_on_candidate" for f in state["continuation"]["known_findings"]):
        return _decision(state, "STOP", "known historical findings lack candidate resolution evidence")
    pr, remote = state["pr"], state["remote"]
    head = state["current"]["head"]
    before_push = remote["head"] == START
    if before_push:
        if pr["head_sha"] != START or pr["base_sha"] not in (BASELINE, DEVELOP):
            return _decision(state, "REFRESH_EVIDENCE", "pre-push PR metadata differs from the retained original")
        if state["pr_ci"] is not None:
            return _decision(state, "STOP", "old PR CI cannot stand for unpublished candidate CI")
    elif (pr["base_sha"], pr["head_sha"]) != (DEVELOP, head):
        return _decision(state, "REFRESH_EVIDENCE", "post-push PR API scope is not synchronized")
    if not pr["findings_checked"]:
        return _decision(state, "REFRESH_EVIDENCE", "check latest PR findings and map them to the candidate")
    if pr["new_blockers"]:
        return _decision(state, "STOP", "confirmed candidate code findings require an unauthorized sixth round")
    for number in (1, 2):
        reported = _review(state, number, DEVELOP, head)
        if reported is not None:
            problem = _review_problem(state, reported, number)
            if problem:
                return _decision(state, "STOP", problem)
            if reported["verdict"] == "BLOCKED" and reported["blocker_kind"] == "code":
                return _decision(state, "STOP", "fifth round exhausted; confirmed code finding requires unauthorized sixth round",
                                 blocked_report_ref=reported["report_ref"])
    if any(c["status"] != "success" for c in state["local_validation"]["checks"].values()):
        return _decision(state, "REFRESH_EVIDENCE", "candidate requires all local acceptance evidence")
    for number in (1, 2):
        review = _review(state, number, DEVELOP, head)
        if review is not None:
            problem = _review_problem(state, review, number)
            if problem:
                return _decision(state, "STOP", problem)
            if review["verdict"] == "BLOCKED":
                kind = review["blocker_kind"]
                action = "REFRESH_EVIDENCE" if kind == "evidence" else "STOP"
                reason = f"round {number} is BLOCKED ({kind})"
                if kind == "code":
                    reason += "; fifth round exhausted; no sixth-round code changes authorized"
                return _decision(state, action, reason, blocked_report_ref=review["report_ref"])
        if number == 2:
            if before_push:
                return _decision(state, "PUSH_CANDIDATE", "round one passed; recheck direct refs and ordinary-push original branch",
                                 expected_remote_head=START, expected_base_sha=DEVELOP, candidate_head=head)
            ci = state["pr_ci"]
            if ci is None or ci["status"] in ("queued", "in_progress"):
                return _decision(state, "WAIT_PR_CI", "matching completed candidate PR CI is required")
            problem = _ci_problem(ci, "pull_request", BRANCH, head, [DEVELOP, head])
            if problem:
                return _decision(state, *problem)
            if ci["tree"] != state["current"]["tree"]:
                return _decision(state, "REFRESH_EVIDENCE", "tested integration tree differs from candidate tree")
        if review is None:
            if number in state["unavailable_review_rounds"]:
                return _decision(state, "STOP", f"round {number} reviewer unavailable")
            return _decision(state, f"REQUEST_REVIEW_{number}", "independent complete candidate review required")
    if "pr" not in state["authorization"]["operations"]:
        return _decision(state, "STOP", "updating original PR evidence is not authorized")
    return _decision(state, "STOP", "unmerged draft PR may be delivered; merge is not authorized",
                     delivery_ready=True, pr_number=29, candidate_head=head,
                     mergeable=pr["mergeable"], protection_satisfied=pr["protection_satisfied"])


def _utc_instant(value, where):
    _text(value, where)
    _require(value.endswith("Z"), f"{where}: expected UTC Z timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise EvidenceError(f"{where}: invalid UTC timestamp") from exc
    return parsed.astimezone(timezone.utc)


def validate_merge_state(state):
    """Closed, PR29-only sixth-round schema; old fifth-round inputs remain unchanged."""
    _fields(state, "schema task authorization current implementers corrections reviews "
            "unavailable_review_rounds pr pr_ci handoffs continuation remote local_validation "
            "prior_fifth ready_transition merge post_merge_ancestry push_event push_ci", "state")
    _require(state["schema"] == MERGE_SCHEMA, "unsupported merge continuation schema")
    task = state["task"]
    _fields(task, "id repository baseline base_branch head_branch", "task")
    _require(task == {"id": TASK, "repository": REPOSITORY, "baseline": BASELINE,
                      "base_branch": "develop", "head_branch": BRANCH}, "wrong fixed PR29 task")
    auth = state["authorization"]
    _fields(auth, "task_id active operations source_ref source_sha256", "authorization")
    _boolean(auth["active"], "authorization.active")
    _texts(auth["operations"], "authorization.operations")
    _require(set(auth["operations"]) <= MERGE_OPERATIONS, "unknown sixth-round operation")
    _require(auth["task_id"] == TASK and auth["source_ref"] == MERGE_AUTHORIZATION_REF and
             auth["source_sha256"] == MERGE_AUTHORIZATION_SHA256,
             "wrong or unbound sixth-round user adoption")
    current = state["current"]
    _fields(current, "base head tree working_tree_clean evidence_ref", "current")
    for field in ("base", "head", "tree"):
        _sha(current[field], "current." + field)
    _require(current["base"] == DEVELOP and current["head"] not in
             (BASELINE, DEVELOP, *HISTORY, FIFTH_HEAD), "wrong sixth-round candidate scope")
    _boolean(current["working_tree_clean"], "current.working_tree_clean")
    _text(current["evidence_ref"], "current.evidence_ref")
    _texts(state["implementers"], "implementers")
    _require(set(FIFTH_KNOWN_IMPLEMENTERS) <= set(state["implementers"]),
             "retained fifth-round implementation actors are missing")
    _require(type(state["unavailable_review_rounds"]) is list and
             all(type(n) is int and n in (1, 2) for n in state["unavailable_review_rounds"]) and
             len(state["unavailable_review_rounds"]) == len(set(state["unavailable_review_rounds"])),
             "invalid unavailable review rounds")

    corrections = state["corrections"]
    _require(type(corrections) is list and len(corrections) == 6,
             "exactly five retained rounds and one sixth round are required; seventh round is not authorized")
    chain = (*HISTORY, FIFTH_HEAD, current["head"])
    for index, correction in enumerate(corrections):
        _fields(correction, "number from_head to_head evidence_ref", "correction")
        _require(type(correction["number"]) is int and correction["number"] == index + 1 and
                 (correction["from_head"], correction["to_head"]) == chain[index:index + 2],
                 "correction history, fixed fifth candidate, or sixth-round count changed")
        _text(correction["evidence_ref"], "correction.evidence_ref")

    fifth = state["prior_fifth"]
    _fields(fifth, "candidate_head state_ref state_sha256 stop_ref stop_sha256 "
            "stop_action report_refs missing_originals", "prior_fifth")
    _require(fifth["candidate_head"] == FIFTH_HEAD and
             fifth["state_sha256"] == FIFTH_STATE_SHA256 and
             fifth["stop_sha256"] == FIFTH_STOP_SHA256 and fifth["stop_action"] == "STOP" and
             fifth["report_refs"] == list(FIFTH_REPORT_REFS) and
             fifth["missing_originals"] == list(MISSING_ORIGINALS),
             "frozen fifth-round evidence, STOP, reports, or fourteen gaps changed")
    for field in ("state_ref", "stop_ref"):
        _text(fifth[field], "prior_fifth." + field)

    continuation = state["continuation"]
    _fields(continuation, "created_at reconstructed history_ref missing_originals known_findings "
            "finding_inventory_ref original_stop_ref candidate_head integration_ref contains_start "
            "contains_develop contains_fifth", "continuation")
    _utc_instant(continuation["created_at"], "continuation.created_at")
    _require(continuation["reconstructed"] is True and continuation["candidate_head"] == current["head"]
             and continuation["contains_start"] is True and continuation["contains_develop"] is True
             and continuation["contains_fifth"] is True and
             continuation["missing_originals"] == list(MISSING_ORIGINALS),
             "candidate ancestry or retained historical gaps changed")
    for field in ("history_ref", "finding_inventory_ref", "original_stop_ref", "integration_ref"):
        _text(continuation[field], "continuation." + field)
    findings = continuation["known_findings"]
    _require(type(findings) is list, "known finding inventory must be a list")
    identifiers = []
    for finding in findings:
        _fields(finding, "id source_ref description status verification_ref verified_head", "known_finding")
        for field in ("id", "source_ref", "description"):
            _text(finding[field], "known_finding." + field)
        _require(finding["status"] in ("verified_on_candidate", "open", "unknown"),
                 "unknown historical finding status")
        if finding["status"] == "verified_on_candidate":
            _text(finding["verification_ref"], "known_finding.verification_ref")
            _require(finding["verified_head"] == current["head"],
                     "historical finding proof belongs to another candidate")
        else:
            _require(finding["verification_ref"] is None and finding["verified_head"] is None,
                     "unresolved finding cannot claim verification")
        identifiers.append(finding["id"])
    _require(len(identifiers) == len(set(identifiers)) and KNOWN_FINDING_IDS <= set(identifiers),
             "known historical finding inventory is incomplete")

    checks = state["local_validation"]
    _fields(checks, "head platform python_version evidence_ref checks performance", "local_validation")
    _require(checks["head"] == current["head"] and checks["platform"] == "Windows" and
             checks["python_version"] == "3.13.0", "local validation is for another scope/runtime")
    _text(checks["evidence_ref"], "local_validation.evidence_ref")
    _fields(checks["checks"], " ".join(MERGE_LOCAL_CHECKS), "local_validation.checks")
    for name, check in checks["checks"].items():
        _fields(check, "status evidence_ref", "local_validation." + name)
        _require(check["status"] in ("success", "failure", "missing"), "unknown local check status")
        _text(check["evidence_ref"], "local_validation." + name + ".evidence_ref")
    performance = checks["performance"]
    _fields(performance, "status source_head product_unchanged harness_unchanged "
            "impact_ref evidence_ref", "local_validation.performance")
    _require(performance["status"] in ("reused", "measured", "missing"), "invalid performance status")
    _sha(performance["source_head"], "performance.source_head")
    for field in ("product_unchanged", "harness_unchanged"):
        _boolean(performance[field], "performance." + field)
    for field in ("impact_ref", "evidence_ref"):
        _text(performance[field], "performance." + field)
    if performance["status"] == "reused":
        _require(performance["source_head"] == FIFTH_HEAD and
                 performance["product_unchanged"] and performance["harness_unchanged"],
                 "N performance may be reused only for unchanged product and harness")
    if performance["status"] == "measured":
        _require(performance["source_head"] == current["head"],
                 "new performance result must belong to candidate")

    reviews = state["reviews"]
    _require(type(reviews) is list, "reviews: expected list")
    for review in reviews:
        _fields(review, "round reviewer baseline base head scope verdict independent "
                "full_diff_reviewed findings report_ref ci blocker_kind supersedes_report_ref "
                "resolution_evidence_ref", "review")
        _require(type(review["round"]) is int and review["round"] in (1, 2), "invalid review round")
        for field in ("reviewer", "scope", "report_ref"):
            _text(review[field], "review." + field)
        for field in ("baseline", "base", "head"):
            _sha(review[field], "review." + field)
        _require((review["baseline"], review["base"], review["head"]) ==
                 (BASELINE, DEVELOP, current["head"]), "old or different-head review cannot clear C")
        for field in ("independent", "full_diff_reviewed"):
            _boolean(review[field], "review." + field)
        _texts(review["findings"], "review.findings")
        _require(review["verdict"] in ("PASS", "BLOCKED"), "unknown review verdict")
        if review["verdict"] == "PASS":
            _require(review["blocker_kind"] is None and not review["findings"],
                     "PASS cannot contain blockers")
        else:
            _require(review["blocker_kind"] in ("code", "evidence", "capability", "contract")
                     and bool(review["findings"]), "BLOCKED needs kind and findings")
        for field in ("supersedes_report_ref", "resolution_evidence_ref"):
            if review[field] is not None:
                _text(review[field], "review." + field)
        if review["round"] == 1:
            _require(review["ci"] is None, "round one does not attest PR CI")
        elif review["ci"] is not None:
            _fields(review["ci"], "run_id attempt tested_sha", "review.ci")
            _integer(review["ci"]["run_id"], "review.ci.run_id")
            _integer(review["ci"]["attempt"], "review.ci.attempt")
            _sha(review["ci"]["tested_sha"], "review.ci.tested_sha")
        else:
            _require(review["verdict"] == "BLOCKED" and review["blocker_kind"] != "code",
                     "round two PASS requires CI identity")
    _validate_review_history(state)
    _require(not ({review["report_ref"] for review in reviews} & set(FIFTH_REPORT_REFS)),
             "sixth-round report_ref reuses a retained fifth-round report")
    for number in (1, 2):
        report = _review(state, number, DEVELOP, current["head"])
        if report is not None:
            _require(_review_problem(state, report, number) is None, "reviewer or review scope is not independent")

    pr = state["pr"]
    _fields(pr, "number url state base_branch head_branch base_sha head_sha head_repository "
            "mergeable protection_satisfied findings_checked new_blockers evidence_ref draft "
            "merged merge_commit_sha", "pr")
    _require(type(pr["number"]) is int and pr["number"] == 29 and pr["url"] == PR_URL and
             (pr["base_branch"], pr["head_branch"], pr["head_repository"]) ==
             ("develop", BRANCH, REPOSITORY), "not the original same-repository PR29")
    _require(pr["state"] in ("open", "closed"), "invalid PR state")
    for field in ("base_sha", "head_sha"):
        _sha(pr[field], "pr." + field)
    for field in ("mergeable", "protection_satisfied", "findings_checked", "draft", "merged"):
        _boolean(pr[field], "pr." + field)
    _texts(pr["new_blockers"], "pr.new_blockers")
    _text(pr["evidence_ref"], "pr.evidence_ref")
    if pr["merge_commit_sha"] is not None:
        _sha(pr["merge_commit_sha"], "pr.merge_commit_sha")
    _ci_schema(state["pr_ci"], "pr_ci")
    _ci_schema(state["push_ci"], "push_ci")
    remote = state["remote"]
    _fields(remote, "base head head_ref_exists evidence_ref", "remote")
    _sha(remote["base"], "remote.base")
    _boolean(remote["head_ref_exists"], "remote.head_ref_exists")
    if remote["head_ref_exists"]:
        _sha(remote["head"], "remote.head")
    else:
        _require(remote["head"] is None, "absent feature ref cannot claim a SHA")
    _text(remote["evidence_ref"], "remote.evidence_ref")
    _require(type(state["handoffs"]) is list, "handoffs: expected list")
    for handoff in state["handoffs"]:
        _fields(handoff, "from to head evidence_ref", "handoff")
        for field in ("from", "to", "evidence_ref"):
            _text(handoff[field], "handoff." + field)
        _sha(handoff["head"], "handoff.head")

    ready = state["ready_transition"]
    if ready is not None:
        _fields(ready, "decision_ref ready_event_ref ready_at rechecked_at pr_ref refs_ref "
                "findings_ref rules_ref ci_ref mergeable_at_ready protection_satisfied_at_ready",
                "ready_transition")
        for field in ("decision_ref", "ready_event_ref", "pr_ref", "refs_ref", "findings_ref",
                      "rules_ref", "ci_ref"):
            _text(ready[field], "ready_transition." + field)
        _require(_utc_instant(ready["ready_at"], "ready_transition.ready_at") <
                 _utc_instant(ready["rechecked_at"], "ready_transition.rechecked_at"),
                 "ready PR must be freshly rechecked after the transition")
        _require(ready["mergeable_at_ready"] is True and
                 ready["protection_satisfied_at_ready"] is True,
                 "ready transition lacks applicable protection/mergeability evidence")
    merge = state["merge"]
    if merge is not None:
        _fields(merge, "sha parents tree method pr_number expected_base expected_head merged_at "
                "api_record_ref git_ref develop_ref", "merge")
        for field in ("sha", "tree", "expected_base", "expected_head"):
            _sha(merge[field], "merge." + field)
        _require(type(merge["parents"]) is list and len(merge["parents"]) == 2,
                 "actual merge must have exactly two ordered parents")
        for parent in merge["parents"]:
            _sha(parent, "merge.parent")
        _require(merge["method"] == "merge" and type(merge["pr_number"]) is int
                 and merge["pr_number"] == 29, "wrong PR or merge method")
        _require(merge["sha"] not in merge["parents"], "merge commit cannot be its own parent")
        _require(ready is not None and
                 _utc_instant(merge["merged_at"], "merge.merged_at") >
                 _utc_instant(ready["rechecked_at"], "ready_transition.rechecked_at"),
                 "merge must follow the fresh ready-state check")
        for field in ("api_record_ref", "git_ref", "develop_ref"):
            _text(merge[field], "merge." + field)
    ancestry = state["post_merge_ancestry"]
    if ancestry is not None:
        _fields(ancestry, "tip merge_sha first_parent_commits git_ref", "post_merge_ancestry")
        _sha(ancestry["tip"], "post_merge_ancestry.tip")
        _sha(ancestry["merge_sha"], "post_merge_ancestry.merge_sha")
        _text(ancestry["git_ref"], "post_merge_ancestry.git_ref")
        _require(type(ancestry["first_parent_commits"]) is list and
                 bool(ancestry["first_parent_commits"]), "advanced develop needs a first-parent object chain")
        cursor = ancestry["tip"]
        seen = set()
        for commit in ancestry["first_parent_commits"]:
            _fields(commit, "sha parents object_ref", "post_merge_ancestry.commit")
            _sha(commit["sha"], "post_merge_ancestry.commit.sha")
            _require(commit["sha"] == cursor and cursor not in seen,
                     "advanced develop object chain is disconnected or cyclic")
            _require(type(commit["parents"]) is list and bool(commit["parents"]),
                     "advanced develop commit lacks parents")
            for parent in commit["parents"]:
                _sha(parent, "post_merge_ancestry.commit.parent")
            _text(commit["object_ref"], "post_merge_ancestry.commit.object_ref")
            seen.add(cursor)
            cursor = commit["parents"][0]
        _require(cursor == ancestry["merge_sha"],
                 "first-parent object chain does not reach the verified PR merge")
    push_event = state["push_event"]
    if push_event is not None:
        _fields(push_event, "repository ref before_sha after_sha evidence_ref", "push_event")
        for field in ("before_sha", "after_sha"):
            _sha(push_event[field], "push_event." + field)
        _text(push_event["evidence_ref"], "push_event.evidence_ref")
        _require(push_event["repository"] == REPOSITORY and push_event["ref"] == "refs/heads/develop",
                 "wrong develop push event")


def _merge_decision(state, action, reason, **details):
    required = {"REQUEST_REVIEW_1": {"delegate"}, "REQUEST_REVIEW_2": {"delegate"},
                "PUSH_CANDIDATE": {"push", "pr"}, "READY_FOR_REVIEW": {"ready", "pr"},
                "MERGE_PROPOSAL": {"merge"}}.get(action, set())
    missing = required - set(state["authorization"]["operations"])
    if missing:
        return _decision(state, "STOP", f"sixth-round action lacks authorization: {sorted(missing)}")
    return _decision(state, action, reason, **details)


def next_merge_action(state):
    """Read-only stage decisions; source refs and flags need external raw verification."""
    try:
        validate_merge_state(state)
    except (EvidenceError, TypeError, KeyError) as exc:
        return {"action": "STOP", "reason": f"invalid evidence: {exc}"}
    auth, current, pr, remote = (state[name] for name in ("authorization", "current", "pr", "remote"))
    head = current["head"]
    if not auth["active"]:
        return _merge_decision(state, "STOP", "sixth-round user authorization is absent or revoked")
    if not current["working_tree_clean"]:
        return _merge_decision(state, "STOP", "candidate working tree is not clean")
    if any(f["status"] != "verified_on_candidate" for f in state["continuation"]["known_findings"]):
        return _merge_decision(state, "STOP", "known finding lacks sixth-candidate verification")
    if not pr["findings_checked"]:
        return _merge_decision(state, "REFRESH_EVIDENCE", "latest PR findings have not been checked")
    if pr["new_blockers"]:
        return _merge_decision(state, "STOP", "confirmed code finding after C requires unauthorized seventh round")
    for number in (1, 2):
        report = _review(state, number, DEVELOP, head)
        if report is not None and report["verdict"] == "BLOCKED" and report["blocker_kind"] == "code":
            return _merge_decision(state, "STOP", "sixth round exhausted; confirmed code finding needs seventh round",
                                   blocked_report_ref=report["report_ref"])
    if (any(check["status"] != "success" for check in state["local_validation"]["checks"].values()) or
            state["local_validation"]["performance"]["status"] == "missing"):
        return _merge_decision(state, "REFRESH_EVIDENCE", "candidate local acceptance evidence is incomplete")
    if pr["merged"]:
        if pr["state"] != "closed" or pr["draft"] or state["ready_transition"] is None:
            return _merge_decision(state, "STOP", "actual merge is inconsistent with ready PR history")
        for number in (1, 2):
            report = _review(state, number, DEVELOP, head)
            if report is None or report["verdict"] != "PASS":
                return _merge_decision(state, "STOP", "merged record lacks both applicable independent PASS reports")
        ci = state["pr_ci"]
        if ci is None:
            return _merge_decision(state, "STOP", "merged record lacks candidate PR CI")
        problem = _ci_problem(ci, "pull_request", BRANCH, head, [DEVELOP, head])
        if problem:
            return _merge_decision(state, *problem)
        if ci["tree"] != current["tree"]:
            return _merge_decision(state, "REFRESH_EVIDENCE", "merged record has mismatched PR CI tree")
        merge = state["merge"]
        if merge is None:
            return _merge_decision(state, "VERIFY_MERGE", "read actual PR merge record and Git object")
        if (merge["expected_base"], merge["expected_head"], merge["parents"], merge["tree"]) != (
                DEVELOP, head, [DEVELOP, head], current["tree"]):
            return _merge_decision(state, "STOP", "actual merge parents/tree differ from reviewed D/C")
        if (pr["merge_commit_sha"] != merge["sha"] or pr["head_sha"] != head or
                (remote["head_ref_exists"] and remote["head"] != head)):
            return _merge_decision(state, "REFRESH_EVIDENCE", "PR or direct refs do not identify actual merge")
        if pr["base_sha"] not in (DEVELOP, merge["sha"], remote["base"]):
            return _merge_decision(state, "REFRESH_EVIDENCE", "merged PR API base is unrelated to verified develop")
        ancestry = state["post_merge_ancestry"]
        if remote["base"] == merge["sha"]:
            if ancestry is not None:
                return _merge_decision(state, "STOP", "direct merge tip must not claim later ancestry")
        elif ancestry is None:
            return _merge_decision(state, "REFRESH_EVIDENCE", "advanced develop needs Git first-parent object evidence")
        elif (ancestry["tip"], ancestry["merge_sha"]) != (remote["base"], merge["sha"]):
            return _merge_decision(state, "STOP", "advanced develop object chain has wrong tip or merge")
        push_event, push_ci = state["push_event"], state["push_ci"]
        if push_event is None or push_ci is None or push_ci["status"] in ("queued", "in_progress"):
            return _merge_decision(state, "WAIT_PUSH_CI", "actual merge SHA needs develop push CI")
        if (push_event["before_sha"], push_event["after_sha"]) != (DEVELOP, merge["sha"]):
            return _merge_decision(state, "STOP", "push event does not span D to the actual merge")
        problem = _ci_problem(push_ci, "push", "develop", merge["sha"], [DEVELOP, head])
        if problem:
            return _merge_decision(state, *problem)
        if push_ci["tree"] != current["tree"]:
            return _merge_decision(state, "REFRESH_EVIDENCE", "post-merge CI tree differs from C")
        return _merge_decision(state, "COMPLETE", "actual merge and its own develop push CI verified",
                               merge_sha=merge["sha"], candidate_head=head)
    if (pr["state"] != "open" or state["merge"] is not None or
            state["post_merge_ancestry"] is not None or state["push_event"] is not None or
            state["push_ci"] is not None):
        return _merge_decision(state, "STOP", "unmerged PR cannot carry merged/push evidence")
    if not remote["head_ref_exists"]:
        return _merge_decision(state, "STOP", "original feature ref disappeared before merge")
    if remote["base"] != DEVELOP or pr["base_sha"] != DEVELOP or remote["head"] not in (FIFTH_HEAD, head):
        return _merge_decision(state, "STOP", "external base or feature ref drifted from fixed D/N/C")
    before_push = remote["head"] == FIFTH_HEAD
    if pr["head_sha"] != remote["head"]:
        return _merge_decision(state, "REFRESH_EVIDENCE", "direct feature ref and original PR API disagree")
    if before_push and (not pr["draft"] or state["pr_ci"] is not None or
                        state["ready_transition"] is not None or
                        any(review["round"] == 2 for review in state["reviews"])):
        return _merge_decision(state, "STOP", "C is unpublished; old CI or ready state cannot apply")
    if not before_push and pr["draft"] and state["ready_transition"] is not None:
        return _merge_decision(state, "STOP", "ready transition cannot coexist with draft PR")
    first = _review(state, 1, DEVELOP, head)
    if first is not None and first["verdict"] == "BLOCKED":
        kind = first["blocker_kind"]
        return _merge_decision(state, "REFRESH_EVIDENCE" if kind == "evidence" else "STOP",
                               f"round one BLOCKED ({kind})", blocked_report_ref=first["report_ref"])
    if first is None:
        return _merge_decision(state, "STOP" if 1 in state["unavailable_review_rounds"] else
                               "REQUEST_REVIEW_1", "independent B-to-C full cumulative review required")
    if before_push:
        return _merge_decision(state, "PUSH_CANDIDATE", "round one and local checks passed; recheck D/N before ordinary push",
                               expected_remote_head=FIFTH_HEAD, expected_base_sha=DEVELOP,
                               candidate_head=head)
    ci = state["pr_ci"]
    if ci is None or ci["status"] in ("queued", "in_progress"):
        return _merge_decision(state, "WAIT_PR_CI", "new C PR CI is required")
    problem = _ci_problem(ci, "pull_request", BRANCH, head, [DEVELOP, head])
    if problem:
        return _merge_decision(state, *problem)
    if ci["tree"] != current["tree"]:
        return _merge_decision(state, "REFRESH_EVIDENCE", "PR CI tested a different integration tree")
    second = _review(state, 2, DEVELOP, head)
    if second is not None and second["verdict"] == "BLOCKED":
        kind = second["blocker_kind"]
        return _merge_decision(state, "REFRESH_EVIDENCE" if kind == "evidence" else "STOP",
                               f"round two BLOCKED ({kind})", blocked_report_ref=second["report_ref"])
    if second is None:
        return _merge_decision(state, "STOP" if 2 in state["unavailable_review_rounds"] else
                               "REQUEST_REVIEW_2", "independent full PR and exact latest CI review required")
    if pr["draft"]:
        return _merge_decision(state, "READY_FOR_REVIEW", "two reviews and CI passed; original draft PR may be marked ready",
                               pr_number=29, expected_base_sha=DEVELOP, expected_head_sha=head)
    if state["ready_transition"] is None:
        return _merge_decision(state, "REFRESH_EVIDENCE", "ready transition and fresh post-ready readback are missing")
    if not pr["mergeable"] or not pr["protection_satisfied"]:
        return _merge_decision(state, "STOP", "ready PR lacks mergeability or protection evidence")
    return _merge_decision(state, "MERGE_PROPOSAL", "fresh ready-state evidence supports authorized merge commit",
                           pr_number=29, merge_method="merge", expected_base_sha=DEVELOP,
                           expected_head_sha=head)


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
