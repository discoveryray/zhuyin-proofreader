"""Small isolated runner regressions; these fixtures never create a Tk window."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from scripts import test_entrypoint_audit as audit
from scripts import validation_runner as runner


# Exact adopted bytes, not a self-authored fixture authorization. Original
# artifact pins alone are replaced with this independently specified tiny ZIP;
# every admission, immutable manifest, skip, claim and coverage reader stays real.
_PR43_ADOPTED_BYTES = {"authorization":"ew0KICAic2NoZW1hIjogInByNDMtZ3VpLW9uY2UtaHVtYW4tYWRvcHRpb24vMSIsDQogICJodW1hbl9tZXNzYWdlX2V4YWN0IjogIlx1NjgzOFx1NTFjNiIsDQogICJhbnRlY2VkZW50IjogIkltbWVkaWF0ZWx5IHByZWNlZGluZyBhc3Npc3RhbnQgZmluYWwgcmVjb21tZW5kYXRpb24gd2l0aCB0aHJlZSByZXF1ZXN0ZWQgZGVsaXZlcmFibGVzOyBleGFjdCBzY29wZSByZXByZXNlbnRlZCBpbiBhcHByb3ZlZC1wcm9wb3NhbC5qc29uIiwNCiAgInByb3Bvc2FsX3JlZiI6ICJDOlxcVXNlcnNcXEUwNDA2OVxcRGVza3RvcFxcR2l0SHViMlxcemh1eWluLXByb29mcmVhZGVyXFx0bXBcXHdvcmt0cmVlc1xcYWN0dWFsLWdwdC1iYm94LWV4cG9ydFxcdG1wXFxwci1yZXZpZXctYXV0b21hdGlvblxcYWN0dWFsLWdwdC1iYm94LWV4cG9ydFxccHI0My1ndWktb25jZS1hZG9wdGlvblxcYXBwcm92ZWQtcHJvcG9zYWwuanNvbiIsDQogICJwcm9wb3NhbF9zaGEyNTYiOiAiOWIwYWJhMzNmMmNmMzRiNWVjMjljMjkwZjMzODg1MDE4OTllOWU1NzdlNWExMjNiNTVjOWJmNTMyMTJjMzY0YSIsDQogICJodW1hbl9yZXBseV9yZWYiOiAiQzpcXFVzZXJzXFxFMDQwNjlcXERlc2t0b3BcXEdpdEh1YjJcXHpodXlpbi1wcm9vZnJlYWRlclxcdG1wXFx3b3JrdHJlZXNcXGFjdHVhbC1ncHQtYmJveC1leHBvcnRcXHRtcFxccHItcmV2aWV3LWF1dG9tYXRpb25cXGFjdHVhbC1ncHQtYmJveC1leHBvcnRcXHByNDMtZ3VpLW9uY2UtYWRvcHRpb25cXGh1bWFuLXJlcGx5LnR4dCIsDQogICJodW1hbl9yZXBseV9zaGEyNTYiOiAiZjVhNWU4N2Q3NzcwZTQ2ZmNmOWUwODc1ZGQzYjA4ZjA1ZmY3YmVjMjk4YWE1YTlkZjlkZjI3MDBlMTg1MjIwYyIsDQogICJhZG9wdGVkX2F0IjogIjIwMjYtMTAtMDZUMTM6MzE6MDguNDMzMDI1KzAwOjAwIiwNCiAgIm5ld19jYW5kaWRhdGVfdG9fZnJlZXplX2FmdGVyX2ltcGxlbWVudGF0aW9uIjogdHJ1ZSwNCiAgImNvcnJlY3RpdmVfZGVsdGEiOiAwDQp9DQo=","proposal":"ew0KICAic2NoZW1hIjogInByNDMtZ3VpLW9uY2UtYXBwcm92ZWQtcHJvcG9zYWwvMSIsDQogICJ0YXNrIjogImFjdHVhbC1ncHQtYmJveC1leHBvcnQiLA0KICAicmVwb3NpdG9yeSI6ICJkaXNjb3ZlcnlyYXkvemh1eWluLXByb29mcmVhZGVyIiwNCiAgInByIjogNDMsDQogICJzdGFydGluZ19oZWFkIjogImQ1OTM1OGNkNDVjYzY1YTczYjFkNGRjYjBhYTIyOGZmZTcyMzM0MzUiLA0KICAiYmFzZWxpbmUiOiAiOGVhYjlhMWRlMzRiNTkxMTU2NThhMmM3ZDM1NjVhZGIzNDdlNTlhYiIsDQogICJvcmlnaW5hbF9ydW4iOiAzNzQ1Mzg4OTI3OCwNCiAgIm9yaWdpbmFsX2F0dGVtcHQiOiAxLA0KICAib3JpZ2luYWxfYXJ0aWZhY3QiOiAxMTQxMjk2NzI3OCwNCiAgImFydGlmYWN0X3NoYTI1NiI6ICIyMDBmZjc2NzM4YzdjYjU2NWNmYWQxY2M0NzBiZTk3ZmEwYmMzYTIxOTc5MDhhNjMzOTFjMTMzNWYwZDgyMjM1IiwNCiAgIm9yaWdpbmFsX2d1aV9leGVjdXRpb24iOiAiMDhlMTkzZDY1YWZhNDRhNmI5NDk4ZjYzMTU2MTUyMWIiLA0KICAicHJvcG9zYWwiOiAiT25seSBQUjQzIGZpbml0ZSBHVUkgc3VwcGxlbWVudCBzaWRlIGV2aWRlbmNlOyBwcmVzZXJ2ZSBsZWdhY3kgcmV0cnlfZWxpZ2libGUgZmFsc2UgYW5kIGFsbCBza2lwL2V4aXQvYXJ0aWZhY3RzOyByZXN0b3JlX2hpc3RvcnkgaW1wb3J0cyBhdXRob3JpemF0aW9uLCBfcnVuX2dyb3VwIGR1cmFibHkgcmVzZXJ2ZXMgb25lIHN1cHBsZW1lbnQgYmVmb3JlIGxhdW5jaCwgcmV0cnlfaGlzdG9yeSBlbmZvcmNlcyBvcmlnaW5hbCtvbmUgYWNyb3NzIGNhbmRpZGF0ZSBjaGFuZ2UsIGFnZ3JlZ2F0ZSB2YWxpZGF0ZXMgc2FtZSBhZG1pc3Npb24gYW5kIG5ldyBjYW5kaWRhdGUgY29yZS9ndWkuIHJlYWRfdmVyaWZpZWRfbWFuaWZlc3QgbGVnYWN5IHJldHJ5IGVsaWdpYmlsaXR5IHJlbWFpbnMgdW5jaGFuZ2VkLiB2ZXJpZnlfY292ZXJhZ2UgZGVsZWdhdGVzIGFnZ3JlZ2F0ZTsgbm8gcGFyYWxsZWwgc3VjY2VzcyBzZXJ2aWNlIG9yIHdvcmtmbG93IGpvYi4gTmV3IHRyYWNrZWQgaW1wbGVtZW50YXRpb24gY29tbWl0IHJlcXVpcmVzIG5ldyBjYW5kaWRhdGUgb25jZSBmb3JtYWwgY29yZWFsbCBwbHVzIGNvbXBsZXRlR1VJLCBubyBvbGQgY29yZSByZWxhYmVsL3JldXNlLiBTaG9ydCBDTEkvaGlzdG9yeS9hZ2dyZWdhdGUgcmVncmVzc2lvbnMgdGhlbiBpbmRlcGVuZGVudCBmdWxsIGNvZGUgcmV2aWV3IGJlZm9yZSBwdXNoOyB0d28gZm9ybWFsIHJldmlld2VycyBhbmQgbWVyZ2UvcG9zdG1lcmdlIGdhdGVzIHJldGFpbmVkLiBHZW5lcmFsIHNraXAsIGdlbmVyaWMvdW5rbm93biBUY2xFcnJvciwgYXNzZXJ0aW9ucywgbWl4ZWQgZmFpbHVyZXMsIG1pc3NpbmcvY29ycnVwdCBldmlkZW5jZSByZWZ1c2UuIEV4YWN0IG9yaWdpbmFsIGluaXRpYWxpemF0aW9uIHJlYWQtZXJyb3IgZXZpZGVuY2Ugb25seTsgc291cmNlIGJpbmRpbmcsIEpVbml0L2V2ZW50cy9yYXcgYWdyZWVtZW50LiBTdXBwbGVtZW50IGFsbCBHVUkgbXVzdCBwYXNzIG5vIHNraXA7IGZhaWx1cmUvdGltZW91dC9pbnRlcnJ1cHRpb24gY29uc3VtZXMgZmluYWwgR1VJIGJ1ZGdldCBhbmQgU1RPUC4gTm8gYmJveCBmZWF0dXJlL2NhcHR1cmUvc3lzdGVtIFRjbC9QeXRob24vZ2VuZXJhbCByZXRyeSBmcmFtZXdvcmsgY2hhbmdlcy4gQ291bnRzIGJib3gyLzMgQ0ZGMi8zIHJldGFpbmVkOyBzdWJzZXF1ZW50IGNvbmZpcm1lZCBjb2RlIGNvcnJlY3RpdmUgdW5kZXIgb3JpZ2luYWwgcnVsZXMuIg0KfQ0K","human_reply":"5qC45YeG"}


class Pr43GuiSupplementTests(unittest.TestCase):
    def setUp(self):
        import base64
        import hashlib
        import io
        import zipfile
        from scripts import validation_evidence
        self.evidence_module = validation_evidence
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="g43-")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.root = self.folder / "evidence"
        self.root.mkdir()
        self.original = self.folder / "original"
        self.original.mkdir()
        self.spec = dict(runner.PR43_GUI_ONCE)
        self.feature = "1" * 40
        self.candidate = {"head": "2" * 40, "tree": "3" * 40,
                          "parents": [self.spec["baseline"], self.feature]}
        self.environment = {"python": "3.13.0", "platform": "Windows", "capture": "fd",
                            "dependencies": {}, "environment": {key: "" for key in runner.ENV_KEYS}}
        for line in (ROOT / "requirements-ci-lock.txt").read_text().splitlines():
            if line.strip() and not line.startswith("#"):
                name, version = line.split("==")
                self.environment["dependencies"][name.lower().replace("_", "-")] = version
        self.environment["environment"]["TCL_LIBRARY"] = "C:\\Tk"
        self.environment["environment"]["TK_LIBRARY"] = "C:\\Tk"
        self.names = ["test_fixture_" + str(i) for i in range(11)]
        module = self.folder / "test_manual_actual_gui_batch_v570.py"
        module.write_text("import unittest\nclass ManualActualGuiVisibleLayoutTests(unittest.TestCase):\n"
                          + "".join("    def " + name + "(self): self.assertTrue(True)\n" for name in self.names)
                          + "class Other(unittest.TestCase):\n    def test_gui(self): self.assertTrue(True)\n"
                          + "class Core(unittest.TestCase):\n    def test_core(self): self.assertTrue(True)\n")
        relative = module.relative_to(ROOT).as_posix()
        methods = [("ManualActualGuiVisibleLayoutTests", name, "gui") for name in self.names]
        methods += [("Other", "test_gui", "gui"), ("Core", "test_core", "core")]
        self.inventory = {"schema": "zhuyin-test-groups/1", "records": [
            {"identity": "test_manual_actual_gui_batch_v570." + cls + "." + name,
             "nodeid": relative + "::" + cls + "::" + name, "group": group} for cls, name, group in methods]}
        self.inventory.update(pytest_ids=[row["identity"] for row in self.inventory["records"]],
                              core_ids=[row["identity"] for row in self.inventory["records"] if row["group"] == "core"],
                              gui_ids=[row["identity"] for row in self.inventory["records"] if row["group"] == "gui"])
        original_inventory = copy.deepcopy(self.inventory)
        for row in original_inventory["records"]:
            row["nodeid"] = "tests/test_manual_actual_gui_batch_v570.py::" + row["nodeid"].split("::", 1)[1]
        self.old = self.original / self.spec["execution_id"]
        self.old.mkdir()
        runner.write_json(self.old / "inventory.json", original_inventory)
        invhash = runner.digest(self.old / "inventory.json")
        message = ("Tk display unavailable: Can't find a usable init.tcl in the following directories: \n    C:/Tk\n\n"
                   'C:/Tk/init.tcl: couldn\'t read file "C:/Tk/init.tcl": No error\n'
                   'couldn\'t read file "C:/Tk/init.tcl": No error\n    while executing\n"source C:/Tk/init.tcl"\n'
                   '    ("uplevel" body line 1)\n    invoked from within\n"uplevel #0 [list source $tclfile]"\n\n\n'
                   "This probably means that Tcl wasn't installed properly.")
        xml = ET.Element("testsuites")
        suite = ET.SubElement(xml, "testsuite")
        properties = ET.SubElement(suite, "properties")
        for key, value in (("validation_execution_id", self.spec["execution_id"]), ("validation_inventory_sha256", invhash)):
            ET.SubElement(properties, "property", name=key, value=value)
        events = []
        for row in original_inventory["records"]:
            if row["group"] != "gui": continue
            cls, name = row["nodeid"].split("::")[1:]
            case = ET.SubElement(suite, "testcase", classname="tests.test_manual_actual_gui_batch_v570." + cls, name=name)
            skip = cls == "ManualActualGuiVisibleLayoutTests"
            if skip: ET.SubElement(case, "skipped", message=message)
            for phase in (("setup", "teardown") if skip else ("setup", "call", "teardown")):
                events.append(dict(nodeid=row["nodeid"], when=phase, outcome="skipped" if skip and phase == "setup" else "passed",
                                   subtest=False, traceback=repr(("C:/site-packages/_pytest/unittest.py", 523, "Skipped: " + message))
                                   if skip and phase == "setup" else None))
        ET.ElementTree(xml).write(self.old / "junit.xml", encoding="utf-8")
        (self.old / "events.jsonl").write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")
        (self.old / "raw.log").write_text(message, encoding="utf-8")
        (self.old / "collection.log").write_text("independent tiny fixture\n")
        for name, value in (("preflight.json", {"stage": "complete"}), ("history-context.json", {"task_id": self.spec["task_id"]})):
            runner.write_json(self.old / name, value)
        (self.old / "preflight.log").write_text("fixture preflight")
        (self.old / "preflight.xml").write_text('<testsuite><testcase name="probe"/></testsuite>')
        value = dict(schema=runner.LEGACY_SCHEMA, execution_id=self.spec["execution_id"], group="gui",
                     candidate={"head": "4" * 40, "tree": "5" * 40, "parents": [self.spec["baseline"], self.spec["starting_head"]]},
                     command=[sys.executable, str(ROOT / "scripts/validation_runner.py"), "_pytest", str(self.old), "gui"],
                     environment=self.environment, inventory_sha256=invhash,
                     started_at="2026-10-06T00:00:00+00:00", finished_at="2026-10-06T00:00:01+00:00",
                     exit_code=0, outcome="failed", verification={"top_level": 0, "subtests": 0, "error": "skipped"},
                     ci={"run_id": self.spec["run_id"], "attempt": 1, "job": "grouped", "event": "pull_request"},
                     retry_of=None, artifacts={}, retry_key=runner.canonical_digest({"task": self.spec["task_id"], "tree": "5" * 40}),
                     retry_eligible=False, preflight={"outcome": "success", "exit_code": 0}, runner_start_error=None, code_blockers=False)
        runner.write_json(self.old / "started.json", value)
        value["artifacts"] = {key: runner.artifact_entry(self.old / filename, self.old) for key, filename in
                              (("inventory", "inventory.json"), ("started", "started.json"), ("history_context", "history-context.json"),
                               ("raw_log", "raw.log"), ("junit", "junit.xml"), ("events", "events.jsonl"),
                               ("preflight_result", "preflight.json"), ("preflight_log", "preflight.log"), ("preflight_junit", "preflight.xml"))}
        runner.write_json(self.old / "manifest.json", value)
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as zipped:
            for path in self.original.rglob("*"):
                if path.is_file(): zipped.write(path, path.relative_to(self.original).as_posix())
        self.zipbytes = payload.getvalue()
        self.spec.update(archive_sha256=hashlib.sha256(self.zipbytes).hexdigest(),
                         manifest_sha256=runner.digest(self.old / "manifest.json"))
        self.patch_spec = patch.dict(runner.PR43_GUI_ONCE, self.spec)
        self.patch_spec.start(); self.addCleanup(self.patch_spec.stop)
        for name, value in _PR43_ADOPTED_BYTES.items():
            (self.folder / name).write_bytes(base64.b64decode(value))
        self.block = runner.pr43_gui_block(self.feature, self.folder / "authorization", self.folder / "proposal", self.folder / "human_reply")
        # Independent empty local-history fixture; never bootstrap/reset the
        # real task's already sealed canonical store.
        local_payload = io.BytesIO()
        ledger = dict(schema=runner.LOCAL_SCHEMA, task_id=self.spec["task_id"], source_ref="independent fixture original task",
                      baseline=self.spec["baseline"], repository=runner.REPOSITORY,
                      created_at="2026-10-06T00:00:00+00:00", store_id="6" * 32)
        with zipfile.ZipFile(local_payload, "w", zipfile.ZIP_DEFLATED) as zipped:
            zipped.writestr("ledger.json", json.dumps(ledger))
            for name in ("executions", "declarations"):
                zipped.writestr(name + "/store.json", json.dumps({"schema": "zhuyin-local-store/1", "store_id": ledger["store_id"]}))
        local_handoff = dict(schema=runner.HANDOFF_SCHEMA, task_id=self.spec["task_id"], source_ref=ledger["source_ref"],
                             archive_sha256=hashlib.sha256(local_payload.getvalue()).hexdigest(),
                             archive_base64=base64.b64encode(local_payload.getvalue()).decode())
        self.block += "\n<!-- validation-local-history\n" + json.dumps(local_handoff) + "\n-->"
        self.event = self.folder / "event.json"
        runner.write_json(self.event, {"pull_request": {"number": 43, "body": self.block,
                          "head": {"sha": self.feature, "ref": "codex/actual-gpt-bbox-export", "repo": {"full_name": runner.REPOSITORY}},
                          "base": {"sha": self.spec["baseline"], "repo": {"full_name": runner.REPOSITORY}}}})
        self.vars = {"GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "pull_request", "GITHUB_REPOSITORY": runner.REPOSITORY,
                     "GITHUB_EVENT_PATH": str(self.event), "GITHUB_RUN_ID": "777", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_JOB": "grouped"}
        self.patches = [patch.dict("os.environ", self.vars), patch.object(runner, "snapshot", return_value=self.candidate),
                        patch.object(runner, "git", side_effect=lambda *args: self.spec["starting_head"] if args[:1] == ("show",) else ""),
                        patch.object(runner, "environment", return_value=self.environment),
                        patch.object(runner, "collect_inventory", side_effect=self.collect),
                        patch.object(runner, "run_process", side_effect=self.process)]
        self.real_process = ORIGINAL_RUN_PROCESS
        self.preflight_ok = True
        self.launches = []
        for item in self.patches: item.start(); self.addCleanup(item.stop)
        # Current execution has exactly one real non-window test, independently
        # from the synthetic legacy archive: no stub can authorize new GUI runs.
        core_module = self.folder / "test_pr43_current.py"
        core_module.write_text("def test_core(): assert 2 + 3 == 5\n")
        identity = "test_pr43_current.test_core"
        self.core_inventory = {"schema": "zhuyin-test-groups/2", "policy": runner.POLICY,
            "window_validation": audit.WINDOW_NOTICE, "pytest_ids": [identity], "core_ids": [identity],
            "cancelled_window_ids": [], "records": [{"identity": identity, "group": "core",
            "nodeid": core_module.relative_to(ROOT).as_posix() + "::test_core"}]}
        self.restore()

    def api(self, endpoint):
        if "/artifacts/" in endpoint:
            return {"id": self.spec["artifact_id"], "digest": "sha256:" + self.spec["archive_sha256"],
                    "workflow_run": {"id": self.spec["run_id"]}, "expired": False}
        if endpoint.endswith("/artifacts?per_page=100&page=1"):
            return {"total_count": 1, "artifacts": [{"name": "validation-evidence-37453889278-1", "expired": False}]}
        return {"total_count": 2, "workflow_runs": [
            {"id": 777, "head_sha": self.feature, "run_attempt": 1, "status": "in_progress",
             "head_repository": {"full_name": runner.REPOSITORY}, "pull_requests": [{"number": 43}]},
            {"id": self.spec["run_id"], "head_sha": self.spec["starting_head"], "run_attempt": 1, "status": "completed",
             "head_repository": {"full_name": runner.REPOSITORY}, "pull_requests": [{"number": 43}]}]}

    def restore(self):
        import shutil
        def output(command, **kwargs):
            return self.zipbytes if command[-1].endswith("/zip") else json.dumps(self.api(command[-1]))
        def download(command, **kwargs):
            shutil.copytree(self.original, Path(command[-1]))
            return subprocess.CompletedProcess(command, 0)
        with patch.object(runner.subprocess, "check_output", side_effect=output), patch.object(runner.subprocess, "run", side_effect=download):
            runner.restore_history(self.root)
        indexes = list(self.root.glob("history-index-*.json"))
        self.context = runner.read_json(indexes[0])
        # Materialize a synthetic PAST declaration, without executing its GUI.
        side = self.root / "pr43-gui-once"; side.mkdir()
        (side / "pr-artifact.zip").write_bytes(self.zipbytes)
        declaration = json.loads(self.block.split("<!-- pr43-gui-once\n", 1)[1].split("\n-->", 1)[0])
        runner.write_json(side / "declaration.json", declaration)
        self.legacy_context = {**self.context, "pr43_gui_once": {
            "declaration": runner.artifact_entry(side / "declaration.json", self.root)}}
        item = patch.object(runner, "validate_history_context", return_value=self.context)
        item.start(); self.addCleanup(item.stop)

    def collect(self, folder, tests=None):
        runner.write_json(folder / "inventory.json", self.core_inventory)
        (folder / "collection.log").write_text("finite source fixture inventory")
        return self.core_inventory

    def process(self, command, folder, name, timeout, env=None):
        self.launches.append(name)
        self.assertNotEqual(name, "preflight.log", "current runner must never launch preflight")
        return self.real_process(command, folder, name, timeout, env)

    def historical_claim(self, execution="7" * 32):
        """Synthetic legacy evidence only; never a real task ledger/receipt."""
        claim = self.root / "pr43-gui-once/claim.json"
        runner.write_json(claim, dict(execution_id=execution, candidate=self.candidate,
            environment=self.environment, declaration_sha256=self.legacy_context["pr43_gui_once"]["declaration"]["sha256"],
            declared_at="2026-10-06T00:01:00+00:00"))
        context = copy.deepcopy(self.legacy_context)
        context["pr43_gui_once"]["claim"] = runner.artifact_entry(claim, self.root)
        folder = self.root / execution; folder.mkdir()
        runner.write_json(folder / "history-context.json", context)
        return folder, context

    def historical_execution(self, group, folder=None, context=None, blocked=False):
        """Construct bounded past metadata; no retired runner/GUI is executed."""
        import shutil
        if folder is None:
            folder = self.root / ("8" * 32); folder.mkdir()
        context = context or self.legacy_context
        old = runner.read_json(self.old / "manifest.json")
        inventory = runner.read_json(self.old / "inventory.json")
        for name in ("inventory.json", "preflight.json", "preflight.log", "preflight.xml"):
            shutil.copyfile(self.old / name, folder / name)
        if not (folder / "history-context.json").exists(): runner.write_json(folder / "history-context.json", context)
        selected = [row for row in inventory["records"] if row["group"] == group]
        xml = ET.Element("testsuites"); suite = ET.SubElement(xml, "testsuite")
        props = ET.SubElement(suite, "properties")
        for key, value in (("validation_execution_id", folder.name), ("validation_inventory_sha256", old["inventory_sha256"])):
            ET.SubElement(props, "property", name=key, value=value)
        events = []
        for row in selected:
            cls, name = row["nodeid"].split("::")[1:]
            ET.SubElement(suite, "testcase", classname="tests.test_manual_actual_gui_batch_v570." + cls, name=name)
            events.extend(dict(nodeid=row["nodeid"], when=phase, outcome="passed", subtest=False, traceback=None)
                          for phase in ("setup", "call", "teardown"))
        ET.ElementTree(xml).write(folder / "junit.xml", encoding="utf-8")
        (folder / "events.jsonl").write_text("\n".join(json.dumps(x) for x in events), encoding="utf-8")
        (folder / "raw.log").write_text("SYNTHETIC HISTORICAL CONTRACT FIXTURE; not an execution receipt")
        data = {**old, "execution_id": folder.name, "group": group, "candidate": self.candidate,
            "command": [sys.executable, str(ROOT / "scripts/validation_runner.py"), "_pytest", str(folder), group],
            "started_at": "2026-10-06T00:01:00+00:00", "finished_at": "2026-10-06T00:01:01+00:00",
            "retry_key": runner.canonical_digest({"task": self.spec["task_id"], "tree": self.candidate["tree"]}),
            "retry_of": self.spec["execution_id"] if group == "gui" else None,
            "outcome": "blocked" if blocked else "success", "exit_code": 1 if blocked else 0,
            "verification": {"top_level": len(selected), "subtests": 0, "error": None}, "artifacts": {}}
        if blocked:
            data["preflight"] = {"outcome": "failed", "exit_code": 1}
            (folder / "preflight.log").write_text("AssertionError: synthetic historical preflight failure")
        runner.write_json(folder / "started.json", data)
        data["artifacts"] = {key: runner.artifact_entry(folder / item["path"], folder) for key, item in old["artifacts"].items()}
        runner.write_json(folder / "manifest.json", data)
        runner.read_verified_manifest(folder / "manifest.json")
        return folder / "manifest.json"

    def assert_current_gui_refused(self):
        before = list(self.launches)
        with patch.object(runner, "collect_inventory", side_effect=AssertionError("GUI collection forbidden")):
            for call in (lambda: runner.run_group("gui", self.root, mode="validation"),
                         lambda: runner._run_group("gui", self.root, mode="validation"),
                         lambda: runner.pytest_child(self.root, "gui")):
                with self.assertRaisesRegex(ValueError, "視窗|window|Tk"):
                    call()
        self.assertEqual(self.launches, before)

    def test_real_cli_child_history_aggregate_and_coverage_keep_legacy_false(self):
        old = runner.history(self.root)[0][0]; original_bytes = old.read_bytes()
        self.assertFalse(runner.read_verified_manifest(old)["retry_eligible"])
        core = runner.run_group("core", self.root, mode="validation")
        self.assertEqual(runner.read_verified_manifest(core)["schema"], runner.SCHEMA)
        original_core = core.read_bytes()
        for field, value in (("group", "gui"), ("preflight", {"outcome": "success", "exit_code": 0}),
                             ("retry_of", self.spec["execution_id"]), ("retry_eligible", True)):
            with self.subTest(current_rejects=field):
                changed = json.loads(original_core); changed[field] = value
                core.write_text(json.dumps(changed), encoding="utf-8")
                try:
                    with self.assertRaisesRegex(ValueError, "no-window"):
                        runner.read_verified_manifest(core)
                finally: core.write_bytes(original_core)
        self.assertEqual(runner.main(["aggregate", "--evidence-root", str(self.root), "--output", str(self.root / "coverage.json")]), 0)
        self.assertEqual(self.evidence_module.verify_coverage(self.root / "coverage.json")["status"], "success")
        self.assertEqual(runner.run_group("core", self.root, mode="validation"), core)
        self.assert_current_gui_refused()
        self.assertEqual(self.launches.count("raw.log"), 1)
        self.assertEqual(old.read_bytes(), original_bytes)
        with self.assertRaisesRegex(ValueError, "claim"):
            runner.validate_pr43_gui_once(self.root, self.legacy_context, self.candidate, require_claim=True)

    def test_claim_before_collection_failure_consumes_final_budget(self):
        folder, context = self.historical_claim()
        supplement = runner.validate_pr43_gui_once(self.root, context, self.candidate, require_claim=True)
        self.assertEqual(len(supplement["claims"]), 1)
        key = runner.canonical_digest({"task": self.spec["task_id"], "tree": self.candidate["tree"]})
        with self.assertRaisesRegex(ValueError, "unfinished.*budget"):
            runner.retry_history(runner.history(self.root), key, supplement)
        self.assert_current_gui_refused()
        self.assertTrue((self.root / "pr43-gui-once/claim.json").is_file())
        self.assertEqual(self.launches, [])

    def test_uploaded_restored_side_refs_recompute_coverage_and_reject_corrupt_claim(self):
        import shutil
        folder, context = self.historical_claim()
        self.historical_execution("gui", folder, context)
        self.historical_execution("core")
        restored = self.folder / "restored"; bundle = restored / "downloaded"
        for path in self.root.rglob("*"):
            if path.is_file() and (path.suffix in {".json", ".jsonl", ".xml", ".log"} or path.name == "pr-artifact.zip"):
                target = bundle / path.relative_to(self.root); target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
        self.assertTrue((bundle / "pr43-gui-once/pr-artifact.zip").is_file())
        self.assertFalse((restored / "pr43-gui-once").exists())
        coverage = runner.aggregate(restored, legacy=True)
        runner.write_json(restored / "coverage.json", coverage)
        self.assertEqual(self.evidence_module.verify_coverage(restored / "coverage.json")["status"], "success")
        claim = bundle / "pr43-gui-once/claim.json"; claim.write_bytes(claim.read_bytes() + b"corruption")
        with self.assertRaises(ValueError): self.evidence_module.verify_coverage(restored / "coverage.json")
        self.assertEqual(self.launches, [])

    def test_missing_marker_stops_new_tree_gui_before_first_launch(self):
        with self.assertRaisesRegex(ValueError, "missing"):
            runner.validate_pr43_gui_once(self.root, self.context, self.candidate)
        self.assert_current_gui_refused()
        self.assertEqual(self.launches, [])
        self.assertFalse((self.root / "pr43-gui-once/claim.json").exists())

    def test_normal_legacy_fresh_groups_and_success_reuse_stay_separate(self):
        root = self.folder / "ordinary"
        with patch.object(runner, "validate_history_context", return_value={"task_id": "independent-fixture"}):
            core = runner.run_group("core", root, mode="validation")
            self.assertEqual(runner.run_group("core", root, mode="validation"), core)
        self.assertIsNone(runner.read_verified_manifest(core)["retry_of"])
        self.assertEqual(runner.aggregate(root)["status"], "success")
        self.assertFalse((root / "pr43-gui-once/claim.json").exists())
        self.assertEqual(self.launches.count("raw.log"), 1)
        self.assert_current_gui_refused()

    def test_preflight_failure_has_real_claim_and_no_third_attempt(self):
        folder, context = self.historical_claim()
        path = self.historical_execution("gui", folder, context, blocked=True)
        self.assertEqual(runner.read_verified_manifest(path)["outcome"], "blocked")
        supplement = runner.validate_pr43_gui_once(self.root, context, self.candidate, require_claim=True)
        values = runner.history(self.root); key = runner.read_json(path)["retry_key"]
        self.assertEqual(len(runner.retry_history(values, key, supplement)), 2)
        with self.assertRaisesRegex(ValueError, "budget"):
            runner.retry_history([*values, (path, {**runner.read_json(path), "execution_id": "9" * 32})], key, supplement)
        self.assert_current_gui_refused()
        self.assertEqual(self.launches, [])

    def test_binding_head_environment_gui_identity_and_corruption_refuse(self):
        for kind in ("missing", "sha", "candidate", "environment", "identities", "archive", "initializer", "authorization"):
            with self.subTest(kind=kind):
                context = copy.deepcopy(self.legacy_context); candidate = copy.deepcopy(self.candidate)
                env = copy.deepcopy(self.environment); inventory = copy.deepcopy(self.inventory)
                patches = []
                if kind == "missing": context.pop("pr43_gui_once")
                elif kind == "sha": context["pr43_gui_once"]["declaration"]["sha256"] = "0" * 64
                elif kind == "candidate": candidate["parents"][1] = "9" * 40
                elif kind == "environment": env["environment"]["TCL_LIBRARY"] = "foreign"
                elif kind == "identities": inventory["gui_ids"].pop()
                elif kind == "archive": patches.append(patch.dict(runner.PR43_GUI_ONCE, archive_sha256="0" * 64))
                elif kind == "initializer": patches.append(patch.dict(runner.PR43_GUI_ONCE, setup_sha256="0" * 64))
                elif kind == "authorization": patches.append(patch.dict(runner.PR43_GUI_ONCE, authorization_sha256="0" * 64))
                for item in patches: item.start()
                try:
                    with self.assertRaises(ValueError):
                        runner.validate_pr43_gui_once(self.root, context, candidate, env=env, inventory=inventory)
                finally:
                    for item in reversed(patches): item.stop()
        archive = self.root / "pr43-gui-once/pr-artifact.zip"
        original = archive.read_bytes()
        archive.write_bytes(original + b"corruption")
        with self.assertRaisesRegex(ValueError, "binary artifact"):
            runner.validate_pr43_gui_once(self.root, self.legacy_context, self.candidate)
        archive.write_bytes(original)

    def test_archived_initializer_requires_full_blob_and_ast_without_execution(self):
        # Real pinned git object; AST only, no old module import/initializer call.
        runner._pr43_setup_source()
        command = ["git", "show", "d59358cd45cc65a73b1d4dcb0aa228ffe7233435:tests/test_manual_actual_gui_batch_v570.py"]
        raw = subprocess.check_output(command, cwd=ROOT)
        import hashlib
        self.assertEqual(hashlib.sha256(raw).hexdigest(), "1373c00997ca98db7562a39b1b0370b818bc9302c1e2c3a9ef7bc915ade416da")
        with patch.object(runner.subprocess, "check_output", return_value=raw + b"\n") as read:
            with self.assertRaisesRegex(ValueError, "full source changed"): runner._pr43_setup_source()
            read.assert_called_once_with(command, cwd=ROOT)
        with patch.dict(runner.PR43_GUI_ONCE, setup_sha256="0" * 64):
            with self.assertRaisesRegex(ValueError, "initializer source changed"): runner._pr43_setup_source()
        with patch.object(runner.subprocess, "check_output", side_effect=subprocess.CalledProcessError(128, command)):
            with self.assertRaises(subprocess.CalledProcessError): runner._pr43_setup_source()
        self.assertEqual(self.launches, [])

    def test_original_general_skip_unknown_wrapper_mixed_and_noninitialization_refuse(self):
        old = runner.history(self.root)[0]
        folder, value = old[0].parent, old[1]
        # Exercise the real evidence grammar independently of outer ZIP pins.
        original_xml = (folder / "junit.xml").read_bytes()
        original_events = (folder / "events.jsonl").read_bytes()
        original_raw = (folder / "raw.log").read_bytes()
        for kind in ("general", "owner", "mixed", "noninit", "phase", "unknown-event-wrapper"):
            with self.subTest(kind=kind):
                xml = ET.fromstring(original_xml)
                skipped = next(xml.iter("skipped"))
                events = [json.loads(line) for line in original_events.decode().splitlines()]
                if kind == "general": skipped.set("message", "Tk display unavailable")
                elif kind == "owner": next(xml.iter("testcase")).set("classname", "tests.Unknown")
                elif kind == "mixed": ET.SubElement(next(xml.iter("testcase")), "failure", message="AssertionError: data")
                elif kind == "noninit": skipped.set("message", skipped.get("message").replace("init.tcl", "combobox.tcl"))
                elif kind == "phase": events[0]["when"] = "call"
                elif kind == "unknown-event-wrapper": events[0]["traceback"] = repr(("unknown.py", 1, "Skipped: " + skipped.get("message")))
                ET.ElementTree(xml).write(folder / "junit.xml", encoding="utf-8")
                (folder / "events.jsonl").write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")
                with self.assertRaises(ValueError): runner._pr43_skips(folder, value)
                (folder / "junit.xml").write_bytes(original_xml)
                (folder / "events.jsonl").write_bytes(original_events)
                (folder / "raw.log").write_bytes(original_raw)

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL_RUN_PROCESS = runner.run_process


def tk_initializer_wrapper(resource="button", reason="no such file or directory"):
    """Retained Tk8.6 wrapper/SourceLibFile structure, with portable paths."""
    return f'''_tkinter.TclError: Can't find a usable tk.tcl in the following directories:
    {{C:/Python/tcl/tk8.6}} C:/Python/lib/tk8.6

C:/Python/tcl/tk8.6/tk.tcl: couldn't read file "C:/Python/tcl/tk8.6/{resource}.tcl": {reason}
couldn't read file "C:/Python/tcl/tk8.6/{resource}.tcl": {reason}
    while executing
"source -encoding utf-8 C:/Python/tcl/tk8.6/{resource}.tcl"
    (in namespace eval "::" script line 1)
    invoked from within
"namespace eval :: [list source -encoding utf-8 [file join $::tk_library $file.tcl]]"
    (procedure "SourceLibFile" line 2)
    invoked from within
"SourceLibFile {resource}"
    (in namespace eval "::tk" script line 3)
    invoked from within
"namespace eval ::tk {{
\tSourceLibFile icons
\tSourceLibFile button
\tSourceLibFile entry
\tSourceLibFile listbox
\tSourceLibFile menu
\tSourceLibFile panedw..."
    (file "C:/Python/tcl/tk8.6/tk.tcl" line 506)
    invoked from within
"source C:/Python/tcl/tk8.6/tk.tcl"
    ("uplevel" body line 1)
    invoked from within
"uplevel #0 [list source $file]"


This probably means that tk wasn't installed properly.'''


def write_retry_suite(folder, message, nodeid="tests/test_probe.py::Probe::test_root"):
    parts = nodeid.split("::")
    classname = ".".join([parts[0].replace("/", ".").removesuffix(".py"), *parts[1:-1]])
    root = ET.Element("testsuite")
    case = ET.SubElement(root, "testcase", classname=classname, name=parts[-1])
    traceback = ('Tk.__init__\n>       self.tk = _tkinter.create(screenName, baseName)\n'
                 + "\n".join("E       " + line for line in message.splitlines())
                 + '\n\nC:/Python/Lib/tkinter/__init__.py:2459: TclError')
    ET.SubElement(case, "failure", message=message).text = traceback
    ET.ElementTree(root).write(folder / "junit.xml")
    event = {"nodeid": nodeid, "outcome": "failed", "when": "call", "subtest": False, "traceback": traceback}
    (folder / "events.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")
    return root, event


class RunnerRetryClassificationTests(unittest.TestCase):
    """Finite raw-error fixtures; no Tk, Tcl repair or formal GUI execution."""
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)

    def eligible(self):
        return runner.retry_eligible(self.folder, {}, {"outcome": "success"})

    def test_original_wrapper_inner_resource_error_is_retryable(self):
        for resource, reason in (("button", "no such file or directory"), ("entry", "permission denied")):
            with self.subTest(resource=resource):
                write_retry_suite(self.folder, tk_initializer_wrapper(resource, reason))
                self.assertTrue(self.eligible())

    def test_wrapper_unknown_inner_cause_and_missing_proof_are_rejected(self):
        message = tk_initializer_wrapper()
        invalid = [message.replace("Can't find a usable", "Unknown initialization wrapper"),
                   message.split("\n\n", 1)[0],
                   message.replace("no such file or directory", "unknown filesystem error"),
                   message.replace('"SourceLibFile button"', '"load_saved_review button"'),
                   message.replace('C:/Python/tcl/tk8.6/button.tcl"', 'C:/Other/button.tcl"'),
                   '_tkinter.TclError: invalid command name "save"',
                   'AssertionError: bad saved review']
        for value in invalid:
            with self.subTest(message=value[:100]):
                write_retry_suite(self.folder, value)
                self.assertFalse(self.eligible())

    def test_direct_resource_read_still_needs_initializer_and_matching_failure(self):
        message = '_tkinter.TclError: couldn\'t read file "C:/Tk/entry.tcl": invalid argument'
        root, event = write_retry_suite(self.folder, message)
        self.assertTrue(self.eligible())
        event["traceback"] = event["traceback"].replace('self.tk = _tkinter.create(screenName, baseName)', 'save_review()')
        root.find("testcase/failure").text = event["traceback"]
        ET.ElementTree(root).write(self.folder / "junit.xml")
        (self.folder / "events.jsonl").write_text(json.dumps(event) + "\n")
        self.assertFalse(self.eligible())
        _root, event = write_retry_suite(self.folder, message)
        event["traceback"] = event["traceback"].replace("invalid argument", "permission denied")
        (self.folder / "events.jsonl").write_text(json.dumps(event) + "\n")
        self.assertFalse(self.eligible())

    def test_wrapper_non_initializer_identity_mismatch_and_extra_event_rejected(self):
        message = tk_initializer_wrapper()
        for change in ("non_initializer", "wrong_case", "duplicate", "missing_field", "wrong_field_type", "subtest", "teardown"):
            with self.subTest(change=change):
                root, event = write_retry_suite(self.folder, message)
                events = [event]
                if change == "non_initializer":
                    event["traceback"] = event["traceback"].replace('self.tk = _tkinter.create(screenName, baseName)', 'load_saved_review()')
                    root.find("testcase/failure").text = event["traceback"]
                    ET.ElementTree(root).write(self.folder / "junit.xml")
                elif change == "wrong_case":
                    event["nodeid"] += "_different"
                elif change == "duplicate":
                    events.append(event.copy())
                elif change == "missing_field":
                    del event["when"]
                elif change == "wrong_field_type":
                    event["outcome"] = []
                elif change == "subtest":
                    event["subtest"] = True
                else:
                    event["when"] = "teardown"
                (self.folder / "events.jsonl").write_text("\n".join(json.dumps(value) for value in events) + "\n")
                self.assertFalse(self.eligible())

    def test_single_failure_exception_chains_and_extra_blocks_are_rejected(self):
        root, event = write_retry_suite(self.folder, tk_initializer_wrapper())
        # Exception names mentioned in ordinary frame source are not failures.
        event["traceback"] = '        note = "AssertionError: saved review mismatch"\n' + event["traceback"]
        root.find("testcase/failure").text = event["traceback"]
        ET.ElementTree(root).write(self.folder / "junit.xml")
        (self.folder / "events.jsonl").write_text(json.dumps(event) + "\n")
        self.assertTrue(self.eligible())
        for separator, summary in (
                ("During handling of the above exception, another exception occurred:",
                 "AssertionError: saved review mismatch"),
                ("The above exception was the direct cause of the following exception:",
                 "AssertionError: saved review mismatch"),
                ("", "review.StateMismatch: unexpected saved state"),
                ("", "ReviewFailure")):
            with self.subTest(separator=separator, summary=summary):
                root, event = write_retry_suite(self.folder, tk_initializer_wrapper())
                earlier = ('save_review\n>       validate_saved_review()\nE       ' + summary
                           + '\n\nC:/project/review.py:12: ' + summary.split(":", 1)[0] + '\n\n')
                event["traceback"] = earlier + separator + '\n\n' + event["traceback"]
                root.find("testcase/failure").text = event["traceback"]
                ET.ElementTree(root).write(self.folder / "junit.xml")
                (self.folder / "events.jsonl").write_text(json.dumps(event) + "\n")
                self.assertEqual(root.find("testcase/failure").text, event["traceback"])
                self.assertFalse(self.eligible())

    def test_mixed_assertion_skip_and_corrupt_reports_are_rejected(self):
        for change in ("assertion", "skip", "xml", "json", "duplicate_json_key", "duplicate_case", "missing_event", "mismatched_junit_trace"):
            with self.subTest(change=change):
                root, event = write_retry_suite(self.folder, tk_initializer_wrapper())
                if change in {"assertion", "skip", "duplicate_case"}:
                    case = ET.SubElement(root, "testcase", classname="tests.test_probe.Probe", name="test_other")
                    if change == "duplicate_case":
                        case.set("name", "test_root")
                        ET.SubElement(case, "failure", message=tk_initializer_wrapper())
                    else:
                        ET.SubElement(case, "failure" if change == "assertion" else "skipped", message="AssertionError: bad save")
                    ET.ElementTree(root).write(self.folder / "junit.xml")
                    other = {**event, "nodeid": "tests/test_probe.py::Probe::test_other",
                             "outcome": "skipped" if change == "skip" else "failed", "traceback": "AssertionError: bad save"}
                    (self.folder / "events.jsonl").write_text(json.dumps(event) + "\n" + json.dumps(other) + "\n")
                elif change == "xml":
                    (self.folder / "junit.xml").write_text("<testcase")
                elif change == "json":
                    (self.folder / "events.jsonl").write_text("{broken json")
                elif change == "duplicate_json_key":
                    (self.folder / "events.jsonl").write_text('{"outcome":"passed",' + json.dumps(event)[1:])
                elif change == "mismatched_junit_trace":
                    root.find("testcase/failure").text = "AssertionError: bad saved review"
                    ET.ElementTree(root).write(self.folder / "junit.xml")
                else:
                    (self.folder / "events.jsonl").write_text("")
                self.assertFalse(self.eligible())


def fixture_registration(folder, module, gui, core):
    """Explicit tiny-fixture declarations; not an automatic classifier."""
    path = folder / "registry.json"
    registry = {"schema": "zhuyin-test-group-registry/1", "source_scope": "tests",
                "source_review": {"basis": "Explicit source-reviewed non-window regression fixture",
                                  "normalized_source_sha256": {source.relative_to(folder).as_posix(): audit.normalized_source_sha256(source)
                                                               for source in sorted(folder.rglob("*.py"))}},
                "entries": [{"prefix": module, "gui": sorted(gui), "core": sorted(core)}]}
    path.write_text(json.dumps(registry), encoding="utf-8")
    return path


class GroupedCollectionTests(unittest.TestCase):
    def test_partition_rejects_missing_duplicate_unknown_and_overlap(self):
        good = {"schema": "zhuyin-test-groups/1", "pytest_ids": ["m.a", "m.b"],
                "core_ids": ["m.a"], "gui_ids": ["m.b"],
                "records": [{"identity": "m.a", "nodeid": "test_m.py::a", "group": "core"},
                            {"identity": "m.b", "nodeid": "test_m.py::b", "group": "gui"}]}
        audit.validate_groups(good)
        for field, value in (("core_ids", []), ("core_ids", ["m.a", "m.a"]),
                             ("gui_ids", ["m.a", "m.b"]), ("pytest_ids", ["m.a", "m.z"])):
            changed = copy.deepcopy(good)
            changed[field] = value
            with self.assertRaises(ValueError):
                audit.validate_groups(changed)

    def test_imported_helper_inherited_setup_and_pure_logic(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as temporary:
            folder = Path(temporary)
            (folder / "helper_probe.py").write_text("import tkinter as tk\ndef make_root(): return tk.Tk()\n")
            (folder / "source_probe.py").write_text(
                "from helper_probe import make_root\n"
                "class Parent:\n    def setUp(self): self.root = make_root()\n"
                "class Child(Parent):\n    def test_inherited(self): pass\n"
                "def test_function(): return make_root()\n"
                "def test_logic(): return 1 + 2\n")
            sys.path.insert(0, str(folder))
            try:
                spec = importlib.util.spec_from_file_location("source_probe", folder / "source_probe.py")
                source_probe = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(source_probe)
                self.assertTrue(audit.callable_uses_tk(source_probe.Child.setUp, source_probe.Child))
                self.assertTrue(audit.callable_uses_tk(source_probe.test_function))
                self.assertFalse(audit.callable_uses_tk(source_probe.test_logic))
            finally:
                sys.path.remove(str(folder))
                sys.modules.pop("source_probe", None)
                sys.modules.pop("helper_probe", None)

    def test_package_alias_requires_the_exact_already_loaded_helper_source(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as temporary:
            root = Path(temporary)
            (root / "tests").mkdir()
            helper = root / "tests" / "aliased_helper.py"
            helper.write_text("import tkinter as tk\ndef make_root(): return tk.Tk()\n")
            probe = root / "probe.py"
            probe.write_text("def probe():\n    from tests.aliased_helper import make_root\n    return make_root()\n")
            def load(name, path):
                spec = importlib.util.spec_from_file_location(name, path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                return module
            provider = load("aliased_helper", helper)
            consumer = load("alias_consumer", probe)
            with patch.object(audit, "ROOT", root), patch.dict(sys.modules, {"aliased_helper": provider}):
                self.assertTrue(audit.callable_uses_tk(consumer.probe))
                provider.__file__ = str(root / "different_source.py")
                with self.assertRaisesRegex(ValueError, "unresolved local imported helper"):
                    audit.callable_uses_tk(consumer.probe)

    def test_script_entrypoint_alias_requires_exact_loaded_sibling(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as temporary:
            root = Path(temporary)
            scripts = root / "scripts"
            scripts.mkdir()
            helper = scripts / "entry_helper.py"
            helper.write_text("import tkinter as tk\ndef make_root(): return tk.Tk()\n")
            probe = scripts / "entry_probe.py"
            probe.write_text("def probe():\n    from entry_helper import make_root\n    return make_root()\n")
            def load(name, path):
                spec = importlib.util.spec_from_file_location(name, path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                return module
            provider = load("scripts.entry_helper", helper)
            consumer = load("scripts.entry_probe", probe)
            with patch.object(audit, "ROOT", root), patch.dict(sys.modules, {"scripts.entry_helper": provider}):
                self.assertTrue(audit.callable_uses_tk(consumer.probe))
                provider.__file__ = str(root / "different_source.py")
                with self.assertRaisesRegex(ValueError, "unresolved local imported helper"):
                    audit.callable_uses_tk(consumer.probe)

    def test_real_collection_cli_tracks_aliases_and_rejects_unknown_callbacks(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as temporary:
            folder = Path(temporary)
            source = folder / "test_alias.py"
            (folder / "pure_source_helper.py").write_text("def dumps(value): return value\n")
            source.write_text("import tkinter as tk\ndef make_window(): return tk.Tk()\ndef pure(): return 3\ndef test_alias():\n    factory = make_window\n    nested_alias = factory\n    return nested_alias()\ndef test_pure():\n    factory = pure\n    return factory()\n")
            source.write_text(source.read_text() + "from contextlib import contextmanager\n@contextmanager\ndef manager():\n    def callback(): return tk.Tk()\n    yield callback\n@contextmanager\ndef pure_manager():\n    def callback(): return 2\n    yield callback\ndef invoke(callback): return callback()\ndef test_context():\n    with manager() as callback: return callback()\ndef test_pure_context():\n    with pure_manager() as callback: return callback()\ndef test_distinct_callbacks():\n    invoke(pure)\n    invoke(make_window)\n")
            source.write_text(source.read_text() + "def test_literal_pure():\n    for callback in [lambda: 3, pure]: callback()\ndef test_literal_tk():\n    for callback in (pure, make_window): callback()\n")
            source.write_text(source.read_text() + "def test_local_class():\n    class Value:\n        result = 3\n    return Value()\n")
            source.write_text(source.read_text() + "import pure_source_helper as json\nclass PureFactory:\n    @classmethod\n    def make(cls): return cls()\nclass TkFactory:\n    @classmethod\n    def make(cls): return tk.Tk()\ndef test_classmethod_pure():\n    factory = PureFactory.make\n    return factory()\ndef test_classmethod_tk(): return TkFactory.make()\ndef test_tuple_alias():\n    first, second = pure, make_window\n    first()\n    second()\ndef test_fixed_getattr_tk():\n    factory = getattr(tk, 'Tk')\n    return factory()\ndef test_fixed_getattr_pure():\n    factory = getattr(json, 'dumps')\n    return factory(1)\n")
            source.write_text(source.read_text() + "def use(callback=None): return callback()\ndef test_kwargs_tk():\n    kwargs = {'callback': make_window}\n    return use(**kwargs)\ndef test_kwargs_pure(): return use(**{'callback': pure})\n")
            source.write_text(source.read_text() + "def test_kwargs_conditional_tk(): return use(**({'callback': make_window} if globals()['flag'] else {}))\ndef test_kwargs_written_tk():\n    kwargs = {'callback': pure}\n    kwargs['callback'] = make_window\n    return use(**kwargs)\ndef test_kwargs_other_key_pure():\n    kwargs = {'callback': pure}\n    kwargs['unused'] = 1\n    return invoke_options(**kwargs)\ndef invoke_options(callback, **kwargs): return callback()\ndef test_known_prefix_tk(): return use(make_window, **globals()['kwargs'])\ndef test_nested_prefix_pure():\n    def inject(callback, *args, **kwargs): return callback(*args, **kwargs)\n    thunk = lambda *args, **kwargs: inject(pure, *args, **kwargs)\n    return thunk()\ndef test_nested_prefix_tk():\n    def inject(callback, *args, **kwargs): return callback(*args, **kwargs)\n    thunk = lambda *args, **kwargs: inject(make_window, *args, **kwargs)\n    return thunk()\n")
            source.write_text(source.read_text() + "def test_kwargs_alias_write_tk():\n    kwargs = {'callback': pure}\n    alias = kwargs\n    alias['callback'] = make_window\n    return use(**kwargs)\n")
            source.write_text(source.read_text() + "def invoke_kw(**kwargs): return kwargs['a']()\ndef invoke_pos(*args): return args[0]()\ndef test_key_order_tk():\n    invoke_kw(a=pure, b=make_window)\n    invoke_kw(a=make_window, b=pure)\ndef test_position_order_tk():\n    invoke_pos(pure, make_window)\n    invoke_pos(make_window, pure)\ndef test_unused_callback_pure():\n    invoke_kw(a=pure, b=make_window)\n    invoke_pos(pure, make_window)\n")
            output = folder / "inventory.json"
            registry = fixture_registration(folder, "test_alias",
                {identity.removeprefix("test_alias.") for identity in {"test_alias.test_alias", "test_alias.test_context", "test_alias.test_distinct_callbacks", "test_alias.test_literal_tk", "test_alias.test_classmethod_tk", "test_alias.test_tuple_alias", "test_alias.test_fixed_getattr_tk", "test_alias.test_kwargs_tk", "test_alias.test_kwargs_conditional_tk", "test_alias.test_kwargs_written_tk", "test_alias.test_known_prefix_tk", "test_alias.test_nested_prefix_tk", "test_alias.test_kwargs_alias_write_tk", "test_alias.test_key_order_tk", "test_alias.test_position_order_tk"}},
                {identity.removeprefix("test_alias.") for identity in {"test_alias.test_pure", "test_alias.test_pure_context", "test_alias.test_literal_pure", "test_alias.test_local_class", "test_alias.test_classmethod_pure", "test_alias.test_fixed_getattr_pure", "test_alias.test_kwargs_pure", "test_alias.test_kwargs_other_key_pure", "test_alias.test_nested_prefix_pure", "test_alias.test_unused_callback_pure"}})
            command = [sys.executable, str(ROOT / "scripts/test_entrypoint_audit.py"), "collect-groups", str(output), "--tests", str(folder), "--registry", str(registry)]
            result = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            inventory = json.loads(output.read_text())
            self.assertEqual(set(inventory["gui_ids"]), {"test_alias.test_alias", "test_alias.test_context", "test_alias.test_distinct_callbacks", "test_alias.test_literal_tk", "test_alias.test_classmethod_tk", "test_alias.test_tuple_alias", "test_alias.test_fixed_getattr_tk", "test_alias.test_kwargs_tk", "test_alias.test_kwargs_conditional_tk", "test_alias.test_kwargs_written_tk", "test_alias.test_known_prefix_tk", "test_alias.test_nested_prefix_tk", "test_alias.test_kwargs_alias_write_tk", "test_alias.test_key_order_tk", "test_alias.test_position_order_tk"})
            self.assertEqual(set(inventory["core_ids"]), {"test_alias.test_pure", "test_alias.test_pure_context", "test_alias.test_literal_pure", "test_alias.test_local_class", "test_alias.test_classmethod_pure", "test_alias.test_fixed_getattr_pure", "test_alias.test_kwargs_pure", "test_alias.test_kwargs_other_key_pure", "test_alias.test_nested_prefix_pure", "test_alias.test_unused_callback_pure"})
            unknowns = ["def invoke(**kwargs): return kwargs['callback']()\ndef test_alias(): return invoke(**globals()['kwargs'])\n",
                        "def invoke(*args): return args[0]()\ndef test_alias(): return invoke(*globals()['args'])\n",
                        "def use(callback=None): return callback()\ndef test_alias():\n    kwargs = {'callback': lambda: 1}\n    globals()['mutate'](kwargs)\n    return use(**kwargs)\n",
                        "def use(callback=None): return callback()\ndef test_alias():\n    kwargs = {'callback': lambda: 1}\n    kwargs[globals()['key']] = lambda: 2\n    return use(**kwargs)\n",
                        "def test_alias():\n    def inject(callback, *args, **kwargs): return callback(*args, **kwargs)\n    return inject(**globals()['kwargs'])\n",
                        "def use(callback=None): return callback()\ndef test_alias(): return use(**globals()['kwargs'])\n",
                        "def use(callback=None): return callback()\ndef test_alias(): return use(**{'callback': globals()['callback']})\n",
                        "def use(callback=None): return callback()\ndef test_alias():\n    kwargs = {'callback': lambda: 1}\n    kwargs['callback'] = globals()['callback']\n    return use(**kwargs)\n",
                        "def use(callback=None): return callback()\ndef test_alias(): return use(*globals()['args'])\n",
                        "def test_alias():\n    factory = globals()['callback']\n    return factory()\n",
                        "def invoke(callback): return callback()\ndef test_alias(): return invoke(object())\n",
                        "from contextlib import contextmanager\n@contextmanager\ndef manager(): yield globals()['callback']\ndef test_alias():\n    with manager() as callback: return callback()\n",
                        "def test_alias():\n    first = second\n    second = first\n    return first()\n",
                        "def test_alias():\n    for callback in globals()['callbacks']: callback()\n",
                        "def test_alias():\n    for callback in [lambda: 1, globals()['callback']]: callback()\n",
                        "def test_alias():\n    callbacks = [lambda: 1]\n    alias = callbacks\n    alias.append(globals()['callback'])\n    for callback in callbacks: callback()\n",
                        "def test_alias():\n    for name, callback in [('known', lambda: 1), globals()['row']]: callback()\n"]
            output.unlink()  # retain no stale success after the fixture source changes
            for index, body in enumerate(unknowns):
                with self.subTest(index=index):
                    source.write_text(body)
                    result = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=30)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(b"unresolved registration", result.stdout + result.stderr)

    def test_real_cli_instance_and_returned_callees_share_source_resolution(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as temporary:
            folder = Path(temporary)
            source = folder / "test_receiver.py"
            source.write_text("import tkinter as tk\nclass Helper:\n    def make_window(self): return tk.Tk()\n    def invoke(self, callback): return callback()\n    def pure(self): return 3\nclass Child(Helper): pass\ndef factory(): return tk.Tk\ndef pure(): return 4\ndef pure_factory(): return pure\ndef receiver_factory(): return Helper()\ndef test_instance():\n    helper = Helper()\n    return helper.make_window()\ndef test_method_alias():\n    helper = Helper()\n    factory = helper.make_window\n    return factory()\ndef test_bound_argument():\n    helper = Helper()\n    method = helper.invoke\n    return method(tk.Tk)\ndef test_returned(): return factory()()\ndef test_returned_alias():\n    callback = factory()\n    return callback()\ndef test_returned_receiver(): return receiver_factory().make_window()\ndef test_inherited(): return Child().make_window()\ndef test_pure_instance(): return Helper().pure()\ndef test_pure_return(): return pure_factory()()\ndef test_plain(): return 2 + 3\n")
            output = folder / "inventory.json"
            gui = {"test_instance", "test_method_alias", "test_bound_argument", "test_returned", "test_returned_alias", "test_returned_receiver", "test_inherited"}
            registry = fixture_registration(folder, "test_receiver", gui, {"test_pure_instance", "test_pure_return", "test_plain"})
            command = [sys.executable, str(ROOT / "scripts/test_entrypoint_audit.py"), "collect-groups", str(output), "--tests", str(folder), "--registry", str(registry)]
            result = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            inventory = json.loads(output.read_text())
            self.assertEqual(set(inventory["gui_ids"]), {"test_receiver." + name for name in gui})
            self.assertEqual(set(inventory["core_ids"]), {"test_receiver.test_pure_instance", "test_receiver.test_pure_return", "test_receiver.test_plain"})
            spec = importlib.util.spec_from_file_location("receiver_sentinel_probe", source)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            class ReachedTk(Exception): pass
            with patch.object(module.tk, "Tk", side_effect=ReachedTk("reached constructor sentinel")):
                for name in sorted(gui):
                    with self.subTest(name=name), self.assertRaises(ReachedTk):
                        getattr(module, name)()

    def test_real_cli_unknown_callable_shapes_and_protocols_fail_closed(self):
        bodies = [
            "def test_unknown(receiver): return receiver.method()\n",
            "def factory(): return globals()['callback']\ndef test_unknown(): return factory()()\n",
            "def test_unknown(): return globals()['factory']()()\n",
            "def test_unknown(): return getattr(globals()['receiver'], globals()['attribute'])()\n",
            "class Helper:\n    def pure(self): return 1\ndef test_unknown():\n    helper = Helper()\n    helper.pure = globals()['callback']\n    return helper.pure()\n",
            "class Meta(type):\n    def __call__(cls): return globals()['callback']()\nclass Helper(metaclass=Meta): pass\ndef test_unknown(): return Helper()\n",
            "class Helper:\n    def __new__(cls): return globals()['receiver']\ndef test_unknown(): return Helper().method()\n",
            "class Helper:\n    @property\n    def callback(self): return globals()['callback']\ndef test_unknown(): return Helper().callback()\n",
            "from pathlib import Path\ndef test_unknown(): return Path.cwd()\n",
            "exec(compile(\"\\n\" * 10000 + \"def generated(): return 3\\n\", __file__, \"exec\"))\ndef factory(): return generated()\ndef test_unknown(): return factory()()\n",
        ]
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as temporary:
            folder = Path(temporary)
            for index, body in enumerate(bodies):
                with self.subTest(index=index):
                    source = folder / "test_unknown.py"
                    source.write_text(body + "def test_control(): return 2 + 3\n")
                    registry = fixture_registration(folder, "test_unknown", set(), {"test_control"})
                    output = folder / (str(index) + ".json")
                    command = [sys.executable, str(ROOT / "scripts/test_entrypoint_audit.py"), "collect-groups", str(output), "--tests", str(folder), "--registry", str(registry)]
                    result = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=30)
                    self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertFalse(output.exists())
                    self.assertIn(b"unresolved", result.stdout + result.stderr)
                    self.assertIn(b"unregistered", result.stdout + result.stderr)
                    if "def generated" in body:
                        self.assertIn(b"test_unknown.test_unknown", result.stdout + result.stderr)

    def test_callable_binding_union_is_finite_and_keeps_distinct_targets(self):
        def pure(): return 1
        def possible_tk(): return 2  # Identity must differ even before source tracing.
        self.assertEqual(audit._callable_binding_key(pure), audit._callable_binding_key([[[pure]], pure]))
        self.assertNotEqual(audit._callable_binding_key(pure), audit._callable_binding_key([pure, possible_tk]))
        self.assertNotEqual(audit._callable_binding_key(pure), audit._callable_binding_key([pure, None]))
        self.assertNotEqual(audit._callable_binding_key({"a": pure, "b": possible_tk}), audit._callable_binding_key({"a": possible_tk, "b": pure}))
        self.assertNotEqual(audit._callable_binding_key((pure, possible_tk)), audit._callable_binding_key((possible_tk, pure)))
        self.assertEqual(audit._callable_binding_key({"a": (pure,)}), audit._callable_binding_key({"a": (pure,)}))
        self.assertNotEqual(audit._callable_binding_key("Tk"), audit._callable_binding_key("Tcl"))
        cycle = []; cycle.append(cycle)
        with self.assertRaisesRegex(ValueError, "cyclic"):
            audit._callable_binding_key(cycle)
        deep = (pure,)
        for _ in range(65):
            deep = (deep,)
        with self.assertRaisesRegex(ValueError, "depth"):
            audit._callable_binding_key(deep)
        nested = [pure]
        for _ in range(1200):
            nested = [nested]
        self.assertEqual(audit._callable_binding_key(nested), audit._callable_binding_key(pure))

    def test_bounded_alias_adapters_bind_sources_and_still_follow_target(self):
        import inspect
        import hashlib
        import tkinter
        import test_global_legacy_migration_v580 as native
        import test_legacy_migration_inspector_v580 as instance
        import global_exact_glyph_library as library
        owner = instance.MigrationInspectionTests
        caller = owner.test_one_plan_and_one_snapshot_with_post_snapshot_source_bracketing
        self.assertIs(audit._bounded_callable_alias(native.fixture_path_alias, None, "get_short_path"), audit._NATIVE_NON_TK_LEAF)
        self.assertIs(audit._bounded_callable_alias(caller, owner, "real"), library.GlobalExactGlyphRepository.load_snapshot)
        self.assertIs(audit._bounded_callable_alias(caller, owner(), "real"), library.GlobalExactGlyphRepository.load_snapshot)
        with patch.dict(audit.CALLABLE_ALIAS_SOURCE_HASHES, {"native_caller": "0" * 64}), self.assertRaisesRegex(ValueError, "source changed"):
            audit._bounded_callable_alias(native.fixture_path_alias, None, "get_short_path")
        with patch.object(library.GlobalExactGlyphRepository, "load_snapshot", tkinter.Tk), self.assertRaises(ValueError):
            audit._bounded_callable_alias(caller, owner, "real")
        with patch.object(library, "_load_snapshot_from_path", tkinter.Tk):
            self.assertTrue(audit.callable_uses_tk(caller, owner))
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as temporary:
            root = Path(temporary)
            (root / "tests").mkdir()
            path = root / "tests/test_global_legacy_migration_v580.py"
            path.write_text(inspect.getsource(native.fixture_path_alias).replace("GetShortPathNameW", "CreateWindowExW"))
            spec = importlib.util.spec_from_file_location("isolated_native_alias", path)
            probe = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(probe)
            expected = hashlib.sha256(inspect.getsource(probe.fixture_path_alias).encode()).hexdigest()
            with patch.object(audit, "ROOT", root), patch.dict(audit.CALLABLE_ALIAS_SOURCE_HASHES, {"native_caller": expected}), self.assertRaisesRegex(ValueError, "callsite changed"):
                audit._bounded_callable_alias(probe.fixture_path_alias, None, "get_short_path")
            path = root / "tests/test_legacy_migration_inspector_v580.py"
            path.write_text("class MigrationInspectionTests:\n" + inspect.getsource(caller).replace("self.repo.load_snapshot", "self.repo.load_inspection_snapshot"))
            spec = importlib.util.spec_from_file_location("isolated_instance_alias", path)
            probe = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(probe)
            changed = probe.MigrationInspectionTests.test_one_plan_and_one_snapshot_with_post_snapshot_source_bracketing
            expected = hashlib.sha256(inspect.getsource(changed).encode()).hexdigest()
            with patch.object(audit, "ROOT", root), patch.dict(audit.CALLABLE_ALIAS_SOURCE_HASHES, {"instance_caller": expected}), self.assertRaisesRegex(ValueError, "callsite changed"):
                audit._bounded_callable_alias(changed, owner, "real")

    def test_async_save_adapter_binds_initialization_and_recurses_into_real_service(self):
        import tkinter
        import test_review_async_save as fixture
        import review_save_service as service
        owner = fixture.AsyncReviewSaveTests
        caller = owner.test_worker_keeps_ui_unlocked_but_guards_all_mutating_actions_and_debounce
        target = audit._bounded_callable_alias(caller, owner(), "save")
        self.assertIs(target.__func__, service.ReviewSaveService.save_event)
        self.assertIs(target.__self__, service.ReviewSaveService)
        for key in ("async_caller", "async_setup", "headless", "queued_root", "reload_records", "invalidate_snapshot", "save_service", "save_target"):
            with self.subTest(key=key), patch.dict(audit.CALLABLE_ALIAS_SOURCE_HASHES, {key: "0" * 64}), self.assertRaisesRegex(ValueError, "source changed"):
                audit._bounded_callable_alias(caller, owner, "save")
        with patch.object(service.ReviewSaveService, "save_event", tkinter.Tk), self.assertRaises(ValueError):
            audit._bounded_callable_alias(caller, owner, "save")
        with patch.object(service.ReviewSaveService, "_save", tkinter.Tk):
            self.assertTrue(audit.callable_uses_tk(caller, owner))

    def test_busy_operation_adapter_rejects_guard_caller_and_method_drift(self):
        import test_review_async_save as fixture
        import review_gui as gui
        owner = fixture.AsyncReviewSaveTests
        caller = owner.test_worker_keeps_ui_unlocked_but_guards_all_mutating_actions_and_debounce
        self.assertIs(audit._bounded_callable_alias(caller, owner, "operation"), audit._BUSY_GUARDED_LEAF)
        keys = [key for key in audit.CALLABLE_ALIAS_SOURCE_HASHES if key.startswith("busy_")] + ["async_caller"]
        for key in keys:
            with self.subTest(key=key), patch.dict(audit.CALLABLE_ALIAS_SOURCE_HASHES, {key: "0" * 64}), self.assertRaisesRegex(ValueError, "source changed"):
                audit._bounded_callable_alias(caller, owner, "operation")
        with patch.object(gui.ReviewApp, "_save_busy", lambda self: False), self.assertRaises(ValueError):
            audit._bounded_callable_alias(caller, owner, "operation")
        with patch.object(gui.ReviewApp, "prev", gui.ReviewApp.next), self.assertRaises(ValueError):
            audit._bounded_callable_alias(caller, owner, "operation")

    def test_exact_mock_forwarding_adapter_preserves_actual_callbacks(self):
        import ast
        import tkinter
        import test_pdf_portability_binding as fixture
        sp = fixture.sp
        caller = fixture.test_bundle_actual_late_marker_prevents_commit
        node = ast.parse("original(*args, **kwargs)", mode="eval").body
        bindings = audit._bounded_forward_bindings(caller, node, sp.apply_direct_visual_actual_batch)
        self.assertIs(bindings["prewrite_guard"], sp._validate_actual_import_target)
        self.assertIs(bindings["apply_function"], sp.apply_verified_actual_group)
        self.assertIs(bindings["initialize_evidence"], sp.initialize_project_actual_evidence)
        for key in [key for key in audit.CALLABLE_ALIAS_SOURCE_HASHES if key.startswith("forward_")]:
            with self.subTest(key=key), patch.dict(audit.CALLABLE_ALIAS_SOURCE_HASHES, {key: "0" * 64}), self.assertRaisesRegex(ValueError, "source changed"):
                audit._bounded_forward_bindings(caller, node, sp.apply_direct_visual_actual_batch)
        with patch.object(sp, "initialize_project_actual_evidence", tkinter.Tk), self.assertRaises(ValueError):
            audit._bounded_forward_bindings(caller, node, sp.apply_direct_visual_actual_batch)
        with patch.object(sp, "json_load_strict", tkinter.Tk):
            self.assertTrue(audit.callable_uses_tk(bindings["prewrite_guard"]))
        self.assertEqual(audit._bounded_forward_bindings(caller, ast.parse("other(*args, **kwargs)", mode="eval").body, sp.apply_direct_visual_actual_batch), {})

    def test_local_imports_cannot_silently_become_core(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as temporary:
            path = Path(temporary) / "local_import_probe.py"
            path.write_text("def tk_test():\n    from tkinter import Tk\n    return Tk()\ndef unknown_helper():\n    from uncatalogued_helper import make_root\n    return make_root()\n")
            spec = importlib.util.spec_from_file_location("local_import_probe", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.assertTrue(audit.callable_uses_tk(module.tk_test))
            with self.assertRaisesRegex(ValueError, "unresolved local imported helper"):
                audit.callable_uses_tk(module.unknown_helper)



class RunnerCliTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "tmp").mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT / "tmp")
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.execution = self.folder / "execution"
        self.execution.mkdir()
        (self.execution / "temp").mkdir()
        self.test_path = self.folder / "test_isolated.py"

    def execute(self, body):
        self.test_path.write_text(body, encoding="utf-8")
        relative = self.test_path.relative_to(ROOT).as_posix()
        inventory = {"schema": "zhuyin-test-groups/2", "policy": runner.POLICY, "window_validation": audit.WINDOW_NOTICE, "pytest_ids": ["test_isolated.Case.test_probe"],
                     "core_ids": ["test_isolated.Case.test_probe"], "cancelled_window_ids": [],
                     "records": [{"identity": "test_isolated.Case.test_probe", "nodeid": relative + "::Case::test_probe", "group": "core"}]}
        runner.write_json(self.execution / "inventory.json", inventory)
        result = subprocess.run([sys.executable, str(ROOT / "scripts/validation_runner.py"), "_pytest",
                                 str(self.execution), "core"], cwd=ROOT, capture_output=True, timeout=30)
        return result, inventory

    def test_cli_success_counts_top_level_separately_from_subtests(self):
        result, inventory = self.execute("import unittest\nclass Case(unittest.TestCase):\n    def test_probe(self):\n        for i in range(2):\n            with self.subTest(i=i): self.assertLess(i, 2)\n")
        self.assertEqual(result.returncode, 0, result.stdout.decode())
        self.assertEqual(audit.verify_group(self.execution / "junit.xml", inventory, "core"), 1)
        self.assertEqual(runner.verify_events(self.execution / "events.jsonl", inventory, "core"), {"top_level": 1, "subtests": 2, "error": None})
        runner.junit_binding(self.execution / "junit.xml", "execution", runner.digest(self.execution / "inventory.json"))
        with self.assertRaisesRegex(ValueError, "stale"):
            runner.junit_binding(self.execution / "junit.xml", "other", runner.digest(self.execution / "inventory.json"))

    def test_cli_subtest_failure_is_retained_and_rejected(self):
        result, inventory = self.execute("import unittest\nclass Case(unittest.TestCase):\n    def test_probe(self):\n        with self.subTest(i=1): self.assertEqual(1, 2)\n")
        self.assertNotEqual(result.returncode, 0)
        events = (self.execution / "events.jsonl").read_text(encoding="utf-8")
        self.assertIn('"outcome": "failed"', events)
        self.assertIn("AssertionError", events)
        with self.assertRaises(ValueError):
            runner.verify_events(self.execution / "events.jsonl", inventory, "core")
        with self.assertRaises(ValueError):
            audit.verify_group(self.execution / "junit.xml", inventory, "core")

    def test_cli_skip_is_not_success(self):
        result, inventory = self.execute("import unittest\nclass Case(unittest.TestCase):\n    @unittest.skip('fixture')\n    def test_probe(self): pass\n")
        self.assertEqual(result.returncode, 0)
        with self.assertRaises(ValueError):
            audit.verify_group(self.execution / "junit.xml", inventory, "core")

    def test_cli_setup_error_and_failure_leave_raw_events(self):
        result, inventory = self.execute("import unittest\nclass Case(unittest.TestCase):\n    def setUp(self): raise RuntimeError('fixture setup failure')\n    def test_probe(self): pass\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("fixture setup failure", (self.execution / "events.jsonl").read_text())
        with self.assertRaises(ValueError):
            audit.verify_group(self.execution / "junit.xml", inventory, "core")

    def test_missing_partial_or_wrong_junit_is_rejected(self):
        result, inventory = self.execute("import unittest\nclass Case(unittest.TestCase):\n    def test_probe(self): pass\n")
        self.assertEqual(result.returncode, 0)
        report = self.execution / "junit.xml"
        report.write_text("<testsuites><testsuite><testcase")
        with self.assertRaises(ET.ParseError):
            audit.verify_group(report, inventory, "core")
        report.unlink()
        with self.assertRaises(FileNotFoundError):
            runner.junit_binding(report, "execution", "missing")

    def test_interrupt_keeps_reported_failure_even_without_junit(self):
        result, inventory = self.execute("import os\nimport unittest\nclass Case(unittest.TestCase):\n    def test_probe(self):\n        with self.subTest(i=1): self.assertEqual(1, 2)\n        os._exit(7)\n")
        self.assertEqual(result.returncode, 7)
        self.assertFalse((self.execution / "junit.xml").exists())
        self.assertIn("AssertionError", (self.execution / "events.jsonl").read_text())

    def test_write_and_process_log_are_exclusive(self):
        runner.write_json(self.execution / "once.json", {"original": True})
        with self.assertRaises(FileExistsError):
            runner.write_json(self.execution / "once.json", {})
        (self.execution / "run.log").write_text("original")
        with self.assertRaises(FileExistsError):
            runner.run_process([sys.executable, "-c", "print('changed')"], self.execution, "run.log", 1)
        self.assertEqual((self.execution / "run.log").read_text(), "original")

    def test_gui_retry_requires_original_link_and_only_one_retry(self):
        first = {"retry_key": "key", "group": "gui", "started_at": "1", "retry_of": None,
                 "execution_id": "first", "retry_eligible": True, "code_blockers": False, "outcome": "blocked"}
        second = {**first, "started_at": "2", "retry_of": "first", "execution_id": "second", "outcome": "success"}
        runner.retry_history([(Path("one"), first), (Path("two"), second)], "key")
        for values in ([second], [first, second, {**second, "execution_id": "third"}],
                       [{**first, "retry_eligible": False}, second], [{**first, "code_blockers": True}, second]):
            with self.assertRaises(ValueError):
                runner.retry_history([(Path(str(i)), value) for i, value in enumerate(values)], "key")

    def test_only_specific_initialization_resource_failure_is_retryable(self):
        runner.write_json(self.execution / "preflight.json", {"stage": "tk_initialization"})
        report = self.execution / "preflight.xml"
        preflight = {"outcome": "failed"}
        for message, expected in (("_tkinter.TclError: couldn't read file \"C:/Tk/combobox.tcl\": permission denied", True),
                                  ("_tkinter.TclError: invalid command name", False),
                                  ("AssertionError: bad save", False)):
            root = ET.Element("testsuite")
            case = ET.SubElement(root, "testcase")
            ET.SubElement(case, "failure", message=message)
            ET.ElementTree(root).write(report)
            self.assertEqual(runner.retry_eligible(self.execution, {}, preflight), expected)

    def test_development_cli_cannot_start_formal_group(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/validation_runner.py"), "run", "--group", "core", "--mode", "development", "--evidence-root", str(self.execution)], cwd=ROOT, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"explicit --mode validation", result.stderr)


if __name__ == "__main__":
    unittest.main()

class RunnerOrchestrationTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "tmp").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "tmp")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.evidence = self.folder / "evidence"
        test_path = self.folder / "test_pair.py"
        test_path.write_text("def test_core(): assert 2 + 3 == 5\n")
        relative = test_path.relative_to(ROOT).as_posix()
        self.inventory = {"schema": "zhuyin-test-groups/2", "policy": runner.POLICY,
                          "window_validation": audit.WINDOW_NOTICE,
                          "pytest_ids": ["test_pair.test_core"], "core_ids": ["test_pair.test_core"],
                          "cancelled_window_ids": ["test_pair.test_native"],
                          "records": [{"identity": "test_pair.test_core", "nodeid": relative + "::test_core", "group": "core"}]}
        self.candidate = {"head": "a" * 40, "tree": "b" * 40, "parents": ["c" * 40, "d" * 40]}
        self.real_run = ORIGINAL_RUN_PROCESS
        self.calls = []
        # These are explicit isolated fixture boundaries, never formal Windows CI.
        self.patches = [patch.dict("os.environ", {"GITHUB_ACTIONS": "true"}),
                        patch.object(runner, "validate_environment"),
                        patch.object(runner, "validate_history_context", return_value={"task_id": "fixture-task"}),
                        patch.object(runner, "snapshot", return_value=self.candidate),
                        patch.object(runner, "git", return_value=""),
                        patch.object(runner, "collect_inventory", side_effect=self.collect),
                        patch.object(runner, "run_process", side_effect=self.process)]
        for context in self.patches:
            context.start()
            self.addCleanup(context.stop)

    def collect(self, folder, tests=None):
        runner.write_json(folder / "inventory.json", self.inventory)
        (folder / "collection.log").write_text("isolated fixture inventory\n")
        return self.inventory

    def process(self, command, folder, name, timeout, env=None):
        self.calls.append(name)
        self.assertNotIn("gui_preflight.py", " ".join(command))
        return self.real_run(command, folder, name, timeout, env)

    def test_core_only_run_and_aggregate_need_no_gui_or_preflight(self):
        core = runner.run_group("core", self.evidence, mode="validation")
        original = core.read_bytes()
        combined = runner.aggregate(self.evidence)
        self.assertEqual(combined["schema"], "zhuyin-validation-coverage/2")
        self.assertEqual(combined["window_validation"], audit.WINDOW_NOTICE)
        self.assertNotIn("gui_manifest", combined)
        self.assertEqual(runner.run_group("core", self.evidence, mode="validation"), core)
        self.assertEqual(self.calls, ["raw.log"])
        self.assertEqual(core.read_bytes(), original)
        self.assertFalse(list(self.evidence.rglob("preflight*")))

    def test_gui_launcher_and_child_refuse_before_any_process(self):
        with self.assertRaisesRegex(ValueError, "取消"):
            runner.run_group("gui", self.evidence, mode="validation")
        with self.assertRaisesRegex(ValueError, "取消"):
            runner.pytest_child(self.folder, "gui")
        self.assertEqual(self.calls, [])
        self.assertFalse(self.evidence.exists())

    def test_new_candidate_preserves_old_evidence_without_relabelling(self):
        old_core = runner.run_group("core", self.evidence, mode="validation")
        original = old_core.read_bytes()
        self.candidate.update(head="e" * 40, tree="f" * 40)
        new_core = runner.run_group("core", self.evidence, mode="validation")
        bundle = runner.aggregate(self.evidence, head="e" * 40)
        self.assertEqual(bundle["core_manifest"], new_core.relative_to(self.evidence).as_posix())
        self.assertEqual(old_core.read_bytes(), original)
        self.assertEqual(len(bundle["history_manifests"]), 2)

    def test_core_failure_cannot_be_cleared_by_same_candidate_rerun(self):
        def fail(command, folder, name, timeout, env=None):
            (folder / "raw.log").write_text("AssertionError: isolated code failure")
            return {"exit_code": 1, "outcome": "failed", "started_at": runner.utc_now(),
                    "finished_at": runner.utc_now(), "runner_start_error": None}
        self.real_run = fail
        failed = runner.run_group("core", self.evidence, mode="validation")
        self.assertEqual(runner.read_verified_manifest(failed)["outcome"], "failed")
        with self.assertRaisesRegex(ValueError, "core failure remains unresolved"):
            runner.run_group("core", self.evidence, mode="validation")
        with self.assertRaisesRegex(ValueError, "unresolved"):
            runner.aggregate(self.evidence)

    def test_incomplete_history_and_corrupt_artifact_fail_closed(self):
        core = runner.run_group("core", self.evidence, mode="validation")
        (core.parent / "raw.log").write_text("changed")
        with self.assertRaisesRegex(ValueError, "corrupt"):
            runner.history(self.evidence)
        incomplete = self.evidence / "unfinished"
        incomplete.mkdir()
        runner.write_json(incomplete / "started.json", {})
        with self.assertRaisesRegex(ValueError, "unfinished"):
            runner.history(self.evidence)


    def test_current_manifest_cannot_claim_preflight_or_gui_success(self):
        core = runner.run_group("core", self.evidence, mode="validation")
        data = runner.read_json(core)
        for field, value in (("group", "gui"), ("preflight", {"outcome": "success"}),
                             ("policy", "future"), ("retry_eligible", True)):
            changed = dict(data, **{field: value})
            core.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "policy"):
                runner.read_verified_manifest(core)
        core.write_text(json.dumps(data), encoding="utf-8")

    def test_original_false_manifest_rejects_new_classifier_without_rewriting_history(self):
        # Construct retained raw files only. Never invoke the retired GUI launcher.
        from test_validation_evidence import raw_bundle, ref, write
        raw_bundle(self.folder)
        folder = self.folder / "gui"
        manifest = folder / "manifest.json"
        data = runner.read_json(manifest)
        write_retry_suite(folder, tk_initializer_wrapper(), "tests/test_small.py::test_gui")
        data.update(outcome="failed", exit_code=1, retry_eligible=False)
        for key in ("junit", "events"):
            data["artifacts"][key] = ref(folder / data["artifacts"][key]["path"])
        write(manifest, data)
        original = {path: path.read_bytes() for path in folder.iterdir() if path.is_file()}
        with patch.object(runner, "retry_eligible", return_value=False):
            self.assertFalse(runner.read_verified_manifest(manifest)["retry_eligible"])
        self.assertTrue(runner.retry_eligible(folder, {}, {"outcome": "success"}))
        with self.assertRaisesRegex(ValueError, "retry eligibility differs from original raw evidence"):
            runner.read_verified_manifest(manifest)
        self.assertEqual(original, {path: path.read_bytes() for path in folder.iterdir() if path.is_file()})


class HistoryRetrievalTests(unittest.TestCase):
    def test_prior_attempt_missing_artifact_fails_cli_without_reset(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            event = folder / "event.json"
            runner.write_json(event, {"pull_request": {"number": 99, "base": {"sha": "b" * 40}, "head": {"sha": "a" * 40, "ref": "task-branch"}}})
            responses = [{"workflow_runs": [{"id": 9, "status": "in_progress", "run_attempt": 2, "head_sha": "a" * 40,
                                             "head_repository": {"full_name": "owner/repo"}, "pull_requests": [{"number": 99}]}], "total_count": 1},
                         {"artifacts": [], "total_count": 0}]
            with patch.object(runner, "import_local_handoff", return_value={"task_id": "fixture-task", "archive_sha256": "f" * 64}), patch.object(runner, "snapshot", return_value={"head": "c" * 40, "tree": "d" * 40, "parents": ["b" * 40, "a" * 40]}), patch.dict("os.environ", {"GITHUB_EVENT_PATH": str(event), "GITHUB_REPOSITORY": "owner/repo",
                                            "GITHUB_RUN_ID": "9", "GITHUB_RUN_ATTEMPT": "2"}), patch.object(runner.subprocess, "check_output", side_effect=[json.dumps(value) for value in responses]) as query:
                self.assertEqual(runner.main(["restore-history", "--evidence-root", str(folder / "history")]), 2)
                self.assertIn("branch=task-branch", query.call_args_list[0].args[0][-1])
                self.assertNotIn("head_sha=", query.call_args_list[0].args[0][-1])

    def test_retry_suite_requires_initializer_trace_not_arbitrary_tclerror(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            message = '_tkinter.TclError: couldn\'t read file "C:/Tk/fonts.tcl": permission denied'
            root = ET.Element("testsuite")
            case = ET.SubElement(root, "testcase", classname="tests.test_probe.Probe", name="test_root")
            event = {"nodeid": "tests/test_probe.py::Probe::test_root", "outcome": "failed", "when": "call", "subtest": False, "traceback": "setUp\n self.tk = _tkinter.create()\n" + message}
            ET.SubElement(case, "failure", message=message).text = event["traceback"]
            ET.ElementTree(root).write(folder / "junit.xml")
            (folder / "events.jsonl").write_text(json.dumps(event) + "\n")
            self.assertTrue(runner.retry_eligible(folder, {}, {"outcome": "success"}))
            event["traceback"] = "save_data\n" + message
            (folder / "events.jsonl").write_text(json.dumps(event) + "\n")
            self.assertFalse(runner.retry_eligible(folder, {}, {"outcome": "success"}))

class CFFLegacyFailureReadTests(unittest.TestCase):
    """Synthetic raw files only; fixture digest substitution never proves provenance."""

    def setUp(self):
        from test_validation_evidence import NoWindowEvidenceTests, ref, write
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ref, self.write = ref, write
        NoWindowEvidenceTests().new_bundle(self.root)
        old = self.root / "gui"
        self.folder = self.root / "0a56d940d31c4b48865cbda948494a7a"
        old.rename(self.folder)
        self.manifest = self.folder / "manifest.json"
        data = runner.read_json(self.manifest)
        data.update(execution_id=self.folder.name, outcome="failed", exit_code=1,
                    candidate={"head": "4453a35dc7d66b9d9a2f0cca43f49087b4a4cdbc",
                               "tree": "d216214e0ff371486e2a0e34cae8332b3fc2918c",
                               "parents": ["8eab9a1de34b59115658a2c7d3565adb347e59ab",
                                           "431b43be38d655b51e58a81ceaa2caff141672e7"]},
                    ci={"run_id": 37293413665, "attempt": 1, "job": "grouped", "event": "pull_request"})
        data["command"][3] = str(self.folder)
        write_retry_suite(self.folder, tk_initializer_wrapper(), "tests/test_small.py::test_gui")
        self.rebind(self.manifest, data, runner.CFF_HISTORY_TASK)
        core = self.root / "core/manifest.json"
        self.rebind(core, runner.read_json(core), runner.CFF_HISTORY_TASK)
        self.fixture_hash = runner.digest(self.manifest)
        self.kw = {"history_policy": runner.POLICY, "task_id": runner.CFF_HISTORY_TASK}

    def rebind(self, manifest, data, task):
        folder = manifest.parent
        context = runner.read_json(folder / "history-context.json")
        context["task_id"] = task
        self.write(folder / "history-context.json", context)
        data["retry_key"] = runner.canonical_digest({"task": task, "tree": data["candidate"]["tree"]})
        self.write(folder / "started.json", data)
        data["artifacts"] = {key: self.ref(folder / entry["path"]) for key, entry in data["artifacts"].items()}
        self.write(manifest, data)

    def test_frozen_false_read_is_opt_in_immutable_and_not_retry_authority(self):
        originals = {p: p.read_bytes() for p in self.folder.iterdir()}
        # The production digest refuses these invented records.
        with self.assertRaises(ValueError):
            runner.read_verified_manifest(self.manifest, **self.kw)
        with patch.object(runner, "CFF_LEGACY_FAILURE_SHA256", self.fixture_hash):
            data = runner.read_verified_manifest(self.manifest, **self.kw)
            self.assertEqual((data["outcome"], data["exit_code"], data["retry_eligible"]), ("failed", 1, False))
            for kwargs in ({}, dict(self.kw, task_id="bbox"), dict(self.kw, history_policy="future")):
                with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                    runner.read_verified_manifest(self.manifest, **kwargs)
            second = dict(data, execution_id="second", retry_of=data["execution_id"])
            with self.assertRaisesRegex(ValueError, "illegal GUI retry"):
                runner.retry_history([(self.manifest, data), (Path("second"), second)], data["retry_key"])
        self.assertEqual(originals, {p: p.read_bytes() for p in self.folder.iterdir()})

    def test_byte_integrity_unknown_fields_and_identity_fail_closed(self):
        original = self.manifest.read_bytes()
        changes = [{"outcome": "success"}, {"retry_eligible": True}, {"retry_of": "prior"},
                   {"code_blockers": True}, {"schema": "future"}, {"extra": True},
                   {"ci": {"run_id": 37293413665, "attempt": 2, "job": "grouped", "event": "pull_request"}},
                   {"candidate": {"head": "a" * 40, "tree": "b" * 40, "parents": []}}]
        with patch.object(runner, "CFF_LEGACY_FAILURE_SHA256", self.fixture_hash):
            for change in changes:
                with self.subTest(change=change):
                    self.write(self.manifest, dict(json.loads(original), **change))
                    with self.assertRaises(ValueError):
                        runner.read_verified_manifest(self.manifest, **self.kw)
            self.manifest.write_bytes(b'{"schema":"duplicate",' + original[1:])
            with self.assertRaisesRegex(ValueError, "duplicate"):
                runner.read_verified_manifest(self.manifest, **self.kw)
            self.manifest.write_bytes(original)
            for artifact in runner.read_json(self.manifest)["artifacts"].values():
                target = self.folder / artifact["path"]
                payload = target.read_bytes()
                with self.subTest(artifact=target.name):
                    target.write_bytes(payload + b"modified")
                    with self.assertRaisesRegex(ValueError, "corrupt"):
                        runner.read_verified_manifest(self.manifest, **self.kw)
                    target.unlink()
                    with self.assertRaisesRegex(ValueError, "corrupt"):
                        runner.read_verified_manifest(self.manifest, **self.kw)
                    target.write_bytes(payload)

    def test_history_aggregate_and_coverage_keep_failure_and_require_current_core(self):
        import shutil
        from scripts import validation_evidence
        with patch.object(runner, "CFF_LEGACY_FAILURE_SHA256", self.fixture_hash):
            shutil.copytree(self.folder, self.root / "duplicate")
            values = runner.history(self.root, policy=runner.POLICY)
            self.assertEqual(len(values), 2)
            self.assertEqual(sum(v["outcome"] == "failed" for _, v in values), 1)
            bundle = runner.aggregate(self.root)
            self.write(self.root / "coverage.json", bundle)
            self.assertEqual(validation_evidence.verify_coverage(self.root / "coverage.json"), bundle)
            self.assertEqual(bundle["schema"], runner.COVERAGE_SCHEMA)
            self.assertEqual(bundle["core_manifest"], "core/manifest.json")
            with self.assertRaises(ValueError):
                runner.aggregate(self.root, legacy=True)
            core = self.root / "core/manifest.json"
            data = runner.read_json(core)
            self.rebind(core, data, "other-task")
            with self.assertRaisesRegex(ValueError, "task scope"):
                runner.aggregate(self.root)
            self.rebind(core, data, runner.CFF_HISTORY_TASK)
            shutil.rmtree(core.parent)
            with self.assertRaisesRegex(ValueError, "missing core"):
                runner.aggregate(self.root)
            # Even a fully valid old core cannot provide new /2 coverage.
            from test_validation_evidence import raw_bundle
            other = self.root / "old-bundle"
            other.mkdir()
            raw_bundle(other)
            shutil.rmtree(other / "gui")
            old_core = other / "core/manifest.json"
            self.rebind(old_core, runner.read_json(old_core), runner.CFF_HISTORY_TASK)
            with self.assertRaisesRegex(ValueError, "missing core"):
                runner.aggregate(self.root)

    def test_restore_history_binds_exact_pr_task_and_preserves_all_bundles(self):
        import shutil
        event = self.root / "event.json"
        current = {"head": "a" * 40, "tree": "b" * 40, "parents": ["c" * 40, "d" * 40]}
        self.write(event, {"pull_request": {"number": 42, "base": {"sha": "c" * 40},
                   "head": {"sha": "d" * 40, "ref": "codex/cff-batch-fingerprint"}}})
        runs = [{"id": 999, "head_sha": "d" * 40, "run_attempt": 1},
                {"id": 37293413665, "head_sha": "431b43be38d655b51e58a81ceaa2caff141672e7",
                 "run_attempt": 2, "status": "completed", "head_repository": {"full_name": runner.REPOSITORY},
                 "pull_requests": [{"number": 42}]}]
        artifacts = [{"name": f"validation-evidence-37293413665-{n}", "expired": False} for n in (1, 2)]
        def download(command, **kwargs):
            destination = Path(command[-1])
            shutil.copytree(self.folder, destination / self.folder.name)
        env = {"GITHUB_EVENT_PATH": str(event), "GITHUB_REPOSITORY": runner.REPOSITORY,
               "GITHUB_RUN_ID": "999", "GITHUB_RUN_ATTEMPT": "1",
               "GITHUB_JOB": "grouped", "GITHUB_EVENT_NAME": "pull_request"}
        with patch.object(runner, "CFF_LEGACY_FAILURE_SHA256", self.fixture_hash), patch.dict("os.environ", env), \
                patch.object(runner, "snapshot", return_value=current), \
                patch.object(runner, "import_local_handoff", return_value={"task_id": runner.CFF_HISTORY_TASK, "archive_sha256": "f" * 64}), \
                patch.object(runner.subprocess, "check_output", side_effect=[json.dumps({"workflow_runs": runs, "total_count": 2}), json.dumps({"artifacts": artifacts, "total_count": 2})]), \
                patch.object(runner.subprocess, "run", side_effect=download):
            output = self.root / "restored"
            runner.restore_history(output)
            index = runner.read_json(next(output.glob("history-index-*.json")))
            self.assertEqual(index["executions"], [self.folder.name])
            self.assertEqual(len(list(output.rglob("manifest.json"))), 2)
            with self.assertRaisesRegex(ValueError, "missing core"):
                runner.aggregate(output)
            from scripts import validation_evidence
            shutil.copytree(self.root / "core", output / "core")
            bundle = runner.aggregate(output)
            self.write(output / "coverage.json", bundle)
            self.assertEqual(validation_evidence.verify_coverage(output / "coverage.json"), bundle)
            self.assertEqual(len(bundle["history_manifests"]), 2)
        # Wrong PR/branch/task must not opt in, even with the same retained files.
        for field, value in (("number", 43), ("branch", "codex/other"), ("task", "other-task"),
                             ("repository", "other/repo")):
            changed_event = runner.read_json(event)
            changed_env = dict(env)
            task = runner.CFF_HISTORY_TASK
            if field == "number":
                changed_event["pull_request"]["number"] = value
            elif field == "branch":
                changed_event["pull_request"]["head"]["ref"] = value
            elif field == "task":
                task = value
            else:
                changed_env["GITHUB_REPOSITORY"] = value
            self.write(event, changed_event)
            prior = dict(runs[1], head_repository={"full_name": changed_env["GITHUB_REPOSITORY"]},
                         pull_requests=[{"number": changed_event["pull_request"]["number"]}])
            with self.subTest(field=field), patch.object(runner, "CFF_LEGACY_FAILURE_SHA256", self.fixture_hash), \
                    patch.dict("os.environ", changed_env), patch.object(runner, "snapshot", return_value=current), \
                    patch.object(runner, "import_local_handoff", return_value={"task_id": task, "archive_sha256": "f" * 64}), \
                    patch.object(runner.subprocess, "check_output", side_effect=[json.dumps({"workflow_runs": [runs[0], prior], "total_count": 2}), json.dumps({"artifacts": artifacts, "total_count": 2})]), \
                    patch.object(runner.subprocess, "run", side_effect=download):
                with self.assertRaises(ValueError):
                    runner.restore_history(self.root / ("rejected-" + field))
            self.write(event, {"pull_request": {"number": 42, "base": {"sha": "c" * 40},
                       "head": {"sha": "d" * 40, "ref": "codex/cff-batch-fingerprint"}}})


class Pr43PostmergeHistoryTests(unittest.TestCase):
    """Finite synthetic API/archive fixtures; never real CI or review evidence."""

    def setUp(self):
        import base64
        import hashlib
        import io
        import zipfile
        from test_validation_evidence import raw_bundle, ref, write
        self.write, self.ref = write, ref
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="p7r-")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.source = self.folder / "source"
        self.source.mkdir()
        raw_bundle(self.source)
        self.spec = copy.deepcopy(runner.PR43_POSTMERGE_HISTORY)
        # Deliberately retained failure with complete raw metadata; not PASS.
        for manifest in self.source.rglob("manifest.json"):
            value = runner.read_json(manifest)
            context = runner.read_json(manifest.parent / "history-context.json")
            context["task_id"] = self.spec["task_id"]
            write(manifest.parent / "history-context.json", context)
            value["retry_key"] = runner.canonical_digest({"task": self.spec["task_id"], "tree": value["candidate"]["tree"]})
            if value["group"] == "core":
                value.update(outcome="failed", exit_code=1)
            write(manifest.parent / "started.json", value)
            value["artifacts"] = {key: ref(manifest.parent / entry["path"]) for key, entry in value["artifacts"].items()}
            write(manifest, value)
        ledger = dict(schema=runner.LOCAL_SCHEMA, task_id=self.spec["task_id"],
                      baseline=self.spec["baseline"], repository=runner.REPOSITORY,
                      source_ref=self.spec["original_source_ref"],
                      created_at="2026-10-09T00:00:00+00:00", store_id="6" * 32)
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as zipped:
            zipped.writestr("ledger.json", json.dumps(ledger))
            for name in ("executions", "declarations"):
                zipped.writestr(name + "/store.json", json.dumps({"schema": "zhuyin-local-store/1", "store_id": ledger["store_id"]}))
        self.handoff = dict(schema=runner.HANDOFF_SCHEMA, task_id=self.spec["task_id"],
            source_ref=ledger["source_ref"], archive_sha256=hashlib.sha256(payload.getvalue()).hexdigest(),
            archive_base64=base64.b64encode(payload.getvalue()).decode())
        self.old_block = json.dumps(self.handoff)
        self.spec["original_archive_sha256"] = self.handoff["archive_sha256"]
        self.spec["original_history_block_sha256"] = hashlib.sha256(self.old_block.encode()).hexdigest()
        self.old_pr = {"number": 43, "merged": True, "state": "closed", "merge_commit_sha": self.spec["original_merge"],
            "head": {"sha": self.spec["original_head"], "ref": runner.PR43_ORIGINAL_BRANCH,
                     "repo": {"full_name": runner.REPOSITORY}},
            "base": {"sha": self.spec["original_merge_parents"][0], "ref": "develop", "repo": {"full_name": runner.REPOSITORY}},
            "body": "Preserved original description\n<!-- validation-local-history\n" + self.old_block + "\n-->"}
        self.head = "a" * 40
        self.metadata = {**self.spec, "current_pr": 57, "head": self.head}
        self.event_data = {"pull_request": {"number": 57,
            "head": {"sha": self.head, "ref": self.spec["branch"], "repo": {"full_name": runner.REPOSITORY}},
            "base": {"sha": self.spec["original_merge"], "ref": "develop", "repo": {"full_name": runner.REPOSITORY}}}}
        self.event = self.folder / "event.json"
        self.save_event()
        self.candidate = {"head": "b" * 40, "tree": "c" * 40, "parents": [self.spec["original_merge"], self.head]}
        self.env = {"GITHUB_EVENT_PATH": str(self.event), "GITHUB_REPOSITORY": runner.REPOSITORY,
                    "GITHUB_RUN_ID": "900", "GITHUB_RUN_ATTEMPT": "1"}
        self.new_runs = [self.make_run(900, self.head, self.spec["branch"], 57, "in_progress"),
                         self.make_run(901, "d" * 40, self.spec["branch"], 57, "completed", attempts=2)]
        self.old_runs = [self.make_run(runner.PR43_ORIGINAL_FULL_RUN, self.spec["original_head"], runner.PR43_ORIGINAL_BRANCH, 43, "completed"),
                         self.make_run(37789520018, "e" * 40, runner.PR43_ORIGINAL_BRANCH, 43, "completed")]
        self.old_runs[1]["conclusion"] = "failure"
        self.push = self.make_run(runner.PR43_ORIGINAL_PUSH_RUN, self.spec["original_merge"], "develop", 43, "completed")
        self.push["event"] = "push"
        self.short_folder = self.folder / "short-original"
        self.short_folder.mkdir()
        (self.short_folder / "proof.log").write_text("synthetic preserved original raw log", encoding="utf-8")
        from scripts import validation_evidence as evidence
        short_env = {"python": "3.13.0", "platform": "Windows", "capture": "fd",
                     "environment": {key: "" for key in runner.ENV_KEYS}, "dependencies": {}}
        for line in (ROOT / "requirements-ci-lock.txt").read_text().splitlines():
            if line.strip() and not line.startswith("#"):
                key, version = line.split("==")
                short_env["dependencies"][key.lower().replace("_", "-")] = version
        write(self.short_folder / "started.json", {"started_at": "2026-10-09T00:00:00+00:00", "command": ["synthetic", "post-merge"]})
        write(self.short_folder / "short.json", {"schema": evidence.SHORT_SCHEMA, "policy": runner.POLICY,
              "window_validation": audit.WINDOW_NOTICE, "outcome": "success", "environment": short_env,
              "candidate": {"head": self.spec["original_merge"], "parents": self.spec["original_merge_parents"], "tree": self.spec["original_merge_tree"]},
              "ci": {"run_id": runner.PR43_ORIGINAL_PUSH_RUN, "attempt": 1, "job": "short", "event": "push"},
              "coverage_sha256": runner.digest(self.source / "coverage.json"),
              "source": {key: ref(self.short_folder / "proof.log") for key in ("archive", "artifact", "assets", "commit", "pr", "pr_assets", "run", "runs")},
              "checks": [{"name": name, "command": command, "exit_code": 0, "log": ref(self.short_folder / "proof.log")}
                         for name, command in evidence.short_commands(self.spec["original_merge_parents"][0], self.spec["original_merge"])]})
        push_stream = io.BytesIO()
        with zipfile.ZipFile(push_stream, "w", zipfile.ZIP_DEFLATED) as zipped:
            for path in self.source.rglob("*"):
                if path.is_file():
                    zipped.write(path, path.relative_to(self.source).as_posix())
            for path in self.short_folder.iterdir():
                zipped.write(path, "short-synthetic/" + path.name)
        self.push_zip = push_stream.getvalue()
        self.api_calls = []
        self.missing_artifact = None
        self.corrupt_download = None
        self.conflicting_execution = False
        self.patches = [patch.dict(runner.PR43_POSTMERGE_HISTORY, self.spec),
            patch.object(runner, "PR43_ORIGINAL_PUSH_ARCHIVE_SHA256", hashlib.sha256(self.push_zip).hexdigest()),
            patch.object(runner, "PR43_ORIGINAL_SHORT_SHA256", runner.digest(self.short_folder / "short.json")),
            patch.object(runner, "PR43_ORIGINAL_COVERAGE_SHA256", runner.digest(self.source / "coverage.json")),
            patch.object(runner, "snapshot", return_value=self.candidate),
            patch.object(runner, "git", side_effect=self.git),
            patch.dict("os.environ", self.env),
            patch.object(runner.subprocess, "check_output", side_effect=self.query),
            patch.object(runner.subprocess, "run", side_effect=self.download)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def make_run(self, identity, head, branch, pr, status, attempts=1):
        return {"id": identity, "head_sha": head, "head_branch": branch, "run_attempt": attempts,
                "event": "pull_request", "status": status, "conclusion": "success",
                "head_repository": {"full_name": runner.REPOSITORY}, "pull_requests": [{"number": pr}]}

    def save_event(self):
        self.event_data["pull_request"]["body"] = "<!-- validation-postmerge-continuation\n" + json.dumps(self.metadata) + "\n-->"
        self.write(self.event, self.event_data)

    def git(self, *args):
        identity = args[-1]
        if args[2] == "--format=%P":
            return self.spec["original_merge"] if identity == self.head else " ".join(self.spec["original_merge_parents"])
        return self.candidate["tree"] if identity == self.head else self.spec["original_merge_tree"]

    def query(self, command, **kwargs):
        response = self.api(command[-1])
        return response if isinstance(response, bytes) else json.dumps(response)

    def api(self, endpoint):
        self.api_calls.append(endpoint)
        if endpoint == "repos/discoveryray/zhuyin-proofreader/actions/artifacts/11595410764/zip":
            return self.push_zip
        if endpoint == "repos/discoveryray/zhuyin-proofreader/pulls/43":
            return self.old_pr
        if endpoint == f"repos/discoveryray/zhuyin-proofreader/actions/runs/{runner.PR43_ORIGINAL_PUSH_RUN}":
            return self.push
        if "/artifacts?" in endpoint:
            identity = int(endpoint.split("/runs/", 1)[1].split("/", 1)[0])
            attempts = 2 if identity == 901 else 1
            items = [{"name": f"validation-evidence-{identity}-{attempt}", "expired": False,
                      "id": runner.PR43_ORIGINAL_PUSH_ARTIFACT,
                      "digest": "sha256:" + runner.PR43_ORIGINAL_PUSH_ARCHIVE_SHA256}
                     for attempt in range(1, attempts + 1) if (identity, attempt) != self.missing_artifact]
            return {"total_count": len(items), "artifacts": items}
        from urllib.parse import parse_qs, urlsplit
        branch = parse_qs(urlsplit(endpoint).query)["branch"][0]
        rows = self.new_runs if branch == self.spec["branch"] else self.old_runs
        return {"total_count": len(rows), "workflow_runs": rows}

    def download(self, command, **kwargs):
        import shutil
        destination = Path(command[-1])
        shutil.copytree(self.source, destination)
        if self.corrupt_download:
            target = destination / self.corrupt_download
            if target.is_file():
                target.write_bytes(target.read_bytes() + b"corrupt")
        if self.conflicting_execution and command[-3] == "validation-evidence-901-2":
            manifest = destination / "core/manifest.json"
            value = runner.read_json(manifest)
            value["finished_at"] = "2026-10-01T00:00:02+00:00"
            self.write(manifest, value)
            # Both records pass the strict reader; their identity conflict is
            # independently rejected rather than silently selecting the last.
            runner.read_verified_manifest(manifest)
        return subprocess.CompletedProcess(command, 0)

    def restore(self, name="restored"):
        root = self.folder / name
        runner.restore_history(root)
        return root, runner.read_json(next(root.glob("history-index-*.json")))

    def test_dual_pr_runs_attempts_and_original_failed_bytes_are_retained(self):
        original = {p.relative_to(self.source): p.read_bytes() for p in self.source.rglob("*") if p.is_file()}
        # This is the exact body available before creation; the opened event
        # assigns the real number. A later body edit cannot repair this snapshot.
        self.metadata["current_pr"] = "event.pull_request.number"
        self.event_data["action"] = "opened"
        self.save_event()
        opened_snapshot = self.event.read_bytes()
        root, index = self.restore()
        self.assertEqual(index["executions"], ["core", "gui"])
        self.assertEqual(len(list((root / "history").iterdir())), 5)
        self.assertEqual(len(runner.history(root)), 2)
        self.assertEqual(sum(value["outcome"] == "failed" for _, value in runner.history(root)), 1)
        self.assertEqual({row["run_id"] for row in index["postmerge_continuation"]["origins"]}, {900, 901, runner.PR43_ORIGINAL_FULL_RUN, 37789520018, runner.PR43_ORIGINAL_PUSH_RUN})
        for retained in (root / "history").iterdir():
            if retained.name.endswith(str(runner.PR43_ORIGINAL_PUSH_RUN) + "-1"):
                import zipfile
                raw = retained / "pr-artifact.zip"
                self.assertEqual(raw.read_bytes(), self.push_zip)
                with zipfile.ZipFile(raw) as zipped:
                    self.assertEqual(zipped.read("short-synthetic/started.json"), (self.short_folder / "started.json").read_bytes())
                    for relative, payload in original.items():
                        self.assertEqual(zipped.read(relative.as_posix()), payload)
                self.assertFalse((retained / "short-synthetic/started.json").exists())
            else:
                for relative, payload in original.items():
                    self.assertEqual((retained / relative).read_bytes(), payload)
        self.assertEqual(runner.read_local_ledger(root / "local-history", self.spec["task_id"])["baseline"], self.spec["baseline"])
        self.assertNotIn("archive_base64", self.event_data["pull_request"]["body"])
        self.assertEqual(self.api_calls.count("repos/discoveryray/zhuyin-proofreader/pulls/43"), 1)
        self.assertTrue(any("branch=codex%2Factual-gpt-bbox-export&" in endpoint for endpoint in self.api_calls))
        self.assertEqual(index["postmerge_continuation"]["reference"]["current_pr"], 57)
        self.assertIs(type(index["postmerge_continuation"]["reference"]["current_pr"]), int)
        # The ordinary explicit identity is still exact, while an arbitrary
        # future PR number works only when the event and current run agree.
        self.new_runs[0]["pull_requests"] = [{"number": 58}]
        self.new_runs[1]["pull_requests"] = [{"number": 58}]
        self.event_data["pull_request"]["number"] = 58
        self.save_event()
        self.new_runs[0]["pull_requests"] = []  # legitimate API omission; exact event/raw run still bind
        self.old_runs[1]["pull_requests"] = []  # legitimate retained old API history
        integration_root, creation = self.restore("tmp/validation-evidence")
        self.assertEqual(creation["postmerge_continuation"]["reference"]["current_pr"], 58)
        snapshot_bytes = self.event.read_bytes()
        from scripts import validation_evidence
        pure = self.folder / "test_creation.py"
        pure.write_text("def test_creation(): assert 2 + 3 == 5\n", encoding="utf-8")
        nodeid = pure.relative_to(ROOT).as_posix() + "::test_creation"
        inventory = {"schema": "zhuyin-test-groups/2", "policy": runner.POLICY,
            "window_validation": audit.WINDOW_NOTICE, "pytest_ids": ["test_creation.test_creation"],
            "core_ids": ["test_creation.test_creation"], "cancelled_window_ids": [],
            "records": [{"identity": "test_creation.test_creation", "nodeid": nodeid, "group": "core"}]}
        (self.folder / "requirements-ci-lock.txt").write_bytes((ROOT / "requirements-ci-lock.txt").read_bytes())
        def collect(folder, tests=None):
            self.write(folder / "inventory.json", inventory)
            (folder / "collection.log").write_text("isolated creation snapshot inventory\n", encoding="utf-8")
            return inventory
        def process(command, folder, name, timeout, env=None):
            # Execute the genuine headless pytest child from the repository;
            # only API/Git/inventory boundaries are synthetic in this fixture.
            with patch.object(runner, "ROOT", ROOT):
                return ORIGINAL_RUN_PROCESS(command, folder, name, timeout, env)
        def git(*args):
            return "" if args == ("status", "--porcelain") else self.git(*args)
        with patch.object(runner, "ROOT", self.folder), patch.object(runner, "git", side_effect=git), \
                patch.object(runner, "collect_inventory", side_effect=collect), \
                patch.object(runner, "run_process", side_effect=process), \
                patch.dict("os.environ", GITHUB_ACTIONS="true", GITHUB_EVENT_NAME="pull_request", GITHUB_JOB="core"):
            context = runner.validate_history_context(integration_root, self.candidate)
            self.assertEqual(context["task_id"], self.spec["task_id"])
            self.assertEqual(context["postmerge_continuation"]["reference"]["current_pr"], 58)
            manifest = runner.run_group("core", integration_root, mode="validation", timeout=30)
            selected = runner.read_verified_manifest(manifest)
            self.assertEqual(selected["outcome"], "success", selected["verification"])
            self.assertEqual(selected["ci"], {"run_id": 900, "attempt": 1, "job": "core", "event": "pull_request"})
            saved_context = runner.read_json(manifest.parent / "history-context.json")
            self.assertEqual(saved_context["postmerge_continuation"]["reference"]["current_pr"], 58)
            coverage = runner.aggregate(integration_root, head=self.candidate["head"])
            output = integration_root / "coverage.json"
            self.write(output, coverage)
            self.assertEqual(validation_evidence.verify_coverage(output), coverage)
            self.assertEqual(sum(value["outcome"] == "failed" for _, value in runner.history(integration_root)), 1)
        self.assertEqual(self.event.read_bytes(), snapshot_bytes)
        self.assertIn('"current_pr": "event.pull_request.number"', json.loads(opened_snapshot)["pull_request"]["body"])
        self.assertNotIn("archive_base64", self.event_data["pull_request"]["body"])

    def test_real_pins_refuse_synthetic_handoff(self):
        with patch.dict(runner.PR43_POSTMERGE_HISTORY,
                        original_history_block_sha256="6baf58dcf47f2726c1f4c83fb66e48afd5b4af29a78dbf9186a5fdcc7d184d88",
                        original_archive_sha256="322ddf29015c63ed646733af9f6c937f0ae4807120f347d135649b6d8ae6660e"):
            with self.assertRaises(ValueError):
                self.restore()
        self.assertFalse((self.folder / "restored/local-history").exists())

    def test_wrong_identity_hash_schema_count_and_unknown_fields_refuse(self):
        original = copy.deepcopy(self.metadata)
        for key, value in (("task_id", "other"), ("baseline", "f" * 40), ("authorization_sha256", "0" * 64),
                           ("original_merge", "f" * 40), ("original_merge_parents", list(reversed(self.spec["original_merge_parents"]))),
                           ("original_merge_tree", "f" * 40), ("original_source_ref", "unknown"),
                           ("p1_report_sha256", "0" * 64), ("sixth_state_sha256", "0" * 64),
                           ("schema", "validation-postmerge-continuation/2"), ("corrective_count", 8), ("corrective_count", 7.0),
                           ("current_pr", True), ("current_pr", 43), ("current_pr", 58),
                           ("current_pr", None), ("current_pr", "event.pull_request.number.other"),
                           ("current_pr", {"event": "pull_request.number"}), ("head", "0" * 40), ("extra", True)):
            self.metadata = {**original, key: value}
            self.save_event()
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.restore("bad-" + key + "-" + str(len(list(self.folder.glob("bad-*")))))
        self.metadata = {**original, "current_pr": "event.pull_request.number"}
        for number in (True, None, 0, 42, 43, 57.0, "57"):
            self.event_data["pull_request"]["number"] = number
            self.save_event()
            with self.subTest(event_number=number), self.assertRaises(ValueError):
                runner.pr43_postmerge_handoff(self.event_data, self.candidate, self.api, runner.REPOSITORY)
        self.event_data["pull_request"]["number"] = 57
        self.metadata = original

    def test_current_scope_and_single_corrective_parent_are_required(self):
        original = copy.deepcopy(self.event_data)
        for side, key, value in (("head", "ref", "codex/other"), ("base", "sha", "d" * 40),
                                 ("head", "repo", {"full_name": "other/repo"}), ("base", "ref", "main")):
            self.event_data = copy.deepcopy(original)
            self.event_data["pull_request"][side][key] = value
            self.save_event()
            with self.subTest(side=side, key=key), self.assertRaises(ValueError):
                runner.pr43_postmerge_handoff(self.event_data, self.candidate, self.api, runner.REPOSITORY)
        self.event_data = original
        self.save_event()
        for parents in ([], [self.spec["original_merge"], "d" * 40], ["d" * 40]):
            def wrong_git(*args):
                return " ".join(parents) if args[-1] == self.head and args[2] == "--format=%P" else self.git(*args)
            with self.subTest(parents=parents), patch.object(runner, "git", side_effect=wrong_git), self.assertRaisesRegex(ValueError, "parents/tree"):
                self.restore("parent-" + str(len(parents)))

    def test_changed_missing_duplicate_old_block_and_corrupt_archive_refuse(self):
        original = self.old_pr["body"]
        for index, body in enumerate(("", original + original, original.replace(self.old_block, self.old_block.replace('"schema":', '"schema" :')))):
            self.old_pr["body"] = body
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.restore("block-" + str(index))
        self.old_pr["body"] = original
        self.handoff["archive_base64"] = "corrupt"
        self.old_block = json.dumps(self.handoff)
        self.spec["original_history_block_sha256"] = __import__("hashlib").sha256(self.old_block.encode()).hexdigest()
        runner.PR43_POSTMERGE_HISTORY["original_history_block_sha256"] = self.spec["original_history_block_sha256"]
        self.metadata = {**self.spec, "current_pr": 57, "head": self.head}
        self.old_pr["body"] = "<!-- validation-local-history\n" + self.old_block + "\n-->"
        self.save_event()
        with self.assertRaises((ValueError, __import__("binascii").Error)):
            self.restore("corrupt-archive")

    def test_original_merge_and_full_ci_identity_or_missing_history_refuse(self):
        old = copy.deepcopy(self.old_pr)
        self.old_pr["merge_commit_sha"] = "f" * 40
        with self.assertRaises(ValueError):
            self.restore("wrong-merge")
        self.old_pr = old
        for index, rows in enumerate(([], [self.old_runs[1]], [dict(self.old_runs[0], run_attempt=2), self.old_runs[1]])):
            with patch.object(self, "old_runs", rows), self.subTest(index=index), self.assertRaises(ValueError):
                self.restore("full-" + str(index))
        for key, value in (("head_sha", "f" * 40), ("event", "pull_request"), ("conclusion", "failure"), ("run_attempt", 2)):
            with patch.dict(self.push, {key: value}), self.subTest(key=key), self.assertRaises(ValueError):
                self.restore("push-" + key)

    def test_missing_expired_and_duplicate_attempt_artifacts_refuse(self):
        for identity, attempt in ((901, 1), (901, 2), (runner.PR43_ORIGINAL_FULL_RUN, 1), (runner.PR43_ORIGINAL_PUSH_RUN, 1)):
            self.missing_artifact = (identity, attempt)
            with self.subTest(identity=identity, attempt=attempt), self.assertRaisesRegex(ValueError, "history unavailable"):
                self.restore("missing-" + str(identity) + "-" + str(attempt))
        self.missing_artifact = None
        original_api = self.api
        for name in ("expired", "duplicate", "identity", "digest"):
            def changed_api(endpoint):
                response = original_api(endpoint)
                if "/artifacts?" in endpoint:
                    if name == "expired":
                        response["artifacts"][0]["expired"] = True
                    elif name == "duplicate":
                        response["artifacts"].append(dict(response["artifacts"][0]))
                        response["total_count"] += 1
                    elif name == "identity":
                        response["artifacts"][0]["id"] = 0
                    else:
                        response["artifacts"][0]["digest"] = "sha256:" + "0" * 64
                return response
            with patch.object(self, "api", side_effect=changed_api), self.subTest(name=name), self.assertRaises(ValueError):
                self.restore(name)

    def test_unfinished_wrong_scope_and_duplicate_runs_refuse(self):
        original_runs = copy.deepcopy(self.new_runs)
        for links in ({}, None, [{"number": True}], [{"number": 58}],
                      [{"number": 57}, {"number": 57}], [{"number": 57}, {"number": 58}]):
            self.new_runs = copy.deepcopy(original_runs)
            self.new_runs[0]["pull_requests"] = links
            with self.subTest(current_pr_links=links), self.assertRaises(ValueError):
                self.restore("links-" + str(len(list(self.folder.glob("links-*")))))
        self.new_runs = copy.deepcopy(original_runs)
        self.new_runs[0].pop("pull_requests")
        with self.assertRaises(ValueError):
            self.restore("missing-current-pr-links")
        for links in ([{"number": 57}, {"number": 58}], [{"number": 58}], [{"number": True}]):
            self.new_runs = copy.deepcopy(original_runs)
            self.new_runs[1]["pull_requests"] = links
            with self.subTest(prior_pr_links=links), self.assertRaises(ValueError):
                self.restore("prior-links-" + str(len(list(self.folder.glob("prior-links-*")))))
        self.new_runs = original_runs
        changes = [dict(self.new_runs[1], status="in_progress"), dict(self.new_runs[1], head_branch="foreign"),
                   dict(self.new_runs[1], head_repository={"full_name": "other/repo"}),
                   dict(self.new_runs[1], pull_requests=[{"number": 58}]), dict(self.new_runs[0])]
        for index, row in enumerate(changes):
            with patch.object(self, "new_runs", [self.new_runs[0], row]), self.subTest(index=index), self.assertRaises(ValueError):
                self.restore("run-" + str(index))

    def test_corrupt_manifest_short_coverage_and_raw_receipts_refuse(self):
        for index, path in enumerate(("core/raw.log", "coverage.json")):
            self.corrupt_download = path
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.restore("bytes-" + str(index))
        self.corrupt_download = None
        import io, zipfile, hashlib
        original_zip = self.push_zip
        for mode in ("short", "proof", "unknown_started", "missing_closure", "duplicate_member", "unsafe_member", "zip"):
            changed = io.BytesIO()
            if mode == "zip":
                self.push_zip = b"not a zip archive"
            else:
                with zipfile.ZipFile(io.BytesIO(original_zip)) as source, zipfile.ZipFile(changed, "w", zipfile.ZIP_DEFLATED) as target:
                    for name in source.namelist():
                        if mode == "missing_closure" and name == "core/raw.log":
                            continue
                        data = source.read(name)
                        if mode == "short" and name == "short-synthetic/short.json":
                            data += b"corrupt"
                        if mode == "proof" and name == "short-synthetic/proof.log":
                            data += b"corrupt"
                        target.writestr(name, data)
                    if mode == "unknown_started":
                        target.writestr("unknown/started.json", "{}")
                    if mode == "duplicate_member":
                        target.writestr("core/raw.log", b"duplicate")
                    if mode == "unsafe_member":
                        target.writestr("../unknown.json", "{}")
                self.push_zip = changed.getvalue()
            with patch.object(runner, "PR43_ORIGINAL_PUSH_ARCHIVE_SHA256", hashlib.sha256(self.push_zip).hexdigest()), self.subTest(mode=mode), self.assertRaises((ValueError, zipfile.BadZipFile)):
                self.restore("zip-" + mode)
        self.push_zip = original_zip
        self.conflicting_execution = True
        with self.assertRaisesRegex(ValueError, "conflicting retained execution"):
            self.restore("conflicting-execution")

    def test_no_reference_or_mixed_archive_never_bootstraps_continuation(self):
        self.event_data["pull_request"]["body"] = ""
        self.write(self.event, self.event_data)
        with self.assertRaisesRegex(ValueError, "exactly one bounded PR43"):
            self.restore("missing-reference")
        self.save_event()
        self.event_data["pull_request"]["body"] += self.old_pr["body"]
        self.write(self.event, self.event_data)
        with self.assertRaisesRegex(ValueError, "without a new archive"):
            self.restore("mixed-archive")
