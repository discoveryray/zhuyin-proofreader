import json
from pathlib import Path

from cross_version_compat import (
    ACTUAL_DECODER_SEMANTICS_EPOCH,
    EXPECTED_RESOLVER_SEMANTICS_EPOCH,
    fingerprint_compatible,
    schema_compatible,
)
from runtime_source_validation import compute_actual_asset_fingerprint, compute_expected_asset_fingerprint


def test_schema_patch_family_is_compatible_but_minor_break_is_not():
    assert schema_compatible("2.6.0", "2.6.9")
    assert not schema_compatible("2.5.9", "2.6.0")


def test_actual_tool_and_source_code_drift_do_not_change_reuse_fingerprint(tmp_path: Path):
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"pdf")
    decoder = tmp_path / "decoder.py"
    decoder.write_text("print(1)", encoding="utf-8")
    report = {"ok": True, "manifest_schema_version": "2.6.0", "assets": [{"name": "map", "chain": "actual", "ok": True, "sha256": "1" * 64}]}
    first = compute_actual_asset_fingerprint(tmp_path, pdf, report, decoder_version="5.5.1", source_files=["decoder.py"])
    decoder.write_text("print(2)", encoding="utf-8")
    second = compute_actual_asset_fingerprint(tmp_path, pdf, report, decoder_version="5.6.0", source_files=["decoder.py"])
    assert first["fingerprint"] == second["fingerprint"]
    assert first["components"]["actual_decoder_source_hashes"] != second["components"]["actual_decoder_source_hashes"]


def test_expected_tool_and_source_code_drift_do_not_change_reuse_fingerprint(tmp_path: Path):
    resolver = tmp_path / "resolver.py"
    resolver.write_text("print(1)", encoding="utf-8")
    report = {"ok": True, "manifest_schema_version": "2.6.0", "assets": [{"name": "rules", "chain": "expected", "ok": True, "sha256": "2" * 64}]}
    first = compute_expected_asset_fingerprint(tmp_path, report, resolver_version="5.5.1", source_files=["resolver.py"])
    resolver.write_text("print(2)", encoding="utf-8")
    second = compute_expected_asset_fingerprint(tmp_path, report, resolver_version="5.6.0", source_files=["resolver.py"])
    assert first["fingerprint"] == second["fingerprint"]


def test_v551_style_actual_components_are_semantically_compatible_with_v56_payload():
    old = {
        "fingerprint_schema_version": "2.8.0",
        "pdf_sha256": "a" * 64,
        "actual_ledger_schema_version": "2.6.0",
        "decoder_version": "5.5.1",
        "actual_decoder_source_hashes": {"decoder.py": "1" * 64},
        "actual_asset_hashes": {"map": "2" * 64},
        "dynamic_actual_evidence_hashes": {"override": "3" * 64},
    }
    current = {
        "fingerprint": "new",
        "components": {
            **old,
            "fingerprint_schema_version": "2.9.0",
            "reuse_policy": "evidence_assets_v1",
            "actual_decoder_semantics_epoch": ACTUAL_DECODER_SEMANTICS_EPOCH,
            "decoder_version": "5.6.0",
            "actual_decoder_source_hashes": {"decoder.py": "9" * 64},
        },
    }
    assert fingerprint_compatible("old", json.dumps(old), current, chain="actual")


def test_v551_style_expected_components_are_semantically_compatible_with_v56_payload():
    old = {
        "fingerprint_schema_version": "2.6.0",
        "resolver_version": "5.5.1",
        "expected_resolver_source_hashes": {"resolver.py": "1" * 64},
        "expected_asset_hashes": {"dict": "2" * 64},
    }
    current = {
        "fingerprint": "new",
        "components": {
            **old,
            "fingerprint_schema_version": "2.7.0",
            "reuse_policy": "evidence_assets_v1",
            "expected_resolver_semantics_epoch": EXPECTED_RESOLVER_SEMANTICS_EPOCH,
            "resolver_version": "5.6.0",
            "expected_resolver_source_hashes": {"resolver.py": "9" * 64},
        },
    }
    assert fingerprint_compatible("old", json.dumps(old), current, chain="expected")
