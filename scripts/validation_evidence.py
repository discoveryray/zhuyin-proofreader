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
SHORT_SCHEMA = "zhuyin-merge-short/1"


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
    require(bundle.get("schema") == "zhuyin-validation-coverage/1", "unknown coverage schema")
    require(bundle.get("status") == "success", "coverage is not successful")
    for reference in [bundle["core_manifest"], bundle["gui_manifest"], *bundle["history_manifests"]]:
        local_path(path.parent, reference)
    # aggregate reads log/JUnit/inventory hashes and enforces complete history,
    # one-success-per-identity, exact environment and the bounded GUI retry.
    try:
        actual = runner_module().aggregate(path.parent, head=bundle["candidate"]["head"], ci=bundle["ci"])
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
    for reference in (bundle["core_manifest"], bundle["gui_manifest"]):
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


def verify_reuse(coverage_path, short_path, *, _record=None):
    coverage = verify_coverage(coverage_path)
    short_path = Path(short_path)
    short = load_json(short_path) if _record is None else _record
    require(short.get("schema") == SHORT_SCHEMA and short.get("outcome") == "success", "short validation did not succeed")
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
    require(artifact["name"] == f"validation-evidence-{run['id']}-{run['run_attempt']}" and not artifact["expired"], "wrong/expired source artifact")
    archive = _artifact(short_path.parent, source["archive"])
    require(artifact["digest"] == "sha256:" + sha256_file(archive), "archive differs from GitHub artifact digest")
    _archive_matches(archive, Path(coverage_path).parent)
    require(short["assets_sha256"] == short["pr_assets_sha256"], "runtime manifest differs from PR integration tree")
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


def fetch_pr_evidence(client, candidate, output):
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
    wanted = f"validation-evidence-{run['id']}-{run['run_attempt']}"
    matches = [a for a in artifacts if a["name"] == wanted and not a["expired"]]
    require(len(matches) == 1, "latest PR attempt needs exactly one retained evidence artifact")
    artifact = matches[0]
    data = client.request(f"actions/artifacts/{artifact['id']}/zip", binary=True)
    archive = output / "pr-artifact.zip"
    archive.write_bytes(data)
    require(artifact["digest"] == "sha256:" + sha256_file(archive), "downloaded artifact digest mismatch")
    extracted = output / "pr-evidence"
    extract_evidence(data, extracted)
    coverage_path = extracted / "coverage.json"
    coverage = verify_coverage(coverage_path)
    source = {"archive": _reference(output, archive)}
    for key, value in (("pr", pr), ("run", run), ("artifact", artifact), ("commit", commit), ("runs", runs)):
        path = output / (key + ".json")
        _write_json(path, value)
        source[key] = _reference(output, path)
    return coverage_path, coverage, source


def post_merge(evidence_root):
    runner = runner_module()
    output = Path(evidence_root) / ("short-" + str(uuid.uuid4()))
    output.mkdir(parents=True, exist_ok=False)
    started = datetime.now(timezone.utc).isoformat()
    _write_json(output / "started.json", {"started_at": started, "command": sys.argv})
    result = {"schema": SHORT_SCHEMA, "started_at": started, "outcome": "failed", "checks": []}
    try:
        require(os.environ.get("GITHUB_EVENT_NAME") == "push" and os.environ.get("GITHUB_REF") == "refs/heads/develop", "short entrypoint requires actual develop push")
        candidate = runner.snapshot()
        require(len(candidate["parents"]) == 2 and candidate["head"] == os.environ.get("GITHUB_SHA"), "checkout is not actual pushed merge")
        result["candidate"] = candidate
        result["environment"] = runner.environment()
        result["ci"] = {"run_id": int(os.environ["GITHUB_RUN_ID"]), "attempt": int(os.environ["GITHUB_RUN_ATTEMPT"]), "job": os.environ["GITHUB_JOB"], "event": "push"}
        coverage_path, coverage, source = fetch_pr_evidence(GitHub(os.environ.get("GITHUB_TOKEN")), candidate, output)
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
    reuse = sub.add_parser("verify-reuse")
    reuse.add_argument("coverage", type=Path)
    reuse.add_argument("short", type=Path)
    short = sub.add_parser("post-merge")
    short.add_argument("--evidence-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "verify-coverage":
            verify_coverage(args.coverage)
        elif args.command == "verify-reuse":
            verify_reuse(args.coverage, args.short)
        else:
            path, result = post_merge(args.evidence_root)
            print(json.dumps({"path": str(path), "outcome": result["outcome"], "error": result.get("error")}))
            return 0 if result["outcome"] == "success" else 2
    except (OSError, ValueError, TypeError, KeyError, zipfile.BadZipFile) as exc:
        print(json.dumps({"outcome": "blocked", "error": str(exc)}))
        return 2
    print(json.dumps({"outcome": "success"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
