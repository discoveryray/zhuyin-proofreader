"""Isolated contract tests: synthetic evidence never authorizes real writes."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts import pr_review_gate as gate


ROOT = Path(__file__).resolve().parents[1]
BASE = "a" * 40
HEAD = "b" * 40
SYNTHETIC = "c" * 40
TREE = "d" * 40
MERGE = "e" * 40
NEW = "f" * 40


def ci_evidence(event="pull_request"):
    """Artificial CI record for tests only; not evidence of any real CI run."""
    tested = SYNTHETIC if event == "pull_request" else MERGE
    whitespace = "pull request" if event == "pull_request" else "push"
    return {
        "event": event, "branch": "feat/example" if event == "pull_request" else "develop",
        "workflow": gate.WORKFLOW, "run_id": 100, "attempt": 1,
        "latest_run_id": 100, "latest_attempt": 1,
        "head_sha": HEAD if event == "pull_request" else MERGE,
        "tested_sha": tested, "parents": [BASE, HEAD], "tree": TREE,
        "status": "completed", "conclusion": "success", "url": "fixture://ci/100",
        "evidence_ref": "fixture://raw-run-and-git-metadata",
        "jobs": [{
            "name": f"Python {version}", "runner": "windows-2025", "python_version": "3.13.0",
            "run_id": 100, "attempt": 1, "tested_sha": tested, "conclusion": "success",
            "steps": {step: "success" for step in (*gate.COMMON_STEPS,
                       f"Check committed whitespace ({whitespace})")},
            "evidence_ref": f"fixture://job-log/{version}",
        } for version in gate.PYTHONS],
    }


def review_evidence(number):
    return {
        "round": number, "reviewer": f"independent-agent-{number}",
        "baseline": BASE, "base": BASE, "head": HEAD,
        "scope": "baseline_to_head" if number == 1 else "cumulative_and_full_pr_with_ci",
        "verdict": "PASS", "independent": True, "full_diff_reviewed": True, "review_mode": "full_diff",
        "findings": [], "report_ref": f"fixture://independent-report/{number}",
        "blocker_kind": None, "supersedes_report_ref": None, "resolution_evidence_ref": None,
        "ci": None if number == 1 else {"run_id": 100, "attempt": 1, "tested_sha": SYNTHETIC},
    }


def block_review(review, kind="code"):
    review.update(verdict="BLOCKED", blocker_kind=kind, findings=[f"confirmed {kind} finding; original report retained"])


def supplement(review, suffix="supplement"):
    result = deepcopy(review)
    result.update(verdict="PASS", blocker_kind=None, findings=[],
                  reviewer=review["reviewer"] + "-" + suffix,
                  report_ref=review["report_ref"] + "/" + suffix,
                  supersedes_report_ref=review["report_ref"],
                  resolution_evidence_ref="fixture://original-resolution/" + suffix)
    return result


def evidence_state():
    """Return a synthetic merge-ready state for adversarial contract tests."""
    return {
        "schema": gate.SCHEMA,
        "task": {"id": "isolated-test", "repository": gate.REPOSITORY, "baseline": BASE,
                 "base_branch": "develop", "head_branch": "feat/example"},
        "authorization": {"task_id": "isolated-test", "active": True,
                          "operations": sorted(gate.OPERATIONS), "source_ref": "fixture://user-task"},
        "current": {"base": BASE, "head": HEAD, "working_tree_clean": True, "evidence_ref": "fixture://fetch"},
        "implementers": ["implementation-agent", "coordinator-who-edited-docs"],
        "corrections": [], "reviews": [review_evidence(1), review_evidence(2)],
        "unavailable_review_rounds": [],
        "pr": {"number": 20, "url": "fixture://pr/20", "state": "open", "base_branch": "develop",
               "head_branch": "feat/example", "base_sha": BASE, "head_sha": HEAD,
               "mergeable": True, "protection_satisfied": True, "findings_checked": True,
               "new_blockers": [], "evidence_ref": "fixture://fresh-pr-query"},
        "pr_ci": ci_evidence(), "merge": None, "push_ci": None,
        "handoffs": [{"from": "implementation-agent", "to": "independent-agent-1",
                      "head": HEAD, "evidence_ref": "fixture://delegation-log"}],
    }


def merged_state():
    state = evidence_state()
    state["pr"]["state"] = "merged"
    state["current"]["base"] = MERGE
    state["merge"] = {"sha": MERGE, "parents": [BASE, HEAD], "tree": TREE,
                      "develop_head": MERGE, "develop_contains_merge": True,
                      "evidence_ref": "fixture://actual-merge-object-and-fetch"}
    state["push_ci"] = ci_evidence("push")
    return state


class FakeService:
    """The coordinator's lookup/recheck contract, with no external effects."""

    def __init__(self):
        self.pr = None
        self.creations = 0
        self.merges = 0

    def reconcile(self, state):
        decision = gate.next_action(state)
        if decision["action"] == "ENSURE_PR":
            # Search even if local evidence is absent (including crash recovery).
            if self.pr is None:
                self.pr = evidence_state()["pr"]
                self.creations += 1
            state["pr"] = deepcopy(self.pr)
        elif decision["action"] == "MERGE_PROPOSAL":
            # Real coordinator must repeat these checks against the server.
            if self.pr is not None and self.pr["state"] == "merged":
                state["pr"] = deepcopy(self.pr)
                return
            if self.pr is None:
                self.pr = deepcopy(state["pr"])
            if (self.pr["base_sha"], self.pr["head_sha"]) != (
                    decision["expected_base_sha"], decision["expected_head_sha"]):
                raise RuntimeError("remote scope changed")
            self.pr["state"] = "merged"
            self.merges += 1
            state["pr"] = deepcopy(self.pr)


class ReviewGateTests(unittest.TestCase):
    def assertAction(self, state, expected):
        decision = gate.next_action(state)
        self.assertEqual(decision["action"], expected, decision)
        return decision

    def test_same_reviewer_may_close_only_evidence_gap_at_unchanged_scope(self):
        for number in (1, 2):
            state = evidence_state()
            old = state["reviews"][number - 1]
            block_review(old, "evidence")
            fresh = supplement(old)
            fresh.update(reviewer=old["reviewer"], review_mode="evidence_gap", full_diff_reviewed=False)
            state["reviews"].append(fresh)
            self.assertAction(state, "MERGE_PROPOSAL")
            # A chain remains rooted in the retained full review, without inventing a reread.
            block_review(fresh, "capability")
            latest = supplement(fresh)
            latest["reviewer"] = fresh["reviewer"]
            state["reviews"].append(latest)
            self.assertAction(state, "MERGE_PROPOSAL")

    def test_gap_review_cannot_replace_initial_review_or_change_reviewer_or_scope(self):
        for field, value in (("reviewer", "another-agent"), ("head", NEW),
                             ("base", NEW), ("baseline", NEW), ("scope", "partial"),
                             ("resolution_evidence_ref", None), ("supersedes_report_ref", None)):
            with self.subTest(field=field):
                state = evidence_state()
                old = state["reviews"][0]
                block_review(old, "evidence")
                fresh = supplement(old)
                fresh.update(reviewer=old["reviewer"], review_mode="evidence_gap", full_diff_reviewed=False)
                fresh[field] = value
                state["reviews"].append(fresh)
                self.assertAction(state, "STOP")

    def test_gap_review_never_clears_code_blocker_or_fabricates_full_review(self):
        state = evidence_state()
        old = state["reviews"][0]
        block_review(old, "code")
        fresh = supplement(old)
        fresh.update(reviewer=old["reviewer"], review_mode="evidence_gap", full_diff_reviewed=False)
        state["reviews"].append(fresh)
        self.assertAction(state, "STOP")
        block_review(old, "evidence")
        fresh["full_diff_reviewed"] = True
        self.assertAction(state, "STOP")

    def test_new_schema_does_not_silently_adopt_old_task_contract(self):
        state = evidence_state()
        state["schema"] = "zhuyin-pr-review-gate/2"
        self.assertAction(state, "STOP")

    def test_pass_progresses_through_both_reviews_and_post_merge(self):
        state = evidence_state()
        state.update(pr=None, pr_ci=None, reviews=[])
        self.assertAction(state, "REQUEST_REVIEW_1")
        state["reviews"].append(review_evidence(1))
        self.assertAction(state, "ENSURE_PR")
        state["pr"] = evidence_state()["pr"]
        self.assertAction(state, "WAIT_PR_CI")
        state["pr_ci"] = ci_evidence()
        self.assertAction(state, "REQUEST_REVIEW_2")
        state["reviews"].append(review_evidence(2))
        decision = self.assertAction(state, "MERGE_PROPOSAL")
        self.assertEqual((decision["merge_method"], decision["expected_base_sha"],
                          decision["expected_head_sha"]), ("merge", BASE, HEAD))
        state["pr"]["state"] = "merged"
        self.assertAction(state, "VERIFY_MERGE")
        state = merged_state()
        state["push_ci"] = None
        self.assertAction(state, "WAIT_PUSH_CI")
        state["push_ci"] = ci_evidence("push")
        decision = self.assertAction(state, "COMPLETE")
        self.assertEqual(decision["merge_sha"], MERGE)
        self.assertNotEqual(MERGE, SYNTHETIC)

    def test_blocked_either_round_prevents_merge(self):
        for number in (1, 2):
            with self.subTest(round=number):
                state = evidence_state()
                block_review(state["reviews"][number - 1])
                state["reviews"][number - 1]["findings"] = ["blocking finding"]
                self.assertAction(state, "CORRECT_IMPLEMENTATION")

    def test_three_corrections_are_a_task_wide_limit(self):
        state = evidence_state()
        block_review(state["reviews"][0])
        chain = ["1" * 40, "2" * 40, "3" * 40, HEAD]
        for count in range(4):
            state["corrections"] = [{"number": n + 1, "from_head": chain[n], "to_head": chain[n + 1],
                                     "evidence_ref": f"fixture://correction/{n + 1}"}
                                    for n in range(count)]
            self.assertAction(state, "STOP" if count == 3 else "CORRECT_IMPLEMENTATION")

    def test_corrective_head_requires_two_fresh_full_reviews(self):
        state = evidence_state()
        state["current"]["head"] = state["pr"]["head_sha"] = NEW
        self.assertAction(state, "REQUEST_REVIEW_1")
        first = review_evidence(1)
        first["head"] = NEW
        first["report_ref"] += "/new-head"
        state["reviews"].append(first)
        state["pr_ci"]["head_sha"] = NEW
        state["pr_ci"]["parents"] = [BASE, NEW]
        self.assertAction(state, "REQUEST_REVIEW_2")

    def test_base_change_invalidates_old_passes_without_changing_baseline(self):
        state = evidence_state()
        state["current"]["base"] = state["pr"]["base_sha"] = NEW
        self.assertAction(state, "REQUEST_REVIEW_1")
        self.assertEqual(state["task"]["baseline"], BASE)

    def test_remote_head_or_base_drift_cannot_merge(self):
        for field in ("base_sha", "head_sha"):
            state = evidence_state()
            state["pr"][field] = NEW
            self.assertAction(state, "REFRESH_EVIDENCE")

    def test_review_must_cover_original_cumulative_baseline(self):
        state = evidence_state()
        state["reviews"][0]["baseline"] = NEW
        self.assertAction(state, "REQUEST_REVIEW_1")

    def test_unavailable_or_missing_review_never_becomes_pass(self):
        for number in (1, 2):
            state = evidence_state()
            state["reviews"] = [r for r in state["reviews"] if r["round"] != number]
            self.assertAction(state, f"REQUEST_REVIEW_{number}")
            state["unavailable_review_rounds"] = [number]
            self.assertAction(state, "STOP")

    def test_review_separation_and_direct_review_are_required(self):
        changes = [
            (0, "reviewer", "implementation-agent"), (1, "reviewer", "coordinator-who-edited-docs"),
            (1, "reviewer", "independent-agent-1"), (1, "report_ref", "fixture://independent-report/1"),
            (0, "independent", False), (1, "full_diff_reviewed", False),
            (0, "scope", "implementation_summary"), (1, "scope", "CI_green_only"),
            (0, "findings", ["unresolved blocker"]),
        ]
        for index, field, value in changes:
            with self.subTest(index=index, field=field, value=value):
                state = evidence_state()
                state["reviews"][index][field] = value
                self.assertAction(state, "STOP")

    def test_pr_ci_failure_or_missing_step_never_merges(self):
        for version_index in range(len(gate.PYTHONS)):
            for step in (*gate.COMMON_STEPS, "Check committed whitespace (pull request)"):
                for outcome in (None, "failure", "skipped", "cancelled"):
                    with self.subTest(job=version_index, step=step, outcome=outcome):
                        state = evidence_state()
                        state["pr_ci"]["jobs"][version_index]["steps"][step] = outcome
                        self.assertAction(state, "INVESTIGATE_CI")
        state = evidence_state()
        state["pr_ci"]["conclusion"] = "failure"
        self.assertAction(state, "INVESTIGATE_CI")

    def test_ci_requires_exact_run_event_attempt_checkout_and_parents(self):
        for field, value in (("event", "push"), ("branch", "develop"), ("workflow", "other.yml"),
                             ("head_sha", NEW), ("tested_sha", HEAD), ("parents", [HEAD, BASE]),
                             ("latest_run_id", 101), ("latest_attempt", 2)):
            with self.subTest(field=field):
                state = evidence_state()
                state["pr_ci"][field] = value
                self.assertAction(state, "REFRESH_EVIDENCE")

    def test_each_job_requires_its_own_matching_checkout_attempt_and_windows_python(self):
        for field, value in (("runner", "ubuntu-latest"), ("python_version", "3.11.10"),
                             ("run_id", 99), ("attempt", 2), ("tested_sha", HEAD),
                             ("conclusion", "skipped")):
            with self.subTest(field=field):
                state = evidence_state()
                state["pr_ci"]["jobs"][0][field] = value
                self.assertAction(state, "INVESTIGATE_CI" if field == "conclusion" else "REFRESH_EVIDENCE")
        state = evidence_state()
        state["pr_ci"]["jobs"].pop()
        self.assertAction(state, "REFRESH_EVIDENCE")

    def test_new_ci_attempt_requires_new_round_two_attestation(self):
        state = evidence_state()
        state["pr_ci"]["attempt"] = state["pr_ci"]["latest_attempt"] = 2
        for job in state["pr_ci"]["jobs"]:
            job["attempt"] = 2
        self.assertAction(state, "REQUEST_REVIEW_2")

    def test_rerun_ci_does_not_erase_blocked_review_against_unchanged_code(self):
        state = evidence_state()
        block_review(state["reviews"][1])
        state["reviews"][1]["findings"] = ["unchanged code blocker"]
        state["pr_ci"]["attempt"] = state["pr_ci"]["latest_attempt"] = 2
        for job in state["pr_ci"]["jobs"]:
            job["attempt"] = 2
        fresh = review_evidence(2)
        fresh["ci"]["attempt"] = 2
        fresh["report_ref"] += "/new-ci"
        state["reviews"].append(fresh)
        self.assertAction(state, "CORRECT_IMPLEMENTATION")

    def test_pending_ci_is_not_success(self):
        for event in ("pull_request", "push"):
            state = evidence_state() if event == "pull_request" else merged_state()
            ci = state["pr_ci"] if event == "pull_request" else state["push_ci"]
            ci.update(status="in_progress", conclusion=None, jobs=[])
            self.assertAction(state, "WAIT_PR_CI" if event == "pull_request" else "WAIT_PUSH_CI")

    def test_pr_findings_and_protection_are_checked(self):
        for field in ("mergeable", "protection_satisfied"):
            state = evidence_state()
            state["pr"][field] = False
            self.assertAction(state, "STOP")
        state = evidence_state()
        state["pr"]["findings_checked"] = False
        self.assertAction(state, "REFRESH_EVIDENCE")
        state["pr"]["findings_checked"] = True
        state["pr"]["new_blockers"] = ["new substantive finding"]
        self.assertAction(state, "CORRECT_IMPLEMENTATION")

    def test_authorization_scope_and_clean_tree_are_required(self):
        state = evidence_state()
        state["authorization"]["active"] = False
        self.assertAction(state, "STOP")
        state = evidence_state()
        state["authorization"]["task_id"] = "another-task"
        self.assertAction(state, "STOP")
        state = evidence_state()
        state["current"]["working_tree_clean"] = False
        self.assertAction(state, "STOP")
        for field, value in (("base_branch", "main"), ("head_branch", "develop"),
                             ("repository", "other/repo")):
            state = evidence_state()
            state["task"][field] = value
            self.assertAction(state, "STOP")

    def test_authorization_only_through_pr_cannot_expand_to_merge_or_correction(self):
        state = evidence_state()
        state["authorization"]["operations"].remove("merge")
        self.assertAction(state, "STOP")
        state.update(pr=None, pr_ci=None, reviews=[review_evidence(1)])
        self.assertAction(state, "ENSURE_PR")
        state["authorization"]["operations"].remove("pr")
        self.assertAction(state, "STOP")
        state = evidence_state()
        block_review(state["reviews"][0])
        state["authorization"]["operations"].remove("commit")
        self.assertAction(state, "STOP")
        state = evidence_state()
        state["reviews"] = []
        state["authorization"]["operations"].remove("delegate")
        self.assertAction(state, "STOP")

    def test_ci_evidence_problems_do_not_consume_or_bypass_correction_budget(self):
        state = evidence_state()
        chain = ["1" * 40, "2" * 40, "3" * 40, HEAD]
        state["corrections"] = [{"number": n + 1, "from_head": chain[n], "to_head": chain[n + 1],
                                 "evidence_ref": f"fixture://correction/{n + 1}"} for n in range(3)]
        original = deepcopy(state["corrections"])
        state["pr_ci"]["latest_attempt"] = 2
        self.assertAction(state, "REFRESH_EVIDENCE")
        state["pr_ci"]["latest_attempt"] = 1
        del state["pr_ci"]["jobs"][0]["steps"]["Run full pytest suite"]
        self.assertAction(state, "REFRESH_EVIDENCE")
        state["pr_ci"] = ci_evidence()
        state["pr_ci"]["conclusion"] = "failure"
        self.assertAction(state, "INVESTIGATE_CI")
        self.assertEqual(state["corrections"], original)
        # Only a confirmed review finding calls for a code correction, now exhausted.
        state["pr_ci"] = ci_evidence()
        block_review(state["reviews"][0])
        self.assertAction(state, "STOP")

    def test_confirmed_blockers_reach_correction_before_ci_success_with_one_task_limit(self):
        for source in ("pr", "round2"):
            for status in ("failure", "queued", "in_progress"):
                for count in range(4):
                    with self.subTest(source=source, status=status, corrections=count):
                        state = evidence_state()
                        if status == "failure":
                            state["pr_ci"]["conclusion"] = "failure"
                            state["pr_ci"]["jobs"][0]["conclusion"] = "failure"
                            state["pr_ci"]["jobs"][0]["steps"]["Run full pytest suite"] = "failure"
                        else:
                            state["pr_ci"].update(status=status, conclusion=None, jobs=[])
                        if source == "pr":
                            state["reviews"] = [state["reviews"][0]]
                            state["pr"]["new_blockers"] = ["confirmed current-scope code failure; raw logs retained"]
                        else:
                            block_review(state["reviews"][1])
                            state["reviews"][1]["findings"] = ["confirmed current-scope code failure"]
                        chain = ["1" * 40, "2" * 40, "3" * 40, HEAD]
                        state["corrections"] = [{"number": n + 1, "from_head": chain[n], "to_head": chain[n + 1],
                                                 "evidence_ref": f"fixture://correction/{n + 1}"}
                                                for n in range(count)]
                        before = deepcopy(state)
                        result = self.assertAction(state, "STOP" if count == 3 else "CORRECT_IMPLEMENTATION")
                        if count < 3:
                            self.assertEqual(result["correction_round"], count + 1)
                        self.assertEqual(state, before)

    def test_ci_blocker_correction_keeps_authorization_independence_and_evidence_checks(self):
        for source in ("pr", "round2"):
            state = evidence_state()
            state["pr_ci"]["conclusion"] = "failure"
            if source == "pr":
                state["reviews"] = [state["reviews"][0]]
                state["pr"]["new_blockers"] = ["confirmed CI code defect"]
            else:
                block_review(state["reviews"][1])
                state["reviews"][1]["findings"] = ["confirmed CI code defect"]
            for operation in ("implement", "test", "commit", "push", "delegate"):
                restricted = deepcopy(state)
                restricted["authorization"]["operations"].remove(operation)
                self.assertAction(restricted, "STOP")
            unconfirmed = deepcopy(state)
            unconfirmed["pr"]["findings_checked"] = False
            self.assertAction(unconfirmed, "REFRESH_EVIDENCE")
            if source == "round2":
                for field, value in (("reviewer", "implementation-agent"), ("reviewer", "independent-agent-1"),
                                     ("independent", False), ("full_diff_reviewed", False),
                                     ("scope", "summary_only"), ("report_ref", "")):
                    invalid = deepcopy(state)
                    invalid["reviews"][1][field] = value
                    self.assertAction(invalid, "STOP")

    def test_drift_invalidates_confirmed_blockers_before_any_correction(self):
        for changed in ("base", "head"):
            state = evidence_state()
            state["pr_ci"]["conclusion"] = "failure"
            state["pr"]["new_blockers"] = ["finding bound to old PR snapshot"]
            block_review(state["reviews"][1])
            state["reviews"][1]["findings"] = ["finding against old review scope"]
            state["current"][changed] = NEW
            self.assertAction(state, "REFRESH_EVIDENCE")
            # A newly fetched PR snapshot has no confirmed finding for its new scope.
            state["pr"][changed + "_sha"] = NEW
            state["pr"]["new_blockers"] = []
            self.assertAction(state, "REQUEST_REVIEW_1")
            fresh_first = review_evidence(1)
            fresh_first[changed] = NEW
            fresh_first["report_ref"] += "/new-scope"
            state["reviews"].append(fresh_first)
            # The old second-round BLOCKED cannot request correction for new code.
            self.assertAction(state, "REFRESH_EVIDENCE")

    def test_failed_ci_without_confirmed_finding_does_not_create_correction(self):
        state = evidence_state()
        state["reviews"] = [state["reviews"][0]]
        state["pr_ci"]["conclusion"] = "failure"
        self.assertAction(state, "INVESTIGATE_CI")
        self.assertEqual(state["corrections"], [])

    def test_non_code_blocked_never_requests_a_corrective_commit(self):
        for number in (1, 2):
            for kind in ("evidence", "capability", "contract"):
                for status in ("failure", "queued", "in_progress", "success"):
                    with self.subTest(round=number, kind=kind, ci=status):
                        state = evidence_state()
                        block_review(state["reviews"][number - 1], kind)
                        if status in ("failure", "success"):
                            state["pr_ci"]["conclusion"] = status
                        else:
                            state["pr_ci"].update(status=status, conclusion=None, jobs=[])
                        before = deepcopy(state)
                        result = self.assertAction(state, "REFRESH_EVIDENCE" if kind == "evidence" else "STOP")
                        self.assertNotIn("correction_round", result)
                        self.assertEqual(state, before)

    def test_append_only_non_code_supplement_can_resume_at_unchanged_head(self):
        for number in (1, 2):
            for kind in ("evidence", "capability", "contract"):
                for attempt in (1, 2):
                    with self.subTest(round=number, kind=kind, attempt=attempt):
                        state = evidence_state()
                        old = state["reviews"][number - 1]
                        block_review(old, kind)
                        original = deepcopy(old)
                        fresh = supplement(old)
                        if number == 2:
                            state["pr_ci"]["attempt"] = state["pr_ci"]["latest_attempt"] = attempt
                            for job in state["pr_ci"]["jobs"]:
                                job["attempt"] = attempt
                            fresh["ci"]["attempt"] = attempt
                        state["reviews"].append(fresh)
                        self.assertAction(state, "MERGE_PROPOSAL")
                        self.assertEqual(state["reviews"][number - 1], original)
                        self.assertEqual((state["current"]["head"], state["task"]["baseline"], state["corrections"]),
                                         (HEAD, BASE, []))
                        self.assertEqual(len(state["reviews"]), 3)

    def test_evidence_blocked_can_record_unavailable_ci_then_supplement_real_attestation(self):
        state = evidence_state()
        old = state["reviews"][1]
        block_review(old, "evidence")
        old["ci"] = None
        state["pr_ci"] = None
        self.assertAction(state, "REFRESH_EVIDENCE")
        fresh = supplement(old)
        fresh["ci"] = {"run_id": 100, "attempt": 1, "tested_sha": SYNTHETIC}
        state["reviews"].append(fresh)
        self.assertAction(state, "WAIT_PR_CI")
        state["pr_ci"] = ci_evidence()
        self.assertAction(state, "MERGE_PROPOSAL")

    def test_unlinked_pass_or_ci_rerun_does_not_resolve_non_code_blocked(self):
        for number in (1, 2):
            state = evidence_state()
            old = state["reviews"][number - 1]
            block_review(old, "evidence")
            fresh = review_evidence(number)
            fresh["report_ref"] += "/unlinked"
            state["reviews"].append(fresh)
            self.assertAction(state, "STOP")  # ambiguous same-scope reports
            if number == 2:
                fresh["ci"]["attempt"] = 2
                state["pr_ci"]["attempt"] = state["pr_ci"]["latest_attempt"] = 2
                for job in state["pr_ci"]["jobs"]:
                    job["attempt"] = 2
                self.assertAction(state, "REFRESH_EVIDENCE")  # old BLOCKED still active

    def test_supersession_rejects_missing_resolution_scope_changes_and_invalid_reviewers(self):
        mutations = (("supersedes_report_ref", "fixture://missing-report"),
                     ("resolution_evidence_ref", None), ("resolution_evidence_ref", ""),
                     ("reviewer", "implementation-agent"), ("independent", False),
                     ("full_diff_reviewed", False), ("scope", "summary_only"),
                     ("head", NEW), ("base", NEW), ("baseline", NEW))
        for number in (1, 2):
            for field, value in mutations:
                with self.subTest(round=number, field=field):
                    state = evidence_state()
                    old = state["reviews"][number - 1]
                    block_review(old, "evidence")
                    fresh = supplement(old)
                    fresh[field] = value
                    state["reviews"].append(fresh)
                    self.assertAction(state, "STOP")
            state = evidence_state()
            old = state["reviews"][number - 1]
            block_review(old, "evidence")
            fresh = supplement(old)
            # A late definition, self-reference, and two children are all rejected.
            state["reviews"].insert(0, fresh)
            self.assertAction(state, "STOP")
            state["reviews"].pop(0)
            fresh["supersedes_report_ref"] = fresh["report_ref"]
            state["reviews"].append(fresh)
            self.assertAction(state, "STOP")
            state["reviews"][-1] = supplement(old)
            state["reviews"].append(supplement(old, "fork"))
            self.assertAction(state, "STOP")

    def test_invalid_predecessor_or_cross_round_supersession_cannot_hide_independence_failure(self):
        for field, value in (("reviewer", "implementation-agent"), ("independent", False),
                             ("full_diff_reviewed", False), ("reviewer", "independent-agent-1")):
            state = evidence_state()
            old = state["reviews"][1]
            block_review(old, "evidence")
            fresh = supplement(old)
            old[field] = value
            state["reviews"].append(fresh)
            self.assertAction(state, "STOP")
        state = evidence_state()
        block_review(state["reviews"][0], "evidence")
        state["reviews"][1].update(supersedes_report_ref=state["reviews"][0]["report_ref"],
                                   resolution_evidence_ref="fixture://resolution")
        self.assertAction(state, "STOP")

    def test_supplement_does_not_bypass_current_ci_or_clear_confirmed_code(self):
        for number in (1, 2):
            state = evidence_state()
            old = state["reviews"][number - 1]
            block_review(old)
            state["reviews"].append(supplement(old))
            self.assertAction(state, "STOP")
        state = evidence_state()
        old = state["reviews"][1]
        block_review(old, "evidence")
        state["reviews"].append(supplement(old))
        state["pr_ci"]["conclusion"] = "failure"
        self.assertAction(state, "INVESTIGATE_CI")
        state["pr_ci"]["conclusion"] = "success"
        state["pr_ci"]["attempt"] = state["pr_ci"]["latest_attempt"] = 2
        for job in state["pr_ci"]["jobs"]:
            job["attempt"] = 2
        self.assertAction(state, "REQUEST_REVIEW_2")

    def test_supplement_chain_preserves_exhausted_budget_merge_and_replay(self):
        state = merged_state()
        old = state["reviews"][1]
        block_review(old, "evidence")
        chain = ["1" * 40, "2" * 40, "3" * 40, HEAD]
        state["corrections"] = [{"number": n + 1, "from_head": chain[n], "to_head": chain[n + 1],
                                 "evidence_ref": f"fixture://correction/{n + 1}"} for n in range(3)]
        before_merge, before_history = deepcopy(state["merge"]), deepcopy(state["corrections"])
        self.assertAction(state, "REFRESH_EVIDENCE")
        middle = supplement(old)
        block_review(middle, "capability")
        state["reviews"].append(middle)
        result = self.assertAction(state, "STOP")
        self.assertNotIn("exhausted", result["reason"])
        state["reviews"].append(supplement(middle, "resolved"))
        self.assertAction(state, "COMPLETE")
        service = FakeService()
        service.pr = deepcopy(state["pr"])
        service.reconcile(state)
        service.reconcile(state)
        self.assertEqual((service.creations, service.merges), (0, 0))
        self.assertEqual((state["merge"], state["corrections"]), (before_merge, before_history))
        self.assertEqual((state["task"]["baseline"], len(state["reviews"])), (BASE, 4))

    def test_blocker_classification_is_explicit_and_schema_one_is_not_guessed(self):
        for kind in (None, "unknown", "code", "evidence"):
            state = evidence_state()
            state["reviews"][0].update(verdict="BLOCKED", blocker_kind=kind, findings=[])
            self.assertAction(state, "STOP")
        state = evidence_state()
        state["reviews"][0]["blocker_kind"] = "code"
        self.assertAction(state, "STOP")
        state = evidence_state()
        block_review(state["reviews"][0], "unknown")
        self.assertAction(state, "STOP")
        for field in ("blocker_kind", "supersedes_report_ref", "resolution_evidence_ref"):
            state = evidence_state()
            del state["reviews"][0][field]
            self.assertAction(state, "STOP")
        state = evidence_state()
        state["reviews"][0]["resolution_evidence_ref"] = "fixture://unlinked-resolution"
        self.assertAction(state, "STOP")
        state = evidence_state()
        state["reviews"].append(supplement(state["reviews"][0]))
        self.assertAction(state, "STOP")  # an existing PASS is not a non-code BLOCKED target
        state = evidence_state()
        state["schema"] = "zhuyin-pr-review-gate/1"
        self.assertAction(state, "STOP")

    def test_actual_merge_must_have_reviewed_parents_tree_and_ancestry(self):
        for field, value in (("parents", [HEAD, BASE]), ("parents", [BASE]), ("tree", NEW),
                             ("develop_head", HEAD), ("develop_contains_merge", False)):
            with self.subTest(field=field, value=value):
                state = merged_state()
                state["merge"][field] = value
                self.assertAction(state, "STOP")

    def test_pr_ci_cannot_substitute_for_actual_merge_push_ci(self):
        state = merged_state()
        state["push_ci"] = deepcopy(state["pr_ci"])
        self.assertAction(state, "STOP")
        for field, value in (("head_sha", SYNTHETIC), ("tested_sha", SYNTHETIC),
                             ("event", "workflow_dispatch"), ("tree", NEW), ("conclusion", "failure")):
            state = merged_state()
            state["push_ci"][field] = value
            self.assertAction(state, "STOP")

    def test_develop_advancement_is_reported_separately_from_verified_merge(self):
        state = merged_state()
        state["merge"]["develop_head"] = state["current"]["base"] = NEW
        result = self.assertAction(state, "COMPLETE")
        self.assertEqual(result["merge_sha"], MERGE)
        self.assertEqual(result["develop_head"], NEW)

    def test_post_merge_blockers_stop_completion_with_same_task_handoff_and_history(self):
        for source in ("pr", "round1", "round2"):
            for count in range(4):
                with self.subTest(source=source, corrections=count):
                    state = merged_state()
                    if source == "pr":
                        state["pr"]["new_blockers"] = ["confirmed defect in actual merged scope"]
                    else:
                        review = state["reviews"][int(source[-1]) - 1]
                        block_review(review)
                        review["findings"] = ["confirmed defect in actual merged scope"]
                    chain = ["1" * 40, "2" * 40, "3" * 40, HEAD]
                    state["corrections"] = [{"number": n + 1, "from_head": chain[n], "to_head": chain[n + 1],
                                             "evidence_ref": f"fixture://correction/{n + 1}"}
                                            for n in range(count)]
                    before = deepcopy(state)
                    result = self.assertAction(state, "STOP")
                    self.assertIn("post-merge", result["reason"])
                    self.assertEqual((result["task_id"], result["baseline"], result["merge_sha"],
                                      result["corrections_used"], result["correction_limit"]),
                                     (state["task"]["id"], BASE, MERGE, count, 3))
                    self.assertIn("do not re-merge, push develop, or revert", result["handoff"])
                    service = FakeService()
                    service.pr = deepcopy(state["pr"])
                    service.reconcile(state)
                    service.reconcile(state)
                    self.assertEqual((service.creations, service.merges), (0, 0))
                    self.assertEqual(state, before)

    def test_post_merge_findings_must_be_checked_before_completion(self):
        state = merged_state()
        state["pr"]["findings_checked"] = False
        self.assertAction(state, "REFRESH_EVIDENCE")
        self.assertEqual(state["merge"]["sha"], MERGE)
        state["pr"]["findings_checked"] = True
        self.assertAction(state, "COMPLETE")

    def test_replay_and_uncertain_response_do_not_duplicate_pr_or_merge(self):
        state = evidence_state()
        state.update(pr=None, pr_ci=None, reviews=[review_evidence(1)])
        service = FakeService()
        service.reconcile(state)
        service.reconcile(state)
        self.assertEqual(service.creations, 1)
        # Simulate a crash losing the local acknowledgement, with remote PR kept.
        state["pr"] = None
        service.reconcile(state)
        self.assertEqual(service.creations, 1)
        state["pr_ci"] = ci_evidence()
        state["reviews"].append(review_evidence(2))
        before_merge = deepcopy(state)
        service.reconcile(state)
        service.reconcile(state)
        service.reconcile(before_merge)  # stale local state after uncertain response
        self.assertEqual(service.merges, 1)
        self.assertAction(state, "VERIFY_MERGE")

    def test_coordinator_must_recheck_remote_base_and_head_before_merge(self):
        for field in ("base_sha", "head_sha"):
            state = evidence_state()
            service = FakeService()
            service.pr = deepcopy(state["pr"])
            service.pr[field] = NEW
            with self.assertRaisesRegex(RuntimeError, "remote scope changed"):
                service.reconcile(state)
            self.assertEqual(service.merges, 0)

    def test_closed_pr_or_mismatched_pair_does_not_create_replacement(self):
        for field, value in (("state", "closed"), ("head_branch", "feat/other"), ("base_branch", "main")):
            state = evidence_state()
            state["pr"][field] = value
            self.assertAction(state, "STOP")

    def test_decisions_are_deterministic_and_input_is_not_modified(self):
        state = evidence_state()
        before = deepcopy(state)
        self.assertEqual(gate.next_action(state), gate.next_action(state))
        self.assertEqual(state, before)

    def test_closed_schema_rejects_missing_unknown_duplicate_or_malformed_data(self):
        for field in evidence_state():
            state = evidence_state()
            del state[field]
            self.assertAction(state, "STOP")
        for item in (None, [], {}, {"schema": "future"}):
            self.assertAction(item, "STOP")
        for container in ("task", "authorization", "current"):
            state = evidence_state()
            state[container]["unknown"] = True
            self.assertAction(state, "STOP")
        state = evidence_state()
        state["reviews"].append(deepcopy(state["reviews"][0]))
        self.assertAction(state, "STOP")
        state = evidence_state()
        state["pr_ci"]["jobs"].append(deepcopy(state["pr_ci"]["jobs"][0]))
        self.assertAction(state, "STOP")
        state = evidence_state()
        state["pr_ci"]["attempt"] = True
        self.assertAction(state, "STOP")

    def test_cli_is_read_only_and_schema_validation_is_not_attestation(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "evidence.json"
            original = json.dumps(evidence_state()).encode()
            path.write_bytes(original)
            command = [sys.executable, str(ROOT / "scripts/pr_review_gate.py")]
            result = subprocess.run([*command, "validate", str(path)], capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(result.stdout), {"schema_valid": True, "authenticity_verified": False})
            result = subprocess.run([*command, "next-action", str(path)], capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(result.stdout)["action"], "MERGE_PROPOSAL")
            self.assertEqual(path.read_bytes(), original)
            for content in ('{"schema": 1, "schema": 1}', '{"schema": NaN}', '{"schema": Infinity}'):
                path.write_text(content, encoding="utf-8")
                result = subprocess.run([*command, "next-action", str(path)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(json.loads(result.stdout)["action"], "STOP")

    def test_gate_required_step_names_still_match_repository_workflow(self):
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        for step in (*gate.COMMON_STEPS, "Check committed whitespace (pull request)",
                     "Check committed whitespace (push)"):
            self.assertIn(f"- name: {step}\n", workflow)
        for version in gate.PYTHONS:
            self.assertIn(f"|| '[\"{version}\"]'", workflow)
        self.assertIn("&& '3.13.0'", workflow)
        self.assertIn("github.head_ref == 'codex/simplify-validation'", workflow)
        self.assertEqual(workflow.count("github.event.pull_request.base.sha == '1593e7af65596d320b4427f1b15bb2bc0bdc949c'"), 2)


if __name__ == "__main__":
    unittest.main()
