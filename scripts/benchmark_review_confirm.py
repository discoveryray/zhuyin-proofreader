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
import functools
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
    return {"median_ms": statistics.median(ordered) * 1000,
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
    args = parser.parse_args()
    if args.rows <= args.events + args.groups + args.operations or min(args.events, args.groups) < 0 or args.operations < 2:
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
            if saved["configuration"] != fixture_config:
                parser.error("Existing fixture has different row/event/group counts")
            (output / "人工判定資料庫.json").write_bytes(bytes.fromhex(saved["initial_db_hex"]))
            manifest = sp.json_load_strict(output / "校對工作階段.json")
            if hashlib.sha256((output / "校對工作階段.json").read_bytes()).hexdigest() != saved["session_sha256"]:
                raise AssertionError("Benchmark fixture session was modified")
            db = sp.json_load_strict(output / "人工判定資料庫.json")
        else:
            manifest, db = create_fixture(output, args, create_gui_fixture, make_entry, make_manifest, sp, ar, ol, Workbook)
            saved = {"configuration": fixture_config,
                     "initial_db_hex": (output / "人工判定資料庫.json").read_bytes().hex(),
                     "session_sha256": hashlib.sha256((output / "校對工作階段.json").read_bytes()).hexdigest()}
            marker.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
        fixture_digest = hashlib.sha256(json.dumps(saved, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        ol.validate_occurrence_ledger(sp.materialize_ledger(manifest, db))
        summary = sp.manual_actual_staging_summary(output, ledger=sp.materialize_ledger(manifest, db))
        if summary.get("staging_error") or summary["staged_group_count"] != args.groups:
            raise AssertionError(summary)

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
    root = tk.Tk()
    root.withdraw()
    window = tk.Toplevel(root)
    app = gui.ReviewApp(window, output)
    window.update()
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
    heartbeat_running = True

    def beat():
        heartbeat.append(time.perf_counter())
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
                })
        finally:
            heartbeat_running = False
            window.destroy()
            root.destroy()

    steady = samples[1:]  # retain cold first operation in raw output
    result = {
        "fixture": "synthetic sealed session, PDF and hashed XLSX; not real textbook/decoder acceptance",
        "repository": str(args.repository.resolve()), "platform": platform.platform(),
        "fixture_content_sha256": fixture_digest,
        "python": sys.version, "pymupdf": fitz.VersionBind,
        "rows": args.rows, "initial_events": args.events, "staged_groups": args.groups,
        "operations": args.operations, "summary_excludes_first_operation": True,
        "p95_method": "nearest rank", "heartbeat_interval_ms": 10,
        "processing": summarize([s["processing_seconds"] for s in steady]),
        "button_callback": summarize([s["button_callback_seconds"] for s in steady]),
        "ui_max_heartbeat_gap": summarize([s["max_heartbeat_gap_seconds"] for s in steady]),
        "cooldown_ms": 500,
        "stages_inclusive": {name: summarize([s["stage_seconds"].get(name, 0) for s in steady])
                             for name in sorted({name for s in steady for name in s["stage_seconds"]})},
        "service_stages": {name: summarize([s["service_timings"].get(name, 0) for s in steady])
                           for name in sorted({name for s in steady for name in s["service_timings"]})},
        "samples": samples,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "samples"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
