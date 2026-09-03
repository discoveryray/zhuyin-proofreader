from __future__ import annotations

import hashlib
import json
import struct
from typing import Any, Mapping


TTF_GLYF_SHA256 = "TTF_GLYF_SHA256"
CFF_GLYPH_SHA256 = "CFF_GLYPH_SHA256"
SESSION_STABLE_KEY = "SESSION_STABLE_KEY"
OCCURRENCE_ONLY = "OCCURRENCE_ONLY"

GLOBAL_ELIGIBLE_SIMPLE_GLYF_V1 = "GLOBAL_ELIGIBLE_SIMPLE_GLYF_V1"
GLOBAL_ELIGIBLE_COMPLETE_CFF_RECORDING_V1 = "GLOBAL_ELIGIBLE_COMPLETE_CFF_RECORDING_V1"
NON_GLOBAL_ELIGIBLE = "NON_GLOBAL_ELIGIBLE"
CANONICAL_EXACT_IDENTITY = "CANONICAL_EXACT_IDENTITY"
UNSUPPORTED_EXACT_IDENTITY = "UNSUPPORTED_EXACT_IDENTITY"

ACTUAL_GROUP_IDENTITY_KINDS = frozenset({
    TTF_GLYF_SHA256,
    CFF_GLYPH_SHA256,
    SESSION_STABLE_KEY,
    OCCURRENCE_ONLY,
})


def _text(value: Any) -> str:
    return str(value or "").strip()


def _sha256_text(value: Any, *, field: str) -> str:
    digest = _text(value).lower()
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise ValueError(f"{field} 必須是完整 lowercase SHA-256")
    return digest


def _hash_payload(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def canonical_actual_group_identity(
    kind: Any,
    exact_key: Any,
    style_group: Any = "",
) -> dict[str, str]:
    """Canonical project-review identity without claiming global eligibility.

    Existing project-local TTF evidence is intentionally allowed to retain its
    raw-record SHA contract.  Structural TTF global admission is a separate
    operation performed by :func:`classify_ttf_glyf_record`.
    """

    canonical_kind = _text(kind)
    if canonical_kind not in ACTUAL_GROUP_IDENTITY_KINDS:
        raise ValueError(f"不支援的 actual group identity kind：{canonical_kind!r}")
    canonical_style = _text(style_group)
    if canonical_kind in {TTF_GLYF_SHA256, CFF_GLYPH_SHA256}:
        canonical_key = _sha256_text(exact_key, field="exact_key")
    else:
        canonical_key = _text(exact_key)
        if not canonical_key:
            raise ValueError("exact_key 不得空白")

    if canonical_kind == CFF_GLYPH_SHA256:
        if not canonical_style:
            raise ValueError("CFF exact identity 必須有 non-empty style_group")
    elif canonical_style:
        raise ValueError("style_group 只適用於 CFF exact identity")

    return {
        "kind": canonical_kind,
        "style_group": canonical_style,
        "exact_key": canonical_key,
    }


def actual_group_match_key(identity: Mapping[str, Any]) -> tuple[str, str, str]:
    canonical = canonical_actual_group_identity(
        identity.get("kind"),
        identity.get("exact_key"),
        identity.get("style_group", ""),
    )
    return (
        canonical["kind"],
        canonical["style_group"],
        canonical["exact_key"],
    )


def actual_group_identity_payload(identity: Mapping[str, Any]) -> dict[str, str]:
    """Return the versioned group-ID/snapshot identity payload.

    TTF and occurrence-local payloads remain byte-for-byte compatible with the
    v1.0 encoder.  CFF adds ``style_group`` because its exact reusable identity
    is the pair ``(style_group, complete glyph SHA-256)``.
    """

    canonical = canonical_actual_group_identity(
        identity.get("kind"),
        identity.get("exact_key"),
        identity.get("style_group", ""),
    )
    payload = {
        "kind": canonical["kind"],
        "exact_key": canonical["exact_key"],
    }
    if canonical["kind"] == CFF_GLYPH_SHA256:
        payload["style_group"] = canonical["style_group"]
    return payload


def actual_group_id(identity: Mapping[str, Any]) -> str:
    return "agr_" + _hash_payload(actual_group_identity_payload(identity))[:24]


def legacy_v1_actual_group_id(identity: Mapping[str, Any]) -> str:
    """Encode the frozen v1.0 staging identity for the explicit read adapter."""

    canonical = canonical_actual_group_identity(
        identity.get("kind"),
        identity.get("exact_key"),
        identity.get("style_group", ""),
    )
    return "agr_" + _hash_payload({
        "kind": canonical["kind"],
        "exact_key": canonical["exact_key"],
    })[:24]


def canonical_cff_exact_identity(style_group: Any, glyph_sha256: Any) -> dict[str, str]:
    canonical = canonical_actual_group_identity(
        CFF_GLYPH_SHA256,
        glyph_sha256,
        style_group,
    )
    return {
        "kind": canonical["kind"],
        "style_group": canonical["style_group"],
        "glyph_sha256": canonical["exact_key"],
    }


def _ttf_result(raw_record: bytes, *, eligible: bool, reason: str) -> dict[str, Any]:
    return {
        "status": CANONICAL_EXACT_IDENTITY if eligible else UNSUPPORTED_EXACT_IDENTITY,
        "kind": TTF_GLYF_SHA256,
        "style_group": "",
        "glyph_sha256": hashlib.sha256(raw_record).hexdigest() if raw_record else "",
        "eligibility": GLOBAL_ELIGIBLE_SIMPLE_GLYF_V1 if eligible else NON_GLOBAL_ELIGIBLE,
        "reason": reason,
    }


def _legal_glyf_padding(raw_record: bytes, parsed_length: int) -> bool:
    padding = raw_record[parsed_length:]
    return len(padding) <= 3 and not any(padding)


def classify_ttf_glyf_record(
    raw_record: bytes | bytearray | memoryview,
    *,
    record_range_valid: bool = True,
) -> dict[str, Any]:
    """Classify one complete raw ``glyf`` record for future global admission.

    This parser deliberately does not accept font names, font indexes, GIDs,
    characters, expected readings, dictionaries, or corpus assumptions.  Equal
    raw bytes receive an eligible identity only when the complete record is a
    legally parseable, visible simple glyph (signed ``numberOfContours > 0``).
    A structurally simple zero-contour glyph is deliberately non-eligible
    because it has no visible outline for direct visual actual evidence.
    """

    try:
        raw = bytes(raw_record)
    except (TypeError, ValueError):
        return _ttf_result(b"", eligible=False, reason="INVALID_GLYF_RECORD_TYPE")
    if not record_range_valid:
        return _ttf_result(raw, eligible=False, reason="INVALID_GLYF_RECORD_RANGE")
    if len(raw) < 10:
        return _ttf_result(raw, eligible=False, reason="TRUNCATED_GLYF_HEADER")

    try:
        number_of_contours, x_min, y_min, x_max, y_max = struct.unpack_from(">hhhhh", raw, 0)
    except struct.error:
        return _ttf_result(raw, eligible=False, reason="TRUNCATED_GLYF_HEADER")
    if x_min > x_max or y_min > y_max:
        return _ttf_result(raw, eligible=False, reason="INVALID_GLYF_BOUNDS")
    if number_of_contours < 0:
        return _ttf_result(raw, eligible=False, reason="COMPOSITE_GLYF")
    if number_of_contours == 0:
        return _ttf_result(raw, eligible=False, reason="EMPTY_SIMPLE_GLYF")

    pos = 10
    end_points: tuple[int, ...] = ()
    if number_of_contours:
        end_points_bytes = 2 * number_of_contours
        if pos + end_points_bytes > len(raw):
            return _ttf_result(raw, eligible=False, reason="TRUNCATED_SIMPLE_END_POINTS")
        try:
            end_points = struct.unpack_from(f">{number_of_contours}H", raw, pos)
        except struct.error:
            return _ttf_result(raw, eligible=False, reason="TRUNCATED_SIMPLE_END_POINTS")
        pos += end_points_bytes
        if any(current <= previous for previous, current in zip(end_points, end_points[1:])):
            return _ttf_result(raw, eligible=False, reason="INVALID_SIMPLE_END_POINTS")

    if pos + 2 > len(raw):
        return _ttf_result(raw, eligible=False, reason="TRUNCATED_SIMPLE_INSTRUCTION_LENGTH")
    instruction_length = struct.unpack_from(">H", raw, pos)[0]
    pos += 2
    if pos + instruction_length > len(raw):
        return _ttf_result(raw, eligible=False, reason="TRUNCATED_SIMPLE_INSTRUCTIONS")
    pos += instruction_length

    point_count = end_points[-1] + 1 if end_points else 0
    flags: list[int] = []
    encoded_flag_index = 0
    while len(flags) < point_count:
        if pos >= len(raw):
            return _ttf_result(raw, eligible=False, reason="TRUNCATED_SIMPLE_FLAGS")
        flag = raw[pos]
        pos += 1
        if flag & 0x80:
            return _ttf_result(raw, eligible=False, reason="INVALID_SIMPLE_RESERVED_FLAG")
        if flag & 0x40 and encoded_flag_index != 0:
            return _ttf_result(raw, eligible=False, reason="INVALID_OVERLAP_SIMPLE_POSITION")
        encoded_flag_index += 1
        flags.append(flag)
        if flag & 0x08:
            if pos >= len(raw):
                return _ttf_result(raw, eligible=False, reason="TRUNCATED_SIMPLE_REPEAT_FLAG")
            repeat_count = raw[pos]
            pos += 1
            if len(flags) + repeat_count > point_count:
                return _ttf_result(raw, eligible=False, reason="INVALID_SIMPLE_REPEAT_FLAG")
            flags.extend([flag] * repeat_count)

    x_bytes = sum(1 if flag & 0x02 else (0 if flag & 0x10 else 2) for flag in flags)
    y_bytes = sum(1 if flag & 0x04 else (0 if flag & 0x20 else 2) for flag in flags)
    coordinate_end = pos + x_bytes + y_bytes
    if coordinate_end > len(raw):
        return _ttf_result(raw, eligible=False, reason="TRUNCATED_SIMPLE_COORDINATES")
    if not _legal_glyf_padding(raw, coordinate_end):
        return _ttf_result(raw, eligible=False, reason="INVALID_SIMPLE_TRAILING_DATA")

    return _ttf_result(raw, eligible=True, reason="")
