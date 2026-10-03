"""Same-installation wiring and early probe regressions; no real Tk is created."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import validation_tk_environment as wiring
from scripts import validation_runner as runner


class TkEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root / "Python313"
        (self.base / "DLLs").mkdir(parents=True)
        for name in ("_tkinter.pyd", "tcl86t.dll", "tk86t.dll"):
            (self.base / "DLLs" / name).write_bytes(b"fixture, not a runtime DLL")
        for kind, file, version in (("tcl", "init.tcl", "Tcl"), ("tk", "tk.tcl", "Tk")):
            folder = self.base / "tcl" / (kind + "8.6")
            folder.mkdir(parents=True)
            # Actual CPython resources have two spaces before the Tk patchlevel.
            (folder / file).write_text("package require -exact " + version + "  8.6.14\n")
        self.loaded = {name: str(self.base / "DLLs" / name) for name in ("tcl86t.dll", "tk86t.dll")}

    def select(self, extension=None, loaded=None):
        return wiring.select_resources(self.base, extension or self.base / "DLLs/_tkinter.pyd",
                                       "8.6", "8.6", loaded or self.loaded)

    def test_exact_installation_resources_accept_venv_base_prefix_and_patchlevels(self):
        result = self.select()
        self.assertEqual(result["tcl_library"], str((self.base / "tcl/tcl8.6").resolve()))
        self.assertEqual(result["tk_library"], str((self.base / "tcl/tk8.6").resolve()))
        self.assertEqual((result["tcl_version"], result["tk_version"]), ("8.6.14", "8.6.14"))
        self.assertEqual(result["loaded_dlls"], self.loaded)

    def test_missing_resource_and_incompatible_or_ambiguous_versions_fail_closed(self):
        resource = self.base / "tcl/tk8.6/tk.tcl"
        for payload in ("package require -exact Tk 8.5.14\n", "no declared Tk version\n",
                        "package require -exact Tk 8.6.14\npackage require -exact Tk 8.6.14\n"):
            resource.write_text(payload)
            with self.assertRaises(ValueError):
                self.select()
        resource.unlink()
        with self.assertRaises(FileNotFoundError):
            self.select()

    def test_foreign_extension_and_loaded_dll_are_rejected_without_disk_search(self):
        foreign = self.root / "foreign.pyd"
        foreign.write_bytes(b"foreign")
        with self.assertRaisesRegex(ValueError, "_tkinter"):
            self.select(extension=foreign)
        with self.assertRaisesRegex(ValueError, "loaded Tcl/Tk DLL"):
            self.select(loaded={**self.loaded, "tcl86t.dll": str(foreign)})
        with patch.object(Path, "rglob", side_effect=AssertionError("no filesystem scanning")):
            self.select()

    def test_configuration_writes_only_current_job_environment_and_retains_failure(self):
        envfile = self.root / "job-env"
        envfile.write_text("")
        selected = self.select()
        with patch.dict(os.environ, {"GITHUB_ENV": str(envfile)}), patch.object(wiring, "configuration", return_value=selected):
            self.assertEqual(wiring.main(["configure", "--evidence-root", str(self.root / "evidence"), "--github-env", str(envfile)]), 0)
            self.assertEqual(envfile.read_text().splitlines(), ["TCL_LIBRARY=" + selected["tcl_library"], "TK_LIBRARY=" + selected["tk_library"]])
            other = self.root / "unrelated-env"
            self.assertEqual(wiring.main(["configure", "--evidence-root", str(self.root / "evidence"), "--github-env", str(other)]), 2)
            self.assertFalse(other.exists())
        records = [json.loads(path.read_text()) for path in (self.root / "evidence").glob("tk-configure-*/configuration.json")]
        self.assertEqual(sorted(row["outcome"] for row in records), ["failed", "success"])
        self.assertTrue(all(row["started_at"] and row["finished_at"] and row["source_sha256"] for row in records))

    def test_early_probe_is_fd_same_interpreter_bounded_and_preserves_failed_raw_evidence(self):
        folder = self.root / "preflight"
        folder.mkdir()
        env = runner.environment()
        def failure(command, output, name, timeout, child_env):
            self.assertEqual(command[0], wiring.sys.executable)
            self.assertIn("--capture=fd", command)
            self.assertEqual(timeout, 60)
            self.assertEqual({name: child_env.get(name, "") for name in runner.ENV_KEYS}, env["environment"])
            (output / name).write_text("original initialization failure")
            return {"outcome": "failed", "exit_code": 1, "started_at": "start", "finished_at": "end", "runner_start_error": None}
        with patch.object(runner, "validate_environment"), patch.object(runner, "snapshot", return_value={"head": "a" * 40}), patch.object(runner, "run_process", side_effect=failure):
            self.assertFalse(wiring.early_preflight(folder))
        result = json.loads((folder / "preflight-result.json").read_text())
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual((folder / "preflight.log").read_text(), "original initialization failure")
        self.assertEqual(len(result["source_sha256"]), 2)
        self.assertFalse((folder / "manifest.json").exists())

    def test_early_probe_does_not_accept_incomplete_success(self):
        folder = self.root / "preflight"
        folder.mkdir()
        def incomplete(command, output, name, timeout, env):
            (output / name).write_text("incomplete synthetic probe")
            (output / "preflight.json").write_text('{"stage": "widgets"}')
            return {"outcome": "success", "exit_code": 0}
        with patch.object(runner, "validate_environment"), patch.object(runner, "snapshot", return_value={}), patch.object(runner, "run_process", side_effect=incomplete):
            self.assertFalse(wiring.early_preflight(folder))
        self.assertEqual(json.loads((folder / "preflight-result.json").read_text())["outcome"], "failed")

class EarlyPreflightCliTests(unittest.TestCase):
    """Real isolated CLI/pytest/history flow; only the Tk child is a tiny stub."""
    def setUp(self):
        import hashlib
        import shutil
        import subprocess
        import uuid
        self.hashlib, self.subprocess = hashlib, subprocess
        self.source = Path(__file__).resolve().parents[1]
        retained = os.environ.get("VALIDATION_CLI_FIXTURE_ROOT")
        if retained:
            # Git/CreateProcess on Windows still have short cwd constraints.
            # Keep the real fixture short; retain raw proof by explicit reference.
            self.root = Path(tempfile.mkdtemp(prefix="cli5-", dir=self.source / "tmp"))
            self.proof = Path(retained) / ("case-" + uuid.uuid4().hex[:8])
        else:
            temporary = tempfile.TemporaryDirectory(dir=self.source / "tmp")
            self.addCleanup(temporary.cleanup)
            self.root = Path(temporary.name)
            self.proof = self.root / "proof"
        self.proof.mkdir(parents=True)
        self.repo = self.root / "repo"
        (self.repo / "scripts").mkdir(parents=True)
        (self.repo / "tests").mkdir()
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith("GITHUB_") and key not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")}
        self.env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTEST_ADDOPTS="",
                        GITHUB_ACTIONS="true", GITHUB_EVENT_NAME="pull_request",
                        GITHUB_RUN_ID="101", GITHUB_RUN_ATTEMPT="1", GITHUB_JOB="isolated-fixture")
        self.commands = []
        for name in ("validation_tk_environment.py", "validation_runner.py", "test_entrypoint_audit.py"):
            shutil.copyfile(self.source / "scripts" / name, self.repo / "scripts" / name)
        shutil.copyfile(self.source / "requirements-ci-lock.txt", self.repo / "requirements-ci-lock.txt")
        (self.repo / ".gitignore").write_text("tmp/\n__pycache__/\n.pytest_cache/\n")
        # This is deliberately a stub child, never a Tk acceptance claim.
        (self.repo / "scripts/gui_preflight.py").write_text(
            "import json, os\nfrom pathlib import Path\n"
            "def test_probe():\n"
            "    arm = os.environ.get('FIXTURE_PROBE_ARM', 'success')\n"
            "    if arm == 'abrupt': os._exit(7)\n"
            "    stage = 'widgets' if arm == 'incomplete' else 'complete'\n"
            "    Path(os.environ['VALIDATION_PREFLIGHT_OUTPUT']).write_text(json.dumps({'stage': stage}))\n"
            "    assert arm != 'failure', 'isolated child failure'\n", encoding="utf-8")
        test_source = "from pathlib import Path\nimport os\ndef test_core():\n    Path(os.environ['FIXTURE_CORE_MARKER']).write_text('actual core body executed')\n"
        (self.repo / "tests/test_probe.py").write_text(test_source, encoding="utf-8")
        registry = {"schema": "zhuyin-test-group-registry/1", "source_scope": "tests",
                    "source_review": {"basis": "One explicit isolated non-window marker test",
                                      "normalized_source_sha256": {"test_probe.py": hashlib.sha256(test_source.encode()).hexdigest()}},
                    "entries": [{"prefix": "test_probe", "core": ["test_core"], "gui": []}]}
        (self.repo / "scripts/test_group_registry.json").write_text(json.dumps(registry), encoding="utf-8")
        self.cli(["git", "init"], "git-init")
        self.cli(["git", "add", "."], "git-fixture-add")
        self.cli(["git", "-c", "user.name=Isolated Fixture", "-c", "user.email=fixture@example.invalid",
                  "-c", "commit.gpgsign=false", "commit", "-m", "isolated CLI fixture only"], "git-fixture-commit")
        def git(*args):
            result = self.cli(["git", *args], "git-snapshot")
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout.strip()
        self.candidate = {"head": git("rev-parse", "HEAD"), "tree": git("rev-parse", "HEAD^{tree}"),
                          "parents": git("show", "-s", "--format=%P", "HEAD").split()}
        self.evidence = self.repo / "tmp/validation-evidence"
        self.evidence.mkdir(parents=True)
        # Synthetic hosted-context boundary, not a remote response or task ledger.
        context = {"task_id": "isolated-preflight-cli-fixture", "current_run": 101, "current_attempt": 1,
                   "candidate": self.candidate, "head": self.candidate["head"], "executions": [],
                   "runs": [], "raw_runs": [], "local_handoff_sha256": "f" * 64}
        (self.evidence / "history-index-fixture.json").write_text(json.dumps(context), encoding="utf-8")
        self.marker = self.repo / "tmp/core-body-marker.txt"
        self.env["FIXTURE_CORE_MARKER"] = str(self.marker)
        self.producer = [wiring.sys.executable, "scripts/validation_tk_environment.py", "preflight",
                         "--evidence-root", str(self.evidence)]
        self.aggregate = [wiring.sys.executable, "scripts/validation_runner.py", "aggregate",
                          "--evidence-root", str(self.evidence), "--output", str(self.evidence / "coverage.json")]
        self.core = [wiring.sys.executable, "scripts/validation_runner.py", "run", "--group", "core",
                     "--mode", "validation", "--evidence-root", str(self.evidence), "--timeout", "60"]
        self.addCleanup(self.save_proof)

    def cli(self, command, label):
        from datetime import datetime, timezone
        started = datetime.now(timezone.utc).isoformat()
        result = self.subprocess.run(command, cwd=self.repo, env=self.env, capture_output=True,
                                     text=True, encoding="utf-8", timeout=60)
        number = len(self.commands)
        (self.proof / f"{number:02d}-{label}.stdout").write_text(result.stdout, encoding="utf-8")
        (self.proof / f"{number:02d}-{label}.stderr").write_text(result.stderr, encoding="utf-8")
        self.commands.append({"argv": command, "cwd": str(self.repo), "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(), "exit_code": result.returncode,
                              "stdout_ref": f"{number:02d}-{label}.stdout", "stderr_ref": f"{number:02d}-{label}.stderr"})
        return result

    def save_proof(self):
        sources = {str(path.relative_to(self.repo)): self.hashlib.sha256(path.read_bytes()).hexdigest()
                   for path in (self.repo / "scripts").glob("*.py")}
        artifacts = {str(path.relative_to(self.repo)): self.hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in self.evidence.rglob("*") if path.is_file() and path.suffix in (".json", ".jsonl", ".xml", ".log")}
        raw = {}
        markers = {"core": self.marker, "next_core": self.repo / "tmp/next-core-body-marker.txt"}
        if os.environ.get("VALIDATION_CLI_FIXTURE_ROOT"):
            # Persist raw bytes independently of fixture lifetime; never copy
            # .git, pytest scratch, DLLs or real-task assets/ledgers.
            paths = [(self.repo / name, "evidence/" + str((self.repo / name).relative_to(self.evidence))) for name in artifacts]
            paths += [(path, "sources/" + path.relative_to(self.repo).as_posix()) for path in (self.repo / "scripts").glob("*.py")]
            paths += [(self.repo / name, "sources/" + name) for name in ("tests/test_probe.py", "scripts/test_group_registry.json", "requirements-ci-lock.txt")]
            paths += [(path, "markers/" + name + ".txt") for name, path in markers.items() if path.is_file()]
            for original, relative in paths:
                payload = original.read_bytes()
                target = self.proof / "raw" / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
                digest = self.hashlib.sha256(payload).hexdigest()
                self.assertEqual(self.hashlib.sha256(target.read_bytes()).hexdigest(), digest)
                raw[relative] = {"path": "raw/" + relative, "sha256": digest}
        (self.proof / "cli-proof.json").write_text(json.dumps({"boundary": "Synthetic standalone Git/CI history-index fixture; real unmodified runner/history/pytest/CLI. Only Tk child stubbed. Not hosted or formal coverage.",
              "test_case": self._testMethodName, "fixture_root": str(self.root), "commands": self.commands, "copied_source_sha256": sources, "execution_artifact_sha256": artifacts,
              "retained_raw": raw, "markers": {name: path.exists() for name, path in markers.items()},
              "core_marker_exists": self.marker.exists(), "candidate": self.candidate}, indent=2), encoding="utf-8")

    def probe_then_core(self):
        probe = self.cli(self.producer, "preflight-producer")
        if probe.returncode != 0:
            return probe, None
        history_check = self.cli(self.aggregate, "real-history-aggregate-before-core")
        self.assertEqual(history_check.returncode, 2)
        self.assertIn("no execution evidence", history_check.stderr)
        self.assertNotIn("unfinished execution", history_check.stderr)
        return probe, self.cli(self.core, "real-core")

    def test_successful_producer_real_history_and_core_create_marker_events_junit_manifest(self):
        probe, core = self.probe_then_core()
        self.assertEqual(probe.returncode, 0, probe.stderr)
        self.assertEqual(core.returncode, 0, core.stderr)
        self.assertEqual(self.marker.read_text(), "actual core body executed")
        preflight = next(self.evidence.glob("tk-preflight-*"))
        self.assertFalse((preflight / "started.json").exists())
        self.assertFalse((preflight / "manifest.json").exists())
        workflow = (self.source / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        for suffix in ("json", "xml", "log"):
            self.assertIn("tmp/validation-evidence/**/*." + suffix, workflow)
        for name in ("preflight-start.json", "preflight-result.json"):
            data = json.loads((preflight / name).read_text())
            self.assertEqual(data["schema"], "zhuyin-early-preflight/1")
            self.assertEqual(data["kind"], "early_preflight")
        manifest = json.loads(next(self.evidence.glob("*/manifest.json")).read_text())
        self.assertEqual((manifest["outcome"], manifest["group"], manifest["verification"]["top_level"]), ("success", "core", 1))
        group_folder = self.evidence / manifest["execution_id"]
        self.assertTrue((group_folder / "started.json").is_file())
        self.assertTrue((group_folder / "junit.xml").is_file())
        events = [json.loads(line) for line in (group_folder / "events.jsonl").read_text().splitlines()]
        self.assertEqual([(row["when"], row["outcome"]) for row in events], [("setup", "passed"), ("call", "passed"), ("teardown", "passed")])
        missing_gui = self.cli(self.aggregate, "real-history-after-core")
        self.assertEqual(missing_gui.returncode, 2)
        self.assertIn("missing gui evidence", missing_gui.stderr)
        self.assertFalse((self.evidence / "coverage.json").exists())

    def test_failed_incomplete_and_abrupt_child_cli_stop_sequence_before_core_marker(self):
        for arm in ("failure", "incomplete", "abrupt"):
            with self.subTest(arm=arm):
                self.env["FIXTURE_PROBE_ARM"] = arm
                probe, core = self.probe_then_core()
                self.assertEqual(probe.returncode, 2)
                self.assertIsNone(core)
                self.assertFalse(self.marker.exists())
                self.assertFalse(list(self.evidence.glob("*/manifest.json")))
        self.assertFalse(any(row["argv"] == self.core for row in self.commands))
        results = [json.loads(path.read_text()) for path in self.evidence.glob("tk-preflight-*/preflight-result.json")]
        self.assertEqual(len(results), 3)
        self.assertTrue(all(row["outcome"] == "failed" for row in results))
        self.assertEqual(sorted(row["exit_code"] for row in results), [0, 1, 7])

    def test_genuine_group_start_without_manifest_still_blocks_next_core_and_marker(self):
        _, first = self.probe_then_core()
        self.assertEqual(first.returncode, 0, first.stderr)
        actual = next(self.evidence.glob("*/manifest.json")).parent / "started.json"
        # Preserve the complete real producer start bytes; an incomplete retained
        # copy must never be inferred complete from a different group's manifest.
        unfinished = self.evidence / "retained-unfinished-group"
        unfinished.mkdir()
        start = unfinished / "started.json"
        start.write_bytes(actual.read_bytes())
        original_hash = self.hashlib.sha256(start.read_bytes()).hexdigest()
        next_marker = self.repo / "tmp/next-core-body-marker.txt"
        self.env["FIXTURE_CORE_MARKER"] = str(next_marker)
        blocked = self.cli(self.core, "real-core-with-unfinished-group")
        self.assertEqual(blocked.returncode, 2)
        self.assertIn("unfinished execution history", blocked.stderr)
        self.assertFalse(next_marker.exists())
        self.assertFalse((unfinished / "manifest.json").exists())
        self.assertEqual(self.hashlib.sha256(start.read_bytes()).hexdigest(), original_hash)
        aggregate = self.cli(self.aggregate, "real-aggregate-with-unfinished-group")
        self.assertEqual(aggregate.returncode, 2)
        self.assertIn("unfinished execution history", aggregate.stderr)
