"""Finite, non-window exact-byte roster and historical N/True regressions."""
import copy

import pytest

import occurrence_ledger as ol
import pdf_portability as p
import standalone_proofread as sp
from tests.test_pdf_portability_exact_batch import batch


def sealed(manifest):
    return sp.seal_manifest(manifest)


def roster_batch(tmp_path):
    manifest, proof = batch(tmp_path, "roster")
    manifest.update(session_id="synthetic", session_schema_version=ol.SESSION_SCHEMA_VERSION,
                    workbook_schema_version=ol.WORKBOOK_SCHEMA_VERSION,
                    ledger_schema_version=ol.LEDGER_SCHEMA_VERSION,
                    review_id_schema_version=ol.REVIEW_ID_SCHEMA_VERSION, records=[])
    for info in manifest["pdfs"]:
        for glyph in (1, 2):
            raw = {"physical_page": 1, "stable_key": "same", "font": "font", "font_xref": 3,
                   "glyph_id": glyph, "zhuyin_component_id": "", "char": "一",
                   "x0": 10, "y0": 20, "x1": 10, "y1": 30,
                   "ledger_schema_version": ol.LEDGER_SCHEMA_VERSION,
                   "workbook_schema_version": ol.WORKBOOK_SCHEMA_VERSION}
            oid, confidence, fallback = ol.make_occurrence_id(info["pdf_sha256"], raw)
            raw.update(occurrence_id=oid, review_id=ol.make_review_id(oid),
                       identity_confidence=confidence, identity_row_fallback="N")
            entry = {**raw, "pdf_sha256": info["pdf_sha256"], "source_record": raw,
                     "identity_row_fallback": True, "review_id_schema_version": ol.REVIEW_ID_SCHEMA_VERSION}
            manifest["records"].append(entry)
    return sealed(manifest), proof


def mapped(source, target, proof, root):
    pdf_map, geometry = p._match_pdfs(proof, source, target, root)
    return p._map_reviews(source, target, pdf_map, geometry)


@pytest.mark.parametrize("reverse_source", [False, True])
@pytest.mark.parametrize("reverse_target", [False, True])
def test_same_sha_full_roster_preserves_zero_width_and_order(tmp_path, reverse_source, reverse_target):
    source, proof = roster_batch(tmp_path)
    target = copy.deepcopy(source)
    if reverse_source:
        source["records"].reverse(); source["pdfs"].reverse(); proof["pdfs"].reverse()
    if reverse_target:
        target["records"].reverse(); target["pdfs"].reverse()
    sealed(source); sealed(target)
    before = p._canonical([source, target])
    result = mapped(source, target, proof, tmp_path)
    assert len(result) == 4 and len(result.fallback_adapter_ids) == 8
    assert all(a["occurrence_id"] == b["occurrence_id"] and a["x0"] == a["x1"] for a, b in result.values())
    assert p._canonical([source, target]) == before


def test_corrected_producer_and_historical_adapter_share_identity(tmp_path):
    source, proof = roster_batch(tmp_path)
    target = copy.deepcopy(source)
    for row in target["records"]:
        row["identity_row_fallback"] = False
    sealed(target)
    result = mapped(source, target, proof, tmp_path)
    assert len(result.fallback_adapter_ids) == 4
    assert all(side == "source" for side, oid, rid in result.fallback_adapter_ids)


@pytest.mark.parametrize("case", ["raw_true", "top_true_raw_bool", "top_string_true", "top_true_raw_zero",
    "unknown_flag", "missing_flag", "confidence", "collision", "fake_occurrence", "fake_review",
    "top_glyph", "missing_raw_glyph", "alias_conflict", "source_pdf_sha", "duplicate_base",
    "roster_missing", "raw_bbox_signature", "unknown_schema", "record_schema", "zero_height",
    "negative_width", "infinite_bbox", "fractional_page", "missing_matrix", "singular_matrix",
    "raw_stable_key", "raw_font_whitespace", "raw_xref_representation"])
def test_roster_contract_failures_are_not_hidden_by_anchor_fallback(tmp_path, case):
    source, proof = roster_batch(tmp_path)
    target = copy.deepcopy(source)
    entry = target["records"][0]; raw = entry["source_record"]
    if case == "raw_true": raw["identity_row_fallback"] = "Y"
    elif case == "top_true_raw_bool": raw["identity_row_fallback"] = False
    elif case == "top_string_true": entry["identity_row_fallback"] = "TRUE"
    elif case == "top_true_raw_zero": raw["identity_row_fallback"] = "0"
    elif case == "unknown_flag": raw["identity_row_fallback"] = "maybe"
    elif case == "missing_flag": del raw["identity_row_fallback"]
    elif case == "confidence": raw["identity_confidence"] = entry["identity_confidence"] = "FALLBACK_SOURCE_ROW"
    elif case == "collision": raw["identity_collision_base"] = "occ_collision"
    elif case == "fake_occurrence": raw["occurrence_id"] = entry["occurrence_id"] = "occ_forged"
    elif case == "fake_review": raw["review_id"] = entry["review_id"] = "rev_forged"
    elif case == "top_glyph": entry["glyph_id"] = 99
    elif case == "missing_raw_glyph": del raw["glyph_id"]
    elif case == "alias_conflict": raw["glyph_id_字形索引"] = 99
    elif case == "source_pdf_sha": raw["pdf_sha256"] = "f" * 64
    elif case == "duplicate_base": target["records"].append(copy.deepcopy(entry))
    elif case == "roster_missing": target["records"].pop()
    elif case == "unknown_schema": target["workbook_schema_version"] = "9.9.9"
    elif case == "record_schema": raw["workbook_schema_version"] = "9.9.9"
    elif case == "raw_stable_key": raw["stable_key"] = entry["stable_key"] = "same|row=999"
    elif case == "raw_font_whitespace": raw["font"] = entry["font"] = " font "
    elif case == "raw_xref_representation": raw["font_xref"] = entry["font_xref"] = "3"
    elif case in ("raw_bbox_signature", "zero_height", "negative_width", "infinite_bbox", "fractional_page"):
        field, value = {"raw_bbox_signature": ("y1", 30.00001), "zero_height": ("y1", 20),
                        "negative_width": ("x1", 9), "infinite_bbox": ("x0", "Infinity"),
                        "fractional_page": ("physical_page", 1.5)}[case]
        raw[field] = entry[field] = value
    elif case == "missing_matrix": del proof["pdfs"][0]["pages"][0]["display_matrix"]
    elif case == "singular_matrix": proof["pdfs"][0]["pages"][0]["display_matrix"] = [0]*6
    sealed(target)
    with pytest.raises(ValueError):
        mapped(source, target, proof, tmp_path)


def test_admission_binds_physical_checks_and_unchanged_seals_pages(tmp_path):
    source, proof = roster_batch(tmp_path)
    target = copy.deepcopy(source)
    pdf_map, geometry = p._match_pdfs(proof, source, target, tmp_path)
    source["records"][0]["state"] = "changed after admission"
    sealed(source)
    with pytest.raises(ValueError, match="admission"):
        p._map_reviews(source, target, pdf_map, geometry)
    # An ordinary dictionary has no physical admission to use zero-width IDs.
    with pytest.raises(ValueError, match="bbox"):
        p._map_reviews(source, target, pdf_map, dict(geometry))


def test_decisions_status_paths_do_not_select_identity(tmp_path):
    source, proof = roster_batch(tmp_path)
    target = copy.deepcopy(source)
    for entry in target["records"]:
        entry.update(actual="different actual", expected_set=["different expected"], state="independent", pdf="other")
        entry["source_record"].update(actual="raw actual", expected_set=["raw expected"], source_row_number=900)
    sealed(target)
    assert len(mapped(source, target, proof, tmp_path)) == 4


def test_exact_roster_still_requires_original_seal(tmp_path):
    source, proof = roster_batch(tmp_path)
    target = copy.deepcopy(source)
    target["session_id"] = "tampered without sealing"
    with pytest.raises(ValueError, match="payload hash"):
        mapped(source, target, proof, tmp_path)


def test_verified_geometry_cannot_be_changed_after_admission(tmp_path):
    source, proof = roster_batch(tmp_path)
    target = copy.deepcopy(source)
    pdf_map, geometry = p._match_pdfs(proof, source, target, tmp_path)
    geometry[next(iter(geometry))][1][0]["display_matrix"][4] += 1
    with pytest.raises(ValueError, match="admission"):
        p._map_reviews(source, target, pdf_map, geometry)


@pytest.mark.parametrize("side", ["source", "target"])
@pytest.mark.parametrize("case", ["lower_glyph_alias", "masked_char", "masked_page", "missing_formal_char",
    "missing_formal_page", "blank_formal_char", "blank_formal_page", "masked_stable_key",
    "masked_component", "top_raw_char_whitespace", "null_formal_char"])
def test_all_top_aliases_and_formal_projection_must_match_original(tmp_path, side, case):
    source, proof = roster_batch(tmp_path)
    target = copy.deepcopy(source)
    manifest = source if side == "source" else target
    entry = manifest["records"][0]
    if case == "lower_glyph_alias": entry["候選glyph_id"] = 99
    elif case == "masked_char": entry.update({"字元": "一", "char": "角"})
    elif case == "masked_page": entry.update({"實體頁碼": 1, "physical_page": 2})
    elif case == "missing_formal_char": entry["字元"] = entry.pop("char")
    elif case == "missing_formal_page": entry["實體頁碼"] = entry.pop("physical_page")
    elif case == "blank_formal_char": entry.update({"字元": "一", "char": ""})
    elif case == "blank_formal_page": entry.update({"實體頁碼": 1, "physical_page": ""})
    elif case == "masked_stable_key": entry.update({"穩定注音鍵": "same", "stable_key": "same|row=7"})
    elif case == "masked_component": entry.update({"注音元件ID": "", "候選注音元件ID": 99})
    elif case == "top_raw_char_whitespace": entry.update({"字元": "一", "char": " 一 "})
    elif case == "null_formal_char": entry.update({"字元": "一", "char": None})
    sealed(manifest)
    before = p._canonical([source, target])
    with pytest.raises(ValueError, match="alias|projection|identity|原始身份|合法數值"):
        mapped(source, target, proof, tmp_path)
    assert p._canonical([source, target]) == before


@pytest.mark.parametrize("side", ["source", "target"])
def test_consistent_registered_top_aliases_preserve_canonical_mapping(tmp_path, side):
    source, proof = roster_batch(tmp_path)
    target = copy.deepcopy(source)
    manifest = source if side == "source" else target
    for entry in manifest["records"]:
        for key, aliases in p._ROSTER_IDENTITY_ALIASES.items():
            for alias in aliases:
                entry[alias] = entry[key]
    sealed(manifest)
    before = p._canonical([source, target])
    result = mapped(source, target, proof, tmp_path)
    assert len(result) == 4 and len(result.fallback_adapter_ids) == 8
    assert p._canonical([source, target]) == before


@pytest.mark.parametrize("side", ["source", "target"])
@pytest.mark.parametrize("value", [None, ""], ids=["null", "empty"])
def test_nullable_component_uses_existing_empty_projection(tmp_path, side, value):
    source, proof = roster_batch(tmp_path)
    target = copy.deepcopy(source)
    for candidate in (source, target):
        for entry in candidate["records"]:
            entry["source_record"]["zhuyin_component_id"] = value
    manifest = source if side == "source" else target
    for entry in manifest["records"]:
        entry["zhuyin_component_id"] = value
    sealed(source)
    sealed(target)
    before = p._canonical([source, target])
    result = mapped(source, target, proof, tmp_path)
    assert len(result) == 4 and len(result.fallback_adapter_ids) == 8
    assert p._canonical([source, target]) == before


@pytest.mark.parametrize("value, expected", [(False, False), (True, True), ("N", False), ("Y", True),
    ("FALSE", False), ("TRUE", True), ("0", False), ("1", True), (0, False), (1, True)],
    ids=["bool_false", "bool_true", "N", "Y", "FALSE", "TRUE", "str0", "str1", "int0", "int1"])
def test_producer_boolean_projection_is_explicit_and_keeps_source_ids(value, expected):
    from tests.test_dual_evidence_architecture_v550 import _source
    source = _source(); source["identity_row_fallback"] = value
    before = copy.deepcopy(source)
    ledger = ol.build_occurrence_ledger([source], {source["occurrence_id"]: {"state": "ACTUAL_UNRESOLVED"}})
    assert ledger[0]["identity_row_fallback"] is expected
    assert ledger[0]["occurrence_id"] == source["occurrence_id"]
    assert ledger[0]["source_record"] == source == before


@pytest.mark.parametrize("value", ["maybe", "n", " N ", 2, 0.0, [], {}],
                         ids=["unknown", "lowercase", "space", "int2", "float0", "list", "dict"])
def test_unknown_producer_boolean_is_rejected(value):
    with pytest.raises(ol.LedgerError, match="identity_row_fallback"):
        ol.parse_identity_row_fallback(value)
