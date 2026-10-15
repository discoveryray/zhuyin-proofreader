"""Finite exact-byte original aliases through real public transfer services."""
import copy
import hashlib
import json
import sys

import fitz
import pytest

import actual_review as ar
import pdf_portability as p
import standalone_proofread as sp
from tests.test_pdf_portability import pdf, project, add_override, reseal_actual_dynamic


def _state(root):
    # Existing advisory lock files carry no project/evidence state.
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*")
            if path.is_file() and path.name != ".global_exact_glyph_delivery.lock"}


def _case(tmp_path, monkeypatch, *, moved=True, covered=False):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-user"))
    source_pdf, local = tmp_path / "original.pdf", tmp_path / "local.pdf"
    pdf(source_pdf, title="source", genuine_source=True)
    pdf(local, title="target", genuine_source=True)
    if covered:
        for path in (source_pdf, local):
            with fitz.open(path) as document:
                document[0].draw_rect(fitz.Rect(19, 19, 40, 46), fill=(1, 1, 1), color=None)
                document.saveIncr()
    source = tmp_path / "source"
    manifest, _ = project(source, source_pdf, session="source-session", genuine_source=True)
    add_override(source, manifest["records"][0])
    reseal_actual_dynamic(source, source_pdf)
    manifest = sp.json_load_strict(source / "校對工作階段.json")
    p.prepare_portable_project(source)
    alias = source_pdf.rename(tmp_path / "moved original.pdf") if moved else source_pdf
    binding = {"source_project": str(source), "session_id": manifest["session_id"],
               "pdf_sha256": manifest["pdfs"][0]["pdf_sha256"], "path": str(alias)}
    return source, local, alias, binding, manifest


def _pipeline(monkeypatch, local):
    calls = []
    def build(paths, output_dir, **kwargs):
        calls.append("pipeline")
        guard = kwargs.get("_prewrite_guard")
        if guard:
            guard()
        project(output_dir, local, session="target-session", genuine_source=True)
        if guard:
            guard()
    def refresh(output_dir, **kwargs):
        calls.append("refresh")
        guard = kwargs.get("_prewrite_guard")
        if guard:
            guard()
        reseal_actual_dynamic(output_dir, local)
        if guard:
            guard()
    monkeypatch.setattr(sp, "run_pipeline_pdfs", build)
    monkeypatch.setattr(sp, "refresh_actual_project", refresh)
    monkeypatch.setattr(p, "_publish_portable_outputs", lambda *_args: None)
    return calls


def test_missing_original_and_same_picture_wrong_sha_refuse_before_target(tmp_path, monkeypatch):
    source, local, alias, binding, manifest = _case(tmp_path, monkeypatch)
    before = _state(source)
    for number, aliases in enumerate(([], [dict(binding, path=str(local))])):
        target = tmp_path / f"refused-{number}"
        with pytest.raises((ValueError, FileNotFoundError)) as failure:
            p.continue_project(source, local, target, source_pdf_aliases=aliases)
        message = str(failure.value)
        assert manifest["session_id"] in message and manifest["pdfs"][0]["pdf_sha256"] in message
        assert "original.pdf" in message
        if aliases:
            assert "SOURCE_PDF_SHA_MISMATCH" in message
        assert not target.exists() and _state(source) == before
    # Metadata alone differs; a raster/contents mapping is not an original seal.
    assert p._page_signatures(alias) == p._page_signatures(local)


@pytest.mark.parametrize("fault", ["project", "session", "sha", "duplicate"])
def test_alias_binding_is_owned_by_loaded_source_not_caller_claims(tmp_path, monkeypatch, fault):
    source, local, alias, binding, _ = _case(tmp_path, monkeypatch)
    bad = copy.deepcopy(binding)
    if fault == "project":
        bad["source_project"] = str(tmp_path / "other project")
    elif fault == "session":
        bad["session_id"] = "other-session"
    elif fault == "sha":
        bad["pdf_sha256"] = "a" * 64
    aliases = [bad, bad] if fault == "duplicate" else [bad]
    target = tmp_path / "refused"
    before = _state(source), alias.read_bytes()
    with pytest.raises(ValueError, match="綁定不符|唯一來源|歧義") as failure:
        p.merge_projects([source], local, target, source_pdf_aliases=aliases)
    assert all(value in str(failure.value) for value in ("source-session", "original.pdf", binding["pdf_sha256"]))
    assert not target.exists() and (_state(source), alias.read_bytes()) == before


def test_exact_alias_cannot_authorize_covered_source_before_target(tmp_path, monkeypatch):
    source, local, alias, binding, manifest = _case(tmp_path, monkeypatch, covered=True)
    before = _state(source)
    assert hashlib.sha256(alias.read_bytes()).hexdigest() == binding["pdf_sha256"]
    with pytest.raises(ValueError, match="SOURCE_OVERPAINT_CONFIRMED"):
        p.continue_project(source, local, tmp_path / "refused", source_pdf_aliases=[binding])
    assert not (tmp_path / "refused").exists() and _state(source) == before
    assert sp.json_load_strict(source / "校對工作階段.json") == manifest


@pytest.mark.parametrize("changed_source", ["alias", "target"])
def test_alias_snapshot_drift_after_native_capture_is_prewrite_no_partial_actual(tmp_path, monkeypatch, changed_source):
    source, local, alias, binding, _ = _case(tmp_path, monkeypatch)
    before = _state(source)
    original = p._preflight_merge_sources
    calls = []
    def drift(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(1)
        changed = alias if changed_source == "alias" else local
        changed.write_bytes(changed.read_bytes() + b"\n")
        return result
    monkeypatch.setattr(p, "_preflight_merge_sources", drift)
    with pytest.raises(ValueError, match="SOURCE_FILE_CHANGED_DURING_OPERATION"):
        p.merge_projects([source], local, tmp_path / "refused", source_pdf_aliases=[binding])
    assert calls == [1] and not (tmp_path / "refused").exists()
    assert _state(source) == before


def test_public_alias_and_original_path_share_analysis_without_new_truth(tmp_path, monkeypatch):
    from actual_source_visual_safety import SourceVisualOperation
    for moved in (False, True):
        folder = tmp_path / str(moved)
        folder.mkdir()
        source, local, alias, binding, original = _case(folder, monkeypatch, moved=moved)
        _pipeline(monkeypatch, local)
        before = _state(source), alias.read_bytes()
        pages = []
        real_page = SourceVisualOperation._page
        def page(self, raw, digest, physical_page):
            if (digest, physical_page) not in self._pages:
                pages.append((digest, physical_page))
            return real_page(self, raw, digest, physical_page)
        with monkeypatch.context() as patch:
            patch.setattr(SourceVisualOperation, "_page", page)
            target = folder / "continued"
            result = p.continue_project(source, local, target,
                **({"source_pdf_aliases": [binding]} if moved else {}))
        assert result["matched_actual_overrides"] == 1
        assert sorted(pages) == sorted([(binding["pdf_sha256"], 1), (sp.sha256_file(local), 1)])
        assert (_state(source), alias.read_bytes()) == before
        target_rows = ar._read_csv(sp.project_actual_evidence_root(target) / ar.OCCURRENCE_OVERRIDE_FILE,
                                  ar.OVERRIDE_HEADERS)
        assert len(target_rows) == 1 and target_rows[0]["actual_reading"] == "ㄐㄩㄝˊ"
        assert original["records"][0]["occurrence_id"] in target_rows[0]["note"]
        reopened, _ = p._load_project(target)
        assert [row["expected_set"] for row in reopened["records"]] == [row["expected_set"] for row in original["records"]]
        assert not (target / p.INCOMPLETE_FILE).exists()
        assert ar._read_csv(sp.project_actual_evidence_root(target) / ar.USER_GLYF_FILE,
                            ar.USER_GLYF_HEADERS) == []
        assert not list((folder / "isolated-user").rglob("*.sqlite*"))


def test_existing_project_import_rechecks_explicit_alias_before_any_decision_write(tmp_path, monkeypatch):
    source, local, alias, binding, manifest = _case(tmp_path, monkeypatch)
    target = tmp_path / "target"
    tm, _ = project(target, local, session="target", genuine_source=True)
    add_override(target, tm["records"][0])
    reseal_actual_dynamic(target, local)
    before = _state(source), _state(target)
    original = p._mapped_occurrence_overrides
    calls = []
    def drift(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(1)
        if len(calls) == 1:
            alias.write_bytes(alias.read_bytes() + b"\n")
        return result
    monkeypatch.setattr(p, "_mapped_occurrence_overrides", drift)
    with pytest.raises(ValueError, match="SOURCE_FILE_CHANGED_DURING_OPERATION"):
        p.import_project_decisions(source, target, source_pdf_aliases=[binding])
    assert calls and (_state(source), _state(target)) == before


def test_real_cli_completed_merge_retains_saved_result_after_native_close_failure(tmp_path, monkeypatch, capsys):
    from actual_source_visual_safety import SourceVisualOperation, _CURRENT_OPERATION
    source, local, alias, binding, original = _case(tmp_path, monkeypatch)
    calls = _pipeline(monkeypatch, local)
    owned, closed, operations = {}, [], []
    real_page, real_close = SourceVisualOperation._page, fitz.Document.close
    def page(self, raw, digest, physical_page):
        result = real_page(self, raw, digest, physical_page)
        document = self._documents[digest][1]
        owned[id(document)] = document
        if self not in operations:
            operations.append(self)
        return result
    def close(document):
        real_close(document)
        if id(document) in owned:
            closed.append(id(document))
            raise RuntimeError("injected alias native close failure")
    monkeypatch.setattr(SourceVisualOperation, "_page", page)
    monkeypatch.setattr(fitz.Document, "close", close)
    target = tmp_path / "continued"
    before = _state(source), alias.read_bytes()
    monkeypatch.setattr(sys, "argv", ["standalone_proofread.py", "--continue-from-project", str(source),
        "--local-pdf", str(local), "-o", str(target), "--source-pdf-alias",
        binding["source_project"], binding["session_id"], binding["pdf_sha256"], binding["path"]])
    assert sp.main() == 0
    output = capsys.readouterr().out.splitlines()
    assert any("已保存接續專案；來源清理警示" in line for line in output)
    result = json.loads(output[-1])
    assert result["project_actual_commit"] == "COMMITTED" and "injected alias" in result["source_cleanup_error"]
    assert result["matched_actual_overrides"] == 1 and calls == ["pipeline", "refresh"]
    assert sorted(closed) == sorted(owned) and all(document.is_closed for document in owned.values())
    assert operations and all(operation._closed and not operation._pages and not operation._sources
                              and not operation._documents for operation in operations)
    assert _CURRENT_OPERATION.get() is None
    assert not (target / p.INCOMPLETE_FILE).exists()
    assert (_state(source), alias.read_bytes()) == before
    reopened, _ = p._load_project(target)
    assert [row["expected_set"] for row in reopened["records"]] == [row["expected_set"] for row in original["records"]]
    overrides = ar._read_csv(sp.project_actual_evidence_root(target) / ar.OCCURRENCE_OVERRIDE_FILE, ar.OVERRIDE_HEADERS)
    assert len(overrides) == 1 and overrides[0]["actual_reading"] == "ㄐㄩㄝˊ"
