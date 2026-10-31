"""Operation-local Global views: independent baseline, real SQL and WAL writers.

All databases and genuine PDF/font fixtures are disposable. No Tk is created.
Fixed baseline comparison is separate external development evidence.
"""
from collections.abc import Mapping
from contextlib import closing, contextmanager, nullcontext
from dataclasses import fields, is_dataclass, FrozenInstanceError
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
from unittest.mock import patch

import pytest

import global_exact_glyph_library as lib
import glyph_learning_service as learning
from test_global_promotion_v580 import intent, sql_rows
from test_global_library_inspector_v580 import exact, populated_repo, durable_bytes
from test_global_legacy_migration_review_v580 import approval_request


AUDIT = ("source_evidence", "promotion_approval", "glyph_conflict",
         "migration_candidate", "provenance_event")


def plain(value):
    if is_dataclass(value):
        return {field.name: plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    return value


def evidence(name, value):
    # Optional raw detail capture; assertions do not depend on this location.
    if root := os.environ.get("PA03_EVIDENCE_DIR"):
        path = Path(root) / (name + ".json")
        with path.open("x", encoding="utf-8") as stream:
            json.dump(plain(value), stream, ensure_ascii=False, indent=2)


class ObservedConnection:
    def __init__(self, connection, fail=None):
        self.connection, self.fail = connection, fail
        self.sql, self.events, self.closed = [], [], False
        self.validation_ranges = []
        connection.set_trace_callback(self.sql.append)

    @property
    def in_transaction(self):
        return self.connection.in_transaction

    def execute(self, sql, *args):
        self.events.append(sql)
        if self.fail and self.fail(sql):
            raise sqlite3.OperationalError("injected read failure: " + sql)
        return self.connection.execute(sql, *args)

    def close(self):
        self.events.append("CLOSE")
        self.closed = True
        return self.connection.close()


@contextmanager
def observe(module=lib, fail=None):
    connect, validate = module._connect_existing, module._validate_connection
    connections, validations = [], []

    def opened(path, **kwargs):
        assert kwargs["read_only"] is True
        result = ObservedConnection(connect(path, **kwargs), fail)
        connections.append(result)
        return result

    def validated(connection, path):
        validations.append(str(path))
        start = len(connection.sql)
        try:
            return validate(connection, path)  # Always the entire real validator.
        finally:
            connection.validation_ranges.append((start, len(connection.sql)))

    with patch.object(module, "_connect_existing", side_effect=opened), \
         patch.object(module, "_validate_connection", side_effect=validated):
        yield connections, validations


def check_read(observed, validations, expected_connections, audit_count):
    assert len(observed) == len(validations) == expected_connections
    for connection in observed:
        assert connection.closed and connection.events[-1] == "CLOSE"
        assert connection.sql.count("BEGIN") == 1
        assert connection.sql.count("COMMIT") == 1
        assert not any(re.match(r"\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|DROP|ALTER)\b", sql, re.I)
                       for sql in connection.sql)
    assert sum(bool(re.fullmatch(r"SELECT \* FROM (" + "|".join(AUDIT) + r") ORDER BY \w+", sql))
               for connection in observed for index, sql in enumerate(connection.sql)
               if not any(start <= index < end for start, end in connection.validation_ranges)) == audit_count


def timed_call(call):
    before = datetime.now(timezone.utc)
    result = call()
    after = datetime.now(timezone.utc)
    views = (result.inspection, result.reuse) if isinstance(result, lib.GlobalLearningSnapshots) else (result,)
    for view in views:
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", view.loaded_at)
        loaded = datetime.fromisoformat(view.loaded_at.replace("Z", "+00:00"))
        assert before.replace(microsecond=0) <= loaded <= after
    return result


def without_loaded(value):
    result = plain(value)
    del result["loaded_at"]
    return result


def test_old_api_cost_absence_and_next_operation_revalidate(tmp_path):
    repo = lib.GlobalExactGlyphRepository(tmp_path / "missing" / "library.sqlite3")
    with observe() as (connections, validations):
        pair = timed_call(repo.load_learning_snapshots)
    assert not connections and not validations and not repo.path.parent.exists()
    assert pair.inspection.store_status == pair.reuse.store_status == lib.ABSENT
    repo.initialize()
    for method, audits in ((repo.load_snapshot, 0), (repo.load_inspection_snapshot, 5),
                           (repo.load_learning_snapshots, 5), (repo.load_learning_snapshots, 5)):
        with observe() as (connections, validations):
            timed_call(method)
        check_read(connections, validations, 1, audits)
    # Existing URI helper must really open mode=ro, not just receive a boolean.
    connect = sqlite3.connect
    with patch.object(lib.sqlite3, "connect", wraps=connect) as calls:
        repo.load_learning_snapshots()
    assert calls.call_count == 1
    assert calls.call_args.args[0].endswith("?mode=ro")
    assert calls.call_args.kwargs["uri"] is True


def test_loaded_times_follow_commit_and_pair_is_deeply_immutable(tmp_path):
    repo = populated_repo(tmp_path / "populated")
    with observe() as (connections, validations):
        def now():
            assert connections[0].sql[-1] == "COMMIT"
            return next(times)
        times = iter(("2026-11-01T00:00:01Z", "2026-11-01T00:00:02Z"))
        with patch.object(lib, "_utc_now", side_effect=now) as clock:
            pair = repo.load_learning_snapshots()
    check_read(connections, validations, 1, 5)
    assert clock.call_count == 2
    assert pair.inspection.loaded_at == "2026-11-01T00:00:01Z"
    assert pair.reuse.loaded_at == "2026-11-01T00:00:02Z"
    with pytest.raises(FrozenInstanceError):
        pair.reuse = None
    with pytest.raises(TypeError):
        pair.inspection.metadata["generation"] = 999
    for record in pair.inspection.records:
        with pytest.raises(FrozenInstanceError):
            record.global_reuse_allowed = True
        with pytest.raises(TypeError):
            record.truth["revision"] = 999
        for rows in (record.source_evidence, record.approvals, record.conflicts,
                     record.legacy_candidates, record.provenance):
            for row in rows:
                with pytest.raises(TypeError):
                    row["glyph_id"] = "polluted"
    with pytest.raises(FrozenInstanceError):
        pair.reuse.trusted_identities[0].active_reading = "ㄇ"


def test_wal_writer_after_validation_and_between_projections(tmp_path):
    for point in ("validated", "inspection_built"):
        repo = lib.GlobalExactGlyphRepository(tmp_path / point / "library.sqlite3")
        identity = exact(point)
        lib.deliver_global_direct_evidence(repo, [intent(1, identity=identity), intent(2, identity=identity)])
        lib.approve_global_exact_glyph(repo, **approval_request(repo, identity))
        initial = repo.load_learning_snapshots()
        original = lib._validate_connection if point == "validated" else lib._build_inspection_records
        written = False

        def synchronized(connection, value):
            nonlocal written
            result = original(connection, value)
            if not written:
                written = True  # Writer uses its own connection and real validation.
                lib.deliver_global_direct_evidence(repo, [intent(3, "ㄆ", identity=identity)])
            return result

        # A live owner preserves a nonempty WAL; writer commits while reader's
        # transaction is synchronously paused, with no sleep or timing lottery.
        with closing(sqlite3.connect(repo.path, isolation_level=None)) as keeper:
            keeper.execute("PRAGMA wal_autocheckpoint=0")
            keeper.execute("SELECT generation FROM library_meta").fetchall()
            with patch.object(lib, "_validate_connection" if point == "validated" else
                              "_build_inspection_records", side_effect=synchronized):
                same = repo.load_learning_snapshots()
            assert Path(str(repo.path) + "-wal").stat().st_size > 0
            newer = repo.load_learning_snapshots()
        assert written
        assert without_loaded(same.inspection) == without_loaded(initial.inspection)
        assert without_loaded(same.reuse) == without_loaded(initial.reuse)
        record = same.inspection.records[0]
        assert record.stored_state == lib.VERIFIED_GLOBAL and len(record.source_evidence) == 2
        assert record.approvals[0]["status"] == lib.APPROVED and record.conflicts == ()
        assert same.reuse.trusted_identities[0].active_reading == "ㄅ"
        record = newer.inspection.records[0]
        assert record.stored_state == lib.QUARANTINED_CONFLICT and len(record.source_evidence) == 3
        assert len(record.approvals) == 2
        assert {row["status"] for row in record.approvals} == {lib.APPROVED, lib.REVOKED}
        # Revocation is append-only; the original approved event is retained.
        assert {row["quorum_digest"] for row in record.approvals} == {same.inspection.records[0].approvals[0]["quorum_digest"]}
        assert record.conflicts[0]["status"] == lib.OPEN
        assert record.readings == ("ㄅ", "ㄆ")
        assert newer.reuse.trusted_identities == ()
        assert newer.reuse.quarantined_identities[0].conflicting_readings == ("ㄅ", "ㄆ")
        assert newer.reuse.generation > same.reuse.generation
        evidence("wal-" + point, dict(initial=initial, same=same, newer=newer))


def test_invalid_empty_busy_permission_and_stale_approval(tmp_path):
    for case, expected in (("corrupt", lib.GlobalLibraryCorruptError),
                           ("schema", lib.GlobalLibrarySchemaError),
                           ("row", lib.GlobalLibraryValidationError),
                           ("stale", lib.GlobalLibraryValidationError)):
        repo = populated_repo(tmp_path / case)
        if case == "corrupt":
            repo.path.write_bytes(b"not a database")
        else:
            with closing(sqlite3.connect(repo.path)) as connection:
                connection.execute({"schema": "PRAGMA user_version=99",
                    "row": "UPDATE glyph_truth SET direct_source_count=999",
                    "stale": "UPDATE promotion_approval SET glyph_revision=glyph_revision+1 WHERE status='APPROVED'"}[case])
                connection.commit()
        before = durable_bytes(repo)
        with observe() as (connections, validations), pytest.raises(expected):
            repo.load_learning_snapshots()
        assert len(validations) == 1 and connections[0].closed
        assert "ROLLBACK" in connections[0].events and "COMMIT" not in connections[0].sql
        assert durable_bytes(repo) == before
    repo = lib.GlobalExactGlyphRepository(tmp_path / "empty.sqlite3")
    repo.initialize()
    empty = repo.load_learning_snapshots()
    assert empty.inspection.store_status == empty.reuse.store_status == lib.VALID
    assert empty.inspection.records == empty.reuse.trusted_identities == ()
    for exc, expected in ((sqlite3.OperationalError("database is locked"), lib.GlobalLibraryBusyError),
                          (PermissionError("permission denied"), lib.GlobalLibraryPermissionError)):
        with patch.object(lib, "_connect_existing", side_effect=exc), pytest.raises(expected):
            repo.load_learning_snapshots()


def test_failure_boundaries_rollback_close_and_no_partial_pair(tmp_path):
    repo = populated_repo(tmp_path / "failures")
    before = {table: sql_rows(repo, table) for table in lib._REQUIRED_COLUMNS}
    for point in ("BEGIN", "validation", "audit", "inspection", "reuse", "COMMIT"):
        phases, audit_faults, matching_sql_phases = [], [], []
        audit_sql = "SELECT * FROM source_evidence ORDER BY evidence_id"
        audit_failure = sqlite3.OperationalError("PA03_F1_INSPECTION_AUDIT_SQL")

        def fail_audit(sql):
            if sql == audit_sql:
                matching_sql_phases.append({"sql": sql,
                    "phase": "inspection" if "inspection_enter" in phases else "validation"})
            if phases == ["validation_ok", "inspection_enter"] and sql == audit_sql:
                audit_faults.append(sql)
                phases.append("audit_fault")
                raise audit_failure  # The execute point, after genuine validation.
            if sql == "ROLLBACK":
                phases.append("rollback")
            return False

        @contextmanager
        def trace_audit():
            # observe's validator wrapper still runs the complete real validator.
            validate = lib._validate_connection
            build = lib._build_inspection_records
            close = ObservedConnection.close

            def validated(connection, path):
                result = validate(connection, path)
                phases.append("validation_ok")
                return result

            def inspection(connection, validated_rows):
                assert phases == ["validation_ok"]
                phases.append("inspection_enter")
                return build(connection, validated_rows)

            def closed(connection):
                close(connection)
                phases.append("close")

            with patch.object(lib, "_validate_connection", side_effect=validated), \
                 patch.object(lib, "_build_inspection_records", side_effect=inspection), \
                 patch.object(ObservedConnection, "close", closed):
                yield

        fail = (lambda sql: sql == point) if point in ("BEGIN", "COMMIT") else (
            fail_audit if point == "audit" else None)
        with observe(fail=fail) as (connections, validations):
            target = {"validation": "_validate_rows", "inspection": "_build_inspection_records",
                      "reuse": "_build_reuse_records"}.get(point)
            with trace_audit() if point == "audit" else nullcontext(), \
                 patch.object(lib, target, side_effect=ValueError(point)) if target else nullcontext():
                not_returned = object()
                result = not_returned
                with pytest.raises(lib.GlobalLibraryValidationError) as rejected:
                    result = repo.load_learning_snapshots()
                assert result is not_returned
        assert len(connections) == 1 and connections[0].closed
        assert ("ROLLBACK" in connections[0].events) == (point != "BEGIN")
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connections[0].connection.execute("SELECT 1")
        if point == "audit":
            assert len(validations) == 1
            assert phases == ["validation_ok", "inspection_enter", "audit_fault", "rollback", "close"]
            assert audit_faults == [audit_sql]
            assert matching_sql_phases == [{"sql": audit_sql, "phase": "validation"},
                                           {"sql": audit_sql, "phase": "inspection"}]
            assert rejected.value.__cause__ is audit_failure
            assert str(rejected.value).endswith(str(audit_failure))
            # The first identical SELECT completed inside the true validator;
            # only the inspection's second execute faults before SQLite sees it.
            assert connections[0].events.count(audit_sql) == 2
            assert connections[0].sql.count(audit_sql) == 1
            assert "COMMIT" not in connections[0].events
            assert not any(re.match(r"\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|DROP|ALTER)\b", sql, re.I)
                           for sql in connections[0].sql)
            assert {table: sql_rows(repo, table) for table in lib._REQUIRED_COLUMNS} == before
            evidence("F1-audit-phase", dict(phases=phases, audit_faults=audit_faults,
                matching_sql_phases=matching_sql_phases,
                error_type=type(rejected.value).__name__, error=str(rejected.value),
                cause_type=type(audit_failure).__name__, cause=str(audit_failure),
                cause_is_injected_object=rejected.value.__cause__ is audit_failure,
                validations=validations, execute_events=connections[0].events,
                sqlite_trace=connections[0].sql, validation_ranges=connections[0].validation_ranges,
                result_is_original_sentinel=result is not_returned,
                result_type=type(result).__name__, closed=connections[0].closed))
    # Acquired handle must close even before _connect_existing returns it.
    connect, handles = sqlite3.connect, []
    def opened(*args, **kwargs):
        result = connect(*args, **kwargs); handles.append(result); return result
    with patch.object(lib.sqlite3, "connect", side_effect=opened), \
         patch.object(lib, "_configure_connection", side_effect=PermissionError("configuration permission")), \
         pytest.raises(lib.GlobalLibraryPermissionError):
        repo.load_learning_snapshots()
    assert len(handles) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        handles[0].execute("SELECT 1")
    assert {table: sql_rows(repo, table) for table in lib._REQUIRED_COLUMNS} == before
