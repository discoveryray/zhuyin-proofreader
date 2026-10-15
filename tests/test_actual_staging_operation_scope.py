"""Real public staging/resume/save operation boundaries; no caller context/Tk."""
import copy
import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest
import fitz
import actual_review as actual
import actual_source_visual_safety as safety
import standalone_proofread as pipeline
from review_save_service import ReviewSaveService
from tests.test_actual_source_visual_safety import make_visual_source
from tests.test_staged_review_navigation_v580 import create_staged_navigation_fixture
from export_pdf_text_diagnostics import TrueTypeGlyphInspector


def project_with_staging(root, *, checked_count=3):
    output=root/'project'
    create_staged_navigation_fixture(output,same_exact_peers=True)
    service=ReviewSaveService(output)
    snapshot=service.load_resume_snapshot()
    projected=pipeline._manual_actual_snapshot_ledger(snapshot.manifest,snapshot.db,snapshot.ledger,
        base_ledger=snapshot.baseline)
    group=actual.build_actual_group_for_entry(projected,projected[0])
    assert len(group['members'])==3
    ids=[r['occurrence_id'] for r in group['members']]
    actual.stage_manual_actual_group(pipeline.project_actual_evidence_root(output),group,'ㄅ',
        checked_occurrence_ids=ids[:checked_count],source='real scope fixture')
    return output,service,service.load_resume_snapshot(),group,ids


def current_files(output):
    return {str(p.relative_to(output)):p.read_bytes() for p in output.rglob('*') if p.is_file()}


def mutate_source(row):
    path=Path(row['pdf'])
    path.write_bytes(path.read_bytes()+b'\n% isolated source drift\n')


def assert_only_injected_source_drift(output,before,row):
    expected=dict(before)
    source=str(Path(row['pdf']).relative_to(output))
    expected[source]+=b'\n% isolated source drift\n'
    assert current_files(output)==expected


def test_public_validator_proves_each_checked_occurrence_in_one_owned_page(tmp_path):
    rows=make_visual_source(tmp_path/'source',count=4)
    group=actual.build_actual_group_for_entry(rows,rows[0])
    ids={r['occurrence_id'] for r in rows}
    with (patch.object(safety,'_capture_native_page',wraps=safety._capture_native_page) as capture,
          patch.object(actual,'require_actual_visual_target',wraps=actual.require_actual_visual_target) as checked):
        pipeline._validate_manual_actual_group_sources(tmp_path,[group],checked_occurrence_ids=ids)
    assert capture.call_count==1
    assert {c.args[0]['occurrence_id'] for c in checked.call_args_list}==ids
    assert safety._CURRENT_OPERATION.get() is None
    with patch.object(safety,'_capture_native_page',wraps=safety._capture_native_page) as capture:
        pipeline._validate_manual_actual_group_sources(tmp_path,[group],checked_occurrence_ids={rows[0]['occurrence_id']})
    assert capture.call_count==1  # a new public operation must build its own page


def test_real_resume_and_public_summary_keep_three_checks_with_one_page_per_operation(tmp_path):
    output,service,snapshot,_group,ids=project_with_staging(tmp_path)
    for read in (lambda:service.validate_resume_staging(snapshot),
                 lambda:pipeline.manual_actual_staging_summary(output,ledger=snapshot.ledger)):
        with patch.object(safety,'_capture_native_page',wraps=safety._capture_native_page) as capture:
            result=read()
        assert capture.call_count==1 and not result.get('staging_error')
        assert set(result['staged_checked_occurrence_ids'])==set(ids)
        assert safety._CURRENT_OPERATION.get() is None


def test_public_append_staging_shares_prior_new_and_lower_checks(tmp_path):
    output,_service,_snapshot,group,ids=project_with_staging(tmp_path,checked_count=1)
    with patch.object(safety,'_capture_native_page',wraps=safety._capture_native_page) as capture:
        result=pipeline.stage_manual_actual_correction(output,group['members'][0]['review_id'],'ㄅ',
            checked_occurrence_ids=ids[1:])
    assert capture.call_count==1
    assert set(result['staged_group']['checked_occurrence_ids'])==set(ids)
    assert actual.load_manual_actual_staging(pipeline.project_actual_evidence_root(output))['staged_groups'][0]['checked_occurrence_ids']==ids
    assert safety._CURRENT_OPERATION.get() is None


def test_real_save_reuses_staging_page_and_preserves_independent_expected(tmp_path):
    output,service,snapshot,_group,ids=project_with_staging(tmp_path)
    row=snapshot.ledger[0]
    before=copy.deepcopy(row)
    event=pipeline.build_manual_expected_event(row,operation='ENTER_EXPECTED',expected_set='ㄅ')
    with patch.object(safety,'_capture_native_page',wraps=safety._capture_native_page) as capture:
        result=service.save_event(row['review_id'],event,expected_manifest=snapshot.manifest,expected_db=snapshot.db)
    assert capture.call_count==1 and not result.staging_summary.get('staging_error')
    assert set(result.staging_summary['staged_checked_occurrence_ids'])==set(ids)
    assert result.resolved_entry['expected_set']==['ㄅ']
    assert result.resolved_entry['actual']==before['actual']
    assert safety._CURRENT_OPERATION.get() is None


def test_mid_roster_failure_closes_operation_and_publishes_no_checked_ids(tmp_path):
    output,service,snapshot,_group,_ids=project_with_staging(tmp_path)
    before=current_files(output)
    original=actual.require_actual_visual_target
    calls=[]
    operations=[]
    initialize=safety.SourceVisualOperation.__init__
    def observe(operation):
        initialize(operation);operations.append(operation)
    def change_after_second(*args,**kwargs):
        result=original(*args,**kwargs)
        calls.append(args[0]['occurrence_id'])
        if len(calls)==2:mutate_source(args[0])
        return result
    with (patch.object(safety.SourceVisualOperation,'__init__',observe),
          patch.object(actual,'require_actual_visual_target',change_after_second)):
        result=service.validate_resume_staging(snapshot)
    assert result.get('staging_error') and result['staged_checked_occurrence_ids']==[]
    assert_only_injected_source_drift(output,before,snapshot.ledger[0])
    assert operations and all(o._closed and not o._pages and not o._documents and not o._sources for o in operations)
    assert safety._CURRENT_OPERATION.get() is None


def test_resume_rechecks_current_source_after_final_content_capture(tmp_path):
    output,service,snapshot,_group,_ids=project_with_staging(tmp_path)
    before=current_files(output)
    original=service._capture
    def change_after_capture(*args,**kwargs):
        captured=original(*args,**kwargs)
        mutate_source(snapshot.ledger[0])
        return captured
    with patch.object(service,'_capture',change_after_capture):
        result=service.validate_resume_staging(snapshot)
    assert result.get('staging_error') and result['staged_checked_occurrence_ids']==[]
    assert_only_injected_source_drift(output,before,snapshot.ledger[0])
    assert safety._CURRENT_OPERATION.get() is None


def test_append_and_save_drift_are_refused_before_physical_writes(tmp_path):
    output,service,snapshot,group,ids=project_with_staging(tmp_path/'append',checked_count=1)
    before=current_files(output)
    original=pipeline.stage_manual_actual_group
    def change_before_lower(*args,**kwargs):
        mutate_source(group['members'][0])
        return original(*args,**kwargs)
    with patch.object(pipeline,'stage_manual_actual_group',change_before_lower):
        with pytest.raises(ValueError):
            pipeline.stage_manual_actual_correction(output,group['members'][0]['review_id'],'ㄅ',checked_occurrence_ids=ids[1:])
    assert_only_injected_source_drift(output,before,group['members'][0])
    assert safety._CURRENT_OPERATION.get() is None
    output,service,snapshot,_group,_ids=project_with_staging(tmp_path/'save')
    before=current_files(output)
    row=snapshot.ledger[0]
    event=pipeline.build_manual_expected_event(row,operation='ENTER_EXPECTED',expected_set='ㄅ')
    def notify(phase):
        if phase=='正在原子寫入人工判定':mutate_source(row)
    with pytest.raises(ValueError):
        service.save_event(row['review_id'],event,expected_manifest=snapshot.manifest,expected_db=snapshot.db,progress=notify)
    assert_only_injected_source_drift(output,before,row)
    assert safety._CURRENT_OPERATION.get() is None


def test_post_commit_source_change_preserves_commit_but_retires_checked_summary(tmp_path):
    output,service,snapshot,_group,_ids=project_with_staging(tmp_path)
    row=snapshot.ledger[0]
    event=pipeline.build_manual_expected_event(row,operation='ENTER_EXPECTED',expected_set='ㄅ')
    def notify(phase):
        if phase=='人工判定已寫入':mutate_source(row)
    result=service.save_event(row['review_id'],event,expected_manifest=snapshot.manifest,expected_db=snapshot.db,progress=notify)
    assert result.staging_summary.get('staging_error') and result.staging_summary['staged_checked_occurrence_ids']==[]
    db=pipeline.json_load_strict(output/'人工判定資料庫.json')
    assert db['events'][row['review_id']]['expected_set']==['ㄅ']
    assert result.resolved_entry['actual']==row['actual']
    assert safety._CURRENT_OPERATION.get() is None


def test_public_validator_separates_different_sealed_pdfs_and_physical_pages(tmp_path):
    first=make_visual_source(tmp_path/'first',count=1)[0]
    second=make_visual_source(tmp_path/'second',count=1)[0]
    new_pdf=tmp_path/'two-pages.pdf'
    with fitz.open(second['pdf']) as document:
        font=document.extract_font(second['font_xref'])[3]
        page=document.new_page(width=220,height=240)
        page.insert_font(fontname='ActualVisual',fontbuffer=font)
        page.insert_text((40,50),'A',fontname='ActualVisual',fontsize=20)
        document.save(new_pdf)
    digest=hashlib.sha256(new_pdf.read_bytes()).hexdigest()
    rows=[first]
    for number in (1,2):
        row=copy.deepcopy(second)
        with fitz.open(new_pdf) as document:
            page=document[number-1]
            char=page.get_texttrace()[0]['chars'][0]
            xref=page.get_fonts(full=True)[0][0]
            glyph_sha=TrueTypeGlyphInspector(document.extract_font(xref)[3]).glyph_sha256(char[1])
        row.update(pdf=str(new_pdf),pdf_name=new_pdf.name,pdf_sha256=digest,physical_page=number,
            font_xref=xref,glyph_id=char[1],zhuyin_component_id=char[1],
            occurrence_id='occ_'+hashlib.sha256((digest+str(number)).encode()).hexdigest(),
            review_id='rev_'+hashlib.sha256((digest+'review'+str(number)).encode()).hexdigest())
        row.update(dict(zip(('x0','y0','x1','y1'),char[3])))
        row['source_record'].update(font_xref=xref,glyph_id_字形索引=char[1],
            注音元件ID=char[1],TTF字形SHA256=glyph_sha)
        rows.append(row)
    with patch.object(safety,'_capture_native_page',wraps=safety._capture_native_page) as capture:
        pipeline._validate_manual_actual_group_sources(tmp_path,[{'members':rows}],
            checked_occurrence_ids={r['occurrence_id'] for r in rows})
    assert capture.call_count==3 and safety._CURRENT_OPERATION.get() is None
