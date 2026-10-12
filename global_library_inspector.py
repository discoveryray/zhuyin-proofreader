"""Global library inspection and explicit approval. Workers never call Tk."""
from __future__ import annotations

import json
import queue
import threading
import tkinter as tk
from tkinter import ttk
from typing import Callable

import global_exact_glyph_library as library
import global_library_approval as approval_ui


STATE_LABELS = {
    library.CANDIDATE: "候選",
    library.PROMOTION_READY: "待核准",
    library.VERIFIED_GLOBAL: "已驗證",
    library.QUARANTINED_CONFLICT: "衝突隔離",
}
READ_CONFLICT_LABEL = "讀取時衝突（停用重用）"
FILTER_LABELS = ("全部", *STATE_LABELS.values(), READ_CONFLICT_LABEL)
REASONS = {
    library.CANDIDATE: "候選資料尚未取得有效的全域核准。",
    library.PROMOTION_READY: "已達既有獨立來源門檻，仍須明確核准。",
    library.QUARANTINED_CONFLICT: "資料庫已保存衝突隔離；不允許重用。",
    "LEGACY_V0_READ_CONFLICT": (
        "舊版候選與保留讀音互相矛盾，已在本次讀取停用重用。"
        "本次查閱未寫入隔離，也未撤銷資料庫中的核准。"),
}
ERROR_LABELS = {
    library.BUSY: "資料庫忙碌；請稍後重新讀取。",
    library.LOCKED: "資料庫已鎖定；請稍後重新讀取。",
    library.PERMISSION_DENIED: "權限不足，無法讀取資料庫。",
    library.CORRUPT: "資料庫損壞，無法安全查閱。",
    library.SCHEMA_INCOMPATIBLE: "資料庫版本或契約不相容，無法安全查閱。",
    library.VALIDATION_FAILED: "資料庫未通過安全驗證，無法安全查閱。",
    "READ_FAILED": "讀取失敗，無法確認目前資料。",
}
COUNT_EXPLANATION = (
    "直接證據：可計入門檻的直接目視確認筆數（涵蓋所有保留讀音）。\n"
    "獨立來源：跨專案、PDF、嵌入字型的最大獨立來源數（涵蓋所有保留讀音），"
    "不是同讀音且同時滿足 occurrence／review 獨立性的 quorum。\n"
    "舊資料候選：歷史匯入筆數，永遠不計 quorum。任何筆數或來源數都不代表已核准或可重用。"
)


def record_status(record: library.GlobalGlyphInspectionRecord) -> str:
    if record.read_side_conflict is not None:
        return READ_CONFLICT_LABEL
    return STATE_LABELS[record.stored_state]


def reuse_text(record: library.GlobalGlyphInspectionRecord) -> str:
    return "本庫允許重用" if record.global_reuse_allowed else "停用重用"


def filter_records(snapshot, status="全部", query=""):
    """Presentation filtering only; this never changes the service's safety result."""
    if snapshot is None:
        return ()
    query = query.strip().casefold()
    return tuple(record for record in snapshot.records
                 if (status == "全部" or status == STATE_LABELS[record.stored_state]
                     or (status == READ_CONFLICT_LABEL and record.read_side_conflict is not None))
                 and (not query or query in " ".join((record.glyph_id, record.identity.kind,
                     record.identity.style_group, record.identity.glyph_sha256, *record.readings)).casefold()))


def _replace_pending(requests, value):
    while True:
        try:
            requests.put_nowait(value)
            return
        except queue.Full:
            try:
                requests.get_nowait()
            except queue.Empty:
                pass


def _read_worker(loader, requests, results, closed):
    while not closed.is_set():
        request = requests.get()
        if request is None or closed.is_set():
            return
        try:
            snapshot = loader()
            result = (request, snapshot, "", "")
        except library.GlobalLibraryError as exc:
            result = (request, None, exc.status, str(exc))
        except Exception as exc:
            result = (request, None, "READ_FAILED", f"{type(exc).__name__}: {exc}")
        if not closed.is_set():
            results.put(result)


class InspectionReader:
    """One worker and at most one pending read; latest request wins on the UI thread."""

    def __init__(self, loader: Callable[[], library.GlobalLibraryInspectionSnapshot]):
        self.requests = queue.Queue(maxsize=1)
        self.results = queue.Queue()
        self.closed = threading.Event()
        self.request_id = 0
        self.snapshot = None
        self.loading = False
        self.error_status = ""
        self.error_detail = ""
        self.last_successful_read_at = ""
        self.worker = threading.Thread(
            target=_read_worker, args=(loader, self.requests, self.results, self.closed), daemon=True)
        self.worker.start()

    def refresh(self):
        if self.closed.is_set():
            return
        self.request_id += 1
        self.snapshot = None
        self.error_status = self.error_detail = ""
        self.loading = True
        _replace_pending(self.requests, self.request_id)

    def drain(self):
        changed = False
        while not self.closed.is_set():
            try:
                request, snapshot, status, detail = self.results.get_nowait()
            except queue.Empty:
                break
            if request != self.request_id:
                continue
            self.snapshot, self.error_status, self.error_detail = snapshot, status, detail
            self.loading = False
            if snapshot is not None and snapshot.store_status == library.VALID:
                self.last_successful_read_at = snapshot.loaded_at
            changed = True
        return changed

    def close(self):
        self.closed.set()
        self.snapshot = None
        _replace_pending(self.requests, None)


def database_status(reader: InspectionReader) -> str:
    if reader.loading:
        return "正在重新讀取；清單暫不顯示，避免將舊資料當成最新結果。"
    if reader.error_status:
        return ERROR_LABELS.get(reader.error_status, ERROR_LABELS["READ_FAILED"]) + " 清單已清除。"
    snapshot = reader.snapshot
    if snapshot is None:
        return "尚未讀取。"
    if snapshot.store_status == library.ABSENT:
        return "資料庫尚未建立；本次唯讀查閱未建立任何資料。"
    if not snapshot.records:
        return "資料庫正常，但尚無字形資料。"
    return f"資料庫正常；本次快照共 {len(snapshot.records)} 筆字形。"


def record_sections(record: library.GlobalGlyphInspectionRecord) -> dict[str, str]:
    reason = REASONS.get(record.reuse_block_reason, "本庫已驗證並具有效核准；實際解碼仍須通過跨來源衝突檢查。")
    overview = (
        f"目前狀態：{record_status(record)}\n資料庫儲存狀態：{STATE_LABELS[record.stored_state]}\n"
        f"Global reuse：{reuse_text(record)}\n原因：{reason}\n\n"
        f"保留讀音：{'、'.join(record.readings) or '尚無讀音'}\n"
        f"資料庫儲存的作用中讀音：{record.stored_active_reading or '無'}\n"
        f"直接證據：{record.direct_source_count}；獨立來源：{record.independent_source_count}；"
        f"舊資料候選：{len(record.legacy_candidates)}\n\n{COUNT_EXPLANATION}\n\n"
        "原始字形預覽：資料庫未保存可顯示的原始字形影像／字型內容。\n"
        "本視窗的注音是讀音文字，不是原始 exact glyph 預覽。來源 PDF 頁面內容未載入。"
    )
    source_labels = {row["evidence_id"]: f"證據 {index}" for index, row in enumerate(record.source_evidence, 1)}
    evidence_classes = {library.DIRECT_VISUAL_ACTUAL: "直接目視確認",
                        library.LEGACY_PROJECT_CANDIDATE: "舊專案候選（不計門檻）",
                        library.PROPAGATED_PROJECT_OCCURRENCE: "專案傳播（不計門檻）"}
    sources = []
    for index, row in enumerate(record.source_evidence, 1):
        sources.append(f"證據 {index}｜{row['reading']}｜{evidence_classes[row['evidence_class']]}\n"
                       f"確認時間：{row['confirmed_at']}\n"
                       f"計入直接證據門檻：{'是' if row['counts_toward_global_quorum'] else '否'}\n"
                       "來源識別與決策摘要雜湊請見詳細資訊；未載入原始來源頁面或確認畫面。")
    approvals = []
    for index, row in enumerate(record.approvals, 1):
        roster = "、".join(source_labels[item] for item in json.loads(row["quorum_evidence_ids_json"]))
        approvals.append(f"核准紀錄 {index}｜{row['reading']}｜"
                         f"{'已核准（儲存紀錄）' if row['status'] == library.APPROVED else '已撤銷（儲存紀錄）'}\n"
                         f"核准時間：{row['approved_at']}\n綁定證據：{roster}")
    conflicts = []
    if record.read_side_conflict is not None:
        conflicts.append("本次讀取的安全判定：\n" + REASONS["LEGACY_V0_READ_CONFLICT"] +
                         "\n矛盾讀音：" + "、".join(record.read_side_conflict.conflicting_readings))
    for index, row in enumerate(record.conflicts, 1):
        conflicts.append(f"持久化衝突紀錄 {index}｜{'未解決' if row['status'] == library.OPEN else '已解決'}\n"
                         f"保留讀音：{'、'.join(json.loads(row['conflicting_readings_json']))}\n"
                         f"首次記錄：{row['first_event_at']}\n最後記錄：{row['last_event_at']}")
    legacy_labels = {library.MIGRATION_CANDIDATE: "舊資料候選",
                     library.MIGRATION_INSUFFICIENT: "來源映射不足的歷史候選",
                     library.MIGRATION_CONFLICT: "參與衝突的歷史候選"}
    legacy_levels = {"USER_VERIFIED_SINGLE": "歷史單一來源確認", "VERIFIED_EXACT_GLYPH": "歷史專案字形驗證",
                     "QUARANTINED_CONFLICT": "歷史衝突隔離"}
    legacy = [f"舊資料候選 {index}｜{row['reading']}｜{legacy_labels[row['status']]}\n"
              f"舊狀態：{legacy_levels[row['old_verification_level']]}\n匯入時間：{row['imported_at']}\n"
              "quorum 貢獻：0；未載入舊教材內容或字形預覽。"
              for index, row in enumerate(record.legacy_candidates, 1)]
    raw = {"glyph_truth": dict(record.truth),
           "source_evidence": [dict(row) for row in record.source_evidence],
           "promotion_approval": [dict(row) for row in record.approvals],
           "glyph_conflict": [dict(row) for row in record.conflicts],
           "migration_candidate": [dict(row) for row in record.legacy_candidates],
           "provenance_event": [dict(row) for row in record.provenance]}
    if record.read_side_conflict:
        raw["read_side_conflict"] = {
            "diagnostic": record.read_side_conflict.diagnostic,
            "legacy_import_ids": record.read_side_conflict.legacy_import_ids,
            "conflicting_readings": record.read_side_conflict.conflicting_readings}
    return {"概覽": overview, "來源證據": "\n\n".join(sources) or "尚無來源證據。",
            "核准紀錄": "\n\n".join(approvals) or "尚無核准紀錄；筆數不代表核准。",
            "衝突紀錄": "\n\n".join(conflicts) or "本次有效快照沒有衝突紀錄或讀取時衝突。",
            "舊資料候選": "\n\n".join(legacy) or "尚無舊資料候選。",
            "詳細資訊": json.dumps(raw, ensure_ascii=False, indent=2)}


class GlobalLibraryInspector(tk.Toplevel):
    def __init__(self, master, *, loader=None, repository=None):
        super().__init__(master)
        self.title("全域字形庫｜狀態與證據查閱")
        self.geometry(f"{min(1120, self.winfo_screenwidth() - 80)}x{min(800, self.winfo_screenheight() - 100)}")
        # Resolve once and retain the exact repository for reads and confirmation.
        # A custom read-only loader must never acquire a live write target.
        if repository is None and loader is None:
            try:
                repository = library.GlobalExactGlyphRepository.resolved()
            except library.GlobalLibraryError as exc:
                def failed_loader(error=exc):
                    raise error
                loader = failed_loader
        self.repository = repository
        self.reader = InspectionReader(loader or repository.load_inspection_snapshot)
        self.approval_dialog = None
        self._approval_refresh = None
        self._poll_id = None
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Destroy>", self._on_destroy, add=True)
        top = ttk.Frame(self, padding=12)
        top.pack(fill="x")
        ttk.Label(top, text="全域字形庫", font=("Microsoft JhengHei UI", 16, "bold")).pack(side="left")
        self.refresh_button = ttk.Button(top, text="重新讀取", command=self.refresh)
        self.refresh_button.pack(side="right")
        self.approval_button = ttk.Button(top, text="檢視並核准", command=self.open_approval, state="disabled")
        self.approval_button.pack(side="right", padx=10)
        self.status_text = tk.StringVar(self)
        ttk.Label(self, textvariable=self.status_text, wraplength=950, justify="left").pack(fill="x", padx=12)
        self.last_read_text = tk.StringVar(self)
        ttk.Label(self, textvariable=self.last_read_text).pack(fill="x", padx=12, pady=4)
        ttk.Label(self, text="開啟、搜尋與重新讀取維持唯讀；核准須另行檢視並明確確認。實際重用仍須通過跨來源安全檢查。",
                  wraplength=950).pack(fill="x", padx=12)
        filters = ttk.Frame(self, padding=(12, 8))
        filters.pack(fill="x")
        ttk.Label(filters, text="狀態：").pack(side="left")
        self.filter_status = tk.StringVar(self, "全部")
        self.status_filter = ttk.Combobox(filters, textvariable=self.filter_status, values=FILTER_LABELS,
                                         state="readonly", width=27)
        self.status_filter.pack(side="left", padx=(0, 12))
        ttk.Label(filters, text="搜尋注音／字形識別：").pack(side="left")
        self.search_query = tk.StringVar(self)
        self.search_entry = ttk.Entry(filters, textvariable=self.search_query)
        self.search_entry.pack(side="left", fill="x", expand=True)
        self.filter_status.trace_add("write", lambda *_args: self._render_records())
        self.search_query.trace_add("write", lambda *_args: self._render_records())
        panes = ttk.Panedwindow(self, orient="vertical")
        panes.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        list_frame = ttk.Frame(panes)
        panes.add(list_frame, weight=1)
        columns = ("reading", "state", "reuse", "direct", "independent", "legacy")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings", selectmode="browse", height=7)
        for column, label, width in zip(columns,
                ("保留讀音", "目前狀態", "Global reuse", "直接證據", "獨立來源", "舊資料候選"),
                (150, 230, 150, 90, 90, 100)):
            self.tree.heading(column, text=label)
            self.tree.column(column, width=width, minwidth=70)
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._select)
        self.list_summary = tk.StringVar(self)
        ttk.Label(list_frame, textvariable=self.list_summary).pack(anchor="w", pady=3)
        self.details = ttk.Notebook(panes)
        panes.add(self.details, weight=3)
        self.texts = {}
        for label in ("概覽", "來源證據", "核准紀錄", "衝突紀錄", "舊資料候選", "詳細資訊"):
            frame = ttk.Frame(self.details)
            self.details.add(frame, text=label)
            text = tk.Text(frame, wrap="word", state="disabled", font=("Microsoft JhengHei UI", 10), height=12)
            scrollbar = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
            text.configure(yscrollcommand=scrollbar.set)
            scrollbar.pack(side="right", fill="y")
            text.pack(fill="both", expand=True)
            self.texts[label] = text
        self.refresh()
        self._poll_id = self.after(50, self._poll)

    def _set_text(self, name, value):
        widget = self.texts[name]
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
        widget.configure(state="disabled")

    def _database_details(self):
        snapshot = self.reader.snapshot
        if snapshot is None:
            return self.reader.error_detail or "尚無目前有效快照。"
        return json.dumps({"database_path": snapshot.database_path, "loaded_at": snapshot.loaded_at,
                           "library_meta": dict(snapshot.metadata)}, ensure_ascii=False, indent=2)

    def refresh(self):
        self.reader.refresh()
        self._render()

    def _render(self):
        self.status_text.set(database_status(self.reader))
        self.last_read_text.set("最後成功讀取資料庫：" + (self.reader.last_successful_read_at or "尚無成功紀錄"))
        self._render_records()

    def _render_records(self):
        old_selection = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        rows = filter_records(self.reader.snapshot, self.filter_status.get(), self.search_query.get())
        self._visible = {row.glyph_id: row for row in rows}
        for row in rows:
            self.tree.insert("", "end", iid=row.glyph_id, values=("、".join(row.readings) or "尚無讀音",
                record_status(row), reuse_text(row), row.direct_source_count,
                row.independent_source_count, len(row.legacy_candidates)))
        self.list_summary.set(f"篩選結果：{len(rows)} 筆；依資料庫儲存狀態篩選，停用重用原因請看概覽。")
        if old_selection and old_selection[0] in self._visible:
            self.tree.selection_set(old_selection[0])
        self._select()

    def _select(self, _event=None):
        selected = self.tree.selection()
        record = self._visible.get(selected[0]) if selected else None
        snapshot = self.reader.snapshot
        bound = (self.repository is not None and snapshot is not None
                 and snapshot.database_path == str(self.repository.path))
        pending = self.repository is not None and approval_ui.pending_submission(self.repository) is not None
        self.approval_button.configure(state="normal" if pending or (record is not None and bound) else "disabled")
        dialog = self.approval_dialog
        if dialog is not None and dialog.winfo_exists() and not dialog.session.attempted:
            if record is None or record.glyph_id != dialog.session.glyph_id:
                dialog.destroy()
        sections = record_sections(record) if record is not None else {}
        for name in self.texts:
            value = sections.get(name, "請選取一筆字形查看證據。" if self._visible else database_status(self.reader))
            if name == "詳細資訊":
                value = self._database_details() + ("\n\n" + sections[name] if record is not None else "")
            self._set_text(name, value)

    def open_approval(self):
        if self.repository is None:
            return
        if self.approval_dialog is not None and self.approval_dialog.winfo_exists():
            self.approval_dialog.lift()
            return
        # Recover even a closed in-flight/uncertain dialog using the original request.
        session = approval_ui.pending_submission(self.repository)
        if session is None:
            selected = self.tree.selection()
            snapshot = self.reader.snapshot
            if (not selected or selected[0] not in self._visible or snapshot is None
                    or snapshot.database_path != str(self.repository.path)):
                return
            session = approval_ui.ApprovalSession(self.repository, selected[0])
        self.approval_dialog = approval_ui.ApprovalDialog(self, session, on_committed=self._approval_committed)

    def _approval_committed(self, dialog):
        self.refresh()
        self._approval_refresh = (self.reader.request_id, dialog)

    def _finish_approval_refresh(self, error=""):
        if self._approval_refresh is None:
            return
        request_id, dialog = self._approval_refresh
        if request_id != self.reader.request_id:
            error = "核准後的讀取已被另一個讀取取代；請查看最新字形庫狀態。"
        self._approval_refresh = None
        if dialog.winfo_exists():
            dialog.refresh_finished(error or self.reader.error_detail)

    def _poll(self):
        self._poll_id = None
        if self.reader.closed.is_set():
            return
        if self.reader.drain():
            try:
                self._render()
            except Exception as exc:
                self._finish_approval_refresh(f"顯示失敗：{exc}")
                if self.approval_dialog is None or not self.approval_dialog.winfo_exists():
                    raise
            else:
                self._finish_approval_refresh()
        self._poll_id = self.after(50, self._poll)

    def _on_destroy(self, event):
        if event.widget is self:
            self.reader.close()
            if self._poll_id is not None:
                self.after_cancel(self._poll_id)
                self._poll_id = None
