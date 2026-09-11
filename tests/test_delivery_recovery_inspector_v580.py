from __future__ import annotations

from contextlib import closing
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import tkinter as tk
from tkinter import ttk
import types
import unittest
from unittest.mock import patch

import delivery_recovery_inspector as gui
import delivery_recovery_service as service
import global_exact_glyph_library as lib
import global_glyph_promotion as promotion
import standalone_gui
from standalone_proofread import project_actual_evidence_root
from test_global_promotion_v580 import intent, sha, sql_rows
from test_global_library_inspector_v580 import durable_bytes


def plan():
    oid = "occ_" + sha("affected")
    return {"schema_version": "1.0", "affected_occurrence_ids": [oid],
            "checked_postconditions": [{"group_id": "agr_" + "a" * 24, "occurrence_id": oid, "reading": "ㄅ"}]}


def project_bytes(project):
    return {str(p.relative_to(project)): p.read_bytes() for p in project.rglob("*") if p.is_file()}


def wait_reader(reader):
    deadline = time.monotonic() + 8
    while reader.loading and time.monotonic() < deadline:
        reader.drain()
        time.sleep(0.005)
    if reader.loading:
        raise AssertionError("delivery reader did not finish")


class DeliveryRecoveryServiceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="delivery-")
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.project = self.base / "project"
        self.project.mkdir()
        self.root = project_actual_evidence_root(self.project)
        self.repo = lib.GlobalExactGlyphRepository.resolved(self.base / "global", busy_timeout_ms=5)

    def inspect(self, **kwargs):
        return service.inspect_delivery_recovery(self.project, repository=self.repo, **kwargs)

    def commit(self):
        with promotion.direct_visual_project_transaction(self.root) as bind:
            bind(plan())
            promotion.enqueue_promotion_intents(self.root, [intent()])

    def test_no_selection_does_not_resolve_or_read(self):
        with patch.object(lib.GlobalExactGlyphRepository, "resolved", side_effect=AssertionError("resolve")), \
             patch.object(service, "_capture", side_effect=AssertionError("read")):
            self.assertEqual(service.inspect_delivery_recovery().status, "NO_PROJECT")
        self.assertIn("請選擇", gui.snapshot_summary(service.inspect_delivery_recovery()))

    def test_missing_project_missing_artifacts_and_empty_outbox_stay_distinct(self):
        missing = self.base / "absent"
        result = service.inspect_delivery_recovery(missing, repository=self.repo)
        self.assertEqual(result.status, "PROJECT_ABSENT")
        self.assertFalse(missing.exists())
        result = self.inspect()
        self.assertEqual((result.journal_status, result.outbox_status, result.global_status), ("ABSENT", "ABSENT", "ABSENT"))
        self.assertFalse(self.root.exists())
        self.assertFalse(self.repo.path.parent.exists())
        self.root.mkdir(parents=True)
        promotion._write_json(self.root / promotion.GLOBAL_PROMOTION_OUTBOX_FILE,
                              {"schema_version": "1.0", "items": []})
        result = self.inspect()
        self.assertEqual((result.outbox_status, result.items), ("VALID", ()))
        self.assertIn("清單為空", gui.snapshot_summary(result))
        self.assertIn("無法由缺少紀錄判定", gui.snapshot_summary(result))

    def test_only_explicit_project_and_exact_helper_path(self):
        sibling = self.base / "other"
        sibling.mkdir()
        (sibling / promotion.PROJECT_TRANSACTION_FILE).write_text("bad", encoding="utf-8")
        self.assertEqual(self.inspect().journal_status, "ABSENT")
        self.assertEqual(self.inspect().actual_root, str(self.root))
        result = service.inspect_delivery_recovery("relative", repository=self.repo)
        self.assertEqual(result.status, "INVALID_PATH")

    def test_prepared_partial_outbox_is_never_presented_as_committed(self):
        with promotion.direct_visual_project_transaction(self.root) as bind:
            bind(plan())
            promotion.enqueue_promotion_intents(self.root, [intent()])
            lib.deliver_global_direct_evidence(self.repo, [intent()])
            # Windows excludes reads of the producer's actively locked byte;
            # compare every source consulted here, then the unlocked full tree
            # in the dedicated all-operations preservation test below.
            before = service._capture(self.root)
            result = self.inspect()
            self.assertEqual(result.journal_status, "PREPARED")
            self.assertEqual(result.items[0].status, "UNCONFIRMED")
            self.assertIn("部分", result.items[0].explanation)
            self.assertEqual(service._capture(self.root), before)

    def test_committed_pending_global_committed_unacked_then_acked_refresh_unconfirmed(self):
        self.commit()
        first = self.inspect()
        self.assertEqual(first.journal_status, "COMMITTED")
        self.assertEqual(json.loads(first.recovery_plan), plan())
        self.assertEqual(first.items[0].status, "PENDING")
        self.assertIn("尚未確認完成", gui.snapshot_summary(first))
        receipts = lib.deliver_global_direct_evidence(self.repo, [intent()])
        second = self.inspect()
        self.assertEqual(second.items[0].status, "AWAITING_ACK")
        self.assertEqual(second.items[0].local_status, "PENDING")
        promotion.acknowledge_promotion_delivery(self.root, [intent()], receipts)
        third = self.inspect()
        self.assertEqual(third.items[0].status, "DELIVERED")
        self.assertEqual(third.transaction_id, first.transaction_id)
        self.assertEqual(third.journal_status, "COMMITTED")
        self.assertIn("交付不代表", third.items[0].explanation)
        promotion.acknowledge_project_refresh(self.root, third.transaction_id)
        last = self.inspect()
        self.assertEqual(last.journal_status, "ABSENT")
        self.assertIn("無法由缺少紀錄判定", gui.snapshot_summary(last))

    def test_local_receipt_survives_absent_and_failing_global(self):
        self.commit()
        receipts = lib.deliver_global_direct_evidence(self.repo, [intent()])
        promotion.acknowledge_promotion_delivery(self.root, [intent()], receipts)
        absent = lib.GlobalExactGlyphRepository.resolved(self.base / "absent-global")
        result = service.inspect_delivery_recovery(self.project, repository=absent)
        self.assertEqual((result.items[0].local_status, result.items[0].global_status), ("DELIVERED", "ABSENT"))
        for error in (lib.GlobalLibraryBusyError("busy"), lib.GlobalLibraryPermissionError("permission"),
                      lib.GlobalLibraryCorruptError("corrupt"), lib.GlobalLibrarySchemaError("schema")):
            with self.subTest(error=error), patch.object(lib.GlobalExactGlyphRepository, "load_processed_intent_snapshot", side_effect=error):
                result = self.inspect()
                self.assertEqual(result.journal_status, "COMMITTED")
                self.assertEqual(result.items[0].local_status, "DELIVERED")
                self.assertEqual(result.items[0].global_status, "UNCONFIRMED")
                self.assertEqual(result.global_status, error.status)
                self.assertEqual(result.status, "PARTIAL")

    def test_actual_invalid_database_and_schema_fail_closed_even_with_empty_roster(self):
        self.repo.path.parent.mkdir()
        self.repo.path.write_bytes(b"not sqlite")
        self.assertEqual(self.inspect().global_status, lib.CORRUPT)
        self.repo.path.unlink()
        self.repo.initialize()
        with closing(sqlite3.connect(self.repo.path)) as connection:
            connection.execute("PRAGMA user_version=99")
        self.assertEqual(self.inspect().global_status, lib.SCHEMA_INCOMPATIBLE)

    def test_permission_and_busy_sqlite_errors_translate_without_writes(self):
        self.repo.initialize()
        for error, status in ((PermissionError("access denied"), lib.PERMISSION_DENIED),
                              (sqlite3.OperationalError("database is locked"), lib.BUSY)):
            with self.subTest(error=error), patch.object(lib, "_connect_existing", side_effect=error):
                self.assertEqual(self.inspect().global_status, status)

    def test_path_resolution_failure_preserves_committed_local_facts(self):
        self.commit()
        with patch.object(lib.GlobalExactGlyphRepository, "resolved", side_effect=lib.GlobalLibraryPathError("no root")):
            result = service.inspect_delivery_recovery(self.project)
        self.assertEqual(result.journal_status, "COMMITTED")
        self.assertEqual(result.items[0].status, "PENDING")
        self.assertEqual(result.status, "PARTIAL")

    def test_invalid_outbox_and_corrupt_journal_do_not_erase_independent_valid_facts(self):
        self.commit()
        path = self.root / promotion.GLOBAL_PROMOTION_OUTBOX_FILE
        original = path.read_bytes()
        for raw in (b'{"schema_version":"future","items":[]}', b'{"items":[],"items":[]}'):
            path.write_bytes(raw)
            result = self.inspect()
            self.assertEqual((result.journal_status, result.outbox_status), ("COMMITTED", "INVALID"))
            self.assertEqual(result.items, ())
        path.write_bytes(original)
        (self.root / promotion.PROJECT_TRANSACTION_FILE).write_bytes(b"broken journal")
        result = self.inspect()
        self.assertEqual((result.journal_status, result.outbox_status), ("INVALID", "VALID"))
        self.assertEqual(result.items[0].status, "UNCONFIRMED")

    def test_unknown_journal_schema_invalid_plan_and_corrupt_preimage_fail_closed(self):
        self.commit()
        path = self.root / promotion.PROJECT_TRANSACTION_FILE
        original = promotion._json(path.read_bytes())
        variants = []
        bad = copy.deepcopy(original)
        bad["schema_version"] = "future"
        variants.append(bad)
        bad = copy.deepcopy(original)
        bad["recovery_plan"]["schema_version"] = "future"
        variants.append(bad)
        bad = copy.deepcopy(original)
        bad["files"][promotion.PROJECT_FILES[0]] = {"base64": "eA==", "sha256": sha("wrong")}
        variants.append(bad)
        for document in variants:
            with self.subTest(document=document):
                promotion._write_json(path, document)
                before = project_bytes(self.project)
                result = self.inspect()
                self.assertEqual(result.journal_status, "INVALID")
                self.assertEqual(result.recovery_plan, "")
                self.assertEqual(project_bytes(self.project), before)

    def test_forged_local_receipt_and_mismatched_global_receipt_are_rejected(self):
        self.commit()
        receipts = lib.deliver_global_direct_evidence(self.repo, [intent()])
        promotion.acknowledge_promotion_delivery(self.root, [intent()], receipts)
        path = self.root / promotion.GLOBAL_PROMOTION_OUTBOX_FILE
        document = promotion.load_promotion_outbox(self.root)
        original = copy.deepcopy(document)
        document["items"][0]["receipt"]["receipt_digest"] = sha("forged")
        promotion._write_json(path, document)
        self.assertEqual(self.inspect().outbox_status, "INVALID")
        # Recomputable local receipts are records, not proof that Global agrees.
        document = original
        receipt = document["items"][0]["receipt"]
        receipt["committed_generation"] += 1
        receipt["receipt_digest"] = lib.compute_processed_intent_receipt(
            receipt["intent_id"], receipt["payload_digest"], receipt["committed_generation"], receipt["result_state"])
        promotion._write_json(path, document)
        result = self.inspect()
        self.assertEqual(result.items[0].global_status, "MISMATCH")
        self.assertEqual(result.items[0].local_status, "DELIVERED")
        self.assertEqual(result.items[0].status, "UNCONFIRMED")

    def test_same_intent_different_persisted_payload_does_not_confirm_delivery(self):
        self.commit()
        self.repo.initialize()
        with self.repo.write_transaction() as transaction:
            transaction.record_processed_intent(intent_id=intent()["intent_id"], payload_digest=sha("other"),
                                                committed_generation=0, result_state=lib.INTENT_NO_EFFECT)
        result = self.inspect()
        self.assertEqual(result.items[0].global_status, "MISMATCH")

    def test_no_effect_receipt_is_not_labeled_new_commit(self):
        self.commit()
        self.repo.initialize()
        with self.repo.write_transaction() as transaction:
            transaction.record_processed_intent(intent_id=intent()["intent_id"], payload_digest=intent()["payload_digest"],
                                                committed_generation=0, result_state=lib.INTENT_NO_EFFECT)
        result = self.inspect()
        self.assertEqual(result.items[0].status, "PENDING")
        self.assertIn("無變更", result.items[0].explanation)

    def test_project_changes_between_reads_retry_and_do_not_mix_transactions(self):
        self.commit()
        first_token = promotion.project_refresh_token(self.root)
        real = service._global_read
        calls = []
        def read(*args):
            result = real(*args)
            if not calls:
                promotion.acknowledge_project_refresh(self.root, first_token)
                self.commit()
            calls.append(1)
            return result
        with patch.object(service, "_global_read", side_effect=read):
            result = self.inspect()
        self.assertNotEqual(result.transaction_id, first_token)
        self.assertEqual(result.transaction_id, promotion.project_refresh_token(self.root))
        self.assertGreaterEqual(len(calls), 3)

    def test_journal_change_during_outbox_capture_retries_before_global_read(self):
        self.commit()
        original = service._optional_bytes
        changed = []
        def read(path):
            result = original(path)
            if path.name == promotion.GLOBAL_PROMOTION_OUTBOX_FILE and not changed:
                changed.append(True)
                promotion.acknowledge_project_refresh(self.root, promotion.project_refresh_token(self.root))
            return result
        with patch.object(service, "_optional_bytes", side_effect=read):
            result = self.inspect()
        self.assertEqual(result.journal_status, "ABSENT")
        self.assertEqual(result.transaction_id, "")

    def test_global_commit_between_snapshots_retries_exact_receipt_roster(self):
        self.commit()
        real = service._global_read
        calls = []
        def read(*args):
            result = real(*args)
            if not calls:
                lib.deliver_global_direct_evidence(self.repo, [intent()])
            calls.append(1)
            return result
        with patch.object(service, "_global_read", side_effect=read):
            result = self.inspect()
        self.assertEqual(result.items[0].status, "AWAITING_ACK")
        self.assertEqual(len(calls), 4)

    def test_repository_validation_and_receipts_share_one_sqlite_snapshot(self):
        self.repo.initialize()
        original = lib._validate_connection
        mutated = []
        def validate(connection, path):
            result = original(connection, path)
            if not mutated:
                mutated.append(True)
                lib.deliver_global_direct_evidence(self.repo, [intent()])
            return result
        with patch.object(lib, "_validate_connection", side_effect=validate):
            snapshot = self.repo.load_processed_intent_snapshot([intent()["intent_id"]])
        self.assertEqual(snapshot.receipts, ())
        self.assertEqual(self.repo.load_processed_intent_snapshot([intent()["intent_id"]]).receipts[0].intent_id,
                         intent()["intent_id"])

    def test_continuous_project_changes_and_global_roster_changes_are_bounded(self):
        self.commit()
        real = service._capture
        calls = []
        def unstable(root):
            journal, outbox = real(root)
            calls.append(1)
            return journal + b" " * len(calls), outbox
        with patch.object(service, "_capture", side_effect=unstable):
            result = self.inspect(max_attempts=2)
        self.assertEqual((result.status, result.items), ("UNSTABLE", ()))
        self.assertEqual(len(calls), 4)
        absent = lib.ProcessedIntentInspectionSnapshot(lib.ABSENT, str(self.repo.path), ())
        valid = lib.ProcessedIntentInspectionSnapshot(lib.VALID, str(self.repo.path), ())
        with patch.object(lib.GlobalExactGlyphRepository, "load_processed_intent_snapshot",
                          side_effect=[absent, valid] * 2) as reads:
            self.assertEqual(self.inspect(max_attempts=2).status, "UNSTABLE")
        self.assertEqual(reads.call_count, 4)

    def test_global_second_read_failure_preserves_local_but_not_first_global_confirmation(self):
        self.commit()
        lib.deliver_global_direct_evidence(self.repo, [intent()])
        first = self.repo.load_processed_intent_snapshot([intent()["intent_id"]])
        with patch.object(lib.GlobalExactGlyphRepository, "load_processed_intent_snapshot",
                          side_effect=[first, lib.GlobalLibraryBusyError("busy")]):
            result = self.inspect()
        self.assertEqual(result.journal_status, "COMMITTED")
        self.assertEqual(result.items[0].global_status, "UNCONFIRMED")
        self.assertEqual(result.items[0].status, "PENDING")

    def test_all_inspection_and_filtering_preserve_project_bytes_and_logical_database(self):
        self.commit()
        promotion.deliver_pending_promotion_outbox(self.root, self.repo)
        before = project_bytes(self.project)
        before_rows = {table: sql_rows(self.repo, table) for table in lib._REQUIRED_COLUMNS}
        before_db = durable_bytes(self.repo)
        with patch.object(promotion, "project_delivery_lock", side_effect=AssertionError("lock writes")), \
             patch.object(promotion, "recover_project_actual_transaction", side_effect=AssertionError("recover")), \
             patch.object(promotion, "acknowledge_project_refresh", side_effect=AssertionError("ack")), \
             patch.object(lib.GlobalExactGlyphRepository, "initialize", side_effect=AssertionError("initialize")):
            for _ in range(2):
                result = self.inspect()
                for status in gui.FILTER_LABELS:
                    gui.filter_items(result, status)
                    gui.snapshot_summary(result)
        self.assertEqual(project_bytes(self.project), before)
        self.assertEqual(durable_bytes(self.repo), before_db)
        self.assertEqual({table: sql_rows(self.repo, table) for table in lib._REQUIRED_COLUMNS}, before_rows)


class DeliveryReaderTests(unittest.TestCase):
    def test_switch_refresh_failure_deselect_and_close_discard_late_results(self):
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        seen = []
        def load(project):
            seen.append((project, threading.get_ident()))
            if project == "A":
                started.set()
                release.wait(5)
            if project == "error":
                raise RuntimeError("read failure")
            return service.DeliveryRecoverySnapshot(project_output=project)
        reader = gui.DeliveryRecoveryReader(load)
        self.addCleanup(reader.close)
        reader.select("A")
        self.assertTrue(started.wait(3))
        reader.select("B")
        reader.refresh()
        self.assertIsNone(reader.snapshot)
        release.set()
        wait_reader(reader)
        self.assertEqual(reader.snapshot.project_output, "B")
        self.assertTrue(all(tid != threading.get_ident() for _, tid in seen))
        reader.select("error")
        self.assertIsNone(reader.snapshot)
        wait_reader(reader)
        self.assertIn("read failure", reader.error)
        self.assertIsNone(reader.snapshot)
        reader.select(None)
        self.assertFalse(reader.loading)
        reader.results.put((reader.token - 1, service.DeliveryRecoverySnapshot(project_output="old"), ""))
        reader.drain()
        self.assertIsNone(reader.snapshot)
        reader.close()
        reader.results.put((reader.token, service.DeliveryRecoverySnapshot(project_output="closed"), ""))
        reader.drain()
        self.assertIsNone(reader.snapshot)

    def test_close_while_worker_blocked_never_applies_late_result(self):
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def load(project):
            started.set()
            release.wait(5)
            return service.DeliveryRecoverySnapshot(project_output=project)
        reader = gui.DeliveryRecoveryReader(load)
        reader.select("A")
        self.assertTrue(started.wait(3))
        reader.close()
        release.set()
        reader.worker.join(3)
        self.assertFalse(reader.worker.is_alive())
        self.assertIsNone(reader.snapshot)
        self.assertTrue(reader.results.empty())


class DeliveryTkTests(unittest.TestCase):
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

    def test_no_project_and_main_entry_forward_only_selected_project(self):
        window = gui.DeliveryRecoveryInspector(self.tk, loader=lambda _: self.fail("unexpected project read"))
        self.assertIn("請選擇", window.summary_text.get())
        window.refresh_button.invoke()
        self.assertIsNone(window.reader.snapshot)
        window.destroy()
        app = types.SimpleNamespace(root=self.tk, output=types.SimpleNamespace(get=lambda: ""),
                                    delivery_recovery_window=None)
        with patch.object(gui, "DeliveryRecoveryInspector") as create:
            standalone_gui.App.open_delivery_recovery(app)
            create.assert_called_once_with(self.tk, project_output=None)

    def test_real_widgets_selection_filter_refresh_failure_and_destroy(self):
        item = service.DeliveryItem("intent", "ㄅ", "PENDING", "COMMITTED", "AWAITING_ACK", "待本機 ack", "receipt details")
        seen = []
        def load(project):
            seen.append(threading.get_ident())
            if project == "bad":
                raise RuntimeError("read failure")
            return service.DeliveryRecoverySnapshot(project_output=project, status="OBSERVED",
                journal_status="COMMITTED", outbox_status="VALID", global_status="VALID", items=(item,))
        window = gui.DeliveryRecoveryInspector(self.tk, loader=load, project_output="A")
        self.pump(window)
        self.assertEqual(len(window.tree.get_children()), 1)
        window.tree.selection_set("0")
        window._show_item()
        self.assertIn("待本機 ack", window.item_text.get("1.0", "end"))
        window.filter_status.set(gui.ITEM_LABELS["DELIVERED"])
        self.assertEqual(window.tree.get_children(), ())
        window.filter_status.set("全部")
        window.refresh_button.invoke()
        self.assertEqual(window.tree.get_children(), ())
        self.pump(window)
        with patch.object(gui.filedialog, "askdirectory", return_value="bad"):
            window.select_button.invoke()
        self.assertEqual(window.tree.get_children(), ())
        self.pump(window)
        self.assertIn("讀取失敗", window.summary_text.get())
        self.assertTrue(all(tid != threading.get_ident() for tid in seen))
        self.assertEqual(window.tree.get_children(), ())
        reader = window.reader
        window.destroy()
        self.assertTrue(reader.closed.is_set())


if __name__ == "__main__":
    unittest.main()
