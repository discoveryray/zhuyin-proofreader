from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from actual_review import (
    GLYPH_CONFLICT_FILE,
    GLYPH_PROVENANCE_FILE,
    MANUAL_ACTUAL_STAGING_FILE,
    MANUAL_ACTUAL_STAGING_SCHEMA_VERSION,
    OCCURRENCE_OVERRIDE_FILE,
    USER_CFF_FILE,
    USER_GLYF_FILE,
    build_actual_group_for_entry,
    dynamic_actual_hashes,
    ensure_user_evidence_files,
    load_manual_actual_staging,
    manual_actual_staging_path,
    remove_staged_manual_actual_group,
    stage_manual_actual_group,
)


ROOT = Path(__file__).resolve().parents[1]
SHA_A = "a" * 64
SHA_B = "b" * 64
AUTHORITATIVE_DYNAMIC_FILES = (
    OCCURRENCE_OVERRIDE_FILE,
    USER_GLYF_FILE,
    USER_CFF_FILE,
    GLYPH_CONFLICT_FILE,
    GLYPH_PROVENANCE_FILE,
)


def entry(occurrence_id: str, glyph_sha256: str, *, x0: float) -> dict:
    return {
        "occurrence_id": occurrence_id,
        "review_id": "review-" + occurrence_id,
        "state": "ACTUAL_DECODE_ERROR",
        "pdf_name": "book.pdf",
        "physical_page": 1,
        "printed_page": "1",
        "char": "轉",
        "stable_key": "key-" + occurrence_id,
        "x0": x0,
        "y0": 20.0,
        "actual": "",
        "actual_evidence": "decoder unresolved",
        # These expected fields deliberately exercise the evidence boundary:
        # stage_manual_actual_group() must not serialize them.
        "expected_set": ["ㄓㄨㄢˇ"],
        "expected_evidence": "SECRET_EXPECTED_EVIDENCE",
        "dictionary_result": "SECRET_DICTIONARY_RESULT",
        "source_record": {
            "TTF字形SHA256": glyph_sha256,
            "穩定注音鍵": "key-" + occurrence_id,
            "預期注音": "SECRET_EXPECTED_SOURCE_FIELD",
        },
    }


def group_a() -> dict:
    members = [entry("occ-a1", SHA_A, x0=10.0), entry("occ-a2", SHA_A, x0=30.0)]
    return build_actual_group_for_entry(members, members[0])


def group_b() -> dict:
    members = [entry("occ-b1", SHA_B, x0=50.0)]
    return build_actual_group_for_entry(members, members[0])


def stage(root: Path, group: dict | None = None, reading: str = "ㄓㄨㄢˇ") -> dict:
    current = group or group_a()
    return stage_manual_actual_group(
        root,
        current,
        reading,
        checked_occurrence_ids=[current["members"][0]["occurrence_id"]],
        source="manual visual actual confirmation",
        note="direct PDF visual review",
    )


def file_snapshot(root: Path) -> dict[str, bytes | None]:
    return {
        name: (root / name).read_bytes() if (root / name).exists() else None
        for name in AUTHORITATIVE_DYNAMIC_FILES
    }


def all_keys(value) -> list[str]:
    if isinstance(value, dict):
        return [str(key) for key in value] + [key for item in value.values() for key in all_keys(item)]
    if isinstance(value, list):
        return [key for item in value for key in all_keys(item)]
    return []


class ManualActualStagingV570Tests(unittest.TestCase):
    def test_absent_file_is_valid_empty_staging_without_creating_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "_專案證據" / "actual"
            self.assertEqual(
                load_manual_actual_staging(root),
                {
                    "schema_version": MANUAL_ACTUAL_STAGING_SCHEMA_VERSION,
                    "staged_groups": [],
                },
            )
            self.assertFalse(manual_actual_staging_path(root).exists())

    def test_valid_stage_is_persisted_to_project_actual_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "_專案證據" / "actual"
            staged = stage(root)
            path = manual_actual_staging_path(root)
            self.assertEqual(path, root / MANUAL_ACTUAL_STAGING_FILE)
            self.assertTrue(path.is_file())
            loaded = load_manual_actual_staging(root)
            self.assertEqual(loaded["schema_version"], MANUAL_ACTUAL_STAGING_SCHEMA_VERSION)
            self.assertEqual(loaded["staged_groups"], [staged])
            self.assertEqual(staged["reading"], "ㄓㄨㄢˇ")

    def test_restart_reload_in_fresh_python_process_preserves_decision(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "_專案證據" / "actual"
            stage(root)
            expected = load_manual_actual_staging(root)
            result_path = Path(directory) / "fresh_process_result.json"
            self.assertNotEqual(result_path, manual_actual_staging_path(root))
            script = (
                "import json, sys; "
                "from pathlib import Path; "
                "from actual_review import load_manual_actual_staging; "
                "result = load_manual_actual_staging(Path(sys.argv[1])); "
                "Path(sys.argv[2]).write_text(json.dumps(result, ensure_ascii=False, sort_keys=True), encoding='utf-8')"
            )
            env = dict(os.environ)
            env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
            completed = subprocess.run(
                [sys.executable, "-c", script, str(root), str(result_path)],
                cwd=ROOT,
                env=env,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertTrue(
                result_path.is_file(),
                f"fresh process did not write result; stdout={completed.stdout!r}; stderr={completed.stderr!r}",
            )
            self.assertEqual(json.loads(result_path.read_text(encoding="utf-8")), expected)

    def test_same_group_upsert_is_deduplicated_and_keeps_original_staged_at(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = stage(root, reading="ㄓㄨㄢˇ")
            second = stage(root, reading="ㄓㄨㄢˋ")
            loaded = load_manual_actual_staging(root)
            self.assertEqual(len(loaded["staged_groups"]), 1)
            self.assertEqual(loaded["staged_groups"][0]["reading"], "ㄓㄨㄢˋ")
            self.assertEqual(loaded["staged_groups"][0]["staged_at"], first["staged_at"])
            self.assertEqual(loaded["staged_groups"][0]["updated_at"], second["updated_at"])

    def test_same_group_id_with_changed_snapshot_fails_closed_without_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage(root)
            path = manual_actual_staging_path(root)
            before = path.read_bytes()
            stale = group_a()
            stale["group_snapshot"] = "f" * 64
            with self.assertRaisesRegex(ValueError, "stale/contradictory"):
                stage(root, stale, reading="ㄓㄨㄢˋ")
            self.assertEqual(path.read_bytes(), before)

    def test_invalid_reading_is_rejected_without_creating_store(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "reading"):
                stage(root, reading="轉")
            self.assertFalse(manual_actual_staging_path(root).exists())

    def test_malformed_json_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = manual_actual_staging_path(root)
            path.write_text('{"schema_version":', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "JSON 損壞"):
                load_manual_actual_staging(root)

    def test_non_object_root_and_duplicate_json_keys_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = manual_actual_staging_path(root)
            path.write_text("[]", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "root 必須是 object"):
                load_manual_actual_staging(root)
            path.write_text(
                '{"schema_version":"1.0","schema_version":"1.0","staged_groups":[]}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "key 重複"):
                load_manual_actual_staging(root)

    def test_unknown_schema_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manual_actual_staging_path(root).write_text(
                json.dumps({"schema_version": "99.0", "staged_groups": []}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "schema 不相容"):
                load_manual_actual_staging(root)

    def test_missing_required_decision_field_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage(root)
            path = manual_actual_staging_path(root)
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["staged_groups"][0].pop("checked_occurrence_ids")
            path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "缺少欄位"):
                load_manual_actual_staging(root)

    def test_invalid_checked_occurrence_and_duplicate_group_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current = group_a()
            with self.assertRaisesRegex(ValueError, "不屬於 group members"):
                stage_manual_actual_group(
                    root,
                    current,
                    "ㄓㄨㄢˇ",
                    checked_occurrence_ids=["unknown-occurrence"],
                    source="manual visual actual confirmation",
                )
            staged = stage(root)
            path = manual_actual_staging_path(root)
            path.write_text(
                json.dumps(
                    {
                        "schema_version": MANUAL_ACTUAL_STAGING_SCHEMA_VERSION,
                        "staged_groups": [staged, staged],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "group_id 重複"):
                load_manual_actual_staging(root)

    def test_atomic_replace_failure_preserves_old_bytes_and_removes_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage(root)
            path = manual_actual_staging_path(root)
            original = path.read_bytes()
            with patch.object(Path, "replace", side_effect=OSError("simulated replace failure")):
                with self.assertRaisesRegex(OSError, "simulated replace failure"):
                    stage(root, group_b(), reading="ㄅ")
            self.assertEqual(path.read_bytes(), original)
            self.assertFalse(path.with_suffix(path.suffix + ".tmp").exists())
            self.assertEqual(len(load_manual_actual_staging(root)["staged_groups"]), 1)

    def test_staging_does_not_modify_authoritative_dynamic_actual_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ensure_user_evidence_files(root)
            before = file_snapshot(root)
            stage(root)
            self.assertEqual(file_snapshot(root), before)

    def test_staging_does_not_change_dynamic_actual_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ensure_user_evidence_files(root)
            before = dynamic_actual_hashes(root)
            stage(root)
            after = dynamic_actual_hashes(root)
            self.assertEqual(after, before)
            self.assertNotIn(MANUAL_ACTUAL_STAGING_FILE, after)

    def test_staging_calls_neither_apply_nor_refresh(self):
        import standalone_proofread

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch("actual_review.apply_verified_actual_group") as apply_mock,
                patch.object(standalone_proofread, "refresh_actual_project") as refresh_mock,
            ):
                stage(root)
            apply_mock.assert_not_called()
            refresh_mock.assert_not_called()

    def test_serialized_payload_contains_no_expected_or_dictionary_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage(root)
            raw_text = manual_actual_staging_path(root).read_text(encoding="utf-8")
            payload = json.loads(raw_text)
            keys = all_keys(payload)
            self.assertFalse(any("expected" in key.lower() or "預期" in key or "dictionary" in key.lower() for key in keys))
            self.assertNotIn("SECRET_EXPECTED", raw_text)
            self.assertNotIn("SECRET_DICTIONARY", raw_text)

    def test_unknown_expected_field_in_existing_payload_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage(root)
            path = manual_actual_staging_path(root)
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["staged_groups"][0]["expected_evidence"] = "forbidden"
            path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "未知欄位"):
                load_manual_actual_staging(root)

    def test_compare_and_remove_cannot_clear_a_later_upsert(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = stage(root, reading="ㄓㄨㄢˇ")
            current = stage(root, reading="ㄓㄨㄢˋ")
            with self.assertRaisesRegex(ValueError, "decision 已更新"):
                remove_staged_manual_actual_group(
                    root,
                    first["group_id"],
                    group_snapshot=first["group_snapshot"],
                    updated_at=first["updated_at"],
                )
            self.assertTrue(
                remove_staged_manual_actual_group(
                    root,
                    current["group_id"],
                    group_snapshot=current["group_snapshot"],
                    updated_at=current["updated_at"],
                )
            )
            self.assertEqual(load_manual_actual_staging(root)["staged_groups"], [])


if __name__ == "__main__":
    unittest.main()
