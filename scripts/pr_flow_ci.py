"""Compatibility routing for the inactive full-CI first edition.

All pull requests retain the existing complete non-window CI. This module
does not parse documentation, read review capture, select an exemption, or
authenticate a source. The retired parser/transport experiment and unchanged
known defects are retained only in docs/research/PR_FLOW_DEFERRED_822d494.zip.
The first-edition workflow uses the existing full/short consumers directly.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import pr_flow_policy as policy

ROOT = Path(__file__).resolve().parents[1]


def dispatch(event, environment, root=ROOT):
    """Describe existing event routing; never use capture or classification."""
    name, ref = environment.get("GITHUB_EVENT_NAME"), environment.get("GITHUB_REF")
    if name == "push" and ref == "refs/heads/develop":
        route = "short"
    elif name == "pull_request":
        base = event.get("pull_request", {}).get("base", {}).get("ref")
        policy.require(base in {"develop", "main"}, "unsupported current PR base")
        route = "full"
    elif name == "push" and ref == "refs/heads/main":
        route = "full"
    else:
        raise policy.PolicyError("unsupported current validation event")
    return {"schema": "pr-flow-ci-plan/1", "policy": policy.POLICY_ID,
            "route": route, "reason": "first-edition-full-ci; no documentation exemption"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("dispatch")
    command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        event_path = os.environ.get("GITHUB_EVENT_PATH")
        event = json.loads(Path(event_path).read_text(encoding="utf-8")) if event_path else {}
        result = dispatch(event, os.environ)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        if os.environ.get("GITHUB_OUTPUT"):
            with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8", newline="\n") as stream:
                stream.write("route=" + result["route"] + "\n")
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"error": str(error), "merge_authorized": False}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
