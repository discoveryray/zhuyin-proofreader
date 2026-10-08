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
