"""Read-only observation of one explicitly selected project's operational state.

This is not a completion gate, a recovery executor, or a source of glyph truth.
The project files and SQLite do not share an atomic transaction. We retain exact
inputs and compare bracketing observations, never timestamps or generations.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import json

import global_exact_glyph_library as library
import global_glyph_promotion as promotion


@dataclass(frozen=True)
class DeliveryItem:
    intent_id: str
    reading: str
    local_status: str
    global_status: str
    status: str
    explanation: str
    details: str


@dataclass(frozen=True)
class DeliveryRecoverySnapshot:
    project_output: str = ""
    actual_root: str = ""
    status: str = "NO_PROJECT"
    journal_status: str = "NOT_READ"
    outbox_status: str = "NOT_READ"
    global_status: str = "NOT_READ"
    database_path: str = ""
    transaction_id: str = ""
    recovery_plan: str = ""
    items: tuple[DeliveryItem, ...] = ()
    diagnostics: tuple[str, ...] = ()
    input_hashes: tuple[tuple[str, str], ...] = ()


class _Changed(Exception):
    pass


def _optional_bytes(path):
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _capture(root):
    # A journal sandwiches the outbox read, so even a partially published
    # transaction is bound to the exact PREPARED/COMMITTED identity observed.
    journal = _optional_bytes(root / promotion.PROJECT_TRANSACTION_FILE)
    outbox = _optional_bytes(root / promotion.GLOBAL_PROMOTION_OUTBOX_FILE)
    if journal != _optional_bytes(root / promotion.PROJECT_TRANSACTION_FILE):
        raise _Changed
    return journal, outbox


def _local(frozen):
    journal_raw, outbox_raw = frozen
    journal, outbox, diagnostics = None, None, []
    journal_status, outbox_status = "ABSENT", "ABSENT"
    if journal_raw is not None:
        try:
            journal = promotion._validated_project_journal(journal_raw)
            journal_status = journal["state"]
        except library.GlobalLibraryError as exc:
            journal_status = "INVALID"
            diagnostics.append("交易紀錄損壞或契約不相容，無法確認本機交易：" + str(exc))
    if outbox_raw is not None:
        try:
            outbox = promotion.validate_outbox(promotion._json(outbox_raw))
            outbox_status = "VALID"
        except library.GlobalLibraryError as exc:
            outbox_status = "INVALID"
            diagnostics.append("交付清單損壞或契約不相容，無法確認項目：" + str(exc))
    return journal, outbox, journal_status, outbox_status, diagnostics


def _global_read(repository, ids):
    try:
        return repository.load_processed_intent_snapshot(ids), "", ""
    except library.GlobalLibraryError as exc:
        return None, exc.status, str(exc)
    except Exception as exc:
        return None, "READ_FAILED", f"{type(exc).__name__}: {exc}"


def _receipt_dict(receipt):
    return {key: getattr(receipt, key) for key in (
        "intent_id", "payload_digest", "committed_generation", "result_state", "receipt_digest")}


def _items(outbox, journal_status, global_snapshot):
    receipts = {row.intent_id: row for row in global_snapshot.receipts} if global_snapshot else {}
    rows = []
    for item in (outbox or {}).get("items", []):
        observed = receipts.get(item["intent_id"])
        global_receipt = _receipt_dict(observed) if observed is not None else None
        global_status = "UNCONFIRMED"
        reason = "Global 尚未核實；PENDING 本身不足以證明 Global 尚未提交。"
        if global_snapshot is not None:
            if global_snapshot.store_status == library.ABSENT:
                global_status = "ABSENT"
                reason = "指定 Global 資料庫不存在，無法核實跨端狀態。"
            elif observed is None:
                global_status = "NOT_RECORDED"
                reason = "本次有效 Global 快照未找到此 intent receipt；不能據此判定歷史從未交付。"
            else:
                try:
                    promotion._receipt(global_receipt, item)
                    if item["receipt"] is not None and item["receipt"] != global_receipt:
                        raise library.GlobalLibraryValidationError("local / Global receipt mismatch")
                    global_status = observed.result_state
                    reason = ("本次已核實 Global 提交 receipt。" if observed.result_state == library.INTENT_COMMITTED
                              else "本次已核實 Global 無變更 receipt；不宣稱新增提交。")
                except library.GlobalLibraryError as exc:
                    global_status = "MISMATCH"
                    reason = "Global receipt 與本機 intent／receipt 不符，無法確認：" + str(exc)
        status = "DELIVERED" if item["status"] == "DELIVERED" else "PENDING"
        if item["status"] == "PENDING" and global_status == library.INTENT_COMMITTED:
            status = "AWAITING_ACK"
            reason += " project 尚未記錄交付 ack。"
        if item["status"] == "DELIVERED":
            reason = "本機已保存通過驗證的交付 receipt。" + reason
        if global_status == "MISMATCH":
            status = "UNCONFIRMED"
        if journal_status in {"PREPARED", "INVALID"}:
            status = "UNCONFIRMED"
            reason = "本機交易尚未提交或無法驗證；目前 outbox 可能包含部分寫入，不代表已提交。" + reason
        reason += " 交付不代表字形已核准或可重用。"
        rows.append(DeliveryItem(
            item["intent_id"], item["payload"]["evidence"]["reading"], item["status"], global_status,
            status, reason, json.dumps({"local_outbox_item": item,
                                      "global_observed_receipt": global_receipt}, ensure_ascii=False, indent=2)))
    return tuple(rows)


def inspect_delivery_recovery(project_output=None, *, repository=None, max_attempts=3):
    """Observe only selected project artifacts and one explicitly resolved store.

    Stable means the consulted inputs agreed across bounded observations, not
    that writers are excluded or that project proofreading/refresh is complete.
    Global failures preserve independently validated, stable local facts.
    """
    if project_output is None or str(project_output).strip() == "":
        return DeliveryRecoverySnapshot()
    project = Path(project_output)
    if not project.is_absolute():
        return DeliveryRecoverySnapshot(project_output=str(project), status="INVALID_PATH",
                                        diagnostics=("請明確選擇 absolute 校對專案資料夾。",))
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or not 1 <= max_attempts <= 5:
        raise ValueError("max_attempts must be between 1 and 5")
    # This existing helper only resolves a path. Do not call the neighboring
    # initialize/validate/repair helpers: they may create or migrate artifacts.
    from standalone_proofread import project_actual_evidence_root
    root = project_actual_evidence_root(project)
    project = project.resolve()
    base = dict(project_output=str(project), actual_root=str(root))
    try:
        if not project.is_dir():
            return DeliveryRecoverySnapshot(**base, status="PROJECT_ABSENT",
                                            diagnostics=("所選專案資料夾不存在或不是資料夾；未建立任何資料。",))
        resolution_error = None
        if repository is None:
            try:
                repository = library.GlobalExactGlyphRepository.resolved()
            except library.GlobalLibraryError as exc:
                resolution_error = (None, exc.status, str(exc))
        database_path = str(repository.path) if repository is not None else ""
        for _attempt in range(max_attempts):
            try:
                frozen = _capture(root)
                journal, outbox, js, os, diagnostics = _local(frozen)
                ids = tuple(item["intent_id"] for item in (outbox or {}).get("items", []))
                first = resolution_error or _global_read(repository, ids)
                if frozen != _capture(root):
                    raise _Changed
                second = resolution_error or _global_read(repository, ids)
                if frozen != _capture(root):
                    raise _Changed
                # A failed Global observation cannot confirm any cross-store
                # fact. If both succeed, compare actual requested receipts,
                # including absence and payloads, rather than generation/time.
                if first[0] is not None and second[0] is not None and first[0] != second[0]:
                    raise _Changed
                global_snapshot = second[0] if first[0] is not None else None
                error = first if first[0] is None else second
                global_status = global_snapshot.store_status if global_snapshot is not None else error[1]
                if global_snapshot is None:
                    diagnostics.append("Global 無法核實；保留獨立有效的本機紀錄。" + error[2])
                items = _items(outbox, js, global_snapshot)
                status = "PARTIAL" if (js == "INVALID" or os == "INVALID" or global_snapshot is None
                                         or any(item.global_status == "MISMATCH" for item in items)) else "OBSERVED"
                return DeliveryRecoverySnapshot(
                    **base, status=status, journal_status=js, outbox_status=os, global_status=global_status,
                    database_path=database_path, transaction_id=journal["transaction_id"] if journal else "",
                    recovery_plan=json.dumps(journal["recovery_plan"], ensure_ascii=False, indent=2)
                    if journal and journal["recovery_plan"] is not None else "",
                    items=items, diagnostics=tuple(diagnostics),
                    input_hashes=tuple((name, hashlib.sha256(raw).hexdigest() if raw is not None else "ABSENT")
                                      for name, raw in zip((promotion.PROJECT_TRANSACTION_FILE,
                                                            promotion.GLOBAL_PROMOTION_OUTBOX_FILE), frozen)))
            except _Changed:
                continue
        return DeliveryRecoverySnapshot(**base, status="UNSTABLE", database_path=database_path,
                                        diagnostics=("讀取期間來源持續變動；已有限重讀，仍無法確認一致狀態。請稍後重新讀取。",))
    except (OSError, ValueError) as exc:
        return DeliveryRecoverySnapshot(**base, status="READ_FAILED",
                                        diagnostics=("專案資料無法讀取：" + str(exc),))
