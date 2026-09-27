"""Round 23: bind consumed snapshots and ownership at the final write boundary."""
import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest
import pdf_portability as p
import standalone_proofread as sp
from tests.test_pdf_portability import pdf, project, _filled_expected_excel
from tests.test_pdf_portability_presentation import real_project, read


@pytest.fixture
def pair(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'isolated'))
    ap, bp = tmp_path / 'a.pdf', tmp_path / 'b.pdf'
    pdf(ap, title='A'); pdf(bp, title='B')
    a, b = tmp_path / 'a', tmp_path / 'b'
    project(a, ap, session='A', event_positions=(0,))
    bm, _ = project(b, bp, session='B')
    monkeypatch.setattr(p, '_publish_portable_outputs', lambda *args: b / 'report.xlsx')
    return a, b, bp, bm


def state(root):
    return {name: read(root / name) for name in (
        '校對工作階段.json', '人工判定資料庫.json', '待人工確認.json',
        '注音校對_最終報告.xlsx', p.CONFLICT_FILE, p.PENDING_ACTUAL_FILE)}


@pytest.mark.parametrize('entry', ['direct', 'dry_run', 'auto'])
def test_expected_rejects_known_changed_pdf_after_excel_read(pair, tmp_path, monkeypatch, entry):
    a, b, bp, _ = pair
    x = _filled_expected_excel(b, tmp_path / 'filled.xlsx')
    before = state(b)
    changed = tmp_path / 'changed.pdf'; pdf(changed, title='C', text='different lesson')
    original = sp.workbook_rows
    def swap(*args, **kwargs):
        result = original(*args, **kwargs)
        bp.write_bytes(changed.read_bytes())
        return result
    monkeypatch.setattr(sp, 'workbook_rows', swap)
    with pytest.raises((ValueError, FileNotFoundError)):
        if entry == 'auto':
            monkeypatch.setattr('sys.argv', ['standalone_proofread.py', '--import-gpt-auto', str(x), '-o', str(b)])
            sp.main()
        else:
            sp.import_gpt_decisions(b, x, dry_run=entry == 'dry_run')
    assert state(b) == before
    assert not (b / p.INCOMPLETE_FILE).exists()


@pytest.mark.parametrize('kind', ['manifest', 'db_other', 'db_same'])
def test_expected_snapshot_does_not_adopt_later_preimage(pair, tmp_path, monkeypatch, kind):
    a, b, bp, bm = pair
    x = _filled_expected_excel(b, tmp_path / 'filled.xlsx')
    path = b / ('校對工作階段.json' if kind == 'manifest' else '人工判定資料庫.json')
    replacement = sp.json_load_strict(path)
    if kind == 'manifest':
        replacement['session_id'] = 'FOREIGN'; sp.seal_manifest(replacement)
    else:
        rid = bm['records'][1 if kind == 'db_other' else 0]['review_id']
        replacement['events'][rid] = {'action': '確認非校對範圍', 'exclusion_reason': 'NEW_LOCAL_VERDICT', 'exclusion_evidence': 'local page 1'}
    replacement_bytes = json.dumps(replacement, ensure_ascii=False).encode('utf-8')
    before = state(b); before[path.name] = replacement_bytes
    original_text, original_bytes = Path.read_text, Path.read_bytes
    injected = False
    def inject(self, method, *args, **kwargs):
        nonlocal injected
        result = method(self, *args, **kwargs)
        if self == path and not injected:
            injected = True
            self.write_bytes(replacement_bytes)
        return result
    monkeypatch.setattr(Path, 'read_text', lambda self, *args, **kw: inject(self, original_text, *args, **kw))
    monkeypatch.setattr(Path, 'read_bytes', lambda self, *args, **kw: inject(self, original_bytes, *args, **kw))
    with pytest.raises(ValueError):
        sp.import_gpt_decisions(b, x)
    assert injected
    assert state(b) == before
    assert not (b / p.INCOMPLETE_FILE).exists()


@pytest.mark.parametrize('lane', ['same_expected', 'project', 'project_conflict'])
def test_foreign_owner_after_marker_creation_prevents_db_and_receipts(pair, tmp_path, monkeypatch, lane):
    a, b, bp, bm = pair
    x = _filled_expected_excel(b, tmp_path / 'filled.xlsx')
    if lane == 'project_conflict':
        db = sp.json_load_strict(b / '人工判定資料庫.json')
        db['events'][bm['records'][0]['review_id']] = {
            'action': '確認非校對範圍', 'exclusion_reason': 'local verdict', 'exclusion_evidence': 'page 1'}
        sp.json_save(b / '人工判定資料庫.json', db)
    before = state(b); marker = b / p.INCOMPLETE_FILE
    foreign = b'{"owner":"foreign after creation"}'
    original = sp.json_save
    injected = False
    def takeover(path, value, **kwargs):
        nonlocal injected
        result = original(path, value, **kwargs)
        if Path(path) == marker and not injected:
            injected = True; marker.write_bytes(foreign)
        return result
    monkeypatch.setattr(sp, 'json_save', takeover)
    with pytest.raises(ValueError):
        sp.import_gpt_decisions(b, x) if lane == 'same_expected' else p.import_project_decisions(a, b)
    assert injected and marker.read_bytes() == foreign
    assert state(b) == before


@pytest.mark.parametrize('moment', ['creation', 'after_import', 'final_plan'])
def test_merge_never_adopts_foreign_marker(pair, tmp_path, monkeypatch, moment):
    a, b, bp, _ = pair
    target = tmp_path / 'new'; marker = target / p.INCOMPLETE_FILE
    foreign = b'{"owner":"foreign merge"}'
    original_import, original_save, original_plan = p.import_project_decisions, sp.json_save, p._new_presentation_marker
    injected = False
    def build(paths, output, **kwargs):
        project(output, paths[0], session='NEW')
    def takeover():
        nonlocal injected
        injected = True; marker.write_bytes(foreign)
    def save(path, value, **kwargs):
        result = original_save(path, value, **kwargs)
        if moment == 'creation' and Path(path) == marker and not injected: takeover()
        return result
    def importing(*args, **kwargs):
        result = original_import(*args, **kwargs)
        if moment == 'after_import': takeover()
        return result
    def plan(*args, **kwargs):
        result = original_plan(*args, **kwargs)
        if moment == 'final_plan' and kwargs.get('phase') == 'NEW_PROJECT_FINAL': takeover()
        return result
    monkeypatch.setattr(sp, 'run_pipeline_pdfs', build)
    monkeypatch.setattr(sp, 'json_save', save)
    monkeypatch.setattr(p, 'import_project_decisions', importing)
    monkeypatch.setattr(p, '_new_presentation_marker', plan)
    with pytest.raises(ValueError): p.continue_project(a, bp, target)
    assert injected and marker.read_bytes() == foreign


@pytest.mark.parametrize('condition', ['marker', 'transaction', 'conflict'])
def test_project_revalidates_source_before_commit(pair, monkeypatch, condition):
    a, b, bp, _ = pair
    before = state(b)
    original = p._mapped_occurrence_overrides
    injected = False
    def change(*args, **kwargs):
        nonlocal injected
        result = original(*args, **kwargs)
        if not injected:
            injected = True
            if condition == 'marker': sp.json_save(a / p.INCOMPLETE_FILE, {'owner': 'late source'})
            elif condition == 'transaction':
                from global_glyph_promotion import PROJECT_TRANSACTION_FILE
                sp.json_save(sp.project_actual_evidence_root(a) / PROJECT_TRANSACTION_FILE, {'status': 'PREPARED'})
            else:
                am = sp.json_load_strict(a / '校對工作階段.json')
                conflict = {'target_review_id': am['records'][1]['review_id'],
                            'source_event': {}, 'target_event': {},
                            'source_pdf_sha256': 'a' * 64, 'target_pdf_sha256': am['records'][1]['pdf_sha256']}
                sp.json_save(a / p.CONFLICT_FILE, {'version': 1, 'conflicts': [conflict]})
                assert p.validate_conflict_state(a, am, sp.json_load_strict(a / '人工判定資料庫.json')) == [conflict['target_review_id']]
        return result
    monkeypatch.setattr(p, '_mapped_occurrence_overrides', change)
    with pytest.raises(ValueError): p.import_project_decisions(a, b)
    assert injected and state(b) == before
    assert not (b / p.INCOMPLETE_FILE).exists()


@pytest.mark.parametrize('operation', ['regenerate_report', 'repair_project_state'])
@pytest.mark.parametrize('entry', ['direct', 'cli'])
def test_report_repair_rechecks_marker_after_collect(tmp_path, monkeypatch, operation, entry):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'isolated'))
    b = real_project(tmp_path / 'real')
    before = state(b); marker = b / p.INCOMPLETE_FILE
    foreign = b'{"owner":"late report"}'
    original = sp.collect_manifest
    injected = False
    def change(*args, **kwargs):
        nonlocal injected
        result = original(*args, **kwargs)
        if not injected: injected = True; marker.write_bytes(foreign)
        return result
    monkeypatch.setattr(sp, 'collect_manifest', change)
    monkeypatch.setattr(sp, 'actual_workbook_global_exact_dependencies', lambda *args: ())
    monkeypatch.setattr('check_pronunciation_candidates.actual_workbook_global_exact_dependencies', lambda *args: ())
    with pytest.raises(ValueError):
        if entry == 'cli':
            flag = '--report-only' if operation == 'regenerate_report' else '--repair-project'
            monkeypatch.setattr('sys.argv', ['standalone_proofread.py', flag, '-o', str(b)])
            sp.main()
        else:
            getattr(sp, operation)(b)
    assert injected and marker.read_bytes() == foreign
    assert state(b) == before

@pytest.mark.parametrize('location', ['offline', 'relocated'])
def test_expected_keeps_legitimate_offline_and_relocated_pdf(pair, tmp_path, location):
    a, b, bp, _ = pair
    x = _filled_expected_excel(b, tmp_path / 'filled.xlsx')
    if location == 'offline': bp.unlink()
    else:
        relocated = tmp_path / 'moved'; relocated.mkdir()
        bp.rename(relocated / bp.name)
    assert sp.import_gpt_decisions(b, x)[0] == 1
    assert not (b / p.INCOMPLETE_FILE).exists()


def test_expected_snapshot_restored_original_bytes_is_safe(pair, tmp_path, monkeypatch):
    a, b, bp, _ = pair
    x = _filled_expected_excel(b, tmp_path / 'filled.xlsx')
    path = b / '人工判定資料庫.json'; raw = path.read_bytes()
    original = Path.read_bytes
    injected = False
    def transient(self):
        nonlocal injected
        result = original(self)
        if self == path and not injected:
            injected = True
            self.write_bytes(b'{"events":{}}')
            self.write_bytes(raw)
        return result
    monkeypatch.setattr(Path, 'read_bytes', transient)
    assert sp.import_gpt_decisions(b, x)[0] == 1
    assert injected and not (b / p.INCOMPLETE_FILE).exists()


@pytest.mark.parametrize('operation', ['regenerate_report', 'repair_project_state'])
def test_report_repair_rechecks_late_conflict(tmp_path, monkeypatch, operation):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'isolated'))
    b = real_project(tmp_path / 'real')
    before = state(b)
    original = sp.collect_manifest
    manifest = sp.json_load_strict(b / '校對工作階段.json')
    row = manifest['records'][0]
    conflict = {'target_review_id': row['review_id'], 'source_event': {}, 'target_event': {},
                'source_pdf_sha256': 'a' * 64, 'target_pdf_sha256': row['pdf_sha256']}
    foreign = json.dumps({'version': 1, 'conflicts': [conflict]}).encode('utf-8')
    def change(*args, **kwargs):
        result = original(*args, **kwargs)
        (b / p.CONFLICT_FILE).write_bytes(foreign)
        return result
    monkeypatch.setattr(sp, 'collect_manifest', change)
    monkeypatch.setattr(sp, 'actual_workbook_global_exact_dependencies', lambda *args: ())
    monkeypatch.setattr('check_pronunciation_candidates.actual_workbook_global_exact_dependencies', lambda *args: ())
    with pytest.raises(ValueError): getattr(sp, operation)(b)
    before[p.CONFLICT_FILE] = foreign
    assert state(b) == before

@pytest.mark.parametrize('lane', ['same_expected', 'project', 'merge'])
def test_publication_handoff_cannot_adopt_another_valid_owner(pair, tmp_path, monkeypatch, lane):
    a, b, bp, _ = pair
    x = _filled_expected_excel(b, tmp_path / 'filled.xlsx')
    target = tmp_path / 'new' if lane == 'merge' else b
    original = p.resume_portable_project
    injected = False
    foreign_bytes = None
    def takeover(output, *args, **kwargs):
        nonlocal injected, foreign_bytes
        marker = output / p.INCOMPLETE_FILE
        foreign = sp.json_load_strict(marker)
        foreign['owner_token'] = 'f' * 32
        foreign['plan_integrity_sha256'] = p._document_digest({k: v for k, v in foreign.items() if k != 'plan_integrity_sha256'})
        sp.json_save(marker, foreign)
        foreign_bytes = marker.read_bytes(); injected = True
        return original(output, *args, **kwargs)
    def build(paths, output, **kwargs): project(output, paths[0], session='NEW')
    monkeypatch.setattr(p, 'resume_portable_project', takeover)
    monkeypatch.setattr(sp, 'run_pipeline_pdfs', build)
    with pytest.raises(ValueError):
        if lane == 'same_expected': sp.import_gpt_decisions(b, x)
        elif lane == 'project': p.import_project_decisions(a, b)
        else: p.continue_project(a, bp, target)
    assert injected and (target / p.INCOMPLETE_FILE).read_bytes() == foreign_bytes

@pytest.mark.parametrize('lane', ['project', 'same_expected', 'cross_expected'])
def test_target_late_journal_prevents_all_presentation_writes(pair, tmp_path, monkeypatch, lane):
    from global_glyph_promotion import PROJECT_TRANSACTION_FILE
    a, b, bp, _ = pair
    x = _filled_expected_excel(b if lane == 'same_expected' else a, tmp_path / 'journal.xlsx')
    journal = sp.project_actual_evidence_root(b) / PROJECT_TRANSACTION_FILE
    foreign = b'{"status":"PREPARED","owner":"foreign late journal"}'
    before = state(b)
    hook = '_new_presentation_marker'
    module = p
    original = getattr(module, hook)
    injected = False
    def inject(*args, **kwargs):
        nonlocal injected
        if not injected:
            injected = True; journal.write_bytes(foreign)
        return original(*args, **kwargs)
    monkeypatch.setattr(module, hook, inject)
    original_save = sp.json_save
    writes = []
    def save(path, value, **kwargs):
        if Path(path).parent == b:
            writes.append(Path(path).name)
        return original_save(path, value, **kwargs)
    monkeypatch.setattr(sp, 'json_save', save)
    with pytest.raises(ValueError):
        p.import_project_decisions(a, b) if lane == 'project' else sp.import_gpt_decisions(b, x)
    assert injected and journal.read_bytes() == foreign
    assert state(b) == before
    assert not (b / p.INCOMPLETE_FILE).exists()
    assert writes == []


def test_receipt_backup_and_digest_use_one_read(pair, monkeypatch):
    import base64
    a, b, bp, _ = pair
    receipt = b / p.PENDING_ACTUAL_FILE
    first = b'{"status":"RECHECK_LOCAL_PDF_REQUIRED","sources":[]}'
    foreign = b'{"status":"RECHECK_LOCAL_PDF_REQUIRED","sources":[],"new_record":"foreign"}'
    receipt.write_bytes(first)
    original = Path.read_bytes
    injected = False
    def swap(path):
        nonlocal injected
        raw = original(path)
        if path == receipt and not injected:
            injected = True; receipt.write_bytes(foreign)
        return raw
    monkeypatch.setattr(Path, 'read_bytes', swap)
    try:
        plan = p._presentation_receipt_plan(b, {})[p.PENDING_ACTUAL_FILE]
    except ValueError:
        pass
    else:
        assert p._document_digest(json.loads(base64.b64decode(plan['before_bytes']))) == plan['before_digest']
    assert injected and receipt.read_bytes() == foreign


def test_receipt_snapshot_still_rejects_duplicate_json_keys(pair):
    a, b, bp, _ = pair
    (b / p.PENDING_ACTUAL_FILE).write_bytes(b'{"sources":[],"sources":[1]}')
    with pytest.raises(ValueError, match='JSON|重複'):
        p._presentation_receipt_plan(b, {})


@pytest.mark.parametrize('lane', ['project', 'cross_expected'])
@pytest.mark.parametrize('moment', ['before_plan', 'after_plan', 'receipt_write'])
def test_receipt_candidate_cannot_lose_later_record(pair, tmp_path, monkeypatch, lane, moment):
    a, b, bp, bm = pair
    if lane == 'cross_expected':
        # Ensure the filled workbook and the local conflicting verdict address
        # the same occurrence; source event 0 would otherwise hide that row.
        sp.json_save(a / '人工判定資料庫.json', sp.normalize_db({}))
    x = _filled_expected_excel(a, tmp_path / 'conflicting.xlsx')
    dbpath = b / '人工判定資料庫.json'
    db = sp.json_load_strict(dbpath)
    db['events'][bm['records'][0]['review_id']] = {
        'action': '確認非校對範圍', 'exclusion_reason': 'local verdict', 'exclusion_evidence': 'page 1'}
    sp.json_save(dbpath, db)
    before = state(b)
    receipt = b / p.CONFLICT_FILE
    foreign = json.dumps({'version': 1, 'conflicts': [{
        'target_review_id': bm['records'][1]['review_id'], 'source_event': {'note': 'NEW RECORD'},
        'target_event': {}, 'source_pdf_sha256': 'a' * 64,
        'target_pdf_sha256': bm['records'][1]['pdf_sha256']}]}).encode('utf-8')
    original_plan, original_save = p._new_presentation_marker, sp.json_save
    injected = False
    def swap():
        nonlocal injected
        injected = True; receipt.write_bytes(foreign)
    def plan(*args, **kwargs):
        if moment == 'before_plan': swap()
        result = original_plan(*args, **kwargs)
        if moment == 'after_plan': swap()
        return result
    def save(path, data, **kwargs):
        if Path(path) == receipt and moment == 'receipt_write' and not injected: swap()
        return original_save(path, data, **kwargs)
    monkeypatch.setattr(p, '_new_presentation_marker', plan)
    monkeypatch.setattr(sp, 'json_save', save)
    with pytest.raises(ValueError):
        p.import_project_decisions(a, b) if lane == 'project' else sp.import_gpt_decisions(b, x)
    assert injected and receipt.read_bytes() == foreign
    before[p.CONFLICT_FILE] = foreign
    assert state(b) == before


def test_mapped_pdf_renders_once_then_checks_verified_target_bytes(pair, monkeypatch):
    a, b, bp, bm = pair
    am = sp.json_load_strict(a / '校對工作階段.json')
    proof = p._load_proof(a, am)
    assert am['pdfs'][0]['pdf_sha256'] != bm['pdfs'][0]['pdf_sha256']
    original = p._pdf_content_snapshot
    calls = []
    def observe(path, **kwargs):
        calls.append(Path(path)); return original(path, **kwargs)
    monkeypatch.setattr(p, '_pdf_content_snapshot', observe)
    mapping, geometry = p._match_pdfs(proof, am, bm, b)
    p._verify_mapped_target_pdfs(b, bm, mapping, geometry)
    p._verify_mapped_target_pdfs(b, bm, mapping, geometry)
    assert calls == [bp]
