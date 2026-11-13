"""Finite activation wiring with real Git/CLI and synthetic hosted evidence.

No fixture represents user approval, hosted execution or policy activation.
The existing current coverage/short consumers run without success mocks.
"""
from copy import deepcopy
from contextlib import chdir
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

from scripts import pr_flow_policy as policy
from scripts import pr_review_gate as gate
from scripts import validation_evidence as evidence
from test_current_validation_evidence import current_short
import test_pr_flow_policy as review_fixtures
from test_pr_review_gate import BASE, HEAD, MERGE, TREE, SYNTHETIC, evidence_state


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tmp/pfa"


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def bind_short(root, values):
    """Rebind the existing explicitly synthetic full/short fixture to real Git."""
    coverage, short = current_short(root)
    for path in root.rglob("*"):
        if path.is_file() and path.suffix in {".json", ".log"}:
            raw = path.read_bytes()
            for old, new in values.items():
                raw = raw.replace(old.encode(), new.encode())
            path.write_bytes(raw)

    def hashes(value, parent):
        if type(value) is dict:
            if set(value) == {"path", "sha256"}:
                value["sha256"] = evidence.sha256_file(parent / value["path"])
            else:
                for item in value.values(): hashes(item, parent)
        elif type(value) is list:
            for item in value: hashes(item, parent)
    current = evidence.load_json(coverage)
    hashes(current, coverage.parent)
    write(coverage, current)
    with zipfile.ZipFile(root / "pr-artifact.zip", "w") as archive:
        for path in sorted(coverage.parent.rglob("*")):
            if path.is_file(): archive.write(path, path.relative_to(coverage.parent).as_posix())
    artifact = evidence.load_json(root / "artifact.json")
    artifact["digest"] = "sha256:" + evidence.sha256_file(root / "pr-artifact.zip")
    write(root / "artifact.json", artifact)
    result = evidence.load_json(short)
    result["coverage_sha256"] = evidence.sha256_file(coverage)
    hashes(result, root)
    write(short, result)
    return coverage, short


class ActivationPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        OUT.mkdir(parents=True, exist_ok=True)
        cls.temporary = tempfile.TemporaryDirectory(prefix="git-", dir=OUT)
        cls.repo = Path(cls.temporary.name).resolve()
        cls.git("init", "-q")
        common = subprocess.check_output(["git", "--no-optional-locks", "-c", "safe.directory=" + str(ROOT),
                                          "rev-parse", "--git-common-dir"], cwd=ROOT, text=True).strip()
        objects = (ROOT / common / "objects").resolve()
        alternates = cls.repo / ".git/objects/info/alternates"
        alternates.write_text(objects.as_posix() + "\n", encoding="utf-8", newline="\n")
        cls.git("read-tree", policy.ACTIVATION_BASELINE)
        for name in ("pr_flow_policy.py", "pr_review_gate.py"):
            raw = (ROOT / "scripts" / name).read_bytes().replace(b"\r\n", b"\n")
            blob = cls.git("hash-object", "-w", "--stdin", payload=raw).decode().strip()
            cls.git("update-index", "--add", "--cacheinfo", "100644," + blob + ",scripts/" + name)
        cls.tree = cls.git("write-tree").decode().strip()
        cls.feature = cls.git("commit-tree", cls.tree, "-p", policy.ACTIVATION_BASELINE,
                              payload=b"synthetic activation feature\n").decode().strip()
        cls.merge = cls.git("commit-tree", cls.tree, "-p", policy.ACTIVATION_BASELINE, "-p", cls.feature,
                            payload=b"synthetic activation merge\n").decode().strip()
        cls.tested = cls.git("commit-tree", cls.tree, "-p", policy.ACTIVATION_BASELINE, "-p", cls.feature,
                             payload=b"synthetic activation PR integration\n").decode().strip()

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    @classmethod
    def git(cls, *args, payload=None):
        env = dict(os.environ, GIT_AUTHOR_NAME="Synthetic fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
                   GIT_COMMITTER_NAME="Synthetic fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid")
        result = subprocess.run(["git", "--no-optional-locks", "-c", "safe.directory=" + str(cls.repo), *args],
                                cwd=cls.repo, env=env, input=payload, capture_output=True)
        if result.returncode: raise AssertionError(result.stderr.decode(errors="replace"))
        return result.stdout

    def setUp(self):
        # Composition avoids re-executing inherited old test methods.
        self.fixture = review_fixtures.TieredReviewPolicyTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.activation_serial = 0

    def source(self, value, ref):
        return self.fixture.original(value, ref)

    def activated(self, tier="local", *, merged=False):
        state = self.fixture.full_merged_lifecycle() if merged else self.fixture.lifecycle_state(tier)
        state["schema"] = gate.ACTIVATED_TIERED_SCHEMA
        state["candidate"]["baseline"] = state["task_policy"]["baseline"] = self.merge
        state["task_policy"]["source_ref"] = "fixture://task-creation-and-adoption-source"
        for pointer in state["review_originals"]:
            report = json.loads(Path(pointer["source_file"]).read_text())
            report["candidate"]["baseline"] = self.merge
            report["reviewed_range"]["from_head"] = self.merge
            Path(pointer["source_file"]).write_text(json.dumps(report), encoding="utf-8")
            pointer["sha256"] = evidence.sha256_file(Path(pointer["source_file"]))
        self.activation_serial += 1
        folder = self.root / ("activation-" + str(self.activation_serial)); folder.mkdir()
        coverage, short = bind_short(folder, {BASE: policy.ACTIVATION_BASELINE, HEAD: self.feature,
                                             MERGE: self.merge, TREE: self.tree, SYNTHETIC: self.tested})
        # The activation branch is a source qualification, not an author label.
        for path in folder.rglob("*.json"):
            raw = path.read_bytes().replace(b"codex/fixture", policy.ACTIVATION_BRANCH.encode())
            path.write_bytes(raw)
        # Branch changes affect the original environment/event/coverage and ZIP.
        current = evidence.load_json(coverage)
        for item in current["artifacts"].values():
            item["sha256"] = evidence.sha256_file(coverage.parent / item["path"])
        write(coverage, current)
        with zipfile.ZipFile(folder / "pr-artifact.zip", "w") as archive:
            for path in sorted(coverage.parent.rglob("*")):
                if path.is_file(): archive.write(path, path.relative_to(coverage.parent).as_posix())
        artifact = evidence.load_json(folder / "artifact.json")
        artifact["digest"] = "sha256:" + evidence.sha256_file(folder / "pr-artifact.zip")
        write(folder / "artifact.json", artifact)
        result = evidence.load_json(short)
        result["coverage_sha256"] = evidence.sha256_file(coverage)
        for item in result["source"].values():
            item["sha256"] = evidence.sha256_file(folder / item["path"])
        write(short, result)
        activation = {"schema": "pr-flow-activation/1", "policy": policy.POLICY_ID,
                      "task_id": policy.ACTIVATION_TASK, "baseline": policy.ACTIVATION_BASELINE,
                      "reviewed_head": self.feature, "actual_merge": self.merge,
                      "completed_at": "2026-11-14T00:00:00+00:00", "source_ref": "fixture://activation-platform-source",
                      "authorization": self.source(b"Synthetic explicit activation instruction, not real user approval", "fixture://activation-platform-source"),
                      "coverage": str(coverage), "short_validation": str(short)}
        activation_pointer = self.source(activation, "activation-receipt")
        task = {"schema": "pr-flow-task-adoption/1", "policy": policy.POLICY_ID,
                "task_id": state["candidate"]["task_id"], "baseline": self.merge,
                "created_at": "2026-11-14T00:00:01+00:00", "adopted_at": "2026-11-14T00:00:02+00:00",
                "activation_ref": activation_pointer["report_ref"], "source_ref": "fixture://task-creation-and-adoption-source",
                "authorization": self.source(b"Synthetic new task explicit policy adoption, not real user approval", "fixture://task-creation-and-adoption-source")}
        state["adoption"] = {"activation": activation_pointer, "task": self.source(task, "task-adoption")}
        if not merged: self.fixture.premerge_recheck(state)
        return state

    def decision(self, state):
        with chdir(self.repo): return gate.next_action(state)

    def mutate_receipt(self, state, which, **changes):
        pointer = state["adoption"][which]
        value = json.loads(Path(pointer["source_file"]).read_text())
        value.update(changes)
        write(Path(pointer["source_file"]), value)
        pointer["sha256"] = evidence.sha256_file(Path(pointer["source_file"]))

    def cli(self, state, command="next-action"):
        file = self.root / "state.json"; write(file, state)
        argv = [sys.executable, "-X", "utf8", "-B", str(ROOT / "scripts/pr_review_gate.py"), command, str(file)]
        result = subprocess.run(argv, cwd=self.repo, capture_output=True, text=True)
        destination = os.environ.get("PR_FLOW_ACTIVATION_TEST_EVIDENCE")
        if destination:
            # Preserve original bytes before normal TemporaryDirectory cleanup.
            # A short task-local leaf avoids the platform's deep-path limit.
            # This is test evidence only, never a transport or gate input.
            snapshot_root = Path(destination).parent / "original-bytes"
            snapshot_root.mkdir(exist_ok=True)
            originals = []
            for origin in (self.root, self.repo):
                for item in sorted(origin.rglob("*")):
                    if not item.is_file(): continue
                    raw = item.read_bytes(); digest = hashlib.sha256(raw).hexdigest()
                    saved = snapshot_root / digest
                    if saved.exists():
                        if saved.read_bytes() != raw: raise AssertionError("snapshot digest collision")
                    else:
                        with saved.open("xb") as stream: stream.write(raw)
                    originals.append({"original_path":str(item),"saved_path":str(saved),"sha256":digest,"bytes":len(raw)})
            with (Path(destination) / "cli-records.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"kind":"synthetic Git/API/command fixtures, not hosted or approval",
                                         "test":self.id(),"command":argv,"cwd":str(self.repo),
                                         "state_sha256":hashlib.sha256(file.read_bytes()).hexdigest(),
                                         "state":state,"returncode":result.returncode,
                                         "stdout":result.stdout,"stderr":result.stderr,
                                         "retained_originals":originals}, ensure_ascii=False) + "\n")
        return result

    def test_public_new_task_tiers_use_one_one_two_and_real_full_evidence_consumer(self):
        for tier, count in (("docs", 1), ("local", 1), ("core", 2)):
            with self.subTest(tier=tier):
                state = self.activated(tier)
                self.assertEqual(len(state["review_originals"]), count)
                result = self.cli(state)
                value = json.loads(result.stdout)
                self.assertEqual((result.returncode, value["action"]), (0, "MERGE_PROPOSAL"), result.stdout + result.stderr)
                self.assertFalse(value["activation_pending"])
                self.assertTrue(value["merge_ready"])
                self.assertFalse(value["authenticity_verified"])
                Path(state["review_originals"][0]["source_file"]).unlink()
                for rejected in (self.decision(state), json.loads(self.cli(state).stdout)):
                    self.assertEqual(rejected["action"], "STOP")
                    self.assertEqual(rejected["stage"], "review")
                    self.assertFalse(rejected["activation_pending"] or rejected["merge_ready"])
        # RB-ACT-F1: qualifying adoption must not be mislabeled pending when
        # downstream canonical review bytes fail their independent checks.
        for case in ("altered", "invalid_json", "wrong_candidate"):
            with self.subTest(review_original=case):
                state = self.activated()
                pointer = state["review_originals"][0]
                file = Path(pointer["source_file"])
                if case == "altered": file.write_bytes(b"altered review bytes")
                elif case == "invalid_json":
                    file.write_bytes(b"{not-json}")
                    pointer["sha256"] = evidence.sha256_file(file)
                else:
                    report = json.loads(file.read_text())
                    report["candidate"]["baseline"] = BASE
                    write(file, report)
                    pointer["sha256"] = evidence.sha256_file(file)
                for rejected in (self.decision(state), json.loads(self.cli(state).stdout)):
                    self.assertEqual((rejected["action"], rejected["stage"]), ("STOP", "review"))
                    self.assertFalse(rejected["activation_pending"] or rejected["merge_ready"])

    def test_pending_v5_and_global_flag_never_migrate_automatically(self):
        state = self.fixture.lifecycle_state()
        self.fixture.premerge_recheck(state)
        self.assertEqual(self.decision(state)["action"], "STOP")
        state["schema"] = gate.ACTIVATED_TIERED_SCHEMA
        self.assertEqual(self.decision(state)["action"], "STOP")
        self.assertFalse(policy.ENABLED)
        state = self.activated()
        state.pop("lifecycle")
        with self.assertRaisesRegex(policy.PolicyError, "complete lifecycle"):
            gate.validate_tiered_evidence(state)
        self.assertEqual(self.decision(state)["action"], "STOP")

    def test_activation_task_original_source_and_own_acceptance_cannot_be_substituted(self):
        cases = ({"task_id": "pr-flow-decisions-20261112"}, {"baseline": BASE}, {"actual_merge": policy.ACTIVATION_BASELINE},
                 {"reviewed_head": HEAD}, {"policy": "unadopted/1"})
        for changes in cases:
            with self.subTest(changes=changes):
                state = self.activated()
                self.mutate_receipt(state, "activation", **changes)
                for rejected in (self.decision(state), json.loads(self.cli(state).stdout)):
                    self.assertEqual(rejected["action"], "STOP")
                    self.assertTrue(rejected["activation_pending"])
                    self.assertFalse(rejected["merge_ready"])
        for task in (policy.ACTIVATION_TASK, "pr-flow-decisions-20261112"):
            state = self.activated(); state["candidate"]["task_id"] = state["task_policy"]["task_id"] = state["authorization"]["task_id"] = task
            self.assertEqual(self.decision(state)["action"], "STOP")

    def test_missing_altered_or_wrong_task_adoption_originals_stop(self):
        for case in ("missing", "hash", "source", "task", "baseline", "reference", "policy_source", "original_source"):
            with self.subTest(case=case):
                state = self.activated()
                if case == "missing": Path(state["adoption"]["activation"]["source_file"]).unlink()
                elif case == "hash": state["adoption"]["task"]["sha256"] = "f" * 64
                elif case == "source":
                    record = json.loads(Path(state["adoption"]["task"]["source_file"]).read_text())
                    Path(record["authorization"]["source_file"]).write_bytes(b"changed source")
                elif case == "policy_source": state["task_policy"]["source_ref"] = "fixture://unrelated-task-source"
                elif case == "original_source": self.mutate_receipt(state, "task", source_ref="fixture://unrelated-task-source")
                else: self.mutate_receipt(state, "task", **{{"task":"task_id","baseline":"baseline","reference":"activation_ref"}[case]: BASE if case == "baseline" else "wrong"})
                for rejected in (self.decision(state), json.loads(self.cli(state).stdout)):
                    self.assertEqual(rejected["action"], "STOP")
                    self.assertTrue(rejected["activation_pending"])
                    self.assertFalse(rejected["merge_ready"])

    def test_activation_must_finish_before_real_new_task_creation_and_explicit_adoption(self):
        cases = ({"created_at":"2026-11-13T23:59:59+00:00"}, {"adopted_at":"2026-11-14T00:00:00+00:00"},
                 {"created_at":"2026-11-14T00:00:01"})
        for changes in cases:
            state = self.activated(); self.mutate_receipt(state, "task", **changes)
            self.assertEqual(self.decision(state)["action"], "STOP")
        state = self.activated(); self.mutate_receipt(state, "activation", completed_at="2026-09-01T00:00:00+00:00")
        self.assertEqual(self.decision(state)["action"], "STOP")

    def test_native_git_ancestry_and_consumer_source_are_required(self):
        state = self.activated()
        state["candidate"]["baseline"] = state["task_policy"]["baseline"] = policy.ACTIVATION_BASELINE
        self.mutate_receipt(state, "task", baseline=policy.ACTIVATION_BASELINE)
        self.assertEqual(self.decision(state)["action"], "STOP")
        state = self.activated()
        with chdir(ROOT): self.assertEqual(gate.next_action(state)["action"], "STOP")  # synthetic activation object is isolated
        # A real native merge and valid full/short fixture for the activation
        # branch still cannot activate a different implementation's bytes.
        self.tree = self.git("rev-parse", policy.ACTIVATION_BASELINE + "^{tree}").decode().strip()
        self.feature = self.git("commit-tree", self.tree, "-p", policy.ACTIVATION_BASELINE,
                                payload=b"synthetic wrong consumer feature\n").decode().strip()
        self.merge = self.git("commit-tree", self.tree, "-p", policy.ACTIVATION_BASELINE, "-p", self.feature,
                              payload=b"synthetic wrong consumer merge\n").decode().strip()
        self.tested = self.git("commit-tree", self.tree, "-p", policy.ACTIVATION_BASELINE, "-p", self.feature,
                               payload=b"synthetic wrong consumer integration\n").decode().strip()
        state = self.activated()
        result = self.decision(state)
        self.assertEqual(result["action"], "STOP")
        self.assertIn("consumer source differs", result["reason"])

    def test_public_lifecycle_keeps_authorization_full_ci_and_premerge_guards(self):
        for case, action in (("development","WAIT_VALIDATION_AUTHORIZATION"), ("pending","WAIT_PR_CI"),
                             ("failed","INVESTIGATE_CI"), ("coverage","REFRESH_EVIDENCE"),
                             ("auth","STOP"), ("merge_auth","STOP"), ("premerge","REFRESH_EVIDENCE")):
            with self.subTest(case=case):
                state = self.activated()
                if case == "development": state["execution"] = {"mode":"development","validation_authorization_ref":None}
                elif case == "pending": state["pr_ci"] = None
                elif case == "failed": state["pr_ci"]["conclusion"] = "failure"
                elif case == "coverage": state["coverage"] = None
                elif case == "auth": state["authorization"]["active"] = False
                elif case == "merge_auth": state["authorization"]["operations"].remove("merge")
                else: state["lifecycle"]["premerge_recheck"] = None
                result = self.cli(state); value = json.loads(result.stdout)
                self.assertEqual(value["action"], action, result.stdout + result.stderr)
                self.assertFalse(value["merge_ready"])
        state = self.activated()
        result = self.cli(state, "diagnose-lifecycle"); value = json.loads(result.stdout)
        self.assertEqual((result.returncode, value["action"]), (2, "STOP"))
        self.assertFalse(value["merge_ready"] or value["activation_pending"])
        self.assertEqual(value["evaluation"]["action"], "MERGE_PROPOSAL")
        # A partial assessment must not call a contradictory open/merged state
        # ready when the same complete public lifecycle rejects it.
        state["lifecycle"]["merge"] = self.fixture.full_merged_lifecycle()["lifecycle"]["merge"]
        with chdir(self.repo):
            assessment = gate.validate_tiered_evidence(state)
        self.assertFalse(assessment["merge_ready"] or assessment["requirements_satisfied"])
        self.assertEqual(self.decision(state)["action"], "STOP")

    def test_public_actual_merge_and_own_short_are_required_for_completion(self):
        state = self.activated(merged=True)
        result = self.cli(state); value = json.loads(result.stdout)
        self.assertEqual((result.returncode, value["action"]), (0, "COMPLETE"), result.stdout + result.stderr)
        self.assertFalse(value["merge_ready"] or value["activation_pending"])
        for case, action in (("parents","STOP"),("tree","STOP"),("push","WAIT_PUSH_CI"),("short","REFRESH_EVIDENCE")):
            altered = deepcopy(state)
            if case == "parents": altered["lifecycle"]["merge"]["parents"].reverse()
            elif case == "tree": altered["lifecycle"]["merge"]["tree"] = BASE
            elif case == "push": altered["lifecycle"]["push_ci"] = None
            else: altered["lifecycle"]["short_validation"] = None
            result = self.cli(altered); value = json.loads(result.stdout)
            self.assertEqual(value["action"], action, result.stdout + result.stderr)

    def test_existing_v3_public_behavior_retains_original_meaning(self):
        result = self.cli(evidence_state())
        self.assertEqual(json.loads(result.stdout)["action"], "MERGE_PROPOSAL")


if __name__ == "__main__":
    unittest.main()
