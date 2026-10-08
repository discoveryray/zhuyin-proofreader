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


class CFFFourthRoundAdapterTests(unittest.TestCase):
    """Separate synthetic source bytes, never copies of private human reports."""

    def setUp(self):
        CFFHistoryAdapterTests.setUp(self)
        state = self.state
        state["current"].update(base=gate.CFF_CORRECTION4_EXCEPTION["base"],
                                head=gate.CFF_CORRECTION4_EXCEPTION["starting_head"])
        state["pr"] = evidence_state()["pr"]
        state["pr"].update(number=42, base_branch="develop", head_branch=state["task"]["head_branch"],
                           base_sha=state["current"]["base"], head_sha=state["current"]["head"])
        state["corrections"].append({"number": 3, "from_head": "d" * 40,
                                     "to_head": state["current"]["head"], "evidence_ref": "fixture://third"})
        code = dict(state["reviews"][0], base=state["current"]["base"], head=state["current"]["head"],
                    report_ref="fixture://third-code")
        blocked = dict(code, report_ref="fixture://third-blocked", verdict="BLOCKED", blocker_kind="contract",
                       findings=["synthetic retained contract restriction"],
                       ci={"run_id": 1, "attempt": 1, "tested_sha": "f" * 40})
        state["reviews"].extend((code, blocked))
        projection = dict(blocked, ci=None)
        payloads = {"snapshot3": state, "code_json": code, "blocked_json": blocked, "projection": projection}
        self.v2_digests = {}
        sources = state["cff_review_history_adapter"]["sources"]
        for name in gate.CFF_HISTORY_V2_DIGESTS:
            raw = json.dumps(payloads[name]).encode() if name in payloads else ("synthetic " + name).encode()
            path = Path(self.temp.name) / (name + ".json")
            path.write_bytes(raw)
            sources[name] = str(path)
            self.v2_digests[name] = hashlib.sha256(raw).hexdigest()
        state["reviews"][-1] = projection
        state["cff_review_history_adapter"]["contract"] = "cff-review-history/2"
        self.exception = dict(gate.CFF_CORRECTION4_EXCEPTION,
                              authorization_sha256=self.v2_digests["authorization4"])
        state["correction_exception"] = dict(self.exception, authorization_ref=sources["authorization4"])
        for name, value in (("CFF_HISTORY_DIGESTS", self.digests),
                            ("CFF_HISTORY_V2_DIGESTS", self.v2_digests),
                            ("CFF_CORRECTION4_EXCEPTION", self.exception)):
            patcher = patch.object(gate, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def fourth(self):
        state = deepcopy(self.state)
        state["corrections"].append({"number": 4, "from_head": state["current"]["head"],
                                     "to_head": "e" * 40, "evidence_ref": "fixture://fourth"})
        state["current"]["head"] = state["pr"]["head_sha"] = "e" * 40
        return state

    def test_exact_projection_preserves_blocker_and_fourth_round_stops_at_four(self):
        before = deepcopy(self.state)
        gate.validate_state(self.state)
        self.assertEqual(gate.next_action(self.state)["action"], "STOP")
        self.assertEqual(gate._review(self.state, 1, self.state["current"]["base"],
                                     self.state["current"]["head"])["blocker_kind"], "contract")
        self.assertEqual(self.state, before)
        state = self.fourth()
        gate.validate_state(state)
        self.assertEqual(gate.next_action(state)["action"], "REQUEST_REVIEW_1")
        state["reviews"].append(dict(state["reviews"][-1], head="e" * 40,
                                     report_ref="fixture://fourth-code-failure", blocker_kind="code"))
        decision = gate.next_action(state)
        self.assertEqual(decision["action"], "STOP")
        self.assertIn("four corrective rounds exhausted", decision["reason"])

    def test_original_nonnull_ci_still_rejected(self):
        state = deepcopy(self.state)
        state["reviews"][-1]["ci"] = {"run_id": 1, "attempt": 1, "tested_sha": "f" * 40}
        with self.assertRaises(gate.EvidenceError):
            gate.validate_state(state)
        del state["correction_exception"]
        with self.assertRaisesRegex(gate.EvidenceError, "round 1 does not attest"):
            gate.validate_state(state)

    def test_identity_prefix_relations_duplicates_and_limit_fail_closed(self):
        mutations = [lambda s: s.update(reviews=None),
                     lambda s: s["task"].update(id="other"),
                     lambda s: s["task"].update(baseline="f" * 40),
                     lambda s: s["pr"].update(number=43),
                     lambda s: s["pr"].update(number=41),
                     lambda s: s["pr"].update(base_sha="f" * 40),
                     lambda s: s["pr"].update(head_branch="other"),
                     lambda s: s["current"].update(base="f" * 40),
                     lambda s: s["current"].update(head="f" * 40),
                     lambda s: s["corrections"][0].update(evidence_ref="changed"),
                     lambda s: s["corrections"][2].update(to_head="f" * 40),
                     lambda s: s["correction_exception"].update(schema="cff-correction-exception/2"),
                     lambda s: s["correction_exception"].update(limit=5),
                     lambda s: s["correction_exception"].update(extra_rounds=True),
                     lambda s: s["correction_exception"].update(authorization_sha256="0" * 64),
                     lambda s: s["correction_exception"].update(authorization_ref="missing-file"),
                     lambda s: s["cff_review_history_adapter"].update(contract="cff-review-history/3"),
                     lambda s: s["cff_review_history_adapter"]["sources"].pop("authorization4"),
                     lambda s: s["reviews"][-1].update(findings=["changed"]),
                     lambda s: s["reviews"][-1].update(reviewer="different"),
                     lambda s: s["reviews"][-1].update(scope="other"),
                     lambda s: s["reviews"][-1].update(report_ref="other"),
                     lambda s: s["reviews"][-1].update(blocker_kind="code"),
                     lambda s: s["reviews"][-1].update(supersedes_report_ref=s["reviews"][-2]["report_ref"]),
                     lambda s: s["reviews"][-1].update(finalizes_report_ref=s["reviews"][-2]["report_ref"]),
                     lambda s: s["reviews"].append(dict(s["reviews"][-1], report_ref="third-duplicate")),
                     lambda s: s["reviews"].append(dict(s["reviews"][-2], report_ref="duplicate-pass", verdict="PASS"))]
        for index, mutate in enumerate(mutations):
            with self.subTest(mutation=index):
                state = deepcopy(self.state)
                mutate(state)
                with self.assertRaises(gate.EvidenceError):
                    gate.validate_state(state)
        for field, value in (("from_head", "f" * 40), ("to_head", self.state["current"]["head"]),
                             ("number", 5)):
            with self.subTest(fourth_field=field):
                state = self.fourth()
                state["corrections"][-1][field] = value
                with self.assertRaises(gate.EvidenceError):
                    gate.validate_state(state)
        state = self.fourth()
        state["corrections"].append({"number": 5, "from_head": "e" * 40,
                                     "to_head": "f" * 40, "evidence_ref": "fixture://forbidden"})
        with self.assertRaises(gate.EvidenceError):
            gate.validate_state(state)

    def test_all_new_originals_and_projection_bytes_are_required(self):
        for name in self.v2_digests:
            path = Path(self.state["cff_review_history_adapter"]["sources"][name])
            raw = path.read_bytes()
            with self.subTest(source=name):
                path.write_bytes(raw + b"corrupt")
                with self.assertRaises(gate.EvidenceError):
                    gate.validate_state(self.state)
                path.unlink()
                with self.assertRaises(gate.EvidenceError):
                    gate.validate_state(self.state)
                path.write_bytes(raw)

    def test_projection_rule_rejects_multiple_changes_even_with_fixture_digest(self):
        path = Path(self.state["cff_review_history_adapter"]["sources"]["projection"])
        projection = json.loads(path.read_bytes())
        projection["findings"] = ["unauthorized change"]
        raw = json.dumps(projection).encode()
        path.write_bytes(raw)
        self.v2_digests["projection"] = hashlib.sha256(raw).hexdigest()
        self.state["reviews"][-1] = projection
        with self.assertRaisesRegex(gate.EvidenceError, "only original ci"):
            gate.validate_state(self.state)
