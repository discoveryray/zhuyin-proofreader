"""Inactive tiered CI wiring and bounded, file-backed documentation checks.

The coordinator captures independently obtained review originals at CAPTURE.
That local handoff is not an authenticity proof or an Actions transport.  A
normal checkout without the capture keeps full CI, including after activation.
No CLI argument or environment variable can enable the policy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import posixpath
import re
import subprocess
import sys
from urllib.parse import unquote, urlsplit

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import pr_flow_policy as policy

ROOT = Path(__file__).resolve().parents[1]
CAPTURE = Path("tmp/pr-review-automation/pr-flow-ci/current-capture.json")
SCHEMA = "pr-flow-documentation-checks/1"
CHECKS = ("bytes", "local_links", "diff")
FULL_STEPS = (
    "Upgrade pip", "Install development dependencies", "Capture main full environment",
    "Validate runtime asset integrity", "Audit test entrypoint coverage", "Run full pytest suite",
    "Verify active test execution", "Compile Python sources", "Check committed whitespace (pull request)",
    "Check committed whitespace (push)", "Check repository working tree", "Produce current validation coverage",
    "Verify current validation coverage", "Upload main full evidence",
)
DOC_STEPS = ("Run required documentation checks", "Verify required documentation checks", "Upload documentation evidence")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def unique(pairs):
    result = {}
    for key, value in pairs:
        policy.require(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError("non-finite JSON")))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")


def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root, stderr=subprocess.PIPE)


def bind_git(candidate, root=ROOT):
    """Verify the complete cumulative file list and actual Git objects."""
    policy.validate_candidate(candidate)
    policy.require(git(root, "rev-parse", candidate["head"] + "^{tree}").decode().strip() == candidate["tree"],
                   "candidate Git tree differs")
    policy.require(git(root, "merge-base", candidate["base"], candidate["head"]).decode().strip() == candidate["merge_base"],
                   "candidate merge base differs")
    subprocess.run(["git", "merge-base", "--is-ancestor", candidate["baseline"], candidate["head"]],
                   cwd=root, check=True, capture_output=True)
    actual = git(root, "diff", "--name-only", "-z", candidate["baseline"], candidate["head"]).decode("utf-8").rstrip("\0").split("\0")
    policy.require(sorted(actual) == sorted(candidate["changed_files"]), "complete cumulative changed files differ")


def bind_event(candidate, event, environment, root=ROOT):
    bind_git(candidate, root)
    policy.require(environment["GITHUB_EVENT_NAME"] == "pull_request", "documentation route requires a current PR event")
    pr = event["pull_request"]
    policy.require((environment["GITHUB_REPOSITORY"], pr["base"]["repo"]["full_name"], pr["base"]["sha"], pr["head"]["sha"])
                   == (candidate["repository"], candidate["repository"], candidate["base"], candidate["head"]),
                   "current event repository/base/head differs")
    policy.require(pr["base"]["ref"] in {"develop", "main"}, "unsupported PR target")
    checkout_sha = git(root, "rev-parse", "HEAD").decode().strip()
    parents = git(root, "show", "-s", "--format=%P", "HEAD").decode().strip().split()
    policy.require(environment["GITHUB_SHA"] == checkout_sha and parents == [candidate["base"], candidate["head"]]
                   and checkout_sha not in parents, "current synthetic merge SHA/ordered parents differ")
    ci = {"run_id": int(environment["GITHUB_RUN_ID"]), "attempt": int(environment["GITHUB_RUN_ATTEMPT"]),
          "job": environment["GITHUB_JOB"], "event": "pull_request"}
    policy.require(ci["run_id"] > 0 and ci["attempt"] > 0 and ci["job"] == "test", "invalid current CI identity")
    checkout = {"number": pr["number"], "repository": candidate["repository"],
                "base_repository": pr["base"]["repo"]["full_name"], "head_repository": pr["head"]["repo"]["full_name"],
                "base_ref": pr["base"]["ref"], "head_ref": pr["head"]["ref"],
                "base_sha": candidate["base"], "head_sha": candidate["head"],
                "checkout_sha": checkout_sha, "checkout_tree": git(root, "rev-parse", "HEAD^{tree}").decode().strip(),
                "checkout_parents": parents}
    policy.require(type(checkout["number"]) is int and checkout["number"] > 0, "invalid current PR number")
    return {"ci": ci, "checkout": checkout}


def validated_documentation_plan(capture, event, environment, root=ROOT):
    """Pure validation for the future handoff; never enables public dispatch."""
    policy.fields(capture, "schema policy coordinator candidate classification implementers review_originals", "review capture")
    policy.require(capture["schema"] == "pr-flow-ci-capture/1" and capture["policy"] == policy.POLICY_ID,
                   "unknown review capture")
    coordinator = capture["coordinator"]
    policy.fields(coordinator, "actor source_refs authorization_ref independent_sources_checked", "coordinator capture")
    policy.text(coordinator["actor"], "coordinator.actor")
    policy.texts(coordinator["source_refs"], "coordinator.source_refs", nonempty=True)
    policy.text(coordinator["authorization_ref"], "coordinator.authorization_ref")
    policy.require(coordinator["independent_sources_checked"] is True,
                   "coordinator must independently check original provenance; author JSON is insufficient")
    candidate = capture["candidate"]
    binding = bind_event(candidate, event, environment, root)
    # Canonical originals, author exclusion, classification and latest review
    # chains share the gate's validator. Hashes cannot prove an actor's identity.
    from scripts.pr_review_gate import validate_tiered_reviews
    reviewed = validate_tiered_reviews(candidate, capture["classification"], capture["implementers"], capture["review_originals"])
    ready = reviewed["code_ready"] and reviewed["classification"]["confirmed"] and reviewed["classification"]["tier"] == "docs"
    # The initial bounded checker supports existing Markdown files. Other
    # document formats/deletions retain full CI until a reviewed checker exists.
    supported = all(PurePosixPath(name).suffix.lower() == ".md" for name in candidate["changed_files"])
    if ready and supported:
        for name in candidate["changed_files"]:
            git(root, "cat-file", "blob", binding["checkout"]["checkout_sha"] + ":" + name)
    return {"schema": "pr-flow-ci-plan/1", "policy": policy.POLICY_ID,
            "route": "docs" if ready and supported else "full", "candidate": candidate,
            "reason": "confirmed-documentation" if ready and supported else "full-validation-required", **binding}


def dispatch(event, environment, root=ROOT):
    name, ref = environment.get("GITHUB_EVENT_NAME"), environment.get("GITHUB_REF")
    if name == "push" and ref == "refs/heads/develop":
        route = "short"
    elif (name == "pull_request" and event["pull_request"]["base"]["ref"] in {"develop", "main"}) or (name == "push" and ref == "refs/heads/main"):
        route = "full"
    else:
        raise policy.PolicyError("unsupported current validation event")
    result = {"schema": "pr-flow-ci-plan/1", "policy": policy.POLICY_ID, "route": route,
              "reason": "policy-inactive" if not policy.ENABLED else "trusted-capture-unavailable"}
    if not policy.ENABLED or route != "full" or name != "pull_request":
        return result
    source = Path(root) / CAPTURE
    if not source.is_file():
        return result
    # Capture is a coordinator handoff, never a checked-in author manifest.
    policy.require(not git(root, "ls-files", "--", CAPTURE.as_posix()).strip(), "tracked author capture cannot exempt full CI")
    try:
        result = validated_documentation_plan(load(source), event, environment, root)
        result["capture_sha256"] = digest(source.read_bytes())
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        result.update(route="full", reason="invalid-trusted-capture: " + str(error))
    return result


def _tested_checkout(root, candidate, tested_sha=None):
    """Resolve the actual integration object, never substitute the feature tree."""
    bind_git(candidate, root)
    sha = tested_sha or git(root, "rev-parse", "HEAD").decode().strip()
    policy.sha(sha, "tested checkout SHA")
    identity = {"head": sha, "tree": git(root, "rev-parse", sha + "^{tree}").decode().strip(),
                "parents": git(root, "show", "-s", "--format=%P", sha).decode().strip().split()}
    policy.require(identity["parents"] == [candidate["base"], candidate["head"]] and sha not in identity["parents"],
                   "tested checkout must be the actual ordered PR integration")
    return identity


def _bound_checkout(root, candidate, checkout, *, current=False):
    identity = _tested_checkout(root, candidate, checkout["checkout_sha"])
    policy.require(identity == {"head": checkout["checkout_sha"], "tree": checkout["checkout_tree"],
                                "parents": checkout["checkout_parents"]}, "tested checkout Git identity differs from binding")
    policy.require((checkout["base_sha"], checkout["head_sha"]) == (candidate["base"], candidate["head"]),
                   "tested checkout candidate binding differs")
    if current:
        policy.require(git(root, "rev-parse", "HEAD").decode().strip() == identity["head"],
                       "tested checkout changed during documentation checks")
    return identity


def _markdown(root, candidate, tested_sha):
    result = {}
    for name in candidate["changed_files"]:
        policy.require(PurePosixPath(name).suffix.lower() == ".md", "bounded documentation checker requires Markdown")
        raw = git(root, "cat-file", "blob", tested_sha + ":" + name)
        text = raw.decode("utf-8")
        policy.require(bool(text.strip()) and "\0" not in text, "empty or binary documentation")
        result[name] = (raw, text)
    return result


def _visible(text, *, preserve_inline=False):
    # Fenced examples and inline code are not rendered links/anchors.
    lines, fence = [], None
    for line in text.splitlines():
        match = re.match(r"^\s*(`{3,}|~{3,})", line)
        if match:
            marker = match[1][0]
            if fence is None: fence = marker
            elif fence == marker: fence = None
            continue
        if fence is None:
            lines.append(re.sub(r"`+([^`]*)`+", r"\1" if preserve_inline else "", line))
    policy.require(fence is None, "unclosed Markdown code fence")
    return "\n".join(lines)


def _anchors(text):
    text = _visible(text, preserve_inline=True)
    anchors = set(re.findall(r"\b(?:id|name)\s*=\s*['\"]([^'\"]+)['\"]", text))
    used = {}
    for line in text.splitlines():
        match = re.match(r"^ {0,3}#{1,6}\s+(.+?)(?:\s+#+)?$", line)
        if match:
            title = re.sub(r"<[^>]+>", "", match[1]).lower()
            title = re.sub(r"[^\w\-\s]", "", title).replace(" ", "-")
            count = used.get(title, 0); used[title] = count + 1
            anchors.add(title + ("-" + str(count) if count else ""))
    return anchors


def inspect_documentation(candidate, check, root=ROOT, *, tested_sha=None):
    tested = _tested_checkout(root, candidate, tested_sha)
    documents = _markdown(root, candidate, tested["head"])
    files = [{"path": name, "sha256": digest(raw), "size": len(raw)} for name, (raw, _) in documents.items()]
    if check == "bytes":
        return {"files": files, "inspected_files": len(files), "tested_checkout": tested}
    if check == "diff":
        results = []
        for end in (candidate["head"], tested["head"]):
            command = ["git", "diff", "--check", candidate["baseline"], end, "--", *candidate["changed_files"]]
            result = subprocess.run(command, cwd=root, capture_output=True, check=False)
            policy.require(result.returncode == 0, "documentation diff check failed: " + result.stdout.decode("utf-8", errors="replace"))
            results.append({"command": command, "stdout": result.stdout.decode("utf-8"), "stderr": result.stderr.decode("utf-8")})
        return {"results": results, "inspected_files": len(files), "tested_checkout": tested}
    policy.require(check == "local_links", "unknown documentation check")
    checked = []
    for name, (_, text) in documents.items():
        visible = _visible(text)
        definitions = dict(re.findall(r"^ {0,3}\[([^\]]+)\]:\s*<?([^\s>]+)>?", visible, re.M))
        targets = [a or b for a, b in re.findall(r"!?\[[^\]]*\]\(\s*(?:<([^>]+)>|([^\s)]+))(?:\s+[^)]*)?\)", visible)]
        for label in re.findall(r"!?\[[^\]]+\]\[([^\]]+)\]", visible):
            policy.require(label in definitions, "missing Markdown reference definition: " + label)
            targets.append(definitions[label])
        targets += list(definitions.values())
        for target in targets:
            split = urlsplit(target)
            if split.scheme or split.netloc:
                policy.require(split.scheme in {"http", "https", "mailto"} or target.startswith("//"), "unsupported documentation link scheme")
                continue
            policy.require(not split.query, "local documentation link query is unsupported")
            path = unquote(split.path)
            policy.require("\\" not in path and not path.startswith("/"), "non-relative local documentation link")
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), path)) if path else name
            policy.require(resolved not in {".", ".."} and not resolved.startswith("../"), "documentation link escapes repository")
            try:
                content = git(root, "cat-file", "blob", tested["head"] + ":" + resolved)
            except subprocess.CalledProcessError as error:
                raise policy.PolicyError("missing local documentation target: " + resolved) from error
            if split.fragment:
                policy.require(PurePosixPath(resolved).suffix.lower() == ".md", "local non-Markdown anchor requires another checker")
                policy.require(unquote(split.fragment) in _anchors(content.decode("utf-8")), "missing local documentation anchor: " + target)
            checked.append({"source": name, "target": target, "path": resolved, "sha256": digest(content)})
    return {"inspected_files": len(files), "local_links": checked, "tested_checkout": tested}


def _reference(parent, path):
    return {"path": path.relative_to(parent).as_posix(), "sha256": digest(path.read_bytes())}


def _artifact(parent, pointer):
    policy.fields(pointer, "path sha256", "documentation artifact")
    policy.sha(pointer["sha256"], "artifact.sha256", 64)
    path = Path(parent) / pointer["path"]
    policy.require(type(pointer["path"]) is str and not Path(pointer["path"]).is_absolute()
                   and path.resolve().is_relative_to(Path(parent).resolve()), "documentation artifact escapes evidence root")
    policy.require(digest(path.read_bytes()) == pointer["sha256"], "documentation artifact hash mismatch")
    return path


def run_documentation_checks(candidate, binding, output, root=ROOT):
    """Execute three real stdlib/Git checks, retaining failures and raw streams."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    candidate_path = output.parent / "candidate.json"
    write(candidate_path, candidate)
    tested = _bound_checkout(root, candidate, binding["checkout"], current=True)
    checks = []
    for name in CHECKS:
        command = [sys.executable, str(Path(__file__).resolve()), "inspect", "--candidate", str(candidate_path.resolve()), "--check", name,
                   "--tested-sha", tested["head"]]
        result = subprocess.run(command, cwd=root, capture_output=True, check=False)
        stdout, stderr = output.parent / (name + ".stdout.json"), output.parent / (name + ".stderr.log")
        with stdout.open("xb") as stream: stream.write(result.stdout)
        with stderr.open("xb") as stream: stream.write(result.stderr)
        checks.append({"name": name, "command": ["<python>", "scripts/pr_flow_ci.py", "inspect", "--candidate", "candidate.json", "--check", name,
                                                   "--tested-sha", tested["head"]],
                       "exit_code": result.returncode, "stdout": _reference(output.parent, stdout), "stderr": _reference(output.parent, stderr)})
    record = {"schema": SCHEMA, "policy": policy.POLICY_ID, "candidate": candidate,
              "status": "success" if all(item["exit_code"] == 0 for item in checks) else "failed",
              "execution": {"python": platform.python_version(), "platform": platform.system(),
                            "checker_sha256": digest(Path(__file__).read_bytes())},
              "candidate_original": _reference(output.parent, candidate_path), "checks": checks, **binding}
    # Retain failed evidence when the checkout drifts while a child runs.
    # Immutable object reads stay bound to the original tested SHA.
    try:
        _bound_checkout(root, candidate, binding["checkout"], current=True)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        record["status"] = "failed"
        write(output, record)
        raise
    write(output, record)
    return record


def verify_documentation_checks(path, candidate, *, _root=None):
    """Verify retained, nonempty results; caller also verifies current PR/run."""
    path = Path(path); record = load(path)
    policy.fields(record, "schema policy candidate status execution candidate_original checks ci checkout", "documentation checks")
    policy.require(record["schema"] == SCHEMA and record["policy"] == policy.POLICY_ID and record["status"] == "success"
                   and record["candidate"] == candidate, "unsuccessful or stale documentation checks")
    policy.validate_candidate(candidate)
    root = ROOT if _root is None else _root
    tested = _bound_checkout(root, candidate, record["checkout"])
    execution = record["execution"]
    policy.fields(execution, "python platform checker_sha256", "documentation execution")
    policy.require(execution == {"python": "3.13.0", "platform": "Windows", "checker_sha256": digest(Path(__file__).read_bytes())},
                   "unsupported or changed documentation checker environment")
    policy.require(load(_artifact(path.parent, record["candidate_original"])) == candidate, "candidate original differs")
    policy.require(type(record["checks"]) is list and [item["name"] for item in record["checks"]] == list(CHECKS),
                   "missing, duplicate or empty required documentation checks")
    for item in record["checks"]:
        policy.fields(item, "name command exit_code stdout stderr", "documentation check")
        policy.require(type(item["exit_code"]) is int and item["exit_code"] == 0 and item["command"] ==
                       ["<python>", "scripts/pr_flow_ci.py", "inspect", "--candidate", "candidate.json", "--check", item["name"],
                        "--tested-sha", tested["head"]],
                       "failed or changed documentation command")
        actual = load(_artifact(path.parent, item["stdout"]))
        policy.require(type(actual) is dict and actual.get("inspected_files") == len(candidate["changed_files"])
                       and type(actual["inspected_files"]) is int and actual["inspected_files"] > 0,
                       "documentation check did not inspect the actual nonempty candidate")
        policy.require(actual.get("tested_checkout") == tested, "raw documentation tested identity differs")
        policy.require(not _artifact(path.parent, item["stderr"]).read_bytes().strip(), "documentation command emitted errors")
        # Recompute these bounded checks from the same immutable Git objects;
        # a hand-written empty success log is never enough.
        policy.require(actual == inspect_documentation(candidate, item["name"], root, tested_sha=tested["head"]), "documentation result differs from actual candidate bytes/links/diff")
    return record


def verify_documentation_ci(ci, pr, candidate, record):
    """Bind document evidence to the current original successful Actions run."""
    policy.require(ci is not None and pr is not None, "documentation checks require current PR CI")
    policy.require((ci["workflow"], ci["event"], ci["branch"], ci["head_sha"], ci["parents"])
                   == (".github/workflows/ci.yml", "pull_request", pr["head_branch"], candidate["head"], [candidate["base"], candidate["head"]]),
                   "documentation CI scope differs")
    policy.require(ci["status"] == "completed" and ci["conclusion"] == "success" and
                   (ci["run_id"], ci["attempt"]) == (ci["latest_run_id"], ci["latest_attempt"]), "documentation CI is unsuccessful or superseded")
    policy.require(record["ci"] == {"run_id": ci["run_id"], "attempt": ci["attempt"], "job": "test", "event": "pull_request"},
                   "documentation checks refer to another CI run/attempt")
    checkout = record["checkout"]
    policy.fields(checkout, "number repository base_repository head_repository base_ref head_ref base_sha head_sha checkout_sha checkout_tree checkout_parents", "documentation checkout")
    policy.require(checkout["number"] == pr["number"] and checkout["head_ref"] == pr["head_branch"] and
                   checkout["base_ref"] == pr["base_branch"] and checkout["repository"] == checkout["base_repository"] == candidate["repository"] and
                   (checkout["base_sha"], checkout["head_sha"], checkout["checkout_sha"], checkout["checkout_tree"], checkout["checkout_parents"])
                   == (candidate["base"], candidate["head"], ci["tested_sha"], ci["tree"], ci["parents"]), "documentation checkout differs from current PR integration")
    policy.require(ci["tested_sha"] not in ci["parents"], "documentation requires synthetic merge validation")
    jobs = {job["name"]: job for job in ci["jobs"]}
    summary = jobs.get("Python 3.13")
    policy.require(summary is not None and summary["runner"].startswith("windows-") and summary["python_version"] == "3.13.0", "missing supported documentation CI job")
    for job in ci["jobs"]:
        if job["name"] == "Merge short validation" and job["conclusion"] == "skipped": continue
        policy.require(job["name"] == "Python 3.13" and job["conclusion"] == "success" and
                       (job["run_id"], job["attempt"], job["tested_sha"]) == (ci["run_id"], ci["attempt"], ci["tested_sha"]), "documentation CI jobs are mixed or unsuccessful")
    required = ("Check out repository", "Set up Python", "Show Python version", "Resolve current validation route", *DOC_STEPS, "Verify required validation results")
    policy.require(all(summary["steps"].get(name) == "success" for name in required), "required documentation CI step missing or unsuccessful")
    policy.require(all(summary["steps"].get(name) == "skipped" for name in FULL_STEPS), "documentation CI mixes full and document results")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    route = commands.add_parser("dispatch"); route.add_argument("--output", type=Path, required=True)
    run = commands.add_parser("documentation"); run.add_argument("--plan", type=Path, required=True); run.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser("verify-documentation"); verify.add_argument("path", type=Path)
    inspect = commands.add_parser("inspect"); inspect.add_argument("--candidate", type=Path, required=True); inspect.add_argument("--check", choices=CHECKS, required=True)
    inspect.add_argument("--tested-sha")
    args = parser.parse_args(argv)
    try:
        if args.command == "inspect":
            # Read-only bounded checker; supplies no route or merge permission.
            result = inspect_documentation(load(args.candidate), args.check, Path.cwd(), tested_sha=args.tested_sha)
        elif args.command == "verify-documentation":
            policy.require(policy.ENABLED, "tiered documentation dispatch is inactive")
            record = load(args.path); result = verify_documentation_checks(args.path, record["candidate"])
        else:
            event = load(os.environ["GITHUB_EVENT_PATH"])
            result = dispatch(event, os.environ)
            if args.command == "documentation":
                policy.require(policy.ENABLED and result["route"] == "docs" and result == load(args.plan), "inactive or stale documentation plan")
                result = run_documentation_checks(result["candidate"], {key: result[key] for key in ("ci", "checkout")}, args.output)
                policy.require(result["status"] == "success", "documentation checks failed; original results retained")
            else:
                write(args.output, result)
            if os.environ.get("GITHUB_OUTPUT"):
                with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as stream:
                    if args.command == "dispatch": stream.write("route=" + result["route"] + "\npolicy_active=" + str(policy.ENABLED).lower() + "\n")
                    else: stream.write("check_count=" + str(len(result["checks"])) + "\n")
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
