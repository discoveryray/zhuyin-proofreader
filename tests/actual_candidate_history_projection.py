"""Finite authorized actual-v2 deltas; the original historical oracles stay strict.

These are independent reviewed literals, not values learned from the current
implementation/manifest. Unknown assets, fields, bytes or producer edits fail.
Only product tests consume this module; no validator or runtime uses it.
"""
from __future__ import annotations

import ast
import copy
import csv
import hashlib
import io
import json


CURRENT_ASSET_SHA256 = {
    "zhuyin_component_map.csv": "2e4d3a9bf454e2653f2b7caed42ca7c82aa5cd77a845cc919bda28adf83a9998",
    "ttf_verified_outline_signatures.csv": "ad24d8fda6a9c6321d5d31ecbbf5b98b674e11f936dfed41f60824bc8d85b59b",
    "ttf_verified_mapping_corrections.csv": "dd5827bce2d7d5f08723e8613d4a52419fde79ef434ce59fd840edfc6732691a",
}
HISTORICAL_ASSET_SHA256 = {
    "zhuyin_component_map.csv": "53a5369e96f60a552aeae6503b1b2584741bf3930facedf1f4d273048c90b291",
    "ttf_verified_outline_signatures.csv": "d00e60764d976fbd19934f20399d5fa9326cae12039fbd45a5f9973368a8f4a0",
    "ttf_verified_mapping_corrections.csv": "e7176b983f027f18d1883026caa845191670b4a1bed640f7bb37d7c2c125040b",
}
HISTORICAL_COLUMNS = {
    "zhuyin_component_map.csv": ("font", "zhuyin_component_id", "bopomofo", "verification", "notes"),
    "ttf_verified_outline_signatures.csv": ("outline_signature", "bopomofo", "source_count", "source_examples", "verification", "notes"),
    "ttf_verified_mapping_corrections.csv": ("font", "component_id", "glyph_sha256", "old_reading", "new_reading", "source_book", "evidence", "verification", "notes"),
}


def _unique_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            assert key not in result, "duplicate historical-projection JSON field"
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique)


def _canonical_json(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def project_current_actual_assets(current_manifest, historical_manifest, assets):
    """Verify exactly three approved assets/deltas before restoring old bytes."""
    old = _unique_json(historical_manifest)
    current = _unique_json(current_manifest)
    assert old["tool_version"] == "5.7.0"
    assert current["tool_version"] == "5.8.0"
    assert len(old["assets"]) == 20
    expected = copy.deepcopy(old)
    expected["tool_version"] = "5.8.0"
    for spec in expected["assets"]:
        path = spec["path"]
        if path not in CURRENT_ASSET_SHA256:
            continue
        assert spec["sha256"] == HISTORICAL_ASSET_SHA256[path]
        spec["sha256"] = CURRENT_ASSET_SHA256[path]
        if path == "zhuyin_component_map.csv":
            spec["required_columns"] += ["legacy_bopomofo", "calibration_status", "target_bindings"]
            spec["duplicate_value_columns"] += ["legacy_bopomofo", "calibration_status", "target_bindings"]
            assert spec["bopomofo_columns"] == [{"column": "bopomofo", "mode": "single", "allow_empty": False}]
            spec["bopomofo_columns"][0]["allow_empty"] = True
            spec["bopomofo_columns"].append({"column": "legacy_bopomofo", "mode": "single", "allow_empty": False})
        elif path == "ttf_verified_outline_signatures.csv":
            spec["required_columns"] += ["legacy_verification", "revocation_evidence"]
            spec["duplicate_value_columns"] += ["legacy_verification", "revocation_evidence"]
        else:
            assert spec["expected_rows"] == 10
            spec["expected_rows"] = 1174
    # Exact known serialization also binds field/asset order and formatting.
    # No arbitrary current manifest field is removed or accepted by projection.
    assert current_manifest.replace(b"\r\n", b"\n") == _canonical_json(expected)
    assert current == expected
    assert set(assets) == {spec["path"] for spec in old["assets"]}
    projected_assets = dict(assets)
    for spec in old["assets"]:
        path = spec["path"]
        raw = assets[path]
        digest = hashlib.sha256(raw).hexdigest()
        if path not in CURRENT_ASSET_SHA256:
            assert digest == spec["sha256"], path  # All seventeen others, including every expected asset.
            continue
        assert digest == CURRENT_ASSET_SHA256[path], path
        rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline="")))
        assert len(rows) == {"zhuyin_component_map.csv": 1384,
                             "ttf_verified_outline_signatures.csv": 1563,
                             "ttf_verified_mapping_corrections.csv": 1174}[path]
        columns = HISTORICAL_COLUMNS[path]
        if path == "ttf_verified_mapping_corrections.csv":
            rows = rows[:10]  # Original ten assertions remain exact; added rows were pinned above.
        projected_rows = []
        for row in rows:
            projected_rows.append({column: (
                row["legacy_bopomofo"] if path == "zhuyin_component_map.csv" and column == "bopomofo" else
                row["legacy_verification"] if path == "ttf_verified_outline_signatures.csv" and column == "verification" else
                row[column]) for column in columns})
        buffer = io.StringIO(newline="")
        bom = b"" if path == "ttf_verified_mapping_corrections.csv" else b"\xef\xbb\xbf"
        writer = csv.DictWriter(buffer, fieldnames=columns, lineterminator="\n" if not bom else "\r\n")
        writer.writeheader()
        writer.writerows(projected_rows)
        restored = bom + buffer.getvalue().encode("utf-8")
        assert hashlib.sha256(restored).hexdigest() == HISTORICAL_ASSET_SHA256[path] == spec["sha256"]
        projected_assets[path] = restored
    projected_manifest = copy.deepcopy(old)
    projected_manifest["tool_version"] = "5.8.0"
    return _canonical_json(projected_manifest), projected_assets


def project_current_actual_epoch(raw):
    normalized = raw.replace(b"\r\n", b"\n")
    tree = ast.parse(normalized)
    for name, value in (("ACTUAL_DECODER_SEMANTICS_EPOCH", "2"),
                        ("EXPECTED_RESOLVER_SEMANTICS_EPOCH", "1")):
        assignments = [node for node in tree.body if isinstance(node, ast.Assign)
                       and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                       and node.targets[0].id == name]
        stores = [node for node in ast.walk(tree) if isinstance(node, ast.Name)
                  and isinstance(node.ctx, ast.Store) and node.id == name]
        assert len(assignments) == len(stores) == 1
        assert isinstance(assignments[0].value, ast.Constant) and assignments[0].value.value == value
    admitted = b'ACTUAL_DECODER_SEMANTICS_EPOCH = "2"'
    assert normalized.count(admitted) == 1
    return normalized.replace(admitted, b'ACTUAL_DECODER_SEMANTICS_EPOCH = "1"', 1)


# Independently spelled authorized cache-seal addition. Removing exactly this
# checked block is only for the old byte oracle; runtime behavior stays intact.
CACHE_SEAL_ADDITION = b'''        metadata = {str(row[0].value or ""): row[1].value for row in ws.iter_rows(min_row=1, max_col=2)}
        try:
            stored_components = json.loads(str(metadata.get("actual_asset_fingerprint_components") or "{}"))
        except (TypeError, ValueError):
            raise ValueError(f"{path.name} invalid actual fingerprint components")
        target_components = fingerprint_payload.get("components") or {}
        # An epoch marker alone does not record which evidence produced rows.
        # Optional-fingerprint decoder output stays unsealed; it cannot later
        # be relabelled with a newly fabricated static/PDF contract.
        if not isinstance(stored_components, dict) or not isinstance(target_components, dict):
            raise ValueError(f"{path.name} actual fingerprint contract must be a mapping")
        fingerprint_contract_components(stored_components, chain="actual")
        fingerprint_contract_components(target_components, chain="actual")
        # Dependency sealing is not a migration/re-decoding operation. Only a
        # workbook actually produced by the current decoder may be resealed;
        # rewriting an old fingerprint must never bless old pronunciation rows.
        if (str(metadata.get("actual_decoder_semantics_epoch") or "") != ACTUAL_DECODER_SEMANTICS_EPOCH
                or str(stored_components.get("actual_decoder_semantics_epoch") or "") != ACTUAL_DECODER_SEMANTICS_EPOCH
                or str(target_components.get("actual_decoder_semantics_epoch") or "") != ACTUAL_DECODER_SEMANTICS_EPOCH):
            raise ValueError(f"{path.name} actual epoch requires fresh decoding before fingerprint sealing")
        # Sealing may narrow dynamic dependencies discovered during this run.
        # It cannot bless cached labels after changing a static truth asset,
        # PDF or other required contract field (including a revoked root).
        scoped_keys = {"dynamic_actual_evidence_hashes", "global_exact_glyph_evidence_hashes"}
        changed = [key for key in FINGERPRINT_COMPATIBILITY_REQUIRED_KEYS["actual"]
                   if key not in scoped_keys and stored_components.get(key) != target_components.get(key)]
        if changed:
            raise ValueError(f"{path.name} actual contract changed; fresh decoding required: {changed}")
'''


def project_current_actual_cache_guard(raw):
    normalized = raw.replace(b"\r\n", b"\n")
    imported = b"    FINGERPRINT_COMPATIBILITY_REQUIRED_KEYS,\n"
    assert normalized.count(imported) == 1
    assert normalized.count(CACHE_SEAL_ADDITION) == 1
    return normalized.replace(imported, b"", 1).replace(CACHE_SEAL_ADDITION, b"", 1)
