"""Read-only PR29 fifth-round continuation; frozen v2 safety plus user addendum.

Derived from 1593e7af65596d320b4427f1b15bb2bc0bdc949c:scripts/pr_review_gate.py.
The ordinary gate is unchanged. Historical gaps are never review evidence.

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


SCHEMA = "zhuyin-pr29-continuation/1"
BASELINE = "1593e7af65596d320b4427f1b15bb2bc0bdc949c"
START = "a38bdf1c84889c52e7281a0c23c9458e3762afa8"
DEVELOP = "502414b3b38e004a6d8d9cb693cf21b65148765a"
TASK = "review-confirm-responsive"
BRANCH = "codex/review-confirm-responsive"
PR_URL = "https://github.com/discoveryray/zhuyin-proofreader/pull/29"
AUTHORIZATION_REF = "pr29-round5/user-adopted-contract"
AUTHORIZATION_SHA256 = "53790b7433d64197d046dbaad0e61c6ebb0ad5775a1d5ad3ec36e142371a0b26"
HISTORY = (
    "e53f44d2da29b46dfe0bd0f2c06af9919808b99f",
    "89e0297bd6e5b9b08d077b2216029fb6224976fc",
    "bb4c136fbc49c706fecfd48d1dacf81b4d97584e",
    "bd6b4789a646f8bed075c256447f54922a5c4f90",
    START,
)
LOCAL_CHECKS = ("unittest", "pytest", "gui", "runtime", "compile", "diff", "performance")
KNOWN_FINDING_IDS = {*(f"KF-{number:02d}" for number in range(1, 8)),
                     *(f"RC-{number:02d}" for number in range(1, 5))}
REPOSITORY = "discoveryray/zhuyin-proofreader"
WORKFLOW = ".github/workflows/ci.yml"
PYTHONS = ("3.12", "3.13")
OPERATIONS = {"implement", "delegate", "test", "commit", "push", "pr"}
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
