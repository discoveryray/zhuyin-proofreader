from __future__ import annotations

import copy
import hashlib
import json
import multiprocessing
import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import actual_review as review
import global_exact_glyph_library as library
import global_glyph_promotion as promotion
import standalone_proofread as pipeline
from test_global_promotion_v580 import intent, sql_rows


def oid(index):
    return 'occ_' + hashlib.sha256(str(index).encode()).hexdigest()


def fixture_group():
    members = [{
        'occurrence_id': oid(i), 'review_id': 'rev_' + hashlib.sha256(str(i).encode()).hexdigest(),
        'pdf_name': 'book.pdf', 'physical_page': 1, 'char': '字', 'x0': i * 20, 'y0': 10,
        'x1': i * 20 + 10, 'y1': 30, 'actual': '', 'state': 'ACTUAL_DECODE_ERROR',
        'source_record': {'TTF字形SHA256': 'a' * 64},
    } for i in range(3)]
    return review.build_actual_group_for_entry(members, members[0])


def commit(output, eligible=True):
    root = pipeline.project_actual_evidence_root(Path(output))
    group = fixture_group()
    checked = [oid(0), oid(1)]
    review.stage_manual_actual_group(root, group, 'ㄅ', checked_occurrence_ids=checked, source='test visual')
    with ExitStack() as stack:
        if eligible:
            stack.enter_context(patch.object(review, 'direct_visual_intents', return_value=([intent()], [])))
        return review.apply_staged_manual_actual_batch(root, [group])


def crash_worker(output, store, after_delivery):
    commit(output)
    if after_delivery:
        promotion.deliver_pending_promotion_outbox(
            pipeline.project_actual_evidence_root(Path(output)), library.GlobalExactGlyphRepository.resolved(store))
    os._exit(29)


def prepared_publication_crash_worker(output):
    write = promotion._write_json
    def interrupted(path, value):
        if isinstance(value, dict) and value.get('state') == 'COMMITTED':
            os._exit(33)
        return write(path, value)
    with patch.object(promotion, '_write_json', side_effect=interrupted):
        commit(output)


def crash_after_refresh_worker(output, store):
    output = Path(output)
    ledger = [dict(row, actual='ㄅ') for row in fixture_group()['members']]
    with ExitStack() as stack:
        stack.enter_context(patch.object(pipeline, 'json_load_strict', return_value={}))
        stack.enter_context(patch.object(pipeline, 'validate_manifest_integrity'))
        stack.enter_context(patch.object(pipeline, 'validate_output_artifact_hashes'))
        stack.enter_context(patch.object(pipeline, 'load_or_initialize_db', return_value={}))
        stack.enter_context(patch.object(pipeline, 'materialize_ledger', return_value=ledger))
        stack.enter_context(patch.object(pipeline, '_clear_actual_dependent_events', return_value=0))
        stack.enter_context(patch.object(pipeline, 'refresh_actual_project', return_value=output / 'report.xlsx'))
        stack.enter_context(patch.object(pipeline, 'deliver_pending_promotion_outbox', side_effect=lambda root:
            promotion.deliver_pending_promotion_outbox(root, library.GlobalExactGlyphRepository.resolved(store))))
        stack.enter_context(patch.object(pipeline, 'acknowledge_project_refresh', side_effect=lambda *args: os._exit(31)))
        pipeline.recover_committed_actual_project(output)


class DurableActualRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='phase4-recovery-')
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / 'project'
        self.root = pipeline.project_actual_evidence_root(self.output)
        self.repo = library.GlobalExactGlyphRepository.resolved(Path(self.temp.name) / 'global')
        self.ledger = [dict(row, actual='ㄅ') for row in fixture_group()['members']]
        self.calls = []
        self.events = {'events': {row['review_id']: {
            'action': '確認現版差異', 'expected_set': ['ㄆ'],
            'expected_evidence': 'independent expected', 'context_evidence': 'context',
        } for row in self.ledger}}

    def run_recovery(self, fail=None, wrapper=False, mismatch=False, defer_ack=False):
        real_clear = pipeline._clear_actual_dependent_events
        def deliver(root):
            self.calls.append('delivery')
            if fail == 'global':
                return {'status': 'PENDING_RETRY'}
            return promotion.deliver_pending_promotion_outbox(root, self.repo)
        def clear(output, ledger, ids):
            self.calls.append(('clear', sorted(ids)))
            if fail == 'clear':
                raise OSError('clear failed')
            return real_clear(output, ledger, ids)
        def refresh(output):
            self.calls.append('refresh')
            if fail == 'refresh':
                raise OSError('refresh failed')
            return output / 'report.xlsx'
        def verify(ledger, conditions):
            self.calls.append(('verify', copy.deepcopy(conditions)))
            return original_verify(ledger, conditions)
        original_verify = pipeline._verify_manual_actual_batch_postconditions
        refreshed = copy.deepcopy(self.ledger)
        if mismatch:
            refreshed[0]['actual'] = 'ㄆ'
        with ExitStack() as stack:
            stack.enter_context(patch.object(pipeline, 'deliver_pending_promotion_outbox', side_effect=deliver))
            stack.enter_context(patch.object(pipeline, 'json_load_strict', return_value={}))
            stack.enter_context(patch.object(pipeline, 'validate_manifest_integrity'))
            stack.enter_context(patch.object(pipeline, 'validate_output_artifact_hashes'))
            stack.enter_context(patch.object(pipeline, 'load_or_initialize_db', return_value=self.events))
            stack.enter_context(patch.object(pipeline, 'materialize_ledger', side_effect=[self.ledger, refreshed]))
            stack.enter_context(patch.object(pipeline, '_clear_actual_dependent_events', side_effect=clear))
            stack.enter_context(patch.object(pipeline, 'refresh_actual_project', side_effect=refresh))
            stack.enter_context(patch.object(pipeline, '_verify_manual_actual_batch_postconditions', side_effect=verify))
            stack.enter_context(patch.object(review, 'apply_staged_manual_actual_batch', side_effect=AssertionError('must not reapply staging')))
            if fail == 'ack_crash':
                stack.enter_context(patch.object(pipeline, 'acknowledge_project_refresh', side_effect=SystemExit(31)))
            if wrapper:
                return pipeline.refresh_actual_with_recovery(self.output)
            return pipeline.recover_committed_actual_project(self.output, acknowledge=not defer_ack)

    def crash(self, after_delivery):
        context = multiprocessing.get_context('spawn')
        process = context.Process(target=crash_worker, args=(str(self.output), str(self.repo.path.parent), after_delivery))
        process.start(); process.join(30)
        self.assertFalse(process.is_alive())
        self.assertEqual(process.exitcode, 29)
        self.assertEqual(review.load_manual_actual_staging(self.root)['staged_groups'], [])

    def assert_recovered(self):
        self.assertIsNone(promotion.committed_project_recovery(self.root))
        self.assertEqual(self.calls[0], 'delivery')
        self.assertEqual(self.calls[1], ('clear', sorted(oid(i) for i in range(3))))
        self.assertEqual(self.calls[2], 'refresh')
        self.assertEqual(len(self.calls[3][1]), 2)
        self.assertTrue(all(event['action'] == '解決expected證據' for event in self.events['events'].values()))
        self.assertTrue(all(event['expected_set'] == ['ㄆ'] for event in self.events['events'].values()))

    def test_plan_is_durable_before_committed_and_prepared_still_rolls_back(self):
        process = multiprocessing.get_context('spawn').Process(
            target=prepared_publication_crash_worker, args=(str(self.output),))
        process.start(); process.join(30)
        self.assertFalse(process.is_alive())
        self.assertEqual(process.exitcode, 33)
        journal = json.loads((self.root / promotion.PROJECT_TRANSACTION_FILE).read_text())
        self.assertEqual(journal['state'], 'PREPARED')
        self.assertEqual(len(journal['recovery_plan']['checked_postconditions']), 2)
        self.assertEqual(review.load_manual_actual_staging(self.root)['staged_groups'], [])
        self.assertEqual(promotion.recover_pending_project_actual_write(self.root), 'PREPARED')
        self.assertTrue(review.load_manual_actual_staging(self.root)['staged_groups'])
        self.assertTrue(all(not (self.root / name).exists() for name in promotion.PROJECT_FILES))
        self.assertFalse(self.repo.path.exists())

    def test_process_restart_after_project_commit_before_global_delivery(self):
        self.crash(False)
        self.assertFalse(self.repo.path.exists())
        result = self.run_recovery()
        self.assertEqual(result['project_refresh'], 'SUCCESS')
        self.assert_recovered()
        self.assertEqual(len(sql_rows(self.repo, 'source_evidence')), 1)

    def test_process_restart_after_global_delivery_reuses_original_receipt(self):
        self.crash(True)
        receipts = sql_rows(self.repo, 'processed_intent')
        self.run_recovery()
        self.assert_recovered()
        self.assertEqual(sql_rows(self.repo, 'processed_intent'), receipts)
        self.assertEqual(len(sql_rows(self.repo, 'source_evidence')), 1)

    def test_clear_failure_preserves_plan_and_retries_durable_affected_ids(self):
        commit(self.output)
        before = (self.root / review.OCCURRENCE_OVERRIDE_FILE).read_bytes()
        plan = promotion.committed_project_recovery(self.root)
        with self.assertRaises(pipeline.ManualActualPostApplyError):
            self.run_recovery('clear')
        self.assertNotIn('refresh', self.calls)
        self.assertEqual(promotion.committed_project_recovery(self.root), plan)
        self.assertEqual((self.root / review.OCCURRENCE_OVERRIDE_FILE).read_bytes(), before)
        self.calls.clear(); self.run_recovery(); self.assert_recovered()

    def test_refresh_failure_preserves_both_commits_and_plan(self):
        commit(self.output)
        with self.assertRaises(pipeline.ManualActualPostApplyError):
            self.run_recovery('refresh')
        self.assertIsNotNone(promotion.committed_project_recovery(self.root))
        receipts = sql_rows(self.repo, 'processed_intent')
        self.calls.clear(); self.run_recovery(); self.assert_recovered()
        self.assertEqual(sql_rows(self.repo, 'processed_intent'), receipts)

    def test_crash_after_refresh_before_ack_is_idempotent(self):
        commit(self.output)
        process = multiprocessing.get_context('spawn').Process(
            target=crash_after_refresh_worker, args=(str(self.output), str(self.repo.path.parent)))
        process.start(); process.join(30)
        self.assertFalse(process.is_alive())
        self.assertEqual(process.exitcode, 31)
        self.assertIsNotNone(promotion.committed_project_recovery(self.root))
        receipts = sql_rows(self.repo, 'processed_intent')
        self.calls.clear(); self.run_recovery(); self.assert_recovered()
        self.assertEqual(sql_rows(self.repo, 'processed_intent'), receipts)

    def test_single_correction_can_defer_ack_until_additional_postcondition(self):
        commit(self.output)
        result = self.run_recovery(defer_ack=True)
        self.assertEqual(result['project_refresh'], 'SUCCESS')
        self.assertEqual(promotion.project_refresh_token(self.root), result['project_refresh_token'])
        promotion.acknowledge_project_refresh(self.root, result['project_refresh_token'])
        self.assertIsNone(promotion.committed_project_recovery(self.root))

    def test_checked_mismatch_fails_closed_and_retains_plan(self):
        commit(self.output)
        with self.assertRaises(pipeline.ManualActualPostApplyError) as raised:
            self.run_recovery(mismatch=True)
        self.assertEqual(raised.exception.project_refresh, 'FAILED')
        self.assertIsNotNone(promotion.committed_project_recovery(self.root))

    def test_ineligible_local_correction_has_durable_plan_without_outbox(self):
        commit(self.output, eligible=False)
        self.assertFalse((self.root / promotion.GLOBAL_PROMOTION_OUTBOX_FILE).exists())
        plan = promotion.committed_project_recovery(self.root)[1]
        self.assertEqual(len(plan['checked_postconditions']), 2)
        self.run_recovery(); self.assert_recovered()
        self.assertFalse(self.repo.path.exists())

    def test_previous_plan_blocks_new_commit_without_losing_old_plan(self):
        commit(self.output)
        before = (self.root / promotion.PROJECT_TRANSACTION_FILE).read_bytes()
        with self.assertRaises(library.GlobalLibraryValidationError):
            commit(self.output)
        self.assertEqual((self.root / promotion.PROJECT_TRANSACTION_FILE).read_bytes(), before)
        self.assertTrue(review.load_manual_actual_staging(self.root)['staged_groups'])
        self.run_recovery()
        self.assertTrue(review.load_manual_actual_staging(self.root)['staged_groups'])
        commit(self.output)
        self.assertIsNotNone(promotion.committed_project_recovery(self.root))

    def test_refresh_actual_controller_finishes_pending_recovery(self):
        commit(self.output)
        self.assertEqual(self.run_recovery(wrapper=True), self.output / 'report.xlsx')
        self.assert_recovered()

    def test_no_pending_refresh_preserves_existing_behavior_without_creating_root(self):
        with patch.object(pipeline, 'refresh_actual_project', return_value=self.output / 'normal.xlsx') as refresh:
            self.assertEqual(pipeline.refresh_actual_with_recovery(self.output), self.output / 'normal.xlsx')
        refresh.assert_called_once_with(self.output)
        self.assertFalse(self.root.exists())

    def test_global_failure_is_nonfatal_split_pending_retry(self):
        commit(self.output)
        result = self.run_recovery('global')
        self.assertEqual(result['global_promotion_delivery']['status'], 'PENDING_RETRY')
        self.assert_recovered()
        self.assertEqual(promotion.load_promotion_outbox(self.root)['items'][0]['status'], 'PENDING')

    def test_recovery_plan_accepts_existing_canonical_neutral_tone(self):
        plan = {'schema_version': '1.0', 'affected_occurrence_ids': [oid(0)],
                'checked_postconditions': [{'group_id': fixture_group()['group_id'],
                                           'occurrence_id': oid(0), 'reading': '˙ㄒㄧ'}]}
        self.assertEqual(promotion.validate_post_commit_recovery_plan(plan), plan)

    def test_strict_plan_and_journal_reject_malformed_contracts_without_mutation(self):
        commit(self.output)
        path = self.root / promotion.PROJECT_TRANSACTION_FILE
        original = json.loads(path.read_text())
        bad = []
        for key, value in [('state', 'UNKNOWN'), ('unknown', 1), ('recovery_plan', None)]:
            bad.append(dict(original, **{key: value}))
        for mutation in [
            lambda p: p.update(unknown=1),
            lambda p: p.update(schema_version='future'),
            lambda p: p['affected_occurrence_ids'].append(p['affected_occurrence_ids'][0]),
            lambda p: p['checked_postconditions'].append(p['checked_postconditions'][0]),
            lambda p: p['checked_postconditions'].append(dict(p['checked_postconditions'][0], reading='ㄆ')),
            lambda p: p['affected_occurrence_ids'].append('bad'),
            lambda p: p['checked_postconditions'][0].update(reading='word'),
            lambda p: p['checked_postconditions'][0].update(reading='ㄒㄧ˙'),
            lambda p: p['checked_postconditions'][0].update(group_id='bad'),
            lambda p: p['checked_postconditions'][0].update(occurrence_id='bad'),
            lambda p: p['checked_postconditions'][0].update(expected='forbidden'),
        ]:
            journal = copy.deepcopy(original); mutation(journal['recovery_plan']); bad.append(journal)
        for journal in bad:
            raw = json.dumps(journal).encode(); path.write_bytes(raw)
            with self.subTest(journal=journal), self.assertRaises(library.GlobalLibraryValidationError):
                promotion.committed_project_recovery(self.root)
            self.assertEqual(path.read_bytes(), raw)


if __name__ == '__main__':
    unittest.main()
