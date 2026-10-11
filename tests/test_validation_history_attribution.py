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
            else:
                sha = self.origins[identity, attempt][1]
                original = self.commits[sha]
                value["candidate"] = {"head": sha, "parents": [item["sha"] for item in original["parents"]], "tree": original["tree"]["sha"]}
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


def historical_api_fixture(endpoint, rows, pr, repository, candidates=None):
    """Full synthetic original-checkout API boundary for retained adapters."""
    number = pr["number"]
    for row in rows:
        for attempt in range(1, row["run_attempt"] + 1):
            candidate = (candidates or {}).get((row["id"], attempt), {
                "head": format(row["id"] * 100 + attempt, "040x"), "parents": [pr["base"]["sha"], row["head_sha"]], "tree": "8" * 40})
            sha = candidate["head"]
            base = f"repos/{repository}/actions/runs/{row['id']}/attempts/{attempt}"
            if endpoint == base:
                return {**row, "run_attempt": attempt}
            if endpoint == base + "/jobs?per_page=100&page=1":
                return {"total_count": 2, "jobs": [{"id": row["id"] * 10 + index,
                    "name": "Proof " + str(index), "run_id": row["id"], "run_attempt": attempt,
                    "head_sha": row["head_sha"], "head_branch": row["head_branch"],
                    "steps": [{"name": "Check out repository", "conclusion": "success"}]} for index in (0, 1)]}
            commit = HistoryFixture.commit(sha, candidate["parents"], candidate["tree"])
            if endpoint == f"repos/{repository}/git/commits/{sha}":
                return commit
            if endpoint == base + "/logs":
                fixture = HistoryFixture()
                fixture.repo = repository
                fixture.origins = {(row["id"], attempt): (number, sha)}
                fixture.commits = {sha: commit}
                return fixture.logs(row["id"], attempt)
    return None


def own_core_fixture(destination, identity, attempt, candidate, task):
    """Add a mutable complete own fixture; never change pinned nested bytes."""
    container = destination / ("model-" + str(identity) + "-" + str(attempt));container.mkdir()
    fixtures.NoWindowEvidenceTests().new_bundle(container)
    folder = destination / ("own-" + str(identity) + "-" + str(attempt))
    (container / "core").rename(folder)
    assert container.resolve().is_relative_to(destination.resolve())
    shutil.rmtree(container)  # only the isolated raw GUI fixture, no Tk execution
    value = runner.read_json(folder / "manifest.json")
    context = runner.read_json(folder / "history-context.json");context["task_id"] = task
    write(folder / "history-context.json", context)
    value.update(execution_id=folder.name, candidate=candidate, ci={"run_id": identity, "attempt": attempt, "job": "grouped", "event": "pull_request"})
    value["command"][3] = str(folder)
    value["retry_key"] = runner.canonical_digest({"task": task, "tree": candidate["tree"]})
    (folder / "junit.xml").write_text((folder / "junit.xml").read_text().replace('value="core"', 'value="' + folder.name + '"'))
    write(folder / "started.json", value)
    value["artifacts"] = {key: ref(folder / entry["path"]) for key, entry in value["artifacts"].items()}
    write(folder / "manifest.json", value)
    runner.read_verified_manifest(folder / "manifest.json")


class HistoryAttributionTests(HistoryFixture):
    def test_nonempty_mutable_links_are_observations_not_original_ownership(self):
        row = self.add_run(number=7, links=[{"number": self.current_pr, "head": self.prs[self.current_pr]["head"]}])
        root, context = self.restore("drift-other")
        original = context["attributions"][1]
        self.assertEqual((original["pr"], original["disposition"]), (7, "excluded-other-task"))
        self.assertTrue(original["proof"]["association_drift"])
        self.assertEqual(original["proof"]["run_links_observation"], row["pull_requests"])
        self.assertEqual(self.downloads, [])
        self.assertFalse(any("7001/artifacts" in path for path in self.calls))
        runner._validate_history_scope_artifacts(context, root)
        row["pull_requests"] = [{"number": 7}]
        self.origins[7001, 1] = self.current_pr, self.origins[7001, 1][1]
        root, context = self.restore("drift-same")
        self.assertEqual(context["attributions"][1]["pr"], self.current_pr)
        self.assertEqual(self.downloads, [(7001, 1)])
        # A later PR head differs from the original run head. Original checkout
        # and parents remain authoritative; equality to latest metadata is not.
        self.assertNotEqual(self.prs[self.current_pr]["head"]["sha"], row["head_sha"])
        self.assertEqual(len(runner.history(root)), 2)

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
        coverage = runner.aggregate(producer)
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
        self.origins[7000, 1] = 61, self.merge
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


class ReleaseNextHistoryTests(HistoryFixture):
    """Finite /2 release fixtures; no network, Tk or synthetic formal PASS."""
    def setUp(self):
        super().setUp()
        self.spec = dict(runner.RELEASE_HISTORY)
        self.task = self.spec["task_id"]
        self.handoff = self.make_handoff(self.task, self.spec["baseline"], self.spec["source_ref"])
        self.spec["archive_sha256"] = self.handoff["archive_sha256"]
        self.next_spec = {**runner.RELEASE_HISTORY_NEXT, "archive_sha256": self.handoff["archive_sha256"]}
        for target, value in ((runner.RELEASE_HISTORY, self.spec), (runner.RELEASE_HISTORY_NEXT, self.next_spec)):
            item = patch.dict(target, value);item.start();self.addCleanup(item.stop)
        self.branch = self.spec["branch"]
        self.local = "<!-- validation-local-history\n" + json.dumps(self.handoff) + "\n-->\n"
        self.body = self.local + runner.release_history_block(successor=True)
        self.current_pr = 53
        self.base = self.next_spec["predecessor_merge"]
        self.candidate["parents"] = [self.base, self.head]
        self.commits[self.merge] = self.commit(self.merge, self.candidate["parents"], self.tree)
        self.commits[self.head] = self.commit(self.head, [self.next_spec["predecessor_head"]], self.tree)
        for identity, parents, tree in (
            (self.spec["original_head"], ["6" * 40], "9" * 40),
            (self.spec["original_merge"], [self.spec["baseline"], self.spec["original_head"]], "9" * 40),
            (self.next_spec["predecessor_head"], [self.spec["original_head"]], "4" * 40),
            (self.base, [self.spec["original_merge"], self.next_spec["predecessor_head"]], "4" * 40)):
            self.commits[identity] = self.commit(identity, parents, tree)
        self.prs = {53: self.pr(53, self.head, self.base, self.branch, self.body)}
        self.prs[50] = self.pr(50, self.spec["original_head"], self.spec["baseline"], self.branch, self.local)
        self.prs[50].update(state="closed", merged=True, merge_commit_sha=self.spec["original_merge"])
        self.prs[51] = self.pr(51, self.next_spec["predecessor_head"], self.spec["original_merge"], self.branch,
                               self.local + runner.release_history_block())
        self.prs[51].update(state="closed", merged=True, merge_commit_sha=self.base)
        self.prs[52] = self.pr(52, self.base, self.spec["main_baseline"], "develop",
                               self.local + runner.release_history_block(53, successor=True))
        self.prs[52]["base"]["ref"] = "main"
        self.event_data = {"pull_request": copy.deepcopy(self.prs[53])}
        self.runs[0] = self.run_row(7000, self.head, 1, "in_progress", [{"number": 53}])
        self.precore_rows = {}

    def main_stage(self):
        self.origins[7000, 1] = 53, self.merge
        self.prs[53].update(state="closed", merged=True, merge_commit_sha="7" * 40)
        self.commits["7" * 40] = self.commit("7" * 40, [self.base, self.head], self.tree)
        self.commits["2" * 40] = self.commit("2" * 40, [self.spec["main_baseline"], "7" * 40], self.tree)
        self.prs[52]["head"]["sha"] = "7" * 40
        self.event_data = {"pull_request": copy.deepcopy(self.prs[52])}
        self.current_pr, self.current_run = 52, 8000
        self.candidate = {"head": "2" * 40, "tree": self.tree, "parents": [self.spec["main_baseline"], "7" * 40]}
        self.runs[0].update(status="completed", conclusion="success")
        current = self.run_row(8000, "7" * 40, 1, "in_progress", [{"number": 52}]);current["head_branch"] = "develop"
        self.runs.insert(0, current)

    def add_precore(self, identity=8100):
        row = self.run_row(identity, self.base, 1, "completed", [{"number": 52}]);row["head_branch"] = "develop"
        self.runs.append(row)
        sha = "5" * 40
        self.commits[sha] = self.commit(sha, [self.spec["main_baseline"], self.base], "4" * 40)
        self.origins[identity, 1] = 52, sha
        job_id = identity * 100 + 10
        names = [("Check out repository", "success"), ("Restore validation history", "failure"),
                 ("Run core group", "skipped"), ("Aggregate functional coverage", "skipped"),
                 ("Verify functional coverage", "skipped")]
        steps = [{"name": name, "number": i, "status": "completed", "conclusion": result,
                  "started_at": "2026-10-11T00:00:00Z", "completed_at": "2026-10-11T00:00:00Z"}
                 for i, (name, result) in enumerate(names, 1)]
        job = {"id": job_id, "run_id": identity, "run_attempt": 1, "head_sha": self.base,
               "head_branch": "develop", "name": "Grouped validation", "status": "completed", "conclusion": "failure", "steps": steps}
        with zipfile.ZipFile(io.BytesIO(super().logs(identity, 1))) as old:
            checkout = old.read("0_Proof 0.txt");system = old.read("Proof 0/system.txt")
        failure = ("2026-10-11T00:00:00Z ##[group]Run python scripts/validation_runner.py restore-history --evidence-root tmp/validation-evidence\n"
                   "2026-10-11T00:00:00Z prior run/attempt history unavailable: validation-evidence-other-1\n"
                   "2026-10-11T00:00:00Z ##[error]Process completed with exit code 1.\n").encode()
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as zipped:
            zipped.writestr("0_Grouped validation.txt", checkout + failure)
            zipped.writestr("Grouped validation/system.txt", system)
            zipped.writestr("Grouped validation/2_Restore validation history.txt", failure)
        original_pr = copy.deepcopy(self.prs[52]);original_pr["body"] = self.local + runner.release_history_block(51)
        original_pr["head"]["sha"] = self.base
        archived = {"method": "current-live-checkout", "event": {"pull_request": original_pr},
                    "candidate": {"head": sha, "parents": [self.spec["main_baseline"], self.base], "tree": "4" * 40}, "jobs": [job_id]}
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(self.handoff["archive_base64"]))) as sealed:
            ledger = sealed.read("ledger.json")
            stores = {"local-history/" + name: sealed.read(name) for name in ("executions/store.json", "declarations/store.json")}
        contents = {"history-scope-" + "1" * 32 + "/current-checkout-proof.json": json.dumps(archived).encode(),
                    "history-scope-" + "2" * 32 + "/attribution.json": b'{"pr":52,"disposition":"applicable","interpretation":"original wrong attribution preserved"}',
                    "local-history/handoff.json": json.dumps(self.handoff).encode(), "local-history/ledger.json": ledger, **stores}
        self.precore_rows[identity] = {"job": job, "logs": stream.getvalue(), "contents": contents}

    def artifact_zip(self, identity):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as zipped:
            for name, payload in self.precore_rows[identity]["contents"].items():zipped.writestr(name, payload)
        return stream.getvalue()

    def contradict_precore_identity(self, contents, label):
        """Alter only a new negative copy, retaining a valid rebuilt ZIP/hash."""
        if label in {"base-sha", "base-ref"}:
            name = next(name for name in contents if name.endswith("current-checkout-proof.json"))
            value = json.loads(contents[name])
            value["event"]["pull_request"]["base"]["sha" if label == "base-sha" else "ref"] = (
                "f" * 40 if label == "base-sha" else "develop")
            contents[name] = json.dumps(value).encode()
        elif label == "store-rebinding":
            for name in ("ledger.json", "executions/store.json", "declarations/store.json"):
                name = "local-history/" + name;value = json.loads(contents[name]);value["store_id"] = "f" * 32
                contents[name] = json.dumps(value).encode()
        elif label in {"ledger-store", "ledger-schema", "ledger-created"}:
            name = "local-history/ledger.json";value = json.loads(contents[name])
            key, new_value = {"ledger-store": ("store_id", "f" * 32),
                              "ledger-schema": ("schema", "zhuyin-local-history/future"),
                              "ledger-created": ("created_at", "2026-10-12T00:00:00+00:00")}[label]
            value[key] = new_value
            contents[name] = json.dumps(value).encode()
        else:
            kind, action = label.split("-")
            name = "local-history/" + kind + "/store.json"
            if action == "missing":del contents[name]
            else:
                value = json.loads(contents[name]);value["store_id"] = "f" * 32
                contents[name] = json.dumps(value).encode()

    precore_identity_negatives = ("base-sha", "base-ref", "ledger-store", "ledger-schema", "ledger-created", "store-rebinding",
                                  "executions-missing", "executions-mismatch", "declarations-missing", "declarations-mismatch")

    def api(self, endpoint):
        match = re.fullmatch(r"repos/" + re.escape(self.repo) + r"/actions/runs/([0-9]+)/attempts/1/jobs\?per_page=100&page=1", endpoint)
        if match and int(match[1]) in self.precore_rows:
            self.calls.append(endpoint)
            return {"total_count": 1, "jobs": [copy.deepcopy(self.precore_rows[int(match[1])]["job"])]}
        match = re.fullmatch(r"repos/" + re.escape(self.repo) + r"/actions/artifacts/([0-9]+)/zip", endpoint)
        if match and int(match[1]) in self.precore_rows:
            self.calls.append(endpoint);return self.artifact_zip(int(match[1]))
        result = super().api(endpoint)
        match = re.fullmatch(r"repos/" + re.escape(self.repo) + r"/actions/runs/([0-9]+)/artifacts\?per_page=100&page=1", endpoint)
        if match and int(match[1]) in self.precore_rows and result["artifacts"]:
            identity = int(match[1]);row = next(row for row in self.runs if row["id"] == identity)
            result["artifacts"][0].update(id=identity, digest="sha256:" + hashlib.sha256(self.artifact_zip(identity)).hexdigest(),
                workflow_run={"id": identity, "head_sha": row["head_sha"], "head_branch": row["head_branch"]})
        return result

    def logs(self, identity, attempt):
        return self.precore_rows[identity]["logs"] if identity in self.precore_rows else super().logs(identity, attempt)

    def download(self, command, **kwargs):
        identity = int(command[3])
        if identity not in self.precore_rows:return super().download(command, **kwargs)
        destination = Path(command[-1]);destination.mkdir(parents=True)
        for name, payload in self.precore_rows[identity]["contents"].items():
            target = destination / name;target.parent.mkdir(parents=True, exist_ok=True);target.write_bytes(payload)
        self.downloads.append((identity, 1))
        return subprocess.CompletedProcess(command, 0)

    def test_new_corrective_and_main_keep_pr50_pr51_old_heads_every_attempt_and_precore(self):
        self.add_run(number=50, identity=7001, attempts=2)
        self.add_run(number=51, identity=7002, attempts=2, links=[{"number": 53}])
        self.add_precore()
        root, context = self.restore("next-corrective")
        self.assertEqual(context["release_continuation"]["admitted_prs"], [50, 51, 52, 53])
        self.assertEqual(self.downloads, [(7001, 1), (7001, 2), (7002, 1), (7002, 2), (8100, 1)])
        self.assertEqual(len(context["pre_core_failures"]), 1)
        self.assertEqual(len(context["executions"]), 8)
        self.assertEqual(len(runner.history(root)), 8)
        record = runner.read_pre_core_history(root / context["pre_core_failures"][0]["path"])
        self.assertEqual((record["run_id"], record["attempt"], record["pr"]), (8100, 1, 52))
        self.assertNotIn("execution_id", record)
        self.assertFalse(self.prs[53]["merged"])
        with self.assertRaisesRegex(ValueError, "latest core|earlier same|missing core"):
            runner.aggregate(root)
        # A genuinely new multiple-successor head keeps both single-parent
        # edges back through the independently pinned PR51 head.
        previous = self.head;self.head = "1" * 40
        self.commits[self.head] = self.commit(self.head, [previous], self.tree)
        self.commits[self.merge] = self.commit(self.merge, [self.base, self.head], self.tree)
        self.prs[53]["head"]["sha"] = self.head
        self.runs[0]["head_sha"] = self.head
        self.event_data = {"pull_request": copy.deepcopy(self.prs[53])}
        self.candidate["parents"] = [self.base, self.head]
        multiple, multiple_context = self.restore("next-multiple-successor")
        self.assertEqual([node["sha"] for node in multiple_context["release_continuation"]["feature_chain"]],
                         [self.head, previous, self.next_spec["predecessor_head"], self.spec["original_head"]])
        runner._validate_history_scope_artifacts(multiple_context, multiple)
        self.main_stage()
        root, context = self.restore("next-main")
        self.assertEqual(context["release_continuation"]["admitted_prs"], [50, 51, 52, 53])
        self.assertEqual(len(context["executions"]), 10)
        runner._validate_history_scope_artifacts(context, root)

    def test_next_chain_source_decl_actual_merge_and_missing_anchor_refuse(self):
        originals = {name: copy.deepcopy(getattr(self, name)) for name in ("prs", "commits", "event_data", "candidate", "runs", "current_pr", "current_run")}
        for phase in ("corrective", "main"):
            for label in ("base", "prior-head", "prior-parents", "prior-tree", "prior-marker", "handoff", "anchor", "unknown-schema", "multiple-marker", "main-number", "actual-parents", "actual-tree"):
                for name, value in originals.items():setattr(self, name, copy.deepcopy(value))
                if phase == "main":self.main_stage()
                if label == "base":self.prs[53]["base"]["sha"] = self.spec["original_merge"]
                elif label == "prior-head":self.prs[51]["head"]["sha"] = "f" * 40
                elif label == "prior-parents":self.commits[self.base]["parents"].reverse()
                elif label == "prior-tree":self.commits[self.base]["tree"]["sha"] = "f" * 40
                elif label == "prior-marker":self.prs[51]["body"] = self.local + runner.release_history_block(successor=True)
                elif label == "handoff":self.prs[51]["body"] = self.prs[51]["body"].replace(self.spec["source_ref"], "wrong-source")
                elif label == "anchor":self.commits[self.head]["parents"] = [{"sha": self.spec["original_head"]}]
                elif label == "unknown-schema":self.event_data["pull_request"]["body"] = self.body.replace("continuation/2", "continuation/3")
                elif label == "multiple-marker":self.event_data["pull_request"]["body"] += runner.release_history_block(successor=True)
                elif label == "main-number":
                    self.prs[52]["number"] = 54
                    if phase == "main":self.event_data["pull_request"]["number"] = 54
                elif label == "actual-parents" and phase == "main":self.commits["7" * 40]["parents"].reverse()
                elif label == "actual-tree" and phase == "main":self.commits["7" * 40]["tree"]["sha"] = "f" * 40
                else:continue
                if phase == "corrective" and label == "base":self.event_data["pull_request"] = copy.deepcopy(self.prs[53])
                with self.subTest(phase=phase, label=label), self.assertRaises((ValueError, KeyError)):
                    runner.release_history_scope(self.event_data, self.candidate, self.handoff, self.api, self.repo)

    def test_precore_started_missing_or_contaminated_originals_refuse(self):
        self.add_precore()
        original = copy.deepcopy(self.precore_rows[8100])
        for label in ("started-step", "skipped-but-duration", "missing-failure-log", "core-command", "started", "manifest", "raw", "wrong-event", "wrong-seal", "unsafe-path", *self.precore_identity_negatives):
            self.precore_rows[8100] = copy.deepcopy(original)
            row = self.precore_rows[8100]
            if label == "started-step":row["job"]["steps"][2]["conclusion"] = "failure"
            elif label == "skipped-but-duration":row["job"]["steps"][2]["completed_at"] = "2026-10-11T00:00:01Z"
            elif label in {"missing-failure-log", "core-command"}:
                stream = io.BytesIO()
                with zipfile.ZipFile(io.BytesIO(row["logs"])) as old, zipfile.ZipFile(stream, "w") as new:
                    for name in old.namelist():
                        if label == "missing-failure-log" and name.endswith("_Restore validation history.txt"):continue
                        new.writestr(name, old.read(name))
                    if label == "core-command":new.writestr("Grouped validation/3_Run core group.txt", "core started")
                row["logs"] = stream.getvalue()
            elif label in {"started", "manifest", "raw"}:row["contents"]["core/" + {"started": "started.json", "manifest": "manifest.json", "raw": "raw.log"}[label]] = b"{}"
            elif label in {"wrong-event", "wrong-seal"}:
                name = next(name for name in row["contents"] if name.endswith("current-checkout-proof.json"))
                value = json.loads(row["contents"][name]);key = value["event"]["pull_request"]
                if label == "wrong-event":key["head"]["sha"] = "f" * 40
                else:key["body"] = key["body"].replace(self.spec["archive_sha256"], "f" * 64)
                row["contents"][name] = json.dumps(value).encode()
            elif label == "unsafe-path":row["contents"]["../outside.json"] = b"{}"
            else:self.contradict_precore_identity(row["contents"], label)
            with self.subTest(label=label), self.assertRaises((ValueError, KeyError)):
                self.restore("bad-precore-" + label)

    def test_precore_download_reader_context_aggregate_verify_relocation_and_hash_failures(self):
        self.add_precore()
        producer, scope = self.restore()
        self.bundle(producer / "current", self.current_run, 1, context=scope)
        coverage = runner.aggregate(producer)
        write(producer / "coverage.json", coverage)
        self.assertEqual(evidence.verify_coverage(producer / "coverage.json"), coverage)
        self.assertEqual(len(coverage["history_manifests"]), 2)
        uploaded = self.folder / "consumer/tmp/validation-evidence";uploaded.mkdir(parents=True)
        for path in producer.rglob("*"):
            if path.is_file() and (path.suffix in {".json", ".jsonl", ".xml", ".log"} or path.name == "pr-artifact.zip"):
                target = uploaded / path.relative_to(producer);target.parent.mkdir(parents=True, exist_ok=True);shutil.copyfile(path, target)
        shutil.rmtree(producer)
        with patch.object(runner, "ROOT", self.folder / "consumer"), patch.dict("os.environ", {
                "GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "pull_request", "GITHUB_RUN_ID": str(self.current_run), "GITHUB_RUN_ATTEMPT": "1"}):
            self.assertEqual(runner.validate_history_context(uploaded, self.candidate), scope)
        self.assertEqual(evidence.verify_coverage(uploaded / "coverage.json"), coverage)
        record_path = uploaded / scope["pre_core_failures"][0]["path"]
        record = runner.read_pre_core_history(record_path)
        # Rehash every negative's ZIP, record, context and manifest. Coverage
        # keeps its existing relative manifest paths, without invented hashes.
        snapshot = {path: path.read_bytes() for path in uploaded.rglob("*") if path.is_file()}
        archive_path = uploaded / record["sources"]["artifact"]["path"]
        for label in self.precore_identity_negatives:
            contents = copy.deepcopy(self.precore_rows[8100]["contents"])
            self.contradict_precore_identity(contents, label)
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w") as zipped:
                for name, payload in contents.items():zipped.writestr(name, payload)
            archive_path.write_bytes(stream.getvalue())
            wrong = copy.deepcopy(record);archive_sha = runner.digest(archive_path)
            wrong["sources"]["artifact"]["sha256"] = archive_sha
            wrong["artifact"]["digest"] = "sha256:" + archive_sha
            wrong["artifact_members"] = {name: hashlib.sha256(payload).hexdigest() for name, payload in sorted(contents.items())}
            write(record_path, wrong)
            altered_scope = copy.deepcopy(scope)
            for entry in altered_scope["scope_artifacts"] + altered_scope["pre_core_failures"]:
                if entry["path"] == record["sources"]["artifact"]["path"]:entry["sha256"] = archive_sha
                elif entry["path"] == scope["pre_core_failures"][0]["path"]:entry["sha256"] = runner.digest(record_path)
            for path in uploaded.glob("history-index-*.json"):write(path, altered_scope)
            for path in uploaded.rglob("history-context.json"):
                write(path, altered_scope)
                manifest_path = path.parent / "manifest.json"
                manifest = runner.read_json(manifest_path)
                manifest["artifacts"]["history_context"]["sha256"] = runner.digest(path)
                write(manifest_path, manifest)
            error = "pre-core original event/candidate/source differs" if label.startswith("base-") else "pre-core original ledger/store differs"
            with self.subTest(label=label, consumer="reader"), self.assertRaisesRegex(ValueError, error):
                runner.read_pre_core_history(record_path)
            with patch.object(runner, "ROOT", self.folder / "consumer"), patch.dict("os.environ", {
                    "GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "pull_request", "GITHUB_RUN_ID": str(self.current_run), "GITHUB_RUN_ATTEMPT": "1"}):
                with self.subTest(label=label, consumer="context"), self.assertRaisesRegex(ValueError, error):
                    runner.validate_history_context(uploaded, self.candidate)
            with self.subTest(label=label, consumer="aggregate"), self.assertRaisesRegex(ValueError, error):runner.aggregate(uploaded)
            with self.subTest(label=label, consumer="verify"), self.assertRaisesRegex(ValueError, error):
                evidence.verify_coverage(uploaded / "coverage.json")
            for path, payload in snapshot.items():path.write_bytes(payload)
        self.assertEqual(evidence.verify_coverage(uploaded / "coverage.json"), coverage)
        for entry in (scope["pre_core_failures"][0], *record["sources"].values()):
            path = uploaded / entry["path"];original = path.read_bytes();path.unlink()
            with self.subTest(missing=entry["path"]), self.assertRaises(ValueError):evidence.verify_coverage(uploaded / "coverage.json")
            path.write_bytes(original + b"corrupt")
            with self.subTest(corrupt=entry["path"]), self.assertRaises(ValueError):evidence.verify_coverage(uploaded / "coverage.json")
            path.write_bytes(original)
        # Hash-updated records still must pass the semantic originals reader.
        wrong = copy.deepcopy(record);wrong["attempt"] = 2;write(record_path, wrong)
        with self.assertRaises(ValueError):runner.read_pre_core_history(record_path)
        write(record_path, record)
        duplicate = uploaded / ("history-scope-" + "e" * 32) / "pre-core.json";duplicate.parent.mkdir()
        wrong = copy.deepcopy(record);wrong["sources"]["logs"]["sha256"] = "f" * 64;write(duplicate, wrong)
        with self.assertRaises(ValueError):runner.aggregate(uploaded)
