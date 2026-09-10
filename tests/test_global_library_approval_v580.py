from __future__ import annotations

from contextlib import closing
from dataclasses import FrozenInstanceError, replace
import gc
import json
import sqlite3
import tempfile
import threading
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

import global_exact_glyph_library as lib
import global_library_approval as approval
import global_library_inspector as inspector
from test_global_promotion_v580 import intent, sql_rows
from test_global_library_inspector_v580 import durable_bytes, exact, legacy_intent
import test_global_legacy_migration_review_v580 as historical


def wait(session):
    deadline = time.monotonic() + 10
    while session.busy and time.monotonic() < deadline:
        session.drain()
        time.sleep(.005)
    if session.busy:
        raise AssertionError("approval worker did not finish")


class ApprovalFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ap-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.repo = lib.GlobalExactGlyphRepository.resolved(self.base / "db", busy_timeout_ms=20, write_retry_limit=0)
        self.identity = exact("approval")
        self.glyph_id = lib.compute_global_glyph_id(**self.identity)
        self.addCleanup(self.clear_pending)

    def clear_pending(self):
        session = approval.pending_submission(self.repo)
        if session is not None:
            wait(session)
        with approval._submissions_lock:
            approval._submissions.pop(approval._repository_key(self.repo), None)

    def ready(self, count=3):
        lib.deliver_global_direct_evidence(self.repo, [intent(i, identity=self.identity) for i in range(1, count + 1)])
        return self.repo.load_approval_preview(self.glyph_id)

    def session(self):
        session = approval.ApprovalSession(self.repo, self.glyph_id)
        wait(session)
        return session

    def rows(self):
        return {name: sql_rows(self.repo, name) for name in lib._REQUIRED_COLUMNS}


class ApprovalTests(ApprovalFixture):
    def test_preview_full_roster_and_immutable_fixed_request_no_writes(self):
        self.ready()
        before, raw = self.rows(), durable_bytes(self.repo)
        session = self.session()
        request = session.preview.request
        self.assertEqual(len(request.quorum_evidence_ids), 3)
        self.assertEqual(request.quorum_evidence_ids,
                         tuple(sorted(row["evidence_id"] for row in before["source_evidence"])))
        self.assertEqual(request.reading, "ㄅ")
        self.assertEqual(self.repo.lookup_approval_outcome(request).status, "NOT_OBSERVED")
        with self.assertRaises(FrozenInstanceError):
            request.reading = "ㄆ"
        with self.assertRaises(TypeError):
            session.preview.record.source_evidence[0]["reading"] = "ㄆ"
        self.assertEqual(self.rows(), before)
        self.assertEqual(durable_bytes(self.repo), raw)
        sections = approval.preview_sections(session.preview)
        self.assertEqual(sections["直接來源證據"].count("本次完整提交清單：是"), 3)
        self.assertIn("永遠不計 quorum", sections["核准確認"])
        self.assertIn("不是重新確認 PDF", sections["核准確認"])
        self.assertEqual(json.loads(sections["詳細資訊"])["database_path"], str(self.repo.path))

    def test_absent_preview_and_outcome_create_nothing(self):
        preview = self.repo.load_approval_preview(self.glyph_id)
        self.assertIsNone(preview.request)
        self.assertIn("尚未建立", preview.reason)
        other = lib.GlobalExactGlyphRepository.resolved(self.base / "other")
        lib.deliver_global_direct_evidence(other, [intent(1, identity=self.identity), intent(2, identity=self.identity)])
        request = replace(other.load_approval_preview(self.glyph_id).request, database_path=str(self.repo.path))
        self.assertEqual(self.repo.lookup_approval_outcome(request).status, "NOT_OBSERVED")
        self.assertFalse(self.repo.path.parent.exists())

    def test_success_state_approval_provenance_revision_no_new_source_or_receipt(self):
        preview = self.ready()
        before = self.rows()
        session = self.session()
        self.assertTrue(session.confirm())
        self.assertFalse(session.confirm())
        wait(session)
        self.assertEqual(session.state, "APPROVAL_COMMITTED")
        after = self.rows()
        self.assertEqual(after["glyph_truth"][0]["state"], lib.VERIFIED_GLOBAL)
        self.assertEqual(after["glyph_truth"][0]["active_reading"], "ㄅ")
        self.assertEqual(after["glyph_truth"][0]["revision"], preview.request.glyph_revision + 1)
        self.assertEqual(after["source_evidence"], before["source_evidence"])
        self.assertEqual(after["processed_intent"], before["processed_intent"])
        self.assertEqual(len(after["promotion_approval"]), 1)
        row = after["promotion_approval"][0]
        self.assertEqual(row["approval_id"], preview.request.approval_id)
        self.assertEqual(json.loads(row["quorum_evidence_ids_json"]), list(preview.request.quorum_evidence_ids))
        added = {r["event_type"] for r in after["provenance_event"] if r not in before["provenance_event"]}
        self.assertEqual(added, {"PROMOTION_APPROVED", "TRUTH_VERIFIED"})
        self.assertFalse(session.confirm())
        with self.assertRaises(lib.GlobalLibraryValidationError):
            lib.approve_global_exact_glyph(self.repo, **preview.request.service_arguments())
        self.assertEqual(self.rows(), after)

    def test_quorum_axes_legacy_only_quarantine_and_verified_blocked(self):
        lib.deliver_global_migration(self.repo, [legacy_intent(self.identity, "ㄅ")])
        self.assertIn("缺少", self.repo.load_approval_preview(self.glyph_id).reason)
        lib.deliver_global_direct_evidence(self.repo, [intent(1, identity=self.identity),
            intent(2, identity=self.identity, source_review_id="review-1")])
        self.assertIsNone(self.repo.load_approval_preview(self.glyph_id).request)
        lib.deliver_global_direct_evidence(self.repo, [intent(3, identity=self.identity)])
        session = self.session()
        session.confirm()
        wait(session)
        self.assertIn("已有核准", self.repo.load_approval_preview(self.glyph_id).reason)
        lib.deliver_global_direct_evidence(self.repo, [intent(4, "ㄆ", identity=self.identity)])
        self.assertIn("已衝突隔離", self.repo.load_approval_preview(self.glyph_id).reason)

    def test_stale_revision_roster_and_reading_never_refresh_request(self):
        for reading in ("ㄅ", "ㄆ"):
            with self.subTest(reading=reading):
                self.repo = lib.GlobalExactGlyphRepository.resolved(self.base / reading)
                self.ready()
                session = self.session()
                fixed = session.preview.request
                lib.deliver_global_direct_evidence(self.repo, [intent(4, reading, identity=self.identity)])
                before = self.rows()
                session.confirm()
                wait(session)
                self.assertEqual(session.state, "REJECTED")
                self.assertIs(session.preview.request, fixed)
                self.assertEqual(self.rows(), before)
                self.assertEqual(sql_rows(self.repo, "promotion_approval"), [])

    def test_preview_snapshot_consistency_when_writer_adds_direct_source(self):
        self.ready(2)
        validate = lib._validate_connection
        changed = False
        def concurrent(connection, path):
            nonlocal changed
            value = validate(connection, path)
            if not changed:
                changed = True
                lib.deliver_global_direct_evidence(self.repo, [intent(3, identity=self.identity)])
            return value
        with patch.object(lib, "_validate_connection", side_effect=concurrent):
            preview = self.repo.load_approval_preview(self.glyph_id)
        self.assertEqual(len(preview.request.quorum_evidence_ids), 2)
        self.assertEqual(len(preview.record.source_evidence), 2)
        with self.assertRaises(lib.GlobalLibraryValidationError):
            lib.approve_global_exact_glyph(self.repo, **preview.request.service_arguments())

    def test_fixed_outcome_cannot_match_other_request_or_repository_and_keeps_revoked_history(self):
        preview = self.ready()
        lib.approve_global_exact_glyph(self.repo, **preview.request.service_arguments())
        self.assertEqual(self.repo.lookup_approval_outcome(preview.request).status, "APPROVAL_COMMITTED")
        self.assertEqual(self.repo.lookup_approval_outcome(replace(preview.request, glyph_revision=100)).status, "NOT_OBSERVED")
        other = lib.GlobalExactGlyphRepository.resolved(self.base / "other")
        with self.assertRaisesRegex(lib.GlobalLibraryValidationError, "repository mismatch"):
            other.lookup_approval_outcome(preview.request)
        lib.deliver_global_direct_evidence(self.repo, [intent(4, "ㄆ", identity=self.identity)])
        outcome = self.repo.lookup_approval_outcome(preview.request)
        self.assertEqual(outcome.status, "APPROVAL_COMMITTED")
        self.assertEqual(outcome.current_state, lib.QUARANTINED_CONFLICT)
        self.assertFalse(outcome.current_reuse_allowed)

    def test_query_rejects_noncanonical_revision_without_reading_or_writing(self):
        preview = self.ready()
        for invalid in (True, False, -1, 3.0, "3"):
            with self.subTest(invalid=invalid), \
                 patch.object(lib, "_connect_existing", side_effect=AssertionError("must reject before read")), \
                 self.assertRaises(lib.GlobalLibraryValidationError):
                self.repo.lookup_approval_outcome(replace(preview.request, glyph_revision=invalid))

    def test_approval_and_conflict_change_only_affected_global_dependency(self):
        self.ready()
        exact_identity = lib.canonical_global_exact_identity(**self.identity)
        unrelated = lib.canonical_global_exact_identity(**exact("unrelated"))
        before = self.repo.load_snapshot()
        affected_before = lib.global_exact_glyph_evidence_hashes(before, (exact_identity,))
        unrelated_before = lib.global_exact_glyph_evidence_hashes(before, (unrelated,))
        session = self.session()
        session.confirm()
        wait(session)
        verified = self.repo.load_snapshot()
        self.assertEqual(verified.trusted_identities[0].active_reading, "ㄅ")
        affected_verified = lib.global_exact_glyph_evidence_hashes(verified, (exact_identity,))
        self.assertNotEqual(affected_before, affected_verified)
        self.assertEqual(unrelated_before, lib.global_exact_glyph_evidence_hashes(verified, (unrelated,)))
        lib.deliver_global_direct_evidence(self.repo, [intent(4, "ㄆ", identity=self.identity)])
        quarantined = self.repo.load_snapshot()
        self.assertEqual(quarantined.trusted_identities, ())
        self.assertNotEqual(affected_verified, lib.global_exact_glyph_evidence_hashes(quarantined, (exact_identity,)))
        self.assertEqual(unrelated_before, lib.global_exact_glyph_evidence_hashes(quarantined, (unrelated,)))

    def test_lost_success_response_resolves_durable_approval_without_retry(self):
        self.ready()
        session = self.session()
        submit = lib.approve_global_exact_glyph
        def lost(*args, **kwargs):
            submit(*args, **kwargs)
            raise OSError("lost response")
        with patch.object(lib, "approve_global_exact_glyph", side_effect=lost) as mocked:
            session.confirm()
            wait(session)
            self.assertFalse(session.confirm())
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(session.state, "APPROVAL_COMMITTED")
        self.assertEqual(session.outcome.approval_id, session.preview.request.approval_id)

    def test_unknown_not_found_query_failure_and_explicit_read_only_recovery(self):
        self.ready()
        session = self.session()
        submit = lib.approve_global_exact_glyph
        def lost(*args, **kwargs):
            submit(*args, **kwargs)
            raise OSError("lost response")
        with patch.object(lib, "approve_global_exact_glyph", side_effect=lost) as mocked, \
             patch.object(lib.GlobalExactGlyphRepository, "lookup_approval_outcome", side_effect=PermissionError("blocked")):
            session.confirm()
            wait(session)
        self.assertEqual(session.state, "UNKNOWN")
        self.assertIs(approval.pending_submission(self.repo), session)
        self.assertFalse(session.confirm())
        self.assertTrue(session.query_outcome())
        wait(session)
        self.assertEqual(session.state, "APPROVAL_COMMITTED")
        self.assertIsNone(approval.pending_submission(self.repo))
        self.assertEqual(mocked.call_count, 1)

    def test_transaction_failure_busy_permission_and_unknown_never_imply_commit(self):
        self.ready()
        before = self.rows()
        for error in (OSError("unavailable"), lib.GlobalLibraryBusyError("busy"), PermissionError("permission")):
            session = self.session()
            with patch.object(lib, "_write_provenance", side_effect=error):
                session.confirm()
                wait(session)
            self.assertEqual(session.state, "UNKNOWN")
            self.assertEqual(self.rows(), before)
            session.query_outcome()
            wait(session)
            self.assertEqual(session.state, "UNKNOWN")
            self.assertIn("尚不確定", approval.outcome_text(session))
            self.clear_pending()
        with closing(sqlite3.connect(self.repo.path, isolation_level=None)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            session = self.session()
            session.confirm()
            wait(session)
            self.assertEqual(session.state, "UNKNOWN")
            connection.execute("ROLLBACK")
        self.assertEqual(self.rows(), before)

    def test_preview_corrupt_incompatible_permission_errors_are_blocked_read_only(self):
        self.ready()
        before = durable_bytes(self.repo)
        for error in (PermissionError("denied"), sqlite3.OperationalError("database is locked")):
            with patch.object(lib, "_connect_existing", side_effect=error):
                session = self.session()
            self.assertEqual(session.state, "BLOCKED")
            self.assertFalse(session.confirm())
            self.assertEqual(durable_bytes(self.repo), before)
        with closing(sqlite3.connect(self.repo.path)) as connection:
            connection.execute("PRAGMA user_version=99")
        self.assertEqual(self.session().state, "BLOCKED")
        corrupt = lib.GlobalExactGlyphRepository.resolved(self.base / "corrupt")
        corrupt.path.parent.mkdir()
        corrupt.path.write_bytes(b"corrupt fixture")
        session = approval.ApprovalSession(corrupt, self.glyph_id)
        wait(session)
        self.assertEqual(session.state, "BLOCKED")
        self.assertEqual(corrupt.path.read_bytes(), b"corrupt fixture")

    def test_parallel_sessions_claim_one_repository_and_closed_session_stays_recoverable(self):
        self.ready()
        first, second = self.session(), self.session()
        entered, release = threading.Event(), threading.Event()
        submit = lib.approve_global_exact_glyph
        def slow(*args, **kwargs):
            entered.set()
            release.wait(5)
            return submit(*args, **kwargs)
        with patch.object(lib, "approve_global_exact_glyph", side_effect=slow) as mocked:
            first.confirm()
            self.assertTrue(entered.wait(5))
            self.assertFalse(second.confirm())
            self.assertIs(approval.pending_submission(self.repo), first)
            release.set()
            wait(first)
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(first.state, "APPROVAL_COMMITTED")


class LegacyApprovalTests(ApprovalFixture):
    # Reuse the pinned historical implementation as a fixture producer, without
    # inheriting or rerunning its test cases.
    @classmethod
    def setUpClass(cls):
        historical.FrozenV0CompatibilityTests.setUpClass.__func__(cls)

    def test_v0_read_suppression_blocks_preview_but_post_preview_conflict_commits(self):
        for lost in (False, True):
            with self.subTest(lost=lost):
                self.repo = lib.GlobalExactGlyphRepository.resolved(self.base / str(lost))
                self.ready()
                session = self.session()
                old_repo = self.old.GlobalExactGlyphRepository(self.repo.path)
                # A real frozen-v0 transaction introduces retained counterevidence
                # after confirmation preview without changing the glyph revision.
                with old_repo.write_transaction() as tx:
                    args = dict(glyph_id=self.glyph_id, reading="ㄆ", legacy_project_id="old",
                        legacy_project_sha256="b" * 64, legacy_evidence_sha256="c" * 64,
                        old_verification_level="USER_VERIFIED_SINGLE")
                    row = dict(import_id=self.old.compute_migration_import_id(**args), **args,
                               status="CANDIDATE", imported_at=historical.NOW)
                    columns = self.old._REQUIRED_COLUMNS["migration_candidate"]
                    tx._connection.execute("INSERT INTO migration_candidate (" + ",".join(columns) + ") VALUES (" +
                        ",".join("?" for _ in columns) + ")", tuple(row[key] for key in columns))
                    tx.bump_generation()
                before = durable_bytes(self.repo)
                suppressed = self.repo.load_approval_preview(self.glyph_id)
                self.assertIsNone(suppressed.request)
                self.assertIn("尚未寫入隔離", suppressed.reason)
                self.assertEqual(durable_bytes(self.repo), before)
                submit = lib.approve_global_exact_glyph
                def response(*args, **kwargs):
                    result = submit(*args, **kwargs)
                    if lost:
                        raise OSError("lost conflict response")
                    return result
                with patch.object(lib, "approve_global_exact_glyph", side_effect=response):
                    session.confirm()
                    wait(session)
                self.assertEqual(session.state, "CONFLICT_COMMITTED")
                self.assertIn("核准未授予", approval.outcome_text(session))
                self.assertEqual(sql_rows(self.repo, "glyph_truth")[0]["state"], lib.QUARANTINED_CONFLICT)
                self.assertIsNone(sql_rows(self.repo, "glyph_truth")[0]["active_reading"])
                self.assertEqual(sql_rows(self.repo, "promotion_approval"), [])
                self.assertEqual(len(sql_rows(self.repo, "glyph_conflict")), 1)
                self.assertEqual(self.repo.lookup_approval_outcome(session.preview.request).status, "CONFLICT_COMMITTED")
                self.assertTrue(session.outcome.receipt_digest)


class ApprovalTkTests(ApprovalFixture):
    def setUp(self):
        super().setUp()
        self.ready()
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            raise unittest.SkipTest(f"Tk display unavailable: {exc}")
        self.root.withdraw()
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.addCleanup(self.destroy_root)

    def destroy_root(self):
        root, self.root = self.root, None
        readers = [window.reader for window in root.winfo_children()
                   if isinstance(window, inspector.GlobalLibraryInspector)]
        root.destroy()
        for reader in readers:
            reader.worker.join(5)
            self.assertFalse(reader.worker.is_alive())
        del root
        # Tests create multiple Tcl interpreters. Retire widget cycles on their
        # owner thread before another test's data worker can trigger cyclic GC.
        gc.collect()

    def settle(self, condition):
        deadline = time.monotonic() + 10
        while not condition() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        self.root.update()
        self.assertTrue(condition())
        self.assertEqual(self.errors, [])

    def window(self, **kwargs):
        window = inspector.GlobalLibraryInspector(self.root, **(kwargs or {"repository": self.repo}))
        self.settle(lambda: not window.reader.loading)
        window.tree.selection_set(self.glyph_id)
        window._select()
        return window

    def dialog(self, window):
        window.approval_button.invoke()
        dialog = window.approval_dialog
        self.settle(lambda: not dialog.session.busy)
        return dialog

    def test_actual_cancel_selection_and_refresh_never_write(self):
        before, raw = self.rows(), durable_bytes(self.repo)
        window = self.window()
        dialog = self.dialog(window)
        self.assertEqual(dialog.session.state, "READY")
        dialog.cancel_button.invoke()
        window.refresh_button.invoke()
        self.settle(lambda: not window.reader.loading)
        self.assertEqual(self.rows(), before)
        self.assertEqual(durable_bytes(self.repo), raw)

    def test_initial_failure_in_each_required_tab_blocks_handler_until_complete_new_confirmation(self):
        for tab in ("核准確認", "直接來源證據", "詳細資訊"):
            with self.subTest(tab=tab):
                self.repo = lib.GlobalExactGlyphRepository.resolved(self.base / tab)
                self.ready()
                window = self.window()
                before, raw = self.rows(), durable_bytes(self.repo)
                window.open_approval()
                dialog = window.approval_dialog
                with patch.object(lib, "approve_global_exact_glyph", wraps=lib.approve_global_exact_glyph) as submit:
                    with patch.object(dialog.texts[tab], "insert", side_effect=tk.TclError("initial preview failure")):
                        self.settle(lambda: not dialog.session.busy)
                        self.assertTrue(dialog.confirm_button.instate(["disabled"]))
                        dialog.confirm_button.invoke()
                        dialog.confirm()
                        self.assertFalse(dialog.session.attempted)
                        self.assertIn("尚未提交", dialog.status_text.get())
                        self.assertNotIn("已提交結果保留", dialog.status_text.get())
                    # Removing the fault alone cannot authorize the original button/handler.
                    dialog.confirm_button.invoke()
                    dialog.confirm()
                    self.assertFalse(dialog.session.attempted)
                    self.assertEqual(submit.call_count, 0)
                    self.assertEqual(self.rows(), before)
                    self.assertEqual(durable_bytes(self.repo), raw)
                    fixed = dialog.session.preview.request
                    dialog.destroy()
                    recovered = self.dialog(window)
                    self.assertEqual(recovered.session.preview.request, fixed)
                    self.assertTrue(all(text.get("1.0", "end").strip() for text in recovered.texts.values()))
                    self.assertTrue(all(str(text.cget("state")) == "disabled" for text in recovered.texts.values()))
                    self.assertEqual(submit.call_count, 0)
                    self.assertEqual(self.rows(), before)
                    recovered.confirm_button.invoke()
                    self.settle(lambda: recovered.notified and not window.reader.loading)
                    self.assertEqual(submit.call_count, 1)
                    self.assertEqual(recovered.session.state, "APPROVAL_COMMITTED")
                window.destroy()

    def test_dialog_render_failure_during_submission_preserves_real_committed_outcome(self):
        window = self.window()
        dialog = self.dialog(window)
        with patch.object(dialog.texts["直接來源證據"], "insert", side_effect=tk.TclError("post-confirm render failure")):
            dialog.confirm_button.invoke()
            self.settle(lambda: dialog.notified and not window.reader.loading)
        self.assertEqual(dialog.session.state, "APPROVAL_COMMITTED")
        self.assertTrue(dialog.session.attempted)
        self.assertTrue(dialog.confirm_button.instate(["disabled"]))
        self.assertIn("核准已成功提交", dialog.status_text.get())
        self.assertIn("顯示失敗", dialog.status_text.get())
        self.assertNotIn("尚未提交", dialog.status_text.get())
        self.assertEqual(self.repo.lookup_approval_outcome(dialog.session.preview.request).status, "APPROVAL_COMMITTED")

    def test_custom_loader_alone_cannot_fall_through_to_live_database(self):
        with patch.object(lib.GlobalExactGlyphRepository, "resolved", side_effect=AssertionError("live lookup")):
            window = self.window(loader=self.repo.load_inspection_snapshot)
            self.assertTrue(window.approval_button.instate(["disabled"]))
            window.open_approval()
            self.assertIsNone(window.approval_dialog)
        other = lib.GlobalExactGlyphRepository.resolved(self.base / "other")
        window = self.window(repository=other, loader=self.repo.load_inspection_snapshot)
        self.assertTrue(window.approval_button.instate(["disabled"]))
        window.open_approval()
        self.assertIsNone(window.approval_dialog)
        self.assertFalse(other.path.parent.exists())

    def test_default_target_resolved_once_even_if_environment_changes(self):
        with patch.object(lib.GlobalExactGlyphRepository, "resolved", return_value=self.repo) as resolver:
            window = inspector.GlobalLibraryInspector(self.root)
            self.settle(lambda: not window.reader.loading)
            window.tree.selection_set(self.glyph_id)
            window._select()
            dialog = self.dialog(window)
        with patch.object(lib.GlobalExactGlyphRepository, "resolved", side_effect=AssertionError("target changed")):
            dialog.confirm_button.invoke()
            dialog.confirm_button.invoke()
            self.settle(lambda: dialog.notified and not window.reader.loading)
        self.assertEqual(resolver.call_count, 1)
        self.assertEqual(dialog.session.state, "APPROVAL_COMMITTED")
        self.assertIn("已重新讀取", dialog.status_text.get())

    def test_success_then_read_or_renderer_failure_preserves_commit(self):
        for renderer in (False, True):
            self.repo = lib.GlobalExactGlyphRepository.resolved(self.base / str(renderer))
            self.ready()
            fail = False
            repository = self.repo
            def loader():
                if fail and not renderer:
                    raise lib.GlobalLibraryPermissionError("fixture refresh denied")
                return repository.load_inspection_snapshot()
            window = self.window(repository=self.repo, loader=loader)
            dialog = self.dialog(window)
            fail = True
            render = window._render_records
            def failed_render():
                if renderer and not window.reader.loading:
                    raise RuntimeError("fixture renderer failed")
                render()
            with patch.object(window, "_render_records", side_effect=failed_render):
                dialog.confirm_button.invoke()
                self.settle(lambda: dialog.notified and not window.reader.loading)
            self.assertEqual(dialog.session.state, "APPROVAL_COMMITTED")
            self.assertIn("已提交結果保留", dialog.status_text.get())
            self.assertEqual(self.repo.lookup_approval_outcome(dialog.session.preview.request).status, "APPROVAL_COMMITTED")
            window.destroy()

    def test_close_reopen_during_submit_uses_same_request_and_only_one_write(self):
        window = self.window()
        dialog = self.dialog(window)
        session = dialog.session
        entered, release = threading.Event(), threading.Event()
        submit = lib.approve_global_exact_glyph
        def slow(*args, **kwargs):
            entered.set()
            release.wait(5)
            return submit(*args, **kwargs)
        with patch.object(lib, "approve_global_exact_glyph", side_effect=slow) as mocked:
            dialog.confirm_button.invoke()
            self.assertTrue(entered.wait(5))
            window.destroy()
            reopened = self.window()
            reopened.approval_button.invoke()
            recovered = reopened.approval_dialog
            self.assertIs(recovered.session, session)
            recovered.confirm_button.invoke()
            release.set()
            self.settle(lambda: recovered.notified and not reopened.reader.loading)
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(recovered.session.state, "APPROVAL_COMMITTED")

    def test_late_preview_after_selection_change_never_updates_or_submits_other_row(self):
        other = exact("other")
        lib.deliver_global_direct_evidence(self.repo, [intent(8, "ㄆ", identity=other)])
        other_id = lib.compute_global_glyph_id(**other)
        window = self.window()
        entered, release = threading.Event(), threading.Event()
        preview = self.repo.load_approval_preview(self.glyph_id)
        def slow(_repo, _glyph):
            entered.set()
            release.wait(5)
            return preview
        with patch.object(lib.GlobalExactGlyphRepository, "load_approval_preview", slow):
            window.approval_button.invoke()
            dialog = window.approval_dialog
            self.assertTrue(entered.wait(5))
            window.tree.selection_set(other_id)
            window._select()
            self.assertFalse(dialog.winfo_exists())
            release.set()
            wait(dialog.session)
        self.root.update()
        self.assertEqual(window.tree.selection(), (other_id,))
        self.assertEqual(sql_rows(self.repo, "promotion_approval"), [])
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
