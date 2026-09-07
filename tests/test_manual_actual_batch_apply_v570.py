from __future__ import annotations

import copy
import csv
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import actual_review
from actual_review import (
    GLYPH_CONFLICT_FILE,
    GLYPH_PROVENANCE_FILE,
    OCCURRENCE_OVERRIDE_FILE,
    USER_CFF_FILE,
    USER_GLYF_FILE,
    apply_staged_manual_actual_batch,
    apply_verified_actual_group,
    build_actual_group_for_entry,
    ensure_user_evidence_files,
    load_manual_actual_staging,
    manual_actual_staging_path,
    remove_staged_manual_actual_groups,
    stage_manual_actual_group,
)


SHA_A = "1" * 64
SHA_B = "2" * 64
SHA_C = "3" * 64
SHA_D = "4" * 64
AUTHORITATIVE_FILES = (
    OCCURRENCE_OVERRIDE_FILE,
    USER_GLYF_FILE,
    USER_CFF_FILE,
    GLYPH_CONFLICT_FILE,
    GLYPH_PROVENANCE_FILE,
)


def oid(value):
    return "occ_" + hashlib.sha256(value.encode()).hexdigest()


def entry(
    occurrence_id: str,
    glyph_sha256: str,
    *,
    pdf_name: str,
    x0: float,
) -> dict:
    return {
        "occurrence_id": occurrence_id,
        "review_id": "review-" + occurrence_id,
        "state": "ACTUAL_DECODE_ERROR",
        "pdf_name": pdf_name,
        "physical_page": 1,
        "printed_page": "1",
        "char": "轉",
        "stable_key": "stable-" + occurrence_id,
        "x0": x0,
        "y0": 20.0,
        "x1": x0 + 12.0,
        "y1": 40.0,
        "actual": "",
        "actual_evidence": "decoder unresolved",
        "expected_set": ["SECRET_EXPECTED_READING"],
        "expected_evidence": "SECRET_EXPECTED_EVIDENCE",
        "source_record": {
            "TTF字形SHA256": glyph_sha256,
            "穩定注音鍵": "stable-" + occurrence_id,
            "預期注音": "SECRET_EXPECTED_SOURCE",
        },
    }


def group(prefix: str, glyph_sha256: str, *, count: int = 1, pdf_name: str | None = None) -> dict:
    members = [
        entry(
            oid(f"{prefix}-{index + 1}"),
            glyph_sha256,
            pdf_name=pdf_name or f"{prefix}.pdf",
            x0=10.0 + index * 20.0,
        )
        for index in range(count)
    ]
    return build_actual_group_for_entry(members, members[0])


def stage(
    root: Path,
    current_group: dict,
    reading: str,
    *,
    checked_occurrence_ids: list[str] | None = None,
    source: str | None = None,
    note: str | None = None,
) -> dict:
    checked = checked_occurrence_ids or [current_group["members"][0]["occurrence_id"]]
    return stage_manual_actual_group(
        root,
        current_group,
        reading,
        checked_occurrence_ids=checked,
        source=source or f"manual source {current_group['group_id']}",
        note=note or f"manual note {current_group['group_id']}",
    )


def evidence_snapshot(root: Path) -> dict[str, bytes | None]:
    return {
        name: (root / name).read_bytes() if (root / name).exists() else None
        for name in AUTHORITATIVE_FILES
    }


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


class ManualActualBatchApplyV570Tests(unittest.TestCase):
    def assert_preapply_failure_has_no_authoritative_mutation(
        self,
        current_group: dict,
        live_groups: list[dict],
        message: str,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "_專案證據" / "actual"
            stage(root, current_group, "ㄓㄨㄢˇ")
            staging_before = manual_actual_staging_path(root).read_bytes()
            evidence_before = evidence_snapshot(root)
            with self.assertRaisesRegex(ValueError, message):
                apply_staged_manual_actual_batch(root, live_groups)
            self.assertEqual(evidence_snapshot(root), evidence_before)
            self.assertEqual(manual_actual_staging_path(root).read_bytes(), staging_before)

    def test_empty_staging_is_deterministic_noop_without_authoritative_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "_專案證據" / "actual"
            ensure_user_evidence_files(root)
            before = evidence_snapshot(root)
            expected = {
                "applied_group_count": 0,
                "group_results": [],
                "affected_occurrence_ids": [],
                "reopened_occurrence_ids": [],
                "quarantined_group_ids": [],
                "affected_pdf_names": [],
                "staging_remaining_count": 0,
            }
            with patch("actual_review.apply_verified_actual_group") as apply_mock:
                self.assertEqual(apply_staged_manual_actual_batch(root, []), expected)
                self.assertEqual(apply_staged_manual_actual_batch(root, []), expected)
            apply_mock.assert_not_called()
            self.assertEqual(evidence_snapshot(root), before)
            self.assertFalse(manual_actual_staging_path(root).exists())

    def test_two_valid_groups_apply_once_in_group_id_order_and_acknowledge_together(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = group("first", SHA_A, count=2, pdf_name="z-book.pdf")
            second = group("second", SHA_B, pdf_name="a-book.pdf")
            for current in sorted([first, second], key=lambda item: item["group_id"], reverse=True):
                checked = [member["occurrence_id"] for member in current["members"]]
                stage(root, current, "ㄓㄨㄢˇ", checked_occurrence_ids=checked)

            real_apply = actual_review.apply_verified_actual_group
            applied_order: list[str] = []

            def record_apply(root_arg, live_group, reading, **kwargs):
                applied_order.append(live_group["group_id"])
                return real_apply(root_arg, live_group, reading, **kwargs)

            with patch("actual_review.apply_verified_actual_group", side_effect=record_apply) as apply_mock:
                result = apply_staged_manual_actual_batch(root, [second, first])

            expected_order = sorted([first["group_id"], second["group_id"]])
            self.assertEqual(applied_order, expected_order)
            self.assertEqual(apply_mock.call_count, 2)
            self.assertEqual(result["applied_group_count"], 2)
            self.assertEqual([item["group_id"] for item in result["group_results"]], expected_order)
            self.assertEqual(result["affected_occurrence_ids"], sorted([oid("first-1"), oid("first-2"), oid("second-1")]))
            self.assertEqual(result["affected_pdf_names"], ["a-book.pdf", "z-book.pdf"])
            self.assertEqual(result["staging_remaining_count"], 0)
            self.assertEqual(load_manual_actual_staging(root)["staged_groups"], [])

            learning = read_csv(root / USER_GLYF_FILE)
            first_learning = next(row for row in learning if row["glyph_sha256"] == SHA_A)
            self.assertEqual(first_learning["verification_level"], "VERIFIED_EXACT_GLYPH")
            overrides = read_csv(root / OCCURRENCE_OVERRIDE_FILE)
            self.assertEqual(len(overrides), 3)
            self.assertTrue(all(row["source"].startswith("manual source agr_") for row in overrides))
            self.assertTrue(all(row["note"].startswith("manual note agr_") for row in overrides))

    def test_second_group_late_exception_rolls_back_all_five_files_and_keeps_staging(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ensure_user_evidence_files(root)
            seed = group("seed", SHA_C)
            apply_verified_actual_group(root, seed, "ㄙㄜˋ", source="seed")
            self.assertTrue(all((root / name).exists() for name in AUTHORITATIVE_FILES))

            first = group("first", SHA_A)
            second = group("second", SHA_B)
            stage(root, first, "ㄓㄨㄢˇ")
            stage(root, second, "ㄅ")
            evidence_before = evidence_snapshot(root)
            staging_before = manual_actual_staging_path(root).read_bytes()
            real_apply = actual_review.apply_verified_actual_group
            call_count = 0

            def fail_second(root_arg, live_group, reading, **kwargs):
                nonlocal call_count
                call_count += 1
                if call_count == 2:
                    raise RuntimeError("simulated late second-group failure")
                return real_apply(root_arg, live_group, reading, **kwargs)

            with patch("actual_review.apply_verified_actual_group", side_effect=fail_second):
                with self.assertRaisesRegex(RuntimeError, "late second-group"):
                    apply_staged_manual_actual_batch(root, [second, first])

            self.assertEqual(evidence_snapshot(root), evidence_before)
            self.assertEqual(manual_actual_staging_path(root).read_bytes(), staging_before)
            self.assertEqual(len(load_manual_actual_staging(root)["staged_groups"]), 2)

    def test_missing_live_group_fails_before_any_authoritative_mutation(self):
        current = group("missing", SHA_A)
        self.assert_preapply_failure_has_no_authoritative_mutation(current, [], "找不到 live group")

    def test_stale_live_snapshot_fails_before_any_authoritative_mutation(self):
        current = group("stale", SHA_A)
        live = copy.deepcopy(current)
        live["group_snapshot"] = "f" * 64
        self.assert_preapply_failure_has_no_authoritative_mutation(current, [live], "group_snapshot")

    def test_changed_live_identity_fields_fail_before_any_authoritative_mutation(self):
        for field, value in (
            ("kind", "OCCURRENCE_ONLY"),
            ("exact_key", SHA_D),
            ("style_group", "unexpected-style"),
        ):
            with self.subTest(field=field):
                current = group("identity-" + field, SHA_A)
                live = copy.deepcopy(current)
                live[field] = value
                self.assert_preapply_failure_has_no_authoritative_mutation(current, [live], f"field={field}")

    def test_changed_member_roster_fails_before_any_authoritative_mutation(self):
        current = group("roster", SHA_A, count=2)
        live = copy.deepcopy(current)
        live["members"][1]["occurrence_id"] = "replacement-occurrence"
        self.assert_preapply_failure_has_no_authoritative_mutation(current, [live], "member roster")

    def test_invalidated_checked_occurrence_fails_before_any_authoritative_mutation(self):
        current = group("checked", SHA_A, count=2)
        live = copy.deepcopy(current)
        live["members"] = live["members"][1:]
        live["occurrence_count"] = 1
        self.assert_preapply_failure_has_no_authoritative_mutation(current, [live], "checked occurrence 已失效")

    def test_existing_glyph_conflict_is_quarantined_without_rolling_back_other_group(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            conflicting = group("conflict", SHA_A, count=3)
            checked = [member["occurrence_id"] for member in conflicting["members"][:2]]
            apply_verified_actual_group(
                root,
                conflicting,
                "ㄩㄝˋ",
                checked_occurrence_ids=checked,
                source="old direct visual evidence",
            )
            other = group("other", SHA_B)
            stage(root, conflicting, "ㄌㄜˋ", checked_occurrence_ids=checked)
            stage(root, other, "ㄅ")

            result = apply_staged_manual_actual_batch(root, [other, conflicting])

            self.assertEqual(result["applied_group_count"], 2)
            self.assertEqual(result["quarantined_group_ids"], [conflicting["group_id"]])
            self.assertEqual(result["reopened_occurrence_ids"], [oid("conflict-3")])
            conflict_result = next(
                item for item in result["group_results"]
                if item["group_id"] == conflicting["group_id"]
            )
            self.assertTrue(conflict_result["glyph_truth_conflict"])
            self.assertTrue(conflict_result["quarantined"])
            self.assertTrue(any(row["glyph_sha256"] == SHA_A for row in read_csv(root / GLYPH_CONFLICT_FILE)))
            self.assertTrue(any(row["glyph_sha256"] == SHA_B for row in read_csv(root / USER_GLYF_FILE)))
            self.assertEqual(load_manual_actual_staging(root)["staged_groups"], [])

    def test_newer_same_group_upsert_during_apply_rolls_back_evidence_and_preserves_new_intent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current = group("newer", SHA_A)
            frozen = stage(root, current, "ㄓㄨㄢˇ", source="old source", note="old note")
            evidence_before = evidence_snapshot(root)
            real_apply = actual_review.apply_verified_actual_group

            def apply_then_upsert(root_arg, live_group, reading, **kwargs):
                result = real_apply(root_arg, live_group, reading, **kwargs)
                stage(
                    root_arg,
                    current,
                    "ㄓㄨㄢˋ",
                    source="new source",
                    note="new note",
                )
                return result

            with patch("actual_review.apply_verified_actual_group", side_effect=apply_then_upsert):
                with self.assertRaisesRegex(ValueError, "decision 已更新"):
                    apply_staged_manual_actual_batch(root, [current])

            self.assertEqual(evidence_snapshot(root), evidence_before)
            self.assertTrue(all(value is None for value in evidence_snapshot(root).values()))
            remaining = load_manual_actual_staging(root)["staged_groups"]
            self.assertEqual(len(remaining), 1)
            self.assertEqual(remaining[0]["reading"], "ㄓㄨㄢˋ")
            self.assertEqual(remaining[0]["source"], "new source")
            self.assertNotEqual(remaining[0]["updated_at"], frozen["updated_at"])

    def test_unrelated_group_staged_during_apply_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selected = group("selected", SHA_A)
            unrelated = group("unrelated", SHA_B)
            stage(root, selected, "ㄓㄨㄢˇ")
            real_apply = actual_review.apply_verified_actual_group

            def apply_then_add_unrelated(root_arg, live_group, reading, **kwargs):
                result = real_apply(root_arg, live_group, reading, **kwargs)
                stage(root_arg, unrelated, "ㄅ")
                return result

            with patch("actual_review.apply_verified_actual_group", side_effect=apply_then_add_unrelated):
                result = apply_staged_manual_actual_batch(root, [selected])

            self.assertEqual(result["applied_group_count"], 1)
            self.assertEqual(result["staging_remaining_count"], 1)
            remaining = load_manual_actual_staging(root)["staged_groups"]
            self.assertEqual([item["group_id"] for item in remaining], [unrelated["group_id"]])
            self.assertEqual(remaining[0]["reading"], "ㄅ")

    def test_batch_acknowledgement_performs_one_atomic_staging_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = group("ack-first", SHA_A)
            second = group("ack-second", SHA_B)
            stage(root, first, "ㄓㄨㄢˇ")
            stage(root, second, "ㄅ")
            real_write = actual_review._write_manual_actual_staging

            with patch(
                "actual_review._write_manual_actual_staging",
                wraps=real_write,
            ) as write_mock:
                result = apply_staged_manual_actual_batch(root, [second, first])

            self.assertEqual(result["staging_remaining_count"], 0)
            self.assertEqual(write_mock.call_count, 1)

    def test_atomic_acknowledgement_rejects_one_stale_token_without_partial_removal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = group("ack-stale-first", SHA_A)
            second = group("ack-stale-second", SHA_B)
            stage(root, first, "ㄓㄨㄢˇ")
            stage(root, second, "ㄅ")
            frozen = copy.deepcopy(load_manual_actual_staging(root)["staged_groups"])
            stage(root, first, "ㄓㄨㄢˋ")
            before = manual_actual_staging_path(root).read_bytes()

            with patch("actual_review._write_manual_actual_staging") as write_mock:
                with self.assertRaisesRegex(ValueError, "decision 已更新"):
                    remove_staged_manual_actual_groups(root, frozen)

            write_mock.assert_not_called()
            self.assertEqual(manual_actual_staging_path(root).read_bytes(), before)
            self.assertEqual(len(load_manual_actual_staging(root)["staged_groups"]), 2)

    def test_selected_tokens_are_rechecked_before_first_authoritative_apply(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current = group("preapply-race", SHA_A)
            old = stage(root, current, "ㄓㄨㄢˇ")
            old_document = copy.deepcopy(load_manual_actual_staging(root))
            newer = stage(root, current, "ㄓㄨㄢˋ")
            newer_document = copy.deepcopy(load_manual_actual_staging(root))
            self.assertNotEqual(old["updated_at"], newer["updated_at"])

            with (
                patch(
                    "actual_review.load_manual_actual_staging",
                    side_effect=[old_document, newer_document],
                ),
                patch("actual_review.apply_verified_actual_group") as apply_mock,
            ):
                with self.assertRaisesRegex(ValueError, "decision 已更新"):
                    apply_staged_manual_actual_batch(root, [current])

            apply_mock.assert_not_called()
            self.assertTrue(all(value is None for value in evidence_snapshot(root).values()))

    def test_batch_does_not_refresh_decode_or_touch_expected_evidence(self):
        import standalone_proofread

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "_專案證據" / "actual"
            expected_path = Path(directory) / "_專案證據" / "expected" / "sentinel.json"
            expected_path.parent.mkdir(parents=True)
            expected_path.write_bytes(b'{"expected":"immutable sentinel"}\n')
            expected_before = expected_path.read_bytes()
            current = group("boundary", SHA_A)
            stage(root, current, "ㄓㄨㄢˇ")

            with (
                patch.object(standalone_proofread, "refresh_actual_project") as refresh_mock,
                patch.object(standalone_proofread, "run_pipeline_pdfs") as pipeline_mock,
            ):
                result = apply_staged_manual_actual_batch(root, [current])

            self.assertEqual(result["applied_group_count"], 1)
            refresh_mock.assert_not_called()
            pipeline_mock.assert_not_called()
            self.assertEqual(expected_path.read_bytes(), expected_before)
            authoritative_text = "".join(
                (root / name).read_text(encoding="utf-8-sig")
                for name in AUTHORITATIVE_FILES
            )
            self.assertNotIn("SECRET_EXPECTED", authoritative_text)
            self.assertNotIn("預期注音", authoritative_text)


if __name__ == "__main__":
    unittest.main()
