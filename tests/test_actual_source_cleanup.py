"""Native cleanup faults at real durable product boundaries; no Tk/window."""
import json
import pickle
import time
from pathlib import Path
from multiprocessing.reduction import ForkingPickler
from unittest.mock import patch

import fitz
import pytest
import actual_review as actual
import actual_source_visual_safety as safety
import global_glyph_promotion as promotion
import review_gui as gui
import standalone_proofread as pipeline
from review_operation_worker import ReviewOperationState
from tests.test_actual_source_visual_safety import make_visual_source
from tests.test_actual_staging_operation_scope import project_with_staging
from tests.no_window_review_support import Surface
from tests.review_save_test_support import QueuedRoot


def failing_real_close(original):
    def close(document):
        original(document)
        raise RuntimeError('injected native document teardown failure')
    return close


def test_all_owned_documents_are_attempted_and_primary_failure_is_preserved(tmp_path):
    rows=[make_visual_source(tmp_path/name,count=1)[0] for name in ('first','second')]
    original=fitz.Document.close
    for primary in (None,ValueError('primary prewrite failure')):
        operation=safety.SourceVisualOperation()
        attempted=[]
        documents=[]
        def close(document):
            attempted.append(document)
            original(document)
            if len(attempted)==1:raise RuntimeError('first document close failed')
        expected=safety.SourceVisualCleanupError if primary is None else ValueError
        with patch.object(fitz.Document,'close',close),pytest.raises(expected) as caught:
            with operation:
                for row in rows:
                    assert safety.source_visual_safety(row)['location_readable']
                documents=[document for _raw,document in operation._documents.values()]
                if primary is not None:raise primary
        assert attempted==documents and all(d.is_closed for d in documents)
        assert operation._closed and not operation._pages and not operation._documents
        assert not operation._sources and not operation._raws and safety._CURRENT_OPERATION.get() is None
        if primary is not None:
            assert caught.value is primary and 'teardown' in caught.value.__notes__[0]


def test_real_dispatch_save_retains_result_through_codec_and_gui_recovery(tmp_path):
    output,service,snapshot,_group,_ids=project_with_staging(tmp_path)
    row=snapshot.ledger[0]
    event=pipeline.build_manual_expected_event(row,operation='ENTER_EXPECTED',expected_set='ㄅ')
    state=ReviewOperationState(output,service=service)
    with patch.object(fitz.Document,'close',failing_real_close(fitz.Document.close)):
        result=state.dispatch('save',dict(review_id=row['review_id'],event=event,
            expected_manifest=snapshot.manifest,expected_db=snapshot.db),lambda phase:None,lambda:False)
    assert result.cleanup_error and result.staging_summary['staged_checked_occurrence_ids']==[]
    # The production connection uses ForkingPickler for its unversioned DTO.
    result=pickle.loads(ForkingPickler.dumps(result))
    assert result.cleanup_error and result.db['events'][row['review_id']]['expected_set']==['ㄅ']
    app=gui.ReviewApp.__new__(gui.ReviewApp)
    app.root=QueuedRoot();app.status=Surface();app.primary=Surface()
    app.output_dir=output;app.manifest=snapshot.manifest;app.db=snapshot.db
    app.records=[row];app.index=0;app.focused_entry=None
    app._project_generation=0;app._save_service=service;app._saved_control_states=[]
    app._async_disposed=False;app._staging_request=None;app._save_in_progress=True;app._save_recovery_required=False
    request=dict(generation=0,manifest=app.manifest,db=app.db,output_dir=output,
        review_id=row['review_id'],current_review_id=row['review_id'],event=event,
        pending_staging=False,started=time.perf_counter(),focus_after_save=False)
    app._save_request=request
    warnings=[]
    with patch.object(gui.messagebox,'showwarning',lambda title,text,**kw:warnings.append((title,text))):
        gui.ReviewApp._finish_event_save(app,request,result,None,None)
    assert app._last_event_saved and app._save_recovery_required and not app._save_in_progress
    assert app.db['events'][row['review_id']]['expected_set']==['ㄅ']
    assert warnings[0][0]=='人工判定已保存，但畫面更新失敗'
    assert app.primary.cget('state')=='disabled'
    assert pipeline.json_load_strict(output/'人工判定資料庫.json')['events'][row['review_id']]['expected_set']==['ㄅ']


def test_low_and_public_staging_cleanup_keeps_durable_result(tmp_path):
    rows=make_visual_source(tmp_path/'source',count=2)
    group=actual.build_actual_group_for_entry(rows,rows[0])
    root=tmp_path/'actual'
    with patch.object(fitz.Document,'close',failing_real_close(fitz.Document.close)):
        with pytest.raises(safety.SourceVisualPostCommitCleanupError) as caught:
            actual.stage_manual_actual_group(root,group,'ㄅ',checked_occurrence_ids=[r['occurrence_id'] for r in rows],source='visual')
    assert caught.value.commit_state=='STAGED_REFRESH_PENDING'
    assert actual.load_manual_actual_staging(root)['staged_groups'][0]==caught.value.completed_result
    output,_service,_snapshot,group,ids=project_with_staging(tmp_path/'public')
    with patch.object(fitz.Document,'close',failing_real_close(fitz.Document.close)):
        with pytest.raises(safety.SourceVisualPostCommitCleanupError) as caught:
            pipeline.stage_manual_actual_correction(output,group['members'][0]['review_id'],'ㄅ',checked_occurrence_ids=ids)
    assert caught.value.commit_state=='STAGED_REFRESH_PENDING'
    saved=actual.load_manual_actual_staging(pipeline.project_actual_evidence_root(output))['staged_groups'][0]
    assert saved==caught.value.completed_result['staged_group']


def test_committed_batch_cleanup_retains_journal_ack_and_never_reapplies(tmp_path):
    rows=make_visual_source(tmp_path/'source',count=2)
    group=actual.build_actual_group_for_entry(rows,rows[0])
    root=tmp_path/'actual'
    actual.stage_manual_actual_group(root,group,'ㄅ',checked_occurrence_ids=[r['occurrence_id'] for r in rows],source='visual')
    original=fitz.Document.close
    def close_after_commit(document):
        original(document)
        journal=root/promotion.PROJECT_TRANSACTION_FILE
        if journal.exists() and json.loads(journal.read_text(encoding='utf8'))['state']=='COMMITTED':
            raise RuntimeError('postcommit source teardown fault')
    with (patch.object(fitz.Document,'close',close_after_commit),
          patch.object(actual,'apply_verified_actual_group',wraps=actual.apply_verified_actual_group) as apply):
        with pytest.raises(safety.SourceVisualPostCommitCleanupError) as caught:
            actual.apply_staged_manual_actual_batch(root,[group])
    assert apply.call_count==1 and caught.value.commit_state=='COMMITTED_REFRESH_PENDING'
    assert caught.value.completed_result['project_actual_commit']=='COMMITTED'
    assert json.loads((root/promotion.PROJECT_TRANSACTION_FILE).read_text(encoding='utf8'))['state']=='COMMITTED'
    assert not actual.load_manual_actual_staging(root)['staged_groups']
    before={p.name:p.read_bytes() for p in root.iterdir() if p.is_file()}
    with patch.object(actual,'apply_verified_actual_group',wraps=actual.apply_verified_actual_group) as apply:
        result=actual.apply_staged_manual_actual_batch(root,[group])
    assert result['applied_group_count']==0 and result['group_results']==[] and apply.call_count==0
    assert before=={p.name:p.read_bytes() for p in root.iterdir() if p.is_file()}


def test_readonly_native_and_prewrite_failures_are_not_committed_cleanup(tmp_path):
    row=make_visual_source(tmp_path/'source',count=1)[0]
    original=fitz.Document.close
    with patch.object(fitz.Document,'close',failing_real_close(original)):
        proof=safety.source_visual_safety(row)
    assert not proof['location_readable'] and not proof['donor_eligible']
    group=actual.build_actual_group_for_entry([row],row)
    from tests.test_actual_visual_write_guards import context_for
    intents,admissions=promotion.direct_visual_intents(group,'ㄅ',[row['occurrence_id']],context_for([row]))
    assert len(intents)==1 and admissions[0]['status']=='ELIGIBLE'
    outbox_root=tmp_path/'pending'
    promotion.enqueue_promotion_intents(outbox_root,intents)
    before=(outbox_root/promotion.GLOBAL_PROMOTION_OUTBOX_FILE).read_bytes()
    from global_exact_glyph_library import GlobalExactGlyphRepository
    repository=GlobalExactGlyphRepository.resolved(tmp_path/'global')
    with patch.object(fitz.Document,'close',failing_real_close(original)):
        delivered=promotion.deliver_pending_promotion_outbox(outbox_root,repository=repository,
            source_context=context_for([row]),source_entries=[row])
    assert delivered['status']=='PENDING_RETRY' and delivered['delivered_count']==0
    assert (outbox_root/promotion.GLOBAL_PROMOTION_OUTBOX_FILE).read_bytes()==before
    assert not list((tmp_path/'global').rglob('*.sqlite*'))
    root=tmp_path/'actual'
    failure=OSError('primary staging writer failure')
    with (patch.object(actual,'_write_manual_actual_staging',side_effect=failure),
          patch.object(fitz.Document,'close',failing_real_close(original))):
        with pytest.raises(OSError) as caught:
            actual.stage_manual_actual_group(root,group,'ㄅ',checked_occurrence_ids=[row['occurrence_id']],source='visual')
    assert caught.value is failure and not root.exists()
    # Even the same typed cleanup class raised at the writer boundary is
    # still a failed body, never proof that the action completed a write.
    typed=safety.SourceVisualCleanupError('typed primary prewrite failure')
    with (patch.object(actual,'_write_manual_actual_staging',side_effect=typed),
          patch.object(fitz.Document,'close',failing_real_close(original))):
        with pytest.raises(safety.SourceVisualCleanupError) as caught:
            actual.stage_manual_actual_group(root,group,'ㄅ',checked_occurrence_ids=[row['occurrence_id']],source='visual')
    assert caught.value is typed and not isinstance(caught.value,safety.SourceVisualPostCommitCleanupError)
    assert not root.exists()
    native=safety._native_pdf.mupdf
    close_device=native.fz_close_device
    def device_fault(device):
        close_device(device)
        raise RuntimeError('native proof device closure fault')
    with patch.object(native,'fz_close_device',device_fault):
        with pytest.raises(ValueError):
            actual.stage_manual_actual_group(root,group,'ㄅ',checked_occurrence_ids=[row['occurrence_id']],source='visual')
    assert not root.exists() and safety._CURRENT_OPERATION.get() is None


def test_save_result_cleanup_field_defaults_compatibly(tmp_path):
    output,service,snapshot,_group,_ids=project_with_staging(tmp_path)
    row=snapshot.ledger[0]
    result=service.save_event(row['review_id'],pipeline.build_manual_expected_event(row,
        operation='ENTER_EXPECTED',expected_set='ㄅ'))
    assert result.cleanup_error is None
    assert pickle.loads(ForkingPickler.dumps(result)).cleanup_error is None
    before=(output/'人工判定資料庫.json').read_bytes()
    forged=safety.SourceVisualPostCommitCleanupError(result,'COMMITTED_REFRESH_PENDING',
        safety.SourceVisualCleanupError('body-provided carrier is not a completed write'))
    with patch.object(pipeline,'json_save',side_effect=forged):
        with pytest.raises(safety.SourceVisualPostCommitCleanupError) as caught:
            service.save_event(row['review_id'],pipeline.build_manual_expected_event(row,
                operation='ENTER_EXPECTED',expected_set='ㄆ'))
    assert caught.value is forged and (output/'人工判定資料庫.json').read_bytes()==before
