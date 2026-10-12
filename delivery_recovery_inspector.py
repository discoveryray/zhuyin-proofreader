"""Project delivery/recovery observation UI; background readers never call Tk."""
from __future__ import annotations

import json
import queue
import threading
import tkinter as tk
from tkinter import filedialog, ttk

from delivery_recovery_service import inspect_delivery_recovery


ITEM_LABELS = {"PENDING": "待交付／待核實", "AWAITING_ACK": "Global 已提交，待本機 ack",
               "DELIVERED": "本機已記錄交付", "UNCONFIRMED": "無法確認"}
FILTER_LABELS = ("全部", *ITEM_LABELS.values())
GLOBAL_LABELS = {"VALID": "已核查指定資料庫", "ABSENT": "指定資料庫不存在",
                 "BUSY": "資料庫忙碌", "LOCKED": "資料庫鎖定", "PERMISSION_DENIED": "權限不足",
                 "CORRUPT": "資料庫損毀", "SCHEMA_INCOMPATIBLE": "資料庫契約不相容",
                 "VALIDATION_FAILED": "資料庫未通過驗證", "PATH_UNRESOLVED": "資料庫路徑無法解析",
                 "READ_FAILED": "讀取失敗", "NOT_READ": "尚未核查"}


def filter_items(snapshot, status="全部"):
    if snapshot is None:
        return ()
    return tuple(row for row in snapshot.items if status == "全部" or ITEM_LABELS[row.status] == status)


def snapshot_summary(snapshot):
    if snapshot is None or snapshot.status == "NO_PROJECT":
        return "請選擇要查閱的校對專案資料夾。"
    if snapshot.status in {"UNSTABLE", "READ_FAILED", "INVALID_PATH", "PROJECT_ABSENT"}:
        return "\n".join(snapshot.diagnostics)
    local = {
        "ABSENT": "沒有交易 journal；無法由缺少紀錄判定歷史交易或 refresh 已完成。",
        "PREPARED": "本機 actual 交易尚未完成；可能已有部分寫入，需由既有明確恢復流程處理。",
        "COMMITTED": "本機 actual 已提交，已保存 recovery plan；refresh／refresh ack 尚未確認完成。",
        "INVALID": "交易紀錄無法驗證；本機 actual 是否提交無法確認。",
    }.get(snapshot.journal_status, "本機交易尚未確認。")
    if snapshot.outbox_status == "ABSENT":
        delivery = "沒有 outbox 紀錄。"
    elif snapshot.outbox_status == "INVALID":
        delivery = "outbox 未通過驗證；項目無法確認。"
    elif not snapshot.items:
        delivery = "outbox 有效，目前清單為空。"
    else:
        pending = sum(row.local_status == "PENDING" for row in snapshot.items)
        recorded = sum(row.local_status == "DELIVERED" for row in snapshot.items)
        delivery = f"目前 outbox 標記：待交付 {pending} 筆；本機已記錄交付 {recorded} 筆。"
        if snapshot.journal_status in {"PREPARED", "INVALID"}:
            delivery += " 這些可能是部分交易寫入，不代表已提交的項目。"
    return (local + "\n" + delivery + "\nGlobal：" +
            GLOBAL_LABELS.get(snapshot.global_status, "無法核實（" + snapshot.global_status + "）") +
            "。\n" + "\n".join(snapshot.diagnostics))


def _replace_pending(requests, request):
    while True:
        try:
            requests.put_nowait(request)
            return
        except queue.Full:
            try:
                requests.get_nowait()
            except queue.Empty:
                pass


def _worker(loader, requests, results, closed):
    while not closed.is_set():
        request = requests.get()
        if request is None or closed.is_set():
            return
        token, project = request
        try:
            result = token, loader(project), ""
        except Exception as exc:
            result = token, None, f"{type(exc).__name__}: {exc}"
        if not closed.is_set():
            results.put(result)


class DeliveryRecoveryReader:
    """One worker, bounded pending work; only the latest project request applies."""
    def __init__(self, loader=inspect_delivery_recovery):
        self.requests, self.results = queue.Queue(maxsize=1), queue.Queue()
        self.closed = threading.Event()
        self.token = 0
        self.project = None
        self.snapshot = None
        self.error = ""
        self.loading = False
        self.worker = threading.Thread(target=_worker, args=(loader, self.requests, self.results, self.closed),
                                       daemon=True)
        self.worker.start()

    def select(self, project):
        if self.closed.is_set():
            return
        self.project = project or None
        self.refresh()

    def refresh(self):
        if self.closed.is_set():
            return
        self.token += 1
        self.snapshot = None
        self.error = ""
        self.loading = bool(self.project)
        if self.project:
            _replace_pending(self.requests, (self.token, self.project))
        else:
            try:
                self.requests.get_nowait()
            except queue.Empty:
                pass

    def drain(self):
        changed = False
        while not self.closed.is_set():
            try:
                token, snapshot, error = self.results.get_nowait()
            except queue.Empty:
                break
            if token == self.token:
                self.snapshot, self.error, self.loading = snapshot, error, False
                changed = True
        return changed

    def close(self):
        self.closed.set()
        self.token += 1
        self.snapshot = None
        self.loading = False
        _replace_pending(self.requests, None)


class DeliveryRecoveryInspector(tk.Toplevel):
    def __init__(self, master, *, project_output=None, repository=None, loader=None):
        super().__init__(master)
        self.title("交付與恢復狀態｜唯讀查閱")
        self.geometry(f"{min(1080, self.winfo_screenwidth() - 80)}x{min(800, self.winfo_screenheight() - 100)}")
        self.reader = DeliveryRecoveryReader(loader or (
            lambda project: inspect_delivery_recovery(project, repository=repository)))
        self._poll_id = None
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Destroy>", self._on_destroy, add=True)
        bar = ttk.Frame(self, padding=12)
        bar.pack(fill="x")
        ttk.Label(bar, text="交付與恢復狀態", font=("Microsoft JhengHei UI", 16, "bold")).pack(side="left")
        self.refresh_button = ttk.Button(bar, text="重新讀取", command=self.refresh)
        self.refresh_button.pack(side="right")
        self.select_button = ttk.Button(bar, text="選擇校對專案", command=self.pick_project)
        self.select_button.pack(side="right", padx=8)
        self.project_text, self.summary_text = tk.StringVar(self), tk.StringVar(self)
        ttk.Label(self, textvariable=self.project_text, wraplength=980).pack(fill="x", padx=12)
        ttk.Label(self, textvariable=self.summary_text, wraplength=980, justify="left").pack(fill="x", padx=12, pady=8)
        ttk.Label(self, text="唯讀觀察：沒有紀錄不代表全專案完成；交付不代表核准或可重用。筆數僅供呈現。\n"
                  "COMMITTED 留存時，可由既有明確的 actual refresh／恢復流程處理；本視窗不執行交付、ack 或恢復。",
                  wraplength=980, justify="left").pack(fill="x", padx=12)
        filters = ttk.Frame(self, padding=(12, 8))
        filters.pack(fill="x")
        ttk.Label(filters, text="項目狀態：").pack(side="left")
        self.filter_status = tk.StringVar(self, "全部")
        self.status_filter = ttk.Combobox(filters, textvariable=self.filter_status, values=FILTER_LABELS,
                                         state="readonly", width=32)
        self.status_filter.pack(side="left")
        self.filter_status.trace_add("write", lambda *_args: self._render_items())
        self.tree = ttk.Treeview(self, columns=("reading", "status"), show="headings", height=6,
                                selectmode="browse")
        self.tree.heading("reading", text="實際注音（交付內容）")
        self.tree.heading("status", text="交付觀察")
        self.tree.column("reading", width=200)
        self.tree.column("status", width=600)
        self.tree.pack(fill="both", expand=True, padx=12)
        self.tree.bind("<<TreeviewSelect>>", self._show_item)
        tabs = ttk.Notebook(self)
        tabs.pack(fill="both", expand=True, padx=12, pady=8)
        self.item_text = self._text_tab(tabs, "項目說明")
        self.plan_text = self._text_tab(tabs, "恢復計畫與識別資訊")
        self.details_text = self._text_tab(tabs, "項目詳細資訊")
        self.set_project(project_output)
        self._poll_id = self.after(50, self._poll)

    def _text_tab(self, tabs, name):
        frame = ttk.Frame(tabs)
        tabs.add(frame, text=name)
        text = tk.Text(frame, wrap="word", height=10, state="disabled")
        scroll = ttk.Scrollbar(frame, command=text.yview)
        scroll.pack(side="right", fill="y")
        text.configure(yscrollcommand=scroll.set)
        text.pack(fill="both", expand=True)
        return text

    def _text(self, widget, value):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
        widget.configure(state="disabled")

    def pick_project(self):
        project = filedialog.askdirectory(parent=self, title="選擇校對專案資料夾")
        if project:
            self.set_project(project)

    def set_project(self, project):
        self.reader.select(str(project) if project else None)
        self._render()

    def refresh(self):
        self.reader.refresh()
        self._render()

    def _render(self):
        self.project_text.set("專案：" + (str(self.reader.project) if self.reader.project else "尚未選取"))
        self.summary_text.set("正在重新讀取；舊結果已清除。" if self.reader.loading else
                              "讀取失敗；目前狀態無法確認。\n" + self.reader.error if self.reader.error else
                              snapshot_summary(self.reader.snapshot))
        self._render_items()
        snapshot = self.reader.snapshot
        detail = "尚無可確認的恢復計畫。"
        if snapshot:
            detail = (f"actual 目錄：{snapshot.actual_root}\nGlobal 資料庫：{snapshot.database_path or '無法解析'}\n"
                      f"交易狀態：{snapshot.journal_status}\ntransaction_id：{snapshot.transaction_id or '無'}\n"
                      "此 journal 的計畫（PREPARED 時尚未提交）：\n" + (snapshot.recovery_plan or "無") +
                      "\n本次固定輸入 SHA-256（僅稽核，不是 fingerprint）：\n" +
                      json.dumps(dict(snapshot.input_hashes), ensure_ascii=False, indent=2))
        self._text(self.plan_text, detail)

    def _render_items(self):
        self.tree.delete(*self.tree.get_children())
        self._visible = filter_items(self.reader.snapshot, self.filter_status.get())
        for index, row in enumerate(self._visible):
            self.tree.insert("", "end", iid=str(index), values=(row.reading, ITEM_LABELS[row.status]))
        self._text(self.item_text, "請選取項目。" if self._visible else "目前篩選沒有可顯示項目。")
        self._text(self.details_text, "尚未選取項目。")

    def _show_item(self, _event=None):
        selected = self.tree.selection()
        if not selected:
            return
        row = self._visible[int(selected[0])]
        self._text(self.item_text, row.explanation)
        self._text(self.details_text, row.details)

    def _poll(self):
        if self.reader.closed.is_set():
            return
        if self.reader.drain():
            self._render()
        self._poll_id = self.after(50, self._poll)

    def _on_destroy(self, event):
        if event.widget is self:
            self.reader.close()
            if self._poll_id is not None:
                self.after_cancel(self._poll_id)
                self._poll_id = None
