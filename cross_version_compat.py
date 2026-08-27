from __future__ import annotations

import json
from typing import Any, Mapping


# These are cache-compatibility fields, not a list of every fingerprint
# component.  Audit/provenance metadata (tool versions, source hashes, and
# convenience aliases) must not become an accidental cache boundary.
#
# A future semantics epoch/profile must be added to the applicable tuple.  The
# fallback then fails closed for old/missing/different values instead of
# silently ignoring the new contract field.
FINGERPRINT_COMPATIBILITY_REQUIRED_KEYS: dict[str, tuple[str, ...]] = {
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
}


# v5.6 deliberately provided semantic reuse adapters for the immediately
# preceding v5.5.1 fingerprints.  Those legacy components predate the explicit
# reuse_policy component, but their construction is known to mean the current
# evidence_assets_v1 policy.  All other schema transitions fail closed unless
# an equally explicit, tested adapter is added here.
_FINGERPRINT_SCHEMA_ADAPTERS: dict[str, dict[tuple[str, str], Mapping[str, Any]]] = {
    "actual": {("2.8.0", "2.9.0"): {"reuse_policy": "evidence_assets_v1"}},
    "expected": {("2.6.0", "2.7.0"): {"reuse_policy": "evidence_assets_v1"}},
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _parts(value: Any) -> tuple[int, ...]:
    text = _text(value)
    out: list[int] = []
    for token in text.split("."):
        try:
            out.append(int(token))
        except Exception:
            break
    return tuple(out)


def schema_compatible(observed: Any, current: Any) -> bool:
    """Allow patch-level schema drift within the same major/minor family.

    Tool releases are intentionally *not* schema boundaries.  A future release
    may continue to read an older workbook/session as long as the data layout
    family is unchanged.  A real layout break must bump major/minor and provide
    an explicit adapter instead of using a version-number hard stop.
    """
    if _text(observed) == _text(current):
        return True
    left = _parts(observed)
    right = _parts(current)
    return len(left) >= 2 and len(right) >= 2 and left[:2] == right[:2]


def review_id_schema_compatible(observed: Any, current: Any) -> bool:
    """Review identity must remain exact unless an explicit ID adapter exists."""
    return _text(observed) == _text(current)


def parse_components(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    text = _text(value)
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except Exception:
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _same_mapping(left: Any, right: Any) -> bool:
    if not isinstance(left, Mapping) or not isinstance(right, Mapping):
        return False
    return json.dumps(dict(left), ensure_ascii=False, sort_keys=True, separators=(",", ":")) == json.dumps(
        dict(right), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _same_contract_value(left: Any, right: Any) -> bool:
    """Compare one declared contract value without coercing unlike types."""
    if isinstance(left, Mapping) or isinstance(right, Mapping):
        return _same_mapping(left, right)
    try:
        return json.dumps(left, ensure_ascii=False, sort_keys=True, separators=(",", ":")) == json.dumps(
            right, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    except (TypeError, ValueError):
        return left == right


def _adapt_stored_fingerprint_contract(
    stored: Mapping[str, Any],
    current: Mapping[str, Any],
    *,
    chain: str,
) -> dict[str, Any] | None:
    """Return stored components expressed in the current schema contract."""
    stored_schema = _text(stored.get("fingerprint_schema_version"))
    current_schema = _text(current.get("fingerprint_schema_version"))
    if not stored_schema or not current_schema:
        return None
    if stored_schema == current_schema:
        return dict(stored)

    adapter = _FINGERPRINT_SCHEMA_ADAPTERS.get(chain, {}).get((stored_schema, current_schema))
    if adapter is None:
        return None
    adapted = dict(stored)
    for key, value in adapter.items():
        adapted.setdefault(key, value)
    adapted["fingerprint_schema_version"] = current_schema
    return adapted


def _compatibility_contract_matches(
    stored: Mapping[str, Any],
    current: Mapping[str, Any],
    *,
    chain: str,
) -> bool:
    required_keys = FINGERPRINT_COMPATIBILITY_REQUIRED_KEYS.get(chain)
    if required_keys is None:
        raise ValueError(f"unknown fingerprint chain: {chain}")
    adapted = _adapt_stored_fingerprint_contract(stored, current, chain=chain)
    if adapted is None:
        return False
    for key in required_keys:
        if key not in adapted or key not in current:
            return False
        if key in {"fingerprint_schema_version", "reuse_policy"} and (
            not _text(adapted[key]) or not _text(current[key])
        ):
            return False
        if not _same_contract_value(adapted[key], current[key]):
            return False
    return True


def fingerprint_contract_components(components: Mapping[str, Any], *, chain: str) -> dict[str, Any]:
    """Select the declared contract fields used to build a reuse fingerprint.

    Fingerprint producers and the semantic fallback share the same registry so
    a newly declared required key cannot be added to only one side by accident.
    """
    required_keys = FINGERPRINT_COMPATIBILITY_REQUIRED_KEYS.get(chain)
    if required_keys is None:
        raise ValueError(f"unknown fingerprint chain: {chain}")
    missing = [key for key in required_keys if key not in components]
    if missing:
        raise ValueError(f"{chain} fingerprint compatibility contract missing fields: {missing}")
    selected = {key: components[key] for key in required_keys}
    if not _text(selected.get("fingerprint_schema_version")) or not _text(selected.get("reuse_policy")):
        raise ValueError(f"{chain} fingerprint compatibility schema/reuse policy must be explicit")
    return selected


def fingerprint_compatible(
    stored_fingerprint: Any,
    stored_components: Any,
    current_payload: Mapping[str, Any],
    *,
    chain: str,
) -> bool:
    """Compare the explicit cache-compatibility contract.

    v5.5.x fingerprints included resolver/decoder version and source-code hashes,
    so every small program release invalidated otherwise identical workbooks.
    v5.6 keeps those values for audit.  Semantic fallback compares only the
    declared chain-specific contract: schema, reuse policy, and result-affecting
    evidence.  Unknown schema transitions and missing required fields fail
    closed; audit-only metadata remains non-blocking.
    """
    stored_fp = _text(stored_fingerprint)
    current_fp = _text(current_payload.get("fingerprint"))
    if stored_fp and current_fp and stored_fp == current_fp:
        return True

    old = parse_components(stored_components)
    new = parse_components(current_payload.get("components"))
    if not old or not new:
        return False

    return _compatibility_contract_matches(old, new, chain=chain)


def metadata_compatibility_warnings(
    stored_components: Any,
    current_payload: Mapping[str, Any],
    *,
    chain: str,
) -> list[str]:
    """Return non-blocking audit notes for source-code/tool drift."""
    old = parse_components(stored_components)
    new = parse_components(current_payload.get("components"))
    if not old or not new:
        return []
    warnings: list[str] = []
    version_key = "decoder_version" if chain == "actual" else "resolver_version"
    source_key = "actual_decoder_source_hashes" if chain == "actual" else "expected_resolver_source_hashes"
    if _text(old.get(version_key)) and _text(old.get(version_key)) != _text(new.get(version_key)):
        warnings.append(f"{chain} tool version changed: {old.get(version_key)} -> {new.get(version_key)}")
    if old.get(source_key) and new.get(source_key) and not _same_mapping(old.get(source_key), new.get(source_key)):
        warnings.append(f"{chain} implementation source changed; cache reused because evidence assets are unchanged")
    return warnings
