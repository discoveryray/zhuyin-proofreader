from __future__ import annotations

import json
from typing import Any, Mapping


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


def fingerprint_compatible(
    stored_fingerprint: Any,
    stored_components: Any,
    current_payload: Mapping[str, Any],
    *,
    chain: str,
) -> bool:
    """Compare cache evidence semantically instead of by tool-version bytes.

    v5.5.x fingerprints included resolver/decoder version and source-code hashes,
    so every small program release invalidated otherwise identical workbooks.
    v5.6 keeps those values for audit, but reuse depends only on evidence that can
    change the result for the PDF: source PDF, approved data assets and relevant
    project dynamic actual evidence.
    """
    stored_fp = _text(stored_fingerprint)
    current_fp = _text(current_payload.get("fingerprint"))
    if stored_fp and current_fp and stored_fp == current_fp:
        return True

    old = parse_components(stored_components)
    new = parse_components(current_payload.get("components"))
    if not old or not new:
        return False

    if chain == "actual":
        if _text(old.get("pdf_sha256")) != _text(new.get("pdf_sha256")):
            return False
        if not _same_mapping(old.get("actual_asset_hashes"), new.get("actual_asset_hashes")):
            return False
        if not _same_mapping(old.get("dynamic_actual_evidence_hashes"), new.get("dynamic_actual_evidence_hashes")):
            return False
        return True

    if chain == "expected":
        return _same_mapping(old.get("expected_asset_hashes"), new.get("expected_asset_hashes"))

    raise ValueError(f"unknown fingerprint chain: {chain}")


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
