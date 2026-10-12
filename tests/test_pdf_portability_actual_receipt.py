"""Actual Excel conflict receipts bind their candidate and CAS to one snapshot."""
import copy
import inspect
from pathlib import Path

import pytest
import pdf_portability as p
import standalone_proofread as sp
from tests.test_pdf_portability_binding import actual_projects, filled_actual, read, watched


def conflict_import(target, workbook):
    with pytest.raises(ValueError):
        sp.import_actual_gpt_decisions(target, workbook)


def inputs(a, b, tmp_path):
    first = filled_actual(a, tmp_path / 'first.xlsx')
    second = filled_actual(a, tmp_path / 'second.xlsx', reading='ㄐㄩㄝˇ')
    third = filled_actual(a, tmp_path / 'third.xlsx', reading='ㄐㄩㄝˋ')
    sp.import_actual_gpt_decisions(b, first)
    return second, third


@pytest.mark.parametrize('scenario', ['absent', 'present_swap_restore'])
def test_actual_conflict_receipt_snapshot_cas_preserves_other_sources(
        actual_projects, tmp_path, monkeypatch, scenario):
    a, b, _ = actual_projects
    second, third = inputs(a, b, tmp_path)
    receipt = b / p.ACTUAL_EXCEL_CONFLICT_FILE
    if scenario == 'present_swap_restore':
        conflict_import(b, second)
    before = {path: read(path) for path in watched(b)}
    original_save, original_validate = sp.json_save, p._validated_actual_excel_receipt
    observed = {}

    def foreign(document):
        replacement = copy.deepcopy(document)
        replacement['conflicts'][0]['source_record']['source_excel_sha256'] = 'e' * 64
        return p._sealed_actual_excel_conflicts(replacement['conflicts'])

    def inject_absent(path, value, **kwargs):
        if Path(path) == receipt and not observed:
            replacement = foreign(value)
            original_save(receipt, replacement, expected_sha256='')
            assert original_validate(receipt) == replacement
            observed['foreign'] = receipt.read_bytes()
        return original_save(path, value, **kwargs)

    def swap_present(path, *args, **kwargs):
        caller = inspect.currentframe().f_back
        if (Path(path) == receipt and caller.f_code.co_name == 'import_actual_excel'
                and caller.f_locals.get('conflict_records') and not observed):
            original_save(receipt, foreign(original_validate(receipt)))
            consumed = original_validate(path, *args, **kwargs)
            receipt.write_bytes(before[receipt])
            observed['restored'] = True
            return consumed
        return original_validate(path, *args, **kwargs)

    if scenario == 'absent':
        monkeypatch.setattr(sp, 'json_save', inject_absent)
    else:
        monkeypatch.setattr(p, '_validated_actual_excel_receipt', swap_present)
    conflict_import(b, second if scenario == 'absent' else third)
    assert observed, 'the intended receipt boundary must be injected'
    if scenario == 'absent':
        before[receipt] = observed['foreign']
    assert {path: read(path) for path in watched(b)} == before
    assert not (b / p.INCOMPLETE_FILE).exists()


def test_actual_conflict_receipt_cas_failure_preserves_late_status_and_receipt(
        actual_projects, tmp_path, monkeypatch):
    a, b, _ = actual_projects
    second, _ = inputs(a, b, tmp_path)
    receipt = b / p.ACTUAL_EXCEL_CONFLICT_FILE
    status = b / 'pipeline_status.json'
    before = {path: read(path) for path in watched(b)}
    assert not receipt.exists()
    original_save = sp.json_save
    observed = {}

    def inject_late_writer(path, value, **kwargs):
        if Path(path) == receipt and not observed:
            foreign = copy.deepcopy(value)
            foreign['conflicts'][0]['source_record']['source_excel_sha256'] = 'e' * 64
            original_save(receipt, p._sealed_actual_excel_conflicts(foreign['conflicts']),
                          expected_sha256='')
            foreign_status = sp.json_load_strict(status)
            foreign_status['foreign_writer'] = 'after-actual-import-status-write'
            original_save(status, foreign_status)
            observed['receipt'] = receipt.read_bytes()
            observed['status'] = status.read_bytes()
        return original_save(path, value, **kwargs)

    monkeypatch.setattr(sp, 'json_save', inject_late_writer)
    conflict_import(b, second)
    assert observed, 'the receipt CAS boundary must be reached'
    assert receipt.read_bytes() == observed['receipt']
    assert status.read_bytes() == observed['status']
    assert status.read_bytes() != before[status]
    assert before[receipt] is None
    assert {path: read(path) for path in before if path not in {status, receipt}} == {
        path: content for path, content in before.items()
        if path not in {status, receipt}}
    assert not (b / p.INCOMPLETE_FILE).exists()


def test_receipt_cas_failure_does_not_enter_nonatomic_status_restore(
        actual_projects, tmp_path, monkeypatch):
    a, b, _ = actual_projects
    second, _ = inputs(a, b, tmp_path)
    receipt = b / p.ACTUAL_EXCEL_CONFLICT_FILE
    status = b / 'pipeline_status.json'
    before = {path: read(path) for path in watched(b)}
    original_save = sp.json_save
    original_restore = p._restore_exact_file
    observed = {}

    def inject_foreign_receipt(path, value, **kwargs):
        if Path(path) == receipt and 'receipt' not in observed:
            foreign = copy.deepcopy(value)
            foreign['conflicts'][0]['source_record']['source_excel_sha256'] = 'e' * 64
            original_save(receipt, p._sealed_actual_excel_conflicts(foreign['conflicts']),
                          expected_sha256='')
            observed['receipt'] = receipt.read_bytes()
        return original_save(path, value, **kwargs)

    def inject_at_old_restore_window(path, original):
        if Path(path) == status:
            foreign_status = sp.json_load_strict(status)
            foreign_status['foreign_writer'] = 'after-status-hash-before-restore'
            original_save(status, foreign_status)
            observed['foreign_status'] = status.read_bytes()
        return original_restore(path, original)

    monkeypatch.setattr(sp, 'json_save', inject_foreign_receipt)
    monkeypatch.setattr(p, '_restore_exact_file', inject_at_old_restore_window)
    conflict_import(b, second)
    assert receipt.read_bytes() == observed['receipt']
    if 'foreign_status' in observed:
        assert status.read_bytes() == observed['foreign_status']
    else:
        assert status.read_bytes() == before[status]
    assert {path: read(path) for path in before if path not in {receipt, status}} == {
        path: content for path, content in before.items()
        if path not in {receipt, status}}
    assert not (b / p.INCOMPLETE_FILE).exists()


def test_status_write_failure_keeps_durable_conflict_receipt_and_blocks_report(
        actual_projects, tmp_path, monkeypatch):
    a, b, _ = actual_projects
    second, _ = inputs(a, b, tmp_path)
    receipt = b / p.ACTUAL_EXCEL_CONFLICT_FILE
    status = b / 'pipeline_status.json'
    before = {path: read(path) for path in watched(b)}
    original_save = sp.json_save
    observed = {}

    def fail_late_status(path, value, **kwargs):
        if Path(path) == status and receipt.exists():
            observed['receipt'] = receipt.read_bytes()
            raise OSError('status disk full')
        return original_save(path, value, **kwargs)

    monkeypatch.setattr(sp, 'json_save', fail_late_status)
    with pytest.raises(OSError, match='status disk full'):
        sp.import_actual_gpt_decisions(b, second)
    assert observed, 'receipt must be durable before the status write'
    assert receipt.read_bytes() == observed['receipt']
    assert status.read_bytes() == before[status]
    assert {path: read(path) for path in before if path not in {receipt, status}} == {
        path: content for path, content in before.items()
        if path not in {receipt, status}}
    assert not (b / p.INCOMPLETE_FILE).exists()
    manifest = sp.json_load_strict(b / '校對工作階段.json')
    db = sp.json_load_strict(b / '人工判定資料庫.json')
    assert p.actual_excel_conflict_state(b, manifest, db)
    with pytest.raises(ValueError, match='actual.*衝突'):
        sp.regenerate_report(b)
    assert read(b / '注音校對_最終報告.xlsx') == before[b / '注音校對_最終報告.xlsx']


def test_actual_conflict_receipt_first_append_and_replay(actual_projects, tmp_path):
    a, b, _ = actual_projects
    second, third = inputs(a, b, tmp_path)
    receipt = b / p.ACTUAL_EXCEL_CONFLICT_FILE
    stable = {path: read(path) for path in watched(b)
              if path not in {receipt, b / 'pipeline_status.json'}}
    assert not receipt.exists()
    conflict_import(b, second)
    initial = p._validated_actual_excel_receipt(receipt)
    assert len(initial['conflicts']) == 1
    conflict_import(b, third)
    appended = p._validated_actual_excel_receipt(receipt)
    assert len(appended['conflicts']) == 2
    assert appended['conflicts'][0] == initial['conflicts'][0]
    assert {item['source_record']['source_excel_sha256'] for item in appended['conflicts']} == {
        p._sha(second), p._sha(third)}
    assert {path: read(path) for path in stable} == stable
    before = {path: read(path) for path in watched(b)}
    conflict_import(b, second)
    assert {path: read(path) for path in watched(b)} == before


@pytest.mark.parametrize('damage', ['duplicate_json_key', 'integrity'])
def test_actual_conflict_receipt_invalid_input_preserves_bytes(
        actual_projects, tmp_path, damage):
    a, b, _ = actual_projects
    second, third = inputs(a, b, tmp_path)
    conflict_import(b, second)
    receipt = b / p.ACTUAL_EXCEL_CONFLICT_FILE
    if damage == 'duplicate_json_key':
        receipt.write_bytes(b'{"version":1,' + receipt.read_bytes()[1:])
    else:
        value = sp.json_load_strict(receipt)
        value['integrity_sha256'] = '0' * 64
        sp.json_save(receipt, value)
    before = {path: read(path) for path in watched(b)}
    conflict_import(b, third)
    assert {path: read(path) for path in watched(b)} == before
    assert not (b / p.INCOMPLETE_FILE).exists()
