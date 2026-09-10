"""Explicit single-glyph approval UI; workers exchange data and never call Tk."""
from __future__ import annotations

import json
import os
import queue
import threading
import tkinter as tk
from tkinter import ttk

import global_exact_glyph_library as library


# Keep unresolved submissions available even if their window closes. A reopened
# window queries this SAME request; it cannot silently replace or resubmit it.
_submissions_lock = threading.Lock()
_submissions = {}


def _repository_key(repository):
    return os.path.normcase(str(repository.path.resolve()))


def pending_submission(repository):
    with _submissions_lock:
        return _submissions.get(_repository_key(repository))


def _work(function, results):
    try:
        results.put((function(), None))
    except Exception as exc:
        results.put((None, exc))


class ApprovalSession:
    """One immutable decision and at most one write, independent of widget lifetime."""

    def __init__(self, repository, glyph_id):
        self.repository = repository
        self.glyph_id = glyph_id
        self.preview = None
        self.outcome = None
        self.state = "LOADING"
        self.detail = ""
        self.attempted = False
        self.results = queue.Queue()
        self.operation = "preview"
        self.busy = True
        self._start(lambda: repository.load_approval_preview(glyph_id))

    def _start(self, function):
        threading.Thread(target=_work, args=(function, self.results), daemon=True).start()

    def confirm(self):
        if self.busy or self.attempted or self.preview is None or self.preview.request is None:
            return False
        request = self.preview.request
        if request.database_path != str(self.repository.path):
            self.state, self.detail = "BLOCKED", "預覽與目標資料庫不符，禁止提交。"
            return False
        with _submissions_lock:
            key = _repository_key(self.repository)
            if key in _submissions:
                self.state, self.detail = "BLOCKED", "此資料庫已有提交待確認；請查看原固定請求的結果。"
                return False
            _submissions[key] = self
        self.attempted, self.busy, self.operation, self.state = True, True, "submit", "SUBMITTING"
        self._start(self._submit)
        return True

    def _submit(self):
        request = self.preview.request
        try:
            result = library.approve_global_exact_glyph(self.repository, **request.service_arguments())
            if (result.get("glyph_id") == request.glyph_id
                    and result.get("glyph_revision") == request.glyph_revision + 1):
                if (result.get("state") == library.VERIFIED_GLOBAL
                        and result.get("approval_id") == request.approval_id
                        and result.get("approval_granted") is not False):
                    return (library.GlobalApprovalOutcome("APPROVAL_COMMITTED", approval_id=request.approval_id), "")
                if (result.get("state") == library.QUARANTINED_CONFLICT
                        and result.get("approval_granted") is False and result.get("receipt_digest")):
                    return (library.GlobalApprovalOutcome("CONFLICT_COMMITTED",
                            receipt_digest=result["receipt_digest"]), "")
            error = RuntimeError("核准服務回傳無法辨識的結果")
        except Exception as exc:
            error = exc
        # Never retry a write. Resolve uncertainty using the exact durable identity.
        try:
            outcome = self.repository.lookup_approval_outcome(request)
        except Exception as query_error:
            return (library.GlobalApprovalOutcome("UNKNOWN"), f"{error}\n固定請求查詢失敗：{query_error}")
        if outcome.status == "NOT_OBSERVED":
            state = "REJECTED" if isinstance(error, library.GlobalLibraryValidationError) else "UNKNOWN"
            outcome = library.GlobalApprovalOutcome(state)
        return outcome, f"{type(error).__name__}: {error}"

    def query_outcome(self):
        if self.busy or not self.attempted or self.state != "UNKNOWN":
            return False
        self.busy, self.operation = True, "query"
        self._start(lambda: self.repository.lookup_approval_outcome(self.preview.request))
        return True

    def drain(self):
        try:
            value, error = self.results.get_nowait()
        except queue.Empty:
            return False
        self.busy = False
        if self.operation == "preview":
            if error is not None:
                self.state, self.detail = "BLOCKED", f"無法安全預覽：{type(error).__name__}: {error}"
            else:
                self.preview = value
                self.state = "READY" if value.request else "BLOCKED"
                self.detail = value.reason
            return True
        if error is not None:
            self.state, self.detail = "UNKNOWN", f"固定請求結果仍不確定：{type(error).__name__}: {error}"
        else:
            outcome, detail = value if self.operation == "submit" else (value, "")
            self.outcome = outcome  # Preserve durable history BEFORE any rendering.
            self.state = "UNKNOWN" if outcome.status == "NOT_OBSERVED" else outcome.status
            self.detail = detail
        if self.state != "UNKNOWN":
            with _submissions_lock:
                key = _repository_key(self.repository)
                if _submissions.get(key) is self:
                    del _submissions[key]
        return True


def preview_sections(preview):
    record, request = preview.record, preview.request
    overview = preview.reason
    sources = []
    if record is not None:
        overview += (f"\n\n字形識別：{record.identity.kind}"
                     f"／{record.identity.style_group or '無樣式群組'}／{record.glyph_id[:12]}…"
                     f"\n本次讀音：{request.reading if request else '、'.join(record.readings) or '無'}")
        roster = set(request.quorum_evidence_ids) if request else set()
        for index, row in enumerate(record.source_evidence, 1):
            if row["evidence_class"] != library.DIRECT_VISUAL_ACTUAL:
                continue
            sources.append(f"直接證據 {index}｜讀音 {row['reading']}｜確認時間 {row['confirmed_at']}\n"
                           f"來源專案／session：{row['source_project_id']}\n"
                           f"本次完整提交清單：{'是' if row['evidence_id'] in roster else '不可提交'}\n"
                           "PDF、字型、occurrence、review 與決策識別請見詳細資訊。")
    overview += ("\n\n核准影響：核准既有證據的全域重用資格，成功後成為 VERIFIED_GLOBAL。"
                 "實際解碼仍須通過跨來源衝突檢查。\n"
                 "這不是重新確認 PDF 注音，不產生 DIRECT_VISUAL_ACTUAL。\n"
                 "Legacy 舊資料永遠不計 quorum；來源清單包含全部要求的直接證據。\n"
                 "本畫面未載入來源 PDF 或原始字形影像。核准後僅重新讀取本字形庫視窗。\n"
                 "若資料已改變，請求會被拒絕；必須重新檢視並確認。")
    raw = {"database_path": preview.database_path, "glyph_id": preview.glyph_id,
           "exact_identity": dict(record.identity.__dict__) if record else None,
           "fixed_request": request.service_arguments() if request else None,
           "source_evidence": [dict(row) for row in record.source_evidence] if record else []}
    return {"核准確認": overview, "直接來源證據": "\n\n".join(sources) or "沒有直接來源證據。",
            "詳細資訊": json.dumps(raw, ensure_ascii=False, indent=2)}


def outcome_text(session):
    messages = {
        "LOADING": "正在唯讀預覽；尚未提交。",
        "READY": "請檢視固定資料與直接來源證據，再明確確認。",
        "BLOCKED": "不符合提交條件；未由本視窗提交核准。",
        "SUBMITTING": "正在提交固定請求；請勿重複提交。關閉視窗不會取消已開始的交易。",
        "APPROVAL_COMMITTED": "核准已成功提交。這是固定請求的歷史結果，目前重用狀態以重新讀取為準。",
        "CONFLICT_COMMITTED": "核准未授予；後端已提交衝突隔離及適用的核准撤銷。這不是整筆 rollback。",
        "REJECTED": "請求過期或條件不符；未確認此請求的核准提交。請重新讀取並重新確認。",
        "UNKNOWN": "寫入結果尚不確定；不得假設 rollback 或再次提交。請唯讀查詢這份固定請求。",
    }
    return messages[session.state] + ("\n" + session.detail if session.detail else "")


class ApprovalDialog(tk.Toplevel):
    def __init__(self, master, session, *, on_committed):
        super().__init__(master)
        self.session = session
        self.on_committed = on_committed
        self.notified = False
        self.refresh_detail = ""
        self.display_error = ""
        self._displayed_request = None
        self._poll_id = None
        self.title("全域字形庫｜檢視並核准")
        self.geometry(f"{min(940, self.winfo_screenwidth() - 80)}x{min(740, self.winfo_screenheight() - 100)}")
        self.status_text = tk.StringVar(self)
        ttk.Label(self, textvariable=self.status_text, wraplength=850, justify="left").pack(fill="x", padx=12, pady=10)
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=12)
        self.texts = {}
        for name in ("核准確認", "直接來源證據", "詳細資訊"):
            frame = ttk.Frame(notebook)
            notebook.add(frame, text=name)
            text = tk.Text(frame, wrap="word", state="disabled", font=("Microsoft JhengHei UI", 11))
            scroll = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
            text.configure(yscrollcommand=scroll.set)
            scroll.pack(side="right", fill="y")
            text.pack(fill="both", expand=True)
            self.texts[name] = text
        buttons = ttk.Frame(self, padding=12)
        buttons.pack(fill="x")
        self.confirm_button = ttk.Button(buttons, text="確認核准這一筆", command=self.confirm, state="disabled")
        self.confirm_button.pack(side="right")
        self.cancel_button = ttk.Button(buttons, text="取消／關閉", command=self.destroy)
        self.cancel_button.pack(side="right", padx=10)
        self.query_button = ttk.Button(buttons, text="唯讀查詢固定請求結果", command=self.query)
        self.query_button.pack(side="left")
        self.bind("<Destroy>", self._on_destroy, add=True)
        self._render()
        self._poll_id = self.after(50, self._poll)

    def _render(self):
        # A ready backend request is not proof that its confirmation was shown.
        # Invalidate the display binding BEFORE touching any required field.
        self._displayed_request = None
        self.confirm_button.configure(state="disabled")
        try:
            self.query_button.configure(state="normal" if self.session.state == "UNKNOWN" and not self.session.busy else "disabled")
            if self.session.preview is not None:
                sections = preview_sections(self.session.preview)
                for name, widget in self.texts.items():
                    try:
                        widget.configure(state="normal")
                        widget.delete("1.0", "end")
                        widget.insert("1.0", sections[name])
                    finally:
                        widget.configure(state="disabled")
                self._displayed_request = self.session.preview.request
            self.display_error = ""
            if (self._displayed_request is not None and self.session.state == "READY"
                    and not self.session.busy and not self.session.attempted):
                self.confirm_button.configure(state="normal")
        except Exception as exc:
            self._displayed_request = None
            if not self.session.attempted:
                self.display_error = f"\n固定資料顯示失敗；尚未提交。請關閉後重新檢視並確認：{exc}"
            else:
                self.display_error = f"\n固定資料顯示失敗；提交結果以上述固定請求狀態為準：{exc}"
        self._update_status()

    def _update_status(self):
        self.status_text.set(outcome_text(self.session) + self.refresh_detail + self.display_error)

    def confirm(self):
        if (self.session.preview is None or self._displayed_request is None
                or self._displayed_request is not self.session.preview.request):
            return
        self.session.confirm()
        self._render()

    def query(self):
        self.session.query_outcome()
        self._render()

    def refresh_finished(self, error=""):
        committed = self.session.state in {"APPROVAL_COMMITTED", "CONFLICT_COMMITTED"}
        if error:
            prefix = "已提交結果保留，但畫面重新讀取失敗：" if committed else "畫面重新讀取失敗："
            self.refresh_detail = "\n" + prefix + error
        else:
            self.refresh_detail = "\n全域字形庫畫面已重新讀取。"
        self._update_status()

    def _poll(self):
        self._poll_id = None
        changed = self.session.drain()
        if changed:
            self._render()
        if self.session.state in {"APPROVAL_COMMITTED", "CONFLICT_COMMITTED"} and not self.notified:
            self.notified = True
            try:
                self.on_committed(self)
            except Exception as exc:
                self.refresh_finished(str(exc))
        self._poll_id = self.after(50, self._poll)

    def _on_destroy(self, event):
        if event.widget is self and self._poll_id is not None:
            self.after_cancel(self._poll_id)
            self._poll_id = None
