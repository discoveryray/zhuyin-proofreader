from __future__ import annotations

import copy
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import actual_review as ar
import standalone_proofread as sp
import review_save_service as service_module
from review_save_service import ReviewSaveService, StaleReviewProjectError
from tests.test_staged_review_navigation_v580 import create_staged_navigation_fixture


class ReviewSaveServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = tempfile.TemporaryDirectory()
        cls.template = Path(cls.fixture.name)
        create_staged_navigation_fixture(cls.template)

    @classmethod
    def tearDownClass(cls):
        cls.fixture.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)
        shutil.copytree(self.template, self.output, dirs_exist_ok=True)
        raw = (self.output / "校對工作階段.json").read_text(encoding="utf-8")
        # Use JSON-safe path replacement in a copy of an explicitly synthetic
        # source fixture, never in an installed runtime asset.
        import json
        old = json.dumps(str(self.template))[1:-1]
        new = json.dumps(str(self.output))[1:-1]
        self.manifest = sp.seal_manifest(json.loads(raw.replace(old, new)))
        sp.json_save(self.output / "校對工作階段.json", self.manifest)
        self.db = sp.load_or_initialize_db(self.output)
        self.service = ReviewSaveService(self.output)

    def row(self, n=0):
        return self.manifest["records"][n]

    def event(self, n=0, reading="ㄎㄢˋ"):
        return sp.build_manual_expected_event(self.row(n), operation="ENTER_EXPECTED", expected_set=reading)

    def save(self, event, n=0):
        result = self.service.save_event(self.row(n)["review_id"], event,
                                         expected_manifest=self.manifest, expected_db=self.db)
        self.db = result.db
        return result

    def test_incremental_overwrite_undo_match_full_and_preserve_actual(self):
        original_actual = sp.actual_confirmation_snapshot(self.row())
        first = self.save(self.event())
        with patch.object(sp, "prepare_review_ledger", wraps=sp.prepare_review_ledger) as full:
            for event in (self.event(reading="ㄎㄢ"), self.event(), None, self.event(reading="ㄎㄢ")):
                result = self.save(event)
                self.assertEqual(result.ledger, sp.materialize_ledger(self.manifest, result.db))
                self.assertEqual(sp.actual_confirmation_snapshot(result.resolved_entry), original_actual)
                self.assertEqual(result.timings["ledger_full_rebuild"], 0)
            # Only the explicit reference materializations above ran; the
            # incremental service replayed precisely one original baseline row.
            self.assertEqual(full.call_count, 4)
        self.assertNotEqual(first.version_token, result.version_token)
        self.assertEqual(first.resolved_entry["state"], "PASS")

    def test_expected_survives_unresolved_actual_and_confirmation_replacement(self):
        unresolved = self.save(self.event(2), 2)
        self.assertEqual(unresolved.resolved_entry["actual_status"], "UNRESOLVED")
        self.assertEqual(unresolved.resolved_entry["expected_status"], "RESOLVED")
        result = self.save(self.event(reading="ㄎㄢ"))
        row = result.resolved_entry
        confirmation = {**self.db["events"][row["review_id"]], "action": "確認現版差異",
                        "confirmation_actual_snapshot": sp.actual_confirmation_snapshot(row),
                        **{gate: True for gate in sp.CONFIRMATION_GATES}}
        result = self.save(confirmation)
        self.assertEqual(result.resolved_entry["state"], "TEXTBOOK_ERROR_CONFIRMED")
        self.assertEqual(result.ledger, sp.materialize_ledger(self.manifest, result.db))
        result = self.save(None)
        self.assertEqual(result.ledger, sp.materialize_ledger(self.manifest, result.db))

    def test_reusable_rule_baseline_is_restored_by_undo(self):
        rules = sp._canonical_rule_doc({"rules": [{
            "rule_id": "isolated-test-rule", "phrase": "看一看", "target_char": "看", "target_index": 0,
            "expected_set": ["ㄎㄢ"], "evidence": "isolated approved expected rule", "context_contains": "",
        }]})
        self.manifest["reusable_expected_rules"] = rules
        self.manifest["reusable_expected_rules_sha256"] = sp.reusable_rules_sha256(rules)
        sp.json_save(self.output / "校對工作階段.json", sp.seal_manifest(self.manifest))
        original = sp.materialize_ledger(self.manifest, self.db)
        self.assertEqual(original[0]["expected_set"], ["ㄎㄢ"])
        result = self.save(self.event())
        self.assertEqual(result.resolved_entry["expected_set"], ["ㄎㄢˋ"])
        result = self.save(None)
        self.assertEqual(result.ledger, original)

    def test_actual_evidence_external_change_invalidates_cache(self):
        self.save(self.event())
        path = sp.project_actual_evidence_root(self.output) / sp.GLYPH_CONFLICT_FILE
        path.write_text("external evidence change", encoding="utf-8")
        original_db = (self.output / "人工判定資料庫.json").read_bytes()
        anchor = self.service.evidence_anchor
        for replacement in (False, True, False, True):
            if replacement:
                self.service = self.service.with_invalidated_cache()
            else:
                self.service.invalidate()
            self.assertIs(self.service.evidence_anchor, anchor)
            with self.subTest(replacement=replacement), self.assertRaisesRegex(StaleReviewProjectError, "actual 證據或應標規則"):
                self.save(self.event(1), 1)
            self.assertEqual(original_db, (self.output / "人工判定資料庫.json").read_bytes())

    def test_external_rule_change_and_explicit_reload_invalidation(self):
        rules_path = self.output / "isolated-reusable-rules.json"
        sp.json_save(rules_path, {"schema_version": "1.0", "rules": []})
        original_rules = rules_path.read_bytes()
        with patch.object(sp, "REUSABLE_EXPECTED_RULES", rules_path):
            self.save(self.event())
            rules_path.write_text('{"schema_version":"1.0", "rules": []}', encoding="utf-8")
            with self.assertRaisesRegex(StaleReviewProjectError, "應標規則已變更"):
                self.save(self.event(1), 1)
            # Cache eviction is not source validation, even when changed rules
            # are semantically equal. Repeated reload/replacement must reject.
            self.service.invalidate()
            with self.assertRaises(StaleReviewProjectError):
                self.save(self.event(1), 1)
            self.service = self.service.with_invalidated_cache()
            with self.assertRaises(StaleReviewProjectError):
                self.save(self.event(1), 1)
            # Restoring the exact previously verified evidence permits retry.
            rules_path.write_bytes(original_rules)
            with patch.object(sp, "prepare_review_ledger", wraps=sp.prepare_review_ledger) as full:
                result = self.save(self.event(1), 1)
                self.assertEqual(full.call_count, 1)
            self.assertEqual(result.ledger, sp.materialize_ledger(self.manifest, result.db))

    def test_changed_sealed_snapshot_requires_full_validation_before_reanchor(self):
        self.save(self.event())
        old_anchor = self.service.evidence_anchor
        # Valid empty project evidence is a synthetic external change. A fresh
        # sealed fixture snapshot below represents successful pipeline refresh;
        # this test is not a claim about real textbook decoding.
        sp.ensure_user_evidence_files(sp.project_actual_evidence_root(self.output))
        with self.assertRaises(StaleReviewProjectError):
            self.save(self.event(1), 1)
        self.service = self.service.with_invalidated_cache()
        refreshed = copy.deepcopy(self.manifest)
        refreshed["session_id"] = "isolated-successful-refresh"
        sp.json_save(self.output / "校對工作階段.json", sp.seal_manifest(refreshed))
        self.manifest = refreshed
        with patch.object(sp, "prepare_review_ledger", side_effect=ValueError("invalid refreshed ledger")), \
                self.assertRaisesRegex(ValueError, "invalid refreshed ledger"):
            self.save(self.event(1), 1)
        self.assertIs(self.service.evidence_anchor, old_anchor)
        with patch.object(sp, "prepare_review_ledger", wraps=sp.prepare_review_ledger) as full:
            result = self.save(self.event(1), 1)
            self.assertEqual(full.call_count, 1)
        self.assertNotEqual(self.service.evidence_anchor, old_anchor)
        self.assertEqual(self.service.evidence_anchor.manifest_seal, refreshed["manifest_integrity_sha256"])
        self.assertEqual(result.ledger, sp.materialize_ledger(refreshed, result.db))

    def test_failed_first_write_keeps_verified_anchor_and_unchanged_source_can_retry(self):
        with patch.object(sp.os, "fsync", side_effect=OSError("disk full")), self.assertRaises(OSError):
            self.save(self.event())
        self.assertIsNotNone(self.service.evidence_anchor)
        self.assertEqual(self.db, sp.load_or_initialize_db(self.output))
        self.service = self.service.with_invalidated_cache()
        result = self.save(self.event())
        self.assertEqual(result.resolved_entry["state"], "PASS")

    def test_no_staging_does_not_materialize_or_read_again(self):
        with patch.object(sp, "prepare_review_ledger", wraps=sp.prepare_review_ledger) as full, \
                patch.object(sp, "manual_actual_staging_summary", side_effect=AssertionError("duplicate read")):
            result = self.save(self.event())
            self.assertEqual(full.call_count, 1)
            result = self.save(self.event(1), 1)
            self.assertEqual(full.call_count, 1)
        self.assertEqual(result.staging_summary["staged_checked_occurrence_ids"], [])

    def test_valid_staging_expected_edit_and_undo_match_full_summary(self):
        sp.stage_manual_actual_correction(self.output, self.row()["review_id"], "ㄎㄢ")
        for event in (self.event(reading="ㄎㄢ"), self.event(), None):
            result = self.save(event)
            self.assertEqual(result.staging_summary, sp.manual_actual_staging_summary(self.output, ledger=result.ledger))
            self.assertEqual(result.staging_summary["staged_checked_occurrence_ids"], [self.row()["occurrence_id"]])

    def test_changed_staging_forces_full_and_invalid_staging_never_hides(self):
        self.save(self.event())
        sp.stage_manual_actual_correction(self.output, self.row(1)["review_id"], "ㄎㄢ")
        with patch.object(sp, "prepare_review_ledger", wraps=sp.prepare_review_ledger) as full:
            result = self.save(self.event(1), 1)
            self.assertEqual(full.call_count, 1)
        staging_path = ar.manual_actual_staging_path(sp.project_actual_evidence_root(self.output))
        staging = sp.json_load_strict(staging_path)
        staging["staged_groups"][0]["group_snapshot"] = "0" * 64
        sp.json_save(staging_path, staging)
        result = self.save(self.event(1, "ㄎㄢ"), 1)
        self.assertIn("staging_error", result.staging_summary)
        self.assertEqual(result.staging_summary["staged_checked_occurrence_ids"], [])
        self.assertEqual(result.db, sp.load_or_initialize_db(self.output))

    def test_malformed_staging_does_not_lose_saved_expected_or_hide(self):
        self.save(self.event())
        path = ar.manual_actual_staging_path(sp.project_actual_evidence_root(self.output))
        path.write_text('{"staged_groups": [], "staged_groups": []}', encoding="utf-8")
        result = self.save(self.event(1), 1)
        self.assertIn("staging_error", result.staging_summary)
        self.assertEqual(result.staging_summary["staged_checked_occurrence_ids"], [])
        self.assertEqual(result.db, sp.load_or_initialize_db(self.output))

    def test_same_size_mtime_source_and_artifact_mutation_rejected(self):
        self.save(self.event())
        for key in ("pdf", "actual_workbook", "candidate_workbook"):
            path = Path(self.manifest["pdfs"][0][key])
            raw, stat = path.read_bytes(), path.stat()
            modified = bytes([raw[0] ^ 1]) + raw[1:]
            path.write_bytes(modified)
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            before = (self.output / "人工判定資料庫.json").read_bytes()
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "內容已變更"):
                self.save(self.event(1), 1)
            self.assertEqual(before, (self.output / "人工判定資料庫.json").read_bytes())
            path.write_bytes(raw)

    def test_external_sealed_session_and_database_changes_rejected(self):
        self.save(self.event())
        path = self.output / "校對工作階段.json"
        original = path.read_bytes()
        changed = copy.deepcopy(self.manifest)
        changed["session_id"] = "different session"
        sp.json_save(path, sp.seal_manifest(changed))
        with self.assertRaises(StaleReviewProjectError):
            self.save(self.event(1), 1)
        path.write_bytes(original)
        db = copy.deepcopy(self.db)
        db["events"][self.row(1)["review_id"]] = self.event(1)
        sp.json_save(self.output / "人工判定資料庫.json", db)
        with self.assertRaises(StaleReviewProjectError):
            self.save(self.event(1, "ㄎㄢ"), 1)

    def test_non_object_db_roots_reject_before_replay_and_write_in_cold_and_warm_cache(self):
        path = self.output / "人工判定資料庫.json"
        initial_bytes = path.read_bytes()
        expected_message = "DATA_INTEGRITY_ERROR：現版人工判定資料庫根節點必須是物件"
        for warm in (False, True):
            for invalid in (b"[]", b"null", b"false", b"0", b'""'):
                with self.subTest(warm=warm, root=invalid):
                    path.write_bytes(initial_bytes)
                    self.db = sp.load_or_initialize_db(self.output)
                    self.service = ReviewSaveService(self.output)
                    if warm:
                        self.save(self.event())
                        self.save(None)
                        self.assertEqual(self.db["events"], {})
                        self.assertIsNotNone(self.service._ledger)
                    displayed_db = self.db
                    before_db = copy.deepcopy(displayed_db)
                    before_manifest = copy.deepcopy(self.manifest)
                    path.write_bytes(invalid)
                    with self.assertRaisesRegex(ValueError, expected_message):
                        sp.load_or_initialize_db(self.output)
                    for attempt in ("initial", "retry", "invalidate", "replacement"):
                        if attempt == "invalidate":
                            self.service.invalidate()
                        elif attempt == "replacement":
                            self.service = self.service.with_invalidated_cache()
                        with self.subTest(attempt=attempt), \
                                patch.object(sp, "prepare_review_ledger") as replay, \
                                patch.object(sp, "json_save") as write:
                            with self.assertRaisesRegex(ValueError, expected_message):
                                self.save(self.event())
                            replay.assert_not_called()
                            write.assert_not_called()
                        self.assertEqual(path.read_bytes(), invalid)
                        self.assertIs(self.db, displayed_db)
                        self.assertEqual(self.db, before_db)
                        self.assertEqual(self.manifest, before_manifest)

    def test_missing_and_empty_object_databases_keep_initialization_contract(self):
        path = self.output / "人工判定資料庫.json"
        for initial in (None, b"{}"):
            with self.subTest(initial=initial):
                if initial is None:
                    path.unlink()
                else:
                    path.write_bytes(initial)
                self.db = sp.load_or_initialize_db(self.output)
                self.assertEqual(self.db, sp.normalize_db({}))
                self.service = ReviewSaveService(self.output)
                result = self.save(self.event())
                self.assertEqual(result.resolved_entry["state"], "PASS")
                self.assertEqual(set(result.db["events"]), {self.row()["review_id"]})
                self.assertEqual(sp.load_or_initialize_db(self.output), result.db)

    def test_object_root_preserves_version_normalization_and_schema_rejection(self):
        path = self.output / "人工判定資料庫.json"
        old_version = {**sp.normalize_db({}), "version": "5.6.9"}
        sp.json_save(path, old_version)
        self.db = sp.load_or_initialize_db(self.output)
        self.service = ReviewSaveService(self.output)
        result = self.save(self.event())
        self.assertEqual(result.db["version"], sp.VERSION)
        self.assertEqual(result.db["migrated_from_version"], "5.6.9")
        invalid_objects = (
            {**sp.normalize_db({}), "events": []},
            {**sp.normalize_db({}), "session_schema_version": "999.0"},
            {"version": "5.1.5", "legacy_decisions": []},
        )
        for invalid in invalid_objects:
            with self.subTest(invalid=invalid):
                sp.json_save(path, invalid)
                before = path.read_bytes()
                self.service = self.service.with_invalidated_cache()
                with self.assertRaisesRegex(ValueError, "DECISION_SCHEMA_INCOMPATIBLE"):
                    self.save(self.event(1), 1)
                self.assertEqual(path.read_bytes(), before)
                self.assertFalse((self.output / "legacy_orphan_decisions.json").exists())

    def test_dependency_changed_during_replay_rejected_before_write(self):
        self.save(self.event())
        path = self.output / "校對工作階段.json"
        before = (self.output / "人工判定資料庫.json").read_bytes()
        replay = sp._apply_review_event

        def external_edit(row, event):
            result = replay(row, event)
            changed = copy.deepcopy(self.manifest)
            changed["session_id"] = "changed during worker"
            sp.json_save(path, sp.seal_manifest(changed))
            return result

        with patch.object(sp, "_apply_review_event", side_effect=external_edit), self.assertRaises(StaleReviewProjectError):
            self.save(self.event(1), 1)
        self.assertEqual(before, (self.output / "人工判定資料庫.json").read_bytes())

    def test_failed_write_retains_snapshot_then_reopen_recovers(self):
        first = self.save(self.event())
        before = (self.output / "人工判定資料庫.json").read_bytes()
        with patch.object(sp.os, "fsync", side_effect=OSError("disk full")), self.assertRaisesRegex(OSError, "disk full"):
            self.save(self.event(1), 1)
        self.assertIs(self.service._db, first.db)
        self.assertEqual(before, (self.output / "人工判定資料庫.json").read_bytes())
        self.assertEqual(list(self.output.glob("*.tmp")), [])
        result = self.save(self.event(1), 1)
        self.service = ReviewSaveService(self.output)
        reopened = self.save(None, 1)
        self.assertEqual(reopened.ledger, sp.materialize_ledger(self.manifest, reopened.db))
        self.assertIn(self.row()["review_id"], reopened.db["events"])
        self.assertIn(self.row(1)["review_id"], result.db["events"])

    def test_atomic_db_guard_rejects_concurrent_edit(self):
        path = self.output / "人工判定資料庫.json"
        old_sha = sp.sha256_file(path)
        changed = {**self.db, "events": {self.row()["review_id"]: self.event()}}
        sp.json_save(path, changed)
        with self.assertRaisesRegex(ValueError, "其他操作變更"):
            sp.json_save(path, self.db, expected_sha256=old_sha)
        self.assertEqual(sp.json_load_strict(path), changed)

    def test_service_reentrancy_and_runtime_failure_rejected(self):
        self.service._lock.acquire()
        try:
            with self.assertRaisesRegex(RuntimeError, "正在保存"):
                self.save(self.event())
        finally:
            self.service._lock.release()
        parse = service_module._json

        def invalid_manifest(raw, path):
            if path.name == "runtime_asset_manifest.json":
                return {"schema_version": "unknown", "assets": []}
            return parse(raw, path)

        with patch.object(service_module, "_json", side_effect=invalid_manifest), \
                self.assertRaises(sp.SourceValidationError):
            self.save(self.event())
        self.assertEqual(sp.load_or_initialize_db(self.output), self.db)


if __name__ == "__main__":
    unittest.main()
