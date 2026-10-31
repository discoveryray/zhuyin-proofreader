"""Finite review jobs in one isolated process; no arbitrary callable remoting.

The child retains the existing service's verified cache and evidence anchor.
Its wire snapshots are display data, never substitutes for write-boundary
validation. Only read-only validation can be cancelled; a writer is never killed.
"""
from __future__ import annotations

import copy
import base64
import atexit
import hashlib
import json
import math
import multiprocessing
import os
import struct
import threading
import zlib
import uuid
from pathlib import Path, WindowsPath, PosixPath


class ReviewOperationError(RuntimeError):
    def __init__(self, message, *, commit_state="UNKNOWN"):
        super().__init__(message)
        self.commit_state = commit_state


def _snapshot_key(snapshot):
    return hashlib.sha256(json.dumps(sorted(snapshot.hashes.items())).encode()).hexdigest()


_PAYLOAD_FIELDS = {
    "startup": (set(), set()), "invalidate": (set(), set()),
    "project_status": (set(), set()),
    "resume": (set(), {"expected_manifest", "expected_db"}),
    "validate_staging": ({"snapshot_key"}, {"snapshot_key"}),
    "save": ({"review_id", "event"}, {"review_id", "event", "expected_manifest", "expected_db",
             "defer_staging_validation", "confirm_current_actual_snapshot"}),
    "prepare_actual": ({"review_id", "expected_manifest", "expected_db"}, {"review_id", "expected_manifest", "expected_db"}),
    "load_learning": ({"expected_manifest", "expected_db"}, {"expected_manifest", "expected_db"}),
    "list_learning_recovery": (set(), set()),
    "prepare_learning_target": ({"group_id", "expected_manifest", "expected_db"},
                                {"group_id", "review_id", "expected_manifest", "expected_db"}),
    "prepare_learning": ({"group_id", "reading", "checked_occurrence_ids", "images", "expected_manifest", "expected_db"},
                         {"group_id", "reading", "checked_occurrence_ids", "images", "expected_manifest", "expected_db"}),
    "confirm_learning": ({"plan_token"}, {"plan_token"}),
    "discard_learning": (set(), set()),
    "retry_learning": ({"operation_id"}, {"operation_id"}),
    "save_learning_surface": ({"review_id", "reading", "proof", "actor", "whole_unit_readable", "target_character_matches",
                               "reading_from_original", "enable_format", "expected_manifest", "expected_db"},
                              {"review_id", "reading", "proof", "actor", "whole_unit_readable", "target_character_matches",
                               "reading_from_original", "enable_format", "expected_manifest", "expected_db"}),
    "stage_actual": ({"review_id", "reading", "checked_occurrence_ids", "expected_manifest", "expected_db"},
                     {"review_id", "reading", "checked_occurrence_ids", "note", "expected_manifest", "expected_db"}),
    "stage_surface": ({"review_id", "reading", "proof", "actor", "whole_unit_readable", "target_character_matches",
                       "reading_from_original", "enable_format", "expected_manifest", "expected_db"},
                      {"review_id", "reading", "proof", "actor", "whole_unit_readable", "target_character_matches",
                       "reading_from_original", "enable_format", "expected_manifest", "expected_db"}),
    "actual_summary": (set(), {"expected_manifest", "expected_db", "recovery_only"}),
    "apply_actual": (set(), {"expected_manifest", "expected_db", "recovery_only", "expected_recovery"}),
    "report": ({"expected_manifest", "expected_db"}, {"expected_manifest", "expected_db"}),
    "reload": ({"expected_manifest", "expected_db"}, {"expected_manifest", "expected_db"}),
    "preview": ({"entry", "padding", "output_dir", "display_width"}, {"entry", "padding", "output_dir", "display_width"}),
}


def validate_payload(operation, payload):
    if operation not in _PAYLOAD_FIELDS or type(payload) is not dict:
        raise ValueError("unknown review operation/payload")
    required, allowed = _PAYLOAD_FIELDS[operation]
    if not required <= payload.keys() or not payload.keys() <= allowed:
        raise ValueError("review payload fields do not match operation")
    def pure(value):
        if value is None or type(value) in (str, bool, int, float):
            return
        if type(value) in (list, tuple):
            for item in value:
                pure(item)
            return
        if type(value) is dict and all(type(key) is str for key in value):
            for item in value.values():
                pure(item)
            return
        raise ValueError("review payload must contain only plain data")
    pure(payload)
    for key in ("review_id", "reading", "note", "snapshot_key", "group_id", "plan_token", "operation_id"):
        if key in payload and type(payload[key]) is not str:
            raise ValueError("review payload string field invalid")
    for key in ("expected_manifest", "expected_db"):
        if key in payload and type(payload[key]) is not dict:
            raise ValueError("review displayed project must be an object")
    for key in ("recovery_only", "defer_staging_validation", "whole_unit_readable", "target_character_matches",
                "reading_from_original", "enable_format"):
        if key in payload and type(payload[key]) is not bool:
            raise ValueError("review payload boolean field invalid")
    if operation == "apply_actual":
        identity = payload.get("expected_recovery")
        if payload.get("recovery_only"):
            if (type(identity) not in (tuple, list) or len(identity) != 2
                    or type(identity[0]) is not str or type(identity[1]) is not dict):
                raise ValueError("actual recovery requires the confirmed durable identity")
            from global_glyph_promotion import validate_post_commit_recovery_plan
            from global_exact_glyph_library import _sha256_text, GlobalLibraryValidationError
            try:
                _sha256_text(identity[0], "project transaction ID")
                validate_post_commit_recovery_plan(identity[1])
            except (ValueError, GlobalLibraryValidationError) as exc:
                raise ValueError("actual recovery requires the confirmed durable identity") from exc
        elif "expected_recovery" in payload:
            raise ValueError("recovery identity is only valid for recovery-only apply")
    if "checked_occurrence_ids" in payload and (type(payload["checked_occurrence_ids"]) is not list
            or any(type(value) is not str for value in payload["checked_occurrence_ids"])):
        raise ValueError("checked occurrence payload invalid")


class ReviewOperationState:
    """One project, one child, one serial service; the opcode list is closed."""
    def __init__(self, output_dir, *, service=None):
        from review_save_service import ReviewSaveService
        self.output_dir = Path(output_dir).resolve()
        self.service = service or ReviewSaveService(self.output_dir)
        self.snapshots = {}
        self.learning_plans = {}
        from glyph_learning_images import PreparedLearningImages
        self.prepared_learning_images = PreparedLearningImages()

    def remember(self, snapshot):
        self.snapshots[_snapshot_key(snapshot)] = snapshot
        while len(self.snapshots) > 2:
            self.snapshots.pop(next(iter(self.snapshots)))
        return snapshot

    def dispatch(self, operation, payload, progress, cancelled):
        validate_payload(operation, payload)
        import standalone_proofread as sp
        from review_save_service import ReviewSaveService
        if operation == "project_status":
            from standalone_gui import project_status_details
            return project_status_details(self.output_dir)
        if operation == "startup":
            from review_gui import load_review_project
            value = load_review_project(self.output_dir, progress, service=self.service)
            self.service = value.pop("service")
            self.remember(value["snapshot"])
            return value
        if operation == "resume":
            return self.remember(self.service.load_resume_snapshot(**payload))
        if operation == "validate_staging":
            snapshot = self.snapshots.get(payload["snapshot_key"])
            if snapshot is None:
                raise ValueError("actual 暫存快照已過期；請重新載入")
            return self.service.validate_resume_staging(snapshot, cancelled=cancelled)
        if operation == "save":
            result = self.service.save_event(**payload, progress=progress)
            if result.resume_snapshot is not None:
                self.remember(result.resume_snapshot)
            return result
        if operation == "invalidate":
            self.service = self.service.with_invalidated_cache()
            self.snapshots.clear()
            self.learning_plans.clear()
            self.prepared_learning_images.clear()
            return None
        if operation == "discard_learning":
            self.learning_plans.clear()
            self.prepared_learning_images.clear()
            return None
        if operation == "prepare_learning":
            self.learning_plans.clear()
            self.prepared_learning_images.clear()
        if operation in {"load_learning", "list_learning_recovery", "prepare_learning_target", "prepare_learning", "confirm_learning", "retry_learning"}:
            try:
                return self._learning(operation, payload, progress)
            except Exception:
                if operation == "prepare_learning":
                    self.learning_plans.clear()
                    self.prepared_learning_images.clear()
                raise
        if operation == "save_learning_surface":
            return self._surface_learning(payload, progress)
        if operation in {"reload", "actual_summary", "prepare_actual", "stage_actual", "stage_surface", "report", "apply_actual"}:
            expected = {key: payload[key] for key in ("expected_manifest", "expected_db")
                        if key in payload}
            if operation == 'stage_surface':
                # The service's resume lock is a write boundary too. Reject
                # bad first input/proof/consent before entering that service.
                import manual_surface_actual as surface
                prepared=sp.prepare_surface_actual_correction(self.output_dir,payload['review_id'])
                if prepared['proof'] != payload['proof']:
                    raise ValueError('SURFACE_CONTRACT: preview proof differs before service admission')
                if prepared['requires_format_conversion'] and payload['enable_format'] is not True:
                    raise ValueError('SURFACE_CONTRACT: explicit format conversion required')
                surface.record_for(payload['proof'],payload['reading'],actor=payload['actor'],
                    **{key:payload[key] for key in ('whole_unit_readable','target_character_matches','reading_from_original')})
            # Caller dictionaries only describe what was displayed. The existing
            # service compares them with fully verified durable bytes.
            if not payload.get("recovery_only"):
                try:
                    snapshot = self.service.load_resume_snapshot(**expected)
                except Exception as exc:
                    raise ReviewOperationError(str(exc), commit_state="NOT_WRITTEN") from exc
            if operation == "reload":
                summary = self.service.validate_resume_staging(snapshot)
                return dict(snapshot=self.remember(snapshot), summary=summary,
                            review_view=self.service._conflicts.review_view(snapshot.db, snapshot.ledger))
            if operation == "actual_summary":
                if payload.get("recovery_only"):
                    from global_glyph_promotion import committed_project_recovery
                    try:
                        identity = committed_project_recovery(sp.project_actual_evidence_root(self.output_dir))
                    except Exception as exc:
                        return dict(actual_recovery_pending=False, staging_error=f"actual 復原紀錄無法驗證：{exc}")
                    return dict(actual_recovery_pending=identity is not None,
                                actual_recovery_identity=identity)
                return self.service.validate_resume_staging(snapshot)
            if operation == "prepare_actual":
                from actual_review import build_actual_group_for_entry
                rows = {row["review_id"]: row for row in snapshot.ledger}
                entry = rows[payload["review_id"]]
                group = build_actual_group_for_entry(snapshot.ledger, entry)
                summary = self.service.validate_resume_staging(snapshot)
                view = self.service._conflicts.review_view(snapshot.db, snapshot.ledger)
                from actual_review import actual_visual_source_safety
                safety = actual_visual_source_safety(entry, self.output_dir)
                surface = None
                if not safety['location_readable'] or entry.get('manual_surface_actual') is not None:
                    # Exact native eligibility is re-proven; an unsafe/unknown
                    # slot is not made eligible by the presence of pixels.
                    surface = sp.prepare_surface_actual_correction(self.output_dir, payload['review_id'])
                from glyph_learning_service import collect_learning_candidates
                learning = collect_learning_candidates(snapshot.ledger,
                    sp._global_direct_source_context(self.output_dir, snapshot.manifest),
                    evidence_root=sp.project_actual_evidence_root(self.output_dir))
                learning_group = next((candidate for candidate in learning["groups"]
                    if any(member.get("occurrence_id") == entry.get("occurrence_id")
                           for member in candidate.get("candidate_members", candidate["members"]))), None)
                return dict(group=group, summary=summary, ledger=view, surface=surface,
                            learning_group=learning_group)
            if operation == "stage_actual":
                progress("正在保存 actual 暫存")
                sp.stage_manual_actual_correction(self.output_dir, payload["review_id"],
                    payload["reading"], payload["checked_occurrence_ids"], payload.get("note", ""))
            elif operation == 'stage_surface':
                progress('正在保存單一位置人工表面 actual')
                result = sp.stage_surface_actual_correction(self.output_dir,
                    **{key:value for key,value in payload.items() if key not in ('expected_manifest','expected_db')})
            elif operation == "report":
                progress("正在更新 Excel 報告")
                result = sp.regenerate_report(self.output_dir)
            else:
                progress("正在套用 actual 與更新受影響 PDF")
                if payload.get("recovery_only"):
                    result = sp.recover_committed_actual_project(self.output_dir,
                        expected_recovery=payload["expected_recovery"])
                    if result is None:
                        raise ValueError("已提交 actual 復原紀錄已變更；未套用新暫存")
                else:
                    result = sp.apply_staged_manual_actual_corrections(self.output_dir)
            # A successful actual apply/report can establish a new sealed
            # session. No pre-mutation actual snapshot is used for the result.
            try:
                if operation not in {"stage_actual", "stage_surface"}:
                    self.service = ReviewSaveService(self.output_dir, evidence_anchor=self.service.evidence_anchor)
                else:
                    self.service = self.service.with_invalidated_cache()
                self.snapshots.clear()
                snapshot = self.remember(self.service.load_resume_snapshot())
                summary = self.service.validate_resume_staging(snapshot)
                view = self.service._conflicts.review_view(snapshot.db, snapshot.ledger)
            except Exception as exc:
                status = {"stage_actual": "STAGED_REFRESH_PENDING", "stage_surface": "STAGED_REFRESH_PENDING", "report": "REPORT_SAVED_REFRESH_PENDING",
                          "apply_actual": "COMMITTED_REFRESH_PENDING"}[operation]
                raise ReviewOperationError(str(exc), commit_state=status) from exc
            return dict(result=str(result) if operation == "report" else result if operation == "apply_actual" else None,
                        snapshot=snapshot, summary=summary,
                        review_view=view)
        raise ValueError("unknown review operation")

    def _learning(self, operation, payload, progress):
        """Keep plans in this serial worker; the GUI can only commit its token."""
        import glyph_learning_service as learning
        import standalone_proofread as sp
        from review_save_service import ReviewSaveService
        if operation == "list_learning_recovery":
            # Durable fixed decisions must remain reachable even when a
            # committed journal correctly prevents accepting a resume view.
            return learning.list_learning_confirmations(self.output_dir)
        if operation in {"confirm_learning", "retry_learning"}:
            if operation == "confirm_learning":
                plan = self.learning_plans.get(payload["plan_token"])
                if plan is None:
                    raise ReviewOperationError("確認計畫已失效；請重新看圖並準備保存。", commit_state="NOT_WRITTEN")
                progress("保存已固定的目視確認與核准")
                result = learning.confirm_learning(plan, explicit_action=True,
                    prepared_images=self.prepared_learning_images)
                self.learning_plans.clear()
                self.prepared_learning_images.clear()
            else:
                progress("接續原本固定的確認，不增加確認次數")
                result = learning.retry_learning_confirmation(self.output_dir, payload["operation_id"])
            value = dict(result=result)
            if result.get("global_status") == "QUARANTINED_CONFLICT":
                from glyph_learning_impact import known_impacts
                try:
                    value["impacts"] = known_impacts(result.get("glyph_ids", []))
                except Exception as exc:
                    value["impacts"] = dict(projects=[], notice="已知影響回查未完成：" + str(exc))
            if result.get("local_status") == "SAVED":
                try:
                    self.service = ReviewSaveService(self.output_dir, evidence_anchor=self.service.evidence_anchor)
                    self.snapshots.clear()
                    snapshot = self.remember(self.service.load_resume_snapshot())
                    value.update(snapshot=snapshot, summary=self.service.validate_resume_staging(snapshot),
                                 review_view=self.service._conflicts.review_view(snapshot.db, snapshot.ledger))
                except Exception as exc:
                    value["reload_error"] = str(exc)
            return value
        if operation == "prepare_learning_target" and (type(payload.get("group_id")) is not str
                                                       or not payload["group_id"].strip()):
            raise ValueError("學習組 ID 不合法；請重新載入清單")
        snapshot = self.service.load_resume_snapshot(**{key: payload[key] for key in ("expected_manifest", "expected_db")})
        if operation == "prepare_learning":
            # A replacement preparation retires the prior UI decision even if
            # its new image/input validation fails below.
            self.learning_plans.clear()
            from glyph_learning_images import LearningImageStore
            store = LearningImageStore()
            rows = {row["occurrence_id"]: row for row in snapshot.ledger}
            images = payload["images"]
            if (type(images) is not list or len(images) != len(payload["checked_occurrence_ids"])
                    or {image.get("occurrence_id") for image in images if type(image) is dict}
                    != set(payload["checked_occurrence_ids"])):
                raise ValueError("確認圖片與實際勾選樣本不符")
            evidence = []
            for image in images:
                if type(image) is not dict or set(image) != {"occurrence_id", "review_id", "source_sha256", "png_base64",
                                                           "padding", "display_width"}:
                    raise ValueError("確認圖片欄位不完整")
                row = rows[image["occurrence_id"]]
                if image["review_id"] != row["review_id"] or image["source_sha256"] != row["pdf_sha256"]:
                    raise ValueError("確認圖片不屬於目前來源與位置")
                if (type(image["padding"]) not in (list, tuple) or list(image["padding"]) != [95, 60]
                        or any(type(item) is not int for item in image["padding"])):
                    raise ValueError("確認圖片裁切參數不符合學習原頁規格")
                width = image["display_width"]
                if width is not None and (type(width) is not int or width <= 0):
                    raise ValueError("確認圖片寬度不合法")
                raw = base64.b64decode(image["png_base64"], validate=True)
                # Re-render the sealed, worker-owned occurrence at the exact
                # displayed geometry. Labels supplied next to arbitrary PNG
                # bytes cannot authorize direct visual evidence.
                verified = preview_png(row, padding=(95, 60), output_dir=self.output_dir, display_width=width)
                if (verified["target"] is None or verified.get("warning_target") is not None
                        or verified["png"] != raw or verified["source_sha256"] != row["pdf_sha256"]
                        or verified["occurrence_id"] != row["occurrence_id"]
                        or verified["review_id"] != row["review_id"]):
                    raise ValueError("確認圖片像素不屬於已核實的原頁位置；請重新載入並看圖")
                evidence.append(self.prepared_learning_images.add(store, image["occurrence_id"], raw))
            plan = learning.prepare_learning_confirmation(self.output_dir, snapshot,
                payload["group_id"], payload["reading"], payload["checked_occurrence_ids"],
                image_evidence=evidence, image_store_root=str(store.root),
                prepared_images=self.prepared_learning_images)
            token = uuid.uuid4().hex
            self.learning_plans.clear()
            self.learning_plans[token] = plan
            return dict(plan_token=token, message="讀音、來源與證據集合已固定。請按「並記住」保存本次確認；"
                        "符合全部獨立性條件時，同次啟用跨教材重用。")
        collected = learning.collect_learning_candidates(snapshot.ledger,
            sp._global_direct_source_context(self.output_dir, snapshot.manifest),
            evidence_root=sp.project_actual_evidence_root(self.output_dir),
            **({"target_group_id": payload["group_id"]} if operation == "prepare_learning_target" else {}))
        if operation == "load_learning":
            pending = learning.list_learning_confirmations(self.output_dir)
            return dict(snapshot=self.remember(snapshot), candidates=collected, pending=pending)
        group = next((item for item in collected["groups"] if item["group_id"] == payload["group_id"]), None)
        if group is None:
            raise ValueError("學習候選已變動；請重新載入清單")
        members = group.get("candidate_members") or group["members"]
        entry = next((row for row in members if row["review_id"] == payload.get("review_id")), members[0])
        from actual_review import actual_visual_source_safety
        safety = actual_visual_source_safety(entry, self.output_dir)
        surface = None
        if not safety["location_readable"] or entry.get("manual_surface_actual") is not None:
            surface = sp.prepare_surface_actual_correction(self.output_dir, entry["review_id"])
        return dict(group=group, entry=entry, surface=surface)

    def _surface_learning(self, payload, progress):
        """Apply only the viewed surface occurrence, through the existing path."""
        import standalone_proofread as sp
        import manual_surface_actual as surface
        from global_glyph_promotion import project_delivery_lock
        from project_write_admission import capture_project_write_admission
        from glyph_learning_images import LearningImageStore
        from review_save_service import ReviewSaveService
        expected = {key: payload[key] for key in ("expected_manifest", "expected_db")}
        prepared = sp.prepare_surface_actual_correction(self.output_dir, payload["review_id"])
        if prepared["proof"] != payload["proof"]:
            raise ReviewOperationError("原頁已變更；未保存", commit_state="NOT_WRITTEN")
        surface.record_for(payload["proof"], payload["reading"], actor=payload["actor"],
            **{key: payload[key] for key in ("whole_unit_readable", "target_character_matches", "reading_from_original")})
        if prepared["requires_format_conversion"] and not payload["enable_format"]:
            raise ReviewOperationError("未確認既有專案格式轉換", commit_state="NOT_WRITTEN")
        snapshot = self.service.load_resume_snapshot(**expected)
        root = sp.project_actual_evidence_root(self.output_dir)
        admission = capture_project_write_admission(self.output_dir)
        oid = payload["proof"]["source"]["occurrence_id"]
        with project_delivery_lock(root, _admission=admission):
            admission.recheck()
            document = surface.load_snapshot(root, snapshot.manifest).document
            if document is not None and any(row["proof"]["source"]["occurrence_id"] != oid for row in document["pending"]):
                raise ReviewOperationError("另有其他位置的原頁確認尚未套用；請先處理，未套用其他位置。", commit_state="NOT_WRITTEN")
            store = LearningImageStore()
            store.save_evidence(oid, base64.b64decode(payload["proof"]["render"]["target_png"], validate=True))
            progress("保存本位置原頁確認；不寫入全域字形讀音")
            staged = sp.stage_surface_actual_correction(self.output_dir,
                **{key: value for key, value in payload.items() if key not in ("expected_manifest", "expected_db")})
            if not staged.get("already_applied"):
                try:
                    sp.apply_staged_surface_actual_corrections(self.output_dir)
                except Exception as exc:
                    state = ("COMMITTED_REFRESH_PENDING" if isinstance(exc, sp.ManualActualPostApplyError)
                             or getattr(exc, "commit_state", "") == "COMMITTED_REFRESH_PENDING"
                             else "STAGED_REFRESH_PENDING")
                    raise ReviewOperationError(str(exc), commit_state=state) from exc
        self.service = ReviewSaveService(self.output_dir, evidence_anchor=self.service.evidence_anchor)
        self.snapshots.clear()
        try:
            snapshot = self.remember(self.service.load_resume_snapshot())
            return dict(result=dict(status="LOCAL_SAVED_WAITING_SOURCE", local_status="SAVED", global_status="LOCAL_ONLY"),
                        snapshot=snapshot, summary=self.service.validate_resume_staging(snapshot),
                        review_view=self.service._conflicts.review_view(snapshot.db, snapshot.ledger))
        except Exception as exc:
            return dict(result=dict(status="REFRESH_PENDING", local_status="SAVED", global_status="LOCAL_ONLY"),
                        reload_error=str(exc))


def _review_child(connection, output_dir, cancel_signal):
    state = ReviewOperationState(output_dir)
    try:
        while True:
            request = connection.recv()
            if request is None:
                return
            number, project, operation, payload = request
            if project != str(state.output_dir):
                connection.send((number, "error", "foreign project owner", "UNKNOWN", None))
                continue
            try:
                def notify(phase):
                    if operation in {"startup", "prepare_actual", "reload", "actual_summary"} and cancel_signal.is_set():
                        raise InterruptedError("review preparation cancelled")
                    connection.send((number, "progress", phase))
                value = state.dispatch(operation, payload,
                    notify,
                    cancel_signal.is_set)
                connection.send((number, "result", value, state.service.evidence_anchor, os.getpid()))
            except Exception as exc:
                import standalone_proofread as sp
                committed = ("COMMITTED_REFRESH_PENDING" if isinstance(exc, sp.ManualActualPostApplyError)
                             else getattr(exc, "commit_state", "UNKNOWN"))
                connection.send((number, "error", str(exc), committed, os.getpid()))
    except (EOFError, BrokenPipeError):
        # A vanished owner cannot revoke a transaction already in progress.
        return
    finally:
        connection.close()


class ReviewProcessService:
    """Finite service-shaped client. Supervisor calls contain no Tk or fitz."""
    def __init__(self, output_dir):
        self.output_dir = Path(output_dir).resolve()
        self.evidence_anchor = None
        self.worker_pid = None
        self._lock = threading.Lock()
        self._serial = 0
        self._process = None
        self._closed = False
        self._invalidate_pending = False
        self._context = multiprocessing.get_context("spawn")
        self._cancel_signal = self._context.Event()

    def _start(self):
        if self._process is None:
            self._connection, child = self._context.Pipe()
            self._process = self._context.Process(target=_review_child,
                args=(child, str(self.output_dir), self._cancel_signal), daemon=False)
            try:
                self._process.start()
                # Registered after multiprocessing's join handler: an idle
                # owner cannot leave its child waiting for a command at exit.
                atexit.register(self.close)
            except Exception:
                self._connection.close()
                self._process = None
                raise
            finally:
                child.close()

    def invoke(self, operation, payload, *, progress=None, cancelled=None):
        validate_payload(operation, payload)
        if self._invalidate_pending and operation != "invalidate":
            self._invalidate_pending = False
            self.invoke("invalidate", {})
        with self._lock:
            if self._closed:
                raise ReviewOperationError("review owner closed")
            self._start()
            self._serial += 1
            number = self._serial
            self._cancel_signal.clear()
            self._connection.send((number, str(self.output_dir), operation, copy.deepcopy(payload)))
            progress_error = None
            while True:
                if operation == "validate_staging" and cancelled is not None and cancelled():
                    self._cancel_signal.set()
                if not self._connection.poll(.05):
                    if not self._process.is_alive():
                        raise ReviewOperationError("review child ended before a verified result")
                    continue
                response = self._connection.recv()
                if response[0] != number:
                    raise ReviewOperationError("review result owner mismatch")
                if response[1] == "progress":
                    if progress is not None:
                        try:
                            progress(response[2])
                        except Exception as exc:
                            # Drain the terminal reply before closing. A child
                            # must not block sending a large snapshot to a caller
                            # that abandoned the pipe. Writer progress is advisory.
                            if operation in {"startup", "prepare_actual", "reload", "actual_summary"}:
                                progress_error = exc
                                self._cancel_signal.set()
                    continue
                self.worker_pid = response[4]
                if progress_error is not None:
                    raise progress_error
                if response[1] == "error":
                    raise ReviewOperationError(response[2], commit_state=response[3])
                self.evidence_anchor = response[3]
                return response[2]

    def load_resume_snapshot(self, **kwargs):
        return self.invoke("resume", kwargs)

    def validate_resume_staging(self, snapshot, *, cancelled=None):
        return self.invoke("validate_staging", {"snapshot_key": _snapshot_key(snapshot)}, cancelled=cancelled)

    def save_event(self, review_id, event, *, progress=None, **kwargs):
        return self.invoke("save", dict(review_id=review_id, event=event, **kwargs), progress=progress)

    def with_invalidated_cache(self):
        # Same child retains the anchor. Invalidation is executed in its serial
        # command stream on the supervisor, never as heavy Tk work.
        self._invalidate_pending = True
        return self

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            atexit.unregister(self.close)
            if self._process is not None:
                try:
                    if self._process.is_alive():
                        self._connection.send(None)
                except (EOFError, BrokenPipeError, OSError):
                    pass
                finally:
                    self._process.join()
                    self._connection.close()


def preview_png(entry, *, padding, output_dir, display_width):
    """Called only by the isolated raster child, with the real preview service."""
    from review_display import occurrence_preview
    from actual_review import _resolve_pdf_path
    from actual_source_visual_safety import source_visual_operation
    path = _resolve_pdf_path(entry, output_dir)
    with source_visual_operation() as operation:
        before = operation.read_source(path, entry.get("pdf_sha256"))
        digest = entry["pdf_sha256"]
        details = occurrence_preview(entry, padding=padding,
            output_dir=output_dir, display_width=display_width, return_details=True)
        pixmap = details["pixmap"]
        # The pixels come from immutable, SHA-proved bytes. Check revision at
        # publication; writers independently re-read the complete source seal.
        if operation.read_source(path, digest) != before:
            raise ValueError("预覽期間來源 PDF 已變更")
    return dict(png=pixmap.tobytes("png"), width=pixmap.width, height=pixmap.height,
                target=details["target"], warning_target=details["warning_target"],
                diagnostic=details["diagnostic"], notice=details["notice"],
                physical_page=entry.get("physical_page"), pdf_name=entry.get("pdf_name"),
                pid=os.getpid(), source_sha256=digest,
                review_id=entry.get("review_id"), occurrence_id=entry.get("occurrence_id"))


def _preview_child(connection, payload):
    from actual_source_visual_safety import SourceVisualOperation
    operation = owner = None
    try:
        while payload is not None:
            try:
                entry = payload["entry"]
                current_owner = (payload["output_dir"], entry.get("pdf_sha256"), entry.get("physical_page"))
                if current_owner != owner:
                    if operation is not None:
                        retired, operation, owner = operation, None, None
                        retired.__exit__(None, None, None)
                    fresh = SourceVisualOperation()
                    fresh.__enter__()
                    operation = fresh
                    owner = current_owner
                connection.send((preview_png(**payload), None))
            except Exception as exc:
                if operation is not None:
                    retired, operation, owner = operation, None, None
                    retired.__exit__(None, None, None)
                connection.send((None, str(exc)))
            payload = connection.recv()
    except (EOFError, BrokenPipeError):
        pass
    finally:
        if operation is not None:
            operation.__exit__(None, None, None)
        connection.close()


def validate_preview_png(value):
    # These display fields never substitute for the shared prewrite source
    # proof. Keep an unsafe/warning result from becoming a UI-ready target.
    diagnostic = value.get("diagnostic")
    if diagnostic is not None:
        if (type(diagnostic) is not dict or type(diagnostic.get("location_readable")) is not bool
                or type(diagnostic.get("donor_eligible")) is not bool):
            raise ValueError("預覽來源診斷不完整")
        if not diagnostic["location_readable"] and value.get("target") is not None:
            raise ValueError("不可確認的警示不能提供確認目標")
    if value.get("warning_target") is not None and value.get("target") is not None:
        raise ValueError("警示框與確認目標不能混用")
    for key in ("target", "warning_target"):
        region = value.get(key)
        if region is not None and (type(region) not in (tuple,list) or len(region) != 4
                or any(type(item) not in (int,float) or not math.isfinite(item) for item in region)
                or region[2] <= region[0] or region[3] <= region[1]):
            raise ValueError("預覽定位框無效")
    physical_page = value.get("physical_page")
    if physical_page is not None and (type(physical_page) not in (int,float)
            or not math.isfinite(physical_page) or int(physical_page) != physical_page or physical_page < 1):
        raise ValueError("預覽實體頁碼無效")
    raw = value["png"]
    if (not isinstance(raw, bytes) or raw[:8] != b"\x89PNG\r\n\x1a\n"
            or raw[12:16] != b"IHDR" or raw[8:12] != b"\x00\x00\x00\r" or len(raw) < 33):
        raise ValueError("預覽圖片不完整")
    offset, ended, data = 8, False, False
    while offset + 12 <= len(raw):
        length = struct.unpack(">I", raw[offset:offset + 4])[0]
        end = offset + 12 + length
        if end > len(raw):
            raise ValueError("預覽 PNG chunk 截斷")
        kind = raw[offset + 4:offset + 8]
        expected = struct.unpack(">I", raw[end - 4:end])[0]
        if zlib.crc32(raw[offset + 4:end - 4]) & 0xffffffff != expected:
            raise ValueError("預覽 PNG CRC 不符")
        data = data or kind == b"IDAT"
        if kind == b"IEND":
            ended = length == 0 and end == len(raw)
            break
        offset = end
    if not ended or not data:
        raise ValueError("預覽 PNG 缺少完整 IEND／影像資料")
    width, height = struct.unpack(">II", raw[16:24])
    from review_display import MAX_PREVIEW_DIMENSION, MAX_PREVIEW_PIXELS
    if (width != value["width"] or height != value["height"] or not width or not height
            or max(width, height) > MAX_PREVIEW_DIMENSION or width * height > MAX_PREVIEW_PIXELS):
        raise ValueError("預覽圖片尺寸不符")
    return value


class PreviewJob:
    def __init__(self, payload):
        payload = self._payload(payload)
        context = multiprocessing.get_context("spawn")
        self.connection, child = context.Pipe()
        self.process = context.Process(target=_preview_child, args=(child, payload), daemon=True)
        try:
            self.process.start()
        except Exception:
            self.connection.close()
            raise
        finally:
            child.close()
        self.active = True

    def submit(self, payload):
        if self.active:
            raise ValueError("preview already in flight")
        self.connection.send(self._payload(payload))
        self.active = True

    @staticmethod
    def _payload(payload):
        if type(payload) is not dict:
            raise ValueError("preview payload must be a plain object")
        payload = payload.copy()
        if type(payload.get("output_dir")) in {Path, WindowsPath, PosixPath}:
            payload["output_dir"] = str(payload["output_dir"])
        validate_payload("preview", payload)
        if type(payload["entry"]) is not dict:
            raise ValueError("preview entry must be plain data")
        return copy.deepcopy(payload)

    def poll(self):
        if self.connection.poll():
            value, error = self.connection.recv()
            self.active = False
            if error:
                raise ValueError(error)
            return validate_preview_png(value)
        if not self.process.is_alive():
            raise ValueError("預覽子程序未回傳完整結果")
        return None

    def cancel(self):
        # Preview is strictly read-only; official write jobs have no such API.
        if self.process.is_alive():
            self.process.terminate()
        process, connection = self.process, self.connection
        def cleanup():
            process.join()
            connection.close()
        self._cleanup = threading.Thread(target=cleanup, daemon=False)
        self._cleanup.start()
