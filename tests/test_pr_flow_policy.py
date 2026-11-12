"""Bounded synthetic v5 contracts; no activation, product full run or real CI."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts import pr_flow_policy as policy
from scripts import pr_review_gate as gate
from test_current_validation_evidence import bundle
from test_pr_review_gate import BASE, HEAD, TREE, NEW, MERGE, ci_evidence, evidence_state


ROOT = Path(__file__).resolve().parents[1]
# Git for Windows needs short temporary repository paths. Retained driver,
# source captures and results live in the task's single integration directory.
OUT = ROOT / "tmp/pfl"


class TieredReviewPolicyTests(unittest.TestCase):
    def setUp(self):
        OUT.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="synthetic-", dir=OUT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.serial = 0

    def original(self, value, ref=None):
        self.serial += 1
        file = self.root / (str(self.serial) + ".json")
        raw = value if type(value) is bytes else json.dumps(value, sort_keys=True).encode("utf-8")
        file.write_bytes(raw)
        return {"report_ref": ref or value["report_ref"], "source_file": str(file.resolve()),
                "sha256": hashlib.sha256(raw).hexdigest(), "backups": []}

    def candidate(self, tier="local"):
        return {"task_id": "synthetic-new-task", "repository": gate.REPOSITORY,
                "baseline": BASE, "base": BASE, "merge_base": BASE, "head": HEAD, "tree": TREE,
                "scope": "one frozen objective", "changed_files": ["docs/guide.md" if tier == "docs" else "ui_labels.py"],
                "risks": ["safety"] if tier == "core" else []}

    def classification(self, tier="local"):
        return {"tier": tier, "author": "author", "rationale": "Known isolated presentation behavior",
                "known_consumers": ["the directly named consumer"], "impact_known": True}

    def report(self, candidate=None, *, reviewer="reviewer-1", session="session-1", ref="review-1"):
        candidate = deepcopy(candidate or self.candidate())
        return {"schema": "pr-flow-code-review/1", "policy": policy.POLICY_ID, "report_ref": ref,
                "candidate": candidate, "reviewer": reviewer, "session": session, "independent": True,
                "confirmed_tier": "core" if candidate["risks"] else ("docs" if candidate["changed_files"] == ["docs/guide.md"] else "local"),
                "mode": "full", "reviewed_range": {"from_head": candidate["baseline"], "to_head": candidate["head"]},
                "previous_report_ref": None, "full_review_ref": ref, "delta_from": None,
                "impact_known": True, "architecture_changed": False, "integration_reviewed": True,
                "impact_summary": "Direct label consumer and its known navigation path; no contract effect", "review_checks": [],
                "verdict": "PASS", "findings": [], "resolutions": []}

    def review(self, candidate, classification, reports):
        return gate.validate_tiered_reviews(candidate, classification, ["author", "coordinator"],
                                           [self.original(report) for report in reports])

    def state(self, tier="local"):
        candidate = self.candidate(tier)
        reports = [self.report(candidate)]
        if tier == "core":
            reports.append(self.report(candidate, reviewer="reviewer-2", session="session-2", ref="review-2"))
        ci = ci_evidence()
        ci["branch"] = "codex/fixture"
        names = ("Check out repository", "Set up Python", "Show Python version", "Upgrade pip",
                 "Install development dependencies", "Validate runtime asset integrity", "Audit test entrypoint coverage",
                 "Run full pytest suite", "Verify active test execution", "Compile Python sources",
                 "Check committed whitespace (pull request)", "Check repository working tree",
                 "Capture main full environment", "Produce current validation coverage", "Verify current validation coverage",
                 "Upload main full evidence", "Verify required validation results", "Resolve current validation route")
        ci["jobs"][0]["steps"] = {name: "success" for name in names}
        pr = deepcopy(evidence_state()["pr"])
        pr["head_branch"] = "codex/fixture"
        coverage = bundle(self.root / "coverage")
        self.full_plan(coverage)
        return {"schema": gate.TIERED_SCHEMA, "policy": policy.POLICY_ID,
                "task_policy": {"policy": policy.POLICY_ID, "task_id": candidate["task_id"], "baseline": BASE,
                                "origin": "new_task", "starting_head": HEAD, "base_branch": "develop",
                                "head_branch": "codex/fixture", "source_ref": "synthetic://task-policy-basis"},
                "candidate": candidate, "classification": self.classification(tier), "implementers": ["author", "coordinator"],
                "corrections": [], "review_originals": [self.original(report) for report in reports],
                "authorization": {"task_id": candidate["task_id"], "active": True, "operations": ["merge"],
                                  "source_ref": "synthetic://authorization-not-real-user-approval"},
                "execution": {"mode": "validation", "validation_authorization_ref": "synthetic://start-validation"},
                "current": {"base": BASE, "head": HEAD, "tree": TREE, "scope": candidate["scope"],
                            "working_tree_clean": True, "evidence_ref": "synthetic://exact-current-git"},
                "pr": pr, "pr_ci": ci, "coverage": str(coverage), "documentation_checks": None}

    def full_plan(self, coverage):
        from scripts import pr_flow_ci as ci
        path = Path(coverage).parent / "pr-flow-ci/plan.json"; path.parent.mkdir(parents=True, exist_ok=True)
        plan = ci.dispatch({"pull_request": {"base": {"ref": "develop"}}},
                           {"GITHUB_EVENT_NAME": "pull_request", "GITHUB_REF": "refs/pull/20/merge"})
        path.write_text(json.dumps(plan), encoding="utf-8")

    def test_reviewer_counts_are_one_one_two_without_classification_round(self):
        for tier in ("docs", "local", "core"):
            with self.subTest(tier=tier):
                candidate, classification = self.candidate(tier), self.classification(tier)
                report = self.report(candidate)
                result = self.review(candidate, classification, [report])
                self.assertEqual(result["code_ready"], tier != "core")
                if tier == "core":
                    report2 = self.report(candidate, reviewer="reviewer-2", session="session-2", ref="review-2")
                    self.assertTrue(self.review(candidate, classification, [report, report2])["code_ready"])

    def test_core_markdown_mixed_and_risks_cannot_downgrade(self):
        for files, risks in ((["AGENTS.md"], []), (["docs/PR_REVIEW_GATE.md"], []),
                             (["docs/guide.md", "scripts/pr_flow_ci.py"], []),
                             (["ui_labels.py"], ["evidence"]), (["docs/guide.md"], ["commands"])):
            for tier in ("docs", "local"):
                with self.subTest(files=files, risks=risks, tier=tier):
                    candidate = self.candidate(tier)
                    candidate.update(changed_files=files, risks=risks)
                    with self.assertRaises(policy.PolicyError):
                        policy.validate_classification(candidate, self.classification(tier), ["author"], [])
        candidate = self.candidate("docs")
        candidate["changed_files"].append("ui_labels.py")
        with self.assertRaises(policy.PolicyError):
            policy.validate_classification(candidate, self.classification("docs"), ["author"], [])

    def test_unknown_impact_or_unconfirmed_classification_never_exempts(self):
        candidate, classification = self.candidate("core"), self.classification("core")
        candidate["risks"] = ["unknown"]
        reports = [self.report(candidate), self.report(candidate, reviewer="r2", session="s2", ref="r2")]
        self.assertFalse(self.review(candidate, classification, reports)["code_ready"])
        candidate, classification = self.candidate(), self.classification()
        classification["impact_known"] = False
        with self.assertRaises(policy.PolicyError):
            self.review(candidate, classification, [self.report(candidate)])
        self.assertFalse(self.review(self.candidate("docs"), self.classification("docs"), [])["code_ready"])

    def test_author_session_exclusion_and_two_distinct_sessions(self):
        candidate, classification = self.candidate(), self.classification()
        for field in ("reviewer", "session"):
            report = self.report(candidate)
            report[field] = "author"
            with self.subTest(field=field), self.assertRaises(policy.PolicyError):
                self.review(candidate, classification, [report])
        candidate, classification = self.candidate("core"), self.classification("core")
        reports = [self.report(candidate), self.report(candidate, reviewer="r2", ref="r2")]
        self.assertFalse(self.review(candidate, classification, reports)["code_ready"])

    def test_reviewer_disagreement_requires_escalation(self):
        report = self.report()
        report["confirmed_tier"] = "core"
        with self.assertRaises(policy.PolicyError):
            self.review(self.candidate(), self.classification(), [report])

    def test_canonical_bytes_backup_hash_and_retrievability(self):
        pointer = self.original(self.report())
        backup = self.root / "backup.json"
        backup.write_bytes(Path(pointer["source_file"]).read_bytes())
        pointer["backups"] = [{"source_file": str(backup), "sha256": pointer["sha256"]}]
        self.assertEqual(policy.verify_original(pointer), backup.read_bytes())
        backup.write_bytes(b"changed backup")
        with self.assertRaisesRegex(policy.PolicyError, "backup differs"):
            policy.verify_original(pointer)
        pointer["backups"] = []
        Path(pointer["source_file"]).write_bytes(b"changed canonical")
        with self.assertRaisesRegex(policy.PolicyError, "hash mismatch"):
            policy.verify_original(pointer)
        Path(pointer["source_file"]).unlink()
        with self.assertRaises(OSError):
            policy.verify_original(pointer)

    def test_same_file_cannot_be_its_own_backup_or_relative_original(self):
        pointer = self.original(self.report())
        pointer["backups"] = [{"source_file": pointer["source_file"], "sha256": pointer["sha256"]}]
        with self.assertRaises(policy.PolicyError):
            policy.verify_original(pointer)
        pointer["backups"] = []
        pointer["source_file"] = "relative.json"
        with self.assertRaises(policy.PolicyError):
            policy.verify_original(pointer)

    def test_duplicate_json_unknown_fields_and_pointer_identity_rejected(self):
        raw = json.dumps(self.report()).encode()
        duplicate = raw[:-1] + b', "verdict": "PASS"}'
        pointer = self.original(duplicate, "review-1")
        with self.assertRaises(gate.EvidenceError):
            gate.validate_tiered_reviews(self.candidate(), self.classification(), ["author"], [pointer])
        for field, value in (("extra", True), ("report_ref", "different")):
            report = self.report()
            report[field] = value
            pointer = self.original(report)
            if field == "report_ref":
                pointer["report_ref"] = "review-1"
            with self.assertRaises(policy.PolicyError):
                gate.validate_tiered_reviews(self.candidate(), self.classification(), ["author"], [pointer])

    def delta(self, previous, candidate=None, ref="delta-2"):
        report = self.report(candidate or dict(previous["candidate"], head=NEW), ref=ref)
        report.update(mode="delta", previous_report_ref=previous["report_ref"],
                      reviewed_range={"from_head": previous["candidate"]["head"], "to_head": report["candidate"]["head"]},
                      full_review_ref=previous["full_review_ref"],
                      delta_from={key: previous["candidate"][key] for key in ("base", "head")})
        return report

    def test_initial_full_then_same_reviewer_delta_on_new_head_and_base(self):
        old = self.report()
        candidate = dict(self.candidate(), head=NEW, base="1" * 40)
        delta = self.delta(old, candidate)
        self.assertTrue(self.review(candidate, self.classification(), [old, delta])["code_ready"])
        self.assertFalse(self.review(candidate, self.classification(), [old])["code_ready"])

    def test_delta_requires_retrievable_full_original_exact_predecessor_and_actor(self):
        old, candidate = self.report(), dict(self.candidate(), head=NEW)
        for field, value in (("previous_report_ref", None), ("full_review_ref", "absent"),
                             ("reviewer", "new-reviewer"), ("session", "new-session"),
                             ("delta_from", {"base": BASE, "head": "1" * 40})):
            delta = self.delta(old, candidate)
            delta[field] = value
            with self.subTest(field=field), self.assertRaises(policy.PolicyError):
                self.review(candidate, self.classification(), [old, delta])
        with self.assertRaises(policy.PolicyError):
            self.review(candidate, self.classification(), [self.delta(old, candidate)])

    def test_architecture_and_unknown_delta_require_new_full(self):
        old = self.report()
        for field, value in (("architecture_changed", True), ("impact_known", False)):
            delta = self.delta(old)
            delta[field] = value
            with self.subTest(field=field), self.assertRaises(policy.PolicyError):
                self.review(delta["candidate"], self.classification(), [old, delta])
        new = self.report(dict(self.candidate(), head=NEW), reviewer="new-reviewer", session="new-session", ref="new-full")
        self.assertTrue(self.review(new["candidate"], self.classification(), [old, new])["code_ready"])

    def test_latest_scope_task_baseline_and_candidate_binding(self):
        for field in ("task_id", "baseline", "scope", "repository"):
            report = self.report()
            report["candidate"][field] = "other-task" if field in ("task_id", "scope", "repository") else NEW
            with self.subTest(field=field), self.assertRaises(policy.PolicyError):
                self.review(self.candidate(), self.classification(), [report])
        for field in ("base", "head", "tree", "merge_base"):
            report = self.report()
            report["candidate"][field] = NEW
            if field == "head":
                report["reviewed_range"]["to_head"] = NEW
            self.assertFalse(self.review(self.candidate(), self.classification(), [report])["code_ready"], field)

    def test_chain_branch_duplicate_ref_and_reset_rejected(self):
        old = self.report()
        delta = self.delta(old)
        for reports in ([old, old], [old, delta, self.delta(old, ref="fork")],
                        [old, self.report(ref="reset")]):
            with self.assertRaises(policy.PolicyError):
                self.review(delta["candidate"], self.classification(), reports)

    def test_unresolved_finding_survives_new_full_empty_pass(self):
        old = self.report()
        old.update(verdict="BLOCKED", findings=[{"id": "F1", "kind": "code", "description": "confirmed synthetic defect"}])
        new = self.report(dict(self.candidate(), head=NEW), reviewer="r2", session="s2", ref="new-full")
        result = self.review(new["candidate"], self.classification(), [old, new])
        self.assertFalse(result["code_ready"])
        self.assertEqual(result["unresolved_findings"], ["review-1#F1"])
        new["resolutions"] = [{"report_ref": "review-1", "finding_id": "F1",
                               "evidence": self.original(b"synthetic corrective diff/check evidence", "synthetic-resolution")}]
        self.assertTrue(self.review(new["candidate"], self.classification(), [old, new])["code_ready"])

    def test_resolution_requires_new_head_named_finding_and_valid_bytes(self):
        old = self.report()
        old.update(verdict="BLOCKED", findings=[{"id": "F1", "kind": "code", "description": "confirmed synthetic defect"}])
        new = self.report(ref="full-2")
        new["previous_report_ref"] = old["report_ref"]
        new["resolutions"] = [{"report_ref": "review-1", "finding_id": "F1",
                               "evidence": self.original(b"synthetic resolution", "resolution")}]
        with self.assertRaisesRegex(policy.PolicyError, "same HEAD"):
            self.review(self.candidate(), self.classification(), [old, new])
        new["candidate"]["head"] = NEW
        new["reviewed_range"]["to_head"] = NEW
        new["resolutions"][0]["finding_id"] = "missing"
        with self.assertRaises(policy.PolicyError):
            self.review(new["candidate"], self.classification(), [old, new])
        new["resolutions"][0]["finding_id"] = "F1"
        new["resolutions"][0]["evidence"]["sha256"] = "a" * 64
        with self.assertRaises(policy.PolicyError):
            self.review(new["candidate"], self.classification(), [old, new])

    def test_fake_empty_blocked_or_nonempty_pass_rejected(self):
        for verdict, findings in (("BLOCKED", []), ("PASS", [{"id": "F", "kind": "code", "description": "defect"}])):
            report = self.report()
            report.update(verdict=verdict, findings=findings)
            with self.assertRaises(policy.PolicyError):
                self.review(self.candidate(), self.classification(), [report])

    def test_code_conclusion_is_independent_of_ci_and_immutable_after_ci(self):
        state = self.state()
        originals = {p["source_file"]: Path(p["source_file"]).read_bytes() for p in state["review_originals"]}
        self.assertTrue(gate.validate_tiered_evidence(state)["requirements_satisfied"])
        state["pr_ci"] = None
        result = gate.validate_tiered_evidence(state)
        self.assertTrue(result["code_ready"])
        self.assertFalse(result["checks_ready"])
        self.assertFalse(result["requirements_satisfied"])
        self.assertEqual(originals, {p: Path(p).read_bytes() for p in originals})

    def test_current_full_ci_and_raw_coverage_are_consumed_without_history(self):
        state = self.state("core")
        result = gate.validate_tiered_evidence(state)
        self.assertTrue(result["requirements_satisfied"], result)
        (Path(state["coverage"]).parent / "pytest.log").write_text("altered raw exit", encoding="utf-8")
        result = gate.validate_tiered_evidence(state)
        self.assertFalse(result["checks_ready"])
        self.assertTrue(result["code_ready"])

    def test_failed_missing_skipped_stale_ci_and_wrong_run_scope_rejected(self):
        cases = ("missing", "failure", "pending", "attempt", "parent", "step", "pr_number", "coverage_run")
        for case in cases:
            with self.subTest(case=case):
                state = self.state()
                if case == "missing": state["pr_ci"] = None
                elif case == "failure": state["pr_ci"]["conclusion"] = "failure"
                elif case == "pending": state["pr_ci"]["status"] = "in_progress"
                elif case == "attempt": state["pr_ci"]["latest_attempt"] = 2
                elif case == "parent": state["pr_ci"]["parents"].reverse()
                elif case == "step": state["pr_ci"]["jobs"][0]["steps"]["Run full pytest suite"] = "skipped"
                elif case == "pr_number": state["pr"]["number"] += 1
                else:
                    state["pr_ci"]["run_id"] = state["pr_ci"]["latest_run_id"] = 101
                    state["pr_ci"]["jobs"][0]["run_id"] = 101
                result = gate.validate_tiered_evidence(state)
                self.assertFalse(result["checks_ready"], result)
                self.assertTrue(result["code_ready"])

    def test_merge_requirements_auth_phase_clean_scope_and_protection(self):
        for case in ("auth", "operation", "development", "dirty", "base", "head", "closed", "protection", "mergeable", "findings", "unchecked"):
            with self.subTest(case=case):
                state = self.state()
                if case == "auth": state["authorization"]["active"] = False
                elif case == "operation": state["authorization"]["operations"] = []
                elif case == "development": state["execution"] = {"mode": "development", "validation_authorization_ref": None}
                elif case == "dirty": state["current"]["working_tree_clean"] = False
                elif case in ("base", "head"): state["pr"][case + "_sha"] = NEW
                elif case == "closed": state["pr"]["state"] = "closed"
                elif case == "protection": state["pr"]["protection_satisfied"] = False
                elif case == "mergeable": state["pr"]["mergeable"] = False
                elif case == "findings": state["pr"]["new_blockers"] = ["unresolved current PR finding"]
                else: state["pr"]["findings_checked"] = False
                result = gate.validate_tiered_evidence(state)
                self.assertFalse(result["requirements_satisfied"], result)
                self.assertFalse(result["merge_ready"])

    def test_task_policy_correction_count_and_history_cannot_reset(self):
        for case in ("policy", "origin", "baseline", "task", "head", "four"):
            state = self.state()
            if case == "policy": state["task_policy"]["policy"] = "other"
            elif case == "origin": state["task_policy"]["origin"] = "migrated_old_stop"
            elif case == "baseline": state["task_policy"]["baseline"] = NEW
            elif case == "task": state["task_policy"]["task_id"] = "reset-task"
            elif case == "head": state["task_policy"]["starting_head"] = NEW
            else:
                state["corrections"] = [{"number": n, "from_head": HEAD, "to_head": NEW, "evidence_ref": "synthetic://history"} for n in range(1, 5)]
            with self.subTest(case=case), self.assertRaises(policy.PolicyError):
                gate.validate_tiered_evidence(state)

    def test_full_review_range_and_required_review_check_originals(self):
        report = self.report()
        report["reviewed_range"]["from_head"] = HEAD
        with self.assertRaisesRegex(policy.PolicyError, "complete task baseline"):
            self.review(self.candidate(), self.classification(), [report])
        report = self.report()
        check = self.original(b"synthetic targeted check raw bytes", "targeted-check")
        report["review_checks"] = [check]
        self.assertTrue(self.review(self.candidate(), self.classification(), [report])["code_ready"])
        Path(check["source_file"]).write_bytes(b"altered targeted check")
        with self.assertRaisesRegex(policy.PolicyError, "hash mismatch"):
            self.review(self.candidate(), self.classification(), [report])

    def test_retained_review_head_requires_corresponding_corrective_history(self):
        state = self.state()
        old = self.report(dict(self.candidate(), head=NEW), reviewer="old-reviewer", session="old-session", ref="old-full")
        state["review_originals"].insert(0, self.original(old))
        with self.assertRaisesRegex(policy.PolicyError, "retained corrective history"):
            gate.validate_tiered_evidence(state)
        state["task_policy"]["starting_head"] = NEW
        state["corrections"] = [{"number": 1, "from_head": NEW, "to_head": HEAD, "evidence_ref": "synthetic://corrective"}]
        self.assertTrue(gate.validate_tiered_evidence(state)["requirements_satisfied"])
        state["corrections"][0]["number"] = True
        with self.assertRaises(policy.PolicyError):
            gate.validate_tiered_evidence(state)

    def test_original_three_corrective_rounds_allow_evidence_completion(self):
        state = self.state()
        heads = ["1" * 40, "2" * 40, "3" * 40, HEAD]
        state["task_policy"]["starting_head"] = heads[0]
        state["corrections"] = [{"number": number, "from_head": before, "to_head": after,
                                 "evidence_ref": "synthetic://retained-corrective"}
                                for number, (before, after) in enumerate(zip(heads, heads[1:]), 1)]
        self.assertTrue(gate.validate_tiered_evidence(state)["requirements_satisfied"])
        state["pr_ci"] = None
        self.assertFalse(gate.validate_tiered_evidence(state)["requirements_satisfied"])

    def test_exact_branch_pair_and_route_step_are_merge_requirements(self):
        state = self.state()
        state["task_policy"]["head_branch"] = "codex/different"
        self.assertFalse(gate.validate_tiered_evidence(state)["requirements_satisfied"])
        state = self.state()
        state["pr_ci"]["jobs"][0]["steps"]["Resolve current validation route"] = "skipped"
        self.assertFalse(gate.validate_tiered_evidence(state)["checks_ready"])

    def documentation_state(self):
        from scripts import pr_flow_ci as ci
        from scripts import pr_flow_transport as transport
        from test_pr_flow_ci import synthetic_ci
        from test_pr_flow_transport import SyntheticAPI, SyntheticFixture
        source = self.root / "document-source"
        fixture = SyntheticFixture(source)
        candidate, event, environment = fixture.candidate, fixture.event, fixture.environment
        environment["GITHUB_RUN_ID"] = "1234"
        binding = ci.bind_event(candidate, event, environment, source)
        transfer = transport.fetch_capture(SyntheticAPI(fixture), candidate, event, source / "tmp/pr-flow-ci/transport", source)
        capture = transfer["capture"]
        self.documentation_resolver = transfer["original_resolver"]
        plan = ci.validated_documentation_plan(capture, event, environment, source, original_resolver=self.documentation_resolver)
        plan["source"] = transfer["source"]
        (source / "tmp/pr-flow-ci/plan.json").write_text(json.dumps(plan), encoding="utf-8")
        output = source / "tmp/pr-flow-ci/docs/checks.json"
        self.assertEqual(ci.run_documentation_checks(candidate, binding, output, source)["status"], "success")
        current_ci, current_pr = synthetic_ci(candidate, binding)
        state = self.state("docs")
        current_ci.update(url="synthetic://CI/1234", evidence_ref="synthetic://current-original-CI")
        current_ci["jobs"][0]["evidence_ref"] = "synthetic://current-original-job"
        state.update(candidate=candidate, classification=capture["classification"], implementers=capture["implementers"],
                     review_originals=capture["review_originals"], pr_ci=current_ci, coverage=None,
                     documentation_checks=str(output))
        state["pr"].update(current_pr, base_sha=candidate["base"], head_sha=candidate["head"])
        state["authorization"]["task_id"] = candidate["task_id"]
        state["task_policy"].update(task_id=candidate["task_id"], baseline=candidate["baseline"],
                                    starting_head=candidate["head"], head_branch=current_pr["head_branch"])
        state["current"].update({key: candidate[key] for key in ("base", "head", "tree", "scope")})
        return source, state

    def test_real_documentation_checks_integrate_with_current_ci_and_inactive_gate(self):
        from scripts import pr_flow_ci as ci
        source, state = self.documentation_state()
        pointer = state["review_originals"][0]
        raw = policy.verify_original(pointer, resolver=self.documentation_resolver)
        with patch.object(ci, "ROOT", source):
            result = gate.validate_tiered_evidence(state, original_resolver=self.documentation_resolver)
            self.assertTrue(result["requirements_satisfied"], result)
            public = gate.next_action(state, original_resolver=self.documentation_resolver)
            self.assertEqual(public["action"], "STOP")
            self.assertFalse(public["merge_ready"])
        self.assertEqual(policy.verify_original(pointer, resolver=self.documentation_resolver), raw)

    def test_documentation_ci_wrong_run_or_missing_confirmation_cannot_exempt(self):
        from scripts import pr_flow_ci as ci
        source, state = self.documentation_state()
        with patch.object(ci, "ROOT", source):
            state["pr_ci"]["latest_attempt"] = 2
            result = gate.validate_tiered_evidence(state, original_resolver=self.documentation_resolver)
            self.assertTrue(result["code_ready"])
            self.assertFalse(result["checks_ready"])
            state["pr_ci"]["latest_attempt"] = 1
            state["review_originals"] = []
            result = gate.validate_tiered_evidence(state, original_resolver=self.documentation_resolver)
            self.assertFalse(result["code_ready"])
            self.assertFalse(result["checks_ready"])

    def test_public_gate_remains_stop_even_with_all_requirements_and_fake_enable(self):
        state = self.state()
        self.assertTrue(gate.validate_tiered_evidence(state)["requirements_satisfied"])
        for enable in (False, True):
            with patch.object(policy, "ENABLED", enable):
                result = gate.next_action(state)
                self.assertEqual(result["action"], "STOP")
                self.assertTrue(result["activation_pending"])
                self.assertFalse(result["merge_ready"])
                self.assertNotIn("MERGE_PROPOSAL", json.dumps(result))

    def test_unknown_state_fields_do_not_enable_v5_and_v3_behavior_is_unchanged(self):
        state = self.state()
        state["ignore_inactive"] = True
        self.assertEqual(gate.next_action(state)["action"], "STOP")
        self.assertEqual(gate.next_action(evidence_state())["action"], "MERGE_PROPOSAL")

    def lifecycle_state(self, tier="local"):
        state = self.state(tier)
        state["authorization"]["operations"] = sorted(gate.OPERATIONS)
        state["lifecycle"] = {"schema": gate.TIERED_LIFECYCLE_SCHEMA,
                              "checks_route": "docs" if tier == "docs" else "full",
                              "premerge_recheck": None, "merge": None, "push_ci": None, "short_validation": None}
        return state

    def premerge_recheck(self, state):
        state["lifecycle"]["premerge_recheck"] = {key: state["candidate"][key] for key in ("base", "head", "tree", "scope")}
        state["lifecycle"]["premerge_recheck"].update(working_tree_clean=True, findings_checked=True,
                                                     new_blockers=[], mergeable=True, protection_satisfied=True,
                                                     evidence_ref="synthetic://immediate-remote-recheck")

    def full_merged_lifecycle(self):
        from test_current_validation_evidence import current_short
        folder = self.root / "actual-full-short"; folder.mkdir()
        coverage, short = current_short(folder)
        self.full_plan(coverage)
        from scripts import validation_evidence as evidence
        data = json.loads(short.read_text())
        with zipfile.ZipFile(folder / "pr-artifact.zip", "w") as archive:
            for file in coverage.parent.rglob("*"):
                if file.is_file(): archive.write(file, file.relative_to(coverage.parent).as_posix())
        data["source"]["archive"]["sha256"] = evidence.sha256_file(folder / "pr-artifact.zip")
        artifact_path = folder / "artifact.json"; artifact = json.loads(artifact_path.read_text())
        artifact["digest"] = "sha256:" + evidence.sha256_file(folder / "pr-artifact.zip")
        artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
        data["source"]["artifact"]["sha256"] = evidence.sha256_file(artifact_path)
        short.write_text(json.dumps(data), encoding="utf-8")
        state = self.lifecycle_state()
        state["coverage"] = str(coverage)
        state["pr"]["state"] = "merged"
        push = ci_evidence("push")
        push.update(run_id=101, latest_run_id=101)
        for job in push["jobs"]:
            job["run_id"] = 101
            job["steps"]["Verify required validation results"] = "success"
        push["jobs"].append(dict(push["jobs"][0], name="Merge short validation", steps={name: "success" for name in (
            "Check out repository", "Set up Python", "Install locked validation dependencies",
            "Verify merge and run short validation", "Upload validation evidence")}))
        state["lifecycle"].update(merge={"sha": MERGE, "parents": [BASE, HEAD], "tree": TREE, "develop_head": NEW,
                                         "develop_contains_merge": True, "evidence_ref": "synthetic://actual-merge-and-develop"},
                                  push_ci=push, short_validation=str(short))
        return state

    def test_lifecycle_development_review_validation_pr_ci_and_immediate_recheck(self):
        state = self.lifecycle_state()
        state.update(pr=None, pr_ci=None, review_originals=[])
        state["execution"] = {"mode": "development", "validation_authorization_ref": None}
        self.assertEqual(gate.evaluate_tiered_lifecycle(state)["action"], "REQUEST_REVIEW_1")
        state["review_originals"] = [self.original(self.report())]
        self.assertEqual(gate.evaluate_tiered_lifecycle(state)["action"], "WAIT_VALIDATION_AUTHORIZATION")
        state["execution"] = {"mode": "validation", "validation_authorization_ref": "synthetic://explicit-start"}
        self.assertEqual(gate.evaluate_tiered_lifecycle(state)["action"], "ENSURE_DRAFT_PR")
        ready = self.lifecycle_state()
        state.update(pr=ready["pr"], pr_ci=None)
        self.assertEqual(gate.evaluate_tiered_lifecycle(state)["action"], "WAIT_PR_CI")
        state["pr_ci"] = ready["pr_ci"]
        result = gate.evaluate_tiered_lifecycle(state)
        self.assertEqual((result["action"], result["stage"]), ("REFRESH_EVIDENCE", "premerge_recheck"))
        self.premerge_recheck(state)
        self.assertEqual(gate.evaluate_tiered_lifecycle(state)["action"], "MERGE_PROPOSAL")
        self.assertEqual(gate.next_action(state)["action"], "STOP")

    def test_lifecycle_core_requires_two_current_independent_conclusions(self):
        state = self.lifecycle_state("core")
        state["review_originals"].pop()
        self.assertEqual(gate.evaluate_tiered_lifecycle(state)["action"], "REQUEST_REVIEW_2")
        state["authorization"]["operations"].remove("delegate")
        self.assertEqual(gate.evaluate_tiered_lifecycle(state)["action"], "STOP")

    def test_lifecycle_code_and_noncode_blockers_precede_ci_without_consuming_evidence_rounds(self):
        for kind, action in (("code", "CORRECT_IMPLEMENTATION"), ("evidence", "REFRESH_EVIDENCE"),
                             ("capability", "STOP"), ("contract", "STOP")):
            with self.subTest(kind=kind):
                state = self.lifecycle_state()
                report = self.report()
                report.update(verdict="BLOCKED", findings=[{"id": "F", "kind": kind, "description": "confirmed synthetic finding"}])
                state.update(review_originals=[self.original(report)], pr_ci=None)
                before = deepcopy(state)
                self.assertEqual(gate.evaluate_tiered_lifecycle(state)["action"], action)
                self.assertEqual(state, before)
        state["execution"] = {"mode": "development", "validation_authorization_ref": None}
        report["findings"][0]["kind"] = "code"
        state["review_originals"] = [self.original(report)]
        state["authorization"]["operations"].remove("push")
        result = gate.evaluate_tiered_lifecycle(state)
        self.assertEqual((result["action"], result["delivery"]), ("CORRECT_IMPLEMENTATION", "local_commit_only"))

    def test_lifecycle_original_three_round_limit_and_same_head_evidence_supplement(self):
        state = self.lifecycle_state()
        heads = ["1" * 40, "2" * 40, "3" * 40, HEAD]
        state["task_policy"]["starting_head"] = heads[0]
        state["corrections"] = [{"number": number, "from_head": before, "to_head": after,
                                 "evidence_ref": "synthetic://retained-round"}
                                for number, (before, after) in enumerate(zip(heads, heads[1:]), 1)]
        report = self.report(); report.update(verdict="BLOCKED", findings=[{"id": "F", "kind": "code", "description": "defect"}])
        state["review_originals"] = [self.original(report)]
        self.assertEqual(gate.evaluate_tiered_lifecycle(state)["action"], "STOP")
        report["findings"][0]["kind"] = "evidence"
        state["review_originals"] = [self.original(report)]
        self.assertEqual(gate.evaluate_tiered_lifecycle(state)["action"], "REFRESH_EVIDENCE")
        supplement = self.report(ref="supplement"); supplement["previous_report_ref"] = "review-1"
        supplement["resolutions"] = [{"report_ref": "review-1", "finding_id": "F",
                                       "evidence": self.original(b"real retained supplemental bytes", "resolution")}]
        state["review_originals"].append(self.original(supplement))
        self.premerge_recheck(state)
        self.assertEqual(gate.evaluate_tiered_lifecycle(state)["action"], "MERGE_PROPOSAL")
        self.assertEqual(len(state["corrections"]), 3)

    def test_lifecycle_ci_failure_missing_checks_and_stale_attempt_are_distinct(self):
        state = self.lifecycle_state()
        state["pr_ci"]["conclusion"] = "failure"
        self.assertEqual(gate.evaluate_tiered_lifecycle(state)["action"], "INVESTIGATE_CI")
        state["pr_ci"]["conclusion"] = "success"; state["coverage"] = None
        self.assertEqual(gate.evaluate_tiered_lifecycle(state)["action"], "REFRESH_EVIDENCE")
        state = self.lifecycle_state(); state["pr_ci"]["latest_attempt"] = 2
        self.assertEqual(gate.evaluate_tiered_lifecycle(state)["action"], "REFRESH_EVIDENCE")

    def test_lifecycle_premerge_authorization_scope_findings_and_protection(self):
        for case in ("revoked", "merge_operation", "base", "head", "tree", "scope", "findings", "unchecked", "dirty", "protection", "mergeable"):
            with self.subTest(case=case):
                state = self.lifecycle_state(); self.premerge_recheck(state)
                check = state["lifecycle"]["premerge_recheck"]
                if case == "revoked": state["authorization"]["active"] = False
                elif case == "merge_operation": state["authorization"]["operations"].remove("merge")
                elif case in {"base", "head", "tree"}: check[case] = NEW
                elif case == "scope": check[case] = "other scope"
                elif case == "findings": check["new_blockers"] = ["confirmed code finding"]
                elif case == "unchecked": check["findings_checked"] = False
                elif case == "dirty": check["working_tree_clean"] = False
                else: check["protection_satisfied" if case == "protection" else "mergeable"] = False
                result = gate.evaluate_tiered_lifecycle(state)
                self.assertNotEqual(result["action"], "MERGE_PROPOSAL")

    def test_lifecycle_actual_merge_push_and_short_are_required_and_source_candidate_is_retained(self):
        state = self.full_merged_lifecycle(); before = deepcopy(state)
        result = gate.evaluate_tiered_lifecycle(state)
        self.assertEqual(result["action"], "COMPLETE", result)
        self.assertEqual((result["merge_sha"], result["develop_head"]), (MERGE, NEW))
        self.assertEqual(state, before)
        for field, action in (("merge", "VERIFY_MERGE"), ("push_ci", "WAIT_PUSH_CI"), ("short_validation", "REFRESH_EVIDENCE")):
            changed = deepcopy(state); changed["lifecycle"][field] = None
            with self.subTest(field=field): self.assertEqual(gate.evaluate_tiered_lifecycle(changed)["action"], action)
        self.assertEqual(gate.next_action(state)["action"], "STOP")

    def test_lifecycle_documentation_source_cannot_complete_without_actual_merge_push_and_short(self):
        from scripts import pr_flow_ci as ci
        source, state = self.documentation_state()
        state["lifecycle"] = {"schema": gate.TIERED_LIFECYCLE_SCHEMA, "checks_route": "docs", "premerge_recheck": None,
                              "merge": None, "push_ci": None, "short_validation": None}
        state["pr"]["state"] = "merged"
        with patch.object(ci, "ROOT", source):
            assess = lambda: gate.evaluate_tiered_lifecycle(state, original_resolver=self.documentation_resolver)
            self.assertEqual(assess()["action"], "VERIFY_MERGE")
            parents = [state["candidate"]["base"], state["candidate"]["head"]]
            state["lifecycle"]["merge"] = {"sha": NEW, "parents": parents, "tree": state["pr_ci"]["tree"],
                "develop_head": NEW, "develop_contains_merge": True, "evidence_ref": "synthetic://actual-merge-model"}
            self.assertEqual(assess()["action"], "WAIT_PUSH_CI")
            push = ci_evidence("push")
            push.update(head_sha=NEW, tested_sha=NEW, parents=parents, tree=state["pr_ci"]["tree"])
            for job in push["jobs"]: job["tested_sha"] = NEW; job["steps"]["Verify required validation results"] = "success"
            push["jobs"].append(dict(push["jobs"][0], name="Merge short validation", steps={name: "success" for name in (
                "Check out repository", "Set up Python", "Install locked validation dependencies",
                "Verify merge and run short validation", "Upload validation evidence")}))
            state["lifecycle"]["push_ci"] = push
            self.assertEqual(assess()["action"], "REFRESH_EVIDENCE")

    def test_lifecycle_documentation_zip_and_short_parser_positive_is_only_inactive_diagnosis(self):
        from scripts import pr_flow_ci as ci
        from test_pr_flow_evidence import documentation_short_fixture
        fixture = documentation_short_fixture(self.root / "documentation-short-model")
        self.assertIn("synthetic", fixture["fixture_context"]["short_commands"])
        candidate, capture, actual = fixture["candidate"], fixture["capture"], fixture["actual_merge"]
        state = self.lifecycle_state("docs")
        state.update(candidate=candidate, classification=capture["classification"], implementers=capture["implementers"],
                     review_originals=capture["review_originals"], coverage=None, documentation_checks=str(fixture["documentation_path"]))
        state["task_policy"].update(task_id=candidate["task_id"], baseline=candidate["baseline"], starting_head=candidate["head"],
                                     head_branch=fixture["pr"]["head_branch"])
        state["authorization"]["task_id"] = candidate["task_id"]
        state["current"].update({key: candidate[key] for key in ("base", "head", "tree", "scope")})
        state["pr"].update(fixture["pr"], state="merged", base_sha=candidate["base"], head_sha=candidate["head"])
        state["pr_ci"] = deepcopy(fixture["pr_ci"])
        state["pr_ci"].update(url="synthetic://PR-CI", evidence_ref="synthetic://PR-CI-original")
        for job in state["pr_ci"]["jobs"]: job["evidence_ref"] = "synthetic://PR-job-original"
        push = ci_evidence("push")
        push.update(head_sha=actual["head"], tested_sha=actual["head"], tree=actual["tree"], parents=actual["parents"],
                    run_id=501, latest_run_id=501)
        for job in push["jobs"]:
            job.update(run_id=501, tested_sha=actual["head"])
            job["steps"]["Verify required validation results"] = "success"
        push["jobs"].append(dict(push["jobs"][0], name="Merge short validation", steps={name: "success" for name in (
            "Check out repository", "Set up Python", "Install locked validation dependencies",
            "Verify merge and run short validation", "Upload validation evidence")}))
        state["lifecycle"].update(merge={"sha": actual["head"], "tree": actual["tree"], "parents": actual["parents"],
            "develop_head": actual["head"], "develop_contains_merge": True, "evidence_ref": "synthetic://Git-actual-merge"},
            push_ci=push, short_validation=str(fixture["short_path"]))
        with patch.object(ci, "ROOT", fixture["source_root"]):
            result = gate.evaluate_tiered_lifecycle(state, original_resolver=fixture["original_resolver"])
            self.assertEqual(result["action"], "COMPLETE", result)
            self.assertEqual(result["merge_sha"], actual["head"])
            self.assertNotEqual(result["merge_sha"], state["pr_ci"]["tested_sha"])
            self.assertEqual(gate.next_action(state, original_resolver=fixture["original_resolver"])["action"], "STOP")

    def test_lifecycle_postmerge_failure_retains_task_actual_merge_and_count(self):
        state = self.full_merged_lifecycle()
        for mutate in (lambda s: s["lifecycle"]["merge"]["parents"].reverse(),
                       lambda s: s["lifecycle"]["merge"].update(tree=NEW),
                       lambda s: s["lifecycle"]["merge"].update(develop_contains_merge=False),
                       lambda s: s["lifecycle"]["push_ci"].update(conclusion="failure"),
                       lambda s: s["lifecycle"]["push_ci"].update(latest_attempt=2),
                       lambda s: s["lifecycle"]["push_ci"].update(tested_sha=NEW),
                       lambda s: s["pr"].update(new_blockers=["new code finding"])):
            changed = deepcopy(state); mutate(changed)
            result = gate.evaluate_tiered_lifecycle(changed)
            self.assertEqual((result["action"], result["stage"]), ("STOP", "postmerge_failure"), result)
            self.assertEqual((result["task_id"], result["baseline"], result["merge_sha"], result["corrections_used"]),
                             (state["candidate"]["task_id"], BASE, MERGE, 0))
        short = Path(state["lifecycle"]["short_validation"])
        value = json.loads(short.read_text()); value["ci"]["attempt"] = 2
        short.write_text(json.dumps(value), encoding="utf-8")
        self.assertEqual(gate.evaluate_tiered_lifecycle(state)["stage"], "postmerge_failure")

    def test_lifecycle_unknown_contract_route_and_old_pending_input_cannot_complete(self):
        self.assertEqual(gate.evaluate_tiered_lifecycle(self.state())["stage"], "pending_contract")
        for route in ("docs", "docs_fallback_full", "unknown"):
            state = self.lifecycle_state(); state["lifecycle"]["checks_route"] = route
            with self.subTest(route=route), self.assertRaises(policy.PolicyError): gate.evaluate_tiered_lifecycle(state)
        state = self.lifecycle_state(); state["lifecycle"]["enable"] = True
        self.assertEqual(gate.next_action(state)["action"], "STOP")
        state = self.lifecycle_state("core"); state["candidate"]["risks"] = ["unknown"]
        state["review_originals"] = []
        self.assertEqual(gate.evaluate_tiered_lifecycle(state)["action"], "STOP")

    def test_lifecycle_real_cli_is_outer_stop_with_nested_diagnosis(self):
        state = self.lifecycle_state(); self.premerge_recheck(state)
        path = self.root / "lifecycle-state.json"; path.write_text(json.dumps(state), encoding="utf-8")
        for command in ("next-action", "diagnose-lifecycle"):
            result = subprocess.run([sys.executable, str(ROOT / "scripts/pr_review_gate.py"), command, str(path)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, result.stderr)
            value = json.loads(result.stdout); self.assertEqual(value["action"], "STOP")
            if command == "diagnose-lifecycle": self.assertEqual(value["evaluation"]["action"], "MERGE_PROPOSAL")

    def test_resolver_preserves_original_bytes_and_resolves_nested_checks_and_resolutions(self):
        old = self.report(); old.update(verdict="BLOCKED", findings=[{"id": "F", "kind": "evidence", "description": "missing evidence"}])
        latest = self.report(ref="supplement"); latest["previous_report_ref"] = old["report_ref"]
        latest["review_checks"] = [self.original(b"targeted raw check", "check")]
        latest["resolutions"] = [{"report_ref": old["report_ref"], "finding_id": "F",
                                  "evidence": self.original(b"resolution raw bytes", "resolution")}]
        pointers = [self.original(old), self.original(latest)]
        backup = self.root / "separate-backup.json"; backup.write_bytes(Path(pointers[0]["source_file"]).read_bytes())
        pointers[0]["backups"] = [{"source_file": str(backup), "sha256": pointers[0]["sha256"]}]
        originals = [*pointers, *latest["review_checks"], latest["resolutions"][0]["evidence"]]
        mapping = {}; relocated = self.root / "relocated"; relocated.mkdir()
        for number, pointer in enumerate(originals):
            for label, item in [("canonical", pointer), *(("backup-" + str(i), value) for i, value in enumerate(pointer["backups"]))]:
                source = Path(item["source_file"]); target = relocated / (str(number) + label)
                target.write_bytes(source.read_bytes()); source.unlink()
                mapping[(item["source_file"], item["sha256"], pointer["report_ref"])] = target
        calls = []
        def resolver(source, digest, ref):
            calls.append((source, digest, ref))
            return mapping[(source, digest, ref)]
        result = gate.validate_tiered_reviews(self.candidate(), self.classification(), ["author"], pointers, original_resolver=resolver)
        self.assertTrue(result["code_ready"])
        self.assertEqual(set(calls), set(mapping))
        self.assertEqual(len(calls), 5)
        state = self.lifecycle_state(); state["review_originals"] = pointers
        self.premerge_recheck(state)
        gate.validate_state(state, original_resolver=resolver)
        self.assertTrue(gate.validate_tiered_evidence(state, original_resolver=resolver)["requirements_satisfied"])
        self.assertEqual(gate.evaluate_tiered_lifecycle(state, original_resolver=resolver)["action"], "MERGE_PROPOSAL")
        mapping[next(key for key in mapping if key[2] == "check")].write_bytes(b"tampered")
        with self.assertRaises(policy.PolicyError):
            gate.validate_tiered_reviews(self.candidate(), self.classification(), ["author"], pointers, original_resolver=resolver)

    def test_resolver_unknown_tuple_relative_target_and_collapsed_backup_fail_closed(self):
        pointer = self.original(self.report())
        def missing(source, digest, ref):
            raise KeyError((source, digest, ref))
        with self.assertRaises(KeyError): policy.verify_original(pointer, resolver=missing)
        for target in (Path("relative.json"), pointer["source_file"]):
            with self.subTest(target=str(target)), self.assertRaises(policy.PolicyError):
                policy.verify_original(pointer, resolver=lambda *args: target)
        other = self.root / "backup.json"; other.write_bytes(Path(pointer["source_file"]).read_bytes())
        pointer["backups"] = [{"source_file": str(other), "sha256": pointer["sha256"]}]
        with self.assertRaises(policy.PolicyError):
            policy.verify_original(pointer, resolver=lambda *args: Path(pointer["source_file"]))


if __name__ == "__main__":
    unittest.main()
