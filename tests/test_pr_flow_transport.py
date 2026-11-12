"""Synthetic API-shaped local fixtures; no hosted attestation or network calls."""
from copy import deepcopy
from contextlib import contextmanager
import builtins
import io
import json
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import warnings
import zipfile

from scripts import pr_flow_policy as policy
from scripts import pr_flow_transport as transport
from scripts.pr_review_gate import validate_tiered_reviews


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=3) + " \n").encode("utf-8")


def zip_bytes(members):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, raw in members.items():
            archive.writestr(name, raw)
    return stream.getvalue()


def evidence_tree(root):
    """Record paths, kinds and exact file bytes, including empty directories."""
    return {path.relative_to(root).as_posix(): ("directory", None) if path.is_dir() else
            ("file", transport.digest(path.read_bytes())) for path in root.rglob("*")}


@contextmanager
def forbid_evidence_writes():
    """Tripwires retain genuine reads/validators, rejecting any write attempt."""
    path_open, builtin_open = Path.open, builtins.open
    def guarded_path_open(path, mode="r", *args, **kwargs):
        if any(flag in mode for flag in "wax+"): raise AssertionError("Path.open attempted an evidence write")
        return path_open(path, mode, *args, **kwargs)
    def guarded_open(file, mode="r", *args, **kwargs):
        if any(flag in mode for flag in "wax+"): raise AssertionError("open attempted an evidence write")
        return builtin_open(file, mode, *args, **kwargs)
    with patch.object(Path, "mkdir", side_effect=AssertionError("mkdir attempted during readonly verification")), \
         patch.object(Path, "open", guarded_path_open), patch.object(builtins, "open", guarded_open):
        yield


class SyntheticAPI:
    """Finite fixture responses for actual adapter calls, not checker stubs."""
    def __init__(self, fixture):
        self.fixture = fixture
        self.calls = []
        self.run_reads = self.inventory_reads = 0

    def request(self, endpoint, *, binary=False):
        self.calls.append(("request", endpoint, binary))
        f = self.fixture
        if endpoint == "": value = f.repository
        elif endpoint == "pulls/123": value = f.current_pr
        elif endpoint == "actions/runs/700":
            self.run_reads += 1
            value = f.run if self.run_reads == 1 else f.after_run
        elif endpoint == "collaborators/synthetic-coordinator/permission": value = f.permission
        elif endpoint == "actions/artifacts/900/zip":
            if not binary: raise AssertionError("archive request must be binary")
            return f.archive
        else: raise AssertionError("unexpected fixed API endpoint: " + endpoint)
        if binary: raise AssertionError("JSON response requested as binary")
        return deepcopy(value)

    def pages(self, endpoint, key=None):
        self.calls.append(("pages", endpoint, key))
        f = self.fixture
        if endpoint == f.runs_endpoint and key == "workflow_runs":
            self.inventory_reads += 1
            return deepcopy(f.runs if self.inventory_reads == 1 else f.after_runs)
        if endpoint == "actions/runs/700/artifacts" and key == "artifacts":
            return deepcopy(f.artifacts)
        raise AssertionError("unexpected fixed paged API endpoint: " + endpoint)


class SyntheticFixture:
    """All IDs, authorization and reports below are explicitly test-only."""
    def __init__(self, root, *, configured=True, initial_files=None):
        self.root = root
        root.mkdir()
        self.source = dict(schema=transport.SOURCE_SCHEMA, repository=transport.REPOSITORY,
                           repository_id=991001, workflow_id=881001,
                           workflow_path=".github/workflows/synthetic-review-source.yml", base_ref="develop",
                           allowed_actor_ids=[771001], artifact_prefix="synthetic-review",
                           authority="review-capture-coordinator")
        def git(*args, input=None):
            return subprocess.run(["git", *args], cwd=root, input=input, capture_output=True, check=True).stdout.decode().strip()
        self.git = git
        git("init", "-b", "synthetic"); git("config", "user.name", "Synthetic fixture")
        git("config", "user.email", "synthetic@example.invalid")
        for name, raw in (initial_files or {}).items():
            transport._safe_path(name)
            if type(raw) is not bytes: raise TypeError("synthetic initial file must contain bytes")
            path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
        (root / "guide.md").write_bytes(b"# Guide\n")
        if configured:
            path = root / transport.SOURCE_PATH
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                if transport._json(path.read_bytes()) != self.source:
                    raise ValueError("existing synthetic source policy differs from fixture-owned policy")
            else:
                path.write_bytes(json_bytes(self.source))
        git("add", "."); git("commit", "-m", "synthetic base policy")
        baseline = git("rev-parse", "HEAD")
        (root / "guide.md").write_bytes(b"# Guide\n\nSynthetic clarification.\n")
        git("add", "guide.md"); git("commit", "-m", "synthetic docs candidate")
        head, tree = git("rev-parse", "HEAD"), git("rev-parse", "HEAD^{tree}")
        merge = git("commit-tree", tree, "-p", baseline, "-p", head, input=b"synthetic integration\n")
        git("update-ref", "refs/heads/synthetic", merge)
        self.candidate = dict(task_id="synthetic-transport", repository=transport.REPOSITORY, baseline=baseline,
                              base=baseline, merge_base=baseline, head=head, tree=tree,
                              scope="synthetic complete task diff", changed_files=["guide.md"], risks=[])
        repo = dict(id=self.source["repository_id"], full_name=transport.REPOSITORY)
        self.repository = deepcopy(repo)
        self.event = dict(pull_request=dict(number=123, base=dict(sha=baseline, ref="develop", repo=deepcopy(repo)),
                                            head=dict(sha=head, ref="synthetic/docs", repo=deepcopy(repo))))
        self.current_pr = dict(state="open", **deepcopy(self.event["pull_request"]))
        self.environment = dict(GITHUB_EVENT_NAME="pull_request", GITHUB_REF="refs/pull/123/merge", GITHUB_SHA=merge,
                                GITHUB_REPOSITORY=transport.REPOSITORY, GITHUB_RUN_ID="500", GITHUB_RUN_ATTEMPT="1", GITHUB_JOB="test")
        actor = dict(id=771001, login="synthetic-coordinator")
        self.run = dict(id=700, run_number=7, run_attempt=2, workflow_id=self.source["workflow_id"],
                        path=self.source["workflow_path"], event="workflow_dispatch", head_sha=baseline, head_branch="develop",
                        repository=deepcopy(repo), actor=actor, triggering_actor=deepcopy(actor), status="completed", conclusion="success")
        self.runs = [deepcopy(self.run)]
        self.after_run, self.after_runs = deepcopy(self.run), deepcopy(self.runs)
        self.permission = dict(permission="write", role_name="maintain", user=deepcopy(actor))
        self.runs_endpoint = f"actions/workflows/{self.source['workflow_id']}/runs?event=workflow_dispatch&head_sha={baseline}"
        self.attachment_rows, self.members = [], {}
        def pointer(ref, name, raw, *, backup=True):
            original = str((root / "unavailable-author-machine" / name).absolute())
            attachment = dict(report_ref=ref, source_file=original, sha256=transport.digest(raw), path="raw/" + name)
            self.attachment_rows.append(attachment); self.members[attachment["path"]] = raw
            backups = []
            if backup:
                other = str((root / "unavailable-backup-machine" / name).absolute())
                backup_attachment = dict(attachment, source_file=other, path="backups/" + name)
                self.attachment_rows.append(backup_attachment); self.members[backup_attachment["path"]] = raw
                backups.append(dict(source_file=other, sha256=attachment["sha256"]))
            return dict(report_ref=ref, source_file=original, sha256=attachment["sha256"], backups=backups)
        self.check = pointer("synthetic://check", "check.raw", b"synthetic independent raw check\r\n")
        self.resolution = pointer("synthetic://resolution", "resolution.raw", b"synthetic captured missing evidence supplied\n")
        first = dict(schema="pr-flow-code-review/1", policy=policy.POLICY_ID, report_ref="synthetic://review/1",
                     candidate=deepcopy(self.candidate), reviewer="synthetic-independent-reviewer", session="synthetic-independent-session",
                     independent=True, confirmed_tier="docs", mode="full",
                     reviewed_range=dict(from_head=baseline, to_head=head), previous_report_ref=None,
                     full_review_ref="synthetic://review/1", delta_from=None, impact_known=True, architecture_changed=False,
                     integration_reviewed=True, impact_summary="Synthetic complete diff, consumers and integration reviewed.",
                     review_checks=[], verdict="BLOCKED", findings=[dict(id="E1", kind="evidence", description="synthetic missing check")], resolutions=[])
        second = deepcopy(first)
        second.update(report_ref="synthetic://review/2", previous_report_ref=first["report_ref"], full_review_ref="synthetic://review/2",
                      verdict="PASS", findings=[], review_checks=[self.check],
                      resolutions=[dict(finding_id="E1", report_ref=first["report_ref"], evidence=self.resolution)])
        self.reports = [first, second]
        originals = [pointer(report["report_ref"], f"review-{index}.json", json_bytes(report)) for index, report in enumerate(self.reports)]
        self.capture = dict(schema="pr-flow-ci-capture/1", policy=policy.POLICY_ID,
                            coordinator=dict(actor="synthetic-coordinator", source_refs=["synthetic://independent-capture"],
                                             authorization_ref="synthetic://task-authorization", independent_sources_checked=True),
                            candidate=deepcopy(self.candidate), classification=dict(tier="docs", author="synthetic-implementer",
                                             rationale="Synthetic known Markdown consumers", known_consumers=["Markdown renderer"], impact_known=True),
                            implementers=["synthetic-implementer"], review_originals=originals)
        self.members["capture.json"] = json_bytes(self.capture)
        self.manifest = dict(schema=transport.ARTIFACT_SCHEMA, candidate=deepcopy(self.candidate),
                             capture_sha256=transport.digest(self.members["capture.json"]), attachments=self.attachment_rows)
        self.artifacts = [dict(id=900, name=f"synthetic-review-123-{head}-700-2", expired=False,
                               workflow_run=dict(id=700, repository_id=991001, head_sha=baseline, head_branch="develop"))]
        self.repack()

    def repack(self):
        self.members["manifest.json"] = json_bytes(self.manifest)
        self.archive = zip_bytes(self.members)
        self.artifacts[0].update(digest="sha256:" + transport.digest(self.archive), size_in_bytes=len(self.archive))


class PrFlowTransportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.f = SyntheticFixture(self.folder / "repo")
        self.output_number = 0

    def fetch(self, fixture=None, candidate=True, event=None):
        f = fixture or self.f
        self.output_number += 1
        self.client = SyntheticAPI(f)
        output = self.folder / ("output-" + str(self.output_number))
        return transport.fetch_capture(self.client, f.candidate if candidate is True else candidate,
                                       event or f.event, output, f.root)

    def test_exact_base_policy_ignores_working_tree_and_missing_means_unconfigured(self):
        path = self.f.root / transport.SOURCE_PATH
        path.write_bytes(b'{"authority":"author-forged"}')
        self.assertEqual(transport.load_source_policy(self.f.event, self.f.root), self.f.source)
        other = SyntheticFixture(self.folder / "unconfigured", configured=False)
        self.assertIsNone(transport.load_source_policy(other.event, other.root))
        with self.assertRaisesRegex(ValueError, "unconfigured"): self.fetch(other)

    def test_initial_files_can_include_github_parent_and_matching_policy_without_overwrite(self):
        workflow = b"name: existing synthetic workflow\n"
        original_policy = json.dumps(self.f.source, separators=(",", ":")).encode("utf-8")
        other = SyntheticFixture(self.folder / "github-parent", initial_files={
            ".github/workflows/existing.yml": workflow, transport.SOURCE_PATH: original_policy})
        self.assertEqual((other.root / ".github/workflows/existing.yml").read_bytes(), workflow)
        self.assertEqual((other.root / transport.SOURCE_PATH).read_bytes(), original_policy)
        self.assertEqual(transport.load_source_policy(other.event, other.root), other.source)
        self.assertEqual(self.fetch(other)["capture"], other.capture)
        self.assertEqual(other.git("diff", "--name-only", other.candidate["baseline"], other.candidate["head"]), "guide.md")

    def test_initial_files_cannot_silently_replace_a_different_source_policy(self):
        original_policy = b'{"schema":"synthetic-other-source-policy"}\n'
        root = self.folder / "different-policy"
        with self.assertRaisesRegex(ValueError, "differs from fixture-owned"):
            SyntheticFixture(root, initial_files={transport.SOURCE_PATH: original_policy})
        self.assertEqual((root / transport.SOURCE_PATH).read_bytes(), original_policy)

    def test_closed_source_policy_rejects_unknown_fields_types_and_actor_ids(self):
        cases = [dict(extra=True), dict(repository="wrong/repo"), dict(repository_id=True), dict(workflow_id=0),
                 dict(allowed_actor_ids=[]), dict(allowed_actor_ids=[True]), dict(allowed_actor_ids=[1, 1]),
                 dict(workflow_path="../source.yml"), dict(base_ref="main"), dict(authority="author"), dict(artifact_prefix="../bad")]
        for change in cases:
            with self.subTest(change=change), self.assertRaises(ValueError):
                transport._source_policy(json_bytes(dict(self.f.source, **change)), self.f.event)
        with self.assertRaises(ValueError): transport._json(b'{"schema":1,"schema":2}')
        with self.assertRaises(ValueError): transport._json(b'{"value":NaN}')

    def test_real_adapter_zip_resolver_shared_validator_and_git_bound_plan(self):
        value = self.fetch(candidate=None)
        self.assertEqual(value["capture"], self.f.capture)
        resolver = value["original_resolver"]
        reviewed = validate_tiered_reviews(self.f.candidate, self.f.capture["classification"], self.f.capture["implementers"],
                                           self.f.capture["review_originals"], original_resolver=resolver)
        self.assertTrue(reviewed["code_ready"])
        self.assertEqual(reviewed["unresolved_findings"], [])
        from scripts import pr_flow_ci as ci
        plan = ci.validated_documentation_plan(value["capture"], self.f.event, self.f.environment, self.f.root,
                                               original_resolver=resolver)
        self.assertEqual(plan["route"], "docs")
        output = self.folder / "output-1"
        self.assertEqual((output / "artifact.zip").read_bytes(), self.f.archive)
        self.assertEqual((output / "originals/capture.json").read_bytes(), self.f.members["capture.json"])
        self.assertEqual((output / "source-policy.json").read_bytes(), json_bytes(self.f.source))
        for row in self.f.attachment_rows:
            self.assertFalse(Path(row["source_file"]).exists())
            path = resolver(row["source_file"], row["sha256"], row["report_ref"])
            self.assertEqual(path.read_bytes(), self.f.members[row["path"]])
        self.assertEqual(value["source"]["run_attempt"], 2)
        self.assertIn("permission-actor", value["source"]["artifacts"])
        self.assertTrue(all(not call[1].startswith("http") for call in self.client.calls))

    def test_same_bytes_reentry_is_idempotent_and_no_pointer_is_rewritten(self):
        first = self.fetch()
        output = self.folder / "output-1"
        second = transport.fetch_capture(SyntheticAPI(self.f), self.f.candidate, self.f.event, output, self.f.root)
        self.assertEqual(first["capture"], second["capture"])
        self.assertEqual(first["source"], second["source"])
        self.assertEqual(second["capture"], self.f.capture)

    def test_actual_merged_source_pr_requires_api_merged_and_exact_merge_sha(self):
        merge = self.f.environment["GITHUB_SHA"]
        self.f.event["pull_request"].update(merged=True, merge_commit_sha=merge)
        self.f.current_pr.update(state="closed", merged=True, merge_commit_sha=merge)
        self.assertEqual(self.fetch()["capture"], self.f.capture)
        self.f.current_pr["merge_commit_sha"] = "f" * 40
        with self.assertRaisesRegex(ValueError, "merge SHA differs"): self.fetch()
        self.f.current_pr.update(merge_commit_sha=merge, merged=False)
        with self.assertRaises(ValueError): self.fetch()

    def test_retained_source_verification_relocates_originals_and_labels_historical_context(self):
        value = self.fetch()
        relocated = self.folder / "retained-relocated"
        shutil.copytree(self.folder / "output-1", relocated)
        # A caller independently checks the current actual merge. The retained
        # open PR snapshot remains the original historical retrieval context.
        event = deepcopy(self.f.event)
        event["pull_request"].update(merged=True, merge_commit_sha=self.f.environment["GITHUB_SHA"])
        result = transport.verify_retained_capture(value["source"], event, relocated, self.f.root, candidate=self.f.candidate)
        self.assertEqual(result["capture"], value["capture"])
        self.assertEqual(result["source"], value["source"])
        self.assertEqual(result["verification_context"], "retained-source-integrity-only")
        self.assertEqual(json.loads((relocated / "pr.json").read_bytes())["state"], "open")
        for pointer in (*self.f.capture["review_originals"], self.f.check, self.f.resolution):
            self.assertEqual(policy.verify_original(pointer, resolver=result["original_resolver"]),
                             policy.verify_original(pointer, resolver=value["original_resolver"]))
        row = self.f.attachment_rows[0]
        path = result["original_resolver"](row["source_file"], row["sha256"], row["report_ref"])
        self.assertTrue(path.is_relative_to(relocated))

    def test_retained_verification_never_writes_or_repairs_any_missing_file_or_directory(self):
        value = self.fetch()
        output = self.folder / "output-1"
        def verify():
            before = evidence_tree(output)
            try:
                with forbid_evidence_writes():
                    return transport.verify_retained_capture(value["source"], self.f.event, output, self.f.root, candidate=self.f.candidate)
            finally:
                self.assertEqual(evidence_tree(output), before)
        self.assertEqual(verify()["capture"], self.f.capture)
        # Every metadata file and every canonical/check/resolution/backup is
        # required on disk, even though the trusted ZIP still has its bytes.
        for path in sorted(output.rglob("*")):
            if not path.is_file(): continue
            raw = path.read_bytes(); path.unlink()
            with self.subTest(missing=path.relative_to(output)), self.assertRaises(ValueError): verify()
            self.assertFalse(path.exists())
            path.write_bytes(raw)
        originals = output / "originals"
        shutil.rmtree(originals)
        with self.assertRaises(ValueError): verify()
        self.assertFalse(originals.exists())
        # Only an explicit fresh producer call may materialize those originals.
        restored = transport.fetch_capture(SyntheticAPI(self.f), self.f.candidate, self.f.event, output, self.f.root)
        self.assertEqual(restored["capture"], self.f.capture)
        self.assertTrue(originals.is_dir())

    def test_retained_forged_or_tampered_evidence_is_rejected_without_writes(self):
        value = self.fetch()
        output = self.folder / "output-1"
        for name in ("source.json", "originals/raw/check.raw", "originals/backups/review-0.json"):
            path = output / name; raw = path.read_bytes(); path.write_bytes(raw + b"tamper")
            before = evidence_tree(output)
            with self.subTest(name=name), forbid_evidence_writes(), self.assertRaises(ValueError):
                transport.verify_retained_capture(value["source"], self.f.event, output, self.f.root)
            self.assertEqual(evidence_tree(output), before)
            path.write_bytes(raw)
        forged = deepcopy(value["source"]); forged["artifact_name"] = "forged"
        before = evidence_tree(output)
        with forbid_evidence_writes(), self.assertRaises(ValueError):
            transport.verify_retained_capture(forged, self.f.event, output, self.f.root)
        self.assertEqual(evidence_tree(output), before)

    def test_public_gate_and_lifecycle_do_not_repair_missing_transport_originals(self):
        from test_pr_flow_policy import TieredReviewPolicyTests
        from scripts import pr_flow_ci as ci
        from scripts import pr_review_gate as gate
        helper = TieredReviewPolicyTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        source, state = helper.documentation_state()
        state["lifecycle"] = dict(schema=gate.TIERED_LIFECYCLE_SCHEMA, checks_route="docs", premerge_recheck=None,
                                  merge=None, push_ci=None, short_validation=None)
        helper.premerge_recheck(state)
        evidence = source / "tmp/pr-flow-ci"
        members = transport._archive((evidence / "transport/artifact.zip").read_bytes())
        manifest = json.loads(members["manifest.json"])
        cache = self.folder / "independent-originals"; cache.mkdir()
        mapping = {}
        for index, row in enumerate(manifest["attachments"]):
            path = cache / str(index); path.write_bytes(members[row["path"]])
            mapping[row["source_file"], row["sha256"], row["report_ref"]] = path
        def independent_resolver(source_file, sha256, report_ref):
            return mapping[source_file, sha256, report_ref]
        def assess(ready):
            before = evidence_tree(evidence)
            with patch.object(ci, "ROOT", source), forbid_evidence_writes():
                result = gate.validate_tiered_evidence(state, original_resolver=independent_resolver)
                self.assertEqual(result["checks_ready"], ready, result)
                self.assertEqual(gate.next_action(state, original_resolver=independent_resolver)["action"], "STOP")
                action = gate.evaluate_tiered_lifecycle(state, original_resolver=independent_resolver)["action"]
                self.assertEqual(action, "MERGE_PROPOSAL" if ready else "REFRESH_EVIDENCE")
            self.assertEqual(evidence_tree(evidence), before)
        assess(True)
        for name in ("originals/raw/review-0.json", "originals/raw/check.raw", "originals/raw/resolution.raw",
                     "originals/backups/review-1.json", "permission-actor.json"):
            path = evidence / "transport" / name; raw = path.read_bytes(); path.unlink()
            with self.subTest(missing=name): assess(False)
            self.assertFalse(path.exists()); path.write_bytes(raw)
        path = evidence / "transport/originals/raw/check.raw"
        raw = path.read_bytes(); path.write_bytes(raw + b"forged")
        assess(False)
        self.assertEqual(path.read_bytes(), raw + b"forged")

    def test_hidden_archive_paths_and_dos_hidden_entries_are_explicitly_unsupported(self):
        for name in (".report", "raw/.report", ".hidden/report", "raw/.hidden/report"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "hidden"):
                transport._archive(zip_bytes({name: b"original"}))
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            member = zipfile.ZipInfo("raw/report"); member.external_attr = 0x02
            archive.writestr(member, b"original")
        with self.assertRaisesRegex(ValueError, "hidden"): transport._archive(stream.getvalue())

    def test_retained_source_requires_every_fixed_reference_exact_bytes_and_git_policy(self):
        value = self.fetch()
        output = self.folder / "output-1"
        for key, pointer in value["source"]["artifacts"].items():
            path = output / pointer["path"]
            original = path.read_bytes(); path.write_bytes(original + b"tamper")
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "hash differs"):
                transport.verify_retained_capture(value["source"], self.f.event, output, self.f.root, candidate=self.f.candidate)
            path.write_bytes(original)
        for key in value["source"]["artifacts"]:
            changed = deepcopy(value["source"]); del changed["artifacts"][key]
            with self.subTest(missing=key), self.assertRaisesRegex(ValueError, "missing/unknown"):
                transport.verify_retained_capture(changed, self.f.event, output, self.f.root)
        changed = deepcopy(value["source"]); changed["artifacts"]["archive"]["path"] = "../outside.zip"
        with self.assertRaisesRegex(ValueError, "path differs"):
            transport.verify_retained_capture(changed, self.f.event, output, self.f.root)
        for field in ("run_id", "run_attempt", "artifact_id", "artifact_name"):
            changed = deepcopy(value["source"])
            changed[field] = 999 if field != "artifact_name" else "forged"
            with self.subTest(field=field), self.assertRaises(ValueError):
                transport.verify_retained_capture(changed, self.f.event, output, self.f.root)
        changed = deepcopy(value["source"]); changed["policy_git"]["sha256"] = "f" * 64
        with self.assertRaisesRegex(ValueError, "authoritative Git base"):
            transport.verify_retained_capture(changed, self.f.event, output, self.f.root)

    def test_current_pr_api_rejects_forged_or_stale_event_repository_base_head(self):
        original = deepcopy(self.f.current_pr)
        for side, field, replacement in (("base", "sha", "a" * 40), ("head", "sha", "b" * 40),
                                          ("base", "ref", "main"), ("head", "ref", "other")):
            self.f.current_pr = deepcopy(original); self.f.current_pr[side][field] = replacement
            with self.subTest(side=side, field=field), self.assertRaises(ValueError): self.fetch()
        self.f.current_pr = deepcopy(original); self.f.current_pr["base"]["repo"]["full_name"] = "wrong/repo"
        with self.assertRaises(ValueError): self.fetch()
        self.f.current_pr = deepcopy(original); self.f.current_pr["base"]["repo"]["id"] += 1
        with self.assertRaises(ValueError): self.fetch()
        self.f.current_pr = deepcopy(original); self.f.current_pr["state"] = "closed"
        with self.assertRaises(ValueError): self.fetch()

    def test_repository_api_identity_is_required(self):
        for replacement in (dict(id=991001, full_name="wrong/repo"), dict(id=991002, full_name=transport.REPOSITORY),
                            dict(id=True, full_name=transport.REPOSITORY)):
            self.f.repository = replacement
            with self.subTest(replacement=replacement), self.assertRaises(ValueError): self.fetch()

    def test_source_run_event_workflow_base_repository_and_success_are_required(self):
        original = deepcopy(self.f.run)
        changes = [dict(event="pull_request"), dict(head_sha="f" * 40), dict(head_branch="main"),
                   dict(path=".github/workflows/author.yml"), dict(workflow_id=881002), dict(run_attempt=1),
                   dict(status="in_progress"), dict(conclusion="failure"),
                   dict(repository=dict(id=991001, full_name="wrong/repo"))]
        for change in changes:
            self.f.run = dict(deepcopy(original), **change)
            with self.subTest(change=change), self.assertRaises(ValueError): self.fetch()

    def test_actor_and_triggering_actor_need_allowed_api_ids_and_current_permission(self):
        original = deepcopy(self.f.run)
        for field in ("actor", "triggering_actor"):
            self.f.run = deepcopy(original); self.f.run[field]["id"] = 771002
            with self.subTest(field=field), self.assertRaises(ValueError): self.fetch()
        self.f.run = deepcopy(original)
        for change in (dict(permission="write", role_name="write"), dict(permission="read"), dict(permission=True),
                       dict(permission="maintain", role_name="maintain"), dict(role_name="custom-role"),
                       dict(permission="admin", role_name="write"),
                       dict(user=dict(id=771002, login="synthetic-coordinator")),
                       dict(user=dict(id=771001, login="different-login"))):
            self.f.permission = dict(permission="write", role_name="maintain", user=deepcopy(original["actor"]))
            self.f.permission.update(change)
            with self.subTest(change=change), self.assertRaises(ValueError): self.fetch()
        self.f.permission = dict(permission="admin", role_name="admin", user=deepcopy(original["actor"]))
        self.assertEqual(self.fetch()["capture"], self.f.capture)

    def test_newer_failed_inprogress_other_candidate_or_rerun_never_falls_back(self):
        original = deepcopy(self.f.run)
        for state in ("failure", "in_progress", "other-candidate-success"):
            newer = dict(deepcopy(original), id=701, run_number=8)
            if state == "failure": newer["conclusion"] = "failure"
            elif state == "in_progress": newer.update(status="in_progress", conclusion=None)
            # Even a completed newer dispatch for a different candidate causes
            # conservative full fallback: no search through earlier successes.
            self.f.runs = [deepcopy(original), newer]
            with self.subTest(state=state), self.assertRaises((ValueError, AssertionError)): self.fetch()
        self.f.runs = [dict(deepcopy(original), run_attempt=3)]
        with self.assertRaisesRegex(ValueError, "stale"): self.fetch()

    def test_attempt_or_latest_run_drift_during_download_is_rejected(self):
        self.f.after_run["run_attempt"] = 3
        with self.assertRaisesRegex(ValueError, "changed"): self.fetch()
        self.f.after_run = deepcopy(self.f.run)
        self.f.after_runs.append(dict(deepcopy(self.f.run), id=701, run_number=8))
        with self.assertRaisesRegex(ValueError, "changed"): self.fetch()

    def test_artifact_uniqueness_binding_expiry_digest_and_identity(self):
        original = deepcopy(self.f.artifacts[0])
        changes = [dict(name=original["name"].replace("-123-", "-124-")), dict(name=original["name"].replace("-700-2", "-700-1")),
                   dict(expired=True), dict(expired=0), dict(id=True), dict(size_in_bytes=True),
                   dict(digest="sha256:" + "f" * 64), dict(workflow_run=dict(original["workflow_run"], id=701)),
                   dict(workflow_run=dict(original["workflow_run"], repository_id=991002)),
                   dict(workflow_run=dict(original["workflow_run"], head_sha="f" * 40))]
        for change in changes:
            self.f.artifacts = [dict(deepcopy(original), **change)]
            with self.subTest(change=change), self.assertRaises(ValueError): self.fetch()
        self.f.artifacts = [deepcopy(original), deepcopy(original)]
        with self.assertRaisesRegex(ValueError, "exactly one"): self.fetch()

    def test_manifest_candidate_task_base_head_and_capture_are_exactly_bound(self):
        original = deepcopy(self.f.manifest)
        for field, value in (("task_id", "other-task"), ("baseline", "a" * 40), ("base", "b" * 40), ("head", "c" * 40),
                             ("repository", "wrong/repo"), ("tree", "d" * 40)):
            self.f.manifest = deepcopy(original); self.f.manifest["candidate"][field] = value; self.f.repack()
            with self.subTest(field=field), self.assertRaises(ValueError): self.fetch()
        self.f.manifest = deepcopy(original); self.f.manifest["capture_sha256"] = "e" * 64; self.f.repack()
        with self.assertRaisesRegex(ValueError, "capture original hash"): self.fetch()

    def test_download_tamper_and_canonical_attachment_tamper_fail_independently(self):
        self.f.archive += b"tampered download"
        with self.assertRaisesRegex(ValueError, "digest/size"): self.fetch()
        self.f.repack()
        self.f.members["raw/review-1.json"] += b"\n"
        self.f.repack()  # API digest authenticates the ZIP; inner hash still fails.
        with self.assertRaisesRegex(ValueError, "attachment bytes/hash"): self.fetch()

    def test_self_consistent_manifest_cannot_make_a_different_backup_same_bytes(self):
        pointer = self.f.capture["review_originals"][0]
        backup = pointer["backups"][0]
        row = next(item for item in self.f.attachment_rows if item["source_file"] == backup["source_file"])
        self.f.members[row["path"]] += b"different backup"
        backup["sha256"] = row["sha256"] = transport.digest(self.f.members[row["path"]])
        self.f.members["capture.json"] = json_bytes(self.f.capture)
        self.f.manifest["capture_sha256"] = transport.digest(self.f.members["capture.json"])
        self.f.repack()
        with self.assertRaisesRegex(ValueError, "backup differs"): self.fetch()

    def test_canonical_utf8_bom_bytes_follow_shared_report_parser_without_rewriting(self):
        pointer = self.f.capture["review_originals"][0]
        for row in self.f.attachment_rows:
            if row["report_ref"] == pointer["report_ref"]:
                raw = b"\xef\xbb\xbf" + self.f.members[row["path"]]
                self.f.members[row["path"]] = raw; row["sha256"] = transport.digest(raw)
        pointer["sha256"] = pointer["backups"][0]["sha256"] = transport.digest(self.f.members["raw/review-0.json"])
        self.f.members["capture.json"] = json_bytes(self.f.capture)
        self.f.manifest["capture_sha256"] = transport.digest(self.f.members["capture.json"]); self.f.repack()
        value = self.fetch()
        self.assertEqual(policy.verify_original(pointer, resolver=value["original_resolver"]), self.f.members["raw/review-0.json"])

    def test_manifest_extra_member_unknown_fields_duplicate_binding_and_bad_json_fail(self):
        self.f.members["not-bound.raw"] = b"unbound"
        self.f.repack()
        with self.assertRaisesRegex(ValueError, "unbound"): self.fetch()
        del self.f.members["not-bound.raw"]
        self.f.manifest["extra"] = True; self.f.repack()
        with self.assertRaisesRegex(ValueError, "unknown or missing"): self.fetch()
        del self.f.manifest["extra"]
        self.f.manifest["attachments"].append(deepcopy(self.f.attachment_rows[0])); self.f.repack()
        with self.assertRaisesRegex(ValueError, "duplicate"): self.fetch()
        self.f.manifest["attachments"].pop()
        self.f.members["capture.json"] = b'{"schema":1,"schema":2}'
        self.f.manifest["capture_sha256"] = transport.digest(self.f.members["capture.json"]); self.f.repack()
        with self.assertRaisesRegex(ValueError, "duplicate JSON"): self.fetch()

    def test_missing_nested_check_resolution_and_each_backup_binding_is_rejected(self):
        original_rows = deepcopy(self.f.attachment_rows)
        for row in original_rows:
            self.f.manifest["attachments"] = [item for item in original_rows if item != row]
            # Remove member too so a missing pointer, rather than unbound entry,
            # is the actual reason for rejection.
            raw = self.f.members.pop(row["path"]); self.f.repack()
            with self.subTest(path=row["path"]), self.assertRaisesRegex(ValueError, "binding is missing"): self.fetch()
            self.f.members[row["path"]] = raw
        self.f.manifest["attachments"] = original_rows

    def test_resolver_unknown_tuple_and_relocated_original_tamper_are_rejected(self):
        value = self.fetch()
        resolver = value["original_resolver"]
        row = self.f.attachment_rows[0]
        for key, replacement in (("source_file", str((self.folder / "unknown").absolute())), ("sha256", "f" * 64),
                                 ("report_ref", "synthetic://unknown")):
            changed = dict(row, **{key: replacement})
            with self.subTest(key=key), self.assertRaises(ValueError): resolver(changed["source_file"], changed["sha256"], changed["report_ref"])
        for pointer in (self.f.check, self.f.resolution, self.f.capture["review_originals"][0]):
            for source in (pointer, pointer["backups"][0]):
                path = resolver(source["source_file"], source["sha256"], pointer["report_ref"])
                original = path.read_bytes(); path.write_bytes(original + b"\n")
                with self.subTest(ref=pointer["report_ref"], source=source["source_file"]), self.assertRaises(ValueError):
                    validate_tiered_reviews(self.f.candidate, self.f.capture["classification"], self.f.capture["implementers"],
                                           self.f.capture["review_originals"], original_resolver=resolver)
                path.write_bytes(original)

    def test_zip_escape_alias_link_and_member_limit_are_rejected(self):
        for name in ("../outside", "/absolute", "C:/drive", "C:relative", "raw\\escape", "raw/../escape", "raw//double", "raw/./dot", "raw/trailing.", "raw/NUL", ".", "raw/a?b"):
            # Windows ZipInfo normalizes '\\' when *writing* a fixture. Replace
            # the equal-length local+central filename bytes to create a real
            # hostile ZIP rather than test the writer's normalized safe path.
            normalized = name.replace("\\", "/")
            raw = zip_bytes({normalized: b"x"})
            if "\\" in name:
                raw = raw.replace(normalized.encode(), name.encode())
            with self.subTest(name=name), self.assertRaises(ValueError): transport._archive(raw)
        raw = zip_bytes({"raw/nulx": b"x"}).replace(b"raw/nulx", b"raw/nul\0")
        with self.assertRaises(ValueError): transport._archive(raw)
        for names in (("raw/a", "raw/A"), ("raw", "raw/a")):
            with self.subTest(names=names), self.assertRaises(ValueError): transport._archive(zip_bytes({name: b"x" for name in names}))
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            link = zipfile.ZipInfo("raw/link"); link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16; archive.writestr(link, "../../outside")
        with self.assertRaises(ValueError): transport._archive(data.getvalue())
        data = io.BytesIO()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(data, "w") as archive:
                archive.writestr("raw/same", b"1"); archive.writestr("raw/same", b"2")
        with self.assertRaises(ValueError): transport._archive(data.getvalue())
        with self.assertRaises(ValueError): transport._archive(zip_bytes({"big": b"x" * (transport.MAX_MEMBER_BYTES + 1)}))
        with self.assertRaises(ValueError): transport._archive(zip_bytes({str(i): b"" for i in range(transport.MAX_MEMBERS + 1)}))


if __name__ == "__main__":
    unittest.main()
