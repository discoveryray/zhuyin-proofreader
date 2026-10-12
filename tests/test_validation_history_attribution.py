"""Finite API-boundary fixtures; original ownership/closure readers execute.

No real CI or authorization is manufactured by these synthetic records.
"""
import base64
import copy
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts import validation_runner as runner
from scripts import validation_evidence as evidence
import test_validation_evidence as fixtures

ref, write = fixtures.ref, fixtures.write


ROOT = Path(__file__).resolve().parents[1]


def live_api_fixture(endpoint, event, candidate, runs, repository):
    """API replies needed by older fixtures' actual current-checkout proof."""
    pr = copy.deepcopy(event["pull_request"])
    for side in ("head", "base"):
        pr[side].setdefault("repo", {"full_name": repository})
    pr["base"].setdefault("ref", "develop")
    if endpoint == f"repos/{repository}/pulls/{pr['number']}":
        return pr
    if endpoint == f"repos/{repository}/git/commits/{candidate['head']}":
        return {"sha": candidate["head"], "tree": {"sha": candidate["tree"]},
                "parents": [{"sha": value} for value in candidate["parents"]]}
    match = re.fullmatch(r"repos/" + re.escape(repository) + r"/actions/runs/([0-9]+)/attempts/([0-9]+)(/jobs\?per_page=100&page=1)?", endpoint)
    if match:
        row = next((row for row in runs if row["id"] == int(match[1])), None)
        if row is None:
            return None  # Different historical API boundary owns this run.
        if match[3]:
            return {"total_count": 1, "jobs": [{"id": row["id"] + 100000, "run_id": row["id"],
                "run_attempt": int(match[2]), "head_sha": row["head_sha"], "head_branch": row["head_branch"],
                "name": "Grouped validation", "steps": [{"name": "Check out repository", "conclusion": "success"}]}]}
        return {**row, "run_attempt": int(match[2])}
    return None


class HistoryFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="history3-")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.repo = runner.REPOSITORY
        self.current_pr = 61
        self.branch = "fixture-history-branch"
        self.base, self.head, self.merge, self.tree = (letter * 40 for letter in "bacd")
        self.task = "fixture-history"
        self.handoff = self.make_handoff(self.task, self.base, "fixture original human scope")
        self.body = "<!-- validation-local-history\n" + json.dumps(self.handoff) + "\n-->"
        self.prs = {self.current_pr: self.pr(self.current_pr, self.head, self.base, self.branch, self.body)}
        self.event_data = {"pull_request": copy.deepcopy(self.prs[self.current_pr])}
        self.event = self.folder / "event.json"
        write(self.event, self.event_data)
        self.candidate = {"head": self.merge, "tree": self.tree, "parents": [self.base, self.head]}
        self.runs = [self.run_row(7000, self.head, 1, "in_progress", [])]
        self.current_run = 7000
        self.current_attempt = 1
        self.commits = {self.merge: self.commit(self.merge, self.candidate["parents"], self.tree)}
        self.origins = {}
        self.calls, self.downloads = [], []
        self.missing, self.expired, self.corrupt = set(), set(), set()
        self.api_mutation = None
        self.run_pages, self.job_pages, self.artifact_pages = None, None, None
        self.saved_bundle_paths = []

    def make_handoff(self, task, baseline, source):
        stream = io.BytesIO()
        ledger = {"schema": runner.LOCAL_SCHEMA, "task_id": task, "baseline": baseline,
                  "repository": self.repo, "source_ref": source,
                  "created_at": "2026-10-11T00:00:00+00:00", "store_id": "6" * 32}
        with zipfile.ZipFile(stream, "w") as zipped:
            zipped.writestr("ledger.json", json.dumps(ledger))
            for name in ("executions", "declarations"):
                zipped.writestr(name + "/store.json", json.dumps({"schema": "zhuyin-local-store/1", "store_id": ledger["store_id"]}))
        payload = stream.getvalue()
        return {"schema": runner.HANDOFF_SCHEMA, "task_id": task, "source_ref": source,
                "archive_sha256": hashlib.sha256(payload).hexdigest(), "archive_base64": base64.b64encode(payload).decode()}

    def pr(self, number, head, base, branch, body=""):
        return {"number": number, "state": "open", "merged": False, "body": body,
                "head": {"sha": head, "ref": branch, "repo": {"full_name": self.repo}},
                "base": {"sha": base, "ref": "develop", "repo": {"full_name": self.repo}}}

    def run_row(self, identity, head, attempts, status, links):
        return {"id": identity, "head_sha": head, "head_branch": self.branch,
                "run_attempt": attempts, "event": "pull_request", "status": status,
                "conclusion": "failure", "head_repository": {"full_name": self.repo}, "pull_requests": links}

    @staticmethod
    def commit(identity, parents, tree):
        return {"sha": identity, "parents": [{"sha": value} for value in parents], "tree": {"sha": tree}}

    def add_run(self, identity=7001, number=7, attempts=1, links=None):
        row = self.run_row(identity, "e" * 40, attempts, "completed", [] if links is None else links)
        self.runs.append(row)
        self.prs.setdefault(number, self.pr(number, row["head_sha"], "f" * 40, self.branch))
        for attempt in range(1, attempts + 1):
            sha = format(identity * 100 + attempt, "040x")
            self.commits[sha] = self.commit(sha, ["f" * 40, row["head_sha"]], "8" * 40)
            self.origins[identity, attempt] = number, sha
        return row

    def logs(self, identity, attempt):
        number, sha = self.origins[identity, attempt]
        parents = [item["sha"] for item in self.commits[sha]["parents"]]
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as zipped:
            for index in (0, 1):
                name = "Proof " + str(index)
                system = f"2026-10-11T00:00:00Z Job defined at: {self.repo}/.github/workflows/ci.yml@refs/pull/{number}/merge\n"
                lines = ["##[group]Run actions/checkout@v7", "with:", "  repository: " + self.repo,
                    f"[command]git.exe fetch origin +{sha}:refs/remotes/pull/{number}/merge",
                    f"[command]git.exe checkout --progress --force refs/remotes/pull/{number}/merge",
                    f"HEAD is now at {sha[:7]} Merge {parents[1]} into {parents[0]}",
                    "[command]git.exe log -1 --format=%H", sha, "##[group]Run actions/setup-python@v7"]
                log = "\n".join("2026-10-11T00:00:00Z " + line for line in lines) + "\n"
                zipped.writestr(str(index) + "_" + name + ".txt", log)
                zipped.writestr(name + "/system.txt", system)
        return stream.getvalue()

    def api(self, endpoint):
        self.calls.append(endpoint)
        suffix = endpoint.split(f"repos/{self.repo}/", 1)[1]
        if suffix.startswith("actions/workflows/"):
            page = int(endpoint.rsplit("page=", 1)[1])
            if self.run_pages is not None:
                return self.run_pages[page - 1]
            branch = __import__("urllib.parse", fromlist=["parse_qs"]).parse_qs(endpoint.split("?", 1)[1])["branch"][0]
            rows = [row for row in self.runs if row["head_branch"] == branch]
            return {"total_count": len(rows), "workflow_runs": rows}
        if suffix.startswith("pulls/"):
            return copy.deepcopy(self.prs[int(suffix.split("/")[1])])
        if suffix.startswith("git/commits/"):
            return copy.deepcopy(self.commits[suffix.rsplit("/", 1)[1]])
        match = re.fullmatch(r"actions/runs/([0-9]+)/attempts/([0-9]+)(.*)", suffix)
        if match:
            identity, attempt = int(match[1]), int(match[2])
            row = next(row for row in self.runs if row["id"] == identity)
            if match[3] == "":
                return {**row, "run_attempt": attempt,
                        "status": "completed" if attempt < row["run_attempt"] else row["status"]}
            if match[3].startswith("/jobs?"):
                if self.job_pages is not None and identity != self.current_run:
                    return self.job_pages[int(endpoint.rsplit("page=", 1)[1]) - 1]
                jobs = [{"id": identity * 100 + attempt * 10 + index, "run_id": identity,
                         "run_attempt": attempt, "head_sha": row["head_sha"], "head_branch": row["head_branch"],
                         "name": "Proof " + str(index), "steps": [{"name": "Check out repository", "conclusion": "success"}]}
                        for index in (0, 1)]
                return {"total_count": len(jobs), "jobs": jobs}
            if match[3] == "/logs":
                return self.logs(identity, attempt)
        match = re.fullmatch(r"actions/runs/([0-9]+)/artifacts\?per_page=100&page=([0-9]+)", suffix)
        if match:
            identity = int(match[1])
            if self.artifact_pages is not None:
                return self.artifact_pages[int(match[2]) - 1]
            row = next(row for row in self.runs if row["id"] == identity)
            items = [{"name": f"validation-evidence-{identity}-{attempt}", "expired": (identity, attempt) in self.expired}
                     for attempt in range(1, row["run_attempt"] + 1) if (identity, attempt) not in self.missing]
            return {"total_count": len(items), "artifacts": items}
        raise AssertionError("unexpected API endpoint: " + endpoint)

    def query(self, command, **kwargs):
        endpoint = command[-1]
        reply = self.api(endpoint)
        if self.api_mutation is not None:
            reply = self.api_mutation(endpoint, reply)
        return reply if isinstance(reply, bytes) else json.dumps(reply)

    def bundle(self, destination, identity, attempt, context=None):
        destination.mkdir(parents=True)
        fixtures.NoWindowEvidenceTests().new_bundle(destination)
        for group in ("core", "gui"):
            folder = destination / (group + "-" + str(identity) + "-" + str(attempt))
            (destination / group).rename(folder)
            manifest = folder / "manifest.json"
            value = json.loads(manifest.read_text())
            scope = json.loads((manifest.parent / "history-context.json").read_text())
            scope["task_id"] = self.task
            if context is not None:
                scope = context
                value["candidate"] = self.candidate
                value.update(started_at="2026-10-11T00:00:00+00:00", finished_at="2026-10-11T00:00:01+00:00")
            write(manifest.parent / "history-context.json", scope)
            value["execution_id"] = folder.name
            value["command"][3] = str(folder)
            report = (folder / "junit.xml").read_text().replace('value="' + group + '"', 'value="' + folder.name + '"')
            (folder / "junit.xml").write_text(report)
            value["retry_key"] = runner.canonical_digest({"task": self.task, "tree": value["candidate"]["tree"]})
            value["ci"].update(run_id=identity, attempt=attempt)
            if identity != self.current_run or attempt < self.current_attempt:
                value.update(outcome="failed", exit_code=1)
            write(manifest.parent / "started.json", value)
            value["artifacts"] = {key: ref(manifest.parent / entry["path"]) for key, entry in value["artifacts"].items()}
            write(manifest, value)
        if (identity, attempt) in self.corrupt:
            (destination / ("core-" + str(identity) + "-" + str(attempt)) / "raw.log").write_text("corrupt")
        self.saved_bundle_paths.append(destination)

    def download(self, command, **kwargs):
        identity = int(command[3]);attempt = int(command[-3].rsplit("-", 1)[1])
        self.downloads.append((identity, attempt))
        self.bundle(Path(command[-1]), identity, attempt)
        return subprocess.CompletedProcess(command, 0)

    def restore(self, name="restored"):
        write(self.event, self.event_data)
        root = self.folder / name
        env = {"GITHUB_EVENT_PATH": str(self.event), "GITHUB_REPOSITORY": self.repo,
               "GITHUB_RUN_ID": str(self.current_run), "GITHUB_RUN_ATTEMPT": str(self.current_attempt)}
        with patch.dict("os.environ", env), patch.object(runner, "snapshot", return_value=self.candidate), \
                patch.object(runner.subprocess, "check_output", side_effect=self.query), \
                patch.object(runner.subprocess, "run", side_effect=self.download):
            runner.restore_history(root)
        return root, runner.read_json(next(root.glob("history-index-*.json")))


def historical_api_fixture(endpoint, rows, pr, repository):
    """Full synthetic original-checkout API boundary for retained adapters."""
    number = pr["number"]
    for row in rows:
        for attempt in range(1, row["run_attempt"] + 1):
            sha = format(row["id"] * 100 + attempt, "040x")
            base = f"repos/{repository}/actions/runs/{row['id']}/attempts/{attempt}"
            if endpoint == base:
                return {**row, "run_attempt": attempt}
            if endpoint == base + "/jobs?per_page=100&page=1":
                return {"total_count": 2, "jobs": [{"id": row["id"] * 10 + index,
                    "name": "Proof " + str(index), "run_id": row["id"], "run_attempt": attempt,
                    "head_sha": row["head_sha"], "head_branch": row["head_branch"],
                    "steps": [{"name": "Check out repository", "conclusion": "success"}]} for index in (0, 1)]}
            commit = HistoryFixture.commit(sha, [pr["base"]["sha"], row["head_sha"]], "8" * 40)
            if endpoint == f"repos/{repository}/git/commits/{sha}":
                return commit
            if endpoint == base + "/logs":
                fixture = HistoryFixture()
                fixture.repo = repository
                fixture.origins = {(row["id"], attempt): (number, sha)}
                fixture.commits = {sha: commit}
                return fixture.logs(row["id"], attempt)
    return None


class HistoryAttributionTests(HistoryFixture):
    def test_empty_links_different_pr_is_excluded_with_originals_and_no_artifact_request(self):
        self.add_run()
        root, scope = self.restore()
        excluded = [row for row in scope["attributions"] if row["disposition"] == "excluded-other-task"]
        self.assertEqual([(row["pr"], row["attempt"]) for row in excluded], [(7, 1)])
        self.assertEqual(excluded[0]["proof"]["method"], "original-run-attempt-checkout")
        self.assertTrue(excluded[0]["reason"])
        self.assertEqual(self.downloads, [])
        self.assertFalse(any("runs/7001/artifacts" in endpoint for endpoint in self.calls))
        runner._validate_history_scope_artifacts(scope, root)
        self.assertTrue(any(entry["path"].endswith("/pr-artifact.zip") for entry in scope["scope_artifacts"]))

    def test_empty_links_current_pr_old_heads_and_all_failed_attempts_retained(self):
        self.add_run(number=self.current_pr, attempts=2)
        root, scope = self.restore()
        self.assertEqual(self.downloads, [(7001, 1), (7001, 2)])
        self.assertEqual([row["attempt"] for row in scope["attributions"] if row["run_id"] == 7001], [1, 2])
        self.assertTrue(all(value["outcome"] == "failed" for _, value in runner.history(root)))
        self.assertEqual(scope["raw_runs"][1]["head_sha"], "e" * 40)

    def test_current_run_empty_links_prior_attempt_failure_is_retained(self):
        self.current_attempt = 2
        self.runs[0]["run_attempt"] = 2
        self.origins[7000, 1] = self.current_pr, self.merge
        root, scope = self.restore()
        self.assertEqual(self.downloads, [(7000, 1)])
        self.assertEqual([row["attempt"] for row in scope["attributions"]], [2, 1])
        self.assertTrue(all(value["outcome"] == "failed" for _, value in runner.history(root)))

    def test_missing_expired_corrupt_applicable_artifacts_fail_closed(self):
        self.add_run(number=self.current_pr, attempts=2)
        for label, target in (("missing", self.missing), ("expired", self.expired), ("corrupt", self.corrupt)):
            target.add((7001, 1))
            with self.subTest(label=label), self.assertRaises(ValueError):
                self.restore(label)
            target.clear()

    def test_unknown_conflicting_api_failure_and_job_attempt_mismatch_refuse(self):
        self.add_run()
        original_logs = self.logs
        def mutation(endpoint, value):
            if "attempts/1/jobs" in endpoint and "runs/7001/" in endpoint:
                value["jobs"][0]["run_attempt"] = 2
            return value
        with patch.object(self, "api_mutation", mutation), self.assertRaises(ValueError):
            self.restore("wrong-attempt")
        with patch.object(self, "logs", return_value=b"corrupt zip"), self.assertRaises(ValueError):
            self.restore("corrupt-logs")
        def unknown(endpoint, value):
            if endpoint.endswith("/pulls/7"):
                value["head"]["ref"] = "other-branch"
            return value
        with patch.object(self, "api_mutation", unknown), self.assertRaises(ValueError):
            self.restore("unknown")
        def failed(endpoint, value):
            if endpoint.endswith("/attempts/1/logs"):
                raise subprocess.CalledProcessError(1, ["gh", "api", endpoint], stderr=b"API unavailable")
            return value
        with patch.object(self, "api_mutation", failed), self.assertRaises(subprocess.CalledProcessError):
            self.restore("api-failed")
        def conflict(identity, attempt):
            stream = io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(original_logs(identity, attempt))) as old, zipfile.ZipFile(stream, "w") as new:
                for item in old.namelist():
                    data = old.read(item)
                    if item.startswith("1_") or item.startswith("Proof 1/"):
                        data = data.replace(b"pull/7/merge", b"pull/8/merge")
                    new.writestr(item, data)
            return stream.getvalue()
        with patch.object(self, "logs", side_effect=conflict), self.assertRaises(ValueError):
            self.restore("conflict")

    def test_checkout_proof_requires_full_step_and_git_ordered_parents(self):
        self.add_run()
        payload = self.logs(7001, 1)
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            log = archive.read("0_Proof 0.txt");system = archive.read("Proof 0/system.txt")
        self.assertEqual(runner.checkout_history_proof(log, system, self.repo)["pr"], 7)
        for label, changed in (("arbitrary-string", b"refs/pull/7/merge"),
                               ("wrong-final-sha", log.replace(b"log -1 --format=%H", b"log -1 --format=%P")),
                               ("missing-action", log.replace(b"actions/checkout@v7", b"custom/action@v1"))):
            with self.subTest(label=label), self.assertRaises(ValueError):
                runner.checkout_history_proof(changed, system, self.repo)
        sha = self.origins[7001, 1][1]
        self.commits[sha]["parents"].reverse()
        with self.assertRaises(ValueError):
            self.restore("wrong-ordered-parents")

    def test_current_nonempty_links_metadata_conflict_multiple_and_source_drift_refuse(self):
        for links in ([{"number": 62}], [{"number": 61}, {"number": 62}], [{"number": True}]):
            self.runs[0]["pull_requests"] = links
            with self.subTest(links=links), self.assertRaises(ValueError):
                self.restore("links-" + str(len(self.calls)))
        self.runs[0]["pull_requests"] = [{"number": 61}]
        self.prs[61]["head"]["sha"] = "f" * 40
        with self.assertRaises(ValueError):
            self.restore("current-metadata-drift")

    def test_run_job_and_artifact_pagination_and_duplicate_guards(self):
        row = self.add_run(number=self.current_pr, links=[{"number": self.current_pr}])
        self.run_pages = [{"total_count": 2, "workflow_runs": self.runs[:1]}, {"total_count": 2, "workflow_runs": self.runs[1:]}]
        self.restore("two-pages")
        self.run_pages[1]["workflow_runs"] = [self.runs[0]]
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.restore("duplicate-runs")
        self.run_pages[1]["workflow_runs"] = []
        with self.assertRaisesRegex(ValueError, "pagination"):
            self.restore("incomplete-runs")
        self.run_pages = None
        row["pull_requests"] = []
        self.job_pages = [{"total_count": 2, "jobs": []}]
        with self.assertRaisesRegex(ValueError, "pagination"):
            self.restore("incomplete-jobs")
        self.job_pages = None
        row["pull_requests"] = [{"number": self.current_pr}]
        self.artifact_pages = [{"total_count": 2, "artifacts": []}]
        with self.assertRaisesRegex(ValueError, "pagination"):
            self.restore("incomplete-artifacts")

    def test_uploaded_style_relocation_history_context_aggregate_verify_and_proof_corruption(self):
        self.add_run(number=self.current_pr, attempts=2)
        self.add_run(identity=7002)
        # The current candidate is independently distinct from the frozen
        # prior fixture tree; retained failures cannot become successful reruns.
        self.candidate["tree"] = "9" * 40
        self.commits[self.merge]["tree"]["sha"] = self.candidate["tree"]
        producer, scope = self.restore()
        # Current successful raw fixture has a different candidate tree than
        # retained failures; it cannot erase them or relabel their candidates.
        self.bundle(producer / "current", self.current_run, 1, context=scope)
        fixture_ci = {"run_id": self.current_run, "attempt": self.current_attempt,
                      "job": "grouped", "event": "pull_request"}
        coverage = runner.aggregate(producer, ci=fixture_ci)
        self.assertEqual(coverage["ci"], {"run_id": 7000, "attempt": 1,
                                          "job": "grouped", "event": "pull_request"})
        write(producer / "coverage.json", coverage)
        self.assertEqual(evidence.verify_coverage(producer / "coverage.json"), coverage)
        uploaded = self.folder / "consumer/tmp/validation-evidence";uploaded.mkdir(parents=True)
        for path in producer.rglob("*"):
            if path.is_file() and (path.suffix in {".json", ".jsonl", ".xml", ".log"} or path.name == "pr-artifact.zip"):
                target = uploaded / path.relative_to(producer);target.parent.mkdir(parents=True, exist_ok=True);shutil.copyfile(path, target)
        shutil.rmtree(producer)
        self.assertEqual(evidence.verify_coverage(uploaded / "coverage.json"), coverage)
        with patch.object(runner, "ROOT", self.folder / "consumer"), patch.dict("os.environ", {
                "GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "pull_request",
                "GITHUB_RUN_ID": str(self.current_run), "GITHUB_RUN_ATTEMPT": str(self.current_attempt)}):
            self.assertEqual(runner.validate_history_context(uploaded, self.candidate), scope)
        folder = uploaded / "current/core-7000-1"
        runner._validate_history_scope_artifacts(scope, folder)
        # Removing an applicable retained original must invalidate the consumer.
        applicable = next(path for path in uploaded.rglob("*.log") if "core-7001-1" in path.parts)
        original = applicable.read_bytes()
        applicable.unlink()
        with self.assertRaises(ValueError):
            evidence.verify_coverage(uploaded / "coverage.json")
        applicable.write_bytes(original)
        proof = uploaded / next(entry["path"] for entry in scope["scope_artifacts"] if entry["path"].endswith("/pr-artifact.zip"))
        proof.write_bytes(proof.read_bytes() + b"corrupt")
        with self.assertRaisesRegex(ValueError, "scope|attribution"):
            evidence.verify_coverage(uploaded / "coverage.json")


class ReleaseContinuationTests(HistoryFixture):
    def setUp(self):
        super().setUp()
        self.spec = dict(runner.RELEASE_HISTORY)
        self.task = self.spec["task_id"]
        self.handoff = self.make_handoff(self.task, self.spec["baseline"], self.spec["source_ref"])
        self.spec["archive_sha256"] = self.handoff["archive_sha256"]
        p = patch.dict(runner.RELEASE_HISTORY, self.spec);p.start();self.addCleanup(p.stop)
        self.branch = self.spec["branch"]
        local = "<!-- validation-local-history\n" + json.dumps(self.handoff) + "\n-->"
        self.body = local + "\n" + runner.release_history_block()
        self.base = self.spec["original_merge"]
        self.candidate["parents"] = [self.base, self.head]
        self.commits[self.merge] = self.commit(self.merge, self.candidate["parents"], self.tree)
        self.commits[self.head] = self.commit(self.head, [self.spec["original_head"]], self.tree)
        self.commits[self.base] = self.commit(self.base, [self.spec["baseline"], self.spec["original_head"]], "9" * 40)
        self.commits[self.spec["original_head"]] = self.commit(self.spec["original_head"], ["6" * 40], "9" * 40)
        self.prs[61] = self.pr(61, self.head, self.base, self.branch, self.body)
        original = self.pr(50, self.spec["original_head"], self.spec["baseline"], self.branch, local)
        original.update(state="closed", merged=True, merge_commit_sha=self.base)
        self.prs[50] = original
        self.event_data = {"pull_request": copy.deepcopy(self.prs[61])}
        self.runs[0] = self.run_row(7000, self.head, 1, "in_progress", [{"number": 61}])

    def test_adopted_pr50_old_head_failures_and_every_attempt_are_preserved(self):
        self.add_run(number=50, attempts=2)
        root, scope = self.restore()
        self.assertEqual(self.downloads, [(7001, 1), (7001, 2)])
        self.assertEqual(scope["release_continuation"]["admitted_prs"], [50, 61])
        self.assertEqual([row["pr"] for row in scope["attributions"]], [61, 50, 50])
        self.assertEqual(len(list(root.rglob("manifest.json"))), 4)
        self.assertTrue(all(value["outcome"] == "failed" for _, value in runner.history(root)))

    def main_stage(self):
        corrective = self.prs[61]
        corrective.update(state="closed", merged=True, merge_commit_sha="7" * 40)
        self.commits["7" * 40] = self.commit("7" * 40, [self.base, self.head], self.tree)
        self.commits["2" * 40] = self.commit("2" * 40, [self.spec["main_baseline"], "7" * 40], self.tree)
        self.body = "<!-- validation-local-history\n" + json.dumps(self.handoff) + "\n-->\n" + runner.release_history_block(61)
        main = self.pr(62, "7" * 40, self.spec["main_baseline"], "develop", self.body)
        main["base"]["ref"] = "main"
        self.prs[62] = main
        self.event_data = {"pull_request": copy.deepcopy(main)}
        self.current_pr = 62
        self.current_run = 8000
        self.candidate = {"head": "2" * 40, "parents": [self.spec["main_baseline"], "7" * 40], "tree": self.tree}
        self.runs[0].update(status="completed", conclusion="success")
        current = self.run_row(8000, "7" * 40, 1, "in_progress", [])
        current["head_branch"] = "develop"
        self.runs.insert(0, current)

    def test_main_stage_imports_corrective_pr_and_pr50_without_generic_continuation(self):
        self.add_run(number=50, attempts=2)
        self.main_stage()
        root, scope = self.restore()
        self.assertEqual(scope["release_continuation"]["admitted_prs"], [50, 61, 62])
        self.assertEqual(self.downloads, [(7000, 1), (7001, 1), (7001, 2)])
        self.assertTrue(all(row["pr"] in {50, 61, 62} for row in scope["attributions"]))
        self.assertEqual(len(list(root.rglob("manifest.json"))), 6)

    def test_marker_source_fake_pr_ledger_and_actual_merge_conflicts_refuse(self):
        original_body = self.event_data["pull_request"]["body"]
        for key, wrong in (("task_id", "other-task"), ("source_ref", "other-source"),
                           ("archive_sha256", "0" * 64), ("main_baseline", "0" * 40),
                           ("original_head", "f" * 40),
                           ("corrective_pr", 61)):
            value = {**self.spec, "corrective_pr": "event.pull_request.number", key: wrong}
            body = original_body.split("<!-- validation-release-continuation", 1)[0] + "<!-- validation-release-continuation\n" + json.dumps(value) + "\n-->"
            self.event_data["pull_request"]["body"] = body
            self.prs[61]["body"] = body
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.restore("bad-" + key)
        self.event_data["pull_request"]["body"] = original_body
        self.prs[61]["body"] = original_body
        self.commits[self.base]["parents"].reverse()
        with self.assertRaisesRegex(ValueError, "ordered parents"):
            self.restore("original-merge-conflict")
        self.commits[self.base]["parents"].reverse()
        self.main_stage()
        self.prs[61]["merge_commit_sha"] = "f" * 40
        with self.assertRaises(ValueError):
            self.restore("main-unproven-corrective-pr")

    def set_feature_chain(self, count):
        parent = self.spec["original_head"]
        for index in reversed(range(count)):
            identity = self.head if index == 0 else format(10000 + index, "040x")
            self.commits[identity] = self.commit(identity, [parent], self.tree if index == 0 else "4" * 40)
            parent = identity

    def scope_only(self):
        return runner.release_history_scope(self.event_data, self.candidate, self.handoff, self.api, self.repo)

    def test_corrective_and_main_single_and_multiple_successors_keep_complete_chain(self):
        names = ("commits", "prs", "event_data", "candidate", "runs", "current_pr", "current_run", "body")
        original = {name: copy.deepcopy(getattr(self, name)) for name in names}
        for phase in ("corrective", "main"):
            for count in (1, 3):
                for name, value in original.items():setattr(self, name, copy.deepcopy(value))
                self.set_feature_chain(count)
                if phase == "main":self.main_stage()
                with self.subTest(phase=phase, successors=count):
                    root, context = self.restore(phase + str(count))
                    chain = context["release_continuation"]["feature_chain"]
                    self.assertEqual(len(chain), count + 1)
                    self.assertEqual(chain[0]["sha"], self.head)
                    self.assertEqual(chain[-1]["sha"], self.spec["original_head"])
                    self.assertEqual(chain[-1]["tree"]["sha"], "9" * 40)
                    self.assertTrue(all(node["parents"] == [{"sha": following["sha"]}]
                                        for node, following in zip(chain, chain[1:])))
                    self.assertTrue((root / context["scope_artifacts"][0]["path"]).is_file())

    def test_both_entrypoints_refuse_missing_cycle_disconnected_nonsingle_and_contradictory_chain(self):
        names = ("commits", "prs", "event_data", "candidate", "runs", "current_pr", "current_run", "body")
        original = {name: copy.deepcopy(getattr(self, name)) for name in names}
        for phase in ("corrective", "main"):
            for defect in ("missing", "cycle", "disconnected", "nonsingle", "wrong-sha", "invalid-tree", "wrong-anchor-tree", "api-failure", "bound"):
                for name, value in original.items():setattr(self, name, copy.deepcopy(value))
                self.set_feature_chain(17 if defect == "bound" else 3)
                intermediate = format(10001, "040x")
                if phase == "main":self.main_stage()
                if defect == "missing":del self.commits[intermediate]
                elif defect == "cycle":self.commits[intermediate]["parents"] = [{"sha": self.head}]
                elif defect == "disconnected":self.commits[intermediate]["parents"] = []
                elif defect == "nonsingle":self.commits[intermediate]["parents"].append({"sha": "f" * 40})
                elif defect == "wrong-sha":self.commits[intermediate]["sha"] = "f" * 40
                elif defect == "invalid-tree":self.commits[intermediate]["tree"]["sha"] = "not-a-tree"
                elif defect == "wrong-anchor-tree":self.commits[self.spec["original_head"]]["tree"]["sha"] = "f" * 40
                def api(endpoint):
                    if defect == "api-failure" and endpoint.endswith("/" + intermediate):
                        raise subprocess.CalledProcessError(1, ["gh", "api", endpoint], stderr="unavailable")
                    return self.api(endpoint)
                with self.subTest(phase=phase, defect=defect), self.assertRaises((ValueError, KeyError, subprocess.CalledProcessError)):
                    runner.release_history_scope(self.event_data, self.candidate, self.handoff, api, self.repo)
