from __future__ import annotations

import copy
import tempfile
import unittest
import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from openpyxl import Workbook

import actual_review as ar
import occurrence_ledger as ol
import review_gui as gui
import standalone_proofread as sp
from tests.review_save_test_support import QueuedRoot, wait_for_save
from tests.test_manual_review_usability_v580 import create_gui_fixture
from tests.test_manual_actual_gui_batch_v570 import DummyButton, FakeProgressWidget, ImmediateRoot, ImmediateThread
from export_zhuyin_readings import load_actual_occurrence_overrides, match_actual_occurrence_override


def create_staged_navigation_fixture(output, *, same_exact_peers=False):
    """Synthetic visual samples plus sealed fixture workbooks; no real textbook.

    Workbook rows are explicit fixture input, not a decoder acceptance claim.
    Their recorded hashes exercise the ordinary artifact integrity gate.
    """
    manifest = create_gui_fixture(output)
    if same_exact_peers:
        # Explicit synthetic group identity for navigation/transaction tests,
        # not a claim of production glyph identity or Global eligibility.
        for row in manifest["records"][:3]:
            row["source_record"]["TTF字形SHA256"] = "a" * 64
            row.update(actual="ㄎㄢˋ", actual_status="RESOLVED", actual_evidence="isolated fixture reading")
            row.update(ol.refresh_derived_state(row))
    pdf = Path(manifest["records"][0]["pdf"])
    info = {"pdf": str(pdf), "pdf_name": pdf.name,
            "pdf_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest()}
    for kind in ("actual", "candidate"):
        path = output / f"isolated-{kind}.xlsx"
        wb = Workbook()
        wb.active.title = "隔離測試輸入"
        wb.active.append(["occurrence_id", "actual", "expected"])
        for row in manifest["records"]:
            wb.active.append([row["occurrence_id"], row["actual"], "|".join(row["expected_set"])])
        wb.save(path)
        info[f"{kind}_workbook"] = str(path)
        info[f"{kind}_workbook_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest["pdfs"] = [info]
    manifest = sp.seal_manifest(manifest)
    sp.json_save(output / "校對工作階段.json", manifest)
    return manifest


def headless_app(output):
    app = gui.ReviewApp.__new__(gui.ReviewApp)
    app.root = QueuedRoot()
    app.output_dir = output
    app.manifest = sp.json_load_strict(output / "校對工作階段.json")
    app.db = sp.load_or_initialize_db(output)
    app.records, app.index = [], 0
    app.apply_actual_button = DummyButton()
    app.staging_status = DummyButton()
    app.show = MagicMock()
    app.reload_records()
    return app


def controlled_refresh(output):
    """Consume the real committed overrides, substituting only PDF re-decoding.

    This synthetic PDF has no textbook embedded Zhuyin glyph decoder fixture.
    The transaction, staging ack, expected replay and postconditions are real.
    """
    manifest = sp.json_load_strict(output / "校對工作階段.json")
    for row in manifest["records"]:
        overrides = load_actual_occurrence_overrides(sp.project_actual_evidence_root(output) / ar.OCCURRENCE_OVERRIDE_FILE, Path(row["pdf"]))
        override = match_actual_occurrence_override(row["source_record"], overrides)
        if override:
            row.update(actual=override["actual_reading"], actual_status="RESOLVED", actual_evidence="isolated refreshed direct override")
            row.update(ol.refresh_derived_state(row, preserve_confirmed=False))
    sp.json_save(output / "校對工作階段.json", sp.seal_manifest(manifest))
    return output / "fixture-report.xlsx"


class StagedNavigationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)
        create_staged_navigation_fixture(self.output)
        self.app = headless_app(self.output)

    def row(self, number):
        return next(row for row in sp.materialize_ledger(self.app.manifest, self.app.db)
                    if int(row["source_row_number"]) == number)

    def select(self, row):
        self.app.index = next(n for n, item in enumerate(self.app.records) if item["review_id"] == row["review_id"])

    def stage(self, row, reading="ㄎㄢ"):
        sp.stage_manual_actual_correction(self.output, row["review_id"], reading)
        self.app.reload_records()

    def save_expected(self, row, reading="ㄎㄢ"):
        self.select(row)
        event = sp.build_manual_expected_event(row, operation="ENTER_EXPECTED", expected_set=reading)
        self.app.save_event(row, event)
        wait_for_save(self.app)
        return event

    def test_difference_stage_removes_only_pending_comparison_and_navigates(self):
        row = self.row(5)
        self.select(row)
        before = copy.deepcopy(self.app.manifest)
        with patch.object(gui, "ActualReadingDialog", return_value=SimpleNamespace(result={
                "reading": "ㄎㄢ", "checked_occurrence_ids": [row["occurrence_id"]]})), \
                patch.object(gui.messagebox, "showinfo"):
            self.app.correct_actual()
        self.assertNotIn(row["review_id"], [item["review_id"] for item in self.app.records])
        self.assertNotEqual(self.app.current()["review_id"], row["review_id"])
        self.assertIn(row["occurrence_id"], self.app.waiting_actual_occurrence_ids)
        self.assertEqual(self.app.manifest, before)
        self.assertIn("1 筆等待套用後重新比較", self.app.staging_status.options["text"])

    def test_staged_expected_unresolved_remains_then_save_does_not_reselect_old_difference(self):
        row = self.row(1)
        self.stage(row)
        self.assertIn(row["review_id"], [item["review_id"] for item in self.app.records])
        event = self.save_expected(row)
        self.assertNotIn(row["review_id"], [item["review_id"] for item in self.app.records])
        self.assertEqual(self.app.current()["source_row_number"], 2)
        self.assertIn(row["occurrence_id"], self.app.staged_checked_occurrence_ids)
        with patch.object(gui.messagebox, "showinfo") as info, patch.object(gui.messagebox, "showwarning") as warning:
            self.app._explain_saved_expected(row)
        self.assertIn("應標注音已保存，目前注音套用後再比較", info.call_args.args[1])
        warning.assert_not_called()
        reopened = headless_app(self.output)
        self.assertEqual(reopened.waiting_actual_occurrence_ids, self.app.waiting_actual_occurrence_ids)
        self.assertEqual(reopened.db["events"][row["review_id"]], event)

    def test_no_stage_real_difference_remains_but_success_selects_next_same_character(self):
        row = self.row(5)
        # Two same-character comparisons, followed by an independent blocker.
        second = self.row(2)
        self.save_expected(second, "ㄎㄢ")
        self.app.reload_records()
        self.save_expected(row, "ㄎㄢ")
        self.assertNotEqual(self.app.current()["review_id"], row["review_id"])
        self.assertIn(second["review_id"], [item["review_id"] for item in self.app.records])
        self.assertIn(row["review_id"], [item["review_id"] for item in self.app.records])
        with patch.object(gui.messagebox, "showwarning") as warning:
            self.app._explain_saved_expected(row)
        self.assertIn("仍保留差異待確認", warning.call_args.args[1])

    def test_unknown_pending_and_hard_blocks_are_not_hidden(self):
        row = self.row(5)
        self.app.staged_checked_occurrence_ids = {row["occurrence_id"]}
        for state in {*ol.HARD_BLOCKING_STATES, "REVIEW_PENDING"}:
            with self.subTest(state=state):
                pending = {**row, "state": state}
                self.app._set_actionable_records_from_ledger([pending])
                self.assertEqual(self.app.records, [pending])
        pending = {**row, "blocking_state": "SOURCE_INVALID"}
        self.app._set_actionable_records_from_ledger([pending])
        self.assertEqual(self.app.records, [pending])

    def test_unchecked_exact_peer_remains_and_defer_revisit_preserve_counts(self):
        manifest = self.app.manifest
        for row in manifest["records"][:2]:
            row["source_record"]["TTF字形SHA256"] = "a" * 64
        sp.json_save(self.output / "校對工作階段.json", sp.seal_manifest(manifest))
        self.app = headless_app(self.output)
        first, second = self.row(1), self.row(2)
        self.save_expected(first)
        self.save_expected(second)
        self.stage(first)
        self.assertNotIn(first["review_id"], [r["review_id"] for r in self.app.records])
        self.assertIn(second["review_id"], [r["review_id"] for r in self.app.records])
        self.assertEqual(self.app.staged_checked_occurrence_ids, {first["occurrence_id"]})
        pending = len(self.app.records)
        for _ in range(pending):
            self.app.defer_current()
        self.assertEqual(len(self.app.deferred_items), pending)
        self.assertIn("未視為完成", self.app._empty_actionable_state()[1])
        self.app.revisit_deferred()
        self.assertEqual(len(self.app.records), pending)
        self.assertEqual(gui.review_lane(self.app.current()), "expected")

    def test_last_item_waiting_is_not_complete(self):
        row = self.row(5)
        self.stage(row)
        self.app.deferred_items = set()
        self.app._set_actionable_records_from_ledger([row])
        self.assertFalse(self.app.records)
        detail = self.app._empty_actionable_state()[1]
        self.assertIn("1 組", detail)
        self.assertIn("1 個位置", detail)
        self.assertIn("未視為完成", detail)

    def test_stale_actual_roster_and_source_changes_never_hide(self):
        row = self.row(5)
        self.stage(row)
        stage_path = ar.manual_actual_staging_path(sp.project_actual_evidence_root(self.output))
        stage_bytes = stage_path.read_bytes()
        before = copy.deepcopy(self.app.manifest)
        for mutation in ("actual", "identity", "source", "artifact"):
            with self.subTest(mutation=mutation):
                manifest = copy.deepcopy(before)
                item = next(r for r in manifest["records"] if r["review_id"] == row["review_id"])
                if mutation == "actual":
                    item["actual"] = "ㄎㄢˊ"
                elif mutation == "identity":
                    item["source_record"]["TTF字形SHA256"] = "a" * 64
                elif mutation == "source":
                    item["pdf_sha256"] = "0" * 64
                else:
                    manifest["pdfs"] = [{"pdf_name": "missing", "actual_workbook": "absent", "actual_workbook_sha256": "0" * 64}]
                sp.json_save(self.output / "校對工作階段.json", sp.seal_manifest(manifest))
                self.app.manifest = sp.json_load_strict(self.output / "校對工作階段.json")
                self.app.reload_records()
                self.assertTrue(self.app.staging_summary.get("staging_error"))
                self.assertFalse(self.app.staged_checked_occurrence_ids)
                self.assertIn(row["review_id"], [r["review_id"] for r in self.app.records])
                self.assertEqual(stage_path.read_bytes(), stage_bytes)

    def test_staging_read_failure_reveals_previous_waiting_without_losing_expected(self):
        row = self.row(1)
        self.stage(row)
        with patch.object(sp, "_manual_actual_summary_from_verified_snapshot", side_effect=OSError("isolated read failure")):
            event = self.save_expected(row)
        self.assertTrue(self.app._last_event_saved)
        self.assertIn(row["review_id"], [r["review_id"] for r in self.app.records])
        self.assertFalse(self.app.staged_checked_occurrence_ids)
        self.assertEqual(sp.load_or_initialize_db(self.output)["events"][row["review_id"]], event)

    def test_expected_save_failure_does_not_advance_and_actual_duplicate_does_not_stage_next(self):
        row = self.app.current()
        before = copy.deepcopy(self.app.db)
        with patch.object(sp, "json_save", side_effect=OSError("isolated write failure")), patch.object(gui.messagebox, "showerror"):
            self.app.save_event(row, sp.build_manual_expected_event(row, operation="ENTER_EXPECTED", expected_set="ㄎㄢ"))
            wait_for_save(self.app)
        self.assertEqual(self.app.current(), row)
        self.assertEqual(self.app.db, before)
        row = self.row(5)
        self.select(row)
        with patch.object(gui, "ActualReadingDialog", return_value=SimpleNamespace(result={
                "reading": "ㄎㄢ", "checked_occurrence_ids": [row["occurrence_id"]]})) as dialog, \
                patch.object(gui.messagebox, "showinfo"):
            self.app.correct_actual()
            self.app.correct_actual()
        self.assertEqual(dialog.call_count, 1)
        self.assertEqual(self.app.staging_summary["staged_group_count"], 1)

    def test_stage_failure_never_advances_or_reports_success(self):
        row = self.app.current()
        with patch.object(gui, "ActualReadingDialog", return_value=SimpleNamespace(result={
                "reading": "ㄎㄢ", "checked_occurrence_ids": [row["occurrence_id"]]})), \
                patch.object(gui, "stage_manual_actual_correction", side_effect=OSError("isolated failure")), \
                patch.object(gui.messagebox, "showerror") as error, patch.object(gui.messagebox, "showinfo") as success:
            self.app.correct_actual()
        error.assert_called_once()
        success.assert_not_called()
        self.assertEqual(self.app.current(), row)

    def test_expected_before_stage_then_changed_expected_uses_provable_projection(self):
        row = self.row(1)
        self.save_expected(row, "ㄎㄢ")
        self.stage(self.row(1), "ㄎㄢˊ")
        staged = ar.load_manual_actual_staging(sp.project_actual_evidence_root(self.output))["staged_groups"]
        current = self.row(1)
        event = sp.build_manual_expected_event(current, operation="ENTER_EXPECTED", expected_set="ㄎㄢˋ")
        self.app.db["events"][row["review_id"]] = event
        sp.json_save(self.output / "人工判定資料庫.json", self.app.db)
        self.app.reload_records()
        self.assertFalse(self.app.staging_summary.get("staging_error"))
        self.assertIn(row["occurrence_id"], self.app.waiting_actual_occurrence_ids)
        self.assertEqual(ar.load_manual_actual_staging(sp.project_actual_evidence_root(self.output))["staged_groups"], staged)
        # A historical snapshot after a different expected decision cannot be
        # reconstructed from current event + sealed baseline: preserve/reject.
        historical_group = ar.build_actual_group_for_entry([current], current)
        staged[0]["group_snapshot"] = historical_group["group_snapshot"]
        ar.manual_actual_staging_path(sp.project_actual_evidence_root(self.output)).write_text(
            __import__("json").dumps({"schema_version": ar.MANUAL_ACTUAL_STAGING_SCHEMA_VERSION, "staged_groups": staged}), encoding="utf-8")
        self.app.reload_records()
        self.assertTrue(self.app.staging_summary.get("staging_error"))
        self.assertFalse(self.app.staged_checked_occurrence_ids)

    def test_real_transaction_expected_retained_and_refresh_match_or_difference(self):
        for expected, wanted_state in (("ㄎㄢ", "PASS"), ("ㄎㄢˊ", "DIFFERENCE_PENDING_CONFIRMATION")):
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as directory:
                output = Path(directory)
                create_staged_navigation_fixture(output)
                app = headless_app(output)
                row = app.current()
                sp.stage_manual_actual_correction(output, row["review_id"], "ㄎㄢ")
                app.save_event(row, sp.build_manual_expected_event(row, operation="ENTER_EXPECTED", expected_set=expected))
                wait_for_save(app)
                expected_event = copy.deepcopy(app.db["events"][row["review_id"]])
                with patch.object(sp, "refresh_actual_project", side_effect=controlled_refresh):
                    sp.apply_staged_manual_actual_corrections(output)
                reopened = headless_app(output)
                result = next(r for r in sp.materialize_ledger(reopened.manifest, reopened.db) if r["review_id"] == row["review_id"])
                self.assertEqual(result["state"], wanted_state)
                self.assertEqual(reopened.db["events"][row["review_id"]], expected_event)
                self.assertEqual(reopened.staging_summary["staged_group_count"], 0)
                self.assertEqual(row["review_id"] in [r["review_id"] for r in reopened.records], wanted_state != "PASS")

    def test_commit_refresh_failure_is_visible_and_recoverable_without_reapplying(self):
        row = self.row(1)
        self.stage(row)
        expected_event = self.save_expected(row)
        with patch.object(sp, "refresh_actual_project", side_effect=OSError("isolated refresh failure")):
            with self.assertRaises(sp.ManualActualPostApplyError):
                sp.apply_staged_manual_actual_corrections(self.output)
        reopened = headless_app(self.output)
        self.assertEqual(reopened.staging_summary["staged_group_count"], 0)
        self.assertTrue(reopened.staging_summary["actual_recovery_pending"])
        self.assertEqual(reopened.apply_actual_button.options["state"], "normal")
        self.assertEqual(reopened.db["events"][row["review_id"]], expected_event)
        with patch.object(sp, "apply_staged_manual_actual_batch") as batch, patch.object(sp, "refresh_actual_project", side_effect=controlled_refresh):
            sp.apply_staged_manual_actual_corrections(self.output)
        batch.assert_not_called()
        self.assertFalse(headless_app(self.output).staging_summary["actual_recovery_pending"])

    def test_failed_transaction_preserves_stage_expected_and_gui_work_then_retry(self):
        row = self.row(1)
        self.stage(row)
        expected_event = self.save_expected(row)
        stage_path = ar.manual_actual_staging_path(sp.project_actual_evidence_root(self.output))
        before = stage_path.read_bytes()
        with patch.object(ar, "apply_verified_actual_group", side_effect=OSError("isolated transactional failure")), \
                patch.object(gui.messagebox, "askyesno", return_value=True), \
                patch.object(gui.messagebox, "showerror") as error, patch.object(gui.messagebox, "showinfo") as info, \
                patch.object(gui.threading, "Thread", ImmediateThread), \
                patch.object(gui.tk, "Toplevel", FakeProgressWidget), \
                patch.object(gui, "WrappedLabel", FakeProgressWidget), \
                patch.object(gui.ttk, "Progressbar", FakeProgressWidget), \
                patch.object(gui, "apply_screen_safe_geometry"):
            self.app.apply_staged_actuals()
            self.app.root.update()
        error.assert_called_once()
        info.assert_not_called()
        self.assertEqual(stage_path.read_bytes(), before)
        self.assertEqual(self.app.db["events"][row["review_id"]], expected_event)
        self.assertIn(row["occurrence_id"], self.app.waiting_actual_occurrence_ids)
        self.assertFalse(self.app._apply_in_progress)

    def test_apply_double_click_and_invalid_summary_do_not_launch_another_worker(self):
        self.stage(self.row(5))
        with patch.object(gui.messagebox, "askyesno", return_value=True), \
                patch.object(self.app, "_start_staged_actual_apply") as launch:
            self.app.apply_staged_actuals()
            self.app.apply_staged_actuals()
        launch.assert_called_once()
        self.app._apply_in_progress = False
        with patch.object(gui, "manual_actual_staging_summary", side_effect=OSError("unreadable")), \
                patch.object(gui.messagebox, "showerror") as error, \
                patch.object(self.app, "_start_staged_actual_apply") as launch:
            self.app.apply_staged_actuals()
            self.app.root.update()
        error.assert_called_once()
        launch.assert_not_called()

    def test_saved_expected_survives_render_failure_without_extra_success_prompt(self):
        row = self.app.current()
        with patch.object(self.app, "show", side_effect=RuntimeError("isolated render failure")), \
                patch.object(gui.messagebox, "showwarning") as warning, \
                patch.object(gui.messagebox, "showinfo") as success:
            event = self.save_expected(row)
            self.app._explain_saved_expected(row)
        warning.assert_called_once()
        success.assert_not_called()
        self.assertEqual(sp.load_or_initialize_db(self.output)["events"][row["review_id"]], event)

    def test_actual_save_with_expected_pending_advances_but_keeps_expected_task(self):
        row = self.app.current()
        with patch.object(gui, "ActualReadingDialog", return_value=SimpleNamespace(result={
                "reading": "ㄎㄢ", "checked_occurrence_ids": [row["occurrence_id"]]})), \
                patch.object(gui.messagebox, "showinfo"):
            self.app.correct_actual()
        self.assertEqual(self.app.current()["source_row_number"], 2)
        self.assertIn(row["review_id"], [r["review_id"] for r in self.app.records])
        self.select(row)
        self.app.primary = DummyButton()
        self.app.secondary = DummyButton()
        self.app.configure_actions(row)
        self.assertEqual(self.app.primary.options["text"], "輸入其他應標注音")
        before = copy.deepcopy(self.app.db)
        self.app._review_action_cooldown_until = 0
        self.app._rendered_review_id = row["review_id"]
        self.app.confirm_current_expected(row["review_id"])
        self.assertEqual(self.app.db, before)


class SameExactStagedNavigationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)
        manifest = create_staged_navigation_fixture(self.output, same_exact_peers=True)
        self.peers = manifest["records"][:3]
        self.ids = [row["occurrence_id"] for row in self.peers]
        self.app = headless_app(self.output)
        for row in self.peers:
            self.app.index = next(n for n, item in enumerate(self.app.records) if item["review_id"] == row["review_id"])
            self.app.save_event(row, sp.build_manual_expected_event(row, operation="ENTER_EXPECTED", expected_set="ㄎㄢ"))
            wait_for_save(self.app)
        self.expected_before = copy.deepcopy(self.app.db["events"])
        self.stage_path = ar.manual_actual_staging_path(sp.project_actual_evidence_root(self.output))

    def stage_gui(self, index, reading="ㄎㄢ"):
        row = self.peers[index]
        self.app.index = next(n for n, item in enumerate(self.app.records) if item["review_id"] == row["review_id"])
        self.app._review_action_cooldown_until = 0
        with patch.object(gui, "ActualReadingDialog", return_value=SimpleNamespace(result={
                "reading": reading, "checked_occurrence_ids": [row["occurrence_id"]]})), \
                patch.object(gui.messagebox, "showinfo") as success, \
                patch.object(gui.messagebox, "showwarning") as warning, \
                patch.object(gui.messagebox, "showerror") as error:
            self.app.correct_actual()
        return success, warning, error

    def test_individual_a_b_c_accumulate_only_checked_then_reopen_and_apply_once(self):
        for index in range(3):
            success, warning, error = self.stage_gui(index)
            success.assert_called_once()
            warning.assert_not_called()
            error.assert_not_called()
            wanted = set(self.ids[:index + 1])
            self.assertEqual(self.app.staged_checked_occurrence_ids, wanted)
            self.assertEqual(self.app.waiting_actual_occurrence_ids, wanted)
            self.assertEqual({r["occurrence_id"] for r in self.app.records} & set(self.ids), set(self.ids[index + 1:]))
            self.assertEqual(self.app.staging_summary["staged_group_count"], 1)
            self.assertEqual(ar.load_manual_actual_staging(sp.project_actual_evidence_root(self.output))["staged_groups"][0]["checked_occurrence_ids"], self.ids[:index + 1])
        reopened = headless_app(self.output)
        self.assertEqual(reopened.staged_checked_occurrence_ids, set(self.ids))
        self.assertEqual(reopened.db["events"], self.expected_before)
        # Retrying the same explicit checked item cannot duplicate evidence.
        sp.stage_manual_actual_correction(self.output, self.peers[0]["review_id"], "ㄎㄢ", [self.ids[0]])
        self.assertEqual(ar.load_manual_actual_staging(sp.project_actual_evidence_root(self.output))["staged_groups"][0]["checked_occurrence_ids"], self.ids)
        with patch.object(ar, "apply_verified_actual_group", wraps=ar.apply_verified_actual_group) as apply, \
                patch.object(sp, "refresh_actual_project", side_effect=controlled_refresh):
            result = sp.apply_staged_manual_actual_corrections(self.output)
        apply.assert_called_once()
        self.assertEqual(apply.call_args.kwargs["checked_occurrence_ids"], self.ids)
        self.assertCountEqual(result["postcondition_checked_occurrence_ids"], self.ids)
        self.assertEqual(result["applied_group_count"], 1)
        reopened = headless_app(self.output)
        self.assertEqual(reopened.db["events"], self.expected_before)
        self.assertFalse(reopened.staged_checked_occurrence_ids)
        self.assertFalse({r["occurrence_id"] for r in reopened.records} & set(self.ids))

    def test_different_reading_or_rejudgment_preserves_prior_and_does_not_advance(self):
        self.stage_gui(0)
        before = self.stage_path.read_bytes()
        success, warning, error = self.stage_gui(1, "ㄎㄢˊ")
        error.assert_called_once()
        self.assertIn("不同讀音", error.call_args.args[1])
        warning.assert_not_called()
        success.assert_not_called()
        self.assertEqual(self.app.current()["review_id"], self.peers[1]["review_id"])
        self.assertEqual(self.stage_path.read_bytes(), before)
        self.assertEqual(self.app.staged_checked_occurrence_ids, {self.ids[0]})
        with self.assertRaisesRegex(ValueError, "不同讀音"):
            sp.stage_manual_actual_correction(self.output, self.peers[0]["review_id"], "ㄎㄢˊ", [self.ids[0]])
        self.assertEqual(self.stage_path.read_bytes(), before)
        self.assertEqual(sp.load_or_initialize_db(self.output)["events"], self.expected_before)

    def test_stale_snapshot_or_changed_pdf_cannot_retain_prior_checks(self):
        self.stage_gui(0)
        before = self.stage_path.read_bytes()
        original_manifest = sp.json_load_strict(self.output / "校對工作階段.json")
        changed = copy.deepcopy(original_manifest)
        changed["records"][0]["actual"] = "ㄎㄢˊ"
        sp.json_save(self.output / "校對工作階段.json", sp.seal_manifest(changed))
        with self.assertRaisesRegex(ValueError, "identity 已變更"):
            sp.stage_manual_actual_correction(self.output, self.peers[1]["review_id"], "ㄎㄢ", [self.ids[1]])
        self.assertEqual(self.stage_path.read_bytes(), before)
        sp.json_save(self.output / "校對工作階段.json", original_manifest)
        pdf = Path(self.peers[0]["pdf"])
        pdf.write_bytes(pdf.read_bytes() + b"\nsource drift")
        with self.assertRaisesRegex(ValueError, "SOURCE_INVALID"):
            sp.stage_manual_actual_correction(self.output, self.peers[1]["review_id"], "ㄎㄢ", [self.ids[1]])
        self.assertEqual(self.stage_path.read_bytes(), before)
        self.app.reload_records()
        self.assertEqual({r["occurrence_id"] for r in self.app.records} & set(self.ids), set(self.ids))
        self.assertFalse(self.app.staged_checked_occurrence_ids)

    def test_bad_checked_lists_and_failed_stage_write_do_not_change_prior(self):
        self.stage_gui(0)
        before = self.stage_path.read_bytes()
        for checked in ([], [self.ids[1], self.ids[1]], ["unknown"], [" " + self.ids[1]]):
            with self.subTest(checked=checked), self.assertRaises(ValueError):
                sp.stage_manual_actual_correction(self.output, self.peers[1]["review_id"], "ㄎㄢ", checked)
            self.assertEqual(self.stage_path.read_bytes(), before)
        with patch.object(ar.os, "replace", side_effect=OSError("isolated staging replacement failure")):
            success, warning, error = self.stage_gui(1)
        error.assert_called_once()
        success.assert_not_called()
        self.assertEqual(self.stage_path.read_bytes(), before)
        self.assertEqual(self.app.staged_checked_occurrence_ids, {self.ids[0]})
        self.assertEqual(self.app.current()["review_id"], self.peers[1]["review_id"])

    def test_accumulated_checks_survive_apply_failure_and_committed_refresh_recovery(self):
        self.stage_gui(0)
        self.stage_gui(1)
        before = self.stage_path.read_bytes()
        with patch.object(ar, "apply_verified_actual_group", side_effect=OSError("isolated apply failure")):
            with self.assertRaisesRegex(OSError, "isolated apply failure"):
                sp.apply_staged_manual_actual_corrections(self.output)
        self.assertEqual(self.stage_path.read_bytes(), before)
        reopened = headless_app(self.output)
        self.assertEqual(reopened.staged_checked_occurrence_ids, set(self.ids[:2]))
        self.assertIn(self.ids[2], {r["occurrence_id"] for r in reopened.records})
        with patch.object(sp, "refresh_actual_project", side_effect=OSError("isolated refresh failure")):
            with self.assertRaises(sp.ManualActualPostApplyError):
                sp.apply_staged_manual_actual_corrections(self.output)
        with patch.object(sp, "apply_staged_manual_actual_batch") as batch, \
                patch.object(sp, "refresh_actual_project", side_effect=controlled_refresh):
            result = sp.apply_staged_manual_actual_corrections(self.output)
        batch.assert_not_called()
        self.assertCountEqual(result["postcondition_checked_occurrence_ids"], self.ids[:2])
        self.assertEqual(sp.load_or_initialize_db(self.output)["events"], self.expected_before)


if __name__ == "__main__":
    unittest.main()
