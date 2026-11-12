"""First-edition isolation tests; full/short positives below are explicit models.

No retired parser or transport is imported or executed. The unchanged research
source, original test oracles and RA5-F1/RB5-F1 failures remain in their retained
archive/reports. A modeled coverage/short success is never hosted/full evidence.
"""
from copy import deepcopy
import builtins
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts import pr_flow_ci as ci
from scripts import pr_flow_policy as policy
from scripts import validation_evidence as evidence
from scripts import validation_runner as runner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from test_current_validation_evidence import bundle, current_short

ARCHIVE = ROOT / "docs/research/PR_FLOW_DEFERRED_822d494.zip"
DEFERRED_IMPORTS = {"pr_flow_transport", "pr_flow_evidence"}


@contextmanager
def forbid_deferred_imports():
    original = builtins.__import__
    def guarded(name, globals=None, locals=None, fromlist=(), level=0):
        if name.rsplit(".", 1)[-1] in DEFERRED_IMPORTS or (name == "scripts" and DEFERRED_IMPORTS.intersection(fromlist or ())):
            raise AssertionError("first edition attempted a deferred research import: " + name)
        return original(name, globals, locals, fromlist, level)
    with patch("builtins.__import__", side_effect=guarded):
        yield


class FirstEditionCiTests(unittest.TestCase):
    def test_all_pr_categories_and_activation_states_keep_full_without_capture(self):
        event = {"pull_request": {"base": {"ref": "develop"}}}
        environment = {"GITHUB_EVENT_NAME": "pull_request", "GITHUB_REF": "refs/pull/20/merge"}
        with tempfile.TemporaryDirectory() as folder, forbid_deferred_imports():
            root = Path(folder)
            capture = root / "tmp/pr-review-automation/pr-flow-ci/current-capture.json"
            capture.parent.mkdir(parents=True)
            capture.write_text('{"route":"docs","trusted":true}', encoding="utf-8")
            for tier in ("docs", "local", "core"):
                for enabled in (False, True):
                    current = deepcopy(event)
                    current["pull_request"]["labels"] = [{"name": tier}]
                    current["author_capture"] = {"classification": tier, "route": "docs", "full_exempt": True}
                    with self.subTest(tier=tier, enabled=enabled), patch.object(policy, "ENABLED", enabled), patch.object(Path, "read_text", side_effect=AssertionError("router must not inspect capture, source policy or a document")):
                        result = ci.dispatch(current, environment, root)
                    self.assertEqual(result["route"], "full")
                    self.assertEqual(set(result), {"schema", "policy", "route", "reason"})
                    self.assertIn("no documentation exemption", result["reason"])
            self.assertEqual(ci.dispatch({"pull_request": {"base": {"ref": "main"}}}, environment, root)["route"], "full")
            for ref, expected in (("refs/heads/main", "full"), ("refs/heads/develop", "short")):
                self.assertEqual(ci.dispatch({}, {"GITHUB_EVENT_NAME": "push", "GITHUB_REF": ref}, root)["route"], expected)
            for actual, env in (({}, environment), (event, {"GITHUB_EVENT_NAME": "workflow_dispatch"}), ({}, {"GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/heads/other"})):
                with self.assertRaises(ValueError): ci.dispatch(actual, env, root)

    def test_dispatch_cli_runs_without_capture_and_has_no_documentation_commands(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            event = root / "event.json"
            event.write_text(json.dumps({"pull_request": {"base": {"ref": "develop"}}}), encoding="utf-8")
            output, github_output = root / "plan.json", root / "github-output.txt"
            environment = {**os.environ, "GITHUB_EVENT_PATH": str(event), "GITHUB_EVENT_NAME": "pull_request",
                           "GITHUB_REF": "refs/pull/20/merge", "GITHUB_OUTPUT": str(github_output),
                           "PR_FLOW_ENABLE": "true", "PYTHONDONTWRITEBYTECODE": "1"}
            result = subprocess.run([sys.executable, "-X", "utf8", "-B", str(Path(ci.__file__)), "dispatch", "--output", str(output)], cwd=root, env=environment, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(json.loads(output.read_text())["route"], "full")
            self.assertEqual(github_output.read_text(), "route=full\n")
            self.assertFalse((root / "tmp").exists())
            for command in ("documentation", "documentation-pr", "verify-documentation", "verify-pr-documentation", "inspect", "inspect-pr"):
                response = subprocess.run([sys.executable, "-X", "utf8", "-B", str(Path(ci.__file__)), command], cwd=root, env=environment, capture_output=True, timeout=15)
                self.assertNotEqual(response.returncode, 0)
                self.assertIn(b"invalid choice", response.stderr)

    def test_deferred_sources_and_oracles_remain_exact_and_are_not_importable(self):
        self.assertEqual(hashlib.sha256(ARCHIVE.read_bytes()).hexdigest(), "a33a9a69236e40b66646f384360d7e0b90a70d5c14340b5917f90bb1870d9679")
        with zipfile.ZipFile(ARCHIVE) as archive:
            self.assertEqual(len(archive.namelist()), 9)
            for name, expected in {
                "scripts/pr_flow_ci.py": "712ae0dcf041efd6e0afd861039b02d86499d92193d14b46298a159cc399873c",
                "scripts/pr_flow_transport.py": "1ba0687443570fe4ea7ea6fefb19a2e5f099db76fbed526ccb77ae90472ee9a1",
                "scripts/pr_flow_evidence.py": "eed7d6c0cdb3e0980fb4f2bc301106b4d279a2c7de9af9dc0ec1be83d1b7d272",
                "tests/test_pr_flow_ci.py": "683c00beace463a55de6210188694646155d28b847f9d67681b17189aff96349",
            }.items():
                self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), expected)
        for name in DEFERRED_IMPORTS:
            self.assertIsNone(importlib.util.find_spec("scripts." + name))
            self.assertFalse((ROOT / "tests" / ("test_" + name + ".py")).exists())
        for name in ("_documentation_lex", "_anchors", "inspect_documentation", "run_documentation_checks", "verify_pr_checks", "validated_documentation_plan"):
            self.assertFalse(hasattr(ci, name), "retired capability remained callable in first-edition router: " + name)
        self.assertFalse(policy.ENABLED)

    def test_original_parser_counterexamples_cannot_be_full_validation_evidence(self):
        # These are the unchanged known failing research inputs. The assertion
        # proves only exclusion from first-edition evidence, not a parser fix.
        counterexamples = {
            "RA5-F1": '# Target\n\nParagraph <pre></pre>\n`<a id="example-only"></a>`\n',
            "RB5-F1": '# Guide\n\n<!-- comment block -->\n<span>\n`<a href="missing.md">Real HTML resource</a>`\n</span>\n',
        }
        with tempfile.TemporaryDirectory() as folder, forbid_deferred_imports():
            root = Path(folder)
            for finding, text in counterexamples.items():
                packet = root / (finding + ".json")
                packet.write_text(json.dumps({"schema": "pr-flow-documentation-checks/2", "status": "success", "research_source": "822d494", "original_counterexample": text}), encoding="utf-8")
                with self.subTest(finding=finding), self.assertRaises(ValueError):
                    evidence.verify_current_coverage(packet)
            valid = bundle(root / "full-model")
            self.assertEqual(evidence.verify_current_coverage(valid)["schema"], evidence.CURRENT_COVERAGE_SCHEMA)
            self.assertFalse((valid.parent / "pr-flow-ci").exists())

    def _model_post_merge(self, root, *, artifact_name=None, different_environment=False):
        """Model API/checkout/environment/short execution; never a real CI run."""
        root = Path(root)
        retained = root / "source"
        retained.mkdir()
        coverage, short = current_short(retained)
        original = evidence.load_json(short)
        responses = {key: evidence.load_json(retained / (key + ".json")) for key in ("pr", "run", "commit", "artifact", "runs")}
        if artifact_name is not None: responses["artifact"]["name"] = artifact_name
        archive = (retained / "pr-artifact.zip").read_bytes()
        class ModelAPI:
            def __init__(self): self.calls = []
            def request(self, endpoint, *, binary=False):
                self.calls.append(endpoint)
                if endpoint.startswith("commits/"): return deepcopy(responses["commit"])
                if endpoint.startswith("pulls/"): return deepcopy(responses["pr"])
                if endpoint == "actions/runs/100": return deepcopy(responses["run"])
                if endpoint == "actions/artifacts/7/zip": return archive
                raise AssertionError(endpoint)
            def pages(self, endpoint, key=None):
                self.calls.append(endpoint)
                if endpoint.endswith("/pulls"): return [{**deepcopy(responses["pr"]), "state": "closed"}]
                if "head_sha=" + original["candidate"]["parents"][1] in endpoint: return deepcopy(responses["runs"])
                if endpoint == "actions/runs/100/artifacts": return [deepcopy(responses["artifact"])]
                raise AssertionError(endpoint)
        api = ModelAPI()
        assets = (coverage.parent / "assets.json").read_bytes()
        (root / "runtime_asset_manifest.json").write_bytes(assets)
        environment = deepcopy(original["environment"])
        if different_environment: environment["dependencies"].pop("pytest")
        def command_transport(command, **kwargs):
            if command[:3] == ["git", "cat-file", "--filters"]:
                return subprocess.CompletedProcess(command, 0, stdout=assets, stderr=b"")
            self.assertIn("stdout", kwargs)
            if command != ["git", "status", "--porcelain"]:
                kwargs["stdout"].write(b"synthetic short command transport only; no actual execution claim\n")
            return subprocess.CompletedProcess(command, 0)
        pushed = {"GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/heads/develop", "GITHUB_SHA": original["candidate"]["head"],
                  "GITHUB_RUN_ID": "501", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_JOB": "short"}
        with patch.object(policy, "ENABLED", True), patch.object(evidence, "ROOT", root), patch.object(runner, "ROOT", root), \
             patch.object(runner, "snapshot", return_value=original["candidate"]), patch.object(runner, "environment", return_value=environment), \
             patch.object(evidence, "GitHub", return_value=api), patch.object(subprocess, "run", side_effect=command_transport), \
             patch.dict(os.environ, pushed), forbid_deferred_imports():
            path, record = evidence.post_merge(root / "output", current_only=True)
        return path, record, api

    def test_model_full_post_merge_uses_existing_reader_without_deferred_imports(self):
        with tempfile.TemporaryDirectory() as folder:
            path, record, api = self._model_post_merge(folder)
            self.assertEqual(record["outcome"], "success", record.get("error"))
            self.assertEqual(record["schema"], evidence.CURRENT_SHORT_SCHEMA)
            self.assertEqual([item["name"] for item in record["checks"]], ["runtime", "import", "cli", "compile", "diff", "clean"])
            self.assertEqual(evidence.verify_reuse(path.parent / record["coverage_path"], path, current_only=True)["outcome"], "success")
            self.assertEqual(sum(call.endswith("/zip") for call in api.calls), 1)
            self.assertFalse(any("capture" in call or "permission" in call for call in api.calls))

    def test_model_full_post_merge_rejects_documentation_artifact_and_environment_difference(self):
        for options, error in (({"artifact_name": "documentation-validation-evidence-100-1"}, "exactly one retained evidence artifact"),
                               ({"different_environment": True}, "environment differs from PR coverage")):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as folder:
                _, record, _ = self._model_post_merge(folder, **options)
                self.assertEqual(record["outcome"], "failed")
                self.assertEqual(record["checks"], [])
                self.assertIn(error, record["error"])


if __name__ == "__main__":
    unittest.main()
