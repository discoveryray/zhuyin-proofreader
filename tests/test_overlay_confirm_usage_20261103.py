"""Isolated final-surface annotation confirmation; never uses a real textbook."""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import fitz
import pytest

import actual_review as ar
import manual_surface_actual as surface
import standalone_proofread as sp
from actual_source_visual_safety import source_visual_safety, surface_visual_proof
from review_save_service import ReviewSaveService, StaleReviewProjectError, _require_applied_surface_actual
from tests.test_actual_source_visual_safety import make_visual_source
from tests.test_staged_review_navigation_v580 import create_staged_navigation_fixture
from tests.actual_source_test_support import bind_source_from_pdf


def _annotate(pdf, bounds, *, opacity=.7, filled=True):
    with fitz.open(pdf) as document:
        page = document[0]
        annotation = page.add_rect_annot(fitz.Rect(bounds))
        annotation.set_colors(stroke=(1, 0, 0), **({'fill': (1, 1, 1)} if filled else {}))
        annotation.set_opacity(opacity)
        annotation.update()
        temporary = pdf.with_name('annotated.pdf')
        document.save(temporary)
    temporary.replace(pdf)


def _annotated_project(tmp_path, monkeypatch, *, overpaint=False):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'isolated-localappdata'))
    output = tmp_path / 'project'
    output.mkdir()
    manifest = create_staged_navigation_fixture(output, same_exact_peers=True)
    pdf = Path(manifest['pdfs'][0]['pdf'])
    with fitz.open(pdf) as document:
        page = document[0].rect
        outline = (10, 10, page.width - 10, page.height - 10)
    if overpaint:
        with fitz.open(pdf) as document:
            page = document[0]
            x, y = page.get_texttrace()[0]['chars'][0][2]
            page.draw_rect(fitz.Rect(x + 1, y - 4, x + 2, y - 2),
                           fill=(1, 1, 1), color=None)
            document.saveIncr()
    else:
        _annotate(pdf, outline, opacity=1, filled=False)
    for row in manifest['records']:
        bind_source_from_pdf(row, pdf)
        row['source_record']['pdf_sha256'] = row['pdf_sha256']
    manifest['pdfs'][0]['pdf_sha256'] = hashlib.sha256(pdf.read_bytes()).hexdigest()
    sp.json_save(output / '校對工作階段.json', sp.seal_manifest(manifest))
    root = sp.project_actual_evidence_root(output)
    ar.ensure_user_evidence_files(root)
    ar._write_actual_evidence_csv(root / ar.OCCURRENCE_OVERRIDE_FILE, ar.OVERRIDE_HEADERS, [])
    return output, sp.json_load_strict(output / '校對工作階段.json')


def test_annotation_requires_explicit_surface_input_and_keeps_raw_guard(tmp_path):
    row = make_visual_source(tmp_path / 'source', count=1)[0]
    pdf = Path(row['pdf'])
    _annotate(pdf, (10, 10, 200, 200), opacity=1, filled=False)
    row['pdf_sha256'] = hashlib.sha256(pdf.read_bytes()).hexdigest()
    safety = source_visual_safety(row)
    assert safety['location_readable'] and safety['reason'] == 'SOURCE_DRAW_UNCONTRADICTED'
    proof = surface_visual_proof(row, pdf=pdf, session_id='isolated')
    assert proof['raw_reason'] == 'SOURCE_DRAW_UNCONTRADICTED'
    assert proof['render']['annots'] is True and proof['source_contribution_pixels'] > 0
    assert proof['donor_eligible'] is False
    with fitz.open(pdf) as document:
        page = document[0]
        target = fitz.Rect(proof['render']['target_rect'])
        assert hashlib.sha256(page.get_pixmap(matrix=fitz.Matrix(4, 4), clip=target,
                                               alpha=False, annots=True).tobytes('png')).hexdigest() == proof['render']['target_sha256']
        assert page.get_pixmap(matrix=fitz.Matrix(4, 4), clip=target, alpha=False,
                               annots=False).tobytes('png') == page.get_pixmap(
                                   matrix=fitz.Matrix(4, 4), clip=target, alpha=False,
                                   annots=True).tobytes('png')
    for reading, attest in [('', True), ('ㄅ', False)]:
        with pytest.raises(ValueError):
            surface.record_for(proof, reading, actor='isolated test',
                whole_unit_readable=attest, target_character_matches=True,
                reading_from_original=True)
    opaque = make_visual_source(tmp_path / 'opaque', count=1)[0]
    opaque_pdf = Path(opaque['pdf'])
    _annotate(opaque_pdf, (opaque['x0'] - 4, opaque['y0'] - 4,
                           opaque['x1'] + 4, opaque['y1'] + 4), opacity=1)
    opaque['pdf_sha256'] = hashlib.sha256(opaque_pdf.read_bytes()).hexdigest()
    with fitz.open(opaque_pdf) as document:
        hidden = document[0].get_pixmap(matrix=fitz.Matrix(4, 4),
            clip=fitz.Rect(opaque['x0'], opaque['y0'], opaque['x1'], opaque['y1']),
            alpha=False, annots=True)
        assert set(hidden.samples) == {255}
    assert source_visual_safety(opaque)['reason'] == 'SOURCE_ANNOTATION_INTERSECTION_UNPROVABLE'
    with pytest.raises(ValueError, match='SURFACE_SOURCE_REFUSED: SOURCE_ANNOTATION_INTERSECTION_UNPROVABLE'):
        surface_visual_proof(opaque, pdf=opaque_pdf, session_id='isolated')


@pytest.mark.parametrize('overpaint', [False, True], ids=['frame', 'overpaint'])
def test_applied_annotation_surface_can_confirm_expected_only_for_its_occurrence(tmp_path, monkeypatch, overpaint):
    output, manifest = _annotated_project(tmp_path, monkeypatch, overpaint=overpaint)
    rid = manifest['records'][0]['review_id']
    before = ReviewSaveService(output).load_resume_snapshot()
    assert source_visual_safety(manifest['records'][0])['location_readable'] is (not overpaint)
    assert before.ledger[0].get('manual_surface_actual') is None
    if overpaint:
        assert source_visual_safety(manifest['records'][0])['reason'] == 'SOURCE_OVERPAINT_CONFIRMED'
        with pytest.raises(ValueError, match='SOURCE_OVERPAINT_CONFIRMED'):
            sp.require_current_actual_visual_source(before.ledger[0], output)
    else:
        assert sp.require_current_actual_visual_source(before.ledger[0], output)['location_readable']
    prepared = sp.prepare_surface_actual_correction(output, rid)
    assert prepared['reading'] == '' and prepared['proof']['render']['annots'] is True
    sp.stage_surface_actual_correction(output, rid, 'ㄅㄨˋ', proof=prepared['proof'],
        actor='isolated explicit final-image reading', whole_unit_readable=True,
        target_character_matches=True, reading_from_original=True, enable_format=True)
    staged = surface.load(sp.project_actual_evidence_root(output), sp.json_load_strict(output / '校對工作階段.json'))
    assert len(staged['pending']) == 1 and not staged['records']
    sp.apply_staged_surface_actual_corrections(output)
    reopened = ReviewSaveService(output).load_resume_snapshot()
    row = next(item for item in reopened.ledger if item['review_id'] == rid)
    assert row['actual'] == 'ㄅㄨˋ' and row['manual_surface_actual']['reading'] == 'ㄅㄨˋ'
    assert row['surface_donor_eligible'] is False and row['raw_source_warning_retained'] is True
    assert all(item.get('manual_surface_actual') is None for item in reopened.ledger if item['review_id'] != rid)
    wrong = copy.deepcopy(row)
    wrong['actual'] = 'ㄅㄨˊ'
    with pytest.raises(StaleReviewProjectError):
        _require_applied_surface_actual(wrong, reopened.manifest, reopened.content, output)
    event = sp.build_manual_expected_event(row, operation='CONFIRM_CURRENT_AS_EXPECTED')
    saved = ReviewSaveService(output).save_event(rid, event,
        expected_manifest=reopened.manifest, expected_db=reopened.db,
        confirm_current_actual_snapshot=sp.actual_confirmation_snapshot(row))
    assert saved.db['events'][rid]['manual_expected_decision']['expected_set'] == ['ㄅㄨˋ']
    fresh = ReviewSaveService(output).load_resume_snapshot()
    resolved = next(item for item in fresh.ledger if item['review_id'] == rid)
    assert resolved['expected_set'] == ['ㄅㄨˋ']
    assert all(item.get('manual_surface_actual') is None for item in fresh.ledger if item['review_id'] != rid)
    db_path = output / '人工判定資料庫.json'
    db_before = db_path.read_bytes()
    with Path(manifest['pdfs'][0]['pdf']).open('ab') as source:
        source.write(b'\n% changed source after confirmation\n')
    with pytest.raises(ValueError):
        ReviewSaveService(output).save_event(rid, event,
            expected_manifest=fresh.manifest, expected_db=fresh.db)
    assert db_path.read_bytes() == db_before
