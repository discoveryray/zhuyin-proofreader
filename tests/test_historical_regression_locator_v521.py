from __future__ import annotations

import pytest

from check_pronunciation_candidates import historical_regression_potential_match


def rec(page: int, char: str, line: str, pos: int):
    return {"課本頁": str(page), "字元": char, "所在行": line, "行內字元位置": pos}


def rg(page: int, phrase: str, char: str, index: str = ""):
    return {
        "case_id": "T",
        "page": str(page),
        "phrase": phrase,
        "target_char": char,
        "target_occurrence_index": index,
    }


def test_phrase_must_cover_target_not_merely_exist_elsewhere_on_line():
    line = "如果球落地兩次或沒擊中球時停止擊球,計算個人擊中牆的次數。"
    first = line.index("中")
    second = line.rindex("中")
    case = rg(141, "擊中牆", "中")
    assert not historical_regression_potential_match(rec(141, "中", line, first), case)
    assert historical_regression_potential_match(rec(141, "中", line, second), case)


def test_a_yi_a_span_excludes_unrelated_yi_on_same_line():
    line = "先找一些例子,最後找一找答案"
    unrelated = line.index("一")
    target = line.index("找一找") + 1
    case = rg(23, "找一找", "一")
    assert not historical_regression_potential_match(rec(23, "一", line, unrelated), case)
    assert historical_regression_potential_match(rec(23, "一", line, target), case)


def test_repeated_target_inside_phrase_uses_identity_only_occurrence_index():
    line = "後來被媽媽阻止了"
    first = line.index("媽")
    second = first + 1
    case = rg(36, "被媽媽阻止了", "媽", "1")
    assert not historical_regression_potential_match(rec(36, "媽", line, first), case)
    assert historical_regression_potential_match(rec(36, "媽", line, second), case)


def test_invalid_target_occurrence_index_fails_closed():
    line = "被媽媽阻止了"
    case = rg(36, "被媽媽阻止了", "媽", "2")
    with pytest.raises(ValueError):
        historical_regression_potential_match(rec(36, "媽", line, line.index("媽")), case)


def test_single_character_line_can_use_unique_target_centered_fallback_context():
    record = rec(51, "載", "載", 0)
    record["上下文"] = "用電不超過負載電器旁不放可燃物"
    case = rg(51, "負載", "載")
    assert historical_regression_potential_match(record, case)


def test_fallback_context_refuses_ambiguous_repeated_target():
    record = rec(1, "中", "中", 0)
    record["上下文"] = "擊中球後再擊中牆"
    case = rg(1, "擊中牆", "中")
    assert not historical_regression_potential_match(record, case)
