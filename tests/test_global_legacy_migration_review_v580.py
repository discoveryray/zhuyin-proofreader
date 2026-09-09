"""Independent-review regressions, including the actual pinned baseline reader."""
from __future__ import annotations

from contextlib import closing, nullcontext, redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from openpyxl import load_workbook

import actual_review as review
import global_exact_glyph_library as lib
import legacy_global_migration as migration
import migrate_legacy_global_candidates as cli
from test_global_legacy_migration_v580 import Project, ROOT, seal, sha, tree_bytes
from test_global_promotion_v580 import FakeDocument, approve, intent, sql_rows
from test_exact_glyph_identity_v580 import composite_glyph, synthetic_sfnt


BASELINE = "d2d558808bd2902857fad30f824d8bdc352beb73"
NOW = "2026-09-09T00:00:00Z"


def store_evidence_bytes(repo):
    # SQLite may create/remove SHM and an empty WAL on a read-only open. SHM is
    # the transient lock/index, not durable evidence. Compare the main DB, every
    # nonempty WAL byte and any other file; logical tables are also checked below.
    return {name: raw for name, raw in tree_bytes(repo.path.parent).items()
            if name != repo.path.name + "-shm" and not (name == repo.path.name + "-wal" and not raw)}


class MigrationReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="migration-review-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)

    def assert_source_blocked(self, project, message):
        source_before = tree_bytes(project.root)
        for existing in (False, True):
            with self.subTest(existing=existing):
                repo = lib.GlobalExactGlyphRepository.resolved(self.base / str(existing))
                if existing:
                    lib.deliver_global_direct_evidence(repo, [intent()])
                global_before = tree_bytes(repo.path.parent) if existing else {}
                with patch.object(lib.GlobalExactGlyphRepository, "initialize", side_effect=AssertionError), \
                     patch.object(lib, "_ingest_migration", side_effect=AssertionError), \
                     self.assertRaisesRegex(migration.MigrationValidationError, message):
                    migration.migrate_project(project.root, global_library_root=repo.path.parent, apply=True)
                self.assertEqual(tree_bytes(project.root), source_before)
                if existing:
                    self.assertEqual(tree_bytes(repo.path.parent), global_before)
                    self.assertEqual(sql_rows(repo, "migration_candidate"), [])
                else:
                    self.assertFalse(repo.path.parent.exists())

    def test_quarantined_retained_reading_missing_blocks_before_any_global_write(self):
        for cff in (False, True):
            with self.subTest(cff=cff):
                project = Project(self.base / f"missing-{cff}", cff=cff, reading="ㄇ")
                project.learning["verification_level"] = lib.QUARANTINED_CONFLICT
                project.save_learning()
                project.conflict(readings="ㄅ|ㄆ")
                self.assert_source_blocked(project, "retained reading missing")

    def test_quarantined_stale_sample_blocks_even_with_valid_conflict_samples(self):
        project = Project(self.base / "stale", count=2)
        project.learning["verification_level"] = lib.QUARANTINED_CONFLICT
        project.save_learning()
        project.conflict(ids=project.rows[0]["occurrence_id"])
        book = load_workbook(project.actual)
        sheet = book["實際注音"]
        headers = [cell.value for cell in sheet[1]]
        sheet.cell(3, headers.index("TTF字形SHA256") + 1).value = None
        book.save(project.actual)
        book.close()
        project.manifest["records"][1]["source_record"]["TTF字形SHA256"] = ""
        project.manifest["pdfs"][0]["actual_workbook_sha256"] = sha(project.actual.read_bytes())
        seal(project)
        self.assert_source_blocked(project, "different current identity")

    def test_valid_quarantined_learning_keeps_matching_reconfirmation_targets(self):
        for cff in (False, True):
            with self.subTest(cff=cff):
                project = Project(self.base / f"valid-{cff}", cff=cff, count=2)
                project.learning["verification_level"] = lib.QUARANTINED_CONFLICT
                project.save_learning()
                # Registry has one sample; learning retains an additional one.
                project.conflict(ids=project.rows[0]["occurrence_id"])
                before = tree_bytes(project.root)
                repo = lib.GlobalExactGlyphRepository.resolved(self.base / f"global-{cff}")
                result = migration.migrate_project(project.root, global_library_root=repo.path.parent, apply=True)
                self.assertEqual(result["mode"], "APPLIED")
                imports = {item["payload"]["reading"]: item["payload"] for item in result["intents"]}
                self.assertEqual(set(imports), {"ㄅ", "ㄆ"})
                self.assertEqual({item["legacy_evidence_sha256"] for item in imports.values()},
                                 {sha((project.evidence / review.GLYPH_CONFLICT_FILE).read_bytes())})
                targets = result["reconfirmation_targets"]
                for reading, expected in (("ㄅ", {row["occurrence_id"] for row in project.rows}),
                                          ("ㄆ", {project.rows[0]["occurrence_id"]})):
                    self.assertEqual({item["occurrence_id"] for item in targets
                                      if item["import_id"] == imports[reading]["import_id"]}, expected)
                self.assertEqual(len(targets), 3)  # The shared sample is not duplicated per import.
                self.assertTrue(all("reading" not in item for item in targets))
                self.assertEqual(tree_bytes(project.root), before)
                self.assertEqual(sql_rows(repo, "source_evidence"), [])
                self.assertEqual(sql_rows(repo, "promotion_approval"), [])
                snapshot = repo.load_snapshot()
                self.assertEqual(snapshot.trusted_identities, ())
                self.assertEqual(snapshot.quarantined_identities[0].conflicting_readings, ("ㄅ", "ㄆ"))

    def test_empty_apply_strict_validation_matrix(self):
        for donor in ("none", "noneligible"):
            project = Project(self.base / donor, record=composite_glyph() if donor == "noneligible" else None)
            if donor == "none":
                project.save_learning([])
            font = synthetic_sfnt([composite_glyph()], font_name_marker=b"test")
            for state in ("absent", "valid", "corrupt", "unknown", "invalid-row"):
                with self.subTest(donor=donor, state=state):
                    repo = lib.GlobalExactGlyphRepository.resolved(self.base / f"{donor}-{state}")
                    if state == "corrupt":
                        repo.path.parent.mkdir()
                        repo.path.write_bytes(b"corrupt sqlite")
                    elif state != "absent":
                        # Nonempty valid store makes no-op audit/generation assertions meaningful.
                        lib.deliver_global_direct_evidence(repo, [intent()])
                        if state in {"unknown", "invalid-row"}:
                            with closing(sqlite3.connect(repo.path)) as connection:
                                connection.execute("PRAGMA user_version=999" if state == "unknown" else
                                                   "UPDATE glyph_truth SET direct_source_count=999")
                                connection.commit()
                    project_before = tree_bytes(project.root)
                    global_before = store_evidence_bytes(repo) if repo.path.parent.exists() else {}
                    tables_before = ({name: sql_rows(repo, name) for name in lib._REQUIRED_COLUMNS}
                                     if state == "valid" else None)
                    args = ["--project-output", str(project.root), "--global-library-root", str(repo.path.parent)]
                    for apply in (False, True):
                        out, error = io.StringIO(), io.StringIO()
                        pdf = (patch.object(migration.fitz, "open", return_value=FakeDocument(font))
                               if donor == "noneligible" else nullcontext())
                        with pdf, redirect_stdout(out), redirect_stderr(error), \
                             patch.object(lib.GlobalExactGlyphRepository, "initialize", side_effect=AssertionError), \
                             patch.object(lib.GlobalExactGlyphRepository, "write_transaction", side_effect=AssertionError):
                            code = cli.main(args + (["--apply"] if apply else []))
                        if state in {"absent", "valid"}:
                            self.assertEqual(code, 0, error.getvalue())
                            result = json.loads(out.getvalue())
                            self.assertEqual(result["intents"], [])
                            self.assertEqual(len(result["skipped"]), int(donor == "noneligible"))
                            self.assertEqual(result["mode"], "APPLIED" if apply else "DRY_RUN")
                            if apply:
                                self.assertEqual(result["receipts"], [])
                        else:
                            self.assertEqual(code, 1)
                            self.assertEqual(json.loads(error.getvalue())["status"], "BLOCKED")
                            expected_error = {"corrupt": "GlobalLibraryCorruptError", "unknown": "GlobalLibrarySchemaError",
                                              "invalid-row": "GlobalLibraryValidationError"}[state]
                            self.assertEqual(json.loads(error.getvalue())["error_type"], expected_error)
                            self.assertEqual(out.getvalue(), "")
                        self.assertEqual(tree_bytes(project.root), project_before)
                        if state == "absent":
                            self.assertFalse(repo.path.parent.exists())
                        else:
                            self.assertEqual(store_evidence_bytes(repo), global_before)
                    if tables_before is not None:
                        self.assertEqual({name: sql_rows(repo, name) for name in lib._REQUIRED_COLUMNS}, tables_before)


class FrozenV0CompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Execute the pinned historical implementation, including its real ID
        # algorithm, schema creator, transaction validation and strict reader.
        # Missing Git history is a failed verification, never a skipped test.
        source = subprocess.check_output(["git", "show", BASELINE + ":global_exact_glyph_library.py"], cwd=ROOT)
        cls.old = types.ModuleType("_phase5_pinned_baseline_library")
        sys.modules[cls.old.__name__] = cls.old
        cls.addClassCleanup(sys.modules.pop, cls.old.__name__)
        exec(compile(source, BASELINE + ":global_exact_glyph_library.py", "exec"), cls.old.__dict__)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="migration-v0-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.exact = {"kind": lib.TTF_GLYF_SHA256, "style_group": "", "glyph_sha256": "a" * 64,
                      "identity_eligibility": lib.GLOBAL_ELIGIBLE_SIMPLE_GLYF_V1}

    def fixture(self, readings, *, quarantine=None, level="USER_VERIFIED_SINGLE", status="CANDIDATE", name="global"):
        old_repo = self.old.GlobalExactGlyphRepository.resolved(self.base / name)
        old_repo.initialize()
        with old_repo.write_transaction() as tx:
            tx.insert_candidate_identity(self.exact)
            glyph_id = self.old.compute_global_glyph_id(**self.exact)
            for index, reading in enumerate(readings):
                args = dict(glyph_id=glyph_id, reading=reading, legacy_project_id=f"old-project-{index}",
                            legacy_project_sha256="b" * 64, legacy_evidence_sha256="c" * 64,
                            old_verification_level=level)
                row = dict(import_id=self.old.compute_migration_import_id(**args), **args, status=status, imported_at=NOW)
                columns = self.old._REQUIRED_COLUMNS["migration_candidate"]
                tx._connection.execute("INSERT INTO migration_candidate (" + ",".join(columns) + ") VALUES (" +
                                       ",".join("?" for _ in columns) + ")", tuple(row[key] for key in columns))
            generation = tx.bump_generation()
            if quarantine is not None:
                tx._connection.execute("UPDATE glyph_truth SET state=?, revision=revision+1 WHERE glyph_id=?",
                                       (self.old.QUARANTINED_CONFLICT, glyph_id))
                tx._connection.execute("INSERT INTO glyph_conflict VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
                    (self.old.compute_glyph_conflict_id(glyph_id, quarantine, generation), glyph_id, self.old.OPEN,
                     self.old._canonical_json(sorted(quarantine)), NOW, NOW, generation))
        self.assertEqual(old_repo.load_snapshot().store_status, self.old.VALID)
        return old_repo, lib.GlobalExactGlyphRepository.resolved(old_repo.path.parent)

    def test_frozen_audit_requires_candidate_disagreement_quarantine(self):
        audit = subprocess.check_output(["git", "show", BASELINE + ":GLOBAL_EXACT_GLYPH_LIBRARY_AUDIT_v5.8.md"], cwd=ROOT)
        self.assertIn(b"When candidates disagree, quarantine the identity.", audit)
        self.assertIn(b"Import project conflict rows immediately as global quarantine", audit)
        self.assertEqual((self.old.GLOBAL_LIBRARY_SCHEMA_VERSION, self.old.GLOBAL_LIBRARY_SQLITE_USER_VERSION), ("1.0", 1))

    def test_single_agreeing_insufficient_and_explicit_conflict_read_without_rewrite(self):
        cases = ((["ㄅ"], None, "USER_VERIFIED_SINGLE", "CANDIDATE"),
                 (["ㄅ", "ㄅ"], None, "VERIFIED_EXACT_GLYPH", "CANDIDATE"),
                 (["ㄅ"], None, "USER_VERIFIED_SINGLE", "INSUFFICIENT"),
                 (["ㄅ", "ㄆ"], ["ㄅ", "ㄆ"], "USER_VERIFIED_SINGLE", "CANDIDATE"),
                 (["ㄅ", "ㄆ"], ["ㄅ", "ㄆ"], "QUARANTINED_CONFLICT", "CONFLICT"))
        for index, (readings, quarantine, level, status) in enumerate(cases):
            with self.subTest(index=index):
                old, repo = self.fixture(readings, quarantine=quarantine, level=level, status=status, name=str(index))
                before = store_evidence_bytes(repo)
                snapshot = repo.load_snapshot()
                self.assertEqual(snapshot.store_status, lib.VALID)
                self.assertEqual(snapshot.trusted_identities, ())
                self.assertEqual(bool(snapshot.quarantined_identities), quarantine is not None)
                self.assertEqual(store_evidence_bytes(repo), before)
                for table in ("source_evidence", "promotion_approval", "processed_intent", "provenance_event"):
                    self.assertEqual(sql_rows(repo, table), [])
                with closing(sqlite3.connect(repo.path)) as connection:
                    self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
                self.assertEqual(old.load_snapshot().store_status, self.old.VALID)

    def test_baseline_validator_omissions_stay_blocked_and_preserve_bytes(self):
        cases = ((["ㄅ", "ㄆ"], None, "USER_VERIFIED_SINGLE", "CANDIDATE", "requires quarantine"),
                 (["ㄅ", "ㄆ"], None, "QUARANTINED_CONFLICT", "CONFLICT", "requires quarantine"),
                 (["ㄅ", "ㄆ", "ㄇ"], ["ㄅ", "ㄆ"], "USER_VERIFIED_SINGLE", "CANDIDATE", "omits migration reading"))
        for index, (readings, quarantine, level, status, message) in enumerate(cases):
            with self.subTest(index=index):
                old, repo = self.fixture(readings, quarantine=quarantine, level=level, status=status, name=str(index))
                before = store_evidence_bytes(repo)
                self.assertEqual(old.load_snapshot().store_status, self.old.VALID)
                with self.assertRaisesRegex(lib.GlobalLibraryValidationError, message):
                    repo.load_snapshot()
                with self.assertRaises(lib.GlobalLibraryValidationError):
                    lib.deliver_global_direct_evidence(repo, [intent(identity=self.exact)])
                self.assertEqual(store_evidence_bytes(repo), before)

    def test_illegal_id_rejected_by_both_actual_validators(self):
        old, repo = self.fixture(["ㄅ"])
        with closing(sqlite3.connect(repo.path)) as connection:
            connection.execute("UPDATE migration_candidate SET import_id=?", ("f" * 64,))
            connection.commit()
        before = store_evidence_bytes(repo)
        for module, reader in ((self.old, old), (lib, repo)):
            with self.assertRaises(module.GlobalLibraryValidationError):
                reader.load_snapshot()
        self.assertEqual(store_evidence_bytes(repo), before)

    def test_current_imports_cannot_submit_v0_ids(self):
        old, repo = self.fixture(["ㄅ"])
        row = sql_rows(repo, "migration_candidate")[0]
        item = lib.make_migration_intent(self.exact, **{key: row[key] for key in (
            "reading", "legacy_project_id", "legacy_project_sha256", "legacy_evidence_sha256",
            "old_verification_level", "status")})
        self.assertNotEqual(item["payload"]["import_id"], row["import_id"])
        item["payload"]["import_id"] = row["import_id"]
        item["intent_id"] = "gmi_" + row["import_id"]
        item["payload_digest"] = lib._canonical_sha256(item["payload"])
        before = tree_bytes(repo.path.parent)
        with self.assertRaises(lib.GlobalLibraryValidationError):
            lib.deliver_global_migration(repo, [item])
        self.assertEqual(tree_bytes(repo.path.parent), before)

    def test_later_direct_and_migration_conflicts_never_rewrite_v0_rows(self):
        for delivery in ("direct", "migration"):
            with self.subTest(delivery=delivery):
                old, repo = self.fixture(["ㄅ"], name=delivery)
                before = sql_rows(repo, "migration_candidate")
                if delivery == "direct":
                    lib.deliver_global_direct_evidence(repo, [intent(identity=self.exact, reading="ㄆ")])
                else:
                    item = lib.make_migration_intent(self.exact, reading="ㄆ", legacy_project_id="new-project",
                        legacy_project_sha256="d" * 64, legacy_evidence_sha256="e" * 64,
                        old_verification_level="USER_VERIFIED_SINGLE", status=lib.MIGRATION_CANDIDATE)
                    lib.deliver_global_migration(repo, [item])
                    current = next(row for row in sql_rows(repo, "migration_candidate")
                                   if row["import_id"] == item["payload"]["import_id"])
                    self.assertEqual(current["status"], lib.MIGRATION_CONFLICT)
                self.assertEqual([row for row in sql_rows(repo, "migration_candidate")
                                  if row["import_id"] == before[0]["import_id"]], before)
                snapshot = repo.load_snapshot()
                self.assertEqual(snapshot.trusted_identities, ())
                self.assertEqual(snapshot.quarantined_identities[0].conflicting_readings, ("ㄅ", "ㄆ"))
                self.assertEqual(sql_rows(repo, "promotion_approval"), [])
                self.assertFalse(any(row["intent_id"] == "gmi_" + before[0]["import_id"]
                                     for row in sql_rows(repo, "processed_intent")))

    def test_matching_v0_audit_preserves_verified_digest_and_has_zero_quorum(self):
        old, repo = self.fixture(["ㄅ", "ㄅ"])
        before = sql_rows(repo, "migration_candidate")
        lib.deliver_global_direct_evidence(repo, [intent(1, identity=self.exact), intent(2, identity=self.exact)])
        approve(repo)
        exact = lib.canonical_global_exact_identity(**self.exact)
        snapshot = repo.load_snapshot()
        digest = lib.canonical_logical_subset_digest(snapshot, [exact]).sha256
        self.assertEqual(len(sql_rows(repo, "source_evidence")), 2)
        item = lib.make_migration_intent(self.exact, reading="ㄅ", legacy_project_id="new-project",
            legacy_project_sha256="d" * 64, legacy_evidence_sha256="e" * 64,
            old_verification_level="VERIFIED_EXACT_GLYPH", status=lib.MIGRATION_CANDIDATE)
        lib.deliver_global_migration(repo, [item])
        self.assertEqual(lib.canonical_logical_subset_digest(repo.load_snapshot(), [exact]).sha256, digest)
        self.assertEqual([row for row in sql_rows(repo, "migration_candidate")
                          if row["import_id"] in {r["import_id"] for r in before}], before)
        self.assertEqual(len(sql_rows(repo, "source_evidence")), 2)
