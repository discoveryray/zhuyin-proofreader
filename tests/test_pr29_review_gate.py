"""Adversarial PR29 contract tests; fixture records are never real evidence."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

from scripts import pr29_review_gate as gate
from scripts import pr_review_gate as general_gate

ROOT = Path(__file__).resolve().parents[1]
HEAD, TREE, TESTED = "a" * 40, "b" * 40, "c" * 40


def report(number):
    return {"round": number, "reviewer": "fresh-agent-" + str(number),
            "baseline": gate.BASELINE, "base": gate.DEVELOP, "head": HEAD,
            "scope": "baseline_to_head" if number == 1 else "cumulative_and_full_pr_with_ci",
            "verdict": "PASS", "blocker_kind": None, "independent": True,
            "full_diff_reviewed": True, "findings": [], "report_ref": "fixture://report/" + str(number),
            "supersedes_report_ref": None, "resolution_evidence_ref": None,
            "ci": None if number == 1 else {"run_id": 101, "attempt": 2, "tested_sha": TESTED}}


def state(before_push=False):
    chain = (*gate.HISTORY, HEAD)
    return {
        "schema": gate.SCHEMA,
        "task": {"id": gate.TASK, "repository": gate.REPOSITORY, "baseline": gate.BASELINE,
                 "base_branch": "develop", "head_branch": gate.BRANCH},
        "authorization": {"task_id": gate.TASK, "active": True, "operations": sorted(gate.OPERATIONS),
                          "source_ref": gate.AUTHORIZATION_REF, "source_sha256": gate.AUTHORIZATION_SHA256},
        "current": {"base": gate.DEVELOP, "head": HEAD, "tree": TREE,
                    "working_tree_clean": True, "evidence_ref": "fixture://git"},
        "implementers": ["writer"],
        "corrections": [{"number": n + 1, "from_head": chain[n], "to_head": chain[n + 1],
                         "evidence_ref": "fixture://round/" + str(n + 1)} for n in range(5)],
        "reviews": [report(1)] if before_push else [report(1), report(2)],
        "unavailable_review_rounds": [], "handoffs": [],
        "pr": {"number": 29, "url": gate.PR_URL, "state": "open", "base_branch": "develop",
               "head_branch": gate.BRANCH, "draft": True, "base_sha": gate.BASELINE if before_push else gate.DEVELOP,
               "head_sha": gate.START if before_push else HEAD, "mergeable": not before_push,
               "protection_satisfied": not before_push, "findings_checked": True,
               "new_blockers": [], "evidence_ref": "fixture://api-original"},
        "remote": {"base": gate.DEVELOP, "head": gate.START if before_push else HEAD,
                   "evidence_ref": "fixture://direct-refs"},
        "continuation": {"created_at": "2026-09-25T00:00:00Z", "reconstructed": True,
                         "history_ref": "fixture://reconstruction", "missing_originals": ["task.md", "early reports"],
                         "known_findings": [{"id": identifier, "source_ref": "fixture://original/" + identifier,
                                             "description": "required candidate behavior", "status": "verified_on_candidate",
                                             "verification_ref": "fixture://candidate-proof/" + identifier}
                                            for identifier in sorted(gate.KNOWN_FINDING_IDS)],
                         "finding_inventory_ref": "fixture://inventory", "original_stop_ref": "fixture://reported-stop",
                         "candidate_head": HEAD, "integration_ref": "fixture://ancestry",
                         "contains_start": True, "contains_develop": True},
        "local_validation": {"head": HEAD, "platform": "Windows", "python_version": "3.13.0",
                             "evidence_ref": "fixture://local-logs", "checks": {
                                 name: {"status": "success", "evidence_ref": "fixture://local/" + name}
                                 for name in gate.LOCAL_CHECKS}},
        "pr_ci": None if before_push else {
            "event": "pull_request", "branch": gate.BRANCH, "workflow": gate.WORKFLOW,
            "run_id": 101, "attempt": 2, "latest_run_id": 101, "latest_attempt": 2,
            "head_sha": HEAD, "tested_sha": TESTED, "parents": [gate.DEVELOP, HEAD], "tree": TREE,
            "status": "completed", "conclusion": "success", "url": "fixture://ci",
            "evidence_ref": "fixture://all-run-logs", "jobs": [{
                "name": "Python " + version, "runner": "windows-2025", "python_version": version + ".0",
                "run_id": 101, "attempt": 2, "tested_sha": TESTED, "conclusion": "success",
                "steps": {name: "success" for name in (*gate.COMMON_STEPS, "Check committed whitespace (pull request)")},
                "evidence_ref": "fixture://job/" + version,
            } for version in gate.PYTHONS]},
    }


def blocked(review, kind):
    review.update(verdict="BLOCKED", blocker_kind=kind, findings=["confirmed " + kind])


class PR29GateTests(unittest.TestCase):
    def action(self, evidence, action):
        result = gate.next_action(evidence)
        self.assertEqual(result["action"], action, result)
        self.assertNotIn(result["action"], ("MERGE_PROPOSAL", "COMPLETE", "CORRECT_IMPLEMENTATION"))
        return result

    def test_missing_historical_originals_are_not_fabricated_or_required(self):
        evidence = state()
        before = deepcopy(evidence)
        result = self.action(evidence, "STOP")
        self.assertTrue(result["delivery_ready"])
        self.assertIn("not authorized", result["reason"])
        self.assertEqual(evidence, before)
        self.assertEqual(result, gate.next_action(evidence))
        self.assertTrue(evidence["continuation"]["missing_originals"])

    def test_original_adoption_is_portable_and_hash_bound(self):
        path = ROOT / "docs/evidence/pr29_round5_user_adopted_contract.md"
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), gate.AUTHORIZATION_SHA256)
        for field in ("source_ref", "source_sha256", "task_id"):
            evidence = state()
            evidence["authorization"][field] = "wrong"
            self.action(evidence, "STOP")

    def test_adoption_raw_bytes_survive_a_fresh_windows_autocrlf_checkout(self):
        relative = Path("docs/evidence/pr29_round5_user_adopted_contract.md")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository, checkout = root / "isolated-repository", root / "fresh-checkout"
            repository.mkdir()
            checkout.mkdir()
            def git(*args):
                result = subprocess.run(["git", *args], cwd=repository, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return result.stdout
            git("init", "-q")
            git("config", "core.autocrlf", "true")
            (repository / ".gitattributes").write_bytes((ROOT / ".gitattributes").read_bytes())
            (repository / relative).parent.mkdir(parents=True)
            (repository / relative).write_bytes((ROOT / relative).read_bytes())
            git("add", "--", ".gitattributes", relative.as_posix())
            git("checkout-index", "--all", "--prefix=" + checkout.as_posix() + "/")
            self.assertEqual(hashlib.sha256((checkout / relative).read_bytes()).hexdigest(),
                             gate.AUTHORIZATION_SHA256)

    def test_exact_scope_history_and_maximum_five_are_closed(self):
        for part, field, value in (("task", "id", "other"), ("task", "baseline", HEAD),
                                  ("task", "repository", "other/repo"), ("current", "base", gate.BASELINE),
                                  ("remote", "head", TESTED), ("pr", "number", 30), ("pr", "url", "other"),
                                  ("continuation", "reconstructed", False),
                                  ("continuation", "contains_start", False),
                                  ("continuation", "contains_develop", False)):
            with self.subTest(part=part, field=field):
                evidence = state(); evidence[part][field] = value
                self.action(evidence, "STOP")
        for count in (0, 3, 4, 6):
            evidence = state()
            evidence["corrections"] = (evidence["corrections"] * 2)[:count]
            self.action(evidence, "STOP")
        for field in ("from_head", "to_head", "number"):
            evidence = state(); evidence["corrections"][2][field] = 9 if field == "number" else HEAD
            self.action(evidence, "STOP")

    def test_pre_push_requires_first_review_and_preserves_old_api_base(self):
        evidence = state(True)
        original = deepcopy(evidence)
        self.action(evidence, "PUSH_CANDIDATE")
        self.assertEqual(evidence, original)
        evidence["reviews"] = []
        self.action(evidence, "REQUEST_REVIEW_1")
        evidence["unavailable_review_rounds"] = [1]
        self.action(evidence, "STOP")
        for permission in ("push", "pr"):
            evidence = state(True); evidence["authorization"]["operations"].remove(permission)
            self.action(evidence, "STOP")
        evidence = state(True); evidence["pr_ci"] = state()["pr_ci"]
        self.action(evidence, "STOP")

    def test_no_metadata_fabrication_or_remote_drift_after_push(self):
        for field, value in (("head_sha", gate.START), ("base_sha", gate.BASELINE)):
            evidence = state(); evidence["pr"][field] = value
            self.action(evidence, "REFRESH_EVIDENCE")
        for status in ("closed", "merged"):
            evidence = state(); evidence["pr"]["state"] = status
            self.action(evidence, "STOP")
        for key in ("merge", "push_ci", "override_round_limit"):
            evidence = state(); evidence[key] = None
            self.action(evidence, "STOP")

    def test_known_findings_and_local_acceptance_cannot_be_ignored(self):
        for status in ("open", "unknown"):
            evidence = state()
            evidence["continuation"]["known_findings"][0].update(status=status, verification_ref=None)
            self.action(evidence, "STOP")
        evidence = state(); evidence["continuation"]["known_findings"].pop()
        self.action(evidence, "STOP")
        for check in gate.LOCAL_CHECKS:
            evidence = state(); evidence["local_validation"]["checks"][check]["status"] = "missing"
            self.action(evidence, "REFRESH_EVIDENCE")
        evidence = state(); evidence["local_validation"]["head"] = gate.START
        self.action(evidence, "STOP")

    def test_two_fresh_independent_full_reviews_cannot_reuse_history(self):
        for number in (1, 2):
            for field, value in (("reviewer", "writer"), ("independent", False),
                                 ("full_diff_reviewed", False), ("head", gate.START),
                                 ("base", gate.BASELINE), ("baseline", gate.DEVELOP), ("scope", "partial")):
                evidence = state(); evidence["reviews"][number - 1][field] = value
                self.action(evidence, "STOP")
        evidence = state(); evidence["reviews"][1]["reviewer"] = evidence["reviews"][0]["reviewer"]
        self.action(evidence, "STOP")
        evidence = state(); evidence["reviews"] = []
        self.action(evidence, "REQUEST_REVIEW_1")
        evidence["reviews"] = [report(1)]
        self.action(evidence, "REQUEST_REVIEW_2")

    def test_sixth_code_round_stops_even_with_failed_ci(self):
        for number in (1, 2):
            evidence = state(); blocked(evidence["reviews"][number - 1], "code")
            evidence["pr_ci"]["conclusion"] = "failure"
            evidence["local_validation"]["checks"]["pytest"]["status"] = "missing"
            self.assertIn("sixth", self.action(evidence, "STOP")["reason"])
        evidence = state(); evidence["pr"]["new_blockers"] = ["confirmed bug"]
        self.assertIn("sixth", self.action(evidence, "STOP")["reason"])

    def test_non_code_supplements_preserve_full_review_and_history(self):
        for kind in ("evidence", "contract", "capability"):
            evidence = state(); old = evidence["reviews"][0]; blocked(old, kind)
            self.action(evidence, "REFRESH_EVIDENCE" if kind == "evidence" else "STOP")
            fresh = report(1); fresh.update(report_ref="fixture://supplement", reviewer="fresh-third-agent",
                                           supersedes_report_ref=old["report_ref"],
                                           resolution_evidence_ref="fixture://raw-resolution")
            evidence["reviews"].append(fresh)
            self.assertTrue(self.action(evidence, "STOP")["delivery_ready"])
            fresh["full_diff_reviewed"] = False
            self.action(evidence, "STOP")

    def test_illegal_supplements_never_clear_code_or_cross_scope(self):
        for kind in ("code", "evidence"):
            for defect in ("unlinked", "resolution", "head", "duplicate", "fork", "reviewer"):
                evidence = state(); old = evidence["reviews"][0]; blocked(old, kind)
                fresh = report(1); fresh.update(report_ref="fixture://new", supersedes_report_ref=old["report_ref"],
                                               resolution_evidence_ref="fixture://resolution")
                if defect == "unlinked": fresh.update(supersedes_report_ref=None, resolution_evidence_ref=None)
                if defect == "resolution": fresh["resolution_evidence_ref"] = None
                if defect == "head": fresh["head"] = gate.START
                if defect == "duplicate": fresh["report_ref"] = old["report_ref"]
                if defect == "reviewer": fresh["reviewer"] = "writer"
                evidence["reviews"].append(fresh)
                if defect == "fork": evidence["reviews"].append({**fresh, "report_ref": "fixture://fork"})
                self.assertFalse(gate.next_action(evidence).get("delivery_ready", False))

    def test_ci_checks_run_attempt_checkout_parents_tree_all_jobs_and_steps(self):
        mutations = (("event", "push"), ("branch", "develop"), ("workflow", "other"),
                     ("head_sha", gate.START), ("tested_sha", gate.DEVELOP),
                     ("parents", [HEAD, gate.DEVELOP]), ("tree", TESTED), ("attempt", 1),
                     ("latest_run_id", 102), ("conclusion", "failure"))
        for field, value in mutations:
            evidence = state(); evidence["pr_ci"][field] = value
            self.assertFalse(gate.next_action(evidence).get("delivery_ready", False), field)
        for index in (0, 1):
            for field, value in (("runner", "ubuntu-latest"), ("python_version", "3.11.0"),
                                 ("run_id", 102), ("attempt", 1), ("tested_sha", HEAD), ("conclusion", "skipped")):
                evidence = state(); evidence["pr_ci"]["jobs"][index][field] = value
                self.assertFalse(gate.next_action(evidence).get("delivery_ready", False))
            for step in gate.COMMON_STEPS:
                evidence = state(); evidence["pr_ci"]["jobs"][index]["steps"][step] = "skipped"
                self.assertFalse(gate.next_action(evidence).get("delivery_ready", False), step)
        for result in ("failure", "skipped", None, "unknown"):
            evidence = state(); evidence["pr_ci"]["jobs"][0]["steps"]["Additional real gate"] = result
            self.action(evidence, "INVESTIGATE_CI")
        evidence = state()
        evidence["pr_ci"]["jobs"][0]["steps"]["Check committed whitespace (push)"] = "skipped"
        self.assertTrue(self.action(evidence, "STOP")["delivery_ready"])
        evidence = state(); evidence["pr_ci"]["jobs"].pop(0)
        self.action(evidence, "REFRESH_EVIDENCE")
        evidence = state(); evidence["pr_ci"]["status"] = "in_progress"
        self.action(evidence, "WAIT_PR_CI")
        evidence = state(); evidence["pr_ci"] = None
        self.action(evidence, "WAIT_PR_CI")

    def test_ci_rerun_requires_new_second_review_but_cannot_erase_blockers(self):
        evidence = state()
        evidence["pr_ci"].update(attempt=3, latest_attempt=3)
        for job in evidence["pr_ci"]["jobs"]: job["attempt"] = 3
        self.action(evidence, "REQUEST_REVIEW_2")
        blocked(evidence["reviews"][1], "evidence")
        self.action(evidence, "REFRESH_EVIDENCE")

    def test_unmerged_delivery_never_demands_merge_approval_or_changes_its_truth(self):
        evidence = state()
        evidence["pr"].update(mergeable=False, protection_satisfied=False)
        result = self.action(evidence, "STOP")
        self.assertTrue(result["delivery_ready"])
        self.assertFalse(result["mergeable"])
        self.assertFalse(result["protection_satisfied"])
        evidence["pr"]["draft"] = False
        self.action(evidence, "STOP")

    def test_second_round_code_blocker_is_not_hidden_by_missing_first_report(self):
        evidence = state()
        evidence["reviews"] = [report(2)]
        blocked(evidence["reviews"][0], "code")
        self.assertIn("sixth", self.action(evidence, "STOP")["reason"])

    def test_general_gate_still_rejects_four_rounds_and_pr29_schema(self):
        from tests.test_pr_review_gate import evidence_state
        evidence = evidence_state()
        evidence["corrections"] = state()["corrections"][:4]
        self.assertEqual(general_gate.next_action(evidence)["action"], "STOP")
        self.assertEqual(general_gate.next_action(state())["action"], "STOP")

    def test_closed_schema_and_cli_reject_duplicate_keys_without_writes(self):
        for part in (None, "authorization", "continuation", "current", "remote", "local_validation"):
            evidence = state(); target = evidence if part is None else evidence[part]
            target["unknown"] = True
            self.action(evidence, "STOP")
        for operations in (["merge"], ["release"]):
            evidence = state(); evidence["authorization"]["operations"] += operations
            self.action(evidence, "STOP")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(json.dumps(state()), encoding="utf-8")
            before = path.read_bytes()
            process = subprocess.run([sys.executable, str(ROOT / "scripts/pr29_review_gate.py"), "validate", str(path)],
                                     capture_output=True, text=True)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            self.assertFalse(json.loads(process.stdout)["authenticity_verified"])
            self.assertEqual(path.read_bytes(), before)
            decision = subprocess.run([sys.executable, str(ROOT / "scripts/pr29_review_gate.py"), "next-action", str(path)],
                                      capture_output=True, text=True)
            self.assertEqual(decision.returncode, 2)
            self.assertTrue(json.loads(decision.stdout)["delivery_ready"])
            self.assertEqual(path.read_bytes(), before)
            path.write_text('{"schema":1,"schema":2}', encoding="utf-8")
            process = subprocess.run([sys.executable, str(ROOT / "scripts/pr29_review_gate.py"), "next-action", str(path)],
                                     capture_output=True, text=True)
            self.assertEqual(process.returncode, 2)
            self.assertIn("duplicate JSON key", process.stdout)

    def test_workflow_actual_condition_is_limited_to_adopted_pr_and_target(self):
        text = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        expression = re.search(r"- name: Run full unittest suite\s+if: \$\{\{ (.+?) \}\}", text)[1]
        self.assertIn(expression, re.search(r"python-version: \$\{\{ fromJSON\((.+)\) \}\}", text)[1])
        job_env = re.search(r"    env:\n(.*?)    strategy:", text, re.S)[1]
        capture_expression = re.search(r"PYTEST_ADDOPTS: \$\{\{ (.+?) \}\}", job_env)[1]
        self.assertEqual(text.count("PYTEST_ADDOPTS:"), 1)
        capture_condition, capture_choice = capture_expression.rsplit(" && ", 1)
        self.assertEqual(capture_choice, "'--capture=sys' || ''")
        self.assertIn(capture_condition, expression)
        context = {"github.event_name": "pull_request", "github.repository": gate.REPOSITORY,
                   "github.event.pull_request.head.repo.full_name": gate.REPOSITORY,
                   "github.event.pull_request.number": 29, "github.head_ref": gate.BRANCH,
                   "github.event.pull_request.base.ref": "develop", "github.event.pull_request.base.sha": gate.DEVELOP}

        def evaluate(values, actual_expression=expression):
            code = actual_expression
            for name in sorted(values, key=len, reverse=True): code = code.replace(name, repr(values[name]))
            code = code.replace("&&", " and ").replace("||", " or ")
            self.assertNotIn("github.", code)
            return eval(code, {"__builtins__": {}}, {})

        self.assertTrue(evaluate(context))
        self.assertEqual(evaluate(context, capture_expression), "--capture=sys")
        for key in context:
            changed = {**context, key: "wrong" if key != "github.event.pull_request.number" else 30}
            self.assertFalse(evaluate(changed), key)
            self.assertEqual(evaluate(changed, capture_expression), "", key)
        for event in ("push", "workflow_dispatch"):
            changed = {**context, "github.event_name": event}
            self.assertFalse(evaluate(changed))
            self.assertEqual(evaluate(changed, capture_expression), "")
        pr30 = {**context, "github.event.pull_request.number": 30,
                "github.head_ref": "codex/simplify-validation",
                "github.event.pull_request.base.sha": gate.BASELINE}
        self.assertTrue(evaluate(pr30))
        self.assertEqual(evaluate(pr30, capture_expression), "")
        for step in gate.COMMON_STEPS:
            self.assertIn("- name: " + step, text)


if __name__ == "__main__":
    unittest.main()
