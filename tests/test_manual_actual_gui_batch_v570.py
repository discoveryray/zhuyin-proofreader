from __future__ import annotations

import copy
import hashlib
import tempfile
import tkinter as tk
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import fitz
import actual_review
import global_exact_glyph_library as global_library
import review_gui
import standalone_proofread as sp
from actual_review import (
    GLYPH_CONFLICT_FILE,
    GLYPH_PROVENANCE_FILE,
    MANUAL_ACTUAL_STAGING_FILE,
    OCCURRENCE_OVERRIDE_FILE,
    USER_CFF_FILE,
    USER_GLYF_FILE,
    build_actual_group_for_entry,
    load_manual_actual_staging,
    stage_manual_actual_group,
)


AUTHORITATIVE_ACTUAL_FILES = (
    OCCURRENCE_OVERRIDE_FILE,
    USER_GLYF_FILE,
    USER_CFF_FILE,
    GLYPH_CONFLICT_FILE,
    GLYPH_PROVENANCE_FILE,
)


def entry(
    occurrence_id: str,
    glyph_sha256: str,
    *,
    pdf_name: str | None = None,
    actual: str = "",
    expected: str = "ㄓㄨㄢˇ",
    x0: float = 10.0,
) -> dict:
    return {
        "occurrence_id": occurrence_id,
        "review_id": "review-" + occurrence_id,
        "state": "ACTUAL_DECODE_ERROR",
        "pdf_name": pdf_name or f"{occurrence_id}.pdf",
        "physical_page": 1,
        "printed_page": "1",
        "char": "轉",
        "stable_key": "stable-" + occurrence_id,
        "x0": x0,
        "y0": 20.0,
        "x1": x0 + 12.0,
        "y1": 40.0,
        "actual": actual,
        "actual_evidence": "decoder unresolved" if not actual else "refreshed actual evidence",
        "expected_set": [expected],
        "expected_evidence": "SECRET_EXPECTED_EVIDENCE",
        "source_record": {
            "TTF字形SHA256": glyph_sha256,
            "穩定注音鍵": "stable-" + occurrence_id,
            "預期注音": "SECRET_EXPECTED_SOURCE",
        },
    }


def make_project(base: Path) -> Path:
    output_dir = base / "output"
    output_dir.mkdir(parents=True)
    manifest = sp.seal_manifest({"pdfs": [], "records": []})
    sp.json_save(output_dir / "校對工作階段.json", manifest)
    sp.json_save(output_dir / "人工判定資料庫.json", sp.normalize_db({}))
    return output_dir


def actual_root(output_dir: Path) -> Path:
    return sp.project_actual_evidence_root(output_dir)


def attach_source_bytes(output_dir, ledger):
    # Headless behavior samples use auditable bytes; real PDF rendering has
    # separate GUI fixtures. No production source or runtime asset is changed.
    for row in ledger:
        path = output_dir / row["pdf_name"]
        path.write_bytes(b"isolated source hash fixture")
        row.update(pdf=str(path), pdf_sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def group_for(ledger: list[dict], target_index: int = 0) -> dict:
    return build_actual_group_for_entry(ledger, ledger[target_index])


def stage_group(output_dir: Path, group: dict, reading: str = "ㄓㄨㄢˇ") -> dict:
    return stage_manual_actual_group(
        actual_root(output_dir),
        group,
        reading,
        checked_occurrence_ids=[group["members"][0]["occurrence_id"]],
        source="人工 GUI actual 視覺確認",
        note="direct visual note",
    )


def batch_result_for(
    groups: list[dict],
    *,
    reading: str = "ㄓㄨㄢˇ",
    quarantined_group_ids: list[str] | None = None,
) -> dict:
    quarantined = set(quarantined_group_ids or [])
    group_results = []
    affected_occurrence_ids: set[str] = set()
    affected_pdf_names: set[str] = set()
    for group in groups:
        members = list(group["members"])
        checked_id = str(members[0]["occurrence_id"])
        member_ids = [str(member["occurrence_id"]) for member in members]
        affected_occurrence_ids.update(member_ids)
        affected_pdf_names.update(str(member["pdf_name"]) for member in members)
        conflict = group["group_id"] in quarantined
        group_results.append({
            "group_id": group["group_id"],
            "group_snapshot": group["group_snapshot"],
            "reading": reading,
            "verified_occurrence_ids": [checked_id],
            "target_occurrence_ids": [checked_id],
            "affected_occurrence_ids": member_ids,
            "reopened_occurrence_ids": member_ids[1:] if conflict else [],
            "glyph_truth_conflict": conflict,
            "quarantined": conflict,
        })
    return {
        "applied_group_count": len(groups),
        "group_results": group_results,
        "affected_occurrence_ids": sorted(affected_occurrence_ids),
        "reopened_occurrence_ids": sorted({
            occurrence_id
            for item in group_results
            for occurrence_id in item["reopened_occurrence_ids"]
        }),
        "quarantined_group_ids": sorted(quarantined),
        "affected_pdf_names": sorted(affected_pdf_names),
        "staging_remaining_count": 0,
    }


def refreshed_ledger_for(groups: list[dict], reading: str = "ㄓㄨㄢˇ") -> list[dict]:
    refreshed: list[dict] = []
    for group in groups:
        checked_id = str(group["members"][0]["occurrence_id"])
        for member in group["members"]:
            item = copy.deepcopy(member)
            if str(item["occurrence_id"]) == checked_id:
                item["actual"] = reading
                item["actual_evidence"] = "refreshed actual evidence"
            refreshed.append(item)
    return refreshed


def snapshot_paths(paths: list[Path]) -> dict[Path, bytes | None]:
    return {path: path.read_bytes() if path.exists() else None for path in paths}


class ManualActualGuiStagingServiceTests(unittest.TestCase):
    def test_stage_one_only_writes_staging_and_never_refreshes_or_regenerates(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = make_project(Path(directory))
            ledger = [entry("occ-a", "a" * 64, pdf_name="book.pdf")]
            root = actual_root(output_dir)
            root.mkdir(parents=True)
            # Preserve a mix of existing bytes and legitimate absence.
            (root / OCCURRENCE_OVERRIDE_FILE).write_bytes(b"override sentinel\r\n")
            (root / GLYPH_CONFLICT_FILE).write_bytes(b"conflict sentinel\n")
            artifact_paths = [
                output_dir / "校對工作階段.json",
                output_dir / "人工判定資料庫.json",
                output_dir / "01_實際注音" / "book_實際注音解碼.xlsx",
                output_dir / "02_候選報告" / "book_注音校對候選.xlsx",
                output_dir / "注音校對_最終報告.xlsx",
            ]
            for path in artifact_paths[2:]:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((path.name + " sentinel").encode("utf-8"))
            authoritative_paths = [root / name for name in AUTHORITATIVE_ACTUAL_FILES]
            before_artifacts = snapshot_paths(artifact_paths)
            before_authoritative = snapshot_paths(authoritative_paths)

            with (
                patch.object(sp, "materialize_ledger", return_value=ledger),
                patch.object(sp, "validate_manifest_integrity", wraps=sp.validate_manifest_integrity) as manifest_check,
                patch.object(sp, "validate_output_artifact_hashes", wraps=sp.validate_output_artifact_hashes) as artifact_check,
                patch.object(sp, "apply_verified_actual_group") as single_apply,
                patch.object(sp, "apply_staged_manual_actual_batch") as batch_apply,
                patch.object(sp, "_clear_actual_dependent_events") as clear_events,
                patch.object(sp, "refresh_actual_project") as refresh,
                patch.object(sp, "run_pipeline_pdfs") as pipeline,
                patch.object(sp, "decode") as decode,
                patch.object(sp, "analyze") as analyze,
                patch.object(sp, "regenerate_report") as regenerate,
                patch.object(sp, "initialize_project_actual_evidence") as initialize_evidence,
            ):
                result = sp.stage_manual_actual_correction(
                    output_dir,
                    "review-occ-a",
                    "ㄓㄨㄢˇ",
                    ["occ-a"],
                    "checked on original page",
                )

            self.assertEqual(result["staging_summary"]["staged_group_count"], 1)
            self.assertTrue((root / MANUAL_ACTUAL_STAGING_FILE).is_file())
            staged = load_manual_actual_staging(root)["staged_groups"][0]
            self.assertEqual(staged["source"], "人工 GUI actual 視覺確認")
            self.assertEqual(staged["note"], "checked on original page")
            self.assertEqual(snapshot_paths(artifact_paths), before_artifacts)
            self.assertEqual(snapshot_paths(authoritative_paths), before_authoritative)
            manifest_check.assert_called_once()
            artifact_check.assert_called_once()
            for forbidden in (
                single_apply, batch_apply, clear_events, refresh, pipeline,
                decode, analyze, regenerate, initialize_evidence,
            ):
                forbidden.assert_not_called()

    def test_stage_service_rebuilds_group_from_current_materialized_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = make_project(Path(directory))
            current_ledger = [entry("current", "b" * 64, actual="ㄅ")]
            with (
                patch.object(sp, "materialize_ledger", return_value=current_ledger),
                patch.object(
                    sp,
                    "build_actual_group_for_entry",
                    wraps=sp.build_actual_group_for_entry,
                ) as build_group,
            ):
                sp.stage_manual_actual_correction(
                    output_dir,
                    "review-current",
                    "ㄆ",
                    ["current"],
                )
            self.assertIs(build_group.call_args.args[0], current_ledger)
            self.assertIs(build_group.call_args.args[1], current_ledger[0])
            staged = load_manual_actual_staging(actual_root(output_dir))["staged_groups"][0]
            self.assertEqual(staged["member_occurrence_ids"], ["current"])

    def test_gui_service_rejects_different_reading_without_replacing_prior(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = make_project(Path(directory))
            ledger = [
                entry("same-a", "c" * 64, pdf_name="a.pdf", x0=10),
                entry("same-b", "c" * 64, pdf_name="b.pdf", x0=30),
            ]
            with patch.object(sp, "materialize_ledger", return_value=ledger):
                sp.stage_manual_actual_correction(
                    output_dir, "review-same-a", "ㄓㄨㄢˇ", ["same-a"],
                )
                before = (actual_root(output_dir) / MANUAL_ACTUAL_STAGING_FILE).read_bytes()
                with self.assertRaisesRegex(ValueError, "不同讀音"):
                    sp.stage_manual_actual_correction(
                        output_dir, "review-same-b", "ㄓㄨㄢˋ", ["same-b"],
                    )
                self.assertEqual((actual_root(output_dir) / MANUAL_ACTUAL_STAGING_FILE).read_bytes(), before)
            loaded = load_manual_actual_staging(actual_root(output_dir))
            self.assertEqual(len(loaded["staged_groups"]), 1)
            self.assertEqual(loaded["staged_groups"][0]["reading"], "ㄓㄨㄢˇ")
            self.assertEqual(loaded["staged_groups"][0]["checked_occurrence_ids"], ["same-a"])

    def test_restart_summary_recovers_count_group_ids_and_member_ids_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = make_project(Path(directory))
            ledger = [entry("restart", "d" * 64)]
            with patch.object(sp, "materialize_ledger", return_value=ledger):
                staged = sp.stage_manual_actual_correction(
                    output_dir, "review-restart", "ㄖㄣˊ", ["restart"],
                )["staged_group"]
            root = actual_root(output_dir)
            all_paths = list(root.iterdir())
            before = snapshot_paths(all_paths)
            first = sp.manual_actual_staging_summary(output_dir)
            second = sp.manual_actual_staging_summary(output_dir)
            self.assertEqual(first, second)
            self.assertEqual(first["staged_group_count"], 1)
            self.assertEqual(first["staged_group_ids"], [staged["group_id"]])
            self.assertEqual(first["staged_member_occurrence_ids"], ["restart"])
            self.assertEqual(snapshot_paths(all_paths), before)

    def test_missing_current_review_id_fails_closed_without_staging(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = make_project(Path(directory))
            with patch.object(sp, "materialize_ledger", return_value=[]):
                with self.assertRaisesRegex(ValueError, "找不到目前 review_id"):
                    sp.stage_manual_actual_correction(
                        output_dir, "missing-review", "ㄅ", ["missing"],
                    )
            self.assertFalse((actual_root(output_dir) / MANUAL_ACTUAL_STAGING_FILE).exists())


class ManualActualGuiBatchOrchestrationTests(unittest.TestCase):
    def _staged_groups(self, output_dir: Path, count: int, *, same_pdf: bool = False) -> tuple[list[dict], list[dict]]:
        ledger = [
            entry(
                f"occ-{index}",
                f"{index + 1:064x}",
                pdf_name="shared.pdf" if same_pdf else f"book-{index}.pdf",
                x0=10.0 + index,
            )
            for index in range(count)
        ]
        groups = [group_for(ledger, index) for index in range(count)]
        for group in groups:
            stage_group(output_dir, group)
        return ledger, groups

    def test_two_groups_call_batch_clear_and_refresh_once_with_union(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = make_project(Path(directory))
            pre_ledger, groups = self._staged_groups(output_dir, 2, same_pdf=True)
            post_ledger = refreshed_ledger_for(groups)
            core_result = batch_result_for(groups)
            with (
                patch.object(sp, "materialize_ledger", side_effect=[pre_ledger, post_ledger]),
                patch.object(sp, "apply_staged_manual_actual_batch", return_value=core_result) as batch,
                patch.object(sp, "_clear_actual_dependent_events", return_value=2) as clear,
                patch.object(sp, "refresh_actual_project", return_value=output_dir / "report.xlsx") as refresh,
            ):
                result = sp.apply_staged_manual_actual_corrections(output_dir)
            batch.assert_called_once()
            live_groups = batch.call_args.args[1]
            self.assertEqual({group["group_id"] for group in live_groups}, {group["group_id"] for group in groups})
            clear.assert_called_once()
            self.assertIs(clear.call_args.args[1], pre_ledger)
            self.assertEqual(clear.call_args.args[2], {"occ-0", "occ-1"})
            refresh.assert_called_once_with(output_dir)
            self.assertEqual(result["cleared_actual_dependent_event_count"], 2)
            self.assertEqual(result["postcondition_checked_occurrence_ids"], ["occ-0", "occ-1"])

    def test_ten_groups_on_same_pdf_still_refresh_once(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = make_project(Path(directory))
            pre_ledger, groups = self._staged_groups(output_dir, 10, same_pdf=True)
            with (
                patch.object(sp, "materialize_ledger", side_effect=[pre_ledger, refreshed_ledger_for(groups)]),
                patch.object(sp, "apply_staged_manual_actual_batch", return_value=batch_result_for(groups)) as batch,
                patch.object(sp, "_clear_actual_dependent_events", return_value=0) as clear,
                patch.object(sp, "refresh_actual_project", return_value=output_dir / "report.xlsx") as refresh,
            ):
                sp.apply_staged_manual_actual_corrections(output_dir)
            batch.assert_called_once()
            clear.assert_called_once()
            refresh.assert_called_once_with(output_dir)
            self.assertEqual(len(batch.call_args.args[1]), 10)

    def test_refresh_actual_project_forwards_the_complete_session_pdf_roster_once(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "output"
            output_dir.mkdir()
            manifest = {
                "session_id": "full-session-id",
                "pdfs": [
                    {"pdf_name": "affected.pdf"},
                    {"pdf_name": "unaffected.pdf"},
                    {"pdf_name": "cross-file-evidence.pdf"},
                ],
            }
            pdfs = [Path(directory) / item["pdf_name"] for item in manifest["pdfs"]]
            with (
                patch.object(sp, "json_load_strict", return_value=manifest),
                patch.object(sp, "validate_manifest_integrity"),
                patch.object(sp, "validate_output_artifact_hashes"),
                patch.object(sp, "_resolve_session_pdfs", return_value=pdfs) as resolve,
                patch.object(sp, "run_pipeline_pdfs", return_value=output_dir / "report.xlsx") as pipeline,
            ):
                sp.refresh_actual_project(output_dir)
            resolve.assert_called_once_with(output_dir, manifest)
            pipeline.assert_called_once_with(
                pdfs,
                output_dir,
                session_id_override="full-session-id",
                defer_excel_reports=True,
            )

    def test_one_refresh_pass_decodes_each_invalidated_pdf_once_and_reuses_unaffected(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            output_dir = base / "output"
            affected = base / "affected.pdf"
            unaffected = base / "unaffected.pdf"
            affected.write_bytes(b"affected PDF")
            unaffected.write_bytes(b"unaffected PDF")
            actual_dir = output_dir / "01_實際注音"
            candidate_dir = output_dir / "02_候選報告"
            actual_dir.mkdir(parents=True)
            candidate_dir.mkdir(parents=True)
            sp.actual_workbook_path(actual_dir, unaffected).write_bytes(b"reusable actual workbook")
            sp.candidate_workbook_path(candidate_dir, unaffected).write_bytes(b"reusable candidate workbook")
            dynamic_root = output_dir / "_專案證據" / "actual"
            dynamic_root.mkdir(parents=True)
            source_validation = {"ok": True, "errors": []}
            expected_fingerprint = {"fingerprint": "expected-fingerprint", "components": {}}
            produced_manifest = sp.seal_manifest({
                "session_id": "session",
                "pdfs": [],
                "records": [],
                "actual_source_ids": [],
                "regression_gate": {"ok": True},
            })
            reconciliation = SimpleNamespace(ok=True, as_dict=lambda: {"ok": True})
            snapshot = global_library.GlobalExactGlyphRepository.resolved(
                base / "absent-global-store"
            ).load_snapshot()
            snapshot_repository = SimpleNamespace(
                load_snapshot=MagicMock(return_value=snapshot)
            )
            fingerprint_snapshots = []

            def global_component(snapshot_arg, dependencies):
                fingerprint_snapshots.append(snapshot_arg)
                return global_library.global_exact_glyph_evidence_hashes(
                    snapshot_arg,
                    dependencies,
                )

            def fingerprint(_root, pdf, *_args, **_kwargs):
                return {"fingerprint": "fp-" + Path(pdf).name, "components": {}}

            def decode_once(*args, **_kwargs):
                Path(args[1]).write_bytes(b"new actual workbook")

            def analyze_once(*args, **_kwargs):
                Path(args[3]).write_bytes(b"new candidate workbook")

            repository_patcher = patch.object(
                sp.GlobalExactGlyphRepository,
                "resolved",
                return_value=snapshot_repository,
            )
            repository_patcher.start()
            self.addCleanup(repository_patcher.stop)
            component_patcher = patch.object(
                sp,
                "global_exact_glyph_evidence_hashes",
                side_effect=global_component,
            )
            component_patcher.start()
            self.addCleanup(component_patcher.stop)
            with (
                patch.object(sp, "initialize_project_actual_evidence", return_value=dynamic_root),
                patch.object(sp, "validate_asset_manifest", return_value=source_validation),
                patch.object(sp, "load_reuse_baseline_manifest", return_value={"session_id": "session"}),
                patch.object(sp, "compute_expected_asset_fingerprint", return_value=expected_fingerprint),
                patch.object(sp, "actual_workbook_global_exact_dependencies", return_value=()),
                patch.object(sp, "compute_actual_asset_fingerprint", side_effect=fingerprint),
                patch.object(
                    sp,
                    "output_is_reusable",
                    side_effect=lambda _out, pdf, *_args, **_kwargs: Path(pdf).name == "unaffected.pdf",
                ) as actual_reuse,
                patch.object(
                    sp,
                    "candidate_is_baseline_reusable",
                    side_effect=lambda _manifest, pdf, *_args, **_kwargs: Path(pdf).name == "unaffected.pdf",
                ) as candidate_reuse,
                patch.object(sp, "decode", side_effect=decode_once) as decode,
                patch.object(sp, "analyze", side_effect=analyze_once) as analyze,
                patch.object(sp.subprocess, "run") as cff_batch,
                patch.object(sp, "collect_manifest", return_value=produced_manifest),
                patch.object(sp, "_carry_forward_cross_version_identity", return_value=produced_manifest),
                patch.object(sp, "load_or_initialize_db", return_value=sp.normalize_db({})),
                patch.object(sp, "save_pending_json", return_value=0),
                patch.object(sp, "materialize_ledger", return_value=[]),
                patch.object(sp, "reconcile_ledger", return_value=reconciliation),
                patch.object(sp, "completion_gate", return_value={
                    "status": sp.PROCESSING_FINISHED,
                    "failed_gates": [],
                }),
                patch.object(sp, "write_pipeline_blocked"),
                patch("builtins.print"),
            ):
                sp.run_pipeline_pdfs(
                    [affected, unaffected],
                    output_dir,
                    session_id_override="session",
                    defer_excel_reports=True,
                )

            self.assertEqual(actual_reuse.call_count, 2)
            snapshot_repository.load_snapshot.assert_called_once_with()
            self.assertTrue(fingerprint_snapshots)
            self.assertTrue(all(item is snapshot for item in fingerprint_snapshots))
            decode.assert_called_once()
            self.assertEqual(Path(decode.call_args.args[0]), affected.resolve())
            self.assertIs(decode.call_args.kwargs["global_snapshot"], snapshot)
            candidate_reuse.assert_called_once()
            analyze.assert_called_once()
            self.assertEqual(Path(analyze.call_args.args[4]), affected.resolve())
            self.assertIs(analyze.call_args.kwargs["global_snapshot"], snapshot)
            cff_batch.assert_called_once()
            command = cff_batch.call_args.args[0]
            write_index = command.index("--write-only-workbooks")
            self.assertIn(str(sp.actual_workbook_path(actual_dir, affected.resolve())), command[write_index + 1:])
            self.assertNotIn(str(sp.actual_workbook_path(actual_dir, unaffected.resolve())), command[write_index + 1:])

    def test_final_apply_rebuilds_live_groups_from_current_member_objects(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = make_project(Path(directory))
            staged_ledger, staged_groups = self._staged_groups(output_dir, 2)
            current_ledger = copy.deepcopy(staged_ledger)
            post_ledger = refreshed_ledger_for(staged_groups)
            with (
                patch.object(sp, "materialize_ledger", side_effect=[current_ledger, post_ledger]),
                patch.object(
                    sp,
                    "build_actual_group_for_entry",
                    wraps=sp.build_actual_group_for_entry,
                ) as build_group,
                patch.object(sp, "apply_staged_manual_actual_batch", return_value=batch_result_for(staged_groups)) as batch,
                patch.object(sp, "_clear_actual_dependent_events", return_value=0),
                patch.object(sp, "refresh_actual_project", return_value=output_dir / "report.xlsx"),
            ):
                sp.apply_staged_manual_actual_corrections(output_dir)
            self.assertEqual(build_group.call_count, 2)
            self.assertTrue(all(call.args[0] is current_ledger for call in build_group.call_args_list))
            self.assertTrue(all(call.args[1] in current_ledger for call in build_group.call_args_list))
            self.assertTrue(all("members" in live for live in batch.call_args.args[1]))
            self.assertTrue(all("member_occurrence_ids" not in live for live in batch.call_args.args[1]))

    def test_stale_current_live_group_fails_in_phase_2a_before_refresh(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = make_project(Path(directory))
            staged_ledger, groups = self._staged_groups(output_dir, 1)
            current_ledger = copy.deepcopy(staged_ledger)
            current_ledger[0]["actual"] = "ㄅ"
            current_ledger[0]["actual_evidence"] = "new decoder evidence"
            with (
                patch.object(sp, "materialize_ledger", return_value=current_ledger),
                patch.object(sp, "refresh_actual_project") as refresh,
            ):
                with self.assertRaisesRegex(ValueError, "group_snapshot"):
                    sp.apply_staged_manual_actual_corrections(output_dir)
            refresh.assert_not_called()
            self.assertEqual(len(load_manual_actual_staging(actual_root(output_dir))["staged_groups"]), 1)
            self.assertTrue(all(not (actual_root(output_dir) / name).exists() for name in AUTHORITATIVE_ACTUAL_FILES))

    def test_conflict_is_successful_quarantine_and_refreshes_once(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = make_project(Path(directory))
            pre_ledger, groups = self._staged_groups(output_dir, 2)
            conflict_id = groups[0]["group_id"]
            core_result = batch_result_for(groups, quarantined_group_ids=[conflict_id])
            with (
                patch.object(sp, "materialize_ledger", side_effect=[pre_ledger, refreshed_ledger_for(groups)]),
                patch.object(sp, "apply_staged_manual_actual_batch", return_value=core_result),
                patch.object(sp, "_clear_actual_dependent_events", return_value=0),
                patch.object(sp, "refresh_actual_project", return_value=output_dir / "report.xlsx") as refresh,
            ):
                result = sp.apply_staged_manual_actual_corrections(output_dir)
            refresh.assert_called_once()
            self.assertEqual(result["quarantined_group_ids"], [conflict_id])
            self.assertEqual(result["applied_group_count"], 2)

    def test_empty_staging_is_noop_without_clear_or_refresh(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = make_project(Path(directory))
            with (
                patch.object(sp, "materialize_ledger", return_value=[]),
                patch.object(
                    sp,
                    "apply_staged_manual_actual_batch",
                    wraps=actual_review.apply_staged_manual_actual_batch,
                ) as batch,
                patch.object(sp, "_clear_actual_dependent_events") as clear,
                patch.object(sp, "refresh_actual_project") as refresh,
            ):
                result = sp.apply_staged_manual_actual_corrections(output_dir)
            batch.assert_called_once_with(actual_root(output_dir), [])
            clear.assert_not_called()
            refresh.assert_not_called()
            self.assertEqual(result["applied_group_count"], 0)
            self.assertFalse(result["refresh_performed"])

    def test_all_checked_actual_postconditions_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = make_project(Path(directory))
            pre_ledger, groups = self._staged_groups(output_dir, 2)
            with (
                patch.object(sp, "materialize_ledger", side_effect=[pre_ledger, refreshed_ledger_for(groups)]),
                patch.object(sp, "apply_staged_manual_actual_batch", return_value=batch_result_for(groups)),
                patch.object(sp, "_clear_actual_dependent_events", return_value=1),
                patch.object(sp, "refresh_actual_project", return_value=output_dir / "report.xlsx"),
            ):
                result = sp.apply_staged_manual_actual_corrections(output_dir)
            self.assertEqual(result["postcondition_checked_occurrence_ids"], ["occ-0", "occ-1"])
            self.assertTrue(result["refresh_performed"])

    def test_one_mismatched_checked_actual_never_reports_success(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = make_project(Path(directory))
            pre_ledger, groups = self._staged_groups(output_dir, 2)
            post_ledger = refreshed_ledger_for(groups)
            post_ledger[1]["actual"] = "ㄆ"
            with (
                patch.object(sp, "materialize_ledger", side_effect=[pre_ledger, post_ledger]),
                patch.object(sp, "apply_staged_manual_actual_batch", return_value=batch_result_for(groups)),
                patch.object(sp, "_clear_actual_dependent_events", return_value=0),
                patch.object(sp, "refresh_actual_project", return_value=output_dir / "report.xlsx") as refresh,
            ):
                with self.assertRaises(sp.ManualActualPostApplyError) as caught:
                    sp.apply_staged_manual_actual_corrections(output_dir)
            refresh.assert_called_once()
            self.assertEqual(caught.exception.phase, "post_refresh_actual_verification")
            self.assertIn("actual evidence 已正式套用", str(caught.exception))
            self.assertIn("actual 不符", str(caught.exception))

    def test_disappeared_checked_occurrence_never_reports_success(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = make_project(Path(directory))
            pre_ledger, groups = self._staged_groups(output_dir, 1)
            with (
                patch.object(sp, "materialize_ledger", side_effect=[pre_ledger, []]),
                patch.object(sp, "apply_staged_manual_actual_batch", return_value=batch_result_for(groups)),
                patch.object(sp, "_clear_actual_dependent_events", return_value=0),
                patch.object(sp, "refresh_actual_project", return_value=output_dir / "report.xlsx"),
            ):
                with self.assertRaises(sp.ManualActualPostApplyError) as caught:
                    sp.apply_staged_manual_actual_corrections(output_dir)
            self.assertIn("checked occurrence 已消失", str(caught.exception))

    def test_postcondition_ignores_expected_lane(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = make_project(Path(directory))
            pre_ledger, groups = self._staged_groups(output_dir, 1)
            post_ledger = refreshed_ledger_for(groups)
            post_ledger[0]["expected_set"] = ["COMPLETELY_DIFFERENT_SECRET_EXPECTED"]
            post_ledger[0]["expected_evidence"] = "SECRET EXPECTED MUST NOT AFFECT ACTUAL CHECK"
            with (
                patch.object(sp, "materialize_ledger", side_effect=[pre_ledger, post_ledger]),
                patch.object(sp, "apply_staged_manual_actual_batch", return_value=batch_result_for(groups)),
                patch.object(sp, "_clear_actual_dependent_events", return_value=0),
                patch.object(sp, "refresh_actual_project", return_value=output_dir / "report.xlsx"),
            ):
                result = sp.apply_staged_manual_actual_corrections(output_dir)
            self.assertEqual(result["postcondition_checked_occurrence_ids"], ["occ-0"])

    def test_refresh_failure_reports_evidence_committed_and_recovery_path(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = make_project(Path(directory))
            pre_ledger, groups = self._staged_groups(output_dir, 1)
            with (
                patch.object(sp, "materialize_ledger", return_value=pre_ledger),
                patch.object(sp, "apply_staged_manual_actual_batch", return_value=batch_result_for(groups)),
                patch.object(sp, "_clear_actual_dependent_events", return_value=0),
                patch.object(sp, "refresh_actual_project", side_effect=RuntimeError("simulated refresh failure")),
            ):
                with self.assertRaises(sp.ManualActualPostApplyError) as caught:
                    sp.apply_staged_manual_actual_corrections(output_dir)
            self.assertEqual(caught.exception.phase, "refresh_actual_project")
            message = str(caught.exception)
            self.assertIn("actual evidence 已正式套用", message)
            self.assertIn("--refresh-actual", message)
            self.assertIn("不要重新套用", message)


class DummyButton:
    def __init__(self):
        self.options = {}

    def config(self, **kwargs):
        self.options.update(kwargs)


class ImmediateRoot:
    def after(self, _delay, callback):
        callback()


class FakeProgressWidget:
    def __init__(self, *_args, **_kwargs):
        self.started = 0
        self.stopped = 0

    def pack(self, *_args, **_kwargs):
        return self

    def title(self, *_args, **_kwargs):
        return None

    def transient(self, *_args, **_kwargs):
        return None

    def grab_set(self):
        return None

    def grab_release(self):
        return None

    def destroy(self):
        return None

    def start(self, *_args, **_kwargs):
        self.started += 1

    def stop(self):
        self.stopped += 1


class ImmediateThread:
    instances: list["ImmediateThread"] = []

    def __init__(self, *, target, daemon):
        self.target = target
        self.daemon = daemon
        self.__class__.instances.append(self)

    def start(self):
        self.target()


class ManualActualGuiBehaviorTests(unittest.TestCase):
    def test_all_exact_group_occurrences_are_listed_with_checked_peers_last(self):
        ledger = [entry(name, "f" * 64) for name in "abcd"]
        group = group_for(ledger, 2)
        select = review_gui.actual_review_samples
        self.assertEqual(
            [row["occurrence_id"] for row in select(ledger[2], group, verified_checked_occurrence_ids={"a", "b"})],
            ["c", "d", "a", "b"],
        )
        self.assertEqual([row["occurrence_id"] for row in select(ledger[2], group)], ["c", "a", "b", "d"])
        self.assertEqual(
            [row["occurrence_id"] for row in select(ledger[2], group, verified_checked_occurrence_ids={"a", "b", "d"})],
            ["c", "a", "b", "d"],
        )
        many = [entry(f"occ-{number}", "e" * 64, pdf_name="same.pdf", x0=float(number * 15))
                for number in range(80)]
        selected = select(many[0], group_for(many))
        self.assertEqual(len(selected), 80)
        self.assertEqual(len({row["occurrence_id"] for row in selected}), 80)

    def test_unvalidated_staging_does_not_hide_sample_b(self):
        app = review_gui.ReviewApp.__new__(review_gui.ReviewApp)
        ledger = [entry(name, "f" * 64) for name in "abc"]
        app.root = object()
        app.output_dir = Path("project")
        app.manifest = {}
        app.db = {}
        app.records = ledger
        app.index = 2
        app.apply_actual_button = DummyButton()
        app.show = MagicMock()
        group = group_for(ledger, 2)
        with (
            patch.object(review_gui, "materialize_ledger", return_value=ledger),
            patch.object(review_gui, "build_actual_group_for_entry", return_value=group),
            patch.object(review_gui, "manual_actual_staging_summary", return_value={
                "staging_error": "stale evidence",
                "staged_checked_occurrence_ids": ["a", "b"],
            }),
            patch.object(review_gui, "ActualReadingDialog", return_value=SimpleNamespace(result=None)) as dialog,
        ):
            app.correct_actual()
        self.assertEqual(dialog.call_args.kwargs["verified_checked_occurrence_ids"], ())
        self.assertEqual([row["occurrence_id"] for row in review_gui.actual_review_samples(ledger[2], group)], ["c", "a", "b"])

    def test_invalidated_staging_restores_hidden_record_even_when_dialog_is_cancelled(self):
        ledger = [entry(name, "f" * 64) for name in "abc"]
        app = review_gui.ReviewApp.__new__(review_gui.ReviewApp)
        app.root = object()
        app.output_dir = Path("project")
        app.manifest = {}
        app.db = {}
        app.records = [ledger[0], ledger[2]]  # B was hidden by an earlier valid check.
        app.index = 1
        app.staging_summary = {"staged_group_count": 1}
        app.staged_checked_occurrence_ids = {"b"}
        app.apply_actual_button = DummyButton()
        app.staging_status = DummyButton()
        app.status = DummyButton()
        app.summary_text = DummyButton()
        app.tech_text = MagicMock()
        app.configure_actions = MagicMock()
        app.render = MagicMock()
        with (
            patch.object(review_gui, "materialize_ledger", return_value=ledger) as materialize,
            patch.object(review_gui, "manual_actual_staging_summary", return_value={
                "staging_error": "stale evidence",
                "staged_group_count": 1,
                "staged_member_occurrence_ids": ["b"],
                "staged_checked_occurrence_ids": ["b"],
            }),
            patch.object(review_gui, "ActualReadingDialog", return_value=SimpleNamespace(result=None)) as dialog,
            patch.object(review_gui, "stage_manual_actual_correction") as stage,
        ):
            app.correct_actual()
        materialize.assert_called_once_with(app.manifest, app.db)
        dialog.assert_called_once()
        stage.assert_not_called()
        self.assertEqual(dialog.call_args.kwargs["verified_checked_occurrence_ids"], ())
        self.assertEqual([item["occurrence_id"] for item in app.records], ["a", "b", "c"])
        self.assertEqual(app.staging_summary["staged_group_count"], 0)
        self.assertEqual(app.apply_actual_button.options["state"], "disabled")
        self.assertEqual(app.staged_checked_occurrence_ids, set())
        self.assertEqual(app.current()["occurrence_id"], "c")
        self.assertEqual(app.index, 2)
        self.assertIn("actual 暫存無法驗證", app.staging_status.options["text"])
        self.assertIn("本組剩餘 3 筆", app.status.options["text"])

    def test_reload_records_excludes_checked_occurrences_and_reduces_denominator(self):
        ledger = [entry(name, str(index) * 64) for index, name in enumerate("abcde", start=1)]
        app = review_gui.ReviewApp.__new__(review_gui.ReviewApp)
        app.output_dir = Path("project")
        app.manifest = {}
        app.db = {}
        app.records = []
        app.index = 0
        app.apply_actual_button = DummyButton()
        with (
            patch.object(review_gui, "materialize_ledger", return_value=ledger),
            patch.object(review_gui, "manual_actual_staging_summary", return_value={
                "staged_group_count": 1,
                "staged_group_ids": ["group-bc"],
                "staged_member_occurrence_ids": ["b", "c"],
                "staged_checked_occurrence_ids": ["b", "c"],
            }),
        ):
            app.reload_records()

        self.assertEqual([item["occurrence_id"] for item in app.records], ["a", "d", "e"])
        self.assertEqual(len(app.records), 3)
        self.assertEqual(app.apply_actual_button.options["text"], "套用 actual 修正（1）")

    def test_only_directly_checked_member_is_removed_from_actionable_queue(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = make_project(Path(directory))
            checked = entry("checked-a", "9" * 64, pdf_name="a.pdf", x0=10.0)
            unchecked = entry("unchecked-b", "9" * 64, pdf_name="b.pdf", x0=30.0)
            attach_source_bytes(output_dir, [checked, unchecked])
            group = group_for([checked, unchecked])
            stage_manual_actual_group(
                actual_root(output_dir),
                group,
                "ㄓㄨㄢˇ",
                checked_occurrence_ids=["checked-a"],
                source="人工 GUI actual 視覺確認",
            )

            summary = sp.manual_actual_staging_summary(output_dir)
            self.assertEqual(summary["staged_group_count"], 1)
            self.assertEqual(set(summary["staged_member_occurrence_ids"]), {"checked-a", "unchecked-b"})
            self.assertEqual(summary["staged_checked_occurrence_ids"], ["checked-a"])

            app = review_gui.ReviewApp.__new__(review_gui.ReviewApp)
            app.output_dir = output_dir
            app.apply_actual_button = DummyButton()
            app.manifest = {}
            app.db = {}
            app.records = []
            app.index = 0
            app.status = DummyButton()
            app.summary_text = DummyButton()
            app.tech_text = MagicMock()
            app.configure_actions = MagicMock()
            app.render = MagicMock()
            with (patch.object(review_gui, "materialize_ledger", return_value=[checked, unchecked]),
                  patch.object(sp, "materialize_ledger", return_value=[checked, unchecked])):
                app.reload_records()

            self.assertEqual(app.staged_member_occurrence_ids, {"checked-a", "unchecked-b"})
            self.assertEqual(app.staged_checked_occurrence_ids, {"checked-a"})
            self.assertEqual(app.apply_actual_button.options["text"], "套用 actual 修正（1）")
            self.assertEqual([item["occurrence_id"] for item in app.records], ["unchecked-b"])

            app.show()
            self.assertNotIn("actual 已暫存", app.status.options["text"])
            self.assertNotIn("已暫存人工核對結果", app.summary_text.options["text"])
            app.configure_actions.assert_called_with(unchecked)

    def test_count_zero_disables_and_positive_count_enables_button(self):
        app = review_gui.ReviewApp.__new__(review_gui.ReviewApp)
        app.output_dir = Path("unused")
        app.apply_actual_button = DummyButton()
        app.manifest = {}
        app.db = {}
        with patch.object(review_gui, "manual_actual_staging_summary", return_value={
            "staged_group_count": 0,
            "staged_group_ids": [],
            "staged_member_occurrence_ids": [],
            "staged_checked_occurrence_ids": [],
        }):
            app.reload_staging_summary()
        self.assertEqual(app.apply_actual_button.options["state"], "disabled")
        self.assertEqual(app.apply_actual_button.options["text"], "套用 actual 修正（0）")
        with patch.object(review_gui, "manual_actual_staging_summary", return_value={
            "staged_group_count": 3,
            "staged_group_ids": ["a", "b", "c"],
            "staged_member_occurrence_ids": ["o1", "o2", "o3"],
            "staged_checked_occurrence_ids": ["o1", "o3"],
        }):
            app.reload_staging_summary()
        self.assertEqual(app.apply_actual_button.options["state"], "normal")
        self.assertEqual(app.apply_actual_button.options["text"], "套用 actual 修正（3）")
        self.assertEqual(app.staged_member_occurrence_ids, {"o1", "o2", "o3"})
        self.assertEqual(app.staged_checked_occurrence_ids, {"o1", "o3"})

    def test_new_app_filters_durable_checked_occurrence_without_changing_ledger_truth(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = make_project(Path(directory))
            staged_entry = entry("durable", "e" * 64)
            remaining_entry = entry("remaining", "f" * 64)
            ledger = [staged_entry, remaining_entry]
            attach_source_bytes(output_dir, ledger)
            with patch.object(sp, "materialize_ledger", return_value=ledger):
                sp.stage_manual_actual_correction(
                    output_dir, "review-durable", "ㄉㄨˊ", ["durable"],
                )
            for _restart in range(2):
                app = review_gui.ReviewApp.__new__(review_gui.ReviewApp)
                app.output_dir = output_dir
                app.manifest = {}
                app.db = {}
                app.records = []
                app.index = 0
                app.apply_actual_button = DummyButton()
                with (patch.object(review_gui, "materialize_ledger", return_value=ledger),
                      patch.object(sp, "materialize_ledger", return_value=ledger)):
                    app.reload_records()
                self.assertEqual(app.staging_summary["staged_group_count"], 1)
                self.assertEqual(app.apply_actual_button.options["text"], "套用 actual 修正（1）")
                self.assertEqual([item["occurrence_id"] for item in app.records], ["remaining"])
            self.assertEqual(staged_entry["state"], "ACTUAL_DECODE_ERROR")
            self.assertEqual(sp.json_load_strict(output_dir / "人工判定資料庫.json").get("events"), {})

    def test_correct_actual_removes_all_checked_and_advances_from_b_to_d(self):
        app = review_gui.ReviewApp.__new__(review_gui.ReviewApp)
        ledger = [
            entry("a", "a" * 64),
            entry("b", "f" * 64, x0=10.0),
            entry("c", "f" * 64, x0=30.0),
            entry("d", "d" * 64),
            entry("e", "e" * 64),
        ]
        current = ledger[1]
        group = group_for(ledger, 1)
        app.root = object()
        app.output_dir = Path("project")
        app.manifest = {"manifest": "current"}
        app.db = {"events": {}}
        app.records = list(ledger)
        app.index = 1
        app.apply_actual_button = DummyButton()
        app.show = MagicMock()
        dialog = SimpleNamespace(result={
            "reading": "ㄓㄨㄢˇ",
            "checked_occurrence_ids": ["b", "c"],
            "note": "visual",
        })
        stage_result = {
            "staged_group": {"group_id": group["group_id"]},
            "staging_summary": {"staged_group_count": 1},
        }
        with (
            patch.object(review_gui, "materialize_ledger", return_value=ledger),
            patch.object(review_gui, "manual_actual_staging_summary", side_effect=[
                {"staged_group_count": 0, "staged_checked_occurrence_ids": []},
                {
                    "staged_group_count": 1,
                    "staged_group_ids": [group["group_id"]],
                    "staged_member_occurrence_ids": ["b", "c"],
                    "staged_checked_occurrence_ids": ["b", "c"],
                },
            ]),
            patch.object(review_gui, "build_actual_group_for_entry", return_value=group),
            patch.object(review_gui, "ActualReadingDialog", return_value=dialog),
            patch.object(review_gui, "stage_manual_actual_correction", return_value=stage_result) as stage,
            patch.object(review_gui, "apply_staged_manual_actual_corrections") as batch,
            patch.object(review_gui.threading, "Thread") as thread,
            patch.object(review_gui, "json_load_strict") as manifest_reload,
            patch.object(review_gui, "load_or_initialize_db") as db_reload,
            patch.object(review_gui.messagebox, "showinfo"),
            patch.object(review_gui.messagebox, "showerror"),
        ):
            app.correct_actual()
        stage.assert_called_once_with(
            app.output_dir,
            "review-b",
            "ㄓㄨㄢˇ",
            ["b", "c"],
            "visual",
        )
        batch.assert_not_called()
        thread.assert_not_called()
        manifest_reload.assert_not_called()
        db_reload.assert_not_called()
        self.assertEqual([item["occurrence_id"] for item in app.records], ["a", "d", "e"])
        self.assertEqual(app.index, 1)
        self.assertEqual(app.current()["occurrence_id"], "d")
        self.assertTrue(all(item["state"] == "ACTUAL_DECODE_ERROR" for item in ledger))
        self.assertEqual(app.show.call_count, 2)

    def test_filtering_staged_last_item_clamps_to_new_last_item(self):
        ledger = [entry(name, str(index) * 64) for index, name in enumerate("abcde", start=1)]
        app = review_gui.ReviewApp.__new__(review_gui.ReviewApp)
        app.output_dir = Path("project")
        app.manifest = {}
        app.db = {}
        app.records = list(ledger)
        app.index = 4
        app.apply_actual_button = DummyButton()
        with (
            patch.object(review_gui, "materialize_ledger", return_value=ledger),
            patch.object(review_gui, "manual_actual_staging_summary", return_value={
                "staged_group_count": 1,
                "staged_checked_occurrence_ids": ["e"],
            }),
        ):
            app.reload_records()
        self.assertEqual(app.index, 3)
        self.assertEqual(app.current()["occurrence_id"], "d")

    def test_save_event_does_not_reintroduce_previously_staged_occurrence(self):
        current = entry("expected-a", "a" * 64)
        staged_actual = entry("staged-b", "b" * 64)
        next_entry = entry("next-c", "c" * 64)
        resolved = copy.deepcopy(current)
        resolved["state"] = "PASS"
        staged_ledger = [resolved, staged_actual, next_entry]
        app = review_gui.ReviewApp.__new__(review_gui.ReviewApp)
        app.root = object()
        app.output_dir = Path("project")
        app.manifest = {}
        app.db = {"events": {}}
        app.records = [current, next_entry]
        app.index = 0
        app.staged_checked_occurrence_ids = {"staged-b"}
        app.apply_actual_button = DummyButton()
        app.show = MagicMock()
        with (
            patch.object(review_gui, "materialize_ledger", return_value=staged_ledger),
            patch.object(review_gui, "json_save") as save,
            patch.object(review_gui, "manual_actual_staging_summary", return_value={
                "staged_group_count": 1, "staged_checked_occurrence_ids": ["staged-b"],
            }) as summary,
        ):
            terminal = app.save_event(current, {"action": "確認 expected"})
        self.assertTrue(terminal)
        save.assert_called_once()
        summary.assert_called_once_with(app.output_dir, ledger=staged_ledger)
        self.assertEqual([item["occurrence_id"] for item in app.records], ["next-c"])
        self.assertNotIn("staged-b", [item["occurrence_id"] for item in app.records])
        app.show.assert_called_once()

    def test_all_actionable_staged_keeps_batch_enabled_and_explains_next_step(self):
        pending = entry("only", "a" * 64)
        app = review_gui.ReviewApp.__new__(review_gui.ReviewApp)
        app.output_dir = Path("project")
        app.manifest = {}
        app.db = {}
        app.records = [pending]
        app.index = 0
        app.apply_actual_button = DummyButton()
        app.status = DummyButton()
        app.summary_text = DummyButton()
        app.image = MagicMock()
        app.tech_text = MagicMock()
        app.primary = MagicMock()
        app.secondary = MagicMock()
        app.later = object()
        with (
            patch.object(review_gui, "materialize_ledger", return_value=[pending]),
            patch.object(review_gui, "manual_actual_staging_summary", return_value={
                "staged_group_count": 2,
                "staged_checked_occurrence_ids": ["only"],
            }),
        ):
            app.reload_records()
        app.show()

        self.assertEqual(app.records, [])
        self.assertEqual(app.apply_actual_button.options["state"], "normal")
        self.assertEqual(app.apply_actual_button.options["text"], "套用 actual 修正（2）")
        self.assertIn("沒有尚未暫存", app.status.options["text"])
        self.assertIn("仍有 2 組 actual 修正等待批次套用", app.summary_text.options["text"])
        self.assertNotIn("全冊完成", app.summary_text.options["text"])

    def test_final_button_asks_confirmation_before_starting_worker(self):
        app = review_gui.ReviewApp.__new__(review_gui.ReviewApp)
        app.root = object()
        app.reload_staging_summary = MagicMock(return_value={"staged_group_count": 2})
        with (
            patch.object(review_gui.messagebox, "askyesno", return_value=False) as ask,
            patch.object(review_gui, "apply_staged_manual_actual_corrections") as service,
            patch.object(review_gui.threading, "Thread") as thread,
        ):
            app.apply_staged_actuals()
        ask.assert_called_once()
        service.assert_not_called()
        thread.assert_not_called()

    def test_final_worker_is_one_thread_and_success_reloads_current_state(self):
        ImmediateThread.instances = []
        app = review_gui.ReviewApp.__new__(review_gui.ReviewApp)
        app.root = ImmediateRoot()
        app.output_dir = Path("project")
        app.index = 4
        app.records = []
        app.reload_staging_summary = MagicMock(return_value={"staged_group_count": 10})
        app.reload_records = MagicMock()
        app.show = MagicMock()
        result = {
            "applied_group_count": 10,
            "affected_pdf_names": ["a.pdf", "b.pdf"],
            "quarantined_group_ids": ["conflict-group"],
            "cleared_actual_dependent_event_count": 2,
        }
        with (
            patch.object(review_gui.messagebox, "askyesno", return_value=True),
            patch.object(review_gui.messagebox, "showinfo") as showinfo,
            patch.object(review_gui.messagebox, "showerror"),
            patch.object(review_gui, "apply_staged_manual_actual_corrections", return_value=result) as service,
            patch.object(review_gui.threading, "Thread", ImmediateThread),
            patch.object(review_gui.tk, "Toplevel", FakeProgressWidget),
            patch.object(review_gui, "WrappedLabel", FakeProgressWidget),
            patch.object(review_gui.ttk, "Progressbar", FakeProgressWidget),
            patch.object(review_gui, "apply_screen_safe_geometry"),
            patch.object(review_gui, "json_load_strict", return_value={"manifest": "refreshed"}),
            patch.object(review_gui, "validate_manifest_integrity"),
            patch.object(review_gui, "validate_output_artifact_hashes"),
            patch.object(review_gui, "load_or_initialize_db", return_value={"events": {}}),
        ):
            app.apply_staged_actuals()
        self.assertEqual(len(ImmediateThread.instances), 1)
        service.assert_called_once_with(app.output_dir)
        app.reload_records.assert_called_once()
        app.show.assert_called_once()
        self.assertEqual(app.manifest, {"manifest": "refreshed"})
        success_text = showinfo.call_args.args[1]
        self.assertIn("已一次套用 10 組", success_text)
        self.assertIn("工作階段與候選狀態已更新", success_text)
        self.assertIn("Excel 最終報告可按", success_text)
        self.assertIn("進入隔離", success_text)

    def test_reload_records_also_reloads_durable_staging_count(self):
        app = review_gui.ReviewApp.__new__(review_gui.ReviewApp)
        app.manifest = {}
        app.db = {}
        app.index = 0
        app.reload_staging_summary = MagicMock()
        with patch.object(review_gui, "materialize_ledger", return_value=[]):
            app.reload_records()
        app.reload_staging_summary.assert_called_once()


def find_button(widget, text: str):
    for child in widget.winfo_children():
        if isinstance(child, tk.Button) and str(child.cget("text")) == text:
            return child
        found = find_button(child, text)
        if found is not None:
            return found
    return None


def all_widget_text(widget) -> list[str]:
    texts: list[str] = []
    try:
        value = widget.cget("text")
    except (tk.TclError, AttributeError):
        value = ""
    if value:
        texts.append(str(value))
    for child in widget.winfo_children():
        texts.extend(all_widget_text(child))
    return texts


class ManualActualGuiVisibleLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
            cls.root.geometry("900x650+0+0")
            cls.root.update_idletasks()
        except tk.TclError as exc:
            raise unittest.SkipTest(f"Tk display unavailable: {exc}")

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "root", None) is not None:
            cls.root.destroy()

    def wait_for_gui(self, predicate, *, timeout=3.0):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.assertTrue(predicate())

    @staticmethod
    def preview_pixels(*_args, **_kwargs):
        pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 120, 80), False)
        pixmap.clear_with(255)
        return pixmap, (10.0, 10.0, 30.0, 30.0), "合成預覽"

    @staticmethod
    def tall_preview_pixels(*_args, **_kwargs):
        pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 120, 160), False)
        pixmap.clear_with(255)
        return pixmap, (10.0, 10.0, 30.0, 30.0), "合成原頁"

    def reveal_sample_target(self, dialog, index):
        preview = dialog.previews[index]
        target = preview.canvas.coords(preview.canvas.find_withtag("target")[-1])
        dialog.body_canvas.reveal(preview.canvas, target[1], target[3])
        dialog.update()

    def test_actual_dialog_uses_staging_label_and_explains_no_immediate_refresh(self):
        current = entry("dialog", "1" * 64)
        group = group_for([current])
        with patch("review_display.occurrence_preview", side_effect=RuntimeError("no preview fixture")):
            dialog = review_gui.ActualReadingDialog(
                self.root,
                current,
                group,
                Path("unused"),
                wait=False,
            )
        try:
            dialog.wait_visibility()
            dialog.update_idletasks()
            self.assertIsNotNone(find_button(dialog, "暫存這筆 actual"))
            self.assertIsNone(find_button(dialog, "儲存 actual 並重新解碼"))
            text = "\n".join(all_widget_text(dialog))
            self.assertIn("不會立即重跑 PDF", text)
            self.assertIn("套用 actual 修正", text)
            self.assertNotIn("expected_set", text)
            self.assertNotIn("字典答案：", text)
        finally:
            dialog.grab_release()
            dialog.destroy()

    def test_actual_dialog_lists_all_locations_and_marks_staged_peers(self):
        ledger = [entry(name, "f" * 64) for name in "abcd"]
        group = group_for(ledger, 2)
        with patch("review_display.occurrence_preview", side_effect=RuntimeError("no preview fixture")):
            dialog = review_gui.ActualReadingDialog(
                self.root, ledger[2], group, Path("unused"),
                verified_checked_occurrence_ids={"a", "b"}, wait=False,
            )
        try:
            self.assertEqual([row["occurrence_id"] for row in dialog.samples], ["c", "d", "a", "b"])
            self.assertEqual(dialog.previously_checked, [False, False, True, True])
            self.assertEqual(len(dialog.checked_vars), 4)
            self.assertIsNone(dialog.previews[1])
            self.assertIsNone(dialog.check_buttons[2])
            text = "\n".join(all_widget_text(dialog))
            self.assertIn("同組先前已直接核對的位置仍保留在暫存", text)
            self.assertIn("本核對群組共有 4 個位置", text)
            self.assertIn("已直接核對並暫存；本次不必重新勾選", text)
            self.assertNotIn("只勾 A 則只修正本位置", text)
        finally:
            dialog.grab_release()
            dialog.destroy()
        with patch("review_display.occurrence_preview", side_effect=RuntimeError("no preview fixture")):
            dialog = review_gui.ActualReadingDialog(
                self.root, ledger[2], group, Path("unused"),
                verified_checked_occurrence_ids={"a", "b", "d"}, wait=False,
            )
        try:
            self.assertEqual([row["occurrence_id"] for row in dialog.samples], ["c", "a", "b", "d"])
            self.assertEqual(len(dialog.checked_vars), 4)
            self.assertEqual(dialog.previously_checked, [False, True, True, True])
            text = "\n".join(all_widget_text(dialog))
            self.assertIn("同組先前已直接核對的位置仍保留在暫存", text)
            self.assertNotIn("occurrence-specific actual 修正", text)
        finally:
            dialog.grab_release()
            dialog.destroy()

    def test_exact_glyph_group_includes_distinct_char_but_marks_it_for_review(self):
        current = entry("current", "a" * 64)
        other_char = entry("other", "a" * 64)
        other_char["char"] = "壯"
        same_char = entry("same", "a" * 64)
        group = group_for([other_char, same_char, current], 2)
        self.assertEqual(len(group["members"]), 3)
        self.assertEqual(
            [sample["occurrence_id"] for sample in review_gui.actual_review_samples(current, group)],
            ["current", "same", "other"],
        )
        with patch("review_display.occurrence_preview", side_effect=RuntimeError("no preview fixture")):
            dialog = review_gui.ActualReadingDialog(
                self.root, current, group, Path("unused"), wait=False,
            )
        try:
            text = "\n".join(all_widget_text(dialog))
            self.assertIn("1 個位置的文字與目前字不同", text)
            self.assertIn("字不同，須個別確認", text)
            self.assertEqual(str(dialog.check_buttons[2].cget("state")), "disabled")
        finally:
            dialog.grab_release()
            dialog.destroy()

    def test_lazy_preview_requires_successful_image_and_explicit_check(self):
        ledger = [entry(name, "f" * 64) for name in "abc"]
        created = []

        def fake_preview(*_args, **_kwargs):
            preview = MagicMock()
            preview.load_result = True
            preview.photo = object()
            preview.target = (10, 10, 30, 30)
            preview.pixmap = object()
            preview._draw_pending = None
            preview._render_pending = None
            preview.canvas.winfo_ismapped.return_value = True
            preview.canvas.find_withtag.return_value = (1,)

            def load(sample, **_options):
                preview._entry = copy.deepcopy(sample)
                return preview.load_result

            preview.load.side_effect = load
            created.append(preview)
            return preview

        with (patch.object(review_gui, "OccurrencePreview", side_effect=fake_preview),
              patch.object(review_gui.ActualReadingDialog, "_target_in_viewport", return_value=True)):
            dialog = review_gui.ActualReadingDialog(
                self.root, ledger[0], group_for(ledger), Path("unused"), wait=False,
            )
            try:
                self.assertEqual(len(dialog.samples), 3)
                self.assertEqual(len(created), 1)  # A only: no eager peer raster.
                self.assertFalse(any(var.get() for var in dialog.checked_vars))
                self.assertEqual(str(dialog.check_buttons[1].cget("state")), "disabled")
                dialog.show_sample_preview(1)
                self.assertEqual(len(created), 2)
                self.assertEqual(str(dialog.check_buttons[1].cget("state")), "disabled")
                dialog.update()
                self.assertEqual(str(dialog.check_buttons[1].cget("state")), "normal")
                dialog.checked_vars[1].set(True)
                dialog.show_sample_preview(2)
                created[1].destroy.assert_called_once()
                self.assertIsNone(dialog.previews[1])
                self.assertEqual(str(dialog.check_buttons[1].cget("state")), "disabled")
                self.assertTrue(dialog.checked_vars[1].get())
                self.assertEqual(len(created), 3)
                self.assertLessEqual(sum(preview is not None for preview in dialog.previews), 2)
                created[2].load_result = False
                dialog.show_sample_preview(2)
                self.assertEqual(str(dialog.check_buttons[2].cget("state")), "disabled")
                dialog.reading.set("ㄓㄨㄢˇ")
                with patch.object(review_gui.messagebox, "showerror") as error:
                    dialog.submit()
                self.assertIsNone(dialog.result)
                self.assertIn("樣本 A", error.call_args.args[1])
                dialog.checked_vars[0].set(True)
                dialog.submit()
                self.assertEqual(dialog.result["checked_occurrence_ids"], ["a", "b"])
            finally:
                if dialog.winfo_exists():
                    dialog.grab_release()
                    dialog.destroy()

    def test_real_tk_check_waits_for_drawn_page_and_target(self):
        ledger = [entry(name, "f" * 64) for name in "abc"]
        with patch("review_display.occurrence_preview", side_effect=self.tall_preview_pixels):
            dialog = review_gui.ActualReadingDialog(
                self.root, ledger[0], group_for(ledger), Path("unused"), wait=False,
            )
            try:
                self.assertFalse(dialog.sample_available[0])
                self.assertEqual(str(dialog.check_buttons[0].cget("state")), "disabled")
                dialog.wait_visibility()
                self.wait_for_gui(lambda: dialog.sample_available[0])
                self.wait_for_gui(lambda: not dialog.previews[0]._needs_locate)
                self.assertIsNotNone(dialog.previews[0].photo)
                self.assertTrue(dialog.previews[0].canvas.find_withtag("page"))
                target = dialog.previews[0].canvas.coords(dialog.previews[0].canvas.find_withtag("target")[-1])
                target_top = dialog.previews[0].canvas.winfo_rooty() + target[1] - dialog.previews[0].canvas.canvasy(0)
                target_bottom = dialog.previews[0].canvas.winfo_rooty() + target[3] - dialog.previews[0].canvas.canvasy(0)
                self.assertGreaterEqual(target_top, dialog.body_canvas.winfo_rooty() - 1)
                self.assertLessEqual(target_bottom, dialog.body_canvas.winfo_rooty() + dialog.body_canvas.winfo_height() + 1)
                self.assertIn("請向下捲到原頁圖片及其下方的勾選框", "\n".join(all_widget_text(dialog)))
                self.assertIn("本群組共 3 個位置", str(dialog.preview_guidance.cget("text")))
                self.assertTrue(dialog.preview_guidance.winfo_viewable())
                dialog.show_sample_preview(1)
                self.assertFalse(dialog.sample_available[1])
                self.assertEqual(str(dialog.check_buttons[1].cget("state")), "disabled")
                self.wait_for_gui(lambda: dialog._preview_drawn_for_sample(1, dialog.previews[1]))
                self.assertFalse(dialog._target_in_viewport(dialog.previews[1]))
                self.assertFalse(dialog.sample_available[1])
                self.reveal_sample_target(dialog, 1)
                self.wait_for_gui(lambda: dialog.sample_available[1])
                self.assertTrue(dialog._viewed_target[1])
                self.wait_for_gui(lambda: not dialog.previews[1]._needs_locate)
                self.assertTrue(dialog.previews[1].canvas.find_withtag("target"))
                dialog.show_sample_preview(2)
                self.assertFalse(dialog._viewed_target[1])
                self.assertFalse(dialog.sample_available[1])
                for size in ("720x520", "980x760"):
                    dialog.geometry(size)
                    dialog.update()
                    dialog.body_canvas.yview_moveto(1)
                    dialog.update()
                    self.assertTrue(dialog.preview_guidance.winfo_viewable())
                    self.assertGreaterEqual(
                        dialog.preview_guidance.winfo_rooty(),
                        dialog.body_canvas.winfo_rooty() + dialog.body_canvas.winfo_height(),
                    )
                    pending = [dialog]
                    note_entry = None
                    while pending:
                        widget = pending.pop()
                        if isinstance(widget, tk.Entry) and widget.cget("textvariable") == str(dialog.note):
                            note_entry = widget
                            break
                        pending.extend(widget.winfo_children())
                    self.assertIsNotNone(note_entry)
                    self.assertGreaterEqual(note_entry.winfo_rooty(), dialog.body_canvas.winfo_rooty())
                    self.assertLessEqual(
                        note_entry.winfo_rooty() + note_entry.winfo_height(),
                        dialog.body_canvas.winfo_rooty() + dialog.body_canvas.winfo_height(),
                    )
                self.assertFalse(any(var.get() for var in dialog.checked_vars))
            finally:
                dialog.grab_release()
                dialog.destroy()

    def test_two_member_dialog_preloads_b_without_accepting_an_implicit_check(self):
        ledger = [entry(name, "f" * 64) for name in "ab"]
        with patch("review_display.occurrence_preview", side_effect=self.tall_preview_pixels):
            dialog = review_gui.ActualReadingDialog(
                self.root, ledger[0], group_for(ledger), Path("unused"), wait=False,
            )
            try:
                self.assertIsNotNone(dialog.previews[0])
                self.assertIsNotNone(dialog.previews[1])
                self.assertFalse(any(var.get() for var in dialog.checked_vars))
                self.assertEqual(str(dialog.check_buttons[1].cget("state")), "disabled")
                dialog.wait_visibility()
                self.wait_for_gui(lambda: dialog.sample_available[0])
                self.wait_for_gui(lambda: dialog._preview_drawn_for_sample(1, dialog.previews[1]))
                self.assertTrue(dialog.previews[1].canvas.find_withtag("page"))
                self.assertTrue(dialog.previews[1].canvas.find_withtag("target"))
                self.assertFalse(dialog._target_in_viewport(dialog.previews[1]))
                self.assertFalse(dialog.sample_available[1])
                dialog.check_buttons[1].focus_set()
                dialog.check_buttons[1].event_generate("<space>")
                dialog.update()
                self.assertFalse(dialog.checked_vars[1].get())
                self.reveal_sample_target(dialog, 1)
                self.wait_for_gui(lambda: dialog.sample_available[1])
                self.assertEqual(str(dialog.check_buttons[1].cget("state")), "normal")
                self.assertFalse(dialog.checked_vars[1].get())
                dialog.checked_vars[1].set(True)
                dialog.show_sample_preview(1)
                self.assertFalse(dialog._viewed_target[1])
                self.assertFalse(dialog.checked_vars[1].get())
                self.assertEqual(str(dialog.check_buttons[1].cget("state")), "disabled")
                self.assertIn("A、B 兩張原頁會先載入", str(dialog.preview_guidance.cget("text")))
            finally:
                dialog.grab_release()
                dialog.destroy()

    def test_unseen_b_cannot_be_submitted_by_setting_checkbox_variable(self):
        ledger = [entry(name, "f" * 64) for name in "ab"]
        with patch("review_display.occurrence_preview", side_effect=self.tall_preview_pixels):
            dialog = review_gui.ActualReadingDialog(
                self.root, ledger[0], group_for(ledger), Path("unused"), wait=False,
            )
            try:
                dialog.wait_visibility()
                self.wait_for_gui(lambda: dialog.sample_available[0])
                self.wait_for_gui(lambda: dialog._preview_drawn_for_sample(1, dialog.previews[1]))
                self.assertFalse(dialog._target_in_viewport(dialog.previews[1]))
                dialog.checked_vars[0].set(True)
                dialog.checked_vars[1].set(True)  # B was never visible or checked by the user.
                dialog.reading.set("ㄓㄨㄢˇ")
                dialog.submit()
                self.assertEqual(dialog.result["checked_occurrence_ids"], ["a"])
            finally:
                if dialog.winfo_exists():
                    dialog.grab_release()
                    dialog.destroy()

    def test_previously_staged_peer_does_not_require_a_new_view_or_checkbox(self):
        ledger = [entry(name, "f" * 64) for name in "ab"]
        with patch("review_display.occurrence_preview", side_effect=self.tall_preview_pixels):
            dialog = review_gui.ActualReadingDialog(
                self.root, ledger[0], group_for(ledger), Path("unused"),
                verified_checked_occurrence_ids={"b"}, wait=False,
            )
            try:
                dialog.wait_visibility()
                self.wait_for_gui(lambda: dialog.sample_available[0])
                self.assertEqual(dialog.previously_checked, [False, True])
                self.assertIsNone(dialog.check_buttons[1])
                self.assertFalse(dialog._viewed_target[1])
                self.assertFalse(dialog.sample_available[1])
                dialog.checked_vars[0].set(True)
                dialog.reading.set("ㄓㄨㄢˇ")
                dialog.submit()
                self.assertEqual(dialog.result["checked_occurrence_ids"], ["a"])
            finally:
                if dialog.winfo_exists():
                    dialog.grab_release()
                    dialog.destroy()

    def test_scroll_visibility_callback_is_cancelled_during_dialog_teardown(self):
        current = entry("only", "f" * 64)
        with patch("review_display.occurrence_preview", side_effect=self.preview_pixels):
            dialog = review_gui.ActualReadingDialog(
                self.root, current, group_for([current]), Path("unused"), wait=False,
            )
            try:
                dialog.wait_visibility()
                dialog._body_scrollbar.destroy()
                dialog._body_scrolled("0", "1")  # Geometry can outlive its scrollbar during teardown.
                dialog._schedule_visibility_check()
                self.assertIsNotNone(dialog._visibility_after)
                dialog.grab_release()
                dialog.destroy()
                self.assertIsNone(dialog._visibility_after)
                self.root.update()  # Pending layout and scrollbar callbacks must not raise TclError.
                dialog._body_scrolled("0", "1")
            finally:
                if dialog.winfo_exists():
                    dialog.grab_release()
                    dialog.destroy()

    def test_target_without_drawn_photo_times_out_and_cannot_be_checked(self):
        current = entry("only", "f" * 64)
        with (
            patch("review_display.occurrence_preview", side_effect=self.preview_pixels),
            patch.object(review_gui.OccurrencePreview, "_draw", lambda _preview: None),
            patch.object(review_gui.ActualReadingDialog, "_PREVIEW_READY_RETRIES", 1),
        ):
            dialog = review_gui.ActualReadingDialog(
                self.root, current, group_for([current]), Path("unused"), wait=False,
            )
            try:
                self.assertIsNotNone(dialog.previews[0].target)
                dialog.wait_visibility()
                self.wait_for_gui(lambda: "原頁未顯示" in str(dialog.preview_buttons[0].cget("text")))
                self.assertIsNone(dialog.previews[0].photo)
                self.assertFalse(dialog.sample_available[0])
                self.assertEqual(str(dialog.check_buttons[0].cget("state")), "disabled")
                dialog.checked_vars[0].set(True)
                dialog.reading.set("ㄓㄨㄢˇ")
                with patch.object(review_gui.messagebox, "showerror") as error:
                    dialog.submit()
                self.assertIsNone(dialog.result)
                self.assertIn("樣本 A", error.call_args.args[1])
            finally:
                dialog.grab_release()
                dialog.destroy()

    def test_main_batch_button_is_visible_and_not_clipped_at_screen_safe_size(self):
        app_root = tk.Toplevel(self.root)
        try:
            with (
                patch.object(review_gui, "json_load_strict", return_value={"manifest": "fixture"}),
                patch.object(review_gui, "validate_manifest_integrity"),
                patch.object(review_gui, "validate_output_artifact_hashes"),
                patch.object(review_gui, "load_or_initialize_db", return_value={"events": {}}),
                patch.object(review_gui, "materialize_ledger", return_value=[]),
                patch.object(review_gui, "manual_actual_staging_summary", return_value={
                    "staged_group_count": 2,
                    "staged_group_ids": ["one", "two"],
                    "staged_member_occurrence_ids": ["a", "b"],
                }),
                patch.object(review_gui.messagebox, "showinfo"),
            ):
                app = review_gui.ReviewApp(app_root, Path("fixture-output"))
            app_root.wait_visibility()
            app_root.update_idletasks()
            button = find_button(app_root, "套用 actual 修正（2）")
            self.assertIsNotNone(button)
            self.assertEqual(str(button.cget("state")), "normal")
            self.assertTrue(button.winfo_viewable())
            root_bottom = app_root.winfo_rooty() + app_root.winfo_height()
            button_bottom = button.winfo_rooty() + button.winfo_height()
            self.assertLessEqual(button_bottom, root_bottom)
            self.assertGreater(button.winfo_height(), 1)
            self.assertEqual(app.staging_summary["staged_group_count"], 2)
        finally:
            app_root.destroy()


if __name__ == "__main__":
    unittest.main()
