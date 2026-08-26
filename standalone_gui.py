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

VERSION = "5.6.2"


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


def project_status_text(folder: Path) -> str:
    """Return the best current project status, even before a sealed session exists."""
    folder = Path(folder)
    status_path = folder / "pipeline_status.json"
    session_path = folder / "校對工作階段.json"

    if status_path.exists():
        try:
            data = json.loads(status_path.read_text(encoding="utf-8"))
            status = str(data.get("status") or "")
            status_text = str(data.get("status_text") or "").strip()
            if status_text:
                text = status_text
            else:
                text = STATUS_TEXT.get(status, status or "已有校對處理紀錄")

            gate = data.get("completion_gate") or {}
            state_counts = gate.get("state_counts") or {}
            terminal = {"PASS", "TEXTBOOK_ERROR_CONFIRMED", "EXCLUDED_NONINDEPENDENT_LAYER", "EXCLUDED_OUT_OF_SCOPE"}
            pending = sum(int(v or 0) for k, v in state_counts.items() if k not in terminal)
            if pending:
                text += f"；待處理 {pending} 筆"
            failed_gates = [str(value) for value in (gate.get("failed_gates") or []) if str(value).strip()]
            if status == "PROCESSING_FINISHED" and failed_gates:
                text += "；未通過門檻：" + "、".join(failed_gates)

            # A runtime failure can happen before the sealed session manifest is
            # written.  Surface that state instead of incorrectly calling the
            # folder a brand-new project.
            if status == "PIPELINE_BLOCKED":
                source_validation = data.get("source_validation") or {}
                errors = source_validation.get("errors") or []
                if errors:
                    last_error = str(errors[-1]).strip()
                    if last_error:
                        text += f"；{last_error}"
                if not session_path.exists():
                    text += "；工作階段尚未建立"
            elif status == "PROCESSING" and not session_path.exists():
                text += "；首次分析中，完成第 3 階段後建立工作階段"
            return text
        except Exception:
            if session_path.exists():
                return "已有校對工作階段；狀態檔無法讀取，可直接按『繼續校對』。"
            return "已有處理紀錄，但狀態檔無法讀取；工作階段尚未建立。"

    if not session_path.exists():
        return "這是新的校對專案資料夾；尚未建立工作階段。"
    return "已有校對工作階段；尚未產生狀態摘要，可直接按『繼續校對』。"


class App:
    def __init__(self, root):
        self.root = root
        root.title(f"注音校對工具 v{VERSION}")
        root.geometry("1080x560")
        self.q = queue.Queue()
        self.proc = None

        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill="x", padx=12, pady=(10, 4))
        self.tab_proof = tk.Frame(self.notebook)
        self.tab_diff = tk.Frame(self.notebook)
        self.notebook.add(self.tab_proof, text="注音校對")
        self.notebook.add(self.tab_diff, text="審次差異")

        self._build_proof_tab()
        self._build_diff_tab()

        self.run_status = tk.Label(root, text="就緒", anchor="w", justify="left", fg="#555555")
        self.run_status.pack(fill="x", padx=14, pady=(2, 8))

        # Detailed process output is useful for troubleshooting, but it is not a
        # normal proofreading task. Keep collecting it while hiding it by default.
        self.log_visible = False
        self.log_frame = tk.LabelFrame(root, text="執行紀錄（進階）")
        self.log = tk.Text(self.log_frame, wrap="word", font=("Microsoft JhengHei UI", 10), height=10)
        self.log.pack(fill="both", expand=True, padx=6, pady=6)
        root.after(100, self.poll)
        root.after(500, self.poll_project_status)

    def _build_proof_tab(self):
        self.input = tk.StringVar()
        self.output = tk.StringVar()

        header = tk.Frame(self.tab_proof)
        header.pack(fill="x", padx=12, pady=(12, 4))
        tk.Label(header, text="教材注音校對", font=("Microsoft JhengHei UI", 14, "bold")).pack(anchor="w")
        tk.Label(
            header,
            text="選擇 PDF／整冊資料夾後開始新校對；舊版既有專案可按『修復／更新報告』直接接續，不會強制重解碼 actual。",
            fg="#555555", justify="left",
        ).pack(anchor="w", pady=(3, 0))

        frm = tk.Frame(self.tab_proof)
        frm.pack(fill="x", padx=12, pady=8)
        tk.Label(frm, text="教材 PDF／資料夾：").grid(row=0, column=0, sticky="w")
        tk.Entry(frm, textvariable=self.input, width=76).grid(row=0, column=1, padx=5)
        tk.Button(frm, text="選 PDF", command=self.pick_pdf).grid(row=0, column=2, padx=2)
        tk.Button(frm, text="選資料夾", command=self.pick_dir).grid(row=0, column=3, padx=2)

        tk.Label(frm, text="校對專案資料夾：").grid(row=1, column=0, sticky="w", pady=8)
        out_entry = tk.Entry(frm, textvariable=self.output, width=76)
        out_entry.grid(row=1, column=1, padx=5)
        out_entry.bind("<FocusOut>", lambda _e: self.refresh_project_status())
        tk.Button(frm, text="選擇", command=self.pick_out).grid(row=1, column=2, padx=2)

        status_box = tk.LabelFrame(self.tab_proof, text="目前專案")
        status_box.pack(fill="x", padx=12, pady=(2, 8))
        self.project_status = tk.Label(status_box, text="尚未選擇校對專案資料夾", justify="left", anchor="w")
        self.project_status.pack(fill="x", padx=10, pady=8)

        buttons = tk.Frame(self.tab_proof)
        buttons.pack(fill="x", padx=12, pady=(2, 10))
        self.runbtn = tk.Button(buttons, text="開始新校對", height=2, width=16, command=self.run)
        self.runbtn.pack(side="left")
        tk.Button(buttons, text="繼續校對", height=2, width=16, command=self.review).pack(side="left", padx=6)
        tk.Button(buttons, text="修復／更新報告", height=2, width=16, command=self.report).pack(side="left", padx=6)
        tk.Button(buttons, text="查看報告", height=2, width=14, command=self.open_user_report).pack(side="left", padx=6)

        more = tk.Menubutton(buttons, text="更多…", width=12, height=2, relief="raised")
        menu = tk.Menu(more, tearoff=False)
        more.config(menu=menu)
        more.pack(side="left", padx=6)
        menu.add_command(label="輸出 expected／差異 GPT 證據表", command=self.export_gpt)
        menu.add_command(label="匯入 GPT 判定檔／單檔判定包（一鍵）", command=self.import_gpt_auto)
        menu.add_separator()
        menu.add_command(label="輸出 actual 待判定給 GPT", command=self.export_actual_gpt)
        menu.add_command(label="重新套用 actual 修正／重新解碼", command=self.refresh_actual)
        menu.add_separator()
        menu.add_command(label="開啟技術稽核報告", command=self.open_technical_report)
        menu.add_command(label="顯示／隱藏執行紀錄", command=self.toggle_log)
        menu.add_command(label="重新讀取專案狀態", command=self.refresh_project_status)

    def toggle_log(self):
        self.log_visible = not self.log_visible
        if self.log_visible:
            self.log_frame.pack(fill="both", expand=True, padx=12, pady=(0, 10))
            self.root.geometry("1080x760")
        else:
            self.log_frame.pack_forget()
            self.root.geometry("1080x560")

    def _build_diff_tab(self):
        self.prev_input = tk.StringVar()
        self.curr_input = tk.StringVar()
        self.diff_output = tk.StringVar()
        frm = tk.Frame(self.tab_diff)
        frm.pack(fill="x", padx=12, pady=12)

        tk.Label(frm, text="上一審 PDF／資料夾：").grid(row=0, column=0, sticky="w")
        tk.Entry(frm, textvariable=self.prev_input, width=72).grid(row=0, column=1, padx=5)
        tk.Button(frm, text="選 PDF", command=lambda: self.pick_revision("prev", False)).grid(row=0, column=2, padx=2)
        tk.Button(frm, text="選資料夾", command=lambda: self.pick_revision("prev", True)).grid(row=0, column=3, padx=2)

        tk.Label(frm, text="本審 PDF／資料夾：").grid(row=1, column=0, sticky="w", pady=8)
        tk.Entry(frm, textvariable=self.curr_input, width=72).grid(row=1, column=1, padx=5)
        tk.Button(frm, text="選 PDF", command=lambda: self.pick_revision("curr", False)).grid(row=1, column=2, padx=2)
        tk.Button(frm, text="選資料夾", command=lambda: self.pick_revision("curr", True)).grid(row=1, column=3, padx=2)

        tk.Label(frm, text="差異報告資料夾：").grid(row=2, column=0, sticky="w")
        tk.Entry(frm, textvariable=self.diff_output, width=72).grid(row=2, column=1, padx=5)
        tk.Button(frm, text="選擇", command=self.pick_diff_out).grid(row=2, column=2, padx=2)

        b = tk.Frame(self.tab_diff)
        b.pack(fill="x", padx=12, pady=(0, 10))
        self.diff_runbtn = tk.Button(b, text="開始差異檢查", height=2, width=18, command=self.run_diff)
        self.diff_runbtn.pack(side="left")
        tk.Button(b, text="開啟差異報告", height=2, width=18, command=self.open_diff_report).pack(side="left", padx=6)
        tk.Label(b, text="中文字相同但注音改變會優先列出；無法安全解碼時不硬判。", fg="#555555").pack(side="left", padx=12)

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
            self.project_status.config(text="尚未選擇校對專案資料夾")
            return
        requested = Path(out)
        normalized = normalize_existing_project_folder(requested)
        if normalized != requested:
            self.output.set(str(normalized))
            out = str(normalized)
            self.run_status.config(text=f"已自動修正校對專案資料夾：{normalized}")
        self.project_status.config(text=project_status_text(Path(out)))

    def poll_project_status(self):
        # Keep 「目前專案」 synchronized with pipeline_status.json while the
        # subprocess is running, not only after it exits.
        self.refresh_project_status()
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
        if out:
            self.start(self.proof_cmd(["--export-gpt", "-o", out]))

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
        self.start(self.proof_cmd(["--export-actual-gpt", "-o", out]))

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

    def start(self, cmd, button=None, extra_env=None):
        if button is not None:
            button.config(state="disabled")
        self.run_status.config(text="處理中…詳細紀錄可從『更多…』開啟。")

        def worker():
            try:
                env = os.environ.copy()
                env["PYTHONUTF8"] = "1"
                env["PYTHONIOENCODING"] = "utf-8"
                if extra_env:
                    env.update({str(k): str(v) for k, v in extra_env.items()})
                p = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                )
                self.proc = p
                for line in p.stdout:
                    self.q.put(line)
                code = p.wait()
                self.q.put(f"\n[程式結束，代碼 {code}]\n")
                self.q.put(("__DONE__", button, code))
            except Exception as exc:
                self.q.put(f"\n執行失敗：{exc}\n")
                self.q.put(("__DONE__", button, -1))

        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            while True:
                x = self.q.get_nowait()
                if isinstance(x, tuple) and x and x[0] == "__DONE__":
                    if x[1] is not None:
                        x[1].config(state="normal")
                    self.refresh_project_status()
                    code = int(x[2]) if len(x) >= 3 else 0
                    if code == 0:
                        self.run_status.config(text="處理已結束；『目前專案』已重新讀取最新狀態。")
                    else:
                        self.run_status.config(text=f"操作失敗（代碼 {code}）；專案資料未因失敗匯入而更新。請開啟執行紀錄查看原因。")
                else:
                    self.log.insert("end", x)
                    self.log.see("end")
                    line = str(x).strip()
                    if line:
                        if len(line) > 120:
                            line = line[:117] + "…"
                        self.run_status.config(text=line)
        except queue.Empty:
            pass
        self.root.after(100, self.poll)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
