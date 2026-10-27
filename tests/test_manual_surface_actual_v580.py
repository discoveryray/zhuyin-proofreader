"""Non-window service/transaction tests using genuine isolated PDF glyphs."""
from __future__ import annotations

import copy
import hashlib
import json
import inspect
import shutil
from pathlib import Path
from contextlib import contextmanager
from unittest.mock import patch

import pytest
import actual_review as ar
import global_glyph_promotion as promotion
import manual_surface_actual as surface
import standalone_proofread as sp
from project_write_admission import capture_project_write_admission
from review_save_service import ReviewSaveService
from tests.test_staged_review_navigation_v580 import create_staged_navigation_fixture
from tests.test_excel_text_storage import isolated_pipeline


def files(root):
    return {str(path.relative_to(root)):path.read_bytes() for path in root.rglob('*') if path.is_file()}


@pytest.fixture
def project(tmp_path):
    output=tmp_path/'project'
    output.mkdir()
    manifest=create_staged_navigation_fixture(output,same_exact_peers=True)
    ar.ensure_user_evidence_files(sp.project_actual_evidence_root(output))
    ar._write_actual_evidence_csv(sp.project_actual_evidence_root(output)/ar.OCCURRENCE_OVERRIDE_FILE,ar.OVERRIDE_HEADERS,[])
    sp.enable_surface_actual_project(output,confirmation=True)
    return output,manifest


def stage(output, original, reading='ㄅㄛˊ'):
    rid=original['records'][0]['review_id']
    prepared=sp.prepare_surface_actual_correction(output,rid)
    return sp.stage_surface_actual_correction(output,rid,reading,proof=prepared['proof'],
        actor='pytest isolated service input; no human GUI acceptance',whole_unit_readable=True,
        target_character_matches=True,reading_from_original=True)


def test_pending_expected_save_apply_reopen_refresh_and_no_learning(project):
    output, original=project
    root=sp.project_actual_evidence_root(output)
    service=ReviewSaveService(output)
    first=service.load_resume_snapshot()
    anchor=service.evidence_anchor
    actual_files={name:(root/name).read_bytes() if (root/name).exists() else None
        for name in promotion.PROJECT_FILES}
    stage(output,original)
    pending_snapshot=service.load_resume_snapshot()
    assert service.evidence_anchor == anchor
    summary=service.validate_resume_staging(pending_snapshot)
    assert summary['surface_pending_count']==1
    assert sp.manual_actual_staging_summary(output,ledger=pending_snapshot.ledger)['surface_pending_count']==1
    assert sp.manual_actual_staging_summary(output)['surface_pending_count']==1
    event=sp.build_manual_expected_event(pending_snapshot.ledger[0],operation='ENTER_EXPECTED',expected_set='ㄎㄢ')
    saved=service.save_event(original['records'][0]['review_id'],event)
    db_bytes=(output/'人工判定資料庫.json').read_bytes()
    prior=copy.deepcopy(saved.ledger)
    with patch.object(sp,'deliver_pending_promotion_outbox',side_effect=AssertionError('surface must not deliver')):
        result=sp.apply_staged_surface_actual_corrections(output)
        sp.refresh_actual_project(output)
    assert result['surface_applied_count']==1
    assert result['affected_pdf_names']==[original['pdfs'][0]['pdf_name']]
    assert (output/'人工判定資料庫.json').read_bytes()==db_bytes
    reopened=ReviewSaveService(output).load_resume_snapshot()
    assert reopened.manifest['manual_surface_projection_refresh']['origin_manifest_sha256']==original['manifest_integrity_sha256']
    assert reopened.ledger[0]['actual']=='ㄅㄛˊ'
    assert reopened.ledger[0]['automatic_actual_before_surface']==prior[0]['actual']
    assert reopened.ledger[0]['surface_donor_eligible'] is False
    expected=lambda row:{key:value for key,value in row.items() if key.startswith('expected')}
    assert [expected(row) for row in reopened.ledger]==[expected(row) for row in prior]
    assert reopened.ledger[1:]==prior[1:]
    status=sp.json_load_strict(output/'pipeline_status.json')
    assert status['completion_gate']['complete'] is False
    assert status['completion_gate']['hard_gates']['surface_projection_raw_decoder_revalidated'] is False
    for name,raw in actual_files.items():
        assert ((root/name).read_bytes() if (root/name).exists() else None)==raw
    before=files(output)
    assert sp.apply_staged_surface_actual_corrections(output)['surface_applied_count']==0
    assert files(output)==before


@pytest.mark.parametrize('mutation',['missing','corrupt','unknown','session','identity','render','applied','db','override','marker','origin'])
def test_bad_required_surface_rejects_before_lock_or_any_project_write(project,mutation):
    output, original=project
    root=sp.project_actual_evidence_root(output)
    stage(output,original)
    path=root/surface.FILE
    if mutation in ('marker','origin'):
        manifest=sp.json_load_strict(output/'校對工作階段.json')
        manifest.pop(surface.MARKER if mutation=='marker' else 'manual_surface_projection_refresh')
        sp.json_save(output/'校對工作階段.json',sp.seal_manifest(manifest))
    elif mutation=='db':
        (output/'人工判定資料庫.json').unlink()
    elif mutation=='override':
        (root/ar.OCCURRENCE_OVERRIDE_FILE).unlink()
    elif mutation=='missing':
        path.unlink()
    elif mutation=='corrupt':
        path.write_bytes(b'{')
    elif mutation=='unknown':
        document=surface.parse(path.read_bytes());document['schema']='manual-surface-actual/999'
        path.write_text(json.dumps(document),encoding='utf-8')
    elif mutation=='session':
        document=surface.parse(path.read_bytes());document['session_id']='foreign-session'
        path.write_text(json.dumps(document),encoding='utf-8')
    elif mutation in ('identity','render'):
        document=surface.parse(path.read_bytes());record=document['pending'][0]
        if mutation=='identity':
            record['proof']['source']['occurrence_id']='occ_'+'f'*64
        else:
            record['proof']['render']['page_png']='bad'
        record['proof']['proof_sha256']=surface.digest({k:v for k,v in record['proof'].items() if k!='proof_sha256'})
        record['receipt_sha256']=surface.digest({k:v for k,v in record.items() if k!='receipt_sha256'})
        path.write_text(json.dumps(document),encoding='utf-8')
    else:
        document=surface.parse(path.read_bytes());document['records']=document['pending'];document['pending']=[]
        path.write_bytes(surface.bytes_for(document))
    (root/'.global_exact_glyph_delivery.lock').unlink(missing_ok=True)
    before=files(output)
    writers=[lambda:ReviewSaveService(output).load_resume_snapshot(),
        lambda:sp.initialize_project_actual_evidence(output),lambda:sp.refresh_actual_project(output),
        lambda:sp.apply_staged_surface_actual_corrections(output),lambda:sp.recover_committed_actual_project(output),
        lambda:sp.regenerate_report(output)]
    for writer in writers:
        with pytest.raises((ValueError,TypeError,KeyError)):
            writer()
        assert files(output)==before


def test_syntax_valid_drift_between_admission_and_lock_rejects(project):
    output, original=project
    root=sp.project_actual_evidence_root(output)
    stage(output,original)
    path=root/surface.FILE
    (root/'.global_exact_glyph_delivery.lock').unlink(missing_ok=True)
    original_lock=promotion.project_delivery_lock
    changed=[]
    @contextmanager
    def drift_lock(actual_root,**kwargs):
        if not changed:
            document=surface.parse(path.read_bytes());document['revision']+=1
            path.write_bytes(surface.bytes_for(document));changed.append(files(output))
        with original_lock(actual_root,**kwargs):
            yield
    with patch.object(promotion,'project_delivery_lock',drift_lock):
        with pytest.raises(ValueError,match='changed before write'):
            sp.apply_staged_surface_actual_corrections(output)
    assert files(output)==changed[0]


@pytest.mark.parametrize('mutation',['manifest','csv'])
def test_late_surface_snapshot_drift_preserves_external_bytes(project,mutation):
    output,original=project
    root=sp.project_actual_evidence_root(output)
    stage(output,original)
    sp.apply_staged_surface_actual_corrections(output)
    document=surface.load(root,sp.json_load_strict(output/'校對工作階段.json'))
    snapshot=sp._refresh_source_snapshot
    changed=[]
    baseline=files(output)
    expected_copy=output.parent/(mutation+'-external-before')
    shutil.copytree(output,expected_copy)
    def late_snapshot(project_dir):
        if inspect.currentframe().f_back.f_code.co_name=='_surface_projection_refresh' and not changed:
            if mutation=='manifest':
                path=output/'校對工作階段.json'
                manifest=sp.json_load_strict(path)
                manifest['external_capture_window_audit']='valid independently sealed update'
                sp.json_save(path,sp.seal_manifest(manifest))
                sp.validate_manifest_integrity(sp.json_load_strict(path))
            else:
                path=root/ar.OCCURRENCE_OVERRIDE_FILE
                path.write_bytes(path.read_bytes()+b'\n')
                assert ar.validate_dynamic_actual_evidence(root,read_only=True)['ok']
            expected=dict(baseline)
            expected[str(path.relative_to(output))]=path.read_bytes()
            changed.append(expected)
            (expected_copy/path.relative_to(output)).write_bytes(path.read_bytes())
        return snapshot(project_dir)
    try:
        with patch.object(sp,'_refresh_source_snapshot',new=late_snapshot):
            with pytest.raises(ValueError,match='changed before write'):
                sp._surface_projection_refresh(output,document)
    finally:
        shutil.copytree(output,output.parent/(mutation+'-actual-after'))
    assert changed and files(output)==changed[0]
    assert not list(output.parent.glob('.ar-*'))


def test_surface_publisher_rechecks_original_receipt_before_backups(project):
    output,original=project
    root=sp.project_actual_evidence_root(output)
    stage(output,original)
    sp.apply_staged_surface_actual_corrections(output)
    document=surface.load(root,sp.json_load_strict(output/'校對工作階段.json'))
    before=files(output)
    publish=sp._publish_actual_refresh
    observed=[]
    def late_publish(project_dir,stage_dir,manifest,**kwargs):
        assert not (stage_dir/'previous').exists()
        path=root/surface.FILE
        pending=surface.parse(path.read_bytes());pending['revision']+=1
        path.write_bytes(surface.bytes_for(pending))
        before[str(path.relative_to(output))]=path.read_bytes()
        observed.append(True)
        try:
            return publish(project_dir,stage_dir,manifest,**kwargs)
        finally:
            assert not (stage_dir/'previous').exists()
            assert not (output/sp.ACTUAL_REFRESH_PUBLICATION).exists()
    with patch.object(sp,'_publish_actual_refresh',new=late_publish):
        with pytest.raises(ValueError,match='changed before write'):
            sp._surface_projection_refresh(output,document)
    assert observed and files(output)==before
    assert not list(output.parent.glob('.ar-*'))


@pytest.mark.parametrize('external_drift',[False,True])
def test_prepared_surface_publication_recaptures_only_owned_rollback(project,external_drift):
    output,original=project
    root=sp.project_actual_evidence_root(output)
    stage(output,original)
    db=(output/'人工判定資料庫.json').read_bytes()
    baseline_ledger=sp.materialize_ledger(original,sp.json_parse_strict(db,output/'人工判定資料庫.json'))
    validate=sp.validate_output_artifact_hashes
    resume=sp._resume_actual_refresh_publication
    def interrupt_validation(*args,**kwargs):
        if (output/sp.ACTUAL_REFRESH_PUBLICATION).exists():
            raise OSError('isolated interruption after PREPARED publication')
        return validate(*args,**kwargs)
    def preserve_prepared(project_dir,**kwargs):
        if (output/sp.ACTUAL_REFRESH_PUBLICATION).exists():
            raise OSError('isolated process stop before owner rollback')
        return resume(project_dir,**kwargs)
    with patch.object(sp,'validate_output_artifact_hashes',new=interrupt_validation), \
            patch.object(sp,'_resume_actual_refresh_publication',new=preserve_prepared):
        with pytest.raises(sp.ManualActualPostApplyError):
            sp.apply_staged_surface_actual_corrections(output)
    publication=sp._read_actual_refresh_publication(output)
    assert publication[0]['schema']=='actual-deferred-refresh/2'
    assert publication[0]['phase']=='PREPARED'
    capture_project_write_admission(output,recovery=True).recheck()
    document=surface.load(root,sp.json_load_strict(output/'校對工作階段.json'))
    drift=[]
    def rollback_then_drift(project_dir,**kwargs):
        result=resume(project_dir,**kwargs)
        if external_drift and not drift:
            path=root/ar.OCCURRENCE_OVERRIDE_FILE
            path.write_bytes(path.read_bytes()+b'\n')
            assert ar.validate_dynamic_actual_evidence(root,read_only=True)['ok']
            drift.append(path.read_bytes())
        return result
    with patch.object(sp,'_resume_actual_refresh_publication',new=rollback_then_drift), \
            patch.object(sp,'deliver_pending_promotion_outbox',side_effect=AssertionError('no Global delivery')):
        if external_drift:
            with pytest.raises(ValueError,match='changed before write'):
                sp._surface_projection_refresh(output,document)
            # The authorized owner rollback completed; unrelated changed bytes
            # are retained and cannot be re-admitted for the following publish.
            assert (root/ar.OCCURRENCE_OVERRIDE_FILE).read_bytes()==drift[0]
            assert not (output/sp.ACTUAL_REFRESH_PUBLICATION).exists()
            assert (root/promotion.PROJECT_TRANSACTION_FILE).exists()
        else:
            assert sp._surface_projection_refresh(output,document)['project_refresh']=='SUCCESS'
            assert not (root/promotion.PROJECT_TRANSACTION_FILE).exists()
            reopened=ReviewSaveService(output).load_resume_snapshot()
            assert reopened.ledger[0]['actual']=='ㄅㄛˊ'
            assert reopened.ledger[1:]==baseline_ledger[1:]
            assert [r['expected_set'] for r in reopened.ledger]==[r['expected_set'] for r in baseline_ledger]
    assert (output/'人工判定資料庫.json').read_bytes()==db


def test_surface_dependency_is_per_pdf_and_pending_not_authoritative(project,tmp_path):
    output,original=project
    root=sp.project_actual_evidence_root(output)
    pdf=Path(original['pdfs'][0]['pdf'])
    other=tmp_path/'other.pdf';other.write_bytes(b'unrelated bytes')
    dependencies={'ttf_glyph_sha256':[],'cff_glyph_keys':[]}
    baseline=ar.dynamic_actual_hashes(root,pdf_path=pdf,dependencies=dependencies,read_only=True)
    unrelated=ar.dynamic_actual_hashes(root,pdf_path=other,dependencies=dependencies,read_only=True)
    stage(output,original)
    assert ar.dynamic_actual_hashes(root,pdf_path=pdf,dependencies=dependencies,read_only=True)==baseline
    sp.apply_staged_surface_actual_corrections(output)
    assert surface.FILE in ar.dynamic_actual_hashes(root,pdf_path=pdf,dependencies=dependencies,read_only=True)
    assert ar.dynamic_actual_hashes(root,pdf_path=other,dependencies=dependencies,read_only=True)==unrelated


def test_manual_input_is_blank_and_attestation_is_not_inferred(project):
    from review_gui import surface_input_result
    output,original=project
    prepared=sp.prepare_surface_actual_correction(output,original['records'][0]['review_id'])
    assert prepared['reading']==''
    with pytest.raises(ValueError):
        surface_input_result(prepared,reading='',actor='tester',confirmations={})
    with pytest.raises(ValueError):
        surface_input_result(prepared,reading='ㄅㄛˊ',actor='tester',confirmations={
            'whole_unit_readable':False,'target_character_matches':True,'reading_from_original':True})


def test_native_slot_contradiction_full_occlusion_and_unknown_refuse(tmp_path):
    from tests.test_actual_source_visual_safety import make_visual_source
    from actual_source_visual_safety import surface_visual_proof
    for painting in ('same-slot','path','image','replacement'):
        row=make_visual_source(tmp_path/painting,painting=painting,count=1)[0]
        with pytest.raises(ValueError):
            surface_visual_proof(row,pdf=Path(row['pdf']),session_id='isolated')
    row=make_visual_source(tmp_path/'unknown',count=1)[0]
    row['glyph_id']=99999
    with pytest.raises(ValueError):
        surface_visual_proof(row,pdf=Path(row['pdf']),session_id='isolated')


def test_committed_recovery_does_not_deliver_global_or_reapply(project):
    output,original=project
    root=sp.project_actual_evidence_root(output)
    stage(output,original)
    with patch.object(sp,'_surface_projection_refresh',side_effect=OSError('isolated refresh failure')):
        with pytest.raises(sp.ManualActualPostApplyError) as failure:
            sp.apply_staged_surface_actual_corrections(output)
    assert failure.value.project_actual_commit=='COMMITTED'
    committed=surface.parse((root/surface.FILE).read_bytes())
    assert committed['pending']==[] and len(committed['records'])==1
    with patch.object(sp,'deliver_pending_promotion_outbox',side_effect=AssertionError('must not deliver')):
        result=sp.recover_committed_actual_project(output)
    assert result['project_refresh']=='SUCCESS'
    assert not (root/promotion.PROJECT_TRANSACTION_FILE).exists()
    assert surface.parse((root/surface.FILE).read_bytes())==committed
    assert ReviewSaveService(output).load_resume_snapshot().ledger[0]['actual']=='ㄅㄛˊ'


def test_prepared_rollback_keeps_staging_and_all_evidence(project):
    output,original=project
    root=sp.project_actual_evidence_root(output)
    stage(output,original)
    before=files(output)
    atomic=sp._refresh_atomic_bytes
    def fail_after_surface(path,raw):
        atomic(path,raw)
        if path==root/surface.FILE:
            raise OSError('isolated physical commit failure')
    with patch.object(sp,'_refresh_atomic_bytes',side_effect=fail_after_surface):
        with pytest.raises(OSError,match='physical commit failure'):
            sp.apply_staged_surface_actual_corrections(output)
    assert files(output)==before
    assert len(surface.parse((root/surface.FILE).read_bytes())['pending'])==1


def test_normal_override_refresh_keeps_surface_and_uses_decoder_route(project):
    from tests.test_staged_review_navigation_v580 import synthetic_actual_stage
    output,original=project
    root=sp.project_actual_evidence_root(output)
    stage(output,original)
    sp.apply_staged_surface_actual_corrections(output)
    before=ReviewSaveService(output).load_resume_snapshot()
    # This is an explicit isolated occurrence fixture, not a claim of a new
    # human confirmation or donor vote. Exercise the real refresh publisher.
    ar._append_or_update_overrides(root,[original['records'][1]],'ㄆㄧㄥˊ',source='isolated raw override input',note='')
    calls=[]
    def producer(pdfs,destination,**kwargs):
        calls.append((pdfs,destination))
        return synthetic_actual_stage(pdfs,destination,**kwargs)
    with patch.object(sp,'run_pipeline_pdfs',side_effect=producer):
        sp.refresh_actual_project(output)
    assert len(calls)==1
    after=ReviewSaveService(output).load_resume_snapshot()
    assert after.ledger[0]['actual']=='ㄅㄛˊ'
    assert after.ledger[1]['actual']=='ㄆㄧㄥˊ'
    assert [row['expected_set'] for row in after.ledger]==[row['expected_set'] for row in before.ledger]
    from actual_source_visual_safety import source_visual_safety
    assert not source_visual_safety(after.ledger[0],pdf=Path(original['pdfs'][0]['pdf']))['donor_eligible']


def test_durable_publication_cleanup_can_recover_without_global(project):
    output,original=project
    root=sp.project_actual_evidence_root(output)
    stage(output,original)
    with patch.object(sp,'_acknowledge_actual_refresh',side_effect=OSError('isolated cleanup outage')):
        with pytest.raises(sp.ManualActualPostApplyError):
            sp.apply_staged_surface_actual_corrections(output)
    assert (output/sp.ACTUAL_REFRESH_PUBLICATION).exists()
    with patch.object(sp,'deliver_pending_promotion_outbox',side_effect=AssertionError('no Global delivery')):
        result=sp.recover_committed_actual_project(output)
    assert result['project_refresh']=='SUCCESS'
    assert not (output/sp.ACTUAL_REFRESH_PUBLICATION).exists()
    assert not (root/promotion.PROJECT_TRANSACTION_FILE).exists()
    assert ReviewSaveService(output).load_resume_snapshot().ledger[0]['actual']=='ㄅㄛˊ'


def test_changed_applied_reading_keeps_prior_provenance_without_learning(project):
    output,original=project
    root=sp.project_actual_evidence_root(output)
    stage(output,original)
    sp.apply_staged_surface_actual_corrections(output)
    first=surface.parse((root/surface.FILE).read_bytes())['records'][0]
    from review_operation_worker import ReviewOperationState
    state=ReviewOperationState(output)
    prepared=state.dispatch('prepare_actual',{'review_id':original['records'][0]['review_id'],
        'expected_manifest':sp.json_load_strict(output/'校對工作階段.json'),
        'expected_db':sp.normalize_db(sp.json_load_strict(output/'人工判定資料庫.json'))},lambda phase:None,lambda:False)
    assert prepared['surface'] is not None
    before=files(output)
    for raw_writer in (sp.stage_manual_actual_correction,sp.apply_manual_actual_correction):
        with pytest.raises(ValueError,match='表面'):
            raw_writer(output,original['records'][0]['review_id'],'ㄎㄢ',[original['records'][0]['occurrence_id']])
        assert files(output)==before
    stage(output,original,reading='ㄎㄢ')
    sp.apply_staged_surface_actual_corrections(output)
    document=surface.parse((root/surface.FILE).read_bytes())
    assert len(document['records'])==1 and document['records'][0]['reading']=='ㄎㄢ'
    assert document['history']==[first]
    before=files(output)
    assert stage(output,original,reading='ㄎㄢ')['already_applied'] is True
    assert files(output)==before


def test_profile_move_rebinds_sources_preserving_receipts(project,tmp_path):
    output,original=project
    stage(output,original)
    sp.apply_staged_surface_actual_corrections(output)
    manifest=sp.json_load_strict(output/'校對工作階段.json')
    moved=tmp_path/'moved'
    rebased=sp._refresh_rebase_manifest(manifest,output,moved)
    store=(sp.project_actual_evidence_root(output)/surface.FILE).read_bytes()
    sp.json_save(output/'校對工作階段.json',rebased)
    output.rename(moved)
    snapshot=ReviewSaveService(moved).load_resume_snapshot()
    assert snapshot.ledger[0]['actual']=='ㄅㄛˊ'
    assert (sp.project_actual_evidence_root(moved)/surface.FILE).read_bytes()==store


def test_missing_pdf_with_ambiguous_same_sha_candidates_refuses_before_lock(project):
    output,original=project
    stage(output,original)
    pdf=Path(original['pdfs'][0]['pdf'])
    raw=pdf.read_bytes();pdf.unlink()
    for sub in ('a','b'):
        destination=output/sub/pdf.name;destination.parent.mkdir();destination.write_bytes(raw)
    root=sp.project_actual_evidence_root(output)
    (root/'.global_exact_glyph_delivery.lock').unlink(missing_ok=True)
    before=files(output)
    with pytest.raises((ValueError,FileNotFoundError)):
        ReviewSaveService(output).load_resume_snapshot()
    assert files(output)==before


def test_invalid_first_input_cannot_convert_or_create_project_lock(tmp_path):
    output=tmp_path/'legacy';output.mkdir()
    original=create_staged_navigation_fixture(output,same_exact_peers=True)
    root=sp.project_actual_evidence_root(output)
    ar.ensure_user_evidence_files(root)
    ar._write_actual_evidence_csv(root/ar.OCCURRENCE_OVERRIDE_FILE,ar.OVERRIDE_HEADERS,[])
    prepared=sp.prepare_surface_actual_correction(output,original['records'][0]['review_id'])
    before=files(output)
    with pytest.raises(ValueError):
        sp.enable_surface_actual_project(output,confirmation=False)
    assert files(output)==before
    for reading,attest in [('',True),('ㄅㄛˊ',False)]:
        with pytest.raises(ValueError):
            sp.stage_surface_actual_correction(output,original['records'][0]['review_id'],reading,
                proof=prepared['proof'],actor='isolated test invalid input',whole_unit_readable=attest,
                target_character_matches=True,reading_from_original=True,enable_format=True)
        assert files(output)==before
    assert not (root/surface.FILE).exists()


def test_stale_display_or_unknown_review_refuses_before_lock_creation(project):
    output,original=project
    root=sp.project_actual_evidence_root(output)
    (root/'.global_exact_glyph_delivery.lock').unlink(missing_ok=True)
    manifest=sp.json_load_strict(output/'校對工作階段.json')
    db=sp.normalize_db(sp.json_load_strict(output/'人工判定資料庫.json'))
    stale_manifest=copy.deepcopy(manifest);stale_manifest['session_id']='not displayed project'
    stale_db=copy.deepcopy(db);stale_db['events']['foreign']={'action':'not current'}
    before=files(output)
    calls=[lambda:ReviewSaveService(output).load_resume_snapshot(expected_manifest=stale_manifest),
        lambda:ReviewSaveService(output).load_resume_snapshot(expected_db=stale_db),
        lambda:ReviewSaveService(output).save_event('rev_'+'f'*64,None),
        lambda:ReviewSaveService(output).save_event(None,None)]
    for call in calls:
        with pytest.raises(ValueError):
            call()
        assert files(output)==before


def test_worker_invalid_surface_input_refuses_before_resume_lock(tmp_path):
    from review_operation_worker import ReviewOperationState
    output=tmp_path/'legacy';output.mkdir()
    original=create_staged_navigation_fixture(output,same_exact_peers=True)
    root=sp.project_actual_evidence_root(output)
    ar.ensure_user_evidence_files(root)
    ar._write_actual_evidence_csv(root/ar.OCCURRENCE_OVERRIDE_FILE,ar.OVERRIDE_HEADERS,[])
    prepared=sp.prepare_surface_actual_correction(output,original['records'][0]['review_id'])
    payload={'review_id':original['records'][0]['review_id'],'reading':'ㄅㄛˊ','proof':prepared['proof'],
        'actor':'isolated worker input test','whole_unit_readable':True,'target_character_matches':True,
        'reading_from_original':True,'enable_format':True,'expected_manifest':original,
        'expected_db':sp.normalize_db(sp.json_load_strict(output/'人工判定資料庫.json'))}
    before=files(output)
    for key,value in [('reading',''),('whole_unit_readable',False),('enable_format',False)]:
        with pytest.raises(ValueError):
            ReviewOperationState(output).dispatch('stage_surface',{**payload,key:value},lambda phase:None,lambda:False)
        assert files(output)==before


def test_new_profile_rejects_valid_unowned_legacy_journal_before_lock(project):
    output,original=project
    root=sp.project_actual_evidence_root(output)
    journal={'schema_version':'1.0','state':'PREPARED','transaction_id':'a'*64,'recovery_plan':None,
        'files':{name:promotion._packed((root/name).read_bytes() if (root/name).exists() else None)
                 for name in promotion.PROJECT_FILES},
        'staging':promotion._packed(None)}
    raw=(json.dumps(journal)+'\n').encode('utf-8')
    assert promotion._validated_project_journal(raw)['schema_version']=='1.0'
    (root/promotion.PROJECT_TRANSACTION_FILE).write_bytes(raw)
    (root/'.global_exact_glyph_delivery.lock').unlink(missing_ok=True)
    before=files(output)
    with pytest.raises(ValueError,match='owned journal/2'):
        sp.recover_committed_actual_project(output)
    assert files(output)==before


def test_explicit_full_refresh_runs_real_tiny_producer_and_preserves_surface(isolated_pipeline):
    output=isolated_pipeline('actual')
    root=sp.project_actual_evidence_root(output)
    if not (root/ar.OCCURRENCE_OVERRIDE_FILE).exists():
        ar._write_actual_evidence_csv(root/ar.OCCURRENCE_OVERRIDE_FILE,ar.OVERRIDE_HEADERS,[])
    sp.enable_surface_actual_project(output,confirmation=True)
    manifest=sp.json_load_strict(output/'校對工作階段.json')
    stage(output,manifest)
    sp.apply_staged_surface_actual_corrections(output)
    before=ReviewSaveService(output).load_resume_snapshot()
    producer=sp.run_pipeline_pdfs
    with patch.object(sp,'run_pipeline_pdfs',wraps=producer) as actual_producer, \
         patch.object(sp,'_surface_projection_refresh',side_effect=AssertionError('explicit full refresh must use producer')):
        report=sp.refresh_actual_project(output,defer_excel_reports=False)
    assert actual_producer.call_count==1 and report.is_file()
    after=ReviewSaveService(output).load_resume_snapshot()
    assert after.ledger[0]['actual']==before.ledger[0]['actual']=='ㄅㄛˊ'
    assert [row['expected_set'] for row in after.ledger]==[row['expected_set'] for row in before.ledger]
    assert after.manifest['manual_surface_projection_refresh']['automatic_decoder_cache_revalidated'] is True
    assert after.manifest['manual_surface_projection_refresh']['origin_manifest_sha256']==before.manifest['manual_surface_projection_refresh']['origin_manifest_sha256']


def test_cold_missing_manifest_does_not_repair_valid_raw_cache(isolated_pipeline):
    output=isolated_pipeline('actual')
    manifest=sp.json_load_strict(output/'校對工作階段.json')
    root=sp.project_actual_evidence_root(output)
    if not (root/ar.OCCURRENCE_OVERRIDE_FILE).exists():
        ar._write_actual_evidence_csv(root/ar.OCCURRENCE_OVERRIDE_FILE,ar.OVERRIDE_HEADERS,[])
    info=manifest['pdfs'][0];pdf=Path(info['pdf'])
    metadata=sp.workbook_metadata(Path(info['actual_workbook']),'v5.2中繼資料')
    # The expected fingerprint was generated by this actual pipeline, and the
    # actual/candidate bytes are still those sealed producer outputs.
    fingerprint={'fingerprint':info['actual_asset_fingerprint'],
                 'components':json.loads(metadata['actual_asset_fingerprint_components'])}
    sp.enable_surface_actual_project(output,confirmation=True)
    (output/'校對工作階段.json').unlink()
    (root/surface.FILE).unlink()
    (root/'.global_exact_glyph_delivery.lock').unlink(missing_ok=True)
    before=files(output)
    read=sp.collect_manifest([pdf],output/'01_實際注音',output/'02_候選報告',
        actual_fingerprints={pdf.name:fingerprint},source_validation=sp.validate_asset_manifest(Path(sp.__file__).parent))
    assert read['records'] and files(output)==before
    assert sp._has_complete_pre_session_outputs([pdf],output/'01_實際注音',output/'02_候選報告')
    with pytest.raises(ValueError,match='manifest missing'):
        sp.run_pipeline_pdfs([pdf],output,defer_excel_reports=True)
    assert files(output)==before


def test_real_spawn_surface_payload_service_apply_and_reopen_preserve_scope(tmp_path,monkeypatch):
    import os
    from review_operation_worker import ReviewProcessService
    monkeypatch.setenv('LOCALAPPDATA',str(tmp_path/'isolated-appdata'))
    output=tmp_path/'ipc';output.mkdir()
    original=create_staged_navigation_fixture(output,same_exact_peers=True)
    root=sp.project_actual_evidence_root(output)
    ar.ensure_user_evidence_files(root)
    ar._write_actual_evidence_csv(root/ar.OCCURRENCE_OVERRIDE_FILE,ar.OVERRIDE_HEADERS,[])
    prepared=sp.prepare_surface_actual_correction(output,original['records'][0]['review_id'])
    assert prepared['requires_format_conversion'] is True
    db_bytes=(output/'人工判定資料庫.json').read_bytes()
    raw={name:(root/name).read_bytes() if (root/name).exists() else None for name in promotion.PROJECT_FILES}
    client=ReviewProcessService(output)
    try:
        before=client.load_resume_snapshot()
        payload={'review_id':original['records'][0]['review_id'],'reading':'ㄅㄛˊ','proof':prepared['proof'],
            'actor':'pytest isolated real IPC input; no human GUI acceptance','whole_unit_readable':True,
            'target_character_matches':True,'reading_from_original':True,'enable_format':True,
            'expected_manifest':before.manifest,'expected_db':before.db}
        staged=client.invoke('stage_surface',payload)
        assert client.worker_pid != os.getpid()
        assert staged['summary']['surface_pending_count']==1
        applied=client.invoke('apply_actual',{'expected_manifest':staged['snapshot'].manifest,
            'expected_db':staged['snapshot'].db})
        assert applied['result']['surface_applied_count']==1
        assert applied['result']['affected_pdf_names']==[original['pdfs'][0]['pdf_name']]
        assert applied['result']['global_promotion_delivery']['status']=='NOT_APPLICABLE_SURFACE_ONLY'
    finally:
        client.close()
    reopened=ReviewSaveService(output).load_resume_snapshot()
    assert reopened.ledger[0]['actual']=='ㄅㄛˊ'
    assert reopened.ledger[1:]==before.ledger[1:]
    expected=lambda row:{key:value for key,value in row.items() if key.startswith('expected')}
    assert [expected(row) for row in reopened.ledger]==[expected(row) for row in before.ledger]
    assert (output/'人工判定資料庫.json').read_bytes()==db_bytes
    assert not list((tmp_path/'isolated-appdata').rglob('*.sqlite*'))
    for name,value in raw.items():
        assert ((root/name).read_bytes() if (root/name).exists() else None)==value
