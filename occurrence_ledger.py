from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Iterable, Mapping, Sequence
from unicodedata import normalize


LEDGER_SCHEMA_VERSION = "2.6.0"
SESSION_SCHEMA_VERSION = "2.6.0"
WORKBOOK_SCHEMA_VERSION = "2.6.0"
# Review identity is occurrence-based and did not change in v5.5.0. Keep the
# review-id schema stable so a printed occurrence keeps the same durable ID.
REVIEW_ID_SCHEMA_VERSION = "2.5.0"

PROCESSING_FINISHED = "PROCESSING_FINISHED"
PROOFREAD_COMPLETE = "PROOFREAD_COMPLETE"
PIPELINE_BLOCKED = "PIPELINE_BLOCKED"

NON_TERMINAL_STATES = frozenset({
    "ACTUAL_UNRESOLVED",
    "ACTUAL_DECODE_ERROR",
    "EXPECTED_UNRESOLVED",
    "EXPECTED_AMBIGUOUS",
    "RULE_CONFLICT",
    "REVIEW_PENDING",
    "DIFFERENCE_PENDING_CONFIRMATION",
    "REGRESSION_BLOCKED",
    "SOURCE_INVALID",
    "DATA_INTEGRITY_ERROR",
})

TERMINAL_STATES = frozenset({
    "PASS",
    "TEXTBOOK_ERROR_CONFIRMED",
    "EXCLUDED_NONINDEPENDENT_LAYER",
    "EXCLUDED_OUT_OF_SCOPE",
})

ALL_STATES = NON_TERMINAL_STATES | TERMINAL_STATES
EXCLUDED_STATES = frozenset({"EXCLUDED_NONINDEPENDENT_LAYER", "EXCLUDED_OUT_OF_SCOPE"})
COMPARABLE_TERMINAL_STATES = frozenset({"PASS", "TEXTBOOK_ERROR_CONFIRMED"})

# v5.5.0: actual and expected are two independent evidence lanes.  ``state`` is
# now only a derived workflow projection.  A failure or gap in one lane must not
# erase evidence already established in the other lane.
ACTUAL_STATUSES = frozenset({"RESOLVED", "UNRESOLVED", "DECODE_ERROR", "EXCLUDED"})
EXPECTED_STATUSES = frozenset({"RESOLVED", "UNRESOLVED", "AMBIGUOUS", "CONFLICT", "EXCLUDED"})
HARD_BLOCKING_STATES = frozenset({"SOURCE_INVALID", "DATA_INTEGRITY_ERROR", "REGRESSION_BLOCKED"})

CONFIRMATION_GATES = (
    "actual_current_evidence",
    "expected_current_evidence",
    "actual_expected_independent",
    "context_position_verified",
    "source_priority_verified",
    "human_confirmation",
)

BOPOMOFO = set("ㄅㄆㄇㄈㄉㄊㄋㄌㄍㄎㄏㄐㄑㄒㄓㄔㄕㄖㄗㄘㄙㄧㄨㄩㄚㄛㄜㄝㄞㄟㄠㄡㄢㄣㄤㄥㄦ")
TONES = set("ˊˇˋ˙")


class LedgerError(ValueError):
    """Raised when an occurrence ledger invariant is violated."""


class DuplicateIdError(LedgerError):
    """Raised before a duplicate ID can be silently overwritten."""


class InvalidTransitionError(LedgerError):
    """Raised when evidence does not permit a requested state transition."""


@dataclass(frozen=True)
class ReconciliationResult:
    ok: bool
    errors: tuple[str, ...]
    details: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "errors": list(self.errors), **self.details}


def _text(value: Any) -> str:
    return normalize("NFKC", str(value or "")).strip()


def canonical_bopomofo(value: Any) -> str:
    """Return a stable Bopomofo spelling for one comparable reading.

    Tone-position differences are presentation differences, not pronunciation
    differences. A tone mark at either edge is normalized to the project
    convention: neutral tone is prefixed; 2nd/3rd/4th tone is suffixed.

    The MOE source also contains standard erhua spellings such as ``ㄉㄧㄢˇㄦ``;
    those are preserved as the canonical form. Repeated copies of the *same*
    tone at one edge (legacy source typo such as ``ㄌㄧㄤˋˋ``) collapse to one.
    Different/mixed tones or a tone embedded anywhere else fail closed.
    """
    text = _text(value).replace("‧", "˙").replace("・", "˙").replace("一", "ㄧ")
    text = text.replace("\u0307", "˙").replace(" ", "").replace("　", "")
    text = re.sub(r"[（(][^）)]*[）)]", "", text)
    if not text or not all(ch in BOPOMOFO or ch in TONES for ch in text):
        return ""

    tone_positions = [(index, ch) for index, ch in enumerate(text) if ch in TONES]
    if not tone_positions:
        return text if all(ch in BOPOMOFO for ch in text) else ""

    # Preserve compatibility with source data.  A repeated copy of the same
    # tone at one edge is a known legacy typo and collapses to one.  Multiple
    # non-adjacent tones after Bopomofo segments are treated as a concatenated
    # multi-syllable source spelling (e.g. ㄏㄜˊㄕㄤˋ) and left unchanged.
    if len(tone_positions) > 1:
        tone_chars = {ch for _, ch in tone_positions}
        count = len(tone_positions)
        positions = [index for index, _ in tone_positions]
        if len(tone_chars) == 1 and positions == list(range(count)):
            tone = tone_positions[0][1]
            text = tone + text[count:]
            tone_positions = [(index, ch) for index, ch in enumerate(text) if ch in TONES]
        elif len(tone_chars) == 1 and positions == list(range(len(text) - count, len(text))):
            tone = tone_positions[0][1]
            text = text[:-count] + tone
            tone_positions = [(index, ch) for index, ch in enumerate(text) if ch in TONES]
        else:
            # Fail closed for mixed/multiple tones at the beginning or adjacent
            # to one another; those cannot represent a normal concatenated
            # syllable sequence.
            if positions[0] == 0 or any(b == a + 1 for a, b in zip(positions, positions[1:])):
                return ""
            segment_start = 0
            for tone_index, _tone in tone_positions:
                segment = text[segment_start:tone_index]
                if not segment or not all(ch in BOPOMOFO for ch in segment):
                    return ""
                segment_start = tone_index + 1
            tail = text[segment_start:]
            if tail and not all(ch in BOPOMOFO for ch in tail):
                return ""
            return text

    tone_index, tone = tone_positions[0]

    # Standard erhua source spelling places the non-neutral tone immediately
    # before the final ㄦ, e.g. ㄉㄧㄢˇㄦ. Keep that as the canonical form.
    if 0 < tone_index < len(text) - 1:
        if tone != "˙" and tone_index == len(text) - 2 and text.endswith("ㄦ"):
            body = text[:tone_index] + text[tone_index + 1:]
            return text if body and all(ch in BOPOMOFO for ch in body) else ""
        return ""

    body = text[1:] if tone_index == 0 else text[:-1]
    if not body or not all(ch in BOPOMOFO for ch in body):
        return ""
    if tone == "˙":
        return f"˙{body}"
    if body.endswith("ㄦ") and len(body) > 1:
        return f"{body[:-1]}{tone}ㄦ"
    return f"{body}{tone}"


def normalize_expected_set(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        raw = re.split(r"[|；;]", value)
    elif isinstance(value, Iterable):
        raw = list(value)
    else:
        raw = [value]
    out: list[str] = []
    for item in raw:
        reading = canonical_bopomofo(item)
        if reading and reading not in out:
            out.append(reading)
    return tuple(sorted(out))


def _canonical_number(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        number = Decimal(str(value)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return _text(value)
    return format(number.normalize(), "f")


def _canonical_stable_key(value: Any) -> str:
    text = _text(value)
    # Excel row numbers are audit metadata, never normal occurrence identity.
    text = re.sub(r"(?i)(?:^|[|;,])\s*(?:excel_?row|source_?row|row)\s*[:=#]\s*\d+\s*", "", text)
    return text.strip("|;, ")


def _row_value(row: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in row and row.get(name) not in (None, ""):
            return row.get(name)
    return ""


def _identity_payload(pdf_sha256: str, row: Mapping[str, Any]) -> dict[str, str]:
    return {
        "pdf_sha256": _text(pdf_sha256).lower(),
        "physical_page": _text(_row_value(row, "實體頁碼", "physical_page")),
        "stable_key": _canonical_stable_key(_row_value(row, "穩定注音鍵", "stable_key")),
        "font": _text(_row_value(row, "font")),
        "font_xref": _text(_row_value(row, "font_xref")),
        "glyph_id": _text(_row_value(row, "glyph_id_字形索引", "glyph_id", "候選glyph_id")),
        "zhuyin_component_id": _text(_row_value(row, "注音元件ID", "候選注音元件ID", "zhuyin_component_id")),
        "char": _text(_row_value(row, "字元", "target_char", "char")),
        "x0": _canonical_number(_row_value(row, "x0")),
        "y0": _canonical_number(_row_value(row, "y0")),
        "x1": _canonical_number(_row_value(row, "x1")),
        "y1": _canonical_number(_row_value(row, "y1")),
    }


def make_occurrence_id(
    pdf_sha256: str,
    row: Mapping[str, Any],
    *,
    fallback_row_number: int | None = None,
    force_fallback: bool = False,
) -> tuple[str, str, bool]:
    """Return (occurrence_id, identity_confidence, used_row_fallback).

    A source Excel row number is deliberately excluded from normal identity. It
    is used only when the PDF metadata cannot distinguish two detected source
    records, and that downgrade is explicit in the returned metadata.
    """

    payload = _identity_payload(pdf_sha256, row)
    discriminator_names = (
        "physical_page", "stable_key", "font", "font_xref", "glyph_id",
        "zhuyin_component_id", "char", "x0", "y0", "x1", "y1",
    )
    has_discriminator = bool(payload["pdf_sha256"] and any(payload[k] for k in discriminator_names))
    use_fallback = force_fallback or not has_discriminator
    if use_fallback:
        if fallback_row_number is None:
            raise LedgerError("occurrence identity 缺少穩定 discriminator，且未明示 source row fallback")
        payload["fallback_source_row_number"] = str(int(fallback_row_number))
        confidence = "FALLBACK_SOURCE_ROW"
    else:
        confidence = "PDF_STABLE"
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "occ_" + hashlib.sha256(raw).hexdigest(), confidence, use_fallback


def make_review_id(occurrence_id: str) -> str:
    raw = f"{REVIEW_ID_SCHEMA_VERSION}|{_text(occurrence_id)}".encode("utf-8")
    return "rev_" + hashlib.sha256(raw).hexdigest()


def assert_unique_ids(records: Sequence[Mapping[str, Any]], field: str) -> None:
    values = [_text(row.get(field)) for row in records]
    missing = [index + 1 for index, value in enumerate(values) if not value]
    counts = Counter(value for value in values if value)
    duplicates = sorted(value for value, count in counts.items() if count > 1)
    if missing or duplicates:
        parts = []
        if missing:
            parts.append(f"missing {field} at rows {missing[:20]}")
        if duplicates:
            parts.append(f"duplicate {field}: {duplicates[:20]}")
        raise DuplicateIdError("；".join(parts))


def prepare_occurrence_rows(pdf_sha256: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach stable IDs without using Excel row order unless unavoidable."""

    base_ids: list[str] = []
    for row in rows:
        source_row = int(row.get("source_row_number") or row.get("來源列號") or 0)
        occurrence_id, confidence, used_fallback = make_occurrence_id(
            pdf_sha256, row, fallback_row_number=source_row or None
        )
        row["occurrence_id"] = occurrence_id
        row["identity_confidence"] = confidence
        row["identity_row_fallback"] = used_fallback
        row["review_id"] = make_review_id(occurrence_id)
        base_ids.append(occurrence_id)

    duplicate_bases = {value for value, count in Counter(base_ids).items() if count > 1}
    if duplicate_bases:
        seen: Counter[str] = Counter()
        for row in rows:
            base = row["occurrence_id"]
            if base not in duplicate_bases:
                continue
            seen[base] += 1
            # One indistinguishable source row keeps the canonical PDF identity;
            # other physical source layers are explicitly downgraded.
            if seen[base] == 1:
                continue
            source_row = int(row.get("source_row_number") or row.get("來源列號") or 0)
            occurrence_id, confidence, used_fallback = make_occurrence_id(
                pdf_sha256,
                row,
                fallback_row_number=source_row or None,
                force_fallback=True,
            )
            row["occurrence_id"] = occurrence_id
            row["identity_confidence"] = confidence
            row["identity_row_fallback"] = used_fallback
            row["identity_collision_base"] = base
            row["review_id"] = make_review_id(occurrence_id)

    assert_unique_ids(rows, "occurrence_id")
    assert_unique_ids(rows, "review_id")
    return rows



def infer_actual_status(entry: Mapping[str, Any]) -> str:
    explicit = _text(entry.get("actual_status")).upper()
    if explicit in ACTUAL_STATUSES:
        return explicit
    state = _text(entry.get("state"))
    if state in EXCLUDED_STATES:
        return "EXCLUDED"
    if state == "ACTUAL_DECODE_ERROR":
        return "DECODE_ERROR"
    if state == "ACTUAL_UNRESOLVED":
        return "UNRESOLVED"
    actual = canonical_bopomofo(entry.get("actual"))
    return "RESOLVED" if actual and entry.get("actual_evidence") else "UNRESOLVED"


def infer_expected_status(entry: Mapping[str, Any]) -> str:
    explicit = _text(entry.get("expected_status")).upper()
    if explicit in EXPECTED_STATUSES:
        return explicit
    state = _text(entry.get("state"))
    if state in EXCLUDED_STATES:
        return "EXCLUDED"
    if state == "RULE_CONFLICT":
        return "CONFLICT"
    if state == "EXPECTED_AMBIGUOUS":
        return "AMBIGUOUS"
    if state == "EXPECTED_UNRESOLVED":
        return "UNRESOLVED"
    expected = normalize_expected_set(entry.get("expected_set"))
    return "RESOLVED" if expected and entry.get("expected_evidence") else "UNRESOLVED"


def derive_comparison_result(entry: Mapping[str, Any]) -> str:
    if infer_actual_status(entry) != "RESOLVED" or infer_expected_status(entry) != "RESOLVED":
        return ""
    actual = canonical_bopomofo(entry.get("actual"))
    expected = set(normalize_expected_set(entry.get("expected_set")))
    if not actual or not expected:
        return ""
    return "MATCH" if actual in expected else "MISMATCH"


def derive_authoritative_state(entry: Mapping[str, Any], *, preserve_confirmed: bool = True) -> str:
    """Derive the public workflow state from independent evidence lanes.

    Priority is deliberately asymmetric only at the *workflow* layer.  Actual
    gaps can be shown first to the user while expected evidence remains intact
    in its own lane.  Completion gates inspect both lane statuses directly.
    """
    state = _text(entry.get("state"))
    excluded_state = _text(entry.get("exclusion_state"))
    if excluded_state in EXCLUDED_STATES:
        return excluded_state
    if state in EXCLUDED_STATES:
        return state
    blocking_state = _text(entry.get("blocking_state"))
    if blocking_state in HARD_BLOCKING_STATES:
        return blocking_state
    if state in HARD_BLOCKING_STATES:
        return state

    actual_status = infer_actual_status(entry)
    expected_status = infer_expected_status(entry)
    if actual_status == "DECODE_ERROR":
        return "ACTUAL_DECODE_ERROR"
    if actual_status == "UNRESOLVED":
        return "ACTUAL_UNRESOLVED"
    if expected_status == "CONFLICT":
        return "RULE_CONFLICT"
    if expected_status == "AMBIGUOUS":
        return "EXPECTED_AMBIGUOUS"
    if expected_status == "UNRESOLVED":
        return "EXPECTED_UNRESOLVED"

    comparison = derive_comparison_result(entry)
    if comparison == "MATCH":
        return "PASS"
    if comparison == "MISMATCH":
        if preserve_confirmed and state == "TEXTBOOK_ERROR_CONFIRMED":
            gates = entry.get("confirmation_gates") or {}
            if all(gates.get(gate) is True for gate in CONFIRMATION_GATES):
                return "TEXTBOOK_ERROR_CONFIRMED"
        return "DIFFERENCE_PENDING_CONFIRMATION"
    return "REVIEW_PENDING"


def refresh_derived_state(entry: Mapping[str, Any], *, preserve_confirmed: bool = True) -> dict[str, Any]:
    out = deepcopy(dict(entry))
    out["actual_status"] = infer_actual_status(out)
    out["expected_status"] = infer_expected_status(out)
    out["comparison_result"] = derive_comparison_result(out)
    out["state"] = derive_authoritative_state(out, preserve_confirmed=preserve_confirmed)
    out["active_review"] = out["state"] in NON_TERMINAL_STATES
    return out


def build_occurrence_ledger(
    source_rows: Sequence[Mapping[str, Any]],
    classifications: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Build the only authoritative occurrence table from source detections."""

    ledger: list[dict[str, Any]] = []
    for source in source_rows:
        occurrence_id = _text(source.get("occurrence_id"))
        review_id = _text(source.get("review_id")) or make_review_id(occurrence_id)
        classified = dict(classifications.get(occurrence_id) or {})
        state = _text(classified.get("state")) or "DATA_INTEGRITY_ERROR"
        row = {
            "ledger_schema_version": LEDGER_SCHEMA_VERSION,
            "review_id_schema_version": REVIEW_ID_SCHEMA_VERSION,
            "occurrence_id": occurrence_id,
            "review_id": review_id,
            "active_review": state in NON_TERMINAL_STATES,
            "state": state,
            "source_row_number": source.get("source_row_number") or source.get("來源列號") or "",
            "identity_confidence": source.get("identity_confidence") or "",
            "identity_row_fallback": bool(source.get("identity_row_fallback")),
            "identity_collision_base": source.get("identity_collision_base") or "",
            "pdf_sha256": source.get("pdf_sha256") or classified.get("pdf_sha256") or "",
            "pdf": source.get("pdf") or classified.get("pdf") or "",
            "pdf_name": source.get("pdf_name") or classified.get("pdf_name") or "",
            "physical_page": _row_value(source, "實體頁碼", "physical_page"),
            "printed_page": _row_value(source, "課本頁", "page", "printed_page"),
            "char": _row_value(source, "字元", "target_char", "char"),
            "stable_key": _row_value(source, "穩定注音鍵", "stable_key"),
            "font": _row_value(source, "font"),
            "font_xref": _row_value(source, "font_xref"),
            "glyph_id": _row_value(source, "glyph_id_字形索引", "glyph_id", "候選glyph_id"),
            "zhuyin_component_id": _row_value(source, "注音元件ID", "候選注音元件ID", "zhuyin_component_id"),
            "x0": _row_value(source, "x0"),
            "y0": _row_value(source, "y0"),
            "x1": _row_value(source, "x1"),
            "y1": _row_value(source, "y1"),
            "actual": canonical_bopomofo(classified.get("actual", _row_value(source, "實際注音", "actual"))),
            "actual_raw": classified.get("actual_raw", _row_value(source, "實際注音", "actual")),
            "actual_evidence": classified.get("actual_evidence", _row_value(source, "解碼依據", "actual_evidence")),
            "actual_status": classified.get("actual_status") or "",
            "expected_set": list(normalize_expected_set(classified.get("expected_set"))),
            "expected_evidence": classified.get("expected_evidence") or "",
            "expected_status": classified.get("expected_status") or "",
            "context_evidence": classified.get("context_evidence") or "",
            "comparison_result": classified.get("comparison_result") or "",
            "blocking_state": classified.get("blocking_state") or (state if state in HARD_BLOCKING_STATES else ""),
            "exclusion_state": classified.get("exclusion_state") or (state if state in EXCLUDED_STATES else ""),
            "source_view": classified.get("source_view") or "",
            "canonical_occurrence_id": classified.get("canonical_occurrence_id") or "",
            "exclusion_reason": classified.get("exclusion_reason") or "",
            "confirmation_gates": dict(classified.get("confirmation_gates") or {}),
            "note": classified.get("note") or "",
            "source_record": dict(classified.get("source_record") or source),
        }
        # ``state`` is a projection.  Evidence lanes are authoritative, but the
        # candidate producer must still agree with that projection.  Silently
        # correcting a contradictory source classification would hide an
        # upstream bug (for example a claimed PASS where actual is not in the
        # expected set), so fail closed instead.
        requested_state = state
        row = refresh_derived_state(row, preserve_confirmed=True)
        if requested_state != row.get("state"):
            raise LedgerError(
                f"classification/state 與雙證據鏈不一致：{occurrence_id} "
                f"requested={requested_state} derived={row.get('state')}"
            )
        ledger.append(row)
    validate_occurrence_ledger(ledger)
    return ledger


def validate_terminal_state(entry: Mapping[str, Any]) -> None:
    state = _text(entry.get("state"))
    if state not in TERMINAL_STATES:
        raise LedgerError(f"{state or '(missing)'} 不是 terminal state")
    if state == "PASS":
        actual = canonical_bopomofo(entry.get("actual"))
        expected = set(normalize_expected_set(entry.get("expected_set")))
        if not actual or not entry.get("actual_evidence"):
            raise LedgerError("PASS 缺少合法 actual 或 actual 證據")
        if not expected or not entry.get("expected_evidence"):
            raise LedgerError("PASS 缺少 expected_set 或 expected 證據")
        if not entry.get("context_evidence"):
            raise LedgerError("PASS 缺少必要詞語／語境／位置證據")
        if actual not in expected or entry.get("comparison_result") != "MATCH":
            raise LedgerError("PASS 必須由 actual ∈ expected_set 的機械比較產生")
    elif state == "TEXTBOOK_ERROR_CONFIRMED":
        actual = canonical_bopomofo(entry.get("actual"))
        expected = set(normalize_expected_set(entry.get("expected_set")))
        gates = entry.get("confirmation_gates") or {}
        if not actual or not entry.get("actual_evidence") or not expected or not entry.get("expected_evidence"):
            raise LedgerError("TEXTBOOK_ERROR_CONFIRMED 缺少現版 actual／expected 證據鏈")
        if not entry.get("context_evidence"):
            raise LedgerError("TEXTBOOK_ERROR_CONFIRMED 缺少語境／位置證據")
        if actual in expected or entry.get("comparison_result") != "MISMATCH":
            raise LedgerError("TEXTBOOK_ERROR_CONFIRMED 必須由 actual ∉ expected_set 的機械比較產生")
        missing = [gate for gate in CONFIRMATION_GATES if gates.get(gate) is not True]
        if missing:
            raise LedgerError(f"TEXTBOOK_ERROR_CONFIRMED 六閘門未全數通過：{missing}")
    elif state == "EXCLUDED_NONINDEPENDENT_LAYER":
        if not entry.get("canonical_occurrence_id") or not entry.get("exclusion_reason"):
            raise LedgerError("非獨立技術層必須記錄 canonical occurrence 與排除理由")
        if entry.get("canonical_occurrence_id") == entry.get("occurrence_id"):
            raise LedgerError("非獨立技術層不得把自己列為 canonical occurrence")
    elif state == "EXCLUDED_OUT_OF_SCOPE" and not entry.get("exclusion_reason"):
        raise LedgerError("真正排除項目必須記錄可稽核理由")


def validate_occurrence_ledger(ledger: Sequence[Mapping[str, Any]]) -> None:
    if not ledger:
        raise LedgerError("occurrence ledger 不得為空")
    assert_unique_ids(ledger, "occurrence_id")
    assert_unique_ids(ledger, "review_id")
    for index, entry in enumerate(ledger, 1):
        if entry.get("ledger_schema_version") != LEDGER_SCHEMA_VERSION:
            raise LedgerError(f"ledger row {index} schema 不相容")
        state = _text(entry.get("state"))
        if state not in ALL_STATES:
            raise LedgerError(f"ledger row {index} state 無效：{state}")
        actual_status = infer_actual_status(entry)
        expected_status = infer_expected_status(entry)
        if actual_status not in ACTUAL_STATUSES:
            raise LedgerError(f"ledger row {index} actual_status 無效：{actual_status}")
        if expected_status not in EXPECTED_STATUSES:
            raise LedgerError(f"ledger row {index} expected_status 無效：{expected_status}")
        derived_state = derive_authoritative_state(entry, preserve_confirmed=True)
        if state != derived_state:
            raise LedgerError(
                f"ledger row {index} state 不是雙證據鏈機械投影：{state} != {derived_state} "
                f"(actual={actual_status}, expected={expected_status})"
            )
        if bool(entry.get("active_review")) != (state in NON_TERMINAL_STATES):
            raise LedgerError(f"ledger row {index} active_review 與 state 不一致")
        if state in TERMINAL_STATES:
            validate_terminal_state(entry)


_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    state: frozenset({state, "DATA_INTEGRITY_ERROR", "SOURCE_INVALID"}) for state in ALL_STATES
}
for _state in ("EXPECTED_UNRESOLVED", "EXPECTED_AMBIGUOUS", "REVIEW_PENDING"):
    _ALLOWED_TRANSITIONS[_state] |= frozenset({"PASS", "DIFFERENCE_PENDING_CONFIRMATION", "EXCLUDED_OUT_OF_SCOPE"})
_ALLOWED_TRANSITIONS["DIFFERENCE_PENDING_CONFIRMATION"] |= frozenset({"TEXTBOOK_ERROR_CONFIRMED", "REVIEW_PENDING", "EXCLUDED_OUT_OF_SCOPE"})
_ALLOWED_TRANSITIONS["ACTUAL_UNRESOLVED"] |= frozenset({"EXCLUDED_OUT_OF_SCOPE"})
_ALLOWED_TRANSITIONS["ACTUAL_DECODE_ERROR"] |= frozenset({"EXCLUDED_OUT_OF_SCOPE"})
_ALLOWED_TRANSITIONS["RULE_CONFLICT"] |= frozenset({"EXCLUDED_OUT_OF_SCOPE"})
_ALLOWED_TRANSITIONS["REGRESSION_BLOCKED"] |= frozenset({"PASS", "DIFFERENCE_PENDING_CONFIRMATION", "REVIEW_PENDING"})


def transition_state(entry: Mapping[str, Any], new_state: str, evidence_updates: Mapping[str, Any] | None = None) -> dict[str, Any]:
    old_state = _text(entry.get("state"))
    new_state = _text(new_state)
    if old_state not in ALL_STATES or new_state not in ALL_STATES:
        raise InvalidTransitionError(f"未知 state transition：{old_state} -> {new_state}")
    if new_state not in _ALLOWED_TRANSITIONS[old_state]:
        raise InvalidTransitionError(f"禁止 state transition：{old_state} -> {new_state}")
    out = deepcopy(dict(entry))
    for key, value in dict(evidence_updates or {}).items():
        if key in {"occurrence_id", "review_id", "pdf_sha256", "ledger_schema_version", "review_id_schema_version"}:
            if value != out.get(key):
                raise InvalidTransitionError(f"不得由人工事件改寫 immutable field：{key}")
            continue
        out[key] = deepcopy(value)
    out["state"] = new_state
    out["active_review"] = new_state in NON_TERMINAL_STATES
    if new_state in TERMINAL_STATES:
        try:
            validate_terminal_state(out)
        except LedgerError as exc:
            raise InvalidTransitionError(str(exc)) from exc
    return out


def reconcile_ledger(
    ledger: Sequence[Mapping[str, Any]],
    actual_source_ids: Iterable[str],
    *,
    worksheet_counts: Mapping[str, tuple[int, int]] | None = None,
    summary_counts: Mapping[str, tuple[int, int]] | None = None,
) -> ReconciliationResult:
    errors: list[str] = []
    duplicate_occurrence_ids: list[str] = []
    duplicate_review_ids: list[str] = []
    for field, target in (("occurrence_id", duplicate_occurrence_ids), ("review_id", duplicate_review_ids)):
        counts = Counter(_text(row.get(field)) for row in ledger if _text(row.get(field)))
        target.extend(sorted(key for key, count in counts.items() if count > 1))
        if target:
            errors.append(f"duplicate {field}: {target[:20]}")

    actual_ids = {_text(value) for value in actual_source_ids if _text(value)}
    ledger_ids = {_text(row.get("occurrence_id")) for row in ledger if _text(row.get("occurrence_id"))}
    actual_only = sorted(actual_ids - ledger_ids)
    ledger_only = sorted(ledger_ids - actual_ids)
    if actual_only:
        errors.append(f"actual 有、ledger 無：{len(actual_only)}")
    if ledger_only:
        errors.append(f"ledger 有、actual 無：{len(ledger_only)}")

    states: dict[str, set[str]] = defaultdict(set)
    unclassified: list[str] = []
    for row in ledger:
        occurrence_id = _text(row.get("occurrence_id"))
        state = _text(row.get("state"))
        if state not in ALL_STATES:
            unclassified.append(occurrence_id)
        else:
            states[state].add(occurrence_id)
    if unclassified:
        errors.append(f"unclassified：{len(unclassified)}")

    memberships: Counter[str] = Counter()
    for members in states.values():
        memberships.update(members)
    multiple_states = sorted(key for key, count in memberships.items() if count > 1)
    if multiple_states:
        errors.append(f"multiple authoritative states：{len(multiple_states)}")
    state_union = set().union(*states.values()) if states else set()
    if state_union != ledger_ids:
        errors.append("authoritative states 聯集不等於 ledger")

    excluded_ids = set().union(*(states.get(state, set()) for state in EXCLUDED_STATES))
    in_scope_ids = ledger_ids - excluded_ids
    if actual_ids != in_scope_ids | excluded_ids:
        errors.append("actual_detected != in_scope + explicitly_excluded")

    worksheet_mismatches: list[str] = []
    for name, pair in dict(worksheet_counts or {}).items():
        expected, observed = pair
        if int(expected) != int(observed):
            worksheet_mismatches.append(f"{name}:{expected}!={observed}")
    if worksheet_mismatches:
        errors.append(f"worksheet count mismatch：{worksheet_mismatches}")
    summary_mismatches: list[str] = []
    for name, pair in dict(summary_counts or {}).items():
        expected, observed = pair
        if int(expected) != int(observed):
            summary_mismatches.append(f"{name}:{expected}!={observed}")
    if summary_mismatches:
        errors.append(f"summary mismatch：{summary_mismatches}")

    return ReconciliationResult(
        ok=not errors,
        errors=tuple(errors),
        details={
            "actual_count": len(actual_ids),
            "ledger_count": len(ledger_ids),
            "in_scope_count": len(in_scope_ids),
            "explicitly_excluded_count": len(excluded_ids),
            "actual_only": actual_only,
            "ledger_only": ledger_only,
            "duplicate_occurrence_ids": duplicate_occurrence_ids,
            "duplicate_review_ids": duplicate_review_ids,
            "multiple_authoritative_states": multiple_states,
            "unclassified": unclassified,
            "worksheet_count_mismatch": worksheet_mismatches,
            "summary_mismatch": summary_mismatches,
            "state_counts": {state: len(states.get(state, set())) for state in sorted(ALL_STATES)},
        },
    )


def completion_gate(
    ledger: Sequence[Mapping[str, Any]],
    reconciliation: ReconciliationResult | Mapping[str, Any],
    regression_report: Mapping[str, Any],
    *,
    source_validation_ok: bool = True,
    runtime_fatal_error: str = "",
) -> dict[str, Any]:
    reconciliation_ok = reconciliation.ok if isinstance(reconciliation, ReconciliationResult) else bool(reconciliation.get("ok"))
    in_scope = [row for row in ledger if row.get("state") not in EXCLUDED_STATES]
    actual_status_counts = Counter(infer_actual_status(row) for row in in_scope)
    expected_status_counts = Counter(infer_expected_status(row) for row in in_scope)
    actual_covered = actual_status_counts["RESOLVED"]
    expected_covered = expected_status_counts["RESOLVED"]
    state_counts = Counter(_text(row.get("state")) for row in ledger)
    nonterminal_count = sum(state_counts[state] for state in NON_TERMINAL_STATES)
    comparable_terminal_count = sum(state_counts[state] for state in COMPARABLE_TERMINAL_STATES)

    regression_required = int(regression_report.get("required", 0) or 0)
    regression_executed = int(regression_report.get("executed", 0) or 0)
    regression_failed = int(regression_report.get("failed", 0) or 0)
    regression_not_executed = int(regression_report.get("not_executed", 0) or 0)
    regression_duplicates = int(regression_report.get("duplicate_case_id", 0) or 0)
    regression_ok = bool(regression_report.get("ok")) and (
        regression_required > 0
        and regression_executed == regression_required
        and regression_failed == 0
        and regression_not_executed == 0
        and regression_duplicates == 0
    )

    hard_gates = {
        "source_validation": bool(source_validation_ok),
        "runtime_fatal_error_zero": not runtime_fatal_error,
        "ledger_reconciliation": reconciliation_ok,
        "in_scope_occurrence_count_positive": len(in_scope) > 0,
        "actual_coverage_100": bool(in_scope) and actual_covered == len(in_scope),
        "expected_coverage_100": bool(in_scope) and expected_covered == len(in_scope),
        "pending_zero": nonterminal_count == 0,
        # v5.5.0 lane gates inspect evidence dimensions directly.  A current
        # ACTUAL_DECODE_ERROR may coexist with EXPECTED_CONFLICT/AMBIGUOUS and
        # must not hide that expected-side incompleteness.
        "decode_error_zero": actual_status_counts["DECODE_ERROR"] == 0 and actual_status_counts["UNRESOLVED"] == 0,
        "expected_unresolved_zero": expected_status_counts["UNRESOLVED"] == 0,
        "expected_ambiguous_zero": expected_status_counts["AMBIGUOUS"] == 0,
        "rule_conflict_zero": expected_status_counts["CONFLICT"] == 0,
        "data_integrity_error_zero": state_counts["DATA_INTEGRITY_ERROR"] == 0,
        "source_invalid_zero": state_counts["SOURCE_INVALID"] == 0,
        "regression_gate": regression_ok,
        "all_in_scope_terminal": comparable_terminal_count == len(in_scope),
    }
    blocked = (
        not source_validation_ok
        or bool(runtime_fatal_error)
        or not reconciliation_ok
        or state_counts["DATA_INTEGRITY_ERROR"] > 0
        or state_counts["SOURCE_INVALID"] > 0
    )
    complete = all(hard_gates.values())
    status = PIPELINE_BLOCKED if blocked else PROOFREAD_COMPLETE if complete else PROCESSING_FINISHED
    return {
        "status": status,
        "complete": complete,
        "hard_gates": hard_gates,
        "failed_gates": [name for name, passed in hard_gates.items() if not passed],
        "in_scope_total": len(in_scope),
        "actual_covered": actual_covered,
        "expected_covered": expected_covered,
        "actual_coverage": actual_covered / len(in_scope) if in_scope else 0.0,
        "expected_coverage": expected_covered / len(in_scope) if in_scope else 0.0,
        "state_counts": dict(state_counts),
        "actual_status_counts": dict(actual_status_counts),
        "expected_status_counts": dict(expected_status_counts),
        "regression": dict(regression_report),
    }


def ledger_to_jsonable(ledger: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [deepcopy(dict(row)) for row in ledger]


def candidate_payload_sha256(
    ledger: Sequence[Mapping[str, Any]],
    mandatory_results: Sequence[Mapping[str, Any]],
    pdf_regression_results: Sequence[Mapping[str, Any]],
) -> str:
    """Hash authoritative candidate evidence without Excel formatting noise."""

    ledger_fields = (
        "ledger_schema_version", "review_id_schema_version", "occurrence_id", "review_id", "active_review", "state",
        "source_row_number", "identity_confidence", "identity_row_fallback", "identity_collision_base",
        "pdf_sha256", "physical_page", "printed_page", "char", "stable_key", "font", "font_xref", "glyph_id",
        "zhuyin_component_id", "x0", "y0", "x1", "y1", "actual", "actual_raw", "actual_evidence", "actual_status",
        "expected_evidence", "expected_status", "context_evidence", "comparison_result", "source_view", "blocking_state", "exclusion_state", "canonical_occurrence_id",
        "exclusion_reason", "note",
    )
    mandatory_fields = (
        "case_id", "context", "target_char", "target_index", "expected_resolution", "expected_set",
        "required_matched_phrase", "forbidden_matched_phrase", "control_type", "result", "actual_resolution",
        "actual_expected", "actual_matched_phrase", "failure_reason", "note",
    )
    pdf_fields = (
        "案例ID", "課本頁", "完整詞語", "字元", "真值實際注音", "真值預期注音", "真值結論",
        "回歸結果", "執行說明", "來源", "備註",
    )

    canonical_ledger = []
    for row in ledger:
        item = {field: _text(row.get(field)) for field in ledger_fields}
        item["actual"] = canonical_bopomofo(row.get("actual"))
        item["expected_set"] = list(normalize_expected_set(row.get("expected_set")))
        item["confirmation_gates"] = {
            gate: bool((row.get("confirmation_gates") or {}).get(gate)) for gate in CONFIRMATION_GATES
        }
        canonical_ledger.append(item)
    canonical_ledger.sort(key=lambda row: row["occurrence_id"])

    def canonical_rows(rows: Sequence[Mapping[str, Any]], fields: Sequence[str], id_field: str) -> list[dict[str, str]]:
        out = [{field: _text(row.get(field)) for field in fields} for row in rows]
        return sorted(out, key=lambda row: row.get(id_field, ""))

    payload = {
        "ledger": canonical_ledger,
        "mandatory": canonical_rows(mandatory_results, mandatory_fields, "case_id"),
        "pdf_regression": canonical_rows(pdf_regression_results, pdf_fields, "案例ID"),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
