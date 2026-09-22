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


SCHEMA = "zhuyin-pr-review-gate/3"
REPOSITORY = "discoveryray/zhuyin-proofreader"
WORKFLOW = ".github/workflows/ci.yml"
PYTHONS = ("3.13",)
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


def validate_state(state):
    """Validate the closed input schema. This does not grant permission."""
    _fields(state, "schema task authorization current implementers corrections reviews "
            "unavailable_review_rounds pr pr_ci merge push_ci handoffs", "state")
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
    _require(type(state["corrections"]) is list and len(state["corrections"]) <= 3,
             "at most three correction rounds")
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
                "supersedes_report_ref resolution_evidence_ref review_mode", "review")
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
        "ENSURE_PR": {"pr"}, "MERGE_PROPOSAL": {"merge"},
        "CORRECT_IMPLEMENTATION": {"implement", "test", "commit", "push", "delegate"},
    }.get(action, set())
    missing = needed - set(state["authorization"]["operations"])
    if missing:
        action, reason, details = "STOP", f"next action is not authorized: {sorted(missing)}", {}
    identity = {"task": state["task"], "base": state["current"]["base"],
                "head": state["current"]["head"], "action": action}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest()
    return {"action": action, "reason": reason, "idempotency_key": key, **details}


def _correct(state, reason):
    count = len(state["corrections"])
    if count >= 3:
        return _decision(state, "STOP", "three corrective rounds exhausted: " + reason)
    return _decision(state, "CORRECT_IMPLEMENTATION", reason, correction_round=count + 1)


def _post_merge_blocked(state, reason, *, code=True):
    if code and len(state["corrections"]) >= 3:
        reason += "; three corrective rounds exhausted"
    return _decision(
        state, "STOP", "post-merge blocker: " + reason,
        handoff="retain this task ledger and correction limit; do not reset the task; "
                "do not re-merge, push develop, or revert",
        task_id=state["task"]["id"], baseline=state["task"]["baseline"],
        merge_sha=state["merge"]["sha"] if state["merge"] is not None else None,
        corrections_used=len(state["corrections"]), correction_limit=3,
    )


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
            _require(review["review_mode"] == "full_diff", "gap review needs a retained full-review chain")
        else:
            _require(prior in reports and prior not in superseded,
                     "supersession target must be earlier, retained, and not already superseded")
            old = reports[prior]
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
        if job["python_version"] != "3.13.0":
            return "REFRESH_EVIDENCE", f"wrong actual Python version: {name}"
        if (job["run_id"], job["attempt"], job["tested_sha"]) != (
                ci["run_id"], ci["attempt"], ci["tested_sha"]):
            return "REFRESH_EVIDENCE", f"job is from another run attempt or checkout: {name}"
        required = (*COMMON_STEPS, f"Check committed whitespace ({'pull request' if event == 'pull_request' else 'push'})")
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
            problem = _ci_problem(ci, "pull_request", state["task"]["head_branch"], head, [base, head])
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
        problem = _ci_problem(ci, "push", "develop", merge["sha"], [base, head])
        if problem or ci["tree"] != merge["tree"]:
            return _decision(state, "STOP", problem[1] if problem else "push CI tree does not match the actual merge")
        return _decision(state, "COMPLETE", "both reviews, PR CI, actual merge and post-merge CI verified",
                         merge_sha=merge["sha"], develop_head=merge["develop_head"])
    if not pr["mergeable"] or not pr["protection_satisfied"]:
        return _decision(state, "STOP", "mergeability or repository protection requirements are not satisfied")
    return _decision(state, "MERGE_PROPOSAL", "recheck remote base/head and protection immediately before write",
                     pr_number=pr["number"], merge_method="merge", expected_head_sha=head,
                     expected_base_sha=base)


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
