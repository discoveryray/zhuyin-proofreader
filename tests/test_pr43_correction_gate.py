"""Synthetic PR43 gate evidence only; no Git writes, windows or real approvals."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import pr_review_gate as gate
from test_pr_review_gate import evidence_state, block_review


class Pr43CorrectionGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        canonical = [{"number": n, "from_head": start, "to_head": end,
                      "evidence_ref": "fixture://canonical/" + str(n),
                      "actual_commit_parent": start, "classification": "retained fixture"}
                     for n, (start, end) in enumerate(gate.PR43_PREFIX, 1)]
        self.refs, pins = {}, {}
        for name in gate.PR43_SOURCES:
            raw = (json.dumps({"corrections": canonical}).encode() if name == "canonical_ledger"
                   else ("SYNTHETIC ONLY " + name).encode())
            path = self.root / name
            path.write_bytes(raw)
            self.refs[name] = str(path)
            pins[name] = hashlib.sha256(raw).hexdigest()
        fifth = self.root / "fifth_authorization"
        fifth.write_bytes(b"SYNTHETIC fifth authorization")
        self.refs["fifth_authorization"] = str(fifth)
        # Substitute only immutable fixture source pins, never the validator/result.
        self.addCleanup(patch.stopall)
        patch.object(gate, "PR43_SOURCES", pins).start()
        patch.object(gate, "PR43_FIFTH_AUTHORIZATION", hashlib.sha256(fifth.read_bytes()).hexdigest()).start()
        self.state = evidence_state()
        self.state["task"].update(id=gate.PR43_TASK, baseline=gate.PR43_BASELINE,
                                  head_branch=gate.PR43_BRANCH)
        self.state["authorization"]["task_id"] = gate.PR43_TASK
        self.state["current"].update(base=gate.PR43_BASELINE, head=gate.PR43_PREFIX[-1][1])
        self.state["pr"].update(number=43, head_branch=gate.PR43_BRANCH,
                               base_sha=gate.PR43_BASELINE, head_sha=gate.PR43_PREFIX[-1][1])
        self.state["reviews"] = []
        self.state["pr_ci"] = None
        self.state["corrections"] = [
            {"number": n, "from_head": gate.PR43_PREFIX[n - 2][1] if n > 1 else start,
             "to_head": end, "evidence_ref": self.refs["projection_proposal"] if n == 2 else
             self.refs["closed_gate_proposal"] if n == 3 else "fixture://canonical/" + str(n)}
            for n, (start, end) in enumerate(gate.PR43_PREFIX, 1)]
        self.state["correction_exception"] = {
            "schema": gate.PR43_CORRECTION_SCHEMA, "limit": 5,
            "sources": dict(self.refs), "integration_commits": [], "corrective_head": None}

    def commit(self, parents, name):
        raw = ("tree " + "a" * 40 + "\n" + "".join("parent " + p + "\n" for p in parents)
               + "author Synthetic <fixture@example.invalid> 1 +0000\n"
               + "committer Synthetic <fixture@example.invalid> 1 +0000\n\n" + name + "\n").encode()
        path = self.root / (name + ".commit")
        path.write_bytes(raw)
        sha = hashlib.sha1(b"commit " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        return {"sha": sha, "object_ref": str(path)}

    def candidate(self, separate=False, subsequent=False):
        base = "c" * 40
        first = self.commit([gate.PR43_PREFIX[-1][1], base], "ordinary-integration")
        records = [first]
        if separate:
            # A later authorized develop integration, then the same fifth correction.
            base = "d" * 40
            records.append(self.commit([first["sha"], base], "later-integration"))
            records.append(self.commit([records[-1]["sha"]], "fifth-correction"))
        corrective = records[-1]["sha"]
        if subsequent:
            base = "e" * 40
            records.append(self.commit([corrective, base], "subsequent-ordinary-merge"))
        final = records[-1]
        self.state["correction_exception"]["corrective_head"] = corrective
        self.state["correction_exception"]["integration_commits"] = records
        self.state["corrections"].append({"number": 5, "from_head": gate.PR43_PREFIX[-1][1],
                                          "to_head": final["sha"], "evidence_ref": final["object_ref"]})
        self.state["current"].update(base=base, head=final["sha"])
        self.state["pr"].update(base_sha=base, head_sha=final["sha"])

    def test_retained_fourth_and_fifth_require_fresh_reviews_without_mutation(self):
        initial = deepcopy(self.state)
        for separate, subsequent in ((False, False), (True, False), (False, True)):
            with self.subTest(separate=separate, subsequent=subsequent):
                self.state = deepcopy(initial)
                gate.validate_state(self.state)
                self.candidate(separate, subsequent)
                before = deepcopy(self.state)
                self.assertEqual(gate.next_action(self.state)["action"], "REQUEST_REVIEW_1")
                self.assertEqual(self.state, before)
        retained = deepcopy(self.state)
        retained["corrections"] = retained["corrections"][:4]
        retained["correction_exception"].update(limit=4, integration_commits=[], corrective_head=None)
        del retained["correction_exception"]["sources"]["fifth_authorization"]
        retained["current"].update(head=gate.PR43_PREFIX[-1][1], base=gate.PR43_BASELINE)
        retained["pr"].update(head_sha=gate.PR43_PREFIX[-1][1], base_sha=gate.PR43_BASELINE)
        self.assertEqual(gate._correction_limit(retained), 4)

    def test_wrong_identity_prefix_count_and_sixth_round_refuse(self):
        self.candidate()
        cases = []
        for container, key, value in (("task", "id", "validation-flow-reduction"),
                                     ("task", "baseline", "a" * 40),
                                     ("task", "repository", "other/repo"),
                                     ("task", "head_branch", "other/branch"),
                                     ("pr", "number", 41), ("pr", "head_sha", "b" * 40),
                                     ("current", "base", "e" * 40),
                                     ("correction_exception", "limit", 6),
                                     ("correction_exception", "limit", True)):
            state = deepcopy(self.state); state[container][key] = value; cases.append(state)
        state = deepcopy(self.state); state["corrections"][1]["from_head"] = gate.PR43_PREFIX[1][0]; cases.append(state)
        state = deepcopy(self.state); state["corrections"].append(dict(state["corrections"][-1], number=6)); cases.append(state)
        state = deepcopy(self.state); state["corrections"].pop(0); cases.append(state)
        for state in cases:
            with self.subTest(state=state):
                with self.assertRaises(gate.EvidenceError): gate.validate_state(state)

    def test_every_original_source_missing_or_changed_refuses(self):
        for name, ref in self.refs.items():
            with self.subTest(name=name):
                original = Path(ref).read_bytes()
                Path(ref).write_bytes(original + b"changed")
                with self.assertRaisesRegex(gate.EvidenceError, "source hash differs"):
                    gate.validate_state(self.state)
                Path(ref).write_bytes(original)
                state = deepcopy(self.state)
                state["correction_exception"]["sources"][name] = str(self.root / "absent")
                with self.assertRaisesRegex(gate.EvidenceError, "unavailable"):
                    gate.validate_state(state)

    def test_actual_objects_ordered_parents_base_and_standalone_refuse(self):
        self.candidate()
        original = deepcopy(self.state)
        for parents in (["c" * 40, gate.PR43_PREFIX[-1][1]],
                        [gate.PR43_PREFIX[-1][1]], [gate.PR43_PREFIX[-1][1], "e" * 40],
                        [gate.PR43_PREFIX[-1][1], "c" * 40, "d" * 40]):
            self.state = deepcopy(original)
            record = self.commit(parents, "invalid")
            self.state["correction_exception"]["integration_commits"] = [record]
            self.state["corrections"][-1].update(to_head=record["sha"], evidence_ref=record["object_ref"])
            self.state["current"]["head"] = self.state["pr"]["head_sha"] = record["sha"]
            with self.assertRaises(gate.EvidenceError): gate.validate_state(self.state)
        self.state = deepcopy(original)
        self.state["correction_exception"]["corrective_head"] = "f" * 40
        with self.assertRaisesRegex(gate.EvidenceError, "corrective commit is missing"):
            gate.validate_state(self.state)
        self.state = deepcopy(original)
        sixth = self.commit([self.state["current"]["head"]], "unauthorized-sixth")
        self.state["correction_exception"]["integration_commits"].append(sixth)
        self.state["corrections"][-1].update(to_head=sixth["sha"], evidence_ref=sixth["object_ref"])
        self.state["current"]["head"] = self.state["pr"]["head_sha"] = sixth["sha"]
        with self.assertRaisesRegex(gate.EvidenceError, "only the fifth"):
            gate.validate_state(self.state)
        self.state = original
        ref = self.state["correction_exception"]["integration_commits"][0]["object_ref"]
        Path(ref).write_bytes(Path(ref).read_bytes() + b"tampered")
        with self.assertRaisesRegex(gate.EvidenceError, "object hash differs"):
            gate.validate_state(self.state)

    def test_exhaustion_and_noncode_blockers_keep_original_decisions(self):
        self.candidate()
        for kind, action in (("code", "STOP"), ("evidence", "REFRESH_EVIDENCE"),
                             ("capability", "STOP"), ("contract", "STOP")):
            review = evidence_state()["reviews"][0]
            review.update(baseline=gate.PR43_BASELINE, base=self.state["current"]["base"],
                          head=self.state["current"]["head"])
            block_review(review, kind)
            self.state["reviews"] = [review]
            before = deepcopy(self.state)
            result = gate.next_action(self.state)
            self.assertEqual(result["action"], action)
            if kind == "code": self.assertIn("five corrective rounds exhausted", result["reason"])
            self.assertEqual(before, self.state)

    def test_default_and_pr41_dispatch_remain_separate(self):
        state = evidence_state()
        self.assertEqual(gate._correction_limit(state), 3)
        state["correction_exception"] = {"schema": gate.CORRECTION4_EXCEPTION["schema"]}
        with self.assertRaisesRegex(gate.EvidenceError, "fields must be"):
            gate.validate_state(state)
        state = deepcopy(self.state)
        state["correction_exception"]["schema"] = "pr43-correction-exception/2"
        with self.assertRaises(gate.EvidenceError): gate.validate_state(state)


class Pr43SixthGateTests(unittest.TestCase):
    """Synthetic bytes and explicitly substituted pins; never authentic reports."""
    commit = Pr43CorrectionGateTests.commit
    candidate = Pr43CorrectionGateTests.candidate

    def setUp(self):
        from test_pr_review_gate import review_evidence
        Pr43CorrectionGateTests.setUp(self)
        self.candidate(subsequent=True)
        self.original_pins = dict(gate.PR43_SIXTH_SOURCES)
        ex = self.state["correction_exception"]
        self.old_head = self.state["current"]["head"]
        self.old_base = self.state["current"]["base"]
        for name, value in (("PR43_FIFTH_HEAD", self.old_head), ("PR43_FIFTH_BASE", self.old_base),
                            ("PR43_FIFTH_CORRECTIVE", ex["corrective_head"]), ("PR43_FIFTH_TREE", "a" * 40)):
            patch.object(gate, name, value).start()
        self.state.update(schema=gate.STAGED_SCHEMA, execution={"mode": "validation",
                          "validation_authorization_ref": "fixture://explicit-validation"},
                          coverage=None, short_validation=None)
        current = []
        for number, verdict in ((1, "CODE_REVIEWED"), (2, "CODE_REVIEWED"), (1, "BLOCKED"), (2, "BLOCKED")):
            review = review_evidence(number)
            review.update(baseline=gate.PR43_BASELINE, base=self.old_base, head=self.old_head,
                          verdict=verdict, report_ref=f"fixture://r{number}/{verdict}", ci=None,
                          finalizes_report_ref=None, coverage_sha256=None)
            if verdict == "BLOCKED":
                review.update(blocker_kind="code", findings=["confirmed synthetic code finding " + str(n) for n in range(4)])
                if number == 2:
                    review["ci"] = {"run_id": 37789520018, "attempt": 1,
                                    "tested_sha": "ccb0f970ee3ab4fa707b75ad8c366848746f5a68"}
            current.append(review)
        older = []
        for number in (1, 2):
            review = deepcopy(current[number - 1])
            review.update(head="b" * 40, report_ref=f"fixture://old-r{number}")
            older.append(review)
        self.state["reviews"] = older + current
        manifest = {"ci": {"run_id": 37789520018, "attempt": 1, "event": "pull_request"},
                    "outcome": "failed", "exit_code": 1, "retry_eligible": False,
                    "candidate": {"head": "ccb0f970ee3ab4fa707b75ad8c366848746f5a68",
                                  "tree": "a" * 40, "parents": [self.old_base, self.old_head]}}
        payloads = dict(zip(("r1_code", "r2_code", "r1_blocked", "r2_blocked"), current))
        payloads.update(snapshot=deepcopy(self.state), ci_manifest=manifest)
        self.sixth_refs, pins = {}, {}
        for name in gate.PR43_SIXTH_SOURCES:
            data = (Path(ex["integration_commits"][-1]["object_ref"]).read_bytes() if name == "fifth_object" else
                    json.dumps(payloads[name]).encode() if name in payloads else ("SYNTHETIC ONLY " + name).encode())
            path = self.root / ("sixth-" + name)
            path.write_bytes(data)
            self.sixth_refs[name] = str(path)
            pins[name] = hashlib.sha256(data).hexdigest()
        patch.object(gate, "PR43_SIXTH_SOURCES", pins).start()
        self.state["pr43_review_history_adapter"] = {"contract": gate.PR43_HISTORY_CONTRACT,
                                                    "sources": self.sixth_refs.copy()}
        self.old_state = deepcopy(self.state)

    def sixth(self, before=False, after=False):
        previous, base, records = self.old_head, self.old_base, []
        def merge(name, new_base):
            nonlocal previous, base
            record = self.commit([previous, new_base], name)
            observed = self.root / (name + ".json")
            original = self.root / (name + ".raw")
            original.write_bytes((new_base + "\n").encode("ascii"))
            observed.write_text(json.dumps({"repository": gate.REPOSITORY, "ref": "refs/heads/develop",
                                           "sha": new_base, "evidence_ref": str(original)}), encoding="utf-8")
            record["develop_observation_ref"] = str(observed)
            records.append(record)
            previous, base = record["sha"], new_base
        if before:
            merge("before-sixth", "c" * 40)
        correction = self.commit([previous], "sixth")
        correction["develop_observation_ref"] = None
        records.append(correction)
        previous = correction["sha"]
        if after:
            merge("after-sixth", "d" * 40)
        self.state["correction_exception"] = {"schema": gate.PR43_SIXTH_SCHEMA, "limit": 6,
            "sixth_corrective_head": correction["sha"], "candidate_tree": "a" * 40, "integration_commits": records}
        self.state["corrections"].append({"number": 6, "from_head": self.old_head,
                                         "to_head": previous, "evidence_ref": records[-1]["object_ref"]})
        self.state["current"].update(head=previous, base=base)
        self.state["pr"].update(head_sha=previous, base_sha=base)

    def test_old_blocked_stays_stop_new_candidate_requires_reviews_and_six_is_final(self):
        result = gate.next_action(self.old_state)
        self.assertEqual(result["action"], "STOP")
        self.assertIn("five corrective rounds exhausted", result["reason"])
        for before, after in ((False, False), (True, False), (False, True), (True, True)):
            with self.subTest(before=before, after=after):
                self.state = deepcopy(self.old_state)
                self.sixth(before, after)
                saved = deepcopy(self.state)
                self.assertEqual(gate.next_action(self.state)["action"], "REQUEST_REVIEW_1")
                self.assertEqual(self.state, saved)
                new = deepcopy(self.state["reviews"][4])
                new.update(head=self.state["current"]["head"], base=self.state["current"]["base"],
                           report_ref="fixture://new-blocked")
                self.state["reviews"].append(new)
                result = gate.next_action(self.state)
                self.assertEqual(result["action"], "STOP")
                self.assertIn("six corrective rounds exhausted", result["reason"])

    def test_sixth_exact_identity_prefix_budget_and_unknown_contract_refuse(self):
        self.sixth()
        cases = []
        for container, key, value in (("task", "id", "other"), ("task", "baseline", "f" * 40),
                ("task", "repository", "other/repo"), ("task", "head_branch", "other/branch"),
                ("pr", "number", 42), ("pr", "head_sha", self.old_head), ("pr", "base_sha", "f" * 40),
                ("correction_exception", "limit", 7), ("correction_exception", "limit", True),
                ("correction_exception", "schema", "pr43-correction-exception/3"),
                ("pr43_review_history_adapter", "contract", "pr43-review-history/2")):
            state = deepcopy(self.state); state[container][key] = value; cases.append(state)
        for index in range(5):
            state = deepcopy(self.state); state["corrections"][index]["evidence_ref"] += "/changed"; cases.append(state)
        state = deepcopy(self.state); state["corrections"].append(dict(state["corrections"][-1], number=7)); cases.append(state)
        state = deepcopy(self.state); state["corrections"].pop(); cases.append(state)
        for state in cases:
            with self.subTest(state=state):
                with self.assertRaises(gate.EvidenceError): gate.validate_state(state)

    def test_sixth_raw_objects_tree_order_develop_and_extra_code_refuse(self):
        self.sixth(after=True)
        original = deepcopy(self.state)
        cases = []
        for field, value in (("candidate_tree", "b" * 40), ("sixth_corrective_head", "f" * 40)):
            state = deepcopy(original); state["correction_exception"][field] = value; cases.append(state)
        state = deepcopy(original); state["correction_exception"]["integration_commits"].reverse(); cases.append(state)
        state = deepcopy(original); state["correction_exception"]["integration_commits"][0]["sha"] = "f" * 40; cases.append(state)
        for parents in ([original["current"]["head"]], ["f" * 40, "d" * 40], [original["current"]["head"], "d" * 40, "c" * 40]):
            state = deepcopy(original)
            record = self.commit(parents, "illegal")
            record["develop_observation_ref"] = None
            state["correction_exception"]["integration_commits"].append(record); cases.append(state)
        for state in cases:
            with self.subTest(state=state):
                with self.assertRaises(gate.EvidenceError): gate.validate_state(state)
        record = original["correction_exception"]["integration_commits"][0]
        path = Path(record["object_ref"]); raw = path.read_bytes()
        for tree in (b"tree invalid", b"tree " + b"b" * 40 + b"\ntree " + b"a" * 40):
            malformed = tree + b"\n" + raw.split(b"\n", 1)[1]
            path.write_bytes(malformed)
            sha = hashlib.sha1(b"commit " + str(len(malformed)).encode() + b"\0" + malformed).hexdigest()
            with self.assertRaises(gate.EvidenceError):
                gate._pr43_commit({"sha": sha, "object_ref": str(path)})
        path.write_bytes(raw)
        observation = Path(original["correction_exception"]["integration_commits"][-1]["develop_observation_ref"])
        data = json.loads(observation.read_text()); data["sha"] = "f" * 40
        observation.write_text(json.dumps(data))
        with self.assertRaisesRegex(gate.EvidenceError, "develop second parent"):
            gate.validate_state(original)
        data["sha"] = "d" * 40
        observation.write_text(json.dumps(data))
        Path(data["evidence_ref"]).write_bytes(b"f" * 40 + b"\n")
        with self.assertRaisesRegex(gate.EvidenceError, "raw develop observation SHA"):
            gate.validate_state(original)

    def test_history_all_sources_require_exact_bytes_and_real_allowlist_refuses_fixture(self):
        self.sixth()
        for name, ref in self.sixth_refs.items():
            with self.subTest(name=name):
                path = Path(ref); raw = path.read_bytes(); path.write_bytes(raw + b"tampered")
                with self.assertRaisesRegex(gate.EvidenceError, "source hash differs"):
                    gate.validate_state(self.state)
                path.write_bytes(raw)
                state = deepcopy(self.state); state["pr43_review_history_adapter"]["sources"][name] = str(self.root / "missing")
                with self.assertRaisesRegex(gate.EvidenceError, "unavailable"): gate.validate_state(state)
        with patch.object(gate, "PR43_SIXTH_SOURCES", self.original_pins):
            with self.assertRaisesRegex(gate.EvidenceError, "source hash differs"): gate.validate_state(self.state)

    def test_history_full_prefix_null_relations_findings_and_only_r1_pair_are_immutable(self):
        self.sixth()
        cases = []
        for index in range(6):
            state = deepcopy(self.state); state["reviews"].pop(index); cases.append(state)
        for key, value in (("supersedes_report_ref", "invented"), ("finalizes_report_ref", "invented"),
                           ("coverage_sha256", "a" * 64), ("findings", []), ("ci", {}),
                           ("head", "f" * 40), ("reviewer", "replacement")):
            state = deepcopy(self.state); state["reviews"][4][key] = value; cases.append(state)
        for index in (2, 3, 4):
            state = deepcopy(self.state); extra = deepcopy(state["reviews"][index])
            extra.update(report_ref="fixture://third-record", verdict="PASS", findings=[], blocker_kind=None)
            state["reviews"].append(extra); cases.append(state)
        state = deepcopy(self.state); state["reviews"][2:4] = reversed(state["reviews"][2:4]); cases.append(state)
        state = deepcopy(self.state); del state["pr43_review_history_adapter"]; cases.append(state)
        for state in cases:
            with self.subTest(state=state):
                with self.assertRaises(gate.EvidenceError): gate.validate_state(state)


class Pr43PostmergeGateTests(unittest.TestCase):
    """Finite C7 synthetic objects; patched pins never assert real authorization."""
    commit = Pr43CorrectionGateTests.commit
    candidate = Pr43CorrectionGateTests.candidate
    sixth = Pr43SixthGateTests.sixth

    def setUp(self):
        from test_pr_review_gate import review_evidence
        Pr43SixthGateTests.setUp(self)
        self.sixth(after=True)
        integration, base = self.state["current"]["head"], self.state["current"]["base"]
        merge = self.commit([base, integration], "actual-original-merge")
        for name, value in (("PR43_INTEGRATION_HEAD", integration), ("PR43_MERGE_BASE", base),
                            ("PR43_ACTUAL_MERGE", merge["sha"]), ("PR43_MERGE_TREE", "a" * 40)):
            patch.object(gate, name, value).start()
        codes = []
        for number in (1, 2):
            code = review_evidence(number)
            code.update(baseline=gate.PR43_BASELINE, base=base, head=integration,
                        verdict="CODE_REVIEWED", ci=None, finalizes_report_ref=None,
                        coverage_sha256=None, report_ref=f"fixture://integration-code-{number}")
            codes.append(code)
        self.state["reviews"].extend(codes)
        for code in codes:
            report = deepcopy(code)
            report.update(verdict="PASS", report_ref=code["report_ref"] + "/pass",
                          finalizes_report_ref=code["report_ref"], resolution_evidence_ref="fixture://old-resolution",
                          coverage_sha256="c" * 64, review_mode="evidence_gap", full_diff_reviewed=False)
            if report["round"] == 2:
                report["ci"] = {"run_id": 37873515033, "attempt": 1, "tested_sha": "e" * 40}
            self.state["reviews"].append(report)
        self.state["pr"].update(state="merged", new_blockers=["confirmed original postmerge P1"], protection_satisfied=False)
        self.state["current"]["base"] = merge["sha"]
        self.state["merge"] = {"sha": merge["sha"], "parents": [base, integration], "tree": "a" * 40,
                               "develop_head": merge["sha"], "develop_contains_merge": True,
                               "evidence_ref": "fixture://actual-merge"}
        self.old_merged = deepcopy(self.state)
        short = {"candidate": {"head": merge["sha"], "tree": "a" * 40, "parents": [base, integration]},
                 "ci": {"run_id": 37881982664, "attempt": 1, "event": "push", "job": "short"}, "outcome": "success"}
        finding = {"task": gate.PR43_TASK, "baseline": gate.PR43_BASELINE, "reviewed_head": integration,
                   "actual_merge": merge["sha"], "verdict": "BLOCKED", "blocker_kind": "code",
                   "findings": ["synthetic confirmed P1"], "supersedes_report_ref": None, "finalizes_report_ref": None}
        payloads = {"snapshot": self.old_merged, "short": short, "finding": finding}
        self.post_refs, pins = {}, {}
        self.production_pins = dict(gate.PR43_POSTMERGE_SOURCES)
        for name in gate.PR43_POSTMERGE_SOURCES:
            raw = (Path(merge["object_ref"]).read_bytes() if name == "merge_object" else
                   json.dumps(payloads[name]).encode() if name in payloads else ("SYNTHETIC " + name).encode())
            path = self.root / ("postmerge-" + name); path.write_bytes(raw)
            self.post_refs[name] = str(path); pins[name] = hashlib.sha256(raw).hexdigest()
        patch.object(gate, "PR43_POSTMERGE_SOURCES", pins).start()
        self.c7 = self.commit([merge["sha"]], "seventh-correction")
        self.state["task"]["head_branch"] = gate.PR43_POSTMERGE_BRANCH
        self.state["current"].update(base=merge["sha"], head=self.c7["sha"])
        self.state["corrections"].append({"number": 7, "from_head": merge["sha"],
                                         "to_head": self.c7["sha"], "evidence_ref": self.c7["object_ref"]})
        self.state["correction_exception"] = {"schema": gate.PR43_POSTMERGE_SCHEMA, "limit": 7,
            "sources": self.post_refs.copy(), "candidate": self.c7.copy(), "pr_observation_ref": None}
        self.state.update(pr=None, pr_ci=None, merge=None, push_ci=None, coverage=None, short_validation=None)

    def add_pr(self):
        self.state["pr"] = deepcopy(self.old_merged["pr"])
        self.state["pr"].update(number=47, state="open", head_branch=gate.PR43_POSTMERGE_BRANCH,
                                head_sha=self.c7["sha"], base_sha=gate.PR43_ACTUAL_MERGE,
                                new_blockers=[], findings_checked=True)
        raw = {"number": 47, "base": {"ref": "develop", "sha": gate.PR43_ACTUAL_MERGE,
                "repo": {"full_name": gate.REPOSITORY}}, "head": {"ref": gate.PR43_POSTMERGE_BRANCH,
                "sha": self.c7["sha"], "repo": {"full_name": gate.REPOSITORY}}}
        self.raw_pr = self.root / "pr-get.json"; self.raw_pr.write_text(json.dumps(raw))
        self.raw_list = self.root / "pr-list.json"; self.raw_list.write_text(json.dumps([raw]))
        observation = {"repository": gate.REPOSITORY, "list_evidence_ref": str(self.raw_list),
            "pull_requests": [{**{k: self.state["pr"][k] for k in
                ("number", "base_branch", "head_branch", "base_sha", "head_sha")}, "evidence_ref": str(self.raw_pr)}]}
        self.observation = self.root / "pr-observation.json"; self.observation.write_text(json.dumps(observation))
        self.state["correction_exception"]["pr_observation_ref"] = str(self.observation)

    def new_code(self, number):
        report = deepcopy(self.state["reviews"][6 + number - 1])
        report.update(base=gate.PR43_ACTUAL_MERGE, head=self.c7["sha"], report_ref=f"fixture://C7-code-{number}")
        return report

    def test_pre_pr_and_unique_new_pr_require_both_new_reviews_and_ci(self):
        before = deepcopy(self.state)
        self.assertEqual(gate.next_action(self.state)["action"], "REQUEST_REVIEW_1")
        self.assertEqual(self.state, before)
        self.state["reviews"].append(self.new_code(1))
        self.assertEqual(gate.next_action(self.state)["action"], "ENSURE_DRAFT_PR")
        self.add_pr()
        self.assertEqual(gate.next_action(self.state)["action"], "REQUEST_REVIEW_2")
        self.state["reviews"].append(self.new_code(2))
        self.assertEqual(gate.next_action(self.state)["action"], "WAIT_PR_CI")
        self.assertEqual(self.state["reviews"][:10], before["reviews"])
        self.assertEqual(self.state["corrections"][:6], before["corrections"][:6])

    def test_identity_prefix_eighth_and_old_merge_cannot_clear_blocker(self):
        self.add_pr()
        cases = []
        for container, key, value in (("task", "id", "other"), ("task", "repository", "other/repo"),
                ("task", "baseline", "f" * 40), ("task", "head_branch", gate.PR43_BRANCH),
                ("current", "head", gate.PR43_ACTUAL_MERGE), ("current", "base", "f" * 40),
                ("pr", "number", 43), ("correction_exception", "limit", 8),
                ("correction_exception", "limit", True), ("correction_exception", "schema", "pr43-postmerge-correction/2")):
            state = deepcopy(self.state); state[container][key] = value; cases.append(state)
        for index in range(6):
            state = deepcopy(self.state); state["corrections"][index]["evidence_ref"] += "/changed"; cases.append(state)
        for index in range(10):
            state = deepcopy(self.state); state["reviews"].pop(index); cases.append(state)
        state = deepcopy(self.state); state["corrections"].append(dict(state["corrections"][-1], number=8)); cases.append(state)
        state = deepcopy(self.state); state["corrections"][6]["from_head"] = gate.PR43_INTEGRATION_HEAD; cases.append(state)
        state = deepcopy(self.state); state["implementers"].pop(); cases.append(state)
        for state in cases:
            with self.subTest(state=state):
                with self.assertRaises(gate.EvidenceError): gate.validate_state(state)
        self.assertEqual(gate.next_action(self.old_merged)["action"], "STOP")
        self.state["pr"]["new_blockers"] = ["confirmed C7 code blocker"]
        result = gate.next_action(self.state)
        self.assertEqual(result["action"], "STOP")
        self.assertIn("seven corrective rounds exhausted", result["reason"])

    def test_raw_candidate_parents_hash_tree_and_scope_refuse(self):
        original = deepcopy(self.state)
        for parents in ([gate.PR43_INTEGRATION_HEAD], [gate.PR43_ACTUAL_MERGE, "f" * 40], ["f" * 40]):
            self.state = deepcopy(original)
            record = self.commit(parents, "bad-candidate-" + str(len(parents)))
            self.state["correction_exception"]["candidate"] = record
            self.state["corrections"][6].update(to_head=record["sha"], evidence_ref=record["object_ref"])
            self.state["current"]["head"] = record["sha"]
            with self.assertRaises(gate.EvidenceError): gate.validate_state(self.state)
        self.state = deepcopy(original)
        self.state["reviews"].append(deepcopy(self.state["reviews"][8]))
        self.state["reviews"][-1]["report_ref"] += "/fake-new-pass"
        with self.assertRaises(gate.EvidenceError): gate.validate_state(self.state)
        self.state = deepcopy(original)
        Path(self.c7["object_ref"]).write_bytes(Path(self.c7["object_ref"]).read_bytes() + b"tamper")
        with self.assertRaisesRegex(gate.EvidenceError, "object hash differs"): gate.validate_state(self.state)

    def test_all_pinned_sources_missing_corrupt_or_real_allowlist_refuse(self):
        for name, ref in self.post_refs.items():
            with self.subTest(name=name):
                path = Path(ref); raw = path.read_bytes(); path.write_bytes(raw + b"tamper")
                with self.assertRaisesRegex(gate.EvidenceError, "source hash differs"): gate.validate_state(self.state)
                path.write_bytes(raw)
                state = deepcopy(self.state); state["correction_exception"]["sources"][name] = str(self.root / "missing")
                with self.assertRaisesRegex(gate.EvidenceError, "unavailable"): gate.validate_state(state)
        with patch.object(gate, "PR43_POSTMERGE_SOURCES", self.production_pins):
            with self.assertRaisesRegex(gate.EvidenceError, "source hash differs"): gate.validate_state(self.state)

    def test_pr_raw_get_list_number_repository_base_head_and_duplicates_refuse(self):
        self.add_pr()
        original = deepcopy(self.state)
        raw = json.loads(self.raw_pr.read_text())
        list_row = {**raw, "list_only_metadata": "synthetic API shape difference"}
        self.raw_list.write_text(json.dumps([list_row]))
        gate.validate_state(self.state)
        self.raw_list.write_text(json.dumps([raw]))
        variants = []
        for section, field, value in (("base", "sha", "f" * 40), ("head", "sha", "f" * 40),
                                     ("base", "ref", "main"), ("head", "ref", gate.PR43_BRANCH),
                                     ("base", "repo", {"full_name": "other/repo"}),
                                     ("head", "repo", {"full_name": "other/repo"})):
            value_raw = deepcopy(raw); value_raw[section][field] = value; variants.append(value_raw)
        for number in (43, 48, True):
            value_raw = deepcopy(raw); value_raw["number"] = number; variants.append(value_raw)
        for value_raw in variants:
            self.raw_pr.write_text(json.dumps(value_raw)); self.raw_list.write_text(json.dumps([value_raw]))
            with self.assertRaises(gate.EvidenceError): gate.validate_state(self.state)
        self.raw_pr.write_text(json.dumps(raw))
        for rows in ([], [raw, raw]):
            self.raw_list.write_text(json.dumps(rows))
            with self.assertRaises(gate.EvidenceError): gate.validate_state(self.state)
        self.raw_list.write_text(json.dumps([raw])); self.raw_pr.unlink()
        with self.assertRaisesRegex(gate.EvidenceError, "unavailable"): gate.validate_state(original)

    def test_pre_pr_pass_and_old_ci_reuse_and_new_scope_code_remain_blocked(self):
        for field in ("pr_ci", "push_ci", "merge", "coverage", "short_validation"):
            state = deepcopy(self.state); state[field] = "invented"
            with self.assertRaises(gate.EvidenceError): gate.validate_state(state)
        state = deepcopy(self.state)
        report = self.new_code(1); report.update(verdict="PASS", coverage_sha256="a" * 64)
        state["reviews"].append(report)
        with self.assertRaisesRegex(gate.EvidenceError, "pre-PR cannot claim formal PASS"): gate.validate_state(state)
        self.add_pr()
        for run in (37873515033, 37881982664):
            state = deepcopy(self.state); state["pr_ci"] = {"run_id": run}
            with self.assertRaisesRegex(gate.EvidenceError, "original CI cannot"): gate.validate_state(state)
        report = self.new_code(1); block_review(report)
        self.state["reviews"].append(report)
        result = gate.next_action(self.state)
        self.assertEqual(result["action"], "STOP")
        self.assertIn("seven corrective rounds exhausted", result["reason"])
