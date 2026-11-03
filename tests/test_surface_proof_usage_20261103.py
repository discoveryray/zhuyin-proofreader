"""Known native uncertainty admits human surface input, never raw glyph truth."""
import hashlib
from pathlib import Path

import fitz
import pytest

from actual_source_visual_safety import source_visual_safety, surface_visual_proof
from tests.test_actual_source_visual_safety import clipped_visual_source
from tests.test_actual_overlay_warnings import painted_source


@pytest.mark.parametrize(('name','prefix','reason'), [
    ('background', b'q\n1 0 0 rg\n40 190 2 10 re f\n0 0 0 rg\n',
     'SOURCE_PREFIX_BACKGROUND_UNPROVABLE'),
    ('contrast', b'q\n1 1 1 rg\n0 0 220 240 re f\n0 0 0 rg\n40 190 2 10 re f\n',
     'SOURCE_PREFIX_BACKGROUND_UNPROVABLE'),
], ids=['background','contrast'])
def test_native_uncertain_prefix_can_only_offer_occurrence_surface_input(tmp_path,name,prefix,reason):
    row=clipped_visual_source(tmp_path/name,prefix)
    raw=source_visual_safety(row)
    assert raw['reason']==reason
    assert not raw['location_readable'] and not raw['donor_eligible']
    proof=surface_visual_proof(row,pdf=Path(row['pdf']),session_id='isolated')
    assert proof['raw_reason']==reason and proof['source_contribution_pixels']>0
    assert proof['source_coverage_pixels']>=proof['source_contribution_pixels']
    assert proof['donor_eligible'] is False
    assert source_visual_safety(row)==raw


def test_unsampled_known_later_ink_allows_human_surface_but_keeps_raw_refusal(tmp_path):
    original=painted_source(tmp_path/'normal')
    changed=tmp_path/'micro-paint.pdf'
    with fitz.open(original['pdf']) as document:
        document[0].draw_rect(fitz.Rect(74.031,84.031,74.032,84.032),color=None,fill=(1,1,1))
        document.save(changed)
    row=dict(original,pdf=str(changed),pdf_name=changed.name,
             pdf_sha256=hashlib.sha256(changed.read_bytes()).hexdigest())
    raw=source_visual_safety(row)
    assert raw['reason']=='SOURCE_LATER_INK_INTERSECTION_UNPROVABLE'
    assert not raw['location_readable'] and not raw['donor_eligible']
    proof=surface_visual_proof(row,pdf=changed,session_id='isolated')
    assert proof['raw_reason']==raw['reason'] and proof['source_contribution_pixels']>0
    assert proof['donor_eligible'] is False


def test_zero_source_contribution_and_wrong_source_do_not_become_visual_confirmation(tmp_path):
    row=painted_source(tmp_path/'invisible',color=(1,1,1))
    assert source_visual_safety(row)['reason']=='SOURCE_COMPLETE_CONTRAST_UNPROVABLE'
    with pytest.raises(ValueError,match='FULLY_OCCLUDED'):
        surface_visual_proof(row,pdf=Path(row['pdf']),session_id='isolated')
    visible=painted_source(tmp_path/'visible')
    with pytest.raises(ValueError,match='SHA|SOURCE_REFUSED'):
        surface_visual_proof(visible,pdf=Path(row['pdf']),session_id='isolated')
