from __future__ import annotations

import hashlib
import json
from unittest.mock import patch

import cross_version_compat as compat


def actual_components(**updates):
    components = {
        "fingerprint_schema_version": "2.9.0",
        "reuse_policy": "evidence_assets_v1",
        "actual_decoder_semantics_epoch": compat.ACTUAL_DECODER_SEMANTICS_EPOCH,
        "pdf_sha256": "a" * 64,
        "actual_asset_hashes": {"actual-map": "b" * 64},
        "dynamic_actual_evidence_hashes": {"scoped-glyphs": "c" * 64},
        "decoder_version": "5.6.2",
        "actual_decoder_source_hashes": {"decoder.py": "d" * 64},
    }
    components.update(updates)
    return components


def expected_components(**updates):
    components = {
        "fingerprint_schema_version": "2.7.0",
        "reuse_policy": "evidence_assets_v1",
        "expected_resolver_semantics_epoch": compat.EXPECTED_RESOLVER_SEMANTICS_EPOCH,
        "expected_asset_hashes": {"dictionary": "e" * 64},
        "resolver_version": "5.6.2",
        "expected_resolver_source_hashes": {"resolver.py": "f" * 64},
    }
    components.update(updates)
    return components


def compatible(old, new, *, chain, stored_fingerprint="stored", current_fingerprint="current"):
    return compat.fingerprint_compatible(
        stored_fingerprint,
        old,
        {"fingerprint": current_fingerprint, "components": new},
        chain=chain,
    )


def test_exact_fingerprint_equality_fast_path_still_validates_contract():
    assert compatible(actual_components(), actual_components(), chain="actual", stored_fingerprint="same", current_fingerprint="same")
    assert not compat.fingerprint_compatible(
        "same", {}, {"fingerprint": "same", "components": {}}, chain="actual"
    )


def test_reuse_policy_mismatch_fails_closed_for_both_chains():
    assert not compatible(actual_components(), actual_components(reuse_policy="evidence_assets_v2"), chain="actual")
    assert not compatible(
        expected_components(), expected_components(reuse_policy="evidence_assets_v2"), chain="expected"
    )


def test_unknown_fingerprint_schema_transition_fails_closed_for_both_chains():
    assert not compatible(
        actual_components(fingerprint_schema_version="2.9.0"),
        actual_components(fingerprint_schema_version="2.9.1"),
        chain="actual",
    )
    assert not compatible(
        expected_components(fingerprint_schema_version="2.7.0"),
        expected_components(fingerprint_schema_version="2.7.1"),
        chain="expected",
    )


def test_audit_only_source_and_tool_drift_remains_compatible():
    assert compatible(
        actual_components(),
        actual_components(decoder_version="5.7.0", actual_decoder_source_hashes={"decoder.py": "0" * 64}),
        chain="actual",
    )
    assert compatible(
        expected_components(),
        expected_components(
            resolver_version="5.7.0", expected_resolver_source_hashes={"resolver.py": "0" * 64}
        ),
        chain="expected",
    )


def test_actual_and_expected_contracts_remain_separate():
    old_actual = actual_components(expected_asset_hashes={"dictionary": "1" * 64})
    new_actual = actual_components(expected_asset_hashes={"dictionary": "2" * 64})
    assert compatible(old_actual, new_actual, chain="actual")

    old_expected = expected_components(
        pdf_sha256="1" * 64,
        actual_asset_hashes={"actual-map": "1" * 64},
        dynamic_actual_evidence_hashes={"scoped-glyphs": "1" * 64},
    )
    new_expected = expected_components(
        pdf_sha256="2" * 64,
        actual_asset_hashes={"actual-map": "2" * 64},
        dynamic_actual_evidence_hashes={"scoped-glyphs": "2" * 64},
    )
    assert compatible(old_expected, new_expected, chain="expected")

    assert not compatible(
        actual_components(), actual_components(actual_asset_hashes={"actual-map": "0" * 64}), chain="actual"
    )
    assert not compatible(
        expected_components(),
        expected_components(expected_asset_hashes={"dictionary": "0" * 64}),
        chain="expected",
    )


def test_future_declared_contract_key_cannot_be_silently_ignored():
    required = {
        **compat.FINGERPRINT_COMPATIBILITY_REQUIRED_KEYS,
        "actual": (*compat.FINGERPRINT_COMPATIBILITY_REQUIRED_KEYS["actual"], "future_required_key"),
    }
    with patch.object(compat, "FINGERPRINT_COMPATIBILITY_REQUIRED_KEYS", required):
        try:
            compat.fingerprint_contract_components(actual_components(), chain="actual")
        except ValueError as exc:
            assert "future_required_key" in str(exc)
        else:
            raise AssertionError("fingerprint producer silently omitted a newly required contract key")
        assert compatible(
            actual_components(future_required_key="contract-v1"),
            actual_components(future_required_key="contract-v1"),
            chain="actual",
        )
        assert not compatible(
            actual_components(future_required_key="contract-v1"),
            actual_components(future_required_key="contract-v2"),
            chain="actual",
        )
        assert not compatible(actual_components(), actual_components(), chain="actual")


def test_reuse_fingerprint_payload_contains_schema_policy_and_epoch_but_not_audit_metadata():
    actual = compat.fingerprint_contract_components(actual_components(), chain="actual")
    assert actual["fingerprint_schema_version"] == "2.9.0"
    assert actual["reuse_policy"] == "evidence_assets_v1"
    assert actual["actual_decoder_semantics_epoch"] == "1"
    assert "decoder_version" not in actual
    assert "actual_decoder_source_hashes" not in actual

    expected = compat.fingerprint_contract_components(expected_components(), chain="expected")
    assert expected["fingerprint_schema_version"] == "2.7.0"
    assert expected["reuse_policy"] == "evidence_assets_v1"
    assert expected["expected_resolver_semantics_epoch"] == "1"
    assert "resolver_version" not in expected
    assert "expected_resolver_source_hashes" not in expected


def test_v562_components_and_legacy_future_missing_fields_remain_compatible():
    stored = actual_components(
        decoder_version="5.6.0",
        actual_decoder_source_hashes={"decoder.py": "1" * 64},
        presentation_only_excel_sha256="1" * 64,
    )
    stored.pop("actual_decoder_semantics_epoch")
    current = actual_components(
        decoder_version="5.6.2",
        actual_decoder_source_hashes={"decoder.py": "2" * 64},
        future_audit_metadata="not-yet-a-contract",
        presentation_only_excel_sha256="2" * 64,
    )
    old_reuse_components = {
        key: stored[key]
        for key in (
            "fingerprint_schema_version",
            "reuse_policy",
            "pdf_sha256",
            "actual_asset_hashes",
            "dynamic_actual_evidence_hashes",
        )
    }
    current_reuse_components = compat.fingerprint_contract_components(current, chain="actual")
    stored_fingerprint = hashlib.sha256(
        json.dumps(old_reuse_components, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    current_fingerprint = hashlib.sha256(
        json.dumps(current_reuse_components, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert stored_fingerprint != current_fingerprint
    assert compatible(
        stored,
        current,
        chain="actual",
        stored_fingerprint=stored_fingerprint,
        current_fingerprint=current_fingerprint,
    )


def test_result_affecting_actual_evidence_changes_fail_closed():
    assert not compatible(actual_components(), actual_components(pdf_sha256="0" * 64), chain="actual")
    assert not compatible(
        actual_components(), actual_components(actual_asset_hashes={"actual-map": "0" * 64}), chain="actual"
    )
    assert not compatible(
        actual_components(),
        actual_components(dynamic_actual_evidence_hashes={"scoped-glyphs": "0" * 64}),
        chain="actual",
    )


def test_missing_required_contract_fields_fail_closed():
    stored_missing = actual_components()
    stored_missing.pop("reuse_policy")
    assert not compatible(stored_missing, actual_components(), chain="actual")

    current_missing = actual_components()
    current_missing.pop("reuse_policy")
    assert not compatible(actual_components(), current_missing, chain="actual")

    current_missing_epoch = actual_components()
    current_missing_epoch.pop("actual_decoder_semantics_epoch")
    assert not compatible(actual_components(), current_missing_epoch, chain="actual")


def test_presentation_only_metadata_is_not_a_semantic_cache_key():
    assert compatible(
        expected_components(presentation_only_excel_sha256="1" * 64),
        expected_components(presentation_only_excel_sha256="2" * 64),
        chain="expected",
    )
