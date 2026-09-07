from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

import fitz

import global_exact_glyph_library as library
from occurrence_ledger import canonical_bopomofo
from cff_zhuyin_decoder import CFFZhuyinInspector
from export_pdf_text_diagnostics import (
    TrueTypeGlyphInspector, build_font_lookup, resolve_font, normalize_basefont,
)

GLOBAL_PROMOTION_OUTBOX_SCHEMA_VERSION = "1.0"
GLOBAL_PROMOTION_OUTBOX_FILE = "global_exact_glyph_promotion_outbox.json"
PROJECT_TRANSACTION_FILE = "global_exact_glyph_project_transaction.json"
PROJECT_FILES = (
    "manual_actual_occurrence_overrides.csv", "user_verified_glyf_fingerprints.csv",
    "user_verified_cff_glyph_fingerprints.csv", "glyph_truth_conflicts.csv",
    "glyph_truth_provenance.csv", GLOBAL_PROMOTION_OUTBOX_FILE,
)
STAGING_FILE = "manual_actual_staging.json"
_LOCK = threading.RLock()
_ACTIVE_LOCKS: dict[str, int] = {}


def _json(raw: bytes):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise library.GlobalLibraryValidationError("duplicate JSON key: " + key)
            result[key] = value
        return result
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeError, ValueError) as exc:
        raise library.GlobalLibraryValidationError("invalid operational JSON") from exc


def _atomic_bytes(path: Path, raw: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_json(path: Path, value):
    _atomic_bytes(path, (library._canonical_json(value) + "\n").encode("utf-8"))


@contextmanager
def project_delivery_lock(root: Path):
    """Serialize project apply/delivery/ack; nested calls reuse the same lock."""
    root = Path(root).resolve()
    key = str(root)
    with _LOCK:
        if key in _ACTIVE_LOCKS:
            _ACTIVE_LOCKS[key] += 1
            try:
                yield
            finally:
                _ACTIVE_LOCKS[key] -= 1
            return
        root.mkdir(parents=True, exist_ok=True)
        with (root / ".global_exact_glyph_delivery.lock").open("a+b") as handle:
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            _ACTIVE_LOCKS[key] = 1
            try:
                yield
            finally:
                del _ACTIVE_LOCKS[key]
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _receipt(value, intent):
    fields = ("intent_id", "payload_digest", "committed_generation", "result_state", "receipt_digest")
    receipt = library._exact_fields(value, fields, "outbox receipt")
    generation = library._nonnegative_int(receipt["committed_generation"], "receipt generation")
    if (receipt["intent_id"] != intent["intent_id"]
            or receipt["payload_digest"] != intent["payload_digest"]
            or receipt["result_state"] not in library.INTENT_RESULT_STATES
            or receipt["receipt_digest"] != library.compute_processed_intent_receipt(
                receipt["intent_id"], receipt["payload_digest"], generation, receipt["result_state"])):
        raise library.GlobalLibraryValidationError("outbox receipt mismatch")
    return receipt


def validate_outbox(value):
    document = library._exact_fields(value, ("schema_version", "items"), "outbox")
    if document["schema_version"] != GLOBAL_PROMOTION_OUTBOX_SCHEMA_VERSION:
        raise library.GlobalLibraryValidationError("unsupported outbox schema")
    if not isinstance(document["items"], list):
        raise library.GlobalLibraryValidationError("outbox items must be a list")
    result, seen = [], set()
    for raw in document["items"]:
        item = library._exact_fields(raw, (
            "intent_id", "payload_digest", "payload", "status", "receipt",
        ), "outbox item")
        intent = library.canonical_direct_evidence_intent({
            key: item[key] for key in ("intent_id", "payload_digest", "payload")
        })
        if intent["intent_id"] in seen:
            raise library.GlobalLibraryIntentConflictError("duplicate outbox intent ID")
        seen.add(intent["intent_id"])
        if item["status"] == "PENDING":
            if item["receipt"] is not None:
                raise library.GlobalLibraryValidationError("pending intent cannot have receipt")
        elif item["status"] == "DELIVERED":
            _receipt(item["receipt"], intent)
        else:
            raise library.GlobalLibraryValidationError("unknown outbox status")
        result.append(item)
    return {"schema_version": GLOBAL_PROMOTION_OUTBOX_SCHEMA_VERSION,
            "items": sorted(result, key=lambda item: item["intent_id"])}


def load_promotion_outbox(root: Path):
    path = Path(root) / GLOBAL_PROMOTION_OUTBOX_FILE
    if not path.exists():
        return {"schema_version": GLOBAL_PROMOTION_OUTBOX_SCHEMA_VERSION, "items": []}
    return validate_outbox(_json(path.read_bytes()))


def enqueue_promotion_intents(root: Path, intents):
    batch = library.canonical_direct_evidence_batch(intents)
    with project_delivery_lock(root):
        document = load_promotion_outbox(root)
        by_id = {item["intent_id"]: item for item in document["items"]}
        changed = False
        for intent in batch:
            old = by_id.get(intent["intent_id"])
            if old is not None:
                if old["payload_digest"] != intent["payload_digest"] or old["payload"] != intent["payload"]:
                    raise library.GlobalLibraryIntentConflictError("same outbox ID / different payload")
                continue
            by_id[intent["intent_id"]] = dict(intent, status="PENDING", receipt=None)
            changed = True
        if changed:
            _write_json(Path(root) / GLOBAL_PROMOTION_OUTBOX_FILE, validate_outbox({
                "schema_version": GLOBAL_PROMOTION_OUTBOX_SCHEMA_VERSION, "items": list(by_id.values()),
            }))
    return len(batch)


def acknowledge_promotion_delivery(root: Path, frozen, receipts):
    batch = library.canonical_direct_evidence_batch(frozen)
    if len(batch) != len(receipts):
        raise library.GlobalLibraryValidationError("delivery receipt roster mismatch")
    by_receipt = {}
    for receipt in receipts:
        raw = {key: getattr(receipt, key) for key in (
            "intent_id", "payload_digest", "committed_generation", "result_state", "receipt_digest",
        )}
        if raw["intent_id"] in by_receipt:
            raise library.GlobalLibraryValidationError("duplicate receipt")
        by_receipt[raw["intent_id"]] = raw
    with project_delivery_lock(root):
        document = load_promotion_outbox(root)
        by_id = {item["intent_id"]: item for item in document["items"]}
        for intent in batch:
            old = by_id.get(intent["intent_id"])
            if old is None or any(old[key] != intent[key] for key in intent):
                raise library.GlobalLibraryIntentConflictError("outbox acknowledgement token is stale")
            receipt = _receipt(by_receipt.get(intent["intent_id"]), intent)
            if old["status"] == "DELIVERED" and old["receipt"] != receipt:
                raise library.GlobalLibraryIntentConflictError("different delivered receipt")
            by_id[intent["intent_id"]] = dict(intent, status="DELIVERED", receipt=receipt)
        _write_json(Path(root) / GLOBAL_PROMOTION_OUTBOX_FILE, validate_outbox({
            "schema_version": GLOBAL_PROMOTION_OUTBOX_SCHEMA_VERSION, "items": list(by_id.values()),
        }))


def _packed(raw):
    return None if raw is None else {
        "sha256": hashlib.sha256(raw).hexdigest(), "base64": base64.b64encode(raw).decode("ascii"),
    }


def _unpacked(value):
    if value is None:
        return None
    item = library._exact_fields(value, ("sha256", "base64"), "transaction preimage")
    try:
        raw = base64.b64decode(item["base64"], validate=True)
    except Exception as exc:
        raise library.GlobalLibraryValidationError("invalid transaction preimage") from exc
    if hashlib.sha256(raw).hexdigest() != item["sha256"]:
        raise library.GlobalLibraryValidationError("transaction preimage digest mismatch")
    return raw


def _restore_staging(root, packed):
    """Restore missing acknowledged decisions, preserving newer/upserted work."""
    raw = _unpacked(packed)
    if raw is None:
        return
    before = _json(raw)
    path = root / STAGING_FILE
    if not path.exists():
        _atomic_bytes(path, raw)
        return
    current = _json(path.read_bytes())
    if (current.get("schema_version") != before.get("schema_version")
            or not isinstance(current.get("staged_groups"), list)):
        raise library.GlobalLibraryValidationError("cannot recover changed staging contract")
    ids = {item["group_id"] for item in current["staged_groups"]}
    missing = [item for item in before["staged_groups"] if item["group_id"] not in ids]
    if missing:
        current["staged_groups"].extend(missing)
        _write_json(path, current)


def validate_post_commit_recovery_plan(value):
    plan = library._exact_fields(value, (
        "schema_version", "affected_occurrence_ids", "checked_postconditions",
    ), "post-commit recovery plan")
    if plan["schema_version"] != "1.0":
        raise library.GlobalLibraryValidationError("invalid recovery plan version")
    def identity(value, pattern):
        if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
            raise library.GlobalLibraryValidationError("malformed recovery identity")
        return value
    affected = plan["affected_occurrence_ids"]
    checked = plan["checked_postconditions"]
    if not isinstance(affected, list) or not isinstance(checked, list):
        raise library.GlobalLibraryValidationError("recovery plan lists required")
    for oid in affected:
        identity(oid, r"occ_[0-9a-f]{64}")
    if affected != sorted(set(affected)):
        raise library.GlobalLibraryValidationError("noncanonical/duplicate affected IDs")
    conditions = []
    for value in checked:
        condition = library._exact_fields(value, ("group_id", "occurrence_id", "reading"), "checked postcondition")
        identity(condition["group_id"], r"agr_[0-9a-f]{24}")
        identity(condition["occurrence_id"], r"occ_[0-9a-f]{64}")
        reading = condition["reading"]
        if not isinstance(reading, str) or not reading or canonical_bopomofo(reading) != reading:
            raise library.GlobalLibraryValidationError("noncanonical recovery reading")
        conditions.append(dict(condition))
    ids = [item["occurrence_id"] for item in conditions]
    if ids != sorted(set(ids)) or not set(ids).issubset(affected):
        raise library.GlobalLibraryValidationError("duplicate/unsorted/unaffected checked postconditions")
    return {"schema_version": "1.0", "affected_occurrence_ids": list(affected),
            "checked_postconditions": conditions}


def post_commit_recovery_plan_from_results(results):
    affected, conditions = set(), []
    for item in results:
        affected.update(item.get("affected_occurrence_ids") or item.get("target_occurrence_ids") or [])
        for oid in item["verified_occurrence_ids"]:
            conditions.append({"group_id": item["group_id"], "occurrence_id": oid, "reading": item["reading"]})
            affected.add(oid)
    return validate_post_commit_recovery_plan({
        "schema_version": "1.0", "affected_occurrence_ids": sorted(affected),
        "checked_postconditions": sorted(conditions, key=lambda item: item["occurrence_id"]),
    })


def _validated_project_journal(raw):
    journal = library._exact_fields(_json(raw), (
        "schema_version", "state", "files", "staging", "transaction_id", "recovery_plan",
    ), "project transaction")
    if journal["schema_version"] != "1.0" or journal["state"] not in {"PREPARED", "COMMITTED"}:
        raise library.GlobalLibraryValidationError("invalid project transaction contract")
    library._sha256_text(journal["transaction_id"], "project transaction ID")
    if journal["state"] == "COMMITTED" or journal["recovery_plan"] is not None:
        validate_post_commit_recovery_plan(journal["recovery_plan"])
    files = library._exact_fields(journal["files"], PROJECT_FILES, "transaction files")
    for value in files.values():
        _unpacked(value)
    _unpacked(journal["staging"])
    return journal


def recover_project_actual_transaction(root: Path):
    root = Path(root)
    path = root / PROJECT_TRANSACTION_FILE
    if not path.exists():
        return
    journal = _validated_project_journal(path.read_bytes())
    state = journal["state"]
    if state == "PREPARED":
        for name, packed in journal["files"].items():
            raw = _unpacked(packed)
            target = root / name
            if raw is None:
                target.unlink(missing_ok=True)
            else:
                _atomic_bytes(target, raw)
        _restore_staging(root, journal["staging"])
        path.unlink()
    return state


def recover_pending_project_actual_write(root: Path):
    """Explicit write/retry entry point; absent read-only projects stay absent."""
    root = Path(root)
    if (root / PROJECT_TRANSACTION_FILE).exists():
        with project_delivery_lock(root):
            return recover_project_actual_transaction(root)
    return None


def project_refresh_token(root: Path):
    path = Path(root) / PROJECT_TRANSACTION_FILE
    if not path.exists():
        return None
    journal = _validated_project_journal(path.read_bytes())
    if journal["state"] != "COMMITTED":
        raise library.GlobalLibraryValidationError("project actual transaction is incomplete")
    return journal["transaction_id"]


def committed_project_recovery(root: Path):
    """Read the strict durable plan without mutating operational files."""
    path = Path(root) / PROJECT_TRANSACTION_FILE
    if not path.exists():
        return None
    journal = _validated_project_journal(path.read_bytes())
    if journal["state"] != "COMMITTED":
        raise library.GlobalLibraryValidationError("project actual transaction is incomplete")
    return journal["transaction_id"], journal["recovery_plan"]


def assert_project_actual_readable(root: Path):
    # Read paths never perform recovery or write operational files.
    project_refresh_token(root)


def acknowledge_project_refresh(root: Path, token):
    if token is None:
        return
    with project_delivery_lock(root):
        if project_refresh_token(root) != token:
            raise library.GlobalLibraryIntentConflictError("project refresh acknowledgement is stale")
        (Path(root) / PROJECT_TRANSACTION_FILE).unlink()


@contextmanager
def direct_visual_project_transaction(root: Path):
    """Durable undo journal for the six-file commit and staging acknowledgement.

    An interrupted PREPARED transaction is rolled back before any retry/delivery.
    COMMITTED is published only after project writes and acknowledgement finish.
    No Global SQLite operation occurs inside this context.
    """
    root = Path(root)
    with project_delivery_lock(root):
        recover_project_actual_transaction(root)
        if project_refresh_token(root) is not None:
            raise library.GlobalLibraryValidationError("finish committed project recovery before a new actual transaction")
        load_promotion_outbox(root)  # malformed outbox fails before project writes
        journal = {
            "schema_version": "1.0", "state": "PREPARED",
            "transaction_id": hashlib.sha256(uuid4().bytes).hexdigest(),
            "recovery_plan": None,
            "files": {name: _packed((root / name).read_bytes() if (root / name).exists() else None)
                      for name in PROJECT_FILES},
            "staging": _packed((root / STAGING_FILE).read_bytes() if (root / STAGING_FILE).exists() else None),
        }
        path = root / PROJECT_TRANSACTION_FILE
        _write_json(path, journal)
        def bind_recovery_plan(plan):
            journal["recovery_plan"] = validate_post_commit_recovery_plan(plan)
        try:
            yield bind_recovery_plan
            validate_post_commit_recovery_plan(journal["recovery_plan"])
            _write_json(path, journal)  # plan durable before COMMITTED publication
            journal["state"] = "COMMITTED"
            _write_json(path, journal)
        except BaseException:
            recover_project_actual_transaction(root)
            raise
        # Retain the COMMITTED marker until a full-session refresh succeeds.
        # A crash after Global acknowledgement must not lose refresh recovery.


def deliver_pending_promotion_outbox(root: Path, repository=None):
    """A Global failure is a split outcome, never a project evidence rollback."""
    if not any((Path(root) / name).exists() for name in (GLOBAL_PROMOTION_OUTBOX_FILE, PROJECT_TRANSACTION_FILE)):
        return {"status": "NO_PENDING", "delivered_count": 0}
    try:
        with project_delivery_lock(root):
            recover_project_actual_transaction(root)
            document = load_promotion_outbox(root)
            pending = [{key: item[key] for key in ("intent_id", "payload_digest", "payload")}
                       for item in document["items"] if item["status"] == "PENDING"]
            if not pending:
                return {"status": "NO_PENDING", "delivered_count": 0}
            repository = repository or library.GlobalExactGlyphRepository.resolved()
            receipts = library.deliver_global_direct_evidence(repository, pending)
            acknowledge_promotion_delivery(root, pending, receipts)
            return {"status": "DELIVERED", "delivered_count": len(receipts),
                    "receipt_digests": [receipt.receipt_digest for receipt in receipts]}
    except Exception as exc:
        return {"status": "PENDING_RETRY", "delivered_count": 0,
                "error_type": type(exc).__name__, "error": str(exc)}


def _index(value, label):
    if isinstance(value, bool) or isinstance(value, float) and not value.is_integer():
        raise ValueError(label + " must be integral")
    result = int(value)
    if result < 0:
        raise ValueError(label + " is negative")
    return result


def direct_visual_intents(group, reading, checked_occurrence_ids, source_context):
    """Only frozen checked decisions admitted from current, sealed PDF bytes.

    Insufficient Global proof is explicitly reported without rejecting the local
    occurrence correction. No expected/decoder reading is consulted here.
    """
    ids = list(checked_occurrence_ids)
    if len(set(ids)) != len(ids) or not ids:
        raise ValueError("direct visual checked IDs must be nonempty and unique")
    members = {member["occurrence_id"]: member for member in group["members"]}
    if not set(ids).issubset(members):
        raise ValueError("direct visual checked IDs are not group members")
    intents, admissions = [], []
    for oid in ids:
        try:
            if source_context is None:
                raise ValueError("NO_SEALED_SESSION_CONTEXT")
            project_id = library._strict_text(source_context["session_id"], "source_project_id")
            member = members[oid]
            source = member.get("source_record") or {}
            info = source_context["pdfs"][str(member["pdf_name"])]
            raw_pdf = Path(info["pdf"]).read_bytes()
            pdf_sha = hashlib.sha256(raw_pdf).hexdigest()
            if library._sha256_text(info["pdf_sha256"], "sealed PDF SHA") != pdf_sha:
                raise ValueError("CURRENT_PDF_SEALED_SHA_MISMATCH")
            if member.get("pdf_sha256") and member["pdf_sha256"] != pdf_sha:
                raise ValueError("OCCURRENCE_PDF_SHA_MISMATCH")
            xref = _index(source.get("font_xref", member.get("font_xref")), "font_xref")
            gid = _index(source.get("注音元件ID", source.get("glyph_id_字形索引", member.get("glyph_id"))), "glyph_id")
            page_index = _index(member.get("physical_page"), "physical_page") - 1
            if page_index < 0:
                raise ValueError("invalid physical page")
            with fitz.open(stream=raw_pdf, filetype="pdf") as document:
                fonts = document[page_index].get_fonts(full=True)
                if xref not in {int(font[0]) for font in fonts}:
                    raise ValueError("OCCURRENCE_FONT_NOT_ON_CURRENT_PAGE")
                lookup = build_font_lookup(fonts)
                root_gid = _index(
                    source.get("glyph_id_字形索引", member.get("glyph_id", gid)), "root glyph",
                )
                matches = []
                for span in document[page_index].get_texttrace():
                    matched_xref, _basefont, _resource, _encoding = resolve_font(
                        lookup, normalize_basefont(span.get("font")),
                    )
                    if matched_xref != xref:
                        continue
                    for character in span.get("chars", ()):
                        codepoint, printed_gid, _origin, bbox = character[:4]
                        if (chr(codepoint) == member.get("char") and printed_gid == root_gid
                                and abs(float(bbox[0]) - float(member["x0"])) <= 0.8
                                and abs(float(bbox[1]) - float(member["y0"])) <= 0.8):
                            matches.append(printed_gid)
                if len(matches) != 1:
                    raise ValueError("CURRENT_OCCURRENCE_FONT_GLYPH_UNPROVABLE")
                font_name, extension, _kind, font_bytes = document.extract_font(xref)
            if not font_bytes:
                raise ValueError("EMBEDDED_FONT_UNAVAILABLE")
            if group["kind"] == library.TTF_GLYF_SHA256:
                if extension != "ttf":
                    raise ValueError("NOT_CURRENT_TTF")
                inspector = TrueTypeGlyphInspector(font_bytes)
                if root_gid != gid and gid not in {part["gid"] for part in inspector.components(root_gid)}:
                    raise ValueError("CURRENT_TTF_GLYPH_NOT_ATTACHED_TO_OCCURRENCE")
                identity = inspector.global_exact_identity(gid)
                if identity["eligibility"] != library.GLOBAL_ELIGIBLE_SIMPLE_GLYF_V1:
                    raise ValueError(identity.get("reason") or "NON_GLOBAL_ELIGIBLE")
                style = ""
            elif group["kind"] == library.CFF_GLYPH_SHA256:
                if extension != "cff":
                    raise ValueError("NOT_CURRENT_CFF")
                if root_gid != gid:
                    raise ValueError("CURRENT_CFF_GLYPH_MISMATCH")
                name = normalize_basefont(font_name)
                inspector = CFFZhuyinInspector(font_bytes, name, {})
                identity = inspector.global_exact_identity(gid)
                style = identity["style_group"]
                if style != group["style_group"] or style != source.get("CFF樣式群組"):
                    raise ValueError("CFF_STYLE_MISMATCH")
            else:
                raise ValueError("NO_GLOBAL_EXACT_IDENTITY")
            current_sha = identity["glyph_sha256"]
            field = "TTF字形SHA256" if group["kind"] == library.TTF_GLYF_SHA256 else "CFF整字字形SHA256"
            if current_sha != group["exact_key"] or current_sha != source.get(field):
                raise ValueError("WORKBOOK_CURRENT_GLYPH_SHA_MISMATCH")
            exact = {
                "kind": group["kind"], "style_group": style, "glyph_sha256": current_sha,
                "identity_eligibility": identity["eligibility"],
            }
            frozen = {
                "session_id": project_id, "pdf_sha256": pdf_sha,
                "font_program_sha256": hashlib.sha256(font_bytes).hexdigest(),
                "occurrence_id": oid, "review_id": member["review_id"], "identity": exact,
                "reading": reading, "confirmation_channel": library.PDF_PAGE_VISUAL,
            }
            intent = library.make_direct_evidence_intent(
                exact, reading=reading, source_project_id=project_id, source_pdf_sha256=pdf_sha,
                source_font_program_sha256=frozen["font_program_sha256"],
                source_occurrence_id=oid, source_review_id=member["review_id"],
                decision_snapshot_sha256=library._canonical_sha256(frozen),
            )
            intents.append(intent)
            admissions.append({"occurrence_id": oid, "status": "ELIGIBLE", "intent_id": intent["intent_id"]})
        except Exception as exc:
            admissions.append({"occurrence_id": oid, "status": "NON_GLOBAL_ELIGIBLE",
                               "reason": str(exc), "error_type": type(exc).__name__})
    return intents, admissions
