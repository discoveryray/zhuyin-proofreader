"""Explicit-start legacy preflight UI. Workers never call Tk or write sources."""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import filedialog, ttk

import legacy_global_migration as migration
from legacy_migration_inspection import (
    ITEM_LABELS, SCOPE_NOTE, blocked_report, filter_items,
    inspect_legacy_migration, report_summary,
)


def _worker(loader, requests, results, closed, watch_interval):
    watching = None
    while not closed.is_set():
        try:
            request = requests.get(timeout=watch_interval)
        except queue.Empty:
            if watching is not None:
                token, report, cancelled = watching
                if cancelled.is_set():
                    watching = None
                    continue
                try:
                    migration.validate_source_snapshot(dict(report.source_input_hashes))
                except Exception as exc:
                    if not closed.is_set() and not cancelled.is_set():
                        results.put((token, blocked_report(report.project_output, report.pdf_roots, exc)))
                    watching = None
            continue
        if request is None or closed.is_set():
            return
        token, project, roots, cancelled = request
        watching = None
        try:
            report = loader(project, pdf_roots=roots)
        except Exception as exc:
            report = blocked_report(project, roots, exc)
        if not closed.is_set():
            results.put((token, report))
            if report.status == "VALID" and not cancelled.is_set():
                watching = token, report, cancelled


class MigrationInspectionReader:
    """One worker and at most one requested run; selection never runs preflight.

    After success only exact, already admitted source files are watched. A
    change clears the report and requires another explicit start, with no retry.
    """
    def __init__(self, loader=inspect_legacy_migration, *, watch_interval=1.0):
        self.requests, self.results = queue.Queue(maxsize=1), queue.Queue()
        self.closed, self.cancelled = threading.Event(), threading.Event()
        self.token, self.active_token = 0, None
        self.project, self.pdf_roots = None, ()
        self.report = None
        self.loading = False
        self.worker = threading.Thread(target=_worker,
            args=(loader, self.requests, self.results, self.closed, watch_interval), daemon=True)
        self.worker.start()

    def select(self, project, pdf_roots=()):
        if self.closed.is_set():
            return
        self.cancelled.set()
        self.token += 1
        self.project = str(project) if project else None
        self.pdf_roots = tuple(map(str, pdf_roots))
        self.report = None

    def start(self):
        if self.closed.is_set() or self.loading or not self.project:
            return False
        self.cancelled.set()
        self.cancelled = threading.Event()
        self.token += 1
        self.active_token = self.token
        self.report = None
        self.loading = True
        self.requests.put_nowait((self.token, self.project, self.pdf_roots, self.cancelled))
        return True

    def drain(self):
        changed = False
        while not self.closed.is_set():
            try:
                token, report = self.results.get_nowait()
            except queue.Empty:
                break
            if token == self.active_token:
                self.loading = False
                changed = True
            if token == self.token:
                self.report = report
                changed = True
        return changed

    def close(self):
        self.closed.set()
        self.cancelled.set()
        self.token += 1
        self.report = None
        self.loading = False
        try:
            self.requests.get_nowait()
        except queue.Empty:
            pass
        self.requests.put_nowait(None)


class LegacyMigrationInspector(tk.Toplevel):
    def __init__(self, master, *, project_output=None, pdf_roots=(), global_library_root=None, loader=None):
        super().__init__(master)
        self.title("舊資料匯入預檢｜唯讀診斷")
        self.geometry(f"{min(1150, self.winfo_screenwidth() - 80)}x{min(880, self.winfo_screenheight() - 100)}")
        self.reader = MigrationInspectionReader(loader or (
            lambda project, *, pdf_roots: inspect_legacy_migration(
                project, pdf_roots=pdf_roots, global_library_root=global_library_root)))
        self._poll_id = None
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Destroy>", self._on_destroy, add=True)
        bar = ttk.Frame(self, padding=10)
        bar.pack(fill="x")
        ttk.Label(bar, text="舊資料匯入預檢", font=("Microsoft JhengHei UI", 16, "bold")).pack(side="left")
        self.start_button = ttk.Button(bar, text="開始預檢", command=self.start)
        self.start_button.pack(side="right")
        self.select_button = ttk.Button(bar, text="選擇校對專案", command=self.pick_project)
        self.select_button.pack(side="right", padx=8)
        self.project_text, self.roots_text, self.summary_text = (tk.StringVar(self) for _ in range(3))
        ttk.Label(self, textvariable=self.project_text, wraplength=1030).pack(fill="x", padx=12)
        sources = ttk.Frame(self, padding=(12, 4))
        sources.pack(fill="x")
        self.pdf_button = ttk.Button(sources, text="加入 PDF 來源資料夾", command=self.pick_pdf_root)
        self.pdf_button.pack(side="right")
        self.clear_pdf_button = ttk.Button(sources, text="清除 PDF 來源資料夾", command=lambda: self.set_pdf_roots(()))
        self.clear_pdf_button.pack(side="right", padx=8)
        ttk.Label(sources, textvariable=self.roots_text, wraplength=650).pack(side="left", fill="x")
        ttk.Label(self, textvariable=self.summary_text, wraplength=1030, justify="left").pack(fill="x", padx=12, pady=6)
        ttk.Label(self, text=SCOPE_NOTE, wraplength=1030, justify="left").pack(fill="x", padx=12)
        filters = ttk.Frame(self, padding=(12, 8))
        filters.pack(fill="x")
        ttk.Label(filters, text="診斷篩選：").pack(side="left")
        self.filter_status = tk.StringVar(self, "全部")
        self.status_filter = ttk.Combobox(filters, textvariable=self.filter_status,
                                         values=("全部", *ITEM_LABELS.values()), state="readonly", width=38)
        self.status_filter.pack(side="left")
        ttk.Label(filters, text="搜尋來源／讀音／識別：").pack(side="left", padx=(8, 0))
        self.query = tk.StringVar(self)
        ttk.Entry(filters, textvariable=self.query, width=25).pack(side="left")
        self.filter_status.trace_add("write", lambda *_: self._render_items())
        self.query.trace_add("write", lambda *_: self._render_items())
        table = ttk.Frame(self)
        table.pack(fill="both", expand=True, padx=12)
        self.tree = ttk.Treeview(table, columns=("status", "source", "reading", "identity"),
                                 show="headings", selectmode="browse", height=6)
        for column, label, width in (("status", "診斷狀態", 270), ("source", "來源檔", 310),
                                      ("reading", "保留讀音", 110), ("identity", "字形識別類型", 230)):
            self.tree.heading(column, text=label)
            self.tree.column(column, width=width)
        scroll = ttk.Scrollbar(table, command=self.tree.yview)
        scroll.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._show_item)
        tabs = ttk.Notebook(self)
        tabs.pack(fill="both", expand=True, padx=12, pady=8)
        self.item_text = self._text_tab(tabs, "問題與下一步")
        self.targets_text = self._text_tab(tabs, "待確認位置")
        self.details_text = self._text_tab(tabs, "項目詳細資訊")
        self.report_text = self._text_tab(tabs, "整批報告與錯誤")
        self.reader.select(project_output, pdf_roots)
        self._render()
        self._poll_id = self.after(50, self._poll)

    def _text_tab(self, tabs, name):
        frame = ttk.Frame(tabs)
        tabs.add(frame, text=name)
        widget = tk.Text(frame, wrap="word", height=10, state="disabled")
        scroll = ttk.Scrollbar(frame, command=widget.yview)
        scroll.pack(side="right", fill="y")
        widget.configure(yscrollcommand=scroll.set)
        widget.pack(fill="both", expand=True)
        return widget

    def _text(self, widget, value):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
        widget.configure(state="disabled")

    def pick_project(self):
        project = filedialog.askdirectory(parent=self, title="選擇一個校對專案資料夾（不掃描其他專案）")
        if project:
            self.set_project(project)

    def pick_pdf_root(self):
        root = filedialog.askdirectory(parent=self, title="選擇原始 PDF 資料夾（只找指定檔名，不遞迴）")
        if root:
            self.set_pdf_roots(tuple(dict.fromkeys((*self.reader.pdf_roots, root))))

    def set_project(self, project):
        # A new project requires its own explicit optional roots selection.
        self.reader.select(project)
        self._render()

    def set_pdf_roots(self, roots):
        self.reader.select(self.reader.project, roots)
        self._render()

    def start(self):
        self.reader.start()
        self._render()

    def _render(self):
        self.project_text.set("專案：" + (self.reader.project or "尚未選取"))
        self.roots_text.set("另選 PDF 資料夾：" + ("；".join(self.reader.pdf_roots) or "無"))
        if self.reader.loading:
            summary = ("正在預檢；舊結果已清除。" if self.reader.active_token == self.reader.token else
                       "來源已切換；等待前次工作結束後，請再次按「開始預檢」。")
        else:
            summary = report_summary(self.reader.report)
        self.summary_text.set(summary)
        self.start_button.configure(state="disabled" if self.reader.loading or not self.reader.project else "normal")
        self._render_items()
        self._text(self.report_text, self.reader.report.details if self.reader.report else "尚無本次完整報告。")

    def _render_items(self):
        self.tree.delete(*self.tree.get_children())
        self._visible = filter_items(self.reader.report, self.filter_status.get(), self.query.get())
        for index, item in enumerate(self._visible):
            self.tree.insert("", "end", iid=str(index), values=(ITEM_LABELS[item.status], item.source_file,
                                                               item.reading, item.identity_text))
        self._text(self.item_text, "請選取項目。" if self._visible else "目前沒有可顯示項目。")
        self._text(self.targets_text, "尚未選取項目；未提供字形預覽。")
        self._text(self.details_text, "尚未選取項目。")

    def _show_item(self, _event=None):
        selected = self.tree.selection()
        if selected:
            item = self._visible[int(selected[0])]
            self._text(self.item_text, item.explanation)
            self._text(self.targets_text, item.targets_text)
            self._text(self.details_text, item.details)

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
