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


def environment(output):
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


def run(output, command):
    if not command:
        raise ValueError("child command required")
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
    parser.add_argument("command", choices=("environment", "run"))
    parser.add_argument("--output", type=Path, required=True)
    args, child = parser.parse_known_args(argv)
    if args.command == "environment":
        if child:
            parser.error("unexpected environment arguments")
        environment(args.output)
        return 0
    return run(args.output, child[1:] if child and child[0] == "--" else child)


if __name__ == "__main__":
    raise SystemExit(main())
