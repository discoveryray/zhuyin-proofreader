from __future__ import annotations

import csv
import tempfile
from pathlib import Path

from occurrence_ledger import (
    LEDGER_SCHEMA_VERSION,
    build_occurrence_ledger,
    completion_gate,
    prepare_occurrence_rows,
    refresh_derived_state,
)
from standalone_proofread import (
    _apply_review_event,
    needs_expected_review,
    initialize_project_actual_evidence,
)
from actual_review import OCCURRENCE_OVERRIDE_FILE, USER_GLYF_FILE, USER_CFF_FILE, OVERRIDE_HEADERS

SHA = "a" * 64


def _source():
    rows = [{
        "pdf_sha256": SHA,
        "pdf_name": "book.pdf",
        "實體頁碼": 1,
        "課本頁": 1,
        "字元": "頭",
        "穩定注音鍵": "TTF:test",
        "font": "Bpmf",
        "font_xref": 1,
        "glyph_id_字形索引": 10,
        "注音元件ID": 20,
        "x0": 10.0, "y0": 20.0, "x1": 30.0, "y1": 40.0,
        "所在行": "拳頭",
        "局部詞境": "拳頭",
    }]
    prepare_occurrence_rows(SHA, rows)
    return rows[0]


def test_actual_gap_does_not_erase_resolved_expected_lane():
    source = _source()
    ledger = build_occurrence_ledger([source], {
        source["occurrence_id"]: {
            "state": "ACTUAL_DECODE_ERROR",
            "actual": "",
            "actual_evidence": "glyph decode failed",
            "actual_status": "DECODE_ERROR",
            "expected_set": ["˙ㄊㄡ"],
            "expected_evidence": "完整詞條：拳頭",
            "expected_status": "RESOLVED",
            "context_evidence": "完整詞：拳頭；目標位置：1",
        }
    })
    row = ledger[0]
    assert row["state"] == "ACTUAL_DECODE_ERROR"
    assert row["actual_status"] == "DECODE_ERROR"
    assert row["expected_status"] == "RESOLVED"
    gate = completion_gate(
        ledger, {"ok": True},
        {"ok": True, "required": 1, "executed": 1, "passed": 1, "failed": 0, "not_executed": 0, "duplicate_case_id": 0},
    )
    assert gate["actual_covered"] == 0
    assert gate["expected_covered"] == 1
    assert gate["hard_gates"]["expected_coverage_100"] is True
    assert gate["hard_gates"]["decode_error_zero"] is False


def test_actual_gap_cannot_hide_expected_conflict_gate():
    source = _source()
    ledger = build_occurrence_ledger([source], {
        source["occurrence_id"]: {
            "state": "ACTUAL_DECODE_ERROR",
            "actual_status": "DECODE_ERROR",
            "actual_evidence": "glyph decode failed",
            "expected_status": "CONFLICT",
            "expected_evidence": "規則 A 與規則 B 衝突",
            "context_evidence": "拳頭",
        }
    })
    row = ledger[0]
    assert row["state"] == "ACTUAL_DECODE_ERROR"
    assert row["expected_status"] == "CONFLICT"
    gate = completion_gate(
        ledger, {"ok": True},
        {"ok": True, "required": 1, "executed": 1, "passed": 1, "failed": 0, "not_executed": 0, "duplicate_case_id": 0},
    )
    assert gate["hard_gates"]["rule_conflict_zero"] is False


def test_expected_review_event_survives_actual_unresolved_then_mechanically_matches():
    source = _source()
    base = build_occurrence_ledger([source], {
        source["occurrence_id"]: {
            "state": "ACTUAL_DECODE_ERROR",
            "actual_status": "DECODE_ERROR",
            "actual_evidence": "glyph decode failed",
            "expected_status": "UNRESOLVED",
            "context_evidence": "拳頭",
        }
    })[0]
    reviewed = _apply_review_event(base, {
        "action": "解決expected證據",
        "expected_set": "˙ㄊㄡ",
        "expected_evidence": "教育部完整詞條：拳頭",
        "context_evidence": "完整詞：拳頭；目標位置：1",
        "source": "test",
    })
    assert reviewed["state"] == "ACTUAL_DECODE_ERROR"
    assert reviewed["expected_status"] == "RESOLVED"
    assert reviewed["expected_set"] == ["˙ㄊㄡ"]

    after_actual = dict(reviewed)
    after_actual["actual"] = "˙ㄊㄡ"
    after_actual["actual_evidence"] = "direct glyph visual evidence"
    after_actual["actual_status"] = "RESOLVED"
    after_actual = refresh_derived_state(after_actual, preserve_confirmed=False)
    assert after_actual["comparison_result"] == "MATCH"
    assert after_actual["state"] == "PASS"


def test_expected_workbook_filter_excludes_pure_actual_problem():
    pure_actual = {
        "state": "ACTUAL_DECODE_ERROR",
        "actual_status": "DECODE_ERROR",
        "expected_status": "RESOLVED",
        "expected_set": ["ㄉㄢˋ"],
        "expected_evidence": "完整詞條",
    }
    assert needs_expected_review(pure_actual) is False
    unresolved_expected = dict(pure_actual, expected_status="UNRESOLVED", expected_set=[], expected_evidence="")
    assert needs_expected_review(unresolved_expected) is True
    true_difference = dict(pure_actual, state="DIFFERENCE_PENDING_CONFIRMATION", actual_status="RESOLVED")
    assert needs_expected_review(true_difference) is True


def test_project_actual_evidence_is_not_written_back_to_program_directory():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        program = base / "program"
        output = base / "project"
        program.mkdir()
        # Seed a valid occurrence override and empty user glyph files.
        (program / OCCURRENCE_OVERRIDE_FILE).write_text(
            ",".join(OVERRIDE_HEADERS) + "\n", encoding="utf-8"
        )
        for name in (USER_GLYF_FILE, USER_CFF_FILE):
            # initialize_project_actual_evidence will normalize headers when absent;
            # leave them absent here to test project creation.
            pass
        project_root = initialize_project_actual_evidence(output, program)
        assert project_root.parent.name == "_專案證據"
        original = (program / OCCURRENCE_OVERRIDE_FILE).read_bytes()
        with (project_root / OCCURRENCE_OVERRIDE_FILE).open("a", encoding="utf-8") as f:
            f.write("x" * 5)
        assert (program / OCCURRENCE_OVERRIDE_FILE).read_bytes() == original
        assert (project_root / USER_GLYF_FILE).exists()
        assert (project_root / USER_CFF_FILE).exists()
