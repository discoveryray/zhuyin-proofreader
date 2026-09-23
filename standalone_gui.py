from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from occurrence_ledger import ALL_STATES, NON_TERMINAL_STATES, EXCLUDED_STATES
from review_display import ActionRows, WrappedLabel, scrollable_entry

VERSION = "5.7.0"


STATUS_TEXT = {
    "PROCESSING_FINISHED": "尚有項目待處理",
    "PROOFREAD_COMPLETE": "全冊校對完成",
    "PIPELINE_BLOCKED": "處理被阻擋",
}


def normalize_existing_project_folder(folder: Path) -> Path:
    """Correct only unambiguous one-level project-folder selection mistakes."""
    folder = Path(folder)
    session_name = "校對工作階段.json"
    if (folder / session_name).exists():
        return folder
    candidates = []
    if folder.name == "注音校對_輸出" and (folder.parent / session_name).exists():
        candidates.append(folder.parent)
    child = folder / "注音校對_輸出"
    if (child / session_name).exists():
        candidates.append(child)
    unique = []
    seen = set()
    for candidate in candidates:
        key = str(candidate)
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique[0] if len(unique) == 1 else folder


# Presentation categories only; completion is always the producer's gate result.
_PROGRESS_CAUSES = {
    "目前注音尚未辨識的文字": {"actual_coverage_100", "decode_error_zero"},
    "應標注音尚待確認的文字": {"expected_coverage_100", "expected_unresolved_zero", "expected_ambiguous_zero", "rule_conflict_zero"},
    "待逐筆確認的項目": {"pending_zero", "all_in_scope_terminal"},
    "來源或工作資料檢查異常": {"source_validation", "runtime_fatal_error_zero", "ledger_reconciliation", "data_integrity_error_zero", "source_invalid_zero"},
    "尚未找到本次需校對的文字": {"in_scope_occurrence_count_positive"},
    "必要回歸檢查尚未通過": {"regression_gate"},
}
_KNOWN_GATES = set().union(*_PROGRESS_CAUSES.values())
_UNKNOWN_PROGRESS = "目前無法確認進度。請按『重新讀取專案狀態』，並查看『進度詳細資訊』；必要時使用『修復／更新報告』。"


def summarize_project_status(data, *, has_session=False):
    if not isinstance(data, dict):
        return _UNKNOWN_PROGRESS
    status = data.get("status")
    if status == "PROCESSING":
        phases = {"1/3": "讀取課本目前注音", "2/3": "核對語境與應標注音", "3/3": "整理報告與校對清單"}
        phase = data.get("phase")
        stage = f" [{phase}] {phases[phase]}" if isinstance(phase, str) and phase in phases else ""
        return f"正在處理教材{stage}。請等候處理結束；詳細紀錄可從『更多…』查看。" + ("首次分析中，工作階段尚未建立。" if not has_session else "")
    if status == "PIPELINE_BLOCKED":
        return "處理被阻擋：來源或工作資料發生異常，校對尚未完成。請查看『進度詳細資訊』，修正來源後按『修復／更新報告』。" + ("工作階段尚未建立。" if not has_session else "")
    if status not in {"PROCESSING_FINISHED", "PROOFREAD_COMPLETE"}:
        return _UNKNOWN_PROGRESS
    gate = data.get("completion_gate")
    if not isinstance(gate, dict):
        return _UNKNOWN_PROGRESS
    counts = gate.get("state_counts")
    failed = gate.get("failed_gates", [])
    if (not isinstance(counts, dict) or not counts or not set(counts) <= ALL_STATES
            or any(type(value) is not int or value < 0 for value in counts.values())
            or not isinstance(failed, list) or any(not isinstance(key, str) or key not in _KNOWN_GATES for key in failed)):
        return _UNKNOWN_PROGRESS
    hard = gate.get("hard_gates")
    if hard is not None and (not isinstance(hard, dict) or set(hard) != _KNOWN_GATES
                             or any(type(value) is not bool for value in hard.values())
                             or set(failed) != {key for key, value in hard.items() if not value}):
        return _UNKNOWN_PROGRESS
    if status == "PROOFREAD_COMPLETE":
        in_scope = sum(value for key, value in counts.items() if key not in EXCLUDED_STATES)
        if (has_session and gate.get("status") == status and gate.get("complete") is True and failed == []
                and isinstance(hard, dict) and set(hard) == _KNOWN_GATES
                and all(value is True for value in hard.values())
                and in_scope > 0
                and all(type(gate.get(key)) is int and gate[key] == in_scope
                        for key in ("in_scope_total", "actual_covered", "expected_covered"))
                and not any(counts.get(key, 0) for key in NON_TERMINAL_STATES)):
            return "全冊校對完成：現有完成檢查全部通過。請按『查看報告』檢視結果。"
        return _UNKNOWN_PROGRESS
    if gate.get("complete") is True or gate.get("status", status) != status:
        return _UNKNOWN_PROGRESS
    # State counts partition occurrences. Dimension/gate counts overlap and must
    # never be added to this number (one row may need both actual and expected).
    pending = sum(value for key, value in counts.items() if key in NON_TERMINAL_STATES)
    failed_set = set(failed)
    causes = [label for label, gates in _PROGRESS_CAUSES.items() if failed_set & gates
              and label != "待逐筆確認的項目"]
    if not causes and pending:
        causes = ["需逐筆核對的注音"]
    text = "本次處理已結束，校對尚未完成。"
    if pending:
        text += f"還有待處理 {pending} 筆" + ("，包含" + "、".join(causes) if causes else "") + "。"
        text += "請按『繼續校對』處理。"
    else:
        text += ("、".join(causes) + "。") if causes else "仍需確認完整檢查結果。"
        text += "請按『修復／更新報告』，並查看『進度詳細資訊』。"
    return text


def project_status_details(folder: Path) -> tuple[str, str]:
    folder = Path(folder)
    status_path = folder / "pipeline_status.json"
    session_path = folder / "校對工作階段.json"
    try:
        has_session = session_path.is_file()
        if status_path.exists():
            raw = status_path.read_text(encoding="utf-8")
            try:
                data = json.loads(raw)
            except Exception as exc:
                return _UNKNOWN_PROGRESS, f"{status_path}\n{exc}\n\n{raw}"
            diagnostics = f"{status_path}\n\n{json.dumps(data, ensure_ascii=False, indent=2)}"
            if not has_session:
                diagnostics += f"\n\n缺少工作階段檔案：{session_path}"
            return summarize_project_status(data, has_session=has_session), diagnostics
        if not has_session:
            return "這是新的校對專案資料夾；尚未建立工作階段。請選擇教材後按『開始新校對』。", str(folder)
        return _UNKNOWN_PROGRESS, f"已有工作階段，但找不到 {status_path}"
    except Exception as exc:
        return _UNKNOWN_PROGRESS, f"{status_path}\n{exc}"


def project_status_text(folder: Path) -> str:
    return project_status_details(folder)[0]


class App:
    def __init__(self, root):
        self.root = root
        root.title(f"注音校對工具 v{VERSION}")
        root.geometry("1080x560")
        self.q = queue.Queue()
        self.proc = None

        self.global_library_window = None
        self.delivery_recovery_window = None
        self.legacy_migration_window = None
        from review_gui import create_scrollable_body
        body, self.body_canvas = create_scrollable_body(root)
        self.body = body
        library_bar = ActionRows(body)
        library_bar.pack(fill="x", padx=12, pady=(8, 0))
        library_bar.set_items([tk.Button(library_bar, text="全域字形庫", command=self.open_global_library),
                               tk.Button(library_bar, text="交付與恢復狀態", command=self.open_delivery_recovery),
                               tk.Button(library_bar, text="舊資料匯入預檢", command=self.open_legacy_migration)])

        self.notebook = ttk.Notebook(body)
        self.notebook.pack(fill="x", padx=12, pady=(10, 4))
        self.tab_proof = tk.Frame(self.notebook)
        self.tab_diff = tk.Frame(self.notebook)
        self.notebook.add(self.tab_proof, text="注音校對")
        self.notebook.add(self.tab_diff, text="審次差異")

        self._build_proof_tab()
        self._build_diff_tab()

        self.run_status = WrappedLabel(body, text="就緒", anchor="w", justify="left", fg="#555555")
        self.run_status.pack(fill="x", padx=14, pady=(2, 8))

        # Detailed process output is useful for troubleshooting, but it is not a
        # normal proofreading task. Keep collecting it while hiding it by default.
        self.log_visible = False
        self.log_frame = tk.LabelFrame(body, text="執行紀錄（進階）")
        self.log = tk.Text(self.log_frame, wrap="word", font=("Microsoft JhengHei UI", 10), height=10)
        self.log.pack(fill="both", expand=True, padx=6, pady=6)
        root.after(100, self.poll)
        root.after(500, self.poll_project_status)

    def open_global_library(self):
        from global_library_inspector import GlobalLibraryInspector

        if self.global_library_window is not None and self.global_library_window.winfo_exists():
            self.global_library_window.lift()
            return
        self.global_library_window = GlobalLibraryInspector(self.root)

    def open_delivery_recovery(self):
        from delivery_recovery_inspector import DeliveryRecoveryInspector

        # Only the explicitly selected output; do not search nearby projects.
        project = self.output.get().strip() or None
        if self.delivery_recovery_window is not None and self.delivery_recovery_window.winfo_exists():
            self.delivery_recovery_window.set_project(project)
            self.delivery_recovery_window.lift()
            return
        self.delivery_recovery_window = DeliveryRecoveryInspector(self.root, project_output=project)

    def open_legacy_migration(self):
        from legacy_migration_inspector import LegacyMigrationInspector

        project = self.output.get().strip() or None
        if self.legacy_migration_window is not None and self.legacy_migration_window.winfo_exists():
            self.legacy_migration_window.set_project(project)
            self.legacy_migration_window.lift()
            return
        self.legacy_migration_window = LegacyMigrationInspector(self.root, project_output=project)

    def _build_proof_tab(self):
        self.input = tk.StringVar()
        self.output = tk.StringVar()

        header = tk.Frame(self.tab_proof)
        header.pack(fill="x", padx=12, pady=(12, 4))
        WrappedLabel(header, text="教材注音校對", font=("Microsoft JhengHei UI", 14, "bold")).pack(fill="x", anchor="w")
        WrappedLabel(
            header,
            text="選擇 PDF／整冊資料夾後開始新校對；舊版既有專案可按『修復／更新報告』直接接續，不會強制重解碼 actual。",
            fg="#555555", justify="left",
        ).pack(fill="x", anchor="w", pady=(3, 0))

        frm = tk.Frame(self.tab_proof)
        frm.pack(fill="x", padx=12, pady=8)
        for label, variable, choices in [
            ("教材 PDF／資料夾：", self.input, [("選 PDF", self.pick_pdf), ("選資料夾", self.pick_dir)]),
            ("校對專案資料夾：", self.output, [("選擇", self.pick_out)]),
        ]:
            self._path_field(frm, label, variable, choices)

        status_box = tk.LabelFrame(self.tab_proof, text="目前專案")
        status_box.pack(fill="x", padx=12, pady=(2, 8))
        self.project_status = WrappedLabel(status_box, text="尚未選擇校對專案資料夾", justify="left", anchor="w")
        self.project_status.pack(fill="x", padx=10, pady=8)

        buttons = ActionRows(self.tab_proof)
        buttons.pack(fill="x", padx=12, pady=(2, 10))
        self.runbtn = tk.Button(buttons, text="開始新校對", height=2, width=16, command=self.run)

        review = tk.Button(buttons, text="繼續校對", command=self.review)
        repair = tk.Button(buttons, text="修復／更新報告", command=self.report)
        report = tk.Button(buttons, text="查看報告", command=self.open_user_report)

        more = tk.Menubutton(buttons, text="更多…", width=12, height=2, relief="raised")
        menu = tk.Menu(more, tearoff=False)
        more.config(menu=menu)
        buttons.set_items([self.runbtn, review, repair, report, more])
        menu.add_command(label="輸出 expected／差異 GPT 證據表", command=self.export_gpt)
        menu.add_command(label="匯入 GPT 判定檔／單檔判定包（一鍵）", command=self.import_gpt_auto)
        menu.add_separator()
        menu.add_command(label="輸出 actual 待判定給 GPT", command=self.export_actual_gpt)
        menu.add_command(label="重新套用 actual 修正／重新解碼", command=self.refresh_actual)
        menu.add_separator()
        menu.add_command(label="開啟技術稽核報告", command=self.open_technical_report)
        menu.add_command(label="顯示／隱藏執行紀錄", command=self.toggle_log)
        menu.add_command(label="重新讀取專案狀態", command=self.refresh_project_status)
        menu.add_command(label="進度詳細資訊（可複製）", command=self.show_project_details)
        status_actions = ActionRows(status_box)
        status_actions.pack(fill="x", padx=10)
        status_actions.set_items([tk.Button(status_actions, text="進度詳細資訊（可複製）", command=self.show_project_details),
                                  tk.Button(status_actions, text="重新讀取專案狀態", command=self.refresh_project_status)])

    def toggle_log(self):
        self.log_visible = not self.log_visible
        if self.log_visible:
            self.log_frame.pack(fill="both", expand=True, padx=12, pady=(0, 10))
        else:
            self.log_frame.pack_forget()

    def _build_diff_tab(self):
        self.prev_input = tk.StringVar()
        self.curr_input = tk.StringVar()
        self.diff_output = tk.StringVar()
        frm = tk.Frame(self.tab_diff)
        frm.pack(fill="x", padx=12, pady=12)

        for label, variable, choices in [
            ("上一審 PDF／資料夾：", self.prev_input, [("選 PDF", lambda: self.pick_revision("prev", False)), ("選資料夾", lambda: self.pick_revision("prev", True))]),
            ("本審 PDF／資料夾：", self.curr_input, [("選 PDF", lambda: self.pick_revision("curr", False)), ("選資料夾", lambda: self.pick_revision("curr", True))]),
            ("差異報告資料夾：", self.diff_output, [("選擇", self.pick_diff_out)]),
        ]:
            self._path_field(frm, label, variable, choices)
        buttons = ActionRows(self.tab_diff)
        buttons.pack(fill="x", padx=12)
        self.diff_runbtn = tk.Button(buttons, text="開始差異檢查", command=self.run_diff)
        buttons.set_items([self.diff_runbtn, tk.Button(buttons, text="開啟差異報告", command=self.open_diff_report)])
        WrappedLabel(self.tab_diff, text="中文字相同但注音改變會優先列出；無法安全解碼時不硬判。", fg="#555555").pack(fill="x", padx=12)

    def _path_field(self, parent, label, variable, choices):
        WrappedLabel(parent, text=label).pack(fill="x")
        field = scrollable_entry(parent, variable)
        field.pack(fill="x")
        if variable is getattr(self, "output", None):
            field.entry.bind("<FocusOut>", lambda _e: self.refresh_project_status())
        buttons = ActionRows(parent)
        buttons.pack(fill="x", pady=(0, 6))
        buttons.set_items([tk.Button(buttons, text=title, command=command) for title, command in choices])

    def show_project_details(self):
        from review_gui import apply_screen_safe_geometry
        self.refresh_project_status()
        dialog = tk.Toplevel(self.root)
        dialog.title("進度詳細資訊（可選取及複製）")
        apply_screen_safe_geometry(dialog, 800, 600)
        buttons = ActionRows(dialog)
        buttons.pack(side="bottom", fill="x", padx=10)
        details = getattr(self, "project_diagnostics", "尚未選擇校對專案資料夾")
        def copy_all():
            dialog.clipboard_clear()
            dialog.clipboard_append(details)
        buttons.set_items([tk.Button(buttons, text="複製全部詳細資訊", command=copy_all),
                           tk.Button(buttons, text="關閉", command=dialog.destroy)])
        text = tk.Text(dialog, wrap="word", width=1)
        bar = ttk.Scrollbar(dialog, command=text.yview)
        text.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        text.pack(fill="both", expand=True)
        text.insert("1.0", details)
        text.configure(state="disabled")

    def pick_pdf(self):
        p = filedialog.askopenfilename(filetypes=[("PDF", "*.pdf")])
        if p:
            self.set_input(p)

    def pick_dir(self):
        p = filedialog.askdirectory()
        if p:
            self.set_input(p)

    def set_input(self, p):
        self.input.set(p)
        x = Path(p)
        default = (x.parent / (x.stem + "_注音校對")) if x.is_file() else (x / "注音校對_輸出")
        self.output.set(str(default))
        self.refresh_project_status()

    def pick_out(self):
        p = filedialog.askdirectory()
        if p:
            self.output.set(p)
            self.refresh_project_status()

    def refresh_project_status(self):
        out = self.output.get().strip()
        if not out:
            self.project_diagnostics = "尚未選擇校對專案資料夾"
            self.project_status.config(text="尚未選擇校對專案資料夾。請先選擇專案或教材。")
            return
        requested = Path(out)
        try:
            normalized = normalize_existing_project_folder(requested)
        except Exception as exc:
            self.project_status.config(text=_UNKNOWN_PROGRESS)
            self.project_diagnostics = f"{requested}\n{exc}"
            return
        if normalized != requested:
            self.output.set(str(normalized))
            out = str(normalized)
            self.run_status.config(text=f"已自動修正校對專案資料夾：{normalized}")
        summary, self.project_diagnostics = project_status_details(Path(out))
        self.project_status.config(text=summary)

    def poll_project_status(self):
        # Keep 「目前專案」 synchronized with pipeline_status.json while the
        # subprocess is running, not only after it exits.
        try:
            self.refresh_project_status()
        except Exception as exc:
            self.project_status.config(text=_UNKNOWN_PROGRESS)
            self.project_diagnostics = str(exc)
        finally:
            self.root.after(500, self.poll_project_status)

    def pick_revision(self, side: str, is_dir: bool):
        p = filedialog.askdirectory() if is_dir else filedialog.askopenfilename(filetypes=[("PDF", "*.pdf")])
        if not p:
            return
        if side == "prev":
            self.prev_input.set(p)
        else:
            self.curr_input.set(p)
        if not self.diff_output.get().strip():
            x = Path(p)
            base = x.parent if x.is_file() else x
            self.diff_output.set(str(base / "審次注音差異_輸出"))

    def pick_diff_out(self):
        p = filedialog.askdirectory()
        if p:
            self.diff_output.set(p)

    def proof_cmd(self, extra):
        root = Path(__file__).resolve().parent
        return [sys.executable, "-X", "utf8", str(root / "standalone_proofread.py"), *extra]

    def diff_cmd(self, previous: str, current: str, out: str):
        root = Path(__file__).resolve().parent
        return [sys.executable, "-X", "utf8", str(root / "revision_diff.py"), previous, current, "-o", out]

    def run(self):
        inp = self.input.get().strip()
        out = self.output.get().strip()
        if not inp or not out:
            messagebox.showerror("缺少資料", "請先選擇教材 PDF／資料夾與校對專案資料夾。")
            return
        if (Path(out) / "校對工作階段.json").exists():
            if not messagebox.askyesno("已有校對工作階段", "這個資料夾已有校對紀錄。若只是要繼續人工校對，請按『否』後使用『繼續校對』。\n\n確定要重新執行完整校對流程？"):
                return
        self.log.delete("1.0", "end")
        self.start(self.proof_cmd([inp, "-o", out]), self.runbtn)

    def report(self):
        out = self.output.get().strip()
        if not out:
            messagebox.showerror("缺少專案資料夾", "請先選擇校對專案資料夾。")
            return
        self.start(self.proof_cmd(["--repair-project", "-o", out]))

    def export_gpt(self):
        out = self.output.get().strip()
        if not out:
            messagebox.showerror("缺少專案資料夾", "請先選擇校對專案資料夾。")
            return
        self.start(self.proof_cmd(["--export-gpt", "-o", out]), export_kind="expected")

    def import_gpt_auto(self):
        out = self.output.get().strip()
        if not out:
            messagebox.showerror("缺少專案資料夾", "請先選擇校對專案資料夾。")
            return
        paths = filedialog.askopenfilenames(
            title="選擇 GPT 判定檔或單檔判定包（ZIP 可一次完成 actual＋expected）",
            filetypes=[("GPT 判定檔", ("*.xlsx", "*.zip")), ("Excel", "*.xlsx"), ("ZIP 判定包", "*.zip")],
        )
        if paths:
            # CLI 會自動辨識 ZIP 單檔判定包或 xlsx，固定先 actual、後 expected；
            # 使用者不需要再決定匯入順序。
            self.start(self.proof_cmd(["--import-gpt-auto", *paths, "-o", out]))

    # Backward-compatible method names: even if an old shortcut/menu binding
    # reaches one of these, workbook type is still auto-detected.
    def import_gpt(self):
        return self.import_gpt_auto()

    def export_actual_gpt(self):
        out = self.output.get().strip()
        if not out:
            messagebox.showerror("缺少專案資料夾", "請先選擇校對專案資料夾。")
            return
        self.start(self.proof_cmd(["--export-actual-gpt", "-o", out]), export_kind="actual")

    def import_actual_gpt(self):
        return self.import_gpt_auto()

    def refresh_actual(self):
        out = self.output.get().strip()
        if not out:
            messagebox.showerror("缺少專案資料夾", "請先選擇校對專案資料夾。")
            return
        if not messagebox.askyesno("重新解碼 actual", "將依目前人工／GPT 已驗證 actual 證據重新解碼此專案。\n舊 actual cache 若不相容會自動重建。確定繼續？"):
            return
        self.start(self.proof_cmd(["--refresh-actual", "-o", out]))

    def review(self):
        out = self.output.get().strip()
        if not out:
            messagebox.showerror("缺少專案資料夾", "請先選擇校對專案資料夾。")
            return
        if not (Path(out) / "校對工作階段.json").exists():
            messagebox.showinfo("尚未建立校對專案", "請先按『開始新校對』完成第一次分析。")
            return
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        subprocess.Popen([sys.executable, "-X", "utf8", str(Path(__file__).resolve().parent / "review_gui.py"), out], env=env)

    def _open_report(self, filename: str, missing_message: str):
        out = self.output.get().strip()
        if not out:
            return
        report = Path(out) / filename
        if not report.exists():
            messagebox.showinfo("尚無報告", missing_message)
            return
        try:
            if os.name == "nt":
                os.startfile(str(report))
            else:
                subprocess.Popen(["xdg-open", str(report)])
        except Exception as exc:
            messagebox.showerror("開啟失敗", str(exc))

    def open_user_report(self):
        self._open_report("注音校對_最終報告.xlsx", "尚未找到校對報告，請先執行校對或按『更新報告』。")

    def open_technical_report(self):
        self._open_report("注音校對_技術稽核.xlsx", "尚未找到技術稽核報告，請先執行校對或按『更新報告』。")

    def run_diff(self):
        previous = self.prev_input.get().strip()
        current = self.curr_input.get().strip()
        out = self.diff_output.get().strip()
        if not previous or not current or not out:
            messagebox.showerror("缺少資料", "請選擇上一審、本審，以及差異報告資料夾。")
            return
        self.log.delete("1.0", "end")
        self.start(self.diff_cmd(previous, current, out), self.diff_runbtn)

    def open_diff_report(self):
        out = self.diff_output.get().strip()
        if not out:
            return
        report = Path(out) / "審次注音差異報告.xlsx"
        if not report.exists():
            messagebox.showinfo("尚無報告", "尚未找到『審次注音差異報告.xlsx』，請先執行差異檢查。")
            return
        try:
            if os.name == "nt":
                os.startfile(str(report))
            else:
                subprocess.Popen(["xdg-open", str(report)])
        except Exception as exc:
            messagebox.showerror("開啟失敗", str(exc))

    def start(self, cmd, button=None, extra_env=None, export_kind=None):
        if button is not None:
            button.config(state="disabled")
        self.run_status.config(text=("正在輸出 GPT 檔案…" if export_kind else
                                     "處理中…詳細紀錄可從『更多…』開啟。"))

        def worker():
            try:
                env = os.environ.copy()
                env["PYTHONUTF8"] = "1"
                env["PYTHONIOENCODING"] = "utf-8"
                if extra_env:
                    env.update({str(k): str(v) for k, v in extra_env.items()})
                with subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                ) as p:
                    self.proc = p
                    last_output = ""
                    for line in p.stdout:
                        self.q.put(line)
                        if line.strip():
                            last_output = line.strip()
                    code = p.wait()
                self.q.put(f"\n[程式結束，代碼 {code}]\n")
                self.q.put(("__DONE__", button, code, export_kind, last_output))
            except Exception as exc:
                self.q.put(f"\n執行失敗：{exc}\n")
                self.q.put(("__DONE__", button, -1, export_kind, f"執行失敗：{exc}"))

        threading.Thread(target=worker, daemon=True).start()

    def _show_export_result(self, kind, code, result):
        if code != 0:
            self.run_status.config(text="GPT 匯出失敗。請查看執行紀錄。")
            messagebox.showerror("GPT 匯出失敗", f"{result or '程式沒有回傳錯誤原因。'}\n\n可從『更多… → 顯示／隱藏執行紀錄』查看完整原因。")
            return
        # The CLI prints its actual output path only after saving it.  A ZIP
        # left by an earlier export cannot turn today's zero-pending result
        # into a false success.
        filename = "待判定候選_給GPT.xlsx" if kind == "expected" else "actual待判定_GPT包.zip"
        path = Path(result)
        if path.name == filename and path.is_file():
            self.run_status.config(text=f"GPT 匯出完成：{path}")
            messagebox.showinfo("GPT 匯出完成", f"檔案已儲存至：\n{path}")
        elif kind == "actual" and result.startswith("目前沒有 ACTUAL_DECODE_ERROR／ACTUAL_UNRESOLVED 可匯出"):
            self.run_status.config(text=result)
            messagebox.showinfo("沒有 actual 待判定項目", result)
        else:
            self.run_status.config(text="GPT 匯出結果無法確認。請查看執行紀錄。")
            messagebox.showerror("GPT 匯出結果無法確認", f"程式沒有回傳可確認的輸出檔案。\n{result}\n\n請查看執行紀錄。")

    def poll(self):
        try:
            while True:
                x = self.q.get_nowait()
                if isinstance(x, tuple) and x and x[0] == "__DONE__":
                    if x[1] is not None:
                        x[1].config(state="normal")
                    self.refresh_project_status()
                    code = int(x[2]) if len(x) >= 3 else 0
                    export_kind = x[3] if len(x) >= 4 else None
                    if export_kind:
                        self._show_export_result(export_kind, code, x[4] if len(x) >= 5 else "")
                    elif code == 0:
                        self.run_status.config(text="處理已結束；『目前專案』已重新讀取最新狀態。")
                    else:
                        self.run_status.config(text=f"操作失敗（代碼 {code}）；專案資料未因失敗匯入而更新。請開啟執行紀錄查看原因。")
                else:
                    self.log.insert("end", x)
                    self.log.see("end")
                    # Raw subprocess lines stay in the copyable execution log.
                    # They may contain exceptions or internal gate names.

        except queue.Empty:
            pass
        self.root.after(100, self.poll)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
