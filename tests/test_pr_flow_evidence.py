"""Synthetic hosted inputs; real Git, documentation CLI, ZIP and validators.

The default short fixture explicitly models command transport. It is semantic
parser evidence, never actual local/hosted short execution. actual_short=True
uses the unchanged six commands and real installed environment.
"""
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts import pr_flow_ci as ci
from scripts import pr_flow_evidence as flow
from scripts import pr_flow_policy as policy
from scripts import pr_flow_transport as transport
from scripts import validation_evidence as evidence
from scripts import validation_runner as runner
try:
    from tests.test_pr_flow_transport import SyntheticAPI, SyntheticFixture, evidence_tree, forbid_evidence_writes
except ModuleNotFoundError:
    from test_pr_flow_transport import SyntheticAPI, SyntheticFixture, evidence_tree, forbid_evidence_writes


def upload_payload_files(workspace, job, upload):
    """Project the actual workflow's bounded paths, including directory inputs."""
    try:
        from tests.test_ci_validation_workflow import current_job, step
    except ModuleNotFoundError:
        from test_ci_validation_workflow import current_job, step
    body = step(current_job(job), upload)
    block = re.search(r"^          path: \|\n((?:            .*\n)+)", body, re.M).group(1)
    included = set()
    for pattern in (line.strip() for line in block.splitlines()):
        matches = set()
        for path in workspace.glob(pattern.removeprefix("!")):
            matches.update(file for file in path.rglob("*") if file.is_file()) if path.is_dir() else matches.add(path)
        if pattern.startswith("!"): included.difference_update(matches)
        else: included.update(matches)
    if "include-hidden-files: false" in body:
        included = {path for path in included if not any(part.startswith(".") for part in path.relative_to(workspace).parts)}
    return included


def documentation_api_fixture(folder, *, initial_files=None):
    """Build a synthetic source authority around genuine current Git objects."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    files = dict(initial_files or {})
    files.setdefault(".gitignore", b"tmp/\n__pycache__/\n*.pyc\n")
    fixture = SyntheticFixture(folder / "source", initial_files=files)
    root = fixture.root
    producer = SyntheticAPI(fixture)
    transfer = transport.fetch_capture(producer, fixture.candidate, fixture.event, root / "tmp/pr-flow-ci/transport", root)
    plan = ci.validated_documentation_plan(transfer["capture"], fixture.event, fixture.environment, root,
                                           original_resolver=transfer["original_resolver"])
    plan["source"] = transfer["source"]
    plan_path = root / "tmp/pr-flow-ci/plan.json"
    ci.write(plan_path, plan)
    documentation = root / "tmp/pr-flow-ci/docs/checks.json"
    record = ci.run_pr_documentation_checks(fixture.event, fixture.environment, documentation, root)
    if record["status"] != "success":
        raise AssertionError("real documentation fixture failed: " + str(record))
    ci.verify_pr_documentation_checks(documentation, fixture.candidate, _root=root)
    binding = {key: record[key] for key in ("ci", "checkout")}
    source_identity = flow._source_identity(record)
    actual_sha = fixture.git("commit-tree", source_identity["tree"], "-p", fixture.candidate["base"], "-p", fixture.candidate["head"],
                             input=b"synthetic actual merge, distinct from tested integration\n")
    fixture.git("update-ref", "refs/heads/synthetic", actual_sha)
    actual = {"head": actual_sha, "tree": source_identity["tree"], "parents": source_identity["parents"]}
    fixture.current_pr.update(state="closed", merged=True, merge_commit_sha=actual_sha)
    fixture.event["pull_request"].update(merged=True, merge_commit_sha=actual_sha)
    pr_run = dict(id=binding["ci"]["run_id"], run_number=50, run_attempt=1, event="pull_request",
                  path=".github/workflows/ci.yml", head_sha=fixture.candidate["head"], head_branch="synthetic/docs",
                  status="completed", conclusion="success", repository=deepcopy(fixture.repository))
    archive_io = io.BytesIO()
    with zipfile.ZipFile(archive_io, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(upload_payload_files(root, "test", "Upload documentation evidence")):
            archive.write(file, file.relative_to(root / "tmp/pr-flow-ci").as_posix())
    pr_archive = archive_io.getvalue()
    artifact = dict(id=901, name=f"documentation-validation-evidence-{pr_run['id']}-1", expired=False,
                    digest="sha256:" + ci.digest(pr_archive), size_in_bytes=len(pr_archive),
                    workflow_run=dict(id=pr_run["id"], repository_id=fixture.repository["id"], head_sha=fixture.candidate["head"], head_branch="synthetic/docs"))
    commit = {"sha": actual_sha, "parents": [{"sha": sha} for sha in actual["parents"]], "commit": {"tree": {"sha": actual["tree"]}}}
    class DocumentationAPI(SyntheticAPI):
        def request(self, endpoint, *, binary=False):
            if endpoint == "commits/" + actual_sha:
                self.calls.append(("request", endpoint, binary)); return deepcopy(commit)
            if endpoint == "actions/runs/" + str(pr_run["id"]):
                self.calls.append(("request", endpoint, binary)); return deepcopy(pr_run)
            if endpoint == "actions/artifacts/901/zip":
                self.calls.append(("request", endpoint, binary))
                if not binary: raise AssertionError("documentation artifact must be binary")
                return pr_archive
            return super().request(endpoint, binary=binary)

        def pages(self, endpoint, key=None):
            self.calls.append(("pages", endpoint, key))
            if endpoint == "commits/" + actual_sha + "/pulls": return [deepcopy(fixture.current_pr)]
            if endpoint == "actions/workflows/ci.yml/runs?event=pull_request&head_sha=" + fixture.candidate["head"]:
                return [deepcopy(pr_run)]
            if endpoint == "actions/runs/" + str(pr_run["id"]) + "/artifacts": return [deepcopy(artifact)]
            self.calls.pop()
            return super().pages(endpoint, key)
    steps = {name: "skipped" for name in ci.FULL_STEPS}
    steps.update({name: "success" for name in ("Check out repository", "Set up Python", "Show Python version",
                  "Resolve current validation route", *ci.DOC_STEPS, "Verify required validation results")})
    pr_ci = dict(workflow=".github/workflows/ci.yml", event="pull_request", branch="synthetic/docs", head_sha=fixture.candidate["head"],
                 parents=source_identity["parents"], tested_sha=source_identity["head"], tree=source_identity["tree"],
                 run_id=pr_run["id"], attempt=1, latest_run_id=pr_run["id"], latest_attempt=1, status="completed", conclusion="success",
                 jobs=[dict(name="Python 3.13", conclusion="success", runner="windows-latest", python_version="3.13.0",
                            run_id=pr_run["id"], attempt=1, tested_sha=source_identity["head"], steps=steps)])
    return {"source_root": root, "candidate": fixture.candidate, "capture": transfer["capture"], "original_resolver": transfer["original_resolver"],
            "transfer": transfer, "event": fixture.event, "environment": fixture.environment, "pr_ci": pr_ci,
            "pr": dict(number=123, base_branch="develop", head_branch="synthetic/docs"), "actual_merge": actual,
            "api": DocumentationAPI(fixture), "documentation_path": documentation, "plan_path": plan_path,
            "fixture": fixture, "artifact": artifact, "pr_run": pr_run,
            "fixture_context": {"source_authority": "synthetic API fixture, not hosted attestation", "documentation": "real CLI/Git checks"}}


def documentation_short_fixture(folder, *, initial_files=None, actual_short=False):
    result = documentation_api_fixture(folder, initial_files=initial_files)
    actual, root = result["actual_merge"], result["source_root"]
    environment = dict(GITHUB_EVENT_NAME="push", GITHUB_REF="refs/heads/develop", GITHUB_SHA=actual["head"],
                       GITHUB_RUN_ID="501", GITHUB_RUN_ATTEMPT="1", GITHUB_JOB="short")
    real_run = subprocess.run
    commands = [command for _, command in evidence.short_commands(actual["parents"][0], actual["head"])]
    def modeled_commands(command, *args, **kwargs):
        if command in commands:
            if command != commands[-1]: kwargs["stdout"].write(b"synthetic short command transport; no actual execution claim\n")
            return subprocess.CompletedProcess(command, 0)
        return real_run(command, *args, **kwargs)
    with patch.object(runner, "ROOT", root), patch.dict(os.environ, environment):
        if actual_short:
            path, record = flow.post_merge_tiered(root / "tmp/short-output", client=result["api"], root=root)
        else:
            with patch.object(subprocess, "run", side_effect=modeled_commands):
                path, record = flow.post_merge_tiered(root / "tmp/short-output", client=result["api"], root=root)
    result.update(short_path=path, short_record=record, source_api_calls=result["api"].calls)
    result["fixture_context"]["short_commands"] = "actual execution" if actual_short else "synthetic command transport, semantic parser evidence only"
    if record["outcome"] != "success":
        raise AssertionError("documentation short fixture failed: " + record.get("error", "unknown"))
    return result


def full_fallback_short_fixture(folder):
    """Model hosted full inputs while exercising actual full/3 reuse parser."""
    try:
        from tests.test_current_validation_evidence import bundle, reference, write
    except ModuleNotFoundError:
        from test_current_validation_evidence import bundle, reference, write
    asset_bytes = b'{"fixture": "immutable synthetic manifest, not product integrity PASS"}'
    result = documentation_api_fixture(folder, initial_files={"runtime_asset_manifest.json": asset_bytes})
    root = result["source_root"]
    coverage_path = bundle(root / "tmp/full-source")
    scope = coverage_path.parent
    record = evidence.load_json(coverage_path)
    # Match the actual current full producer's retained lock location; the
    # generic bundle helper deliberately uses a flattened parser-only layout.
    (scope / "main-full").mkdir()
    (scope / "lock.txt").rename(scope / "main-full/lock.txt")
    record["artifacts"]["lock"] = reference(scope, scope / "main-full/lock.txt")
    docs_record = ci.load(result["documentation_path"])
    identity, binding = flow._source_identity(docs_record), docs_record["ci"]
    checkout = {key: value for key, value in docs_record["checkout"].items() if not key.startswith("checkout_")}
    record.update(candidate=identity, ci=binding, checkout=checkout)
    environment = evidence.load_json(scope / "environment.json"); environment.update(candidate=identity, ci=binding, checkout=checkout)
    write(scope / "environment.json", environment); write(scope / "event.json", {"ci": binding, "checkout": checkout})
    (scope / "assets.json").write_bytes(asset_bytes)
    for name in ("environment", "event", "assets"):
        record["artifacts"][name] = reference(scope, scope / (name + ".json"))
    diff = next(item for item in record["checks"] if item["name"] == "diff")
    diff["command"] = ["git", "diff", "--check", result["candidate"]["base"] + "..." + result["candidate"]["head"]]
    (scope / "diff.log").write_text(json.dumps({"argv": diff["command"]}) + "\nexit_code=0\n", encoding="utf-8")
    diff["log"] = reference(scope, scope / "diff.log"); write(coverage_path, record)
    import shutil
    shutil.copytree(result["documentation_path"].parent, scope / "pr-flow-ci/docs")
    ci.write(scope / "pr-flow-ci/plan.json", {"schema": "pr-flow-ci-plan/1", "policy": policy.POLICY_ID,
             "route": "full", "checks_route": "full", "reason": "trusted-source-unconfigured",
             "execution_scope": docs_record["execution_scope"], "ci": docs_record["ci"], "checkout": docs_record["checkout"]})
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in scope.rglob("*"):
            if file.is_file(): archive.write(file, file.relative_to(scope).as_posix())
    archive = stream.getvalue()
    full_artifact = dict(result["artifact"], id=902, name="current-validation-evidence-500-1", digest="sha256:" + ci.digest(archive), size_in_bytes=len(archive))
    original_api = result["api"]
    class FullAPI:
        def request(self, endpoint, *, binary=False):
            if endpoint == "actions/artifacts/902/zip":
                if not binary: raise AssertionError("full artifact must be binary")
                return archive
            return original_api.request(endpoint, binary=binary)
        def pages(self, endpoint, key=None):
            if endpoint == "actions/runs/500/artifacts": return [deepcopy(full_artifact)]
            return original_api.pages(endpoint, key)
    actual = result["actual_merge"]
    commands = [command for _, command in evidence.short_commands(actual["parents"][0], actual["head"])]
    real_run = subprocess.run
    def modeled(command, *args, **kwargs):
        if command in commands:
            if command != commands[-1]: kwargs["stdout"].write(b"synthetic hosted short command transport\n")
            return subprocess.CompletedProcess(command, 0)
        return real_run(command, *args, **kwargs)
    push = dict(GITHUB_EVENT_NAME="push", GITHUB_REF="refs/heads/develop", GITHUB_SHA=actual["head"], GITHUB_RUN_ID="501", GITHUB_RUN_ATTEMPT="1", GITHUB_JOB="short")
    with patch.object(runner, "ROOT", root), patch.object(runner, "environment", return_value=record["environment"]), patch.dict(os.environ, push), patch.object(subprocess, "run", side_effect=modeled):
        path, short = flow.post_merge_tiered(root / "tmp/full-short", client=FullAPI(), root=root)
    if short["outcome"] != "success": raise AssertionError("full fallback modeled short failed: " + short.get("error", "unknown"))
    result.update(short_path=path, short_record=short, coverage_path=path.parent / short["coverage_path"],
                  documentation_path=path.parent / "pr-evidence/pr-flow-ci/docs/checks.json")
    result["fixture_context"].update(full_inputs="synthetic hosted full coverage/environment, not actual full CI",
                                     short_commands="synthetic command transport, semantic parser evidence only")
    for name in ci.FULL_STEPS:
        result["pr_ci"]["jobs"][0]["steps"][name] = "skipped" if name == "Check committed whitespace (push)" else "success"
    return result


class PrFlowEvidenceTests(unittest.TestCase):
    def test_actual_upload_paths_preserve_readonly_docs_and_fallback_payloads_on_relocation(self):
        # These fixtures model short-command transport; Git/CLI checks, ZIPs,
        # payload selection and every reader below execute normally.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for route, make in (("docs", documentation_short_fixture), ("docs_fallback_full", full_fallback_short_fixture)):
                fixture = make(root / route)
                workspace = root / (route + "-workspace")
                staged = workspace / "tmp/validation-evidence/short-synthetic"
                shutil.copytree(fixture["short_path"].parent, staged)
                (staged / "personal.bin").write_bytes(b"excluded canary")
                included = upload_payload_files(workspace, "short", "Upload validation evidence")
                self.assertNotIn(staged / "personal.bin", included)
                stream = io.BytesIO()
                with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
                    for file in sorted(included):
                        archive.write(file, file.relative_to(workspace / "tmp/validation-evidence").as_posix())
                relocated = root / (route + "-relocated")
                evidence.extract_evidence(stream.getvalue(), relocated)
                moved = relocated / "short-synthetic"
                documentation = moved / ("pr-evidence/docs/checks.json" if route == "docs" else "pr-evidence/pr-flow-ci/docs/checks.json")
                coverage = None if route == "docs" else moved / fixture["short_record"]["coverage_path"]
                def verify():
                    before = evidence_tree(moved)
                    try:
                        with patch.object(ci, "ROOT", fixture["source_root"]), forbid_evidence_writes():
                            return flow.verify_tiered_post_merge(moved / "short.json", fixture["candidate"], route, fixture["pr_ci"],
                                                                coverage_path=coverage, documentation_path=documentation)
                    finally:
                        self.assertEqual(evidence_tree(moved), before)
                self.assertEqual(verify()["source_route"], route)
                missing_paths = [documentation.parent / "tested-commit.raw"]
                if route == "docs":
                    missing_paths += [moved / "pr-evidence/transport/originals/raw/check.raw",
                                      moved / "capture-source/originals/backups/review-1.json"]
                for path in missing_paths:
                    raw = path.read_bytes(); path.unlink()
                    with self.subTest(route=route, missing=path.relative_to(moved)), self.assertRaises((ValueError, OSError)):
                        verify()
                    self.assertFalse(path.exists()); path.write_bytes(raw)

    def test_full_fallback_preserves_full_short_schema_and_requires_retained_documentation(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture = full_fallback_short_fixture(folder)
            with patch.object(ci, "ROOT", fixture["source_root"]):
                result = flow.verify_tiered_post_merge(fixture["short_path"], fixture["candidate"], "docs_fallback_full", fixture["pr_ci"],
                                                       coverage_path=fixture["coverage_path"], documentation_path=fixture["documentation_path"])
                self.assertEqual(result["schema"], evidence.CURRENT_SHORT_SCHEMA)
                self.assertEqual(result["source_route"], "docs_fallback_full")
                with self.assertRaises(ValueError):
                    flow.verify_tiered_post_merge(fixture["short_path"], fixture["candidate"], "docs_fallback_full", fixture["pr_ci"],
                                                  coverage_path=fixture["coverage_path"])
                with self.assertRaises(ValueError):
                    flow.verify_tiered_post_merge(fixture["short_path"], fixture["candidate"], "docs", fixture["pr_ci"],
                                                  documentation_path=fixture["documentation_path"])

    def test_documentation_api_zip_and_short_semantics_preserve_source_and_actual_identities(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture = documentation_short_fixture(folder)
            with patch.object(ci, "ROOT", fixture["source_root"]):
                checked = flow.verify_tiered_post_merge(fixture["short_path"], fixture["candidate"], "docs", fixture["pr_ci"],
                                                        documentation_path=fixture["documentation_path"])
            self.assertEqual(checked["candidate"], fixture["actual_merge"])
            self.assertNotEqual(checked["candidate"]["head"], fixture["pr_ci"]["tested_sha"])
            self.assertNotIn("coverage_path", checked)
            self.assertEqual(checked["source_route"], "docs")

    def test_documentation_short_rejects_raw_commit_tree_attempt_archive_and_failed_commands(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture = documentation_short_fixture(folder)
            path = fixture["short_path"]
            original = path.read_bytes()
            changes = [lambda value: value["candidate"].update(tree="f" * 40),
                       lambda value: value["candidate"]["parents"].reverse(),
                       lambda value: value["checks"][0].update(exit_code=17),
                       lambda value: value.update(documentation_sha256="f" * 64),
                       lambda value: value["ci"].update(attempt=True),
                       lambda value: value["environment"].update(python="3.13.5"),
                       lambda value: value.update(unknown_future_contract=True)]
            for change in changes:
                value = json.loads(original); change(value); path.write_text(json.dumps(value), encoding="utf-8")
                with self.subTest(change=change), patch.object(ci, "ROOT", fixture["source_root"]), self.assertRaises((ValueError, OSError, KeyError)):
                    flow.verify_tiered_post_merge(path, fixture["candidate"], "docs", fixture["pr_ci"])
            path.write_bytes(original)
            raw = path.parent / "pr-evidence/docs/tested-commit.raw"
            raw.write_bytes(raw.read_bytes() + b"tamper")
            with patch.object(ci, "ROOT", fixture["source_root"]), self.assertRaises(ValueError):
                flow.verify_tiered_post_merge(path, fixture["candidate"], "docs", fixture["pr_ci"])

    def test_current_documentation_api_rejects_wrong_repository_attempt_artifact_and_expiry(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture = documentation_api_fixture(folder)
            artifact, run = fixture["artifact"], fixture["pr_run"]
            originals = deepcopy(artifact), deepcopy(run)
            changes = [lambda: artifact.update(expired=True), lambda: artifact.update(name="current-validation-evidence-500-1"),
                       lambda: artifact["workflow_run"].update(repository_id=111),
                       lambda: artifact["workflow_run"].update(head_sha="f" * 40),
                       lambda: run.update(run_attempt=2), lambda: run.update(run_attempt=True), lambda: run.update(conclusion="failure"),
                       lambda: run["repository"].update(full_name="synthetic/wrong-repository")]
            for index, change in enumerate(changes):
                artifact.clear(); artifact.update(deepcopy(originals[0])); run.clear(); run.update(deepcopy(originals[1])); change()
                output = Path(folder) / ("rejected-" + str(index)); output.mkdir()
                with self.subTest(index=index), self.assertRaises((ValueError, OSError, KeyError)):
                    flow.fetch_tiered_pr_evidence(fixture["api"], fixture["actual_merge"], output, root=fixture["source_root"])


if __name__ == "__main__":
    unittest.main()
