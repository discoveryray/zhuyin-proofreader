from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping
from unicodedata import normalize

import fitz
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from export_zhuyin_readings import (
    ACTUAL_DECODER_SOURCE_FILES,
    VERSION as DECODER_VERSION,
    decode,
    DEFAULT_MAP,
    DEFAULT_GROUPS,
)
from pronunciation_rule_engine import (RuleDecision, load_rules, resolve_rules, resolve_handbook_position_rule, resolve_company_position_rule, resolve_semantic_complete_word_rule, resolve_yi, resolve_yi_base_context, resolve_mama, resolve_reduplication, resolve_bu, resolve_ge_classifier, resolve_ya_particle, resolve_fen_numeric_unit, reading_matches, reading_options, target_in_phrase)
from concise_expected_resolver import load_concise_dictionary, resolve_concise_word, resolve_concise_single
from occurrence_ledger import (
    LEDGER_SCHEMA_VERSION,
    WORKBOOK_SCHEMA_VERSION,
    build_occurrence_ledger,
    candidate_payload_sha256,
    canonical_bopomofo,
    normalize_expected_set,
    reconcile_ledger,
    validate_occurrence_ledger,
)
from runtime_regression_gate import (
    load_mandatory_cases,
    run_mandatory_regressions,
    validate_regression_execution,
)
from runtime_source_validation import (
    compute_actual_asset_fingerprint,
    compute_expected_asset_fingerprint,
    rewrite_actual_workbook_fingerprint,
    validate_asset_manifest,
)
from cross_version_compat import fingerprint_compatible, schema_compatible
from actual_review import actual_workbook_dynamic_dependencies

PROGRAM = "注音校對候選比較器"
VERSION = "5.6.2"
EXPECTED_RESOLVER_SOURCE_FILES = [
    "check_pronunciation_candidates.py",
    "pronunciation_rule_engine.py",
    "concise_expected_resolver.py",
    "runtime_regression_gate.py",
    "occurrence_ledger.py",
    "runtime_source_validation.py",
]
DEFAULT_DICT = Path(__file__).with_name("一字多音-字典檔.xlsx")
DEFAULT_RULES = Path(__file__).with_name("pronunciation_lexical_rules.csv")
DEFAULT_HANDBOOK_RULES = Path(__file__).with_name("統一用字手冊_注音規則.csv")
DEFAULT_HANDBOOK_CONSTRAINTS = Path(__file__).with_name("統一用字手冊_字音限制.csv")
DEFAULT_REGRESSIONS = Path(__file__).with_name("pronunciation_regressions.csv")
DEFAULT_CHAR_OVERRIDES = Path(__file__).with_name("character_overrides.csv")
DEFAULT_CONTEXT_OVERRIDES = Path(__file__).with_name("source_context_overrides.csv")
DEFAULT_CONCISE_DICT = Path(__file__).with_name("《國語辭典簡編本》資料 dict_concised_2014_20260626.xlsx")
DEFAULT_MANDATORY_REGRESSIONS = Path(__file__).with_name("mandatory_regression_cases.csv")

BOPOMOFO = set("ㄅㄆㄇㄈㄉㄊㄋㄌㄍㄎㄏㄐㄑㄒㄓㄔㄕㄖㄗㄘㄙㄧㄨㄩㄚㄛㄜㄝㄞㄟㄠㄡㄢㄣㄤㄥㄦ")
TONES = set("ˊˇˋ˙")


def norm_char(value) -> str:
    return normalize("NFKC", str(value or "")).strip()


def norm_bopomofo(value) -> str:
    s = str(value or "").strip()
    if not s:
        return ""
    s = s.replace("‧", "˙").replace("・", "˙")
    s = s.replace("一", "ㄧ")
    s = re.sub(r"[（(][^）)]*[）)]", "", s).strip()
    s = s.replace("\u0307", "˙").replace(" ", "")
    if s and all(ch in BOPOMOFO or ch in TONES for ch in s):
        return s
    return ""


def evaluate_historical_regression(rec: dict | None, rg: dict) -> tuple[str, str]:
    """Evaluate historical truth *after* current actual/expected were built.

    Historical rows are regression controls only.  A formerly wrong occurrence
    passes when the current edition has been corrected to the independently
    reproduced expected reading, or when the wrong reading still exists but the
    current resolver independently flags the difference.  Historical actual is
    never required to remain unchanged.
    """
    if rec is None:
        return "NOT_EXECUTED", "應適用 occurrence 未找到或 scope/context 無法定位"

    status = str(rg.get("status") or "確認錯誤").strip()
    truth_expected = norm_bopomofo(rg.get("expected_reading"))
    current_actual = norm_bopomofo(rec.get("實際注音"))
    current_expected = set(normalize_expected_set(rec.get("預期注音")))

    if not current_actual:
        return "FAIL", "occurrence 已定位，但現版 actual 未能獨立重現"
    if not truth_expected:
        return "FAIL", "歷史回歸真值缺少合法 expected"
    if not current_expected:
        return "FAIL", "occurrence 已定位，但現版 expected 未能獨立重現"
    if truth_expected not in current_expected:
        return "FAIL", f"現版 expected {sorted(current_expected)} 不含歷史真值 {truth_expected}"

    current_is_pass = current_actual in current_expected
    if status == "確認正確":
        if current_actual == truth_expected and current_is_pass:
            return "PASS", "歷史正確控制仍由現版 actual＋expected 獨立重現"
        return "FAIL", f"歷史正確控制退化：現版 actual={current_actual} expected={sorted(current_expected)}"

    if status == "確認錯誤":
        if current_actual == truth_expected and current_is_pass:
            return "PASS", "歷史錯誤已於現版修正；現版 actual 與 expected 獨立一致"
        if current_actual != truth_expected and not current_is_pass:
            return "PASS", "歷史錯誤仍存在或形成新差異；現版流程已獨立檢出差異"
        return "FAIL", f"歷史錯誤控制未被安全處理：現版 actual={current_actual} expected={sorted(current_expected)}"

    return "FAIL", f"未知歷史回歸狀態：{status}"


@dataclass(frozen=True)
class HistoricalRegressionApplicability:
    mode: str
    should_locate: bool
    completion_gate: bool
    locator_scope: str
    state: str
    note: str = ""

    def __iter__(self):
        """Preserve the v5.6.2 tuple API as gate-applicability compatibility.

        Legacy callers unpacked ``(applicable, note)``.  The boolean now means
        completion-gate applicability; callers that need locator behavior must
        use the explicit ``should_locate`` attribute.
        """
        yield self.completion_gate
        yield self.note


def historical_regression_routed_to_pdf(rg: Mapping[str, Any], pdf_stem: str) -> bool:
    """Use names only as a coarse book-family route, never exact source identity."""
    mode = str(rg.get("applicability_mode") or "hard_gate").strip().lower()
    if mode == "hard_gate" and str(rg.get("source_pdf_sha256") or "").strip():
        # Exact bytes remain authoritative even if the file was renamed.
        return True
    family_hint = str(rg.get("pdf_contains") or "").strip()
    return not family_hint or family_hint in str(pdf_stem or "")


def historical_regression_applicability(
    rg: Mapping[str, Any], current_pdf_sha256: str
) -> HistoricalRegressionApplicability:
    """Plan source applicability separately from occurrence re-location.

    Filename substrings are useful for routing a case to a textbook family, but
    they are not a stable source-version identity. ``hard_gate`` may bind to
    exact PDF bytes, ``reference_only`` always attempts a non-gating audit, and
    ``portable_gate`` uses only pronunciation-independent locator fields and
    must resolve to exactly one occurrence across the whole book.

    Existing unbound ``hard_gate`` rows retain their legacy page-scoped gating
    behavior. New cross-version gates must use the explicit portable mode.
    Historical actual/expected truth is never consulted here.
    """
    mode = str(rg.get("applicability_mode") or "hard_gate").strip().lower()
    if mode not in {"hard_gate", "portable_gate", "reference_only"}:
        raise ValueError(f"歷史回歸 applicability_mode 無效：{rg.get('case_id')} / {mode}")
    if str(rg.get("target_occurrence_index") or "").strip():
        _regression_target_relative_offset(dict(rg))
    if mode == "reference_only":
        return HistoricalRegressionApplicability(
            mode=mode,
            should_locate=True,
            completion_gate=False,
            locator_scope="historical_page",
            state="REFERENCE_AUDIT",
            note="非阻擋參考案例，不列入完成門檻；仍嘗試依現版 occurrence locator 重現",
        )

    if mode == "portable_gate":
        if str(rg.get("source_pdf_sha256") or "").strip():
            raise ValueError(f"portable_gate 不得混用 source_pdf_sha256：{rg.get('case_id')}")
        phrase = normalize("NFKC", str(rg.get("phrase") or "").strip())
        target = normalize("NFKC", str(rg.get("target_char") or "").strip())
        if not phrase or not target or target not in phrase:
            raise ValueError(f"portable_gate 缺少完整 phrase／target identity：{rg.get('case_id')}")
        if phrase.count(target) > 1 and str(rg.get("target_occurrence_index") or "").strip() == "":
            raise ValueError(f"portable_gate 的 phrase 含重複 target，必須指定 target_occurrence_index：{rg.get('case_id')}")
        _regression_target_relative_offset(dict(rg))
        locator_context = normalize("NFKC", str(rg.get("locator_context") or "").strip())
        if locator_context and phrase not in locator_context:
            raise ValueError(f"portable_gate locator_context 必須包含完整 phrase：{rg.get('case_id')}")
        return HistoricalRegressionApplicability(
            mode=mode,
            should_locate=True,
            completion_gate=True,
            locator_scope="whole_book_portable",
            state="PORTABLE_GATE",
        )

    bound_hash = str(rg.get("source_pdf_sha256") or "").strip().lower()
    current_hash = str(current_pdf_sha256 or "").strip().lower()
    if bound_hash:
        if not re.fullmatch(r"[0-9a-f]{64}", bound_hash):
            raise ValueError(f"歷史回歸 source_pdf_sha256 格式無效：{rg.get('case_id')}")
        if bound_hash != current_hash:
            return HistoricalRegressionApplicability(
                mode=mode,
                should_locate=False,
                completion_gate=False,
                locator_scope="exact_pdf",
                state="SOURCE_NOT_APPLICABLE",
                note="來源 PDF SHA-256 不同；此歷史座標不適用於現版 PDF",
            )
        return HistoricalRegressionApplicability(
            mode=mode,
            should_locate=True,
            completion_gate=True,
            locator_scope="exact_pdf",
            state="HARD_GATE_EXACT_SHA",
        )
    return HistoricalRegressionApplicability(
        mode=mode,
        should_locate=True,
        completion_gate=True,
        locator_scope="historical_page",
        state="HARD_GATE_LEGACY_UNBOUND",
        note="既有未綁 SHA hard_gate；維持原有頁碼 locator 行為",
    )


def validate_historical_regression_definitions(
    definitions: Iterable[Mapping[str, Any]], current_pdf_sha256: str
) -> None:
    """Fail closed on every definition before filename-family routing.

    A malformed mode or locator must not escape validation merely because its
    filename hint does not match the PDF currently being processed.
    """
    for definition in definitions:
        historical_regression_applicability(definition, current_pdf_sha256)


def _regression_target_relative_offset(rg: dict) -> int | None:
    """Return the 0-based target-character offset inside a historical phrase.

    The optional CSV field target_occurrence_index is an identity-only locator:
    it never consults historical actual/expected readings.
    """
    raw = str(rg.get("target_occurrence_index") or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except Exception as exc:
        raise ValueError(f"歷史回歸 target_occurrence_index 非整數：{rg.get('case_id')}: {raw}") from exc
    if value < 0:
        raise ValueError(f"歷史回歸 target_occurrence_index 不得為負：{rg.get('case_id')}: {raw}")
    phrase = normalize("NFKC", str(rg.get("phrase") or ""))
    target = normalize("NFKC", str(rg.get("target_char") or "").strip())
    offsets = [idx for idx, chx in enumerate(phrase) if chx == target]
    if value >= len(offsets):
        raise ValueError(
            f"歷史回歸 target_occurrence_index 超出詞內目標字數：{rg.get('case_id')} / {phrase} / {target} / {value}"
        )
    return offsets[value]


def _historical_locator_context_matches(rec: Mapping[str, Any], rg: Mapping[str, Any]) -> bool:
    required = normalize("NFKC", str(rg.get("locator_context") or "").strip())
    if not required:
        return True
    return any(
        required in normalize("NFKC", str(rec.get(field) or ""))
        for field in ("所在行", "局部詞境", "上下文")
    )


def historical_regression_potential_match(rec: dict, rg: dict, *, match_page: bool = True) -> bool:
    """Match a historical control to the exact phrase span containing rec.

    Historical pronunciation truth is deliberately excluded from occurrence
    identity.  The phrase must cover the current target position in the
    reconstructed line; appearing elsewhere on the same line is insufficient.
    """
    same_page = True
    if match_page:
        try:
            same_page = int(rec.get("課本頁") or -1) == int(rg.get("page") or -2)
        except Exception:
            same_page = str(rec.get("課本頁") or "").strip() == str(rg.get("page") or "").strip()
    target = normalize("NFKC", str(rg.get("target_char") or "").strip())
    if not same_page or normalize("NFKC", str(rec.get("字元") or "")) != target:
        return False

    phrase = normalize("NFKC", str(rg.get("phrase") or "").strip())
    line_text = normalize("NFKC", str(rec.get("所在行") or ""))
    if not phrase or not line_text:
        return False
    try:
        target_pos = int(rec.get("行內字元位置"))
    except Exception:
        return False
    if not (0 <= target_pos < len(line_text)) or line_text[target_pos] != target:
        return False

    required_relative_offset = _regression_target_relative_offset(rg)
    start = line_text.find(phrase)
    while start >= 0:
        end = start + len(phrase)
        if start <= target_pos < end:
            if required_relative_offset is None:
                if phrase[target_pos - start] == target and _historical_locator_context_matches(rec, rg):
                    return True
            elif target_pos == start + required_relative_offset and _historical_locator_context_matches(rec, rg):
                return True
        start = line_text.find(phrase, start + 1)

    # Some PDFs split the visible word into one-character reconstructed lines
    # even though the coordinate-order fallback context still preserves the
    # neighboring word.  Use that target-centered fallback only when both the
    # phrase and fallback context contain exactly one target character.  This
    # prevents the old "phrase exists elsewhere on a long line" false match.
    if required_relative_offset is None and phrase.count(target) == 1:
        fallback_context = normalize("NFKC", str(rec.get("上下文") or ""))
        if (
            fallback_context
            and fallback_context.count(target) == 1
            and phrase in fallback_context
            and _historical_locator_context_matches(rec, rg)
        ):
            return True
    return False


def execute_historical_regression(
    rg: dict,
    occurrence_results: list[dict],
    current_pdf_sha256: str,
    current_printed_pages: set[str] | None = None,
) -> dict[str, Any]:
    """Locate first, then evaluate truth, while preserving gate semantics."""
    applicability = historical_regression_applicability(rg, current_pdf_sha256)
    page = str(rg.get("page") or "").strip()
    current_printed_pages = current_printed_pages or set()
    match_page = applicability.locator_scope != "whole_book_portable"
    potentials = [
        rec for rec in occurrence_results
        if applicability.should_locate and historical_regression_potential_match(rec, rg, match_page=match_page)
    ]

    result = ""
    state = applicability.state
    note = applicability.note
    matched_records: list[dict] = []
    if not applicability.should_locate:
        result = "NOT_APPLICABLE"
    elif match_page and page and page not in current_printed_pages:
        if applicability.mode == "reference_only":
            result = "NOT_APPLICABLE"
            state = "REFERENCE_NOT_FOUND"
            note = "reference not found：目前分檔未包含歷史課本頁"
        else:
            result = "NOT_EXECUTED"
            state = "GATE_NOT_EXECUTED"
            note = "目前分檔未包含此課本頁；由全冊聚合器跨 PDF 對帳"
    elif not potentials:
        if applicability.mode == "reference_only":
            result = "NOT_APPLICABLE"
            state = "REFERENCE_NOT_FOUND"
            note = "reference not found：現版 occurrence locator 未命中"
        else:
            result = "NOT_EXECUTED"
            state = "GATE_NOT_EXECUTED"
            note = "應適用 occurrence 未找到或 scope/context 無法定位"
    elif len(potentials) > 1:
        status = str(rg.get("status") or "確認錯誤").strip()
        if applicability.mode == "hard_gate" and status == "確認正確":
            evaluations = [evaluate_historical_regression(item, rg) for item in potentials]
            failures = [message for item_result, message in evaluations if item_result != "PASS"]
            if not failures:
                result = "PASS"
                state = "HARD_GATE_EQUIVALENT_MATCHES"
                note = f"歷史正確控制在同頁定位到 {len(potentials)} 個等價 occurrence；全部獨立通過"
                matched_records = potentials
            else:
                result = "FAIL"
                state = "HARD_GATE_EVALUATION_FAILED"
                note = "歷史正確控制至少一筆未通過：" + "｜".join(failures[:3])
        else:
            result = "FAIL"
            state = "REFERENCE_AMBIGUOUS" if applicability.mode == "reference_only" else "IDENTITY_AMBIGUITY"
            note = f"identity ambiguity：locator 命中 {len(potentials)} 筆；禁止任選 occurrence"
    else:
        result, note = evaluate_historical_regression(potentials[0], rg)
        state = "REFERENCE_MATCHED" if applicability.mode == "reference_only" else "GATE_EXECUTED"
        matched_records = potentials

    return {
        "mode": applicability.mode,
        "completion_gate": applicability.completion_gate,
        "applicability_state": state,
        "result": result,
        "note": note,
        "match_count": len(potentials),
        "matched_records": matched_records,
    }


def split_readings(raw) -> list[str]:
    """Parse one or more Bopomofo readings conservatively.

    A few rows in the project dictionary annotate a reading with a single
    source label such as 「語」 or 「讀」 (e.g. 翹: ㄑㄧㄠˊ讀;ㄑㄧㄠˋ語).
    Earlier versions rejected the whole reading because of that suffix.  v3.6
    accepts the Bopomofo token only when the remaining non-Bopomofo text is
    exactly one of those known annotation labels.  Descriptive rows such as
    「一」 therefore remain handled by the dedicated sandhi resolver.
    """
    out = []
    bop_pat = "".join(sorted(BOPOMOFO | TONES))
    for part in re.split(r"[;；]", str(raw or "")):
        p = norm_bopomofo(part)
        if not p:
            text = normalize("NFKC", str(part or "")).strip()
            tokens = re.findall(rf"[{re.escape(bop_pat)}]+", text)
            if len(tokens) == 1:
                remainder = text.replace(tokens[0], "", 1).strip(" \t\r\n\"'「」『』()（）")
                if remainder in {"語", "讀"}:
                    p = norm_bopomofo(tokens[0])
        if p and p not in out:
            out.append(p)
    return out


def clean_example(value: str) -> str:
    s = normalize("NFKC", str(value or ""))
    s = re.sub(r"[（(][^）)]*[）)]", "", s)
    s = s.strip(" \t\r\n「」『』‘’“”\"'。！？!?，,；;：:")
    return s


def split_example_items(group: str) -> list[str]:
    """Split 、/,/， only outside parenthetical notes."""
    out, buf = [], []
    depth = 0
    pairs = {"（": "）", "(": ")"}
    opens = set(pairs)
    closes = set(pairs.values())
    for ch in str(group or ""):
        if ch in opens:
            depth += 1
            buf.append(ch)
            continue
        if ch in closes:
            depth = max(0, depth - 1)
            buf.append(ch)
            continue
        if ch in "、,，" and depth == 0:
            item = "".join(buf).strip()
            if item:
                out.append(item)
            buf = []
        else:
            buf.append(ch)
    item = "".join(buf).strip()
    if item:
        out.append(item)
    return out


def load_dictionary(path: Path):
    wb = load_workbook(path, data_only=True, read_only=True)
    ws = wb.active
    headers = [str(c.value or "").strip() for c in next(ws.iter_rows(min_row=1, max_row=1))]
    idx = {h: i for i, h in enumerate(headers)}
    records = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        def get(name):
            i = idx.get(name)
            return row[i] if i is not None else None

        ch = norm_char(get("char") or get("word"))
        if not ch:
            continue
        raw_readings = str(get("readings") or "").strip()
        readings = split_readings(raw_readings)
        raw_examples = str(get("examples") or "").strip()
        example_groups = re.split(r"[;；]", raw_examples) if raw_examples else []
        # Some dictionary rows insert a standalone 「姓」 group immediately
        # after the pronunciation group it belongs to (乾、盛、共、齊, etc.).
        # That made group counts differ and caused all example-to-reading
        # mappings for the row to be discarded.  Merge only the exact standalone
        # marker 「姓」 into the preceding group; no semantic guessing is used.
        if len(example_groups) != len(readings) and example_groups:
            aligned_groups = []
            for group in example_groups:
                if clean_example(group) == "姓" and aligned_groups:
                    aligned_groups[-1] = aligned_groups[-1] + "、姓"
                else:
                    aligned_groups.append(group)
            example_groups = aligned_groups
        examples_by_reading = defaultdict(list)
        if len(example_groups) == len(readings):
            for reading, group in zip(readings, example_groups):
                for item in split_example_items(group):
                    ex = clean_example(item)
                    if len(ex) >= 2 and ch in ex and ex not in examples_by_reading[reading]:
                        examples_by_reading[reading].append(ex)
        records[ch] = {
            "readings": readings,
            "raw_readings": raw_readings,
            "examples": raw_examples,
            "tags": str(get("tags") or "").strip(),
            "examples_by_reading": dict(examples_by_reading),
        }
    return records


def load_handbook_constraints(path: Path | None):
    """Load top-priority pronunciation constraints from 統一用字手冊.

    These constraints are independent of the PDF observed pronunciation.  They
    either limit the set of globally valid readings for a character or provide
    source metadata used by the checker.
    """
    out = {}
    if not path or not path.exists():
        return out
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            ch = norm_char(row.get("char"))
            vals = [norm_bopomofo(x) for x in str(row.get("allowed_readings") or "").replace(";", "；").split("；")]
            vals = [x for x in vals if x]
            if not ch or not vals:
                continue
            out[ch] = {
                "readings": vals,
                "source": (row.get("source") or "統一用字手冊113.10.04.docx").strip(),
                "section": (row.get("source_section") or "").strip(),
                "note": (row.get("note") or "").strip(),
            }
    return out


def apply_handbook_constraints(dictionary: dict, constraints: dict) -> dict:
    """Apply the handbook before the general dictionary/rule system.

    If the handbook lists the globally accepted readings for a character,
    lower-priority dictionary readings outside that set are removed.  Missing
    dictionary rows are created from the handbook so the character is still
    proofread.  Example mappings are filtered to the surviving readings.
    """
    for ch, hb in constraints.items():
        allowed = list(hb.get("readings") or [])
        if not allowed:
            continue
        d = dictionary.get(ch)
        if not d:
            dictionary[ch] = {
                "readings": allowed,
                "raw_readings": "；".join(allowed),
                "examples": "",
                "tags": "統一用字手冊最高優先字音限制",
                "examples_by_reading": {},
                "handbook_allowed_readings": allowed,
                "handbook_source": hb.get("source", ""),
                "handbook_section": hb.get("section", ""),
                "handbook_note": hb.get("note", ""),
            }
            continue
        old = list(d.get("readings") or [])
        surviving = [r for r in old if r in allowed]
        # The handbook is authoritative for this project.  If the lower
        # dictionary has no overlap, replace its set rather than silently
        # discarding the handbook rule.
        d["readings"] = surviving or allowed
        d["raw_readings"] = "；".join(d["readings"])
        d["examples_by_reading"] = {
            r: ex for r, ex in (d.get("examples_by_reading") or {}).items()
            if r in d["readings"]
        }
        d["handbook_allowed_readings"] = allowed
        d["handbook_source"] = hb.get("source", "")
        d["handbook_section"] = hb.get("section", "")
        d["handbook_note"] = hb.get("note", "")
    return dictionary

def handbook_constraint_decision(ch: str, d: dict) -> RuleDecision | None:
    vals = list(d.get("handbook_allowed_readings") or [])
    if not vals:
        return None
    return RuleDecision(
        expected_reading="；".join(vals),
        rule_id=f"HANDBOOK-ALLOWED-{ch}",
        rule_mode="handbook_constraint",
        matched_phrase=ch,
        source=d.get("handbook_source") or "統一用字手冊113.10.04.docx",
        source_url="",
        note="｜".join(x for x in [d.get("handbook_section", ""), d.get("handbook_note", "")] if x),
        priority=9000,
        evidence_level="handbook_highest",
    )


def unique_path(pdf: Path, suffix: str) -> Path:
    base = pdf.with_name(f"{pdf.stem}_{suffix}.xlsx")
    if not base.exists():
        return base
    i = 2
    while True:
        p = pdf.with_name(f"{pdf.stem}_{suffix}_{i}.xlsx")
        if not p.exists():
            return p
        i += 1


def load_existing_decode_stats(path: Path, expected_fingerprint: Mapping[str, Any] | str | None = None, expected_pdf_sha256: str = ""):
    """Read decoder counts from an existing actual-reading workbook.

    Cross-version reuse is based on PDF/evidence assets, not the application
    release number or source-code hash.
    """
    wb = load_workbook(path, data_only=True, read_only=True)
    try:
        if "摘要" not in wb.sheetnames or "v5.2中繼資料" not in wb.sheetnames:
            return None
        metadata = {
            str(row[0] or ""): row[1] if len(row) > 1 else None
            for row in wb["v5.2中繼資料"].iter_rows(values_only=True)
            if row and row[0] not in (None, "")
        }
        if not schema_compatible(metadata.get("workbook_schema_version"), WORKBOOK_SCHEMA_VERSION):
            return None
        if not schema_compatible(metadata.get("ledger_schema_version"), LEDGER_SCHEMA_VERSION):
            return None
        if metadata.get("pdf_sha256") != expected_pdf_sha256:
            return None
        if not isinstance(expected_fingerprint, Mapping):
            if not expected_fingerprint or metadata.get("actual_asset_fingerprint") != expected_fingerprint:
                return None
        elif not fingerprint_compatible(
            metadata.get("actual_asset_fingerprint"),
            metadata.get("actual_asset_fingerprint_components"),
            expected_fingerprint,
            chain="actual",
        ):
            return None
        vals = {}
        for row in wb["摘要"].iter_rows(values_only=True):
            if len(row) >= 2 and row[0] is not None:
                vals[str(row[0])] = row[1]
        total = int(vals.get("偵測到注音字形筆數") or 0)
        mapped = int(vals.get("已解碼筆數") or 0)
        coverage = float(vals.get("目前解碼覆蓋率") or (mapped / total if total else 0.0))
        return {"total": total, "mapped": mapped, "coverage": coverage, "output": path, "reused_existing": True}
    finally:
        wb.close()

def style_header(ws, fill_hex="D9EAF7"):
    ws.freeze_panes = "A2"
    ws.sheet_view.showGridLines = False
    if ws.max_row >= 1:
        fill = PatternFill("solid", fgColor=fill_hex)
        for c in ws[1]:
            c.fill = fill
            c.font = Font(bold=True)
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    if ws.max_row >= 1 and ws.max_column >= 1:
        ws.auto_filter.ref = ws.dimensions
    for col in range(1, ws.max_column + 1):
        letter = get_column_letter(col)
        max_len = 0
        for cell in ws[letter][: min(ws.max_row, 800)]:
            max_len = max(max_len, len(str(cell.value or "")))
        ws.column_dimensions[letter].width = min(max(max_len + 2, 10), 48)


def build_fallback_context(page_rows, pos, radius=14):
    text, _ = build_fallback_context_with_pos(page_rows, pos, radius=radius)
    return text


def build_fallback_context_with_pos(page_rows, pos, radius=14):
    """Return a coordinate-sequence context plus the exact target offset.

    This is a *secondary* rescue path for layouts where the visual-line builder
    splits or scrambles a short phrase.  The caller must only accept high-
    priority exact lexical rules on this context; default/single-character
    rules are deliberately excluded so page-wide proximity cannot manufacture
    an expected reading.
    """
    a = max(0, pos - radius)
    b = min(len(page_rows), pos + radius + 1)
    before = "".join(norm_char(r["字元"]) for r in page_rows[a:pos] if r.get("字元"))
    target = norm_char(page_rows[pos].get("字元") or "") if 0 <= pos < len(page_rows) else ""
    after = "".join(norm_char(r["字元"]) for r in page_rows[pos+1:b] if r.get("字元"))
    return before + target + after, len(before)


def _sentence_window(text: str, target_pos: int | None):
    """Return the punctuation-bounded sentence containing target_pos.

    Textbook pages often place two independent callout boxes on the same
    baseline.  The lexical engine should see the target sentence, not every
    glyph that happens to share a y-coordinate.  Strong sentence-ending
    punctuation is used as a safe boundary; commas remain inside the sentence.
    """
    if target_pos is None or target_pos < 0 or target_pos >= len(text):
        return text, target_pos
    stops = set("。！？!?；;")
    left = 0
    for i in range(target_pos - 1, -1, -1):
        if text[i] in stops:
            left = i + 1
            break
    right = len(text)
    for i in range(target_pos, len(text)):
        if text[i] in stops:
            right = i + 1
            break
    sent = text[left:right].strip()
    if not sent:
        return text, target_pos
    # strip() can remove leading spaces, so recalculate by locating the exact
    # slice inside the unstripped span rather than assuming a fixed offset.
    raw = text[left:right]
    lead = len(raw) - len(raw.lstrip())
    return sent, max(0, target_pos - left - lead)


def _rawdict_color_rank(value):
    """Prefer visible coloured/black text over a white duplicate underlay."""
    try:
        if isinstance(value, int):
            r = (value >> 16) & 255
            g = (value >> 8) & 255
            b = value & 255
            return 0 if min(r, g, b) >= 247 else 1
        if isinstance(value, (tuple, list)) and len(value) >= 3:
            vals = [float(x) for x in value[:3]]
            if max(vals) <= 1.0:
                return 0 if min(vals) >= 0.965 else 1
            return 0 if min(vals) >= 247 else 1
    except Exception:
        pass
    return 1


def _repair_same_baseline_fragment_order(block_recs):
    """Repair RAWDICT fragments whose logical line numbers are out of visual order.

    Some textbook PDFs emit one visually continuous horizontal label as several
    one-glyph RAWDICT ``line`` records.  The record order is not necessarily the
    reading order (for example the visible ``小知識`` may arrive as ``小／識／知``).
    We stay inside the original PDF text block and only reorder line fragments
    whose vertical boxes strongly overlap.  Normal non-overlapping lines keep
    their original RAWDICT line order.  No OCR, dictionary, pronunciation or
    language-model evidence is used here; this is geometry-only text-layer repair.
    """
    if len(block_recs) < 2:
        return list(block_recs)

    by_line = defaultdict(list)
    for rec in block_recs:
        by_line[int(rec.get("line_no") or 0)].append(rec)
    line_nos = sorted(by_line)
    if len(line_nos) < 2:
        return list(block_recs)

    info = {}
    for ln in line_nos:
        rows = by_line[ln]
        x0 = min(float(z["bbox"][0]) for z in rows)
        y0 = min(float(z["bbox"][1]) for z in rows)
        x1 = max(float(z["bbox"][2]) for z in rows)
        y1 = max(float(z["bbox"][3]) for z in rows)
        wmodes = {int(z.get("wmode") or 0) for z in rows}
        info[ln] = {"bbox": (x0, y0, x1, y1), "wmode": next(iter(wmodes)) if len(wmodes) == 1 else -1}

    parent = {ln: ln for ln in line_nos}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i, a in enumerate(line_nos):
        ia = info[a]
        if ia["wmode"] != 0:
            continue
        ax0, ay0, ax1, ay1 = ia["bbox"]
        ah = max(1e-6, ay1 - ay0)
        acy = (ay0 + ay1) / 2
        for b in line_nos[i + 1:]:
            ib = info[b]
            if ib["wmode"] != 0:
                continue
            bx0, by0, bx1, by1 = ib["bbox"]
            bh = max(1e-6, by1 - by0)
            bcy = (by0 + by1) / 2
            inter = max(0.0, min(ay1, by1) - max(ay0, by0))
            overlap = inter / max(1e-6, min(ah, bh))
            # Strong same-baseline evidence.  This intentionally does not merge
            # merely nearby rows; it repairs only fragments occupying the same
            # visual text band.
            if overlap >= 0.80 and abs(acy - bcy) <= 0.25 * min(ah, bh):
                union(a, b)

    comps = defaultdict(list)
    for ln in line_nos:
        comps[find(ln)].append(ln)

    ordered_components = sorted(comps.values(), key=lambda xs: min(xs))
    out = []
    for comp in ordered_components:
        rows = []
        for ln in sorted(comp):
            rows.extend(by_line[ln])
        if len(comp) > 1 and all(info[ln]["wmode"] == 0 for ln in comp):
            rows.sort(key=lambda z: (float(z["bbox"][0]), float(z["bbox"][1]), z["line_no"], z["span_no"], z["char_no"], z["serial"]))
        else:
            rows.sort(key=lambda z: (z["line_no"], z["span_no"], z["char_no"], z["serial"]))
        out.extend(rows)
    return out


def build_pdf_line_index(pdf_path: Path):
    """Build a block-aware PDF reading index from PyMuPDF RAWDICT.

    v2.6 rebuilt horizontal text by baseline from *all* texttrace spans on a
    page.  On textbook layouts this could merge two unrelated callout boxes
    that merely share the same y-coordinate (P178 is the regression example).

    v2.7 preserves PDF text-block boundaries and line order.  It also resolves
    same-position decorative duplicates (e.g. white underlay + coloured text)
    by selecting the visible/top layer before building the block string.
    """
    overlay_rx = re.compile(r"^ZhuYinNR[0-9]*EG-", re.IGNORECASE)
    doc = fitz.open(pdf_path)
    out = {}
    for page_no, page in enumerate(doc, start=1):
        try:
            raw = page.get_text("rawdict", flags=(fitz.TEXTFLAGS_RAWDICT & ~fitz.TEXT_PRESERVE_IMAGES)) or {}
        except Exception:
            raw = {}

        candidates = []
        serial = 0
        for block_no, block in enumerate(raw.get("blocks", [])):
            if int(block.get("type", 0) or 0) != 0:
                continue
            for line_no, line in enumerate(block.get("lines", [])):
                for span_no, span in enumerate(line.get("spans", [])):
                    font = str(span.get("font") or "")
                    if overlay_rx.search(font):
                        continue
                    color_rank = _rawdict_color_rank(span.get("color"))
                    for char_no, ch in enumerate(span.get("chars", [])):
                        c = norm_char(ch.get("c") or "")
                        if not c or c.isspace() or any(ord(q) < 32 for q in c):
                            continue
                        bbox = ch.get("bbox") or ()
                        if len(bbox) < 4:
                            continue
                        x0, y0, x1, y1 = map(float, bbox[:4])
                        if y1 <= y0:
                            continue
                        serial += 1
                        key = (c, round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2))
                        candidates.append({
                            "key": key, "serial": serial,
                            "block_no": block_no, "line_no": line_no,
                            "span_no": span_no, "char_no": char_no,
                            "c": c, "bbox": (x0, y0, x1, y1),
                            "font": font, "color_rank": color_rank,
                            "wmode": int(line.get("wmode") or 0),
                            "line_bbox": tuple(map(float, line.get("bbox") or block.get("bbox") or (0,0,0,0))),
                        })

        best_by_key = {}
        for rec in candidates:
            rank = (rec["color_rank"], rec["serial"])
            old = best_by_key.get(rec["key"])
            if old is None or rank > old[0]:
                best_by_key[rec["key"]] = (rank, rec)
        chosen_ids = {id(v[1]) for v in best_by_key.values()}

        per_block = defaultdict(list)
        for rec in candidates:
            if id(rec) in chosen_ids:
                per_block[rec["block_no"]].append(rec)

        items = []
        for block_no, block_recs in per_block.items():
            if not block_recs:
                continue
            # Preserve RAWDICT line order and the original span/char order.
            block_recs.sort(key=lambda z: (z["line_no"], z["span_no"], z["char_no"], z["serial"]))

            # Some source fonts expose a narrow line-wrap marker as literal
            # ASCII "!".  It is visually absent and appears at the right edge
            # of wrapped lines (e.g. 「轉!」 + next line 「換的動作」), while
            # genuine exclamation marks occupy a normal CJK punctuation cell.
            # Drop only a line-final ! whose width is <45% of that line's
            # median visible glyph width.  This changes context reconstruction
            # only; observed Zhuyin glyph evidence is untouched.
            cleaned = []
            for _ln in sorted({z["line_no"] for z in block_recs}):
                _lr = [z for z in block_recs if z["line_no"] == _ln]
                if not _lr:
                    continue
                _last = _lr[-1]
                _widths = [
                    max(0.0, float(z["bbox"][2]) - float(z["bbox"][0]))
                    for z in _lr[:-1] if (float(z["bbox"][2]) - float(z["bbox"][0])) > 1.0
                ]
                _drop_last = False
                if _last.get("c") == "!" and _widths:
                    _sw = sorted(_widths)
                    _med = _sw[len(_sw)//2]
                    _lw = max(0.0, float(_last["bbox"][2]) - float(_last["bbox"][0]))
                    _drop_last = _lw < 0.45 * max(_med, 1e-6)
                cleaned.extend(_lr[:-1] if _drop_last else _lr)
            block_recs = cleaned
            if not block_recs:
                continue
            block_recs = _repair_same_baseline_fragment_order(block_recs)
            text = "".join(z["c"] for z in block_recs)
            bx0 = min(z["bbox"][0] for z in block_recs)
            by0 = min(z["bbox"][1] for z in block_recs)
            bx1 = max(z["bbox"][2] for z in block_recs)
            by1 = max(z["bbox"][3] for z in block_recs)
            line_meta = []
            for line_no in sorted({z["line_no"] for z in block_recs}):
                lr = [z for z in block_recs if z["line_no"] == line_no]
                if not lr:
                    continue
                line_meta.append({
                    "line_no": line_no,
                    "text": "".join(z["c"] for z in lr),
                    "bbox": lr[0]["line_bbox"],
                    "wmode": lr[0]["wmode"],
                })
            items.append({
                "text": text,
                "bbox": (bx0, by0, bx1, by1),
                "chars": [
                    {"c": z["c"], "bbox": z["bbox"], "font": z["font"], "line_no": z["line_no"], "wmode": z["wmode"]}
                    for z in block_recs
                ],
                "lines": line_meta,
                "block_no": block_no,
            })
        out[page_no] = items
    doc.close()
    return out


def _visible_font_for_record(rec: dict, line_index):
    """Return the font of the visible RAWDICT glyph nearest this decoded row."""
    try:
        page_no = int(rec.get("實體頁碼") or 0)
        tx0, ty0, tx1, ty1 = [float(rec.get(k)) for k in ("x0", "y0", "x1", "y1")]
    except Exception:
        return ""
    target = norm_char(rec.get("原文字層字元")) or norm_char(rec.get("字元"))
    tcx, tcy = (tx0 + tx1) / 2, (ty0 + ty1) / 2
    best = None
    best_d = 1e30
    for item in line_index.get(page_no, []):
        for ch in item.get("chars", []):
            if norm_char(ch.get("c")) != target:
                continue
            bb = ch.get("bbox") or ()
            if len(bb) < 4:
                continue
            cx = (float(bb[0]) + float(bb[2])) / 2
            cy = (float(bb[1]) + float(bb[3])) / 2
            d = (cx - tcx) ** 2 + 4 * (cy - tcy) ** 2
            if d < best_d:
                best_d = d
                best = ch
    if best is not None and best_d <= 9.0:
        return str(best.get("font") or "")
    return ""


def _dedupe_actual_rows(rows: list[dict], line_index):
    """Suppress hidden same-position duplicate glyph layers conservatively.

    If duplicate rows disagree, a row is removed only when the PDF RAWDICT
    visible glyph at that position clearly identifies one of the candidate
    fonts.  Otherwise all rows are retained for safety.
    """
    groups = defaultdict(list)
    for order, rec in enumerate(rows):
        try:
            key = (
                int(rec.get("實體頁碼") or 0), norm_char(rec.get("字元")),
                round(float(rec.get("x0") or 0), 1), round(float(rec.get("y0") or 0), 1),
                round(float(rec.get("x1") or 0), 1), round(float(rec.get("y1") or 0), 1),
            )
        except Exception:
            key = ("unique", order)
        groups[key].append((order, rec))

    keep_orders = set()
    suppressed_rows: list[dict] = []
    for _key, items in groups.items():
        if len(items) == 1:
            keep_orders.add(items[0][0])
            continue
        readings = {norm_bopomofo(x[1].get("實際注音")) for x in items}
        fonts = {str(x[1].get("font") or "") for x in items}
        if len(readings) <= 1 and len(fonts) <= 1:
            canonical_order, canonical = items[-1]
            keep_orders.add(canonical_order)
            for order, record in items[:-1]:
                suppressed_rows.append({
                    "record": record,
                    "canonical_occurrence_id": canonical.get("occurrence_id", ""),
                    "reason": "同座標、同字型、同讀值的重疊／隱藏非獨立技術層",
                })
            continue
        visible_font = _visible_font_for_record(items[0][1], line_index)
        matches = [x for x in items if visible_font and str(x[1].get("font") or "") == visible_font]
        if len(matches) == 1:
            canonical_order, canonical = matches[0]
            keep_orders.add(canonical_order)
            for order, record in items:
                if order == canonical_order:
                    continue
                suppressed_rows.append({
                    "record": record,
                    "canonical_occurrence_id": canonical.get("occurrence_id", ""),
                    "reason": f"同座標非獨立技術層；RAWDICT 可見字型為 {visible_font}",
                })
        else:
            # Ambiguous: do not hide evidence.
            keep_orders.update(x[0] for x in items)
    return [rec for i, rec in enumerate(rows) if i in keep_orders], suppressed_rows


def _bbox_overlap_ratio_y(a, b):
    """Vertical overlap divided by the shorter bbox height."""
    try:
        ay0, ay1 = float(a[1]), float(a[3])
        by0, by1 = float(b[1]), float(b[3])
    except Exception:
        return 0.0
    inter = max(0.0, min(ay1, by1) - max(ay0, by0))
    denom = max(1e-6, min(ay1 - ay0, by1 - by0))
    return inter / denom


def _try_fuse_microblock_target(items, source_item, source_char_index):
    """Fuse an isolated styled glyph back into a neighbouring RAWDICT line.

    Some textbook PDFs emit one coloured/styled character as its own RAWDICT
    block even though it visually sits inside a normal sentence.  The v3.5
    block-first locator could therefore attach the decoded row to a different
    occurrence of the same character in an enclosing block.  v3.6 accepts a
    fusion only when geometry independently proves that the micro-block glyph
    occupies an *internal one-character gap* of a longer horizontal line.

    This is intentionally exact/fail-closed: no OCR, language model, expected
    pronunciation, or PDF actual reading participates in the fusion decision.
    """
    source_chars = source_item.get("chars") or []
    if not (0 <= source_char_index < len(source_chars)):
        return None
    # Only genuinely tiny blocks are eligible.  Larger blocks retain their own
    # boundary even if another line happens to overlap spatially.
    if len(source_chars) > 3:
        return None
    target_ch = source_chars[source_char_index]
    if int(target_ch.get("wmode") or 0) != 0:
        return None
    tbb = target_ch.get("bbox") or ()
    if len(tbb) < 4:
        return None
    tx0, ty0, tx1, ty1 = map(float, tbb[:4])
    tw = max(1e-6, tx1 - tx0)
    tcx, tcy = (tx0 + tx1) / 2, (ty0 + ty1) / 2

    best = None
    for host in items:
        if host is source_item:
            continue
        host_chars = host.get("chars") or []
        if len(host_chars) < 4:
            continue
        line_nos = sorted({ch.get("line_no") for ch in host_chars if int(ch.get("wmode") or 0) == 0})
        for line_no in line_nos:
            indexed = [(i, ch) for i, ch in enumerate(host_chars)
                       if ch.get("line_no") == line_no and int(ch.get("wmode") or 0) == 0]
            if len(indexed) < 4:
                continue
            indexed.sort(key=lambda pair: ((float(pair[1]["bbox"][0]) + float(pair[1]["bbox"][2])) / 2, pair[0]))
            widths = [max(1e-6, float(ch["bbox"][2]) - float(ch["bbox"][0])) for _, ch in indexed]
            heights = [max(1e-6, float(ch["bbox"][3]) - float(ch["bbox"][1])) for _, ch in indexed]
            sw = sorted(widths); sh = sorted(heights)
            med_w = sw[len(sw)//2]
            med_h = sh[len(sh)//2]
            if not (0.45 * med_w <= tw <= 1.9 * med_w):
                continue
            # Strong baseline/height agreement with the host line.
            host_y0 = min(float(ch["bbox"][1]) for _, ch in indexed)
            host_y1 = max(float(ch["bbox"][3]) for _, ch in indexed)
            if _bbox_overlap_ratio_y((tx0, ty0, tx1, ty1), (0, host_y0, 1, host_y1)) < 0.72:
                continue
            host_cy = sum((float(ch["bbox"][1]) + float(ch["bbox"][3])) / 2 for _, ch in indexed) / len(indexed)
            if abs(tcy - host_cy) > 0.45 * med_h:
                continue

            centers = [((float(ch["bbox"][0]) + float(ch["bbox"][2])) / 2, i, ch) for i, ch in indexed]
            insert_k = next((k for k, (cx, _, _) in enumerate(centers) if cx > tcx), len(centers))
            if 0 < insert_k < len(centers):
                # Internal one-character gap.
                pcx, pi, prev = centers[insert_k - 1]
                ncx, ni, nxt = centers[insert_k]
                pbb = tuple(map(float, prev["bbox"][:4]))
                nbb = tuple(map(float, nxt["bbox"][:4]))
                if not (pcx < tcx < ncx):
                    continue
                left_gap = tx0 - pbb[2]
                right_gap = nbb[0] - tx1
                if abs(left_gap) > 0.60 * med_w or abs(right_gap) > 0.60 * med_w:
                    continue
                left_overlap = max(0.0, pbb[2] - tx0)
                right_overlap = max(0.0, tx1 - nbb[0])
                if left_overlap > 0.22 * tw or right_overlap > 0.22 * tw:
                    continue
                if (tcx - pcx) > 1.55 * med_w or (ncx - tcx) > 1.55 * med_w:
                    continue
                score = (
                    abs(tcy - host_cy) / max(med_h, 1e-6)
                    + abs(left_gap) / max(med_w, 1e-6)
                    + abs(right_gap) / max(med_w, 1e-6)
                )
                insert_before = ni
            elif insert_k == 0:
                # Extremely close left-edge continuation.  This is allowed only
                # for a micro-block target and only when it almost touches the
                # first glyph; it recovers styled first characters without
                # recreating the old same-baseline callout merge problem.
                ncx, ni, nxt = centers[0]
                nbb = tuple(map(float, nxt["bbox"][:4]))
                gap = nbb[0] - tx1
                overlap = max(0.0, tx1 - nbb[0])
                if abs(gap) > 0.28 * med_w or overlap > 0.22 * tw or (ncx - tcx) > 1.55 * med_w:
                    continue
                score = abs(tcy-host_cy)/max(med_h,1e-6) + abs(gap)/max(med_w,1e-6) + 0.15
                insert_before = ni
            else:
                pcx, pi, prev = centers[-1]
                pbb = tuple(map(float, prev["bbox"][:4]))
                gap = tx0 - pbb[2]
                overlap = max(0.0, pbb[2] - tx0)
                # Do not append after explicit strong punctuation.
                if norm_char(prev.get("c")) in set("。！？!?；;"):
                    continue
                if abs(gap) > 0.28 * med_w or overlap > 0.22 * tw or (tcx - pcx) > 1.55 * med_w:
                    continue
                score = abs(tcy-host_cy)/max(med_h,1e-6) + abs(gap)/max(med_w,1e-6) + 0.15
                insert_before = max(i for i, _ in indexed) + 1

            if best is None or score < best[0]:
                best = (score, host, line_no, insert_before)

    if best is None:
        return None
    _, host, line_no, insert_before_global = best
    fused_chars = [dict(ch) for ch in (host.get("chars") or [])]
    fused_target = dict(target_ch)
    fused_target["line_no"] = line_no
    fused_chars.insert(insert_before_global, fused_target)
    fused_text = "".join(norm_char(ch.get("c")) for ch in fused_chars)
    bx0 = min(float(ch["bbox"][0]) for ch in fused_chars)
    by0 = min(float(ch["bbox"][1]) for ch in fused_chars)
    bx1 = max(float(ch["bbox"][2]) for ch in fused_chars)
    by1 = max(float(ch["bbox"][3]) for ch in fused_chars)
    synthetic = {
        **host,
        "text": fused_text,
        "bbox": (bx0, by0, bx1, by1),
        "chars": fused_chars,
        "fused_microblock": True,
        "fused_source_block_no": source_item.get("block_no"),
    }
    return synthetic, insert_before_global



def _try_fuse_singleton_chain(items, source_item, source_char_index):
    """Reconstruct a clearly linear chain of one-glyph RAWDICT blocks.

    Rotated textbook labels sometimes store every visible character as a
    separate one-character block (blind-test example: 「每個寶物代表1分。」).
    This helper uses geometry only: same-sized singleton blocks must form one
    connected, nearly straight, evenly spaced chain.  It never consults OCR,
    dictionaries, expected readings, or the observed Zhuyin value.
    """
    schars = source_item.get("chars") or []
    if len(schars) != 1 or source_char_index != 0:
        return None
    sbb = schars[0].get("bbox") or ()
    if len(sbb) < 4 or int(schars[0].get("wmode") or 0) != 0:
        return None
    sx0, sy0, sx1, sy1 = map(float, sbb[:4])
    sw, sh = max(1e-6, sx1 - sx0), max(1e-6, sy1 - sy0)
    scx, scy = (sx0 + sx1) / 2, (sy0 + sy1) / 2
    scale = max(sw, sh)

    nodes = []
    for item in items:
        chs = item.get("chars") or []
        if len(chs) != 1 or int(chs[0].get("wmode") or 0) != 0:
            continue
        bb = chs[0].get("bbox") or ()
        if len(bb) < 4:
            continue
        x0, y0, x1, y1 = map(float, bb[:4])
        w, h = max(1e-6, x1 - x0), max(1e-6, y1 - y0)
        if not (0.55 <= w / sw <= 1.8 and 0.55 <= h / sh <= 1.8):
            continue
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        if ((cx - scx) ** 2 + (cy - scy) ** 2) ** 0.5 > 9.0 * scale:
            continue
        nodes.append({"item": item, "char": chs[0], "cx": cx, "cy": cy, "w": w, "h": h})
    if len(nodes) < 4:
        return None

    try:
        src_i = next(i for i, n in enumerate(nodes) if n["item"] is source_item)
    except StopIteration:
        return None

    # Geometry graph: adjacent glyph cells have centre distance about one glyph.
    adj = [[] for _ in nodes]
    for i in range(len(nodes)):
        for j in range(i + 1, len(nodes)):
            dx, dy = nodes[j]["cx"] - nodes[i]["cx"], nodes[j]["cy"] - nodes[i]["cy"]
            dist = (dx * dx + dy * dy) ** 0.5
            local_scale = 0.5 * (max(nodes[i]["w"], nodes[i]["h"]) + max(nodes[j]["w"], nodes[j]["h"]))
            if 0.55 * local_scale <= dist <= 1.65 * local_scale:
                adj[i].append(j); adj[j].append(i)

    stack=[src_i]; comp=set([src_i])
    while stack:
        i=stack.pop()
        for j in adj[i]:
            if j not in comp:
                comp.add(j); stack.append(j)
    if len(comp) < 4:
        return None
    comp_nodes=[nodes[i] for i in comp]

    # Principal direction from the farthest pair.  Reading direction is left to
    # right for horizontal/diagonal chains, top to bottom for near-vertical.
    far=None; far_d=-1.0
    for i in range(len(comp_nodes)):
        for j in range(i+1,len(comp_nodes)):
            dx=comp_nodes[j]["cx"]-comp_nodes[i]["cx"]; dy=comp_nodes[j]["cy"]-comp_nodes[i]["cy"]
            d=dx*dx+dy*dy
            if d>far_d:
                far_d=d; far=(dx,dy)
    if not far or far_d <= 0:
        return None
    dx,dy=far; mag=(dx*dx+dy*dy)**0.5; ux,uy=dx/mag,dy/mag
    if abs(ux) >= abs(uy):
        if ux < 0: ux,uy=-ux,-uy
    else:
        if uy < 0: ux,uy=-ux,-uy
    vx,vy=-uy,ux
    mx=sum(n["cx"] for n in comp_nodes)/len(comp_nodes)
    my=sum(n["cy"] for n in comp_nodes)/len(comp_nodes)
    decorated=[]
    for n in comp_nodes:
        rx,ry=n["cx"]-mx,n["cy"]-my
        t=rx*ux+ry*uy; perp=abs(rx*vx+ry*vy)
        decorated.append((t,perp,n))
    decorated.sort(key=lambda z:z[0])
    perps=[z[1] for z in decorated]
    median_scale=sorted(max(z[2]["w"],z[2]["h"]) for z in decorated)[len(decorated)//2]
    if max(perps) > 0.42 * median_scale:
        return None
    gaps=[decorated[i+1][0]-decorated[i][0] for i in range(len(decorated)-1)]
    if not gaps or min(gaps) <= 0:
        return None
    sg=sorted(gaps); med_gap=sg[len(sg)//2]
    if med_gap <= 0 or min(gaps) < 0.45*med_gap or max(gaps) > 1.85*med_gap:
        return None

    ordered=[z[2] for z in decorated]
    text="".join(norm_char(n["char"].get("c")) for n in ordered)
    if len(text) < 4:
        return None
    # Source target must remain exactly once at its geometric occurrence.
    src_positions=[i for i,n in enumerate(ordered) if n["item"] is source_item]
    if len(src_positions)!=1:
        return None
    src_pos=src_positions[0]
    bx0=min(float(n["char"]["bbox"][0]) for n in ordered)
    by0=min(float(n["char"]["bbox"][1]) for n in ordered)
    bx1=max(float(n["char"]["bbox"][2]) for n in ordered)
    by1=max(float(n["char"]["bbox"][3]) for n in ordered)
    synthetic={
        "text": text,
        "bbox": (bx0,by0,bx1,by1),
        "chars": [n["char"] for n in ordered],
        "lines": [],
        "block_no": source_item.get("block_no"),
        "fused_singleton_chain": True,
    }
    return synthetic, src_pos

def locate_line_context(rec, line_index):
    """Locate target by its nearest RAWDICT glyph, then return its sentence.

    v3.6 changes the ownership rule from "first enclosing block" to the nearest
    matching character bbox across all blocks.  This is crucial when a styled
    character is emitted as a separate micro-block inside a longer sentence.
    Such a micro-block is fused into a host line only when exact geometry proves
    it fills an internal one-character gap (see _try_fuse_microblock_target).

    Return tuple:
      sentence, local, prev2, next2, phrase_windows, sentence_pos, block_text
    """
    page_no = rec.get("實體頁碼")
    items = line_index.get(page_no, [])
    try:
        tx0, ty0, tx1, ty1 = [float(rec.get(k)) for k in ("x0", "y0", "x1", "y1")]
    except Exception:
        return "", "", "", "", "", None, ""
    tcx, tcy = (tx0 + tx1) / 2, (ty0 + ty1) / 2
    target = norm_char(rec.get("字元"))
    lookup_target = norm_char(rec.get("原文字層字元")) or target

    char_hits = []
    for item in items:
        for j, ch in enumerate(item.get("chars") or []):
            if norm_char(ch.get("c")) != lookup_target:
                continue
            bb = ch.get("bbox") or ()
            if len(bb) < 4:
                continue
            cx = (float(bb[0]) + float(bb[2])) / 2
            cy = (float(bb[1]) + float(bb[3])) / 2
            d = (cx - tcx) ** 2 + 4 * (cy - tcy) ** 2
            char_hits.append((d, item.get("block_no", 10**9), j, item))
    if not char_hits:
        return "", "", "", "", "", None, ""
    char_hits.sort(key=lambda z: (z[0], z[1], z[2]))
    best_d = char_hits[0][0]
    # Same-position duplicate layers sometimes differ by <1 pt because of font
    # bbox rounding.  Prefer the context-richer block among such near-ties.
    # This recovers cases like a one-char decorative 「背」 over the real
    # 「背帶」 text block without sacrificing coordinate ownership.
    near_ties = [z for z in char_hits if z[0] <= best_d + 1.0]
    _, _, base_char_i, best = max(near_ties, key=lambda z: (len(z[3].get("text") or ""), -z[0]))

    fused = _try_fuse_microblock_target(items, best, base_char_i)
    if fused is not None:
        best, base_char_i = fused
    if len(best.get("chars") or []) <= 2:
        chain = _try_fuse_singleton_chain(items, best, base_char_i)
        if chain is not None:
            best, base_char_i = chain

    block_text = best["text"]
    best_chars = best.get("chars") or []
    if not (0 <= base_char_i < len(best_chars)):
        return "", "", "", "", "", None, block_text
    # RAWDICT can expose one glyph as more than one Unicode code point (e.g.
    # a single record containing "1.").  Therefore the glyph-list index is not
    # a Python string offset.  Convert explicitly before slicing block_text.
    base_i = sum(len(norm_char(ch.get("c"))) for ch in best_chars[:base_char_i])
    glyph_text = norm_char(best_chars[base_char_i].get("c"))
    if not (0 <= base_i < len(block_text)):
        return "", "", "", "", "", None, block_text
    if lookup_target != target:
        block_text = block_text[:base_i] + target + block_text[base_i + max(1, len(glyph_text)):]

    sentence, sent_i = _sentence_window(block_text, base_i)
    if sent_i is None:
        sent_i = 0
    local = sentence[max(0, sent_i - 7): min(len(sentence), sent_i + 8)]
    prev2 = sentence[max(0, sent_i - 2):sent_i]
    next2 = sentence[sent_i + 1:min(len(sentence), sent_i + 3)]
    phrases = []
    for length in (2, 3, 4, 5):
        start_min = max(0, sent_i - length + 1)
        start_max = min(sent_i, len(sentence) - length)
        for start in range(start_min, start_max + 1):
            phrase = sentence[start:start + length]
            if target in phrase and phrase not in phrases:
                phrases.append(phrase)
    return sentence, local, prev2, next2, "｜".join(phrases), sent_i, block_text


def _allow_coordinate_fallback(line_text: str, line_pos: int | None, target: str) -> bool:
    """Conservative gate for the coordinate-sequence rescue context.

    The page-order sequence is not a lexical line and can concatenate unrelated
    blocks (v3.6 regression: 「駝背。」 + 「書包…」 accidentally formed
    「背書包」).  Allow rescue only when the real visual line is itself a tiny
    fragment, or when the target is the unpunctuated final character of the
    visual line and a phrase may genuinely continue in another block.
    """
    text = str(line_text or "").strip()
    if not text or line_pos is None:
        return False
    target = norm_char(target)
    if text == target or len(text) <= 3:
        return True
    if not (0 <= int(line_pos) < len(text)):
        return False
    pos = int(line_pos)
    stops = set("。！？!?；;：:")
    # Never bridge across an explicit sentence/phrase stop adjacent to target.
    if pos + 1 < len(text) and text[pos + 1] in stops:
        return False
    if pos > 0 and text[pos - 1] in stops:
        return False
    return pos == 0 or pos == len(text) - 1

def example_index_hints(d, line_text: str, target_char: str):
    """Return dictionary-example matches as navigation hints only.

    A hit is NOT treated as proof of the word boundary or expected reading.
    """
    if not line_text:
        return ""
    hints = []
    norm_line = normalize("NFKC", line_text)
    for reading, examples in d.get("examples_by_reading", {}).items():
        for ex in examples:
            if target_char in ex and ex in norm_line:
                token = f"{ex}→{reading}"
                if token not in hints:
                    hints.append(token)
    return "；".join(hints)




def resolve_dictionary_examples(d, target_char: str, line_text: str, target_pos: int | None):
    """Use explicit dictionary example-to-reading mappings as lexical evidence.

    Only examples that map to exactly one reading in the source dictionary are
    eligible. Known cross-word false hits are blocked. The actual PDF reading is
    never consulted here.
    """
    if target_pos is None or target_pos < 0 or not line_text:
        return None, []

    blocklist = {
        ("中", "中的"),  # can be 阿中+的 / 家中+的, not the lexical word 中的
    }

    phrase_readings = defaultdict(set)
    for reading, examples in d.get("examples_by_reading", {}).items():
        for ex in examples:
            phrase_readings[ex].add(reading)

    candidates = []
    for reading, examples in d.get("examples_by_reading", {}).items():
        for ex in examples:
            if not ex or target_char not in ex or (target_char, ex) in blocklist:
                continue
            # A phrase listed under more than one reading is semantically ambiguous.
            if len(phrase_readings.get(ex, ())) != 1:
                continue
            # Cross-word boundary guards discovered by blind test.  Dictionary
            # example matching is lexical evidence, not a tokenizer: a visible
            # substring may straddle two syntactic words and must not be promoted
            # to an exact lexical hit merely because the characters are adjacent.
            if target_pos is not None:
                tail4 = line_text[target_pos: target_pos + 4]
                prev2 = line_text[max(0, target_pos - 2): target_pos]
                after_ex = line_text[target_pos + len(ex): target_pos + len(ex) + 1]

                # 比肩 / 比鄰 can be comparative 比 + 肩(膀) / 鄰居.
                # The second pattern also covers layout-extracted text such as
                # 「臀部比肩高」 where 膀 is absent from the local text layer.
                if target_char == "比":
                    comparative_preds = set("高低大小快慢多少長短遠近重輕粗細寬窄強弱")
                    comparative_left = {
                        "臀部", "頭部", "肩部", "背部", "胸部", "腹部",
                        "腰部", "腿部", "手臂", "身高", "腳掌", "膝蓋",
                    }
                    if ex == "比肩" and (tail4.startswith("比肩膀") or (prev2 in comparative_left and after_ex in comparative_preds)):
                        continue
                    if ex == "比鄰" and tail4.startswith("比鄰居"):
                        continue

                # 左傳 is a book/title lexical item.  In 「往左傳球」 the
                # adjacency is direction 左 + verb 傳, so the title reading must
                # not cross the word boundary and override 傳球 ㄔㄨㄢˊ.
                if target_char == "傳" and ex == "左傳":
                    next_char = line_text[target_pos + 1: target_pos + 2]
                    if next_char in {"球", "給", "遞", "送", "出", "到", "回", "向", "接"}:
                        continue

                # 背書 is the lexical verb/noun with 背 ㄅㄟˋ, but in
                # 「背書包」 the visible substring crosses the boundary
                # 背 + 書包 and must keep the carrying verb reading ㄅㄟ.
                if target_char == "背" and ex == "背書":
                    if line_text[target_pos: target_pos + 3].startswith("背書包"):
                        continue
            if not target_in_phrase(line_text, target_pos, ex, target_char):
                continue
            candidates.append(RuleDecision(
                expected_reading=reading,
                rule_id=f"DICT-EX-{target_char}-{ex}",
                rule_mode="dictionary_example",
                matched_phrase=ex,
                source="一字多音-字典檔.xlsx",
                source_url="",
                note="字典例詞與讀音分組直接對應；只在同一行完整命中且該例詞在字典中只有一個讀音時使用；另套用跨詞界假命中防護。",
                priority=95,
                evidence_level="dictionary_example",
            ))
    if not candidates:
        return None, []
    max_len = max(len(c.matched_phrase) for c in candidates)
    top = [c for c in candidates if len(c.matched_phrase) == max_len]
    readings = {c.expected_reading for c in top}
    if len(readings) != 1:
        return None, top
    return top[0], top



def load_character_overrides(path: Path | None, pdf_path: Path | None = None):
    """Load coordinate-specific corrections for proven PDF text-layer substitutions.

    These corrections change only the extracted base character. They never
    manufacture or alter the observed Bopomofo reading.
    """
    if not path or not path.exists():
        return []
    pdf_stem = pdf_path.stem if pdf_path else ""
    out = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            needle = (row.get("pdf_contains") or "").strip()
            if needle and needle not in pdf_stem:
                continue
            try:
                row["physical_page"] = int(row.get("physical_page") or 0)
                row["x0"] = float(row.get("x0") or 0)
                row["y0"] = float(row.get("y0") or 0)
            except Exception:
                continue
            out.append(row)
    return out


def apply_character_override(rec: dict, overrides: list[dict], tol: float = 0.8):
    for ov in overrides:
        try:
            same = (
                int(rec.get("實體頁碼") or 0) == int(ov["physical_page"])
                and abs(float(rec.get("x0") or 0) - float(ov["x0"])) <= tol
                and abs(float(rec.get("y0") or 0) - float(ov["y0"])) <= tol
                and norm_char(rec.get("字元")) == norm_char(ov.get("original_char"))
            )
        except Exception:
            same = False
        if same:
            rec["原文字層字元"] = norm_char(rec.get("字元"))
            rec["字元"] = norm_char(ov.get("corrected_char"))
            rec["字元覆寫依據"] = f"{ov.get('source','')}｜{ov.get('note','')}"
            return ov
    return None



def load_context_overrides(path: Path | None, pdf_path: Path | None = None):
    """Load coordinate-specific visible-text context repairs.

    These rows repair only the Chinese context when the PDF text layer is
    incomplete (e.g. outlined/vector text). They never specify or alter the
    observed or expected pronunciation.
    """
    if not path or not path.exists():
        return []
    pdf_stem = pdf_path.stem if pdf_path else ""
    out=[]
    with path.open("r",encoding="utf-8-sig",newline="") as f:
        for row in csv.DictReader(f):
            needle=(row.get("pdf_contains") or "").strip()
            if needle and needle not in pdf_stem:
                continue
            try:
                row["physical_page"]=int(row.get("physical_page") or 0)
                row["x0"]=float(row.get("x0") or 0)
                row["y0"]=float(row.get("y0") or 0)
                row["target_index"]=int(row.get("target_index") or 0)
            except Exception:
                continue
            out.append(row)
    return out


def apply_context_override(rec: dict, overrides: list[dict], tol: float = 1.0):
    for ov in overrides:
        try:
            same=(
                int(rec.get("實體頁碼") or 0)==int(ov["physical_page"])
                and norm_char(rec.get("字元"))==norm_char(ov.get("target_char"))
                and abs(float(rec.get("x0") or 0)-float(ov["x0"]))<=tol
                and abs(float(rec.get("y0") or 0)-float(ov["y0"]))<=tol
            )
        except Exception:
            same=False
        if same:
            return ov
    return None

def _build_candidate_ledger(actual_source_rows, classifications):
    """Build one PDF candidate ledger without treating a truly empty PDF as corruption.

    Cutter/print-production PDFs can legitimately contain no detectable zhuyin
    occurrences. That is different from losing rows while building a non-empty
    source ledger: non-empty inputs still go through the strict ledger validator.
    """

    if not actual_source_rows:
        if classifications:
            raise ValueError("candidate classification 非空，但 actual source 為空；拒絕把資料遺失當成零 occurrence")
        return []
    ledger = build_occurrence_ledger(actual_source_rows, classifications)
    validate_occurrence_ledger(ledger)
    return ledger


def analyze(
    actual_xlsx: Path,
    dict_path: Path,
    rules_path: Path,
    out_xlsx: Path,
    pdf_path: Path | None = None,
    regressions_path: Path | None = None,
    char_overrides_path: Path | None = None,
    context_overrides_path: Path | None = None,
    dynamic_evidence_root: Path | None = None,
    *,
    runtime_root: Path | None = None,
):
    root = Path(runtime_root or Path(__file__).resolve().parent).resolve()
    runtime_dict = root / DEFAULT_DICT.name
    runtime_rules = root / DEFAULT_RULES.name
    runtime_regressions = root / DEFAULT_REGRESSIONS.name
    runtime_char_overrides = root / DEFAULT_CHAR_OVERRIDES.name
    runtime_context_overrides = root / DEFAULT_CONTEXT_OVERRIDES.name
    runtime_concise_dict = root / DEFAULT_CONCISE_DICT.name
    runtime_handbook_constraints = root / DEFAULT_HANDBOOK_CONSTRAINTS.name
    runtime_handbook_rules = root / DEFAULT_HANDBOOK_RULES.name
    runtime_mandatory_regressions = root / DEFAULT_MANDATORY_REGRESSIONS.name
    source_validation = validate_asset_manifest(root)
    if not source_validation.get("ok"):
        raise ValueError("PIPELINE_BLOCKED：核心資料來源驗證失敗：" + "；".join(source_validation.get("errors") or []))
    expected_fingerprint = compute_expected_asset_fingerprint(
        root,
        source_validation,
        resolver_version=VERSION,
        source_files=EXPECTED_RESOLVER_SOURCE_FILES,
    )
    expected_paths = {
        "dict": (Path(dict_path).resolve(), runtime_dict),
        "rules": (Path(rules_path).resolve(), runtime_rules),
        "regressions": (Path(regressions_path or runtime_regressions).resolve(), runtime_regressions),
        "char_overrides": (Path(char_overrides_path or runtime_char_overrides).resolve(), runtime_char_overrides),
        "context_overrides": (Path(context_overrides_path or runtime_context_overrides).resolve(), runtime_context_overrides),
    }
    unmanifested = [name for name, (actual_path, required_path) in expected_paths.items() if actual_path != required_path]
    if unmanifested:
        raise ValueError(f"PIPELINE_BLOCKED：v5.2.2 不接受未列入 runtime manifest 的 expected 資產：{unmanifested}")
    if not pdf_path or not Path(pdf_path).exists():
        raise ValueError("PIPELINE_BLOCKED：候選分析需要現版 PDF 與 SHA-256，不得只憑舊 workbook")
    dictionary = load_dictionary(dict_path)
    concise_dictionary = load_concise_dictionary(runtime_concise_dict)
    handbook_constraints = load_handbook_constraints(runtime_handbook_constraints)
    dictionary = apply_handbook_constraints(dictionary, handbook_constraints)
    handbook_rules = load_rules(runtime_handbook_rules, pdf_path.stem if pdf_path else "") if runtime_handbook_rules.exists() else []
    # A handbook exact/context rule must still be checked even when the lower
    # project dictionary has no row for its target character. Create a neutral
    # placeholder only to enter the comparison pipeline; the handbook rule,
    # not this placeholder, supplies the expected reading.
    for hr in handbook_rules:
        dictionary.setdefault(hr.target_char, {
            "readings": [], "raw_readings": "", "examples": "", "tags": "",
            "examples_by_reading": {},
        })
    rules = load_rules(rules_path, pdf_path.stem if pdf_path else "")
    line_index = build_pdf_line_index(pdf_path) if pdf_path and pdf_path.exists() else {}
    char_overrides = load_character_overrides(char_overrides_path, pdf_path)
    context_overrides_path = context_overrides_path or runtime_context_overrides
    context_overrides = load_context_overrides(context_overrides_path, pdf_path)

    wb_in = load_workbook(actual_xlsx, data_only=True, read_only=True)

    def actual_workbook_error(message: str):
        wb_in.close()
        raise ValueError(message)

    required_actual_sheets = {"實際注音", "結構偵測排除", "v5.2中繼資料", "摘要"}
    missing_actual_sheets = sorted(required_actual_sheets - set(wb_in.sheetnames))
    if missing_actual_sheets:
        actual_workbook_error(f"actual workbook schema 不相容，缺少工作表：{missing_actual_sheets}")
    ws_runtime = wb_in["v5.2中繼資料"]
    runtime_metadata = {}
    for row_number, row in enumerate(ws_runtime.iter_rows(values_only=True), 1):
        if not row or row[0] in (None, ""):
            continue
        key = str(row[0])
        if key in runtime_metadata:
            actual_workbook_error(f"actual workbook metadata key 重複：{key} (row {row_number})")
        runtime_metadata[key] = row[1] if len(row) > 1 else None
    if not schema_compatible(runtime_metadata.get("workbook_schema_version"), WORKBOOK_SCHEMA_VERSION):
        actual_workbook_error("actual workbook schema family 不相容")
    pdf_sha256 = str(runtime_metadata.get("pdf_sha256") or "").strip()
    if not pdf_sha256:
        actual_workbook_error("actual workbook 缺少 PDF SHA-256")
    if pdf_path and pdf_path.exists():
        hasher = hashlib.sha256()
        with pdf_path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                hasher.update(chunk)
        digest = hasher.hexdigest()
        if digest != pdf_sha256:
            actual_workbook_error("actual workbook 的 PDF SHA-256 與現版 PDF 不一致")
        fingerprint = compute_actual_asset_fingerprint(
            root,
            pdf_path,
            source_validation,
            decoder_version=DECODER_VERSION,
            source_files=ACTUAL_DECODER_SOURCE_FILES,
            dynamic_dependencies=actual_workbook_dynamic_dependencies(actual_xlsx),
            dynamic_evidence_root=dynamic_evidence_root,
        )
        if not fingerprint_compatible(
            runtime_metadata.get("actual_asset_fingerprint"),
            runtime_metadata.get("actual_asset_fingerprint_components"),
            fingerprint,
            chain="actual",
        ):
            actual_workbook_error("actual workbook PDF／evidence assets 已變更，必須重新 decode")

    ws_in = wb_in["實際注音"]
    headers = [c.value for c in next(ws_in.iter_rows(min_row=1, max_row=1))]
    duplicate_actual_headers = [header for header, count in Counter(headers).items() if header and count > 1]
    if duplicate_actual_headers or any(header in (None, "") for header in headers):
        actual_workbook_error(f"actual workbook『實際注音』header 無效／重複：{duplicate_actual_headers}")
    idx = {h: i for i, h in enumerate(headers)}

    required_wanted = [
        "實體頁碼", "課本頁", "頁面標籤", "字元", "實際注音", "解碼狀態", "解碼依據",
        "穩定注音鍵", "群組注音鍵", "font", "font_xref", "注音元件ID",
        "glyph_id_字形索引", "x0", "y0", "x1", "y1",
        "occurrence_id", "review_id", "identity_confidence", "identity_row_fallback",
        "identity_collision_base", "source_row_number", "ledger_schema_version", "workbook_schema_version",
    ]
    optional_actual_evidence = [
        "字形架構", "TTF字形SHA256", "CFF樣式群組", "CFF符號簽名",
        "CFF聲調簽名", "CFF輕聲簽名", "CFF完整注音簽名", "CFF整字字形SHA256",
    ]
    wanted = required_wanted + [name for name in optional_actual_evidence if name in idx]
    missing_actual_columns = [name for name in required_wanted if name not in idx]
    if missing_actual_columns:
        actual_workbook_error(f"actual workbook『實際注音』缺少必要欄位：{missing_actual_columns}")

    rows = []
    pages = defaultdict(list)
    for raw in ws_in.iter_rows(min_row=2, values_only=True):
        rec = {h: raw[idx[h]] if h in idx else None for h in wanted}
        if not schema_compatible(rec.get("ledger_schema_version"), LEDGER_SCHEMA_VERSION) or not schema_compatible(rec.get("workbook_schema_version"), WORKBOOK_SCHEMA_VERSION):
            actual_workbook_error("actual occurrence row schema 不相容")
        rec.update({"pdf_sha256": pdf_sha256, "pdf": str(pdf_path or ""), "pdf_name": pdf_path.name if pdf_path else ""})
        rec["原文字層字元"] = ""
        rec["字元覆寫依據"] = ""
        apply_character_override(rec, char_overrides)
        rec["字元"] = norm_char(rec["字元"])
        # 課本頁 is the printed page number from the page footer.  Keep
        # 實體頁碼 separately for PDF-coordinate/debug work.
        if rec.get("課本頁") in (None, ""):
            rec["課本頁"] = rec.get("頁面標籤") or ""
        rec["實際注音_正規化"] = norm_bopomofo(rec["實際注音"])
        rows.append(rec)

    # Remove only proven hidden duplicate text layers (e.g. P132 white underlay).
    # This occurs after RAWDICT block indexing so the visible font is available.
    all_actual_rows = list(rows)
    ws_excluded = wb_in["結構偵測排除"]
    excluded_headers = [c.value for c in next(ws_excluded.iter_rows(min_row=1, max_row=1))]
    duplicate_excluded_headers = [header for header, count in Counter(excluded_headers).items() if header and count > 1]
    if duplicate_excluded_headers or any(header in (None, "") for header in excluded_headers):
        actual_workbook_error(f"actual workbook『結構偵測排除』header 無效／重複：{duplicate_excluded_headers}")
    excluded_idx = {h: i for i, h in enumerate(excluded_headers)}
    required_excluded_columns = [
        "實體頁碼", "課本頁", "字元", "font", "font_xref", "glyph_id", "候選注音元件ID",
        "x0", "y0", "來源", "排除理由", "occurrence_id", "review_id", "identity_confidence",
        "identity_row_fallback", "source_row_number", "ledger_schema_version", "workbook_schema_version",
    ]
    missing_excluded_columns = [name for name in required_excluded_columns if name not in excluded_idx]
    if missing_excluded_columns:
        actual_workbook_error(f"actual workbook『結構偵測排除』缺少必要欄位：{missing_excluded_columns}")
    structural_exclusion_rows: list[dict] = []
    for raw in ws_excluded.iter_rows(min_row=2, values_only=True):
        rec = {h: raw[excluded_idx[h]] if h in excluded_idx else None for h in required_excluded_columns}
        if not schema_compatible(rec.get("ledger_schema_version"), LEDGER_SCHEMA_VERSION) or not schema_compatible(rec.get("workbook_schema_version"), WORKBOOK_SCHEMA_VERSION):
            actual_workbook_error("actual structural exclusion row schema 不相容")
        rec.update({
            "pdf_sha256": pdf_sha256,
            "pdf": str(pdf_path or ""),
            "pdf_name": pdf_path.name if pdf_path else "",
            "注音元件ID": rec.get("候選注音元件ID"),
            "glyph_id_字形索引": rec.get("glyph_id"),
            "實際注音": "",
            "解碼依據": "結構排除來源",
        })
        structural_exclusion_rows.append(rec)
    wb_in.close()
    rows, suppressed_duplicate_rows = _dedupe_actual_rows(rows, line_index)
    pages = defaultdict(list)
    for rec in rows:
        pages[rec["實體頁碼"]].append(rec)

    page_positions = {}
    for pg, pgrows in pages.items():
        for j, rec in enumerate(pgrows):
            page_positions[id(rec)] = j

    stats = Counter()
    stats["suppressed_duplicate_layers"] = len(suppressed_duplicate_rows)
    single_mismatches, single_agrees = [], []
    lexical_mismatches, lexical_agrees = [], []
    weak_lexical_mismatches = []
    direct_confirmed_errors = []
    multi_unresolved, actual_missing, rule_conflicts, scope_skipped, expected_uncovered = [], [], [], [], []
    stats["visible_annotations"] = len(rows)
    rule_hit_counter = Counter()

    def decide_expected(ch: str, line_text: str, target_pos: int | None, d=None):
        d = d or dictionary.get(ch) or {}

        company_pos = resolve_company_position_rule(line_text, target_pos)
        if company_pos and line_text and target_pos is not None and 0 <= target_pos < len(line_text) and line_text[target_pos] == ch:
            return company_pos, []
        semantic_word = resolve_semantic_complete_word_rule(line_text, target_pos)
        if semantic_word and line_text and target_pos is not None and 0 <= target_pos < len(line_text) and line_text[target_pos] == ch:
            return semantic_word, []

        # 統一用字手冊 is the project-level highest-priority source. Resolve
        # occurrence-position rules first, then its exact/context rules, before
        # every lower dictionary, heuristic or project rule.  A conflict inside
        # the handbook itself is preserved as an abstention rather than letting
        # a lower rule choose for it.
        hb_pos = resolve_handbook_position_rule(line_text, target_pos)
        if hb_pos and line_text and target_pos is not None and 0 <= target_pos < len(line_text) and line_text[target_pos] == ch:
            return hb_pos, []
        hb_decision, hb_conflicts = resolve_rules(handbook_rules, ch, line_text, target_pos)
        if hb_conflicts and hb_decision is None:
            return None, hb_conflicts
        if hb_decision:
            return hb_decision, []

        # Resolve the next character independently.  This helper never sees
        # the current PDF actual reading and therefore is safe for 一/不 sandhi.
        def resolve_next(next_ch: str, next_pos: int):
            nd = dictionary.get(next_ch) or {}
            company2 = resolve_company_position_rule(line_text, next_pos)
            if company2 and line_text and 0 <= next_pos < len(line_text) and line_text[next_pos] == next_ch:
                return company2
            semantic2 = resolve_semantic_complete_word_rule(line_text, next_pos)
            if semantic2 and line_text and 0 <= next_pos < len(line_text) and line_text[next_pos] == next_ch:
                return semantic2
            hbpos2 = resolve_handbook_position_rule(line_text, next_pos)
            if hbpos2 and line_text and 0 <= next_pos < len(line_text) and line_text[next_pos] == next_ch:
                return hbpos2
            hb2, hbc2 = resolve_rules(handbook_rules, next_ch, line_text, next_pos)
            if hbc2 and hb2 is None:
                return None
            if hb2:
                return hb2
            d2, c2 = resolve_rules(rules, next_ch, line_text, next_pos)
            if c2 and d2 is None:
                return None
            if d2:
                allowed2 = set(nd.get("handbook_allowed_readings") or [])
                if allowed2 and not set(reading_options(d2.expected_reading)).issubset(allowed2):
                    return None
            if d2 and d2.rule_mode in {"exact", "skip"}:
                return d2
            held_default = d2 if d2 else None
            rep2 = resolve_reduplication(line_text, next_pos)
            if rep2:
                return rep2
            if next_ch == "呀":
                ya2 = resolve_ya_particle(line_text, next_pos)
                if ya2:
                    return ya2
            if next_ch == "分":
                fen2 = resolve_fen_numeric_unit(line_text, next_pos)
                if fen2:
                    return fen2

            # 2.5: authoritative complete-word evidence precedes project
            # example matching and all single-character fallbacks.
            cw2, cc2 = resolve_concise_word(concise_dictionary, next_ch, line_text, next_pos)
            allowed2 = set(nd.get("handbook_allowed_readings") or [])
            if cw2 and (not allowed2 or set(reading_options(cw2.expected_reading)).issubset(allowed2)):
                return cw2
            if cc2:
                kept = [c for c in cc2 if not allowed2 or set(reading_options(c.expected_reading)).issubset(allowed2)]
                if len({c.expected_reading for c in kept}) == 1 and kept:
                    return kept[0]
                if kept:
                    return None

            de2, ce2 = resolve_dictionary_examples(nd, next_ch, line_text, next_pos)
            if de2:
                return de2
            if ce2:
                return None
            cs2 = resolve_concise_single(concise_dictionary, next_ch)
            if cs2 and (not allowed2 or set(reading_options(cs2.expected_reading)).issubset(allowed2)):
                return cs2
            if len(nd.get("readings", [])) == 1:
                return RuleDecision(
                    expected_reading=nd["readings"][0],
                    rule_id=f"DICT-SINGLE-{next_ch}",
                    rule_mode="dictionary_single",
                    matched_phrase=next_ch,
                    source="一字多音-字典檔.xlsx",
                    source_url="",
                    note="後字在專案字典中只有一個有效讀音，可供變調判斷。",
                    priority=90,
                )
            return held_default

        # 「一」的序數／連續數字本調是明確的外層語境，先擋住
        # 「第一名」被較短「一名」規則吃掉；其餘一／不先讓專案
        # exact 規則表示固定詞、數字標號與既有可接受集合。
        if ch == "一":
            yi_base = resolve_yi_base_context(line_text, target_pos)
            if yi_base:
                return yi_base, []

        # Exact/skip lexical evidence outranks all general/special heuristics.
        # A broad default is held as a fallback so sandhi/reduplication, MOE
        # complete-word evidence and dictionary example evidence can override it safely.
        decision, conflicts = resolve_rules(rules, ch, line_text, target_pos)
        if conflicts and decision is None:
            return None, conflicts
        default_decision = None
        if decision:
            # A lower-priority rule is never allowed to introduce a reading
            # that the handbook explicitly excludes for this character. It is
            # discarded rather than treated as an equal-priority conflict. If
            # the handbook leaves only one possible reading, that reading wins
            # immediately; otherwise later safe context rules may still choose
            # among the handbook-allowed readings.
            allowed = set(d.get("handbook_allowed_readings") or [])
            if allowed and not set(reading_options(decision.expected_reading)).issubset(allowed):
                if len(allowed) == 1:
                    return handbook_constraint_decision(ch, d), []
                decision = None
            if decision and decision.rule_mode in {"exact", "skip"}:
                return decision, []
            default_decision = decision

        # Current user-confirmed 一 sandhi outranks dictionaries, but only after
        # unambiguous project exact exceptions such as 統一／步驟一／一至二
        # have had a chance to resolve.  If the following expected is unknown,
        # abstain rather than falling through to a single-character default.
        if ch == "一":
            yi = resolve_yi(line_text, target_pos, resolve_next)
            if yi:
                return yi, []
            return None, []

        # Project policy for 不 is deliberately conservative: before a fourth
        # tone both ㄅㄨˊ and textbook-retained ㄅㄨˋ are accepted; otherwise
        # ㄅㄨˋ.  Exact project phrases above may narrow this set.
        if ch == "不":
            bu = resolve_bu(line_text, target_pos, resolve_next)
            if bu:
                return bu, []
            return None, []

        # Repeated kinship words must be located by exact glyph position.
        rep = resolve_reduplication(line_text, target_pos)
        if rep:
            return rep, []
        # Backward-compatible special handler kept for existing 媽媽 truth.
        if ch == "媽":
            mama = resolve_mama(line_text, target_pos)
            if mama:
                return mama, []

        # 個 used as a classifier must be resolved before substring example
        # matches such as 每個人 accidentally hitting the lexical item 個人.
        if ch == "個":
            ge = resolve_ge_classifier(line_text, target_pos)
            if ge:
                return ge, []
        if ch == "呀":
            ya = resolve_ya_particle(line_text, target_pos)
            if ya:
                return ya, []
        if ch == "分":
            fen = resolve_fen_numeric_unit(line_text, target_pos)
            if fen:
                return fen, []

        # 2.5: complete MOE entry before project dictionary examples and single
        # character fallbacks. Boundary-ambiguous entries abstain.
        blocked_phrases = set()
        for rr in rules:
            if rr.target_char != ch or not rr.phrase or not rr.excluded_phrases:
                continue
            if any(target_in_phrase(line_text, target_pos, ex, ch) for ex in rr.excluded_phrases):
                blocked_phrases.add(rr.phrase)
        concise_word, concise_conflicts = resolve_concise_word(
            concise_dictionary, ch, line_text, target_pos, blocked_phrases=blocked_phrases
        )
        allowed = set(d.get("handbook_allowed_readings") or [])
        if allowed and concise_conflicts:
            concise_conflicts = [
                c for c in concise_conflicts
                if set(reading_options(c.expected_reading)).issubset(allowed)
            ]
            if concise_word and not set(reading_options(concise_word.expected_reading)).issubset(allowed):
                concise_word = None
            if concise_word is None and concise_conflicts:
                cr = {c.expected_reading for c in concise_conflicts}
                if len(cr) == 1:
                    concise_word = concise_conflicts[0]
        if concise_conflicts and concise_word is None:
            return None, concise_conflicts
        if concise_word:
            return concise_word, []

        ddec, dconf = resolve_dictionary_examples(d, ch, line_text, target_pos)
        if dconf and ddec is None:
            return None, dconf
        if ddec:
            return ddec, []

        concise_single = resolve_concise_single(concise_dictionary, ch)
        if concise_single and (not allowed or set(reading_options(concise_single.expected_reading)).issubset(allowed)):
            return concise_single, []

        if default_decision:
            # A bare isolated glyph/line has no lexical evidence.  Broad default
            # rules for polyphonic characters (e.g. an extracted standalone 重)
            # must not manufacture a strong expected reading from missing layout
            # context.  Leave it unresolved instead of creating a false alarm.
            if target_pos is not None and line_text and line_text.strip() == ch:
                return None, []
            return default_decision, []
        return None, []

    for rec in rows:
        ch = rec["字元"]
        source_d = dictionary.get(ch)
        d = source_d or {
            "readings": [], "raw_readings": "", "examples": "", "tags": "",
            "examples_by_reading": {},
        }
        if source_d:
            stats["dict_occurrences"] += 1
        actual = rec["實際注音_正規化"]
        pgrows = pages[rec["實體頁碼"]]
        pos = page_positions[id(rec)]
        fallback_context, fallback_pos = build_fallback_context_with_pos(pgrows, pos)
        line_text, local_context, prev2, next2, phrase_windows, line_pos, block_text = locate_line_context(rec, line_index)
        context_override = apply_context_override(rec, context_overrides)
        context_override_note = ""
        if context_override:
            line_text = normalize("NFKC", str(context_override.get("context_text") or "")).strip()
            line_pos = int(context_override.get("target_index") or 0)
            if not (0 <= line_pos < len(line_text)) or line_text[line_pos] != ch:
                # Invalid override is ignored rather than guessing a position.
                context_override = None
            else:
                local_context = line_text[max(0, line_pos - 7): min(len(line_text), line_pos + 8)]
                prev2 = line_text[max(0, line_pos - 2):line_pos]
                next2 = line_text[line_pos + 1:min(len(line_text), line_pos + 3)]
                phrases=[]
                for length in (2,3,4,5):
                    start_min=max(0,line_pos-length+1); start_max=min(line_pos,len(line_text)-length)
                    for start in range(start_min,start_max+1):
                        phrase=line_text[start:start+length]
                        if ch in phrase and phrase not in phrases:
                            phrases.append(phrase)
                phrase_windows="｜".join(phrases)
                block_text=line_text
                context_override_note=f"{context_override.get('source','')}｜{context_override.get('note','')}"
        hints = example_index_hints(d, line_text, ch)
        # The coordinate-sequence fallback is kept internally for conservative
        # rescue rules, but it can inherit the PDF object's non-visual glyph
        # order.  For reports/GPT handoff, prefer the geometry-repaired visual
        # block whenever the reconstructed line is absent from that fallback.
        display_context = fallback_context
        if line_text and line_text not in fallback_context:
            display_context = block_text or line_text
        common = {
            **rec,
            "上下文": display_context,
            "句境覆寫依據": context_override_note,
            "所在行": line_text,
            "版面區塊": block_text,
            "行內字元位置": line_pos if line_pos is not None else "",
            "局部詞境": local_context,
            "前2字": prev2,
            "後2字": next2,
            "候選短語": phrase_windows,
            "字典例詞索引提示": hints,
            "字典讀音原文": d["raw_readings"],
            "字典有效讀音": "；".join(d["readings"]),
            "字典例詞": d["examples"],
            "字典備註": d["tags"],
        }

        # v5.5.0 architectural rule: resolve the expected lane for every
        # occurrence regardless of whether actual decoding succeeded.  Actual
        # is not consulted by decide_expected(); only after both lanes are ready
        # do we perform the mechanical comparison.
        lexical_decision, conflicts = decide_expected(ch, line_text, line_pos, d)

        # v1.8: scope exclusions are textual/project-policy decisions and do not
        # require a decoded actual.  They may therefore terminate the occurrence
        # before either comparison lane is complete.
        if lexical_decision and lexical_decision.rule_mode == "skip":
            stats["scope_skipped"] += 1
            scope_skipped.append({
                **common,
                "處理狀態": "專名／特殊項目：保留實際注音，不自動判定應讀音",
                "略過規則ID": lexical_decision.rule_id,
                "略過範圍": lexical_decision.matched_phrase,
                "略過依據": lexical_decision.source,
                "略過備註": lexical_decision.note,
                "expected_status": "EXCLUDED",
            })
            continue

        # Layout rescue is expected-only logic and therefore also runs when
        # actual is unavailable.
        if (
            (lexical_decision is None or lexical_decision.rule_mode == "default")
            and not conflicts
            and fallback_context
            and _allow_coordinate_fallback(line_text, line_pos, ch)
        ):
            fb_full, fb_full_conflicts = decide_expected(ch, fallback_context, fallback_pos, d)
            if (
                fb_full
                and fb_full.priority >= 95
                and (
                    fb_full.rule_mode == "exact"
                    or (bool(line_text) and fb_full.rule_mode in {"special_repeat", "special_yi", "special_bu", "special_ge", "dictionary_example", "concise_word", "concise_single"})
                )
            ):
                lexical_decision = fb_full
            elif not fb_full_conflicts:
                fb_decision, fb_conflicts = resolve_rules(rules, ch, fallback_context, fallback_pos)
                if fb_decision and fb_decision.rule_mode == "exact" and fb_decision.priority >= 130:
                    lexical_decision = fb_decision

        expected_status = "UNRESOLVED"
        expected_result = None
        resolution_kind = ""
        weak_resolution = False

        if conflicts:
            expected_status = "CONFLICT"
            stats["rule_conflict"] += 1
            readings = "；".join(sorted({c.expected_reading for c in conflicts}))
            expected_result = {
                **common,
                "規則候選讀音": readings,
                "規則ID": "；".join(c.rule_id for c in conflicts),
                "處理狀態": "規則衝突，禁止自動判讀",
                "expected_status": "CONFLICT",
            }
        elif len(d["readings"]) == 1:
            dictionary_expected = d["readings"][0]
            evidence = lexical_decision
            evidence_overrides_single = bool(
                evidence
                and evidence.rule_mode in {"exact", "company_position", "handbook_position", "semantic_word", "special_repeat", "special_yi", "special_bu", "special_ge", "special_ya", "special_fen", "dictionary_example", "concise_word", "concise_single"}
                and evidence.priority >= 120
            )
            expected = evidence.expected_reading if evidence_overrides_single else dictionary_expected
            stats["single_evaluable"] += 1
            expected_result = {
                **common,
                "預期注音": expected,
                "預期注音依據": (
                    "統一用字手冊完整詞語規則（最高優先）"
                    if evidence_overrides_single and evidence.evidence_level == "handbook_highest"
                    else "教育部《國語辭典簡編本》完整詞條"
                    if evidence_overrides_single and evidence.rule_mode == "concise_word"
                    else "教育部《國語辭典簡編本》單字主音唯一"
                    if evidence_overrides_single and evidence.rule_mode == "concise_single"
                    else "高信心完整詞語規則（優先於孤立單字讀音）"
                    if evidence_overrides_single
                    else "統一用字手冊：字音限制（最高優先）"
                    if d.get("handbook_allowed_readings")
                    else "專案一字多音字典：此字僅保留一個有效審定讀音"
                ),
                "完整詞語規則": evidence.matched_phrase if evidence else "",
                "規則ID": evidence.rule_id if evidence else "",
                "規則來源": evidence.source if evidence else (d.get("handbook_source") or ""),
                "來源網址": evidence.source_url if evidence else "",
                "規則備註": evidence.note if evidence else ("｜".join(x for x in [d.get("handbook_section", ""), d.get("handbook_note", "")] if x)),
                "證據強度": evidence.evidence_level if evidence else ("handbook_highest" if d.get("handbook_allowed_readings") else "dictionary_single"),
                "expected_status": "RESOLVED",
            }
            if evidence and not reading_matches(dictionary_expected, evidence.expected_reading) and not evidence_overrides_single:
                expected_status = "CONFLICT"
                stats["rule_conflict"] += 1
                expected_result.update({
                    "規則候選讀音": evidence.expected_reading,
                    "處理狀態": "單音字典與完整詞語規則衝突，禁止自動判讀",
                    "expected_status": "CONFLICT",
                })
            else:
                expected_status = "RESOLVED"
                resolution_kind = "single"
        elif lexical_decision:
            expected_status = "RESOLVED"
            resolution_kind = "lexical"
            weak_resolution = lexical_decision.rule_mode == "derived" or lexical_decision.evidence_level == "derived_lexical"
            stats["lexical_evaluable"] += 1
            rule_hit_counter[lexical_decision.rule_id] += 1
            expected_result = {
                **common,
                "預期注音": lexical_decision.expected_reading,
                "預期注音依據": (
                    "教育部《國語辭典簡編本》完整詞條"
                    if lexical_decision.rule_mode == "concise_word"
                    else "教育部《國語辭典簡編本》單字主音唯一"
                    if lexical_decision.rule_mode == "concise_single"
                    else "完整詞語／保守預設規則"
                ),
                "完整詞語規則": lexical_decision.matched_phrase,
                "規則ID": lexical_decision.rule_id,
                "規則模式": lexical_decision.rule_mode,
                "規則來源": lexical_decision.source,
                "來源網址": lexical_decision.source_url,
                "規則備註": lexical_decision.note,
                "證據強度": lexical_decision.evidence_level or ("derived_lexical" if lexical_decision.rule_mode == "derived" else "legacy"),
                "expected_status": "RESOLVED",
            }
        elif d.get("handbook_allowed_readings"):
            # The handbook allowed set is itself expected evidence.  v5.5.0
            # materializes that set without looking at actual; only the later
            # mechanical membership comparison may consult actual.  Earlier
            # versions resolved this branch only when actual happened to fall
            # outside the set, which coupled the expected lane to observation.
            allowed = list(d.get("handbook_allowed_readings") or [])
            hb = handbook_constraint_decision(ch, d)
            expected_status = "RESOLVED"
            resolution_kind = "handbook_allowed_set"
            stats["handbook_constraint_evaluable"] += 1
            expected_result = {
                **common,
                "預期注音": "；".join(allowed),
                "預期注音依據": "統一用字手冊：字音限制（允許讀音集合）",
                "完整詞語規則": hb.matched_phrase if hb else ch,
                "規則ID": hb.rule_id if hb else f"HANDBOOK-ALLOWED-{ch}",
                "規則模式": "handbook_constraint",
                "規則來源": hb.source if hb else (d.get("handbook_source") or ""),
                "來源網址": "",
                "規則備註": hb.note if hb else "",
                "證據強度": "handbook_highest",
                "expected_status": "RESOLVED",
            }
        elif not d.get("readings"):
            expected_status = "UNRESOLVED"
            stats["expected_uncovered"] += 1
            expected_result = {
                **common,
                "處理狀態": "現版未建立可獨立重現的預期注音來源，不得視為通過",
                "覆蓋缺口": "統一用字手冊／專案規則／教育部簡編本完整詞條與唯一主音／專案字典均未形成唯一預期音",
                "expected_status": "UNRESOLVED",
            }
        else:
            expected_status = "AMBIGUOUS"
            stats["multi_or_special"] += 1
            expected_result = {
                **common,
                "處理狀態": "多音／特殊規則：目前沒有足夠的完整詞語規則，保留待判讀",
                "例詞提示限制": "例詞命中只供定位；未建立明確規則者不可直接產生預期注音。",
                "expected_status": "AMBIGUOUS",
            }

        # No expected rule below this point may branch on actual.  The
        # handbook allowed-set, including multi-reading sets, was already
        # materialized above as an independent expected set.

        if not actual:
            stats["actual_missing"] += 1
            missing = {
                **common,
                "處理狀態": "現有注音未解碼；expected 已獨立照常解析",
                "expected_status": expected_status,
            }
            if expected_result:
                for key in (
                    "預期注音", "預期注音依據", "完整詞語規則", "規則ID", "規則模式",
                    "規則來源", "來源網址", "規則備註", "證據強度", "規則候選讀音",
                    "覆蓋缺口", "例詞提示限制",
                ):
                    if key in expected_result:
                        missing[key] = expected_result.get(key)
            actual_missing.append(missing)
            continue

        if expected_status == "CONFLICT":
            rule_conflicts.append(expected_result)
            continue
        if expected_status == "UNRESOLVED":
            expected_uncovered.append(expected_result)
            continue
        if expected_status == "AMBIGUOUS":
            multi_unresolved.append(expected_result)
            continue

        # Both lanes are now independently available.  Comparison is purely
        # mechanical and never feeds back into either lane.
        expected = expected_result.get("預期注音") if expected_result else ""
        matched = reading_matches(actual, expected)
        if resolution_kind == "single":
            if matched:
                stats["agree"] += 1
                single_agrees.append({**expected_result, "比較結果": "一致"})
            else:
                stats["mismatch"] += 1
                single_mismatches.append({
                    **expected_result,
                    "比較結果": "差異候選",
                    "升格限制": "仍須回原頁確認現有注音；若有完整詞條規則/網址，須保留該證據後才能升格。",
                })
        elif resolution_kind == "handbook_allowed_set":
            if matched:
                stats["lexical_agree"] += 1
                lexical_agrees.append({**expected_result, "比較結果": "一致"})
            else:
                stats["lexical_mismatch"] += 1
                lexical_mismatches.append({
                    **expected_result,
                    "比較結果": "差異候選",
                    "升格限制": "統一用字手冊允許讀音集合不含此實際讀音；仍須回原頁確認 PDF actual 解碼。",
                })
        else:
            if matched:
                stats["lexical_agree"] += 1
                lexical_agrees.append({**expected_result, "比較結果": "一致"})
            elif weak_resolution:
                stats["weak_lexical_mismatch"] += 1
                weak_lexical_mismatches.append({
                    **expected_result,
                    "比較結果": "低信心詞境候選",
                    "升格限制": "此規則是由字典讀音分組延伸推定，來源未直接列出完整詞語；不得直接升格教材錯誤，應先補直接詞條／審訂證據。",
                })
            else:
                stats["lexical_mismatch"] += 1
                lexical_mismatches.append({
                    **expected_result,
                    "比較結果": "差異候選",
                    "升格限制": "規則已給出獨立預期音，但仍須回原頁確認實際注音；預設規則候選不得直接視為確認錯誤。",
                })

    # Source-independent v2.5 mandatory suite. The existing resolver closure is
    # invoked unchanged; failures remain red and are never patched by actual.
    mandatory_cases = load_mandatory_cases(runtime_mandatory_regressions)
    mandatory_results = run_mandatory_regressions(
        mandatory_cases,
        lambda target_char, context, target_index: decide_expected(
            target_char, context, target_index, dictionary.get(target_char)
        ),
    )
    mandatory_report = validate_regression_execution(mandatory_cases, mandatory_results)
    stats["mandatory_regression_required"] = mandatory_report["required"]
    stats["mandatory_regression_executed"] = mandatory_report["executed"]
    stats["mandatory_regression_failed"] = mandatory_report["failed"]
    stats["mandatory_regression_not_executed"] = mandatory_report["not_executed"]

    # Apply page-specific user truth cases as regressions. Historical rows are
    # audit controls only: they validate the independently-built current result
    # and never require the historical wrong actual to persist.
    confirmed_errors = list(direct_confirmed_errors)
    confirmed_correct_controls = []
    regression_defs = []
    if regressions_path and regressions_path.exists():
        with regressions_path.open("r", encoding="utf-8-sig", newline="") as f:
            regression_defs = list(csv.DictReader(f))
    validate_historical_regression_definitions(regression_defs, pdf_sha256)
    pdf_stem = pdf_path.stem if pdf_path else ""
    regression_defs = [rg for rg in regression_defs if historical_regression_routed_to_pdf(rg, pdf_stem)]
    current_printed_pages = {str(rec.get("課本頁") or "").strip() for rec in rows if str(rec.get("課本頁") or "").strip()}

    all_mismatches_pre = single_mismatches + lexical_mismatches
    all_agrees_pre = single_agrees + lexical_agrees
    all_occurrence_results = (
        all_mismatches_pre + weak_lexical_mismatches + all_agrees_pre +
        multi_unresolved + actual_missing + rule_conflicts + scope_skipped + expected_uncovered
    )

    regression_rows = []
    remaining_mismatches = list(all_mismatches_pre)
    for rg in regression_defs:
        cid = rg.get("case_id") or ""
        execution = execute_historical_regression(
            rg,
            all_occurrence_results,
            pdf_sha256,
            current_printed_pages,
        )
        regression_result = execution["result"]
        execution_note = execution["note"]

        if (
            execution["completion_gate"]
            and regression_result == "PASS"
            and (rg.get("status") or "確認錯誤").strip() == "確認正確"
        ):
            for matched in execution["matched_records"]:
                confirmed_correct_controls.append({
                    **matched, "回歸案例ID": cid, "人工確認來源": rg.get("source", ""),
                    "人工確認備註": rg.get("note", "")
                })

        regression_rows.append({
            "案例ID": cid, "課本頁": rg.get("page", ""), "完整詞語": rg.get("phrase", ""),
            "字元": rg.get("target_char", ""), "目標在詞內序號": rg.get("target_occurrence_index", ""),
            "定位上下文": rg.get("locator_context", ""),
            "真值實際注音": rg.get("actual_reading", ""),
            "真值預期注音": rg.get("expected_reading", ""), "真值結論": (rg.get("status") or "確認錯誤").strip(),
            "適用性模式": (rg.get("applicability_mode") or "hard_gate").strip(),
            "來源PDF SHA-256": (rg.get("source_pdf_sha256") or "").strip(),
            "適用性狀態": execution["applicability_state"],
            "計入完成門檻": "Y" if execution["completion_gate"] else "N",
            "定位命中數": execution["match_count"],
            "回歸結果": regression_result,
            "執行說明": execution_note,
            "來源": rg.get("source", ""), "備註": rg.get("note", "")
        })

    stats["confirmed_error"] = len(confirmed_errors)
    stats["confirmed_correct"] = len(confirmed_correct_controls)
    gating_regression_rows = [r for r in regression_rows if r["計入完成門檻"] == "Y"]
    reference_regression_rows = [r for r in regression_rows if r["適用性模式"] == "reference_only"]
    stats["regression_fail"] = sum(1 for r in gating_regression_rows if r["回歸結果"] == "FAIL")
    stats["regression_not_executed"] = sum(1 for r in gating_regression_rows if r["回歸結果"] == "NOT_EXECUTED")
    stats["regression_not_applicable"] = sum(1 for r in regression_rows if r["回歸結果"] == "NOT_APPLICABLE")
    stats["regression_required"] = len(gating_regression_rows)
    stats["regression_executed"] = sum(1 for r in gating_regression_rows if r["回歸結果"] in {"PASS", "FAIL"})
    stats["reference_regression_matched"] = sum(1 for r in reference_regression_rows if r["回歸結果"] in {"PASS", "FAIL"})
    stats["reference_regression_passed"] = sum(1 for r in reference_regression_rows if r["回歸結果"] == "PASS")
    stats["reference_regression_failed"] = sum(1 for r in reference_regression_rows if r["回歸結果"] == "FAIL")
    stats["reference_regression_not_found"] = sum(1 for r in reference_regression_rows if r["回歸結果"] == "NOT_APPLICABLE")
    single_mismatches = [r for r in remaining_mismatches if r.get("預期注音依據", "").startswith("專案一字多音字典")]
    lexical_mismatches = [r for r in remaining_mismatches if not r.get("預期注音依據", "").startswith("專案一字多音字典")]

    classifications: dict[str, dict] = {}

    def classify(
        rec: dict,
        state: str,
        source_view: str,
        *,
        expected_value="",
        expected_evidence="",
        comparison="",
        exclusion_reason="",
        canonical_occurrence_id="",
        actual_status="",
        expected_status="",
    ):
        occurrence_id = str(rec.get("occurrence_id") or "").strip()
        if not occurrence_id:
            raise ValueError(f"candidate classification 缺少 occurrence_id：{source_view}")
        item = {
            "state": state,
            "source_view": source_view,
            "actual": rec.get("實際注音"),
            "actual_raw": rec.get("實際注音"),
            "actual_evidence": rec.get("解碼依據") or "",
            "actual_status": actual_status or ("RESOLVED" if rec.get("實際注音") and rec.get("解碼依據") else "DECODE_ERROR" if state == "ACTUAL_DECODE_ERROR" else "UNRESOLVED"),
            "expected_set": list(normalize_expected_set(expected_value)),
            "expected_evidence": expected_evidence,
            "expected_status": expected_status or (
                "RESOLVED" if normalize_expected_set(expected_value) and expected_evidence
                else "CONFLICT" if state == "RULE_CONFLICT"
                else "AMBIGUOUS" if state == "EXPECTED_AMBIGUOUS"
                else "EXCLUDED" if state in {"EXCLUDED_NONINDEPENDENT_LAYER", "EXCLUDED_OUT_OF_SCOPE"}
                else "UNRESOLVED"
            ),
            "context_evidence": rec.get("完整詞語規則") or rec.get("所在行") or rec.get("局部詞境") or rec.get("字元") or "",
            "comparison_result": comparison,
            "exclusion_reason": exclusion_reason,
            "canonical_occurrence_id": canonical_occurrence_id,
            "source_record": rec,
        }
        old = classifications.get(occurrence_id)
        if old and old.get("state") != state:
            classifications[occurrence_id] = {
                **item,
                "state": "DATA_INTEGRITY_ERROR",
                "expected_set": [],
                "comparison_result": "",
                "note": f"同一 occurrence 被分類為 {old.get('state')} 與 {state}",
            }
        else:
            classifications[occurrence_id] = item

    for rec in single_agrees:
        classify(rec, "PASS", "單音一致", expected_value=rec.get("預期注音"), expected_evidence=rec.get("預期注音依據"), comparison="MATCH", actual_status="RESOLVED", expected_status="RESOLVED")
    for rec in lexical_agrees:
        classify(rec, "PASS", "完整詞語一致", expected_value=rec.get("預期注音"), expected_evidence=rec.get("預期注音依據"), comparison="MATCH", actual_status="RESOLVED", expected_status="RESOLVED")
    for rec in all_mismatches_pre:
        classify(rec, "DIFFERENCE_PENDING_CONFIRMATION", "差異候選", expected_value=rec.get("預期注音"), expected_evidence=rec.get("預期注音依據"), comparison="MISMATCH", actual_status="RESOLVED", expected_status="RESOLVED")
    for rec in weak_lexical_mismatches:
        classify(rec, "DIFFERENCE_PENDING_CONFIRMATION", "低信心詞境候選", expected_value=rec.get("預期注音"), expected_evidence=rec.get("預期注音依據"), comparison="MISMATCH", actual_status="RESOLVED", expected_status="RESOLVED")
    for rec in multi_unresolved:
        classify(rec, "EXPECTED_AMBIGUOUS", "多音仍待詞條判讀", actual_status="RESOLVED", expected_status="AMBIGUOUS")
    for rec in actual_missing:
        classify(
            rec,
            "ACTUAL_DECODE_ERROR",
            "現有注音未解碼",
            expected_value=rec.get("預期注音"),
            expected_evidence=rec.get("預期注音依據"),
            actual_status="DECODE_ERROR",
            expected_status=rec.get("expected_status") or "UNRESOLVED",
        )
    for rec in expected_uncovered:
        classify(rec, "EXPECTED_UNRESOLVED", "未建立預期音", actual_status="RESOLVED", expected_status="UNRESOLVED")
    for rec in rule_conflicts:
        classify(rec, "RULE_CONFLICT", "規則衝突", actual_status="RESOLVED", expected_status="CONFLICT")
    for rec in scope_skipped:
        classify(
            rec, "EXCLUDED_OUT_OF_SCOPE", "專名特殊項目略過",
            exclusion_reason="｜".join(str(rec.get(key) or "") for key in ("略過依據", "略過備註") if rec.get(key)),
            actual_status="RESOLVED" if rec.get("實際注音") else "UNRESOLVED", expected_status="EXCLUDED",
        )
    for suppressed in suppressed_duplicate_rows:
        classify(
            suppressed["record"], "EXCLUDED_NONINDEPENDENT_LAYER", "非獨立技術層",
            exclusion_reason=suppressed["reason"], canonical_occurrence_id=suppressed["canonical_occurrence_id"],
            actual_status="EXCLUDED", expected_status="EXCLUDED",
        )
    for rec in structural_exclusion_rows:
        classify(
            rec, "EXCLUDED_OUT_OF_SCOPE", "結構偵測排除",
            exclusion_reason="｜".join(str(rec.get(key) or "") for key in ("來源", "排除理由") if rec.get(key)),
            actual_status="EXCLUDED", expected_status="EXCLUDED",
        )

    actual_source_rows = all_actual_rows + structural_exclusion_rows
    ledger = _build_candidate_ledger(actual_source_rows, classifications)
    reconciliation = reconcile_ledger(ledger, [row.get("occurrence_id") for row in actual_source_rows])
    if not reconciliation.ok:
        raise ValueError("candidate ledger 集合對帳失敗：" + "；".join(reconciliation.errors))
    stats["ledger_total"] = len(ledger)
    stats["ledger_in_scope"] = reconciliation.details["in_scope_count"]
    stats["ledger_excluded"] = reconciliation.details["explicitly_excluded_count"]
    candidate_payload_hash = candidate_payload_sha256(ledger, mandatory_results, regression_rows)

    wb = Workbook()
    ws_sum = wb.active
    ws_sum.title = "摘要"
    ws_ledger = wb.create_sheet("Occurrence Ledger")
    ws_reconcile = wb.create_sheet("集合對帳")
    ws_confirmed = wb.create_sheet("歷史錯誤回歸稽核")
    ws_correct = wb.create_sheet("歷史正確回歸稽核")
    ws_diff = wb.create_sheet("差異候選")
    ws_weak = wb.create_sheet("低信心詞境候選")
    ws_agree = wb.create_sheet("單音一致")
    ws_lex_agree = wb.create_sheet("完整詞語一致")
    ws_multi = wb.create_sheet("多音仍待詞條判讀")
    ws_skipped = wb.create_sheet("專名特殊項目略過")
    ws_nonindependent = wb.create_sheet("非獨立技術層")
    ws_structural_excluded = wb.create_sheet("結構偵測排除")
    ws_missing = wb.create_sheet("現有注音未解碼")
    ws_uncovered = wb.create_sheet("未建立預期音")
    ws_conflict = wb.create_sheet("規則衝突")
    ws_multi_summary = wb.create_sheet("待判讀字摘要")
    ws_rule_summary = wb.create_sheet("規則命中摘要")
    ws_regression = wb.create_sheet("回歸測試")
    ws_mandatory = wb.create_sheet("強制回歸測試")

    summary = [
        ("工具", f"{PROGRAM} v{VERSION}"),
        ("workbook_schema_version", WORKBOOK_SCHEMA_VERSION),
        ("ledger_schema_version", LEDGER_SCHEMA_VERSION),
        ("expected_asset_fingerprint", expected_fingerprint["fingerprint"]),
        ("expected_asset_fingerprint_components", json.dumps(expected_fingerprint["components"], ensure_ascii=False, sort_keys=True)),
        ("candidate_payload_sha256", candidate_payload_hash),
        ("pipeline status", "PROCESSING_FINISHED"),
        ("狀態說明", "候選處理結束；是否全冊完成須由全量 ledger completion gate 判定。"),
        ("來源實際注音工作簿", str(actual_xlsx)),
        ("來源字典", str(dict_path)),
        ("完整詞語規則", str(rules_path)),
        ("產生時間", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        ("PDF偵測母體總數", stats["ledger_total"]),
        ("現版注音校對分母", stats["ledger_in_scope"]),
        ("明示排除總數", stats["ledger_excluded"]),
        ("現版注音座標總數", stats["ledger_in_scope"]),
        ("字典／手冊規則進入判讀位置", stats["dict_occurrences"]),
        ("未建立預期音", stats["expected_uncovered"]),
        ("預期音資料覆蓋率", (sum(1 for row in ledger if row.get("state") not in {"EXCLUDED_NONINDEPENDENT_LAYER", "EXCLUDED_OUT_OF_SCOPE"} and row.get("expected_set") and row.get("expected_evidence")) / stats["ledger_in_scope"]) if stats["ledger_in_scope"] else 0),
        ("單一有效讀音可直接比較", stats["single_evaluable"]),
        ("單音一致", stats["agree"]),
        ("單音差異候選（含已確認前）", stats["mismatch"]),
        ("完整詞語／保守規則可判讀", stats["lexical_evaluable"]),
        ("其中一致", stats["lexical_agree"]),
        ("其中強證據差異候選（含已確認前）", stats["lexical_mismatch"]),
        ("低信心延伸詞境候選", stats["weak_lexical_mismatch"]),
        ("已確認錯誤（人工／高信心規則）", stats["confirmed_error"]),
        ("人工確認正確回歸", stats["confirmed_correct"]),
        ("回歸測試失敗", stats["regression_fail"]),
        ("回歸測試未執行", stats["regression_not_executed"]),
        ("回歸測試不適用現版來源", stats["regression_not_applicable"]),
        ("回歸測試要求數", stats["regression_required"]),
        ("回歸測試實際執行數", stats["regression_executed"]),
        ("reference-only 已定位稽核", stats["reference_regression_matched"]),
        ("reference-only 稽核通過", stats["reference_regression_passed"]),
        ("reference-only 稽核失敗（不阻擋）", stats["reference_regression_failed"]),
        ("reference-only 未找到", stats["reference_regression_not_found"]),
        ("強制回歸要求數", stats["mandatory_regression_required"]),
        ("強制回歸實際執行數", stats["mandatory_regression_executed"]),
        ("強制回歸失敗", stats["mandatory_regression_failed"]),
        ("強制回歸未執行", stats["mandatory_regression_not_executed"]),
        ("多音仍待完整詞條判讀", stats["multi_or_special"]),
        ("專名／特殊項目略過", stats["scope_skipped"]),
        ("現有注音尚未解碼", stats["actual_missing"]),
        ("規則衝突", stats["rule_conflict"]),
        ("同座標隱藏／裝飾文字層已抑制", stats["suppressed_duplicate_layers"]),
        ("版面句境重建", "PDF RAWDICT 文字區塊 → 同區塊同基線碎片依幾何位置重排 → 強句界切句；不同文字盒即使同一水平線也不互相拼接。"),
        ("核心原則", "維持實際注音與預期注音兩條證據鏈分離；附檔黃底只修正來源限定的實際注音辨識，白底列確認教材錯誤，綠底只修正預期音；預期音新增/修正以教育部《國語辭典簡編本》為優先標準。"),
        ("『一』變調", "只有後字的預期音能由規則獨立判讀時才套用；後字不明時保留待判讀。"),
        ("回歸限制", "歷史回歸只驗證現版獨立判讀結果；不得因前版相同、前次曾判正確或曾判錯誤，就直接把現版未決項目升格。"),
        ("通過限制", "只有現版實際注音與現版獨立預期音都能重現且一致，才能稱為通過；未建立預期音不得視為正確。"),
        ("升格限制", "所有差異仍是候選。完整詞條明列與原頁實際注音都可重現時，才具備進一步升格條件。"),
    ]
    for k, v in summary:
        ws_sum.append([k, v])
    ws_sum.column_dimensions["A"].width = 36
    ws_sum.column_dimensions["B"].width = 120
    ws_sum.sheet_view.showGridLines = False
    for c in ws_sum[1]:
        c.font = Font(bold=True)

    ledger_cols = [
        "ledger_schema_version", "review_id_schema_version", "occurrence_id", "review_id", "active_review", "state",
        "source_row_number", "identity_confidence", "identity_row_fallback", "identity_collision_base",
        "pdf_sha256", "pdf", "pdf_name", "physical_page", "printed_page", "char", "stable_key", "font", "font_xref",
        "glyph_id", "zhuyin_component_id", "x0", "y0", "x1", "y1", "actual", "actual_raw", "actual_evidence", "actual_status",
        "expected_set_json", "expected_evidence", "expected_status", "context_evidence", "comparison_result", "source_view",
        "blocking_state", "exclusion_state", "canonical_occurrence_id", "exclusion_reason", "confirmation_gates_json", "note", "source_record_json",
    ]
    ws_ledger.append(ledger_cols)
    for entry in ledger:
        source_json = json.dumps(entry.get("source_record") or {}, ensure_ascii=False, sort_keys=True, default=str)
        if len(source_json) > 32000:
            raise ValueError(f"ledger source_record 超過 Excel 安全長度：{entry.get('occurrence_id')}")
        export_entry = {
            **entry,
            "expected_set_json": json.dumps(entry.get("expected_set") or [], ensure_ascii=False),
            "confirmation_gates_json": json.dumps(entry.get("confirmation_gates") or {}, ensure_ascii=False, sort_keys=True),
            "source_record_json": source_json,
        }
        ws_ledger.append([export_entry.get(column, "") for column in ledger_cols])

    ws_reconcile.append(["項目", "結果", "明細"])
    reconcile_dict = reconciliation.as_dict()
    for key in [
        "ok", "actual_count", "ledger_count", "in_scope_count", "explicitly_excluded_count",
        "actual_only", "ledger_only", "duplicate_occurrence_ids", "duplicate_review_ids",
        "multiple_authoritative_states", "unclassified", "worksheet_count_mismatch", "summary_mismatch", "state_counts", "errors",
    ]:
        value = reconcile_dict.get(key)
        detail = json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list)) else ""
        ws_reconcile.append([key, value if not isinstance(value, (dict, list)) else len(value), detail])

    context_cols = ["原文字層字元", "字元覆寫依據", "句境覆寫依據", "所在行", "版面區塊", "行內字元位置", "局部詞境", "前2字", "後2字", "候選短語", "字典例詞索引提示"]
    columns = [
        "occurrence_id", "review_id", "課本頁", "實體頁碼", "頁面標籤", "字元", *context_cols, "上下文", "實際注音", "預期注音",
        "比較結果", "預期注音依據", "完整詞語規則", "規則ID", "規則模式", "證據強度", "規則來源", "來源網址", "規則備註",
        "字典讀音原文", "字典例詞", "字典備註",
        "解碼依據", "穩定注音鍵", "群組注音鍵", "font", "font_xref", "注音元件ID",
        "x0", "y0", "x1", "y1", "升格限制",
    ]
    confirmed_cols = columns + ["回歸案例ID", "人工確認來源", "人工確認備註"]
    ws_confirmed.append(confirmed_cols)
    ws_correct.append(confirmed_cols)
    ws_diff.append(columns)
    ws_weak.append(columns)
    ws_agree.append(columns)
    ws_lex_agree.append(columns)

    multi_cols = [
        "occurrence_id", "review_id", "課本頁", "實體頁碼", "頁面標籤", "字元", *context_cols, "上下文", "實際注音", "字典讀音原文",
        "字典有效讀音", "字典例詞", "字典備註", "處理狀態", "例詞提示限制", "解碼依據",
        "穩定注音鍵", "font", "font_xref", "注音元件ID", "x0", "y0", "x1", "y1",
    ]
    ws_multi.append(multi_cols)

    skipped_cols = [
        "occurrence_id", "review_id", "課本頁", "實體頁碼", "頁面標籤", "字元", *context_cols, "上下文", "實際注音",
        "處理狀態", "略過規則ID", "略過範圍", "略過依據", "略過備註", "解碼依據",
        "穩定注音鍵", "font", "font_xref", "注音元件ID", "x0", "y0", "x1", "y1",
    ]
    ws_skipped.append(skipped_cols)

    missing_cols = [
        "occurrence_id", "review_id", "課本頁", "實體頁碼", "頁面標籤", "字元", *context_cols, "上下文", "字典讀音原文", "字典有效讀音",
        "字典例詞", "字典備註", "處理狀態", "expected_status", "預期注音", "預期注音依據", "完整詞語規則", "規則候選讀音",
        "穩定注音鍵", "群組注音鍵", "font", "font_xref", "注音元件ID", "x0", "y0", "x1", "y1",
    ]
    ws_missing.append(missing_cols)

    uncovered_cols = [
        "occurrence_id", "review_id", "課本頁", "實體頁碼", "頁面標籤", "字元", *context_cols, "上下文", "實際注音",
        "處理狀態", "覆蓋缺口", "解碼依據", "穩定注音鍵", "群組注音鍵", "font",
        "font_xref", "注音元件ID", "x0", "y0", "x1", "y1",
    ]
    ws_uncovered.append(uncovered_cols)

    conflict_cols = [
        "occurrence_id", "review_id", "課本頁", "實體頁碼", "頁面標籤", "字元", *context_cols, "上下文", "實際注音", "字典讀音原文",
        "字典有效讀音", "規則候選讀音", "規則ID", "處理狀態", "穩定注音鍵", "font", "font_xref", "注音元件ID",
        "x0", "y0", "x1", "y1",
    ]
    ws_conflict.append(conflict_cols)

    excluded_view_cols = [
        "occurrence_id", "canonical_occurrence_id", "state", "physical_page", "printed_page", "char",
        "font", "font_xref", "glyph_id", "zhuyin_component_id", "x0", "y0", "x1", "y1", "exclusion_reason",
    ]
    ws_nonindependent.append(excluded_view_cols)
    ws_structural_excluded.append(excluded_view_cols)
    for entry in ledger:
        target = None
        if entry.get("source_view") == "非獨立技術層":
            target = ws_nonindependent
        elif entry.get("source_view") == "結構偵測排除":
            target = ws_structural_excluded
        if target is not None:
            target.append([entry.get(column, "") for column in excluded_view_cols])

    def append_dict(ws, cols, data):
        for rec in data:
            ws.append([rec.get(c, "") for c in cols])

    all_mismatches = single_mismatches + lexical_mismatches
    append_dict(ws_confirmed, confirmed_cols, confirmed_errors)
    append_dict(ws_correct, confirmed_cols, confirmed_correct_controls)
    append_dict(ws_diff, columns, all_mismatches)
    append_dict(ws_weak, columns, weak_lexical_mismatches)
    append_dict(ws_agree, columns, single_agrees)
    append_dict(ws_lex_agree, columns, lexical_agrees)
    append_dict(ws_multi, multi_cols, multi_unresolved)
    append_dict(ws_skipped, skipped_cols, scope_skipped)
    append_dict(ws_missing, missing_cols, actual_missing)
    append_dict(ws_uncovered, uncovered_cols, expected_uncovered)
    append_dict(ws_conflict, conflict_cols, rule_conflicts)

    # High-frequency overview for planning explicit lexical rules.
    ws_multi_summary.append(["字元", "出現次數", "實際注音分布", "字典有效讀音", "例詞索引命中筆數", "所在行例"])
    by_char = defaultdict(list)
    for rec in multi_unresolved:
        by_char[rec.get("字元", "")].append(rec)
    for ch, items in sorted(by_char.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        actual_dist = Counter(i.get("實際注音", "") for i in items if i.get("實際注音"))
        dist_txt = "；".join(f"{k}×{v}" for k, v in actual_dist.most_common())
        readings = items[0].get("字典有效讀音", "") if items else ""
        hint_count = sum(1 for i in items if i.get("字典例詞索引提示"))
        examples = []
        for i in items:
            t = i.get("所在行") or i.get("局部詞境") or i.get("上下文") or ""
            if t and t not in examples:
                examples.append(t)
            if len(examples) >= 5:
                break
        ws_multi_summary.append([ch, len(items), dist_txt, readings, hint_count, "｜".join(examples)])

    ws_rule_summary.append(["規則ID", "命中筆數", "模式/說明"])
    rule_lookup = {r.rule_id: r for r in rules}
    for rid, count in rule_hit_counter.most_common():
        r = rule_lookup.get(rid)
        desc = f"{r.mode}｜{r.target_char}｜{r.phrase or '(預設)'}｜{r.expected_reading}｜{r.source}" if r else ""
        ws_rule_summary.append([rid, count, desc])

    reg_cols=[
        "案例ID","課本頁","完整詞語","字元","目標在詞內序號","定位上下文",
        "真值實際注音","真值預期注音","真值結論","適用性模式","來源PDF SHA-256",
        "適用性狀態","計入完成門檻","定位命中數","回歸結果","執行說明","來源","備註",
    ]
    ws_regression.append(reg_cols)
    append_dict(ws_regression, reg_cols, regression_rows)

    mandatory_cols = [
        "case_id", "context", "target_char", "target_index", "expected_resolution", "expected_set",
        "required_matched_phrase", "forbidden_matched_phrase", "control_type", "result", "actual_resolution",
        "actual_expected", "actual_matched_phrase", "failure_reason", "note",
    ]
    ws_mandatory.append(mandatory_cols)
    append_dict(ws_mandatory, mandatory_cols, mandatory_results)

    style_header(ws_confirmed, "F4CCCC")
    style_header(ws_correct, "D9EAD3")
    style_header(ws_diff, "FCE5CD")
    style_header(ws_weak, "FFF2CC")
    style_header(ws_agree, "D9EAD3")
    style_header(ws_lex_agree, "D9EAD3")
    style_header(ws_multi, "FCE5CD")
    style_header(ws_skipped, "D9EAD3")
    style_header(ws_nonindependent, "D9EAD3")
    style_header(ws_structural_excluded, "D9EAD3")
    style_header(ws_missing, "FFF2CC")
    style_header(ws_uncovered, "F4CCCC")
    style_header(ws_conflict, "EAD1DC")
    style_header(ws_multi_summary, "D9D2E9")
    style_header(ws_rule_summary, "D0E0E3")
    style_header(ws_regression, "D9EAD3")
    style_header(ws_mandatory, "D9EAD3")
    style_header(ws_ledger, "CFE2F3")
    style_header(ws_reconcile, "CFE2F3")
    for ws in [ws_ledger, ws_reconcile, ws_confirmed, ws_correct, ws_diff, ws_weak, ws_agree, ws_lex_agree, ws_multi, ws_skipped, ws_nonindependent, ws_structural_excluded, ws_missing, ws_uncovered, ws_conflict, ws_multi_summary, ws_rule_summary, ws_regression, ws_mandatory]:
        for row in ws.iter_rows(min_row=2):
            for c in row:
                c.alignment = Alignment(vertical="top", wrap_text=True)

    wb.save(out_xlsx)
    return {
        "output": out_xlsx,
        "stats": stats,
        "mismatches": all_mismatches,
        "multi": multi_unresolved,
        "expected_uncovered": expected_uncovered,
        "ledger": ledger,
        "reconciliation": reconciliation.as_dict(),
        "mandatory_regression": mandatory_report,
        "expected_asset_fingerprint": expected_fingerprint,
    }


def choose_pdf_gui():
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True)
        fn = filedialog.askopenfilename(title="選擇要進行注音候選比較的 PDF", filetypes=[("PDF", "*.pdf")])
        root.destroy()
        return Path(fn) if fn else None
    except Exception:
        return None


def notify(title, message, error=False):
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True)
        (messagebox.showerror if error else messagebox.showinfo)(title, message)
        root.destroy()
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser(description=f"{PROGRAM} v{VERSION}")
    ap.add_argument("pdf", nargs="?")
    ap.add_argument("--dict", default=str(DEFAULT_DICT))
    ap.add_argument("--map", default=str(DEFAULT_MAP))
    ap.add_argument("--groups", default=str(DEFAULT_GROUPS))
    ap.add_argument("--rules", default=str(DEFAULT_RULES))
    ap.add_argument("--regressions", default=str(DEFAULT_REGRESSIONS))
    ap.add_argument("--char-overrides", default=str(DEFAULT_CHAR_OVERRIDES))
    ap.add_argument("--context-overrides", default=str(DEFAULT_CONTEXT_OVERRIDES))
    ap.add_argument("--actual-output")
    ap.add_argument("--output")
    args = ap.parse_args()

    pdf = Path(args.pdf) if args.pdf else choose_pdf_gui()
    if not pdf:
        return 0
    if not pdf.exists():
        raise SystemExit(f"PDF not found: {pdf}")

    actual_out = Path(args.actual_output) if args.actual_output else unique_path(pdf, "實際注音解碼_安全")
    report_out = Path(args.output) if args.output else unique_path(pdf, "注音校對候選")

    root = Path(__file__).resolve().parent
    source_validation = validate_asset_manifest(root)
    if not source_validation.get("ok"):
        raise SystemExit("PIPELINE_BLOCKED：核心資料來源驗證失敗：" + "；".join(source_validation.get("errors") or []))
    if Path(args.map).resolve() != DEFAULT_MAP.resolve() or Path(args.groups).resolve() != DEFAULT_GROUPS.resolve():
        raise SystemExit("PIPELINE_BLOCKED：v5.2.2 不接受未列入 runtime manifest 的自訂 actual mapping 資產")
    fingerprint = compute_actual_asset_fingerprint(
        root,
        pdf,
        source_validation,
        decoder_version=DECODER_VERSION,
        source_files=ACTUAL_DECODER_SOURCE_FILES,
        dynamic_dependencies=actual_workbook_dynamic_dependencies(actual_out) if actual_out.exists() else None,
    )

    dec = None
    if args.actual_output and actual_out.exists():
        dec = load_existing_decode_stats(
            actual_out,
            fingerprint,
            (fingerprint.get("components") or {}).get("pdf_sha256", ""),
        )
    if dec is None:
        dec = decode(pdf, actual_out, Path(args.map), Path(args.groups), actual_asset_fingerprint=fingerprint)
        final_fingerprint = compute_actual_asset_fingerprint(
            root,
            pdf,
            source_validation,
            decoder_version=DECODER_VERSION,
            source_files=ACTUAL_DECODER_SOURCE_FILES,
            dynamic_dependencies=actual_workbook_dynamic_dependencies(actual_out),
        )
        if final_fingerprint.get("fingerprint") != fingerprint.get("fingerprint"):
            rewrite_actual_workbook_fingerprint(actual_out, final_fingerprint)
            fingerprint = final_fingerprint
    result = analyze(actual_out, Path(args.dict), Path(args.rules), report_out, pdf, Path(args.regressions), Path(args.char_overrides), Path(args.context_overrides))
    st = result["stats"]
    message = (
        f"處理結束；是否校對完成須由 occurrence ledger completion gate 判定。\n\n"
        f"實際注音：{dec['mapped']} / {dec['total']}（{dec['coverage']:.1%}）\n"
        f"單一讀音可直接比較：{st['single_evaluable']} 筆（差異 {st['mismatch']}）\n"
        f"完整詞語／保守規則可判讀：{st['lexical_evaluable']} 筆（強證據差異 {st['lexical_mismatch']}；低信心候選 {st['weak_lexical_mismatch']}）\n"
        f"多音仍待判讀：{st['multi_or_special']} 筆\n"
        f"專名／特殊項目略過：{st['scope_skipped']} 筆\n"
        f"現有注音未解碼：{st['actual_missing']} 筆\n"
        f"規則衝突：{st['rule_conflict']} 筆\n"
        f"人工確認錯誤：{st['confirmed_error']} 筆\n"
        f"人工確認正確回歸：{st['confirmed_correct']} 筆\n"
        f"回歸測試失敗：{st['regression_fail']} 筆\n\n"
        f"輸出：\n{report_out}"
    )
    print(message)
    notify(PROGRAM, message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
