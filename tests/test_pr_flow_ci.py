"""Synthetic local evidence only; no product suite, GUI, remote or installs."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import pr_flow_ci as ci
from scripts import pr_flow_policy as policy


def documentation_fixture(folder, text="# Guide\n\n[Target](target.md#details)\n"):
    """Create an isolated synthetic Git object graph, never modify task Git."""
    root = Path(folder)
    def git(*args, input=None):
        result = subprocess.run(["git", *args], cwd=root, input=input, capture_output=True, check=True)
        return result.stdout.decode().strip()
    git("init", "-b", "synthetic")
    git("config", "user.name", "Synthetic fixture")
    git("config", "user.email", "synthetic@example.invalid")
    (root / "guide.md").write_text("# Guide\n", encoding="utf-8")
    (root / "target.md").write_text("# Target\n\n## Details\n", encoding="utf-8")
    git("add", "guide.md", "target.md"); git("commit", "-m", "synthetic baseline")
    baseline = git("rev-parse", "HEAD")
    (root / "guide.md").write_text(text, encoding="utf-8")
    git("add", "guide.md"); git("commit", "-m", "synthetic documentation candidate")
    head, tree = git("rev-parse", "HEAD"), git("rev-parse", "HEAD^{tree}")
    merge = git("commit-tree", tree, "-p", baseline, "-p", head, input=b"synthetic integration\n")
    git("update-ref", "refs/heads/synthetic", merge)
    candidate = dict(task_id="synthetic-ci", repository="discoveryray/zhuyin-proofreader", baseline=baseline,
                     base=baseline, merge_base=baseline, head=head, tree=tree, scope="synthetic complete task diff",
                     changed_files=["guide.md"], risks=[])
    event = {"pull_request": {"number": 123, "base": {"sha": baseline, "ref": "develop", "repo": {"full_name": candidate["repository"]}},
                              "head": {"sha": head, "ref": "synthetic/docs", "repo": {"full_name": candidate["repository"]}}}}
    environment = dict(GITHUB_EVENT_NAME="pull_request", GITHUB_REF="refs/pull/123/merge", GITHUB_SHA=merge,
                       GITHUB_REPOSITORY=candidate["repository"], GITHUB_RUN_ID="1234", GITHUB_RUN_ATTEMPT="1", GITHUB_JOB="test")
    return candidate, event, environment


def review_capture(root, candidate, tier="docs", count=1):
    pointers = []
    for number in range(count):
        ref = "synthetic://review/" + str(number)
        report = dict(schema="pr-flow-code-review/1", policy=policy.POLICY_ID, report_ref=ref,
                      candidate=deepcopy(candidate), reviewer="independent-reviewer-" + str(number), session="independent-session-" + str(number),
                      independent=True, confirmed_tier=tier, mode="full",
                      reviewed_range={"from_head": candidate["baseline"], "to_head": candidate["head"]},
                      previous_report_ref=None, full_review_ref=ref, delta_from=None,
                      impact_known=True, architecture_changed=False, integration_reviewed=True,
                      impact_summary="Synthetic complete diff and Markdown consumer review.", review_checks=[],
                      verdict="PASS", findings=[], resolutions=[])
        path = root / ("review-" + str(number) + ".json")
        path.write_text(json.dumps(report, sort_keys=True), encoding="utf-8")
        pointers.append(dict(report_ref=ref, source_file=str(path.resolve()), sha256=ci.digest(path.read_bytes()), backups=[]))
    return dict(schema="pr-flow-ci-capture/1", policy=policy.POLICY_ID,
                coordinator=dict(actor="synthetic-coordinator", source_refs=["synthetic://independently-captured-originals"],
                                 authorization_ref="synthetic://authorized-task", independent_sources_checked=True),
                candidate=deepcopy(candidate), classification=dict(tier=tier, author="implementer", rationale="Synthetic bounded known consumers; review checks are represented by this local fixture.",
                                                                  known_consumers=["Markdown renderer"], impact_known=True),
                implementers=["implementer"], review_originals=pointers)


def synthetic_ci(candidate, binding):
    steps = {name: "skipped" for name in ci.FULL_STEPS}
    steps.update({name: "success" for name in ("Check out repository", "Set up Python", "Show Python version", "Resolve current validation route", *ci.DOC_STEPS, "Verify required validation results")})
    job = dict(name="Python 3.13", conclusion="success", runner="windows-latest", python_version="3.13.0",
               run_id=1234, attempt=1, tested_sha=binding["checkout"]["checkout_sha"], steps=steps)
    value = dict(workflow=".github/workflows/ci.yml", event="pull_request", branch="synthetic/docs", head_sha=candidate["head"],
                 parents=[candidate["base"], candidate["head"]], tested_sha=job["tested_sha"], tree=binding["checkout"]["checkout_tree"],
                 run_id=1234, attempt=1, latest_run_id=1234, latest_attempt=1, status="completed", conclusion="success", jobs=[job])
    pr = dict(number=123, head_branch="synthetic/docs", base_branch="develop")
    return value, pr


def diverged_documentation_fixture(folder, change):
    """Keep feature scope fixed while testing a distinct current base/merge."""
    candidate, event, environment = documentation_fixture(folder)
    root = Path(folder)
    def git(*args, input=None):
        return subprocess.run(["git", *args], cwd=root, input=input, capture_output=True, check=True).stdout.decode().strip()
    def tree(files):
        entries = []
        for name, raw in sorted(files.items()):
            blob = git("hash-object", "-w", "--stdin", input=raw)
            entries.append("100644 blob " + blob + "\t" + name + "\n")
        return git("mktree", input="".join(entries).encode())
    base_files = {"guide.md": b"# Guide\n", "target.md": b"# Target\n\n## Details\n"}
    merge_files = {**base_files, "guide.md": b"# Guide\n\n[Target](target.md#details)\n"}
    if change == "missing_target":
        del base_files["target.md"]; del merge_files["target.md"]
    elif change == "missing_anchor":
        base_files["target.md"] = merge_files["target.md"] = b"# Target\n\n## Renamed\n"
    elif change in {"guide_bytes", "whitespace"}:
        prefix = b"Base note  \n\n" if change == "whitespace" else b"Base note\n\n"
        base_files["guide.md"] = prefix + base_files["guide.md"]
        merge_files["guide.md"] = prefix + merge_files["guide.md"]
    else:
        raise AssertionError(change)
    base = git("commit-tree", tree(base_files), "-p", candidate["baseline"], input=b"synthetic divergent base\n")
    merged = git("commit-tree", tree(merge_files), "-p", base, "-p", candidate["head"], input=b"synthetic tested integration\n")
    git("update-ref", "refs/heads/synthetic", merged)
    candidate["base"] = base
    event["pull_request"]["base"]["sha"] = base
    environment["GITHUB_SHA"] = merged
    return candidate, event, environment


class PrFlowCiTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.candidate, self.event, self.environment = documentation_fixture(self.root)
        self.binding = ci.bind_event(self.candidate, self.event, self.environment, self.root)

    def test_inactive_public_dispatch_ignores_author_capture_and_keeps_full_and_develop_short(self):
        capture = self.root / ci.CAPTURE
        capture.parent.mkdir(parents=True); capture.write_text('{"route":"docs"}', encoding="utf-8")
        with patch.object(ci, "load", side_effect=AssertionError("inactive must not read capture")):
            self.assertEqual(ci.dispatch(self.event, self.environment, self.root)["route"], "full")
        self.assertFalse(policy.ENABLED)
        for ref, expected in (("refs/heads/develop", "short"), ("refs/heads/main", "full")):
            self.assertEqual(ci.dispatch({}, {"GITHUB_EVENT_NAME": "push", "GITHUB_REF": ref}, self.root)["route"], expected)
        with self.assertRaises(ValueError): ci.dispatch({}, {"GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": "refs/heads/main"}, self.root)

    def test_pure_plan_reads_canonical_independent_review_and_binds_exact_git_event(self):
        capture = review_capture(self.root, self.candidate)
        plan = ci.validated_documentation_plan(capture, self.event, self.environment, self.root)
        self.assertEqual(plan["route"], "docs")
        self.assertEqual(plan["checkout"]["head_sha"], self.candidate["head"])
        for field in ("base", "head"):
            changed = deepcopy(self.event); changed["pull_request"][field]["sha"] = "f" * 40
            with self.subTest(field=field), self.assertRaises(ValueError): ci.validated_documentation_plan(capture, changed, self.environment, self.root)
        for field in ("tree", "merge_base", "head"):
            changed = deepcopy(capture); changed["candidate"][field] = "f" * 40
            with self.subTest(field=field), self.assertRaises((ValueError, subprocess.SubprocessError)):
                ci.validated_documentation_plan(changed, self.event, self.environment, self.root)
        changed = deepcopy(capture); changed["candidate"]["changed_files"] += ["unlisted.md"]
        with self.assertRaises(ValueError): ci.validated_documentation_plan(changed, self.event, self.environment, self.root)
        changed = {**self.environment, "GITHUB_SHA": self.candidate["head"]}
        with self.assertRaises(ValueError): ci.validated_documentation_plan(capture, self.event, changed, self.root)

    def test_unconfirmed_stale_or_forged_original_cannot_exempt_full(self):
        capture = review_capture(self.root, self.candidate)
        no_review = deepcopy(capture); no_review["review_originals"] = []
        self.assertEqual(ci.validated_documentation_plan(no_review, self.event, self.environment, self.root)["route"], "full")
        no_provenance = deepcopy(capture); no_provenance["coordinator"]["independent_sources_checked"] = False
        with self.assertRaises(ValueError): ci.validated_documentation_plan(no_provenance, self.event, self.environment, self.root)
        original = Path(capture["review_originals"][0]["source_file"])
        raw = json.loads(original.read_text())
        for mutate in (lambda r: r.update(reviewer="implementer"), lambda r: r["candidate"].update(head="f" * 40), lambda r: r.update(independent=False)):
            changed = deepcopy(raw); mutate(changed)
            original.write_text(json.dumps(changed), encoding="utf-8")
            bad = deepcopy(capture); bad["review_originals"][0]["sha256"] = ci.digest(original.read_bytes())
            try:
                self.assertNotEqual(ci.validated_documentation_plan(bad, self.event, self.environment, self.root)["route"], "docs")
            except ValueError: pass
        # Same parsed report with different original bytes must still fail.
        original.write_text(json.dumps(raw) + "\n", encoding="utf-8")
        with self.assertRaises(ValueError): ci.validated_documentation_plan(capture, self.event, self.environment, self.root)

    def test_shared_docs_local_core_counts_and_semantic_upgrade_controls(self):
        for tier, paths, risks, count in (("docs", ["guide.md"], [], 1), ("local", ["display.py"], [], 1), ("core", ["docs/VALIDATION_POLICY.md"], ["gate_ci"], 2)):
            candidate = {**self.candidate, "changed_files": paths, "risks": risks}
            classification = dict(tier=tier, author="implementer", rationale="Synthetic known scope", known_consumers=["known consumer"], impact_known=True)
            reports = [dict(candidate=candidate, reviewer="reviewer-"+str(n), session="session-"+str(n), independent=True, confirmed_tier=tier) for n in range(count)]
            self.assertFalse(policy.validate_classification(candidate, classification, ["implementer"], reports[:-1])["confirmed"])
            result = policy.validate_classification(candidate, classification, ["implementer"], reports)
            self.assertEqual((result["confirmed"], result["reviewers_required"]), (True, count))
        for paths, risks in ((["docs/VALIDATION_POLICY.md"], []), (["guide.md", "display.py"], []), (["guide.md"], ["unknown"])):
            candidate = {**self.candidate, "changed_files": paths, "risks": risks}
            classification = dict(tier="docs", author="implementer", rationale="wrong downgrade", known_consumers=["renderer"], impact_known=True)
            with self.assertRaises(ValueError): policy.validate_classification(candidate, classification, ["implementer"], [])

    def test_real_nonempty_documentation_children_and_artifact_verifier(self):
        output = self.root / "evidence/checks.json"
        result = ci.run_documentation_checks(self.candidate, self.binding, output, self.root)
        self.assertEqual(result["status"], "success")
        self.assertEqual([item["name"] for item in result["checks"]], list(ci.CHECKS))
        verified = ci.verify_documentation_checks(output, self.candidate, _root=self.root)
        links = ci.load(output.parent / "local_links.stdout.json")
        self.assertEqual((links["inspected_files"], len(links["local_links"])), (1, 1))
        current, pr = synthetic_ci(self.candidate, self.binding)
        ci.verify_documentation_ci(current, pr, self.candidate, verified)
        for mutate in (lambda r: r.update(checks=[]), lambda r: r.update(status="failed"), lambda r: r["candidate"].update(head="f"*40), lambda r: r["checks"][0].update(exit_code=1)):
            bad = deepcopy(result); mutate(bad); output.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaises(ValueError): ci.verify_documentation_checks(output, self.candidate, _root=self.root)
        output.write_text(json.dumps(result), encoding="utf-8")
        stdout = output.parent / "bytes.stdout.json"
        stdout.write_text('{"inspected_files":1,"files":[]}', encoding="utf-8")
        forged = deepcopy(result); forged["checks"][0]["stdout"]["sha256"] = ci.digest(stdout.read_bytes())
        output.write_text(json.dumps(forged), encoding="utf-8")
        with self.assertRaises(ValueError): ci.verify_documentation_checks(output, self.candidate, _root=self.root)

    def test_broken_anchor_and_whitespace_checks_retain_actual_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            candidate, event, environment = documentation_fixture(folder, "# Guide  \n\n[Missing](target.md#absent)\n")
            binding = ci.bind_event(candidate, event, environment, folder)
            output = Path(folder) / "evidence/checks.json"
            result = ci.run_documentation_checks(candidate, binding, output, folder)
            self.assertEqual(result["status"], "failed")
            self.assertEqual([item["exit_code"] == 0 for item in result["checks"]], [True, False, False])
            self.assertTrue((output.parent / "local_links.stderr.log").read_bytes())
            with self.assertRaises(ValueError): ci.verify_documentation_checks(output, candidate, _root=folder)

    def test_documentation_ci_rejects_failure_missing_mixed_stale_pr_and_attempt(self):
        current, pr = synthetic_ci(self.candidate, self.binding)
        record = {**self.binding}
        for mutate in (lambda v: v.update(conclusion="failure"), lambda v: v.update(latest_attempt=2), lambda v: v.update(tested_sha="f"*40),
                       lambda v: v["jobs"][0]["steps"].pop("Run required documentation checks"),
                       lambda v: v["jobs"][0]["steps"].update({"Run full pytest suite":"success"})):
            bad = deepcopy(current); mutate(bad)
            with self.assertRaises(ValueError): ci.verify_documentation_ci(bad, pr, self.candidate, record)
        with self.assertRaises(ValueError): ci.verify_documentation_ci(current, {**pr,"number":124}, self.candidate, record)
        with self.assertRaises(ValueError): ci.verify_documentation_ci(current, {**pr,"head_branch":"other"}, self.candidate, record)
        bad = deepcopy(record); bad["ci"]["attempt"] = 2
        with self.assertRaises(ValueError): ci.verify_documentation_ci(current, pr, self.candidate, bad)

    def test_public_cli_has_no_activation_bypass_and_refuses_documentation_while_inactive(self):
        for arguments in (("dispatch","--ignore-inactive"), ("documentation","--plan","missing","--output","missing")):
            result = subprocess.run([sys.executable, str(Path(ci.__file__)), *arguments], cwd=self.root, capture_output=True,
                                    env={**os.environ,"GITHUB_EVENT_PATH":str(self.root/'missing-event.json')})
            self.assertNotEqual(result.returncode, 0)

    def test_documentation_checks_use_actual_integration_bytes_links_and_diff(self):
        for change in ("missing_target", "missing_anchor", "guide_bytes", "whitespace"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as folder:
                candidate, event, environment = diverged_documentation_fixture(folder, change)
                binding = ci.bind_event(candidate, event, environment, folder)
                output = Path(folder) / "evidence/checks.json"
                result = ci.run_documentation_checks(candidate, binding, output, folder)
                self.assertNotEqual(candidate["tree"], binding["checkout"]["checkout_tree"])
                actual = ci.load(output.parent / "bytes.stdout.json")
                tested = actual["tested_checkout"]
                self.assertEqual(tested["head"], binding["checkout"]["checkout_sha"])
                self.assertEqual(tested["tree"], binding["checkout"]["checkout_tree"])
                raw = ci.git(folder, "cat-file", "blob", tested["head"] + ":guide.md")
                self.assertEqual(actual["files"][0]["sha256"], ci.digest(raw))
                if change == "guide_bytes":
                    self.assertEqual(result["status"], "success")
                    self.assertNotEqual(raw, ci.git(folder, "cat-file", "blob", candidate["head"] + ":guide.md"))
                    ci.verify_documentation_checks(output, candidate, _root=folder)
                    diff = ci.load(output.parent / "diff.stdout.json")
                    self.assertEqual([entry["command"][4] for entry in diff["results"]], [candidate["head"], tested["head"]])
                else:
                    self.assertEqual(result["status"], "failed")
                    failed = "diff" if change == "whitespace" else "local_links"
                    self.assertNotEqual(next(item["exit_code"] for item in result["checks"] if item["name"] == failed), 0)
                    with self.assertRaises(ValueError): ci.verify_documentation_checks(output, candidate, _root=folder)

    def test_documentation_binding_rejects_forged_git_identity_and_producer_drift(self):
        output = self.root / "evidence/checks.json"
        record = ci.run_documentation_checks(self.candidate, self.binding, output, self.root)
        for field, value in (("checkout_sha", self.candidate["head"]), ("checkout_tree", "f"*40),
                             ("checkout_parents", [self.candidate["head"], self.candidate["base"]])):
            bad = deepcopy(record); bad["checkout"][field] = value
            output.write_text(json.dumps(bad), encoding="utf-8")
            with self.subTest(field=field), self.assertRaises(ValueError):
                ci.verify_documentation_checks(output, self.candidate, _root=self.root)
        output.write_text(json.dumps(record), encoding="utf-8")
        # Verification reads the bound immutable merge object even when the
        # coordinator's current checkout is the feature candidate instead.
        subprocess.run(["git", "update-ref", "refs/heads/synthetic", self.candidate["head"]], cwd=self.root, check=True, capture_output=True)
        ci.verify_documentation_checks(output, self.candidate, _root=self.root)
        with self.assertRaises(ValueError):
            ci.run_documentation_checks(self.candidate, self.binding, self.root / "wrong-start/checks.json", self.root)
        subprocess.run(["git", "update-ref", "refs/heads/synthetic", self.binding["checkout"]["checkout_sha"]], cwd=self.root, check=True, capture_output=True)
        original_run = ci.subprocess.run
        def drift(command, *args, **kwargs):
            result = original_run(command, *args, **kwargs)
            if "inspect" in command:
                original_run(["git", "update-ref", "refs/heads/synthetic", self.candidate["head"]], cwd=self.root, check=True, capture_output=True)
            return result
        drift_output = self.root / "drift/checks.json"
        with patch.object(ci.subprocess, "run", side_effect=drift), self.assertRaises(ValueError):
            ci.run_documentation_checks(self.candidate, self.binding, drift_output, self.root)
        self.assertTrue(drift_output.is_file(), "raw child evidence must survive producer rejection")
        self.assertEqual(ci.load(drift_output)["status"], "failed")


if __name__ == "__main__":
    unittest.main()
