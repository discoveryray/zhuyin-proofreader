"""Daily actual-only learning, using the existing evidence and approval services.

Learning proposals and delivery receipts are workflow records, never decoder
donors. Only the ordinary actual transaction/outbox and the existing explicit
approval service can create reusable truth. Expected values are not inspected.
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import actual_review as actual
import global_exact_glyph_library as library
import global_glyph_promotion as promotion

CONTRACT = "daily-glyph-confirmation/1"
DIRECTORY = "glyph_learning"
ACTION = "CONFIRM_VISIBLE_ACTUAL_AND_ENABLE_FIXED_EVIDENCE"
_AXES = ("source_project_id", "source_pdf_sha256", "source_font_program_sha256",
         "source_occurrence_id", "source_review_id")
_ENTRY_FIELDS = frozenset({"occurrence_id", "review_id", "pdf", "pdf_name", "pdf_sha256",
    "physical_page", "char", "font", "font_xref", "glyph_id", "zhuyin_component_id",
    "x0", "y0", "x1", "y1", "stable_key", "actual", "actual_raw", "actual_evidence", "source_record"})


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _actual_entry(entry):
    result = {key: copy.deepcopy(value) for key, value in entry.items() if key in _ENTRY_FIELDS}
    # Source records are the actual workbook's row. Remove any extraneous
    # expected-labelled metadata rather than retaining it in a decision record.
    result["source_record"] = {key: value for key, value in result.get("source_record", {}).items()
        if "expected" not in key.lower() and "應標" not in key}
    return result


def _repository(repository):
    return repository or library.GlobalExactGlyphRepository.resolved()


def _save_admission(plan, repository):
    # The existing project refresh/recovery chain resolves this same configured
    # repository. Refuse a test or caller override that would split delivery
    # across two stores; callers can isolate LOCALAPPDATA before opening.
    if repository.path != library.GlobalExactGlyphRepository.resolved().path:
        raise ValueError("LEARNING_CONTRACT: repository differs from configured project runtime")
    from project_write_admission import capture_project_write_admission
    admission = capture_project_write_admission(Path(plan["output_dir"]), recovery=True)
    admission.require_writable()
    return admission


def _glyph_id(descriptor):
    identity = descriptor["identity"]
    return library.compute_global_glyph_id(identity["kind"], identity["style_group"],
        identity["glyph_sha256"], identity_eligibility=identity["identity_eligibility"])


def _known_prompt_sources(evidence_root):
    """Read the existing verified sources, never upgrade historical rows."""
    from export_zhuyin_readings import load_verified_glyf_fingerprints
    program = Path(actual.__file__).resolve().parent
    manifest = promotion._json((program / "runtime_asset_manifest.json").read_bytes())
    assets = [item for item in manifest["assets"] if item.get("name") == "ttf_verified_glyf_fingerprints"]
    if len(assets) != 1 or assets[0].get("chain") != "actual":
        raise ValueError("LEARNING_ASSET: static exact source is not registered")
    path = program / actual.STATIC_TTF_FINGERPRINT_FILE
    if assets[0].get("path") != path.name:
        raise ValueError("LEARNING_ASSET: static source path changed")
    seal = assets[0]["sha256"]
    if hashlib.sha256(path.read_bytes()).hexdigest() != seal:
        raise ValueError("LEARNING_ASSET: static source SHA mismatch")
    static = load_verified_glyf_fingerprints(path)
    if hashlib.sha256(path.read_bytes()).hexdigest() != seal:
        raise ValueError("LEARNING_ASSET: static source changed while reading")
    if evidence_root is None:
        return static, {}, {}, {"ttf": set(), "cff": set()}
    root = Path(evidence_root)
    snapshot = actual.load_dynamic_actual_snapshot(root, read_only=True)
    return (static, actual.load_user_verified_glyf(root / actual.USER_GLYF_FILE, snapshot=snapshot),
        actual.load_user_verified_cff(root / actual.USER_CFF_FILE, snapshot=snapshot),
        actual.load_glyph_truth_quarantine(root, snapshot=snapshot))


def _reconfirmation_ids(evidence_root, records):
    if evidence_root is None:
        return set()
    ids = set()
    for receipt in (Path(evidence_root) / DIRECTORY).glob("*.receipt.json"):
        value = _read(receipt)
        if value.get("global_status") != "STALE_APPROVAL_REQUIRES_NEW_ACTION":
            continue
        decision = _read(receipt.with_name(receipt.name.removesuffix(".receipt.json") + ".json"))
        plan = _validate_plan(decision["plan"])
        if decision.get("explicit_action") is not True:
            raise ValueError("LEARNING_CONTRACT: no explicit action for reconfirmation")
        request = _approval_request(plan["approval"])
        record = records.get(request.glyph_id) if request else None
        if record and not record.global_reuse_allowed and record.stored_state != library.QUARANTINED_CONFLICT:
            ids.update(plan["checked_occurrence_ids"])
    return ids


def collect_learning_candidates(entries, source_context, *, repository=None, evidence_root=None):
    """Collect from the complete actual roster, independently of expected/state.

    State is used only to identify overlap with the existing textbook queue.
    Source descriptors are not DIRECT_VISUAL_ACTUAL or migration evidence.
    """
    repository = _repository(repository)
    inspection = repository.load_inspection_snapshot()
    records = {record.glyph_id: record for record in inspection.records}
    global_snapshot = repository.load_snapshot()
    static, project_ttf, project_cff, quarantine = _known_prompt_sources(evidence_root)
    reconfirm = _reconfirmation_ids(evidence_root, records)
    index = actual.index_actual_group_members(entries)
    groups, suppressed, diagnostics = [], [], []
    seen = set()
    with promotion._DirectVisualPDFSnapshot(source_context) as snapshot:
        for entry in entries:
            identity = actual.actual_group_identity(entry)
            key = actual.actual_group_match_key(identity)
            if key in seen:
                continue
            seen.add(key)
            group = actual.build_actual_group_for_entry(entries, entry, member_index=index)
            members = group["members"]
            descriptors, admissions = promotion.visual_source_descriptors(group,
                [member["occurrence_id"] for member in members], source_context, _snapshot=snapshot)
            by_id = {item["source_occurrence_id"]: item for item in descriptors}
            candidates = []
            for member in members:
                oid, reading = member["occurrence_id"], actual._canon(member.get("actual"))
                descriptor = by_id.get(oid)
                record = records.get(_glyph_id(descriptor)) if descriptor else None
                reason = ""
                reuse = None
                if descriptor is not None:
                    ident = descriptor["identity"]
                    is_ttf = ident["kind"] == library.TTF_GLYF_SHA256
                    key = ident["glyph_sha256"] if is_ttf else (ident["style_group"], ident["glyph_sha256"])
                    local = (project_ttf if is_ttf else project_cff).get(key, {})
                    fixed = static.get(key, {}) if is_ttf else {}
                    reuse = library.resolve_exact_glyph_reuse(global_snapshot, ident,
                        higher_priority_sources=(("PROJECT_VERIFIED_EXACT", local["bopomofo"]),) if local.get("bopomofo") else (),
                        lower_priority_sources=(("STATIC_VERIFIED_EXACT", fixed["bopomofo"]),) if fixed.get("bopomofo") else (),
                        exact_identity_quarantined=key in quarantine["ttf" if is_ttf else "cff"],
                        quarantine_readings=fixed.get("conflict_readings", ()))
                if oid in reconfirm:
                    candidates.append(member)
                    continue
                if (evidence_root is not None and reading
                        and actual.has_direct_occurrence_actual(Path(evidence_root), member, reading)):
                    reason = "DIRECT_POSITION_ALREADY_CONFIRMED"
                elif reuse is not None and not reuse.conflict and reading and reuse.reading == reading:
                    reason = "ALREADY_REUSABLE"
                elif record is not None and descriptor is not None:
                    matching = [row for row in record.source_evidence
                        if row["evidence_class"] == library.DIRECT_VISUAL_ACTUAL
                        and row["counts_toward_global_quorum"] == 1 and row["reading"] == reading]
                    # A copy/rename/re-run cannot add an independent source.
                    # This is only a prompt filter, never a quorum/approval gate.
                    if matching and not any(all(row[axis] != descriptor[axis] for axis in _AXES)
                                            for row in matching):
                        reason = "CORRELATED_SOURCE_ALREADY_CONFIRMED"
                if reason:
                    suppressed.append({"occurrence_id": oid, "reason": reason})
                else:
                    candidates.append(member)
            if candidates:
                pending = sorted(member["occurrence_id"] for member in members
                                 if member.get("state") in actual.ACTUAL_PENDING_STATES)
                groups.append({**group, "candidate_members": candidates,
                    "pending_occurrence_ids": pending, "learning_status": "AWAITING_VISUAL_CONFIRMATION",
                    "requires_fresh_approval_action": any(member["occurrence_id"] in reconfirm for member in candidates),
                    "source_admissions": admissions})
            diagnostics.extend(row for row in admissions if row["status"] != "ELIGIBLE")
    return {"groups": groups, "suppressed": suppressed, "diagnostics": diagnostics}


def _assert_hashes(hashes):
    for name, wanted in hashes.items():
        path = Path(name)
        observed = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""
        if observed != wanted:
            raise ValueError("LEARNING_STALE: displayed project data changed: " + name)


def _check_images(plan):
    from glyph_learning_images import LearningImageStore
    store = LearningImageStore(Path(plan["image_store_root"]))
    images = plan["image_evidence"]
    ids = [image["occurrence_id"] for image in images]
    if sorted(ids) != plan["checked_occurrence_ids"] or len(ids) != len(set(ids)):
        raise ValueError("LEARNING_IMAGES: every checked position requires exactly its viewed image")
    for image in images:
        store.read_evidence(image)


def _approval_value(request):
    return asdict(request) if request is not None else None


def _approval_request(value):
    if value is None:
        return None
    request = library.GlobalApprovalRequest(**{**value,
        "quorum_evidence_ids": tuple(value["quorum_evidence_ids"])})
    return request


def prepare_learning_confirmation(output_dir, snapshot, group_id, reading, checked_occurrence_ids,
        *, repository=None, image_evidence=(), image_store_root=None):
    """Freeze the proposal before the user presses the final save/enable button."""
    from standalone_proofread import _global_direct_source_context
    from glyph_learning_images import LearningImageStore
    output_dir = Path(output_dir).resolve()
    repository = _repository(repository)
    reading = actual._canon(reading)
    checked = sorted(checked_occurrence_ids)
    if not reading or not checked or len(checked) != len(set(checked)):
        raise ValueError("LEARNING_INPUT: a canonical reading and unique viewed positions are required")
    _assert_hashes(snapshot.hashes)
    by_id = {entry["occurrence_id"]: entry for entry in snapshot.ledger}
    if len(by_id) != len(snapshot.ledger) or any(oid not in by_id for oid in checked):
        raise ValueError("LEARNING_INPUT: occurrence roster changed")
    current = actual.build_actual_group_for_entry(snapshot.ledger, by_id[checked[0]])
    if current["group_id"] != group_id or not set(checked) <= {m["occurrence_id"] for m in current["members"]}:
        raise ValueError("LEARNING_INPUT: checked positions belong to another exact glyph")
    # Retain all exact peers for the existing conflict invalidation gate. Each
    # checked sample is later applied separately, with propagation disabled by
    # its singleton checked roster, so unseen peers never become direct proof.
    members = [_actual_entry(member) for member in current["members"]]
    if any(by_id[oid].get("manual_surface_actual") is not None for oid in checked):
        raise ValueError("LEARNING_SURFACE_ONLY: use the original-page surface correction service")
    group = actual.build_actual_group_for_entry(members, members[0])
    source_context = _global_direct_source_context(output_dir, snapshot.manifest)
    for member in members:
        if member["occurrence_id"] in checked:
            actual.require_actual_visual_target(member, output_dir)
    intents, admissions = promotion.direct_visual_intents(group, reading, checked, source_context)
    glyph_ids = {item["payload"]["evidence"]["glyph_id"] for item in intents}
    if len(glyph_ids) > 1:
        raise ValueError("LEARNING_INPUT: multiple Global identities in one decision")
    approval = (repository.project_direct_evidence_approval(intents, next(iter(glyph_ids)))
                if intents else None)
    store = LearningImageStore(Path(image_store_root) if image_store_root is not None else None)
    plan = {"contract": CONTRACT, "output_dir": str(output_dir), "repository_path": str(repository.path),
        "group": group, "reading": reading, "checked_occurrence_ids": checked,
        "source_context": source_context, "intents": intents, "admissions": admissions,
        "approval": _approval_value(approval.request) if approval else None,
        "approval_reason": approval.reason if approval else "僅保存本位置；不符合全域字形重用條件。",
        "image_store_root": str(store.root), "image_evidence": copy.deepcopy(list(image_evidence)),
        "input_hashes": copy.deepcopy(snapshot.hashes), "action": ACTION}
    plan = json.loads(json.dumps(plan, ensure_ascii=False, allow_nan=False))
    _check_images(plan)
    _assert_hashes(snapshot.hashes)
    plan["operation_id"] = _digest(plan)
    return plan


def _validate_plan(plan):
    fields = {"contract", "output_dir", "repository_path", "group", "reading", "checked_occurrence_ids",
        "source_context", "intents", "admissions", "approval", "approval_reason", "image_store_root",
        "image_evidence", "input_hashes", "action", "operation_id"}
    if type(plan) is not dict or set(plan) != fields or plan["contract"] != CONTRACT or plan["action"] != ACTION:
        raise ValueError("LEARNING_CONTRACT: unknown confirmation record")
    if plan["operation_id"] != _digest({key: value for key, value in plan.items() if key != "operation_id"}):
        raise ValueError("LEARNING_CONTRACT: fixed confirmation bytes changed")
    for key in ("output_dir", "repository_path", "image_store_root"):
        if not Path(plan[key]).is_absolute():
            raise ValueError("LEARNING_CONTRACT: path must be absolute")
    checked = plan["checked_occurrence_ids"]
    if (not isinstance(checked, list) or not checked or checked != sorted(set(checked))
            or actual._canon(plan["reading"]) != plan["reading"]):
        raise ValueError("LEARNING_CONTRACT: invalid reading/checked roster")
    if not set(checked) <= {member["occurrence_id"] for member in plan["group"]["members"]}:
        raise ValueError("LEARNING_CONTRACT: checked positions are outside the exact group")
    library.canonical_direct_evidence_batch(plan["intents"])
    request = _approval_request(plan["approval"])
    if request is not None and (request.database_path != plan["repository_path"]
                               or request.reading != plan["reading"]):
        raise ValueError("LEARNING_CONTRACT: approval differs from viewed decision")
    return plan


def _paths(output_dir, operation_id):
    library._sha256_text(operation_id, "learning operation ID")
    from standalone_proofread import project_actual_evidence_root
    root = project_actual_evidence_root(Path(output_dir)) / DIRECTORY
    return root / (operation_id + ".json"), root / (operation_id + ".receipt.json")


def _write(path, value):
    from glyph_learning_images import _atomic_write
    _atomic_write(path, (library._canonical_json(value) + "\n").encode("utf-8"))


def _read(path):
    return promotion._json(Path(path).read_bytes())


def _result(plan, *, status, local="SAVED", global_status="PENDING", reusable=False,
            refresh="PENDING", error=""):
    reviewed = next(member for member in plan["group"]["members"]
                    if member["occurrence_id"] == plan["checked_occurrence_ids"][0])
    return {"operation_id": plan["operation_id"], "status": status, "local_status": local,
        "global_status": global_status, "reusable": reusable, "refresh_status": refresh,
        "glyph_ids": sorted({item["payload"]["evidence"]["glyph_id"] for item in plan["intents"]}),
        "group_id": plan["group"]["group_id"], "review_id": reviewed["review_id"],
        "reading": plan["reading"], "checked_occurrence_ids": plan["checked_occurrence_ids"], "error": error}


def list_learning_confirmations(output_dir):
    from standalone_proofread import project_actual_evidence_root
    root = project_actual_evidence_root(Path(output_dir)) / DIRECTORY
    if not root.exists():
        return []
    results, current_records = [], {}
    for path in sorted(root.glob("*.json")):
        if path.name.endswith(".receipt.json"):
            continue
        decision = _read(path)
        if set(decision) != {"plan", "confirmed_at", "explicit_action"} or decision["explicit_action"] is not True:
            raise ValueError("LEARNING_CONTRACT: invalid durable user decision")
        plan = _validate_plan(decision["plan"])
        if str(Path(output_dir).resolve()) != plan["output_dir"] or path.stem != plan["operation_id"]:
            raise ValueError("LEARNING_CONTRACT: decision belongs to another project")
        receipt = path.with_name(path.stem + ".receipt.json")
        if receipt.exists():
            value = _read(receipt)
            if value.get("operation_id") != plan["operation_id"]:
                raise ValueError("LEARNING_CONTRACT: foreign delivery receipt")
        else:
            observed = _local_observed(plan, project_actual_evidence_root(Path(output_dir)))
            value = _result(plan, status="REFRESH_PENDING" if observed else "LOCAL_PENDING",
                local="SAVED" if observed else "UNKNOWN", refresh="PENDING")
        repository_path = plan["repository_path"]
        if repository_path not in current_records:
            current_records[repository_path] = {row.glyph_id: row for row in
                library.GlobalExactGlyphRepository(Path(repository_path)).load_inspection_snapshot().records}
        observed = [current_records[repository_path].get(gid) for gid in value.get("glyph_ids", [])]
        currently_reusable = bool(observed) and all(row is not None and row.global_reuse_allowed
            and row.stored_active_reading == plan["reading"] for row in observed)
        value = {**value, "current_reusable": currently_reusable}
        if any(row is not None and row.stored_state == library.QUARANTINED_CONFLICT for row in observed):
            value.update(reusable=False, status="CONFLICT_SAVED", global_status="QUARANTINED_CONFLICT")
        elif currently_reusable and value.get("global_status") == "STALE_APPROVAL_REQUIRES_NEW_ACTION":
            # Preserve the stale request's history; a newer valid action can
            # make current truth reusable without retroactively approving it.
            value.update(status="SUPERSEDED_BY_CURRENT_TRUTH", reusable=False)
        elif not currently_reusable:
            value["reusable"] = False
        results.append(value)
    return results


def confirm_learning(plan, *, repository=None, explicit_action=False):
    """Persist this user's fixed combined action, then attempt ordinary delivery."""
    if explicit_action is not True:
        raise ValueError("LEARNING_ACTION: an actual final user click is required")
    plan = _validate_plan(copy.deepcopy(plan))
    repository = _repository(repository)
    if str(repository.path) != plan["repository_path"]:
        raise ValueError("LEARNING_CONTRACT: repository changed")
    admission = _save_admission(plan, repository)
    _check_images(plan)
    from standalone_proofread import project_actual_evidence_root
    output = Path(plan["output_dir"])
    path, receipt = _paths(output, plan["operation_id"])
    with promotion.project_delivery_lock(project_actual_evidence_root(output)):
        admission.recheck()
        if path.exists():
            old = _read(path)
            if old.get("plan") != plan or old.get("explicit_action") is not True:
                raise ValueError("LEARNING_CONTRACT: operation ID already has different decision")
        else:
            _assert_hashes(plan["input_hashes"])
            _reprove(plan)
            path.parent.mkdir(parents=True, exist_ok=True)
            _write(path, {"plan": plan, "confirmed_at": datetime.now(timezone.utc).isoformat(),
                "explicit_action": True})
        return _resume(plan, repository, receipt)


def retry_learning_confirmation(output_dir, operation_id, *, repository=None):
    path, receipt = _paths(output_dir, operation_id)
    decision = _read(path)
    if set(decision) != {"plan", "confirmed_at", "explicit_action"} or decision["explicit_action"] is not True:
        raise ValueError("LEARNING_CONTRACT: no durable explicit action to retry")
    plan = _validate_plan(decision["plan"])
    if plan["output_dir"] != str(Path(output_dir).resolve()):
        raise ValueError("LEARNING_CONTRACT: different project")
    repository = _repository(repository)
    if str(repository.path) != plan["repository_path"]:
        raise ValueError("LEARNING_CONTRACT: repository changed")
    admission = _save_admission(plan, repository)
    from standalone_proofread import project_actual_evidence_root
    with promotion.project_delivery_lock(project_actual_evidence_root(Path(output_dir))):
        admission.recheck()
        _check_images(plan)
        return _resume(plan, repository, receipt)


def _reprove(plan):
    for member in plan["group"]["members"]:
        if member["occurrence_id"] in plan["checked_occurrence_ids"]:
            actual.require_actual_visual_target(member, Path(plan["output_dir"]))
    intents, admissions = promotion.direct_visual_intents(plan["group"], plan["reading"],
        plan["checked_occurrence_ids"], plan["source_context"])
    if intents != plan["intents"] or admissions != plan["admissions"]:
        raise ValueError("LEARNING_STALE: direct source/eligibility changed")


def _local_observed(plan, root):
    # The original direct intent must be in the durable local outbox and the
    # occurrence-local reading must still exist. A Global row alone proves
    # neither this project save nor a user click in this project.
    journal = Path(root) / promotion.PROJECT_TRANSACTION_FILE
    if journal.exists() and promotion._validated_project_journal(journal.read_bytes())["state"] != "COMMITTED":
        return False
    outbox = promotion.load_promotion_outbox(root)
    items = {item["intent_id"]: item for item in outbox["items"]}
    if any(item["intent_id"] not in items or
           items[item["intent_id"]]["payload_digest"] != item["payload_digest"] for item in plan["intents"]):
        return False
    return all(actual.has_direct_occurrence_actual(root, member, plan["reading"])
               for member in plan["group"]["members"]
               if member["occurrence_id"] in plan["checked_occurrence_ids"])


def _resume(plan, repository, receipt):
    import standalone_proofread as sp
    from project_write_admission import project_write_entry
    # Apply through the same profile-aware admission used by the existing GUI
    # actual writer, retaining its exact transaction/recovery contract.
    @project_write_entry(recovery=True, locked=True, profile_only=True)
    def run(output_dir):
        root = sp.project_actual_evidence_root(output_dir)
        _reprove(plan)
        promotion.recover_pending_project_actual_write(root)
        observed = _local_observed(plan, root)
        transaction = None
        if not observed:
            _assert_hashes(plan["input_hashes"])
            transaction = actual.apply_direct_visual_actual_batch(root,
                [{"group": plan["group"], "reading": plan["reading"],
                  "checked_occurrence_ids": [member["occurrence_id"]],
                  "source": "日常字形學習：原頁目視確認並供合格跨教材使用",
                  "note": "fixed learning operation " + plan["operation_id"]}
                 for member in plan["group"]["members"] if member["occurrence_id"] in plan["checked_occurrence_ids"]],
                source_context=plan["source_context"],
                initialize_evidence=lambda: sp.initialize_project_actual_evidence(output_dir))
        manifest = sp.json_load_strict(output_dir / "校對工作階段.json")
        delivery = promotion.deliver_pending_promotion_outbox(root, repository,
            source_context=sp._global_direct_source_context(output_dir, manifest), source_entries=manifest["records"])
        global_status, reusable, error = delivery["status"], False, delivery.get("error", "")
        request = _approval_request(plan["approval"])
        if global_status in {"DELIVERED", "NO_PENDING"} and request is not None:
            try:
                _check_images(plan)
                _reprove(plan)
                outcome = repository.lookup_approval_outcome(request)
                if outcome.status == "NOT_OBSERVED":
                    library.approve_global_exact_glyph(repository, **request.service_arguments())
                    outcome = repository.lookup_approval_outcome(request)
                global_status, reusable = outcome.status, outcome.current_reuse_allowed
                if outcome.current_state == library.QUARANTINED_CONFLICT:
                    global_status = "QUARANTINED_CONFLICT"
            except library.GlobalLibraryValidationError as exc:
                global_status, error = "STALE_APPROVAL_REQUIRES_NEW_ACTION", str(exc)
            except Exception as exc:
                global_status, error = "APPROVAL_PENDING_RETRY", str(exc)
        elif global_status in {"DELIVERED", "NO_PENDING"}:
            ids = {item["payload"]["evidence"]["glyph_id"] for item in plan["intents"]}
            records = [row for row in repository.load_inspection_snapshot().records if row.glyph_id in ids]
            reusable = bool(records) and all(row.global_reuse_allowed for row in records)
            global_status = ("QUARANTINED_CONFLICT" if any(row.stored_state == library.QUARANTINED_CONFLICT for row in records)
                             else "REUSABLE" if reusable else "WAITING_INDEPENDENT_SOURCE" if ids else "LOCAL_ONLY")
        # Existing refresh/recovery preserves independent manual expected events.
        refresh = "SUCCESS"
        try:
            if transaction is not None:
                manifest = sp.json_load_strict(output_dir / "校對工作階段.json")
                db = sp.json_load_strict(output_dir / "人工判定資料庫.json")
                ledger = sp.materialize_ledger(manifest, db)
                sp._finish_direct_actual_commit(output_dir, ledger, transaction)
            else:
                sp.recover_committed_actual_project(output_dir)
            from glyph_learning_impact import refresh_stale_project_dependencies
            refresh_stale_project_dependencies(output_dir, repository=repository)
        except Exception as exc:
            refresh, error = "PENDING", str(exc)
        status = ("REFRESH_PENDING" if refresh != "SUCCESS" else "REUSABLE" if reusable else
                  "LOCAL_SAVED_WAITING_SOURCE" if global_status in {"WAITING_INDEPENDENT_SOURCE", "LOCAL_ONLY"} else
                  "CONFLICT_SAVED" if global_status == "QUARANTINED_CONFLICT" else "GLOBAL_PENDING")
        result = _result(plan, status=status, global_status=global_status, reusable=reusable, refresh=refresh, error=error)
        _write(receipt, result)
        return result
    try:
        return run(Path(plan["output_dir"]))
    except Exception as exc:
        root = sp.project_actual_evidence_root(Path(plan["output_dir"]))
        if not _local_observed(plan, root):
            raise
        result = _result(plan, status="REFRESH_PENDING", global_status="UNKNOWN_PENDING_RECHECK",
            refresh="PENDING", error=str(exc))
        _write(receipt, result)
        return result
