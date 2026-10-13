"""Operation-bounded actual evidence reuse; all PDFs/fonts are real fixtures."""
import copy
import csv
import hashlib
import io
import json
from pathlib import Path
from unittest.mock import patch
from dataclasses import FrozenInstanceError
import fitz
import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
import actual_review as ar
import global_glyph_promotion as gp
import runtime_source_validation as rv
import standalone_proofread as sp
from contextlib import contextmanager
from tests.test_excel_text_storage import isolated_pipeline
import tests.test_excel_text_storage as literal_fixture

def digest(value): return hashlib.sha256(value).hexdigest()
def canonical(value): return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
def make_source(root,n=2):
 root.mkdir();pdf=root/'book.pdf';builder=FontBuilder(1000,isTTF=True);names=['.notdef',*[f'g{i}' for i in range(n)]];builder.setupGlyphOrder(names);builder.setupCharacterMap({65+i:names[i+1] for i in range(n)});glyphs={}
 for i,name in enumerate(names):
  pen=TTGlyphPen(None)
  if i:pen.moveTo((0,0));pen.lineTo((200+10*i,0));pen.lineTo((200+10*i,300));pen.closePath()
  glyphs[name]=pen.glyph()
 builder.setupGlyf(glyphs);builder.setupHorizontalMetrics({k:(600,0) for k in names});builder.setupHorizontalHeader(ascent=800,descent=-200);builder.setupNameTable({'familyName':'StageFourFixture','styleName':'Regular'});builder.setupOS2(sTypoAscender=800,sTypoDescender=-200,usWinAscent=800,usWinDescent=200);builder.setupPost();buf=io.BytesIO();builder.save(buf)
 with fitz.open() as doc:
  page=doc.new_page(width=240,height=460);xref=page.insert_font(fontname='StageFour',fontbuffer=buf.getvalue())
  for i in range(n):page.insert_text((45,45+i*40),chr(65+i)*2,fontname='StageFour',fontsize=16)
  doc.save(pdf)
 raw=pdf.read_bytes();rows=[]
 with fitz.open(pdf) as doc:
  inspector=gp.TrueTypeGlyphInspector(doc.extract_font(xref)[3])
  for span in doc[0].get_texttrace():
   for char in span['chars']:
    cp,gid,origin,bbox=char[:4];i=len(rows);identity=inspector.global_exact_identity(gid);bounds=dict(zip(('x0','y0','x1','y1'),bbox));sha=identity['glyph_sha256'];oid='occ_'+digest(str(i).encode());rid='review-'+str(i)
    rows.append(dict(occurrence_id=oid,review_id=rid,pdf=str(pdf),pdf_name=pdf.name,pdf_sha256=digest(raw),physical_page=1,printed_page='1',char=chr(cp),font_xref=xref,glyph_id=gid,state='ACTUAL_DECODE_ERROR',actual='',actual_evidence='unresolved',stable_key='k'+str(i),source_record={'font_xref':xref,'注音元件ID':gid,'glyph_id_字形索引':gid,'TTF字形SHA256':sha},**bounds))
 (root/'rows.json').write_bytes(canonical(rows));ar.ensure_user_evidence_files(root/'fingerprint-data')
 return rows


def source_context(rows):
    return {'session_id':'sealed-stage4','pdfs':{'book.pdf':{'pdf':rows[0]['pdf'],'pdf_sha256':rows[0]['pdf_sha256']}}}


def test_real_pdf_snapshot_preserves_each_locator_identity_and_fresh_single_calls(tmp_path):
    rows=make_source(tmp_path/'source');groups=ar.build_actual_review_groups(rows);context=source_context(rows)
    fresh=[gp.direct_visual_intents(g,'ㄅ',[m['occurrence_id'] for m in g['members']],context) for g in groups]
    with patch.object(gp,'TrueTypeGlyphInspector',wraps=gp.TrueTypeGlyphInspector) as inspect:
        with gp._DirectVisualPDFSnapshot(context) as snapshot:
            shared=[gp.direct_visual_intents(g,'ㄅ',[m['occurrence_id'] for m in g['members']],context,_snapshot=snapshot) for g in groups]
            bad=copy.deepcopy(groups[-1]);bad['members'][-1]['x0']+=30
            intents,admissions=gp.direct_visual_intents(bad,'ㄅ',[m['occurrence_id'] for m in bad['members']],context,_snapshot=snapshot)
            assert len(intents)==1 and 'UNPROVABLE' in admissions[-1]['reason']
        assert inspect.call_count==1
    assert shared==fresh and all(len(intents)==2 for intents,admissions in shared)
    with pytest.raises(ValueError,match='closed'):
        snapshot.__enter__()
    intents,admissions=gp.direct_visual_intents(groups[0],'ㄅ',[groups[0]['members'][0]['occurrence_id']],context,_snapshot=snapshot)
    assert not intents and 'inactive' in admissions[0]['reason']


def test_pdf_snapshot_nested_exception_close_and_foreign_context(tmp_path):
    rows=make_source(tmp_path/'source');context=source_context(rows);group=ar.build_actual_review_groups(rows)[0]
    snapshot=gp._DirectVisualPDFSnapshot(context)
    with pytest.raises(RuntimeError,match='caller failure'):
        with snapshot:
            intents,_=gp.direct_visual_intents(group,'ㄅ',[group['members'][0]['occurrence_id']],context,_snapshot=snapshot)
            assert len(intents)==1
            with pytest.raises(ValueError,match='nested'):snapshot.__enter__()
            document=next(iter(snapshot.pdfs.values()))[2]
            foreign=copy.deepcopy(context);foreign['session_id']='another-session'
            intents,admissions=gp.direct_visual_intents(group,'ㄅ',[group['members'][0]['occurrence_id']],foreign,_snapshot=snapshot)
            assert not intents and 'foreign' in admissions[0]['reason']
            raise RuntimeError('caller failure')
    assert document.is_closed and snapshot.closed and not snapshot.active


def test_pdf_change_during_preparation_blocks_batch_before_initialization(tmp_path,monkeypatch):
    rows=make_source(tmp_path/'source');groups=ar.build_actual_review_groups(rows);pdf=Path(rows[0]['pdf']);original=gp.TrueTypeGlyphInspector.global_exact_identity
    def changed(inspector,gid):
        result=original(inspector,gid);pdf.write_bytes(pdf.read_bytes()+b'\nforeign source change');return result
    monkeypatch.setattr(gp.TrueTypeGlyphInspector,'global_exact_identity',changed)
    calls=[];root=tmp_path/'actual'
    with pytest.raises(ValueError,match='SEALED_SHA_MISMATCH'):
        ar.apply_direct_visual_actual_batch(root,[dict(group=g,reading='ㄅ',checked_occurrence_ids=[m['occurrence_id'] for m in g['members']],source='visual') for g in groups],source_context=source_context(rows),initialize_evidence=lambda:calls.append('initialized'))
    assert not calls and not root.exists()


def learned_rows():
    return [dict(glyph_sha256=key,bopomofo=reading,verification_level='VERIFIED_EXACT_GLYPH',source_count='2',source_examples='first|second',updated_at='audit',notes='') for key,reading in [('a'*64,'ㄅ'),('b'*64,'ㄆ')]]


def populated(root):
    ar.ensure_user_evidence_files(root)
    ar._write_csv(root/ar.USER_GLYF_FILE,ar.USER_GLYF_HEADERS,learned_rows())
    ar._write_csv(root/ar.OCCURRENCE_OVERRIDE_FILE,ar.OVERRIDE_HEADERS,[dict(pdf_contains=name,page='1',target_char='字',stable_key=name,x0='1',y0='2',actual_reading=reading,source='visual') for name,reading in [('bookA','ㄅ'),('bookB','ㄆ')]])
    ar._write_csv(root/ar.GLYPH_CONFLICT_FILE,ar.GLYPH_CONFLICT_HEADERS,[dict(kind='TTF_GLYF_SHA256',glyph_sha256='c'*64,status='GLYPH_TRUTH_CONFLICT',readings='ㄅ|ㄆ',source_occurrence_ids='occ-a|occ-b')])


def test_dynamic_typed_snapshot_parses_same_bytes_once_and_preserves_full_public_hashes(tmp_path):
    root=tmp_path/'actual';populated(root);pdf=tmp_path/'bookA.pdf';deps={'ttf_glyph_sha256':['a'*64],'cff_glyph_keys':[]}
    fresh=ar.dynamic_actual_hashes(root,pdf_path=pdf,dependencies=deps,read_only=True)
    with patch.object(ar,'_read_csv_bytes',wraps=ar._read_csv_bytes) as parse:
        snapshot=ar.load_dynamic_actual_snapshot(root,read_only=True)
        for _ in range(8):assert ar.dynamic_actual_hashes(root,pdf_path=pdf,dependencies=deps,snapshot=snapshot)==fresh
        assert parse.call_count==4
    row=snapshot.rows(ar.USER_GLYF_FILE)[0];row['bopomofo']='ㄇ'
    assert snapshot.rows(ar.USER_GLYF_FILE)[0]['bopomofo']=='ㄅ'
    with pytest.raises(FrozenInstanceError):snapshot.root=tmp_path
    assert ar.dynamic_actual_hashes(root,snapshot=snapshot)==ar.validate_dynamic_actual_evidence(root,read_only=True)['hashes']


def test_dynamic_snapshot_rejects_foreign_untyped_and_unvalidated_values(tmp_path):
    root=tmp_path/'actual';populated(root);snapshot=ar.load_dynamic_actual_snapshot(root,read_only=True)
    with pytest.raises(ValueError,match='foreign'):ar.dynamic_actual_hashes(tmp_path/'foreign',snapshot=snapshot)
    with pytest.raises(ValueError,match='typed'):ar.dynamic_actual_hashes(root,snapshot={'hashes':dict(snapshot.hashes)})
    with pytest.raises(ValueError,match='validated capture'):ar.ValidatedDynamicActualSnapshot(root,{}, {}, {})


@pytest.mark.parametrize('problem',['header','reading','conflict','sha'])
def test_dynamic_snapshot_retains_global_validation_before_dependency_filter(tmp_path,problem):
    root=tmp_path/'actual';populated(root)
    if problem=='header':(root/ar.USER_CFF_FILE).write_text('unknown\n',encoding='utf-8')
    elif problem=='reading':
        rows=learned_rows();rows[-1]['bopomofo']='invalid';ar._write_csv(root/ar.USER_GLYF_FILE,ar.USER_GLYF_HEADERS,rows)
    elif problem=='sha':
        rows=learned_rows();rows[-1]['glyph_sha256']='short';ar._write_csv(root/ar.USER_GLYF_FILE,ar.USER_GLYF_HEADERS,rows)
    else:ar._write_csv(root/ar.GLYPH_CONFLICT_FILE,ar.GLYPH_CONFLICT_HEADERS,[dict(kind='unknown',glyph_sha256='c'*64,status='GLYPH_TRUTH_CONFLICT',readings='ㄅ|ㄆ')])
    assert not ar.validate_dynamic_actual_evidence(root,read_only=True)['ok']
    with pytest.raises(ValueError,match='驗證失敗'):ar.dynamic_actual_hashes(root,pdf_path=tmp_path/'bookA.pdf',dependencies={'ttf_glyph_sha256':['a'*64]},read_only=True)


def test_dynamic_changed_snapshot_rejected_and_new_operation_preserves_unaffected_pdf(tmp_path):
    root=tmp_path/'actual';populated(root);a=tmp_path/'bookA.pdf';b=tmp_path/'bookB.pdf';depsa={'ttf_glyph_sha256':['a'*64]};depsb={'ttf_glyph_sha256':['b'*64]}
    before=ar.load_dynamic_actual_snapshot(root,read_only=True);ha=ar.dynamic_actual_hashes(root,pdf_path=a,dependencies=depsa,snapshot=before);hb=ar.dynamic_actual_hashes(root,pdf_path=b,dependencies=depsb,snapshot=before)
    rows=learned_rows();rows[-1]['bopomofo']='ㄇ';ar._write_csv(root/ar.USER_GLYF_FILE,ar.USER_GLYF_HEADERS,rows)
    with pytest.raises(ValueError,match='source changed'):ar.dynamic_actual_hashes(root,pdf_path=b,dependencies=depsb,snapshot=before)
    after=ar.load_dynamic_actual_snapshot(root,read_only=True)
    assert ar.dynamic_actual_hashes(root,pdf_path=a,dependencies=depsa,snapshot=after)==ha
    assert ar.dynamic_actual_hashes(root,pdf_path=b,dependencies=depsb,snapshot=after)!=hb


def test_typed_snapshot_fingerprint_fresh_operation_changes_only_dependent_pdf(tmp_path):
    from cross_version_compat import fingerprint_compatible
    source=Path(__file__).resolve().parents[1];validation=rv.validate_asset_manifest(source)
    assert validation['ok'],validation['errors']
    rows=make_source(tmp_path/'source');root=tmp_path/'actual';populated(root)
    pdfs=[tmp_path/'bookA.pdf',tmp_path/'bookB.pdf']
    for pdf in pdfs:pdf.write_bytes(Path(rows[0]['pdf']).read_bytes())
    global_hashes=gp.library.global_exact_glyph_evidence_hashes(gp.library.GlobalExactGlyphRepository.resolved(tmp_path/'global-fixture').load_snapshot(),[])
    def fingerprints(snapshot):
        return [rv.compute_actual_asset_fingerprint(source,pdf,validation,decoder_version=sp.ACTUAL_DECODER_VERSION,source_files=['actual_review.py'],global_exact_glyph_evidence_hashes=global_hashes,dynamic_dependencies={'ttf_glyph_sha256':[key],'cff_glyph_keys':[]},dynamic_evidence_root=root,dynamic_snapshot=snapshot) for pdf,key in zip(pdfs,['a'*64,'b'*64])]
    snapshot=ar.load_dynamic_actual_snapshot(root,read_only=True);before=fingerprints(snapshot)
    learned=learned_rows();learned[1]['bopomofo']='ㄇ';ar._write_csv(root/ar.USER_GLYF_FILE,ar.USER_GLYF_HEADERS,learned)
    with pytest.raises(rv.SourceValidationError,match='source changed'):fingerprints(snapshot)
    fresh=ar.load_dynamic_actual_snapshot(root,read_only=True);after=fingerprints(fresh)
    assert before[0]['fingerprint']==after[0]['fingerprint']
    assert before[1]['fingerprint']!=after[1]['fingerprint']
    assert fingerprint_compatible(before[0]['fingerprint'],before[0]['components'],after[0],chain='actual')
    assert not fingerprint_compatible(before[1]['fingerprint'],before[1]['components'],after[1],chain='actual')


def test_dynamic_snapshot_captures_after_finite_initialization_and_tracks_absence(tmp_path):
    root=tmp_path/'actual';snapshot=ar.load_dynamic_actual_snapshot(root)
    assert dict(snapshot.hashes)[ar.OCCURRENCE_OVERRIDE_FILE]=='ABSENT'
    snapshot.assert_current(root)
    ar._write_csv(root/ar.OCCURRENCE_OVERRIDE_FILE,ar.OVERRIDE_HEADERS,[])
    with pytest.raises(ValueError,match='source changed'):snapshot.assert_current(root)
    assert dict(ar.load_dynamic_actual_snapshot(root,read_only=True).hashes)[ar.OCCURRENCE_OVERRIDE_FILE]!='ABSENT'


def test_export_reuses_first_location_keeps_finished_validation_and_stale_render_rejection(tmp_path,monkeypatch):
    rows=make_source(tmp_path/'source');output=tmp_path/'export';meta=dict(version=sp.VERSION,session_id='sealed-stage4',session_schema_version=sp.SESSION_SCHEMA_VERSION,workbook_schema_version=sp.WORKBOOK_SCHEMA_VERSION,review_id_schema_version=sp.REVIEW_ID_SCHEMA_VERSION)
    with patch.object(ar,'_confirmed_render_location',wraps=ar._confirmed_render_location) as location:
        ar.export_actual_review_package(output,rows,**meta)
        # Four first preflight locations plus four necessary final profile checks.
        assert location.call_count==8
    previous={str(p.relative_to(output)):p.read_bytes() for p in output.rglob('*') if p.is_file()};original=ar.render_annotation_only_png
    def drift(entry,*args,**kwargs):
        pdf=Path(entry['pdf']);pdf.write_bytes(pdf.read_bytes()+b'\nforeign render source change');return original(entry,*args,**kwargs)
    monkeypatch.setattr(ar,'render_annotation_only_png',drift)
    with pytest.raises(ValueError,match='預檢後變動'):ar.export_actual_review_package(output,rows,**meta)
    assert {str(p.relative_to(output)):p.read_bytes() for p in output.rglob('*') if p.is_file()}==previous


@pytest.mark.parametrize('boundary',['lock-enter','prewrite-guard'])
def test_pipeline_dynamic_drift_at_locked_publication_boundary_rejects_without_sealing(isolated_pipeline,monkeypatch,boundary):
    monkeypatch.setattr(literal_fixture,'TEXTS',('=1',' +2'))
    root=isolated_pipeline('actual');manifest=sp.json_load_strict(root/'校對工作階段.json');pdf=Path(manifest['pdfs'][0]['pdf']);dynamic=sp.project_actual_evidence_root(root)
    previous={name:(root/name).read_bytes() for name in ['校對工作階段.json','人工判定資料庫.json']};changed=[]
    def drift():
        if not changed:
            path=dynamic/ar.USER_GLYF_FILE;path.write_bytes(path.read_bytes()+b'\n');changed.append(True)
    original=sp.project_delivery_lock
    @contextmanager
    def enter(path):
        with original(path):
            drift();yield
    guards=[]
    def guard():
        guards.append(True)
        if len(guards)==2:drift()
    if boundary=='lock-enter':monkeypatch.setattr(sp,'project_delivery_lock',enter)
    with pytest.raises(ValueError,match='source changed'):
        sp.run_pipeline_pdfs([pdf],root,defer_excel_reports=True,_prewrite_guard=guard if boundary=='prewrite-guard' else None)
    assert changed and {name:(root/name).read_bytes() for name in previous}==previous
    assert sp.json_load_strict(root/'pipeline_status.json')['status']=='PIPELINE_BLOCKED'
