"""Bounded main-full evidence capture, preserving child failure exit codes."""
from __future__ import annotations

import argparse
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys


def environment(output, *, current_only=False):
    if current_only:
        try:
            from scripts import validation_evidence as evidence
        except ModuleNotFoundError:
            import validation_evidence as evidence
        runner = evidence.runner_module()
        ci = {"run_id": int(os.environ["GITHUB_RUN_ID"]), "attempt": int(os.environ["GITHUB_RUN_ATTEMPT"]),
              "job": os.environ["GITHUB_JOB"], "event": os.environ["GITHUB_EVENT_NAME"]}
        candidate = runner.snapshot()
        evidence.require(candidate["head"] == os.environ["GITHUB_SHA"], "environment checkout SHA differs")
        event = evidence.load_json(Path(os.environ["GITHUB_EVENT_PATH"]))
        if ci["event"] == "pull_request":
            pr = event["pull_request"]
            checkout = {"number": pr["number"], "repository": os.environ["GITHUB_REPOSITORY"],
                "base_repository": pr["base"]["repo"]["full_name"], "head_repository": pr["head"]["repo"]["full_name"],
                "base_ref": pr["base"]["ref"], "head_ref": pr["head"]["ref"], "base_sha": pr["base"]["sha"], "head_sha": pr["head"]["sha"]}
        else:
            checkout = {"repository": os.environ["GITHUB_REPOSITORY"], "ref": os.environ["GITHUB_REF"], "before": event["before"]}
        result = {"schema": "zhuyin-current-validation-environment/1", "policy": evidence.CURRENT_POLICY,
            "window_validation": evidence.WINDOW_NOTICE, "environment": runner.environment(), "candidate": candidate,
            "ci": ci, "checkout": checkout}
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, indent=2, ensure_ascii=False)
        # Keep the original capture even on failure; this is metadata, not PASS.
        # The same strict guard is also used by the final coverage consumer.
        evidence._locked_environment(result["environment"], evidence.ROOT / "requirements-ci-lock.txt")
        return
    # Deliberately no environment dump, credentials, user paths or DB inspection.
    result = {"schema": "zhuyin-main-full-environment/1",
              "python": platform.python_version(), "executable": sys.executable,
              "platform": platform.platform(),
              "packages": {item.metadata["Name"]: item.version for item in metadata.distributions()},
              "run_id": os.environ.get("GITHUB_RUN_ID"), "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
              "event": os.environ.get("GITHUB_EVENT_NAME"), "tested_sha": os.environ.get("GITHUB_SHA"),
              "policy": "validation-flow/no-real-tk/1", "window_validation": "cancelled; not GUI PASS",
              "capture": os.environ.get("PYTEST_ADDOPTS", "")}
    for name, command in (("tree", ["git", "rev-parse", "HEAD^{tree}"]), ("parents", ["git", "show", "-s", "--format=%P", "HEAD"])):
        raw = subprocess.run(command, capture_output=True, text=True, check=False)
        result[name] = raw.stdout.strip() if raw.returncode == 0 else None
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def verify_environment(output):
    """Fail before full pytest using the existing exact frozen environment guard."""
    try:
        from scripts import validation_evidence as evidence
    except ModuleNotFoundError:
        import validation_evidence as evidence
    value = evidence.runner_module().environment()
    record = {"schema": "zhuyin-locked-environment-check/1", "environment": value,
              "executable": sys.executable, "prefix": sys.prefix, "base_prefix": sys.base_prefix}
    try:
        evidence._locked_environment(value, evidence.ROOT / "requirements-ci-lock.txt")
    except ValueError as error:
        record.update(status="failed", error=str(error))
        raise
    else:
        record["status"] = "success"
    finally:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as stream:
            json.dump(record, stream, indent=2, ensure_ascii=False)


def run(output, command):
    if not command:
        raise ValueError("child command required")
    # Resolve only the finite unqualified Python spellings. Explicit executables
    # and non-Python commands retain their original identity and arguments.
    command = list(command)
    if command[0].lower() in {"python", "python.exe"}:
        command[0] = sys.executable
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as log:
        log.write((json.dumps({"argv": command}, ensure_ascii=False) + "\n").encode("utf-8"))
        log.flush()
        child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for line in iter(child.stdout.readline, b""):
            log.write(line)
            log.flush()
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()
        code = child.wait()
        log.write(("\nexit_code=" + str(code) + "\n").encode("ascii"))
    return code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("environment", "verify-environment", "run"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--current-only", action="store_true")
    args, child = parser.parse_known_args(argv)
    if args.command in {"environment", "verify-environment"}:
        if child:
            parser.error("unexpected environment arguments")
        if args.command == "verify-environment":
            verify_environment(args.output)
        else:
            environment(args.output, current_only=args.current_only)
        return 0
    return run(args.output, child[1:] if child and child[0] == "--" else child)


if __name__ == "__main__":
    raise SystemExit(main())
