"""Finite owned-journal relocation with real isolated transaction producers.

The small PDF/decoder fixture supplies independent literal actual readings.
Fault injection stops existing writers; no recovery result is mocked successful.
"""
import copy
import os
import shutil
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

import actual_review as ar
import global_exact_glyph_library as library
import global_glyph_promotion as promotion
import manual_surface_actual as surface
import standalone_proofread as sp
from project_write_admission import capture_project_write_admission
from review_save_service import ReviewSaveService
from tests.test_glyph_learning_service import _isolated, _project
from tests.test_manual_surface_actual_v580 import interrupted_surface_transaction
from tests.test_manual_surface_actual_v580 import interrupted_initial_ordinary_journal


def _files(root):
    return {str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob('*') if path.is_file() and path.suffix != '.lock'}


def _profile(tmp_path, monkeypatch):
    root = _isolated(tmp_path, monkeypatch)
    area = root / 'a'
    area.mkdir()
    output = _project(area, monkeypatch)
    actual = sp.project_actual_evidence_root(output)
    ar._write_actual_evidence_csv(actual / ar.OCCURRENCE_OVERRIDE_FILE, ar.OVERRIDE_HEADERS, [])
    sp.enable_surface_actual_project(output, confirmation=True)
    service = ReviewSaveService(output)
    before = service.load_resume_snapshot()
    event = sp.build_manual_expected_event(before.ledger[0], operation='ENTER_EXPECTED', expected_set='ㄎㄢ')
    saved = service.save_event(before.ledger[0]['review_id'], event)
    return root, area, output, saved


def _stage_surface(output, before):
    row = before.ledger[0]
    proof = sp.prepare_surface_actual_correction(output, row['review_id'])['proof']
    sp.stage_surface_actual_correction(output, row['review_id'], 'ㄆ', proof=proof,
        actor='isolated relocation fixture, not human UI acceptance',
        whole_unit_readable=True, target_character_matches=True, reading_from_original=True)


def _retain_publication(output, apply, phase):
    if phase == 'PUBLISHED':
        with patch.object(sp, '_acknowledge_actual_refresh', side_effect=OSError('isolated published stop')):
            with pytest.raises(sp.ManualActualPostApplyError):
                apply(output)
    elif phase == 'ACKNOWLEDGING':
        original = sp.acknowledge_project_refresh
        def after_ack(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError('isolated post-ack stop')
        with patch.object(sp, 'acknowledge_project_refresh', new=after_ack):
            with pytest.raises(sp.ManualActualPostApplyError):
                apply(output)
    elif phase == 'PUBLICATION_PREPARED':
        move = sp._surface_phase_move
        resume = sp._resume_actual_refresh_publication
        interrupted = []
        def after_first_move(source, target, write_phase):
            result = move(source, target, write_phase)
            if not interrupted:
                interrupted.append(True)
                raise OSError('isolated publication prefix stop')
            return result
        def no_hot_resume(*args, **kwargs):
            if interrupted:
                raise OSError('isolated process unavailable for hot rollback')
            return resume(*args, **kwargs)
        with patch.object(sp, '_surface_phase_move', new=after_first_move), \
                patch.object(sp, '_resume_actual_refresh_publication', new=no_hot_resume):
            with pytest.raises(sp.ManualActualPostApplyError):
                apply(output)
        assert interrupted
    else:
        raise AssertionError(phase)
    journal = sp.json_load_strict(output / sp.ACTUAL_REFRESH_PUBLICATION)
    assert journal['phase'] == ('PREPARED' if phase == 'PUBLICATION_PREPARED' else phase)
    return journal


def _relocate(root, area, output):
    """Move the explicitly owned area including PDF and durable sibling stage."""
    destination = root / 'm'
    area.rename(destination)
    selected = destination / 'renamed'
    (destination / output.name).rename(selected)
    # A different project may occupy the historical locator. Never open/write it.
    output.mkdir(parents=True)
    (output / 'foreign-owner.txt').write_bytes(b'unrelated project, keep exactly')
    return selected, _files(area)


def _authority(output):
    path = sp.project_actual_evidence_root(output) / promotion.PROJECT_TRANSACTION_FILE
    if path.exists():
        return path.read_bytes()
    publication = sp.json_load_strict(output / sp.ACTUAL_REFRESH_PUBLICATION)
    return (output.parent / publication['stage'] / 'ack-committed.json').read_bytes()


@pytest.mark.parametrize('variant,relocated', [
    ('journal2-preimages', True), ('journal2-partial', True),
    ('c15-preimage', True), ('c15-afterimage', True),
    ('journal3-initial', True), ('journal3-initial', False),
], ids=['journal2-preimages-moved', 'journal2-partial-moved', 'c15-preimage-moved',
        'c15-afterimage-moved', 'journal3-initial-moved', 'journal3-initial-local'])
def test_none_plan_authority_preserves_finite_recovery_and_new_transaction(tmp_path, monkeypatch, variant, relocated):
    from project_write_admission import SurfaceWritePhase
    from owned_project_relocation import recovery_plan_digest
    root, area, output, before = _profile(tmp_path, monkeypatch)
    original_manifest = sp.json_load_strict(output / '校對工作階段.json')
    actual = sp.project_actual_evidence_root(output)
    if variant.startswith('c15'):
        _stage_surface(output, before)
        journal = interrupted_surface_transaction(output)
        journal['recovery_plan'] = None  # Named finite historical producer state.
        if variant == 'c15-preimage':
            (actual / surface.FILE).write_bytes(promotion._unpacked(journal['files'][surface.FILE]))
    else:
        row = before.ledger[1]
        sp.stage_manual_actual_correction(output, row['review_id'], 'ㄆ',
            checked_occurrence_ids=[row['occurrence_id']], note='isolated None-plan source')
        journal = interrupted_initial_ordinary_journal(output)  # Real initial journal/3 write.
        if variant.startswith('journal2'):
            journal.pop('write_plan')
            journal['schema_version'] = '2.0'
        if variant == 'journal2-partial':
            original = original_manifest['records'][1]
            ar._write_actual_evidence_csv(actual / ar.OCCURRENCE_OVERRIDE_FILE, ar.OVERRIDE_HEADERS, [{
                'pdf_contains': original['pdf_name'], 'pdf_excludes': '',
                'page': str(original['physical_page']), 'target_char': original['char'],
                'stable_key': original['source_record']['穩定注音鍵'],
                'x0': str(original['x0']), 'y0': str(original['y0']), 'actual_reading': 'ㄆ',
                'source': 'finite legacy partial fixture', 'note': '',
            }])
    promotion._write_json(actual / promotion.PROJECT_TRANSACTION_FILE, journal)
    raw = (actual / promotion.PROJECT_TRANSACTION_FILE).read_bytes()
    assert promotion._validated_project_journal(raw)['recovery_plan'] is None
    selected, old = _relocate(root, area, output) if relocated else (output, None)
    actual = sp.project_actual_evidence_root(selected)
    receipt = capture_project_write_admission(selected, recovery=True)
    binding = receipt.relocation
    assert binding.plan_sha is None and _authority(selected) == raw
    # An absent plan is never a publication capability or a COMMITTED plan.
    with pytest.raises(ValueError, match='publication'):
        binding.check_publication({'project': str(selected), 'session_id': original_manifest['session_id'],
            'token': journal['transaction_id'], 'plan_sha': None}, original_manifest)
    committed = copy.deepcopy(journal)
    committed['state'] = 'COMMITTED'
    with pytest.raises(ValueError, match='requires a recovery plan'):
        recovery_plan_digest(committed)
    with pytest.raises(library.GlobalLibraryValidationError):
        promotion._validated_project_journal((library._canonical_json(committed) + '\n').encode('utf-8'))
    if variant == 'journal2-partial':
        unchanged = _files(selected)
        with pytest.raises(ValueError, match='lacks exact afterimages'):
            promotion.recover_pending_project_actual_write(actual)
        assert _files(selected) == unchanged and _authority(selected) == raw
    else:
        retired = []
        finish = SurfaceWritePhase.complete_owned_rollback
        def observe(phase, original_raw):
            assert phase.relocation.plan_sha is None
            finish(phase, original_raw)
            assert phase.relocation is None and phase.retired_relocations[-1].plan_sha is None
            retired.append(original_raw)
        apply = (sp.apply_staged_surface_actual_corrections if variant.startswith('c15')
                 else sp.apply_staged_manual_actual_corrections)
        with patch.object(SurfaceWritePhase, 'complete_owned_rollback', new=observe):
            assert apply(selected)['project_refresh'] == 'SUCCESS'
        assert retired == [raw]  # One public entry: rollback then a real new transaction.
        after = ReviewSaveService(selected).load_resume_snapshot()
        index = 0 if variant.startswith('c15') else 1
        assert after.ledger[index]['actual'] == 'ㄆ' and after.db == before.db
        assert [r['actual'] for i, r in enumerate(after.ledger) if i != index] == [
            r['actual'] for i, r in enumerate(before.ledger) if i != index]
        if variant.startswith('c15'):
            assert not library.GlobalExactGlyphRepository.resolved().path.exists()
            assert apply(selected)['surface_applied_count'] == 0
        else:
            repository = library.GlobalExactGlyphRepository.resolved()
            delivered = repository.load_inspection_snapshot()
            assert len(delivered.records) == 1 and delivered.records[0].direct_source_count == 1
            assert not delivered.records[0].global_reuse_allowed
            apply(selected)
            assert replace(repository.load_inspection_snapshot(), loaded_at=delivered.loaded_at) == delivered
        assert not (actual / promotion.PROJECT_TRANSACTION_FILE).exists()
        assert not (selected / sp.ACTUAL_REFRESH_PUBLICATION).exists()
    if relocated:
        assert _files(area) == old


@pytest.mark.parametrize('phase', ['PREPARED', 'COMMITTED', 'PUBLISHED', 'ACKNOWLEDGING'])
def test_surface_journal2_move_recovers_original_position_and_expected(tmp_path, monkeypatch, phase):
    root, area, output, before = _profile(tmp_path, monkeypatch)
    _stage_surface(output, before)
    if phase == 'PREPARED':
        interrupted_surface_transaction(output)
    elif phase == 'COMMITTED':
        with patch.object(sp, '_surface_projection_refresh', side_effect=OSError('isolated committed stop')):
            with pytest.raises(sp.ManualActualPostApplyError):
                sp.apply_staged_surface_actual_corrections(output)
    else:
        _retain_publication(output, sp.apply_staged_surface_actual_corrections, phase)
    authority = _authority(output)
    journal = promotion._validated_project_journal(authority)
    assert journal['schema_version'] == '2.0' and journal['owner']['operation'] == 'surface-only'
    db = (output / '人工判定資料庫.json').read_bytes()
    selected, old = _relocate(root, area, output)
    receipt = capture_project_write_admission(selected, recovery=True)
    receipt.recheck()
    assert _authority(selected) == authority  # Capture never rebases persisted owner or plan.
    with patch.object(sp, 'deliver_pending_promotion_outbox', side_effect=AssertionError('surface cannot donate')):
        if phase == 'PREPARED':
            assert promotion.recover_pending_project_actual_write(sp.project_actual_evidence_root(selected)) == 'PREPARED'
            pending = surface.parse((sp.project_actual_evidence_root(selected) / surface.FILE).read_bytes())
            assert len(pending['pending']) == 1 and not pending['records']
            sp.apply_staged_surface_actual_corrections(selected)
        else:
            assert sp.recover_committed_actual_project(selected)['project_refresh'] == 'SUCCESS'
        assert sp.apply_staged_surface_actual_corrections(selected)['surface_applied_count'] == 0
    after = ReviewSaveService(selected).load_resume_snapshot()
    assert after.ledger[0]['actual'] == 'ㄆ'
    assert [row['actual'] for row in after.ledger[1:]] == [row['actual'] for row in before.ledger[1:]]
    assert (selected / '人工判定資料庫.json').read_bytes() == db
    assert after.db == before.db
    assert not library.GlobalExactGlyphRepository.resolved().path.exists()
    assert _files(area) == old
    assert not (sp.project_actual_evidence_root(selected) / promotion.PROJECT_TRANSACTION_FILE).exists()
    assert not (selected / sp.ACTUAL_REFRESH_PUBLICATION).exists()


def _retain_direct(output, before, phase):
    row = before.ledger[1]
    sp.stage_manual_actual_correction(output, row['review_id'], 'ㄆ',
        checked_occurrence_ids=[row['occurrence_id']], note='isolated relocation direct input')
    if phase == 'COMMITTED':
        with patch.object(sp, 'deliver_pending_promotion_outbox', side_effect=OSError('isolated delivery stop')):
            with pytest.raises(sp.ManualActualPostApplyError):
                sp.apply_staged_manual_actual_corrections(output)
    elif phase == 'PREPARED':
        actual = sp.project_actual_evidence_root(output)
        atomic = promotion._atomic_bytes
        snapshots = []
        def after_first_csv(path, raw, **kwargs):
            result = atomic(path, raw, **kwargs)
            if Path(path).name == ar.OCCURRENCE_OVERRIDE_FILE and not snapshots:
                journal = promotion._validated_project_journal((actual / promotion.PROJECT_TRANSACTION_FILE).read_bytes())
                assert journal['schema_version'] == '3.0' and journal['state'] == 'PREPARED'
                assert journal['write_plan']['afterimages'] is not None
                snapshots.append(_files(output))
                raise OSError('isolated transaction prefix stop')
            return result
        with patch.object(promotion, '_atomic_bytes', new=after_first_csv):
            with pytest.raises(OSError, match='transaction prefix stop'):
                sp.apply_staged_manual_actual_corrections(output)
        assert snapshots and not (actual / promotion.PROJECT_TRANSACTION_FILE).exists()
        # Exact process-loss bytes captured before the real hot rollback.
        for name, raw in snapshots[0].items():
            path = output / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
    else:
        _retain_publication(output, sp.apply_staged_manual_actual_corrections, phase)


@pytest.mark.parametrize('phase', ['PREPARED', 'COMMITTED', 'PUBLICATION_PREPARED', 'PUBLISHED', 'ACKNOWLEDGING'])
def test_direct_journal3_move_recovers_same_intent_without_recount(tmp_path, monkeypatch, phase):
    root, area, output, before = _profile(tmp_path, monkeypatch)
    _retain_direct(output, before, phase)
    authority = _authority(output)
    journal = promotion._validated_project_journal(authority)
    assert journal['schema_version'] == '3.0'
    db = (output / '人工判定資料庫.json').read_bytes()
    selected, old = _relocate(root, area, output)
    receipt = capture_project_write_admission(selected, recovery=True)
    receipt.recheck()
    assert _authority(selected) == authority
    if phase == 'PREPARED':
        actual = sp.project_actual_evidence_root(selected)
        assert promotion.recover_pending_project_actual_write(actual) == 'PREPARED'
        for name, packed in journal['files'].items():
            path = actual / name
            assert (path.read_bytes() if path.exists() else None) == promotion._unpacked(packed)
        sp.apply_staged_manual_actual_corrections(selected)
    else:
        assert sp.recover_committed_actual_project(selected)['project_refresh'] == 'SUCCESS'
    repository = library.GlobalExactGlyphRepository.resolved()
    first = repository.load_inspection_snapshot()
    assert len(first.records) == 1 and first.records[0].direct_source_count == 1
    assert not first.records[0].global_reuse_allowed
    sp.recover_committed_actual_project(selected)
    assert replace(repository.load_inspection_snapshot(), loaded_at=first.loaded_at) == first
    after = ReviewSaveService(selected).load_resume_snapshot()
    assert after.ledger[1]['actual'] == 'ㄆ'
    # The existing ordinary refresh serializes JSON as LF; expected decisions
    # must remain byte-identical apart from that known newline serialization.
    assert (selected / '人工判定資料庫.json').read_bytes().replace(b'\r\n', b'\n') == db.replace(b'\r\n', b'\n')
    assert after.db == before.db
    assert [row['expected_set'] for row in after.ledger] == [row['expected_set'] for row in before.ledger]
    assert _files(area) == old
    assert not (sp.project_actual_evidence_root(selected) / promotion.PROJECT_TRANSACTION_FILE).exists()
    assert not (selected / sp.ACTUAL_REFRESH_PUBLICATION).exists()


def test_moved_owned_journal_rejects_changed_inputs_and_link_without_writes(tmp_path, monkeypatch):
    root, area, output, before = _profile(tmp_path, monkeypatch)
    _retain_direct(output, before, 'COMMITTED')
    selected, old = _relocate(root, area, output)
    manifest_path = selected / '校對工作階段.json'
    actual = sp.project_actual_evidence_root(selected)
    journal_path = actual / promotion.PROJECT_TRANSACTION_FILE
    manifest = sp.json_load_strict(manifest_path)
    workbook = selected / sp.descriptor(manifest['pdfs'][0], 'actual')
    source = selected.parent / 'one.pdf'
    for path in (source, workbook, actual / surface.FILE):
        original = path.read_bytes()
        path.write_bytes(original + b'foreign bytes')
        frozen = _files(selected)
        with pytest.raises((ValueError, OSError)):
            capture_project_write_admission(selected, recovery=True)
        assert _files(selected) == frozen and _files(area) == old
        path.write_bytes(original)
    original = manifest_path.read_bytes()
    foreign = copy.deepcopy(manifest)
    foreign['session_id'] += '-foreign'
    sp.json_save(manifest_path, sp.seal_manifest(foreign))
    frozen = _files(selected)
    with pytest.raises(ValueError):
        capture_project_write_admission(selected, recovery=True)
    assert _files(selected) == frozen
    manifest_path.write_bytes(original)
    original_journal = journal_path.read_bytes()
    journal = promotion._validated_project_journal(original_journal)
    journal['write_plan']['dependencies'][str(root / 'unknown-outside.bin')] = 'a' * 64
    promotion._write_json(journal_path, journal)
    frozen = _files(selected)
    with pytest.raises(ValueError):
        capture_project_write_admission(selected, recovery=True)
    assert _files(selected) == frozen
    journal_path.write_bytes(original_journal)
    # A hard link preserves bytes/hash but must not authorize an aliased artifact.
    alias = root / 'workbook-alias.xlsx'
    os.link(workbook, alias)
    try:
        frozen = _files(selected)
        with pytest.raises(ValueError):
            capture_project_write_admission(selected, recovery=True)
        assert _files(selected) == frozen and _files(area) == old
    finally:
        alias.unlink()
    capture_project_write_admission(selected, recovery=True).recheck()


def test_moved_publication_missing_registered_backup_refuses_without_old_lookup(tmp_path, monkeypatch):
    root, area, output, before = _profile(tmp_path, monkeypatch)
    _stage_surface(output, before)
    publication = _retain_publication(output, sp.apply_staged_surface_actual_corrections, 'PUBLISHED')
    retained_stage = area / publication['stage']
    backup = _files(retained_stage)
    selected = root / 'b' / 'renamed'
    selected.parent.mkdir()
    output.rename(selected)  # Intentionally omit the registered sibling backup.
    frozen = _files(selected)
    with pytest.raises((ValueError, OSError)):
        sp.recover_committed_actual_project(selected)
    assert _files(selected) == frozen and _files(retained_stage) == backup and not output.exists()
    # A same-name stage is not authority: every retained backup remains sealed.
    relocated_stage = selected.parent / publication['stage']
    shutil.copytree(retained_stage, relocated_stage)
    previous = next((relocated_stage / 'previous').iterdir())
    previous.write_bytes(previous.read_bytes() + b'foreign backup')
    corrupt_backup = _files(relocated_stage)
    with pytest.raises((ValueError, OSError)):
        sp.recover_committed_actual_project(selected)
    assert _files(selected) == frozen and _files(relocated_stage) == corrupt_backup
    assert _files(retained_stage) == backup and not output.exists()


def test_owned_recovery_interrupted_again_then_moved_back_keeps_original_vote(tmp_path, monkeypatch):
    root, area, output, before = _profile(tmp_path, monkeypatch)
    _retain_direct(output, before, 'COMMITTED')
    authority = _authority(output)
    original = promotion._validated_project_journal(authority)
    renamed = area / 'renamed'
    output.rename(renamed)
    capture_project_write_admission(renamed, recovery=True).recheck()
    assert _authority(renamed) == authority and not output.exists()
    # Recovery delivers the original intent and publishes at the selected root,
    # then is interrupted before acknowledgement. No new user action occurs.
    with patch.object(sp, '_acknowledge_actual_refresh', side_effect=OSError('isolated recovery interrupted again')):
        with pytest.raises(sp.ManualActualPostApplyError):
            sp.recover_committed_actual_project(renamed)
    repository = library.GlobalExactGlyphRepository.resolved()
    delivered = repository.load_inspection_snapshot()
    assert delivered.records[0].direct_source_count == 1 and not delivered.records[0].global_reuse_allowed
    retained = promotion._validated_project_journal(_authority(renamed))
    assert retained['owner'] == original['owner']
    assert retained['transaction_id'] == original['transaction_id']
    assert retained['recovery_plan'] == original['recovery_plan']
    assert retained['files'] == original['files']
    assert retained['write_plan']['dependencies'] == original['write_plan']['dependencies']
    publication = sp.json_load_strict(renamed / sp.ACTUAL_REFRESH_PUBLICATION)
    assert publication['phase'] == 'PUBLISHED' and publication['token'] == original['transaction_id']
    # The publication's producer root can differ from original transaction owner.
    # Moving back must verify both authorities without rewriting either locator.
    renamed.rename(output)
    frozen_publication = (output / sp.ACTUAL_REFRESH_PUBLICATION).read_bytes()
    capture_project_write_admission(output, recovery=True).recheck()
    assert (output / sp.ACTUAL_REFRESH_PUBLICATION).read_bytes() == frozen_publication
    assert sp.recover_committed_actual_project(output)['project_refresh'] == 'SUCCESS'
    sp.recover_committed_actual_project(output)
    assert replace(repository.load_inspection_snapshot(), loaded_at=delivered.loaded_at) == delivered
    after = ReviewSaveService(output).load_resume_snapshot()
    assert after.ledger[1]['actual'] == 'ㄆ' and after.db == before.db
    assert not renamed.exists() and not (output / sp.ACTUAL_REFRESH_PUBLICATION).exists()


@pytest.mark.parametrize('kind', ['direct', 'surface'])
def test_one_apply_entry_retires_prepared_authority_before_new_commit(tmp_path, monkeypatch, kind):
    from project_write_admission import SurfaceWritePhase
    root, area, output, before = _profile(tmp_path, monkeypatch)
    if kind == 'direct':
        _retain_direct(output, before, 'PREPARED')
    else:
        _stage_surface(output, before)
        interrupted_surface_transaction(output)
    original = promotion._validated_project_journal(_authority(output))
    selected, old = _relocate(root, area, output)
    retired, written = [], []
    finish, write = SurfaceWritePhase.complete_owned_rollback, promotion._write_json
    def observe_handoff(phase, raw):
        binding = phase.relocation
        assert binding.transaction_id == original['transaction_id']
        finish(phase, raw)
        assert phase.relocation is None and phase.retired_relocations[-1] is binding
        phase.recheck()
        retired.append(binding)
    def observe_journal(path, value, **kwargs):
        write(path, value, **kwargs)
        if Path(path).name == promotion.PROJECT_TRANSACTION_FILE:
            written.append(copy.deepcopy(value))
    apply = sp.apply_staged_manual_actual_corrections if kind == 'direct' else sp.apply_staged_surface_actual_corrections
    with patch.object(SurfaceWritePhase, 'complete_owned_rollback', new=observe_handoff), \
            patch.object(promotion, '_write_json', new=observe_journal):
        result = apply(selected)  # No separate rollback call before this public entry.
    assert result['project_refresh'] == 'SUCCESS' and len(retired) == 1
    assert written and all(item['transaction_id'] != original['transaction_id'] for item in written)
    assert all(item['owner']['project'] == str(selected) for item in written)
    after = ReviewSaveService(selected).load_resume_snapshot()
    changed = 1 if kind == 'direct' else 0
    assert after.ledger[changed]['actual'] == 'ㄆ' and after.db == before.db
    assert [row['actual'] for i, row in enumerate(after.ledger) if i != changed] == [
        row['actual'] for i, row in enumerate(before.ledger) if i != changed]
    if kind == 'direct':
        repository = library.GlobalExactGlyphRepository.resolved()
        first = repository.load_inspection_snapshot()
        assert len(first.records) == 1 and first.records[0].direct_source_count == 1
        assert not first.records[0].global_reuse_allowed
        apply(selected)
        assert replace(repository.load_inspection_snapshot(), loaded_at=first.loaded_at) == first
    else:
        assert not library.GlobalExactGlyphRepository.resolved().path.exists()
        assert apply(selected)['surface_applied_count'] == 0
    assert _files(area) == old
    assert not (sp.project_actual_evidence_root(selected) / promotion.PROJECT_TRANSACTION_FILE).exists()
    assert not (selected / sp.ACTUAL_REFRESH_PUBLICATION).exists()


@pytest.mark.parametrize('kind', ['direct', 'surface'])
def test_rollback_handoff_rejects_foreign_bytes_before_retiring_authority(tmp_path, monkeypatch, kind):
    from project_write_admission import SurfaceWritePhase
    root, area, output, before = _profile(tmp_path, monkeypatch)
    if kind == 'direct':
        _retain_direct(output, before, 'PREPARED')
    else:
        _stage_surface(output, before)
        interrupted_surface_transaction(output)
    selected, old = _relocate(root, area, output)
    original = promotion._validated_project_journal(_authority(selected))
    db_raw = (selected / '人工判定資料庫.json').read_bytes()
    manifest = sp.json_load_strict(selected / '校對工作階段.json')
    target = (selected / sp.descriptor(manifest['pdfs'][0], 'actual') if kind == 'direct'
              else selected / sp.ACTUAL_REFRESH_PUBLICATION)
    foreign = b'foreign boundary bytes must not become a new baseline'
    entered = []
    finish = SurfaceWritePhase.complete_owned_rollback
    def inject_at_handoff(phase, raw):
        binding = phase.relocation
        assert binding.transaction_id == original['transaction_id']
        target.write_bytes(foreign)
        entered.append(phase)
        with pytest.raises(ValueError, match='source changed before write'):
            finish(phase, raw)
        assert phase.relocation is binding and not phase.retired_relocations
        raise ValueError('isolated handoff rejected foreign bytes')
    apply = sp.apply_staged_manual_actual_corrections if kind == 'direct' else sp.apply_staged_surface_actual_corrections
    with patch.object(SurfaceWritePhase, 'complete_owned_rollback', new=inject_at_handoff):
        with pytest.raises(ValueError, match='handoff rejected foreign bytes'):
            apply(selected)
    assert entered and target.read_bytes() == foreign and _files(area) == old
    actual = sp.project_actual_evidence_root(selected)
    assert not (actual / promotion.PROJECT_TRANSACTION_FILE).exists()
    for name, packed in original['files'].items():
        path = actual / name
        assert (path.read_bytes() if path.exists() else None) == promotion._unpacked(packed)
    assert (selected / '人工判定資料庫.json').read_bytes() == db_raw
    assert not library.GlobalExactGlyphRepository.resolved().path.exists()
