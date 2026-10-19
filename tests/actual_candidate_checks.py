"""Targeted actual-only admission tests; no production data or PDF writes."""
from __future__ import annotations

import csv
import json
import hashlib
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import pytest
from openpyxl import Workbook, load_workbook

import cross_version_compat as compat
import export_zhuyin_readings as decoder
import runtime_source_validation as source
import actual_review as actual
from ttf_symbol_recombination import ExactSymbolRecombinationModel
from ttf_zhuyin_shape_decoder import contour_group_signature
from tests.test_global_exact_glyph_read_reuse_v580 import (
    SHA_A, _DecodeDocument, _DecodeTrueTypeInspector, _absent_snapshot,
    _identity, _read_actual_and_audit, _record, _snapshot,
)


def csv_file(path, fields, rows):
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return path


def check_four_historical_proposals_have_no_truth_or_training_qualification(font, gid, old, tmp_path):
    # Frozen historical assertions keep exercising rejection even when the
    # shipped candidate table is corrected or the ambiguous value is cleared.
    source_font = "YenRoundZuinnEG-Bd" if font == "YenRoundZuinnEG-Md" else font
    path = csv_file(tmp_path / f"legacy-{gid}.csv", ["font", "zhuyin_component_id", "bopomofo", "verification"], [
        {"font": source_font, "zhuyin_component_id": gid, "bopomofo": old, "verification": "historical v1.7 proposal"},
    ])
    exact = decoder.load_map(path)
    groups = decoder.load_compatibility_groups(decoder.DEFAULT_GROUPS)
    grouped, _ = decoder.build_group_map(exact, groups)
    rec, _, _, _ = decoder.lookup_reading(font, gid, exact, grouped, groups)
    assert rec["bopomofo"] == old
    assert rec["candidate_only"]
    assert not rec.get("evidence_roots")
    assert decoder.training_reading([{"initial_bopomofo": old, **rec}]) == ""


def check_all_ten_real_corrections_keep_font_gid_and_exact_hash_scope(index):
    rows = list(csv.DictReader(decoder.DEFAULT_MAPPING_CORRECTIONS.open(encoding="utf-8-sig")))
    row = rows[index]
    corrections = decoder.load_verified_mapping_corrections(decoder.DEFAULT_MAPPING_CORRECTIONS)
    font, gid, sha = row["font"], int(row["component_id"]), row["glyph_sha256"]
    accepted = decoder.lookup_verified_mapping_correction(font, gid, sha, corrections)
    assert accepted["bopomofo"] == row["new_reading"]
    assert accepted["evidence_roots"]
    assert accepted["donation_eligible"] is False
    assert not decoder.lookup_verified_mapping_correction(font, gid, "0" * 64, corrections)
    assert not decoder.lookup_verified_mapping_correction(font + "-other", gid, sha, corrections)
    assert not decoder.lookup_verified_mapping_correction(font, gid + 1, sha, corrections)


def check_calibrated_graphics_keep_original_claims_and_scoped_roles(tmp_path):
    rows = list(csv.DictReader(decoder.DEFAULT_MAPPING_CORRECTIONS.open(encoding="utf-8-sig")))
    # Frozen from baseline 02ac433's parsed original ten assertions; no Git
    # history/network is needed by the test or by runtime.
    canonical = json.dumps(rows[:10], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert hashlib.sha256(canonical).hexdigest() == "07d39c7c514511238bf63682db34d3d2c4bca4e1276157d3a5c1054c264002d9"
    root = Path(decoder.__file__).resolve().parent
    proof_path = root / "docs/evidence/map-glyph-review-20261019/observations.json"
    if not proof_path.exists():
        assert len(rows) == 10  # Released pre-calibration contract only.
        return
    proof = json.loads(proof_path.read_text(encoding="utf-8"))
    assert len(rows) == 10 + proof["new_scope_rows"]
    corrections = decoder.load_verified_mapping_corrections(decoder.DEFAULT_MAPPING_CORRECTIONS)
    for observed in proof["observations"]:
        font, gid, sha = observed["font"], observed["component_id"], observed["glyph_sha256"]
        rec = decoder.lookup_verified_mapping_correction(font, gid, sha, corrections)
        assert rec["bopomofo"] == observed["reading"]
        assert rec["donation_eligible"] is False
        assert observed["human_confirmation"] is False
        assert observed["global_approval"] is False
        assert observed["new_global_independent_source"] is False
        assert not decoder.lookup_verified_mapping_correction(font + "-unbound", gid, sha, corrections)
        assert not decoder.lookup_verified_mapping_correction(font, gid, "0" * 64, corrections)
        for field in ("original_crop", "isolated_svg"):
            assert hashlib.sha256((root / observed[field]).read_bytes()).hexdigest() == observed[field + "_sha256"]


def check_same_name_gid_other_variant_and_local_override_cannot_train():
    admitted = {"initial_bopomofo": "ㄅ", "training_eligible": True, "evidence_roots": ("direct:sha-A",)}
    assert decoder.training_reading([admitted]) == "ㄅ"
    assert not decoder.training_reading([admitted, {"initial_bopomofo": "", "training_eligible": False}])
    assert not decoder.training_reading([{**admitted, "training_eligible": False}])
    assert not decoder.training_reading([{**admitted, "evidence_roots": ()}])


def check_transform_target_binding_cannot_launder_unbound_source():
    transforms = {("Target", 7): {"source_font": "Source", "source_gid": 3,
                                 "target_hashes": (SHA_A,), "bopomofo": "ㄅ"}}
    source_map = {("Source", 3): {"bopomofo": "ㄅ", "candidate_only": True}}
    assert decoder.lookup_verified_transform("Target", 7, transforms, source_map, SHA_A) is None


def check_table_agreement_and_long_notes_are_not_truth_and_duplicate_conflict_is_closed(tmp_path):
    path = csv_file(tmp_path / "legacy.csv", ["font", "zhuyin_component_id", "bopomofo", "notes"], [
        {"font": "F", "zhuyin_component_id": 7, "bopomofo": "ㄅ", "notes": "verified " * 100},
        {"font": "F", "zhuyin_component_id": 7, "bopomofo": "ㄆ"},
        {"font": "F", "zhuyin_component_id": 7, "bopomofo": "ㄅ"},
    ])
    assert not decoder.load_map(path)


def check_unbound_automatic_outline_and_templates_are_excluded_before_model_loading(tmp_path):
    sig = "a" * 40
    path = csv_file(tmp_path / "outline.csv", ["outline_signature", "bopomofo", "verification"], [
        {"outline_signature": sig, "bopomofo": "ㄅ", "verification": "v3.7 decoded labels; OOF PASS"},
    ])
    assert not decoder.load_verified_outline_signatures(path)
    path = csv_file(tmp_path / "templates.csv", ["template_type", "outline_signature", "label", "support_keys", "support_fonts", "validation"], [
        {"template_type": "symbol", "outline_signature": sig, "label": "ㄅ", "support_keys": 99,
         "support_fonts": 99, "validation": "static full-data audit + key LOO + cross-book holdout; support>=2"},
    ])
    assert not ExactSymbolRecombinationModel.load_csv(path).templates
    # A directly observed isolated symbol is a distinct surviving truth root.
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"symbol,{sig},ㄅ,1,1,direct_isolated_visual_v39\n")
    templates = ExactSymbolRecombinationModel.load_csv(path).templates
    assert len(templates) == 1 and templates[0].evidence_ref


def run_ttf_fixture(tmp_path, *, snapshot=None, static=None, symbol_model=None):
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"isolated acquisition fixture")
    out = tmp_path / "actual.xlsx"
    mapping = csv_file(tmp_path / "map.csv", ["font", "zhuyin_component_id", "bopomofo"], [
        {"font": "FixtureFont", "zhuyin_component_id": 7, "bopomofo": "ㄇ"},
    ])
    patches = [
        patch.object(decoder.fitz, "open", return_value=_DecodeDocument()),
        patch.object(decoder, "TrueTypeGlyphInspector", _DecodeTrueTypeInspector),
        patch.object(decoder, "load_verified_glyf_fingerprints", return_value=static or {}),
        patch.object(decoder, "cross_validate_font", side_effect=AssertionError("unbound label entered training")),
        patch.object(decoder, "train_model", side_effect=AssertionError("unbound label entered model")),
    ]
    from contextlib import ExitStack
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        if symbol_model:
            stack.enter_context(patch.object(decoder.ExactSymbolRecombinationModel, "load_csv", return_value=symbol_model))
        decoder.decode(pdf, out, mapping, tmp_path / "groups.csv", dynamic_evidence_root=tmp_path / "dynamic",
                       global_snapshot=snapshot or _absent_snapshot())
    return out, _read_actual_and_audit(out)


def check_unbound_candidate_never_enters_actual_training_or_fingerprint_bridge(tmp_path):
    out, (actual, _) = run_ttf_fixture(tmp_path)
    assert not actual["實際注音"]
    with closing(load_workbook(out, read_only=True)) as wb:
        assert wb["舊對照候選稽核"].max_row == 2
        assert wb["TTF跨字型指紋橋接"].max_row == 1


def check_changed_project_source_is_rejected_before_training_and_output(tmp_path):
    def changed_after_capture(path, *, snapshot=None):
        path.write_bytes(path.read_bytes() + b"\r\n")
        return {}
    with patch.object(decoder, "load_user_verified_glyf", side_effect=changed_after_capture):
        with pytest.raises(ValueError, match="snapshot source changed"):
            run_ttf_fixture(tmp_path)
    assert not (tmp_path / "actual.xlsx").exists()


def check_project_readers_consume_one_validated_snapshot(tmp_path):
    actual.ensure_user_evidence_files(tmp_path)
    csv_file(tmp_path / actual.USER_GLYF_FILE, actual.USER_GLYF_HEADERS, [{
        "glyph_sha256": SHA_A, "bopomofo": "ㄅ", "verification_level": "VERIFIED_EXACT_GLYPH",
        "source_count": 2, "notes": "existing local truth",
    }])
    snapshot = actual.load_dynamic_actual_snapshot(tmp_path, read_only=True)
    with patch.object(actual, "_read_csv", side_effect=AssertionError("live rows read after capture")):
        assert actual.load_user_verified_glyf(tmp_path / actual.USER_GLYF_FILE, snapshot=snapshot)[SHA_A]["bopomofo"] == "ㄅ"
        assert actual.load_user_verified_cff(tmp_path / actual.USER_CFF_FILE, snapshot=snapshot) == {}
        assert actual.load_glyph_truth_quarantine(tmp_path, snapshot=snapshot) == {"ttf": set(), "cff": set()}
        assert decoder.load_actual_occurrence_overrides(tmp_path / actual.OCCURRENCE_OVERRIDE_FILE, tmp_path / "source.pdf", snapshot=snapshot) == []


def check_static_only_exact_can_replace_candidate_before_training(tmp_path):
    _, (actual, _) = run_ttf_fixture(tmp_path, static={SHA_A: {
        "bopomofo": "ㄅ", "source_font": "direct isolated", "source_gid": 7,
        "evidence_roots": ("direct-isolated-glyph:sha-A",),
    }})
    assert actual["實際注音"] == "ㄅ"
    assert actual["解碼依據"] == "Project/static exact TTF glyf SHA-256"


def check_independent_symbol_fallback_can_succeed_without_clearing_global_quarantine(tmp_path):
    snapshot = _snapshot(quarantined=(_record(_identity(SHA_A), conflict=("ㄅ", "ㄆ")),))
    # Acquiring geometry is isolated; execution still crosses the real initial,
    # training/donor and all-xref fallback paths. No Global state is modified.
    contours = [[(0, 0, True), (100, 0, True), (100, 100, True), (0, 100, True)]]
    path = csv_file(tmp_path / "symbols.csv", ["template_type", "outline_signature", "label", "support_keys", "support_fonts", "validation"], [
        {"template_type": "symbol", "outline_signature": contour_group_signature(contours), "label": "ㄈ",
         "support_keys": 1, "support_fonts": 1, "validation": "direct_isolated_visual_v39"},
    ])
    model = ExactSymbolRecombinationModel.load_csv(path)
    with patch.object(_DecodeTrueTypeInspector, "simple_contours", return_value=contours):
        _, (actual, audit) = run_ttf_fixture(tmp_path, snapshot=snapshot, symbol_model=model)
    assert actual["實際注音"] == "ㄈ"
    assert actual["解碼依據"] == "TTF exact注音符號重組"
    assert audit[0]["處置"] == decoder.GLOBAL_EXACT_RUNTIME_CONFLICT_LABEL
    assert snapshot.quarantined_identities


def check_old_actual_cannot_be_resealed_and_fresh_epoch_two_can(tmp_path):
    path = tmp_path / "old.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "v5.2中繼資料"
    ws.append(["actual_asset_fingerprint", "old"])
    ws.append(["actual_asset_fingerprint_components", json.dumps({"actual_decoder_semantics_epoch": "1"})])
    wb.save(path)
    payload = {"fingerprint": "new", "components": {"actual_decoder_semantics_epoch": "2"}}
    before = path.read_bytes()
    with pytest.raises(ValueError, match="fresh decoding"):
        source.rewrite_actual_workbook_fingerprint(path, payload)
    assert path.read_bytes() == before
    ws.append(["actual_decoder_semantics_epoch", "2"])
    ws["B2"] = json.dumps({"actual_decoder_semantics_epoch": "2"})
    wb.save(path)
    source.rewrite_actual_workbook_fingerprint(path, payload)
    with closing(load_workbook(path, read_only=True)) as saved:
        assert saved["v5.2中繼資料"]["B1"].value == "new"
    assert compat.EXPECTED_RESOLVER_SEMANTICS_EPOCH == "1"


def run_admission_checks(tmp_path):
    cases = [
        ("DFPBiaoKai-W5-ZhuIn-BFW", 14598, "ㄨㄟˊ"),
        ("DFPBiaoKai-W5-ZhuIn-BFW", 14674, "ㄉㄢˇ"),
        ("DFPBiaoKai-W5-ZhuIn-BFW", 15284, "ㄕㄡˊ"),
        ("YenRoundZuinnEG-Md", 14670, "ㄌㄨˋ"),
    ]
    for font, gid, old in cases:
        check_four_historical_proposals_have_no_truth_or_training_qualification(font, gid, old, tmp_path)
    for index in range(10):
        check_all_ten_real_corrections_keep_font_gid_and_exact_hash_scope(index)
    check_same_name_gid_other_variant_and_local_override_cannot_train()
    check_transform_target_binding_cannot_launder_unbound_source()
    checks = [
        check_table_agreement_and_long_notes_are_not_truth_and_duplicate_conflict_is_closed,
        check_unbound_automatic_outline_and_templates_are_excluded_before_model_loading,
        check_unbound_candidate_never_enters_actual_training_or_fingerprint_bridge,
        check_changed_project_source_is_rejected_before_training_and_output,
        check_project_readers_consume_one_validated_snapshot,
        check_calibrated_graphics_keep_original_claims_and_scoped_roles,
        check_static_only_exact_can_replace_candidate_before_training,
        check_independent_symbol_fallback_can_succeed_without_clearing_global_quarantine,
        check_old_actual_cannot_be_resealed_and_fresh_epoch_two_can,
    ]
    for index, check in enumerate(checks):
        isolated = tmp_path / str(index)
        isolated.mkdir()
        check(isolated)
