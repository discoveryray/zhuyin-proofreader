"""Explicit final-page actual and expected intents; no native windows or textbook writes."""
from __future__ import annotations

import copy
from pathlib import Path
from unittest.mock import Mock

import pytest

import global_exact_glyph_library as library
import global_glyph_promotion as promotion
import manual_surface_actual as surface
import standalone_proofread as sp
from actual_source_visual_safety import source_visual_safety
from glyph_learning_images import LearningImageStore
from review_gui import surface_review_candidate, surface_review_result
from review_operation_worker import ReviewOperationError, ReviewOperationState
from review_save_service import ReviewSaveService
from tests.test_overlay_confirm_usage_20261103 import _annotated_project


def _files(root):
    return {str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob('*') if path.is_file()}


def _request(output, *, reading='ㄅㄨˋ', expected='ㄆㄧㄥˊ', index=0):
    snapshot = ReviewSaveService(output).load_resume_snapshot()
    row = snapshot.ledger[index]
    prepared = sp.prepare_surface_actual_correction(output, row['review_id'])
    value = surface_review_result(prepared, reading=reading,
        actor='isolated explicit final-page input; no human GUI acceptance',
        user_expected_reading=expected)
    return snapshot, row, dict(value, review_id=row['review_id'],
        expected_manifest=snapshot.manifest, expected_db=snapshot.db)


def _dispatch(output, request):
    return ReviewOperationState(output).dispatch(
        'save_surface_review', request, lambda phase: None, lambda: False)


def test_surface_review_candidate_uses_only_actual_evidence_and_never_expected():
    prepared = {'proof': {'automatic_actual': 'ㄅ'}}
    entry = {'actual': 'ㄆ', 'expected_set': ['ㄇ'], 'expected': 'ㄇ'}
    assert surface_review_candidate(prepared, entry) == 'ㄅ'
    entry['manual_surface_actual'] = {'reading': 'ㄈ'}
    assert surface_review_candidate(prepared, entry) == 'ㄈ'
    prepared['proof']['automatic_actual'] = ''
    entry.pop('manual_surface_actual')
    assert surface_review_candidate(prepared, entry) == ''


def test_surface_review_expected_input_never_fills_missing_actual(tmp_path, monkeypatch):
    output, original = _annotated_project(tmp_path, monkeypatch, overpaint=True)
    prepared = sp.prepare_surface_actual_correction(output, original['records'][0]['review_id'])
    before = _files(output)
    with pytest.raises(ValueError):
        surface_review_result(prepared, reading='', actor='isolated explicit input',
                              user_expected_reading='ㄆ')
    assert _files(output) == before


@pytest.mark.parametrize('expected', ['ㄅㄨˋ', 'ㄆㄧㄥˊ'], ids=['matching', 'different'])
def test_surface_review_saves_independent_intents_only_at_current_occurrence(tmp_path, monkeypatch, expected):
    output, _ = _annotated_project(tmp_path, monkeypatch, overpaint=True)
    before, row, request = _request(output, expected=expected)
    root = sp.project_actual_evidence_root(output)
    raw_files = {name: (root / name).read_bytes() if (root / name).exists() else None
                 for name in promotion.PROJECT_FILES}
    assert source_visual_safety(row)['reason'] == 'SOURCE_OVERPAINT_CONFIRMED'
    assert not library.GlobalExactGlyphRepository.resolved().path.exists()
    assert not (LearningImageStore().root / 'evidence').exists()

    result = _dispatch(output, request)
    assert result['result'] == dict(local_status='SAVED', global_status='LOCAL_ONLY',
                                   status='SURFACE_REVIEW_SAVED')
    reopened = ReviewSaveService(output).load_resume_snapshot()
    saved = next(item for item in reopened.ledger if item['review_id'] == row['review_id'])
    assert saved['actual'] == 'ㄅㄨˋ'
    assert saved['expected_set'] == [expected]
    assert saved['surface_donor_eligible'] is False
    assert saved['raw_source_warning_retained'] is True
    event = reopened.db['events'][row['review_id']]
    assert event['manual_expected_decision']['operation'] == 'ENTER_EXPECTED'
    assert event['manual_expected_decision']['expected_set'] == [expected]
    if expected != saved['actual']:
        assert saved['state'] == 'DIFFERENCE_PENDING_CONFIRMATION'
        assert not saved.get('gui_confirmation')
    else:
        assert saved['state'] == 'PASS'
    assert [item for item in reopened.ledger if item['review_id'] != row['review_id']] == [
        item for item in before.ledger if item['review_id'] != row['review_id']]
    document = surface.load(root, reopened.manifest)
    assert len(document['records']) == 1 and not document['pending']
    record = document['records'][0]
    assert record['reading'] == 'ㄅㄨˋ'
    assert record['proof']['raw_reason'] == 'SOURCE_OVERPAINT_CONFIRMED'
    assert record['proof']['donor_eligible'] is False
    assert record['proof']['source']['occurrence_id'] == row['occurrence_id']
    assert {name: (root / name).read_bytes() if (root / name).exists() else None
            for name in promotion.PROJECT_FILES} == raw_files
    assert not library.GlobalExactGlyphRepository.resolved().path.exists()
    assert not (LearningImageStore().root / 'evidence').exists()


def test_surface_saved_expected_failure_is_reported_and_fresh_retry_preserves_actual(tmp_path, monkeypatch):
    output, _ = _annotated_project(tmp_path, monkeypatch, overpaint=True)
    before, row, request = _request(output)
    db_before = (output / '人工判定資料庫.json').read_bytes()
    with monkeypatch.context() as failure:
        failure.setattr(ReviewSaveService, 'save_event', Mock(side_effect=OSError('isolated expected write failure')))
        with pytest.raises(ReviewOperationError) as error:
            _dispatch(output, request)
    assert error.value.commit_state == 'SURFACE_SAVED_EXPECTED_PENDING'
    assert '實際注音已保存' in str(error.value)
    reopened = ReviewSaveService(output).load_resume_snapshot()
    current = next(item for item in reopened.ledger if item['review_id'] == row['review_id'])
    assert current['actual'] == 'ㄅㄨˋ' and current['surface_donor_eligible'] is False
    assert reopened.db == before.db
    assert (output / '人工判定資料庫.json').read_bytes() == db_before
    root = sp.project_actual_evidence_root(output)
    first = surface.load(root, reopened.manifest)
    assert len(first['records']) == 1 and not first['pending']

    _, _, retry = _request(output)
    _dispatch(output, retry)
    final = ReviewSaveService(output).load_resume_snapshot()
    assert final.db['events'][row['review_id']]['expected_set'] == ['ㄆㄧㄥˊ']
    assert surface.load(root, final.manifest) == first
    assert not library.GlobalExactGlyphRepository.resolved().path.exists()
    assert not (LearningImageStore().root / 'evidence').exists()


def test_surface_review_refuses_other_occurrence_pending_without_applying_it(tmp_path, monkeypatch):
    output, original = _annotated_project(tmp_path, monkeypatch, overpaint=True)
    other = original['records'][1]['review_id']
    prepared = sp.prepare_surface_actual_correction(output, other)
    sp.stage_surface_actual_correction(output, other, 'ㄇ', proof=prepared['proof'],
        actor='isolated other occurrence pending', whole_unit_readable=True,
        target_character_matches=True, reading_from_original=True, enable_format=True)
    _, _, request = _request(output)
    before = _files(output)
    with pytest.raises(ReviewOperationError, match='其他位置') as error:
        _dispatch(output, request)
    assert error.value.commit_state == 'NOT_WRITTEN'
    assert _files(output) == before


@pytest.mark.parametrize('mutation', ['proof', 'source', 'displayed-db'])
def test_surface_review_stale_inputs_refuse_before_surface_or_expected_write(tmp_path, monkeypatch, mutation):
    output, _ = _annotated_project(tmp_path, monkeypatch, overpaint=True)
    _, row, request = _request(output)
    if mutation == 'proof':
        request['proof'] = copy.deepcopy(request['proof'])
        request['proof']['raw_reason'] = 'FOREIGN_SOURCE_REASON'
    elif mutation == 'source':
        with Path(row['pdf']).open('ab') as stream:
            stream.write(b'\n% isolated source drift before surface review\n')
    else:
        request['expected_db'] = copy.deepcopy(request['expected_db'])
        request['expected_db']['events']['foreign'] = {'action': 'stale display'}
    before = _files(output)
    with pytest.raises((ValueError, ReviewOperationError)):
        _dispatch(output, request)
    assert _files(output) == before


@pytest.mark.parametrize(('key', 'value'), [
    ('reading', ''), ('user_expected_reading', ''), ('user_expected_reading', 'invalid reading'),
    ('whole_unit_readable', False), ('target_character_matches', False),
    ('reading_from_original', False), ('actor', ''), ('enable_format', False),
], ids=['missing-actual', 'missing-expected', 'invalid-expected', 'unreadable-unit',
        'wrong-character', 'not-original-reading', 'missing-actor', 'unaccepted-conversion'])
def test_surface_review_invalid_input_refuses_before_any_project_write(tmp_path, monkeypatch, key, value):
    output, _ = _annotated_project(tmp_path, monkeypatch, overpaint=True)
    _, _, request = _request(output)
    request[key] = value
    before = _files(output)
    with pytest.raises((ValueError, ReviewOperationError)):
        _dispatch(output, request)
    assert _files(output) == before


def test_surface_review_preserves_previous_expected_event_for_undo(tmp_path, monkeypatch):
    output, _ = _annotated_project(tmp_path, monkeypatch, overpaint=True)
    snapshot, row, _ = _request(output)
    prior = sp.build_manual_expected_event(row, operation='ENTER_EXPECTED',
                                         expected_set='ㄇㄧㄥˊ', rationale='isolated prior human intent')
    prior['undo_previous_event'] = None
    ReviewSaveService(output).save_event(row['review_id'], prior,
        expected_manifest=snapshot.manifest, expected_db=snapshot.db)
    before, _, request = _request(output)
    expected_prior = copy.deepcopy(before.db['events'][row['review_id']])
    expected_prior.pop('undo_previous_event')
    _dispatch(output, request)
    final = ReviewSaveService(output).load_resume_snapshot()
    event = final.db['events'][row['review_id']]
    assert event['manual_expected_decision']['expected_set'] == ['ㄆㄧㄥˊ']
    assert event['undo_previous_event'] == expected_prior
    assert 'undo_previous_event' not in event['undo_previous_event']


def test_surface_review_preserves_intervening_expected_writer_after_actual_commit(tmp_path, monkeypatch):
    output, _ = _annotated_project(tmp_path, monkeypatch, overpaint=True)
    _, row, request = _request(output)
    state = ReviewOperationState(output)
    actual_step = state._surface_learning
    competing = {}

    def apply_then_competing_expected(payload, progress):
        local = actual_step(payload, progress)
        fresh = local['snapshot']
        current = next(item for item in fresh.ledger if item['review_id'] == row['review_id'])
        event = sp.build_manual_expected_event(current, operation='ENTER_EXPECTED',
            expected_set='ㄇ', rationale='isolated intervening human expected decision')
        service = ReviewSaveService(output)
        service.save_event(row['review_id'], event,
                           expected_manifest=fresh.manifest, expected_db=fresh.db)
        local['snapshot'] = service.load_resume_snapshot()
        competing['db'] = copy.deepcopy(local['snapshot'].db)
        return local

    monkeypatch.setattr(state, '_surface_learning', apply_then_competing_expected)
    with pytest.raises(ReviewOperationError) as error:
        state.dispatch('save_surface_review', request, lambda phase: None, lambda: False)
    assert error.value.commit_state == 'SURFACE_SAVED_EXPECTED_PENDING'
    final = ReviewSaveService(output).load_resume_snapshot()
    assert final.db == competing['db']
    assert final.db['events'][row['review_id']]['expected_set'] == ['ㄇ']
    current = next(item for item in final.ledger if item['review_id'] == row['review_id'])
    assert current['actual'] == 'ㄅㄨˋ' and current['surface_donor_eligible'] is False
    assert not library.GlobalExactGlyphRepository.resolved().path.exists()
