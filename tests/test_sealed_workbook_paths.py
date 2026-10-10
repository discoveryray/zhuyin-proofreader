"""Tiny selected-copy locator/recovery tests. No PDF renderer or Tk."""
import base64
import copy
import hashlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pdf_portability as p
import standalone_proofread as sp
import sealed_workbook_paths as locations


class SealedWorkbookPathsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.origin = self.base / "old project name"
        self.selected = self.base / "selected moved copy"
        self.origin.mkdir()
        self.info = {"pdf_name": "source.pdf", "pdf_sha256": "a" * 64}
        for kind, role in locations.ROLES.items():
            path = self.origin / role / (kind + ".xlsx")
            path.parent.mkdir()
            path.write_bytes(("sealed " + kind).encode())
            self.info[kind + "_workbook"] = str(path)
            self.info[kind + "_workbook_sha256"] = sp.sha256_file(path)
        self.manifest = sp.seal_manifest({"session_id": "unchanged", "pdfs": [self.info],
            "records": [{"occurrence_id": "occ-exact", "review_id": "rev-exact",
                "actual_workbook": self.info["actual_workbook"]}]})
        (self.origin / "校對工作階段.json").write_text(json.dumps(self.manifest), encoding="utf-8")
        shutil.copytree(self.origin, self.selected)

    def local(self, kind="actual"):
        return self.selected / locations.ROLES[kind] / (kind + ".xlsx")

    def test_normal_project_and_both_roles(self):
        rows = locations.bind_workbooks(self.origin, self.manifest)
        self.assertEqual(len(rows), 2)
        for kind in locations.ROLES:
            self.assertEqual(p._artifact(self.origin, self.info, kind), self.origin / locations.descriptor(self.info, kind))

    def test_moved_copy_origin_present_reads_only_selected(self):
        with patch.object(locations, "_digest", wraps=locations._digest) as digest:
            sp.validate_output_artifact_hashes(self.manifest, output_dir=self.selected)
            for kind in locations.ROLES:
                self.assertEqual(p._artifact(self.selected, self.info, kind), self.local(kind))
            self.assertTrue(all(call.args[0].is_relative_to(self.selected) for call in digest.call_args_list))

    def test_moved_copy_origin_absent_and_relative_descriptor(self):
        shutil.rmtree(self.origin)
        sp.validate_output_artifact_hashes(self.manifest, output_dir=self.selected)
        other = copy.deepcopy(self.manifest)
        for kind in locations.ROLES:
            other["pdfs"][0][kind + "_workbook"] = str(locations.descriptor(self.info, kind))
        sp.seal_manifest(other)
        self.assertEqual(len(locations.bind_workbooks(self.selected, other)), 2)

    def test_generic_pdf_names_and_multiple_workbook_counts(self):
        other = copy.deepcopy(self.manifest)
        info = {"pdf_name": "different textbook 02.pdf", "pdf_sha256": "c" * 64}
        for kind, role in locations.ROLES.items():
            name = "different textbook 02-" + kind + ".xlsx"
            info[kind + "_workbook"] = str(self.origin / role / name)
            path = self.selected / role / name
            path.write_bytes((name + " unique sealed bytes").encode())
            info[kind + "_workbook_sha256"] = sp.sha256_file(path)
        other["pdfs"].append(info); sp.seal_manifest(other)
        rows = locations.bind_workbooks(self.selected, other)
        self.assertEqual(len(rows), 4)
        self.assertEqual(len({relative for index, kind, relative, sha in rows}), 4)
        self.assertEqual(locations.workbook_path(self.selected, other, info, "candidate"),
                         self.selected / locations.descriptor(info, "candidate"))

    def test_same_sha_duplicate_never_uses_filename_priority(self):
        self.local().with_name("other.xlsx").write_bytes(self.local().read_bytes())
        with self.assertRaisesRegex(ValueError, "不唯一"):
            locations.bind_workbooks(self.selected, self.manifest)

    def test_missing_copy_does_not_return_present_origin(self):
        self.local().unlink()
        with self.assertRaises(ValueError):
            p._artifact(self.selected, self.info, "actual")
        self.assertTrue(Path(self.info["actual_workbook"]).exists())

    def test_wrong_sha_and_renamed_internal_workbook_rejected(self):
        original = self.local().read_bytes()
        self.local().write_bytes(b"changed")
        with self.assertRaises(ValueError): locations.bind_workbooks(self.selected, self.manifest)
        self.local().write_bytes(original)
        self.local().rename(self.local().with_name("renamed.xlsx"))
        with self.assertRaises(ValueError): locations.bind_workbooks(self.selected, self.manifest)

    def test_unknown_role_traversal_and_escaped_relative_rejected(self):
        for value in ("../01_實際注音/actual.xlsx", "other/actual.xlsx", "C:/old/other/actual.xlsx", "01_實際注音/../../actual.xlsx"):
            with self.subTest(value=value):
                changed = copy.deepcopy(self.manifest)
                changed["pdfs"][0]["actual_workbook"] = value
                sp.seal_manifest(changed)
                with self.assertRaises(ValueError): locations.bind_workbooks(self.selected, changed)

    def test_unsealed_and_duplicate_source_identity_rejected(self):
        changed = copy.deepcopy(self.manifest); changed["session_id"] = "foreign"
        with self.assertRaises(ValueError): locations.bind_workbooks(self.selected, changed)

    def test_hardlinked_workbook_is_not_writable_copy(self):
        target = self.local()
        target.unlink()
        os.link(Path(self.info["actual_workbook"]), target)
        with self.assertRaisesRegex(ValueError, "獨立一般"):
            locations.bind_workbooks(self.selected, self.manifest)

    def test_linked_role_directory_is_rejected(self):
        role = self.local().parent
        real = Path.is_symlink
        with patch.object(Path, "is_symlink", lambda path: path == role or real(path)):
            with self.assertRaisesRegex(ValueError, "link/junction"):
                locations.bind_workbooks(self.selected, self.manifest)

    def test_root_junction_rejected_before_entry_resolve_or_write(self):
        real = Path.is_junction
        writes = []
        @locations.bound_operation
        def operation(output_dir):
            writes.append(output_dir.resolve())
        with patch.object(Path, "is_junction", lambda path: path == self.selected or real(path)):
            with self.assertRaisesRegex(ValueError, "selected root"):
                operation(self.selected)
        self.assertEqual(writes, [])

    def test_cached_private_lookup_rejects_mutated_manifest_seal(self):
        with locations.binding_scope():
            locations.bind_workbooks(self.selected, self.manifest)
            self.manifest["session_id"] = "mutated with old recorded seal"
            with self.assertRaisesRegex(ValueError, "payload hash"):
                locations.workbook_path(self.selected, self.manifest, self.info, "actual")
        changed = copy.deepcopy(self.manifest); changed["pdfs"].append(copy.deepcopy(self.info)); sp.seal_manifest(changed)
        with self.assertRaises(ValueError): locations.bind_workbooks(self.selected, changed)

    def test_frozen_binding_does_not_rescan_after_sha_change(self):
        with locations.binding_scope():
            locations.bind_workbooks(self.selected, self.manifest)
            self.local().with_name("alternate.xlsx").write_bytes(self.local().read_bytes())
            self.local().write_bytes(b"updated")
            with patch.object(Path, "glob", side_effect=AssertionError("must not rescan")):
                with self.assertRaisesRegex(ValueError, "frozen"):
                    locations.bind_workbooks(self.selected, self.manifest)
                with self.assertRaisesRegex(ValueError, "frozen"):
                    locations.workbook_path(self.selected, self.manifest, self.info, "actual")

    def test_actual_only_then_both_roles_keeps_frozen_actual(self):
        with locations.binding_scope():
            locations.bind_workbooks(self.selected, self.manifest, require_candidate=False)
            self.local().rename(self.local().with_name("alternate.xlsx"))
            with self.assertRaisesRegex(ValueError, "frozen"):
                locations.bind_workbooks(self.selected, self.manifest)

    def test_rebase_updates_only_copy_locators_and_preserves_identity(self):
        destination = self.base / "stage"
        original = copy.deepcopy(self.manifest)
        rebased = sp._refresh_rebase_manifest(self.manifest, self.selected, destination)
        self.assertEqual(self.manifest, original)
        self.assertEqual(rebased["session_id"], original["session_id"])
        self.assertEqual(rebased["records"][0]["occurrence_id"], "occ-exact")
        self.assertEqual(rebased["records"][0]["actual_workbook"], str(destination / locations.descriptor(self.info, "actual")))

    def test_snapshot_old_adapter_and_new_contract_keep_same_locations(self):
        snapshot = p._sealed_workbook_snapshot(self.selected, self.manifest)
        new = locations.snapshot_entries(self.selected, self.manifest, snapshot)
        legacy = copy.deepcopy(snapshot); legacy.pop("location_contract")
        self.assertEqual(new, locations.snapshot_entries(self.selected, self.manifest, legacy))
        snapshot["entries"][0]["path"] = "02_候選報告/actual.xlsx"
        with self.assertRaises(ValueError): locations.snapshot_entries(self.selected, self.manifest, snapshot)
        snapshot = p._sealed_workbook_snapshot(self.selected, self.manifest)
        for unknown in ("future", None):
            with self.subTest(contract=unknown):
                snapshot["location_contract"] = unknown
                with self.assertRaises(ValueError): locations.snapshot_entries(self.selected, self.manifest, snapshot)

    def test_restore_updated_sha_uses_recorded_copy_only_not_rescan(self):
        snapshot = p._sealed_workbook_snapshot(self.selected, self.manifest)
        original = Path(self.info["actual_workbook"]).read_bytes()
        self.local().with_name("alternate.xlsx").write_bytes(original)
        self.local().write_bytes(b"postcommit updated bytes")
        sha = "b" * 64
        plan = {"checked_postconditions": [{"occurrence_id": "occ-exact", "reading": "ㄧ", "group_id": "group"}]}
        marker = {"sealed_workbooks": snapshot, "pre_state": {"manifest": sp.sha256_file(self.selected / "校對工作階段.json")},
                  "source_excel_sha256": sha, "recovery_plan_sha256": hashlib.sha256(p._canonical(plan)).hexdigest()}
        override = {"actual_reading": "ㄧ", "note": json.dumps({"source_excel_sha256": sha, "source_group_id": "group"}),
                    **{key: "" for key in ("pdf_contains", "pdf_excludes", "page", "target_char", "stable_key", "x0", "y0")}}
        with patch("actual_review._read_csv", return_value=[override]), patch("actual_review._override_key_from_entry", return_value=("",)*7), \
             patch.object(Path, "glob", side_effect=AssertionError("recovery must not rescan")):
            p._restore_sealed_workbooks_for_actual_resume(self.selected, marker, (None, plan))
        self.assertEqual(self.local().read_bytes(), original)
        self.assertEqual(Path(self.info["actual_workbook"]).read_bytes(), original)
        self.assertEqual(self.local().with_name("alternate.xlsx").read_bytes(), original)

    def test_restore_snapshot_path_cannot_escape(self):
        snapshot = p._sealed_workbook_snapshot(self.selected, self.manifest)
        snapshot["entries"][0]["path"] = "../outside.xlsx"
        with self.assertRaises(ValueError): locations.snapshot_entries(self.selected, self.manifest, snapshot)


if __name__ == "__main__":
    unittest.main()
