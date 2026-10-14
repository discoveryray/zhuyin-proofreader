"""Finite review jobs in one isolated process; no arbitrary callable remoting.

The child retains the existing service's verified cache and evidence anchor.
Its wire snapshots are display data, never substitutes for write-boundary
validation. Only read-only validation can be cancelled; a writer is never killed.
"""
from __future__ import annotations

import copy
import atexit
import hashlib
import json
import multiprocessing
import os
import struct
import threading
import zlib
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
    "stage_actual": ({"review_id", "reading", "checked_occurrence_ids", "expected_manifest", "expected_db"},
                     {"review_id", "reading", "checked_occurrence_ids", "note", "expected_manifest", "expected_db"}),
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
    for key in ("review_id", "reading", "note", "snapshot_key"):
        if key in payload and type(payload[key]) is not str:
            raise ValueError("review payload string field invalid")
    for key in ("expected_manifest", "expected_db"):
        if key in payload and type(payload[key]) is not dict:
            raise ValueError("review displayed project must be an object")
    for key in ("recovery_only", "defer_staging_validation"):
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
            value.pop("service")
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
            return None
        if operation in {"reload", "actual_summary", "prepare_actual", "stage_actual", "report", "apply_actual"}:
            expected = {key: payload[key] for key in ("expected_manifest", "expected_db")
                        if key in payload}
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
                return dict(group=group, summary=summary, ledger=view)
            if operation == "stage_actual":
                progress("正在保存 actual 暫存")
                sp.stage_manual_actual_correction(self.output_dir, payload["review_id"],
                    payload["reading"], payload["checked_occurrence_ids"], payload.get("note", ""))
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
                if operation != "stage_actual":
                    self.service = ReviewSaveService(self.output_dir, evidence_anchor=self.service.evidence_anchor)
                else:
                    self.service = self.service.with_invalidated_cache()
                self.snapshots.clear()
                snapshot = self.remember(self.service.load_resume_snapshot())
                summary = self.service.validate_resume_staging(snapshot)
                view = self.service._conflicts.review_view(snapshot.db, snapshot.ledger)
            except Exception as exc:
                status = {"stage_actual": "STAGED_REFRESH_PENDING", "report": "REPORT_SAVED_REFRESH_PENDING",
                          "apply_actual": "COMMITTED_REFRESH_PENDING"}[operation]
                raise ReviewOperationError(str(exc), commit_state=status) from exc
            return dict(result=str(result) if operation == "report" else result if operation == "apply_actual" else None,
                        snapshot=snapshot, summary=summary,
                        review_view=view)
        raise ValueError("unknown review operation")


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
    path = _resolve_pdf_path(entry, output_dir)
    before = path.read_bytes()
    digest = hashlib.sha256(before).hexdigest()
    if digest != entry.get("pdf_sha256"):
        raise ValueError("預覽來源 PDF 與封印不符")
    pixmap, target, notice = occurrence_preview(entry, padding=padding,
        output_dir=output_dir, display_width=display_width)
    if path.read_bytes() != before:
        raise ValueError("预覽期間來源 PDF 已變更")
    return dict(png=pixmap.tobytes("png"), width=pixmap.width, height=pixmap.height,
                target=target, notice=notice, pid=os.getpid(), source_sha256=digest,
                review_id=entry.get("review_id"), occurrence_id=entry.get("occurrence_id"))


def _preview_child(connection, payload):
    try:
        while payload is not None:
            try:
                connection.send((preview_png(**payload), None))
            except Exception as exc:
                connection.send((None, str(exc)))
            payload = connection.recv()
    except (EOFError, BrokenPipeError):
        pass
    finally:
        connection.close()


def validate_preview_png(value):
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
