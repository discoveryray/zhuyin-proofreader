"""Expected Excel schema compatibility at the public same/cross-session entry."""

import pytest
from openpyxl import load_workbook

import pdf_portability as portable
import standalone_proofread as sp
from tests.test_pdf_portability import _filled_expected_excel, pdf, project


@pytest.mark.parametrize("session", ["same", "cross"])
@pytest.mark.parametrize("field", [
    "session_schema_version", "workbook_schema_version", "review_id_schema_version",
])
@pytest.mark.parametrize("invalid", ["missing", "future"])
def test_expected_excel_invalid_source_schema_preserves_target(
        tmp_path, monkeypatch, session, field, invalid):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-localappdata"))
    monkeypatch.setattr(portable, "_publish_portable_outputs", lambda *_args: None)
    source_pdf, target_pdf = tmp_path / "source.pdf", tmp_path / "target.pdf"
    pdf(source_pdf, title="source")
    pdf(target_pdf, title="target")
    source, other = tmp_path / "source", tmp_path / "target"
    project(source, source_pdf, session="A")
    project(other, target_pdf, session="B")
    target = source if session == "same" else other
    workbook_path = _filled_expected_excel(source, tmp_path / "filled.xlsx")
    workbook = load_workbook(workbook_path)
    metadata = workbook["匯入中繼資料"]
    row = next(row for row in metadata if row[0].value == field)
    row[1].value = None if invalid == "missing" else "999.0"
    workbook.save(workbook_path)
    workbook.close()
    with sp.project_delivery_lock(sp.project_actual_evidence_root(target)):
        pass

    def target_bytes():
        return {
            str(path.relative_to(target)): path.read_bytes()
            for path in target.rglob("*") if path.is_file()
        }

    before = target_bytes()
    with pytest.raises(ValueError, match=field):
        sp.import_gpt_decisions(target, workbook_path)
    assert target_bytes() == before
