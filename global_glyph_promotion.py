from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import re
import threading
from contextlib import contextmanager, ExitStack
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
SURFACE_PROJECT_FILES = (*PROJECT_FILES, 'manual_surface_actual.json')
STAGING_FILE = "manual_actual_staging.json"
DIRECT_WRITE_CONTRACT = 'new-profile-direct-write-plan/1'
DIRECT_WRITE_ORDER = (PROJECT_FILES[1],PROJECT_FILES[2],PROJECT_FILES[0],PROJECT_FILES[3],
                      PROJECT_FILES[4],GLOBAL_PROMOTION_OUTBOX_FILE,STAGING_FILE)
_LOCK = threading.RLock()
_ACTIVE_LOCKS: dict[str, int] = {}


def profile_write_phase(root, phase=None):
    """One initial new-profile admission, or the caller's known continuation."""
    from project_write_admission import (project_from_actual_root,has_surface_indicators,
        capture_project_write_admission,entry_write_admission,entry_write_phase,SurfaceWritePhase)
    project=project_from_actual_root(root)
    if phase is not None:
        if project is None or phase.project!=project:
            raise library.GlobalLibraryValidationError('foreign ordinary write phase')
        phase.recheck()
        return phase
    if project is None or not has_surface_indicators(project):return None
    entered=entry_write_phase(project)
    if entered is not None:
        entered.recheck()
        return entered
    original=entry_write_admission(project) or capture_project_write_admission(project,recovery=True)
    original.require_writable()
    if not original.profile:
        raise library.GlobalLibraryValidationError('finish owned new-profile conversion recovery before ordinary write')
    return SurfaceWritePhase(original)


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


def _atomic_bytes(path: Path, raw: bytes, *, _prewrite_guard=None, _phase=None):
    if _phase is not None:
        _phase.plan({path:raw})
    if _prewrite_guard is not None:
        _prewrite_guard()
    if _phase is not None:
        _phase.recheck()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        if _prewrite_guard is not None:
            _prewrite_guard()
        if _phase is not None:
            _phase.recheck()
            _phase.attempting()
        os.replace(temporary, path)
        if _phase is not None:
            _phase.written()
    finally:
        temporary.unlink(missing_ok=True)


def _write_json(path: Path, value, *, _prewrite_guard=None, _phase=None):
    _atomic_bytes(path, (library._canonical_json(value) + "\n").encode("utf-8"),
        **({'_prewrite_guard':_prewrite_guard} if _prewrite_guard is not None else {}),
        **({'_phase':_phase} if _phase is not None else {}))


@contextmanager
def project_delivery_lock(root: Path, *, _admission=None):
    """Serialize project apply/delivery/ack; nested calls reuse the same lock."""
    root = Path(root).resolve()
    key = str(root)
    with _LOCK:
        from project_write_admission import (project_from_actual_root, capture_project_write_admission,
                                            ProjectWriteAdmission, has_surface_indicators)
        project = project_from_actual_root(root)
        if _admission is not None:
            if type(_admission) is not ProjectWriteAdmission or _admission.project != project:
                raise ValueError('PROJECT_WRITE_ADMISSION: foreign lock receipt')
            _admission.require_writable()
        if key in _ACTIVE_LOCKS:
            _ACTIVE_LOCKS[key] += 1
            try:
                yield
            finally:
                _ACTIVE_LOCKS[key] -= 1
            return
        owned = project is not None and has_surface_indicators(project)
        admission = capture_project_write_admission(project, recovery=True) if owned else None
        if admission is not None:
            admission.require_writable()
        if _admission is not None:
            _admission.recheck()
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
                if admission is not None:
                    admission.recheck()
                if _admission is not None:
                    _admission.recheck()
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


def prepare_promotion_outbox(raw,intents):
    document=validate_outbox(_json(raw)) if raw is not None else {'schema_version':'1.0','items':[]}
    batch=library.canonical_direct_evidence_batch(intents)
    by_id={item['intent_id']:item for item in document['items']}
    changed=False
    for intent in batch:
        old=by_id.get(intent['intent_id'])
        if old is not None:
            if old['payload_digest']!=intent['payload_digest'] or old['payload']!=intent['payload']:
                raise library.GlobalLibraryIntentConflictError('same outbox ID / different payload')
        else:
            by_id[intent['intent_id']]=dict(intent,status='PENDING',receipt=None);changed=True
    result=validate_outbox({'schema_version':'1.0','items':list(by_id.values())})
    return ((library._canonical_json(result)+'\n').encode('utf-8') if changed else raw),result


def write_prepared_outbox(root,raw,phase):
    path=Path(root)/GLOBAL_PROMOTION_OUTBOX_FILE
    if phase.expected_bytes(path)==raw:return
    if raw is None:raise library.GlobalLibraryValidationError('ordinary enqueue cannot delete outbox')
    _atomic_bytes(path,raw,_phase=phase)


def register_outbox_delivery(root,phase,raw):
    path=Path(root)/PROJECT_TRANSACTION_FILE
    original=phase.expected_bytes(path)
    if original is None:return  # Independent single-file retry, no fabricated J.
    journal=_validated_project_journal(original)
    if journal['schema_version']!='3.0' or journal['state']!='COMMITTED':
        raise library.GlobalLibraryValidationError('outbox delivery requires its committed journal/3')
    plan=journal['write_plan'];before=_unpacked(plan['afterimages']['files'][GLOBAL_PROMOTION_OUTBOX_FILE])
    if plan['outbox_delivery']:before=_unpacked(plan['outbox_delivery'][-1]['afterimage'])
    if before==raw:return
    if before is None:raise library.GlobalLibraryValidationError('cannot acknowledge absent pending outbox')
    delivery={'before_sha256':hashlib.sha256(before).hexdigest(),'afterimage':_packed(raw)}
    plan['outbox_delivery'].append(delivery)
    _validated_project_journal((library._canonical_json(journal)+'\n').encode('utf-8'))
    _write_json(path,journal,_phase=phase)


def acknowledge_promotion_delivery(root: Path, frozen, receipts, *, _phase=None):
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
    phase=profile_write_phase(root,_phase)
    if phase is not None and phase.expected_bytes(phase.project/'actual_refresh_publication.json') is not None:
        raise library.GlobalLibraryValidationError('finish owned actual publication before Global acknowledgement retry')
    with project_delivery_lock(root,**({'_admission':phase.receipt()} if phase is not None else {})):
        raw=phase.expected_bytes(Path(root)/GLOBAL_PROMOTION_OUTBOX_FILE) if phase is not None else None
        document = ((validate_outbox(_json(raw)) if raw is not None else {'schema_version':'1.0','items':[]})
                    if phase is not None else load_promotion_outbox(root))
        by_id = {item["intent_id"]: item for item in document["items"]}
        for intent in batch:
            old = by_id.get(intent["intent_id"])
            if old is None or any(old[key] != intent[key] for key in intent):
                raise library.GlobalLibraryIntentConflictError("outbox acknowledgement token is stale")
            receipt = _receipt(by_receipt.get(intent["intent_id"]), intent)
            if old["status"] == "DELIVERED" and old["receipt"] != receipt:
                raise library.GlobalLibraryIntentConflictError("different delivered receipt")
            by_id[intent["intent_id"]] = dict(intent, status="DELIVERED", receipt=receipt)
        updated=validate_outbox({
            "schema_version": GLOBAL_PROMOTION_OUTBOX_SCHEMA_VERSION, "items": list(by_id.values()),
        })
        if phase is not None:
            expected=(library._canonical_json(updated)+'\n').encode('utf-8')
            register_outbox_delivery(root,phase,expected)
            _atomic_bytes(Path(root)/GLOBAL_PROMOTION_OUTBOX_FILE,expected,_phase=phase)
        else:_write_json(Path(root)/GLOBAL_PROMOTION_OUTBOX_FILE,updated)


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
    parsed = _json(raw)
    version = parsed.get('schema_version') if isinstance(parsed,dict) else None
    fields = ('schema_version','state','files','staging','transaction_id','recovery_plan')
    required=(*fields,'owner','write_plan') if version=='3.0' else (*fields,'owner') if version=='2.0' else fields
    journal = library._exact_fields(parsed, required, 'project transaction')
    if version not in {'1.0','2.0','3.0'} or journal["state"] not in {"PREPARED", "COMMITTED"}:
        raise library.GlobalLibraryValidationError("invalid project transaction contract")
    if version in {'2.0','3.0'}:
        owner = library._exact_fields(journal['owner'],('project','session_id','origin_manifest_sha256','operation'),'surface transaction owner')
        if not isinstance(owner['project'],str) or not Path(owner['project']).is_absolute() or not owner['session_id']:
            raise library.GlobalLibraryValidationError('invalid surface transaction owner')
        library._sha256_text(owner['origin_manifest_sha256'],'origin manifest')
        if owner['operation'] not in {'direct-visual','surface-only'}:
            raise library.GlobalLibraryValidationError('unknown surface transaction operation')
        if version=='3.0' and owner['operation']!='direct-visual':
            raise library.GlobalLibraryValidationError('journal/3 is limited to new-profile direct-visual')
    library._sha256_text(journal["transaction_id"], "project transaction ID")
    if journal["state"] == "COMMITTED" or journal["recovery_plan"] is not None:
        validate_post_commit_recovery_plan(journal["recovery_plan"])
    files = library._exact_fields(journal["files"], SURFACE_PROJECT_FILES if version in {'2.0','3.0'} else PROJECT_FILES, "transaction files")
    for value in files.values():
        _unpacked(value)
    _unpacked(journal["staging"])
    if version=='3.0':
        _validate_direct_write_plan(journal)
    return journal


def _validate_direct_write_plan(journal):
    plan=library._exact_fields(journal['write_plan'],
        ('contract','dependencies','afterimages','outbox_delivery'),'direct write plan')
    if plan['contract']!=DIRECT_WRITE_CONTRACT or not isinstance(plan['dependencies'],dict):
        raise library.GlobalLibraryValidationError('unsupported direct write plan')
    for path,digest in plan['dependencies'].items():
        if not isinstance(path,str) or not Path(path).is_absolute() or str(Path(path).resolve())!=path:
            raise library.GlobalLibraryValidationError('invalid direct dependency path')
        if digest is not None:library._sha256_text(digest,'direct dependency')
    after=plan['afterimages']
    if not isinstance(plan['outbox_delivery'],list):
        raise library.GlobalLibraryValidationError('delivery history must be finite')
    if after is None:
        if journal['state']!='PREPARED' or journal['recovery_plan'] is not None or plan['outbox_delivery']:
            raise library.GlobalLibraryValidationError('unprepared direct outputs cannot be committed')
        return plan
    images=library._exact_fields(after,('files','staging'),'direct afterimages')
    library._exact_fields(images['files'],SURFACE_PROJECT_FILES,'direct afterimage files')
    for packed in images['files'].values():_unpacked(packed)
    _unpacked(images['staging'])
    validate_post_commit_recovery_plan(journal['recovery_plan'])
    if images['files']['manual_surface_actual.json']!=journal['files']['manual_surface_actual.json']:
        raise library.GlobalLibraryValidationError('ordinary transaction cannot change manual surface evidence')
    import actual_review as ar
    specs=((ar.USER_GLYF_FILE,ar.USER_GLYF_HEADERS),(ar.USER_CFF_FILE,ar.USER_CFF_HEADERS),
        (ar.OCCURRENCE_OVERRIDE_FILE,ar.OVERRIDE_HEADERS),(ar.GLYPH_CONFLICT_FILE,ar.GLYPH_CONFLICT_HEADERS),
        (ar.GLYPH_PROVENANCE_FILE,ar.GLYPH_PROVENANCE_HEADERS))
    for name,headers in specs:
        raw=_unpacked(images['files'][name])
        if raw is None:raise library.GlobalLibraryValidationError('new-profile direct output is missing')
        ar._validated_dynamic_csv_rows(raw,name,headers)
    staging=_unpacked(images['staging'])
    if staging is not None:ar._validate_manual_actual_staging_document(_json(staging))
    outbox=_unpacked(images['files'][GLOBAL_PROMOTION_OUTBOX_FILE])
    current=validate_outbox(_json(outbox)) if outbox is not None else {'schema_version':'1.0','items':[]}
    delivery=plan['outbox_delivery']
    if not isinstance(delivery,list):raise library.GlobalLibraryValidationError('delivery history must be finite')
    pending_count=sum(item['status']=='PENDING' for item in current['items'])
    if len(delivery)>pending_count:raise library.GlobalLibraryValidationError('too many outbox delivery phases')
    for entry in delivery:
        library._exact_fields(entry,('before_sha256','afterimage'),'direct outbox delivery')
        if outbox is None or entry['before_sha256']!=hashlib.sha256(outbox).hexdigest():
            raise library.GlobalLibraryValidationError('delivery is not bound to original pending outbox')
        next_raw=_unpacked(entry['afterimage'])
        updated=validate_outbox(_json(next_raw))
        _validate_outbox_delivery_transition(current,updated)
        if sum(item['status']=='DELIVERED' for item in updated['items'])<=sum(item['status']=='DELIVERED' for item in current['items']):
            raise library.GlobalLibraryValidationError('delivery phase made no monotone progress')
        current,outbox=updated,next_raw
    return plan


def _validate_outbox_delivery_transition(before,after):
    old={item['intent_id']:item for item in before['items']}
    new={item['intent_id']:item for item in after['items']}
    if set(old)!=set(new):raise library.GlobalLibraryValidationError('delivery changed intent roster')
    for key,original in old.items():
        updated=new[key]
        if original['status']=='DELIVERED':
            if updated!=original:raise library.GlobalLibraryValidationError('delivery changed original receipt')
        elif updated==original:
            continue
        elif (updated['status']!='DELIVERED' or
                any(updated[name]!=original[name] for name in ('intent_id','payload_digest','payload'))):
            raise library.GlobalLibraryValidationError('delivery is not exact pending-to-delivered')


def _new_direct_write_plan(root,phase):
    root=Path(root).resolve()
    excluded={str((root/name).resolve()) for name in (*SURFACE_PROJECT_FILES,STAGING_FILE,PROJECT_TRANSACTION_FILE)}
    excluded.add(str((phase.project/'actual_refresh_publication.json').resolve()))
    return {'contract':DIRECT_WRITE_CONTRACT,
        'dependencies':{name:hashlib.sha256(raw).hexdigest() if raw is not None else None
                        for name,raw in phase.files.items() if name not in excluded},
        'afterimages':None,'outbox_delivery':[]}


def _direct_image_map(root,journal,*,after=False):
    root=Path(root).resolve()
    value=journal['write_plan']['afterimages'] if after else journal
    if after and value is None:return None
    result={str((root/name).resolve()):_unpacked(packed) for name,packed in value['files'].items()}
    result[str((root/STAGING_FILE).resolve())]=_unpacked(value['staging'])
    return result


def _check_direct_receipt(root,journal,receipt,publication=None):
    """No writes; old SHA changes require the original publisher's exact link."""
    root=Path(root).resolve();project=receipt.project
    import standalone_proofread as sp
    import manual_surface_actual as surface
    manifest=sp.json_parse_strict(receipt.expected_bytes(project/'校對工作階段.json'),project/'校對工作階段.json')
    if journal['owner']['project']!=str(project) or journal['owner']['session_id']!=manifest['session_id']:
        raise library.GlobalLibraryValidationError('direct write plan owner differs from its project')
    if not surface.enabled(manifest):raise library.GlobalLibraryValidationError('journal/3 requires its surface profile')
    before=_direct_image_map(root,journal);after=_direct_image_map(root,journal,after=True)
    dependencies=journal['write_plan']['dependencies'];current=dict(receipt.files)
    stage=publication[2] if publication is not None else None
    ignored={str((root/PROJECT_TRANSACTION_FILE).resolve()),str((project/sp.ACTUAL_REFRESH_PUBLICATION).resolve()),*before}
    expected={name for name in current if name not in ignored and
              (stage is None or not Path(name).is_relative_to(stage))}
    if set(dependencies)!=expected:
        raise library.GlobalLibraryValidationError('direct dependency roster differs from original project contract')
    entries={};originals={}
    if publication is not None:
        pub,_,_,originals=publication
        if (pub['schema']!='actual-deferred-refresh/2' or pub['token']!=journal['transaction_id'] or
                pub['plan_sha']!=sp._hash_review_payload(journal['recovery_plan'])):
            raise library.GlobalLibraryValidationError('direct publication owner/plan link differs')
        old_manifest=sp.json_parse_strict(originals['校對工作階段.json'],project/sp.ACTUAL_REFRESH_PUBLICATION)
        if old_manifest['manifest_integrity_sha256']!=journal['owner']['origin_manifest_sha256']:
            raise library.GlobalLibraryValidationError('direct publication does not retain original manifest')
        entries={str((project/item['path']).resolve()):item for item in pub['entries']}
    for name,expected_sha in dependencies.items():
        raw=current[name];observed=hashlib.sha256(raw).hexdigest() if raw is not None else None
        if name in entries:
            entry=entries[name]
            if entry['old_sha']!=expected_sha:
                raise library.GlobalLibraryValidationError('publisher backup differs from frozen direct dependency')
            allowed={entry['old_sha'],entry['new_sha']} if publication[0]['phase']=='PREPARED' else {entry['new_sha']}
            if observed not in allowed:raise library.GlobalLibraryValidationError('foreign direct publication dependency')
        elif observed!=expected_sha:
            raise library.GlobalLibraryValidationError('direct dependency changed before recovery')
    actual={name:current[name] for name in before}
    if after is None:
        valid=actual==before
    elif journal['state']=='PREPARED':
        valid=False;prefix=dict(before)
        for name in (None,*DIRECT_WRITE_ORDER):
            if name is not None:prefix[str((root/name).resolve())]=after[str((root/name).resolve())]
            if actual==prefix:valid=True;break
    else:
        valid=actual==after
        delivery=journal['write_plan']['outbox_delivery']
        for item in delivery:
            delivered=dict(after);delivered[str((root/GLOBAL_PROMOTION_OUTBOX_FILE).resolve())]=_unpacked(item['afterimage'])
            valid=valid or actual==delivered
    if not valid:raise library.GlobalLibraryValidationError('direct rollback foreign current bytes; no recovery writes')
    return before,after


def _surface_transaction_images(journal,manifest):
    """Finite surface-only-all-pending/1; never infer general donor writes."""
    import manual_surface_actual as surface
    from actual_review import actual_group_identity
    from exact_glyph_identity import actual_group_id
    if (journal['schema_version']!='2.0' or journal['owner']['operation']!='surface-only'
            or not surface.enabled(manifest)
            or journal['owner']['session_id']!=manifest['session_id']):
        raise library.GlobalLibraryValidationError('surface rollback unsupported owner/profile')
    raw=_unpacked(journal['files'][surface.FILE])
    if raw is None:
        raise library.GlobalLibraryValidationError('surface rollback missing original document')
    document=surface.validate_document(surface.parse(raw),session_id=manifest['session_id'])
    after=surface.bytes_for(surface.apply_pending(document))
    plan=journal['recovery_plan']
    if plan is not None:
        plan=validate_post_commit_recovery_plan(plan)
        roster={item['occurrence_id']:item for item in manifest['records']}
        if len(roster)!=len(manifest['records']):
            raise library.GlobalLibraryValidationError('surface rollback duplicate source identity')
        expected=[]
        for item in document['pending']:
            source=item['proof']['source'];oid=source['occurrence_id']
            if oid not in roster or roster[oid]['review_id']!=source['review_id']:
                raise library.GlobalLibraryValidationError('surface rollback missing original identity')
            expected.append({'group_id':actual_group_id(actual_group_identity(roster[oid])),
                             'occurrence_id':oid,'reading':item['reading']})
        expected.sort(key=lambda item:item['occurrence_id'])
        if (plan['affected_occurrence_ids']!=[item['occurrence_id'] for item in expected]
                or plan['checked_postconditions']!=expected):
            raise library.GlobalLibraryValidationError('surface rollback plan differs from all original pending')
    return raw,after


def _surface_rollback_preconditions(journal,manifest,current_files,staging_raw):
    """One exact owner-state rule, shared by readonly admission and recovery."""
    import manual_surface_actual as surface
    before,after=_surface_transaction_images(journal,manifest)
    for name,packed in journal['files'].items():
        allowed=(before,after) if name==surface.FILE else (_unpacked(packed),)
        if current_files[name] not in allowed:
            raise library.GlobalLibraryValidationError('surface rollback foreign current bytes: '+name)
    if staging_raw!=_unpacked(journal['staging']):
        raise library.GlobalLibraryValidationError('surface rollback foreign staging bytes')
    return before,after


def recover_project_actual_transaction(root: Path, *, _phase=None):
    root = Path(root)
    from project_write_admission import project_from_actual_root, capture_project_write_admission, has_surface_indicators
    project = project_from_actual_root(root)
    path = root / PROJECT_TRANSACTION_FILE
    if _phase is not None:
        _phase.recheck()
        journal_raw=_phase.expected_bytes(path)
        if journal_raw is None:
            return
    else:
        if not path.exists():
            return
        journal_raw=path.read_bytes()
    journal = _validated_project_journal(journal_raw)
    owned = project is not None and (has_surface_indicators(project) or journal['schema_version']=='2.0')
    admission = (_phase.receipt() if _phase is not None else
                 capture_project_write_admission(project, recovery=True) if owned else None)
    if admission is not None and admission.expected_bytes(path)!=journal_raw:
        raise library.GlobalLibraryValidationError('surface rollback journal differs from original receipt')
    if journal['schema_version'] in {'2.0','3.0'}:
        if project is None:
            raise library.GlobalLibraryValidationError('surface transaction has no project owner')
        import standalone_proofread as sp
        if admission is None:
            raise library.GlobalLibraryValidationError('surface transaction has no admitted owner snapshot')
        manifest = sp.json_parse_strict(admission.expected_bytes(project/'校對工作階段.json'),
                                        project/'校對工作階段.json')
        if (journal['owner']['project'] != str(project.resolve())
                or journal['owner']['session_id'] != manifest['session_id']
                or (journal['state']=='PREPARED' and journal['owner']['origin_manifest_sha256'] != manifest['manifest_integrity_sha256'])):
            raise library.GlobalLibraryValidationError('surface transaction owner changed; no recovery writes')
    state = journal["state"]
    if state == "PREPARED":
        if journal['schema_version']=='3.0':
            if _phase is None:
                from project_write_admission import SurfaceWritePhase
                _phase=SurfaceWritePhase(admission)
            before,_after=_check_direct_receipt(root,journal,_phase.receipt())
            for name in reversed(DIRECT_WRITE_ORDER):
                target=root/name;raw=before[str(target.resolve())]
                if _phase.expected_bytes(target)==raw:continue
                if raw is None:
                    _phase.plan({target:None});_phase.attempting();target.unlink(missing_ok=True);_phase.written()
                else:_atomic_bytes(target,raw,_phase=_phase)
            _phase.recheck()
            _phase.plan({path:None});_phase.attempting();path.unlink();_phase.written()
            return state
        if (journal['schema_version']=='2.0' and journal['owner']['operation']=='direct-visual'
                and admission is not None and admission.profile):
            admission.require_writable()
            # Only the finite all-preimages prefix is writable for old /2.
            if (any(admission.expected_bytes(root/name)!=_unpacked(raw) for name,raw in journal['files'].items())
                    or admission.expected_bytes(root/STAGING_FILE)!=_unpacked(journal['staging'])):
                raise library.GlobalLibraryValidationError('legacy profile direct journal lacks exact afterimages')
            if _phase is None:
                from project_write_admission import SurfaceWritePhase
                _phase=SurfaceWritePhase(admission)
            _phase.plan({path:None});_phase.attempting();path.unlink();_phase.written()
            return state
        surface_only=journal['schema_version']=='2.0' and journal['owner']['operation']=='surface-only'
        if surface_only:
            import manual_surface_actual as surface
            before,after=_surface_rollback_preconditions(journal,manifest,
                {name:admission.expected_bytes(root/name) for name in journal['files']},
                admission.expected_bytes(root/STAGING_FILE))
        if admission is not None:
            admission.recheck()
        for name, packed in journal["files"].items():
            if surface_only and name!=surface.FILE:
                continue  # This operation never wrote glyph/outbox files.
            raw = _unpacked(packed)
            target = root / name
            if surface_only and admission.expected_bytes(target)==raw:
                continue
            if raw is None:
                target.unlink(missing_ok=True)
            else:
                _atomic_bytes(target, raw,**({'_phase':_phase} if _phase is not None else
                    {'_prewrite_guard':admission.recheck} if surface_only else {}))
        if not surface_only:
            _restore_staging(root, journal["staging"])
        else:
            from project_write_admission import ProjectWriteAdmission
            restored=dict(admission.files)
            restored[str((root/surface.FILE).resolve())]=before
            if _phase is None:
                ProjectWriteAdmission(admission.project,tuple(sorted(restored.items())),admission.profile,
                    workbook_roster=admission.workbook_roster).recheck()
            else:
                _phase.recheck()
        if _phase is not None:
            _phase.plan({path:None});_phase.attempting();path.unlink();_phase.written()
        else:
            path.unlink()
    return state


def recover_pending_project_actual_write(root: Path, *, _phase=None):
    """Explicit write/retry entry point; absent read-only projects stay absent."""
    root = Path(root)
    phase = profile_write_phase(root, _phase)
    if (phase.expected_bytes(root/PROJECT_TRANSACTION_FILE) is not None if phase is not None
            else (root / PROJECT_TRANSACTION_FILE).exists()):
        with project_delivery_lock(root):
            return recover_project_actual_transaction(root, **({'_phase':phase} if phase is not None else {}))
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


def acknowledge_project_refresh(root: Path, token, *, _phase=None):
    if token is None:
        return
    with project_delivery_lock(root):
        path=Path(root)/PROJECT_TRANSACTION_FILE
        if _phase is not None:
            _phase.recheck()
            journal=_validated_project_journal(_phase.expected_bytes(path))
            observed=journal['transaction_id'] if journal['state']=='COMMITTED' else None
        else:
            observed=project_refresh_token(root)
        if observed != token:
            raise library.GlobalLibraryIntentConflictError("project refresh acknowledgement is stale")
        if _phase is not None:
            _phase.plan({path:None});_phase.attempting();path.unlink();_phase.written()
        else:
            path.unlink()


@contextmanager
def direct_visual_project_transaction(root: Path, *, operation='direct-visual',
                                      _prewrite_guard=None, _surface_preimage=None,
                                      _surface_plan=None, _surface_origin_sha=None, _phase=None):
    """Durable undo journal for the six-file commit and staging acknowledgement.

    An interrupted PREPARED transaction is rolled back before any retry/delivery.
    COMMITTED is published only after project writes and acknowledgement finish.
    No Global SQLite operation occurs inside this context.
    """
    root = Path(root)
    from project_write_admission import project_from_actual_root,has_surface_indicators
    project=project_from_actual_root(root)
    direct_profile=operation=='direct-visual' and project is not None and (
        _phase.profile if _phase is not None else has_surface_indicators(project))
    if direct_profile and _phase is None:
        raise library.GlobalLibraryValidationError('new-profile ordinary transaction requires its original write phase')
    def check_transaction_inputs():
        if _prewrite_guard is not None:
            try:
                _prewrite_guard()
            except ValueError as exc:
                # Preserve the original surface-preimage rejection API when
                # the real receipt reader sees a transient foreign buffer.
                message='PROJECT_WRITE_ADMISSION: source changed before write: '+str((root/'manual_surface_actual.json').resolve())
                if _surface_preimage is not None and str(exc)==message:
                    raise library.GlobalLibraryValidationError(
                        'surface transaction preimage differs from admitted bytes') from exc
                raise
    with project_delivery_lock(root):
        check_transaction_inputs()
        recover_project_actual_transaction(root,**({'_phase':_phase} if _phase is not None else {}))
        from_phase=_phase.expected_bytes(root/PROJECT_TRANSACTION_FILE) if _phase is not None else None
        if (from_phase is not None if _phase is not None else project_refresh_token(root) is not None):
            raise library.GlobalLibraryValidationError("finish committed project recovery before a new actual transaction")
        if _phase is None:
            load_promotion_outbox(root)
        else:
            raw=_phase.expected_bytes(root/GLOBAL_PROMOTION_OUTBOX_FILE)
            if raw is not None:
                validate_outbox(_json(raw))
        surface_path = root/'manual_surface_actual.json'
        surface = _phase.expected_bytes(surface_path) is not None if _phase is not None else surface_path.exists()
        journal = {
            "schema_version": '3.0' if direct_profile else '2.0' if surface else '1.0', "state": "PREPARED",
            "transaction_id": hashlib.sha256(uuid4().bytes).hexdigest(),
            "recovery_plan": None,
            "files": {name: _packed(_phase.expected_bytes(root/name) if _phase is not None else
                                    (root/name).read_bytes() if (root/name).exists() else None)
                      for name in (SURFACE_PROJECT_FILES if surface else PROJECT_FILES)},
            "staging": _packed(_phase.expected_bytes(root/STAGING_FILE) if _phase is not None else
                               (root/STAGING_FILE).read_bytes() if (root/STAGING_FILE).exists() else None),
        }
        if surface:
            import standalone_proofread as sp
            project = root.resolve().parent.parent
            manifest = (sp.json_parse_strict(_phase.expected_bytes(project/'校對工作階段.json'),project/'校對工作階段.json')
                        if _phase is not None else sp.json_load_strict(project/'校對工作階段.json'))
            journal['owner'] = {'project':str(project),'session_id':manifest['session_id'],
                                'origin_manifest_sha256':manifest['manifest_integrity_sha256'],
                                'operation':operation}
            if _surface_origin_sha is not None and manifest['manifest_integrity_sha256']!=_surface_origin_sha:
                raise library.GlobalLibraryValidationError('surface transaction origin differs from admitted seal')
        if direct_profile:
            journal['write_plan']=_new_direct_write_plan(root,_phase)
        if (_surface_preimage is not None and
                (not surface or _unpacked(journal['files']['manual_surface_actual.json']) != _surface_preimage)):
            raise library.GlobalLibraryValidationError('surface transaction preimage differs from admitted bytes')
        if _surface_plan is not None:
            journal['recovery_plan']=validate_post_commit_recovery_plan(_surface_plan)
            _surface_transaction_images(journal,manifest)
        check_transaction_inputs()  # Before the first PREPARED journal write.
        path = root / PROJECT_TRANSACTION_FILE
        initial_journal_raw = (library._canonical_json(journal) + '\n').encode('utf-8')
        _write_json(path, journal,**({'_phase':_phase} if _phase is not None else {}))
        def bind_recovery_plan(plan, *, afterimages=None):
            checked=validate_post_commit_recovery_plan(plan)
            if _surface_plan is not None and checked!=journal['recovery_plan']:
                raise library.GlobalLibraryValidationError('surface transaction changed original pending plan')
            journal["recovery_plan"] = checked
            if direct_profile:
                if afterimages is None:
                    raise library.GlobalLibraryValidationError('new-profile direct plan requires exact prepared afterimages')
                journal['write_plan']['afterimages']={
                    'files':{name:_packed(afterimages[name]) for name in SURFACE_PROJECT_FILES},
                    'staging':_packed(afterimages[STAGING_FILE])}
                _validated_project_journal((library._canonical_json(journal)+'\n').encode('utf-8'))
                _write_json(path,journal,_phase=_phase)
        bind_recovery_plan.phase=_phase
        try:
            yield bind_recovery_plan
            validate_post_commit_recovery_plan(journal["recovery_plan"])
            _write_json(path, journal,**({'_phase':_phase} if _phase is not None else {}))
            journal["state"] = "COMMITTED"
            _write_json(path, journal,**({'_phase':_phase} if _phase is not None else {}))
        except BaseException as exc:
            if operation=='surface-only' and _surface_preimage is not None:
                from manual_surface_actual import SurfaceWriteNotPerformed
                if (isinstance(exc,SurfaceWriteNotPerformed)
                        and exc.path==(root/'manual_surface_actual.json').resolve()
                        and exc.expected_raw==_surface_preimage):
                    # The helper proved no replace was even attempted, whether
                    # a guard or temporary I/O failed. No body data was written.
                    # Undoing untouched files would destroy the competing writer.
                    from project_write_admission import _bytes
                    if _bytes(path) != initial_journal_raw:
                        raise library.GlobalLibraryValidationError(
                            'surface abort journal changed; no rollback or acknowledgement') from exc
                    path.unlink()  # Remove only our exact, uncommitted journal.
                    raise
            if _phase is not None:
                try:
                    _phase.reconcile()
                except ValueError as changed:
                    raise library.GlobalLibraryValidationError(
                        'surface rollback rejected foreign phase; no recovery writes') from changed
            recover_project_actual_transaction(root,**({'_phase':_phase} if _phase is not None else {}))
            raise
        # Retain the COMMITTED marker until a full-session refresh succeeds.
        # A crash after Global acknowledgement must not lose refresh recovery.


def deliver_pending_promotion_outbox(root: Path, repository=None, *, source_context=None, source_entries=None, _phase=None):
    """A Global failure is a split outcome, never a project evidence rollback."""
    phase=profile_write_phase(root,_phase)
    if not any((phase.expected_bytes(Path(root)/name) is not None if phase is not None else (Path(root)/name).exists())
               for name in (GLOBAL_PROMOTION_OUTBOX_FILE, PROJECT_TRANSACTION_FILE)):
        return {"status": "NO_PENDING", "delivered_count": 0}
    try:
        if phase is not None and phase.expected_bytes(phase.project/'actual_refresh_publication.json') is not None:
            raise library.GlobalLibraryValidationError('finish owned actual publication before Global delivery retry')
        with project_delivery_lock(root,**({'_admission':phase.receipt()} if phase is not None else {})):
            recover_project_actual_transaction(root,**({'_phase':phase} if phase is not None else {}))
            raw=phase.expected_bytes(Path(root)/GLOBAL_PROMOTION_OUTBOX_FILE) if phase is not None else None
            document = ((validate_outbox(_json(raw)) if raw is not None else {'schema_version':'1.0','items':[]})
                        if phase is not None else load_promotion_outbox(root))
            pending = [{key: item[key] for key in ("intent_id", "payload_digest", "payload")}
                       for item in document["items"] if item["status"] == "PENDING"]
            if not pending:
                return {"status": "NO_PENDING", "delivered_count": 0}
            # Retained intents are not proof that their source is still a raw
            # donor. Rebuild from current sealed source before Global mutation;
            # retain pending bytes when original source cannot be re-proved.
            if source_context is None or source_entries is None:
                raise library.GlobalLibraryValidationError("PENDING_DONOR_SOURCE_PROOF_MISSING")
            from actual_review import build_actual_group_for_entry
            by_id = {entry["occurrence_id"]: entry for entry in source_entries}
            if len(by_id) != len(source_entries):
                raise library.GlobalLibraryValidationError("PENDING_DONOR_SOURCE_ID_DUPLICATE")
            with _DirectVisualPDFSnapshot(source_context) as snapshot:
                for original in pending:
                    evidence = original["payload"]["evidence"]
                    oid = evidence["source_occurrence_id"]
                    entry = by_id.get(oid)
                    if entry is None or entry.get("review_id") != evidence["source_review_id"]:
                        raise library.GlobalLibraryValidationError("PENDING_DONOR_SOURCE_UNAVAILABLE")
                    group = build_actual_group_for_entry([entry], entry)
                    current, _admissions = direct_visual_intents(group, evidence["reading"], [oid], source_context,
                                                                _snapshot=snapshot)
                    if len(current) != 1 or current[0] != original:
                        raise library.GlobalLibraryValidationError("PENDING_DONOR_SOURCE_NOT_REPROVED")
            repository = repository or library.GlobalExactGlyphRepository.resolved()
            if phase is not None:phase.recheck()
            receipts = library.deliver_global_direct_evidence(repository, pending)
            acknowledge_promotion_delivery(root, pending, receipts,**({'_phase':phase} if phase is not None else {}))
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


class _DirectVisualPDFSnapshot:
    """Finite sealed PDF/page/font view for one direct-visual preparation."""
    def __init__(self, source_context):
        self.source_context = copy.deepcopy(source_context)
        self.stack = ExitStack()
        self.pdfs, self.pages, self.fonts, self.inspectors = {}, {}, {}, {}
        self.active = False
        self.closed = False

    def __enter__(self):
        if self.active or self.closed:
            raise ValueError("nested or closed direct visual PDF snapshot")
        from actual_source_visual_safety import source_visual_operation
        self.visual_operation = self.stack.enter_context(source_visual_operation())
        self.active = True
        return self

    def assert_current(self):
        for path, raw, document in self.pdfs.values():
            if path.read_bytes() != raw:
                raise ValueError("CURRENT_PDF_SEALED_SHA_MISMATCH")
        self.visual_operation.recheck_sources()

    def __exit__(self, kind, error, traceback):
        try:
            if kind is None:
                self.assert_current()
        finally:
            self.active = False
            self.closed = True
            self.stack.close()

    def source(self, source_context, member):
        if not self.active or self.source_context != source_context:
            raise ValueError("inactive or foreign direct visual PDF snapshot")
        if source_context is None:
            raise ValueError("NO_SEALED_SESSION_CONTEXT")
        info = self.source_context["pdfs"][str(member["pdf_name"])]
        path = Path(info["pdf"])
        seal = library._sha256_text(info["pdf_sha256"], "sealed PDF SHA")
        key = (str(path.resolve()), seal)
        if key not in self.pdfs:
            raw = self.visual_operation.read_source(path, seal)
            document = self.stack.enter_context(fitz.open(stream=raw, filetype="pdf"))
            self.pdfs[key] = (path, raw, document)
        return key, seal, self.pdfs[key][2]

    def page(self, key, document, page_index):
        page_key = (key, page_index)
        if page_key not in self.pages:
            page = document[page_index]
            fonts = page.get_fonts(full=True)
            self.pages[page_key] = (fonts, build_font_lookup(fonts), page.get_texttrace())
        return self.pages[page_key]

    def font(self, key, document, xref):
        font_key = (key, xref)
        if font_key not in self.fonts:
            self.fonts[font_key] = document.extract_font(xref)
        return self.fonts[font_key]

    def inspector(self, key, xref, kind, font_bytes, font_name):
        font_key = (key, xref, kind)
        if font_key not in self.inspectors:
            self.inspectors[font_key] = (TrueTypeGlyphInspector(font_bytes)
                if kind == library.TTF_GLYF_SHA256 else
                CFFZhuyinInspector(font_bytes, normalize_basefont(font_name), {}))
        return self.inspectors[font_key]


def direct_visual_intents(group, reading, checked_occurrence_ids, source_context, *, _snapshot=None):
    """Only frozen checked decisions admitted from current, sealed PDF bytes.

    Insufficient Global proof is explicitly reported without rejecting the local
    occurrence correction. No expected/decoder reading is consulted here.
    """
    if _snapshot is None:
        with _DirectVisualPDFSnapshot(source_context) as snapshot:
            return direct_visual_intents(group, reading, checked_occurrence_ids, source_context, _snapshot=snapshot)
    if not isinstance(_snapshot, _DirectVisualPDFSnapshot):
        raise ValueError("invalid direct visual PDF snapshot")
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
            pdf_key, pdf_sha, document = _snapshot.source(source_context, member)
            from actual_source_visual_safety import source_visual_safety
            safety = source_visual_safety(member, document=document)
            if not safety["donor_eligible"]:
                raise ValueError("FINAL_PAINT_NOT_RAW_GLYPH_DONOR: " + safety["reason"])
            if member.get("pdf_sha256") and member["pdf_sha256"] != pdf_sha:
                raise ValueError("OCCURRENCE_PDF_SHA_MISMATCH")
            xref = _index(source.get("font_xref", member.get("font_xref")), "font_xref")
            gid = _index(source.get("注音元件ID", source.get("glyph_id_字形索引", member.get("glyph_id"))), "glyph_id")
            page_index = _index(member.get("physical_page"), "physical_page") - 1
            if page_index < 0:
                raise ValueError("invalid physical page")
            fonts, lookup, trace = _snapshot.page(pdf_key, document, page_index)
            if xref not in {int(font[0]) for font in fonts}:
                raise ValueError("OCCURRENCE_FONT_NOT_ON_CURRENT_PAGE")
            root_gid = _index(
                source.get("glyph_id_字形索引", member.get("glyph_id", gid)), "root glyph",
            )
            matches = []
            for span in trace:
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
            font_name, extension, _kind, font_bytes = _snapshot.font(pdf_key, document, xref)
            if not font_bytes:
                raise ValueError("EMBEDDED_FONT_UNAVAILABLE")
            if group["kind"] == library.TTF_GLYF_SHA256:
                if extension != "ttf":
                    raise ValueError("NOT_CURRENT_TTF")
                inspector = _snapshot.inspector(pdf_key, xref, group["kind"], font_bytes, font_name)
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
                inspector = _snapshot.inspector(pdf_key, xref, group["kind"], font_bytes, font_name)
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
