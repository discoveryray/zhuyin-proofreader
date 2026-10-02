"""Isolated raw-evidence fixtures; none are real CI, GUI or review attestations."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import io

from scripts import validation_evidence as evidence
from scripts import validation_runner as runner
from scripts import pr_review_gate as gate
from test_pr_review_gate import BASE, HEAD, SYNTHETIC, TREE, MERGE, evidence_state, merged_state

ROOT = Path(__file__).resolve().parents[1]


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def ref(path):
    return {"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def raw_bundle(root):
    """An independent two-identity specification with explicit raw records."""
    inventory = {"schema": "zhuyin-test-groups/1", "pytest_ids": ["test_small.test_core", "test_small.test_gui"],
                 "core_ids": ["test_small.test_core"], "gui_ids": ["test_small.test_gui"],
                 "records": [{"identity": "test_small.test_" + group,
                              "nodeid": "tests/test_small.py::test_" + group, "group": group}
                             for group in ("core", "gui")]}
    environment = runner.environment()
    candidate = {"head": SYNTHETIC, "tree": TREE, "parents": [BASE, HEAD]}
    manifests = []
    for group in ("core", "gui"):
        folder = root / group
        folder.mkdir()
        write(folder / "inventory.json", inventory)
        inventory_hash = evidence.sha256_file(folder / "inventory.json")
        key = runner.canonical_digest({"task": "fixture", "tree": TREE})
        data = {"schema": "zhuyin-validation-run/1", "execution_id": group, "group": group,
                "candidate": candidate, "command": [sys.executable, str(ROOT / "scripts/validation_runner.py"), "_pytest", str(folder), group],
                "environment": environment, "inventory_sha256": inventory_hash,
                "started_at": "2026-10-01T00:00:00+00:00", "finished_at": "2026-10-01T00:00:01+00:00",
                "exit_code": 0, "outcome": "success", "verification": {"top_level": 1, "subtests": 0, "error": None},
                "ci": {"run_id": 100, "attempt": 1, "job": "grouped", "event": "pull_request"},
                "retry_of": None, "retry_key": key, "retry_eligible": False, "artifacts": {},
                "preflight": None, "runner_start_error": None, "code_blockers": False}
        (folder / "raw.log").write_text("isolated fixture: one required top-level case passed\n", encoding="utf-8")
        (folder / "junit.xml").write_text(f'<testsuites><testsuite><properties><property name="validation_execution_id" value="{group}"/><property name="validation_inventory_sha256" value="{inventory_hash}"/></properties><testcase classname="tests.test_small" name="test_{group}" /></testsuite></testsuites>', encoding="utf-8")
        (folder / "events.jsonl").write_text("\n".join(json.dumps({"nodeid": "tests/test_small.py::test_" + group, "when": when, "outcome": "passed", "subtest": False, "traceback": None}) for when in ("setup", "call", "teardown")), encoding="utf-8")
        if group == "gui":
            data["preflight"] = {"outcome": "success", "exit_code": 0}
            write(folder / "preflight.json", {"stage": "complete"})
            (folder / "preflight.log").write_text("isolated preflight evidence fixture", encoding="utf-8")
            (folder / "preflight.xml").write_text('<testsuites><testsuite><testcase classname="scripts.gui_preflight" name="test_gui_preflight" /></testsuite></testsuites>', encoding="utf-8")
        write(folder / "started.json", data)
        write(folder / "history-context.json", {"task_id": "fixture", "current_run": 100, "current_attempt": 1,
              "candidate": candidate, "head": HEAD, "runs": [], "executions": [], "raw_runs": [], "local_handoff_sha256": "1" * 64})
        paths = {"inventory": "inventory.json", "raw_log": "raw.log", "started": "started.json", "history_context": "history-context.json", "junit": "junit.xml", "events": "events.jsonl"}
        if group == "gui":
            paths["preflight_result"] = "preflight.json"
            paths["preflight_junit"] = "preflight.xml"
            paths["preflight_log"] = "preflight.log"
        data["artifacts"] = {key: ref(folder / name) for key, name in paths.items()}
        write(folder / "manifest.json", data)
        manifests.append(f"{group}/manifest.json")
    bundle = {"schema": "zhuyin-validation-coverage/1", "candidate": candidate, "environment": environment,
              "inventory_sha256": inventory_hash, "core_manifest": manifests[0], "gui_manifest": manifests[1],
              "history_manifests": manifests, "ci": data["ci"], "status": "success"}
    write(root / "coverage.json", bundle)
    return root / "coverage.json"


def staged_state(coverage=None):
    state = evidence_state()
    state.update(schema=gate.STAGED_SCHEMA, execution={"mode": "validation", "validation_authorization_ref": "fixture://explicit-start"},
                 coverage=str(coverage) if coverage else None, short_validation=None)
    for review in state["reviews"]:
        review.update(verdict="CODE_REVIEWED", ci=None, coverage_sha256=None, finalizes_report_ref=None)
    state["pr_ci"]["jobs"] = [dict(state["pr_ci"]["jobs"][0], steps={"Verify required validation results": "success"}),
                                dict(state["pr_ci"]["jobs"][0], name="Grouped validation", steps={"Aggregate functional coverage": "success"})]
    return state


def formalize(state, coverage):
    for old in state["reviews"][:2]:
        fresh = deepcopy(old)
        fresh.update(verdict="PASS", coverage_sha256=evidence.sha256_file(coverage),
                     finalizes_report_ref=old["report_ref"], report_ref=old["report_ref"] + "/formal",
                     resolution_evidence_ref=str(coverage), review_mode="evidence_gap", full_diff_reviewed=False)
        if fresh["round"] == 2:
            fresh["ci"] = {"run_id": 100, "attempt": 1, "tested_sha": SYNTHETIC}
        state["reviews"].append(fresh)


def short_fixture(root):
    pr_root = root / "pr-evidence"
    pr_root.mkdir()
    coverage_path = raw_bundle(pr_root)
    coverage = evidence.load_json(coverage_path)
    candidate = {"head": MERGE, "tree": TREE, "parents": [BASE, HEAD]}
    repository = {"full_name": "discoveryray/zhuyin-proofreader"}
    pr = {"number": 20, "merged": True, "merge_commit_sha": MERGE,
          "head": {"sha": HEAD, "repo": repository}, "base": {"ref": "develop", "repo": repository}}
    run = {"id": 100, "run_attempt": 1, "run_number": 50, "event": "pull_request", "head_sha": HEAD,
           "path": ".github/workflows/ci.yml", "status": "completed", "conclusion": "success"}
    commit = {"sha": MERGE, "parents": [{"sha": BASE}, {"sha": HEAD}], "commit": {"tree": {"sha": TREE}}}
    archive = root / "pr-artifact.zip"
    with zipfile.ZipFile(archive, "w") as output:
        for path in sorted(pr_root.rglob("*")):
            if path.is_file():
                output.write(path, path.relative_to(pr_root).as_posix())
    artifact = {"id": 7, "name": "validation-evidence-100-1", "expired": False,
                "digest": "sha256:" + evidence.sha256_file(archive)}
    source = {"archive": ref(archive)}
    for key, value in (("pr", pr), ("run", run), ("runs", [run]), ("commit", commit), ("artifact", artifact)):
        path = root / (key + ".json")
        write(path, value)
        source[key] = ref(path)
    for key in ("assets", "pr_assets"):
        write(root / (key + ".json"), {"fixture": "same checked runtime manifest"})
        source[key] = ref(root / (key + ".json"))
    assets_digest = evidence.sha256_file(root / "assets.json")
    short = {"schema": "zhuyin-merge-short/1", "candidate": candidate,
             "environment": coverage["environment"], "coverage_sha256": evidence.sha256_file(coverage_path),
             "coverage_path": "pr-evidence/coverage.json", "source": source,
             "started_at": "2026-10-01T01:00:00+00:00", "finished_at": "2026-10-01T01:00:01+00:00",
             "ci": {"event": "push", "run_id": 101, "attempt": 1, "job": "short"}, "checks": [],
             "assets_sha256": assets_digest, "pr_assets_sha256": assets_digest, "outcome": "success"}
    for name, command in evidence.short_commands(BASE, MERGE):
        log = root / (name + ".log")
        log.write_text("" if name == "clean" else "isolated successful command fixture", encoding="utf-8")
        short["checks"].append({"name": name, "command": command, "exit_code": 0, "log": ref(log)})
    write(root / "short.json", short)
    return coverage_path, root / "short.json"


class StagedGateTests(unittest.TestCase):
    def cli(self, command, path):
        return subprocess.run([sys.executable, str(ROOT / "scripts/pr_review_gate.py"), command, str(path)], capture_output=True, text=True)

    def test_development_and_draft_are_intermediate_not_formal_pass(self):
        state = staged_state()
        state.update(pr=None, pr_ci=None)
        state["reviews"] = state["reviews"][:1]
        state["execution"] = {"mode": "development", "validation_authorization_ref": None}
        self.assertEqual(gate.next_action(state)["action"], "WAIT_VALIDATION_AUTHORIZATION")
        state["execution"] = {"mode": "validation", "validation_authorization_ref": "fixture://start"}
        self.assertEqual(gate.next_action(state)["action"], "ENSURE_DRAFT_PR")
        state["execution"]["validation_authorization_ref"] = None
        self.assertEqual(gate.next_action(state)["action"], "STOP")

    def test_real_cli_development_correction_is_local_only_and_v3_stays_frozen(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "state.json"
            state = staged_state()
            state["execution"] = {"mode": "development", "validation_authorization_ref": None}
            state["authorization"]["operations"] = ["implement", "delegate", "test", "commit"]
            state["reviews"][0].update(verdict="BLOCKED", blocker_kind="code", findings=["confirmed fixture defect"])
            write(path, state)
            result = json.loads(self.cli("next-action", path).stdout)
            self.assertEqual(result["action"], "CORRECT_IMPLEMENTATION")
            self.assertEqual(result["delivery"], "local_commit_only")
            self.assertEqual(result["correction_round"], 1)
            for missing in ("implement", "delegate", "test", "commit"):
                changed = deepcopy(state)
                changed["authorization"]["operations"].remove(missing)
                write(path, changed)
                self.assertEqual(json.loads(self.cli("next-action", path).stdout)["action"], "STOP")
            state["execution"] = {"mode": "validation", "validation_authorization_ref": "fixture://start"}
            write(path, state)
            self.assertEqual(json.loads(self.cli("next-action", path).stdout)["action"], "STOP")
            state["authorization"]["operations"].append("push")
            write(path, state)
            self.assertEqual(json.loads(self.cli("next-action", path).stdout)["action"], "CORRECT_IMPLEMENTATION")
            legacy = evidence_state()
            legacy["authorization"]["operations"] = ["implement", "delegate", "test", "commit"]
            legacy["reviews"][0].update(verdict="BLOCKED", blocker_kind="code", findings=["confirmed fixture defect"])
            write(path, legacy)
            self.assertEqual(json.loads(self.cli("next-action", path).stdout)["action"], "STOP")

    def test_real_cli_requires_both_formal_reviews_bound_to_raw_coverage(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            coverage = raw_bundle(root)
            state = staged_state(coverage)
            path = root / "state.json"
            write(path, state)
            result = self.cli("next-action", path)
            self.assertEqual(json.loads(result.stdout)["action"], "REFRESH_EVIDENCE", result.stdout + result.stderr)
            formalize(state, coverage)
            write(path, state)
            result = self.cli("next-action", path)
            self.assertEqual(json.loads(result.stdout)["action"], "MERGE_PROPOSAL", result.stdout + result.stderr)
            state["reviews"][-1]["coverage_sha256"] = "f" * 64
            write(path, state)
            self.assertEqual(json.loads(self.cli("next-action", path).stdout)["action"], "REFRESH_EVIDENCE")

    def test_finalization_cannot_clear_code_or_change_scope_or_reviewer_for_gap(self):
        for mutation in ("code", "head", "reviewer", "supersession"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                coverage = raw_bundle(Path(temporary))
                state = staged_state(coverage)
                formalize(state, coverage)
                if mutation == "code":
                    state["reviews"][0].update(verdict="BLOCKED", blocker_kind="code", findings=["confirmed code issue"])
                elif mutation == "supersession":
                    state["reviews"][2]["supersedes_report_ref"] = state["reviews"][0]["report_ref"]
                else:
                    state["reviews"][2][mutation] = "f" * 40 if mutation == "head" else "replacement"
                self.assertEqual(gate.next_action(state)["action"], "STOP")

    def test_old_formal_result_cannot_select_or_clear_later_evidence_blocker(self):
        with tempfile.TemporaryDirectory() as temporary:
            coverage = raw_bundle(Path(temporary))
            state = staged_state(coverage)
            formalize(state, coverage)
            old_pass = deepcopy(state["reviews"][2])
            old_pass.update(report_ref="fixture://older-full-pass", review_mode="full_diff", full_diff_reviewed=True,
                            finalizes_report_ref=None, resolution_evidence_ref=None, coverage_sha256="f" * 64)
            state["reviews"].insert(0, old_pass)
            self.assertEqual(gate.next_action(state)["action"], "MERGE_PROPOSAL")
            blocker = deepcopy(state["reviews"][3])
            blocker.update(report_ref="fixture://later-blocked", verdict="BLOCKED", blocker_kind="evidence",
                           findings=["later applicable attempt failed; preserve evidence"], coverage_sha256=None,
                           finalizes_report_ref=None, resolution_evidence_ref=None,
                           review_mode="full_diff", full_diff_reviewed=True)
            state["reviews"].append(blocker)
            self.assertEqual(gate.next_action(state)["action"], "REFRESH_EVIDENCE")

    def test_formal_pass_never_creates_authorization_or_legacy_upgrade(self):
        state = staged_state()
        state["schema"] = gate.SCHEMA
        self.assertEqual(gate.next_action(state)["action"], "STOP")
        with tempfile.TemporaryDirectory() as temporary:
            coverage = raw_bundle(Path(temporary))
            state = staged_state(coverage)
            formalize(state, coverage)
            state["authorization"]["operations"].remove("merge")
            self.assertEqual(gate.next_action(state)["action"], "STOP")


class RawEvidenceTests(unittest.TestCase):
    def cli(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "scripts/validation_evidence.py"), *map(str, args)], capture_output=True, text=True)

    def test_real_cli_rejects_missing_corrupt_stale_and_failed_raw_evidence(self):
        for mutation in ("missing", "corrupt", "stale", "skip", "failure", "error", "unknown", "omitted_history"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                coverage = raw_bundle(root)
                self.assertEqual(self.cli("verify-coverage", coverage).returncode, 0)
                report = root / "gui/junit.xml"
                manifest = evidence.load_json(root / "gui/manifest.json")
                if mutation == "missing":
                    report.unlink()
                elif mutation == "omitted_history":
                    bundle = evidence.load_json(coverage)
                    bundle["history_manifests"].pop()
                    write(coverage, bundle)
                else:
                    text = report.read_text(encoding="utf-8")
                    if mutation == "corrupt":
                        text = text[:20]
                    elif mutation == "stale":
                        text = text.replace('value="gui"', 'value="another-execution"')
                    elif mutation == "unknown":
                        text = text.replace('name="test_gui"', 'name="test_other"')
                    else:
                        tag = "skipped" if mutation == "skip" else mutation
                        text = text.replace('name="test_gui" />', f'name="test_gui"><{tag} /></testcase>')
                    report.write_text(text, encoding="utf-8")
                    manifest["artifacts"]["junit"] = ref(report)
                    write(root / "gui/manifest.json", manifest)
                result = self.cli("verify-coverage", coverage)
                self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_coverage_keeps_original_pr_identity_under_push_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            coverage = raw_bundle(Path(temporary))
            with patch.dict(os.environ, {"GITHUB_RUN_ID": "101", "GITHUB_RUN_ATTEMPT": "2",
                                         "GITHUB_EVENT_NAME": "push", "GITHUB_JOB": "short"}):
                result = self.cli("verify-coverage", coverage)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(evidence.load_json(coverage)["ci"], {"run_id": 100, "attempt": 1, "job": "grouped", "event": "pull_request"})

    def test_local_success_is_not_formal_pr_coverage(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            coverage = raw_bundle(root)
            for group in ("core", "gui"):
                folder = root / group
                data = evidence.load_json(folder / "manifest.json")
                data["ci"] = {"run_id": None, "attempt": None, "job": None, "event": "local"}
                started = evidence.load_json(folder / "started.json")
                started["ci"] = data["ci"]
                write(folder / "started.json", started)
                write(folder / "history-context.json", {"schema": runner.LOCAL_SCHEMA, "task_id": "fixture",
                      "source_ref": "fixture://explicit-local-validation", "baseline": BASE,
                      "repository": "discoveryray/zhuyin-proofreader", "created_at": "2026-10-01T00:00:00+00:00"})
                data["artifacts"]["started"] = ref(folder / "started.json")
                data["artifacts"]["history_context"] = ref(folder / "history-context.json")
                write(folder / "manifest.json", data)
            result = self.cli("verify-coverage", coverage)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_summary_success_does_not_mask_failed_group_exit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            coverage = raw_bundle(root)
            manifest = evidence.load_json(root / "core/manifest.json")
            manifest["exit_code"] = 1
            write(root / "core/manifest.json", manifest)
            self.assertNotEqual(self.cli("verify-coverage", coverage).returncode, 0)

    def test_short_cli_without_push_context_retains_failure_and_unique_execution(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {"GITHUB_EVENT_NAME": "pull_request"}):
            for _ in range(2):
                result = self.cli("post-merge", "--evidence-root", temporary)
                self.assertEqual(result.returncode, 2)
            records = list(Path(temporary).glob("short-*/short.json"))
            self.assertEqual(len(records), 2)
            self.assertTrue(all(evidence.load_json(path)["outcome"] == "failed" for path in records))

    def test_real_cli_short_reuse_rejects_merge_dependencies_assets_and_latest_failure(self):
        for mutation in ("tree", "parents", "dependencies", "assets", "new_failure", "run_attempt", "archive", "failed_check", "dirty", "command", "missing"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                coverage, short_path = short_fixture(root)
                initial = self.cli("verify-reuse", coverage, short_path)
                self.assertEqual(initial.returncode, 0, initial.stdout + initial.stderr)
                short = evidence.load_json(short_path)
                if mutation == "tree":
                    short["candidate"]["tree"] = "f" * 40
                elif mutation == "parents":
                    short["candidate"]["parents"].reverse()
                elif mutation == "dependencies":
                    short["environment"]["dependencies"]["pytest"] = "other"
                elif mutation == "assets":
                    short["assets_sha256"] = "2" * 64
                elif mutation == "new_failure":
                    runs = evidence.load_json(root / "runs.json")
                    newer = dict(runs[0], id=102, run_number=51, conclusion="failure")
                    write(root / "runs.json", [*runs, newer])
                    short["source"]["runs"] = ref(root / "runs.json")
                elif mutation == "run_attempt":
                    run = evidence.load_json(root / "run.json")
                    run["run_attempt"] = 2
                    write(root / "run.json", run)
                    short["source"]["run"] = ref(root / "run.json")
                elif mutation == "archive":
                    (root / "pr-artifact.zip").write_bytes(b"corrupt")
                elif mutation == "failed_check":
                    short["checks"][0]["exit_code"] = 1
                elif mutation == "dirty":
                    (root / "clean.log").write_text(" M source.py", encoding="utf-8")
                    short["checks"][-1]["log"] = ref(root / "clean.log")
                elif mutation == "command":
                    short["checks"][0]["command"] = [sys.executable, "-c", "print('pass')"]
                else:
                    (root / "runtime.log").unlink()
                write(short_path, short)
                result = self.cli("verify-reuse", coverage, short_path)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_actual_merge_complete_requires_raw_short_result(self):
        with tempfile.TemporaryDirectory() as temporary:
            coverage, short = short_fixture(Path(temporary))
            state = staged_state(coverage)
            formalize(state, coverage)
            original = merged_state()
            state.update(merge=original["merge"], push_ci=original["push_ci"], short_validation=str(short))
            state["pr"]["state"] = "merged"
            state["current"]["base"] = MERGE
            state["push_ci"].update(run_id=101, latest_run_id=101)
            state["push_ci"]["jobs"] = [dict(state["push_ci"]["jobs"][0], run_id=101, steps={"Verify required validation results": "success"}),
                                          dict(state["push_ci"]["jobs"][0], run_id=101, name="Merge short validation")]
            self.assertEqual(gate.next_action(state)["action"], "COMPLETE", gate.next_action(state))
            data = evidence.load_json(short)
            data["outcome"] = "failed"
            write(short, data)
            self.assertEqual(gate.next_action(state)["action"], "STOP")
            self.assertEqual(state["merge"]["sha"], MERGE)

    def test_latest_pr_run_is_selected_and_archive_read_through_real_fetch_interface(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = root / "fixture"
            fixture.mkdir()
            coverage, short = short_fixture(fixture)
            candidate = evidence.load_json(short)["candidate"]
            archive = (fixture / "pr-artifact.zip").read_bytes()
            pr = evidence.load_json(fixture / "pr.json")
            run = evidence.load_json(fixture / "run.json")
            artifact = evidence.load_json(fixture / "artifact.json")
            commit = evidence.load_json(fixture / "commit.json")
            class Client:
                def request(self, endpoint, binary=False):
                    return {f"commits/{MERGE}": commit, "pulls/20": pr, "actions/runs/100": run,
                            "actions/artifacts/7/zip": archive}[endpoint]
                def pages(self, endpoint, key=None):
                    if endpoint.endswith("/pulls"):
                        return [dict(pr, state="closed")]
                    return [artifact] if endpoint.endswith("/artifacts") else [run]
            output = root / "download"
            output.mkdir()
            path, bundle, source = evidence.fetch_pr_evidence(Client(), candidate, output)
            self.assertEqual(bundle["candidate"], {"head": SYNTHETIC, "tree": TREE, "parents": [BASE, HEAD]})
            self.assertEqual(evidence.sha256_file(path), evidence.sha256_file(coverage))
            self.assertIn("runs", source)

    def test_short_finalization_failure_or_source_drift_never_persists_success(self):
        for problem in ("source_drift", "final_verifier"):
            with self.subTest(problem=problem), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                original = root / "original"
                original.mkdir()
                coverage_path = raw_bundle(original)
                bundle = evidence.load_json(coverage_path)
                candidate = {"head": MERGE, "tree": TREE, "parents": [BASE, HEAD]}
                after = dict(candidate, head="f" * 40) if problem == "source_drift" else candidate
                def isolated_command(command, **kwargs):
                    payload = (ROOT / "runtime_asset_manifest.json").read_bytes() if command[:3] == ["git", "cat-file", "--filters"] else b""
                    return subprocess.CompletedProcess(command, 0, stdout=payload)
                def isolated_fetch(client, supplied_candidate, output):
                    destination = output / "pr-evidence"
                    shutil.copytree(original, destination)
                    return destination / "coverage.json", bundle, {}
                with patch.dict(os.environ, {"GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/heads/develop",
                                             "GITHUB_SHA": MERGE, "GITHUB_RUN_ID": "101", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_JOB": "short", "GITHUB_TOKEN": "isolated-fixture"}), \
                     patch.object(runner, "snapshot", side_effect=[candidate, after]), \
                     patch.object(evidence, "fetch_pr_evidence", side_effect=isolated_fetch), \
                     patch.object(evidence.subprocess, "run", side_effect=isolated_command), \
                     patch.object(evidence, "verify_reuse", side_effect=ValueError("injected final raw verification failure")):
                    path, result = evidence.post_merge(root / "outputs")
                self.assertEqual(result["outcome"], "failed")
                self.assertEqual(evidence.load_json(path)["outcome"], "failed")
                self.assertIn("changed" if problem == "source_drift" else "injected", result["error"])

    def test_archive_path_escape_or_duplicate_cannot_overwrite_evidence(self):
        for name in ("../outside", "C:/outside", "same"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                data = io.BytesIO()
                with zipfile.ZipFile(data, "w") as archive:
                    archive.writestr(name, "one")
                    if name == "same":
                        archive.writestr(name, "two")
                with self.assertRaises(ValueError):
                    evidence.extract_evidence(data.getvalue(), Path(temporary) / "download")


if __name__ == "__main__":
    unittest.main()
