"""File-backed PR coverage and actual-merge short validation (no GUI/full suite).

GitHub metadata still needs independent review; hashes prove retained bytes,
not the authenticity of a reviewer or authorization.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
import re
from pathlib import Path, PurePosixPath
import subprocess
import sys
import urllib.parse
import urllib.request
import uuid
import zipfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "discoveryray/zhuyin-proofreader"
SHORT_SCHEMA = "zhuyin-merge-short/2"
LEGACY_SHORT_SCHEMA = "zhuyin-merge-short/1"
CURRENT_POLICY = "validation-flow/current-evidence/1"
CURRENT_COVERAGE_SCHEMA = "zhuyin-validation-coverage/3"
CURRENT_SHORT_SCHEMA = "zhuyin-merge-short/3"
WINDOW_NOTICE = "cancelled; not GUI PASS"


def _identity(candidate):
    require(set(candidate) == {"head", "tree", "parents"}, "malformed checkout identity")
    require(all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value)
                for value in [candidate["head"], candidate["tree"], *candidate["parents"]]), "incomplete checkout SHA identity")
    require(isinstance(candidate["parents"], list) and len(candidate["parents"]) == 2 and
            len(set([candidate["head"], *candidate["parents"]])) == 3, "checkout requires ordered merge parents")


def _locked_environment(environment, lock):
    required = {}
    for line in lock.read_text(encoding="utf-8-sig").splitlines():
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_-]+)==([0-9.]+)", line)
        require(match is not None, "unfrozen validation dependency")
        name = re.sub(r"[-_.]+", "-", match[1]).lower()
        require(name not in required, "duplicate locked dependency")
        required[name] = match[2]
    require(required == {"colorama": "0.4.6", "et-xmlfile": "2.0.0", "fonttools": "4.63.0",
            "iniconfig": "2.3.0", "lxml": "6.1.3", "openpyxl": "3.1.5", "packaging": "26.3",
            "pluggy": "1.6.0", "pygments": "2.21.0", "pymupdf": "1.26.7", "pytest": "9.1.1",
            "python-docx": "1.2.0", "typing-extensions": "4.16.0", "pip": "24.2"}, "unknown current validation lock")
    require(environment["python"] == "3.13.0" and environment["platform"] == "Windows" and
            environment["capture"] == "fd" and environment["dependencies"] == required,
            "current evidence requires the exact Windows Python/dependency/capture environment")
    require(environment["environment"] == {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
            "PYTEST_ADDOPTS": "", "TCL_LIBRARY": "", "TK_LIBRARY": ""}, "unexpected critical validation environment")


def current_commands(event):
    """Canonical commands executed by the normal full-validation route."""
    diff = ["git", "diff", "--check"]
    return {
        "pip": ["<python>", "-m", "pip", "install", "--disable-pip-version-check", "pip==24.2"],
        "dependencies": ["<python>", "-m", "pip", "install", "--disable-pip-version-check", "-r", "requirements-ci-lock.txt"],
        "runtime": ["<python>", "-m", "pytest", "tests/test_runtime_asset_manifest_integrity_v562.py", "-q"],
        "inventory": ["<python>", "scripts/test_entrypoint_audit.py", "collect", "tmp/ci-test-inventory.json"],
        "pytest": ["<python>", "-m", "pytest", "tests/", "-q", "-rs", "--junitxml=tmp/ci-pytest.xml"],
        "active": ["<python>", "scripts/test_entrypoint_audit.py", "verify-active", "tmp/ci-test-inventory.json", "--junit", "tmp/ci-pytest.xml"],
        "compile": ["<python>", "-m", "compileall", "-q", "-x", r"(^|[\\/])(\.venv|tmp)([\\/]|$)", "."],
        "diff": diff,
        "clean": ["git", "status", "--porcelain"],
    }


def verify_current_coverage(path, *, _record=None):
    """Verify only this execution; older CI artifacts are never prerequisites."""
    path = Path(path)
    bundle = load_json(path) if _record is None else _record
    require(set(bundle) == {"schema", "policy", "window_validation", "status", "repository", "candidate",
            "ci", "environment", "checkout", "artifacts", "checks"}, "unknown current coverage fields")
    require(bundle["schema"] == CURRENT_COVERAGE_SCHEMA and bundle["policy"] == CURRENT_POLICY and
            bundle["window_validation"] == WINDOW_NOTICE and bundle["status"] == "success", "unknown or unsuccessful current coverage")
    require(bundle["repository"] == REPOSITORY, "wrong current repository")
    candidate, ci = bundle["candidate"], bundle["ci"]
    _identity(candidate)
    require(ci["event"] in {"pull_request", "push"}, "unsupported current event")
    _ci_identity(ci, ci["event"])
    require(ci["job"] == "test", "current full coverage requires original test job")
    checkout = bundle["checkout"]
    if ci["event"] == "pull_request":
        require(set(checkout) == {"number", "repository", "base_repository", "head_repository", "base_ref", "head_ref", "base_sha", "head_sha"}, "unknown PR checkout fields")
        require(type(checkout["number"]) is int and checkout["number"] > 0 and checkout["base_ref"] in {"develop", "main"} and
                isinstance(checkout["head_ref"], str) and bool(checkout["head_ref"]) and
                checkout["base_sha"] == candidate["parents"][0] and checkout["head_sha"] == candidate["parents"][1] and
                checkout["repository"] == checkout["base_repository"] == REPOSITORY and
                isinstance(checkout["head_repository"], str) and bool(checkout["head_repository"]), "PR checkout does not bind original ordered parents/repository")
    else:
        require(checkout == {"repository": REPOSITORY, "ref": "refs/heads/main", "before": candidate["parents"][0]}, "main push checkout mismatch")
    refs = bundle["artifacts"]
    require(set(refs) == {"inventory", "junit", "environment", "lock", "registry", "event", "assets"}, "missing/unknown current artifacts")
    files = {name: _artifact(path.parent, value) for name, value in refs.items()}
    environment = load_json(files["environment"])
    require(environment == {"schema": "zhuyin-current-validation-environment/1", "policy": CURRENT_POLICY,
            "window_validation": WINDOW_NOTICE, "environment": bundle["environment"], "candidate": candidate,
            "ci": ci, "checkout": checkout}, "environment original identity differs")
    _locked_environment(bundle["environment"], files["lock"])
    require(load_json(files["event"])["checkout"] == checkout and load_json(files["event"])["ci"] == ci,
            "original event binding differs")
    audit = runner_module().audit
    inventory, registry = load_json(files["inventory"]), load_json(files["registry"])
    audit.validate_groups(inventory)
    require(inventory["schema"] == "zhuyin-test-groups/2", "current coverage needs active non-window inventory")
    entries = audit.expand_registry(registry)
    require(set(inventory["core_ids"]) == {key for key, group in entries.items() if group == "core"} and
            set(inventory["cancelled_window_ids"]) == {key for key, group in entries.items() if group == "gui"}, "current inventory omitted registered identities")
    require(inventory["registration"] == {"registry_sha256": sha256_file(files["registry"]),
            "source_review": registry["source_review"], "source_scope": registry["source_scope"]}, "inventory registry original differs")
    try:
        audit.verify_group(files["junit"], inventory, "core")
    except ET.ParseError as exc:
        raise ValueError("corrupt current JUnit: " + str(exc)) from exc
    expected = current_commands(ci["event"])
    expected["diff"] += [checkout["base_sha"] + "..." + checkout["head_sha"] if ci["event"] == "pull_request"
                         else checkout["before"] + ".." + candidate["head"]]
    require([item["name"] for item in bundle["checks"]] == list(expected), "missing/duplicate current command evidence")
    for item in bundle["checks"]:
        require(set(item) == {"name", "command", "exit_code", "log"} and item["exit_code"] == 0 and
                _canonical_command(item["command"]) == expected[item["name"]], "changed or failed current command")
        raw = _artifact(path.parent, item["log"]).read_text(encoding="utf-8").splitlines()
        require(bool(raw) and json.loads(raw[0], object_pairs_hook=unique_object) == {"argv": item["command"]} and
                raw[-1] == "exit_code=0" and sum(line.startswith("exit_code=") for line in raw) == 1, "raw current command/exit differs")
        if item["name"] == "clean":
            require(not "\n".join(raw[1:-1]).strip(), "current checkout was dirty")
    return bundle


def produce_current_coverage(evidence_root, output):
    root, output = Path(evidence_root), Path(output)
    environment = load_json(root / "main-full/environment.json")
    require(environment["schema"] == "zhuyin-current-validation-environment/1", "missing current environment capture")
    snapshot = runner_module().snapshot()
    require(snapshot == environment["candidate"] and runner_module().environment() == environment["environment"], "source/environment changed during current validation")
    require(not runner_module().git("status", "--porcelain"), "current validation source is dirty")
    require(output.parent.resolve() == root.resolve(), "coverage must share finite artifact root")
    files = {"inventory": root / "ci-test-inventory.json", "junit": root / "ci-pytest.xml",
             "environment": root / "main-full/environment.json"}
    for name, source in (("lock", "requirements-ci-lock.txt"), ("registry", "scripts/test_group_registry.json"),
                         ("assets", "runtime_asset_manifest.json")):
        destination = root / "main-full" / (name + ".json" if name != "lock" else "lock.txt")
        payload = subprocess.check_output(["git", "cat-file", "--filters", snapshot["head"] + ":" + source], cwd=ROOT)
        require(payload == (ROOT / source).read_bytes(), "current source differs from committed checkout: " + source)
        destination.write_bytes(payload)
        files[name] = destination
    event = root / "main-full/event.json"
    _write_json(event, {"ci": environment["ci"], "checkout": environment["checkout"]})
    files["event"] = event
    commands = current_commands(environment["ci"]["event"])
    commands["diff"] += [environment["checkout"]["base_sha"] + "..." + environment["checkout"]["head_sha"]
        if environment["ci"]["event"] == "pull_request" else environment["checkout"]["before"] + ".." + snapshot["head"]]
    checks = []
    for name in commands:
        log = root / "main-full" / (name + ".log")
        first = json.loads(log.read_text(encoding="utf-8").splitlines()[0], object_pairs_hook=unique_object)
        checks.append({"name": name, "command": first["argv"], "exit_code": 0, "log": _reference(root, log)})
    bundle = {"schema": CURRENT_COVERAGE_SCHEMA, "policy": CURRENT_POLICY, "window_validation": WINDOW_NOTICE,
              "status": "success", "repository": REPOSITORY, "candidate": snapshot, "ci": environment["ci"],
              "environment": environment["environment"], "checkout": environment["checkout"],
              "artifacts": {key: _reference(root, value) for key, value in files.items()}, "checks": checks}
    verified = verify_current_coverage(output, _record=bundle)
    _write_json(output, verified)
    return verified


def require(condition, message):
    if not condition:
        raise ValueError(message)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"), object_pairs_hook=unique_object,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def local_path(root, reference):
    require(isinstance(reference, str) and reference, "missing artifact path")
    path = (root / reference).resolve()
    require(path.is_relative_to(root.resolve()) and path != root.resolve(), "artifact path escapes evidence root")
    return path


def runner_module():
    try:
        from scripts import validation_runner
    except ModuleNotFoundError:
        import validation_runner
    return validation_runner


def _ci_identity(ci, event):
    require(set(ci) == {"run_id", "attempt", "job", "event"}, "malformed CI identity")
    require(all(type(ci[key]) is int and ci[key] > 0 for key in ("run_id", "attempt")) and
            type(ci["job"]) is str and bool(ci["job"].strip()) and ci["event"] == event,
            "missing original CI run/attempt/job/event identity")


def verify_coverage(path):
    """Recompute the bundle from every retained execution, including failures."""
    path = Path(path)
    bundle = load_json(path)
    if bundle.get("schema") == CURRENT_COVERAGE_SCHEMA:
        return verify_current_coverage(path)
    require(bundle.get("schema") in {"zhuyin-validation-coverage/1", "zhuyin-validation-coverage/2"}, "unknown coverage schema")
    legacy = bundle["schema"].endswith("/1")
    selected = [bundle["core_manifest"], bundle["gui_manifest"]] if legacy else [bundle["core_manifest"]]
    if not legacy:
        require(bundle.get("policy") == runner_module().POLICY and bundle.get("window_validation") == runner_module().audit.WINDOW_NOTICE,
                "unknown coverage validation policy")
    require(bundle.get("status") == "success", "coverage is not successful")
    for reference in [*selected, *bundle["history_manifests"]]:
        local_path(path.parent, reference)
    # aggregate reads log/JUnit/inventory hashes and enforces complete history,
    # one-success-per-identity, exact environment and the bounded GUI retry.
    try:
        actual = runner_module().aggregate(path.parent, head=bundle["candidate"]["head"], ci=bundle["ci"], legacy=legacy)
    except ET.ParseError as exc:
        raise ValueError("corrupt raw JUnit: " + str(exc)) from exc
    require(actual == bundle, "coverage does not match retained raw execution history")
    environment = bundle["environment"]
    require(environment["python"] == "3.13.0" and environment["platform"] == "Windows",
            "formal coverage requires Windows Python 3.13.0")
    require(environment["capture"] == "fd", "new coverage contract requires default capture")
    require(bool(environment["dependencies"]), "missing actual dependency versions")
    _ci_identity(bundle["ci"], "pull_request")
    # Imported local attempts remain in aggregate's retry/failure history, but
    # only original PR executions may supply formal functional coverage.
    for reference in selected:
        original = runner_module().read_verified_manifest(local_path(path.parent, reference))
        _ci_identity(original["ci"], "pull_request")
    return bundle


def _artifact(root, value):
    require(set(value) == {"path", "sha256"}, "malformed artifact reference")
    path = local_path(root, value["path"])
    require(sha256_file(path) == value["sha256"], f"artifact hash mismatch: {value['path']}")
    return path


def _archive_matches(archive_path, extracted_root):
    with zipfile.ZipFile(archive_path) as archive:
        seen = set()
        for item in archive.infolist():
            require(item.filename not in seen, "duplicate source archive path")
            seen.add(item.filename)
            require("\\" not in item.filename and not PurePosixPath(item.filename).is_absolute(), "invalid source archive path")
            target = local_path(extracted_root, item.filename)
            if not item.is_dir():
                require(target.read_bytes() == archive.read(item), "retained evidence differs from source archive: " + item.filename)


def _environment_equivalent(left, right):
    # Exact captured environment; no implicit dependency/command equivalence.
    require(left == right, "actual Python/dependency/capture/environment differs from PR coverage")


def short_commands(base, merge):
    return [
        ("runtime", [sys.executable, "-c", "from pathlib import Path; import json; from runtime_source_validation import validate_asset_manifest; r=validate_asset_manifest(Path.cwd()); print(json.dumps(r,ensure_ascii=False)); assert r['ok'] and not r['errors'] and not r['warnings'] and r['required_asset_roster_ok'] and r['required_asset_chain_roster_ok']=={'actual':True,'expected':True}"]),
        ("import", [sys.executable, "-c", "import standalone_proofread; import review_gui; print('non-window imports completed')"]),
        ("cli", [sys.executable, "standalone_proofread.py", "--help"]),
        ("compile", [sys.executable, "-m", "compileall", "-q", "-x", r"(^|[\\/])(\.venv|tmp)([\\/]|$)", "."]),
        ("diff", ["git", "diff", "--check", f"{base}..{merge}"]),
        ("clean", ["git", "status", "--porcelain"]),
    ]


def _canonical_command(command):
    return ["<python>", *command[1:]] if command and Path(command[0]).name.lower() in ("python", "python.exe") else command


def verify_reuse(coverage_path, short_path, *, _record=None, current_only=False):
    coverage = verify_current_coverage(coverage_path) if current_only else verify_coverage(coverage_path)
    short_path = Path(short_path)
    short = load_json(short_path) if _record is None else _record
    current_only = coverage["schema"] == CURRENT_COVERAGE_SCHEMA
    current = coverage["schema"] in {"zhuyin-validation-coverage/2", CURRENT_COVERAGE_SCHEMA}
    require(short.get("schema") == (CURRENT_SHORT_SCHEMA if current_only else SHORT_SCHEMA if current else LEGACY_SHORT_SCHEMA) and short.get("outcome") == "success", "short validation did not succeed")
    if current:
        require(short.get("policy") == coverage["policy"] and short.get("window_validation") == coverage["window_validation"],
                "short validation policy differs from PR coverage")
    require(short["coverage_sha256"] == sha256_file(coverage_path), "short validation refers to different PR coverage")
    candidate = short["candidate"]
    require(len(candidate["parents"]) == 2 and candidate["parents"] == coverage["candidate"]["parents"], "actual merge ordered parents differ")
    require(candidate["head"] not in candidate["parents"] and candidate["tree"] == coverage["candidate"]["tree"], "actual merge tree differs")
    _environment_equivalent(coverage["environment"], short["environment"])
    _ci_identity(short["ci"], "push")
    require(short["started_at"] < short["finished_at"], "invalid short validation interval")
    source = short["source"]
    metadata = {key: load_json(_artifact(short_path.parent, source[key])) for key in ("pr", "run", "artifact", "commit", "runs") }
    pr, run, artifact, commit = (metadata[key] for key in ("pr", "run", "artifact", "commit"))
    require(pr["merged"] is True and pr["merge_commit_sha"] == candidate["head"] and
            pr["head"]["sha"] == candidate["parents"][1] and pr["base"]["ref"] == "develop" and
            pr["base"]["repo"]["full_name"] == REPOSITORY and pr["head"]["repo"]["full_name"] == REPOSITORY,
            "source PR is not the actual reviewed merge")
    if current_only:
        require(pr["number"] == coverage["checkout"]["number"] and pr["base"]["sha"] == candidate["parents"][0] and
                pr["head"]["ref"] == coverage["checkout"]["head_ref"] and coverage["checkout"]["base_ref"] == "develop",
                "current coverage belongs to another source PR/base/branch")
    require(commit["sha"] == candidate["head"] and [p["sha"] for p in commit["parents"]] == candidate["parents"] and commit["commit"]["tree"]["sha"] == candidate["tree"], "GitHub commit object differs from local merge")
    require(run["id"] == coverage["ci"]["run_id"] and run["run_attempt"] == coverage["ci"]["attempt"] and
            run["event"] == "pull_request" and run["head_sha"] == candidate["parents"][1] and
            run["path"] == ".github/workflows/ci.yml" and run["status"] == "completed" and run["conclusion"] == "success",
            "source PR run is not applicable successful CI")
    runs = [r for r in metadata["runs"] if r["head_sha"] == candidate["parents"][1] and r["event"] == "pull_request"]
    require(bool(runs), "retained source run inventory is empty")
    latest = max(runs, key=lambda r: (r["run_number"], r["id"]))
    require((latest["id"], latest["run_attempt"]) == (run["id"], run["run_attempt"]) and
            latest["status"] == "completed" and latest["conclusion"] == "success", "a newer source PR run/attempt is unresolved")
    prefix = "current-validation-evidence" if current_only else "validation-evidence"
    require(artifact["name"] == f"{prefix}-{run['id']}-{run['run_attempt']}" and not artifact["expired"], "wrong/expired source artifact")
    archive = _artifact(short_path.parent, source["archive"])
    require(artifact["digest"] == "sha256:" + sha256_file(archive), "archive differs from GitHub artifact digest")
    _archive_matches(archive, Path(coverage_path).parent)
    require(short["assets_sha256"] == short["pr_assets_sha256"], "runtime manifest differs from PR integration tree")
    if current_only:
        require(short["pr_assets_sha256"] == coverage["artifacts"]["assets"]["sha256"], "runtime manifest differs from retained current PR original")
    for key in ("assets", "pr_assets"):
        require(sha256_file(_artifact(short_path.parent, source[key])) == short[key + "_sha256"], "runtime manifest raw evidence mismatch")
    expected = short_commands(candidate["parents"][0], candidate["head"])
    require([item["name"] for item in short["checks"]] == [name for name, _ in expected], "missing/duplicate/unknown short check")
    for item, (_, command) in zip(short["checks"], expected):
        require(item["exit_code"] == 0 and _canonical_command(item["command"]) == _canonical_command(command), "short check failed or changed its command")
        log = _artifact(short_path.parent, item["log"])
        if item["name"] == "clean":
            require(not log.read_bytes().strip(), "short validation source working tree is dirty")
    return short


class GitHub:
    def __init__(self, token):
        require(bool(token), "GITHUB_TOKEN is required for source evidence retrieval")
        self.token = token

    def request(self, endpoint, *, binary=False):
        url = "https://api.github.com/repos/" + REPOSITORY + "/" + endpoint
        request = urllib.request.Request(url, headers={"Authorization": "Bearer " + self.token,
            "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
        # urllib removes Authorization only when explicitly handled below: use a
        # redirect handler that never forwards it to signed artifact storage.
        class Redirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                result = super().redirect_request(req, fp, code, msg, headers, newurl)
                if result and urllib.parse.urlsplit(newurl).netloc != "api.github.com":
                    result.remove_header("Authorization")
                return result
        with urllib.request.build_opener(Redirect()).open(request, timeout=60) as response:
            data = response.read()
        return data if binary else json.loads(data, object_pairs_hook=unique_object)

    def pages(self, endpoint, key=None):
        result = []
        for page in range(1, 1001):
            separator = "&" if "?" in endpoint else "?"
            data = self.request(f"{endpoint}{separator}per_page=100&page={page}")
            rows = data[key] if key else data
            result.extend(rows)
            if len(rows) < 100:
                return result
        raise ValueError("GitHub pagination did not terminate")


def _write_json(path, value):
    with Path(path).open("x", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False, sort_keys=True, indent=2)


def _reference(root, path):
    return {"path": Path(path).relative_to(root).as_posix(), "sha256": sha256_file(path)}


def extract_evidence(data, destination):
    destination.mkdir()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = set()
        for info in archive.infolist():
            require(info.filename not in names, "duplicate archive entry")
            names.add(info.filename)
            require("\\" not in info.filename and not PurePosixPath(info.filename).is_absolute(), "invalid artifact archive path")
            require((info.external_attr >> 16) & 0o170000 != 0o120000, "artifact contains symbolic link")
            target = local_path(destination, info.filename)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as output:
                    output.write(archive.read(info))


def fetch_pr_evidence(client, candidate, output, *, current_only=False):
    commit = client.request(f"commits/{candidate['head']}")
    prs = client.pages(f"commits/{candidate['head']}/pulls")
    matches = [p for p in prs if p.get("merge_commit_sha") == candidate["head"] and p.get("state") == "closed"]
    require(len(matches) == 1, "actual merge must identify exactly one source PR")
    pr = client.request(f"pulls/{matches[0]['number']}")
    runs = client.pages("actions/workflows/ci.yml/runs?event=pull_request&head_sha=" + candidate["parents"][1], "workflow_runs")
    runs = [r for r in runs if r["head_sha"] == candidate["parents"][1] and r["event"] == "pull_request"]
    require(bool(runs), "source PR has no CI run")
    latest = max(runs, key=lambda r: (r["run_number"], r["id"]))
    run = client.request(f"actions/runs/{latest['id']}")
    require(run["status"] == "completed" and run["conclusion"] == "success", "latest source PR run is unresolved or failed")
    artifacts = client.pages(f"actions/runs/{run['id']}/artifacts", "artifacts")
    prefix = "current-validation-evidence" if current_only else "validation-evidence"
    wanted = f"{prefix}-{run['id']}-{run['run_attempt']}"
    matches = [a for a in artifacts if a["name"] == wanted and not a["expired"]]
    require(len(matches) == 1, "latest PR attempt needs exactly one retained evidence artifact")
    artifact = matches[0]
    data = client.request(f"actions/artifacts/{artifact['id']}/zip", binary=True)
    archive = output / "pr-artifact.zip"
    archive.write_bytes(data)
    require(artifact["digest"] == "sha256:" + sha256_file(archive), "downloaded artifact digest mismatch")
    extracted = output / "pr-evidence"
    extract_evidence(data, extracted)
    coverage_path = extracted / ("current-coverage.json" if current_only else "coverage.json")
    coverage = verify_current_coverage(coverage_path) if current_only else verify_coverage(coverage_path)
    if current_only:
        require((run["id"], run["run_attempt"]) == (latest["id"], latest["run_attempt"]) and
                run["event"] == "pull_request" and run["head_sha"] == candidate["parents"][1] and
                run["path"] == ".github/workflows/ci.yml", "current run metadata differs from latest source PR attempt")
        require(coverage["schema"] == CURRENT_COVERAGE_SCHEMA and coverage["ci"] == {
            "run_id": run["id"], "attempt": run["run_attempt"], "job": "test", "event": "pull_request"}, "wrong current source attempt")
        require(pr["merged"] is True and pr["base"]["ref"] == "develop" and
                pr["base"]["repo"]["full_name"] == pr["head"]["repo"]["full_name"] == REPOSITORY and
                pr["number"] == coverage["checkout"]["number"] and pr["head"]["sha"] == candidate["parents"][1] and
                pr["base"]["sha"] == candidate["parents"][0] and
                coverage["candidate"]["parents"] == candidate["parents"] and coverage["candidate"]["tree"] == candidate["tree"] and
                commit["sha"] == candidate["head"] and [p["sha"] for p in commit["parents"]] == candidate["parents"] and
                commit["commit"]["tree"]["sha"] == candidate["tree"], "current source PR/actual merge identity mismatch")
    source = {"archive": _reference(output, archive)}
    for key, value in (("pr", pr), ("run", run), ("artifact", artifact), ("commit", commit), ("runs", runs)):
        path = output / (key + ".json")
        _write_json(path, value)
        source[key] = _reference(output, path)
    return coverage_path, coverage, source


def post_merge(evidence_root, *, current_only=False):
    runner = runner_module()
    output = Path(evidence_root) / ("short-" + str(uuid.uuid4()))
    output.mkdir(parents=True, exist_ok=False)
    started = datetime.now(timezone.utc).isoformat()
    _write_json(output / "started.json", {"started_at": started, "command": sys.argv})
    result = {"schema": CURRENT_SHORT_SCHEMA if current_only else SHORT_SCHEMA,
              "policy": CURRENT_POLICY if current_only else runner.POLICY,
              "window_validation": WINDOW_NOTICE if current_only else runner.audit.WINDOW_NOTICE,
              "started_at": started, "outcome": "failed", "checks": []}
    try:
        require(os.environ.get("GITHUB_EVENT_NAME") == "push" and os.environ.get("GITHUB_REF") == "refs/heads/develop", "short entrypoint requires actual develop push")
        candidate = runner.snapshot()
        require(len(candidate["parents"]) == 2 and candidate["head"] == os.environ.get("GITHUB_SHA"), "checkout is not actual pushed merge")
        result["candidate"] = candidate
        result["environment"] = runner.environment()
        result["ci"] = {"run_id": int(os.environ["GITHUB_RUN_ID"]), "attempt": int(os.environ["GITHUB_RUN_ATTEMPT"]), "job": os.environ["GITHUB_JOB"], "event": "push"}
        client = GitHub(os.environ.get("GITHUB_TOKEN"))
        coverage_path, coverage, source = (fetch_pr_evidence(client, candidate, output, current_only=True)
                                          if current_only else fetch_pr_evidence(client, candidate, output))
        require(coverage.get("policy") == result["policy"], "post-merge coverage policy differs; legacy evidence remains read-only")
        result.update(coverage_sha256=sha256_file(coverage_path), source=source,
                      coverage_path=coverage_path.relative_to(output).as_posix())
        require(candidate["parents"] == coverage["candidate"]["parents"] and candidate["tree"] == coverage["candidate"]["tree"], "actual merge parents/tree differ from PR integration")
        _environment_equivalent(coverage["environment"], result["environment"])
        result["assets_sha256"] = sha256_file(ROOT / "runtime_asset_manifest.json")
        blob = subprocess.run(["git", "cat-file", "--filters", candidate["tree"] + ":runtime_asset_manifest.json"], cwd=ROOT, capture_output=True, check=True).stdout
        result["pr_assets_sha256"] = hashlib.sha256(blob).hexdigest()
        for name, payload in (("assets", (ROOT / "runtime_asset_manifest.json").read_bytes()), ("pr_assets", blob)):
            artifact_path = output / (name + ".json")
            artifact_path.write_bytes(payload)
            result["source"][name] = _reference(output, artifact_path)
        require(result["assets_sha256"] == result["pr_assets_sha256"], "runtime manifest bytes do not match tested tree")
        for name, command in short_commands(candidate["parents"][0], candidate["head"]):
            log = output / (name + ".log")
            with log.open("xb") as stream:
                try:
                    completed = subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, timeout=300, check=False)
                    code = completed.returncode
                except subprocess.TimeoutExpired:
                    code = 124
            result["checks"].append({"name": name, "command": command, "exit_code": code, "log": _reference(output, log)})
            require(code == 0 and (name != "clean" or not log.read_bytes().strip()), "short check failed: " + name)
        require(runner.snapshot() == candidate and runner.environment() == result["environment"],
                "source or dependency environment changed during short validation")
        result["outcome"] = "success"
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    path = output / "short.json"
    if result["outcome"] == "success":
        try:
            if current_only:
                verify_reuse(output / result["coverage_path"], path, _record=result, current_only=True)
            else:
                verify_reuse(output / result["coverage_path"], path, _record=result)
        except Exception as exc:
            result.update(outcome="failed", error=f"{type(exc).__name__}: {exc}")
    _write_json(path, result)
    return path, result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    verify = sub.add_parser("verify-coverage")
    verify.add_argument("coverage", type=Path)
    current = sub.add_parser("current-coverage")
    current.add_argument("--evidence-root", type=Path, required=True)
    current.add_argument("--output", type=Path, required=True)
    reuse = sub.add_parser("verify-reuse")
    reuse.add_argument("coverage", type=Path)
    reuse.add_argument("short", type=Path)
    reuse.add_argument("--current-only", action="store_true")
    short = sub.add_parser("post-merge")
    short.add_argument("--evidence-root", type=Path, required=True)
    short.add_argument("--current-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "verify-coverage":
            verify_coverage(args.coverage)
        elif args.command == "verify-reuse":
            verify_reuse(args.coverage, args.short, current_only=args.current_only)
        elif args.command == "current-coverage":
            produce_current_coverage(args.evidence_root, args.output)
        else:
            path, result = post_merge(args.evidence_root, current_only=args.current_only)
            print(json.dumps({"path": str(path), "outcome": result["outcome"], "error": result.get("error")}))
            return 0 if result["outcome"] == "success" else 2
    except (OSError, ValueError, TypeError, KeyError, zipfile.BadZipFile) as exc:
        print(json.dumps({"outcome": "blocked", "error": str(exc)}))
        return 2
    print(json.dumps({"outcome": "success"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
