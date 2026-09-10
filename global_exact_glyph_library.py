from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence
from unicodedata import normalize
from uuid import uuid4

from exact_glyph_identity import (
    CFF_GLYPH_SHA256,
    GLOBAL_ELIGIBLE_COMPLETE_CFF_RECORDING_V1,
    GLOBAL_ELIGIBLE_SIMPLE_GLYF_V1,
    NON_GLOBAL_ELIGIBLE,
    TTF_GLYF_SHA256,
)
from occurrence_ledger import canonical_bopomofo


GLOBAL_LIBRARY_SCHEMA_VERSION = "1.0"
GLOBAL_LIBRARY_SQLITE_USER_VERSION = 1
GLOBAL_IDENTITY_CONTRACT_VERSION = "1.0"
GLOBAL_PROMOTION_POLICY_VERSION = "1.0"
GLOBAL_SOURCE_EVIDENCE_ID_SCHEMA_VERSION = "1.0"
GLOBAL_MIGRATION_IMPORT_CONTRACT_VERSION = "1.0"
GLOBAL_EXACT_DEPENDENCY_CONTRACT_VERSION = "1.0"
GLOBAL_LIBRARY_FILENAME = "library.sqlite3"
GLOBAL_LIBRARY_DEFAULT_BUSY_TIMEOUT_MS = 500
GLOBAL_LIBRARY_DEFAULT_WRITE_RETRY_LIMIT = 1

ABSENT = "ABSENT"
VALID = "VALID"
LOCKED = "LOCKED"
BUSY = "BUSY"
PERMISSION_DENIED = "PERMISSION_DENIED"
CORRUPT = "CORRUPT"
SCHEMA_INCOMPATIBLE = "SCHEMA_INCOMPATIBLE"
VALIDATION_FAILED = "VALIDATION_FAILED"

VERIFIED_GLOBAL = "VERIFIED_GLOBAL"
QUARANTINED_CONFLICT = "QUARANTINED_CONFLICT"
CANDIDATE = "CANDIDATE"
PROMOTION_READY = "PROMOTION_READY"

GLOBAL_EXACT_PER_PDF_SCOPE_MODE = "per_pdf_exact_identity_v1"
GLOBAL_EXACT_PROVISIONAL_SCOPE_MODE = "provisional_unsealed_v1"
GLOBAL_EXACT_NO_CONFLICT = "NO_CONFLICT"
GLOBAL_EXACT_EVIDENCE_HASH_KEYS = (
    "dependency_contract_version",
    "identity_contract_version",
    "promotion_policy_version",
    "scope_mode",
    "ttf_subset_sha256",
    "cff_subset_sha256",
    "conflict_subset_sha256",
)

DIRECT_VISUAL_ACTUAL = "DIRECT_VISUAL_ACTUAL"
LEGACY_PROJECT_CANDIDATE = "LEGACY_PROJECT_CANDIDATE"
PROPAGATED_PROJECT_OCCURRENCE = "PROPAGATED_PROJECT_OCCURRENCE"

PDF_PAGE_VISUAL = "PDF_PAGE_VISUAL"
LEGACY_IMPORT = "LEGACY_IMPORT"
PROJECT_PROPAGATION = "PROJECT_PROPAGATION"

APPROVED = "APPROVED"
REVOKED = "REVOKED"
EXPLICIT_MANUAL_APPROVAL = "EXPLICIT_MANUAL_APPROVAL"
MIGRATION_REVIEW_APPROVAL = "MIGRATION_REVIEW_APPROVAL"

OPEN = "OPEN"
RESOLVED = "RESOLVED"

INTENT_COMMITTED = "COMMITTED"
INTENT_NO_EFFECT = "NO_EFFECT"
INTENT_CAS_MISS = "CAS_MISS"

MIGRATION_CANDIDATE = "CANDIDATE"
MIGRATION_CONFLICT = "CONFLICT"
MIGRATION_INSUFFICIENT = "INSUFFICIENT"

GLOBAL_GLYPH_KINDS = frozenset({TTF_GLYF_SHA256, CFF_GLYPH_SHA256})
GLOBAL_IDENTITY_ELIGIBILITIES = frozenset({
    GLOBAL_ELIGIBLE_SIMPLE_GLYF_V1,
    GLOBAL_ELIGIBLE_COMPLETE_CFF_RECORDING_V1,
})
GLYPH_TRUTH_STATES = frozenset({
    CANDIDATE,
    PROMOTION_READY,
    VERIFIED_GLOBAL,
    QUARANTINED_CONFLICT,
})
EVIDENCE_CLASSES = frozenset({
    DIRECT_VISUAL_ACTUAL,
    LEGACY_PROJECT_CANDIDATE,
    PROPAGATED_PROJECT_OCCURRENCE,
})
CONFIRMATION_CHANNELS = frozenset({
    PDF_PAGE_VISUAL,
    LEGACY_IMPORT,
    PROJECT_PROPAGATION,
})
APPROVAL_STATUSES = frozenset({APPROVED, REVOKED})
APPROVAL_SOURCES = frozenset({EXPLICIT_MANUAL_APPROVAL, MIGRATION_REVIEW_APPROVAL})
CONFLICT_STATUSES = frozenset({OPEN, RESOLVED})
PROVENANCE_EVENT_TYPES = frozenset({
    "CANDIDATE_CREATED",
    "SOURCE_EVIDENCE_RECORDED",
    "PROMOTION_APPROVED",
    "TRUTH_VERIFIED",
    "CONFLICT_OPENED",
    "CONFLICT_RESOLVED",
    "INTENT_COMMITTED",
    "MIGRATION_CANDIDATE_IMPORTED",
})
INTENT_RESULT_STATES = frozenset({
    INTENT_COMMITTED,
    INTENT_NO_EFFECT,
    INTENT_CAS_MISS,
})
MIGRATION_STATUSES = frozenset({
    MIGRATION_CANDIDATE,
    MIGRATION_CONFLICT,
    MIGRATION_INSUFFICIENT,
})
LEGACY_VERIFICATION_LEVELS = frozenset({
    "USER_VERIFIED_SINGLE",
    "VERIFIED_EXACT_GLYPH",
    "QUARANTINED_CONFLICT",
})

_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class GlobalLibraryError(RuntimeError):
    status = VALIDATION_FAILED

    def __init__(self, message: str, *, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else None
        super().__init__(message)


class GlobalLibraryPathError(GlobalLibraryError):
    pass


class GlobalLibraryAbsentError(GlobalLibraryError):
    status = ABSENT


class GlobalLibraryBusyError(GlobalLibraryError):
    status = BUSY


class GlobalLibraryPermissionError(GlobalLibraryError):
    status = PERMISSION_DENIED


class GlobalLibraryCorruptError(GlobalLibraryError):
    status = CORRUPT


class GlobalLibrarySchemaError(GlobalLibraryError):
    status = SCHEMA_INCOMPATIBLE


class GlobalLibraryValidationError(GlobalLibraryError):
    status = VALIDATION_FAILED


class GlobalLibraryIntentConflictError(GlobalLibraryValidationError):
    pass


@dataclass(frozen=True, order=True)
class GlobalExactGlyphIdentity:
    kind: str
    style_group: str
    glyph_sha256: str
    identity_eligibility: str

    @property
    def tuple(self) -> tuple[str, str, str]:
        return (self.kind, self.style_group, self.glyph_sha256)


@dataclass(frozen=True)
class GlobalGlyphSnapshotRecord:
    glyph_id: str
    identity: GlobalExactGlyphIdentity
    state: str
    active_reading: str
    conflicting_readings: tuple[str, ...]
    direct_source_count: int
    independent_source_count: int
    revision: int
    updated_at: str


@dataclass(frozen=True)
class LegacyCandidateReadConflict:
    """Validated historical candidates suppress reuse without changing storage."""

    glyph_id: str
    identity: GlobalExactGlyphIdentity
    stored_state: str
    conflicting_readings: tuple[str, ...]
    legacy_import_ids: tuple[str, ...]
    diagnostic: str = "LEGACY_V0_READ_CONFLICT: reuse suppressed; durable quarantine/revocation not performed"


@dataclass(frozen=True)
class GlobalExactGlyphSnapshot:
    store_status: str
    schema_version: str
    identity_contract_version: str
    promotion_policy_version: str
    generation: int | None
    trusted_identities: tuple[GlobalGlyphSnapshotRecord, ...]
    quarantined_identities: tuple[GlobalGlyphSnapshotRecord, ...]
    database_path: str
    loaded_at: str
    provenance_event_count: int
    read_side_conflicts: tuple[LegacyCandidateReadConflict, ...] = ()


@dataclass(frozen=True)
class GlobalLibraryDiagnostic:
    status: str
    database_path: str
    message: str
    schema_version: str = ""
    generation: int | None = None


@dataclass(frozen=True)
class GlobalGlyphInspectionRecord:
    """Audit view only; never a decoder donor or an approval request."""

    glyph_id: str
    identity: GlobalExactGlyphIdentity
    stored_state: str
    stored_active_reading: str
    readings: tuple[str, ...]
    global_reuse_allowed: bool
    reuse_block_reason: str
    direct_source_count: int
    independent_source_count: int
    truth: Mapping[str, Any]
    source_evidence: tuple[Mapping[str, Any], ...]
    approvals: tuple[Mapping[str, Any], ...]
    conflicts: tuple[Mapping[str, Any], ...]
    legacy_candidates: tuple[Mapping[str, Any], ...]
    provenance: tuple[Mapping[str, Any], ...]
    read_side_conflict: LegacyCandidateReadConflict | None


@dataclass(frozen=True)
class GlobalLibraryInspectionSnapshot:
    """One validated read transaction, including all candidate and audit rows."""

    store_status: str
    database_path: str
    loaded_at: str
    metadata: Mapping[str, Any]
    records: tuple[GlobalGlyphInspectionRecord, ...]


@dataclass(frozen=True)
class LogicalSubsetRow:
    identity_contract_version: str
    promotion_policy_version: str
    requested_identity: tuple[str, str, str]
    identity_eligibility: str
    effective_state: str
    active_reading: str
    conflicting_readings: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "identity_contract_version": self.identity_contract_version,
            "promotion_policy_version": self.promotion_policy_version,
            "requested_identity": list(self.requested_identity),
            "identity_eligibility": self.identity_eligibility,
            "effective_state": self.effective_state,
            "active_reading": self.active_reading,
            "conflicting_readings": list(self.conflicting_readings),
        }


@dataclass(frozen=True)
class LogicalSubsetDigest:
    rows: tuple[LogicalSubsetRow, ...]
    canonical_json: str
    sha256: str


@dataclass(frozen=True)
class ExactGlyphReuseResolution:
    """Read-only exact-evidence decision for one canonical glyph identity.

    Project/global/static inputs are compared before any nominal precedence is
    applied.  A disagreement or quarantine suppresses every exact donor while
    leaving the caller free to continue through independently gated decoders.
    """

    reading: str
    sources: tuple[str, ...]
    conflict: bool
    conflicting_readings: tuple[str, ...]
    global_effective_state: str
    global_conflicting_readings: tuple[str, ...]


@dataclass(frozen=True)
class GlyphCASResult:
    applied: bool
    glyph_id: str
    expected_revision: int
    observed_revision: int | None
    current_revision: int | None
    generation: int


@dataclass(frozen=True)
class StorageMutation:
    changed: bool
    value: str = ""


@dataclass(frozen=True)
class ProcessedIntentReceipt:
    intent_id: str
    payload_digest: str
    committed_generation: int
    result_state: str
    receipt_digest: str
    already_processed: bool
    mutation_value: str = ""


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise GlobalLibraryValidationError(f"資料無法 canonical JSON 編碼：{exc}") from exc


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _strict_text(value: Any, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise GlobalLibraryValidationError(f"{field} 必須是文字")
    text = value.strip()
    if text != value or normalize("NFKC", text) != text:
        raise GlobalLibraryValidationError(f"{field} 必須已 canonicalize 且不得有外圍空白")
    if not allow_empty and not text:
        raise GlobalLibraryValidationError(f"{field} 不得空白")
    if any(ord(ch) < 32 for ch in text):
        raise GlobalLibraryValidationError(f"{field} 含控制字元")
    return text


def _sha256_text(value: Any, field: str) -> str:
    text = _strict_text(value, field)
    if _SHA256_RE.fullmatch(text) is None:
        raise GlobalLibraryValidationError(f"{field} 必須是 lowercase 64-hex SHA-256")
    return text


def _canonical_reading(value: Any, field: str = "reading") -> str:
    text = _strict_text(value, field)
    if canonical_bopomofo(text) != text:
        raise GlobalLibraryValidationError(f"{field} 必須是 already-canonical Bopomofo")
    return text


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _validate_timestamp(value: Any, field: str) -> str:
    text = _strict_text(value, field)
    if not text.endswith("Z"):
        raise GlobalLibraryValidationError(f"{field} 必須是 UTC Z timestamp")
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as exc:
        raise GlobalLibraryValidationError(f"{field} timestamp 無效") from exc
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise GlobalLibraryValidationError(f"{field} 必須是 UTC timestamp")
    return text


def canonical_global_exact_identity(
    kind: Any,
    style_group: Any,
    glyph_sha256: Any,
    identity_eligibility: Any,
) -> GlobalExactGlyphIdentity:
    canonical_kind = _strict_text(kind, "kind")
    if canonical_kind not in GLOBAL_GLYPH_KINDS:
        raise GlobalLibraryValidationError(f"global glyph kind 不支援：{canonical_kind!r}")
    canonical_style = _strict_text(style_group, "style_group", allow_empty=True)
    canonical_sha = _sha256_text(glyph_sha256, "glyph_sha256")
    eligibility = _strict_text(identity_eligibility, "identity_eligibility")
    if canonical_kind == TTF_GLYF_SHA256:
        if canonical_style:
            raise GlobalLibraryValidationError("TTF global identity 的 style_group 必須空白")
        if eligibility != GLOBAL_ELIGIBLE_SIMPLE_GLYF_V1:
            raise GlobalLibraryValidationError("TTF global identity 缺少 simple-glyf eligibility contract")
    else:
        if not canonical_style:
            raise GlobalLibraryValidationError("CFF global identity 必須有 canonical style_group")
        if eligibility != GLOBAL_ELIGIBLE_COMPLETE_CFF_RECORDING_V1:
            raise GlobalLibraryValidationError("CFF global identity 缺少 complete-recording eligibility contract")
    return GlobalExactGlyphIdentity(
        kind=canonical_kind,
        style_group=canonical_style,
        glyph_sha256=canonical_sha,
        identity_eligibility=eligibility,
    )


def compute_global_glyph_id(
    kind: Any,
    style_group: Any,
    glyph_sha256: Any,
    *,
    identity_eligibility: Any,
    identity_contract_version: Any = GLOBAL_IDENTITY_CONTRACT_VERSION,
) -> str:
    """Hash one already-classified global identity.

    ``identity_eligibility`` is deliberately required.  This storage helper
    must never infer global eligibility from a kind and SHA alone; TTF callers
    obtain eligibility from the Phase 1 structural parser.
    """
    canonical_kind = _strict_text(kind, "kind")
    identity = canonical_global_exact_identity(
        canonical_kind,
        style_group,
        glyph_sha256,
        identity_eligibility,
    )
    contract = _strict_text(identity_contract_version, "identity_contract_version")
    return _canonical_sha256({
        "identity_contract_version": contract,
        "kind": identity.kind,
        "style_group": identity.style_group,
        "glyph_sha256": identity.glyph_sha256,
    })


def canonical_source_evidence_id_payload(
    *,
    glyph_id: Any,
    reading: Any,
    evidence_class: Any,
    source_project_id: Any,
    source_pdf_sha256: Any,
    source_font_program_sha256: Any,
    source_occurrence_id: Any,
    source_review_id: Any,
    decision_snapshot_sha256: Any,
    confirmation_channel: Any,
) -> dict[str, str]:
    """Return the versioned immutable identity of one frozen evidence decision.

    Project/PDF/font/occurrence/review identity, the decision snapshot, evidence
    class, and confirmation channel are immutable evidence-contract fields.
    ``confirmed_at`` and ``ingested_at`` remain separately validated audit
    metadata in ``source_evidence`` and deliberately do not participate here.
    """
    return {
        "evidence_id_schema_version": GLOBAL_SOURCE_EVIDENCE_ID_SCHEMA_VERSION,
        "glyph_id": _sha256_text(glyph_id, "glyph_id"),
        "reading": _canonical_reading(reading),
        "evidence_class": _strict_text(evidence_class, "evidence_class"),
        "source_project_id": _strict_text(source_project_id, "source_project_id"),
        "source_pdf_sha256": _sha256_text(source_pdf_sha256, "source_pdf_sha256"),
        "source_font_program_sha256": _sha256_text(
            source_font_program_sha256,
            "source_font_program_sha256",
        ),
        "source_occurrence_id": _strict_text(source_occurrence_id, "source_occurrence_id"),
        "source_review_id": _strict_text(source_review_id, "source_review_id"),
        "decision_snapshot_sha256": _sha256_text(
            decision_snapshot_sha256,
            "decision_snapshot_sha256",
        ),
        "confirmation_channel": _strict_text(confirmation_channel, "confirmation_channel"),
    }


def compute_source_evidence_id(
    *,
    glyph_id: Any,
    reading: Any,
    evidence_class: Any,
    source_project_id: Any,
    source_pdf_sha256: Any,
    source_font_program_sha256: Any,
    source_occurrence_id: Any,
    source_review_id: Any,
    decision_snapshot_sha256: Any,
    confirmation_channel: Any,
) -> str:
    return _canonical_sha256(canonical_source_evidence_id_payload(
        glyph_id=glyph_id,
        reading=reading,
        evidence_class=evidence_class,
        source_project_id=source_project_id,
        source_pdf_sha256=source_pdf_sha256,
        source_font_program_sha256=source_font_program_sha256,
        source_occurrence_id=source_occurrence_id,
        source_review_id=source_review_id,
        decision_snapshot_sha256=decision_snapshot_sha256,
        confirmation_channel=confirmation_channel,
    ))


def compute_quorum_digest(
    glyph_id: Any,
    reading: Any,
    evidence_ids: Iterable[Any],
    *,
    promotion_policy_version: Any = GLOBAL_PROMOTION_POLICY_VERSION,
) -> str:
    ids = sorted({_sha256_text(value, "quorum evidence_id") for value in evidence_ids})
    if not ids:
        raise GlobalLibraryValidationError("quorum 至少需要一個 evidence_id")
    return _canonical_sha256({
        "glyph_id": _sha256_text(glyph_id, "glyph_id"),
        "reading": _canonical_reading(reading),
        "evidence_ids": ids,
        "promotion_policy_version": _strict_text(
            promotion_policy_version,
            "promotion_policy_version",
        ),
    })


def compute_promotion_approval_id(
    *,
    glyph_id: Any,
    reading: Any,
    quorum_digest: Any,
    promotion_policy_version: Any,
    glyph_revision: Any,
    approval_source: Any,
    status: Any,
) -> str:
    return _canonical_sha256({
        "glyph_id": _sha256_text(glyph_id, "glyph_id"),
        "reading": _canonical_reading(reading),
        "quorum_digest": _sha256_text(quorum_digest, "quorum_digest"),
        "promotion_policy_version": _strict_text(
            promotion_policy_version,
            "promotion_policy_version",
        ),
        "glyph_revision": _nonnegative_int(glyph_revision, "glyph_revision"),
        "approval_source": _strict_text(approval_source, "approval_source"),
        "status": _strict_text(status, "approval status"),
    })


def compute_glyph_conflict_id(
    glyph_id: Any,
    conflicting_readings: Iterable[Any],
    opened_generation: Any,
) -> str:
    readings = sorted({_canonical_reading(value, "conflicting reading") for value in conflicting_readings})
    if len(readings) < 2:
        raise GlobalLibraryValidationError("glyph conflict 至少需要兩個不同 canonical readings")
    return _canonical_sha256({
        "glyph_id": _sha256_text(glyph_id, "glyph_id"),
        "conflicting_readings": readings,
        "opened_generation": _nonnegative_int(opened_generation, "opened_generation"),
    })


def compute_provenance_event_id(
    *,
    transaction_id: Any,
    glyph_id: Any,
    event_type: Any,
    reading: Any,
    evidence_id: Any,
    approval_id: Any,
    payload_digest: Any,
) -> str:
    reading_text = "" if reading is None else _canonical_reading(reading)
    evidence_text = "" if evidence_id is None else _sha256_text(evidence_id, "evidence_id")
    approval_text = "" if approval_id is None else _sha256_text(approval_id, "approval_id")
    return _canonical_sha256({
        "transaction_id": _strict_text(transaction_id, "transaction_id"),
        "glyph_id": _sha256_text(glyph_id, "glyph_id"),
        "event_type": _strict_text(event_type, "event_type"),
        "reading": reading_text,
        "evidence_id": evidence_text,
        "approval_id": approval_text,
        "payload_digest": _sha256_text(payload_digest, "payload_digest"),
    })


def compute_processed_intent_receipt(
    intent_id: Any,
    payload_digest: Any,
    committed_generation: Any,
    result_state: Any,
) -> str:
    return _canonical_sha256({
        "intent_id": _strict_text(intent_id, "intent_id"),
        "payload_digest": _sha256_text(payload_digest, "payload_digest"),
        "committed_generation": _nonnegative_int(
            committed_generation,
            "committed_generation",
        ),
        "result_state": _strict_text(result_state, "result_state"),
    })


def _legacy_v0_migration_import_id(
    *,
    glyph_id: Any,
    reading: Any,
    legacy_project_id: Any,
    legacy_project_sha256: Any,
    legacy_evidence_sha256: Any,
    old_verification_level: Any,
) -> str:
    return _canonical_sha256({
        "migration_schema_version": "1.0",  # Frozen Phase 2 draft algorithm.
        "glyph_id": _sha256_text(glyph_id, "glyph_id"),
        "reading": _canonical_reading(reading),
        "legacy_project_id": _strict_text(legacy_project_id, "legacy_project_id"),
        "legacy_project_sha256": _sha256_text(
            legacy_project_sha256,
            "legacy_project_sha256",
        ),
        "legacy_evidence_sha256": _sha256_text(
            legacy_evidence_sha256,
            "legacy_evidence_sha256",
        ),
        "old_verification_level": _strict_text(
            old_verification_level,
            "old_verification_level",
        ),
    })


def compute_legacy_row_key(*, glyph_id: Any, reading: Any, old_verification_level: Any) -> str:
    level = _strict_text(old_verification_level, "old_verification_level")
    if level not in LEGACY_VERIFICATION_LEVELS:
        raise GlobalLibraryValidationError("unknown legacy verification level")
    return _canonical_sha256({
        "glyph_id": _sha256_text(glyph_id, "glyph_id"),
        "reading": _canonical_reading(reading), "old_verification_level": level,
    })


def compute_migration_import_id(
    *, glyph_id: Any, reading: Any, legacy_project_id: Any,
    legacy_project_sha256: Any, legacy_evidence_sha256: Any, old_verification_level: Any,
) -> str:
    return _canonical_sha256({
        "migration_import_contract_version": GLOBAL_MIGRATION_IMPORT_CONTRACT_VERSION,
        "glyph_id": _sha256_text(glyph_id, "glyph_id"),
        "reading": _canonical_reading(reading),
        "legacy_project_id": _strict_text(legacy_project_id, "legacy_project_id"),
        "legacy_project_sha256": _sha256_text(legacy_project_sha256, "legacy_project_sha256"),
        "legacy_evidence_sha256": _sha256_text(legacy_evidence_sha256, "legacy_evidence_sha256"),
        "old_verification_level": _strict_text(old_verification_level, "old_verification_level"),
        "legacy_row_key": compute_legacy_row_key(
            glyph_id=glyph_id, reading=reading, old_verification_level=old_verification_level),
    })


def _stored_migration_contract(import_id: str, **identity_args: Any) -> str:
    """Finite ID adapter; neither format is exempt from conflict validation.

    legacy-v0 is exactly the frozen Phase 2 algorithm, without Phase 5 receipts.
    Its original rows remain immutable, including status, after later deliveries.
    Unknown IDs fail closed; baseline validator omissions are not a contract.
    """
    if import_id == compute_migration_import_id(**identity_args):
        return GLOBAL_MIGRATION_IMPORT_CONTRACT_VERSION
    if import_id == _legacy_v0_migration_import_id(**identity_args):
        return "legacy-v0"
    raise GlobalLibraryValidationError("migration import_id 無法重算")


def _nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise GlobalLibraryValidationError(f"{field} 必須是 non-negative integer")
    return value


def _canonical_string_list(raw: Any, field: str, *, minimum: int = 0) -> tuple[str, ...]:
    if not isinstance(raw, str):
        raise GlobalLibraryValidationError(f"{field} 必須是 canonical JSON string")
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise GlobalLibraryValidationError(f"{field} JSON 無效") from exc
    if not isinstance(parsed, list):
        raise GlobalLibraryValidationError(f"{field} 必須是 JSON list")
    values = tuple(_strict_text(value, field) for value in parsed)
    if tuple(sorted(set(values))) != values:
        raise GlobalLibraryValidationError(f"{field} 必須排序且不得重複")
    if len(values) < minimum:
        raise GlobalLibraryValidationError(f"{field} 至少需要 {minimum} 筆")
    if _canonical_json(list(values)) != raw:
        raise GlobalLibraryValidationError(f"{field} 必須使用 canonical JSON encoding")
    return values


def _canonical_reading_list(raw: Any, field: str, *, minimum: int = 0) -> tuple[str, ...]:
    values = _canonical_string_list(raw, field, minimum=minimum)
    for value in values:
        _canonical_reading(value, field)
    return values


_TABLE_SQL: dict[str, str] = {
    "library_meta": f"""
        CREATE TABLE library_meta (
            meta_id INTEGER PRIMARY KEY CHECK (meta_id = 1),
            schema_version TEXT NOT NULL CHECK (schema_version = '{GLOBAL_LIBRARY_SCHEMA_VERSION}'),
            identity_contract_version TEXT NOT NULL CHECK (identity_contract_version = '{GLOBAL_IDENTITY_CONTRACT_VERSION}'),
            promotion_policy_version TEXT NOT NULL CHECK (promotion_policy_version = '{GLOBAL_PROMOTION_POLICY_VERSION}'),
            generation INTEGER NOT NULL CHECK (typeof(generation) = 'integer' AND generation >= 0),
            created_at TEXT NOT NULL CHECK (length(created_at) > 0),
            updated_at TEXT NOT NULL CHECK (length(updated_at) > 0)
        ) STRICT
    """,
    "glyph_truth": f"""
        CREATE TABLE glyph_truth (
            glyph_id TEXT PRIMARY KEY CHECK (length(glyph_id) = 64 AND glyph_id NOT GLOB '*[^0-9a-f]*'),
            identity_contract_version TEXT NOT NULL CHECK (identity_contract_version = '{GLOBAL_IDENTITY_CONTRACT_VERSION}'),
            identity_eligibility TEXT NOT NULL,
            kind TEXT NOT NULL,
            style_group TEXT NOT NULL CHECK (style_group = trim(style_group)),
            glyph_sha256 TEXT NOT NULL CHECK (length(glyph_sha256) = 64 AND glyph_sha256 NOT GLOB '*[^0-9a-f]*'),
            state TEXT NOT NULL CHECK (state IN ('{CANDIDATE}', '{PROMOTION_READY}', '{VERIFIED_GLOBAL}', '{QUARANTINED_CONFLICT}')),
            active_reading TEXT,
            direct_source_count INTEGER NOT NULL CHECK (typeof(direct_source_count) = 'integer' AND direct_source_count >= 0),
            independent_source_count INTEGER NOT NULL CHECK (typeof(independent_source_count) = 'integer' AND independent_source_count >= 0),
            revision INTEGER NOT NULL CHECK (typeof(revision) = 'integer' AND revision >= 0),
            created_at TEXT NOT NULL CHECK (length(created_at) > 0),
            updated_at TEXT NOT NULL CHECK (length(updated_at) > 0),
            CHECK (
                (kind = '{TTF_GLYF_SHA256}' AND style_group = '' AND identity_eligibility = '{GLOBAL_ELIGIBLE_SIMPLE_GLYF_V1}')
                OR
                (kind = '{CFF_GLYPH_SHA256}' AND length(style_group) > 0 AND identity_eligibility = '{GLOBAL_ELIGIBLE_COMPLETE_CFF_RECORDING_V1}')
            ),
            CHECK (
                (state = '{VERIFIED_GLOBAL}' AND active_reading IS NOT NULL AND length(active_reading) > 0)
                OR
                (state <> '{VERIFIED_GLOBAL}' AND active_reading IS NULL)
            )
        ) STRICT
    """,
    "source_evidence": f"""
        CREATE TABLE source_evidence (
            evidence_id TEXT PRIMARY KEY CHECK (length(evidence_id) = 64 AND evidence_id NOT GLOB '*[^0-9a-f]*'),
            glyph_id TEXT NOT NULL,
            reading TEXT NOT NULL CHECK (length(reading) > 0),
            evidence_class TEXT NOT NULL CHECK (evidence_class IN ('{DIRECT_VISUAL_ACTUAL}', '{LEGACY_PROJECT_CANDIDATE}', '{PROPAGATED_PROJECT_OCCURRENCE}')),
            counts_toward_global_quorum INTEGER NOT NULL CHECK (counts_toward_global_quorum IN (0, 1)),
            source_project_id TEXT NOT NULL CHECK (length(source_project_id) > 0 AND source_project_id = trim(source_project_id)),
            source_pdf_sha256 TEXT NOT NULL CHECK (length(source_pdf_sha256) = 64 AND source_pdf_sha256 NOT GLOB '*[^0-9a-f]*'),
            source_font_program_sha256 TEXT NOT NULL CHECK (length(source_font_program_sha256) = 64 AND source_font_program_sha256 NOT GLOB '*[^0-9a-f]*'),
            source_occurrence_id TEXT NOT NULL CHECK (length(source_occurrence_id) > 0 AND source_occurrence_id = trim(source_occurrence_id)),
            source_review_id TEXT NOT NULL CHECK (length(source_review_id) > 0 AND source_review_id = trim(source_review_id)),
            decision_snapshot_sha256 TEXT NOT NULL CHECK (length(decision_snapshot_sha256) = 64 AND decision_snapshot_sha256 NOT GLOB '*[^0-9a-f]*'),
            confirmation_channel TEXT NOT NULL CHECK (confirmation_channel IN ('{PDF_PAGE_VISUAL}', '{LEGACY_IMPORT}', '{PROJECT_PROPAGATION}')),
            confirmed_at TEXT NOT NULL CHECK (length(confirmed_at) > 0),
            ingested_at TEXT NOT NULL CHECK (length(ingested_at) > 0),
            FOREIGN KEY (glyph_id) REFERENCES glyph_truth(glyph_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
            CHECK (
                (evidence_class = '{DIRECT_VISUAL_ACTUAL}' AND counts_toward_global_quorum = 1 AND confirmation_channel = '{PDF_PAGE_VISUAL}')
                OR
                (evidence_class = '{LEGACY_PROJECT_CANDIDATE}' AND counts_toward_global_quorum = 0 AND confirmation_channel = '{LEGACY_IMPORT}')
                OR
                (evidence_class = '{PROPAGATED_PROJECT_OCCURRENCE}' AND counts_toward_global_quorum = 0 AND confirmation_channel = '{PROJECT_PROPAGATION}')
            )
        ) STRICT
    """,
    "promotion_approval": f"""
        CREATE TABLE promotion_approval (
            approval_id TEXT PRIMARY KEY CHECK (length(approval_id) = 64 AND approval_id NOT GLOB '*[^0-9a-f]*'),
            glyph_id TEXT NOT NULL,
            reading TEXT NOT NULL CHECK (length(reading) > 0),
            quorum_digest TEXT NOT NULL CHECK (length(quorum_digest) = 64 AND quorum_digest NOT GLOB '*[^0-9a-f]*'),
            quorum_evidence_ids_json TEXT NOT NULL CHECK (length(quorum_evidence_ids_json) > 0),
            promotion_policy_version TEXT NOT NULL CHECK (promotion_policy_version = '{GLOBAL_PROMOTION_POLICY_VERSION}'),
            glyph_revision INTEGER NOT NULL CHECK (typeof(glyph_revision) = 'integer' AND glyph_revision >= 0),
            approval_source TEXT NOT NULL CHECK (approval_source IN ('{EXPLICIT_MANUAL_APPROVAL}', '{MIGRATION_REVIEW_APPROVAL}')),
            status TEXT NOT NULL CHECK (status IN ('{APPROVED}', '{REVOKED}')),
            approved_at TEXT NOT NULL CHECK (length(approved_at) > 0),
            FOREIGN KEY (glyph_id) REFERENCES glyph_truth(glyph_id) ON UPDATE RESTRICT ON DELETE RESTRICT
        ) STRICT
    """,
    "glyph_conflict": f"""
        CREATE TABLE glyph_conflict (
            conflict_id TEXT PRIMARY KEY CHECK (length(conflict_id) = 64 AND conflict_id NOT GLOB '*[^0-9a-f]*'),
            glyph_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('{OPEN}', '{RESOLVED}')),
            conflicting_readings_json TEXT NOT NULL CHECK (length(conflicting_readings_json) > 0),
            first_event_at TEXT NOT NULL CHECK (length(first_event_at) > 0),
            last_event_at TEXT NOT NULL CHECK (length(last_event_at) > 0),
            opened_generation INTEGER NOT NULL CHECK (typeof(opened_generation) = 'integer' AND opened_generation >= 0),
            resolved_generation INTEGER CHECK (resolved_generation IS NULL OR (typeof(resolved_generation) = 'integer' AND resolved_generation >= opened_generation)),
            resolution_reference TEXT,
            FOREIGN KEY (glyph_id) REFERENCES glyph_truth(glyph_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
            CHECK (
                (status = '{OPEN}' AND resolved_generation IS NULL AND resolution_reference IS NULL)
                OR
                (status = '{RESOLVED}' AND resolved_generation IS NOT NULL AND resolution_reference IS NOT NULL AND length(resolution_reference) > 0)
            )
        ) STRICT
    """,
    "provenance_event": """
        CREATE TABLE provenance_event (
            event_id TEXT PRIMARY KEY CHECK (length(event_id) = 64 AND event_id NOT GLOB '*[^0-9a-f]*'),
            transaction_id TEXT NOT NULL CHECK (length(transaction_id) > 0 AND transaction_id = trim(transaction_id)),
            glyph_id TEXT NOT NULL,
            event_type TEXT NOT NULL CHECK (event_type IN ('CANDIDATE_CREATED', 'SOURCE_EVIDENCE_RECORDED', 'PROMOTION_APPROVED', 'TRUTH_VERIFIED', 'CONFLICT_OPENED', 'CONFLICT_RESOLVED', 'INTENT_COMMITTED', 'MIGRATION_CANDIDATE_IMPORTED')),
            reading TEXT,
            evidence_id TEXT,
            approval_id TEXT,
            payload_digest TEXT NOT NULL CHECK (length(payload_digest) = 64 AND payload_digest NOT GLOB '*[^0-9a-f]*'),
            created_at TEXT NOT NULL CHECK (length(created_at) > 0),
            FOREIGN KEY (glyph_id) REFERENCES glyph_truth(glyph_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
            FOREIGN KEY (evidence_id) REFERENCES source_evidence(evidence_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
            FOREIGN KEY (approval_id) REFERENCES promotion_approval(approval_id) ON UPDATE RESTRICT ON DELETE RESTRICT
        ) STRICT
    """,
    "processed_intent": f"""
        CREATE TABLE processed_intent (
            intent_id TEXT PRIMARY KEY CHECK (length(intent_id) > 0 AND intent_id = trim(intent_id)),
            payload_digest TEXT NOT NULL CHECK (length(payload_digest) = 64 AND payload_digest NOT GLOB '*[^0-9a-f]*'),
            committed_generation INTEGER NOT NULL CHECK (typeof(committed_generation) = 'integer' AND committed_generation >= 0),
            result_state TEXT NOT NULL CHECK (result_state IN ('{INTENT_COMMITTED}', '{INTENT_NO_EFFECT}', '{INTENT_CAS_MISS}')),
            receipt_digest TEXT NOT NULL CHECK (length(receipt_digest) = 64 AND receipt_digest NOT GLOB '*[^0-9a-f]*'),
            processed_at TEXT NOT NULL CHECK (length(processed_at) > 0)
        ) STRICT
    """,
    "migration_candidate": f"""
        CREATE TABLE migration_candidate (
            import_id TEXT PRIMARY KEY CHECK (length(import_id) = 64 AND import_id NOT GLOB '*[^0-9a-f]*'),
            glyph_id TEXT NOT NULL,
            reading TEXT NOT NULL CHECK (length(reading) > 0),
            legacy_project_id TEXT NOT NULL CHECK (length(legacy_project_id) > 0 AND legacy_project_id = trim(legacy_project_id)),
            legacy_project_sha256 TEXT NOT NULL CHECK (length(legacy_project_sha256) = 64 AND legacy_project_sha256 NOT GLOB '*[^0-9a-f]*'),
            legacy_evidence_sha256 TEXT NOT NULL CHECK (length(legacy_evidence_sha256) = 64 AND legacy_evidence_sha256 NOT GLOB '*[^0-9a-f]*'),
            old_verification_level TEXT NOT NULL CHECK (old_verification_level IN ('USER_VERIFIED_SINGLE', 'VERIFIED_EXACT_GLYPH', 'QUARANTINED_CONFLICT')),
            status TEXT NOT NULL CHECK (status IN ('{MIGRATION_CANDIDATE}', '{MIGRATION_CONFLICT}', '{MIGRATION_INSUFFICIENT}')),
            imported_at TEXT NOT NULL CHECK (length(imported_at) > 0),
            FOREIGN KEY (glyph_id) REFERENCES glyph_truth(glyph_id) ON UPDATE RESTRICT ON DELETE RESTRICT
        ) STRICT
    """,
}

_INDEX_SQL: dict[str, str] = {
    "ux_glyph_truth_identity": "CREATE UNIQUE INDEX ux_glyph_truth_identity ON glyph_truth(kind, style_group, glyph_sha256)",
    "ix_glyph_truth_state": "CREATE INDEX ix_glyph_truth_state ON glyph_truth(state, glyph_id)",
    "ix_source_evidence_glyph_reading": "CREATE INDEX ix_source_evidence_glyph_reading ON source_evidence(glyph_id, reading, evidence_id)",
    "ux_source_evidence_source_decision": "CREATE UNIQUE INDEX ux_source_evidence_source_decision ON source_evidence(glyph_id, source_project_id, source_pdf_sha256, source_font_program_sha256, source_occurrence_id, source_review_id, decision_snapshot_sha256, evidence_class, reading)",
    "ix_promotion_approval_glyph_revision": "CREATE INDEX ix_promotion_approval_glyph_revision ON promotion_approval(glyph_id, glyph_revision, status)",
    "ux_promotion_approval_active": f"CREATE UNIQUE INDEX ux_promotion_approval_active ON promotion_approval(glyph_id, glyph_revision) WHERE status = '{APPROVED}'",
    "ix_glyph_conflict_glyph_status": "CREATE INDEX ix_glyph_conflict_glyph_status ON glyph_conflict(glyph_id, status)",
    "ux_glyph_conflict_open": f"CREATE UNIQUE INDEX ux_glyph_conflict_open ON glyph_conflict(glyph_id) WHERE status = '{OPEN}'",
    "ix_provenance_event_glyph": "CREATE INDEX ix_provenance_event_glyph ON provenance_event(glyph_id, created_at, event_id)",
    "ix_provenance_event_transaction": "CREATE INDEX ix_provenance_event_transaction ON provenance_event(transaction_id, event_id)",
    "ix_processed_intent_generation": "CREATE INDEX ix_processed_intent_generation ON processed_intent(committed_generation, intent_id)",
    "ix_migration_candidate_glyph": "CREATE INDEX ix_migration_candidate_glyph ON migration_candidate(glyph_id, status, import_id)",
}

_REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
    "library_meta": (
        "meta_id", "schema_version", "identity_contract_version",
        "promotion_policy_version", "generation", "created_at", "updated_at",
    ),
    "glyph_truth": (
        "glyph_id", "identity_contract_version", "identity_eligibility", "kind",
        "style_group", "glyph_sha256", "state", "active_reading",
        "direct_source_count", "independent_source_count", "revision",
        "created_at", "updated_at",
    ),
    "source_evidence": (
        "evidence_id", "glyph_id", "reading", "evidence_class",
        "counts_toward_global_quorum", "source_project_id", "source_pdf_sha256",
        "source_font_program_sha256", "source_occurrence_id", "source_review_id",
        "decision_snapshot_sha256", "confirmation_channel", "confirmed_at", "ingested_at",
    ),
    "promotion_approval": (
        "approval_id", "glyph_id", "reading", "quorum_digest",
        "quorum_evidence_ids_json", "promotion_policy_version", "glyph_revision",
        "approval_source", "status", "approved_at",
    ),
    "glyph_conflict": (
        "conflict_id", "glyph_id", "status", "conflicting_readings_json",
        "first_event_at", "last_event_at", "opened_generation",
        "resolved_generation", "resolution_reference",
    ),
    "provenance_event": (
        "event_id", "transaction_id", "glyph_id", "event_type", "reading",
        "evidence_id", "approval_id", "payload_digest", "created_at",
    ),
    "processed_intent": (
        "intent_id", "payload_digest", "committed_generation", "result_state",
        "receipt_digest", "processed_at",
    ),
    "migration_candidate": (
        "import_id", "glyph_id", "reading", "legacy_project_id",
        "legacy_project_sha256", "legacy_evidence_sha256",
        "old_verification_level", "status", "imported_at",
    ),
}

_PRIMARY_KEYS = {
    "library_meta": ("meta_id",),
    "glyph_truth": ("glyph_id",),
    "source_evidence": ("evidence_id",),
    "promotion_approval": ("approval_id",),
    "glyph_conflict": ("conflict_id",),
    "provenance_event": ("event_id",),
    "processed_intent": ("intent_id",),
    "migration_candidate": ("import_id",),
}

_FOREIGN_KEYS: dict[str, frozenset[tuple[str, str, str, str, str]]] = {
    "library_meta": frozenset(),
    "glyph_truth": frozenset(),
    "source_evidence": frozenset({("glyph_id", "glyph_truth", "glyph_id", "RESTRICT", "RESTRICT")}),
    "promotion_approval": frozenset({("glyph_id", "glyph_truth", "glyph_id", "RESTRICT", "RESTRICT")}),
    "glyph_conflict": frozenset({("glyph_id", "glyph_truth", "glyph_id", "RESTRICT", "RESTRICT")}),
    "provenance_event": frozenset({
        ("glyph_id", "glyph_truth", "glyph_id", "RESTRICT", "RESTRICT"),
        ("evidence_id", "source_evidence", "evidence_id", "RESTRICT", "RESTRICT"),
        ("approval_id", "promotion_approval", "approval_id", "RESTRICT", "RESTRICT"),
    }),
    "processed_intent": frozenset(),
    "migration_candidate": frozenset({("glyph_id", "glyph_truth", "glyph_id", "RESTRICT", "RESTRICT")}),
}

_INDEX_COLUMNS: dict[str, tuple[str, ...]] = {
    "ux_glyph_truth_identity": ("kind", "style_group", "glyph_sha256"),
    "ix_glyph_truth_state": ("state", "glyph_id"),
    "ix_source_evidence_glyph_reading": ("glyph_id", "reading", "evidence_id"),
    "ux_source_evidence_source_decision": (
        "glyph_id", "source_project_id", "source_pdf_sha256",
        "source_font_program_sha256", "source_occurrence_id", "source_review_id",
        "decision_snapshot_sha256", "evidence_class", "reading",
    ),
    "ix_promotion_approval_glyph_revision": ("glyph_id", "glyph_revision", "status"),
    "ux_promotion_approval_active": ("glyph_id", "glyph_revision"),
    "ix_glyph_conflict_glyph_status": ("glyph_id", "status"),
    "ux_glyph_conflict_open": ("glyph_id",),
    "ix_provenance_event_glyph": ("glyph_id", "created_at", "event_id"),
    "ix_provenance_event_transaction": ("transaction_id", "event_id"),
    "ix_processed_intent_generation": ("committed_generation", "intent_id"),
    "ix_migration_candidate_glyph": ("glyph_id", "status", "import_id"),
}


def resolve_global_exact_glyph_library_path(
    global_library_root: Path | str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> Path:
    if global_library_root is not None:
        root = Path(global_library_root)
        if not root.is_absolute():
            raise GlobalLibraryPathError("explicit global_library_root 必須是 absolute path")
        return root / GLOBAL_LIBRARY_FILENAME
    environment = os.environ if environ is None else environ
    raw = str(environment.get("LOCALAPPDATA") or "").strip()
    if not raw:
        raise GlobalLibraryPathError("缺少 LOCALAPPDATA；不得 fallback 到 cwd")
    local_app_data = Path(raw)
    if not local_app_data.is_absolute():
        raise GlobalLibraryPathError("LOCALAPPDATA 必須是 absolute path")
    return (
        local_app_data
        / "DiscoveryRay"
        / "ZhuyinProofreader"
        / "GlobalExactGlyphLibrary"
        / GLOBAL_LIBRARY_FILENAME
    )


def _normal_sql(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().rstrip(";")).strip()


def _path_is_absent(path: Path) -> bool:
    try:
        info = path.stat()
    except FileNotFoundError:
        return True
    except PermissionError as exc:
        raise GlobalLibraryPermissionError(
            f"global library path 無法存取：{path}",
            path=path,
        ) from exc
    if not path.is_file():
        raise GlobalLibraryValidationError(
            f"global library path 不是一般檔案：{path}",
            path=path,
        )
    if info.st_size < 0:
        raise GlobalLibraryValidationError("global library 檔案大小無效", path=path)
    return False


def _sqlite_uri(path: Path, mode: str) -> str:
    return path.resolve(strict=False).as_uri() + f"?mode={mode}"


def _configure_connection(connection: sqlite3.Connection, busy_timeout_ms: int) -> None:
    connection.row_factory = sqlite3.Row
    connection.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
    connection.execute("PRAGMA foreign_keys = ON")
    if int(connection.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
        raise GlobalLibraryValidationError("SQLite foreign_keys 無法啟用")


def _connect_existing(path: Path, *, read_only: bool, busy_timeout_ms: int) -> sqlite3.Connection:
    """Open the store through SQLite's VFS, which is the sole WAL authority.

    The ``-wal`` file is a live SQLite-managed sidecar and may appear, grow,
    truncate, or disappear between ordinary filesystem calls.  Callers must
    validate only after SQLite has established the corresponding transaction;
    no out-of-band sidecar stat/open preflight is safe here.
    """
    mode = "ro" if read_only else "rw"
    connection = sqlite3.connect(
        _sqlite_uri(path, mode),
        uri=True,
        timeout=busy_timeout_ms / 1000.0,
        isolation_level=None,
    )
    _configure_connection(connection, busy_timeout_ms)
    return connection


def _begin_immediate_with_bounded_retry(
    connection: sqlite3.Connection,
    path: Path,
    retry_limit: int,
) -> None:
    last_error: sqlite3.OperationalError | None = None
    for _attempt in range(retry_limit + 1):
        try:
            connection.execute("BEGIN IMMEDIATE")
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                raise
            last_error = exc
    raise GlobalLibraryBusyError(
        f"BEGIN IMMEDIATE bounded retries exhausted：{last_error}",
        path=path,
    )


def _translated_error(exc: BaseException, path: Path) -> GlobalLibraryError:
    if isinstance(exc, GlobalLibraryError):
        return exc
    if isinstance(exc, PermissionError):
        return GlobalLibraryPermissionError(str(exc), path=path)
    message = str(exc)
    lowered = message.lower()
    if isinstance(exc, sqlite3.OperationalError) and ("locked" in lowered or "busy" in lowered):
        return GlobalLibraryBusyError(f"global library locked/busy：{message}", path=path)
    if (
        "permission" in lowered
        or "readonly" in lowered
        or "unable to open database file" in lowered
        or "access is denied" in lowered
    ):
        return GlobalLibraryPermissionError(f"global library permission denied：{message}", path=path)
    if (
        isinstance(exc, sqlite3.DatabaseError)
        and (
            "malformed" in lowered
            or "not a database" in lowered
            or "disk image" in lowered
            or "disk i/o" in lowered
            or "file is encrypted" in lowered
        )
    ):
        return GlobalLibraryCorruptError(f"global library corrupt：{message}", path=path)
    if isinstance(exc, sqlite3.IntegrityError):
        return GlobalLibraryValidationError(f"global library constraint failure：{message}", path=path)
    if isinstance(exc, sqlite3.Error):
        return GlobalLibraryValidationError(f"global library SQLite failure：{message}", path=path)
    return GlobalLibraryValidationError(f"global library failure：{type(exc).__name__}: {message}", path=path)


def _schema_sql(connection: sqlite3.Connection, object_type: str) -> dict[str, str]:
    rows = connection.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = ? AND sql IS NOT NULL",
        (object_type,),
    ).fetchall()
    return {str(row["name"]): str(row["sql"]) for row in rows if not str(row["name"]).startswith("sqlite_")}


def _validate_schema(connection: sqlite3.Connection, path: Path) -> None:
    user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if user_version != GLOBAL_LIBRARY_SQLITE_USER_VERSION:
        raise GlobalLibrarySchemaError(
            f"unknown global library user_version：{user_version}",
            path=path,
        )
    journal_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
    if journal_mode != "wal":
        raise GlobalLibraryValidationError(
            f"global library journal_mode 必須是 WAL，observed={journal_mode}",
            path=path,
        )

    undeclared_objects = [
        (str(row["type"]), str(row["name"]))
        for row in connection.execute(
            """
            SELECT type, name
            FROM sqlite_master
            WHERE type IN ('view', 'trigger') AND name NOT LIKE 'sqlite_%'
            ORDER BY type, name
            """
        ).fetchall()
    ]
    if undeclared_objects:
        raise GlobalLibrarySchemaError(
            f"global library 有未宣告 schema objects：{undeclared_objects}",
            path=path,
        )

    tables = _schema_sql(connection, "table")
    if set(tables) != set(_TABLE_SQL):
        raise GlobalLibrarySchemaError(
            f"global library table roster 不相容：expected={sorted(_TABLE_SQL)} observed={sorted(tables)}",
            path=path,
        )
    indexes = _schema_sql(connection, "index")
    if set(indexes) != set(_INDEX_SQL):
        raise GlobalLibrarySchemaError(
            f"global library index roster 不相容：expected={sorted(_INDEX_SQL)} observed={sorted(indexes)}",
            path=path,
        )
    for name, expected_sql in _TABLE_SQL.items():
        if _normal_sql(tables[name]) != _normal_sql(expected_sql):
            raise GlobalLibrarySchemaError(f"global library table contract drift：{name}", path=path)
        columns = tuple(
            str(row["name"])
            for row in connection.execute(f'PRAGMA table_xinfo("{name}")').fetchall()
            if int(row["hidden"]) == 0
        )
        if columns != _REQUIRED_COLUMNS[name]:
            raise GlobalLibrarySchemaError(
                f"global library column contract drift：{name}",
                path=path,
            )
        pk_columns = tuple(
            str(row["name"])
            for row in sorted(
                connection.execute(f'PRAGMA table_info("{name}")').fetchall(),
                key=lambda item: int(item["pk"]) if int(item["pk"]) else 999,
            )
            if int(row["pk"])
        )
        if pk_columns != _PRIMARY_KEYS[name]:
            raise GlobalLibrarySchemaError(f"global library primary key drift：{name}", path=path)
        foreign_keys = frozenset(
            (
                str(row["from"]),
                str(row["table"]),
                str(row["to"]),
                str(row["on_update"]),
                str(row["on_delete"]),
            )
            for row in connection.execute(f'PRAGMA foreign_key_list("{name}")').fetchall()
        )
        if foreign_keys != _FOREIGN_KEYS[name]:
            raise GlobalLibrarySchemaError(f"global library foreign key drift：{name}", path=path)

    table_list = {
        str(row["name"]): int(row["strict"])
        for row in connection.execute("PRAGMA table_list").fetchall()
        if str(row["name"]) in _TABLE_SQL
    }
    if table_list != {name: 1 for name in _TABLE_SQL}:
        raise GlobalLibrarySchemaError("global library tables 必須全部是 STRICT", path=path)

    for name, expected_sql in _INDEX_SQL.items():
        if _normal_sql(indexes[name]) != _normal_sql(expected_sql):
            raise GlobalLibrarySchemaError(f"global library index contract drift：{name}", path=path)
        columns = tuple(
            str(row["name"])
            for row in connection.execute(f'PRAGMA index_info("{name}")').fetchall()
        )
        if columns != _INDEX_COLUMNS[name]:
            raise GlobalLibrarySchemaError(f"global library index columns drift：{name}", path=path)


def _maximum_independent_source_count(rows: Sequence[Mapping[str, Any]]) -> int:
    axes = sorted({
        (
            str(row["source_project_id"]),
            str(row["source_pdf_sha256"]),
            str(row["source_font_program_sha256"]),
        )
        for row in rows
        if int(row["counts_toward_global_quorum"]) == 1
    })

    @lru_cache(maxsize=None)
    def visit(
        index: int,
        projects: frozenset[str],
        pdfs: frozenset[str],
        fonts: frozenset[str],
    ) -> int:
        if index >= len(axes):
            return 0
        project, pdf, font = axes[index]
        best = visit(index + 1, projects, pdfs, fonts)
        if project not in projects and pdf not in pdfs and font not in fonts:
            best = max(
                best,
                1 + visit(
                    index + 1,
                    projects | {project},
                    pdfs | {pdf},
                    fonts | {font},
                ),
            )
        return best

    return visit(0, frozenset(), frozenset(), frozenset())


def _validate_rows(connection: sqlite3.Connection, path: Path) -> dict[str, Any]:
    meta_rows = connection.execute("SELECT * FROM library_meta ORDER BY meta_id").fetchall()
    if len(meta_rows) != 1 or int(meta_rows[0]["meta_id"]) != 1:
        raise GlobalLibraryValidationError("library_meta 必須 exactly one singleton row", path=path)
    meta = dict(meta_rows[0])
    if meta["schema_version"] != GLOBAL_LIBRARY_SCHEMA_VERSION:
        raise GlobalLibrarySchemaError("library_meta schema_version 不相容", path=path)
    if meta["identity_contract_version"] != GLOBAL_IDENTITY_CONTRACT_VERSION:
        raise GlobalLibrarySchemaError("library_meta identity contract 不相容", path=path)
    if meta["promotion_policy_version"] != GLOBAL_PROMOTION_POLICY_VERSION:
        raise GlobalLibrarySchemaError("library_meta promotion policy 不相容", path=path)
    generation = _nonnegative_int(meta["generation"], "library_meta.generation")
    _validate_timestamp(meta["created_at"], "library_meta.created_at")
    _validate_timestamp(meta["updated_at"], "library_meta.updated_at")

    glyph_rows = [dict(row) for row in connection.execute("SELECT * FROM glyph_truth ORDER BY glyph_id")]
    glyphs: dict[str, dict[str, Any]] = {}
    for row in glyph_rows:
        glyph_id = _sha256_text(row["glyph_id"], "glyph_truth.glyph_id")
        if row["identity_contract_version"] != GLOBAL_IDENTITY_CONTRACT_VERSION:
            raise GlobalLibrarySchemaError("glyph_truth identity contract 不相容", path=path)
        identity = canonical_global_exact_identity(
            row["kind"],
            row["style_group"],
            row["glyph_sha256"],
            row["identity_eligibility"],
        )
        recomputed = compute_global_glyph_id(
            identity.kind,
            identity.style_group,
            identity.glyph_sha256,
            identity_contract_version=row["identity_contract_version"],
            identity_eligibility=identity.identity_eligibility,
        )
        if recomputed != glyph_id:
            raise GlobalLibraryValidationError(f"glyph_id 無法重算：{glyph_id}", path=path)
        state = _strict_text(row["state"], "glyph_truth.state")
        if state not in GLYPH_TRUTH_STATES:
            raise GlobalLibraryValidationError(f"glyph_truth state 未知：{state}", path=path)
        active = row["active_reading"]
        if state == VERIFIED_GLOBAL:
            _canonical_reading(active, "glyph_truth.active_reading")
        elif active is not None:
            raise GlobalLibraryValidationError("non-verified glyph_truth 不得有 active_reading", path=path)
        direct_count = _nonnegative_int(row["direct_source_count"], "direct_source_count")
        independent_count = _nonnegative_int(
            row["independent_source_count"],
            "independent_source_count",
        )
        if independent_count > direct_count:
            raise GlobalLibraryValidationError("independent_source_count 不得大於 direct_source_count", path=path)
        _nonnegative_int(row["revision"], "glyph_truth.revision")
        _validate_timestamp(row["created_at"], "glyph_truth.created_at")
        _validate_timestamp(row["updated_at"], "glyph_truth.updated_at")
        glyphs[glyph_id] = {**row, "identity": identity}

    evidence_rows = [dict(row) for row in connection.execute("SELECT * FROM source_evidence ORDER BY evidence_id")]
    evidence: dict[str, dict[str, Any]] = {}
    evidence_by_glyph: dict[str, list[dict[str, Any]]] = {glyph_id: [] for glyph_id in glyphs}
    direct_readings_by_glyph: dict[str, set[str]] = {glyph_id: set() for glyph_id in glyphs}
    for row in evidence_rows:
        evidence_id = _sha256_text(row["evidence_id"], "source_evidence.evidence_id")
        glyph_id = _sha256_text(row["glyph_id"], "source_evidence.glyph_id")
        if glyph_id not in glyphs:
            raise GlobalLibraryValidationError("source_evidence references unknown glyph", path=path)
        reading = _canonical_reading(row["reading"], "source_evidence.reading")
        evidence_class = _strict_text(row["evidence_class"], "evidence_class")
        channel = _strict_text(row["confirmation_channel"], "confirmation_channel")
        if evidence_class not in EVIDENCE_CLASSES or channel not in CONFIRMATION_CHANNELS:
            raise GlobalLibraryValidationError("source_evidence enum 未知", path=path)
        expected_count_channel = {
            DIRECT_VISUAL_ACTUAL: (1, PDF_PAGE_VISUAL),
            LEGACY_PROJECT_CANDIDATE: (0, LEGACY_IMPORT),
            PROPAGATED_PROJECT_OCCURRENCE: (0, PROJECT_PROPAGATION),
        }[evidence_class]
        if (int(row["counts_toward_global_quorum"]), channel) != expected_count_channel:
            raise GlobalLibraryValidationError("source_evidence quorum/channel derivation 不一致", path=path)
        _strict_text(row["source_project_id"], "source_project_id")
        _sha256_text(row["source_pdf_sha256"], "source_pdf_sha256")
        _sha256_text(row["source_font_program_sha256"], "source_font_program_sha256")
        _strict_text(row["source_occurrence_id"], "source_occurrence_id")
        _strict_text(row["source_review_id"], "source_review_id")
        _sha256_text(row["decision_snapshot_sha256"], "decision_snapshot_sha256")
        _validate_timestamp(row["confirmed_at"], "source_evidence.confirmed_at")
        _validate_timestamp(row["ingested_at"], "source_evidence.ingested_at")
        recomputed = compute_source_evidence_id(
            glyph_id=glyph_id,
            reading=reading,
            evidence_class=evidence_class,
            source_project_id=row["source_project_id"],
            source_pdf_sha256=row["source_pdf_sha256"],
            source_font_program_sha256=row["source_font_program_sha256"],
            source_occurrence_id=row["source_occurrence_id"],
            source_review_id=row["source_review_id"],
            decision_snapshot_sha256=row["decision_snapshot_sha256"],
            confirmation_channel=channel,
        )
        if recomputed != evidence_id:
            raise GlobalLibraryValidationError(f"evidence_id 無法重算：{evidence_id}", path=path)
        evidence[evidence_id] = row
        evidence_by_glyph[glyph_id].append(row)
        if evidence_class == DIRECT_VISUAL_ACTUAL and int(row["counts_toward_global_quorum"]) == 1:
            direct_readings_by_glyph[glyph_id].add(reading)

    for glyph_id, glyph in glyphs.items():
        source_rows = evidence_by_glyph[glyph_id]
        direct_count = sum(int(row["counts_toward_global_quorum"]) for row in source_rows)
        independent_count = _maximum_independent_source_count(source_rows)
        if int(glyph["direct_source_count"]) != direct_count:
            raise GlobalLibraryValidationError(f"cached direct_source_count drift：{glyph_id}", path=path)
        if int(glyph["independent_source_count"]) != independent_count:
            raise GlobalLibraryValidationError(f"cached independent_source_count drift：{glyph_id}", path=path)

    approvals = [dict(row) for row in connection.execute("SELECT * FROM promotion_approval ORDER BY approval_id")]
    valid_approvals: dict[tuple[str, int, str], list[dict[str, Any]]] = {}
    approval_events: dict[
        tuple[str, str, str, str, int, str],
        dict[str, dict[str, Any]],
    ] = {}
    approval_ids: set[str] = set()
    for row in approvals:
        approval_id = _sha256_text(row["approval_id"], "promotion_approval.approval_id")
        glyph_id = _sha256_text(row["glyph_id"], "promotion_approval.glyph_id")
        if glyph_id not in glyphs:
            raise GlobalLibraryValidationError("promotion_approval references unknown glyph", path=path)
        reading = _canonical_reading(row["reading"], "promotion_approval.reading")
        quorum_digest = _sha256_text(row["quorum_digest"], "quorum_digest")
        evidence_ids = _canonical_string_list(
            row["quorum_evidence_ids_json"],
            "quorum_evidence_ids_json",
            minimum=2,
        )
        policy = _strict_text(row["promotion_policy_version"], "promotion_policy_version")
        if policy != GLOBAL_PROMOTION_POLICY_VERSION:
            raise GlobalLibrarySchemaError("approval promotion policy 不相容", path=path)
        revision = _nonnegative_int(row["glyph_revision"], "approval.glyph_revision")
        source = _strict_text(row["approval_source"], "approval_source")
        status = _strict_text(row["status"], "approval.status")
        if source not in APPROVAL_SOURCES or status not in APPROVAL_STATUSES:
            raise GlobalLibraryValidationError("promotion_approval enum 未知", path=path)
        _validate_timestamp(row["approved_at"], "promotion_approval.approved_at")
        selected: list[dict[str, Any]] = []
        for evidence_id in evidence_ids:
            _sha256_text(evidence_id, "approval evidence_id")
            item = evidence.get(evidence_id)
            if item is None:
                raise GlobalLibraryValidationError("approval quorum evidence 不存在", path=path)
            if (
                item["glyph_id"] != glyph_id
                or item["reading"] != reading
                or int(item["counts_toward_global_quorum"]) != 1
            ):
                raise GlobalLibraryValidationError("approval quorum evidence 不相容", path=path)
            selected.append(item)
        if _maximum_independent_source_count(selected) < 2:
            raise GlobalLibraryValidationError("approval quorum 未達獨立來源門檻", path=path)
        if len({str(item["source_occurrence_id"]) for item in selected}) < 2:
            raise GlobalLibraryValidationError("approval quorum 未達 occurrence independence 門檻", path=path)
        if len({str(item["source_review_id"]) for item in selected}) < 2:
            raise GlobalLibraryValidationError("approval quorum 未達 review independence 門檻", path=path)
        if compute_quorum_digest(
            glyph_id,
            reading,
            evidence_ids,
            promotion_policy_version=policy,
        ) != quorum_digest:
            raise GlobalLibraryValidationError("approval quorum_digest 無法重算", path=path)
        if compute_promotion_approval_id(
            glyph_id=glyph_id,
            reading=reading,
            quorum_digest=quorum_digest,
            promotion_policy_version=policy,
            glyph_revision=revision,
            approval_source=source,
            status=status,
        ) != approval_id:
            raise GlobalLibraryValidationError("approval_id 無法重算", path=path)
        approval_ids.add(approval_id)
        match_key = (glyph_id, reading, quorum_digest, policy, revision, source)
        events_for_approval = approval_events.setdefault(match_key, {})
        if status in events_for_approval:
            raise GlobalLibraryValidationError("duplicate immutable approval event", path=path)
        events_for_approval[status] = row

    for match_key, events_for_approval in approval_events.items():
        approved = events_for_approval.get(APPROVED)
        revoked = events_for_approval.get(REVOKED)
        if revoked is not None and approved is None:
            raise GlobalLibraryValidationError("REVOKED approval 缺少 matching APPROVED identity", path=path)
        if approved is not None and revoked is None:
            glyph_id, reading, _quorum, _policy, revision, _source = match_key
            valid_approvals.setdefault((glyph_id, revision, reading), []).append(approved)

    conflicts = [dict(row) for row in connection.execute("SELECT * FROM glyph_conflict ORDER BY conflict_id")]
    open_conflicts: dict[str, list[tuple[str, ...]]] = {}
    resolved_conflicts_by_glyph: dict[str, list[tuple[str, ...]]] = {}
    for row in conflicts:
        conflict_id = _sha256_text(row["conflict_id"], "glyph_conflict.conflict_id")
        glyph_id = _sha256_text(row["glyph_id"], "glyph_conflict.glyph_id")
        if glyph_id not in glyphs:
            raise GlobalLibraryValidationError("glyph_conflict references unknown glyph", path=path)
        status = _strict_text(row["status"], "glyph_conflict.status")
        if status not in CONFLICT_STATUSES:
            raise GlobalLibraryValidationError("glyph_conflict status 未知", path=path)
        readings = _canonical_reading_list(
            row["conflicting_readings_json"],
            "conflicting_readings_json",
            minimum=2,
        )
        first = _validate_timestamp(row["first_event_at"], "glyph_conflict.first_event_at")
        last = _validate_timestamp(row["last_event_at"], "glyph_conflict.last_event_at")
        if first > last:
            raise GlobalLibraryValidationError("glyph_conflict first/last event 倒置", path=path)
        opened = _nonnegative_int(row["opened_generation"], "opened_generation")
        if opened > generation:
            raise GlobalLibraryValidationError("glyph_conflict generation 超過 library generation", path=path)
        if compute_glyph_conflict_id(glyph_id, readings, opened) != conflict_id:
            raise GlobalLibraryValidationError("conflict_id 無法重算", path=path)
        if status == OPEN:
            if row["resolved_generation"] is not None or row["resolution_reference"] is not None:
                raise GlobalLibraryValidationError("open conflict 不得有 resolution", path=path)
            if glyphs[glyph_id]["state"] != QUARANTINED_CONFLICT or glyphs[glyph_id]["active_reading"] is not None:
                raise GlobalLibraryValidationError("open conflict 必須 quarantine 且無 active_reading", path=path)
            open_conflicts.setdefault(glyph_id, []).append(readings)
        else:
            resolved_generation = _nonnegative_int(row["resolved_generation"], "resolved_generation")
            if resolved_generation < opened or resolved_generation > generation:
                raise GlobalLibraryValidationError("resolved conflict generation 無效", path=path)
            _strict_text(row["resolution_reference"], "resolution_reference")
            resolved_conflicts_by_glyph.setdefault(glyph_id, []).append(readings)

    for glyph_id, glyph in glyphs.items():
        open_for_glyph = open_conflicts.get(glyph_id, [])
        direct_readings = direct_readings_by_glyph[glyph_id]
        if len(direct_readings) >= 2 and glyph["state"] != QUARANTINED_CONFLICT:
            raise GlobalLibraryValidationError(
                "contradictory retained direct readings 必須 QUARANTINED_CONFLICT",
                path=path,
            )
        if glyph["state"] == QUARANTINED_CONFLICT:
            if len(open_for_glyph) != 1:
                raise GlobalLibraryValidationError("quarantined glyph 必須有 exactly one open conflict", path=path)
            if not direct_readings.issubset(set(open_for_glyph[0])):
                raise GlobalLibraryValidationError(
                    "open conflict payload 未涵蓋所有 retained direct readings",
                    path=path,
                )
        elif open_for_glyph:
            raise GlobalLibraryValidationError("non-quarantined glyph 不得有 open conflict", path=path)
        if glyph["state"] == VERIFIED_GLOBAL:
            active_reading = str(glyph["active_reading"])
            if any(reading != active_reading for reading in direct_readings):
                raise GlobalLibraryValidationError(
                    "VERIFIED_GLOBAL retained direct reading 與 active_reading 矛盾",
                    path=path,
                )
            if resolved_conflicts_by_glyph.get(glyph_id):
                raise GlobalLibraryValidationError(
                    "RESOLVED conflict history cannot reauthorize VERIFIED_GLOBAL；"
                    "Phase 2 缺少 adjudication、fresh quorum 與 resolution revision binding",
                    path=path,
                )
            key = (glyph_id, int(glyph["revision"]), active_reading)
            if len(valid_approvals.get(key, [])) != 1:
                raise GlobalLibraryValidationError(
                    "VERIFIED_GLOBAL 缺少 exactly one unrevoked exact revision/quorum approval",
                    path=path,
                )

    provenance = [dict(row) for row in connection.execute("SELECT * FROM provenance_event ORDER BY event_id")]
    for row in provenance:
        event_id = _sha256_text(row["event_id"], "provenance_event.event_id")
        _strict_text(row["transaction_id"], "provenance_event.transaction_id")
        glyph_id = _sha256_text(row["glyph_id"], "provenance_event.glyph_id")
        if glyph_id not in glyphs:
            raise GlobalLibraryValidationError("provenance_event references unknown glyph", path=path)
        event_type = _strict_text(row["event_type"], "provenance_event.event_type")
        if event_type not in PROVENANCE_EVENT_TYPES:
            raise GlobalLibraryValidationError("provenance_event type 未知", path=path)
        reading = None if row["reading"] is None else _canonical_reading(row["reading"])
        evidence_id = row["evidence_id"]
        approval_id = row["approval_id"]
        if evidence_id is not None and _sha256_text(evidence_id, "evidence_id") not in evidence:
            raise GlobalLibraryValidationError("provenance evidence reference 無效", path=path)
        if approval_id is not None and _sha256_text(approval_id, "approval_id") not in approval_ids:
            raise GlobalLibraryValidationError("provenance approval reference 無效", path=path)
        payload_digest = _sha256_text(row["payload_digest"], "payload_digest")
        _validate_timestamp(row["created_at"], "provenance_event.created_at")
        if compute_provenance_event_id(
            transaction_id=row["transaction_id"],
            glyph_id=glyph_id,
            event_type=event_type,
            reading=reading,
            evidence_id=evidence_id,
            approval_id=approval_id,
            payload_digest=payload_digest,
        ) != event_id:
            raise GlobalLibraryValidationError("provenance event_id 無法重算", path=path)

    intents = [dict(row) for row in connection.execute("SELECT * FROM processed_intent ORDER BY intent_id")]
    for row in intents:
        intent_id = _strict_text(row["intent_id"], "processed_intent.intent_id")
        payload_digest = _sha256_text(row["payload_digest"], "processed_intent.payload_digest")
        committed_generation = _nonnegative_int(
            row["committed_generation"],
            "processed_intent.committed_generation",
        )
        if committed_generation > generation:
            raise GlobalLibraryValidationError("processed intent generation 超過 library generation", path=path)
        result_state = _strict_text(row["result_state"], "processed_intent.result_state")
        if result_state not in INTENT_RESULT_STATES:
            raise GlobalLibraryValidationError("processed_intent result_state 未知", path=path)
        receipt = _sha256_text(row["receipt_digest"], "processed_intent.receipt_digest")
        if compute_processed_intent_receipt(
            intent_id,
            payload_digest,
            committed_generation,
            result_state,
        ) != receipt:
            raise GlobalLibraryValidationError("processed intent receipt 無法重算", path=path)
        _validate_timestamp(row["processed_at"], "processed_intent.processed_at")

    migrations = [dict(row) for row in connection.execute("SELECT * FROM migration_candidate ORDER BY import_id")]
    migration_by_id = {row["import_id"]: row for row in migrations}
    intent_by_id = {row["intent_id"]: row for row in intents}
    historical_candidate_ids: set[str] = set()
    for row in migrations:
        import_id = _sha256_text(row["import_id"], "migration_candidate.import_id")
        glyph_id = _sha256_text(row["glyph_id"], "migration_candidate.glyph_id")
        if glyph_id not in glyphs:
            raise GlobalLibraryValidationError("migration_candidate references unknown glyph", path=path)
        reading = _canonical_reading(row["reading"], "migration_candidate.reading")
        _strict_text(row["legacy_project_id"], "legacy_project_id")
        _sha256_text(row["legacy_project_sha256"], "legacy_project_sha256")
        _sha256_text(row["legacy_evidence_sha256"], "legacy_evidence_sha256")
        old_level = _strict_text(row["old_verification_level"], "old_verification_level")
        status = _strict_text(row["status"], "migration_candidate.status")
        if old_level not in LEGACY_VERIFICATION_LEVELS or status not in MIGRATION_STATUSES:
            raise GlobalLibraryValidationError("migration_candidate enum 未知", path=path)
        _validate_timestamp(row["imported_at"], "migration_candidate.imported_at")
        identity_args = dict(
            glyph_id=glyph_id,
            reading=reading,
            legacy_project_id=row["legacy_project_id"],
            legacy_project_sha256=row["legacy_project_sha256"],
            legacy_evidence_sha256=row["legacy_evidence_sha256"],
            old_verification_level=old_level,
        )
        contract = _stored_migration_contract(import_id, **identity_args)
        if (contract == "legacy-v0" and old_level in {"USER_VERIFIED_SINGLE", "VERIFIED_EXACT_GLYPH"}
                and status in {MIGRATION_CANDIDATE, MIGRATION_INSUFFICIENT}):
            historical_candidate_ids.add(import_id)
        if contract == GLOBAL_MIGRATION_IMPORT_CONTRACT_VERSION:
            # Current imports are complete auditable transactions. The exact
            # legacy-v0 adapter predates this workflow and requires no receipts.
            identity = glyphs[glyph_id]["identity"]
            exact = {key: getattr(identity, key) for key in (
                "kind", "style_group", "glyph_sha256", "identity_eligibility")}
            initial_statuses = ((MIGRATION_CONFLICT,) if old_level == QUARANTINED_CONFLICT else
                                ((MIGRATION_CANDIDATE, MIGRATION_INSUFFICIENT)
                                 if row["status"] == MIGRATION_CONFLICT else (row["status"],)))
            digests = {make_migration_intent(
                exact, **{key: value for key, value in identity_args.items() if key != "glyph_id"},
                status=status)["payload_digest"] for status in initial_statuses}
            receipt = intent_by_id.get("gmi_" + import_id)
            events = [event for event in provenance if event["transaction_id"] == "gmi_" + import_id
                      and event["event_type"] == "MIGRATION_CANDIDATE_IMPORTED"]
            if (receipt is None or receipt["payload_digest"] not in digests or
                    receipt["result_state"] != INTENT_COMMITTED or len(events) != 1 or
                    events[0]["payload_digest"] != receipt["payload_digest"] or
                    events[0]["glyph_id"] != glyph_id or events[0]["reading"] != reading or
                    events[0]["evidence_id"] is not None or events[0]["approval_id"] is not None):
                raise GlobalLibraryValidationError("migration import/receipt/provenance mismatch", path=path)
    for item in [*intents, *provenance]:
        transaction_id = str(item.get("intent_id", item.get("transaction_id", "")))
        if transaction_id.startswith("gmi_") and transaction_id[4:] not in migration_by_id:
            raise GlobalLibraryValidationError("orphan migration receipt/provenance", path=path)

    # Adopted v1.1 section 5.6 permits ordinary frozen-v0 historical candidates
    # to remain readable. Only their candidate-conflict invariant is adapted;
    # all direct/approval/schema validation above and current import links stay
    # strict. Every retained reading still participates in effective reuse safety.
    read_side_conflicts: dict[str, LegacyCandidateReadConflict] = {}
    for glyph_id, glyph in glyphs.items():
        legacy_rows = [row for row in migrations if row["glyph_id"] == glyph_id]
        strict_rows = [row for row in legacy_rows if row["import_id"] not in historical_candidate_ids]
        strict_readings = direct_readings_by_glyph[glyph_id] | {row["reading"] for row in strict_rows}
        if glyph["active_reading"] is not None:
            strict_readings.add(glyph["active_reading"])
        readings = strict_readings | {row["reading"] for row in legacy_rows}
        if (len(strict_readings) > 1 or any(row["old_verification_level"] == QUARANTINED_CONFLICT
                                     or row["status"] == MIGRATION_CONFLICT for row in legacy_rows)):
            if glyph["state"] != QUARANTINED_CONFLICT:
                raise GlobalLibraryValidationError("migration contradiction requires quarantine", path=path)
        if glyph["state"] == QUARANTINED_CONFLICT:
            if not readings.issubset(set(open_conflicts[glyph_id][0])):
                raise GlobalLibraryValidationError("open conflict omits migration reading", path=path)
        elif len(readings) > 1:
            read_side_conflicts[glyph_id] = LegacyCandidateReadConflict(
                glyph_id, glyph["identity"], glyph["state"], tuple(sorted(readings)),
                tuple(sorted(row["import_id"] for row in legacy_rows
                             if row["import_id"] in historical_candidate_ids)))

    # These receipts describe a new, explicitly requested safety transaction,
    # never retroactive receipts/provenance for v0 imports.
    for transaction_id in {str(item.get("intent_id", item.get("transaction_id", "")))
                           for item in [*intents, *provenance]}:
        if not transaction_id.startswith("approval_conflict_"):
            continue
        receipt = intent_by_id.get(transaction_id)
        events = [event for event in provenance if event["transaction_id"] == transaction_id]
        if (receipt is None or transaction_id != "approval_conflict_" + receipt["payload_digest"]
                or receipt["result_state"] != INTENT_COMMITTED or len(events) != 1
                or events[0]["event_type"] != "CONFLICT_OPENED"
                or events[0]["payload_digest"] != receipt["payload_digest"]
                or events[0]["evidence_id"] is not None or events[0]["approval_id"] is not None
                or glyphs[events[0]["glyph_id"]]["state"] != QUARANTINED_CONFLICT):
            raise GlobalLibraryValidationError("approval conflict receipt/provenance mismatch", path=path)

    return {
        "meta": meta,
        "glyphs": glyphs,
        "open_conflicts": open_conflicts,
        "read_side_conflicts": read_side_conflicts,
        "provenance_event_count": len(provenance),
    }


def _validate_connection(connection: sqlite3.Connection, path: Path) -> dict[str, Any]:
    quick_rows = [str(row[0]) for row in connection.execute("PRAGMA quick_check").fetchall()]
    if quick_rows != ["ok"]:
        raise GlobalLibraryCorruptError(
            f"global library quick_check failed：{quick_rows}",
            path=path,
        )
    foreign_key_rows = connection.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_key_rows:
        raise GlobalLibraryValidationError(
            f"global library foreign_key_check failed：{len(foreign_key_rows)}",
            path=path,
        )
    _validate_schema(connection, path)
    return _validate_rows(connection, path)


def _absent_snapshot(path: Path) -> GlobalExactGlyphSnapshot:
    return GlobalExactGlyphSnapshot(
        store_status=ABSENT,
        schema_version="",
        identity_contract_version=GLOBAL_IDENTITY_CONTRACT_VERSION,
        promotion_policy_version=GLOBAL_PROMOTION_POLICY_VERSION,
        generation=None,
        trusted_identities=(),
        quarantined_identities=(),
        database_path=str(path),
        loaded_at=_utc_now(),
        provenance_event_count=0,
    )


def _load_snapshot_from_path(path: Path, busy_timeout_ms: int) -> GlobalExactGlyphSnapshot:
    if _path_is_absent(path):
        return _absent_snapshot(path)
    connection: sqlite3.Connection | None = None
    try:
        connection = _connect_existing(path, read_only=True, busy_timeout_ms=busy_timeout_ms)
        connection.execute("BEGIN")
        validated = _validate_connection(connection, path)
        meta = validated["meta"]
        open_conflicts = validated["open_conflicts"]
        trusted: list[GlobalGlyphSnapshotRecord] = []
        quarantined: list[GlobalGlyphSnapshotRecord] = []
        for glyph_id, row in sorted(validated["glyphs"].items()):
            identity = row["identity"]
            if glyph_id in validated["read_side_conflicts"]:
                continue  # Stored VERIFIED_GLOBAL is insufficient for effective reuse.
            if row["state"] == VERIFIED_GLOBAL:
                trusted.append(GlobalGlyphSnapshotRecord(
                    glyph_id=glyph_id,
                    identity=identity,
                    state=VERIFIED_GLOBAL,
                    active_reading=str(row["active_reading"]),
                    conflicting_readings=(),
                    direct_source_count=int(row["direct_source_count"]),
                    independent_source_count=int(row["independent_source_count"]),
                    revision=int(row["revision"]),
                    updated_at=str(row["updated_at"]),
                ))
            elif row["state"] == QUARANTINED_CONFLICT:
                readings = tuple(open_conflicts[glyph_id][0])
                quarantined.append(GlobalGlyphSnapshotRecord(
                    glyph_id=glyph_id,
                    identity=identity,
                    state=QUARANTINED_CONFLICT,
                    active_reading="",
                    conflicting_readings=readings,
                    direct_source_count=int(row["direct_source_count"]),
                    independent_source_count=int(row["independent_source_count"]),
                    revision=int(row["revision"]),
                    updated_at=str(row["updated_at"]),
                ))
        connection.execute("COMMIT")
        return GlobalExactGlyphSnapshot(
            store_status=VALID,
            schema_version=str(meta["schema_version"]),
            identity_contract_version=str(meta["identity_contract_version"]),
            promotion_policy_version=str(meta["promotion_policy_version"]),
            generation=int(meta["generation"]),
            trusted_identities=tuple(trusted),
            quarantined_identities=tuple(quarantined),
            database_path=str(path),
            loaded_at=_utc_now(),
            provenance_event_count=int(validated["provenance_event_count"]),
            read_side_conflicts=tuple(value for _, value in sorted(validated["read_side_conflicts"].items())),
        )
    except BaseException as exc:
        if connection is not None and connection.in_transaction:
            connection.execute("ROLLBACK")
        raise _translated_error(exc, path) from exc
    finally:
        if connection is not None:
            connection.close()


def canonical_logical_subset_digest(
    snapshot: GlobalExactGlyphSnapshot,
    requested_identities: Iterable[GlobalExactGlyphIdentity | Mapping[str, Any]],
) -> LogicalSubsetDigest:
    if snapshot.store_status not in {ABSENT, VALID}:
        raise GlobalLibraryValidationError("logical subset 只接受 ABSENT 或 VALID immutable snapshot")
    trusted = {record.identity.tuple: record for record in snapshot.trusted_identities}
    quarantined = {record.identity.tuple: record for record in snapshot.quarantined_identities}
    # Reuse/dependency semantics are the existing union conflict gate. Storage
    # diagnostics remain distinct; no schema, scope, epoch or generation key.
    quarantined.update({record.identity.tuple: record for record in snapshot.read_side_conflicts})
    canonical_requests: dict[tuple[str, str, str], GlobalExactGlyphIdentity] = {}
    for raw in requested_identities:
        if isinstance(raw, GlobalExactGlyphIdentity):
            if raw.identity_eligibility == NON_GLOBAL_ELIGIBLE:
                canonical_kind = _strict_text(raw.kind, "requested kind")
                if canonical_kind not in GLOBAL_GLYPH_KINDS:
                    raise GlobalLibraryValidationError("NON_GLOBAL_ELIGIBLE request kind 無效")
                canonical_style = _strict_text(
                    raw.style_group,
                    "requested style_group",
                    allow_empty=True,
                )
                canonical_sha = _sha256_text(raw.glyph_sha256, "requested glyph_sha256")
                if canonical_kind == TTF_GLYF_SHA256 and canonical_style:
                    raise GlobalLibraryValidationError("TTF request style_group 必須空白")
                if canonical_kind == CFF_GLYPH_SHA256 and not canonical_style:
                    raise GlobalLibraryValidationError("CFF request style_group 不得空白")
                request = GlobalExactGlyphIdentity(
                    canonical_kind,
                    canonical_style,
                    canonical_sha,
                    NON_GLOBAL_ELIGIBLE,
                )
            else:
                request = canonical_global_exact_identity(
                    raw.kind,
                    raw.style_group,
                    raw.glyph_sha256,
                    raw.identity_eligibility,
                )
        elif isinstance(raw, Mapping):
            eligibility = raw.get("identity_eligibility", raw.get("eligibility"))
            kind = raw.get("kind")
            style = raw.get("style_group", "")
            sha = raw.get("glyph_sha256", raw.get("exact_key"))
            if eligibility == NON_GLOBAL_ELIGIBLE:
                canonical_kind = _strict_text(kind, "requested kind")
                if canonical_kind not in GLOBAL_GLYPH_KINDS:
                    raise GlobalLibraryValidationError("NON_GLOBAL_ELIGIBLE request kind 無效")
                canonical_style = _strict_text(style, "requested style_group", allow_empty=True)
                canonical_sha = _sha256_text(sha, "requested glyph_sha256")
                if canonical_kind == TTF_GLYF_SHA256 and canonical_style:
                    raise GlobalLibraryValidationError("TTF request style_group 必須空白")
                if canonical_kind == CFF_GLYPH_SHA256 and not canonical_style:
                    raise GlobalLibraryValidationError("CFF request style_group 不得空白")
                request = GlobalExactGlyphIdentity(
                    canonical_kind,
                    canonical_style,
                    canonical_sha,
                    NON_GLOBAL_ELIGIBLE,
                )
            else:
                request = canonical_global_exact_identity(kind, style, sha, eligibility)
        else:
            raise GlobalLibraryValidationError("requested identity 必須是 immutable identity 或 mapping")
        existing = canonical_requests.get(request.tuple)
        if existing is not None and existing.identity_eligibility != request.identity_eligibility:
            raise GlobalLibraryValidationError("同一 requested identity 帶有互斥 eligibility")
        canonical_requests[request.tuple] = request

    rows: list[LogicalSubsetRow] = []
    for identity_tuple, request in sorted(canonical_requests.items()):
        state = ABSENT
        reading = ""
        conflicts: tuple[str, ...] = ()
        if request.identity_eligibility == NON_GLOBAL_ELIGIBLE:
            state = NON_GLOBAL_ELIGIBLE
        elif snapshot.store_status == VALID and identity_tuple in quarantined:
            state = QUARANTINED_CONFLICT
            conflicts = quarantined[identity_tuple].conflicting_readings
        elif snapshot.store_status == VALID and identity_tuple in trusted:
            state = VERIFIED_GLOBAL
            reading = trusted[identity_tuple].active_reading
        rows.append(LogicalSubsetRow(
            identity_contract_version=snapshot.identity_contract_version,
            promotion_policy_version=snapshot.promotion_policy_version,
            requested_identity=identity_tuple,
            identity_eligibility=request.identity_eligibility,
            effective_state=state,
            active_reading=reading,
            conflicting_readings=conflicts,
        ))
    payload = {"rows": [row.as_dict() for row in rows]}
    canonical_json = _canonical_json(payload)
    return LogicalSubsetDigest(
        rows=tuple(rows),
        canonical_json=canonical_json,
        sha256=hashlib.sha256(canonical_json.encode("utf-8")).hexdigest(),
    )


def canonical_global_exact_glyph_evidence_hashes(value: Mapping[str, Any]) -> dict[str, str]:
    """Validate the fixed Phase-3 per-PDF global dependency component."""
    if not isinstance(value, Mapping):
        raise GlobalLibraryValidationError("global exact evidence hashes 必須是 mapping")
    missing = [key for key in GLOBAL_EXACT_EVIDENCE_HASH_KEYS if key not in value]
    unexpected = sorted(set(value) - set(GLOBAL_EXACT_EVIDENCE_HASH_KEYS))
    if missing:
        raise GlobalLibraryValidationError(f"global exact evidence hashes 缺少欄位：{missing}")
    if unexpected:
        raise GlobalLibraryValidationError(f"global exact evidence hashes 含未知欄位：{unexpected}")
    result = {
        "dependency_contract_version": _strict_text(
            value.get("dependency_contract_version"),
            "dependency_contract_version",
        ),
        "identity_contract_version": _strict_text(
            value.get("identity_contract_version"),
            "identity_contract_version",
        ),
        "promotion_policy_version": _strict_text(
            value.get("promotion_policy_version"),
            "promotion_policy_version",
        ),
        "scope_mode": _strict_text(value.get("scope_mode"), "scope_mode"),
        "ttf_subset_sha256": _sha256_text(value.get("ttf_subset_sha256"), "ttf_subset_sha256"),
        "cff_subset_sha256": _sha256_text(value.get("cff_subset_sha256"), "cff_subset_sha256"),
        "conflict_subset_sha256": _sha256_text(
            value.get("conflict_subset_sha256"),
            "conflict_subset_sha256",
        ),
    }
    if result["dependency_contract_version"] != GLOBAL_EXACT_DEPENDENCY_CONTRACT_VERSION:
        raise GlobalLibraryValidationError("global exact dependency contract version 不相容")
    if result["identity_contract_version"] != GLOBAL_IDENTITY_CONTRACT_VERSION:
        raise GlobalLibraryValidationError("global exact identity contract version 不相容")
    if result["promotion_policy_version"] != GLOBAL_PROMOTION_POLICY_VERSION:
        raise GlobalLibraryValidationError("global exact promotion policy version 不相容")
    if result["scope_mode"] not in {
        GLOBAL_EXACT_PER_PDF_SCOPE_MODE,
        GLOBAL_EXACT_PROVISIONAL_SCOPE_MODE,
    }:
        raise GlobalLibraryValidationError("global exact dependency scope_mode 不支援")
    return result


def global_exact_glyph_evidence_hashes(
    snapshot: GlobalExactGlyphSnapshot,
    dependencies: Iterable[GlobalExactGlyphIdentity | Mapping[str, Any]] | None,
) -> dict[str, str]:
    """Hash only one PDF's effective logical global exact dependencies.

    ``None`` is an intentionally non-reusable provisional roster used before a
    new/legacy workbook has been decoded.  A real empty roster remains distinct
    through ``scope_mode``.  No database bytes, path, generation, timestamps,
    provenance, notes, revisions, or source counts participate.
    """
    if dependencies is None:
        requested: tuple[GlobalExactGlyphIdentity | Mapping[str, Any], ...] = ()
        scope_mode = GLOBAL_EXACT_PROVISIONAL_SCOPE_MODE
    else:
        if isinstance(dependencies, (str, bytes, Mapping)):
            raise GlobalLibraryValidationError("global exact dependencies 必須是 identity iterable")
        requested = tuple(dependencies)
        scope_mode = GLOBAL_EXACT_PER_PDF_SCOPE_MODE

    logical = canonical_logical_subset_digest(snapshot, requested)
    ttf_rows = [row.as_dict() for row in logical.rows if row.requested_identity[0] == TTF_GLYF_SHA256]
    cff_rows = [row.as_dict() for row in logical.rows if row.requested_identity[0] == CFF_GLYPH_SHA256]
    conflict_rows = [
        {
            "requested_identity": list(row.requested_identity),
            "identity_eligibility": row.identity_eligibility,
            "effective_conflict_state": (
                QUARANTINED_CONFLICT
                if row.effective_state == QUARANTINED_CONFLICT
                else GLOBAL_EXACT_NO_CONFLICT
            ),
            "conflicting_readings": (
                list(row.conflicting_readings)
                if row.effective_state == QUARANTINED_CONFLICT
                else []
            ),
        }
        for row in logical.rows
    ]
    return canonical_global_exact_glyph_evidence_hashes({
        "dependency_contract_version": GLOBAL_EXACT_DEPENDENCY_CONTRACT_VERSION,
        "identity_contract_version": snapshot.identity_contract_version,
        "promotion_policy_version": snapshot.promotion_policy_version,
        "scope_mode": scope_mode,
        "ttf_subset_sha256": _canonical_sha256({"rows": ttf_rows}),
        "cff_subset_sha256": _canonical_sha256({"rows": cff_rows}),
        "conflict_subset_sha256": _canonical_sha256({"rows": conflict_rows}),
    })


def resolve_exact_glyph_reuse(
    snapshot: GlobalExactGlyphSnapshot,
    identity: GlobalExactGlyphIdentity | Mapping[str, Any],
    *,
    higher_priority_sources: Iterable[tuple[str, Any]] = (),
    lower_priority_sources: Iterable[tuple[str, Any]] = (),
    exact_identity_quarantined: bool = False,
    quarantine_readings: Iterable[Any] = (),
) -> ExactGlyphReuseResolution:
    """Resolve project/global/static exact evidence with a union conflict gate."""
    logical = canonical_logical_subset_digest(snapshot, (identity,))
    if len(logical.rows) != 1:
        raise GlobalLibraryValidationError("exact reuse identity 必須產生一筆 logical row")
    row = logical.rows[0]
    if row.identity_eligibility == NON_GLOBAL_ELIGIBLE:
        raise GlobalLibraryValidationError("NON_GLOBAL_ELIGIBLE identity 不得查詢 global exact reuse")

    ordered_sources: list[tuple[str, str]] = []
    seen_labels: set[str] = set()

    def append_sources(raw_sources: Iterable[tuple[str, Any]]) -> None:
        for raw_label, raw_reading in raw_sources:
            label = _strict_text(raw_label, "exact source label")
            if label in seen_labels:
                raise GlobalLibraryValidationError(f"exact source label 重複：{label}")
            reading = _canonical_reading(raw_reading, f"{label} reading")
            seen_labels.add(label)
            ordered_sources.append((label, reading))

    append_sources(higher_priority_sources)
    if row.effective_state == VERIFIED_GLOBAL:
        if "GLOBAL_VERIFIED_EXACT" in seen_labels:
            raise GlobalLibraryValidationError(
                "GLOBAL_VERIFIED_EXACT label 只能由 immutable global snapshot 提供"
            )
        seen_labels.add("GLOBAL_VERIFIED_EXACT")
        ordered_sources.append(("GLOBAL_VERIFIED_EXACT", row.active_reading))
    append_sources(lower_priority_sources)

    readings = {reading for _label, reading in ordered_sources}
    conflict_readings = {
        _canonical_reading(value, "quarantine reading")
        for value in quarantine_readings
    }
    conflict_readings.update(row.conflicting_readings)
    conflict_readings.update(readings)
    conflict = bool(exact_identity_quarantined) or row.effective_state == QUARANTINED_CONFLICT or len(readings) > 1
    if conflict:
        return ExactGlyphReuseResolution(
            reading="",
            sources=(),
            conflict=True,
            conflicting_readings=tuple(sorted(conflict_readings)),
            global_effective_state=row.effective_state,
            global_conflicting_readings=row.conflicting_readings,
        )
    if not ordered_sources:
        return ExactGlyphReuseResolution(
            reading="",
            sources=(),
            conflict=False,
            conflicting_readings=(),
            global_effective_state=row.effective_state,
            global_conflicting_readings=(),
        )
    reading = ordered_sources[0][1]
    return ExactGlyphReuseResolution(
        reading=reading,
        sources=tuple(label for label, _source_reading in ordered_sources),
        conflict=False,
        conflicting_readings=(),
        global_effective_state=row.effective_state,
        global_conflicting_readings=(),
    )


class GlobalWriteTransaction:
    def __init__(self, connection: sqlite3.Connection, path: Path) -> None:
        self._connection = connection
        self._path = path
        self._generation_bumped = False

    @property
    def generation(self) -> int:
        row = self._connection.execute(
            "SELECT generation FROM library_meta WHERE meta_id = 1"
        ).fetchone()
        if row is None:
            raise GlobalLibraryValidationError("library_meta singleton 缺失", path=self._path)
        return _nonnegative_int(row[0], "library_meta.generation")

    def insert_candidate_identity(
        self,
        identity: GlobalExactGlyphIdentity | Mapping[str, Any],
        *,
        created_at: str | None = None,
    ) -> str:
        if isinstance(identity, Mapping):
            canonical = canonical_global_exact_identity(
                identity.get("kind"),
                identity.get("style_group", ""),
                identity.get("glyph_sha256", identity.get("exact_key")),
                identity.get("identity_eligibility", identity.get("eligibility")),
            )
        else:
            canonical = canonical_global_exact_identity(
                identity.kind,
                identity.style_group,
                identity.glyph_sha256,
                identity.identity_eligibility,
            )
        glyph_id = compute_global_glyph_id(
            canonical.kind,
            canonical.style_group,
            canonical.glyph_sha256,
            identity_eligibility=canonical.identity_eligibility,
        )
        timestamp = _validate_timestamp(created_at or _utc_now(), "created_at")
        self._connection.execute(
            """
            INSERT INTO glyph_truth (
                glyph_id, identity_contract_version, identity_eligibility, kind,
                style_group, glyph_sha256, state, active_reading,
                direct_source_count, independent_source_count, revision,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 0, 0, 0, ?, ?)
            """,
            (
                glyph_id,
                GLOBAL_IDENTITY_CONTRACT_VERSION,
                canonical.identity_eligibility,
                canonical.kind,
                canonical.style_group,
                canonical.glyph_sha256,
                CANDIDATE,
                timestamp,
                timestamp,
            ),
        )
        return glyph_id

    def cas_increment_glyph_revision(
        self,
        glyph_id: str,
        expected_revision: int,
        *,
        updated_at: str | None = None,
    ) -> tuple[bool, int | None]:
        canonical_id = _sha256_text(glyph_id, "glyph_id")
        expected = _nonnegative_int(expected_revision, "expected_revision")
        row = self._connection.execute(
            "SELECT revision, state FROM glyph_truth WHERE glyph_id = ?",
            (canonical_id,),
        ).fetchone()
        if row is None:
            return False, None
        observed = int(row["revision"])
        if str(row["state"]) not in {CANDIDATE, PROMOTION_READY}:
            raise GlobalLibraryValidationError(
                "standalone storage CAS revision primitive 只允許 non-reusable candidate state",
                path=self._path,
            )
        timestamp = _validate_timestamp(updated_at or _utc_now(), "updated_at")
        cursor = self._connection.execute(
            """
            UPDATE glyph_truth
            SET revision = revision + 1, updated_at = ?
            WHERE glyph_id = ? AND revision = ?
            """,
            (timestamp, canonical_id, expected),
        )
        return cursor.rowcount == 1, observed

    def bump_generation(self, *, updated_at: str | None = None) -> int:
        if self._generation_bumped:
            raise GlobalLibraryValidationError(
                "同一 write transaction 不得重複 bump generation",
                path=self._path,
            )
        before = self.generation
        timestamp = _validate_timestamp(updated_at or _utc_now(), "updated_at")
        cursor = self._connection.execute(
            """
            UPDATE library_meta
            SET generation = generation + 1, updated_at = ?
            WHERE meta_id = 1 AND generation = ?
            """,
            (timestamp, before),
        )
        if cursor.rowcount != 1:
            raise GlobalLibraryValidationError("library generation CAS miss", path=self._path)
        self._generation_bumped = True
        return before + 1

    def lookup_processed_intent(self, intent_id: str) -> sqlite3.Row | None:
        canonical_id = _strict_text(intent_id, "intent_id")
        return self._connection.execute(
            "SELECT * FROM processed_intent WHERE intent_id = ?",
            (canonical_id,),
        ).fetchone()

    def record_processed_intent(
        self,
        *,
        intent_id: str,
        payload_digest: str,
        committed_generation: int,
        result_state: str,
        processed_at: str | None = None,
    ) -> str:
        canonical_id = _strict_text(intent_id, "intent_id")
        payload = _sha256_text(payload_digest, "payload_digest")
        generation = _nonnegative_int(committed_generation, "committed_generation")
        state = _strict_text(result_state, "result_state")
        if state not in INTENT_RESULT_STATES:
            raise GlobalLibraryValidationError("processed_intent result_state 未知")
        receipt = compute_processed_intent_receipt(canonical_id, payload, generation, state)
        timestamp = _validate_timestamp(processed_at or _utc_now(), "processed_at")
        self._connection.execute(
            """
            INSERT INTO processed_intent (
                intent_id, payload_digest, committed_generation,
                result_state, receipt_digest, processed_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (canonical_id, payload, generation, state, receipt, timestamp),
        )
        return receipt


@dataclass(frozen=True)
class GlobalExactGlyphRepository:
    path: Path
    busy_timeout_ms: int = GLOBAL_LIBRARY_DEFAULT_BUSY_TIMEOUT_MS
    write_retry_limit: int = GLOBAL_LIBRARY_DEFAULT_WRITE_RETRY_LIMIT

    def __post_init__(self) -> None:
        path = Path(self.path)
        if not path.is_absolute():
            raise GlobalLibraryPathError("global repository path 必須是 absolute path")
        if isinstance(self.busy_timeout_ms, bool) or not 1 <= int(self.busy_timeout_ms) <= 60000:
            raise GlobalLibraryPathError("busy_timeout_ms 必須介於 1 與 60000")
        if isinstance(self.write_retry_limit, bool) or not 0 <= int(self.write_retry_limit) <= 10:
            raise GlobalLibraryPathError("write_retry_limit 必須介於 0 與 10")
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "busy_timeout_ms", int(self.busy_timeout_ms))
        object.__setattr__(self, "write_retry_limit", int(self.write_retry_limit))

    @classmethod
    def resolved(
        cls,
        global_library_root: Path | str | None = None,
        *,
        environ: Mapping[str, str] | None = None,
        busy_timeout_ms: int = GLOBAL_LIBRARY_DEFAULT_BUSY_TIMEOUT_MS,
        write_retry_limit: int = GLOBAL_LIBRARY_DEFAULT_WRITE_RETRY_LIMIT,
    ) -> "GlobalExactGlyphRepository":
        return cls(
            resolve_global_exact_glyph_library_path(
                global_library_root,
                environ=environ,
            ),
            busy_timeout_ms=busy_timeout_ms,
            write_retry_limit=write_retry_limit,
        )

    def inspect(self) -> GlobalLibraryDiagnostic:
        try:
            snapshot = self.load_snapshot()
        except GlobalLibraryError as exc:
            return GlobalLibraryDiagnostic(
                status=exc.status,
                database_path=str(self.path),
                message=str(exc),
            )
        if snapshot.store_status == ABSENT:
            return GlobalLibraryDiagnostic(
                status=ABSENT,
                database_path=str(self.path),
                message="global exact glyph library is absent; read-only inspection created nothing",
            )
        return GlobalLibraryDiagnostic(
            status=VALID,
            database_path=str(self.path),
            message="global exact glyph library validated",
            schema_version=snapshot.schema_version,
            generation=snapshot.generation,
        )

    def load_snapshot(self) -> GlobalExactGlyphSnapshot:
        return _load_snapshot_from_path(self.path, self.busy_timeout_ms)

    def load_inspection_snapshot(self) -> GlobalLibraryInspectionSnapshot:
        """Read all states and audit details without initializing or repairing.

        Validation, effective Global safety and audit rows share one SQLite
        snapshot. The decoder's smaller snapshot and dependency contract stay
        unchanged. SQLite itself remains the authority for live WAL reads.
        """
        if _path_is_absent(self.path):
            return GlobalLibraryInspectionSnapshot(
                ABSENT, str(self.path), _utc_now(), MappingProxyType({}), ())
        connection: sqlite3.Connection | None = None
        try:
            connection = _connect_existing(
                self.path, read_only=True, busy_timeout_ms=self.busy_timeout_ms)
            connection.execute("BEGIN")
            validated = _validate_connection(connection, self.path)
            tables = {}
            for table in ("source_evidence", "promotion_approval", "glyph_conflict",
                          "migration_candidate", "provenance_event"):
                grouped: dict[str, list[Mapping[str, Any]]] = {}
                # Table and key names are a closed internal list, never GUI input.
                key = _PRIMARY_KEYS[table][0]
                for row in connection.execute(f"SELECT * FROM {table} ORDER BY {key}"):
                    grouped.setdefault(row["glyph_id"], []).append(MappingProxyType(dict(row)))
                tables[table] = grouped
            records = []
            for glyph_id, row in sorted(validated["glyphs"].items()):
                evidence, approvals, conflicts, legacy, provenance = (
                    tuple(tables[table].get(glyph_id, ())) for table in (
                        "source_evidence", "promotion_approval", "glyph_conflict",
                        "migration_candidate", "provenance_event"))
                read_conflict = validated["read_side_conflicts"].get(glyph_id)
                # Exactly the validated state/suppression gate used by load_snapshot;
                # no quorum or approval inference from presentation counts.
                allowed = row["state"] == VERIFIED_GLOBAL and read_conflict is None
                reason = "" if allowed else (
                    "LEGACY_V0_READ_CONFLICT" if read_conflict else row["state"])
                readings = {item["reading"] for item in (*evidence, *legacy)}
                if row["active_reading"] is not None:
                    readings.add(row["active_reading"])
                for conflict in conflicts:
                    readings.update(json.loads(conflict["conflicting_readings_json"]))
                records.append(GlobalGlyphInspectionRecord(
                    glyph_id=glyph_id, identity=row["identity"], stored_state=row["state"],
                    stored_active_reading=row["active_reading"] or "", readings=tuple(sorted(readings)),
                    global_reuse_allowed=allowed, reuse_block_reason=reason,
                    direct_source_count=row["direct_source_count"],
                    independent_source_count=row["independent_source_count"],
                    truth=MappingProxyType({key: value for key, value in row.items() if key != "identity"}),
                    source_evidence=evidence, approvals=approvals, conflicts=conflicts,
                    legacy_candidates=legacy, provenance=provenance, read_side_conflict=read_conflict))
            connection.execute("COMMIT")
            return GlobalLibraryInspectionSnapshot(
                VALID, str(self.path), _utc_now(), MappingProxyType(dict(validated["meta"])), tuple(records))
        except BaseException as exc:
            if connection is not None and connection.in_transaction:
                connection.execute("ROLLBACK")
            raise _translated_error(exc, self.path) from exc
        finally:
            if connection is not None:
                connection.close()

    def _load_after_initialization_serialization(self) -> GlobalExactGlyphSnapshot:
        """Validate a present target only after taking SQLite's writer lock."""
        if _path_is_absent(self.path):
            raise GlobalLibraryAbsentError(
                "global library initialization target disappeared before validation",
                path=self.path,
            )
        connection: sqlite3.Connection | None = None
        try:
            connection = _connect_existing(
                self.path,
                read_only=False,
                busy_timeout_ms=self.busy_timeout_ms,
            )
            _begin_immediate_with_bounded_retry(
                connection,
                self.path,
                self.write_retry_limit,
            )
            _validate_connection(connection, self.path)
            connection.execute("COMMIT")
        except BaseException as exc:
            if connection is not None and connection.in_transaction:
                connection.execute("ROLLBACK")
            raise _translated_error(exc, self.path) from exc
        finally:
            if connection is not None:
                connection.close()
        return self.load_snapshot()

    def _build_initialization_candidate(self, candidate: Path) -> None:
        """Build and validate a private v1 SQLite store before publication."""
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                _sqlite_uri(candidate, "rwc"),
                uri=True,
                timeout=self.busy_timeout_ms / 1000.0,
                isolation_level=None,
            )
            _configure_connection(connection, self.busy_timeout_ms)
            journal_mode = str(connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]).lower()
            if journal_mode != "wal":
                raise GlobalLibraryValidationError(
                    f"SQLite 無法啟用 WAL：{journal_mode}",
                    path=self.path,
                )
            _begin_immediate_with_bounded_retry(
                connection,
                self.path,
                self.write_retry_limit,
            )
            existing_schema_objects = int(connection.execute(
                """
                SELECT COUNT(*)
                FROM sqlite_master
                WHERE name NOT LIKE 'sqlite_%' AND sql IS NOT NULL
                """
            ).fetchone()[0])
            existing_user_version = int(
                connection.execute("PRAGMA user_version").fetchone()[0]
            )
            if existing_schema_objects or existing_user_version:
                raise GlobalLibraryValidationError(
                    "private initialization candidate was not empty",
                    path=self.path,
                )
            for statement in _TABLE_SQL.values():
                connection.execute(statement)
            for statement in _INDEX_SQL.values():
                connection.execute(statement)
            timestamp = _utc_now()
            connection.execute(
                """
                INSERT INTO library_meta (
                    meta_id, schema_version, identity_contract_version,
                    promotion_policy_version, generation, created_at, updated_at
                ) VALUES (1, ?, ?, ?, 0, ?, ?)
                """,
                (
                    GLOBAL_LIBRARY_SCHEMA_VERSION,
                    GLOBAL_IDENTITY_CONTRACT_VERSION,
                    GLOBAL_PROMOTION_POLICY_VERSION,
                    timestamp,
                    timestamp,
                ),
            )
            connection.execute(
                f"PRAGMA user_version = {GLOBAL_LIBRARY_SQLITE_USER_VERSION}"
            )
            _validate_connection(connection, candidate)
            connection.execute("COMMIT")
            checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if checkpoint is None or int(checkpoint[0]) != 0:
                raise GlobalLibraryBusyError(
                    f"private initialization WAL checkpoint incomplete：{checkpoint}",
                    path=self.path,
                )
            _validate_connection(connection, candidate)
        except BaseException as exc:
            if connection is not None and connection.in_transaction:
                connection.execute("ROLLBACK")
            raise _translated_error(exc, self.path) from exc
        finally:
            if connection is not None:
                connection.close()
        snapshot = _load_snapshot_from_path(candidate, self.busy_timeout_ms)
        if snapshot.store_status != VALID or snapshot.generation != 0:
            raise GlobalLibraryValidationError(
                "private initialization candidate did not validate as generation zero",
                path=self.path,
            )

    def initialize(self) -> GlobalExactGlyphSnapshot:
        if not _path_is_absent(self.path):
            return self._load_after_initialization_serialization()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except PermissionError as exc:
            raise GlobalLibraryPermissionError(
                f"global library parent 無法建立：{self.path.parent}",
                path=self.path,
            ) from exc

        candidate = self.path.parent / (
            f".{self.path.name}.{uuid4().hex}.initializing.sqlite3"
        )
        try:
            self._build_initialization_candidate(candidate)
            try:
                # Hard-link publication is atomic and create-if-absent: no
                # caller can observe the private pre-commit SQLite state, and
                # an already-present target is never overwritten or replaced.
                os.link(candidate, self.path)
            except FileExistsError:
                pass
            except PermissionError as exc:
                raise GlobalLibraryPermissionError(
                    f"global library initialization target 無法 publish：{self.path}",
                    path=self.path,
                ) from exc
            except OSError as exc:
                raise GlobalLibraryValidationError(
                    f"global library initialization publish failure：{exc}",
                    path=self.path,
                ) from exc
        finally:
            for private_path in (
                candidate,
                Path(str(candidate) + "-wal"),
                Path(str(candidate) + "-shm"),
            ):
                try:
                    private_path.unlink()
                except FileNotFoundError:
                    pass
        return self._load_after_initialization_serialization()

    @contextmanager
    def write_transaction(self) -> Iterator[GlobalWriteTransaction]:
        if _path_is_absent(self.path):
            raise GlobalLibraryAbsentError(
                "global library absent；write 不得 implicit initialize",
                path=self.path,
            )
        connection: sqlite3.Connection | None = None
        try:
            connection = _connect_existing(
                self.path,
                read_only=False,
                busy_timeout_ms=self.busy_timeout_ms,
            )
            _begin_immediate_with_bounded_retry(
                connection,
                self.path,
                self.write_retry_limit,
            )
            _validate_connection(connection, self.path)
            transaction = GlobalWriteTransaction(connection, self.path)
            yield transaction
            _validate_connection(connection, self.path)
            connection.execute("COMMIT")
        except BaseException as exc:
            if connection is not None and connection.in_transaction:
                connection.execute("ROLLBACK")
            if isinstance(exc, GlobalLibraryError):
                raise
            if isinstance(exc, (sqlite3.Error, PermissionError)):
                raise _translated_error(exc, self.path) from exc
            raise
        finally:
            if connection is not None:
                connection.close()

    def get_glyph_revision(self, glyph_id: str) -> int | None:
        canonical_id = _sha256_text(glyph_id, "glyph_id")
        if _path_is_absent(self.path):
            return None
        connection: sqlite3.Connection | None = None
        try:
            connection = _connect_existing(
                self.path,
                read_only=True,
                busy_timeout_ms=self.busy_timeout_ms,
            )
            connection.execute("BEGIN")
            _validate_connection(connection, self.path)
            row = connection.execute(
                "SELECT revision FROM glyph_truth WHERE glyph_id = ?",
                (canonical_id,),
            ).fetchone()
            connection.execute("COMMIT")
            return None if row is None else int(row[0])
        except BaseException as exc:
            if connection is not None and connection.in_transaction:
                connection.execute("ROLLBACK")
            raise _translated_error(exc, self.path) from exc
        finally:
            if connection is not None:
                connection.close()

    def compare_and_swap_glyph_revision(
        self,
        glyph_id: str,
        expected_revision: int,
    ) -> GlyphCASResult:
        canonical_id = _sha256_text(glyph_id, "glyph_id")
        expected = _nonnegative_int(expected_revision, "expected_revision")
        with self.write_transaction() as transaction:
            applied, observed = transaction.cas_increment_glyph_revision(canonical_id, expected)
            if applied:
                generation = transaction.bump_generation()
                current = expected + 1
            else:
                generation = transaction.generation
                current = observed
        return GlyphCASResult(
            applied=applied,
            glyph_id=canonical_id,
            expected_revision=expected,
            observed_revision=observed,
            current_revision=current,
            generation=generation,
        )

    def execute_idempotent_write(
        self,
        *,
        intent_id: str,
        payload_digest: str,
        operation: Callable[[GlobalWriteTransaction], StorageMutation],
    ) -> ProcessedIntentReceipt:
        canonical_id = _strict_text(intent_id, "intent_id")
        canonical_payload = _sha256_text(payload_digest, "payload_digest")
        with self.write_transaction() as transaction:
            existing = transaction.lookup_processed_intent(canonical_id)
            if existing is not None:
                if str(existing["payload_digest"]) != canonical_payload:
                    raise GlobalLibraryIntentConflictError(
                        "same intent_id reused with different payload_digest",
                        path=self.path,
                    )
                return ProcessedIntentReceipt(
                    intent_id=canonical_id,
                    payload_digest=canonical_payload,
                    committed_generation=int(existing["committed_generation"]),
                    result_state=str(existing["result_state"]),
                    receipt_digest=str(existing["receipt_digest"]),
                    already_processed=True,
                )
            changes_before = transaction._connection.total_changes
            mutation = operation(transaction)
            if not isinstance(mutation, StorageMutation):
                raise GlobalLibraryValidationError(
                    "idempotent storage operation 必須回傳 StorageMutation",
                    path=self.path,
                )
            storage_changed = transaction._connection.total_changes > changes_before
            if not isinstance(mutation.changed, bool) or mutation.changed != storage_changed:
                raise GlobalLibraryValidationError(
                    "StorageMutation.changed 與實際 SQLite mutation 不一致",
                    path=self.path,
                )
            if mutation.changed:
                generation = transaction.bump_generation()
                result_state = INTENT_COMMITTED
            else:
                generation = transaction.generation
                result_state = INTENT_NO_EFFECT
            receipt = transaction.record_processed_intent(
                intent_id=canonical_id,
                payload_digest=canonical_payload,
                committed_generation=generation,
                result_state=result_state,
            )
            return ProcessedIntentReceipt(
                intent_id=canonical_id,
                payload_digest=canonical_payload,
                committed_generation=generation,
                result_state=result_state,
                receipt_digest=receipt,
                already_processed=False,
                mutation_value=mutation.value,
            )

    def lookup_processed_intent(
        self,
        intent_id: str,
        *,
        payload_digest: str | None = None,
    ) -> ProcessedIntentReceipt | None:
        canonical_id = _strict_text(intent_id, "intent_id")
        if _path_is_absent(self.path):
            return None
        connection: sqlite3.Connection | None = None
        try:
            connection = _connect_existing(
                self.path,
                read_only=True,
                busy_timeout_ms=self.busy_timeout_ms,
            )
            connection.execute("BEGIN")
            _validate_connection(connection, self.path)
            row = connection.execute(
                "SELECT * FROM processed_intent WHERE intent_id = ?",
                (canonical_id,),
            ).fetchone()
            connection.execute("COMMIT")
            if row is None:
                return None
            if payload_digest is not None and str(row["payload_digest"]) != _sha256_text(
                payload_digest,
                "payload_digest",
            ):
                raise GlobalLibraryIntentConflictError(
                    "same intent_id has a different persisted payload_digest",
                    path=self.path,
                )
            return ProcessedIntentReceipt(
                intent_id=canonical_id,
                payload_digest=str(row["payload_digest"]),
                committed_generation=int(row["committed_generation"]),
                result_state=str(row["result_state"]),
                receipt_digest=str(row["receipt_digest"]),
                already_processed=True,
            )
        except BaseException as exc:
            if connection is not None and connection.in_transaction:
                connection.execute("ROLLBACK")
            raise _translated_error(exc, self.path) from exc
        finally:
            if connection is not None:
                connection.close()

    def backup(self, destination: Path | str) -> GlobalExactGlyphSnapshot:
        destination_path = Path(destination)
        if not destination_path.is_absolute():
            raise GlobalLibraryPathError("backup destination 必須是 absolute path")
        if destination_path.resolve(strict=False) == self.path.resolve(strict=False):
            raise GlobalLibraryPathError("backup destination 不得是 live database")
        if destination_path.exists():
            raise GlobalLibraryValidationError("backup destination 已存在；拒絕覆寫")
        source_snapshot = self.load_snapshot()
        if source_snapshot.store_status == ABSENT:
            raise GlobalLibraryAbsentError("global library absent；無法 backup", path=self.path)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination_path.parent / (
            f".{destination_path.name}.{uuid4().hex}.tmp.sqlite3"
        )
        source: sqlite3.Connection | None = None
        target: sqlite3.Connection | None = None
        try:
            source = _connect_existing(
                self.path,
                read_only=True,
                busy_timeout_ms=self.busy_timeout_ms,
            )
            source.execute("BEGIN")
            _validate_connection(source, self.path)
            target = sqlite3.connect(temporary, isolation_level=None)
            _configure_connection(target, self.busy_timeout_ms)
            source.backup(target)
            source.execute("COMMIT")
            target.close()
            target = sqlite3.connect(temporary, isolation_level=None)
            _configure_connection(target, self.busy_timeout_ms)
            journal_mode = str(target.execute("PRAGMA journal_mode = WAL").fetchone()[0]).lower()
            if journal_mode != "wal":
                raise GlobalLibraryValidationError("backup 無法啟用 WAL")
            target.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            target.close()
            target = None
            _load_snapshot_from_path(temporary, self.busy_timeout_ms)
            os.replace(temporary, destination_path)
            backup_snapshot = _load_snapshot_from_path(destination_path, self.busy_timeout_ms)
            return backup_snapshot
        except BaseException as exc:
            if source is not None and source.in_transaction:
                source.execute("ROLLBACK")
            raise _translated_error(exc, self.path) from exc
        finally:
            if source is not None:
                source.close()
            if target is not None:
                target.close()
            for candidate in (
                temporary,
                Path(str(temporary) + "-wal"),
                Path(str(temporary) + "-shm"),
            ):
                try:
                    candidate.unlink()
                except FileNotFoundError:
                    pass


def inspect_global_exact_glyph_library(
    global_library_root: Path | str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    busy_timeout_ms: int = GLOBAL_LIBRARY_DEFAULT_BUSY_TIMEOUT_MS,
) -> GlobalLibraryDiagnostic:
    return GlobalExactGlyphRepository.resolved(
        global_library_root,
        environ=environ,
        busy_timeout_ms=busy_timeout_ms,
    ).inspect()


def initialize_global_exact_glyph_library(
    global_library_root: Path | str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    busy_timeout_ms: int = GLOBAL_LIBRARY_DEFAULT_BUSY_TIMEOUT_MS,
) -> GlobalExactGlyphSnapshot:
    return GlobalExactGlyphRepository.resolved(
        global_library_root,
        environ=environ,
        busy_timeout_ms=busy_timeout_ms,
    ).initialize()


def load_global_exact_glyph_snapshot(
    global_library_root: Path | str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    busy_timeout_ms: int = GLOBAL_LIBRARY_DEFAULT_BUSY_TIMEOUT_MS,
) -> GlobalExactGlyphSnapshot:
    return GlobalExactGlyphRepository.resolved(
        global_library_root,
        environ=environ,
        busy_timeout_ms=busy_timeout_ms,
    ).load_snapshot()


def backup_global_exact_glyph_library(
    destination: Path | str,
    global_library_root: Path | str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    busy_timeout_ms: int = GLOBAL_LIBRARY_DEFAULT_BUSY_TIMEOUT_MS,
) -> GlobalExactGlyphSnapshot:
    return GlobalExactGlyphRepository.resolved(
        global_library_root,
        environ=environ,
        busy_timeout_ms=busy_timeout_ms,
    ).backup(destination)

# Phase 4 write services. These are explicit operations; snapshot/decoder paths
# never call them. Operational intent/receipt bytes are not decoder evidence.
GLOBAL_DIRECT_INTENT_SCHEMA_VERSION = "1.0"


def _exact_fields(value: Any, fields: Iterable[str], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != set(fields):
        raise GlobalLibraryValidationError(f"{label}: missing or unknown fields")
    return dict(value)


def make_direct_evidence_intent(
    identity: Mapping[str, Any], *, reading: str, source_project_id: str,
    source_pdf_sha256: str, source_font_program_sha256: str,
    source_occurrence_id: str, source_review_id: str,
    decision_snapshot_sha256: str,
) -> dict[str, Any]:
    raw = _exact_fields(identity, (
        "kind", "style_group", "glyph_sha256", "identity_eligibility",
    ), "direct identity")
    exact = canonical_global_exact_identity(
        raw["kind"], raw["style_group"], raw["glyph_sha256"], raw["identity_eligibility"],
    )
    if raw != {
        "kind": exact.kind, "style_group": exact.style_group,
        "glyph_sha256": exact.glyph_sha256, "identity_eligibility": exact.identity_eligibility,
    }:
        raise GlobalLibraryValidationError("direct identity is not canonical")
    glyph_id = compute_global_glyph_id(
        exact.kind, exact.style_group, exact.glyph_sha256,
        identity_eligibility=exact.identity_eligibility,
    )
    evidence = canonical_source_evidence_id_payload(
        glyph_id=glyph_id, reading=reading, evidence_class=DIRECT_VISUAL_ACTUAL,
        source_project_id=source_project_id, source_pdf_sha256=source_pdf_sha256,
        source_font_program_sha256=source_font_program_sha256,
        source_occurrence_id=source_occurrence_id, source_review_id=source_review_id,
        decision_snapshot_sha256=decision_snapshot_sha256, confirmation_channel=PDF_PAGE_VISUAL,
    )
    payload = {
        "schema_version": GLOBAL_DIRECT_INTENT_SCHEMA_VERSION,
        "promotion_policy_version": GLOBAL_PROMOTION_POLICY_VERSION,
        "identity": raw, "evidence": evidence,
    }
    digest = _canonical_sha256(payload)
    return {"intent_id": "gpi_" + _canonical_sha256(evidence),
            "payload_digest": digest, "payload": payload}


def canonical_direct_evidence_intent(value: Any) -> dict[str, Any]:
    item = _exact_fields(value, ("intent_id", "payload_digest", "payload"), "intent")
    payload = _exact_fields(item["payload"], (
        "schema_version", "promotion_policy_version", "identity", "evidence",
    ), "intent payload")
    if (payload["schema_version"] != GLOBAL_DIRECT_INTENT_SCHEMA_VERSION
            or payload["promotion_policy_version"] != GLOBAL_PROMOTION_POLICY_VERSION):
        raise GlobalLibraryValidationError("unsupported direct intent contract")
    evidence = _exact_fields(payload["evidence"], (
        "evidence_id_schema_version", "glyph_id", "reading", "evidence_class",
        "source_project_id", "source_pdf_sha256", "source_font_program_sha256",
        "source_occurrence_id", "source_review_id", "decision_snapshot_sha256",
        "confirmation_channel",
    ), "direct evidence")
    canonical = make_direct_evidence_intent(
        payload["identity"], **{key: evidence[key] for key in (
            "reading", "source_project_id", "source_pdf_sha256", "source_font_program_sha256",
            "source_occurrence_id", "source_review_id", "decision_snapshot_sha256",
        )},
    )
    if item != canonical:
        raise GlobalLibraryValidationError("intent ID/payload/evidence is not canonical")
    return canonical


def canonical_direct_evidence_batch(values: Any) -> list[dict[str, Any]]:
    if not isinstance(values, (list, tuple)):
        raise GlobalLibraryValidationError("intent batch must be a sequence")
    result, seen = [], {}
    for value in values:
        item = canonical_direct_evidence_intent(value)
        old = seen.get(item["intent_id"])
        if old is not None:
            raise GlobalLibraryIntentConflictError("duplicate intent ID in delivery batch")
        seen[item["intent_id"]] = item["payload_digest"]
        result.append(item)
    return sorted(result, key=lambda item: item["intent_id"])


def independent_direct_quorum(rows: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    """Choose a deterministic pair with all five independence axes distinct.

    Reuse the repository's project/PDF/font independence algorithm; occurrence
    and review identity are additional gates, never alternative independence.
    """
    direct = sorted(
        (row for row in rows if row["evidence_class"] == DIRECT_VISUAL_ACTUAL
         and int(row["counts_toward_global_quorum"]) == 1),
        key=lambda row: row["evidence_id"],
    )
    for index, first in enumerate(direct):
        for second in direct[index + 1:]:
            if (first["reading"] == second["reading"]
                    and first["source_occurrence_id"] != second["source_occurrence_id"]
                    and first["source_review_id"] != second["source_review_id"]
                    and _maximum_independent_source_count((first, second)) == 2):
                return (str(first["evidence_id"]), str(second["evidence_id"]))
    return ()


def _write_provenance(connection, item, event_type, *, approval_id=None):
    source = item["payload"]["evidence"]
    event = {
        "transaction_id": item["intent_id"], "glyph_id": source["glyph_id"],
        "event_type": event_type, "reading": source["reading"],
        "evidence_id": _canonical_sha256(source) if event_type == "SOURCE_EVIDENCE_RECORDED" else None,
        "approval_id": approval_id, "payload_digest": item["payload_digest"],
    }
    event["event_id"] = compute_provenance_event_id(**event)
    event["created_at"] = _utc_now()
    columns = _REQUIRED_COLUMNS["provenance_event"]
    connection.execute(
        "INSERT INTO provenance_event (" + ",".join(columns) + ") VALUES (" +
        ",".join("?" for _ in columns) + ")", tuple(event[key] for key in columns),
    )


def _revoke_current_approvals(connection, glyph_id):
    rows = [dict(row) for row in connection.execute(
        "SELECT * FROM promotion_approval WHERE glyph_id=? AND status=?",
        (glyph_id, APPROVED),
    )]
    for row in rows:
        args = {key: row[key] for key in (
            "glyph_id", "reading", "quorum_digest", "promotion_policy_version",
            "glyph_revision", "approval_source",
        )}
        revoked_id = compute_promotion_approval_id(**args, status=REVOKED)
        if connection.execute(
            "SELECT 1 FROM promotion_approval WHERE approval_id=?", (revoked_id,),
        ).fetchone() is not None:
            continue
        row.update(approval_id=revoked_id, status=REVOKED, approved_at=_utc_now())
        columns = _REQUIRED_COLUMNS["promotion_approval"]
        connection.execute(
            "INSERT INTO promotion_approval (" + ",".join(columns) + ") VALUES (" +
            ",".join("?" for _ in columns) + ")", tuple(row[key] for key in columns),
        )


def _ingest_direct_evidence(transaction: GlobalWriteTransaction, item: Mapping[str, Any]) -> bool:
    connection = transaction._connection
    payload, now = item["payload"], _utc_now()
    source = payload["evidence"]
    glyph_id, evidence_id = source["glyph_id"], _canonical_sha256(source)
    if connection.execute("SELECT 1 FROM source_evidence WHERE evidence_id=?", (evidence_id,)).fetchone():
        return False
    old = connection.execute("SELECT * FROM glyph_truth WHERE glyph_id=?", (glyph_id,)).fetchone()
    if old is None:
        transaction.insert_candidate_identity(payload["identity"])
        _write_provenance(connection, item, "CANDIDATE_CREATED")
        old = connection.execute("SELECT * FROM glyph_truth WHERE glyph_id=?", (glyph_id,)).fetchone()
    evidence = {key: value for key, value in source.items() if key != "evidence_id_schema_version"}
    evidence.update(evidence_id=evidence_id, counts_toward_global_quorum=1,
                    confirmed_at=now, ingested_at=now)
    columns = _REQUIRED_COLUMNS["source_evidence"]
    connection.execute(
        "INSERT INTO source_evidence (" + ",".join(columns) + ") VALUES (" +
        ",".join("?" for _ in columns) + ")", tuple(evidence[key] for key in columns),
    )
    rows = [dict(row) for row in connection.execute(
        "SELECT * FROM source_evidence WHERE glyph_id=? ORDER BY evidence_id", (glyph_id,),
    )]
    readings = {row["reading"] for row in rows if row["evidence_class"] == DIRECT_VISUAL_ACTUAL}
    readings.update(row[0] for row in connection.execute(
        "SELECT reading FROM migration_candidate WHERE glyph_id=?", (glyph_id,)))
    conflict = len(readings) > 1 or old["state"] == QUARANTINED_CONFLICT
    if conflict:
        _revoke_current_approvals(connection, glyph_id)
        opened = connection.execute(
            "SELECT * FROM glyph_conflict WHERE glyph_id=? AND status=?", (glyph_id, OPEN),
        ).fetchone()
        if opened:
            readings.update(json.loads(opened["conflicting_readings_json"]))
            conflict_id = compute_glyph_conflict_id(glyph_id, readings, int(opened["opened_generation"]))
            connection.execute(
                "UPDATE glyph_conflict SET conflict_id=?, conflicting_readings_json=?, last_event_at=? "
                "WHERE conflict_id=?",
                (conflict_id, _canonical_json(sorted(readings)), now, opened["conflict_id"]),
            )
        else:
            generation = transaction.generation + 1
            conflict_id = compute_glyph_conflict_id(glyph_id, readings, generation)
            connection.execute(
                "INSERT INTO glyph_conflict VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
                (conflict_id, glyph_id, OPEN, _canonical_json(sorted(readings)), now, now, generation),
            )
        state, active = QUARANTINED_CONFLICT, None
        _mark_current_migrations_conflicted(connection, glyph_id)
        _write_provenance(connection, item, "CONFLICT_OPENED")
    elif old["state"] == VERIFIED_GLOBAL:
        # Matching source growth is audit-only; preserve the approved revision.
        state, active = VERIFIED_GLOBAL, old["active_reading"]
    else:
        state = PROMOTION_READY if independent_direct_quorum(rows) else CANDIDATE
        active = None
    revision = int(old["revision"]) + (0 if state == VERIFIED_GLOBAL else 1)
    connection.execute(
        "UPDATE glyph_truth SET state=?, active_reading=?, direct_source_count=?, "
        "independent_source_count=?, revision=?, updated_at=? WHERE glyph_id=? AND revision=?",
        (state, active, sum(int(row["counts_toward_global_quorum"]) for row in rows),
         _maximum_independent_source_count(rows), revision, now, glyph_id, old["revision"]),
    )
    _write_provenance(connection, item, "SOURCE_EVIDENCE_RECORDED")
    return True


def deliver_global_direct_evidence(
    repository: GlobalExactGlyphRepository, intents: Sequence[Mapping[str, Any]],
) -> tuple[ProcessedIntentReceipt, ...]:
    """Validate first, explicitly initialize, then commit one all-or-none batch."""
    batch = canonical_direct_evidence_batch(intents)
    if not batch:
        return ()
    repository.initialize()
    with repository.write_transaction() as transaction:
        old_receipts, new_items = {}, []
        # Check every replay before the first mutation.
        for item in batch:
            old = transaction.lookup_processed_intent(item["intent_id"])
            if old is not None:
                if old["payload_digest"] != item["payload_digest"]:
                    raise GlobalLibraryIntentConflictError("same intent ID / different stored payload")
                old_receipts[item["intent_id"]] = ProcessedIntentReceipt(
                    old["intent_id"], old["payload_digest"], int(old["committed_generation"]),
                    old["result_state"], old["receipt_digest"], True,
                )
            else:
                new_items.append(item)
        changed = {item["intent_id"]: _ingest_direct_evidence(transaction, item) for item in new_items}
        generation = transaction.bump_generation() if any(changed.values()) else transaction.generation
        for item in new_items:
            state = INTENT_COMMITTED if changed[item["intent_id"]] else INTENT_NO_EFFECT
            digest = transaction.record_processed_intent(
                intent_id=item["intent_id"], payload_digest=item["payload_digest"],
                committed_generation=generation, result_state=state,
            )
            old_receipts[item["intent_id"]] = ProcessedIntentReceipt(
                item["intent_id"], item["payload_digest"], generation, state, digest, False,
            )
        return tuple(old_receipts[item["intent_id"]] for item in batch)


def approve_global_exact_glyph(
    repository: GlobalExactGlyphRepository, *, glyph_id: str, reading: str,
    glyph_revision: int, quorum_evidence_ids: Sequence[str], quorum_digest: str,
    promotion_policy_version: str, approval_source: str,
) -> dict[str, Any]:
    """Explicit manual approval; evidence delivery never invokes this service."""
    glyph_id = _sha256_text(glyph_id, "glyph_id")
    reading = _canonical_reading(reading)
    revision = _nonnegative_int(glyph_revision, "glyph_revision")
    if (promotion_policy_version != GLOBAL_PROMOTION_POLICY_VERSION
            or approval_source != EXPLICIT_MANUAL_APPROVAL):
        raise GlobalLibraryValidationError("approval policy/source incompatible")
    if not isinstance(quorum_evidence_ids, (list, tuple)):
        raise GlobalLibraryValidationError("quorum roster must be a sequence")
    ids = [_sha256_text(value, "quorum evidence ID") for value in quorum_evidence_ids]
    if ids != sorted(set(ids)) or len(ids) < 2:
        raise GlobalLibraryValidationError("quorum roster must be canonical")
    if _sha256_text(quorum_digest, "quorum_digest") != compute_quorum_digest(glyph_id, reading, ids):
        raise GlobalLibraryValidationError("stale quorum digest")
    request = {"glyph_id": glyph_id, "reading": reading, "glyph_revision": revision,
               "quorum_evidence_ids": ids, "quorum_digest": quorum_digest,
               "promotion_policy_version": promotion_policy_version, "approval_source": approval_source}
    request_digest = _canonical_sha256(request)
    conflict_intent_id = "approval_conflict_" + request_digest

    def conflict_result(generation, receipt_digest, replay):
        return {"approval_id": "", "approval_granted": False, "glyph_id": glyph_id,
                "state": QUARANTINED_CONFLICT, "glyph_revision": revision + 1,
                "generation": generation, "reason": "LEGACY_V0_READ_CONFLICT",
                "receipt_digest": receipt_digest, "already_processed": replay}

    with repository.write_transaction() as transaction:
        connection = transaction._connection
        receipt = transaction.lookup_processed_intent(conflict_intent_id)
        if receipt is not None:
            if receipt["payload_digest"] != request_digest:
                raise GlobalLibraryIntentConflictError("same approval conflict request / different payload")
            return conflict_result(int(receipt["committed_generation"]), receipt["receipt_digest"], True)
        glyph = connection.execute("SELECT * FROM glyph_truth WHERE glyph_id=?", (glyph_id,)).fetchone()
        if glyph is None or int(glyph["revision"]) != revision:
            raise GlobalLibraryValidationError("stale revision or non-promotion-ready glyph")
        rows = [dict(row) for row in connection.execute(
            "SELECT * FROM source_evidence WHERE glyph_id=? AND evidence_class=? ORDER BY evidence_id",
            (glyph_id, DIRECT_VISUAL_ACTUAL),
        )]
        if (ids != [row["evidence_id"] for row in rows]
                or any(row["reading"] != reading for row in rows)
                or not independent_direct_quorum(rows)):
            raise GlobalLibraryValidationError("stale/invalid approval evidence roster or reading")
        retained = {row["reading"] for row in rows}
        retained.update(row[0] for row in connection.execute(
            "SELECT reading FROM migration_candidate WHERE glyph_id=?", (glyph_id,)))
        if glyph["active_reading"]:
            retained.add(glyph["active_reading"])
        if len(retained) > 1 and glyph["state"] in {PROMOTION_READY, VERIFIED_GLOBAL}:
            # The prevalidation admits only the bounded historical exception.
            # Commit its safety transition instead of raising a rejection inside
            # this transaction (which would roll back quarantine/revocation).
            event = {"intent_id": conflict_intent_id, "payload": request, "payload_digest": request_digest}
            _migration_conflict_gate(transaction, event)
            generation = transaction.bump_generation()
            digest = transaction.record_processed_intent(
                intent_id=conflict_intent_id, payload_digest=request_digest,
                committed_generation=generation, result_state=INTENT_COMMITTED)
            return conflict_result(generation, digest, False)
        if glyph["state"] != PROMOTION_READY:
            raise GlobalLibraryValidationError("stale revision or non-promotion-ready glyph")
        approved_revision = revision + 1
        approval = {
            "glyph_id": glyph_id, "reading": reading, "quorum_digest": quorum_digest,
            "promotion_policy_version": promotion_policy_version, "glyph_revision": approved_revision,
            "approval_source": approval_source, "status": APPROVED,
        }
        approval_id = compute_promotion_approval_id(**approval)
        approval.update(approval_id=approval_id, quorum_evidence_ids_json=_canonical_json(ids),
                        approved_at=_utc_now())
        columns = _REQUIRED_COLUMNS["promotion_approval"]
        connection.execute(
            "INSERT INTO promotion_approval (" + ",".join(columns) + ") VALUES (" +
            ",".join("?" for _ in columns) + ")", tuple(approval[key] for key in columns),
        )
        connection.execute(
            "UPDATE glyph_truth SET state=?, active_reading=?, revision=?, updated_at=? "
            "WHERE glyph_id=? AND revision=?",
            (VERIFIED_GLOBAL, reading, approved_revision, _utc_now(), glyph_id, revision),
        )
        event = {"intent_id": "approval_" + approval_id, "payload_digest": _canonical_sha256(approval),
                 "payload": {"evidence": {"glyph_id": glyph_id, "reading": reading}}}
        _write_provenance(connection, event, "PROMOTION_APPROVED", approval_id=approval_id)
        _write_provenance(connection, event, "TRUTH_VERIFIED", approval_id=approval_id)
        generation = transaction.bump_generation()
        return {"approval_id": approval_id, "glyph_id": glyph_id, "state": VERIFIED_GLOBAL,
                "glyph_revision": approved_revision, "generation": generation}


def make_migration_intent(identity: Mapping[str, Any], *, reading: str,
                          legacy_project_id: str, legacy_project_sha256: str,
                          legacy_evidence_sha256: str, old_verification_level: str,
                          status: str) -> dict[str, Any]:
    exact = _exact_fields(identity, ("kind", "style_group", "glyph_sha256", "identity_eligibility"),
                          "migration identity")
    canonical = canonical_global_exact_identity(**exact)
    glyph_id = compute_global_glyph_id(**exact)
    if exact != {key: getattr(canonical, key) for key in exact}:
        raise GlobalLibraryValidationError("noncanonical migration identity")
    args = dict(glyph_id=glyph_id, reading=_canonical_reading(reading),
                legacy_project_id=_strict_text(legacy_project_id, "legacy_project_id"),
                legacy_project_sha256=_sha256_text(legacy_project_sha256, "legacy_project_sha256"),
                legacy_evidence_sha256=_sha256_text(legacy_evidence_sha256, "legacy_evidence_sha256"),
                old_verification_level=_strict_text(old_verification_level, "old_verification_level"))
    # Session identifiers are opaque tokens, never filesystem paths.
    if any(c in args["legacy_project_id"] for c in ("/", "\\", ":")):
        raise GlobalLibraryValidationError("project ID must not contain a path")
    if status not in MIGRATION_STATUSES or (old_verification_level == QUARANTINED_CONFLICT
                                           and status != MIGRATION_CONFLICT):
        raise GlobalLibraryValidationError("invalid migration status")
    if old_verification_level != QUARANTINED_CONFLICT and status == MIGRATION_CONFLICT:
        raise GlobalLibraryValidationError("ordinary import status is derived by the Global conflict gate")
    import_id = compute_migration_import_id(**args)
    payload = dict(args, identity=exact, import_id=import_id, status=status,
                   migration_import_contract_version=GLOBAL_MIGRATION_IMPORT_CONTRACT_VERSION,
                   legacy_row_key=compute_legacy_row_key(
                       glyph_id=glyph_id, reading=reading, old_verification_level=old_verification_level))
    return {"intent_id": "gmi_" + import_id, "payload": payload,
            "payload_digest": _canonical_sha256(payload)}


def canonical_migration_batch(values: Any) -> list[dict[str, Any]]:
    if not isinstance(values, (list, tuple)):
        raise GlobalLibraryValidationError("migration batch must be a sequence")
    result, seen, projects = [], set(), set()
    for raw in values:
        item = _exact_fields(raw, ("intent_id", "payload", "payload_digest"), "migration intent")
        payload = _exact_fields(item["payload"], (
            "glyph_id", "reading", "legacy_project_id", "legacy_project_sha256",
            "legacy_evidence_sha256", "old_verification_level", "identity", "import_id",
            "status", "migration_import_contract_version", "legacy_row_key"), "migration payload")
        canonical = make_migration_intent(**{key: payload[key] for key in (
            "identity", "reading", "legacy_project_id", "legacy_project_sha256",
            "legacy_evidence_sha256", "old_verification_level", "status")})
        if canonical != item or item["intent_id"] in seen:
            raise GlobalLibraryValidationError("noncanonical or duplicate migration intent")
        seen.add(item["intent_id"])
        projects.add((payload["legacy_project_id"], payload["legacy_project_sha256"]))
        result.append(canonical)
    if len(projects) > 1:
        raise GlobalLibraryValidationError("one selected project per migration batch")
    # An explicit conflict cannot be submitted as a partial one-reading batch.
    conflicts: dict[tuple[str, str], set[str]] = {}
    for item in result:
        p = item["payload"]
        if p["old_verification_level"] == QUARANTINED_CONFLICT:
            conflicts.setdefault((p["glyph_id"], p["legacy_evidence_sha256"]), set()).add(p["reading"])
    if any(len(readings) < 2 for readings in conflicts.values()):
        raise GlobalLibraryValidationError("incomplete project conflict migration")
    return sorted(result, key=lambda item: item["intent_id"])


def _migration_provenance(connection, item, event_type):
    p = item["payload"]
    # Use the common audit writer; no source_evidence row is fabricated.
    audit = dict(item, payload={"evidence": {"glyph_id": p["glyph_id"], "reading": p["reading"]}})
    _write_provenance(connection, audit, event_type)


def _ingest_migration(transaction: GlobalWriteTransaction, item: Mapping[str, Any]) -> bool:
    connection, p, now = transaction._connection, item["payload"], _utc_now()
    glyph_id = p["glyph_id"]
    old = connection.execute("SELECT * FROM glyph_truth WHERE glyph_id=?", (glyph_id,)).fetchone()
    if old is None:
        transaction.insert_candidate_identity(p["identity"])
        _migration_provenance(connection, item, "CANDIDATE_CREATED")
    columns = _REQUIRED_COLUMNS["migration_candidate"]
    row = {key: p[key] for key in columns if key != "imported_at"}
    row["imported_at"] = now
    connection.execute("INSERT INTO migration_candidate (" + ",".join(columns) + ") VALUES (" +
                       ",".join("?" for _ in columns) + ")", tuple(row[key] for key in columns))
    _migration_provenance(connection, item, "MIGRATION_CANDIDATE_IMPORTED")
    return True


def _mark_current_migrations_conflicted(connection: sqlite3.Connection, glyph_id: str) -> None:
    """Update Phase 5 statuses only; retain every legacy-v0 row verbatim."""
    current_ids = []
    for row in connection.execute("SELECT * FROM migration_candidate WHERE glyph_id=?", (glyph_id,)):
        args = {key: row[key] for key in (
            "glyph_id", "reading", "legacy_project_id", "legacy_project_sha256",
            "legacy_evidence_sha256", "old_verification_level")}
        if _stored_migration_contract(row["import_id"], **args) == GLOBAL_MIGRATION_IMPORT_CONTRACT_VERSION:
            current_ids.append((MIGRATION_CONFLICT, row["import_id"], MIGRATION_CONFLICT))
    connection.executemany("UPDATE migration_candidate SET status=? WHERE import_id=? AND status<>?", current_ids)


def _migration_conflict_gate(transaction: GlobalWriteTransaction, item: Mapping[str, Any]) -> None:
    connection, p, now = transaction._connection, item["payload"], _utc_now()
    glyph_id = p["glyph_id"]
    old = connection.execute("SELECT * FROM glyph_truth WHERE glyph_id=?", (glyph_id,)).fetchone()
    migrations = list(connection.execute("SELECT * FROM migration_candidate WHERE glyph_id=?", (glyph_id,)))
    readings = {row["reading"] for row in migrations}
    readings.update(row[0] for row in connection.execute(
        "SELECT reading FROM source_evidence WHERE glyph_id=? AND evidence_class=?",
        (glyph_id, DIRECT_VISUAL_ACTUAL)))
    if old["active_reading"]:
        readings.add(old["active_reading"])
    conflict = (len(readings) > 1 or old["state"] == QUARANTINED_CONFLICT or
                any(row["status"] == MIGRATION_CONFLICT for row in migrations))
    if not conflict:
        return  # Matching audit growth preserves state, approval, and revision.
    _revoke_current_approvals(connection, glyph_id)
    opened = connection.execute("SELECT * FROM glyph_conflict WHERE glyph_id=? AND status=?",
                                (glyph_id, OPEN)).fetchone()
    if opened:
        readings.update(json.loads(opened["conflicting_readings_json"]))
        generation = int(opened["opened_generation"])
        connection.execute(
            "UPDATE glyph_conflict SET conflict_id=?, conflicting_readings_json=?, last_event_at=? WHERE conflict_id=?",
            (compute_glyph_conflict_id(glyph_id, readings, generation), _canonical_json(sorted(readings)),
             now, opened["conflict_id"]))
    else:
        if len(readings) < 2:
            raise GlobalLibraryValidationError("conflict requires retained contradictory readings")
        generation = transaction.generation + 1
        connection.execute("INSERT INTO glyph_conflict VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
                           (compute_glyph_conflict_id(glyph_id, readings, generation), glyph_id, OPEN,
                            _canonical_json(sorted(readings)), now, now, generation))
    _mark_current_migrations_conflicted(connection, glyph_id)
    connection.execute("UPDATE glyph_truth SET state=?, active_reading=NULL, revision=revision+1, updated_at=? WHERE glyph_id=?",
                       (QUARANTINED_CONFLICT, now, glyph_id))
    _migration_provenance(connection, item, "CONFLICT_OPENED")


def deliver_global_migration(repository: GlobalExactGlyphRepository, intents: Sequence[Mapping[str, Any]]
                             ) -> tuple[ProcessedIntentReceipt, ...]:
    """Explicit apply: one project, one write transaction, no project outbox."""
    batch = canonical_migration_batch(intents)
    if not batch:
        repository.load_snapshot()  # Strict read-only validation; ABSENT stays absent.
        return ()
    repository.initialize()
    with repository.write_transaction() as transaction:
        receipts, pending = {}, []
        for item in batch:
            old = transaction.lookup_processed_intent(item["intent_id"])
            if old is not None:
                if old["payload_digest"] != item["payload_digest"]:
                    raise GlobalLibraryIntentConflictError("same migration ID / different payload")
                receipts[item["intent_id"]] = ProcessedIntentReceipt(
                    old["intent_id"], old["payload_digest"], int(old["committed_generation"]),
                    old["result_state"], old["receipt_digest"], True)
            else:
                # Existing rows without a matching receipt cannot be overwritten.
                if transaction._connection.execute("SELECT 1 FROM migration_candidate WHERE import_id=?",
                                                    (item["payload"]["import_id"],)).fetchone():
                    raise GlobalLibraryIntentConflictError("migration import exists without receipt")
                pending.append(item)
        for item in pending:
            _ingest_migration(transaction, item)
        representatives = {item["payload"]["glyph_id"]: item for item in pending}
        for glyph_id in sorted(representatives):
            _migration_conflict_gate(transaction, representatives[glyph_id])
        generation = transaction.bump_generation() if pending else transaction.generation
        for item in pending:
            digest = transaction.record_processed_intent(
                intent_id=item["intent_id"], payload_digest=item["payload_digest"],
                committed_generation=generation, result_state=INTENT_COMMITTED)
            receipts[item["intent_id"]] = ProcessedIntentReceipt(
                item["intent_id"], item["payload_digest"], generation, INTENT_COMMITTED, digest, False)
        return tuple(receipts[item["intent_id"]] for item in batch)
