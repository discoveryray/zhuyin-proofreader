"""Finite tiered-review policy shared by the gate and CI consumers.

Hashes verify retrievable bytes, never reviewer identity or authorization.
Activation requires separately adopted, integrated source and actual short
evidence. Metadata never authenticates an authorization or a task's origin.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
import json
from pathlib import Path, PurePosixPath
import re
import subprocess


POLICY_ID = "development-flow/tiered-review/1"
ENABLED = False
# Retained informational state for the old pending /5 contract. It is not an
# activation switch: /6 eligibility is proved separately for each new task.
ACTIVATION_TASK = "pr-flow-activation-20261113"
ACTIVATION_BASELINE = "3a965ac3dacb51d6c3ccde92fccced51b0afa6f2"
ACTIVATION_BRANCH = "codex/pr-flow-activation-20261113"
# The adopted first edition changes reviewer counts, not test exemptions.
# This source constant is not an input/environment activation switch.
FIRST_EDITION_CHECKS_ROUTE = "full"
TIERS = {"docs": 1, "local": 1, "core": 2}
RISKS = {"commands", "authorization", "safety", "actual_expected", "evidence",
         "identity", "transaction_recovery", "gate_ci", "core_contract",
         "architecture", "unknown"}
PROTECTED_PATHS = {
    "AGENTS.md", "TESTING.md", "requirements-ci-lock.txt", "runtime_asset_manifest.json",
    "docs/DEVELOPMENT_FLOW_DECISIONS.md", "docs/DEVELOPMENT_REVIEW.md",
    "docs/CHATGPT_PROJECT_INSTRUCTIONS.md", "docs/VALIDATION_POLICY.md",
    "docs/PR_REVIEW_GATE.md", "docs/PR_REVIEW_AUTOMATION.md",
    "docs/V58_MASTER_DEVELOPMENT_REVIEW_PLAN_v1.1.md",
}
PROTECTED_PREFIXES = (".codex/", ".github/", "scripts/pr_review", "scripts/pr_flow",
                      "scripts/validation_", "scripts/test_entrypoint", "scripts/test_group_registry")


class PolicyError(ValueError):
    """Missing, unknown, or contradictory policy evidence."""


def require(condition, message):
    if not condition:
        raise PolicyError(message)


def fields(value, names, where):
    require(type(value) is dict and set(value) == set(names.split()),
            f"{where}: unknown or missing fields")


def text(value, where):
    require(type(value) is str and bool(value.strip()), f"{where}: nonempty text required")


def texts(value, where, *, nonempty=False):
    require(type(value) is list, f"{where}: list required")
    for item in value:
        text(item, where)
    require(len(value) == len(set(value)) and (not nonempty or bool(value)),
            f"{where}: duplicate or missing entries")


def sha(value, where, length=40):
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{" + str(length) + "}", value)
            is not None and value != "0" * length, f"{where}: full nonzero SHA required")


def boolean(value, where):
    require(type(value) is bool, f"{where}: boolean required")


def validate_candidate(candidate):
    fields(candidate, "task_id repository baseline base merge_base head tree scope changed_files risks", "candidate")
    for key in ("task_id", "repository", "scope"):
        text(candidate[key], "candidate." + key)
    require(candidate["repository"] == "discoveryray/zhuyin-proofreader", "wrong candidate repository")
    for key in ("baseline", "base", "merge_base", "head", "tree"):
        sha(candidate[key], "candidate." + key)
    texts(candidate["changed_files"], "candidate.changed_files", nonempty=True)
    for name in candidate["changed_files"]:
        path = PurePosixPath(name)
        require("\\" not in name and ":" not in name and not path.is_absolute()
                and path.as_posix() == name and all(p not in (".", "..") for p in path.parts),
                "changed file must be a normalized repository path")
    texts(candidate["risks"], "candidate.risks")
    require(set(candidate["risks"]) <= RISKS, "unknown risk; classify as core/unknown explicitly")


def protected_path(name):
    """A finite downgrade guard, not dependency analysis or semantic classification."""
    lower = name.lower()
    return (lower in {p.lower() for p in PROTECTED_PATHS} or lower.startswith(PROTECTED_PREFIXES)
            or (name.startswith("docs/") and ("contract" in lower or "validation" in lower))
            or lower.startswith(("actual_", "expected_", "manual_surface", "fingerprint")))


def validate_classification(candidate, classification, implementers, confirmations):
    validate_candidate(candidate)
    fields(classification, "tier author rationale known_consumers impact_known", "classification")
    require(classification["tier"] in TIERS, "unknown classification tier")
    texts(implementers, "implementers", nonempty=True)
    text(classification["author"], "classification.author")
    require(classification["author"] in implementers, "classification author must be an implementer")
    text(classification["rationale"], "classification.rationale")
    texts(classification["known_consumers"], "classification.known_consumers", nonempty=True)
    boolean(classification["impact_known"], "classification.impact_known")
    tier = classification["tier"]
    must_core = bool(candidate["risks"]) or any(protected_path(p) for p in candidate["changed_files"])
    require(not must_core or tier == "core", "protected path or risk requires core classification")
    require(classification["impact_known"] or tier == "core", "unknown impact requires core classification")
    if tier == "docs":
        require(all(PurePosixPath(p).suffix.lower() in {".md", ".rst", ".txt"}
                    for p in candidate["changed_files"]), "mixed executable changes cannot be docs")
    require(type(confirmations) is list, "classification confirmations must be a list")
    reviewers, sessions = set(), set()
    for report in confirmations:
        if report["candidate"] != candidate:
            continue
        reviewer, session = report["reviewer"], report["session"]
        text(reviewer, "reviewer")
        text(session, "reviewer.session")
        require(reviewer not in implementers and session not in implementers and report["independent"] is True,
                "classification confirmation must exclude all implementers")
        require(report["confirmed_tier"] == tier, "reviewer classification disagreement requires escalation")
        reviewers.add(reviewer)
        sessions.add(session)
    count = TIERS[tier]
    return {"tier": tier, "reviewers_required": count,
            "confirmed": len(reviewers) >= count and len(sessions) >= count
            and classification["impact_known"] and "unknown" not in candidate["risks"]}


def verify_original(pointer, *, resolver=None):
    """Read one canonical immutable version and any same-byte backup copies."""
    fields(pointer, "report_ref source_file sha256 backups", "original")
    text(pointer["report_ref"], "original.report_ref")
    sha(pointer["sha256"], "original.sha256", 64)
    text(pointer["source_file"], "original.source_file")
    source = Path(pointer["source_file"])
    require(source.is_absolute(), "canonical original requires an absolute retrievable file")
    def resolved(file, digest, original):
        if resolver is None:
            return file
        target = resolver(original, digest, pointer["report_ref"])
        require(isinstance(target, Path) and target.is_absolute(),
                "original resolver must return an absolute verified file")
        return target

    actual = resolved(source, pointer["sha256"], pointer["source_file"])
    raw = actual.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == pointer["sha256"], "canonical original hash mismatch")
    require(type(pointer["backups"]) is list, "backups must be a list")
    seen, originals = {actual.resolve()}, {source.resolve()}
    for backup in pointer["backups"]:
        fields(backup, "source_file sha256", "backup")
        text(backup["source_file"], "backup.source_file")
        sha(backup["sha256"], "backup.sha256", 64)
        file = Path(backup["source_file"])
        require(file.is_absolute() and file.resolve() not in originals, "backup must be a separate absolute file")
        originals.add(file.resolve())
        actual = resolved(file, backup["sha256"], backup["source_file"])
        require(actual.resolve() not in seen, "backup must resolve to a separate absolute file")
        seen.add(actual.resolve())
        data = actual.read_bytes()
        require(backup["sha256"] == pointer["sha256"] and data == raw,
                "backup differs from canonical original bytes")
    return raw


def _receipt(pointer, *, resolver=None):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "duplicate adoption receipt key")
            result[key] = value
        return result
    return json.loads(verify_original(pointer, resolver=resolver).decode("utf-8-sig"),
                      object_pairs_hook=unique,
                      parse_constant=lambda value: require(False, "invalid adoption constant"))


def _instant(value, where):
    text(value, where)
    result = datetime.fromisoformat(value)
    require(result.tzinfo is not None, where + ": timezone required")
    return result.astimezone(timezone.utc)


def _source(receipt, *, resolver=None):
    # A coordinator must retrieve this from the actual user/platform source.
    # Reading/hash checking is necessary, but cannot authenticate that source.
    text(receipt["source_ref"], "adoption.source_ref")
    require(receipt["source_ref"] == receipt["authorization"]["report_ref"],
            "adoption source differs from its retained authorization original")
    raw = verify_original(receipt["authorization"], resolver=resolver)
    require(bool(raw.decode("utf-8-sig").strip()), "adoption authorization original is empty")


def validate_adoption(adoption, candidate, task_policy, *, resolver=None):
    """Check finite activation/new-task evidence; never certify its authenticity.

    Git reads use the real invocation repository, not a path supplied by the
    state. The coordinator independently verifies user identity, original
    creation/adoption events and the latest platform outcomes before use.
    """
    fields(adoption, "activation task", "adoption")
    activation = _receipt(adoption["activation"], resolver=resolver)
    task = _receipt(adoption["task"], resolver=resolver)
    fields(activation, "schema policy task_id baseline reviewed_head actual_merge completed_at source_ref "
           "authorization coverage short_validation", "activation receipt")
    fields(task, "schema policy task_id baseline created_at adopted_at activation_ref source_ref authorization",
           "task adoption receipt")
    require(activation["schema"] == "pr-flow-activation/1" and task["schema"] == "pr-flow-task-adoption/1",
            "unknown adoption receipt version")
    require(activation["policy"] == task["policy"] == POLICY_ID, "adoption policy differs")
    require(activation["task_id"] == ACTIVATION_TASK and activation["baseline"] == ACTIVATION_BASELINE,
            "wrong activation task/baseline")
    require(candidate["task_id"] not in {ACTIVATION_TASK, "pr-flow-decisions-20261112"},
            "activation and original reform tasks retain the old acceptance contract")
    require((task["task_id"], task["baseline"]) == (candidate["task_id"], candidate["baseline"]),
            "adoption belongs to another task/baseline")
    require(task_policy["source_ref"] == task["source_ref"], "task policy points to another adoption source")
    require(task["activation_ref"] == adoption["activation"]["report_ref"], "wrong activation receipt reference")
    _source(activation, resolver=resolver)
    _source(task, resolver=resolver)
    for key in ("reviewed_head", "actual_merge"):
        sha(activation[key], "activation." + key)
    for key in ("coverage", "short_validation"):
        text(activation[key], "activation." + key)
        require(Path(activation[key]).is_absolute(), "activation evidence must be locally retrievable")
    try:
        from scripts.validation_evidence import verify_reuse, verify_current_coverage
    except ModuleNotFoundError:
        from validation_evidence import verify_reuse, verify_current_coverage
    coverage = verify_current_coverage(Path(activation["coverage"]))
    short = verify_reuse(Path(activation["coverage"]), Path(activation["short_validation"]), current_only=True)
    require(short["candidate"]["head"] == activation["actual_merge"]
            and short["candidate"]["parents"] == [ACTIVATION_BASELINE, activation["reviewed_head"]],
            "activation actual merge differs from the fixed activation task")
    require(coverage["checkout"]["head_ref"] == ACTIVATION_BRANCH,
            "activation source is not the activation branch")
    completed = _instant(activation["completed_at"], "activation.completed_at")
    require(completed >= _instant(short["finished_at"], "activation short.finished_at"),
            "activation predates successful short validation")
    created = _instant(task["created_at"], "task.created_at")
    adopted = _instant(task["adopted_at"], "task.adopted_at")
    require(completed < created <= adopted, "only tasks created and explicitly adopted after activation qualify")

    repository = Path.cwd().resolve()
    def git(*args):
        result = subprocess.run(["git", "--no-optional-locks", "-c", "safe.directory=" + str(repository),
                                 *args], cwd=repository, capture_output=True, check=False)
        require(result.returncode == 0, "activation Git source/ancestry unavailable or inconsistent")
        return result.stdout
    require(Path(git("rev-parse", "--show-toplevel").decode("utf-8").strip()).resolve() == repository,
            "invoke from the verified repository root")
    actual = git("show", "-s", "--format=%H%n%T%n%P", activation["actual_merge"]).decode("ascii").splitlines()
    require(actual == [activation["actual_merge"], short["candidate"]["tree"],
                       " ".join(short["candidate"]["parents"])], "native activation Git object differs")
    git("merge-base", "--is-ancestor", activation["actual_merge"], candidate["baseline"])
    # The activation artifact must contain this actual consumer, not an
    # unrelated successful PR (including PR71). LF comparison follows Git's
    # physical checkout convention and is not evidence hash substitution.
    for name in ("pr_flow_policy.py", "pr_review_gate.py"):
        local = Path(__file__).with_name(name).read_bytes().replace(b"\r\n", b"\n")
        committed = git("show", activation["actual_merge"] + ":scripts/" + name).replace(b"\r\n", b"\n")
        require(local == committed, "activation consumer source differs: " + name)
    return {"eligible": True, "activation_pending": False, "actual_merge": activation["actual_merge"],
            "activation_ref": adoption["activation"]["report_ref"], "authenticity_verified": False}
