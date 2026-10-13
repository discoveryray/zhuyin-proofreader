"""Finite ordered actual candidates inside the original owner journal; no Tk."""
import copy
import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
import actual_review as ar
from tests.test_manual_actual_batch_apply_v570 import group, AUTHORITATIVE_FILES


def decisions(current, readings=('ㄅ',)):
    return [{'group': current, 'reading': reading,
             'checked_occurrence_ids': [m['occurrence_id'] for m in current['members'][:2]],
             'source': 'independent visual actual fixture', 'note': '=literal source note'}
            for reading in readings]


def snapshot(root):
    return {name: (root/name).read_bytes() if (root/name).exists() else None for name in AUTHORITATIVE_FILES}


class FrozenTime(datetime.datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 13, 0, 0, 0, tzinfo=tz)


def test_prepared_candidates_match_legacy_ordered_conflict_and_provenance(tmp_path, monkeypatch):
    monkeypatch.setattr(ar, 'datetime', FrozenTime)
    current = group('same-exact', '1'*64, count=4)
    commands = decisions(current, ('ㄅ', 'ㄆ'))
    commands[0]['source'] = '  first visual source  '
    commands[0]['note'] = '  =literal first note  '
    commands[1]['checked_occurrence_ids'] = [current['members'][2]['occurrence_id']]
    legacy = tmp_path/'legacy'; batch = tmp_path/'batch'
    for command in commands:
        ar.apply_verified_actual_group(legacy, current, command['reading'],
                                      checked_occurrence_ids=command['checked_occurrence_ids'],
                                      source=command['source'], note=command['note'])
    result = ar.apply_direct_visual_actual_batch(batch, commands)
    assert snapshot(batch) == snapshot(legacy)
    assert result['group_results'][1]['quarantined'] is True
    assert result['group_results'][1]['reopened_occurrence_ids'] == [current['members'][3]['occurrence_id']]
    conflicts = ar._read_csv(batch/ar.GLYPH_CONFLICT_FILE, ar.GLYPH_CONFLICT_HEADERS)
    assert conflicts[0]['readings'] == 'ㄅ|ㄆ'
    overrides = ar._read_csv(batch/ar.OCCURRENCE_OVERRIDE_FILE, ar.OVERRIDE_HEADERS)
    assert len(overrides) == 3
    assert ar._prepared_actual_csv.get() is None


def test_many_groups_parse_source_once_and_flush_each_dirty_file_once(tmp_path, monkeypatch):
    ar.ensure_user_evidence_files(tmp_path)
    commands = [decisions(group(f'g{i}', f'{i+1:064x}'))[0] for i in range(8)]
    counts = {'dynamic_parse': 0, 'static_parse': 0, 'write': 0, 'guard': 0}; reader = ar.csv.DictReader; writer = ar._write_csv
    def parsed(*args, **kwargs):
        import io
        counts['dynamic_parse' if isinstance(args[0], io.StringIO) else 'static_parse'] += 1
        return reader(*args, **kwargs)
    def written(*args, **kwargs): counts['write'] += 1; return writer(*args, **kwargs)
    def guard(): counts['guard'] += 1
    monkeypatch.setattr(ar.csv, 'DictReader', parsed); monkeypatch.setattr(ar, '_write_csv', written)
    result = ar.apply_direct_visual_actual_batch(tmp_path, commands, prewrite_guard=guard)
    assert len(result['group_results']) == 8
    assert counts == {'dynamic_parse': 4, 'static_parse': 8, 'write': 3, 'guard': 5}
    assert ar._prepared_actual_csv.get() is None


def test_second_group_failure_never_flushes_prepared_csv_and_resets_state(tmp_path, monkeypatch):
    ar.ensure_user_evidence_files(tmp_path); original = snapshot(tmp_path)
    commands = [decisions(group('first', '1'*64))[0], decisions(group('second', '2'*64))[0]]
    applied = ar.apply_verified_actual_group; calls = []; writes = []
    def apply(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2: raise RuntimeError('second group before flush')
        return applied(*args, **kwargs)
    writer = ar._write_csv
    def written(*args, **kwargs): writes.append(args[0]); return writer(*args, **kwargs)
    monkeypatch.setattr(ar, '_write_csv', written)
    with pytest.raises(RuntimeError, match='second group'):
        ar.apply_direct_visual_actual_batch(tmp_path, commands, apply_function=apply)
    assert writes == [] and snapshot(tmp_path) == original
    assert ar._prepared_actual_csv.get() is None


def test_source_csv_drift_before_flush_rolls_back_without_candidate_publication(tmp_path):
    ar.ensure_user_evidence_files(tmp_path); original = snapshot(tmp_path)
    applied = ar.apply_verified_actual_group
    def apply(*args, **kwargs):
        result = applied(*args, **kwargs)
        (tmp_path/ar.USER_CFF_FILE).write_bytes(b'unauthorized drift')
        return result
    with pytest.raises(ValueError, match='來源 CSV 已變動'):
        ar.apply_direct_visual_actual_batch(tmp_path, decisions(group('drift', '1'*64)), apply_function=apply)
    assert snapshot(tmp_path) == original
    assert ar._prepared_actual_csv.get() is None


def test_late_flush_error_restores_all_actual_csv_bytes(tmp_path, monkeypatch):
    ar.ensure_user_evidence_files(tmp_path); original = snapshot(tmp_path); write = ar._write_csv; calls=[]
    def written(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2: raise OSError('late file flush')
        return write(*args, **kwargs)
    monkeypatch.setattr(ar, '_write_csv', written)
    with pytest.raises(OSError, match='late file flush'):
        ar.apply_direct_visual_actual_batch(tmp_path, decisions(group('flush', '1'*64)))
    assert snapshot(tmp_path) == original and ar._prepared_actual_csv.get() is None


def test_ack_cannot_change_published_candidate_csv(tmp_path):
    ar.ensure_user_evidence_files(tmp_path); original = snapshot(tmp_path)
    def ack():
        (tmp_path/ar.USER_CFF_FILE).write_bytes(b'late unauthorized actual change')
        return {}
    with pytest.raises(ValueError, match='拒絕 acknowledgement'):
        ar.apply_direct_visual_actual_batch(tmp_path, decisions(group('ack', '1'*64)), acknowledge=ack)
    assert snapshot(tmp_path) == original and ar._prepared_actual_csv.get() is None


def test_legacy_known_bad_override_migration_is_staged_until_batch_flush(tmp_path, monkeypatch):
    ar.ensure_user_evidence_files(tmp_path)
    row={field: '' for field in ar.OVERRIDE_HEADERS};row.update(ar.KNOWN_BAD_LEGACY_OVERRIDE);row['source']='2026-08-20 GPT patch'
    ar._write_csv(tmp_path/ar.OCCURRENCE_OVERRIDE_FILE, ar.OVERRIDE_HEADERS, [row]);original=snapshot(tmp_path)
    applied=ar.apply_verified_actual_group
    def apply(*args, **kwargs):
        result=applied(*args, **kwargs)
        assert snapshot(tmp_path)==original
        raise RuntimeError('failed candidate after migration')
    with pytest.raises(RuntimeError, match='failed candidate'):
        ar.apply_direct_visual_actual_batch(tmp_path, decisions(group('migration','1'*64)), apply_function=apply)
    assert snapshot(tmp_path)==original and ar._prepared_actual_csv.get() is None


def test_prepared_read_preserves_native_csv_scalars_and_whitespace(tmp_path):
    ar.ensure_user_evidence_files(tmp_path)
    state = ar._PreparedActualCSV(tmp_path)
    headers = ar.OVERRIDE_HEADERS
    row = {field: "" for field in headers}
    row.update(pdf_contains="  book  ", page=0, target_char=False, stable_key=True,
               x0=None, y0=12.5, source=" leading\ntrailing ", note=" =literal ")
    state.write(tmp_path/ar.OCCURRENCE_OVERRIDE_FILE, headers, [row])
    legacy = tmp_path/'legacy.csv'
    ar._write_csv(legacy, headers, [row])
    assert state.read(tmp_path/ar.OCCURRENCE_OVERRIDE_FILE, headers) == ar._read_csv(legacy, headers)
    assert state.read(tmp_path/ar.OCCURRENCE_OVERRIDE_FILE, headers)[0]['target_char'] == 'False'
    assert state.read(tmp_path/ar.OCCURRENCE_OVERRIDE_FILE, headers)[0]['page'] == '0'


def test_candidate_rejects_foreign_root_header_nested_transaction_and_rebind(tmp_path, monkeypatch):
    root=tmp_path/'owned';foreign=tmp_path/'foreign';ar.ensure_user_evidence_files(root)
    state=ar._PreparedActualCSV(root)
    token=ar._prepared_actual_csv.set(state)
    try:
        with pytest.raises(ValueError, match='foreign root'):
            ar.apply_verified_actual_group(foreign, group('foreign','1'*64), 'ㄅ', source='visual')
        assert not foreign.exists()
        with pytest.raises(ValueError, match='header'):
            state.read(root/ar.USER_GLYF_FILE, ['wrong'])
        with pytest.raises(ValueError, match='nested'):
            ar.apply_direct_visual_actual_batch(root, decisions(group('nested','1'*64)))
        # Exercise the exact resolved-target guard without requesting native
        # Windows link creation privileges; this is not an OS symlink test.
        target=root/ar.USER_GLYF_FILE;resolve=Path.resolve
        def rebound(path, *args, **kwargs):
            return foreign/path.name if path==target else resolve(path,*args,**kwargs)
        monkeypatch.setattr(Path,'resolve',rebound)
        with pytest.raises(ValueError, match='root／file／header'):
            state.verify_current()
    finally:
        ar._prepared_actual_csv.reset(token)
    assert ar._prepared_actual_csv.get() is None


@pytest.mark.parametrize('lane', ['db', 'receipt'])
def test_fresh_target_guard_rejects_changed_db_or_receipt_before_flush(tmp_path, monkeypatch, lane):
    import standalone_proofread as sp
    import pdf_portability as pp
    from tests.test_pdf_portability import pdf, project
    monkeypatch.setenv('LOCALAPPDATA',str(tmp_path/'local'))
    source=tmp_path/'source.pdf';pdf(source,title='finite target');output=tmp_path/'project'
    manifest,db=project(output,source,session='fresh-target')
    evidence=sp.project_actual_evidence_root(output);original=snapshot(evidence)
    applied=ar.apply_verified_actual_group
    def apply(*args,**kwargs):
        result=applied(*args,**kwargs)
        if lane=='db':
            changed=copy.deepcopy(db);changed['events']['unexpected']='invalid';sp.json_save(output/'人工判定資料庫.json',changed)
        else:
            (output/pp.CONFLICT_FILE).write_text('{"unknown":"receipt drift"}',encoding='utf-8')
        return result
    with pytest.raises(ValueError):
        ar.apply_direct_visual_actual_batch(evidence, decisions(group('target','1'*64)),
            apply_function=apply,prewrite_guard=lambda: sp._validate_actual_import_target(output,manifest,db))
    assert snapshot(evidence)==original
    assert ar._prepared_actual_csv.get() is None


# Reuse the existing source-reviewed isolated acquisition fixture. Only glyph
# acquisition is synthetic; sealed workbook/project, CSV import and refresh run.
from tests.test_excel_text_storage import isolated_pipeline


def test_zip_csv_uses_one_member_index_and_retains_each_source_check(isolated_pipeline, monkeypatch, tmp_path):
    import csv
    import hashlib
    import standalone_proofread as sp
    import tests.test_excel_text_storage as fixture
    monkeypatch.setattr(fixture,'TEXTS',fixture.TEXTS[:2])
    output=isolated_pipeline('actual');manifest=sp.json_load_strict(output/'校對工作階段.json')
    rows=sp.materialize_ledger(manifest,sp.json_load_strict(output/'人工判定資料庫.json'))
    path=tmp_path/'actual.csv'
    headers=['occurrence_id','review_id','pdf_name','physical_page','char','exported_actual',
             'exported_actual_evidence_sha256','verified_actual','visual_confirmation','note']
    with path.open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=headers);writer.writeheader()
        for row in rows:
            writer.writerow({**{field:row.get(field,'') for field in headers},
                'exported_actual':row['actual'],'exported_actual_evidence_sha256':hashlib.sha256(str(row['actual_evidence']).encode()).hexdigest(),
                'verified_actual':'ㄐㄩㄝˊ','visual_confirmation':'Y','note':'=literal CSV note'})
    meta={**manifest,'bundle_schema_version':sp.GPT_DECISION_BUNDLE_SCHEMA_VERSION}
    index=ar.index_actual_group_members;build=sp.build_actual_group_for_entry;calls=[];builds=[]
    def indexed(*args,**kwargs):calls.append(1);return index(*args,**kwargs)
    def built(*args,**kwargs):
        assert kwargs.get('member_index') is not None
        builds.append(1);return build(*args,**kwargs)
    monkeypatch.setattr(ar,'index_actual_group_members',indexed);monkeypatch.setattr(sp,'build_actual_group_for_entry',built)
    result=sp.import_actual_occurrence_decisions(output,path,package_meta=meta)
    assert result[0]==2 and len(calls)==1 and len(builds)==2
    latest=sp.materialize_ledger(sp.json_load_strict(output/'校對工作階段.json'),sp.json_load_strict(output/'人工判定資料庫.json'))
    assert [row['actual'] for row in latest]==['ㄐㄩㄝˊ']*2
    assert [row['expected_set'] for row in latest]==[row['expected_set'] for row in rows]


def test_ack_has_fresh_source_guard_after_flush_before_acknowledgement(tmp_path):
    ar.ensure_user_evidence_files(tmp_path);original=snapshot(tmp_path);calls=[];acknowledged=[]
    def guard():
        calls.append(1)
        if len(calls)==4:
            # Entry, under-lock initialization, pre-flush, then fresh pre-ack.
            assert snapshot(tmp_path)!=original
            raise ValueError('source drift before acknowledgement')
    with pytest.raises(ValueError, match='before acknowledgement'):
        ar.apply_direct_visual_actual_batch(tmp_path, decisions(group('pre-ack','1'*64)),
            prewrite_guard=guard,acknowledge=lambda: acknowledged.append(1))
    assert acknowledged==[] and snapshot(tmp_path)==original
    assert ar._prepared_actual_csv.get() is None


@pytest.mark.parametrize('lane', ['metadata', 'receipt', 'pdf'])
def test_enqueue_source_drift_rejects_before_ack_and_rolls_back_actual(tmp_path, monkeypatch, lane):
    import standalone_proofread as sp
    import pdf_portability as pp
    from tests.test_pdf_portability import pdf, project
    monkeypatch.setenv('LOCALAPPDATA',str(tmp_path/'local'))
    source=tmp_path/'source.pdf';pdf(source,title='pre-ack source');output=tmp_path/'project'
    manifest,db=project(output,source,session='pre-ack target')
    evidence=sp.project_actual_evidence_root(output);original=snapshot(evidence);ack=[]
    enqueue=ar.enqueue_promotion_intents
    def injected(*args,**kwargs):
        result=enqueue(*args,**kwargs)
        if lane=='metadata':
            changed=copy.deepcopy(manifest);changed['session_id']='foreign owner/session';sp.json_save(output/'校對工作階段.json',sp.seal_manifest(changed))
        elif lane=='receipt':
            (output/pp.CONFLICT_FILE).write_text('{"unknown":"foreign receipt"}',encoding='utf-8')
        else:
            source.write_bytes(source.read_bytes()+b'changed source PDF')
        return result
    monkeypatch.setattr(ar,'enqueue_promotion_intents',injected)
    with pytest.raises((ValueError,FileNotFoundError)):
        ar.apply_direct_visual_actual_batch(evidence,decisions(group('enqueue','1'*64)),
            prewrite_guard=lambda:sp._validate_actual_import_target(output,manifest,db),
            acknowledge=lambda:ack.append('must not run'))
    assert ack==[] and snapshot(evidence)==original
    assert ar._prepared_actual_csv.get() is None
