"""Bounded surface preparation and owned cancellation, without native windows."""
import copy
import threading
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

import actual_review as ar
import glyph_learning_service as learning
import global_exact_glyph_library as library
import review_gui as gui
import standalone_proofread as sp
from review_operation_worker import ReviewOperationState, ReviewProcessService, ReviewOperationError
from review_save_service import ReviewSaveService
from tests.no_window_review_support import Surface
from tests.test_overlay_confirm_usage_20261103 import _annotated_project
from tests.test_review_admission_recovery import admission_app
from tests.test_staged_review_navigation_v580 import create_staged_navigation_fixture


def _files(root):
    return {str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob('*') if path.is_file()}


def _prepare_request(output):
    service = ReviewSaveService(output)
    snapshot = service.load_resume_snapshot()
    return service, snapshot, dict(review_id=snapshot.ledger[0]['review_id'],
        expected_manifest=snapshot.manifest, expected_db=snapshot.db)


@pytest.mark.parametrize('extra', [0, 120], ids=['original-roster', 'unrelated-roster'])
def test_surface_prepare_returns_complete_view_without_learning_scan(tmp_path, monkeypatch, extra):
    output, _ = _annotated_project(tmp_path, monkeypatch, overpaint=True)
    service, snapshot, request = _prepare_request(output)
    # Extra unrelated view rows exercise dispatch scaling only. Admission and
    # proof still execute against the genuine sealed PDF/project. No extra row
    # is promoted to persisted evidence or used to supply the target's proof.
    if extra:
        snapshot = replace(snapshot, ledger=list(snapshot.ledger) + [dict(snapshot.ledger[-1],
            review_id=f'unrelated-review-{index}', occurrence_id=f'unrelated-occurrence-{index}')
            for index in range(extra)])
    admitted = service.load_resume_snapshot
    def load(**kwargs):
        current = admitted(**kwargs)
        return replace(current, ledger=snapshot.ledger)
    monkeypatch.setattr(service, 'load_resume_snapshot', load)
    collector = Mock(side_effect=AssertionError('surface preparation must not scan learning candidates'))
    monkeypatch.setattr(learning, 'collect_learning_candidates', collector)
    safety = Mock(wraps=ar.actual_visual_source_safety)
    proof = Mock(wraps=sp.prepare_surface_actual_correction)
    monkeypatch.setattr(ar, 'actual_visual_source_safety', safety)
    monkeypatch.setattr(sp, 'prepare_surface_actual_correction', proof)
    before = _files(output)
    phases = []
    value = ReviewOperationState(output, service=service).dispatch(
        'prepare_actual', request, phases.append, lambda: False)
    assert set(value) == {'group', 'summary', 'ledger', 'surface', 'learning_group'}
    assert value['ledger'] == service._conflicts.review_view(snapshot.db, snapshot.ledger)
    assert len(value['ledger']) == len(snapshot.ledger)
    assert value['surface']['proof']['source']['occurrence_id'] == snapshot.ledger[0]['occurrence_id']
    assert value['surface']['proof']['donor_eligible'] is False
    assert value['learning_group'] is None
    collector.assert_not_called()
    safety.assert_called_once()
    proof.assert_called_once_with(output, request['review_id'])
    assert phases[0] == '正在核對專案快照與來源依賴'
    assert phases[-1] == '正在核對本位置原頁表面 actual 證據'
    assert _files(output) == before
    assert not library.GlobalExactGlyphRepository.resolved().path.exists()


def test_readable_prepare_keeps_real_learning_path(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'user'))
    create_staged_navigation_fixture(tmp_path)
    sp.ensure_user_evidence_files(sp.project_actual_evidence_root(tmp_path))
    service, snapshot, request = _prepare_request(tmp_path)
    collector = Mock(wraps=learning.collect_learning_candidates)
    monkeypatch.setattr(learning, 'collect_learning_candidates', collector)
    result = ReviewOperationState(tmp_path, service=service).dispatch(
        'prepare_actual', request, lambda phase: None, lambda: False)
    collector.assert_called_once()
    assert result['surface'] is None
    assert result['ledger'] == service._conflicts.review_view(snapshot.db, snapshot.ledger)
    assert result['group']['members']


@pytest.mark.parametrize('mutation', ['missing', 'changed', 'displayed-db', 'review-id'])
def test_surface_prepare_refuses_stale_source_and_identity_before_collector(tmp_path, monkeypatch, mutation):
    output, _ = _annotated_project(tmp_path, monkeypatch, overpaint=True)
    service, snapshot, request = _prepare_request(output)
    pdf = Path(snapshot.ledger[0]['pdf'])
    if mutation == 'missing':
        pdf.unlink()
    elif mutation == 'changed':
        pdf.write_bytes(pdf.read_bytes() + b'\n% changed before preparation\n')
    elif mutation == 'displayed-db':
        request['expected_db'] = copy.deepcopy(snapshot.db)
        request['expected_db']['events']['foreign'] = {'action': 'foreign stale view'}
    else:
        request['review_id'] = 'foreign-review-id'
    collector = Mock(side_effect=AssertionError('rejected preparation cannot collect'))
    monkeypatch.setattr(learning, 'collect_learning_candidates', collector)
    before = _files(output)
    with pytest.raises((ReviewOperationError, ValueError, KeyError)):
        ReviewOperationState(output, service=service).dispatch(
            'prepare_actual', request, lambda phase: None, lambda: False)
    collector.assert_not_called()
    assert _files(output) == before


def test_cancelled_staging_error_stops_before_source_proof_or_learning(tmp_path, monkeypatch):
    output, _ = _annotated_project(tmp_path, monkeypatch, overpaint=True)
    service, _, request = _prepare_request(output)
    cancelled = threading.Event()
    validate = service.validate_resume_staging
    summaries = []
    def interrupt(snapshot, **kwargs):
        cancelled.set()
        summary = validate(snapshot, **kwargs)
        summaries.append(summary)
        return summary
    monkeypatch.setattr(service, 'validate_resume_staging', interrupt)
    proof = Mock(side_effect=AssertionError('cancelled staging must stop'))
    monkeypatch.setattr(sp, 'prepare_surface_actual_correction', proof)
    before = _files(output)
    with pytest.raises(InterruptedError, match='已取消'):
        ReviewOperationState(output, service=service).dispatch(
            'prepare_actual', request, lambda phase: None, cancelled.is_set)
    assert summaries[0]['staging_error']
    proof.assert_not_called()
    assert _files(output) == before


def test_real_client_cancel_drains_terminal_then_retries_and_writer_ignores_cancel(tmp_path, monkeypatch):
    output, _ = _annotated_project(tmp_path, monkeypatch, overpaint=True)
    _, _, request = _prepare_request(output)
    service, cancelled = ReviewProcessService(output), threading.Event()
    before = _files(output)
    def progress(phase):
        cancelled.set()
    try:
        with pytest.raises(InterruptedError, match='已取消'):
            service.invoke('prepare_actual', request, progress=progress, cancelled=cancelled.is_set)
        assert _files(output) == before
        cancelled.clear()
        prepared = service.invoke('prepare_actual', request, cancelled=cancelled.is_set)
        assert prepared['surface'] is not None and prepared['learning_group'] is None
        pid = service.worker_pid
        row = prepared['ledger'][0]
        event = sp.build_manual_expected_event(row, operation='ENTER_EXPECTED', expected_set='ㄇ')
        # The cancellation parameter cannot cancel a real writer.
        saved = service.invoke('save', dict(request, event=event), cancelled=lambda: True)
        assert saved.db['events'][request['review_id']]['manual_expected_decision']['expected_set'] == ['ㄇ']
        assert service.worker_pid == pid
    finally:
        service.close()
    assert not service._process.is_alive()
    assert not library.GlobalExactGlyphRepository.resolved().path.exists()


@pytest.mark.parametrize('ending', ['cancel', 'close', 'failure', 'stale', 'new-owner', 'dispose'])
def test_prepare_controller_retains_terminal_ownership_and_never_publishes_old_result(tmp_path, monkeypatch, ending):
    app = admission_app(tmp_path)
    app.cancel_actual_prepare_button = Surface(app.root)
    app.records = [{'review_id': 'viewed', 'occurrence_id': 'viewed-occurrence'}]
    entered, release = threading.Event(), threading.Event()
    calls, published = [], []
    class DeferredPreparation:
        def invoke(self, operation, payload, *, progress, cancelled):
            assert operation == 'prepare_actual'
            calls.append((payload['review_id'], cancelled))
            entered.set()
            assert release.wait(5)
            if ending == 'failure':
                raise ReviewOperationError('injected preparation failure', commit_state='NOT_WRITTEN')
            return {'summary': {}, 'ledger': [], 'surface': {'unused': True}}
    app._save_service = DeferredPreparation()
    monkeypatch.setattr(app, '_save_surface_review', lambda *args: published.append(args))
    errors = Mock()
    monkeypatch.setattr(gui.messagebox, 'showerror', errors)
    app.correct_actual()
    assert entered.wait(5)
    old_request = app._operation_request
    try:
        if ending == 'close':
            app.close()
            assert app.root.winfo_exists()
        elif ending == 'cancel':
            assert app._cancel_actual_preparation()
        elif ending == 'stale':
            app._project_generation += 1
        elif ending == 'new-owner':
            newer = {'new-owner': True}
            app._operation_request = newer
        elif ending == 'dispose':
            app.root.destroy()
        if ending in {'cancel', 'close'}:
            assert calls[0][1]() and app._operation_in_progress
            app.correct_actual()
            assert len(calls) == 1
        release.set()
        app._operation_worker.join(5)
        assert not app._operation_worker.is_alive()
        app.root.update()
        assert not published
        if ending == 'new-owner':
            assert app._operation_request is newer and app._operation_in_progress
        else:
            assert not app._operation_in_progress
        if ending in {'cancel', 'failure', 'stale'}:
            assert app.primary.cget('state') == 'normal'
        if ending == 'close':
            assert not app.root.winfo_exists()
        if ending == 'failure':
            errors.assert_called_once()
        else:
            errors.assert_not_called()
        assert not list(tmp_path.rglob('*'))
    finally:
        release.set()
        app._operation_worker.join(5)


def test_writer_controller_has_no_prepare_cancellation(tmp_path):
    app = admission_app(tmp_path)
    entered, release = threading.Event(), threading.Event()
    published = []
    def save(progress):
        entered.set()
        assert release.wait(5)
        return 'writer terminal'
    try:
        app._start_owned_operation('保存判定', save, published.append)
        assert entered.wait(5)
        assert not app._cancel_actual_preparation()
        app.close()
        assert app.root.winfo_exists() and app._operation_in_progress
        release.set()
        app._operation_worker.join(5)
        app.root.update()
        assert published == ['writer terminal'] and not app._operation_in_progress
    finally:
        release.set()
        app._operation_worker.join(5)
