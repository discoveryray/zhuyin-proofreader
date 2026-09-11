"""Isolated preflight regressions; no live project or Global store is opened."""
from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import types
import unittest
from unittest.mock import patch

import actual_review as review
import global_exact_glyph_library as lib
import legacy_global_migration as migration
import legacy_migration_inspection as service
import legacy_migration_inspector as gui
import standalone_gui
from test_global_legacy_migration_v580 import Project, ROOT, fixture_path_alias, tree_bytes
import test_global_legacy_migration_review_v580 as historic
from test_global_promotion_v580 import FakeDocument, intent, sql_rows
from test_exact_glyph_identity_v580 import composite_glyph, synthetic_sfnt


def wait_reader(reader):
    deadline = time.monotonic() + 8
    while reader.loading and time.monotonic() < deadline:
        reader.drain()
        time.sleep(0.01)
    reader.drain()
    if reader.loading:
        raise AssertionError("reader timed out")


class MigrationInspectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="p6d-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.project = Project(self.base / "project", count=2)
        self.repo = lib.GlobalExactGlyphRepository.resolved(self.base / "global")

    def inspect(self, project=None, roots=(), repo=None):
        return service.inspect_legacy_migration((project or self.project).root, pdf_roots=roots,
            global_library_root=(repo or self.repo).path.parent)

    def assert_blocked(self, report):
        self.assertEqual(report.status, "BLOCKED", report.details)
        self.assertIsNone(report.counts)
        self.assertEqual(report.items, ())
        self.assertIn("沒有可用的成功統計", service.report_summary(report))
        self.assertNotIn("import 項目 0", service.report_summary(report))
        self.assertTrue(report.details)

    def test_real_ttf_cff_mapping_sources_counts_and_relocation_keep_import_identity(self):
        alias = fixture_path_alias(self.base)
        self.assertNotEqual(str(alias), str(self.base.resolve()))
        self.assertEqual(alias.resolve(), self.base.resolve())
        for cff in (False, True):
            with self.subTest(cff=cff):
                project = Project(alias / str(cff), count=2, cff=cff)
                report = self.inspect(project)
                self.assertEqual(report.status, "VALID", report.details)
                self.assertEqual(report.counts, (1, 1, 2, 2, 0))
                self.assertEqual(report.items[0].source_file, project.learning_file)
                self.assertEqual(report.items[0].reading, "ㄅ")
                detail = json.loads(report.items[0].details)
                targets = detail["reconfirmation_targets"]
                self.assertEqual([target["bbox"] for target in targets],
                                 [list(row[key] for key in ("x0", "y0", "x1", "y1"))
                                  for row in sorted(project.rows, key=lambda row: row["occurrence_id"])])
                self.assertTrue(all(target["physical_page"] == 1 for target in targets))
                self.assertTrue(all("reading" not in target for target in targets))
                original_pdf = project.pdf.resolve()
                self.assertIn("原檔：" + str(original_pdf), report.items[0].targets_text)
                moved = alias / (str(cff) + "-moved")
                shutil.copytree(project.root, moved)
                project.pdf.unlink()
                relocated = service.inspect_legacy_migration(moved, global_library_root=self.repo.path.parent)
                self.assertEqual(json.loads(relocated.items[0].details), detail)
                self.assertIn("原檔：" + str((moved / "book.pdf").resolve()), relocated.items[0].targets_text)
                self.assertNotIn(str(original_pdf), relocated.items[0].targets_text)
        self.assertFalse(self.repo.path.parent.exists())

    def test_insufficient_conflict_and_noneligible_are_not_successful_imports_or_quorum(self):
        self.project.learning["source_count"] = "1"
        self.project.learning["verification_level"] = "USER_VERIFIED_SINGLE"
        self.project.learning["source_examples"] = "occ_" + "a" * 64
        self.project.save_learning()
        report = self.inspect()
        self.assertEqual(report.items[0].status, "INSUFFICIENT")
        self.assertEqual(report.counts, (1, 1, 0, 0, 0))
        self.assertIn("沒有可查證", report.items[0].targets_text)
        self.project.save_learning([])
        self.project.conflict(ids="|".join(row["occurrence_id"] for row in self.project.rows))
        report = self.inspect()
        self.assertEqual([item.status for item in report.items], ["CONFLICT", "CONFLICT"])
        self.assertEqual({item.source_file for item in report.items}, {review.GLYPH_CONFLICT_FILE})
        self.assertEqual(report.counts, (2, 1, 4, 2, 0))
        self.assertIn("legacy quorum 貢獻固定為 0", service.report_summary(report))
        self.assertEqual(json.loads(report.details)["global_quarantined_identities"], [])
        unsupported = Project(self.base / "unsupported", record=composite_glyph())
        font = synthetic_sfnt([composite_glyph()], font_name_marker=b"test")
        with patch.object(migration.fitz, "open", return_value=FakeDocument(font)):
            report = self.inspect(unsupported)
        self.assertEqual(report.status, "VALID", report.details)
        self.assertEqual(report.items[0].status, "NON_GLOBAL_ELIGIBLE")
        self.assertEqual(report.counts, (0, 0, 0, 0, 1))
        self.assertIn("reason", report.items[0].details)
        self.assertFalse(self.repo.path.parent.exists())

    def test_missing_pdf_explicit_root_only_and_wrong_sha(self):
        alias = fixture_path_alias(self.base)
        self.assertNotEqual(str(alias), str(self.base.resolve()))
        self.assertEqual(alias.resolve(), self.base.resolve())
        moved = alias / "book.pdf"
        self.project.pdf.rename(moved)
        self.assert_blocked(self.inspect())
        with patch.object(Path, "rglob", side_effect=AssertionError("no scans")), \
             patch.object(Path, "glob", side_effect=AssertionError("no scans")):
            report = self.inspect(roots=(alias,))
        self.assertEqual(report.status, "VALID", report.details)
        self.assertIn("原檔：" + str(moved.resolve()), report.items[0].targets_text)
        moved.write_bytes(b"wrong PDF")
        self.assert_blocked(self.inspect(roots=(alias,)))

    def test_invalid_duplicate_stale_missing_and_pending_block_whole_batch_without_repair(self):
        for defect in ("missing", "duplicate", "reading", "stale", "pending", "bbox"):
            with self.subTest(defect=defect):
                project = Project(self.base / defect)
                if defect == "missing":
                    (project.evidence / review.USER_CFF_FILE).unlink()
                elif defect == "duplicate":
                    project.save_learning([project.learning, project.learning])
                elif defect == "reading":
                    project.learning["bopomofo"] = "not Bopomofo"
                    project.save_learning()
                elif defect == "stale":
                    project.learning["glyph_sha256"] = "f" * 64
                    project.save_learning()
                elif defect == "pending":
                    (project.evidence / "global_exact_glyph_project_transaction.json").write_text("{}")
                else:
                    # The pre-existing full bbox matrix covers resealed input;
                    # this route must likewise preserve its complete rejection.
                    project.manifest["records"][0]["x1"] = 9999
                    historic.seal(project)
                before = tree_bytes(project.root)
                self.assert_blocked(self.inspect(project))
                self.assertEqual(tree_bytes(project.root), before)
        self.assertFalse(self.repo.path.parent.exists())

    def test_one_plan_and_one_snapshot_with_post_snapshot_source_bracketing(self):
        real = self.repo.load_snapshot
        calls = []
        def read():
            calls.append(1)
            snapshot = real()
            self.project.learning["notes"] = "changed during Global read"
            self.project.save_learning()
            return snapshot
        with patch.object(lib.GlobalExactGlyphRepository, "load_snapshot", side_effect=read), \
             patch.object(migration, "build_migration_plan", wraps=migration.build_migration_plan) as plan:
            self.assert_blocked(self.inspect())
            self.assertEqual(plan.call_count, 1)
        self.assertEqual(calls, [1])
        self.assertFalse(self.repo.path.parent.exists())

    def test_absent_empty_corrupt_incompatible_invalid_and_busy_global_distinguished(self):
        self.assertEqual(self.inspect().global_status, lib.ABSENT)
        self.assertFalse(self.repo.path.parent.exists())
        for state in ("empty", "corrupt", "incompatible", "invalid", "busy"):
            with self.subTest(state=state):
                repo = lib.GlobalExactGlyphRepository.resolved(self.base / state)
                if state == "corrupt":
                    repo.path.parent.mkdir()
                    repo.path.write_bytes(b"broken SQLite")
                else:
                    repo.initialize()
                    if state == "invalid":
                        lib.deliver_global_direct_evidence(repo, [intent()])
                    if state in {"incompatible", "invalid"}:
                        with closing(sqlite3.connect(repo.path)) as connection:
                            connection.execute("PRAGMA user_version=999" if state == "incompatible" else
                                               "UPDATE glyph_truth SET direct_source_count=999")
                            connection.commit()
                before = historic.store_evidence_bytes(repo)
                if state == "busy":
                    # Real exclusive SQLite locking is covered by the repository
                    # tests; verify the UI retains the service BUSY category.
                    with patch.object(lib.GlobalExactGlyphRepository, "load_snapshot",
                                      side_effect=lib.GlobalLibraryBusyError("database is locked")):
                        report = self.inspect(repo=repo)
                else:
                    report = self.inspect(repo=repo)
                if state == "empty":
                    self.assertEqual((report.status, report.global_status), ("VALID", lib.VALID))
                else:
                    self.assert_blocked(report)
                    self.assertEqual(report.global_status, {"corrupt": lib.CORRUPT,
                        "incompatible": lib.SCHEMA_INCOMPATIBLE, "invalid": lib.VALIDATION_FAILED,
                        "busy": lib.BUSY}[state])
                self.assertEqual(historic.store_evidence_bytes(repo), before)

    def test_durable_global_conflict_and_source_plan_are_separate_readonly_snapshots(self):
        lib.deliver_global_direct_evidence(self.repo, [intent(1, identity=self.project.exact),
                                                     intent(2, reading="ㄆ", identity=self.project.exact)])
        before_source, before_db = tree_bytes(self.project.root), historic.store_evidence_bytes(self.repo)
        tables = {name: sql_rows(self.repo, name) for name in lib._REQUIRED_COLUMNS}
        with patch.object(lib.GlobalExactGlyphRepository, "initialize", side_effect=AssertionError("write")), \
             patch.object(lib.GlobalExactGlyphRepository, "write_transaction", side_effect=AssertionError("write")), \
             patch.object(review, "ensure_user_evidence_files", side_effect=AssertionError("write")):
            report = self.inspect()
            self.assertEqual([item.status for item in report.items], ["CANDIDATE", "DURABLE_QUARANTINE"])
            self.assertEqual(report.counts, (1, 1, 2, 2, 0))
            for status in ("全部", *service.ITEM_LABELS.values()):
                service.filter_items(report, status)
        self.assertEqual(tree_bytes(self.project.root), before_source)
        self.assertEqual(historic.store_evidence_bytes(self.repo), before_db)
        self.assertEqual({name: sql_rows(self.repo, name) for name in lib._REQUIRED_COLUMNS}, tables)

    def test_actual_pinned_v0_suppression_not_durable_quarantine_or_plan_conflict(self):
        fixture_class = historic.FrozenV0CompatibilityTests
        fixture_class.setUpClass()
        self.addCleanup(fixture_class.doClassCleanups)
        case = fixture_class()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.exact = self.project.exact
        _, repo = case.fixture(["ㄅ", "ㄆ"], state="VERIFIED_GLOBAL")
        tables = {name: sql_rows(repo, name) for name in lib._REQUIRED_COLUMNS}
        before = historic.store_evidence_bytes(repo)
        report = self.inspect(repo=repo)
        self.assertEqual([item.status for item in report.items], ["CANDIDATE", "READ_SIDE_SUPPRESSION"])
        self.assertIn("VERIFIED_GLOBAL", report.items[1].details)
        self.assertIn("不能宣稱已持久化", report.items[1].explanation)
        self.assertEqual(json.loads(report.details)["global_quarantined_identities"], [])
        self.assertEqual({name: sql_rows(repo, name) for name in lib._REQUIRED_COLUMNS}, tables)
        self.assertEqual(historic.store_evidence_bytes(repo), before)

    def test_live_source_watch_invalidates_even_same_size_and_timestamp_without_new_plan(self):
        alias = fixture_path_alias(self.project.evidence)
        self.assertNotEqual(str(alias), str(self.project.evidence.resolve()))
        self.assertEqual(alias.resolve(), self.project.evidence.resolve())
        path = alias / review.GLYPH_PROVENANCE_FILE
        path.unlink()  # Optional absence is part of the exact snapshot.
        calls = []
        def load(project, *, pdf_roots):
            calls.append(project)
            return service.inspect_legacy_migration(project, pdf_roots=pdf_roots,
                                                    global_library_root=self.repo.path.parent)
        reader = gui.MigrationInspectionReader(load, watch_interval=0.02)
        self.addCleanup(reader.close)
        reader.select(self.project.root)
        self.assertTrue(reader.start())
        wait_reader(reader)
        self.assertEqual(reader.report.status, "VALID", reader.report.details)
        self.assertIsNone(dict(reader.report.source_input_hashes)[str(path.resolve())])
        path.write_text("new optional evidence", encoding="utf-8")
        deadline = time.monotonic() + 4
        while reader.report.status == "VALID" and time.monotonic() < deadline:
            reader.drain()
            time.sleep(0.01)
        self.assert_blocked(reader.report)
        self.assertEqual(len(calls), 1)
        path.unlink()
        reader.start()
        wait_reader(reader)
        self.assertEqual(reader.report.status, "VALID", reader.report.details)
        learning = self.project.evidence / self.project.learning_file
        stamp = learning.stat()
        raw = learning.read_bytes()
        changed = raw.replace("ㄅ".encode(), "ㄆ".encode())
        self.assertNotEqual(raw, changed)
        self.assertEqual(len(raw), len(changed))
        learning.write_bytes(changed)
        os.utime(learning, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        deadline = time.monotonic() + 4
        while reader.report.status == "VALID" and time.monotonic() < deadline:
            reader.drain()
            time.sleep(0.01)
        self.assert_blocked(reader.report)
        self.assertEqual(len(calls), 2)
        self.assertFalse(self.repo.path.parent.exists())

    def test_readonly_adapter_import_chain_has_no_expected_resolver_or_cli(self):
        script = ("import sys; import legacy_migration_inspection; "
                  "assert not any(x in sys.modules for x in "
                  "('standalone_proofread','generate_zhuyin_review','migrate_legacy_global_candidates')); "
                  "print('actual-only adapter imports verified')")
        result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("verified", result.stdout)


class MigrationReaderTests(unittest.TestCase):
    def test_selection_no_autorun_duplicate_switch_failure_and_late_result(self):
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        calls = []
        def load(project, *, pdf_roots):
            calls.append((project, pdf_roots, threading.get_ident()))
            if project == "A":
                started.set()
                release.wait(5)
            if project == "bad":
                raise ValueError("invalid source")
            return service.MigrationInspectionReport(project, pdf_roots, "VALID", "ok", counts=(0, 0, 0, 0, 0))
        reader = gui.MigrationInspectionReader(load)
        self.addCleanup(reader.close)
        reader.select("A", ("pdf-A",))
        self.assertEqual(calls, [])
        self.assertTrue(reader.start())
        self.assertTrue(started.wait(3))
        for _ in range(50):
            self.assertFalse(reader.start())
        reader.select("B", ("pdf-B",))
        self.assertIsNone(reader.report)
        self.assertFalse(reader.start())
        release.set()
        wait_reader(reader)
        self.assertIsNone(reader.report)
        self.assertEqual(len(calls), 1)
        self.assertTrue(reader.start())
        wait_reader(reader)
        self.assertEqual(reader.report.project_output, "B")
        self.assertEqual(reader.report.pdf_roots, ("pdf-B",))
        reader.select("bad")
        self.assertIsNone(reader.report)
        reader.start()
        wait_reader(reader)
        self.assertEqual(reader.report.status, "BLOCKED")
        self.assertTrue(all(tid != threading.get_ident() for _, _, tid in calls))
        reader.select(None)
        self.assertFalse(reader.start())
        self.assertIsNone(reader.report)

    def test_close_during_work_stops_watcher_and_never_publishes_late_result(self):
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def load(project, *, pdf_roots):
            started.set()
            release.wait(5)
            return service.MigrationInspectionReport(project, pdf_roots, "VALID", "ok", counts=(0, 0, 0, 0, 0))
        reader = gui.MigrationInspectionReader(load)
        reader.select("A")
        reader.start()
        self.assertTrue(started.wait(3))
        reader.close()
        release.set()
        reader.worker.join(3)
        self.assertFalse(reader.worker.is_alive())
        self.assertTrue(reader.results.empty())
        self.assertFalse(reader.drain())
        self.assertIsNone(reader.report)


class MigrationTkTests(unittest.TestCase):
    def setUp(self):
        try:
            self.tk = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        self.tk.withdraw()
        self.addCleanup(self.tk.destroy)

    def pump(self, window):
        deadline = time.monotonic() + 8
        while window.reader.loading and time.monotonic() < deadline:
            self.tk.update()
            time.sleep(0.01)
        self.tk.update()
        self.assertFalse(window.reader.loading)

    def test_main_entry_uses_only_explicit_project_and_open_or_cancel_never_loads(self):
        calls = []
        window = gui.LegacyMigrationInspector(self.tk, project_output="A", loader=lambda *a, **k: calls.append(a))
        with patch.object(gui.filedialog, "askdirectory", return_value=""):
            window.select_button.invoke()
            window.pdf_button.invoke()
        self.tk.update()
        self.assertEqual(calls, [])
        self.assertIsNone(window.reader.report)
        window.destroy()
        app = types.SimpleNamespace(root=self.tk, output=types.SimpleNamespace(get=lambda: "A"),
                                    legacy_migration_window=None)
        with patch.object(gui, "LegacyMigrationInspector") as create:
            standalone_gui.App.open_legacy_migration(app)
            create.assert_called_once_with(self.tk, project_output="A")

    def test_widgets_filters_details_failed_clear_source_switch_duplicate_and_destroy(self):
        item = service.MigrationInspectionItem("CANDIDATE", "source.csv", "ㄅ", "TTF 完整 glyf", "請查看原頁",
                                               "實體頁碼：1；bbox (1,2,3,4)", "import_id: test")
        calls = []
        def load(project, *, pdf_roots):
            calls.append((project, pdf_roots))
            if project == "bad":
                raise ValueError("invalid source")
            return service.MigrationInspectionReport(project, pdf_roots, "VALID", "ok", lib.ABSENT,
                                                      (item,), (1, 1, 1, 1, 0), details="raw report")
        window = gui.LegacyMigrationInspector(self.tk, project_output="A", loader=load)
        self.assertEqual(calls, [])
        window.start_button.invoke()
        window.start_button.invoke()
        self.pump(window)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(window.tree.get_children()), 1)
        window.tree.selection_set("0")
        window._show_item()
        self.assertIn("請查看原頁", window.item_text.get("1.0", "end"))
        self.assertIn("bbox", window.targets_text.get("1.0", "end"))
        self.assertIn("import_id", window.details_text.get("1.0", "end"))
        window.filter_status.set(service.ITEM_LABELS["INSUFFICIENT"])
        self.assertEqual(window.tree.get_children(), ())
        window.filter_status.set("全部")
        window.query.set("source.csv")
        self.assertEqual(len(window.tree.get_children()), 1)
        window.query.set("unknown")
        self.assertEqual(window.tree.get_children(), ())
        window.query.set("")
        with patch.object(gui.filedialog, "askdirectory", return_value="pdf-root"):
            window.pdf_button.invoke()
        self.assertEqual(window.tree.get_children(), ())
        self.assertIsNone(window.reader.report)
        self.assertEqual(len(calls), 1)
        window.start_button.invoke()
        self.pump(window)
        self.assertEqual(calls[-1], ("A", ("pdf-root",)))
        window.set_project("bad")
        self.assertEqual(window.reader.pdf_roots, ())
        self.assertEqual(window.tree.get_children(), ())
        window.start_button.invoke()
        self.pump(window)
        self.assertIn("BLOCKED", window.summary_text.get())
        self.assertIn("invalid source", window.report_text.get("1.0", "end"))
        self.assertNotIn("import_id", window.details_text.get("1.0", "end"))
        self.assertEqual(window.tree.get_children(), ())
        window.destroy()
        self.assertTrue(window.reader.closed.is_set())


if __name__ == "__main__":
    unittest.main()
