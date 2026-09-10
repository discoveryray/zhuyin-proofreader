from __future__ import annotations

from contextlib import closing
from dataclasses import FrozenInstanceError, replace
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import ttk
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import global_exact_glyph_library as lib
import global_library_inspector as gui
import standalone_gui
from test_global_promotion_v580 import intent, sha, sql_rows
from test_global_legacy_migration_review_v580 import approval_request


ROOT = Path(__file__).resolve().parents[1]


def exact(name):
    return dict(kind=lib.TTF_GLYF_SHA256, style_group="", glyph_sha256=sha(name),
                identity_eligibility=lib.GLOBAL_ELIGIBLE_SIMPLE_GLYF_V1)


def legacy_intent(identity, reading="ㄇ"):
    return lib.make_migration_intent(identity, reading=reading, legacy_project_id="historical-project",
        legacy_project_sha256=sha("historical-project"), legacy_evidence_sha256=sha("historical-csv"),
        old_verification_level="VERIFIED_EXACT_GLYPH", status=lib.MIGRATION_INSUFFICIENT)


def populated_repo(root):
    repo = lib.GlobalExactGlyphRepository.resolved(root)
    for index, (state, reading) in enumerate(((lib.CANDIDATE, "ㄇ"), (lib.PROMOTION_READY, "ㄈ"),
                                            (lib.VERIFIED_GLOBAL, "ㄉ"), (lib.QUARANTINED_CONFLICT, "ㄊ")), 1):
        identity = exact(state)
        lib.deliver_global_direct_evidence(repo, [intent(index, reading, identity=identity)])
        if state != lib.CANDIDATE:
            lib.deliver_global_direct_evidence(repo, [intent(index + 10, reading, identity=identity)])
        if state in {lib.VERIFIED_GLOBAL, lib.QUARANTINED_CONFLICT}:
            lib.approve_global_exact_glyph(repo, **approval_request(repo, identity))
        if state == lib.QUARANTINED_CONFLICT:
            lib.deliver_global_direct_evidence(repo, [intent(index + 20, "ㄋ", identity=identity)])
        if state == lib.CANDIDATE:
            lib.deliver_global_migration(repo, [legacy_intent(identity, reading)])
    return repo


def durable_bytes(repo):
    # SQLite owns transient SHM and may create an empty WAL on read-only opens.
    return {p.name: p.read_bytes() for p in repo.path.parent.iterdir()
            if p.name != repo.path.name + "-shm" and
            not (p.name == repo.path.name + "-wal" and p.stat().st_size == 0)}


def wait_reader(reader, timeout=5):
    deadline = time.monotonic() + timeout
    while reader.loading and time.monotonic() < deadline:
        reader.drain()
        time.sleep(0.005)
    if reader.loading:
        raise AssertionError("inspection reader did not finish")


class InspectionRepositoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="global-inspector-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_absent_never_initializes_or_creates_parent(self):
        repo = lib.GlobalExactGlyphRepository.resolved(self.root / "missing" / "store")
        with patch.object(lib.GlobalExactGlyphRepository, "initialize", side_effect=AssertionError("write")), \
             patch.object(lib, "_connect_existing", side_effect=AssertionError("open")):
            snapshot = repo.load_inspection_snapshot()
        self.assertEqual(snapshot.store_status, lib.ABSENT)
        self.assertEqual(snapshot.records, ())
        self.assertFalse((self.root / "missing").exists())

    def test_empty_valid_database_is_distinct_from_absent_and_failures(self):
        repo = lib.GlobalExactGlyphRepository.resolved(self.root / "empty")
        repo.initialize()
        snapshot = repo.load_inspection_snapshot()
        self.assertEqual(snapshot.store_status, lib.VALID)
        self.assertEqual(snapshot.records, ())
        self.assertEqual(snapshot.metadata["generation"], 0)
        self.assertEqual(snapshot.metadata["schema_version"], "1.0")
        with self.assertRaises(TypeError):
            snapshot.metadata["generation"] = 20

    def test_all_states_and_linked_audit_details_counts_are_read_only(self):
        repo = populated_repo(self.root / "all")
        before_rows = {name: sql_rows(repo, name) for name in lib._REQUIRED_COLUMNS}
        before_bytes = durable_bytes(repo)
        before_decoder = repo.load_snapshot()
        snapshot = repo.load_inspection_snapshot()
        rows = {row.stored_state: row for row in snapshot.records}
        self.assertEqual(set(rows), {lib.CANDIDATE, lib.PROMOTION_READY, lib.VERIFIED_GLOBAL, lib.QUARANTINED_CONFLICT})
        candidate = rows[lib.CANDIDATE]
        self.assertEqual((candidate.direct_source_count, candidate.independent_source_count, len(candidate.legacy_candidates)), (1, 1, 1))
        self.assertEqual(candidate.legacy_candidates[0]["status"], lib.MIGRATION_INSUFFICIENT)
        ready = rows[lib.PROMOTION_READY]
        self.assertEqual((ready.direct_source_count, ready.independent_source_count, ready.approvals), (2, 2, ()))
        self.assertFalse(ready.global_reuse_allowed)
        verified = rows[lib.VERIFIED_GLOBAL]
        self.assertTrue(verified.global_reuse_allowed)
        self.assertEqual(verified.approvals[0]["status"], lib.APPROVED)
        self.assertEqual(verified.stored_active_reading, "ㄉ")
        quarantine = rows[lib.QUARANTINED_CONFLICT]
        self.assertEqual(quarantine.readings, ("ㄊ", "ㄋ"))
        self.assertEqual((quarantine.direct_source_count, quarantine.independent_source_count), (3, 3))
        self.assertFalse(quarantine.global_reuse_allowed)
        self.assertEqual(quarantine.stored_active_reading, "")
        self.assertTrue(any(row["status"] == lib.REVOKED for row in quarantine.approvals))
        self.assertEqual(quarantine.conflicts[0]["status"], lib.OPEN)
        self.assertIn("涵蓋所有保留讀音", gui.record_sections(quarantine)["概覽"])
        self.assertIn("不是同讀音", gui.COUNT_EXPLANATION)
        for record in snapshot.records:
            for table in (record.source_evidence, record.approvals, record.conflicts, record.legacy_candidates, record.provenance):
                self.assertTrue(all(row["glyph_id"] == record.glyph_id for row in table))
        with self.assertRaises(TypeError):
            candidate.source_evidence[0]["reading"] = "ㄅ"
        with self.assertRaises(FrozenInstanceError):
            candidate.global_reuse_allowed = True
        self.assertEqual(durable_bytes(repo), before_bytes)
        self.assertEqual({name: sql_rows(repo, name) for name in lib._REQUIRED_COLUMNS}, before_rows)
        self.assertEqual(replace(repo.load_snapshot(), loaded_at=before_decoder.loaded_at), before_decoder)

    def test_raw_details_and_overview_do_not_present_fabricated_preview(self):
        repo = populated_repo(self.root / "all")
        for row in repo.load_inspection_snapshot().records:
            sections = gui.record_sections(row)
            for name, text in sections.items():
                if name != "詳細資訊":
                    self.assertNotIn(row.glyph_id, text)
                    self.assertNotIn(row.identity.glyph_sha256, text)
                    self.assertNotIn('"revision"', text)
            self.assertIn("不是原始 exact glyph 預覽", sections["概覽"])
            self.assertIn("未載入", sections["來源證據"])
            self.assertIn(row.glyph_id, sections["詳細資訊"])
            self.assertIn('"provenance_event"', sections["詳細資訊"])

    def test_audit_rows_share_validated_read_transaction_under_concurrent_writer(self):
        repo = lib.GlobalExactGlyphRepository.resolved(self.root / "concurrent")
        lib.deliver_global_direct_evidence(repo, [intent()])
        validate = lib._validate_connection
        did_write = False

        def validate_and_write(connection, path):
            nonlocal did_write
            result = validate(connection, path)
            if not did_write:
                did_write = True
                lib.deliver_global_direct_evidence(repo, [intent(2)])
            return result

        with patch.object(lib, "_validate_connection", side_effect=validate_and_write):
            snapshot = repo.load_inspection_snapshot()
        self.assertEqual(snapshot.metadata["generation"], 1)
        self.assertEqual(snapshot.records[0].stored_state, lib.CANDIDATE)
        self.assertEqual(len(snapshot.records[0].source_evidence), 1)
        newer = repo.load_inspection_snapshot()
        self.assertEqual(newer.metadata["generation"], 2)
        self.assertEqual(newer.records[0].stored_state, lib.PROMOTION_READY)
        self.assertEqual(len(newer.records[0].source_evidence), 2)

    def test_nonempty_wal_read_preserves_durable_bytes_and_rejects_sql_writes(self):
        repo = lib.GlobalExactGlyphRepository.resolved(self.root / "wal")
        repo.initialize()
        with closing(sqlite3.connect(repo.path, isolation_level=None)) as keeper:
            keeper.execute("PRAGMA wal_autocheckpoint=0")
            keeper.execute("SELECT generation FROM library_meta").fetchall()
            lib.deliver_global_direct_evidence(repo, [intent()])
            wal = Path(str(repo.path) + "-wal")
            self.assertGreater(wal.stat().st_size, 0)
            before = durable_bytes(repo)
            connect = lib._connect_existing
            writes = []

            def guarded(path, **kwargs):
                self.assertTrue(kwargs["read_only"])
                self.assertEqual(kwargs["busy_timeout_ms"], lib.GLOBAL_LIBRARY_DEFAULT_BUSY_TIMEOUT_MS)
                connection = connect(path, **kwargs)
                def authorize(action, arg1, arg2, db, source):
                    if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
                                  sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_ALTER_TABLE):
                        writes.append((action, arg1))
                        return sqlite3.SQLITE_DENY
                    return sqlite3.SQLITE_OK
                connection.set_authorizer(authorize)
                return connection

            with patch.object(lib, "_connect_existing", side_effect=guarded):
                snapshot = repo.load_inspection_snapshot()
            self.assertEqual(len(snapshot.records), 1)
            self.assertEqual(writes, [])
            self.assertEqual(durable_bytes(repo), before)

    def test_corrupt_incompatible_invalid_busy_permission_are_typed_failures(self):
        for name, expected in (("corrupt", lib.GlobalLibraryCorruptError),
                               ("schema", lib.GlobalLibrarySchemaError),
                               ("row", lib.GlobalLibraryValidationError)):
            with self.subTest(name=name):
                repo = lib.GlobalExactGlyphRepository.resolved(self.root / name)
                if name == "corrupt":
                    repo.path.parent.mkdir()
                    repo.path.write_bytes(b"not a database")
                else:
                    lib.deliver_global_direct_evidence(repo, [intent()])
                    with closing(sqlite3.connect(repo.path)) as connection:
                        connection.execute("PRAGMA user_version=99" if name == "schema" else
                                           "UPDATE glyph_truth SET direct_source_count=999")
                        connection.commit()
                before = durable_bytes(repo)
                with self.assertRaises(expected):
                    repo.load_inspection_snapshot()
                self.assertEqual(durable_bytes(repo), before)
        repo = lib.GlobalExactGlyphRepository.resolved(self.root / "errors")
        repo.initialize()
        for exception, expected in ((sqlite3.OperationalError("database is locked"), lib.GlobalLibraryBusyError),
                                    (PermissionError("permission denied"), lib.GlobalLibraryPermissionError)):
            with patch.object(lib, "_connect_existing", side_effect=exception), self.assertRaises(expected):
                repo.load_inspection_snapshot()

    def test_filters_use_stored_state_and_search_exact_identification(self):
        snapshot = populated_repo(self.root / "filter").load_inspection_snapshot()
        self.assertEqual(len(gui.filter_records(snapshot)), 4)
        self.assertEqual(gui.filter_records(snapshot, "待核准")[0].stored_state, lib.PROMOTION_READY)
        self.assertEqual(gui.filter_records(snapshot, query=" ㄉ ")[0].stored_state, lib.VERIFIED_GLOBAL)
        for row in snapshot.records:
            self.assertEqual(gui.filter_records(snapshot, query=row.identity.glyph_sha256.upper()), (row,))
            self.assertEqual(gui.filter_records(snapshot, query=row.glyph_id), (row,))
        self.assertEqual(gui.filter_records(snapshot, query="no-match"), ())
        self.assertEqual(gui.filter_records(None), ())

    def test_empty_candidate_and_cff_style_identities_are_distinct(self):
        repo = lib.GlobalExactGlyphRepository.resolved(self.root / "identity")
        repo.initialize()
        for style in ("BIAOKAI_W5", "BIAOKAI_W6"):
            identity = dict(kind=lib.CFF_GLYPH_SHA256, style_group=style, glyph_sha256=sha("same-recording"),
                            identity_eligibility=lib.GLOBAL_ELIGIBLE_COMPLETE_CFF_RECORDING_V1)
            with repo.write_transaction() as tx:
                tx.insert_candidate_identity(identity)
                tx.bump_generation()
        snapshot = repo.load_inspection_snapshot()
        self.assertEqual(len(snapshot.records), 2)
        self.assertNotEqual(snapshot.records[0].glyph_id, snapshot.records[1].glyph_id)
        selected = gui.filter_records(snapshot, query="biaokai_w5")
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].identity.style_group, "BIAOKAI_W5")
        self.assertEqual(selected[0].readings, ())
        self.assertEqual(selected[0].source_evidence, ())
        self.assertFalse(selected[0].global_reuse_allowed)
        self.assertIn("尚無讀音", gui.record_sections(selected[0])["概覽"])

    def test_source_counts_do_not_imply_occurrence_or_review_quorum(self):
        repo = lib.GlobalExactGlyphRepository.resolved(self.root / "correlation")
        lib.deliver_global_direct_evidence(repo, [intent(), intent(2, source_occurrence_id="occ_" + sha("occ-1"))])
        row = repo.load_inspection_snapshot().records[0]
        self.assertEqual((row.direct_source_count, row.independent_source_count), (2, 2))
        self.assertEqual(row.stored_state, lib.CANDIDATE)
        self.assertFalse(row.global_reuse_allowed)
        self.assertEqual(row.approvals, ())
        self.assertIn("不是同讀音", gui.record_sections(row)["概覽"])


class LegacyInspectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = subprocess.check_output(["git", "show", "d2d558808bd2902857fad30f824d8bdc352beb73:global_exact_glyph_library.py"], cwd=ROOT)
        cls.old = types.ModuleType("_inspector_frozen_v0")
        sys.modules[cls.old.__name__] = cls.old
        cls.addClassCleanup(sys.modules.pop, cls.old.__name__)
        exec(compile(source, "frozen-v0", "exec"), cls.old.__dict__)

    def test_stored_verified_v0_suppression_never_implies_durable_quarantine_or_revocation(self):
        with tempfile.TemporaryDirectory(prefix="inspector-v0-") as temporary:
            old_repo = self.old.GlobalExactGlyphRepository.resolved(Path(temporary) / "store")
            identity = exact("legacy")
            self.old.deliver_global_direct_evidence(old_repo, [intent(1, identity=identity), intent(2, identity=identity)])
            self.old.approve_global_exact_glyph(old_repo, **approval_request(old_repo, identity))
            with old_repo.write_transaction() as tx:
                args = dict(glyph_id=self.old.compute_global_glyph_id(**identity), reading="ㄆ",
                    legacy_project_id="historical", legacy_project_sha256=sha("project"),
                    legacy_evidence_sha256=sha("evidence"), old_verification_level="USER_VERIFIED_SINGLE")
                row = dict(import_id=self.old.compute_migration_import_id(**args), **args, status="CANDIDATE",
                           imported_at="2026-09-09T00:00:00Z")
                columns = self.old._REQUIRED_COLUMNS["migration_candidate"]
                tx._connection.execute("INSERT INTO migration_candidate (" + ",".join(columns) + ") VALUES (" +
                    ",".join("?" for _ in columns) + ")", tuple(row[key] for key in columns))
                tx.bump_generation()
            self.assertEqual(old_repo.load_snapshot().store_status, self.old.VALID)
            repo = lib.GlobalExactGlyphRepository.resolved(old_repo.path.parent)
            before = durable_bytes(repo)
            rows_before = {name: sql_rows(repo, name) for name in lib._REQUIRED_COLUMNS}
            decoder = repo.load_snapshot()
            digest = lib.canonical_logical_subset_digest(decoder, [identity]).sha256
            snapshot = repo.load_inspection_snapshot()
            record = snapshot.records[0]
            self.assertEqual(record.stored_state, lib.VERIFIED_GLOBAL)
            self.assertEqual(record.stored_active_reading, "ㄅ")
            self.assertFalse(record.global_reuse_allowed)
            self.assertEqual(record.reuse_block_reason, "LEGACY_V0_READ_CONFLICT")
            self.assertEqual(record.readings, ("ㄅ", "ㄆ"))
            self.assertEqual((record.direct_source_count, record.independent_source_count, len(record.legacy_candidates)), (2, 2, 1))
            self.assertEqual(record.conflicts, ())
            self.assertEqual(record.approvals[0]["status"], lib.APPROVED)
            self.assertEqual(gui.filter_records(snapshot, "已驗證"), (record,))
            self.assertEqual(gui.filter_records(snapshot, gui.READ_CONFLICT_LABEL), (record,))
            self.assertEqual(gui.record_status(record), gui.READ_CONFLICT_LABEL)
            sections = gui.record_sections(record)
            self.assertIn("未寫入隔離", sections["概覽"])
            self.assertIn("未撤銷", sections["衝突紀錄"])
            self.assertIn("已核准（儲存紀錄）", sections["核准紀錄"])
            self.assertIn("quorum 貢獻：0", sections["舊資料候選"])
            self.assertEqual(durable_bytes(repo), before)
            self.assertEqual({name: sql_rows(repo, name) for name in lib._REQUIRED_COLUMNS}, rows_before)
            self.assertEqual(lib.canonical_logical_subset_digest(repo.load_snapshot(), [identity]).sha256, digest)
            self.assertFalse(repo.load_snapshot().trusted_identities)


class InspectionReaderTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="inspector-reader-")
        self.addCleanup(temporary.cleanup)
        self.repo = populated_repo(Path(temporary.name) / "store")
        self.snapshot = self.repo.load_inspection_snapshot()

    def reader(self, loader):
        reader = gui.InspectionReader(loader)
        self.addCleanup(reader.close)
        return reader

    def test_refresh_failure_clears_old_data_and_keeps_last_success_time(self):
        results = iter((self.snapshot, lib.GlobalLibraryBusyError("locked")))
        def loader():
            result = next(results)
            if isinstance(result, Exception):
                raise result
            return result
        reader = self.reader(loader)
        reader.refresh()
        wait_reader(reader)
        self.assertEqual(reader.snapshot, self.snapshot)
        self.assertEqual(reader.last_successful_read_at, self.snapshot.loaded_at)
        reader.refresh()
        self.assertIsNone(reader.snapshot)
        wait_reader(reader)
        self.assertIsNone(reader.snapshot)
        self.assertEqual(reader.last_successful_read_at, self.snapshot.loaded_at)
        self.assertIn("忙碌", gui.database_status(reader))
        self.assertIn("清單已清除", gui.database_status(reader))

    def test_distinct_database_messages_and_unexpected_error_fail_closed(self):
        reader = self.reader(lambda: self.snapshot)
        empty = replace(self.snapshot, records=())
        reader.snapshot = empty
        self.assertIn("正常，但尚無", gui.database_status(reader))
        reader.snapshot = replace(empty, store_status=lib.ABSENT)
        self.assertIn("尚未建立", gui.database_status(reader))
        for status in gui.ERROR_LABELS:
            reader.snapshot = None
            reader.error_status = status
            self.assertIn(gui.ERROR_LABELS[status], gui.database_status(reader))
        broken = self.reader(lambda: 1 / 0)
        broken.refresh()
        wait_reader(broken)
        self.assertIsNone(broken.snapshot)
        self.assertEqual(broken.error_status, "READ_FAILED")

    def test_latest_request_wins_and_worker_never_calls_main_thread_callback(self):
        entered, release = threading.Event(), threading.Event()
        ids, calls = [], []
        newer = replace(self.snapshot, loaded_at="2026-09-10T01:02:03Z")
        def loader():
            ids.append(threading.get_ident())
            calls.append(1)
            if len(calls) == 1:
                entered.set()
                if not release.wait(5):
                    raise AssertionError("test release timeout")
                return self.snapshot
            return newer
        reader = self.reader(loader)
        reader.refresh()
        self.assertTrue(entered.wait(5))
        reader.refresh()
        reader.refresh()  # Replace pending request; never start unbounded workers.
        release.set()
        wait_reader(reader)
        self.assertEqual(len(calls), 2)
        self.assertEqual(reader.snapshot, newer)
        reader.results.put((1, self.snapshot, "", ""))
        self.assertFalse(reader.drain())
        self.assertEqual(reader.snapshot, newer)
        self.assertEqual(len(set(ids)), 1)
        self.assertNotEqual(ids[0], threading.get_ident())

    def test_close_drops_delayed_result_without_waiting_for_worker(self):
        entered, release = threading.Event(), threading.Event()
        def loader():
            entered.set()
            release.wait(5)
            return self.snapshot
        reader = self.reader(loader)
        reader.refresh()
        self.assertTrue(entered.wait(5))
        start = time.monotonic()
        reader.close()
        self.assertLess(time.monotonic() - start, 0.5)
        release.set()
        reader.worker.join(5)
        self.assertFalse(reader.worker.is_alive())
        self.assertFalse(reader.drain())
        self.assertIsNone(reader.snapshot)


class InspectorTkTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="inspector-tk-")
        self.addCleanup(temporary.cleanup)
        self.repo = populated_repo(Path(temporary.name) / "store")
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            raise unittest.SkipTest(f"Tk display unavailable: {exc}")
        self.addCleanup(self.destroy_root)
        self.root.withdraw()
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)

    def destroy_root(self):
        try:
            self.root.destroy()
        except tk.TclError:
            pass  # The root-destruction test already destroyed this interpreter.

    def settle(self, window):
        deadline = time.monotonic() + 5
        while window.reader.loading and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.root.update()
        self.assertFalse(window.reader.loading)
        self.assertEqual(self.errors, [])

    def test_actual_widgets_filter_select_refresh_fail_and_close(self):
        fail = False
        def loader():
            if fail:
                raise lib.GlobalLibraryPermissionError("fixture permission denied")
            return self.repo.load_inspection_snapshot()
        window = gui.GlobalLibraryInspector(self.root, loader=loader)
        self.settle(window)
        self.assertEqual(len(window.tree.get_children()), 4)
        window.filter_status.set("已驗證")
        self.assertEqual(len(window.tree.get_children()), 1)
        selected = window.tree.get_children()[0]
        window.tree.selection_set(selected)
        window.tree.event_generate("<<TreeviewSelect>>")
        self.root.update()
        self.assertIn("本庫允許重用", window.texts["概覽"].get("1.0", "end"))
        self.assertIn("綁定證據", window.texts["核准紀錄"].get("1.0", "end"))
        window.search_query.set("no-match")
        self.assertEqual(window.tree.get_children(), ())
        self.assertNotIn("本庫允許重用", window.texts["概覽"].get("1.0", "end"))
        window.search_query.set("")
        last_read = window.reader.last_successful_read_at
        fail = True
        window.refresh_button.invoke()
        self.assertEqual(window.tree.get_children(), ())
        self.settle(window)
        self.assertIn("權限不足", window.status_text.get())
        self.assertEqual(window.reader.last_successful_read_at, last_read)
        self.assertEqual(window.tree.get_children(), ())
        self.assertNotIn(selected, window.texts["詳細資訊"].get("1.0", "end"))
        window.destroy()
        self.root.update()
        self.assertTrue(window.reader.closed.is_set())
        self.assertIsNone(window._poll_id)
        self.assertEqual(self.errors, [])

    def test_standalone_entry_needs_no_project_and_reuses_open_window(self):
        app = standalone_gui.App(self.root)
        self.assertEqual(app.input.get(), "")
        self.assertEqual(app.output.get(), "")
        def find(widget):
            for child in widget.winfo_children():
                if isinstance(child, ttk.Button) and child.cget("text") == "全域字形庫":
                    return child
                found = find(child)
                if found is not None:
                    return found
            return None
        button = find(self.root)
        self.assertIsNotNone(button)
        with patch.object(lib.GlobalExactGlyphRepository, "resolved", return_value=self.repo):
            button.invoke()
            first = app.global_library_window
            self.settle(first)
            button.invoke()
            self.assertIs(app.global_library_window, first)
        first.destroy()
        for callback in self.root.tk.splitlist(self.root.tk.call("after", "info")):
            self.root.after_cancel(callback)
        self.assertEqual(app.input.get(), "")
        self.assertEqual(app.output.get(), "")

    def test_destroy_root_during_read_does_not_leave_tk_callbacks(self):
        entered, release = threading.Event(), threading.Event()
        snapshot = self.repo.load_inspection_snapshot()
        def loader():
            entered.set()
            release.wait(5)
            return snapshot
        window = gui.GlobalLibraryInspector(self.root, loader=loader)
        self.assertTrue(entered.wait(5))
        self.root.destroy()
        release.set()
        window.reader.worker.join(5)
        self.assertTrue(window.reader.closed.is_set())
        self.assertIsNone(window._poll_id)
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
