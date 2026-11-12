"""Finite current-PR documentation/full artifact to actual-merge adapter.

This is not a hosted attestation service. API transport is injected in local
fixtures; production uses the existing fixed-repository GitHub client.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import subprocess
import uuid

from scripts import pr_flow_ci as ci
from scripts import pr_flow_policy as policy
from scripts import validation_evidence as evidence

DOC_SHORT_SCHEMA = "pr-flow-documentation-merge-short/1"


def _run_numbers(run):
    policy.require(all(type(run[key]) is int and run[key] > 0 for key in ("id", "run_number", "run_attempt")),
                   "source CI run/attempt requires positive integer identities")


def _artifact_identity(artifact, run, pr, actual):
    policy.require(type(artifact["id"]) is int and artifact["id"] > 0 and artifact["expired"] is False, "expired or invalid current PR artifact")
    policy.require(run["repository"]["full_name"] == evidence.REPOSITORY and type(run["repository"]["id"]) is int
                   and run["repository"]["id"] > 0, "source run repository differs")
    identity = artifact["workflow_run"]
    policy.require(type(identity["id"]) is int and identity["id"] == run["id"] and identity["head_sha"] == actual["parents"][1]
                   and identity["head_branch"] == pr["head"]["ref"], "current artifact belongs to another run/head/branch")
    policy.require(type(identity["repository_id"]) is int and identity["repository_id"] == run["repository"]["id"], "current artifact belongs to another repository")


def _source(client, candidate):
    commit = client.request("commits/" + candidate["head"])
    policy.require(commit["sha"] == candidate["head"] and [row["sha"] for row in commit["parents"]] == candidate["parents"]
                   and commit["commit"]["tree"]["sha"] == candidate["tree"], "actual GitHub merge identity differs")
    prs = client.pages("commits/" + candidate["head"] + "/pulls")
    matches = [pr for pr in prs if pr.get("merge_commit_sha") == candidate["head"] and pr.get("state") == "closed"]
    policy.require(len(matches) == 1 and type(matches[0]["number"]) is int, "actual merge needs exactly one source PR")
    pr = client.request("pulls/" + str(matches[0]["number"]))
    policy.require(type(pr["number"]) is int and pr["number"] == matches[0]["number"] and pr["number"] > 0
                   and pr["merged"] is True and pr["merge_commit_sha"] == candidate["head"] and pr["base"]["ref"] == "develop"
                   and pr["base"]["sha"] == candidate["parents"][0] and pr["head"]["sha"] == candidate["parents"][1]
                   and pr["base"]["repo"]["full_name"] == pr["head"]["repo"]["full_name"] == evidence.REPOSITORY,
                   "source PR does not identify the reviewed actual merge")
    runs = client.pages("actions/workflows/ci.yml/runs?event=pull_request&head_sha=" + candidate["parents"][1], "workflow_runs")
    runs = [run for run in runs if run["head_sha"] == candidate["parents"][1] and run["event"] == "pull_request"]
    policy.require(bool(runs), "source PR has no current CI run")
    for row in runs:
        _run_numbers(row)
    latest = max(runs, key=lambda run: (run["run_number"], run["id"]))
    policy.require(type(latest["id"]) is int and latest["id"] > 0, "invalid source run ID")
    run = client.request("actions/runs/" + str(latest["id"]))
    _run_numbers(run)
    policy.require((run["id"], run["run_attempt"]) == (latest["id"], latest["run_attempt"])
                   and type(run["run_attempt"]) is int and run["run_attempt"] > 0
                   and run["head_sha"] == candidate["parents"][1] and run["event"] == "pull_request"
                   and run["path"] == ".github/workflows/ci.yml" and run["status"] == "completed"
                   and run["conclusion"] == "success" and latest["status"] == "completed" and latest["conclusion"] == "success",
                   "latest source PR run/attempt has not succeeded")
    policy.require(run["repository"]["full_name"] == evidence.REPOSITORY and type(run["repository"]["id"]) is int
                   and run["repository"]["id"] > 0, "source run repository differs")
    return {"pr": pr, "run": run, "commit": commit, "runs": runs}


def _retain_source(output, metadata, archive):
    path = output / "pr-artifact.zip"
    path.write_bytes(archive)
    source = {"archive": evidence._reference(output, path)}
    for name, value in metadata.items():
        path = output / (name + ".json")
        evidence._write_json(path, value)
        source[name] = evidence._reference(output, path)
    return source


def _source_identity(record):
    checkout = record["checkout"]
    return {"head": checkout["checkout_sha"], "tree": checkout["checkout_tree"], "parents": checkout["checkout_parents"]}


def _verify_documentation_equivalence(path, task_candidate, actual, root, *, recheck_output=None):
    """Inspect actual equal-tree bytes while retaining the original PR logs."""
    path = Path(path)
    record = ci.load(path)
    policy.require(record.get("schema") in {ci.SCHEMA, ci.PR_SCHEMA} and record.get("policy") == policy.POLICY_ID
                   and record.get("status") == "success", "unknown or unsuccessful source documentation record")
    source = _source_identity(record)
    raw_path = (ci._artifact(path.parent, record["tested_commit"]) if record["schema"] == ci.PR_SCHEMA
                else path.parent / "tested-commit.raw")
    policy.require(ci.raw_commit_identity(raw_path.read_bytes()) == source, "source raw tested commit differs")
    policy.require(actual["tree"] == source["tree"] and actual["parents"] == source["parents"], "actual merge differs from tested documentation tree/parents")
    policy.require(ci.git(root, "rev-parse", actual["head"] + "^{tree}").decode().strip() == actual["tree"]
                   and ci.git(root, "show", "-s", "--format=%P", actual["head"]).decode().strip().split() == actual["parents"],
                   "actual local merge object differs")
    policy.require(record["execution"] == {"python": "3.13.0", "platform": "Windows", "checker_sha256": ci.digest(Path(ci.__file__).read_bytes())},
                   "source documentation checker environment differs")
    if record["schema"] == ci.SCHEMA:
        policy.fields(record, "schema policy candidate status execution candidate_original checks ci checkout", "source documentation")
        policy.require(task_candidate is not None and record["candidate"] == task_candidate, "task-bound documentation belongs to another candidate")
        policy.require(ci.load(ci._artifact(path.parent, record["candidate_original"])) == task_candidate, "source candidate original differs")
        inspected = task_candidate
    else:
        policy.fields(record, "schema policy execution_scope status execution scope_original tested_commit checks ci checkout", "source supplemental documentation")
        scope = record["execution_scope"]
        policy.require(ci.load(ci._artifact(path.parent, record["scope_original"])) == scope, "source PR scope original differs")
        # Feature/base objects are permanent ordered parents of the actual merge.
        ci._pr_scope_checkout(scope, actual["head"], root)
        if task_candidate is not None:
            policy.require((scope["repository"], scope["base"], scope["head"]) ==
                           (task_candidate["repository"], task_candidate["base"], task_candidate["head"]), "supplemental source candidate differs")
            policy.require(set(ci.document_paths(task_candidate["changed_files"])) <= set(ci.document_paths(scope["changed_files"])),
                           "source supplemental scope omits necessary documentation")
        inspected = {"baseline": scope["merge_base"], "head": scope["head"], "changed_files": ci.document_paths(scope["changed_files"])}
    policy.require(type(record["checks"]) is list and [item["name"] for item in record["checks"]] == list(ci.CHECKS), "source required documentation checks are missing")
    expected_kind, original_name = ("inspect", "candidate.json") if record["schema"] == ci.SCHEMA else ("inspect-pr", "scope.json")
    rechecks = []
    for item in record["checks"]:
        policy.fields(item, "name command exit_code stdout stderr", "source documentation check")
        expected_command = ["<python>", "scripts/pr_flow_ci.py", expected_kind,
                            "--candidate" if expected_kind == "inspect" else "--scope", original_name,
                            "--check", item["name"], "--tested-sha", source["head"]]
        policy.require(type(item["exit_code"]) is int and item["exit_code"] == 0 and item["command"] == expected_command,
                       "source documentation command/exit differs")
        observed = ci.load(ci._artifact(path.parent, item["stdout"]))
        policy.require(not ci._artifact(path.parent, item["stderr"]).read_bytes().strip(), "source documentation emitted errors")
        actual_result = ci._inspect_documentation(inspected, item["name"], root, actual)
        if recheck_output is not None:
            retained = Path(recheck_output) / (item["name"] + ".stdout.json")
            ci.write(retained, actual_result)
            rechecks.append({"name": item["name"], "stdout": evidence._reference(Path(recheck_output), retained)})
        # Only the tested-commit context is mapped after proving equal trees and
        # ordered parents. Original JSON/stdout/commands are never rewritten.
        actual_result["tested_checkout"] = source
        if item["name"] == "diff":
            for result in actual_result["results"]:
                result["command"] = [source["head"] if value == actual["head"] else value for value in result["command"]]
        policy.require(observed == actual_result, "documentation source differs from equal-tree actual merge inspection")
    if recheck_output is not None:
        ci.write(Path(recheck_output) / "recheck.json", {"schema": "pr-flow-documentation-actual-recheck/1", "source": source,
                 "actual": actual, "source_documentation_sha256": evidence.sha256_file(path), "checks": rechecks})
    return record


def _verify_documentation_source(record, metadata, actual):
    pr, run = metadata["pr"], metadata["run"]
    checkout = record["checkout"]
    source = _source_identity(record)
    evidence._ci_identity(record["ci"], "pull_request")
    policy.require(record["ci"] == {"run_id": run["id"], "attempt": run["run_attempt"], "job": "test", "event": "pull_request"}
                   and checkout["number"] == pr["number"] and checkout["head_ref"] == pr["head"]["ref"]
                   and checkout["base_ref"] == "develop" and checkout["repository"] == checkout["base_repository"] == checkout["head_repository"] == evidence.REPOSITORY
                   and (checkout["base_sha"], checkout["head_sha"]) == tuple(actual["parents"])
                   and source["parents"] == actual["parents"] and source["tree"] == actual["tree"] and source["head"] not in source["parents"],
                   "documentation evidence belongs to another source PR/run/attempt/tested integration")


def _verify_actual_recheck(pointer, short_root, documentation, actual, root):
    manifest_path = evidence._artifact(short_root, pointer)
    manifest = ci.load(manifest_path)
    policy.fields(manifest, "schema source actual source_documentation_sha256 checks", "actual documentation recheck")
    record = ci.load(documentation)
    policy.require(manifest["schema"] == "pr-flow-documentation-actual-recheck/1" and manifest["source"] == _source_identity(record)
                   and manifest["actual"] == actual and manifest["source_documentation_sha256"] == evidence.sha256_file(documentation),
                   "actual documentation recheck identity differs")
    inspected = record["candidate"] if record["schema"] == ci.SCHEMA else {
        "baseline": record["execution_scope"]["merge_base"], "head": record["execution_scope"]["head"],
        "changed_files": ci.document_paths(record["execution_scope"]["changed_files"])}
    policy.require([item["name"] for item in manifest["checks"]] == list(ci.CHECKS), "actual documentation recheck is incomplete")
    for item in manifest["checks"]:
        policy.fields(item, "name stdout", "actual documentation result")
        observed = ci.load(evidence._artifact(manifest_path.parent, item["stdout"]))
        policy.require(observed == ci._inspect_documentation(inspected, item["name"], root, actual),
                       "actual documentation recheck raw result differs")


def _verify_plan(plan, route, *, candidate=None):
    policy.require(plan.get("schema") == "pr-flow-ci-plan/1" and plan.get("policy") == policy.POLICY_ID
                   and plan.get("route") == route, "archived producer plan route/policy differs")
    if candidate is not None and "candidate" in plan:
        policy.require(plan["candidate"] == candidate, "archived plan belongs to another task/candidate")
    if route == "docs":
        policy.require(plan.get("checks_route") == "docs" and plan.get("classification_tier") == "docs"
                       and candidate is not None and plan.get("candidate") == candidate and type(plan.get("source")) is dict,
                       "documentation exemption lacks original coordinator-attested capture source")


def _capture_source(client, plan, metadata, output, root):
    from scripts.pr_flow_transport import fetch_capture
    from scripts.pr_review_gate import validate_tiered_reviews
    transfer = fetch_capture(client, plan["candidate"], {"pull_request": metadata["pr"]}, output / "capture-source", root)
    old, new = plan["source"], transfer["source"]
    for key in ("schema", "authority", "repository", "repository_id", "policy_git", "run_id", "run_attempt", "artifact_id", "artifact_name"):
        policy.require(old[key] == new[key], "source coordinator policy/run/artifact changed")
    for key in ("archive", "capture", "policy"):
        policy.require(old["artifacts"][key]["sha256"] == new["artifacts"][key]["sha256"], "source coordinator original bytes changed")
    capture = transfer["capture"]
    reviewed = validate_tiered_reviews(capture["candidate"], capture["classification"], capture["implementers"], capture["review_originals"],
                                       original_resolver=transfer["original_resolver"])
    policy.require(reviewed["code_ready"] and reviewed["classification"]["confirmed"] and reviewed["classification"]["tier"] == "docs",
                   "source coordinator capture no longer proves required documentation review")
    return transfer["source"]


def _retained_capture_source(plan, metadata, source, short_root, original_root, root):
    from scripts.pr_flow_transport import verify_retained_capture
    from scripts.pr_review_gate import validate_tiered_reviews
    candidate = plan["candidate"]
    for value, parent in ((plan["source"], original_root / "transport"), (source["coordinator"], short_root / "capture-source")):
        transfer = verify_retained_capture(value, {"pull_request": metadata["pr"]}, parent, root, candidate=candidate)
        capture = transfer["capture"]
        reviewed = validate_tiered_reviews(candidate, capture["classification"], capture["implementers"], capture["review_originals"],
                                           original_resolver=transfer["original_resolver"])
        policy.require(reviewed["code_ready"] and reviewed["classification"]["confirmed"] and reviewed["classification"]["tier"] == "docs",
                       "retained source capture lacks required documentation review")
    old, new = plan["source"], source["coordinator"]
    for key in ("policy_git", "run_id", "run_attempt", "artifact_id", "artifact_name", "authority", "repository", "repository_id"):
        policy.require(old[key] == new[key], "short source changed coordinator authority/run/artifact")
    for key in ("archive", "capture", "policy"):
        policy.require(old["artifacts"][key]["sha256"] == new["artifacts"][key]["sha256"], "short source changed coordinator original bytes")


def fetch_tiered_pr_evidence(client, actual, output, *, root=evidence.ROOT):
    evidence._identity(actual)
    metadata = _source(client, actual)
    run = metadata["run"]
    artifacts = client.pages("actions/runs/" + str(run["id"]) + "/artifacts", "artifacts")
    full_name = f"current-validation-evidence-{run['id']}-{run['run_attempt']}"
    docs_name = f"documentation-validation-evidence-{run['id']}-{run['run_attempt']}"
    full = [row for row in artifacts if row.get("name") == full_name]
    docs = [row for row in artifacts if row.get("name") == docs_name]
    policy.require(len(full) <= 1 and len(docs) <= 1 and bool(full or docs), "wrong or duplicate current PR evidence artifact")
    route, artifact = ("full", full[0]) if full else ("docs", docs[0])
    _artifact_identity(artifact, run, metadata["pr"], actual)
    archive = client.request("actions/artifacts/" + str(artifact["id"]) + "/zip", binary=True)
    policy.require(type(archive) is bytes and artifact["digest"] == "sha256:" + ci.digest(archive), "current artifact digest differs")
    metadata["artifact"] = artifact
    source = _retain_source(output, metadata, archive)
    extracted = output / "pr-evidence"
    evidence.extract_evidence(archive, extracted)
    if route == "full":
        original = extracted / "current-coverage.json"
        record = evidence.verify_current_coverage(original)
        policy.require(record["candidate"]["parents"] == actual["parents"] and record["candidate"]["tree"] == actual["tree"]
                       and record["ci"] == {"run_id": run["id"], "attempt": run["run_attempt"], "job": "test", "event": "pull_request"}
                       and record["checkout"]["number"] == metadata["pr"]["number"], "full source candidate/run/PR differs")
        plan_path, docs_path = extracted / "pr-flow-ci/plan.json", extracted / "pr-flow-ci/docs/checks.json"
    else:
        original, docs_path, plan_path = extracted / "docs/checks.json", extracted / "docs/checks.json", extracted / "plan.json"
        record = ci.load(original)
    plan = ci.load(plan_path)
    task_candidate = plan.get("candidate")
    _verify_plan(plan, route, candidate=task_candidate)
    document_record = None
    if docs_path.is_file():
        document_record = _verify_documentation_equivalence(docs_path, task_candidate, actual, root,
                                                            recheck_output=output / "actual-documentation")
        _verify_documentation_source(document_record, metadata, actual)
        source["actual_documentation"] = evidence._reference(output, output / "actual-documentation/recheck.json")
    else:
        policy.require(route != "docs", "documentation source original is missing")
    if route == "full":
        if task_candidate is None:
            raw_files = ci.git(root, "diff", "--name-only", "-z", actual["parents"][0] + "..." + actual["parents"][1]).decode("utf-8").rstrip("\0")
            scope_candidate = {"changed_files": raw_files.split("\0") if raw_files else []}
        else:
            scope_candidate = task_candidate
        ci.verify_full_plan_scope(plan, record, scope_candidate, document_record)
    if route == "docs":
        source["coordinator"] = _capture_source(client, plan, metadata, output, root)
    # A newer attempt during download invalidates this source, regardless of ZIP integrity.
    after = _source(client, actual)
    policy.require(after == {key: metadata[key] for key in after}, "source PR CI changed during retrieval")
    return {"route": route, "path": original, "record": record, "documentation_path": docs_path if docs_path.is_file() else None,
            "source": source, "plan": plan}


def post_merge_tiered(evidence_root, *, client=None, root=evidence.ROOT):
    """Execute existing short commands; never run or synthesize full coverage."""
    runner = evidence.runner_module()
    output = Path(evidence_root) / ("short-" + str(uuid.uuid4()))
    output.mkdir(parents=True, exist_ok=False)
    started = datetime.now(timezone.utc).isoformat()
    result = {"schema": DOC_SHORT_SCHEMA, "policy": policy.POLICY_ID, "started_at": started,
              "outcome": "failed", "checks": []}
    evidence._write_json(output / "started.json", {"started_at": started})
    try:
        import os
        policy.require(os.environ.get("GITHUB_EVENT_NAME") == "push" and os.environ.get("GITHUB_REF") == "refs/heads/develop", "short entrypoint requires actual develop push")
        actual = runner.snapshot()
        evidence._identity(actual)
        policy.require(actual["head"] == os.environ.get("GITHUB_SHA"), "actual pushed checkout differs")
        result.update(candidate=actual, environment=runner.environment(), ci={"run_id": int(os.environ["GITHUB_RUN_ID"]),
                      "attempt": int(os.environ["GITHUB_RUN_ATTEMPT"]), "job": os.environ["GITHUB_JOB"], "event": "push"})
        client = client or evidence.GitHub(os.environ.get("GITHUB_TOKEN"))
        fetched = fetch_tiered_pr_evidence(client, actual, output, root=root)
        if fetched["route"] == "docs":
            result.update(source_route="docs", source=fetched["source"], documentation_sha256=evidence.sha256_file(fetched["path"]),
                          documentation_path=fetched["path"].relative_to(output).as_posix())
        else:
            # Preserve the existing /3 record and full reuse semantics.
            result.update(schema=evidence.CURRENT_SHORT_SCHEMA, policy=evidence.CURRENT_POLICY, window_validation=evidence.WINDOW_NOTICE,
                          source=fetched["source"], coverage_sha256=evidence.sha256_file(fetched["path"]),
                          coverage_path=fetched["path"].relative_to(output).as_posix())
            evidence._environment_equivalent(fetched["record"]["environment"], result["environment"])
            blob = ci.git(root, "cat-file", "--filters", actual["tree"] + ":runtime_asset_manifest.json")
            result["assets_sha256"] = evidence.sha256_file(Path(root) / "runtime_asset_manifest.json")
            result["pr_assets_sha256"] = ci.digest(blob)
            policy.require(result["assets_sha256"] == result["pr_assets_sha256"], "runtime manifest bytes differ from actual tree")
            for name, raw in (("assets", (Path(root) / "runtime_asset_manifest.json").read_bytes()), ("pr_assets", blob)):
                retained = output / (name + ".json")
                retained.write_bytes(raw)
                result["source"][name] = evidence._reference(output, retained)
        policy.require(result["environment"]["python"] == "3.13.0" and result["environment"]["platform"] == "Windows", "unsupported short environment")
        for name, command in evidence.short_commands(actual["parents"][0], actual["head"]):
            path = output / (name + ".log")
            with path.open("xb") as stream:
                try:
                    code = subprocess.run(command, cwd=root, stdout=stream, stderr=subprocess.STDOUT, timeout=300, check=False).returncode
                except subprocess.TimeoutExpired:
                    code = 124
            result["checks"].append({"name": name, "command": command, "exit_code": code, "log": evidence._reference(output, path)})
            policy.require(code == 0 and (name != "clean" or not path.read_bytes().strip()), "short check failed: " + name)
        policy.require(runner.snapshot() == actual and runner.environment() == result["environment"], "source/environment changed during short validation")
        result["outcome"] = "success"
    except Exception as error:
        result.update(error=type(error).__name__ + ": " + str(error))
    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    path = output / "short.json"
    if result["outcome"] == "success":
        try:
            if result["schema"] == DOC_SHORT_SCHEMA:
                _verify_docs_short(path, result, root=root)
            else:
                evidence.verify_reuse(output / result["coverage_path"], path, _record=result, current_only=True)
        except Exception as error:
            result.update(outcome="failed", error=type(error).__name__ + ": " + str(error))
    evidence._write_json(path, result)
    return path, result


def post_merge_current(evidence_root):
    return post_merge_tiered(evidence_root)


def _verify_docs_short(path, record, *, root=evidence.ROOT, candidate=None):
    path = Path(path)
    policy.fields(record, "schema policy started_at outcome checks candidate environment ci source_route source documentation_sha256 documentation_path finished_at",
                  "documentation merge short")
    policy.require(record.get("schema") == DOC_SHORT_SCHEMA and record.get("policy") == policy.POLICY_ID
                   and record.get("source_route") == "docs" and record.get("outcome") == "success", "documentation short validation did not succeed")
    actual = record["candidate"]
    evidence._identity(actual); evidence._ci_identity(record["ci"], "push")
    policy.require(record["environment"]["python"] == "3.13.0" and record["environment"]["platform"] == "Windows", "unsupported documentation short environment")
    policy.require(record["ci"]["job"] == "short" and record["started_at"] < record["finished_at"], "invalid documentation short execution identity")
    source = record["source"]
    policy.fields(source, "archive pr run artifact commit runs actual_documentation coordinator", "documentation short source")
    metadata = {name: evidence.load_json(evidence._artifact(path.parent, source[name])) for name in ("pr", "run", "artifact", "commit", "runs")}
    _run_numbers(metadata["run"])
    _artifact_identity(metadata["artifact"], metadata["run"], metadata["pr"], actual)
    original = evidence.local_path(path.parent, record["documentation_path"])
    policy.require(evidence.sha256_file(original) == record["documentation_sha256"], "short refers to different documentation bytes")
    archive = evidence._artifact(path.parent, source["archive"])
    policy.require(metadata["artifact"]["name"] == f"documentation-validation-evidence-{metadata['run']['id']}-{metadata['run']['run_attempt']}"
                   and metadata["artifact"]["expired"] is False and metadata["artifact"]["digest"] == "sha256:" + evidence.sha256_file(archive), "short documentation source artifact differs")
    evidence._archive_matches(archive, original.parent.parent)
    plan = ci.load(original.parent.parent / "plan.json")
    _verify_plan(plan, "docs", candidate=candidate or plan.get("candidate"))
    _retained_capture_source(plan, metadata, source, path.parent, original.parent.parent, root)
    document_record = _verify_documentation_equivalence(original, candidate or plan["candidate"], actual, root)
    _verify_documentation_source(document_record, metadata, actual)
    _verify_actual_recheck(source["actual_documentation"], path.parent, original, actual, root)
    # Reuse all existing source PR/run/actual merge/latest attempt checks without
    # requiring a fabricated validation-coverage document.
    pr, run, commit = metadata["pr"], metadata["run"], metadata["commit"]
    policy.require(pr["merged"] is True and pr["merge_commit_sha"] == actual["head"] and pr["base"]["ref"] == "develop"
                   and pr["base"]["sha"] == actual["parents"][0] and pr["head"]["sha"] == actual["parents"][1]
                   and pr["base"]["repo"]["full_name"] == pr["head"]["repo"]["full_name"] == evidence.REPOSITORY,
                   "short source PR differs from actual merge")
    policy.require(commit["sha"] == actual["head"] and [row["sha"] for row in commit["parents"]] == actual["parents"]
                   and commit["commit"]["tree"]["sha"] == actual["tree"], "short source actual commit differs")
    runs = [row for row in metadata["runs"] if row["event"] == "pull_request" and row["head_sha"] == actual["parents"][1]]
    for row in runs:
        _run_numbers(row)
    latest = max(runs, key=lambda row: (row["run_number"], row["id"]))
    policy.require(run["path"] == ".github/workflows/ci.yml" and run["event"] == "pull_request" and run["head_sha"] == actual["parents"][1]
                   and run["status"] == "completed" and run["conclusion"] == "success"
                   and (latest["id"], latest["run_attempt"], latest["status"], latest["conclusion"]) ==
                   (run["id"], run["run_attempt"], "completed", "success"), "short source PR run is stale/failed")
    expected = evidence.short_commands(actual["parents"][0], actual["head"])
    policy.require([item["name"] for item in record["checks"]] == [name for name, _ in expected], "missing/duplicate documentation short check")
    for item, (_, command) in zip(record["checks"], expected):
        policy.fields(item, "name command exit_code log", "documentation short command")
        policy.require(type(item["exit_code"]) is int and item["exit_code"] == 0
                       and evidence._canonical_command(item["command"]) == evidence._canonical_command(command), "short command failed or changed")
        log = evidence._artifact(path.parent, item["log"])
        policy.require(item["name"] != "clean" or not log.read_bytes().strip(), "short source working tree was dirty")
    return record


def verify_tiered_post_merge(path, candidate, checks_route, pr_ci, *, coverage_path=None, documentation_path=None):
    policy.require(checks_route in {"full", "docs", "docs_fallback_full"}, "unknown postmerge checks route")
    path = Path(path)
    if checks_route == "docs":
        record = _verify_docs_short(path, evidence.load_json(path), candidate=candidate, root=ci.ROOT)
        policy.require(record["source_route"] == "docs", "short source route differs")
    else:
        policy.require(coverage_path is not None, "missing original full source coverage")
        record = evidence.verify_reuse(coverage_path, path, current_only=True)
        plan = ci.load(Path(coverage_path).parent / "pr-flow-ci/plan.json")
        _verify_plan(plan, "full", candidate=candidate)
        coverage = evidence.verify_current_coverage(coverage_path)
        policy.require(coverage["candidate"] == {"head": pr_ci["tested_sha"], "tree": pr_ci["tree"], "parents": pr_ci["parents"]}
                       and coverage["ci"] == {"run_id": pr_ci["run_id"], "attempt": pr_ci["attempt"], "job": "test", "event": "pull_request"},
                       "full short source PR CI differs from reviewed integration")
        ci.verify_full_plan_scope(plan, coverage, candidate, ci.load(documentation_path) if documentation_path is not None else None)
        if checks_route == "docs_fallback_full":
            policy.require(not candidate["risks"] and bool(ci.document_paths(candidate["changed_files"]))
                           and len(ci.document_paths(candidate["changed_files"])) == len(candidate["changed_files"]),
                           "postmerge full fallback cannot replace executable/core full validation")
        if checks_route == "docs_fallback_full" or ci.document_paths(candidate["changed_files"]):
            policy.require(documentation_path is not None, "full source lacks necessary documentation checks")
            retained = Path(coverage_path).parent / "pr-flow-ci/docs/checks.json"
            policy.require(retained.is_file() and retained.read_bytes() == Path(documentation_path).read_bytes(), "full short does not retain the checked documentation original")
            _verify_documentation_equivalence(retained, candidate, record["candidate"], ci.ROOT)
            _verify_actual_recheck(record["source"]["actual_documentation"], path.parent, retained, record["candidate"], ci.ROOT)
    policy.require(record["candidate"]["parents"] == [candidate["base"], candidate["head"]]
                   and record["candidate"]["tree"] == pr_ci["tree"], "short actual merge does not match reviewed PR integration")
    if checks_route == "docs":
        original = ci.load(evidence.local_path(path.parent, record["documentation_path"]))
        policy.require(original["ci"] == {"run_id": pr_ci["run_id"], "attempt": pr_ci["attempt"], "job": "test", "event": "pull_request"}
                       and _source_identity(original)["head"] == pr_ci["tested_sha"], "documentation short source PR CI differs")
    return {**record, "source_route": checks_route}
