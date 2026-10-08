"""Technical synthetic adapter tests; real original-byte acceptance is local evidence.

Only fixture digest allowlists are substituted. No fixture is a human report,
actual CI result, textbook asset or authentic authorization.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import pr_review_gate as gate
from test_pr_review_gate import evidence_state, review_evidence


class CFFHistoryAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        state = evidence_state()
        state.update(schema=gate.STAGED_SCHEMA, task=dict(gate.CFF_HISTORY_TASK),
                     pr=None, pr_ci=None, coverage=None, short_validation=None,
                     execution={"mode": "validation", "validation_authorization_ref": "fixture://start"})
        state["authorization"]["task_id"] = state["task"]["id"]
        state["current"].update(base=state["task"]["baseline"], head=gate.CFF_HISTORY_HEAD)
        refs = list(gate.CFF_HISTORY_DIGESTS)[2:]
        reports = []
        for blocked in (False, True):
            for number in (1, 2):
                report = review_evidence(number)
                report.update(baseline=state["task"]["baseline"], base=state["task"]["baseline"],
                              head=gate.CFF_HISTORY_HEAD, ci=None, coverage_sha256=None,
                              finalizes_report_ref=None, report_ref=refs[len(reports)],
                              verdict="BLOCKED" if blocked else "CODE_REVIEWED",
                              blocker_kind="evidence" if blocked else None,
                              findings=["synthetic missing evidence"] if blocked else [])
                reports.append(report)
        for number in range(4):
            report = deepcopy(reports[number % 2])
            report.update(head=("c" if number < 2 else "d") * 40,
                          report_ref=f"fixture://later/{number}")
            reports.append(report)
        state["reviews"] = reports
        state["corrections"] = [{"number": n, "from_head": before, "to_head": after,
                                "evidence_ref": "fixture://correction"}
                               for n, before, after in ((1, gate.CFF_HISTORY_HEAD, "c" * 40),
                                                        (2, "c" * 40, "d" * 40))]
        sources, digests = {}, {}
        for index, name in enumerate(gate.CFF_HISTORY_DIGESTS):
            path = root / f"source-{index}.json"
            payload = json.dumps(state).encode() if name == "snapshot" else b"synthetic technical fixture " + str(index).encode()
            path.write_bytes(payload)
            sources[name] = str(path)
            digests[name] = hashlib.sha256(payload).hexdigest()
        state["cff_review_history_adapter"] = {"contract": "cff-review-history/1", "sources": sources}
        self.state, self.digests = state, digests

    def test_synthetic_structure_keeps_blocked_and_never_produces_pass(self):
        original = deepcopy(self.state)
        with patch.object(gate, "CFF_HISTORY_DIGESTS", self.digests):
            gate.validate_state(self.state)
            for number in (1, 2):
                self.assertEqual(gate._review(self.state, number, self.state["task"]["baseline"],
                                             gate.CFF_HISTORY_HEAD)["verdict"], "BLOCKED")
            self.assertEqual(gate.next_action(self.state)["action"], "REFRESH_EVIDENCE")
            self.state["current"]["head"] = "e" * 40
            self.assertEqual(gate.next_action(self.state)["action"], "REQUEST_REVIEW_1")
            self.state["current"] = original["current"]
        self.assertEqual(self.state, original)
        without = deepcopy(self.state)
        del without["cff_review_history_adapter"]
        with self.assertRaisesRegex(gate.EvidenceError, "ambiguous"):
            gate.validate_state(without)

    def test_real_digest_allowlist_rejects_synthetic_and_corrupt_sources(self):
        with self.assertRaisesRegex(gate.EvidenceError, "hash differs"):
            gate.validate_state(self.state)
        with patch.object(gate, "CFF_HISTORY_DIGESTS", self.digests):
            for name, filename in self.state["cff_review_history_adapter"]["sources"].items():
                path = Path(filename)
                original = path.read_bytes()
                with self.subTest(source=name):
                    path.write_bytes(original + b"corrupt")
                    with self.assertRaisesRegex(gate.EvidenceError, "hash differs"):
                        gate.validate_state(self.state)
                    path.unlink()
                    with self.assertRaisesRegex(gate.EvidenceError, "unavailable"):
                        gate.validate_state(self.state)
                    path.write_bytes(original)

    def test_unknown_scope_relations_code_blockers_and_duplicate_pass_stay_closed(self):
        mutations = [lambda s: s.update(pr={"number": 99}),
                     lambda s: s["task"].update(id="other"),
                     lambda s: s["task"].update(baseline="e" * 40),
                     lambda s: s["task"].update(head_branch="other"),
                     lambda s: s["cff_review_history_adapter"].update(contract="future"),
                     lambda s: s["cff_review_history_adapter"].update(extra=True),
                     lambda s: s["reviews"][2].update(blocker_kind="code"),
                     lambda s: s["reviews"][2].update(findings=["changed"]),
                     lambda s: s["reviews"][2].update(head="e" * 40),
                     lambda s: s["reviews"][2].update(supersedes_report_ref=s["reviews"][0]["report_ref"]),
                     lambda s: s["reviews"].reverse(),
                     lambda s: s["reviews"].pop(0),
                     lambda s: s["corrections"].pop(),
                     lambda s: s["reviews"].append(dict(s["reviews"][0], report_ref="unknown")),
                     lambda s: s["reviews"].append(dict(s["reviews"][0], report_ref="fake-pass", verdict="PASS"))]
        with patch.object(gate, "CFF_HISTORY_DIGESTS", self.digests):
            for mutate in mutations:
                with self.subTest(mutation=mutations.index(mutate)):
                    state = deepcopy(self.state)
                    mutate(state)
                    with self.assertRaises(gate.EvidenceError):
                        gate.validate_state(state)
            state = deepcopy(self.state)
            state["corrections"].append({"number": 3, "from_head": "d" * 40,
                                         "to_head": "e" * 40, "evidence_ref": "fixture://third"})
            state["current"]["head"] = "e" * 40
            blocked = dict(state["reviews"][0], head="e" * 40, report_ref="fixture://new-code",
                           verdict="BLOCKED", blocker_kind="code", findings=["confirmed fixture defect"])
            state["reviews"].append(blocked)
            self.assertEqual(gate.next_action(state)["action"], "STOP")
            self.assertIn("exhausted", gate.next_action(state)["reason"])
