from __future__ import annotations

import ast
import hashlib
import json
import multiprocessing
import sqlite3
import tempfile
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from unittest.mock import patch

import cross_version_compat
import global_exact_glyph_library as library
import runtime_source_validation
from exact_glyph_identity import (
    GLOBAL_ELIGIBLE_COMPLETE_CFF_RECORDING_V1,
    GLOBAL_ELIGIBLE_SIMPLE_GLYF_V1,
    NON_GLOBAL_ELIGIBLE,
    TTF_GLYF_SHA256,
)


ROOT = Path(__file__).resolve().parents[1]
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
NOW = "2026-09-02T00:00:00Z"


def _temporary_root():
    return tempfile.TemporaryDirectory(
        prefix="global-library-v580-",
        dir=ROOT,
    )


def _repo(root: Path, *, busy_timeout_ms: int = 500, write_retry_limit: int = 1):
    return library.GlobalExactGlyphRepository.resolved(
        root,
        busy_timeout_ms=busy_timeout_ms,
        write_retry_limit=write_retry_limit,
    )


def _db_path(root: Path) -> Path:
    return library.resolve_global_exact_glyph_library_path(root)


def _connection(root: Path, *, foreign_keys: bool = True) -> sqlite3.Connection:
    connection = sqlite3.connect(_db_path(root), isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute(f"PRAGMA foreign_keys = {1 if foreign_keys else 0}")
    return connection


def _ttf_identity(sha256: str = SHA_A) -> library.GlobalExactGlyphIdentity:
    return library.canonical_global_exact_identity(
        TTF_GLYF_SHA256,
        "",
        sha256,
        GLOBAL_ELIGIBLE_SIMPLE_GLYF_V1,
    )


def _insert_candidate_fixture(root: Path, sha256: str = SHA_A) -> str:
    repo = _repo(root)
    repo.initialize()
    identity = _ttf_identity(sha256)
    glyph_id = library.compute_global_glyph_id(
        identity.kind,
        identity.style_group,
        identity.glyph_sha256,
        identity_eligibility=identity.identity_eligibility,
    )
    connection = _connection(root)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO glyph_truth (
                glyph_id, identity_contract_version, identity_eligibility,
                kind, style_group, glyph_sha256, state, active_reading,
                direct_source_count, independent_source_count, revision,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 0, 0, 0, ?, ?)
            """,
            (
                glyph_id,
                library.GLOBAL_IDENTITY_CONTRACT_VERSION,
                identity.identity_eligibility,
                identity.kind,
                identity.style_group,
                identity.glyph_sha256,
                library.CANDIDATE,
                NOW,
                NOW,
            ),
        )
        connection.execute(
            "UPDATE library_meta SET generation = generation + 1, updated_at = ? WHERE meta_id = 1",
            (NOW,),
        )
        connection.execute("COMMIT")
    finally:
        connection.close()
    return glyph_id


def _direct_evidence(
    glyph_id: str,
    reading: str,
    index: int,
) -> dict[str, object]:
    digit = str(index)
    values: dict[str, object] = {
        "glyph_id": glyph_id,
        "reading": reading,
        "evidence_class": library.DIRECT_VISUAL_ACTUAL,
        "counts_toward_global_quorum": 1,
        "source_project_id": f"project-{index}",
        "source_pdf_sha256": digit * 64,
        "source_font_program_sha256": hex(index + 8)[2:] * 64,
        "source_occurrence_id": f"occurrence-{index}",
        "source_review_id": f"review-{index}",
        "decision_snapshot_sha256": hex(index + 10)[2:] * 64,
        "confirmation_channel": library.PDF_PAGE_VISUAL,
        "confirmed_at": NOW,
        "ingested_at": NOW,
    }
    values["evidence_id"] = library.compute_source_evidence_id(
        glyph_id=values["glyph_id"],
        reading=values["reading"],
        evidence_class=values["evidence_class"],
        source_project_id=values["source_project_id"],
        source_pdf_sha256=values["source_pdf_sha256"],
        source_font_program_sha256=values["source_font_program_sha256"],
        source_occurrence_id=values["source_occurrence_id"],
        source_review_id=values["source_review_id"],
        decision_snapshot_sha256=values["decision_snapshot_sha256"],
        confirmation_channel=values["confirmation_channel"],
        confirmed_at=values["confirmed_at"],
    )
    return values


def _insert_evidence(connection: sqlite3.Connection, evidence: dict[str, object]) -> None:
    connection.execute(
        """
        INSERT INTO source_evidence (
            evidence_id, glyph_id, reading, evidence_class,
            counts_toward_global_quorum, source_project_id,
            source_pdf_sha256, source_font_program_sha256,
            source_occurrence_id, source_review_id,
            decision_snapshot_sha256, confirmation_channel,
            confirmed_at, ingested_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        tuple(evidence[column] for column in library._REQUIRED_COLUMNS["source_evidence"]),
    )


def _insert_verified_fixture(
    root: Path,
    *,
    sha256: str = SHA_A,
    reading: str = "ㄅ",
) -> str:
    repo = _repo(root)
    repo.initialize()
    identity = _ttf_identity(sha256)
    glyph_id = library.compute_global_glyph_id(
        identity.kind,
        identity.style_group,
        identity.glyph_sha256,
        identity_eligibility=identity.identity_eligibility,
    )
    evidence = [_direct_evidence(glyph_id, reading, 1), _direct_evidence(glyph_id, reading, 2)]
    evidence_ids = sorted(str(item["evidence_id"]) for item in evidence)
    quorum_json = json.dumps(evidence_ids, ensure_ascii=False, separators=(",", ":"))
    quorum_digest = library.compute_quorum_digest(glyph_id, reading, evidence_ids)
    approval_id = library.compute_promotion_approval_id(
        glyph_id=glyph_id,
        reading=reading,
        quorum_digest=quorum_digest,
        promotion_policy_version=library.GLOBAL_PROMOTION_POLICY_VERSION,
        glyph_revision=1,
        approval_source=library.EXPLICIT_MANUAL_APPROVAL,
        status=library.APPROVED,
    )
    connection = _connection(root)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO glyph_truth (
                glyph_id, identity_contract_version, identity_eligibility,
                kind, style_group, glyph_sha256, state, active_reading,
                direct_source_count, independent_source_count, revision,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 2, 2, 1, ?, ?)
            """,
            (
                glyph_id,
                library.GLOBAL_IDENTITY_CONTRACT_VERSION,
                identity.identity_eligibility,
                identity.kind,
                identity.style_group,
                identity.glyph_sha256,
                library.VERIFIED_GLOBAL,
                reading,
                NOW,
                NOW,
            ),
        )
        for item in evidence:
            _insert_evidence(connection, item)
        connection.execute(
            """
            INSERT INTO promotion_approval (
                approval_id, glyph_id, reading, quorum_digest,
                quorum_evidence_ids_json, promotion_policy_version,
                glyph_revision, approval_source, status, approved_at
            ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
            """,
            (
                approval_id,
                glyph_id,
                reading,
                quorum_digest,
                quorum_json,
                library.GLOBAL_PROMOTION_POLICY_VERSION,
                library.EXPLICIT_MANUAL_APPROVAL,
                library.APPROVED,
                NOW,
            ),
        )
        connection.execute(
            "UPDATE library_meta SET generation = 1, updated_at = ? WHERE meta_id = 1",
            (NOW,),
        )
        connection.execute("COMMIT")
    finally:
        connection.close()
    return glyph_id


def _insert_conflict_fixture(root: Path, *, sha256: str = SHA_A) -> str:
    repo = _repo(root)
    repo.initialize()
    identity = _ttf_identity(sha256)
    glyph_id = library.compute_global_glyph_id(
        identity.kind,
        identity.style_group,
        identity.glyph_sha256,
        identity_eligibility=identity.identity_eligibility,
    )
    evidence = [_direct_evidence(glyph_id, "ㄅ", 1), _direct_evidence(glyph_id, "ㄆ", 2)]
    readings = tuple(sorted({"ㄅ", "ㄆ"}))
    readings_json = json.dumps(list(readings), ensure_ascii=False, separators=(",", ":"))
    conflict_id = library.compute_glyph_conflict_id(glyph_id, readings, 1)
    connection = _connection(root)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO glyph_truth (
                glyph_id, identity_contract_version, identity_eligibility,
                kind, style_group, glyph_sha256, state, active_reading,
                direct_source_count, independent_source_count, revision,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 2, 2, 1, ?, ?)
            """,
            (
                glyph_id,
                library.GLOBAL_IDENTITY_CONTRACT_VERSION,
                identity.identity_eligibility,
                identity.kind,
                identity.style_group,
                identity.glyph_sha256,
                library.QUARANTINED_CONFLICT,
                NOW,
                NOW,
            ),
        )
        for item in evidence:
            _insert_evidence(connection, item)
        connection.execute(
            """
            INSERT INTO glyph_conflict (
                conflict_id, glyph_id, status, conflicting_readings_json,
                first_event_at, last_event_at, opened_generation,
                resolved_generation, resolution_reference
            ) VALUES (?, ?, ?, ?, ?, ?, 1, NULL, NULL)
            """,
            (
                conflict_id,
                glyph_id,
                library.OPEN,
                readings_json,
                NOW,
                NOW,
            ),
        )
        connection.execute(
            "UPDATE library_meta SET generation = 1, updated_at = ? WHERE meta_id = 1",
            (NOW,),
        )
        connection.execute("COMMIT")
    finally:
        connection.close()
    return glyph_id


def _count_rows(root: Path, table: str) -> int:
    connection = _connection(root)
    try:
        return int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
    finally:
        connection.close()


def _processed_insert(
    repo: library.GlobalExactGlyphRepository,
    *,
    intent_id: str,
    payload: bytes,
    sha256: str,
) -> library.ProcessedIntentReceipt:
    identity = _ttf_identity(sha256)

    def operation(transaction: library.GlobalWriteTransaction) -> library.StorageMutation:
        glyph_id = transaction.insert_candidate_identity(identity, created_at=NOW)
        return library.StorageMutation(True, glyph_id)

    return repo.execute_idempotent_write(
        intent_id=intent_id,
        payload_digest=hashlib.sha256(payload).hexdigest(),
        operation=operation,
    )


def _busy_holder_worker(root: str, ready, release, queue) -> None:
    try:
        repo = _repo(Path(root), busy_timeout_ms=1000, write_retry_limit=0)
        with repo.write_transaction():
            ready.set()
            if not release.wait(15):
                raise RuntimeError("release event timeout")
        queue.put(("holder", "ok"))
    except BaseException as exc:
        queue.put(("holder", "error", type(exc).__name__, str(exc)))


def _initialize_worker(root: str, barrier, queue) -> None:
    try:
        repo = _repo(Path(root), busy_timeout_ms=3000, write_retry_limit=1)
        barrier.wait(timeout=15)
        snapshot = repo.initialize()
        queue.put((snapshot.store_status, snapshot.generation))
    except BaseException as exc:
        queue.put(("error", type(exc).__name__, str(exc)))


def _busy_contender_worker(root: str, ready, queue) -> None:
    try:
        if not ready.wait(15):
            raise RuntimeError("ready event timeout")
        repo = _repo(Path(root), busy_timeout_ms=50, write_retry_limit=0)
        with repo.write_transaction():
            pass
        queue.put(("contender", "unexpected-success"))
    except library.GlobalLibraryBusyError as exc:
        queue.put(("contender", exc.status))
    except BaseException as exc:
        queue.put(("contender", "error", type(exc).__name__, str(exc)))


def _cas_worker(root: str, glyph_id: str, barrier, queue) -> None:
    try:
        repo = _repo(Path(root), busy_timeout_ms=2000)
        observed = repo.get_glyph_revision(glyph_id)
        barrier.wait(timeout=15)
        result = repo.compare_and_swap_glyph_revision(glyph_id, int(observed))
        queue.put((result.applied, result.observed_revision, result.current_revision, result.generation))
    except BaseException as exc:
        queue.put(("error", type(exc).__name__, str(exc)))


def _same_intent_worker(root: str, barrier, queue) -> None:
    try:
        repo = _repo(Path(root), busy_timeout_ms=3000)
        barrier.wait(timeout=15)
        receipt = _processed_insert(
            repo,
            intent_id="shared-intent",
            payload=b"shared-payload",
            sha256=SHA_A,
        )
        queue.put((receipt.already_processed, receipt.receipt_digest, receipt.committed_generation))
    except BaseException as exc:
        queue.put(("error", type(exc).__name__, str(exc)))


def _different_identity_worker(root: str, index: int, barrier, queue) -> None:
    try:
        repo = _repo(Path(root), busy_timeout_ms=3000)
        barrier.wait(timeout=15)
        sha256 = SHA_A if index == 1 else SHA_B
        receipt = _processed_insert(
            repo,
            intent_id=f"intent-{index}",
            payload=f"payload-{index}".encode("ascii"),
            sha256=sha256,
        )
        queue.put((index, receipt.already_processed, receipt.committed_generation))
    except BaseException as exc:
        queue.put(("error", type(exc).__name__, str(exc)))


def _join_process(test: unittest.TestCase, process, *, timeout: int = 20) -> None:
    process.join(timeout)
    if process.is_alive():
        process.terminate()
        process.join(5)
        test.fail(f"child process did not exit: pid={process.pid}")
    test.assertEqual(process.exitcode, 0)


class GlobalLibraryRootAndInitializationTests(unittest.TestCase):
    def test_production_localappdata_path_and_explicit_root_override(self):
        with _temporary_root() as directory:
            base = Path(directory)
            local = base / "LocalAppData"
            explicit = base / "Explicit"
            production = library.resolve_global_exact_glyph_library_path(
                environ={"LOCALAPPDATA": str(local)}
            )
            self.assertEqual(
                production,
                local
                / "DiscoveryRay"
                / "ZhuyinProofreader"
                / "GlobalExactGlyphLibrary"
                / "library.sqlite3",
            )
            self.assertEqual(
                library.resolve_global_exact_glyph_library_path(
                    explicit,
                    environ={},
                ),
                explicit / "library.sqlite3",
            )
            self.assertFalse(local.exists())
            self.assertFalse(explicit.exists())

    def test_missing_localappdata_fails_without_cwd_fallback(self):
        with self.assertRaisesRegex(library.GlobalLibraryPathError, "LOCALAPPDATA"):
            library.resolve_global_exact_glyph_library_path(environ={})
        self.assertFalse((ROOT / "DiscoveryRay").exists())

    def test_glyph_id_and_repository_never_infer_global_eligibility_from_sha(self):
        with self.assertRaises(TypeError):
            library.compute_global_glyph_id(  # type: ignore[call-arg]
                TTF_GLYF_SHA256,
                "",
                SHA_A,
            )

        with _temporary_root() as directory:
            root = Path(directory) / "store"
            repo = _repo(root)
            repo.initialize()
            with self.assertRaises(library.GlobalLibraryValidationError):
                with repo.write_transaction() as transaction:
                    transaction.insert_candidate_identity({
                        "kind": TTF_GLYF_SHA256,
                        "style_group": "",
                        "glyph_sha256": SHA_A,
                    })
            self.assertEqual(repo.load_snapshot().generation, 0)
            self.assertEqual(_count_rows(root, "glyph_truth"), 0)

    def test_read_only_absent_is_typed_and_creates_no_directory(self):
        with _temporary_root() as directory:
            root = Path(directory) / "not-created"
            snapshot = library.load_global_exact_glyph_snapshot(root)
            diagnostic = library.inspect_global_exact_glyph_library(root)
            self.assertEqual(snapshot.store_status, library.ABSENT)
            self.assertEqual(diagnostic.status, library.ABSENT)
            self.assertFalse(root.exists())

    def test_explicit_initialize_creates_strict_v1_generation_zero_and_wal(self):
        with _temporary_root() as directory:
            root = Path(directory) / "store"
            snapshot = library.initialize_global_exact_glyph_library(root)
            self.assertEqual(snapshot.store_status, library.VALID)
            self.assertEqual(snapshot.schema_version, "1.0")
            self.assertEqual(snapshot.generation, 0)
            path = _db_path(root)
            self.assertTrue(path.is_file())
            connection = library._connect_existing(path, read_only=True, busy_timeout_ms=321)
            try:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
                self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
                self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
                self.assertEqual(connection.execute("PRAGMA busy_timeout").fetchone()[0], 321)
                strict = {
                    row["name"]: row["strict"]
                    for row in connection.execute("PRAGMA table_list")
                    if row["name"] in library._TABLE_SQL
                }
                self.assertEqual(strict, {name: 1 for name in library._TABLE_SQL})
            finally:
                connection.close()

    def test_repeated_initialize_is_idempotent_without_destructive_rewrite(self):
        with _temporary_root() as directory:
            root = Path(directory) / "store"
            first = _repo(root).initialize()
            path = _db_path(root)
            before = path.read_bytes()
            connection = _connection(root)
            try:
                meta_before = tuple(connection.execute("SELECT * FROM library_meta").fetchone())
            finally:
                connection.close()
            second = _repo(root).initialize()
            connection = _connection(root)
            try:
                meta_after = tuple(connection.execute("SELECT * FROM library_meta").fetchone())
            finally:
                connection.close()
            self.assertEqual(first.generation, 0)
            self.assertEqual(second.generation, 0)
            self.assertEqual(meta_after, meta_before)
            self.assertEqual(path.read_bytes(), before)

    def test_initialize_present_invalid_database_preserves_original_bytes(self):
        with _temporary_root() as directory:
            root = Path(directory) / "store"
            root.mkdir(parents=True)
            path = _db_path(root)
            original = b"not-a-sqlite-database"
            path.write_bytes(original)
            with self.assertRaises(library.GlobalLibraryCorruptError):
                _repo(root).initialize()
            self.assertEqual(path.read_bytes(), original)


class GlobalLibraryStrictValidationTests(unittest.TestCase):
    def _diagnostic_after(self, mutation) -> library.GlobalLibraryDiagnostic:
        self.temp = _temporary_root()
        directory = self.temp.__enter__()
        self.addCleanup(self.temp.__exit__, None, None, None)
        root = Path(directory) / "store"
        _repo(root).initialize()
        mutation(root)
        return _repo(root).inspect()

    def test_exact_tables_columns_primary_keys_foreign_keys_checks_and_indexes(self):
        with _temporary_root() as directory:
            root = Path(directory) / "store"
            _repo(root).initialize()
            connection = _connection(root)
            try:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                    )
                }
                self.assertEqual(tables, set(library._TABLE_SQL))
                for table, expected_columns in library._REQUIRED_COLUMNS.items():
                    with self.subTest(table=table):
                        columns = tuple(row[1] for row in connection.execute(f'PRAGMA table_info("{table}")'))
                        self.assertEqual(columns, expected_columns)
                        sql = connection.execute(
                            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                            (table,),
                        ).fetchone()[0]
                        self.assertIn("CHECK", sql)
                indexes = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"
                    )
                }
                self.assertEqual(indexes, set(library._INDEX_SQL))
                self.assertTrue(connection.execute("PRAGMA foreign_key_list(source_evidence)").fetchall())
            finally:
                connection.close()

    def test_unknown_user_version_fails_closed(self):
        def mutation(root: Path) -> None:
            connection = _connection(root)
            try:
                connection.execute("PRAGMA user_version = 99")
            finally:
                connection.close()

        diagnostic = self._diagnostic_after(mutation)
        self.assertEqual(diagnostic.status, library.SCHEMA_INCOMPATIBLE)

    def test_meta_version_mismatch_fails_closed(self):
        def mutation(root: Path) -> None:
            connection = _connection(root)
            try:
                connection.execute("PRAGMA ignore_check_constraints = ON")
                connection.execute("UPDATE library_meta SET schema_version = '99.0'")
            finally:
                connection.close()

        self.assertEqual(self._diagnostic_after(mutation).status, library.SCHEMA_INCOMPATIBLE)

    def test_duplicate_meta_row_fails_closed(self):
        def mutation(root: Path) -> None:
            connection = _connection(root)
            try:
                connection.execute("PRAGMA ignore_check_constraints = ON")
                connection.execute(
                    "INSERT INTO library_meta VALUES (2, '1.0', '1.0', '1.0', 0, ?, ?)",
                    (NOW, NOW),
                )
            finally:
                connection.close()

        self.assertEqual(self._diagnostic_after(mutation).status, library.VALIDATION_FAILED)

    def test_missing_table_and_index_fail_as_schema_incompatible(self):
        def missing_table(root: Path) -> None:
            connection = _connection(root)
            try:
                connection.execute("DROP TABLE migration_candidate")
            finally:
                connection.close()

        self.assertEqual(self._diagnostic_after(missing_table).status, library.SCHEMA_INCOMPATIBLE)

        def missing_index(root: Path) -> None:
            connection = _connection(root)
            try:
                connection.execute("DROP INDEX ix_glyph_truth_state")
            finally:
                connection.close()

        self.assertEqual(self._diagnostic_after(missing_index).status, library.SCHEMA_INCOMPATIBLE)

    def test_missing_column_extra_column_and_extra_table_fail_closed(self):
        def missing_column(root: Path) -> None:
            connection = _connection(root, foreign_keys=False)
            try:
                connection.execute("ALTER TABLE migration_candidate RENAME TO migration_candidate_old")
                connection.execute("CREATE TABLE migration_candidate (import_id TEXT PRIMARY KEY) STRICT")
                connection.execute("DROP TABLE migration_candidate_old")
            finally:
                connection.close()

        self.assertEqual(self._diagnostic_after(missing_column).status, library.SCHEMA_INCOMPATIBLE)

        def extra_column(root: Path) -> None:
            connection = _connection(root)
            try:
                connection.execute("ALTER TABLE library_meta ADD COLUMN unexpected TEXT")
            finally:
                connection.close()

        self.assertEqual(self._diagnostic_after(extra_column).status, library.SCHEMA_INCOMPATIBLE)

        def extra_table(root: Path) -> None:
            connection = _connection(root)
            try:
                connection.execute("CREATE TABLE unexpected(value TEXT) STRICT")
            finally:
                connection.close()

        self.assertEqual(self._diagnostic_after(extra_table).status, library.SCHEMA_INCOMPATIBLE)

    def test_undeclared_view_and_trigger_fail_as_schema_incompatible(self):
        statements = {
            "view": "CREATE VIEW unexpected_view AS SELECT generation FROM library_meta",
            "trigger": (
                "CREATE TRIGGER unexpected_trigger AFTER UPDATE ON library_meta "
                "BEGIN SELECT 1; END"
            ),
        }
        for name, statement in statements.items():
            with self.subTest(name=name):
                def mutation(root: Path, sql=statement) -> None:
                    connection = _connection(root)
                    try:
                        connection.execute(sql)
                    finally:
                        connection.close()

                self.assertEqual(
                    self._diagnostic_after(mutation).status,
                    library.SCHEMA_INCOMPATIBLE,
                )

    def test_invalid_state_sha_glyph_id_and_cached_count_each_fail_entire_store(self):
        mutations = {
            "state": "UPDATE glyph_truth SET state='UNKNOWN_STATE'",
            "sha": "UPDATE glyph_truth SET glyph_sha256='ABC'",
            "glyph_id": "UPDATE glyph_truth SET glyph_id='ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff'",
            "count": "UPDATE glyph_truth SET direct_source_count=1",
        }
        for name, statement in mutations.items():
            with self.subTest(name=name):
                def mutation(root: Path, sql=statement) -> None:
                    _insert_candidate_fixture(root)
                    connection = _connection(root)
                    try:
                        connection.execute("PRAGMA ignore_check_constraints = ON")
                        connection.execute(sql)
                    finally:
                        connection.close()

                self.assertEqual(self._diagnostic_after(mutation).status, library.VALIDATION_FAILED)

    def test_invalid_noncanonical_reading_fails_entire_store(self):
        def mutation(root: Path) -> None:
            glyph_id = _insert_candidate_fixture(root)
            connection = _connection(root)
            try:
                connection.execute(
                    """
                    INSERT INTO source_evidence VALUES (
                        ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        "e" * 64,
                        glyph_id,
                        "ㄅ˙",
                        library.LEGACY_PROJECT_CANDIDATE,
                        "project",
                        "1" * 64,
                        "2" * 64,
                        "occurrence",
                        "review",
                        "3" * 64,
                        library.LEGACY_IMPORT,
                        NOW,
                        NOW,
                    ),
                )
            finally:
                connection.close()

        self.assertEqual(self._diagnostic_after(mutation).status, library.VALIDATION_FAILED)

    def test_broken_foreign_key_fails_before_partial_row_loading(self):
        def mutation(root: Path) -> None:
            connection = _connection(root, foreign_keys=False)
            try:
                connection.execute(
                    """
                    INSERT INTO source_evidence VALUES (
                        ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        "e" * 64,
                        "f" * 64,
                        "ㄅ",
                        library.LEGACY_PROJECT_CANDIDATE,
                        "project",
                        "1" * 64,
                        "2" * 64,
                        "occurrence",
                        "review",
                        "3" * 64,
                        library.LEGACY_IMPORT,
                        NOW,
                        NOW,
                    ),
                )
            finally:
                connection.close()

        self.assertEqual(self._diagnostic_after(mutation).status, library.VALIDATION_FAILED)

    def test_verified_global_without_exact_approval_fails_entire_store(self):
        def mutation(root: Path) -> None:
            _insert_verified_fixture(root)
            connection = _connection(root)
            try:
                connection.execute("DELETE FROM promotion_approval")
            finally:
                connection.close()

        self.assertEqual(self._diagnostic_after(mutation).status, library.VALIDATION_FAILED)

    def test_quarantined_conflict_requires_two_canonical_readings(self):
        def mutation(root: Path) -> None:
            _insert_conflict_fixture(root)
            connection = _connection(root)
            try:
                connection.execute(
                    "UPDATE glyph_conflict SET conflicting_readings_json = ?",
                    (json.dumps(["ㄅ"], ensure_ascii=False, separators=(",", ":")),),
                )
            finally:
                connection.close()

        self.assertEqual(self._diagnostic_after(mutation).status, library.VALIDATION_FAILED)


class GlobalLibraryCorruptionAndDiagnosticsTests(unittest.TestCase):
    def test_malformed_and_truncated_databases_are_corrupt_not_absent(self):
        for payload in (b"not sqlite", b"SQLite format 3\x00" + b"\x00" * 20):
            with self.subTest(size=len(payload)), _temporary_root() as directory:
                root = Path(directory) / "store"
                root.mkdir(parents=True)
                path = _db_path(root)
                path.write_bytes(payload)
                before = path.read_bytes()
                diagnostic = _repo(root).inspect()
                self.assertEqual(diagnostic.status, library.CORRUPT)
                self.assertNotEqual(diagnostic.status, library.ABSENT)
                self.assertEqual(path.read_bytes(), before)

    def test_deterministically_invalid_wal_header_is_corrupt(self):
        with _temporary_root() as directory:
            root = Path(directory) / "store"
            _repo(root).initialize()
            wal = Path(str(_db_path(root)) + "-wal")
            wal.write_bytes(b"invalid-wal-header" + b"\x00" * 32)
            self.assertEqual(_repo(root).inspect().status, library.CORRUPT)

    def test_permission_denied_is_not_collapsed_to_absent(self):
        with _temporary_root() as directory:
            root = Path(directory) / "store"
            _repo(root).initialize()
            with patch.object(library, "_connect_existing", side_effect=PermissionError("denied")):
                diagnostic = _repo(root).inspect()
            self.assertEqual(diagnostic.status, library.PERMISSION_DENIED)

    def test_unknown_schema_and_invalid_row_have_distinct_diagnostics(self):
        with _temporary_root() as first_directory, _temporary_root() as second_directory:
            schema_root = Path(first_directory) / "store"
            row_root = Path(second_directory) / "store"
            _repo(schema_root).initialize()
            _repo(row_root).initialize()
            connection = _connection(schema_root)
            connection.execute("PRAGMA user_version=99")
            connection.close()
            glyph_id = _insert_candidate_fixture(row_root)
            connection = _connection(row_root)
            connection.execute("UPDATE glyph_truth SET direct_source_count=1 WHERE glyph_id=?", (glyph_id,))
            connection.close()
            self.assertEqual(_repo(schema_root).inspect().status, library.SCHEMA_INCOMPATIBLE)
            self.assertEqual(_repo(row_root).inspect().status, library.VALIDATION_FAILED)


class GlobalLibrarySnapshotTests(unittest.TestCase):
    def test_valid_verified_and_conflict_snapshots_are_immutable(self):
        with _temporary_root() as verified_directory, _temporary_root() as conflict_directory:
            verified_root = Path(verified_directory) / "store"
            conflict_root = Path(conflict_directory) / "store"
            verified_id = _insert_verified_fixture(verified_root)
            conflict_id = _insert_conflict_fixture(conflict_root)
            verified = _repo(verified_root).load_snapshot()
            conflict = _repo(conflict_root).load_snapshot()
            self.assertEqual(tuple(item.glyph_id for item in verified.trusted_identities), (verified_id,))
            self.assertEqual(tuple(item.glyph_id for item in conflict.quarantined_identities), (conflict_id,))
            self.assertEqual(conflict.quarantined_identities[0].conflicting_readings, ("ㄅ", "ㄆ"))
            with self.assertRaises(FrozenInstanceError):
                verified.generation = 99  # type: ignore[misc]
            with self.assertRaises(AttributeError):
                verified.trusted_identities.append(verified.trusted_identities[0])  # type: ignore[attr-defined]

    def test_snapshot_ends_read_transaction_and_later_mutation_does_not_change_old_snapshot(self):
        with _temporary_root() as directory:
            root = Path(directory) / "store"
            _insert_verified_fixture(root)
            old = _repo(root).load_snapshot()
            with _repo(root).write_transaction():
                pass
            _insert_candidate_fixture(root, SHA_B)
            new = _repo(root).load_snapshot()
            self.assertEqual(old.generation, 1)
            self.assertEqual(old.trusted_identities, new.trusted_identities)
            self.assertEqual(old.generation, 1)
            self.assertEqual(new.generation, 2)


def _snapshot_record(
    sha256: str,
    *,
    state: str,
    reading: str = "",
    conflicts: tuple[str, ...] = (),
    updated_at: str = NOW,
) -> library.GlobalGlyphSnapshotRecord:
    identity = _ttf_identity(sha256)
    return library.GlobalGlyphSnapshotRecord(
        glyph_id=library.compute_global_glyph_id(
            identity.kind,
            identity.style_group,
            identity.glyph_sha256,
            identity_eligibility=identity.identity_eligibility,
        ),
        identity=identity,
        state=state,
        active_reading=reading,
        conflicting_readings=conflicts,
        direct_source_count=2,
        independent_source_count=2,
        revision=1,
        updated_at=updated_at,
    )


def _logical_snapshot(
    *,
    trusted: tuple[library.GlobalGlyphSnapshotRecord, ...] = (),
    quarantined: tuple[library.GlobalGlyphSnapshotRecord, ...] = (),
    generation: int = 1,
) -> library.GlobalExactGlyphSnapshot:
    return library.GlobalExactGlyphSnapshot(
        store_status=library.VALID,
        schema_version=library.GLOBAL_LIBRARY_SCHEMA_VERSION,
        identity_contract_version=library.GLOBAL_IDENTITY_CONTRACT_VERSION,
        promotion_policy_version=library.GLOBAL_PROMOTION_POLICY_VERSION,
        generation=generation,
        trusted_identities=trusted,
        quarantined_identities=quarantined,
        database_path="C:/audit-only/library.sqlite3",
        loaded_at=NOW,
        provenance_event_count=0,
    )


class GlobalLibraryLogicalSubsetTests(unittest.TestCase):
    def test_explicit_absent_noneligible_verified_and_quarantined_rows(self):
        request = _ttf_identity(SHA_A)
        absent = library.canonical_logical_subset_digest(_logical_snapshot(), [request])
        verified = library.canonical_logical_subset_digest(
            _logical_snapshot(trusted=(_snapshot_record(SHA_A, state=library.VERIFIED_GLOBAL, reading="ㄅ"),)),
            [request],
        )
        conflict = library.canonical_logical_subset_digest(
            _logical_snapshot(quarantined=(
                _snapshot_record(
                    SHA_A,
                    state=library.QUARANTINED_CONFLICT,
                    conflicts=("ㄅ", "ㄆ"),
                ),
            )),
            [request],
        )
        noneligible = library.canonical_logical_subset_digest(
            _logical_snapshot(),
            [{
                "kind": TTF_GLYF_SHA256,
                "style_group": "",
                "glyph_sha256": SHA_A,
                "identity_eligibility": NON_GLOBAL_ELIGIBLE,
            }],
        )
        self.assertEqual(absent.rows[0].effective_state, library.ABSENT)
        self.assertEqual(verified.rows[0].effective_state, library.VERIFIED_GLOBAL)
        self.assertEqual(verified.rows[0].active_reading, "ㄅ")
        self.assertEqual(conflict.rows[0].effective_state, library.QUARANTINED_CONFLICT)
        self.assertEqual(conflict.rows[0].conflicting_readings, ("ㄅ", "ㄆ"))
        self.assertEqual(noneligible.rows[0].effective_state, NON_GLOBAL_ELIGIBLE)
        self.assertEqual(len({absent.sha256, verified.sha256, conflict.sha256, noneligible.sha256}), 4)

    def test_result_affecting_reading_conflict_policy_and_identity_changes_alter_digest(self):
        request = _ttf_identity(SHA_A)
        first = _logical_snapshot(trusted=(_snapshot_record(SHA_A, state=library.VERIFIED_GLOBAL, reading="ㄅ"),))
        reading_changed = _logical_snapshot(trusted=(_snapshot_record(SHA_A, state=library.VERIFIED_GLOBAL, reading="ㄆ"),))
        conflict_first = _logical_snapshot(quarantined=(
            _snapshot_record(SHA_A, state=library.QUARANTINED_CONFLICT, conflicts=("ㄅ", "ㄆ")),
        ))
        conflict_changed = _logical_snapshot(quarantined=(
            _snapshot_record(SHA_A, state=library.QUARANTINED_CONFLICT, conflicts=("ㄅ", "ㄇ")),
        ))
        identity_changed = replace(first, identity_contract_version="2.0")
        policy_changed = replace(first, promotion_policy_version="2.0")
        digests = {
            library.canonical_logical_subset_digest(snapshot, [request]).sha256
            for snapshot in (
                first,
                reading_changed,
                conflict_first,
                conflict_changed,
                identity_changed,
                policy_changed,
            )
        }
        self.assertEqual(len(digests), 6)

    def test_generation_path_timestamp_provenance_and_record_timestamp_are_excluded(self):
        request = _ttf_identity(SHA_A)
        record = _snapshot_record(SHA_A, state=library.VERIFIED_GLOBAL, reading="ㄅ")
        first = _logical_snapshot(trusted=(record,), generation=1)
        changed = replace(
            first,
            generation=999,
            database_path="D:/another/location/library.sqlite3",
            loaded_at="2030-01-01T00:00:00Z",
            provenance_event_count=999,
            trusted_identities=(replace(record, updated_at="2030-01-01T00:00:00Z"),),
        )
        first_digest = library.canonical_logical_subset_digest(first, [request])
        changed_digest = library.canonical_logical_subset_digest(changed, [request])
        self.assertEqual(first_digest.sha256, changed_digest.sha256)
        payload = json.loads(first_digest.canonical_json)
        serialized = json.dumps(payload, ensure_ascii=False)
        for forbidden in ("generation", "timestamp", "updated_at", "database_path", "provenance", "note"):
            self.assertNotIn(forbidden, serialized)

    def test_unrelated_identity_change_does_not_affect_requested_subset(self):
        request = _ttf_identity(SHA_A)
        first = _logical_snapshot()
        unrelated = _logical_snapshot(trusted=(
            _snapshot_record(SHA_B, state=library.VERIFIED_GLOBAL, reading="ㄆ"),
        ))
        self.assertEqual(
            library.canonical_logical_subset_digest(first, [request]).sha256,
            library.canonical_logical_subset_digest(unrelated, [request]).sha256,
        )

    def test_payload_order_is_deterministic_and_duplicate_requests_are_deduped(self):
        snapshot = _logical_snapshot()
        forward = library.canonical_logical_subset_digest(
            snapshot,
            [_ttf_identity(SHA_B), _ttf_identity(SHA_A), _ttf_identity(SHA_A)],
        )
        reverse = library.canonical_logical_subset_digest(
            snapshot,
            [_ttf_identity(SHA_A), _ttf_identity(SHA_B)],
        )
        self.assertEqual(forward.sha256, reverse.sha256)
        self.assertEqual(len(forward.rows), 2)


class GlobalLibraryBackupTests(unittest.TestCase):
    def test_sqlite_backup_validates_and_atomically_publishes_without_changing_source(self):
        with _temporary_root() as directory:
            root = Path(directory) / "store"
            repo = _repo(root)
            repo.initialize()
            _processed_insert(repo, intent_id="seed", payload=b"seed", sha256=SHA_A)
            source_path = _db_path(root)
            source_before = source_path.read_bytes()
            source_snapshot = repo.load_snapshot()
            destination = Path(directory) / "backups" / "library-backup.sqlite3"
            backup_snapshot = repo.backup(destination)
            self.assertTrue(destination.is_file())
            self.assertEqual(backup_snapshot.store_status, library.VALID)
            self.assertEqual(backup_snapshot.generation, source_snapshot.generation)
            self.assertEqual(source_path.read_bytes(), source_before)
            self.assertEqual(
                library._load_snapshot_from_path(destination, 500).generation,
                source_snapshot.generation,
            )

    def test_backup_rejects_live_destination_existing_destination_and_corrupt_source(self):
        with _temporary_root() as directory:
            root = Path(directory) / "store"
            repo = _repo(root)
            repo.initialize()
            with self.assertRaises(library.GlobalLibraryPathError):
                repo.backup(_db_path(root))
            destination = Path(directory) / "copy.sqlite3"
            destination.write_bytes(b"preserve")
            with self.assertRaises(library.GlobalLibraryValidationError):
                repo.backup(destination)
            self.assertEqual(destination.read_bytes(), b"preserve")

            corrupt_root = Path(directory) / "corrupt"
            corrupt_root.mkdir()
            _db_path(corrupt_root).write_bytes(b"bad")
            absent_destination = Path(directory) / "must-not-exist.sqlite3"
            with self.assertRaises(library.GlobalLibraryCorruptError):
                _repo(corrupt_root).backup(absent_destination)
            self.assertFalse(absent_destination.exists())

    def test_backup_implementation_uses_sqlite_api_not_live_file_copy(self):
        path = ROOT / "global_exact_glyph_library.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        calls = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertNotIn("shutil", imported)
        self.assertIn("backup", calls)


class GlobalLibraryTransactionPrimitiveTests(unittest.TestCase):
    def test_idempotent_intent_retry_and_restart_preserve_single_effect_and_receipt(self):
        with _temporary_root() as directory:
            root = Path(directory) / "store"
            repo = _repo(root)
            repo.initialize()
            first = _processed_insert(repo, intent_id="intent", payload=b"payload", sha256=SHA_A)
            second = _processed_insert(repo, intent_id="intent", payload=b"payload", sha256=SHA_A)
            restarted = _repo(root).lookup_processed_intent(
                "intent",
                payload_digest=hashlib.sha256(b"payload").hexdigest(),
            )
            self.assertFalse(first.already_processed)
            self.assertTrue(second.already_processed)
            self.assertEqual(first.receipt_digest, second.receipt_digest)
            self.assertEqual(restarted.receipt_digest, first.receipt_digest)
            self.assertEqual(_count_rows(root, "glyph_truth"), 1)
            self.assertEqual(_count_rows(root, "processed_intent"), 1)
            self.assertEqual(repo.load_snapshot().generation, 1)

    def test_same_intent_different_payload_is_hard_error_without_generation_change(self):
        with _temporary_root() as directory:
            root = Path(directory) / "store"
            repo = _repo(root)
            repo.initialize()
            _processed_insert(repo, intent_id="intent", payload=b"first", sha256=SHA_A)
            with self.assertRaises(library.GlobalLibraryIntentConflictError):
                _processed_insert(repo, intent_id="intent", payload=b"different", sha256=SHA_A)
            self.assertEqual(repo.load_snapshot().generation, 1)
            self.assertEqual(_count_rows(root, "glyph_truth"), 1)

    def test_failed_transaction_rolls_back_mutation_intent_and_generation(self):
        with _temporary_root() as directory:
            root = Path(directory) / "store"
            repo = _repo(root)
            repo.initialize()

            def failure(transaction: library.GlobalWriteTransaction) -> library.StorageMutation:
                transaction.insert_candidate_identity(_ttf_identity(SHA_A), created_at=NOW)
                raise RuntimeError("injected failure")

            with self.assertRaisesRegex(RuntimeError, "injected failure"):
                repo.execute_idempotent_write(
                    intent_id="failed",
                    payload_digest=hashlib.sha256(b"failed").hexdigest(),
                    operation=failure,
                )
            self.assertEqual(repo.load_snapshot().generation, 0)
            self.assertEqual(_count_rows(root, "glyph_truth"), 0)
            self.assertEqual(_count_rows(root, "processed_intent"), 0)

    def test_mutation_receipt_must_match_actual_sqlite_effect(self):
        with _temporary_root() as directory:
            root = Path(directory) / "store"
            repo = _repo(root)
            repo.initialize()

            def hidden_change(transaction: library.GlobalWriteTransaction) -> library.StorageMutation:
                transaction.insert_candidate_identity(_ttf_identity(SHA_A), created_at=NOW)
                return library.StorageMutation(False)

            with self.assertRaisesRegex(
                library.GlobalLibraryValidationError,
                "SQLite mutation",
            ):
                repo.execute_idempotent_write(
                    intent_id="hidden-change",
                    payload_digest=hashlib.sha256(b"hidden-change").hexdigest(),
                    operation=hidden_change,
                )
            with self.assertRaisesRegex(
                library.GlobalLibraryValidationError,
                "SQLite mutation",
            ):
                repo.execute_idempotent_write(
                    intent_id="false-positive",
                    payload_digest=hashlib.sha256(b"false-positive").hexdigest(),
                    operation=lambda _transaction: library.StorageMutation(True),
                )
            self.assertEqual(repo.load_snapshot().generation, 0)
            self.assertEqual(_count_rows(root, "glyph_truth"), 0)
            self.assertEqual(_count_rows(root, "processed_intent"), 0)

    def test_no_effect_retry_does_not_bump_generation(self):
        with _temporary_root() as directory:
            root = Path(directory) / "store"
            repo = _repo(root)
            repo.initialize()
            payload = hashlib.sha256(b"no-effect").hexdigest()
            first = repo.execute_idempotent_write(
                intent_id="no-effect",
                payload_digest=payload,
                operation=lambda _transaction: library.StorageMutation(False),
            )
            second = repo.execute_idempotent_write(
                intent_id="no-effect",
                payload_digest=payload,
                operation=lambda _transaction: library.StorageMutation(True),
            )
            self.assertEqual(first.result_state, library.INTENT_NO_EFFECT)
            self.assertTrue(second.already_processed)
            self.assertEqual(repo.load_snapshot().generation, 0)

    def test_one_effective_batch_with_two_rows_bumps_generation_once(self):
        with _temporary_root() as directory:
            root = Path(directory) / "store"
            repo = _repo(root)
            repo.initialize()

            def two_rows(transaction: library.GlobalWriteTransaction) -> library.StorageMutation:
                transaction.insert_candidate_identity(_ttf_identity(SHA_A), created_at=NOW)
                transaction.insert_candidate_identity(_ttf_identity(SHA_B), created_at=NOW)
                return library.StorageMutation(True)

            repo.execute_idempotent_write(
                intent_id="two-rows",
                payload_digest=hashlib.sha256(b"two-rows").hexdigest(),
                operation=two_rows,
            )
            self.assertEqual(_count_rows(root, "glyph_truth"), 2)
            self.assertEqual(repo.load_snapshot().generation, 1)

    def test_cas_success_then_stale_miss_does_not_overwrite_or_bump_again(self):
        with _temporary_root() as directory:
            root = Path(directory) / "store"
            repo = _repo(root)
            repo.initialize()
            receipt = _processed_insert(repo, intent_id="seed", payload=b"seed", sha256=SHA_A)
            glyph_id = receipt.mutation_value
            success = repo.compare_and_swap_glyph_revision(glyph_id, 0)
            stale = repo.compare_and_swap_glyph_revision(glyph_id, 0)
            self.assertTrue(success.applied)
            self.assertEqual(success.current_revision, 1)
            self.assertFalse(stale.applied)
            self.assertEqual(stale.observed_revision, 1)
            self.assertEqual(repo.get_glyph_revision(glyph_id), 1)
            self.assertEqual(repo.load_snapshot().generation, 2)


class GlobalLibraryMultiProcessTests(unittest.TestCase):
    def test_simultaneous_explicit_initializers_leave_one_retryable_valid_store(self):
        with _temporary_root() as directory:
            root = Path(directory) / "store"
            context = multiprocessing.get_context("spawn")
            barrier = context.Barrier(2)
            queue = context.Queue()
            processes = [
                context.Process(target=_initialize_worker, args=(str(root), barrier, queue))
                for _ in range(2)
            ]
            for process in processes:
                process.start()
            results = [queue.get(timeout=20) for _ in processes]
            for process in processes:
                _join_process(self, process)
            self.assertGreaterEqual(results.count((library.VALID, 0)), 1)
            for result in results:
                self.assertTrue(
                    result == (library.VALID, 0)
                    or result[:2] == ("error", "GlobalLibraryBusyError"),
                    result,
                )
            self.assertEqual(_repo(root).initialize().generation, 0)
            self.assertEqual(_repo(root).inspect().status, library.VALID)
            self.assertEqual(_count_rows(root, "library_meta"), 1)

    def test_bounded_begin_immediate_lock_is_typed_and_store_remains_valid(self):
        with _temporary_root() as directory:
            root = Path(directory) / "store"
            _repo(root).initialize()
            context = multiprocessing.get_context("spawn")
            ready = context.Event()
            release = context.Event()
            queue = context.Queue()
            holder = context.Process(
                target=_busy_holder_worker,
                args=(str(root), ready, release, queue),
            )
            contender = context.Process(
                target=_busy_contender_worker,
                args=(str(root), ready, queue),
            )
            holder.start()
            self.assertTrue(ready.wait(15))
            contender.start()
            contender_result = queue.get(timeout=15)
            self.assertEqual(contender_result, ("contender", library.BUSY))
            release.set()
            holder_result = queue.get(timeout=15)
            self.assertEqual(holder_result, ("holder", "ok"))
            _join_process(self, contender)
            _join_process(self, holder)
            self.assertEqual(_repo(root).inspect().status, library.VALID)

    def test_two_process_same_revision_cas_has_at_most_one_success(self):
        with _temporary_root() as directory:
            root = Path(directory) / "store"
            repo = _repo(root)
            repo.initialize()
            glyph_id = _processed_insert(
                repo,
                intent_id="seed",
                payload=b"seed",
                sha256=SHA_A,
            ).mutation_value
            context = multiprocessing.get_context("spawn")
            barrier = context.Barrier(2)
            queue = context.Queue()
            processes = [
                context.Process(target=_cas_worker, args=(str(root), glyph_id, barrier, queue))
                for _ in range(2)
            ]
            for process in processes:
                process.start()
            results = [queue.get(timeout=20) for _ in processes]
            for process in processes:
                _join_process(self, process)
            self.assertEqual(sorted(result[0] for result in results), [False, True])
            self.assertEqual(repo.get_glyph_revision(glyph_id), 1)
            self.assertEqual(repo.load_snapshot().generation, 2)

    def test_same_processed_intent_retry_from_two_processes_has_one_effect(self):
        with _temporary_root() as directory:
            root = Path(directory) / "store"
            repo = _repo(root)
            repo.initialize()
            context = multiprocessing.get_context("spawn")
            barrier = context.Barrier(2)
            queue = context.Queue()
            processes = [
                context.Process(target=_same_intent_worker, args=(str(root), barrier, queue))
                for _ in range(2)
            ]
            for process in processes:
                process.start()
            results = [queue.get(timeout=20) for _ in processes]
            for process in processes:
                _join_process(self, process)
            self.assertEqual(sorted(result[0] for result in results), [False, True])
            self.assertEqual(len({result[1] for result in results}), 1)
            self.assertEqual(_count_rows(root, "glyph_truth"), 1)
            self.assertEqual(_count_rows(root, "processed_intent"), 1)
            self.assertEqual(repo.load_snapshot().generation, 1)

    def test_different_identity_concurrent_writes_are_both_retained(self):
        with _temporary_root() as directory:
            root = Path(directory) / "store"
            repo = _repo(root)
            repo.initialize()
            context = multiprocessing.get_context("spawn")
            barrier = context.Barrier(2)
            queue = context.Queue()
            processes = [
                context.Process(
                    target=_different_identity_worker,
                    args=(str(root), index, barrier, queue),
                )
                for index in (1, 2)
            ]
            for process in processes:
                process.start()
            results = [queue.get(timeout=20) for _ in processes]
            for process in processes:
                _join_process(self, process)
            self.assertEqual({result[0] for result in results}, {1, 2})
            self.assertEqual(_count_rows(root, "glyph_truth"), 2)
            self.assertEqual(_count_rows(root, "processed_intent"), 2)
            self.assertEqual(repo.load_snapshot().generation, 2)

    def test_concurrency_tests_use_deterministic_sync_not_sleep(self):
        source = Path(__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        sleep_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "sleep"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "time"
        ]
        self.assertFalse(sleep_calls)
        for token in ("Event()", "Barrier(2)", "Queue()"):
            self.assertIn(token, source)


class GlobalLibraryArchitectureBoundaryTests(unittest.TestCase):
    def test_global_module_is_actual_only_and_schema_has_no_expected_fields(self):
        path = ROOT / "global_exact_glyph_library.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        forbidden = {
            "check_pronunciation_candidates",
            "concise_expected_resolver",
            "pronunciation_rule_engine",
        }
        self.assertFalse(imported & forbidden)
        columns = {
            column
            for table_columns in library._REQUIRED_COLUMNS.values()
            for column in table_columns
        }
        self.assertFalse({column for column in columns if "expected" in column.lower()})

    def test_decoder_pipeline_project_review_and_gui_do_not_import_global_store(self):
        for name in (
            "export_zhuyin_readings.py",
            "standalone_proofread.py",
            "actual_review.py",
            "review_gui.py",
        ):
            with self.subTest(file=name):
                tree = ast.parse((ROOT / name).read_text(encoding="utf-8"))
                imports = {
                    node.module
                    for node in ast.walk(tree)
                    if isinstance(node, ast.ImportFrom) and node.module
                }
                imports.update(
                    alias.name
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Import)
                    for alias in node.names
                )
                self.assertNotIn("global_exact_glyph_library", imports)

    def test_no_global_fingerprint_source_roster_policy_schema_or_epoch_change(self):
        def assigned_source_list(path: Path) -> list[str]:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            assignment = next(
                node
                for node in tree.body
                if isinstance(node, (ast.Assign, ast.AnnAssign))
                and any(
                    isinstance(target, ast.Name) and target.id == "ACTUAL_DECODER_SOURCE_FILES"
                    for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
                )
            )
            return ast.literal_eval(assignment.value)

        exported = assigned_source_list(ROOT / "export_zhuyin_readings.py")
        pipeline = assigned_source_list(ROOT / "standalone_proofread.py")
        self.assertEqual(exported, pipeline)
        self.assertNotIn("global_exact_glyph_library.py", exported)
        self.assertEqual(runtime_source_validation.ACTUAL_FINGERPRINT_SCHEMA_VERSION, "2.9.0")
        self.assertEqual(runtime_source_validation.EXPECTED_FINGERPRINT_SCHEMA_VERSION, "2.7.0")
        self.assertEqual(cross_version_compat.ACTUAL_DECODER_SEMANTICS_EPOCH, "1")
        self.assertEqual(cross_version_compat.EXPECTED_RESOLVER_SEMANTICS_EPOCH, "1")
        self.assertNotIn(
            "global_exact_glyph_subset",
            cross_version_compat.FINGERPRINT_COMPATIBILITY_REQUIRED_KEYS["actual"],
        )
        self.assertNotIn("global", json.dumps(
            cross_version_compat.FINGERPRINT_COMPATIBILITY_REQUIRED_KEYS,
            sort_keys=True,
        ))

    def test_global_store_is_dynamic_not_runtime_asset_and_no_project_outbox_exists(self):
        manifest = json.loads((ROOT / "runtime_asset_manifest.json").read_text(encoding="utf-8"))
        serialized = json.dumps(manifest, ensure_ascii=False)
        self.assertNotIn("library.sqlite3", serialized)
        self.assertNotIn("global_exact_glyph_library.py", serialized)
        self.assertFalse((ROOT / "_專案證據" / "actual" / "global_exact_glyph_promotion_outbox.json").exists())

    def test_cff_eligibility_constant_is_non_behavioral_contract_only(self):
        self.assertEqual(
            GLOBAL_ELIGIBLE_COMPLETE_CFF_RECORDING_V1,
            "GLOBAL_ELIGIBLE_COMPLETE_CFF_RECORDING_V1",
        )
        cff = library.canonical_global_exact_identity(
            library.CFF_GLYPH_SHA256,
            "KAICHU_MD",
            SHA_C,
            GLOBAL_ELIGIBLE_COMPLETE_CFF_RECORDING_V1,
        )
        self.assertEqual(cff.style_group, "KAICHU_MD")


if __name__ == "__main__":
    unittest.main()
