"""Actual Excel source schema checks must precede both import routes."""

from unittest.mock import patch

import pytest
from openpyxl import load_workbook

import pdf_portability as portable
import standalone_proofread as sp
from tests.test_pdf_portability_binding import actual_projects, filled_actual


@pytest.mark.parametrize("session", ["same", "cross"])
@pytest.mark.parametrize("field", [
    "session_schema_version", "workbook_schema_version", "review_id_schema_version",
])
@pytest.mark.parametrize("invalid", ["missing", "future"])
def test_actual_excel_invalid_source_schema_preserves_target(
        actual_projects, tmp_path, session, field, invalid):
    source, other, _ = actual_projects
    target = source if session == "same" else other
    valid_workbook = filled_actual(source, tmp_path / "valid.xlsx")
    workbook = load_workbook(valid_workbook)
    metadata = workbook["匯入中繼資料"]
    row = next(row for row in metadata if row[0].value == field)
    row[1].value = None if invalid == "missing" else "999.0"
    invalid_workbook = tmp_path / f"{session}-{field}-{invalid}.xlsx"
    workbook.save(invalid_workbook)
    workbook.close()
    with sp.project_delivery_lock(sp.project_actual_evidence_root(target)):
        pass

    def target_bytes():
        return {
            str(path.relative_to(target)): path.read_bytes()
            for path in target.rglob("*") if path.is_file()
        }

    before = target_bytes()
    with patch.object(portable, "resume_actual_excel_project",
                      wraps=portable.resume_actual_excel_project) as cross_refresh, \
         patch.object(sp, "_finish_direct_actual_commit",
                      wraps=sp._finish_direct_actual_commit) as same_refresh:
        with pytest.raises(ValueError, match=field):
            sp.import_actual_gpt_decisions(target, invalid_workbook)
    assert not cross_refresh.called and not same_refresh.called
    assert target_bytes() == before
