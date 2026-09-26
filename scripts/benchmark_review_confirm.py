"""Compare real Tk confirmation timing against an unchanged Git checkout.

Synthetic contract-valid session/PDF only: no decoder or real-book acceptance
claim. Run this same script with --repository pointing at each revision. Stage
timings are inclusive (nested stages must not be summed); heartbeat gaps are
measured separately from processing and the unchanged 0.5 s cooldown.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import ExitStack, nullcontext
import ctypes
import functools
import importlib.metadata
import hashlib
import json
import math
import platform
from pathlib import Path
import statistics
import sys
import tempfile
import threading
import time
from unittest.mock import patch


def summarize(values):
    ordered = sorted(values)
    return {"n": len(ordered), "median_ms": statistics.median(ordered) * 1000,
            "p95_ms": ordered[max(0, math.ceil(len(ordered) * .95) - 1)] * 1000}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--rows", type=int, default=2000)
    parser.add_argument("--events", type=int, default=500)
    parser.add_argument("--groups", type=int, default=8)
    parser.add_argument("--operations", type=int, default=21)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, help="Dedicated synthetic fixture directory to reuse identical bytes across revisions")
    parser.add_argument("--revision-label", required=True, help="Proven revision or explicitly uncommitted source label")
    parser.add_argument("--dialog-cycles", type=int, default=6, help="Native three-location preview/checkbox/close cycles after timing")
    parser.add_argument("--prepare-fixture-only", action="store_true", help="Create/validate exact bytes in a separate process before paired measurements")
    args = parser.parse_args()
    args.repository = args.repository.resolve()
    args.source_files_sha256 = source_manifest(args.repository)
    if args.rows <= args.events + args.groups + args.operations or min(args.events, args.groups, args.dialog_cycles) < 0 or args.operations < 2:
        parser.error("Need positive rows, at least two operations and enough unreviewed unstaged rows")
    sys.path.insert(0, str(args.repository.resolve()))
    import tkinter as tk
    import fitz
    from openpyxl import Workbook
    import actual_review as ar
    import occurrence_ledger as ol
    import review_gui as gui
    import standalone_proofread as sp
    from tests.test_manual_review_usability_v580 import create_gui_fixture, make_entry, make_manifest

    if args.fixture:
        args.fixture = args.fixture.resolve()
        marker = args.fixture / "benchmark-fixture.json"
        if args.fixture.exists() and any(args.fixture.iterdir()) and not marker.exists():
            parser.error("--fixture must be empty or contain this benchmark's fixture marker")
        args.fixture.mkdir(parents=True, exist_ok=True)
    context = nullcontext(str(args.fixture)) if args.fixture else tempfile.TemporaryDirectory(prefix="zhuyin-confirm-benchmark-")
    with context as directory:
        output = Path(directory)
        marker = output / "benchmark-fixture.json"
        fixture_config = {"rows": args.rows, "events": args.events, "groups": args.groups}
        if marker.exists():
            saved = json.loads(marker.read_text(encoding="utf-8"))
            if saved.get("schema") != 2 or saved["configuration"] != fixture_config:
                parser.error("Fixture must use schema 2 and identical row/event/group counts")
            expected = set(saved["initial_files_hex"])
            actual = {path.relative_to(output).as_posix() for path in output.rglob("*")
                      if path.is_file() and path != marker}
            if actual != expected:
                raise AssertionError("Unexpected or missing fixture files; refusing automatic reset")
            for relative, content_hex in saved["initial_files_hex"].items():
                path = (output / relative).resolve()
                if not path.is_relative_to(output.resolve()):
                    raise AssertionError("Fixture path escaped its dedicated directory")
                path.write_bytes(bytes.fromhex(content_hex))
        else:
            manifest, db = create_fixture(output, args, create_gui_fixture, make_entry, make_manifest, sp, ar, ol, Workbook)
            # A save can acquire this lock even with zero staged groups. Use
            # the real context manager before snapshotting, not a fabricated
            # file or an unknown-file exception during later resets.
            with sp.project_delivery_lock(sp.project_actual_evidence_root(output)):
                pass
            sp.manual_actual_staging_summary(output, ledger=sp.materialize_ledger(manifest, db))
            saved = {"schema": 2, "configuration": fixture_config,
                     "initial_files_hex": {path.relative_to(output).as_posix(): path.read_bytes().hex()
                                           for path in sorted(output.rglob("*")) if path.is_file()}}
            marker.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
        fixture_files = {relative: hashlib.sha256(bytes.fromhex(content)).hexdigest()
                         for relative, content in saved["initial_files_hex"].items()}
        actual_hashes = {relative: hashlib.sha256((output / relative).read_bytes()).hexdigest() for relative in fixture_files}
        if fixture_files != actual_hashes:
            raise AssertionError("Initial fixture bytes were not restored exactly")
        fixture_digest = digest_manifest(fixture_files)
        args.fixture_files_sha256 = fixture_files
        args.fixture_marker_sha256 = hashlib.sha256(marker.read_bytes()).hexdigest()
        manifest = sp.json_load_strict(output / "校對工作階段.json")
        db = sp.json_load_strict(output / "人工判定資料庫.json")
        ol.validate_occurrence_ledger(sp.materialize_ledger(manifest, db))
        summary = sp.manual_actual_staging_summary(output, ledger=sp.materialize_ledger(manifest, db))
        if summary.get("staging_error") or summary["staged_group_count"] != args.groups:
            raise AssertionError(summary)

        if args.prepare_fixture_only:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps({"configuration": fixture_config,
                "fixture_files_sha256": fixture_files, "fixture_content_sha256": fixture_digest,
                "note": "Fixture preparation only; no performance measurement"}, ensure_ascii=False, indent=2), encoding="utf-8")
            return
        return run_gui_benchmark(output, args, gui, sp, tk, fitz, fixture_digest)


def create_fixture(output, args, create_gui_fixture, make_entry, make_manifest, sp, ar, ol, Workbook):
    small = create_gui_fixture(output)
    pdf = Path(small["records"][0]["pdf"])
    sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
    rows = []
    for index in range(args.rows):
        sample = make_entry(index % 7 + 1, pdf=pdf, pdf_sha=sha)
        source = dict(sample["source_record"])
        source.update(source_row_number=index + 1, glyph_id_字形索引=index + 1)
        source["穩定注音鍵"] = f"benchmark-{index}"
        source.pop("occurrence_id", None)
        source.pop("review_id", None)
        ol.prepare_occurrence_rows(sha, [source])
        rows.append(ol.build_occurrence_ledger([source], {source["occurrence_id"]: sample})[0])
    manifest = make_manifest(rows)
    info = {"pdf": str(pdf), "pdf_name": pdf.name, "pdf_sha256": sha}
    for kind in ("actual", "candidate"):
        path = output / f"synthetic-{kind}.xlsx"
        workbook = Workbook()
        workbook.active.append(["occurrence_id", "actual", "expected"])
        for row in rows:
            workbook.active.append([row["occurrence_id"], row["actual"], ""])
        workbook.save(path)
        workbook.close()
        info[f"{kind}_workbook"] = str(path)
        info[f"{kind}_workbook_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest["pdfs"] = [info]
    manifest = sp.seal_manifest(manifest)
    sp.json_save(output / "校對工作階段.json", manifest)
    db = sp.normalize_db({})
    for row in rows[:args.events]:
        db["events"][row["review_id"]] = sp.build_manual_expected_event(row, operation="CONFIRM_CURRENT_AS_EXPECTED")
    sp.json_save(output / "人工判定資料庫.json", db)
    for row in rows[-args.groups:] if args.groups else []:
        ar.stage_manual_actual_group(sp.project_actual_evidence_root(output),
                                    ar.build_actual_group_for_entry(rows, row), "ㄎㄢˋ",
                                    checked_occurrence_ids=[row["occurrence_id"]],
                                    source="isolated synthetic performance fixture", note="not real-book evidence")
    return manifest, db


def run_gui_benchmark(output, args, gui, sp, tk, fitz, fixture_digest):
    memory_before_gui = memory_bytes()
    root = tk.Tk()
    root.withdraw()
    window = tk.Toplevel(root)
    app = gui.ReviewApp(window, output)
    window.update()
    environment = {"tcl": str(root.tk.call("info", "patchlevel")),
                   "tk": str(root.tk.call("package", "provide", "Tk")),
                   "screen_pixels": [root.winfo_screenwidth(), root.winfo_screenheight()],
                   "tk_scaling": float(root.tk.call("tk", "scaling")),
                   "dependencies": dict(sorted((dist.metadata["Name"], dist.version)
                                               for dist in importlib.metadata.distributions()))}
    memory_after_gui = memory_bytes()
    samples = []
    stage_times = defaultdict(list)
    stage_counts = defaultdict(int)
    local = threading.local()

    def timed(name, function):
        @functools.wraps(function)
        def call(*call_args, **kwargs):
            active = getattr(local, "active", set())
            # A GUI alias and its backend implementation can share a label.
            # Count once per nesting level, and retain inclusive stage time.
            if name in active:
                return function(*call_args, **kwargs)
            local.active = active | {name}
            started = time.perf_counter()
            try:
                return function(*call_args, **kwargs)
            finally:
                stage_times[name].append(time.perf_counter() - started)
                stage_counts[name] += 1
                local.active = active
        return call

    heartbeat = []
    heartbeat_memory = []
    heartbeat_running = True

    def beat():
        heartbeat.append(time.perf_counter())
        heartbeat_memory.append(memory_bytes())
        if heartbeat_running:
            root.after(10, beat)

    def pump_until(predicate, timeout=120):
        limit = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() >= limit:
                raise TimeoutError("confirmation did not finish")
            root.update()
            time.sleep(.001)
        root.update()

    dialogs = []
    with ExitStack() as stack:
        for module, attribute, name in (
            (sp, "materialize_ledger", "ledger_full"), (gui, "materialize_ledger", "ledger_full"),
            (sp, "json_save", "json_save"), (gui, "json_save", "json_save"),
            (sp, "manual_actual_staging_summary", "actual_staging"),
            (gui, "manual_actual_staging_summary", "actual_staging"),
            (gui.ReviewApp, "_set_actionable_records_from_ledger", "todo_update"),
            (gui.ReviewApp, "render", "preview_render"),
        ):
            if hasattr(module, attribute):
                stack.enter_context(patch.object(module, attribute, timed(name, getattr(module, attribute))))
        for name in ("showerror", "showwarning", "showinfo"):
            stack.enter_context(patch.object(gui.messagebox, name,
                                            lambda *a, **k: dialogs.append([str(v) for v in a])))
        beat()
        try:
            for operation in range(args.operations):
                pump_until(lambda: time.monotonic() >= getattr(app, "_shortcut_cooldown_until", 0))
                # Exclude cooldown and settle idle geometry before each sample.
                root.update()
                if app._rendered_review_id != app.current()["review_id"]:
                    raise AssertionError("current preview was not loaded")
                before = len(app.db["events"])
                stage_times.clear()
                stage_counts.clear()
                heartbeat.clear()
                heartbeat_memory.clear()
                memory_before = memory_bytes()
                heartbeat.append(time.perf_counter())
                start = time.perf_counter()
                app.primary.invoke()
                callback_return = time.perf_counter()
                pump_until(lambda: not getattr(app, "_save_in_progress", False))
                finished = time.perf_counter()
                heartbeat.append(finished)
                if len(app.db["events"]) != before + 1 or not app._last_event_saved or not app._last_event_refreshed:
                    raise AssertionError({"operation": operation, "dialogs": dialogs})
                if dialogs:
                    raise AssertionError(dialogs)
                gaps = [b - a for a, b in zip(heartbeat, heartbeat[1:])]
                samples.append({
                    "operation": operation, "events_before": before,
                    "processing_seconds": finished - start,
                    "button_callback_seconds": callback_return - start,
                    "max_heartbeat_gap_seconds": max(gaps, default=0),
                    "cooldown_seconds": .5,
                    "stage_seconds": {key: sum(value) for key, value in stage_times.items()},
                    "stage_calls": dict(stage_counts),
                    "service_timings": getattr(app, "last_save_timings", {}),
                    "memory_before": memory_before,
                    "memory_after": memory_bytes(),
                    "memory_sampled_peak": {key: max(value[key] for value in heartbeat_memory + [memory_before, memory_bytes()])
                                            for key in memory_before},
                })
            memory_after_saves = memory_bytes()
            dialog_samples = run_dialog_cycles(window, root, output, args, gui, sp, pump_until)
        finally:
            heartbeat_running = False
            window.destroy()
            root.destroy()

    if source_manifest(args.repository) != args.source_files_sha256:
        raise AssertionError("Measured application sources changed during this process")
    steady = samples[1:]  # retain cold first operation in raw output
    result = {
        "fixture": "synthetic sealed session, PDF and hashed XLSX; not real textbook/decoder acceptance",
        "repository": str(args.repository.resolve()), "platform": platform.platform(),
        "revision_label": args.revision_label,
        "source_files_sha256": args.source_files_sha256,
        "source_manifest_sha256": digest_manifest(args.source_files_sha256),
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "fixture_content_sha256": fixture_digest,
        "fixture_files_sha256": args.fixture_files_sha256,
        "fixture_marker_sha256": args.fixture_marker_sha256,
        "fixture_reset": "Every recorded fixture file restored to exact initial bytes before each fresh process, including DB and staging",
        "environment": environment,
        "python": sys.version, "pymupdf": fitz.VersionBind,
        "rows": args.rows, "initial_events": args.events, "staged_groups": args.groups,
        "operations": args.operations, "summary_excludes_first_operation": True,
        "p95_method": "nearest rank", "heartbeat_interval_ms": 10,
        "processing": summarize([s["processing_seconds"] for s in steady]),
        "button_callback": summarize([s["button_callback_seconds"] for s in steady]),
        "ui_max_heartbeat_gap": summarize([s["max_heartbeat_gap_seconds"] for s in steady]),
        "cooldown_ms": 500,
        "memory": {"before_gui": memory_before_gui, "after_gui": memory_after_gui,
                   "after_saves": memory_after_saves, "after_destroy": memory_bytes(),
                   "method": "GetProcessMemoryInfo: heartbeat and operation-boundary samples include native allocations; private-byte peaks are sampled, not guaranteed instantaneous peaks"},
        "cold": {name: summarize([samples[0][field]]) for name, field in
                 (("processing", "processing_seconds"), ("button_callback", "button_callback_seconds"),
                  ("ui_max_heartbeat_gap", "max_heartbeat_gap_seconds"))},
        "stages_inclusive": {name: summarize([s["stage_seconds"].get(name, 0) for s in steady])
                             for name in sorted({name for s in steady for name in s["stage_seconds"]})},
        "service_stages": {name: summarize([s["service_timings"].get(name, 0) for s in steady])
                           for name in sorted({name for s in steady for name in s["service_timings"]})},
        "samples": samples,
        "dialog_cycles": dialog_samples,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k not in ("samples", "source_files_sha256", "fixture_files_sha256", "dialog_cycles")}, ensure_ascii=False, indent=2))


def digest_manifest(files):
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def source_manifest(repository):
    # Root application sources plus the imported fixture constructor. A measured
    # uncommitted candidate is later bound to its commit by these exact bytes.
    paths = sorted(repository.glob("*.py")) + [repository / "tests/test_manual_review_usability_v580.py"]
    return {path.relative_to(repository).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths}


@functools.lru_cache(maxsize=1)
def _windows_memory_api():
    # ctypes caches POINTER types. Define the structure and API exactly once;
    # defining them per heartbeat would make the measuring tool retain memory.
    if sys.platform != "win32":
        raise RuntimeError("This benchmark requires Windows GetProcessMemoryInfo")
    from ctypes import wintypes

    class ProcessMemoryCountersEx(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in (
                "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                "PagefileUsage", "PeakPagefileUsage", "PrivateUsage")]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessMemoryCountersEx), wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    return psapi.GetProcessMemoryInfo, kernel.GetCurrentProcess(), ProcessMemoryCountersEx


def memory_bytes():
    """OS process memory, including native Tk/PyMuPDF allocations (Windows)."""
    get_memory, process, counter_type = _windows_memory_api()
    counters = counter_type()
    counters.cb = ctypes.sizeof(counters)
    if not get_memory(process, ctypes.byref(counters), counters.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return {"working_set_bytes": counters.WorkingSetSize, "private_bytes": counters.PrivateUsage,
            "process_peak_working_set_bytes": counters.PeakWorkingSetSize}


def run_dialog_cycles(window, root, output, args, gui, sp, pump_until):
    """Real PDF rendering and visible-target checkbox invokes, never viewed flags.

    Synthetic native GUI allocation only; not human reading or reusable-truth
    acceptance. The display-only three-row group and result are never persisted.
    """
    rows = sp.materialize_ledger(sp.json_load_strict(output / "校對工作階段.json"), {})[:3]
    group = {"kind": "benchmark_display_only", "members": rows}
    samples = []
    for cycle in range(args.dialog_cycles):
        before = memory_bytes()
        owner = gui.tk.Toplevel(window)
        owner.geometry("400x200+20+20")
        owner.update()
        dialog = gui.ActualReadingDialog(owner, rows[0], group, output, wait=False)
        try:
            pump_until(lambda: dialog.winfo_viewable())
            opened = memory_bytes()
            viewed = []
            for index in range(len(rows)):
                if index:
                    dialog.preview_buttons[index].invoke()
                pump_until(lambda: dialog.previews[index] is not None and
                           bool(dialog.previews[index].canvas.find_withtag("target")))
                preview = dialog.previews[index]
                target = preview.canvas.coords(preview.canvas.find_withtag("target")[-1])
                dialog.body_canvas.reveal(preview.canvas, target[1], target[3])
                pump_until(lambda: str(dialog.check_buttons[index].cget("state")) == "normal")
                if dialog.checked_vars[index].get():
                    raise AssertionError("Dialog sample checked before checkbox invoke")
                dialog.check_buttons[index].invoke()
                if not dialog.checked_vars[index].get() or not dialog._viewed_target[index]:
                    raise AssertionError("Real visible-target checkbox invoke did not select sample")
                if sum(item is not None for item in dialog.previews) > 2:
                    raise AssertionError("More than A and one peer image retained")
                viewed.append({"index": index, "memory": memory_bytes()})
            mode = ("submit", "cancel", "owner_destroy")[cycle % 3]
            if mode == "submit":
                dialog.reading.set(rows[0]["actual"])
                dialog.submit()
                if not dialog.result or len(dialog.result["checked_occurrence_ids"]) != len(rows):
                    raise AssertionError("Visible confirmed samples missing from dialog result")
            elif mode == "cancel":
                dialog.destroy()
        finally:
            owner.destroy()
            root.update()
        del dialog, owner, preview
        samples.append({"cycle": cycle, "close_mode": mode, "before": before,
                        "opened": opened, "visible_samples": viewed, "after_close": memory_bytes()})
    return samples


if __name__ == "__main__":
    main()
