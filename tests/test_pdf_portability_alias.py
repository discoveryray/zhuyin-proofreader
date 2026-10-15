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


def _case(tmp_path, monkeypatch, *, moved=True, covered=False, proof_kind="complete"):
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
    if proof_kind != "missing":
        p.prepare_portable_project(source)
        if proof_kind == "legacy":
            _legacy_proof(source)
    alias = source_pdf.rename(tmp_path / "moved original.pdf") if moved else source_pdf
    binding = {"source_project": str(source), "session_id": manifest["session_id"],
               "pdf_sha256": manifest["pdfs"][0]["pdf_sha256"], "path": str(alias)}
    return source, local, alias, binding, manifest


def _legacy_proof(source):
    path = source / p.PROOF_FILE
    proof = sp.json_load_strict(path)
    proof.pop("integrity_sha256")
    for info in proof["pdfs"]:
        for page in info["pages"]:
            page.pop("candidate_text_sha256")
    proof["integrity_sha256"] = hashlib.sha256(p._canonical(proof)).hexdigest()
    sp.json_save(path, proof)


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


@pytest.mark.parametrize("proof_kind", ["missing", "legacy"])
def test_alias_content_proof_public_transfer_and_import_reuse_original_bytes(tmp_path, monkeypatch, proof_kind):
    from actual_source_visual_safety import source_visual_operation
    source, local, original, binding, manifest = _case(
        tmp_path, monkeypatch, moved=False, proof_kind=proof_kind)
    source_before = _state(source)
    source_bytes = original.read_bytes()
    with source_visual_operation():
        original_control = p._preflight_merge_sources([source.resolve()], local.resolve())
    assert _state(source) == source_before
    alias = original.rename(tmp_path / "renamed exact original.pdf")
    binding["path"] = str(alias)
    calls = _pipeline(monkeypatch, local)
    snapshots = []
    real_snapshot = p._pdf_content_snapshot
    def snapshot(path, **kwargs):
        if kwargs.get("_source_bytes") is not None:
            snapshots.append((str(path), hashlib.sha256(kwargs["_source_bytes"]).hexdigest()))
            assert kwargs["_source_bytes"] == alias.read_bytes()
        return real_snapshot(path, **kwargs)
    monkeypatch.setattr(p, "_pdf_content_snapshot", snapshot)
    target = tmp_path / "continued"
    result = (p.continue_project(source, local, target, source_pdf_aliases=[binding])
              if proof_kind == "missing" else
              p.merge_projects([source], local, target, source_pdf_aliases=[binding]))
    assert result["matched_actual_overrides"] == 1 and calls == ["pipeline", "refresh"]
    assert len(snapshots) == 1 and all(digest == binding["pdf_sha256"] for _, digest in snapshots)
    assert (_state(source), alias.read_bytes()) == (source_before, source_bytes)
    reopened, _ = p._load_project(target)
    assert [r["expected_set"] for r in reopened["records"]] == [r["expected_set"] for r in manifest["records"]]
    assert len(ar._read_csv(sp.project_actual_evidence_root(target) / ar.OCCURRENCE_OVERRIDE_FILE,
                           ar.OVERRIDE_HEADERS)) == 1
    assert not (target / p.INCOMPLETE_FILE).exists()
    # The target now has its own sealed actual, satisfying the original import
    # prerequisite. Exercise both public direct and selected-local branches.
    for kwargs in ({}, {"_selected_local_pdf": local}):
        imported = p.import_project_decisions(source, target, source_pdf_aliases=[binding], **kwargs)
        assert imported["conflicts"] == []
        assert p._load_project(target)[0]["session_id"] == reopened["session_id"]
    assert _state(source) == source_before
    with source_visual_operation():
        bindings = p._SourcePdfBindings([source], [binding])
        complete = p._load_proof(source, manifest, source_pdfs=bindings)
        assert complete["pdfs"][0]["pages"] == original_control
    assert _state(source) == source_before
    assert not list((tmp_path / "isolated-user").rglob("*.sqlite*"))
    assert ar._read_csv(sp.project_actual_evidence_root(target) / ar.USER_GLYF_FILE,
                        ar.USER_GLYF_HEADERS) == []


@pytest.mark.parametrize("proof_kind", ["missing", "legacy"])
def test_real_pipeline_alias_content_reconstruction_saves_and_reopens(tmp_path, monkeypatch, proof_kind):
    from unittest.mock import patch
    from tests.test_pdf_portability_integration import _pdfs, _synthetic_decode
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-user"))
    original, local = _pdfs(tmp_path)
    source, target = tmp_path / "source", tmp_path / "continued"
    # Only deterministic decoder output is injected; native qualification,
    # content mapping, pipeline, actual refresh, save and publication are real.
    with patch.object(sp, "decode", side_effect=_synthetic_decode), \
         patch.object(sp, "actual_workbook_global_exact_dependencies", return_value=()), \
         patch("check_pronunciation_candidates.actual_workbook_global_exact_dependencies", return_value=()):
        sp.run_pipeline_pdfs([original], source, defer_excel_reports=True)
        initial = sp.json_load_strict(source / "校對工作階段.json")
        seed_overrides = ar._read_csv(sp.project_actual_evidence_root(source) / ar.OCCURRENCE_OVERRIDE_FILE,
                                      ar.OVERRIDE_HEADERS)
        add_override(source, initial["records"][0])
        sp.refresh_actual_project(source)
        manifest, _ = p._load_project(source)
        if proof_kind == "legacy":
            p.prepare_portable_project(source)
            _legacy_proof(source)
        else:
            assert not (source / p.PROOF_FILE).exists()
        before = _state(source)
        source_bytes = original.read_bytes()
        alias = original.rename(tmp_path / "renamed source.pdf")
        binding = {"source_project": str(source), "session_id": manifest["session_id"],
                   "pdf_sha256": manifest["pdfs"][0]["pdf_sha256"], "path": str(alias)}
        result = p.continue_project(source, local, target, source_pdf_aliases=[binding])
        assert result["matched_actual_overrides"] == 1
        reopened, _ = p._load_project(target)
        assert [r["expected_set"] for r in reopened["records"]] == [r["expected_set"] for r in manifest["records"]]
        overrides = ar._read_csv(sp.project_actual_evidence_root(target) / ar.OCCURRENCE_OVERRIDE_FILE,
                                 ar.OVERRIDE_HEADERS)
        fields = ("pdf_contains", "pdf_excludes", "page", "target_char", "stable_key", "x0", "y0")
        target_key = ar._override_key_from_entry(reopened["records"][0])
        mapped = [row for row in overrides if tuple(row[field] for field in fields) == target_key]
        retained = [row for row in overrides if tuple(row[field] for field in fields) != target_key]
        assert len(mapped) == 1 and mapped[0]["actual_reading"] == "ㄐㄩㄝˊ"
        assert retained == seed_overrides and len(overrides) == len(seed_overrides) + 1
        assert not (target / p.INCOMPLETE_FILE).exists()
        assert (target / "注音校對_最終報告.xlsx").is_file()
        assert (target / "待人工確認.json").is_file()
        assert _state(source) == before and alias.read_bytes() == source_bytes
    assert not list((tmp_path / "isolated-user").rglob("*.sqlite*"))


@pytest.mark.parametrize("fault", ["integrity", "session", "pdf_roster", "legacy_visual"])
def test_alias_does_not_replace_corrupt_or_contradictory_content_proof(tmp_path, monkeypatch, fault):
    source, local, alias, binding, _ = _case(tmp_path, monkeypatch,
        proof_kind="legacy" if fault == "legacy_visual" else "complete")
    path = source / p.PROOF_FILE
    proof = sp.json_load_strict(path)
    if fault == "integrity":
        proof["integrity_sha256"] = "0" * 64
    else:
        proof.pop("integrity_sha256")
        if fault == "session":
            proof["session_id"] = "foreign-session"
        elif fault == "pdf_roster":
            proof["pdfs"][0]["pdf_sha256"] = "0" * 64
        else:
            proof["pdfs"][0]["pages"][0]["rgb_sha256"] = "0" * 64
        proof["integrity_sha256"] = hashlib.sha256(p._canonical(proof)).hexdigest()
    sp.json_save(path, proof)
    before = _state(source), alias.read_bytes(), local.read_bytes()
    target = tmp_path / "must-not-create"
    with pytest.raises(ValueError, match="證據損壞|清單不符|原頁不符"):
        p.continue_project(source, local, target, source_pdf_aliases=[binding])
    assert not target.exists() and (_state(source), alias.read_bytes(), local.read_bytes()) == before


def test_import_cannot_reuse_a_foreign_closed_content_binding(tmp_path, monkeypatch):
    from actual_source_visual_safety import source_visual_operation
    source, local, alias, binding, _ = _case(tmp_path, monkeypatch, proof_kind="missing")
    target = tmp_path / "target"
    target_manifest, _ = project(target, local, session="target", genuine_source=True)
    add_override(target, target_manifest["records"][0])
    reseal_actual_dynamic(target, local)
    with source_visual_operation():
        closed_binding = p._SourcePdfBindings([source], [binding])
    before = _state(source), _state(target), alias.read_bytes()
    with pytest.raises(ValueError, match="operation owner 不符"):
        p.import_project_decisions(source, target, source_pdf_aliases=[binding],
                                   _source_pdf_bindings=closed_binding)
    assert (_state(source), _state(target), alias.read_bytes()) == before
