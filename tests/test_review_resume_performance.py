"""Operation-local resume regressions. No native Tk or live Global DB."""
from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import actual_review as ar
import review_gui as gui
import global_glyph_promotion as promotion
import review_save_service as rs
import standalone_proofread as sp
from tests.review_save_test_support import wait_for_save
from tests.test_staged_review_navigation_v580 import create_staged_navigation_fixture, headless_app, controlled_refresh
from tests.test_manual_actual_gui_batch_v570 import FakeProgressWidget, ImmediateThread


def finish_staging(app):
    worker = app._staging_worker
    worker.join(15)
    if worker.is_alive():
        raise AssertionError("staging worker did not finish")
    app.root.update()


class ReviewResumeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template_dir = tempfile.TemporaryDirectory()
        cls.template = Path(cls.template_dir.name)
        create_staged_navigation_fixture(cls.template)

    @classmethod
    def tearDownClass(cls):
        cls.template_dir.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.output = Path(self.tmp.name) / "project"
        shutil.copytree(self.template, self.output)
        raw = (self.output / "校對工作階段.json").read_text(encoding="utf-8")
        self.manifest = sp.seal_manifest(json.loads(raw.replace(
            json.dumps(str(self.template))[1:-1], json.dumps(str(self.output))[1:-1])))
        sp.json_save(self.output / "校對工作階段.json", self.manifest)
        self.db = sp.load_or_initialize_db(self.output)
        self.service = rs.ReviewSaveService(self.output)
        self.localapp = patch.dict(os.environ, {"LOCALAPPDATA": str(Path(self.tmp.name) / "isolated-local")})
        self.localapp.start()
        self.addCleanup(self.localapp.stop)

    def stage(self):
        row = self.manifest["records"][2]
        sp.stage_manual_actual_correction(self.output, row["review_id"], "ㄎㄢ")
        return row

    def test_resume_reuses_one_preparation_and_original_staging_bytes(self):
        row = self.stage()
        expected = sp.manual_actual_staging_summary(
            self.output, ledger=sp.materialize_ledger(self.manifest, self.db))
        with patch.object(sp, "prepare_review_ledger", wraps=sp.prepare_review_ledger) as prepare, \
                patch.object(ar, "_validate_manual_actual_staging_document", wraps=ar._validate_manual_actual_staging_document) as validate:
            snapshot = self.service.load_resume_snapshot(expected_manifest=self.manifest, expected_db=self.db)
            summary = self.service.validate_resume_staging(snapshot)
        self.assertEqual(prepare.call_count, 1)
        self.assertEqual(validate.call_count, 1)
        self.assertEqual(summary, expected)
        self.assertIn(row["occurrence_id"], summary["staged_checked_occurrence_ids"])
        self.assertEqual(snapshot.ledger, sp.materialize_ledger(self.manifest, self.db))
        self.assertFalse((Path(self.tmp.name) / "isolated-local").exists())

    def test_external_changes_during_validation_never_publish_checked_ids(self):
        self.stage()
        paths = [self.output / "校對工作階段.json", self.output / "人工判定資料庫.json",
                 ar.manual_actual_staging_path(sp.project_actual_evidence_root(self.output)),
                 Path(self.manifest["records"][0]["pdf"])]
        validate = sp._manual_actual_summary_from_verified_snapshot
        for path in paths:
            with self.subTest(path=path.name):
                original = path.read_bytes()
                snapshot = self.service.load_resume_snapshot()
                def changed(*args, **kwargs):
                    result = validate(*args, **kwargs)
                    # Same-size mutation, preserving mtime: content must govern.
                    raw = bytearray(original)
                    raw[-1] = 32 if raw[-1] != 32 else 10
                    stat = path.stat()
                    path.write_bytes(raw)
                    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
                    return result
                try:
                    with patch.object(sp, "_manual_actual_summary_from_verified_snapshot", side_effect=changed):
                        summary = self.service.validate_resume_staging(snapshot)
                    self.assertTrue(summary.get("staging_error"))
                    self.assertEqual(summary["staged_checked_occurrence_ids"], [])
                finally:
                    path.write_bytes(original)

    def test_queue_precedes_staging_and_actual_actions_are_gated(self):
        staged = self.stage()
        app = headless_app(self.output)
        entered, release = threading.Event(), threading.Event()
        validate = rs.ReviewSaveService.validate_resume_staging
        def delayed(service, snapshot, **kwargs):
            entered.set()
            if not release.wait(10):
                raise AssertionError("test did not release validator")
            return validate(service, snapshot, **kwargs)
        with patch.object(rs.ReviewSaveService, "validate_resume_staging", delayed), \
                patch.object(gui, "ActualReadingDialog") as dialog, \
                patch.object(gui, "apply_staged_manual_actual_corrections") as apply:
            app.reload_records(defer_staging=True)
            self.assertTrue(entered.wait(5))
            try:
                self.assertTrue(app.records)
                self.assertIn(staged["occurrence_id"], {r["occurrence_id"] for r in app.records})
                self.assertFalse(app.staged_checked_occurrence_ids)
                self.assertFalse(app.waiting_actual_occurrence_ids)
                self.assertTrue(app.staging_summary["staging_validation_pending"])
                self.assertEqual(app.apply_actual_button.options["state"], "disabled")
                app.correct_actual()
                app.apply_staged_actuals()
                dialog.assert_not_called()
                apply.assert_not_called()
                app.next()
                selected = app.current()["review_id"]
            finally:
                release.set()
            app._review_action_in_progress = True
            finish_staging(app)
            self.assertTrue(app.staging_summary["staging_validation_pending"])
            self.assertEqual(app.current()["review_id"], selected)
            app._review_action_in_progress = False
            app.root.update()
        self.assertEqual(app.current()["review_id"], selected)
        self.assertNotIn("staging_validation_pending", app.staging_summary)
        self.assertIn(staged["occurrence_id"], app.staged_checked_occurrence_ids)

    def test_expected_save_finishes_before_staging_and_old_result_is_discarded(self):
        self.stage()
        app = headless_app(self.output)
        entered, release = threading.Event(), threading.Event()
        validate = rs.ReviewSaveService.validate_resume_staging
        def delayed(service, snapshot, **kwargs):
            entered.set()
            if not release.wait(10):
                raise AssertionError("test did not release validator")
            return validate(service, snapshot, **kwargs)
        with patch.object(rs.ReviewSaveService, "validate_resume_staging", delayed), \
                patch.object(gui.messagebox, "showerror") as errors:
            app.reload_records(defer_staging=True)
            self.assertTrue(entered.wait(5))
            old_request, old_worker = app._staging_request, app._staging_worker
            row = app.current()
            event = sp.build_manual_expected_event(row, operation="ENTER_EXPECTED", expected_set="ㄎㄢ")
            try:
                self.assertTrue(app.save_event(row, event))
                wait_for_save(app)
                self.assertTrue(app._last_event_saved)
                errors.assert_not_called()
                self.assertFalse(release.is_set())
                self.assertTrue(old_request["cancelled"].is_set())
                self.assertNotEqual(app._staging_request, old_request)
                self.assertEqual(sp.load_or_initialize_db(self.output)["events"][row["review_id"]], app.db["events"][row["review_id"]])
                expected = copy.deepcopy(app.db)
                self.assertTrue(app.staging_summary["staging_validation_pending"])
            finally:
                release.set()
                old_worker.join(15)
            finish_staging(app)
        self.assertEqual(app.db, expected)
        self.assertFalse(app.staging_summary.get("staging_error"))
        self.assertEqual(app._save_service._ledger, sp.materialize_ledger(app.manifest, app.db))

    def test_pending_save_and_worker_start_failures_leave_no_orphan_pending_state(self):
        app = headless_app(self.output)
        row = app.current()
        event = sp.build_manual_expected_event(row, operation="ENTER_EXPECTED", expected_set="ㄎㄢ")
        for startup in (False, True):
            with self.subTest(startup=startup):
                app._publish_staging_summary({"staging_validation_pending": True})
                target = (patch.object(gui.threading.Thread, "start", side_effect=RuntimeError("thread unavailable"))
                          if startup else patch.object(sp, "json_save", side_effect=OSError("disk full")))
                with target, patch.object(gui.messagebox, "showerror"):
                    app.save_event(row, event)
                    wait_for_save(app)
                    if not startup:
                        finish_staging(app)
                self.assertFalse(app._save_in_progress)
                self.assertFalse(app.staging_summary.get("staging_validation_pending"))
                self.assertFalse(app.staged_checked_occurrence_ids)
                self.assertEqual(sp.load_or_initialize_db(self.output)["events"], {})

    def test_reload_and_disposal_cancel_stale_workers(self):
        app = headless_app(self.output)
        entered, release = threading.Event(), threading.Event()
        validate = rs.ReviewSaveService.validate_resume_staging
        def delayed(service, snapshot, **kwargs):
            entered.set()
            if not release.wait(10):
                raise AssertionError("test did not release validator")
            return validate(service, snapshot, **kwargs)
        with patch.object(rs.ReviewSaveService, "validate_resume_staging", delayed):
            app.reload_records(defer_staging=True)
            self.assertTrue(entered.wait(5))
            first, worker = app._staging_request, app._staging_worker
            try:
                app.defer_current()
                self.assertTrue(first["cancelled"].is_set())
                deferred_request, deferred_worker = app._staging_request, app._staging_worker
                self.assertIsNotNone(deferred_request)
                self.assertTrue(app.staging_summary["staging_validation_pending"])
                app.revisit_deferred()
                self.assertTrue(deferred_request["cancelled"].is_set())
                revisit_worker = app._staging_worker
                self.assertTrue(app.staging_summary["staging_validation_pending"])
                app._dispose_async(SimpleNamespace(widget=app.root))
                self.assertFalse(app.root.callbacks)
                before = copy.deepcopy(app.records)
            finally:
                release.set()
                worker.join(15)
                deferred_worker.join(15)
                revisit_worker.join(15)
            app.root.update()
        self.assertEqual(app.records, before)
        self.assertTrue(app._async_disposed)

    def test_deferred_save_rejects_other_actions_invalid_decision_and_source_drift(self):
        row = self.manifest["records"][0]
        before = (self.output / "人工判定資料庫.json").read_bytes()
        good = sp.build_manual_expected_event(row, operation="ENTER_EXPECTED", expected_set="ㄎㄢ")
        invalid = copy.deepcopy(good)
        invalid["manual_expected_decision"]["target"]["occurrence_id"] = "forged"
        events = [None, {"action": "保留待人工"}, invalid,
                  sp.build_manual_expected_event(row, operation="CONFIRM_CURRENT_AS_EXPECTED")]
        for event in events:
            with self.subTest(event=event), self.assertRaises(ValueError):
                self.service.save_event(row["review_id"], event, defer_staging_validation=True)
            self.assertEqual((self.output / "人工判定資料庫.json").read_bytes(), before)
        pdf = Path(row["pdf"])
        pdf.write_bytes(pdf.read_bytes() + b"drift")
        with self.assertRaisesRegex(ValueError, "SOURCE_INVALID"):
            self.service.save_event(row["review_id"], good, defer_staging_validation=True)
        self.assertEqual((self.output / "人工判定資料庫.json").read_bytes(), before)

    def test_failed_staging_and_expected_save_remain_independent(self):
        row = self.manifest["records"][0]
        path = ar.manual_actual_staging_path(sp.project_actual_evidence_root(self.output))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"corrupt staging")
        event = sp.build_manual_expected_event(row, operation="ENTER_EXPECTED", expected_set="ㄎㄢ")
        result = self.service.save_event(row["review_id"], event, defer_staging_validation=True)
        self.assertTrue(result.staging_summary["staging_validation_pending"])
        summary = self.service.validate_resume_staging(result.resume_snapshot)
        self.assertTrue(summary["staging_error"])
        self.assertFalse(summary["staged_checked_occurrence_ids"])
        self.assertEqual(result.resolved_entry["expected_set"], ["ㄎㄢ"])
        self.assertEqual(result.resolved_entry["actual"], row["actual"])
        self.assertEqual(path.read_bytes(), b"corrupt staging")

    def test_projection_validates_one_immutable_context_and_every_binding(self):
        baseline, _ = sp.prepare_review_ledger(self.manifest, self.db)
        for row in baseline[:3]:
            self.db["events"][row["review_id"]] = sp.bind_expected_resolution(row, {
                "action": "解決expected證據", "expected_set": ["ㄎㄢ"],
                "expected_evidence": "independent dictionary", "context_evidence": "independent context",
                "source": "fixture expected evidence", "resolution_reason": "", "note": "",
            }, self.manifest)
        ledger = sp.materialize_ledger(self.manifest, self.db)
        with patch.object(sp, "_validated_expected_manifest", wraps=sp._validated_expected_manifest) as context, \
                patch.object(sp, "_validate_expected_resolution_binding_in_context", wraps=sp._validate_expected_resolution_binding_in_context) as binding:
            projected = sp._manual_actual_snapshot_ledger(self.manifest, self.db, ledger, base_ledger=baseline)
        self.assertEqual(context.call_count, 1)
        self.assertEqual(binding.call_count, 3)
        for original, reviewed, row in zip(baseline, ledger, projected):
            self.assertEqual(row["state"], original["state"])
            self.assertEqual(row["expected_set"], reviewed["expected_set"])
            self.assertEqual(row["actual"], original["actual"])
        self.db["events"][baseline[1]["review_id"]]["expected_resolution_binding"]["integrity_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "integrity"):
            sp._manual_actual_snapshot_ledger(self.manifest, self.db, ledger, base_ledger=baseline)

    def test_group_index_preserves_exact_groups_and_checks_cancellation(self):
        ledger = [copy.deepcopy(row) for _ in range(100) for row in self.manifest["records"]]
        for number, row in enumerate(ledger):
            row["occurrence_id"] = f"fixture-{number}"
            row["review_id"] = f"review-{number}"
            row["source_record"]["TTF字形SHA256"] = f"{number % 20:064x}"
        # Build the independent legacy reference before spying on identity calls.
        targets = ledger[:20]
        expected = [ar.build_actual_group_for_entry(ledger, row) for row in targets]
        staged = [{"group_id": group["group_id"], "member_occurrence_ids": [row["occurrence_id"]]}
                  for row, group in zip(targets, expected)]
        with patch.object(ar, "actual_group_identity", wraps=ar.actual_group_identity) as identity:
            groups = sp._current_live_groups_for_staged_manual_actual(ledger, staged)
        self.assertEqual(groups, sorted(expected, key=lambda g: g["group_id"]))
        self.assertEqual(identity.call_count, len(ledger) + len(staged))
        with self.assertRaises(InterruptedError):
            sp._current_live_groups_for_staged_manual_actual(ledger, staged, cancelled=lambda: True)


    def test_same_session_source_error_keeps_strict_committed_recovery_only(self):
        app = headless_app(self.output)
        row = app.current()
        sp.stage_manual_actual_correction(self.output, row["review_id"], "ㄎㄢ")
        app.reload_records()
        event = sp.build_manual_expected_event(row, operation="ENTER_EXPECTED", expected_set="ㄎㄢ")
        self.assertTrue(app.save_event(row, event))
        wait_for_save(app)
        anchor = app._save_service.evidence_anchor
        expected_bytes = (self.output / "人工判定資料庫.json").read_bytes()
        root = sp.project_actual_evidence_root(self.output)
        journal = root / promotion.PROJECT_TRANSACTION_FILE
        with patch.object(gui.tk, "Toplevel", FakeProgressWidget), \
                patch.object(gui.ttk, "Progressbar", FakeProgressWidget), \
                patch.object(gui, "WrappedLabel", FakeProgressWidget), \
                patch.object(gui, "apply_screen_safe_geometry"), \
                patch.object(gui.threading, "Thread", ImmediateThread), \
                patch.object(gui.messagebox, "askyesno", return_value=True), \
                patch.object(gui.messagebox, "showerror") as errors, \
                patch.object(gui.messagebox, "showinfo") as success, \
                patch.object(sp, "deliver_pending_promotion_outbox", return_value={"status": "ISOLATED_NO_GLOBAL"}):
            with patch.object(sp, "refresh_actual_project", side_effect=OSError("isolated refresh failure")):
                app.apply_staged_actuals()
                app.root.update()
            self.assertTrue(errors.called)
            self.assertIsNotNone(sp.committed_project_recovery(root))
            self.assertTrue(app.staging_summary.get("staging_error"))
            self.assertTrue(app.staging_summary["actual_recovery_pending"])
            self.assertEqual(app.apply_actual_button.options["state"], "normal")
            self.assertFalse(app.staged_checked_occurrence_ids)
            self.assertIs(app._save_service.evidence_anchor, anchor)
            self.assertEqual((self.output / "人工判定資料庫.json").read_bytes(), expected_bytes)
            raw = journal.read_bytes()
            # A malformed plan is not a trusted recovery entry, nor a reason
            # to clear the original evidence anchor or accept staged checks.
            journal.write_bytes(b'{"state":"COMMITTED","recovery_plan":{}}')
            app.reload_records()
            self.assertFalse(app.staging_summary.get("actual_recovery_pending"))
            self.assertEqual(app.apply_actual_button.options["state"], "disabled")
            self.assertFalse(app.staged_checked_occurrence_ids)
            self.assertIs(app._save_service.evidence_anchor, anchor)
            journal.write_bytes(raw)
            app.reload_records()
            # Remove the journal after the strict availability check but while
            # the user is confirming. The worker must not apply new staging.
            success.reset_mock()
            with patch.object(gui.messagebox, "askyesno", side_effect=lambda *a, **k: (journal.unlink() or True)), \
                    patch.object(gui, "apply_staged_manual_actual_corrections") as ordinary_apply:
                app.apply_staged_actuals()
                app.root.update()
            ordinary_apply.assert_not_called()
            success.assert_not_called()
            self.assertIn("復原紀錄已變更", errors.call_args.args[1])
            self.assertFalse(app.staging_summary.get("actual_recovery_pending"))
            self.assertIs(app._save_service.evidence_anchor, anchor)
            journal.write_bytes(raw)
            app.reload_records()
            with patch.object(gui, "recover_committed_actual_project", wraps=sp.recover_committed_actual_project) as recover, \
                    patch.object(gui, "apply_staged_manual_actual_corrections") as ordinary_apply, \
                    patch.object(sp, "apply_staged_manual_actual_batch") as batch, \
                    patch.object(sp, "refresh_actual_project", side_effect=controlled_refresh):
                app.apply_staged_actuals()
                app.root.update()
            recover.assert_called_once_with(self.output)
            ordinary_apply.assert_not_called()
            batch.assert_not_called()
            self.assertIsNone(sp.committed_project_recovery(root))
            self.assertFalse(app.staging_summary.get("staging_error"))
            self.assertFalse(app.staging_summary.get("actual_recovery_pending"))
            self.assertIs(app._save_service.evidence_anchor, anchor)
            self.assertEqual((self.output / "人工判定資料庫.json").read_bytes(), expected_bytes)
        self.assertFalse((Path(self.tmp.name) / "isolated-local").exists())

    def test_pending_expected_rule_command_keeps_worker_until_validation_finishes(self):
        app = headless_app(self.output)
        entered, release = threading.Event(), threading.Event()
        validate = rs.ReviewSaveService.validate_resume_staging
        workers = []
        def delayed(service, snapshot, **kwargs):
            entered.set()
            if not release.wait(10):
                raise AssertionError("test did not release staging validator")
            return validate(service, snapshot, **kwargs)
        rules_path = Path(self.tmp.name) / "isolated-reusable-rules.json"
        with patch.object(rs.ReviewSaveService, "validate_resume_staging", delayed):
            app.reload_records(defer_staging=True)
            self.assertTrue(entered.wait(5))
            workers.append(app._staging_worker)
            try:
                row = app.current()
                event = sp.build_manual_expected_event(row, operation="ENTER_EXPECTED", expected_set="ㄎㄢ")
                self.assertTrue(app.save_event(row, event))
                wait_for_save(app)
                self.assertTrue(app._last_event_saved)
                workers.append(app._staging_worker)
                request = app._staging_request
                anchor = app._save_service.evidence_anchor
                with patch.object(gui.simpledialog, "askstring") as prompt, \
                        patch.object(gui, "save_reusable_expected_rule") as save_rule, \
                        patch.object(gui.messagebox, "showinfo") as notice:
                    app.create_reusable_expected_rule()
                prompt.assert_not_called()
                save_rule.assert_not_called()
                self.assertIn("仍在核對", notice.call_args.args[0])
                self.assertIs(app._staging_request, request)
                self.assertFalse(request["cancelled"].is_set())
                self.assertIs(app._save_service.evidence_anchor, anchor)
                self.assertTrue(app.staging_summary["staging_validation_pending"])
                self.assertEqual(sp.load_or_initialize_db(self.output), app.db)
            finally:
                release.set()
                for worker in workers:
                    worker.join(15)
                    self.assertFalse(worker.is_alive())
            app.root.update()
        self.assertIsNone(app._staging_request)
        self.assertFalse(app.staging_summary.get("staging_validation_pending"))
        self.assertFalse(app.staging_summary.get("staging_error"))
        with patch.object(gui.simpledialog, "askstring", side_effect=["看", "independent dictionary source"]), \
                patch.object(gui.messagebox, "showinfo"), patch.object(gui.messagebox, "showerror") as errors, \
                patch.object(gui, "save_reusable_expected_rule", side_effect=lambda rule: sp.save_reusable_expected_rule(rule, path=rules_path)) as save_rule:
            app.create_reusable_expected_rule()
        errors.assert_not_called()
        save_rule.assert_called_once()
        self.assertTrue(rules_path.is_file())
        self.assertFalse(app.staging_summary.get("staging_validation_pending"))
        self.assertIs(app._save_service.evidence_anchor, anchor)
        self.assertFalse((Path(self.tmp.name) / "isolated-local").exists())
