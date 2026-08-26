from pathlib import Path

from export_zhuyin_readings import load_structural_exclusions, match_structural_exclusion
from check_pronunciation_candidates import load_context_overrides, apply_context_override

ROOT = Path(__file__).resolve().parents[1]


def test_p32_effective_date_structural_exclusion_matches_current_split_pdf():
    pdf = Path('/mnt/data/03-115國小健體3下課本-L02-三審.pdf')
    exclusions = load_structural_exclusions(ROOT / 'structural_detection_exclusions.csv', pdf)
    hit = match_structural_exclusion(
        9, '32', '限', 'DFKaiShu-SB-Estd-BF', 164, 2395, 460,
        414.318, 230.761, exclusions,
    )
    assert hit is not None
    assert '沒有任何可見注音' in hit['note']


def test_p156_line_wrap_context_repair_supplies_text_only():
    pdf = Path('/mnt/data/09-115國小健體3下課本-L08-三審.pdf')
    overrides = load_context_overrides(ROOT / 'source_context_overrides.csv', pdf)
    hit = apply_context_override({
        '實體頁碼': 3,
        '字元': '西',
        'x0': 68.032,
        'y0': 191.338,
    }, overrides)
    assert hit is not None
    assert hit['context_text'] == '浮潛要準備什麼東西呢?'
    assert hit['target_index'] == 8
    # Context reconstruction must not carry pronunciation truth.
    assert not any('expected' in k.lower() or 'actual_reading' in k.lower() for k in hit)
