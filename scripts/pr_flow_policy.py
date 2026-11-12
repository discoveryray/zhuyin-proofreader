"""Finite, inactive tiered-review policy shared by the gate and CI consumers.

Hashes verify retrievable bytes, never reviewer identity or authorization.
Activation requires a separately reviewed integration; no input flag enables it.
"""
from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
import re


POLICY_ID = "development-flow/tiered-review/1"
ENABLED = False
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
