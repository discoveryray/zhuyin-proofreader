from __future__ import annotations

import csv
import copy
import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import fitz
from openpyxl import load_workbook

from actual_review import (
    USER_GLYF_FILE,
    USER_CFF_FILE,
    build_actual_review_groups,
    build_actual_group_for_entry,
    apply_verified_actual_group,
    export_actual_review_package,
    import_actual_review_workbook,
    ensure_user_evidence_files,
    dynamic_actual_hashes,
    load_user_verified_cff,
    _resolve_pdf_path,
    _publish_actual_review_package,
    render_annotation_only_png,
    render_occurrence_png,
)


O1 = "occ_" + hashlib.sha256(b"o1").hexdigest()
O2 = "occ_" + hashlib.sha256(b"o2").hexdigest()

SHA = "a" * 64


def make_pdf(path: Path):
    doc = fitz.open()
    page = doc.new_page(width=300, height=300)
    page.insert_text((60, 100), "ABC", fontsize=24)
    doc.save(path)
    doc.close()


def entry(oid: str, rid: str, pdf: Path, x: float, *, state="ACTUAL_DECODE_ERROR", source=None):
    src = {
        "TTF字形SHA256": SHA,
        "穩定注音鍵": f"key-{oid}",
    }
    if source:
        src.update(source)
    return {
        "occurrence_id": oid,
        "review_id": rid,
        "state": state,
        "pdf": str(pdf),
        "pdf_name": pdf.name,
        "physical_page": 1,
        "printed_page": "1",
        "char": "字",
        "stable_key": f"key-{oid}",
        "x0": x,
        "y0": 70,
        "x1": x + 20,
        "y1": 110,
        "actual": "",
        "actual_evidence": "decoder unresolved",
        "source_record": src,
    }


class ActualReviewV540Tests(unittest.TestCase):
    def test_export_deep_stage_paths_preserve_rendered_png_bytes(self):
        temporary_root = Path.cwd() / "tmp"
        temporary_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="bbox-long-", dir=temporary_root) as td:
            root = Path(td)
            output = root / ("p" * (171 - len(str(root.resolve())) - 1))
            output.mkdir()
            ledger, package, archive, meta = self._export_fixture(output)
            group = build_actual_review_groups(ledger)[0]
            expected = {}
            for label, row in zip(("a", "b"), ledger):
                for kind, renderer in (("annotation", render_annotation_only_png), ("whole_glyph", render_occurrence_png)):
                    name = f"{group['group_id']}_{label}_{kind}.png"
                    old_stage_path = output / ".actual-gpt-stage-12345678" / package.name / "images" / name
                    self.assertGreaterEqual(len(str(old_stage_path.resolve())), 260)
                    short = root / name
                    renderer(row, short)
                    expected[name] = short.read_bytes()
            self.assertEqual(export_actual_review_package(output, ledger, **meta), archive)
            for name, data in expected.items():
                exported = package / "images" / name
                self.assertEqual(exported.read_bytes(), data)
                with fitz.open(exported) as image:
                    pixmap = image[0].get_pixmap()
                    self.assertGreater(pixmap.width, 0)
                    self.assertGreater(pixmap.height, 0)

    def test_export_cleanup_failure_does_not_change_publication_or_original_failure(self):
        for render_failure in (False, True):
            with self.subTest(render_failure=render_failure), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                ledger, package, archive, meta = self._export_fixture(root)
                with patch("actual_review.shutil.rmtree", side_effect=OSError("cleanup lock")):
                    if render_failure:
                        with patch("actual_review.render_occurrence_png", side_effect=OSError("original render failure")):
                            with self.assertRaisesRegex(OSError, "original render failure"):
                                export_actual_review_package(root, ledger, **meta)
                        self._assert_old_export(package, archive)
                    else:
                        self.assertEqual(export_actual_review_package(root, ledger, **meta), archive)
                        self.assertTrue((package / "actual待判定_給GPT.xlsx").is_file())
                        with zipfile.ZipFile(archive) as zf:
                            self.assertIsNone(zf.testzip())
                        retained = list(root.glob(".ag-*/previous-0/old.txt"))
                        self.assertEqual(len(retained), 1)
                        self.assertEqual(retained[0].read_bytes(), b"old complete package")

    def test_export_pdf_relocation_requires_exact_adjacent_sha(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            original = root / "original" / "book.pdf"
            original.parent.mkdir()
            make_pdf(original)
            copy_pdf = root / "copy" / "book.pdf"
            copy_pdf.parent.mkdir()
            copy_pdf.write_bytes(original.read_bytes())
            output = copy_pdf.parent / "output"
            row = entry(O1, "r1", original, 60)
            row["pdf_sha256"] = hashlib.sha256(original.read_bytes()).hexdigest()
            frozen = copy.deepcopy(row)
            self.assertEqual(_resolve_pdf_path(row, output), copy_pdf)
            copy_pdf.write_bytes(b"changed bytes")
            with self.assertRaisesRegex(ValueError, "SHA"):
                _resolve_pdf_path(row, output)
            self.assertEqual(row, frozen)

    def test_export_rollback_failure_retains_old_bytes_for_recovery(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ledger, package, archive, meta = self._export_fixture(root)
            stage = root / "stage"
            stage.mkdir()
            new_package = stage / package.name
            new_package.mkdir()
            (new_package / "new.txt").write_bytes(b"new package")
            (stage / archive.name).write_bytes(b"new zip")
            replace = Path.replace
            def fail_publish_and_restore(path, destination):
                if path == stage / archive.name or path == stage / "previous-0":
                    raise OSError("injected restore lock")
                return replace(path, destination)
            with patch.object(Path, "replace", fail_publish_and_restore):
                with self.assertRaisesRegex(OSError, "需人工回復"):
                    _publish_actual_review_package(root, stage)
            self.assertEqual((stage / "previous-0" / "old.txt").read_bytes(), b"old complete package")
            self.assertEqual(archive.read_bytes(), b"old archive")

    def _export_fixture(self, root):
        pdf = root / "book.pdf"
        make_pdf(pdf)
        ledger = [entry(O1, "r1", pdf, 60), entry(O2, "r2", pdf, 120, state="PASS")]
        package = root / "actual待判定_GPT包"
        package.mkdir()
        (package / "old.txt").write_bytes(b"old complete package")
        archive = root / "actual待判定_GPT包.zip"
        archive.write_bytes(b"old archive")
        meta = dict(version="5.4.0", session_id="sess", session_schema_version="2.5.0",
                    workbook_schema_version="2.5.0", review_id_schema_version="2.5.0")
        return ledger, package, archive, meta

    def _assert_old_export(self, package, archive):
        self.assertEqual(sorted(p.name for p in package.iterdir()), ["old.txt"])
        self.assertEqual((package / "old.txt").read_bytes(), b"old complete package")
        self.assertEqual(archive.read_bytes(), b"old archive")

    def test_export_preflight_reports_all_selected_zero_width_including_nonpending(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ledger, package, archive, meta = self._export_fixture(root)
            for row in ledger:
                row["x1"] = row["x0"]
            frozen = copy.deepcopy(ledger)
            with patch("actual_review.render_annotation_only_png") as renderer:
                with self.assertRaises(ValueError) as caught:
                    export_actual_review_package(root, ledger, **meta)
            self.assertIn(O1, str(caught.exception))
            self.assertIn(O2, str(caught.exception))
            self.assertIn("r1", str(caught.exception))
            self.assertIn("r2", str(caught.exception))
            self.assertIn("A", str(caught.exception))
            self.assertIn("B", str(caught.exception))
            renderer.assert_not_called()
            self.assertEqual(ledger, frozen)
            self._assert_old_export(package, archive)

    def test_export_preflight_rejects_nonfinite_or_outside_page(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ledger, package, archive, meta = self._export_fixture(root)
            ledger[0]["x0"] = float("nan")
            ledger[1]["physical_page"] = 2
            with self.assertRaises(ValueError) as caught:
                export_actual_review_package(root, ledger, **meta)
            self.assertIn(O1, str(caught.exception))
            self.assertIn(O2, str(caught.exception))
            self._assert_old_export(package, archive)

    def test_export_render_excel_and_zip_failures_preserve_previous_pair(self):
        for target in ("actual_review.render_occurrence_png", "actual_review.Workbook.save", "actual_review.zipfile.ZipFile.write"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                ledger, package, archive, meta = self._export_fixture(root)
                original_write = zipfile.ZipFile.write
                def fail_zip_write(archive, *args, **kwargs):
                    if str(archive.filename).endswith(".zip"):
                        raise OSError("injected ZIP failure")
                    return original_write(archive, *args, **kwargs)
                injection = patch(target, fail_zip_write) if target.endswith("ZipFile.write") else patch(target, side_effect=OSError("injected stage failure"))
                with injection:
                    with self.assertRaises(OSError):
                        export_actual_review_package(root, ledger, **meta)
                self._assert_old_export(package, archive)

    def test_export_publish_failure_restores_previous_directory_and_zip(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ledger, package, archive, meta = self._export_fixture(root)
            replace = Path.replace
            def fail_new_archive(path, destination):
                if path.name == "actual待判定_GPT包.zip" and path.parent != root:
                    raise OSError("injected publish failure")
                return replace(path, destination)
            with patch.object(Path, "replace", fail_new_archive):
                with self.assertRaises(OSError):
                    export_actual_review_package(root, ledger, **meta)
            self._assert_old_export(package, archive)

    def test_export_success_preserves_selection_identity_and_matches_zip(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ledger, package, archive, meta = self._export_fixture(root)
            frozen = copy.deepcopy(ledger)
            group = build_actual_review_groups(ledger)[0]
            self.assertEqual(export_actual_review_package(root, ledger, **meta), archive)
            wb = load_workbook(package / "actual待判定_給GPT.xlsx")
            try:
                ws = wb["actual待判定"]
                row = dict(zip([c.value for c in ws[1]], [c.value for c in ws[2]]))
                self.assertEqual(row["group_id"], group["group_id"])
                self.assertEqual(row["group_snapshot"], group["group_snapshot"])
                self.assertEqual(row["sample_a_occurrence_id"], O1)
                self.assertEqual(row["sample_b_occurrence_id"], O2)
            finally:
                wb.close()
            with zipfile.ZipFile(archive) as zf:
                files = {p.relative_to(package).as_posix(): p.read_bytes() for p in package.rglob("*") if p.is_file()}
                self.assertEqual(set(zf.namelist()), set(files))
                for name, data in files.items():
                    self.assertEqual(zf.read(name), data)
            self.assertEqual(ledger, frozen)

    def test_exact_ttf_groups_and_promotes_only_with_two_examples(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ensure_user_evidence_files(root)
            pdf = root / "book.pdf"
            make_pdf(pdf)
            a = entry(O1, "r1", pdf, 60)
            b = entry(O2, "r2", pdf, 120)
            group = build_actual_review_groups([a, b])[0]
            self.assertEqual(group["kind"], "TTF_GLYF_SHA256")
            self.assertEqual(group["occurrence_count"], 2)

            first = apply_verified_actual_group(root, group, "ㄒㄧ˙", checked_occurrence_ids=[O1], source="test")
            self.assertEqual(first["reading"], "˙ㄒㄧ")
            self.assertEqual(first["learning_level"], "USER_VERIFIED_SINGLE")
            self.assertFalse(first["propagated_to_group"])

            second = apply_verified_actual_group(root, group, "˙ㄒㄧ", checked_occurrence_ids=[O1, O2], source="test")
            self.assertEqual(second["learning_level"], "VERIFIED_EXACT_GLYPH")
            self.assertTrue(second["propagated_to_group"])
            with (root / USER_GLYF_FILE).open(encoding="utf-8-sig", newline="") as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual(rows[0]["source_count"], "2")
            self.assertEqual(rows[0]["bopomofo"], "˙ㄒㄧ")

    def test_user_flagged_non_actual_state_can_build_group(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pdf = root / "book.pdf"
            make_pdf(pdf)
            a = entry(O1, "r1", pdf, 60, state="DIFFERENCE_PENDING_CONFIRMATION")
            b = entry(O2, "r2", pdf, 120, state="PASS")
            group = build_actual_group_for_entry([a, b], a)
            self.assertEqual(group["occurrence_count"], 2)
            self.assertEqual(group["kind"], "TTF_GLYF_SHA256")

    def test_cff_learning_uses_full_glyph_sha_not_annotation_signature(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pdf = root / "book.pdf"
            make_pdf(pdf)
            shared_annotation = {
                "TTF字形SHA256": "",
                "CFF樣式群組": "KAICHU_MD",
                "CFF符號簽名": "body1;body2",
                "CFF聲調簽名": "same-tone",
                "CFF輕聲簽名": "",
                "CFF完整注音簽名": "same-annotation-full-signature",
            }
            a = entry(O1, "r1", pdf, 60, source={**shared_annotation, "CFF整字字形SHA256": "b" * 64})
            b = entry(O2, "r2", pdf, 120, source={**shared_annotation, "CFF整字字形SHA256": "c" * 64})
            groups = build_actual_review_groups([a, b])
            self.assertEqual(len(groups), 2, "same annotation signature must not merge different complete CFF glyphs")
            self.assertTrue(all(g["kind"] == "CFF_GLYPH_SHA256" for g in groups))

    def test_cff_same_full_glyph_sha_can_group_for_double_visual_confirmation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pdf = root / "book.pdf"
            make_pdf(pdf)
            src = {
                "TTF字形SHA256": "",
                "CFF樣式群組": "KAICHU_MD",
                "CFF符號簽名": "body1;body2",
                "CFF聲調簽名": "tone",
                "CFF整字字形SHA256": "d" * 64,
            }
            a = entry(O1, "r1", pdf, 60, source=src)
            b = entry(O2, "r2", pdf, 120, source=src)
            groups = build_actual_review_groups([a, b])
            self.assertEqual(len(groups), 1)
            self.assertEqual(groups[0]["kind"], "CFF_GLYPH_SHA256")
            self.assertEqual(groups[0]["occurrence_count"], 2)

    def test_gpt_package_has_no_expected_columns_and_imports_transactionally(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ensure_user_evidence_files(root)
            pdf = root / "book.pdf"
            make_pdf(pdf)
            a = entry(O1, "r1", pdf, 60)
            b = entry(O2, "r2", pdf, 120)
            ledger = [a, b]
            meta = {
                "version": "5.4.0",
                "session_id": "sess",
                "session_schema_version": "2.5.0",
                "workbook_schema_version": "2.5.0",
                "review_id_schema_version": "2.5.0",
            }
            zip_path = export_actual_review_package(root, ledger, **meta)
            self.assertTrue(zip_path.exists())
            xlsx = root / "actual待判定_GPT包" / "actual待判定_給GPT.xlsx"
            wb = load_workbook(xlsx)
            ws = wb["actual待判定"]
            headers = [c.value for c in ws[1]]
            self.assertFalse(any("expected" in str(h).lower() or "預期" in str(h) or "應標" in str(h) for h in headers))
            self.assertFalse(any(str(h).endswith("_char") for h in headers), "GPT actual sheet must not expose target Han character columns")
            self.assertIn("annotation", str(ws.cell(2, headers.index("sample_a_image") + 1).value))
            self.assertNotIn("context", str(ws.cell(2, headers.index("sample_a_image") + 1).value))
            ix = {h: i + 1 for i, h in enumerate(headers)}
            ws.cell(2, ix["decision"], "VERIFIED")
            ws.cell(2, ix["actual_reading"], "ㄒㄧ˙")
            ws.cell(2, ix["confidence"], "高")
            ws.cell(2, ix["sample_a_checked"], "Y")
            ws.cell(2, ix["sample_b_checked"], "Y")
            wb.save(xlsx)
            wb.close()

            groups = build_actual_review_groups(ledger)
            result = import_actual_review_workbook(root, xlsx, groups, expected_metadata=meta)
            self.assertEqual(result["imported_groups"], 1)
            with (root / USER_GLYF_FILE).open(encoding="utf-8-sig", newline="") as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual(rows[0]["verification_level"], "VERIFIED_EXACT_GLYPH")

    def test_cff_verified_truth_is_keyed_by_full_glyph_sha(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ensure_user_evidence_files(root)
            p = root / USER_CFF_FILE
            p.write_text(
                "style_group,glyph_sha256,full_signature,bopomofo,verification_level,source_count,source_examples,updated_at,notes\n"
                + f"KAICHU_MD,{('e'*64)},shared-annotation,ㄋㄚˇ,VERIFIED_EXACT_GLYPH,2,o1|o2,2026-08-20,test\n",
                encoding="utf-8-sig",
            )
            loaded = load_user_verified_cff(p)
            self.assertIn(("KAICHU_MD", "e" * 64), loaded)
            self.assertEqual(loaded[("KAICHU_MD", "e" * 64)]["bopomofo"], "ㄋㄚˇ")
            self.assertNotIn(("KAICHU_MD", "shared-annotation"), loaded)

    def test_v540_migrates_only_the_known_bad_p122_override(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            override = root / "manual_actual_occurrence_overrides.csv"
            override.write_text(
                "pdf_contains,pdf_excludes,page,target_char,stable_key,x0,y0,actual_reading,source,note\n"
                "15_國小健體3下課本_單元5第2課_(學),,122,那,CFF:DFKaiChuIn-Md-BPMF-BF#1311,142.01,76.595,ㄋㄚˋ,使用者原頁回標＋GPT原頁視覺覆核（2026-08-20；出現位置限定）,bad\n"
                "other,,1,字,key,1,2,ㄗ,人工自訂,keep\n",
                encoding="utf-8-sig",
            )
            ensure_user_evidence_files(root)
            with override.open(encoding="utf-8-sig", newline="") as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["pdf_contains"], "other")

    def test_dynamic_hash_changes_when_user_truth_changes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ensure_user_evidence_files(root)
            first = dynamic_actual_hashes(root)
            p = root / USER_GLYF_FILE
            p.write_text(
                "glyph_sha256,bopomofo,verification_level,source_count,source_examples,updated_at,notes\n"
                + f"{SHA},ㄒㄧ,USER_VERIFIED_SINGLE,1,o1,2026-08-20,test\n",
                encoding="utf-8-sig",
            )
            second = dynamic_actual_hashes(root)
            self.assertNotEqual(first[USER_GLYF_FILE], second[USER_GLYF_FILE])


if __name__ == "__main__":
    unittest.main()
