from pathlib import Path
import json
import os
import subprocess
import sys
import shutil

import fitz
import pytest

import standalone_proofread as sp
import global_glyph_promotion as promotion


def sealed_project(tmp_path):
    output = tmp_path / 'project'
    output.mkdir()
    from actual_review import ensure_user_evidence_files
    ensure_user_evidence_files(sp.project_actual_evidence_root(output))
    pdf = tmp_path / 'source.pdf'
    document = fitz.open()
    document.new_page()
    document.save(pdf)
    document.close()
    info = {'pdf': str(pdf), 'pdf_name': pdf.name, 'pdf_sha256': sp.sha256_file(pdf)}
    for kind, directory in [('actual', '01_實際注音'), ('candidate', '02_候選報告')]:
        path = output / directory / (kind + '.xlsx')
        path.parent.mkdir()
        path.write_bytes(('sealed-' + kind).encode())
        info[kind + '_workbook'] = str(path)
        info[kind + '_workbook_sha256'] = sp.sha256_file(path)
    manifest = sp.seal_manifest({'session_id': 'refresh-fixture', 'pdfs': [info], 'records': []})
    sp.json_save(output / '校對工作階段.json', manifest)
    sp.json_save(output / '人工判定資料庫.json', sp.normalize_db({}))
    return output, manifest


def test_refresh_stage_failure_preserves_sealed_outputs(tmp_path, monkeypatch):
    output, manifest = sealed_project(tmp_path)
    before = {path: path.read_bytes() for path in output.rglob('*') if path.is_file()}

    def fail_after_write(pdfs, destination, **kwargs):
        staged = sp.json_load_strict(Path(destination) / '校對工作階段.json')
        Path(staged['pdfs'][0]['actual_workbook']).write_bytes(b'partial-new-actual')
        raise RuntimeError('AFTER_FIRST_ACTUAL_WRITE')

    monkeypatch.setattr(sp, 'run_pipeline_pdfs', fail_after_write)
    with pytest.raises(RuntimeError, match='AFTER_FIRST_ACTUAL_WRITE'):
        sp.refresh_actual_project(output)
    assert {path: path.read_bytes() for path in before} == before
    sp.validate_output_artifact_hashes(manifest)


def successful_stage(pdfs, destination, **kwargs):
    """Small byte producer; real manifest/hash/DB/publication boundaries remain."""
    destination = Path(destination)
    manifest = sp.json_load_strict(destination / '校對工作階段.json')
    for info in manifest['pdfs']:
        path = Path(info['actual_workbook'])
        path.write_bytes(b'validated-new-actual')
        info['actual_workbook_sha256'] = sp.sha256_file(path)
    sp.json_save(destination / '校對工作階段.json', sp.seal_manifest(manifest))
    sp.json_save(destination / 'pipeline_status.json', {'excel_report_deferred': True, 'status': 'PROCESSING_FINISHED'})
    sp.save_pending_json(destination, manifest, sp.load_or_initialize_db(destination))
    return destination / '注音校對_最終報告.xlsx'


def commit_empty_plan(output):
    with promotion.direct_visual_project_transaction(sp.project_actual_evidence_root(output)) as bind:
        bind({'schema_version': '1.0', 'affected_occurrence_ids': [], 'checked_postconditions': []})
    return promotion.project_refresh_token(sp.project_actual_evidence_root(output))


def _refresh_retained_bytes(output):
    files = {p: p.read_bytes() for p in output.rglob('*') if p.is_file()}
    journal_path = output / sp.ACTUAL_REFRESH_PUBLICATION
    if journal_path.exists():
        stage = output.parent / sp.json_load_strict(journal_path)['stage']
        files.update({p: p.read_bytes() for p in stage.rglob('*') if p.is_file()})
    return files


def _mutate_refresh_pdf(manifest, mutation):
    pdf = Path(manifest['pdfs'][0]['pdf'])
    if mutation == 'missing':
        pdf.unlink()
    elif mutation == 'same_metadata':
        metadata = pdf.stat()
        raw = pdf.read_bytes()
        pdf.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
        os.utime(pdf, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
        assert pdf.stat().st_size == metadata.st_size
        assert pdf.stat().st_mtime_ns == metadata.st_mtime_ns
        assert sp.sha256_file(pdf) != manifest['pdfs'][0]['pdf_sha256']
    else:
        pdf.write_bytes(b'changed PDF after sealed refresh')


@pytest.mark.parametrize('phase', ['PUBLISHED', 'ACKNOWLEDGING', 'ACKNOWLEDGED'])
@pytest.mark.parametrize('mutation', ['missing', 'changed', 'same_metadata', 'unreadable'])
def test_refresh_restart_requires_sealed_pdf_before_token_or_cleanup(tmp_path, monkeypatch, phase, mutation):
    output, manifest = sealed_project(tmp_path)
    token = commit_empty_plan(output)
    monkeypatch.setattr(sp, 'run_pipeline_pdfs', successful_stage)
    sp.refresh_actual_project(output)
    journal_path = output / sp.ACTUAL_REFRESH_PUBLICATION
    if phase != 'PUBLISHED':
        acknowledge = sp.acknowledge_project_refresh
        unlink = Path.unlink
        if phase == 'ACKNOWLEDGING':
            def interrupt_ack(*args):
                raise OSError('before token consumption')
            monkeypatch.setattr(sp, 'acknowledge_project_refresh', interrupt_ack)
        else:
            def interrupt_cleanup(self, *args, **kwargs):
                if self == journal_path:
                    raise OSError('after token consumption')
                return unlink(self, *args, **kwargs)
            monkeypatch.setattr(Path, 'unlink', interrupt_cleanup)
        with pytest.raises(OSError, match='token consumption'):
            sp._acknowledge_actual_refresh(output, token)
        monkeypatch.setattr(sp, 'acknowledge_project_refresh', acknowledge)
        monkeypatch.setattr(Path, 'unlink', unlink)
        assert sp.json_load_strict(journal_path)['phase'] == 'ACKNOWLEDGING'
    if mutation == 'unreadable':
        sha = sp.sha256_file
        pdf = Path(manifest['pdfs'][0]['pdf'])
        def unreadable_pdf(path):
            if Path(path).resolve() == pdf.resolve():
                raise PermissionError('PDF bytes unreadable')
            return sha(path)
        monkeypatch.setattr(sp, 'sha256_file', unreadable_pdf)
    else:
        _mutate_refresh_pdf(manifest, mutation)
    before = _refresh_retained_bytes(output)
    monkeypatch.setattr(sp, 'run_pipeline_pdfs', lambda *a, **kw: pytest.fail('duplicate rebuild'))
    monkeypatch.setattr(sp, '_publish_actual_refresh', lambda *a, **kw: pytest.fail('duplicate publish'))
    with pytest.raises(FileNotFoundError, match='PDF'):
        sp.refresh_actual_with_recovery(output)
    assert _refresh_retained_bytes(output) == before
    assert promotion.project_refresh_token(sp.project_actual_evidence_root(output)) == (None if phase == 'ACKNOWLEDGED' else token)


@pytest.mark.parametrize('boundary', ['before_publish', 'before_published_mark', 'before_ack', 'after_ack_intent'])
@pytest.mark.parametrize('mutation', ['missing', 'changed', 'same_metadata'])
def test_refresh_final_boundaries_reject_pdf_drift(tmp_path, monkeypatch, boundary, mutation):
    output, manifest = sealed_project(tmp_path)
    token = commit_empty_plan(output)
    old = _refresh_retained_bytes(output)
    monkeypatch.setattr(sp, 'run_pipeline_pdfs', successful_stage)
    if boundary == 'before_publish':
        publish = sp._publish_actual_refresh
        def mutate_then_publish(*args, **kwargs):
            _mutate_refresh_pdf(manifest, mutation)
            return publish(*args, **kwargs)
        monkeypatch.setattr(sp, '_publish_actual_refresh', mutate_then_publish)
    elif boundary == 'before_published_mark':
        verify = sp._verify_manual_actual_batch_postconditions
        def mutate_after_postconditions(*args, **kwargs):
            result = verify(*args, **kwargs)
            # The publication check follows the native target renames.
            if (output / sp.ACTUAL_REFRESH_PUBLICATION).exists():
                _mutate_refresh_pdf(manifest, mutation)
            return result
        monkeypatch.setattr(sp, '_verify_manual_actual_batch_postconditions', mutate_after_postconditions)
    else:
        sp.refresh_actual_project(output)
        if boundary == 'before_ack':
            _mutate_refresh_pdf(manifest, mutation)
        else:
            save = sp.json_save
            def mutate_after_ack_intent(path, value, *args, **kwargs):
                result = save(path, value, *args, **kwargs)
                if Path(path) == output / sp.ACTUAL_REFRESH_PUBLICATION and value.get('phase') == 'ACKNOWLEDGING':
                    _mutate_refresh_pdf(manifest, mutation)
                return result
            monkeypatch.setattr(sp, 'json_save', mutate_after_ack_intent)
        old = _refresh_retained_bytes(output)
    with pytest.raises(FileNotFoundError, match='PDF'):
        if boundary in {'before_ack', 'after_ack_intent'}:
            sp._acknowledge_actual_refresh(output, token)
        else:
            sp.refresh_actual_project(output)
    assert promotion.project_refresh_token(sp.project_actual_evidence_root(output)) == token
    assert {p: p.read_bytes() for p in old if p.name != sp.ACTUAL_REFRESH_PUBLICATION} == {
        p: raw for p, raw in old.items() if p.name != sp.ACTUAL_REFRESH_PUBLICATION}
    journal_path = output / sp.ACTUAL_REFRESH_PUBLICATION
    if boundary == 'before_publish':
        assert not journal_path.exists()
    else:
        journal = sp.json_load_strict(journal_path)
        assert journal['phase'] == {'before_published_mark': 'PREPARED', 'before_ack': 'PUBLISHED', 'after_ack_intent': 'ACKNOWLEDGING'}[boundary]
        assert (output.parent / journal['stage'] / 'previous').is_dir()


@pytest.mark.parametrize('phase', ['PUBLISHED', 'ACKNOWLEDGING'])
@pytest.mark.parametrize('relocated', [False, True])
def test_refresh_restart_accepts_exact_pdf_without_duplicate_work(tmp_path, monkeypatch, phase, relocated):
    output, manifest = sealed_project(tmp_path)
    token = commit_empty_plan(output)
    monkeypatch.setattr(sp, 'run_pipeline_pdfs', successful_stage)
    sp.refresh_actual_project(output)
    if phase == 'ACKNOWLEDGING':
        acknowledge = sp.acknowledge_project_refresh
        monkeypatch.setattr(sp, 'acknowledge_project_refresh', lambda *args: (_ for _ in ()).throw(OSError('ack interruption')))
        with pytest.raises(OSError, match='ack interruption'):
            sp._acknowledge_actual_refresh(output, token)
        monkeypatch.setattr(sp, 'acknowledge_project_refresh', acknowledge)
    if relocated:
        destination = tmp_path / 'moved' / 'source.pdf'
        destination.parent.mkdir()
        Path(manifest['pdfs'][0]['pdf']).replace(destination)
        assert sp.sha256_file(destination) == manifest['pdfs'][0]['pdf_sha256']
    journal = sp.json_load_strict(output / sp.ACTUAL_REFRESH_PUBLICATION)
    before = {p: p.read_bytes() for p in output.rglob('*') if p.is_file() and p.name not in {sp.ACTUAL_REFRESH_PUBLICATION, promotion.PROJECT_TRANSACTION_FILE}}
    monkeypatch.setattr(sp, 'run_pipeline_pdfs', lambda *a, **kw: pytest.fail('duplicate rebuild'))
    monkeypatch.setattr(sp, '_publish_actual_refresh', lambda *a, **kw: pytest.fail('duplicate publish'))
    assert sp.refresh_actual_with_recovery(output) == output / '注音校對_最終報告.xlsx'
    assert {p: p.read_bytes() for p in before} == before
    assert promotion.project_refresh_token(sp.project_actual_evidence_root(output)) is None
    assert not (output / sp.ACTUAL_REFRESH_PUBLICATION).exists()
    assert not (output.parent / journal['stage']).exists()


@pytest.mark.parametrize('seal', ['missing', 'changed'])
def test_refresh_cannot_replace_original_sealed_pdf_hash(tmp_path, monkeypatch, seal):
    output, manifest = sealed_project(tmp_path)
    token = commit_empty_plan(output)
    before = _refresh_retained_bytes(output)
    def unbound_stage(pdfs, destination, **kwargs):
        result = successful_stage(pdfs, destination, **kwargs)
        path = Path(destination) / '校對工作階段.json'
        staged = sp.json_load_strict(path)
        if seal == 'missing':
            staged['pdfs'][0].pop('pdf_sha256')
        else:
            _mutate_refresh_pdf(manifest, 'changed')
            staged['pdfs'][0]['pdf_sha256'] = sp.sha256_file(pdfs[0])
        sp.json_save(path, sp.seal_manifest(staged))
        return result
    monkeypatch.setattr(sp, 'run_pipeline_pdfs', unbound_stage)
    with pytest.raises(ValueError, match='PDF'):
        sp.refresh_actual_project(output)
    assert _refresh_retained_bytes(output) == before
    assert promotion.project_refresh_token(sp.project_actual_evidence_root(output)) == token


@pytest.mark.parametrize('phase', ['PUBLISHED', 'ACKNOWLEDGING'])
def test_restored_sealed_pdf_resumes_and_repeat_does_not_reapply_transaction(tmp_path, monkeypatch, phase):
    output, manifest = sealed_project(tmp_path)
    token = commit_empty_plan(output)
    pdf = Path(manifest['pdfs'][0]['pdf'])
    original_pdf = pdf.read_bytes()
    monkeypatch.setattr(sp, 'run_pipeline_pdfs', successful_stage)
    sp.refresh_actual_project(output)
    if phase == 'ACKNOWLEDGING':
        acknowledge = sp.acknowledge_project_refresh
        def interrupt(*args):
            raise OSError('ack interruption')
        monkeypatch.setattr(sp, 'acknowledge_project_refresh', interrupt)
        with pytest.raises(OSError, match='ack interruption'):
            sp._acknowledge_actual_refresh(output, token)
        monkeypatch.setattr(sp, 'acknowledge_project_refresh', acknowledge)
    _mutate_refresh_pdf(manifest, 'same_metadata')
    retained = _refresh_retained_bytes(output)
    with pytest.raises(FileNotFoundError, match='PDF'):
        sp.refresh_actual_with_recovery(output)
    assert _refresh_retained_bytes(output) == retained
    pdf.write_bytes(original_pdf)
    assert sp.sha256_file(pdf) == manifest['pdfs'][0]['pdf_sha256']
    monkeypatch.setattr(sp, 'run_pipeline_pdfs', lambda *a, **kw: pytest.fail('duplicate recovery rebuild'))
    monkeypatch.setattr(sp, 'apply_staged_manual_actual_batch', lambda *a, **kw: pytest.fail('reapplied transaction'))
    report = sp.refresh_actual_with_recovery(output)
    root = sp.project_actual_evidence_root(output)
    actual_bytes = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
    final = sp.json_load_strict(output / '校對工作階段.json')
    assert final['session_id'] == manifest['session_id']
    assert final['pdfs'][0]['pdf_sha256'] == manifest['pdfs'][0]['pdf_sha256']
    # New explicit refresh requests may stage, but cannot recreate the consumed
    # actual transaction, alter evidence, or enqueue/deliver it again.
    monkeypatch.setattr(sp, 'run_pipeline_pdfs', successful_stage)
    assert sp.refresh_actual_with_recovery(output) == report
    assert sp.refresh_actual_with_recovery(output) == report
    assert promotion.project_refresh_token(root) is None
    assert {p: p.read_bytes() for p in root.rglob('*') if p.is_file()} == actual_bytes
    assert not (output / sp.ACTUAL_REFRESH_PUBLICATION).exists()


def test_native_publish_failure_restores_all_old_bytes(tmp_path, monkeypatch):
    output, manifest = sealed_project(tmp_path)
    token = commit_empty_plan(output)
    before = {p: p.read_bytes() for p in output.rglob('*') if p.is_file()}
    monkeypatch.setattr(sp, 'run_pipeline_pdfs', successful_stage)
    replace = Path.replace

    def fail_second(source, target):
        if Path(target) == output / '人工判定資料庫.json' and source.name == '人工判定資料庫.json':
            raise OSError('SECOND_PUBLICATION_RENAME')
        return replace(source, target)

    monkeypatch.setattr(Path, 'replace', fail_second)
    with pytest.raises(OSError, match='SECOND_PUBLICATION_RENAME'):
        sp.refresh_actual_project(output)
    assert {p: p.read_bytes() for p in before} == before
    assert promotion.project_refresh_token(sp.project_actual_evidence_root(output)) == token
    assert not (output / sp.ACTUAL_REFRESH_PUBLICATION).exists()
    sp.validate_output_artifact_hashes(manifest)


def test_failed_rollback_retains_verified_backup_for_restart(tmp_path, monkeypatch):
    output, manifest = sealed_project(tmp_path)
    commit_empty_plan(output)
    before = {p: p.read_bytes() for p in output.rglob('*') if p.is_file()}
    monkeypatch.setattr(sp, 'run_pipeline_pdfs', successful_stage)
    replace = Path.replace

    def fail_publication_and_rollback(source, target):
        if Path(target) == output / '人工判定資料庫.json' and '.ar-' in str(source):
            raise OSError('PUBLICATION_AND_ROLLBACK_UNAVAILABLE')
        return replace(source, target)

    monkeypatch.setattr(Path, 'replace', fail_publication_and_rollback)
    with pytest.raises(OSError, match='PUBLICATION_AND_ROLLBACK_UNAVAILABLE'):
        sp.refresh_actual_project(output)
    journal = sp.json_load_strict(output / sp.ACTUAL_REFRESH_PUBLICATION)
    assert journal['phase'] == 'PREPARED'
    assert (output.parent / journal['stage'] / 'previous' / '0').read_bytes() == b'sealed-actual'
    monkeypatch.setattr(Path, 'replace', replace)
    with promotion.project_delivery_lock(sp.project_actual_evidence_root(output)):
        sp._resume_actual_refresh_publication(output)
    assert {p: p.read_bytes() for p in before} == before
    sp.validate_output_artifact_hashes(manifest)


def test_published_retry_does_not_publish_twice_and_ack_cleans(tmp_path, monkeypatch):
    output, manifest = sealed_project(tmp_path)
    token = commit_empty_plan(output)
    report = output / '注音校對_最終報告.xlsx'
    report.write_bytes(b'old-deferred-report')
    monkeypatch.setattr(sp, 'run_pipeline_pdfs', successful_stage)
    assert sp.refresh_actual_project(output) == report
    pending = output / sp.ACTUAL_REFRESH_PUBLICATION
    journal = sp.json_load_strict(pending)
    assert journal['phase'] == 'PUBLISHED'
    assert journal['token'] == token
    after = {p: p.read_bytes() for p in output.rglob('*') if p.is_file()}
    monkeypatch.setattr(sp, 'run_pipeline_pdfs', lambda *a, **kw: pytest.fail('duplicate rebuild'))
    assert sp.refresh_actual_project(output) == report
    assert {p: p.read_bytes() for p in after} == after
    final = sp.json_load_strict(output / '校對工作階段.json')
    assert final['session_id'] == manifest['session_id']
    assert final['pdfs'][0]['candidate_workbook_sha256'] == manifest['pdfs'][0]['candidate_workbook_sha256']
    assert '.ar-' not in json.dumps(final, ensure_ascii=False)
    assert '.ar-' not in (output / '待人工確認.json').read_text(encoding='utf-8')
    assert '.ar-' not in (output / 'pipeline_status.json').read_text(encoding='utf-8')
    assert report.read_bytes() == b'old-deferred-report'
    with pytest.raises(promotion.library.GlobalLibraryIntentConflictError):
        sp._acknowledge_actual_refresh(output, 'f' * 64)
    assert pending.is_file()
    assert promotion.project_refresh_token(sp.project_actual_evidence_root(output)) == token
    sp._acknowledge_actual_refresh(output, token)
    assert not pending.exists()
    assert not (output.parent / journal['stage']).exists()
    sp.validate_output_artifact_hashes(final)


@pytest.mark.parametrize('mutation', ['unknown', 'backup', 'target', 'plan', 'source', 'escape', 'token_deleted'])
def test_publication_tamper_stops_without_overwrite(tmp_path, monkeypatch, mutation):
    output, _ = sealed_project(tmp_path)
    commit_empty_plan(output)
    monkeypatch.setattr(sp, 'run_pipeline_pdfs', successful_stage)
    sp.refresh_actual_project(output)
    path = output / sp.ACTUAL_REFRESH_PUBLICATION
    journal = sp.json_load_strict(path)
    if mutation == 'unknown':
        journal['unknown'] = 1
    elif mutation == 'backup':
        (output.parent / journal['stage'] / 'previous' / '0').unlink()
    elif mutation == 'target':
        (output / journal['entries'][0]['path']).write_bytes(b'foreign-writer')
    elif mutation == 'plan':
        journal['plan_sha'] = 'f' * 64
    elif mutation == 'source':
        journal['source']['user_verified_cff_glyph_fingerprints.csv'] = 'f' * 64
    elif mutation == 'token_deleted':
        (sp.project_actual_evidence_root(output) / promotion.PROJECT_TRANSACTION_FILE).unlink()
    else:
        journal['entries'][0]['path'] = '../foreign.xlsx'
    sp.json_save(path, journal)
    before = {p: p.read_bytes() for p in output.rglob('*') if p.is_file()}
    with pytest.raises(ValueError):
        sp.refresh_actual_project(output)
    assert {p: p.read_bytes() for p in before} == before


def test_missing_source_evidence_never_seeds(tmp_path, monkeypatch):
    output, _ = sealed_project(tmp_path)
    missing = sp.project_actual_evidence_root(output) / sp.USER_CFF_FILE
    missing.unlink()
    monkeypatch.setattr(sp, 'run_pipeline_pdfs', lambda *a, **k: pytest.fail('missing source reached core'))
    with pytest.raises(ValueError, match='缺失'):
        sp.refresh_actual_project(output)
    assert not missing.exists()


def test_real_process_crash_restores_prepared_before_retry(tmp_path, monkeypatch):
    output, manifest = sealed_project(tmp_path)
    token = commit_empty_plan(output)
    original = {p: p.read_bytes() for p in output.rglob('*') if p.is_file()}
    code = '''
import os, sys
from pathlib import Path
import standalone_proofread as sp
from tests.test_actual_refresh_publication import successful_stage
output=Path(sys.argv[1]); sp.run_pipeline_pdfs=successful_stage
real=Path.replace
def crash(source,target):
    result=real(source,target)
    if Path(target).parent == output/'01_實際注音': os._exit(29)
    return result
Path.replace=crash
sp.refresh_actual_project(output)
'''
    process = subprocess.run([sys.executable, '-B', '-c', code, str(output)],
                             cwd=Path(sp.__file__).parent, env=os.environ.copy(), capture_output=True, timeout=15)
    assert process.returncode == 29, process.stderr.decode('utf-8', 'replace')
    assert sp.json_load_strict(output / sp.ACTUAL_REFRESH_PUBLICATION)['phase'] == 'PREPARED'
    with promotion.project_delivery_lock(sp.project_actual_evidence_root(output)):
        sp._resume_actual_refresh_publication(output)
    assert {p: p.read_bytes() for p in original} == original
    assert promotion.project_refresh_token(sp.project_actual_evidence_root(output)) == token
    sp.validate_output_artifact_hashes(manifest)


def test_checked_postcondition_failure_never_publishes(tmp_path, monkeypatch):
    output, _ = sealed_project(tmp_path)
    occurrence = 'occ_' + '1' * 64
    plan = {'schema_version': '1.0', 'affected_occurrence_ids': [occurrence],
            'checked_postconditions': [{'group_id': 'agr_' + '2' * 24,
                                       'occurrence_id': occurrence, 'reading': 'ㄅ'}]}
    with promotion.direct_visual_project_transaction(sp.project_actual_evidence_root(output)) as bind:
        bind(plan)
    before = {p: p.read_bytes() for p in output.rglob('*') if p.is_file()}
    monkeypatch.setattr(sp, 'run_pipeline_pdfs', successful_stage)
    monkeypatch.setattr(sp, '_publish_actual_refresh', lambda *a, **kw: pytest.fail('unchecked publication'))
    with pytest.raises(ValueError, match='postcondition'):
        sp.refresh_actual_project(output)
    assert {p: p.read_bytes() for p in before} == before
    assert promotion.committed_project_recovery(sp.project_actual_evidence_root(output))[1] == plan


@pytest.mark.parametrize('window', ['before_ack', 'after_ack'])
def test_post_ack_cleanup_failure_resumes_without_second_publication(tmp_path, monkeypatch, window):
    output, _ = sealed_project(tmp_path)
    token = commit_empty_plan(output)
    monkeypatch.setattr(sp, 'run_pipeline_pdfs', successful_stage)
    sp.refresh_actual_project(output)
    path = output / sp.ACTUAL_REFRESH_PUBLICATION
    unlink = Path.unlink
    acknowledge = sp.acknowledge_project_refresh
    def fail_cleanup(self, *args, **kwargs):
        if self == path:
            raise OSError('post-ack cleanup interruption')
        return unlink(self, *args, **kwargs)
    if window == 'after_ack':
        monkeypatch.setattr(Path, 'unlink', fail_cleanup)
    else:
        def fail_ack(*args):
            raise OSError('post-ack intent interruption')
        monkeypatch.setattr(sp, 'acknowledge_project_refresh', fail_ack)
    with pytest.raises(OSError, match='post-ack'):
        sp._acknowledge_actual_refresh(output, token)
    assert promotion.project_refresh_token(sp.project_actual_evidence_root(output)) == (token if window == 'before_ack' else None)
    assert sp.json_load_strict(path)['phase'] == 'ACKNOWLEDGING'
    actual_journal = sp.project_actual_evidence_root(output) / promotion.PROJECT_TRANSACTION_FILE
    live = {p: p.read_bytes() for p in output.rglob('*') if p.is_file() and p not in {path, actual_journal}}
    monkeypatch.setattr(Path, 'unlink', unlink)
    monkeypatch.setattr(sp, 'acknowledge_project_refresh', acknowledge)
    monkeypatch.setattr(sp, 'run_pipeline_pdfs', lambda *a, **kw: pytest.fail('duplicate publication'))
    monkeypatch.setattr(sp, '_publish_actual_refresh', lambda *a, **kw: pytest.fail('duplicate publish'))
    assert sp.refresh_actual_with_recovery(output) == output / '注音校對_最終報告.xlsx'
    assert not path.exists()
    assert {p: p.read_bytes() for p in live} == live


def test_second_process_cannot_enter_owned_refresh_lock(tmp_path, monkeypatch):
    output, _ = sealed_project(tmp_path)
    token = commit_empty_plan(output)
    monkeypatch.setattr(sp, 'run_pipeline_pdfs', successful_stage)
    sp.refresh_actual_project(output)
    before = {p: p.read_bytes() for p in output.rglob('*') if p.is_file()}
    code = '''import sys
from pathlib import Path
import standalone_proofread as sp
try:
    sp.refresh_actual_project(Path(sys.argv[1]))
except OSError as error:
    print(type(error).__name__, str(error)); sys.exit(27)
sys.exit(1)
'''
    with promotion.project_delivery_lock(sp.project_actual_evidence_root(output)):
        result = subprocess.run([sys.executable, '-B', '-c', code, str(output)],
            cwd=Path(sp.__file__).parent, capture_output=True, timeout=15)
    assert result.returncode == 27, (result.stdout, result.stderr)
    assert {p: p.read_bytes() for p in before} == before
    assert promotion.project_refresh_token(sp.project_actual_evidence_root(output)) == token


def test_real_pipeline_stage_keeps_exact_fingerprints_and_identity(tmp_path, monkeypatch):
    from tests.test_cff_batch_fingerprint import CFFBatchFingerprintTests
    CFFBatchFingerprintTests.setUpClass()
    surface = CFFBatchFingerprintTests('test_non_unseen_rows_remain_unchanged_and_candidate_compatible')
    surface.setUp()
    try:
        pdf, workbook, fingerprint = surface.decode()
        surface.run_batch([workbook])
        candidate = workbook.with_name(workbook.stem + '-candidates.xlsx')
        surface.analyze(pdf, workbook)
        output = tmp_path / 'real-project'
        actual = sp.actual_workbook_path(output / '01_實際注音', pdf)
        cand = sp.candidate_workbook_path(output / '02_候選報告', pdf)
        for target, original in ((actual, workbook), (cand, candidate)):
            target.parent.mkdir(parents=True)
            shutil.copyfile(original, target)
        shutil.copytree(surface.dynamic, sp.project_actual_evidence_root(output))
        manifest = sp.collect_manifest([pdf], actual.parent, cand.parent,
            actual_fingerprints={pdf.name: fingerprint}, source_validation=surface.sources)
        sp.json_save(output / '校對工作階段.json', manifest)
        sp.json_save(output / '人工判定資料庫.json', sp.normalize_db({}))
        before = (actual.read_bytes(), cand.read_bytes())
        monkeypatch.setattr(sp, 'decode', lambda *a, **kw: pytest.fail('compatible actual decoded twice'))
        sp.refresh_actual_project(output)
        final = sp.json_load_strict(output / '校對工作階段.json')
        assert [(r['occurrence_id'], r['review_id']) for r in final['records']] == [
            (r['occurrence_id'], r['review_id']) for r in manifest['records']]
        assert final['session_id'] == manifest['session_id']
        assert (actual.read_bytes(), cand.read_bytes()) == before
        assert final['pdfs'][0]['actual_asset_fingerprint'] == manifest['pdfs'][0]['actual_asset_fingerprint']
        assert '.ar-' not in json.dumps(final, ensure_ascii=False)
        sp.validate_output_artifact_hashes(final)
    finally:
        surface.doCleanups()
