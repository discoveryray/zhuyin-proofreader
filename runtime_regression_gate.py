from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from occurrence_ledger import normalize_expected_set


MANDATORY_REGRESSION_SCHEMA_VERSION = "2.5.0"


def _decision_value(decision: Any, field: str, default: Any = "") -> Any:
    if decision is None:
        return default
    if isinstance(decision, Mapping):
        return decision.get(field, default)
    return getattr(decision, field, default)


def load_mandatory_cases(path: Path) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {
            "case_id", "context", "target_char", "target_index", "expected_resolution",
            "expected_set", "required_matched_phrase", "forbidden_matched_phrase", "control_type", "note",
        }
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"mandatory regression 缺少欄位：{sorted(missing)}")
        return [dict(row) for row in reader]


def run_mandatory_regressions(
    cases_or_path: Sequence[Mapping[str, Any]] | Path,
    resolver: Callable[[str, str, int], tuple[Any, Sequence[Any]]],
) -> list[dict[str, Any]]:
    cases = load_mandatory_cases(cases_or_path) if isinstance(cases_or_path, Path) else [dict(row) for row in cases_or_path]
    results: list[dict[str, Any]] = []
    for case in cases:
        case_id = str(case.get("case_id") or "").strip()
        result = {
            "case_id": case_id,
            "context": case.get("context") or "",
            "target_char": case.get("target_char") or "",
            "target_index": case.get("target_index") or "",
            "expected_resolution": case.get("expected_resolution") or "",
            "expected_set": case.get("expected_set") or "",
            "required_matched_phrase": case.get("required_matched_phrase") or "",
            "forbidden_matched_phrase": case.get("forbidden_matched_phrase") or "",
            "control_type": case.get("control_type") or "",
            "note": case.get("note") or "",
            "result": "NOT_EXECUTED",
            "actual_resolution": "",
            "actual_expected": "",
            "actual_matched_phrase": "",
            "failure_reason": "",
        }
        try:
            context = str(case.get("context") or "")
            target_char = str(case.get("target_char") or "")
            target_index = int(str(case.get("target_index") or ""))
            if not context or not target_char or not (0 <= target_index < len(context)) or context[target_index] != target_char:
                raise ValueError("case target position 無效")
            decision, conflicts = resolver(target_char, context, target_index)
        except Exception as exc:
            result["failure_reason"] = f"resolver 未執行完成：{exc}"
            results.append(result)
            continue

        result["result"] = "FAIL"
        if decision is not None:
            actual_resolution = "RESOLVED"
            actual_expected_set = normalize_expected_set(_decision_value(decision, "expected_reading"))
            matched_phrase = str(_decision_value(decision, "matched_phrase") or "")
        elif conflicts:
            actual_resolution = "CONFLICT"
            actual_expected_set = tuple(sorted({
                reading
                for conflict in conflicts
                for reading in normalize_expected_set(_decision_value(conflict, "expected_reading"))
            }))
            matched_phrase = "|".join(sorted({str(_decision_value(conflict, "matched_phrase") or "") for conflict in conflicts if _decision_value(conflict, "matched_phrase")}))
        else:
            actual_resolution = "UNRESOLVED"
            actual_expected_set = ()
            matched_phrase = ""
        result["actual_resolution"] = actual_resolution
        result["actual_expected"] = "|".join(actual_expected_set)
        result["actual_matched_phrase"] = matched_phrase

        expected_resolution = str(case.get("expected_resolution") or "").strip().upper()
        expected_set = set(normalize_expected_set(case.get("expected_set")))
        required_phrase = str(case.get("required_matched_phrase") or "").strip()
        forbidden_phrases = {item.strip() for item in str(case.get("forbidden_matched_phrase") or "").split("|") if item.strip()}
        reasons: list[str] = []
        if expected_resolution not in {"ANY", actual_resolution}:
            reasons.append(f"resolution {actual_resolution} != {expected_resolution}")
        if expected_set and set(actual_expected_set) != expected_set:
            reasons.append(f"expected_set {set(actual_expected_set)} != {expected_set}")
        if required_phrase and required_phrase not in matched_phrase.split("|"):
            reasons.append(f"未命中必要詞條 {required_phrase}")
        bad_hits = sorted(phrase for phrase in forbidden_phrases if phrase in matched_phrase.split("|"))
        if bad_hits:
            reasons.append(f"誤命中禁止詞條 {bad_hits}")
        if not reasons:
            result["result"] = "PASS"
        else:
            result["failure_reason"] = "；".join(reasons)
        results.append(result)
    return results


def validate_regression_execution(required_cases: Sequence[Mapping[str, Any]], results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    required_ids = [str(row.get("case_id") or "").strip() for row in required_cases]
    result_ids = [str(row.get("case_id") or "").strip() for row in results]
    required_duplicates = sorted(key for key, count in Counter(required_ids).items() if key and count > 1)
    result_duplicates = sorted(key for key, count in Counter(result_ids).items() if key and count > 1)
    required_set = {value for value in required_ids if value}
    result_set = {value for value in result_ids if value}
    missing = sorted(required_set - result_set)
    unexpected = sorted(result_set - required_set)
    executed = sum(1 for row in results if row.get("result") in {"PASS", "FAIL"})
    passed = sum(1 for row in results if row.get("result") == "PASS")
    failed = sum(1 for row in results if row.get("result") == "FAIL")
    not_executed = sum(1 for row in results if row.get("result") == "NOT_EXECUTED")
    duplicate_count = len(required_duplicates) + len(result_duplicates)
    ok = (
        len(required_set) > 0
        and not missing
        and not unexpected
        and duplicate_count == 0
        and executed == len(required_set)
        and not_executed == 0
        and failed == 0
    )
    return {
        "ok": ok,
        "required": len(required_set),
        "executed": executed,
        "passed": passed,
        "failed": failed,
        "not_executed": not_executed,
        "duplicate_case_id": duplicate_count,
        "required_case_ids": sorted(required_set),
        "executed_case_ids": sorted(str(row.get("case_id")) for row in results if row.get("result") in {"PASS", "FAIL"}),
        "missing_case_ids": missing,
        "unexpected_case_ids": unexpected,
        "duplicate_required_case_ids": required_duplicates,
        "duplicate_result_case_ids": result_duplicates,
        "results": [dict(row) for row in results],
    }

