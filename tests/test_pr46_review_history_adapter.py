"""Synthetic contracts only; substituted digests never authenticate human evidence."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import pr_review_gate as gate
from test_pr_review_gate import evidence_state, review_evidence


class PR46HistoryAdapterTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        s = evidence_state()
        s.update(schema=gate.STAGED_SCHEMA, task=dict(gate.PR46_HISTORY_TASK), pr_ci=None,
                 coverage=None, short_validation=None,
                 execution={"mode": "validation", "validation_authorization_ref": "fixture://start"})
        s["authorization"]["task_id"] = s["task"]["id"]
        s["current"].update(base=gate.PR46_HISTORY_BASE, head=gate.PR46_HISTORY_HEAD)
        s["pr"].update(number=46, head_branch=s["task"]["head_branch"],
                        base_sha=gate.PR46_HISTORY_BASE, head_sha=gate.PR46_HISTORY_HEAD)
        code = dict(review_evidence(1), baseline=s["task"]["baseline"], base=gate.PR46_HISTORY_BASE,
                    head=gate.PR46_HISTORY_HEAD, verdict="CODE_REVIEWED", ci=None,
                    coverage_sha256=None, finalizes_report_ref=None, report_ref="fixture://r1-code")
        r2 = dict(code, round=2, reviewer="independent-agent-2", report_ref="fixture://r2-code",
                  scope="cumulative_and_full_pr_with_ci")
        blocked = dict(code, verdict="BLOCKED", blocker_kind="code", findings=["confirmed synthetic defect"],
                       report_ref="fixture://r1-blocked")
        s["reviews"] = [dict(code, head="d" * 40, report_ref="fixture://old1"),
                        dict(code, head="e" * 40, report_ref="fixture://old2"), code, r2, blocked]
        manifest = {"ci": {"run_id": 37804577686, "attempt": 1, "job": "grouped", "event": "pull_request"},
                    "candidate": {"head": "843621ba2c47e7cdeb19c46ad34746bf3fbb2e4d",
                                  "tree": "0e647bd2231517a85ada8352d3381b20f18fe6ab",
                                  "parents": [gate.PR46_HISTORY_BASE, gate.PR46_HISTORY_HEAD]},
                    "exit_code": 1, "outcome": "failed", "retry_eligible": False}
        payloads = {"snapshot": deepcopy(s), "code_projection": code,
                    "blocked_projection": blocked, "manifest": manifest}
        sources, digests = {}, {}
        for index, name in enumerate(gate.PR46_HISTORY_DIGESTS):
            raw = json.dumps(payloads[name]).encode() if name in payloads else b"synthetic source " + str(index).encode()
            path = root / (name + ".json")
            path.write_bytes(raw)
            sources[name] = str(path)
            digests[name] = hashlib.sha256(raw).hexdigest()
        s["pr46_review_history_adapter"] = {"contract": "pr46-review-history/1", "sources": sources}
        self.state, self.digests = s, digests

    def test_exact_pair_retains_blocker_and_r2_uses_original_ci_key(self):
        before = deepcopy(self.state)
        with patch.object(gate, "PR46_HISTORY_DIGESTS", self.digests):
            gate.validate_state(self.state)
            self.assertEqual(gate._review(self.state, 1, gate.PR46_HISTORY_BASE, gate.PR46_HISTORY_HEAD),
                             self.state["reviews"][4])
            self.assertEqual(gate.next_action(self.state)["action"], "CORRECT_IMPLEMENTATION")
            r2 = dict(self.state["reviews"][3], report_ref="fixture://r2-blocked", verdict="BLOCKED",
                      blocker_kind="code", findings=["confirmed synthetic defect"],
                      ci={"run_id": 37804577686, "attempt": 1,
                          "tested_sha": "843621ba2c47e7cdeb19c46ad34746bf3fbb2e4d"})
            dual = deepcopy(self.state)
            dual["reviews"].append(r2)
            gate.validate_state(dual)
            self.assertEqual(gate._review(dual, 2, gate.PR46_HISTORY_BASE, gate.PR46_HISTORY_HEAD), r2)
            self.assertEqual(gate._pr46_history_pairs(dual),
                             {frozenset(("fixture://r1-code", "fixture://r1-blocked"))})
        self.assertEqual(self.state, before)

    def test_new_head_needs_fresh_reviews_and_normal_three_round_stop(self):
        s = deepcopy(self.state)
        s["current"]["head"] = s["pr"]["head_sha"] = "f" * 40
        s["corrections"] = [{"number": 1, "from_head": gate.PR46_HISTORY_HEAD,
                              "to_head": "f" * 40, "evidence_ref": "fixture://first"}]
        with patch.object(gate, "PR46_HISTORY_DIGESTS", self.digests):
            self.assertEqual(gate.next_action(s)["action"], "REQUEST_REVIEW_1")
            s["reviews"].append(dict(s["reviews"][2], head="f" * 40, report_ref="fixture://new-r1"))
            self.assertEqual(gate.next_action(s)["action"], "REQUEST_REVIEW_2")
            s["reviews"].append(dict(s["reviews"][3], head="f" * 40, report_ref="fixture://new-r2"))
            self.assertEqual(gate.next_action(s)["action"], "WAIT_PR_CI")
            for number, before, after in [(2, "f" * 40, "9" * 40), (3, "9" * 40, "8" * 40)]:
                s["corrections"].append({"number": number, "from_head": before, "to_head": after,
                                          "evidence_ref": "fixture://correction"})
            s["current"]["head"] = s["pr"]["head_sha"] = "8" * 40
            s["reviews"].append(dict(s["reviews"][4], head="8" * 40, report_ref="fixture://third-blocked"))
            result = gate.next_action(s)
            self.assertEqual(result["action"], "STOP")
            self.assertIn("exhausted", result["reason"])
            s["corrections"].append({"number": 4, "from_head": "8" * 40,
                                      "to_head": "7" * 40, "evidence_ref": "fixture://fourth"})
            with self.assertRaises(gate.EvidenceError):
                gate.validate_state(s)

    def test_real_pins_reject_fixtures_and_all_missing_or_corrupt_sources(self):
        with self.assertRaisesRegex(gate.EvidenceError, "hash differs"):
            gate.validate_state(self.state)
        with patch.object(gate, "PR46_HISTORY_DIGESTS", self.digests):
            for name, filename in self.state["pr46_review_history_adapter"]["sources"].items():
                with self.subTest(source=name):
                    path = Path(filename)
                    raw = path.read_bytes()
                    path.write_bytes(raw + b"corrupt")
                    with self.assertRaisesRegex(gate.EvidenceError, "hash differs"):
                        gate.validate_state(self.state)
                    path.unlink()
                    with self.assertRaisesRegex(gate.EvidenceError, "unavailable"):
                        gate.validate_state(self.state)
                    path.write_bytes(raw)

    def test_task_scope_prefix_relations_and_unknown_adapter_fail_closed(self):
        mutations = [lambda s: s["task"].update(id="other"),
                     lambda s: s["task"].update(baseline="0" * 40),
                     lambda s: s["task"].update(head_branch="other"),
                     lambda s: s["pr"].update(number=41),
                     lambda s: s["pr"].update(number=42),
                     lambda s: s["pr"].update(number=43),
                     lambda s: s["pr"].update(head_branch="other"),
                     lambda s: s.update(pr=None),
                     lambda s: s.update(pr46_review_history_adapter=None),
                     lambda s: s["pr46_review_history_adapter"].update(contract="future"),
                     lambda s: s["pr46_review_history_adapter"].update(extra=True),
                     lambda s: s["pr46_review_history_adapter"]["sources"].pop("authorization"),
                     lambda s: s["pr46_review_history_adapter"]["sources"].update(extra="unknown"),
                     lambda s: s["reviews"].pop(0), lambda s: s["reviews"].reverse(),
                     lambda s: s["reviews"][4].update(findings=["changed"]),
                     lambda s: s["reviews"][4].update(base="0" * 40),
                     lambda s: s["reviews"][4].update(head="0" * 40),
                     lambda s: s["reviews"][4].update(scope="other"),
                     lambda s: s["reviews"][4].update(report_ref="other"),
                     lambda s: s["reviews"][4].update(reviewer="other"),
                     lambda s: s["reviews"][4].update(supersedes_report_ref="fixture://r1-code"),
                     lambda s: s["reviews"][4].update(finalizes_report_ref="fixture://r1-code"),
                     lambda s: s["reviews"][4].update(resolution_evidence_ref="invented")]
        with patch.object(gate, "PR46_HISTORY_DIGESTS", self.digests):
            for index, mutate in enumerate(mutations):
                with self.subTest(mutation=index):
                    s = deepcopy(self.state)
                    mutate(s)
                    with self.assertRaises(gate.EvidenceError):
                        gate.validate_state(s)

    def test_no_allowance_for_missing_adapter_duplicate_ref_third_pair_or_pass(self):
        s = deepcopy(self.state)
        del s["pr46_review_history_adapter"]
        with self.assertRaisesRegex(gate.EvidenceError, "ambiguous"):
            gate.validate_state(s)
        with patch.object(gate, "PR46_HISTORY_DIGESTS", self.digests):
            for extra in [deepcopy(self.state["reviews"][2]),
                          dict(self.state["reviews"][2], report_ref="fixture://third"),
                          dict(self.state["reviews"][2], report_ref="fixture://pass", verdict="PASS",
                               coverage_sha256="0" * 64)]:
                with self.subTest(extra=extra["report_ref"]):
                    s = deepcopy(self.state)
                    s["reviews"].append(extra)
                    with self.assertRaises(gate.EvidenceError):
                        gate.validate_state(s)
