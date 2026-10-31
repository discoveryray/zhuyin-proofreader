"""Single-group learning prepares complete peers from fresh genuine PDF bytes.

The independent fixture IDs and source readings never come from candidate output.
No Tk is initialized and no native safety/admission result is substituted.
"""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import fitz
import pytest

import actual_review as actual
import global_exact_glyph_library as library
import global_glyph_promotion as promotion
import glyph_learning_service as learning
import standalone_proofread as sp
from export_pdf_text_diagnostics import build_font_lookup, normalize_basefont, resolve_font
from review_operation_worker import ReviewOperationState
from tests.test_actual_operation_snapshots import make_source


def target_fixture(root):
    """Three exact groups, four peers each across two real pages; one covered."""
    root.mkdir(parents=True, exist_ok=True)
    original = make_source(root / "original", n=3)
    pdf = root / "three.pdf"
    with fitz.open(original[0]["pdf"]) as source, fitz.open() as document:
        document.insert_pdf(source)
        document.insert_pdf(source)
        # A second same-glyph position on page 2 is genuinely overpainted.
        box = document[1].get_texttrace()[0]["chars"][1][3]
        document[1].draw_rect(box, fill=(1, 1, 1), color=None)
        document.save(pdf)
    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
    rows, expected = [], {}
    with fitz.open(pdf) as document:
        for page_number, page in enumerate(document, 1):
            lookup = build_font_lookup(page.get_fonts(full=True))
            for group_number, span in enumerate(page.get_texttrace()):
                letter = "ABC"[group_number]
                xref, *_ = resolve_font(lookup, normalize_basefont(span["font"]))
                for slot, char in enumerate(span["chars"]):
                    source = copy.deepcopy(original[group_number * 2]["source_record"])
                    source["font_xref"] = xref
                    oid = f"pa01-{letter}-p{page_number}-s{slot}"
                    row = dict(original[group_number * 2], occurrence_id=oid,
                        review_id="review-" + oid, pdf=str(pdf), pdf_name=pdf.name,
                        pdf_sha256=digest, physical_page=page_number, printed_page=str(page_number),
                        font_xref=xref,
                        source_record=source, actual="ㄅ" if page_number == 1 and slot == 0 else
                            "ㄆ" if page_number == 1 else "",
                        state="PASS" if page_number == 1 else "ACTUAL_DECODE_ERROR",
                        **dict(zip(("x0", "y0", "x1", "y1"), char[3])))
                    rows.append(row)
    # Independently encode the existing TTF group-ID contract, not a call into
    # group construction or any value obtained from the collector under test.
    for index, letter in enumerate("ABC"):
        payload = {"kind": "TTF_GLYF_SHA256",
                   "exact_key": original[index * 2]["source_record"]["TTF字形SHA256"]}
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        expected[letter] = dict(group_id="agr_" + hashlib.sha256(raw).hexdigest()[:24],
            ids=[f"pa01-{letter}-p{page}-s{slot}" for page in (1, 2) for slot in (0, 1)],
            exact_key=payload["exact_key"])
    evidence = root / "output" / "actual" / "fingerprint-data"
    actual.ensure_user_evidence_files(evidence)
    actual._write_csv(evidence / actual.USER_GLYF_FILE, actual.USER_GLYF_HEADERS,
        [dict(glyph_sha256=item["exact_key"], bopomofo="ㄅ", verification_level="VERIFIED_EXACT_GLYPH",
            source_count="2", source_examples="fixture-one|fixture-two", updated_at="fixture", notes="")
         for item in expected.values()])
    context = {"session_id": "pa01-isolated", "pdfs": {pdf.name: {"pdf": str(pdf), "pdf_sha256": digest}}}
    return SimpleNamespace(rows=rows, expected=expected, context=context, evidence=evidence,
        repository=library.GlobalExactGlyphRepository(root / "global.sqlite3"), pdf=pdf)


def collect(fixture, **kwargs):
    return learning.collect_learning_candidates(fixture.rows, fixture.context,
        repository=fixture.repository, evidence_root=fixture.evidence, **kwargs)


def test_target_projection_keeps_all_peers_and_position_admissions(tmp_path, monkeypatch):
    fixture = target_fixture(tmp_path)
    # Inject only a common-history diagnostic for this routing assertion; all
    # source identity/admission and native visibility checks execute unchanged.
    common = {"status": "SURFACE_CONFIRMATION_UNVERIFIED", "reason": "fixture historical gap"}
    monkeypatch.setattr(learning, "_confirmed_surface_positions", lambda *args: (set(), [dict(common)]))
    full = collect(fixture)
    assert len(full["groups"]) == 3
    assert [row["occurrence_id"] for row in full["suppressed"]] == [
        f"pa01-{letter}-p1-s0" for letter in "ABC"]
    seen, original = [], promotion.visual_source_descriptors
    def descriptors(group, ids, *args, **kwargs):
        seen.append((group["group_id"], list(ids)))
        return original(group, ids, *args, **kwargs)
    monkeypatch.setattr(promotion, "visual_source_descriptors", descriptors)
    for letter in "ABC":
        seen.clear()
        wanted = fixture.expected[letter]
        target = collect(fixture, target_group_id=wanted["group_id"])
        full_group = next(group for group in full["groups"] if group["group_id"] == wanted["group_id"])
        assert target["groups"] == [full_group]
        assert [row["occurrence_id"] for row in full_group["members"]] == wanted["ids"]
        assert [row["occurrence_id"] for row in full_group["candidate_members"]] == wanted["ids"][1:]
        assert full_group["pending_occurrence_ids"] == wanted["ids"][2:]
        assert seen == [(wanted["group_id"], wanted["ids"])]
        assert target["diagnostics"][0] == full["diagnostics"][0]
        assert target["suppressed"] == [dict(occurrence_id=wanted["ids"][0], reason="ALREADY_REUSABLE")]
    group_a = next(group for group in full["groups"] if group["group_id"] == fixture.expected["A"]["group_id"])
    assert [row["status"] for row in group_a["source_admissions"]] == ["ELIGIBLE"] * 3 + ["NON_GLOBAL_ELIGIBLE"]
    assert "OVERPAINT" in group_a["source_admissions"][-1]["reason"]
    unknown = collect(fixture, target_group_id="unknown")
    assert unknown["groups"] == [] and unknown["suppressed"] == []
    assert unknown["diagnostics"] == full["diagnostics"][:1]
    assert not fixture.repository.path.exists()


def test_target_expected_independence_and_fresh_project_sources(tmp_path):
    fixture = target_fixture(tmp_path)
    selector = fixture.expected["B"]["group_id"]
    before = collect(fixture, target_group_id=selector)
    for row in fixture.rows:
        row["expected_set"], row["expected_evidence"] = ["ㄇ"], "independent expected mutation"
    after = collect(fixture, target_group_id=selector)
    # The collector returns ledger rows including display-only expected fields;
    # compare every decision/identity field and independently confirm no actual changed.
    for group in after["groups"]:
        for key in ("members", "candidate_members"):
            for row in group[key]:
                row.pop("expected_set", None); row.pop("expected_evidence", None)
    assert after == before
    actual._write_csv(fixture.evidence / actual.USER_GLYF_FILE, actual.USER_GLYF_HEADERS, [])
    fresh = collect(fixture, target_group_id=selector)
    assert fresh["suppressed"] == []
    assert [row["occurrence_id"] for row in fresh["groups"][0]["candidate_members"]] == fixture.expected["B"]["ids"]


def test_service_selector_none_full_invalid_and_unknown(tmp_path):
    fixture = target_fixture(tmp_path)
    assert collect(fixture, target_group_id=None) == collect(fixture)
    for invalid in ("", " \t", 0, False, [], {}):
        with pytest.raises(ValueError, match="target group ID"):
            collect(fixture, target_group_id=invalid)
    assert collect(fixture, target_group_id="missing") == dict(groups=[], suppressed=[], diagnostics=[])


def test_target_fresh_quarantine_and_global_failure_still_validate(tmp_path):
    fixture = target_fixture(tmp_path)
    selector = fixture.expected["B"]["group_id"]
    before = collect(fixture, target_group_id=selector)
    assert before["suppressed"] == [dict(occurrence_id="pa01-B-p1-s0", reason="ALREADY_REUSABLE")]
    actual._write_csv(fixture.evidence / actual.GLYPH_CONFLICT_FILE, actual.GLYPH_CONFLICT_HEADERS,
        [dict(kind="TTF_GLYF_SHA256", glyph_sha256=fixture.expected["B"]["exact_key"],
              status="GLYPH_TRUTH_CONFLICT", readings="ㄅ|ㄆ", source_occurrence_ids="occ-a|occ-b")])
    after = collect(fixture, target_group_id=selector)
    assert after["suppressed"] == []
    assert [row["occurrence_id"] for row in after["groups"][0]["candidate_members"]] == fixture.expected["B"]["ids"]
    assert after["groups"] == [group for group in collect(fixture)["groups"] if group["group_id"] == selector]
    fixture.repository.path.write_bytes(b"not a SQLite database")
    # Even an unknown target cannot bypass the common Global inspection gate.
    for group_id in (selector, "unknown"):
        with pytest.raises(library.GlobalLibraryError):
            collect(fixture, target_group_id=group_id)


def worker_fixture(tmp_path, monkeypatch):
    fixture = target_fixture(tmp_path)
    snapshot = SimpleNamespace(ledger=fixture.rows, manifest={}, db={}, hashes={})
    loads = []
    def load(**kwargs):
        loads.append(kwargs)
        return snapshot
    state = ReviewOperationState(tmp_path, service=SimpleNamespace(load_resume_snapshot=load))
    monkeypatch.setattr(sp, "_global_direct_source_context", lambda *args: fixture.context)
    monkeypatch.setattr(sp, "project_actual_evidence_root", lambda *args: fixture.evidence)
    monkeypatch.setattr(library.GlobalExactGlyphRepository, "resolved", classmethod(lambda cls: fixture.repository))
    # Route only the output-path lookup; the real source-safety function runs.
    original = actual.actual_visual_source_safety
    monkeypatch.setattr(actual, "actual_visual_source_safety", lambda row, output: original(row))
    return fixture, snapshot, state, loads


def dispatch(state, operation, **kwargs):
    return state.dispatch(operation, dict(expected_manifest={}, expected_db={}, **kwargs), lambda text: None, lambda: False)


def test_worker_load_full_target_selector_review_fallback_and_freshness(tmp_path, monkeypatch):
    fixture, snapshot, state, loads = worker_fixture(tmp_path, monkeypatch)
    seen, original = [], learning.collect_learning_candidates
    def collector(*args, **kwargs):
        seen.append(kwargs.get("target_group_id", "FULL"))
        return original(*args, **kwargs)
    monkeypatch.setattr(learning, "collect_learning_candidates", collector)
    loaded = dispatch(state, "load_learning")
    assert len(loaded["candidates"]["groups"]) == 3 and seen == ["FULL"]
    selector = fixture.expected["B"]["group_id"]
    selected = dispatch(state, "prepare_learning_target", group_id=selector, review_id="missing-review")
    assert selected["entry"]["occurrence_id"] == "pa01-B-p1-s1" and selected["surface"] is None
    assert seen[-1] == selector
    selected = dispatch(state, "prepare_learning_target", group_id=selector, review_id="review-pa01-B-p2-s0")
    assert selected["entry"]["occurrence_id"] == "pa01-B-p2-s0"
    snapshot.ledger = [row for row in snapshot.ledger if "-B-" not in row["occurrence_id"]]
    with pytest.raises(ValueError, match="候選已變動"):
        dispatch(state, "prepare_learning_target", group_id=selector)
    assert len(loads) == 4


def test_worker_invalid_selector_never_collects_or_loads(tmp_path, monkeypatch):
    fixture, snapshot, state, loads = worker_fixture(tmp_path, monkeypatch)
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid single-group selector reached collection")
    monkeypatch.setattr(learning, "collect_learning_candidates", forbidden)
    for invalid in (None, "", " \n", 1, False, [], {}):
        with pytest.raises(ValueError):
            dispatch(state, "prepare_learning_target", group_id=invalid)
    assert loads == []


def test_worker_all_suppressed_and_unknown_refuse(tmp_path, monkeypatch):
    fixture, snapshot, state, loads = worker_fixture(tmp_path, monkeypatch)
    selector = fixture.expected["B"]["group_id"]
    dispatch(state, "load_learning")
    for row in fixture.rows:
        row["actual"] = "ㄅ"
    for group_id in (selector, "unknown"):
        with pytest.raises(ValueError, match="候選已變動"):
            dispatch(state, "prepare_learning_target", group_id=group_id)
    assert not fixture.repository.path.exists()


def test_real_worker_source_change_after_list_rejects_same_size_bytes(tmp_path, monkeypatch):
    from tests.test_glyph_learning_service import _isolated, _project
    from review_save_service import ReviewSaveService
    root = _isolated(tmp_path, monkeypatch)
    output = _project(root, monkeypatch)
    snapshot = ReviewSaveService(output).load_resume_snapshot()
    state = ReviewOperationState(output)
    displayed = dict(expected_manifest=snapshot.manifest, expected_db=snapshot.db)
    loaded = state.dispatch("load_learning", displayed, lambda text: None, lambda: False)
    selector = loaded["candidates"]["groups"][0]["group_id"]
    pdf = Path(snapshot.ledger[0]["pdf"])
    raw = pdf.read_bytes()
    pdf.write_bytes(raw[:-1] + (b"\r" if raw[-1:] != b"\r" else b"\n"))
    assert pdf.stat().st_size == len(raw)
    with pytest.raises(ValueError, match="SHA|來源|SOURCE|PDF|變更|變動"):
        state.dispatch("prepare_learning_target", dict(displayed, group_id=selector), lambda text: None, lambda: False)
    assert not library.GlobalExactGlyphRepository.resolved().path.exists()


def test_real_worker_prepared_source_change_never_writes_truth(tmp_path, monkeypatch):
    from tests.test_glyph_learning_gui import prepared_worker
    output, snapshot, state, prepared = prepared_worker(tmp_path, monkeypatch)
    pdf = Path(snapshot.ledger[0]["pdf"])
    raw = pdf.read_bytes()
    pdf.write_bytes(raw[:-1] + (b"\r" if raw[-1:] != b"\r" else b"\n"))
    assert pdf.stat().st_size == len(raw)
    with pytest.raises(ValueError):
        state.dispatch("confirm_learning", {"plan_token": prepared["plan_token"]}, lambda text: None, lambda: False)
    assert not library.GlobalExactGlyphRepository.resolved().path.exists()
    evidence = sp.project_actual_evidence_root(output)
    assert not (evidence / promotion.PROJECT_TRANSACTION_FILE).exists()
    assert not (evidence / promotion.GLOBAL_PROMOTION_OUTBOX_FILE).exists()
    assert not list((evidence / learning.DIRECTORY).glob("*.json"))
    assert sp.json_load_strict(output / "人工判定資料庫.json") == snapshot.db


def test_worker_surface_branch_preserves_selected_review(tmp_path, monkeypatch):
    fixture, snapshot, state, loads = worker_fixture(tmp_path, monkeypatch)
    # The actual covered occurrence must take the surface branch. The surface
    # service is a routing double only; admission/native safety stays genuine.
    seen = []
    monkeypatch.setattr(sp, "prepare_surface_actual_correction",
        lambda output, review_id: seen.append(review_id) or {"routing": review_id})
    selected = dispatch(state, "prepare_learning_target", group_id=fixture.expected["A"]["group_id"],
        review_id="review-pa01-A-p2-s1")
    assert seen == ["review-pa01-A-p2-s1"]
    assert selected["surface"] == {"routing": "review-pa01-A-p2-s1"}
    assert selected["entry"]["occurrence_id"] == "pa01-A-p2-s1"
