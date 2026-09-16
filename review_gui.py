from __future__ import annotations

import json
import re
import sys
import threading
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk


from occurrence_ledger import (
    CONFIRMATION_GATES, NON_TERMINAL_STATES, HARD_BLOCKING_STATES, EXCLUDED_STATES,
    canonical_bopomofo, infer_actual_status, infer_expected_status,
)
from standalone_proofread import (
    ManualActualPostApplyError,
    VERSION,
    apply_staged_manual_actual_corrections,
    actual_confirmation_snapshot,
    build_reusable_rule,
    build_manual_expected_event,
    friendly_state,
    json_load,
    json_load_strict,
    json_save,
    load_or_initialize_db,
    manual_actual_staging_summary,
    materialize_ledger,
    regenerate_report,
    save_reusable_expected_rule,
    stage_manual_actual_correction,
    validate_manifest_integrity,
    validate_output_artifact_hashes,
)
from actual_review import build_actual_group_for_entry
from review_display import ActionRows, OccurrencePreview, WrappedLabel, scrollable_entry, wrap_checkbutton


STATE_HELP = {
    "DIFFERENCE_PENDING_CONFIRMATION": "課本目前注音與應標注音不同，請確認是教材錯誤，或補充較高優先的正確讀音依據。",
    "RULE_CONFLICT": "程式找到互相衝突的讀音規則，請依原文與語境判定本句應標讀音。文字依據選填。",
    "EXPECTED_AMBIGUOUS": "目前有多個可能讀音，程式無法安全自動決定。",
    "EXPECTED_UNRESOLVED": "尚未找到足夠的正確讀音依據。",
    "REVIEW_PENDING": "目前證據不足，需要人工確認。",
    "ACTUAL_DECODE_ERROR": "程式可能把課本現標注音辨識錯誤。",
    "ACTUAL_UNRESOLVED": "課本現標注音尚未安全辨識。",
    "REGRESSION_BLOCKED": "內部回歸檢查未通過，這不是一般校對判定。",
    "SOURCE_INVALID": "校對資料來源檢查失敗，需先修復資料。",
    "DATA_INTEGRITY_ERROR": "工作資料完整性異常，需先修復資料。",
}


def screen_safe_window_size(screen_width: int, screen_height: int, desired_width: int, desired_height: int) -> tuple[int, int]:
    """Keep dialogs inside the usable screen area, including high-DPI Windows setups."""
    available_width = max(520, int(screen_width) - 80)
    available_height = max(420, int(screen_height) - 120)
    return min(int(desired_width), available_width), min(int(desired_height), available_height)


def apply_screen_safe_geometry(window: tk.Toplevel | tk.Tk, desired_width: int, desired_height: int, *, min_width: int = 620, min_height: int = 420):
    screen_width = max(1, int(window.winfo_screenwidth()))
    screen_height = max(1, int(window.winfo_screenheight()))
    width, height = screen_safe_window_size(screen_width, screen_height, desired_width, desired_height)
    x = max(0, (screen_width - width) // 2)
    y = max(0, (screen_height - height) // 3)
    window.geometry(f"{width}x{height}+{x}+{y}")
    window.minsize(min(min_width, width), min(min_height, height))


def create_scrollable_body(window: tk.Toplevel, *, fill_height=False) -> tuple[tk.Frame, tk.Canvas]:
    """Scrollable content area; callers can keep action buttons in a fixed footer."""
    holder = tk.Frame(window)
    holder.pack(fill="both", expand=True)
    canvas = tk.Canvas(holder, highlightthickness=0, borderwidth=0, yscrollincrement=24)
    scrollbar = ttk.Scrollbar(holder, orient="vertical", command=lambda *args: scroll_body(*args))
    canvas.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)

    content = tk.Frame(canvas)
    # Give content an explicit width from creation. Without this initial slot,
    # Tk sizes the unmapped canvas window from its wrapping children's requests
    # before the first canvas Configure can supply the viewport width.
    window_id = canvas.create_window((0, 0), window=content, anchor="nw", width=1)

    def update_scrollregion(_event=None):
        if fill_height:
            # Requested height is the minimum (summary + small preview slot),
            # not the last allocated height. Resizing cannot grow this loop.
            height = max(canvas.winfo_height(), content.winfo_reqheight())
            if int(float(canvas.itemcget(window_id, "height"))) != height:
                canvas.itemconfigure(window_id, height=height)
        canvas.configure(scrollregion=canvas.bbox("all"))

    def fit_content_width(event):
        canvas.itemconfigure(window_id, width=max(1, event.width))
        update_scrollregion()

    def reveal(widget, y0, y1):
        offset = widget.winfo_rooty() - canvas.winfo_rooty() + canvas.canvasy(0)
        top, bottom = offset + y0 - 20, offset + y1 + 20
        visible_top = canvas.canvasy(0)
        if top < visible_top or bottom > visible_top + canvas.winfo_height():
            total = max(1, canvas.bbox("all")[3])
            canvas.yview_moveto(max(0, (top + bottom - canvas.winfo_height()) / 2) / total)

    canvas.reveal = reveal

    def scroll_body(*args):
        pending = list(content.winfo_children())
        while pending:
            widget = pending.pop()
            if isinstance(widget, OccurrencePreview):
                widget.cancel_positioning()
            else:
                pending.extend(widget.winfo_children())
        canvas.yview(*args)

    def on_mousewheel(event):
        # The inner preview returns break while it can scroll. A wheel at its
        # boundary reaches here once, as do expanded samples with no inner bar.
        widget = event.widget
        while widget is not None and widget is not canvas:
            if isinstance(widget, (tk.Text, ttk.Combobox)):
                return
            widget = getattr(widget, "master", None)
        if widget is None:
            return
        delta = int(getattr(event, "delta", 0))
        if delta:
            scroll_body("scroll", -1 if delta > 0 else 1, "units")
            return "break"

    content.bind("<Configure>", update_scrollregion)
    canvas.bind("<Configure>", fit_content_width)
    if fill_height:
        def child_layout(event):
            widget = event.widget
            while widget is not None and widget is not content:
                widget = getattr(widget, "master", None)
            if widget is content:
                update_scrollregion()

        # An explicitly sized canvas window need not receive Configure when
        # its children's requested height changes (e.g. a longer summary).
        window.bind("<Configure>", child_layout, add="+")
    window.bind("<MouseWheel>", on_mousewheel)
    return content, canvas


def review_action_labels(state: str) -> tuple[str, str | None]:
    """Human-facing actions only. None means the button must not exist on screen."""
    if state == "DIFFERENCE_PENDING_CONFIRMATION":
        return "確認教材錯誤", "課本其實正確"
    if state == "RULE_CONFLICT":
        return "選擇正確讀音", None
    if state in {"EXPECTED_AMBIGUOUS", "EXPECTED_UNRESOLVED", "REVIEW_PENDING"}:
        return "補充正確讀音", None
    if state in {"ACTUAL_DECODE_ERROR", "ACTUAL_UNRESOLVED"}:
        return "實際注音辨識有誤／待建立", None
    return "暫時無法人工處理", None


LANE_LABELS = {"expected": "第一組：應標注音待確認", "actual": "第二組：課本目前注音待辨識", "other": "其他差異與待處理事項"}
LANE_ORDER = {lane: index for index, lane in enumerate(LANE_LABELS)}


def review_lane(entry):
    if entry.get("state") in HARD_BLOCKING_STATES or entry.get("state") in EXCLUDED_STATES:
        return "other"
    if infer_expected_status(entry) in {"UNRESOLVED", "AMBIGUOUS", "CONFLICT"}:
        return "expected"
    if infer_actual_status(entry) in {"UNRESOLVED", "DECODE_ERROR"}:
        return "actual"
    return "other"


def can_confirm_current_expected(entry):
    return (
        review_lane(entry) == "expected"
        and entry.get("state") in NON_TERMINAL_STATES
        and infer_actual_status(entry) == "RESOLVED"
        and bool(canonical_bopomofo(entry.get("actual")))
        and bool(str(entry.get("actual_evidence") or "").strip())
    )


class ExpectedDialog(tk.Toplevel):
    def __init__(self, parent, entry, title: str, *, wait: bool = True):
        super().__init__(parent)
        self.result = None
        self.entry = entry
        self.title(title)
        apply_screen_safe_geometry(self, 760, 660, min_width=600, min_height=420)
        self.resizable(True, True)
        self.transient(parent)
        self.grab_set()

        # Footer is packed first and stays visible even when the form itself must scroll.
        buttons = ActionRows(self, bd=1, relief="groove")
        buttons.pack(side="bottom", fill="x", padx=0, pady=0)
        cancel = tk.Button(buttons, text="取消", command=self.destroy)
        save = tk.Button(buttons, text="儲存本筆應標判定", command=self.submit, default="active")
        buttons.set_items([save, cancel])

        body, self.body_canvas = create_scrollable_body(self)

        source = entry.get("source_record") or {}
        phrase = str(source.get("局部詞境") or entry.get("context_evidence") or "").strip()
        sentence = str(source.get("所在行") or "").strip()

        intro = tk.Frame(body)
        intro.pack(fill="x", padx=16, pady=(14, 8))
        WrappedLabel(intro, text="請判定本句的應標讀音", font=("Microsoft JhengHei UI", 12, "bold")).pack(fill="x", anchor="w")
        WrappedLabel(
            intro,
            text="請看原文與語境後輸入應標注音。程式會保存本筆人工判定並重新比較；文字依據可以留白。只適用此位置，可重用規則須另外建立。",
            justify="left", wraplength=670, fg="#555555",
        ).pack(fill="x", anchor="w", pady=(4, 0))

        summary = tk.LabelFrame(body, text="目前位置")
        summary.pack(fill="x", padx=16, pady=6)
        text = (
            f"課本頁：{entry.get('printed_page', '')}　目標字：{entry.get('char', '')}\n"
            f"詞語／局部詞境：{phrase}\n"
            f"所在句：{sentence}\n"
            f"課本目前注音：{entry.get('actual', '')}"
        )
        WrappedLabel(summary, text=text, justify="left", anchor="w", wraplength=660).pack(fill="x", padx=10, pady=8)

        form = tk.Frame(body)
        form.pack(fill="x", padx=16, pady=4)
        self.expected = tk.StringVar()
        self.evidence = tk.StringVar()
        default_context = f"完整詞／局部詞境：{phrase}；所在句：{sentence}；目標字：{entry.get('char', '')}"
        self.context = tk.StringVar(value=default_context)
        self.reason = tk.StringVar()
        labels = [
            ("應標注音", self.expected, "多個可接受音請用 | 分隔；聲調可前置或後置，例如：˙ㄒㄧ 或 ㄒㄧ˙ 會視為同音"),
            ("依據（選填）", self.evidence, "可填實際參考來源或判斷理由，也可以留白；不需要填入代用或虛構來源"),
            ("詞語與位置", self.context, "由程式保留原文、所在句與目標字位置"),
            ("補充說明（可空白）", self.reason, "例如：另一個讀音屬不同義項，本句不適用"),
        ]
        for row, (label, var, hint) in enumerate(labels):
            WrappedLabel(form, text=label, width_fraction=0.25).grid(row=row * 2, column=0, sticky="ew", pady=(7, 1))
            scrollable_entry(form, var, readonly=var is self.context).grid(row=row * 2, column=1, sticky="ew", padx=(10, 0), pady=(7, 1))
            WrappedLabel(form, text=hint, width_fraction=0.75, fg="#666666", justify="left", wraplength=520).grid(row=row * 2 + 1, column=1, sticky="ew", padx=(10, 0))
        # Both columns derive their width from the full-width form, not from
        # the wrapping labels' own requested sizes.
        form.columnconfigure(0, weight=1, uniform="expected-form")
        form.columnconfigure(1, weight=3, uniform="expected-form")

        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Escape>", lambda _event: self.destroy())
        self.bind("<Control-Return>", lambda _event: self.submit())
        if wait:
            self.wait_window(self)

    def submit(self):
        expected_raw = self.expected.get().strip()
        evidence = self.evidence.get().strip()
        context = self.context.get().strip()
        if not expected_raw:
            messagebox.showerror("缺少讀音", "請輸入應標注音。", parent=self)
            return
        raw_readings = [item.strip() for item in re.split(r"[|；;]", expected_raw) if item.strip()]
        canonical_readings = [canonical_bopomofo(item) for item in raw_readings]
        if not raw_readings or any(not item for item in canonical_readings):
            messagebox.showerror(
                "注音格式不合法",
                "每個讀音只能有零個或一個聲調符號；聲調只能放在整個注音的最前或最後。\n"
                "例如：˙ㄒㄧ、ㄒㄧ˙、ㄒㄧˊ、ˊㄒㄧ都可接受；ˊㄒㄧˋ、ㄒˋㄧ不接受。",
                parent=self,
            )
            return
        # Deduplicate equivalent spellings after canonicalization while preserving order.
        expected = "|".join(dict.fromkeys(canonical_readings))
        if not context:
            messagebox.showerror("缺少詞境", "請保留完整詞語、所在句與目標位置證據。", parent=self)
            return
        self.result = {
            "expected_set": expected,
            "expected_evidence": evidence,
            "context_evidence": context,
            "resolution_reason": self.reason.get().strip(),
        }
        self.destroy()


class ActualReadingDialog(tk.Toplevel):
    """Human visual confirmation for the actual evidence chain only."""
    def __init__(self, parent, entry, group, output_dir: Path, *, wait: bool = True):
        super().__init__(parent)
        self.result = None
        self.entry = entry
        self.group = group
        self.output_dir = Path(output_dir)
        self.title("確認 PDF 原頁實際注音")
        apply_screen_safe_geometry(self, 980, 760, min_width=720, min_height=520)
        self.transient(parent)
        self.grab_set()
        self.previews = []
        self.sample_available = []

        footer = ActionRows(self, bd=1, relief="groove")
        footer.pack(side="bottom", fill="x")
        cancel = tk.Button(footer, text="取消", command=self.destroy)
        save = tk.Button(footer, text="暫存這筆 actual", command=self.submit)
        footer.set_items([save, cancel])
        body, self.body_canvas = create_scrollable_body(self)

        WrappedLabel(body, text="只看 PDF 原頁，確認實際印出的注音", font=("Microsoft JhengHei UI", 13, "bold")).pack(fill="x", anchor="w", padx=16, pady=(14,4))
        WrappedLabel(
            body,
            text=(
                "這個視窗不提供 expected／字典答案，請只依原頁可見字形輸入 actual。"
                "這次只會保存人工核對結果，不會立即重跑 PDF；你可以繼續確認下一筆，"
                "最後由主畫面的「套用 actual 修正」一次處理。"
            ),
            fg="#555555", justify="left", wraplength=900,
        ).pack(fill="x", anchor="w", padx=16, pady=(0,8))

        members = list(group.get("members") or [])
        target_id = str(entry.get("occurrence_id") or "")
        members.sort(key=lambda m: 0 if str(m.get("occurrence_id") or "") == target_id else 1)
        self.samples = members[:2]
        self.checked_vars = []
        for i, sample in enumerate(self.samples, 1):
            frame = tk.LabelFrame(body, text=f"樣本 {chr(64+i)}")
            frame.pack(fill="x", padx=16, pady=6)
            info = f"{sample.get('pdf_name','')}  課本頁 {sample.get('printed_page','')}  字：{sample.get('char','')}  occurrence：{sample.get('occurrence_id','')}"
            WrappedLabel(frame, text=info).pack(fill="x", padx=8, pady=(6, 2))
            preview = OccurrencePreview(frame, expand_content=True,
                                        on_locate=self.body_canvas.reveal if i == 1 else None)
            preview.pack(fill="x", padx=8, pady=5)
            available = preview.load(sample, padding=(95, 60), output_dir=self.output_dir)
            self.previews.append(preview)
            self.sample_available.append(available)
            var = tk.BooleanVar(value=(i == 1 and available))
            self.checked_vars.append(var)
            check = wrap_checkbutton(tk.Checkbutton(frame, text="我已直接核對這張 PDF 原頁", variable=var,
                                                   state="normal" if available else "disabled"))
            check.pack(fill="x", padx=8, pady=(2, 7))

            def unavailable(index=i - 1, variable=var, button=check):
                self.sample_available[index] = False
                variable.set(False)
                button.configure(state="disabled")

            preview.on_failure = unavailable

        form = tk.LabelFrame(body, text="實際注音")
        form.pack(fill="x", padx=16, pady=8)
        self.reading = tk.StringVar(value="")
        self.note = tk.StringVar(value="")
        WrappedLabel(form, text=f"程式目前 actual：{entry.get('actual') or '尚未辨識'}").grid(row=0,column=0,columnspan=2,sticky="ew",padx=8,pady=(8,4))
        WrappedLabel(form, text="原頁真正 actual：", width_fraction=0.25).grid(row=1,column=0,sticky="ew",padx=8,pady=4)
        scrollable_entry(form, self.reading).grid(row=1,column=1,sticky="ew",padx=8,pady=4)
        WrappedLabel(form, text="備註（可空白）：", width_fraction=0.25).grid(row=2,column=0,sticky="ew",padx=8,pady=4)
        scrollable_entry(form, self.note).grid(row=2,column=1,sticky="ew",padx=8,pady=(4,8))
        form.columnconfigure(0, weight=1, uniform="actual-form")
        form.columnconfigure(1, weight=3, uniform="actual-form")
        kind = str(group.get("kind") or "")
        if len(self.samples) > 1 and kind in {"TTF_GLYF_SHA256", "CFF_GLYPH_SHA256"}:
            WrappedLabel(body, text="若 A、B 都勾選且讀音相同，批次套用時這個 exact 字形可升格為跨位置重用真值；只勾 A 則只修正本位置。", fg="#555555").pack(fill="x", anchor="w", padx=18, pady=(0,10))
        else:
            WrappedLabel(body, text="目前沒有第二個可交叉核對的 exact glyph；本次會先暫存 occurrence-specific actual 修正。", fg="#555555").pack(fill="x", anchor="w", padx=18, pady=(0,10))
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        if wait:
            self.wait_window(self)

    def submit(self):
        reading = canonical_bopomofo(self.reading.get())
        if not reading:
            messagebox.showerror("注音格式不合法", "請輸入單一合法注音；聲調可放音節最前或最後。", parent=self)
            return
        checked = [str(sample.get("occurrence_id") or "") for sample, var, available in zip(self.samples, self.checked_vars, self.sample_available) if var.get() and available]
        target_id = str(self.entry.get("occurrence_id") or "")
        if target_id not in checked:
            messagebox.showerror("尚未核對目前位置", "樣本 A（目前位置）必須勾選已直接核對。", parent=self)
            return
        self.result = {"reading": reading, "checked_occurrence_ids": checked, "note": self.note.get().strip()}
        self.destroy()


class ConfirmationDialog(tk.Toplevel):
    def __init__(self, parent, entry, *, wait: bool = True):
        super().__init__(parent)
        self.result = None
        self.entry = entry
        self.title("確認教材錯誤")
        apply_screen_safe_geometry(self, 780, 680, min_width=620, min_height=440)
        self.resizable(True, True)
        self.transient(parent)
        self.grab_set()

        buttons = ActionRows(self, bd=1, relief="groove")
        buttons.pack(side="bottom", fill="x")
        cancel = tk.Button(buttons, text="取消", command=self.destroy)
        save = tk.Button(buttons, text="確認為教材錯誤", command=self.submit, default="active")
        buttons.set_items([save, cancel])

        body, self.body_canvas = create_scrollable_body(self)

        source = entry.get("source_record") or {}
        phrase = str(source.get("局部詞境") or entry.get("context_evidence") or "").strip()
        sentence = str(source.get("所在行") or "").strip()
        expected_text = " | ".join(entry.get("expected_set") or [])

        WrappedLabel(body, text="確認教材錯誤", font=("Microsoft JhengHei UI", 14, "bold")).pack(fill="x", anchor="w", padx=18, pady=(16, 6))
        summary = tk.LabelFrame(body, text="本筆證據")
        summary.pack(fill="x", padx=18, pady=6)
        lines = (
            f"課本頁：{entry.get('printed_page', '')}　詞語：{phrase}\n"
            f"所在句：{sentence}\n"
            f"課本現標：{entry.get('char', '')}　{entry.get('actual', '')}\n"
            f"應標：{entry.get('char', '')}　{expected_text}\n"
            f"應標文字依據：{entry.get('expected_evidence') or '未填寫（選填）'}"
        )
        if "manual_expected_decision" in entry:
            decision = entry["manual_expected_decision"]
            operation = "確認目前注音就是應標注音" if decision.get("operation") == "CONFIRM_CURRENT_AS_EXPECTED" else "人工輸入應標注音"
            lines += f"\n本筆人工判定：{operation}；{decision.get('decided_at', '')}"
        WrappedLabel(summary, text=lines, justify="left", anchor="w", wraplength=700).pack(fill="x", padx=10, pady=10)

        WrappedLabel(
            body,
            text="思考模式 2.5 的六閘門全部保留，但集中在同一個視窗。只有六項都確認才會正式列為教材錯誤。",
            justify="left", wraplength=700, fg="#555555",
        ).pack(fill="x", anchor="w", padx=18, pady=(4, 8))

        gate_texts = [
            "我已回原頁確認課本現標注音。",
            "我已確認應標注音的現行來源證據。",
            "課本現標與應標讀音的兩條證據鏈互相獨立。",
            "完整詞語、所在句與目標字位置都已確認。",
            "我已檢查公司規定／來源優先序，沒有更高優先的衝突規則。",
            "我本人確認這個現版差異是教材錯誤。",
        ]
        self.vars = []
        gate_frame = tk.Frame(body)
        gate_frame.pack(fill="x", padx=24, pady=4)
        for text in gate_texts:
            var = tk.BooleanVar(value=False)
            self.vars.append(var)
            wrap_checkbutton(tk.Checkbutton(gate_frame, text=text, variable=var)).pack(fill="x", anchor="w", pady=5)

        note_frame = tk.LabelFrame(body, text="補充說明（可空白）")
        note_frame.pack(fill="x", padx=18, pady=6)
        self.note = tk.Text(note_frame, height=4, wrap="word")
        self.note.pack(fill="x", padx=8, pady=8)

        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Escape>", lambda _event: self.destroy())
        self.bind("<Control-Return>", lambda _event: self.submit())
        if wait:
            self.wait_window(self)

    def submit(self):
        if not all(var.get() for var in self.vars):
            messagebox.showinfo("尚未完成", "六項確認必須全部勾選；不確定時請先取消並保留待處理。", parent=self)
            return
        source = self.entry.get("source_record") or {}
        phrase = str(source.get("局部詞境") or self.entry.get("context_evidence") or "").strip()
        auto_note = (
            f"P{self.entry.get('printed_page', '')}「{phrase}」之「{self.entry.get('char', '')}」現標"
            f"{self.entry.get('actual', '')}；應標{' | '.join(self.entry.get('expected_set') or [])}。"
            f"已完成思考模式2.5現版六閘門確認。"
        )
        extra = self.note.get("1.0", "end").strip()
        note = auto_note if not extra else f"{auto_note} {extra}"
        self.result = {gate: True for gate in CONFIRMATION_GATES}
        self.result["note"] = note
        self.destroy()


class ReviewApp:
    def __init__(self, root, output_dir: Path):
        self.root = root
        self.output_dir = output_dir
        self.manifest = json_load_strict(output_dir / "校對工作階段.json")
        validate_manifest_integrity(self.manifest)
        validate_output_artifact_hashes(self.manifest)
        self.db = load_or_initialize_db(output_dir)
        self.index = 0
        self.photo = None
        self.records = []
        self.tech_visible = False
        self.staging_summary = {}
        self.staged_member_occurrence_ids = set()
        self.staged_checked_occurrence_ids = set()
        self.deferred_items = set()
        self._save_in_progress = False

        root.title(f"注音校對－人工確認 v{VERSION}")
        apply_screen_safe_geometry(root, 1180, 850, min_width=760, min_height=520)

        actions = tk.Frame(root)
        actions.pack(side="bottom", fill="x", padx=12, pady=(4, 10))
        body, self.body_canvas = create_scrollable_body(root, fill_height=True)
        top = tk.Frame(body)
        top.pack(fill="x", padx=12, pady=(10, 4))
        self.status = WrappedLabel(top, text="", font=("Microsoft JhengHei UI", 11, "bold"))
        self.status.pack(fill="x")
        navigation = ActionRows(top)
        navigation.pack(fill="x")
        self.revisit_button = tk.Button(navigation, text="重新查看稍後處理（0）", command=self.revisit_deferred, state="disabled")
        navigation.set_items([tk.Button(navigation, text="上一筆", command=self.prev),
                              tk.Button(navigation, text="下一筆", command=self.next), self.revisit_button])
        self.navigation = navigation

        self.summary = tk.LabelFrame(body, text="這一筆要確認什麼")
        self.summary.pack(fill="x", padx=12, pady=5)
        self.summary_text = WrappedLabel(self.summary, text="", justify="left", anchor="w", wraplength=1120, font=("Microsoft JhengHei", 11))
        self.summary_text.pack(fill="x", padx=10, pady=8)

        self.image = OccurrencePreview(body, on_locate=self.body_canvas.reveal, on_failure=self._preview_failed)
        self.image.pack(fill="both", expand=True, padx=12, pady=6)

        self.tech_frame = tk.LabelFrame(body, text="技術資訊")
        self.tech_text = tk.Text(self.tech_frame, height=8, wrap="word", font=("Consolas", 9))
        self.tech_text.pack(fill="x", padx=8, pady=6)

        decision_actions = ActionRows(actions)
        decision_actions.pack(fill="x")
        self.decision_actions = decision_actions
        self.primary = tk.Button(decision_actions, text="", width=18, height=2)
        self.secondary = tk.Button(decision_actions, text="", width=18, height=2)
        self.later = tk.Button(decision_actions, text="稍後處理", width=14, height=2, command=self.defer_current)

        self.more_button = tk.Menubutton(decision_actions, text="更多…", width=12, height=2, relief="raised")
        self.more_menu = tk.Menu(self.more_button, tearoff=False)
        self.more_button.config(menu=self.more_menu)
        self.more_menu.add_command(label="此處不需校對…", command=self.exclude)
        self.more_menu.add_command(label="實際注音辨識有誤…", command=self.correct_actual)
        self.more_menu.add_command(label="撤銷本筆人工判定", command=self.clear)
        self.more_menu.add_command(label="另行建立上筆應標可重用規則…", command=self.create_reusable_expected_rule)
        self.more_menu.add_separator()
        self.more_menu.add_command(label="查看／隱藏技術資訊", command=self.toggle_tech)
        self.more_menu.add_command(label="更新 Excel 報告", command=self.report)

        batch_actions = ActionRows(actions)
        self.batch_actions = batch_actions
        batch_actions.pack(fill="x", pady=(6, 0))
        self.apply_actual_button = tk.Button(
            batch_actions,
            text="套用 actual 修正（0）",
            width=24,
            height=2,
            state="disabled",
            command=self.apply_staged_actuals,
        )
        batch_actions.set_items([self.apply_actual_button])
        WrappedLabel(
            actions,
            text="先逐筆暫存原頁 actual；確認完後再一次套用與增量更新。",
            fg="#555555",
            anchor="w",
        ).pack(fill="x")

        self.reload_records()
        self.show()
        if not self.records:
            title, detail, _primary_text = self._empty_actionable_state()
            messagebox.showinfo(title, detail, parent=self.root)

    def reload_records(self):
        self.reload_staging_summary()
        ledger = materialize_ledger(self.manifest, self.db)
        self._set_actionable_records_from_ledger(ledger)

    def _set_actionable_records_from_ledger(self, ledger):
        previous = list(getattr(self, "records", []))
        index = int(getattr(self, "index", 0))
        old = previous[index] if 0 <= index < len(previous) else None
        lane = review_lane(old) if old else None
        same_character = [item for item in previous[index:] + list(reversed(previous[:index]))
                          if old and review_lane(item) == lane and item.get("char") == old.get("char")]
        following = [(review_lane(item), str(item.get("occurrence_id") or ""))
                     for item in same_character + previous[index:] if review_lane(item) == lane]
        checked = set(getattr(self, "staged_checked_occurrence_ids", set()))
        deferred = set(getattr(self, "deferred_items", set()))
        pending = [item for item in ledger if item.get("state") in NON_TERMINAL_STATES
                   and not (review_lane(item) == "actual" and str(item.get("occurrence_id") or "") in checked)]
        live_keys = {(review_lane(item), str(item.get("occurrence_id") or "")) for item in pending}
        self.deferred_items = deferred & live_keys
        self.pending_lane_counts = Counter(review_lane(item) for item in pending)
        pdf_order = {str(item.get("pdf_name") or ""): n for n, item in enumerate(getattr(self, "manifest", {}).get("pdfs", []))}

        def number(value):
            try:
                return float(value or 0)
            except (TypeError, ValueError):
                return 0.0

        def source_order(item):
            pdf = str(item.get("pdf_name") or "")
            return (pdf_order.get(pdf, len(pdf_order)), pdf,
                    number(item.get("physical_page")), number(item.get("source_row_number")),
                    number(item.get("y0")), number(item.get("x0")), str(item.get("occurrence_id") or ""))

        # Anchor groups to the full original roster, including completed/deferred
        # rows. Refreshing the remaining queue must not move a character group.
        if not hasattr(self, "_character_order"):
            self._character_order = {}
            for item in sorted(ledger, key=source_order):
                self._character_order.setdefault(str(item.get("char") or ""), len(self._character_order))

        def order(item):
            item_lane = review_lane(item)
            char_rank = self._character_order.get(str(item.get("char") or ""), len(self._character_order)) if item_lane != "other" else 0
            return (LANE_ORDER[item_lane], char_rank, source_order(item))

        self.records = sorted([item for item in pending
                               if (review_lane(item), str(item.get("occurrence_id") or "")) not in self.deferred_items], key=order)
        if hasattr(self, "revisit_button"):
            count = len(self.deferred_items)
            self.revisit_button.config(text=f"重新查看稍後處理（{count}）", state="normal" if count else "disabled")
            if hasattr(self, "navigation"):
                self.navigation.refresh()
        indexes = {(review_lane(item), str(item.get("occurrence_id") or "")): n for n, item in enumerate(self.records)}
        for key in following:
            if key in indexes:
                self.index = indexes[key]
                return
        # Finish the current lane before a just-saved row moves to another lane.
        same_lane = [n for n, item in enumerate(self.records) if review_lane(item) == lane]
        self.index = min(same_lane, key=lambda n: abs(n - index)) if same_lane else 0

    def defer_current(self):
        entry = self.current()
        if not entry:
            return
        self.deferred_items = set(getattr(self, "deferred_items", set()))
        self.deferred_items.add((review_lane(entry), str(entry.get("occurrence_id") or "")))
        self.reload_records()
        self.show()

    def revisit_deferred(self):
        self.deferred_items = set()
        self.records = []
        self.index = 0
        self.reload_records()
        self.show()

    def reload_staging_summary(self):
        summary = manual_actual_staging_summary(self.output_dir)
        self.staging_summary = dict(summary)
        self.staged_member_occurrence_ids = set(summary.get("staged_member_occurrence_ids") or [])
        self.staged_checked_occurrence_ids = set(summary.get("staged_checked_occurrence_ids") or [])
        count = int(summary.get("staged_group_count") or 0)
        self.apply_actual_button.config(
            text=f"套用 actual 修正（{count}）",
            state="normal" if count > 0 else "disabled",
        )
        if hasattr(self, "batch_actions"):
            self.batch_actions.refresh()
        return self.staging_summary

    def _empty_actionable_state(self):
        deferred = len(getattr(self, "deferred_items", set()))
        if deferred:
            return (
                "這輪待辦已走完，仍有稍後處理項目",
                f"仍有 {deferred} 筆待處理，未視為完成。請按「重新查看稍後處理（{deferred}）」。actual 暫存仍須另行批次套用。",
                "請重新查看稍後處理",
            )
        count = int(self.staging_summary.get("staged_group_count") or 0)
        if count > 0:
            return (
                "目前沒有尚未暫存的待人工項目",
                f"仍有 {count} 組 actual 修正等待批次套用，"
                f"請按「套用 actual 修正（{count}）」。",
                "請使用批次套用按鈕",
            )
        return (
            "目前沒有待人工項目",
            "請回主畫面更新報告；是否全冊完成仍由完整 completion gate 判定。",
            "沒有待處理項目",
        )

    def current(self):
        return self.records[self.index] if self.records else None

    def save_event(self, entry, event):
        if getattr(self, "_save_in_progress", False):
            return False
        self._save_in_progress = True
        self._last_event_saved = False
        try:
            review_id = entry["review_id"]
            staged = json.loads(json.dumps(self.db, ensure_ascii=False))
            staged.setdefault("events", {})[review_id] = event
            staged_ledger = materialize_ledger(self.manifest, staged)
            resolved_entry = next((item for item in staged_ledger if item.get("review_id") == review_id), None)
            if resolved_entry is None:
                raise ValueError("儲存後找不到本筆 review_id，已取消寫入。")
            json_save(self.output_dir / "人工判定資料庫.json", staged)
        except Exception as exc:
            messagebox.showerror("無法儲存人工判定", str(exc), parent=self.root)
            return False
        finally:
            self._save_in_progress = False
        self.db = staged
        self._last_event_saved = True
        if "manual_expected_decision" in event:
            self.last_saved_expected = resolved_entry
        self._set_actionable_records_from_ledger(staged_ledger)
        self.show()
        return resolved_entry.get("state") not in NON_TERMINAL_STATES

    def prev(self):
        self.index = max(0, self.index - 1)
        self.show()

    def next(self):
        if not self.records:
            return
        self.index = min(len(self.records) - 1, self.index + 1)
        self.show()

    def confirm_current_expected(self, review_id=None):
        if time.monotonic() < getattr(self, "_shortcut_cooldown_until", 0):
            return
        entry = self.current()
        if not entry or (review_id is not None and entry.get("review_id") != review_id):
            return
        if not can_confirm_current_expected(entry):
            return
        if getattr(self, "_rendered_review_id", None) != entry.get("review_id"):
            return
        try:
            event = build_manual_expected_event(entry, operation="CONFIRM_CURRENT_AS_EXPECTED")
        except Exception as exc:
            messagebox.showerror("無法儲存人工判定", str(exc), parent=self.root)
            return
        self.save_event(entry, event)
        if getattr(self, "_last_event_saved", False):
            self._shortcut_cooldown_until = time.monotonic() + 0.5

    def resolve_expected(self):
        entry = self.current()
        if not entry or review_lane(entry) not in {"expected", "other"} or entry.get("state") in HARD_BLOCKING_STATES:
            return
        dialog = ExpectedDialog(self.root, entry, "輸入其他應標注音")
        if not dialog.result:
            return
        try:
            event = build_manual_expected_event(
                entry, operation="ENTER_EXPECTED",
                expected_set=dialog.result.get("expected_set", ""),
                rationale=dialog.result.get("expected_evidence", ""),
                note=dialog.result.get("resolution_reason", ""),
            )
        except Exception as exc:
            messagebox.showerror("無法儲存人工判定", str(exc), parent=self.root)
            return
        self.save_event(entry, event)
        if getattr(self, "_last_event_saved", False):
            updated = next((item for item in self.records if item.get("review_id") == entry.get("review_id")), None)
            if updated and updated.get("state") == "DIFFERENCE_PENDING_CONFIRMATION":
                messagebox.showwarning(
                    "仍有注音差異，尚未確認教材錯誤",
                    f"本筆應標判定已保存。課本目前注音：{updated.get('actual')}；"
                    f"應標注音：{' | '.join(updated.get('expected_set') or [])}。\\n"
                    "仍保留差異待確認；需要時另按「確認教材錯誤」完成六閘門。",
                    parent=self.root,
                )

    def create_reusable_expected_rule(self):
        # A separate explicit operation, available even after the saved row left
        # the current lane. Its stronger requirements cannot prevent item saving.
        entry = getattr(self, "last_saved_expected", None)
        if not entry:
            messagebox.showinfo("請先保存本筆判定", "保存應標判定後，可另行建立可重用規則。", parent=self.root)
            return
        phrase = simpledialog.askstring("另行建立可重用規則", "請輸入適用的完整詞：", parent=self.root)
        if not phrase:
            return
        evidence = simpledialog.askstring("可重用規則依據", "跨位置規則需提供實際來源依據：", parent=self.root)
        if evidence is None:
            return
        try:
            rule = build_reusable_rule(entry, phrase=phrase, expected_set=entry["expected_set"], evidence=evidence)
            save_reusable_expected_rule(rule)
        except Exception as exc:
            messagebox.showerror("可重用規則未儲存", f"{exc}\n本筆已保存的人工判定仍保留。", parent=self.root)
            return
        messagebox.showinfo("已儲存可重用規則", "本規則會在更新報告及未來校對時套用相同完整詞與目標位置。", parent=self.root)

    def _confirm_difference_entry(self, entry):
        dialog = ConfirmationDialog(self.root, entry)
        if not dialog.result:
            return
        self.save_event(entry, {
            "action": "確認現版差異",
            **dialog.result,
            # Preserve the independent expected evidence in the same authoritative
            # event.  This is required when the current mismatch was created by a
            # prior GPT/GUI expected-evidence event, because the DB stores one event
            # per review_id and this confirmation replaces that earlier event.
            "expected_set": list(entry.get("expected_set") or []),
            "expected_evidence": str(entry.get("expected_evidence") or ""),
            "context_evidence": str(entry.get("context_evidence") or ""),
            "expected_resolution_source": str(entry.get("expected_resolution_source") or entry.get("expected_evidence") or ""),
            "expected_resolution_reason": str(entry.get("expected_resolution_reason") or ""),
            "expected_resolution_note": str(entry.get("note") or ""),
            "confirmation_actual_snapshot": actual_confirmation_snapshot(entry),
            **({"manual_expected_decision": entry["manual_expected_decision"]} if "manual_expected_decision" in entry else {}),
            "source": "人工 GUI 現版六閘門",
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        })

    def confirm_difference(self):
        entry = self.current()
        if not entry:
            return
        if entry.get("state") != "DIFFERENCE_PENDING_CONFIRMATION":
            messagebox.showinfo("這筆不是教材差異", "只有課本現標與應標已明確不同的項目才可確認教材錯誤。")
            return
        self._confirm_difference_entry(entry)

    def exclude(self):
        entry = self.current()
        if not entry:
            return
        reason = simpledialog.askstring("此處不需校對", "為什麼這個位置不屬於本次注音校對？", parent=self.root)
        if not reason:
            return
        evidence = simpledialog.askstring("原頁證據", "請輸入原頁／結構／座標等可稽核證據：", parent=self.root)
        if not evidence:
            messagebox.showerror("缺少證據", "排除必須有原頁或結構證據。")
            return
        self.save_event(entry, {
            "action": "確認非校對範圍",
            "exclusion_reason": reason,
            "exclusion_evidence": evidence,
            "note": "",
            "source": "人工 GUI 排除證據",
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        })

    def correct_actual(self):
        entry = self.current()
        if not entry:
            return
        try:
            full_ledger = materialize_ledger(self.manifest, self.db)
            group = build_actual_group_for_entry(full_ledger, entry)
        except Exception as exc:
            messagebox.showerror("無法建立 actual 核對群組", str(exc), parent=self.root)
            return
        dialog = ActualReadingDialog(self.root, entry, group, self.output_dir)
        if not dialog.result:
            return
        try:
            stage_manual_actual_correction(
                self.output_dir,
                str(entry.get("review_id") or ""),
                dialog.result["reading"],
                list(dialog.result.get("checked_occurrence_ids") or []),
                dialog.result.get("note", ""),
            )
        except Exception as exc:
            messagebox.showerror("actual 暫存失敗", str(exc), parent=self.root)
            return
        try:
            self.reload_records()
            self.show()
        except Exception as exc:
            messagebox.showwarning(
                "actual 已暫存，但待人工清單更新失敗",
                f"人工核對結果已寫入 durable staging；重新開啟畫面仍可恢復。\n{exc}",
                parent=self.root,
            )
            return
        fresh_summary = self.staging_summary
        count = int(fresh_summary.get("staged_group_count") or 0)
        messagebox.showinfo(
            "actual 已暫存",
            "這筆已暫存，尚未正式寫入 actual evidence，也尚未重新解碼 PDF。\n"
            f"目前共有 {count} 組等待套用；你可以繼續確認下一筆，最後按「套用 actual 修正」。",
            parent=self.root,
        )

    def apply_staged_actuals(self):
        try:
            summary = self.reload_staging_summary()
        except Exception as exc:
            messagebox.showerror("無法讀取 actual 暫存清單", str(exc), parent=self.root)
            return
        count = int(summary.get("staged_group_count") or 0)
        if count <= 0:
            return
        if not messagebox.askyesno(
            "套用 actual 修正",
            f"目前有 {count} 組 actual 人工確認等待套用。\n"
            "套用後會正式寫入 actual 證據，並一次增量重新處理受影響 PDF。\n"
            "是否繼續？",
            parent=self.root,
        ):
            return

        progress = tk.Toplevel(self.root)
        progress.title("批次套用 actual")
        apply_screen_safe_geometry(progress, 560, 210, min_width=440, min_height=170)
        progress.transient(self.root)
        progress.grab_set()
        # Reserve the progress indicator before the resizable explanation.
        bar = ttk.Progressbar(progress, mode="indeterminate", length=400)
        bar.pack(side="bottom", fill="x", padx=18, pady=12)
        WrappedLabel(
            progress,
            text=f"正在一次套用 {count} 組 actual 修正",
            font=("Microsoft JhengHei UI", 11, "bold"),
        ).pack(fill="x", padx=18, pady=(18, 8))
        WrappedLabel(
            progress,
            text="未受影響 PDF 將直接沿用 cache；請勿關閉程式。",
            fg="#555555",
        ).pack(fill="x", padx=18, pady=4)
        bar.start(12)
        old_index = self.index

        def worker():
            try:
                result = apply_staged_manual_actual_corrections(self.output_dir)
                self.root.after(0, lambda: done(result, None))
            except Exception as exc:
                self.root.after(0, lambda exc=exc: done(None, exc))

        def done(result, error):
            try:
                bar.stop()
                progress.grab_release()
                progress.destroy()
            except Exception:
                pass
            if error:
                try:
                    self.reload_staging_summary()
                    self.show()
                except Exception:
                    pass
                if isinstance(error, ManualActualPostApplyError):
                    title = "actual evidence 已套用，但後續未完成"
                else:
                    title = "套用 actual 修正失敗"
                messagebox.showerror(title, str(error), parent=self.root)
                return
            try:
                self.manifest = json_load_strict(self.output_dir / "校對工作階段.json")
                validate_manifest_integrity(self.manifest)
                validate_output_artifact_hashes(self.manifest)
                self.db = load_or_initialize_db(self.output_dir)
                self.index = old_index
                self.reload_records()
                self.show()
            except Exception as exc:
                messagebox.showerror(
                    "actual 已套用，但畫面重新載入失敗",
                    f"batch、refresh 與 postcondition 已完成；請重新開啟人工校對畫面。\n{exc}",
                    parent=self.root,
                )
                return

            result = dict(result or {})
            applied_count = int(result.get("applied_group_count") or 0)
            affected_pdfs = list(result.get("affected_pdf_names") or [])
            quarantined = list(result.get("quarantined_group_ids") or [])
            cleared = int(result.get("cleared_actual_dependent_event_count") or 0)
            pending_counts = Counter(str(item.get("state") or "") for item in self.records)
            pending_text = "、".join(
                f"{friendly_state(state)} {number} 筆"
                for state, number in sorted(pending_counts.items())
            ) or "0 筆"
            pdf_text = "、".join(affected_pdfs) or "無"
            lines = [
                f"已一次套用 {applied_count} 組 actual 修正。",
                f"受影響 PDF：{len(affected_pdfs)} 個（{pdf_text}）。",
                f"quarantined group：{len(quarantined)} 組。",
                f"因 actual 更新而撤銷的舊差異 confirmation：{cleared} 筆。",
                f"current pending 狀態已重新載入：{pending_text}。",
            ]
            if quarantined:
                lines.append(
                    f"有 {len(quarantined)} 組 exact glyph 因不同位置出現互斥人工讀音，"
                    "已依既有規則進入隔離，不會做全域泛化。"
                )
            lines.append(
                "工作階段與候選狀態已更新；Excel 最終報告可按「更新 Excel 報告」再產生。"
            )
            messagebox.showinfo("actual 批次套用完成", "\n".join(lines), parent=self.root)

        threading.Thread(target=worker, daemon=True).start()

    def actual_instruction(self):
        # Backward-compatible alias for old callbacks/hotkeys.
        return self.correct_actual()

    def clear(self):
        entry = self.current()
        if not entry:
            return
        if entry["review_id"] not in self.db.get("events", {}):
            messagebox.showinfo("沒有可撤銷的判定", "這一筆目前沒有人工事件。")
            return
        if not messagebox.askyesno("撤銷本筆判定", "確定撤銷這一筆的人工作業，回到程式原本狀態？"):
            return
        self.db.setdefault("events", {}).pop(entry["review_id"], None)
        json_save(self.output_dir / "人工判定資料庫.json", self.db)
        self.reload_records()
        self.show()

    def report(self):
        try:
            path = regenerate_report(self.output_dir)
            status = json_load(self.output_dir / "pipeline_status.json", {}).get("status", "")
            messagebox.showinfo("報告已更新", f"{friendly_state(status) if status else '報告已更新'}\n{path}")
            self.manifest = json_load_strict(self.output_dir / "校對工作階段.json")
            self.db = load_or_initialize_db(self.output_dir)
            self.reload_records()
            self.show()
        except Exception as exc:
            messagebox.showerror("無法更新報告", str(exc))

    def toggle_tech(self):
        self.tech_visible = not self.tech_visible
        if self.tech_visible:
            self.tech_frame.pack(fill="x", padx=12, pady=(0, 6))
        else:
            self.tech_frame.pack_forget()

    def configure_actions(self, entry):
        state = str(entry.get("state") or "")
        lane = review_lane(entry)
        primary_text, secondary_text = review_action_labels(state)
        primary_command = secondary_command = None
        if lane == "expected":
            primary_text = "輸入其他應標注音"
            primary_command = self.resolve_expected
            if can_confirm_current_expected(entry):
                primary_text = "確認目前注音就是應標注音"
                primary_command = lambda rid=entry["review_id"]: self.confirm_current_expected(rid)
                secondary_text = "輸入其他應標注音"
                secondary_command = self.resolve_expected
        elif lane == "actual":
            primary_command = self.correct_actual
        elif state == "DIFFERENCE_PENDING_CONFIRMATION":
            primary_command = self.confirm_difference
            secondary_command = self.resolve_expected
        elif state == "REVIEW_PENDING":
            primary_command = self.resolve_expected
        self.primary.config(text=primary_text, width=0, state="normal" if primary_command else "disabled", command=primary_command or (lambda: None))
        buttons = [self.primary]
        if secondary_text and secondary_command:
            self.secondary.config(text=secondary_text, state="normal", command=secondary_command)
            buttons.append(self.secondary)
        if hasattr(self, "decision_actions"):
            self.decision_actions.set_items(buttons + [self.later, self.more_button])

    def show(self):
        entry = self.current()
        if not entry:
            title, detail, primary_text = self._empty_actionable_state()
            self.status.config(text=title)
            self.summary_text.config(text=detail)
            self._rendered_review_id = None
            self.image.clear()
            self.tech_text.delete("1.0", "end")
            self.primary.config(text=primary_text, state="disabled")
            if hasattr(self, "decision_actions"):
                self.decision_actions.set_items([self.primary, self.later, self.more_button])
            return

        source = entry.get("source_record") or {}
        state = str(entry.get("state") or "")
        phrase = str(source.get("局部詞境") or entry.get("context_evidence") or "").strip()
        sentence = str(source.get("所在行") or phrase).strip()
        expected_text = " | ".join(entry.get("expected_set") or []) or "尚未確定"
        lane = review_lane(entry)
        if lane == "actual":
            expected_text = "（actual 獨立辨識階段不顯示）"
        group_count = sum(review_lane(item) == lane and item.get("char") == entry.get("char") for item in self.records)

        is_staged = str(entry.get("occurrence_id") or "") in self.staged_checked_occurrence_ids
        staged_status = "｜actual 已暫存，等待批次套用" if is_staged else ""
        lane_count = sum(review_lane(item) == lane for item in self.records)
        deferred = len(getattr(self, "deferred_items", set()))
        self.status.config(text=f"{LANE_LABELS[lane]}｜本組剩餘 {lane_count} 筆｜稍後 {deferred} 筆{staged_status}")
        help_text = STATE_HELP.get(state, "這一筆需要人工處理。")
        if lane == "expected":
            help_text = "請先依原文與語境判定應標注音；目前注音待辨識時，儲存後再進第二組。依據選填。"
        staged_line = "\nactual 狀態：已暫存人工核對結果，等待批次套用。" if is_staged else ""
        summary = (
            f"課本頁：{entry.get('printed_page', '')}　　目標字：{entry.get('char', '')}　　本組此字剩餘 {group_count} 筆（含本筆，不含稍後處理）\n"
            f"詞語／局部詞境：{phrase}\n"
            f"所在句：{sentence}\n"
            f"課本目前注音：{entry.get('actual', '') or '尚未辨識'}　　應標注音：{expected_text}\n"
            f"原因：{help_text}{staged_line}"
        )
        self.summary_text.config(text=summary)

        tech_lines = [
            f"state: {state}",
            f"occurrence_id: {entry.get('occurrence_id', '')}",
            f"review_id: {entry.get('review_id', '')}",
            f"PDF: {entry.get('pdf_name', '')}",
            f"實體頁碼: {entry.get('physical_page', '')}",
            f"actual evidence: {entry.get('actual_evidence', '')}",
            f"expected evidence: {entry.get('expected_evidence', '')}",
            f"context evidence: {entry.get('context_evidence', '')}",
            f"source view: {entry.get('source_view', '')}",
        ]
        self.tech_text.delete("1.0", "end")
        self.tech_text.insert("1.0", "\n".join(tech_lines))
        self.configure_actions(entry)
        self.render(entry)

    def render(self, entry):
        self._rendered_review_id = None
        if self.image.load(entry):
            self._rendered_review_id = entry.get("review_id")
        elif can_confirm_current_expected(entry):
            self.primary.config(state="disabled")

    def _preview_failed(self):
        self._rendered_review_id = None
        entry = self.current()
        if entry and can_confirm_current_expected(entry):
            self.primary.config(state="disabled")


def main():
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if output is None:
        root = tk.Tk()
        root.withdraw()
        directory = filedialog.askdirectory(title="選擇校對專案資料夾")
        root.destroy()
        if not directory:
            return 0
        output = Path(directory)
    root = tk.Tk()
    ReviewApp(root, output)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
