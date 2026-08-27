from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import cross_version_compat as compat
import runtime_source_validation as source


def actual_components(**updates):
    components = {
        "fingerprint_schema_version": source.ACTUAL_FINGERPRINT_SCHEMA_VERSION,
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
        "fingerprint_schema_version": source.EXPECTED_FINGERPRINT_SCHEMA_VERSION,
        "reuse_policy": "evidence_assets_v1",
        "expected_resolver_semantics_epoch": compat.EXPECTED_RESOLVER_SEMANTICS_EPOCH,
        "expected_asset_hashes": {"dictionary": "e" * 64},
        "resolver_version": "5.6.2",
        "expected_resolver_source_hashes": {"resolver.py": "f" * 64},
    }
    components.update(updates)
    return components


def fingerprint(components, *, chain):
    reuse = compat.fingerprint_contract_components(components, chain=chain)
    raw = json.dumps(reuse, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def historical_fingerprint(components, *, digest_keys):
    reuse = {key: components[key] for key in digest_keys}
    raw = json.dumps(reuse, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def released_v562_fingerprint(components, *, chain):
    digest_keys = {
        "actual": (
            "reuse_policy",
            "pdf_sha256",
            "actual_asset_hashes",
            "dynamic_actual_evidence_hashes",
        ),
        "expected": (
            "reuse_policy",
            "expected_asset_hashes",
        ),
    }[chain]
    return historical_fingerprint(components, digest_keys=digest_keys)


def phase0b1_transitional_fingerprint(components, *, chain):
    digest_keys = {
        "actual": (
            "fingerprint_schema_version",
            "reuse_policy",
            "pdf_sha256",
            "actual_asset_hashes",
            "dynamic_actual_evidence_hashes",
        ),
        "expected": (
            "fingerprint_schema_version",
            "reuse_policy",
            "expected_asset_hashes",
        ),
    }[chain]
    return historical_fingerprint(components, digest_keys=digest_keys)


def compatible(stored, current, *, chain, stored_fingerprint="stored", current_fingerprint="current"):
    return compat.fingerprint_compatible(
        stored_fingerprint,
        stored,
        {"fingerprint": current_fingerprint, "components": current},
        chain=chain,
    )


def validation_report():
    return {
        "ok": True,
        "manifest_schema_version": "2.6.2",
        "assets": [
            {"name": "actual-map", "chain": "actual", "ok": True, "sha256": "1" * 64},
            {"name": "dictionary", "chain": "expected", "ok": True, "sha256": "2" * 64},
        ],
    }


def test_initial_epochs_are_explicit_required_contract_fields():
    assert compat.ACTUAL_DECODER_SEMANTICS_EPOCH == "1"
    assert compat.EXPECTED_RESOLVER_SEMANTICS_EPOCH == "1"
    assert "actual_decoder_semantics_epoch" in compat.FINGERPRINT_COMPATIBILITY_REQUIRED_KEYS["actual"]
    assert "expected_resolver_semantics_epoch" in compat.FINGERPRINT_COMPATIBILITY_REQUIRED_KEYS["expected"]


def test_actual_same_epoch_is_compatible_and_different_epoch_fails_closed():
    assert compatible(actual_components(), actual_components(), chain="actual")
    assert not compatible(
        actual_components(), actual_components(actual_decoder_semantics_epoch="2"), chain="actual"
    )


def test_expected_same_epoch_is_compatible_and_different_epoch_fails_closed():
    assert compatible(expected_components(), expected_components(), chain="expected")
    assert not compatible(
        expected_components(), expected_components(expected_resolver_semantics_epoch="2"), chain="expected"
    )


def test_missing_current_epoch_and_blank_stored_epoch_fail_closed():
    current_actual = actual_components()
    current_actual.pop("actual_decoder_semantics_epoch")
    assert not compatible(actual_components(), current_actual, chain="actual")
    assert not compatible(
        actual_components(actual_decoder_semantics_epoch=""), actual_components(), chain="actual"
    )

    current_expected = expected_components()
    current_expected.pop("expected_resolver_semantics_epoch")
    assert not compatible(expected_components(), current_expected, chain="expected")
    assert not compatible(
        expected_components(expected_resolver_semantics_epoch=""), expected_components(), chain="expected"
    )


def test_released_v562_actual_digest_maps_missing_epoch_to_initial_epoch():
    stored = actual_components()
    stored.pop("actual_decoder_semantics_epoch")
    old_digest = released_v562_fingerprint(stored, chain="actual")
    current = actual_components()
    new_digest = fingerprint(current, chain="actual")
    assert old_digest != new_digest
    assert compatible(
        stored,
        current,
        chain="actual",
        stored_fingerprint=old_digest,
        current_fingerprint=new_digest,
    )


def test_released_v562_expected_digest_maps_missing_epoch_to_initial_epoch():
    stored = expected_components()
    stored.pop("expected_resolver_semantics_epoch")
    old_digest = released_v562_fingerprint(stored, chain="expected")
    current = expected_components()
    new_digest = fingerprint(current, chain="expected")
    assert old_digest != new_digest
    assert compatible(
        stored,
        current,
        chain="expected",
        stored_fingerprint=old_digest,
        current_fingerprint=new_digest,
    )


def test_released_v562_actual_digest_is_incompatible_with_current_epoch_two():
    stored = actual_components()
    stored.pop("actual_decoder_semantics_epoch")
    assert not compatible(
        stored,
        actual_components(actual_decoder_semantics_epoch="2"),
        chain="actual",
        stored_fingerprint=released_v562_fingerprint(stored, chain="actual"),
    )


def test_released_v562_expected_digest_is_incompatible_with_current_epoch_two():
    stored = expected_components()
    stored.pop("expected_resolver_semantics_epoch")
    assert not compatible(
        stored,
        expected_components(expected_resolver_semantics_epoch="2"),
        chain="expected",
        stored_fingerprint=released_v562_fingerprint(stored, chain="expected"),
    )


def test_unknown_legacy_schema_or_policy_missing_epoch_is_incompatible():
    unknown_actual = actual_components(fingerprint_schema_version="2.9.1")
    unknown_actual.pop("actual_decoder_semantics_epoch")
    assert not compatible(
        unknown_actual,
        actual_components(),
        chain="actual",
        stored_fingerprint=released_v562_fingerprint(unknown_actual, chain="actual"),
    )
    wrong_policy_actual = actual_components(reuse_policy="evidence_assets_v2")
    wrong_policy_actual.pop("actual_decoder_semantics_epoch")
    assert not compatible(
        wrong_policy_actual,
        actual_components(),
        chain="actual",
        stored_fingerprint=released_v562_fingerprint(wrong_policy_actual, chain="actual"),
    )

    unknown_expected = expected_components(fingerprint_schema_version="2.7.1")
    unknown_expected.pop("expected_resolver_semantics_epoch")
    assert not compatible(
        unknown_expected,
        expected_components(),
        chain="expected",
        stored_fingerprint=released_v562_fingerprint(unknown_expected, chain="expected"),
    )
    wrong_policy_expected = expected_components(reuse_policy="evidence_assets_v2")
    wrong_policy_expected.pop("expected_resolver_semantics_epoch")
    assert not compatible(
        wrong_policy_expected,
        expected_components(),
        chain="expected",
        stored_fingerprint=released_v562_fingerprint(wrong_policy_expected, chain="expected"),
    )


def test_same_schema_policy_missing_epoch_with_invalid_profile_digest_fails_closed():
    stored_actual = actual_components()
    stored_actual.pop("actual_decoder_semantics_epoch")
    incomplete_actual_digest = historical_fingerprint(
        stored_actual,
        digest_keys=("reuse_policy", "pdf_sha256"),
    )
    assert not compatible(
        stored_actual,
        actual_components(),
        chain="actual",
        stored_fingerprint=incomplete_actual_digest,
    )

    stored_expected = expected_components()
    stored_expected.pop("expected_resolver_semantics_epoch")
    incomplete_expected_digest = historical_fingerprint(
        stored_expected,
        digest_keys=("reuse_policy",),
    )
    assert not compatible(
        stored_expected,
        expected_components(),
        chain="expected",
        stored_fingerprint=incomplete_expected_digest,
    )


def test_same_schema_policy_missing_epoch_with_arbitrary_fingerprint_fails_closed():
    stored_actual = actual_components()
    stored_actual.pop("actual_decoder_semantics_epoch")
    assert not compatible(
        stored_actual,
        actual_components(),
        chain="actual",
        stored_fingerprint="not-a-published-fingerprint",
    )

    stored_expected = expected_components()
    stored_expected.pop("expected_resolver_semantics_epoch")
    assert not compatible(
        stored_expected,
        expected_components(),
        chain="expected",
        stored_fingerprint="0" * 64,
    )


def test_post_epoch_digest_with_epoch_removed_from_stored_components_fails_closed():
    complete_actual = actual_components()
    post_epoch_actual_digest = fingerprint(complete_actual, chain="actual")
    stored_actual = dict(complete_actual)
    stored_actual.pop("actual_decoder_semantics_epoch")
    assert not compatible(
        stored_actual,
        complete_actual,
        chain="actual",
        stored_fingerprint=post_epoch_actual_digest,
        current_fingerprint=post_epoch_actual_digest,
    )

    complete_expected = expected_components()
    post_epoch_expected_digest = fingerprint(complete_expected, chain="expected")
    stored_expected = dict(complete_expected)
    stored_expected.pop("expected_resolver_semantics_epoch")
    assert not compatible(
        stored_expected,
        complete_expected,
        chain="expected",
        stored_fingerprint=post_epoch_expected_digest,
        current_fingerprint=post_epoch_expected_digest,
    )


def test_exact_equal_fingerprint_cannot_bypass_invalid_missing_epoch_profile():
    stored = actual_components()
    stored.pop("actual_decoder_semantics_epoch")
    invalid_digest = "f" * 64
    assert not compatible(
        stored,
        actual_components(),
        chain="actual",
        stored_fingerprint=invalid_digest,
        current_fingerprint=invalid_digest,
    )


def test_tool_and_source_hash_drift_remain_audit_only_when_epoch_is_unchanged():
    assert compatible(
        actual_components(),
        actual_components(decoder_version="9.0", actual_decoder_source_hashes={"decoder.py": "0" * 64}),
        chain="actual",
    )
    assert compatible(
        expected_components(),
        expected_components(
            resolver_version="9.0", expected_resolver_source_hashes={"resolver.py": "0" * 64}
        ),
        chain="expected",
    )


def test_actual_and_expected_epoch_contracts_are_independent():
    assert compatible(
        actual_components(expected_resolver_semantics_epoch="1"),
        actual_components(expected_resolver_semantics_epoch="2"),
        chain="actual",
    )
    assert compatible(
        expected_components(actual_decoder_semantics_epoch="1"),
        expected_components(actual_decoder_semantics_epoch="2"),
        chain="expected",
    )
    assert not compatible(
        actual_components(), actual_components(actual_decoder_semantics_epoch="2"), chain="actual"
    )
    assert not compatible(
        expected_components(), expected_components(expected_resolver_semantics_epoch="2"), chain="expected"
    )


def test_exact_fingerprint_equality_cannot_bypass_epoch_boundary():
    assert not compatible(
        actual_components(),
        actual_components(actual_decoder_semantics_epoch="2"),
        chain="actual",
        stored_fingerprint="same",
        current_fingerprint="same",
    )
    assert not compatible(
        expected_components(),
        expected_components(expected_resolver_semantics_epoch="2"),
        chain="expected",
        stored_fingerprint="same",
        current_fingerprint="same",
    )


def test_presentation_only_metadata_does_not_change_epoch_contract():
    assert compatible(
        actual_components(presentation_only_metadata="old"),
        actual_components(presentation_only_metadata="new"),
        chain="actual",
    )
    assert compatible(
        expected_components(presentation_only_metadata="old"),
        expected_components(presentation_only_metadata="new"),
        chain="expected",
    )


def test_actual_producer_writes_epoch_to_components_reuse_and_digest(tmp_path: Path):
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"pdf")
    decoder = tmp_path / "decoder.py"
    decoder.write_text("decoder", encoding="utf-8")
    first = source.compute_actual_asset_fingerprint(
        tmp_path,
        pdf,
        validation_report(),
        decoder_version="5.6.2",
        source_files=[decoder.name],
    )
    with patch.object(source, "ACTUAL_DECODER_SEMANTICS_EPOCH", "2"):
        second = source.compute_actual_asset_fingerprint(
            tmp_path,
            pdf,
            validation_report(),
            decoder_version="5.6.2",
            source_files=[decoder.name],
        )
    assert first["components"]["actual_decoder_semantics_epoch"] == "1"
    assert first["reuse_components"]["actual_decoder_semantics_epoch"] == "1"
    assert second["fingerprint"] != first["fingerprint"]


def test_expected_producer_writes_epoch_to_components_reuse_and_digest(tmp_path: Path):
    resolver = tmp_path / "resolver.py"
    resolver.write_text("resolver", encoding="utf-8")
    first = source.compute_expected_asset_fingerprint(
        tmp_path,
        validation_report(),
        resolver_version="5.6.2",
        source_files=[resolver.name],
    )
    with patch.object(source, "EXPECTED_RESOLVER_SEMANTICS_EPOCH", "2"):
        second = source.compute_expected_asset_fingerprint(
            tmp_path,
            validation_report(),
            resolver_version="5.6.2",
            source_files=[resolver.name],
        )
    assert first["components"]["expected_resolver_semantics_epoch"] == "1"
    assert first["reuse_components"]["expected_resolver_semantics_epoch"] == "1"
    assert second["fingerprint"] != first["fingerprint"]


def test_producer_epoch_bumps_do_not_cross_invalidate_chains(tmp_path: Path):
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"pdf")
    decoder = tmp_path / "decoder.py"
    decoder.write_text("decoder", encoding="utf-8")
    resolver = tmp_path / "resolver.py"
    resolver.write_text("resolver", encoding="utf-8")

    actual = source.compute_actual_asset_fingerprint(
        tmp_path, pdf, validation_report(), decoder_version="5.6.2", source_files=[decoder.name]
    )
    expected = source.compute_expected_asset_fingerprint(
        tmp_path, validation_report(), resolver_version="5.6.2", source_files=[resolver.name]
    )
    with patch.object(source, "ACTUAL_DECODER_SEMANTICS_EPOCH", "2"):
        expected_after_actual_bump = source.compute_expected_asset_fingerprint(
            tmp_path, validation_report(), resolver_version="5.6.2", source_files=[resolver.name]
        )
    with patch.object(source, "EXPECTED_RESOLVER_SEMANTICS_EPOCH", "2"):
        actual_after_expected_bump = source.compute_actual_asset_fingerprint(
            tmp_path, pdf, validation_report(), decoder_version="5.6.2", source_files=[decoder.name]
        )
    assert expected_after_actual_bump["fingerprint"] == expected["fingerprint"]
    assert actual_after_expected_bump["fingerprint"] == actual["fingerprint"]


def test_legacy_fingerprint_profiles_are_finite_chain_specific_and_frozen():
    assert set(compat.LEGACY_FINGERPRINT_PROFILES) == {"actual", "expected"}
    assert tuple(profile["profile_name"] for profile in compat.LEGACY_FINGERPRINT_PROFILES["actual"]) == (
        "released_v5.6.2",
        "phase0b1_transitional",
    )
    assert tuple(profile["profile_name"] for profile in compat.LEGACY_FINGERPRINT_PROFILES["expected"]) == (
        "released_v5.6.2",
        "phase0b1_transitional",
    )

    released_actual, transitional_actual = compat.LEGACY_FINGERPRINT_PROFILES["actual"]
    assert released_actual["fingerprint_schema_version"] == "2.9.0"
    assert released_actual["reuse_policy"] == "evidence_assets_v1"
    assert released_actual["epoch_key"] == "actual_decoder_semantics_epoch"
    assert released_actual["epoch_value"] == "1"
    assert released_actual["digest_keys"] == (
        "reuse_policy",
        "pdf_sha256",
        "actual_asset_hashes",
        "dynamic_actual_evidence_hashes",
    )
    assert transitional_actual["digest_keys"] == (
        "fingerprint_schema_version",
        *released_actual["digest_keys"],
    )

    released_expected, transitional_expected = compat.LEGACY_FINGERPRINT_PROFILES["expected"]
    assert released_expected["fingerprint_schema_version"] == "2.7.0"
    assert released_expected["reuse_policy"] == "evidence_assets_v1"
    assert released_expected["epoch_key"] == "expected_resolver_semantics_epoch"
    assert released_expected["epoch_value"] == "1"
    assert released_expected["digest_keys"] == (
        "reuse_policy",
        "expected_asset_hashes",
    )
    assert transitional_expected["digest_keys"] == (
        "fingerprint_schema_version",
        *released_expected["digest_keys"],
    )


def test_phase0b1_transitional_actual_digest_maps_only_to_initial_epoch():
    stored = actual_components()
    stored.pop("actual_decoder_semantics_epoch")
    transitional_digest = phase0b1_transitional_fingerprint(stored, chain="actual")
    assert transitional_digest != released_v562_fingerprint(stored, chain="actual")
    assert compatible(
        stored,
        actual_components(),
        chain="actual",
        stored_fingerprint=transitional_digest,
    )
    assert not compatible(
        stored,
        actual_components(actual_decoder_semantics_epoch="2"),
        chain="actual",
        stored_fingerprint=transitional_digest,
    )


def test_phase0b1_transitional_expected_digest_maps_only_to_initial_epoch():
    stored = expected_components()
    stored.pop("expected_resolver_semantics_epoch")
    transitional_digest = phase0b1_transitional_fingerprint(stored, chain="expected")
    assert transitional_digest != released_v562_fingerprint(stored, chain="expected")
    assert compatible(
        stored,
        expected_components(),
        chain="expected",
        stored_fingerprint=transitional_digest,
    )
    assert not compatible(
        stored,
        expected_components(expected_resolver_semantics_epoch="2"),
        chain="expected",
        stored_fingerprint=transitional_digest,
    )


def test_v551_schema_adapters_remain_pinned_to_epoch_one():
    stored_actual = actual_components(fingerprint_schema_version="2.8.0")
    stored_actual.pop("reuse_policy")
    stored_actual.pop("actual_decoder_semantics_epoch")
    assert compatible(stored_actual, actual_components(), chain="actual")
    assert not compatible(
        stored_actual, actual_components(actual_decoder_semantics_epoch="2"), chain="actual"
    )

    stored_expected = expected_components(fingerprint_schema_version="2.6.0")
    stored_expected.pop("reuse_policy")
    stored_expected.pop("expected_resolver_semantics_epoch")
    assert compatible(stored_expected, expected_components(), chain="expected")
    assert not compatible(
        stored_expected, expected_components(expected_resolver_semantics_epoch="2"), chain="expected"
    )
