"""One-collect readonly direct proofs, using isolated real CSV/PDF sources.

Fixture event hashes and expected decisions are independent of the matcher.
No Tk, formal project, Global writes, timers or performance claims are used.
"""
from collections import Counter
from contextlib import contextmanager
from contextvars import copy_context
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
from threading import Thread

import pytest

import actual_review as actual
import global_exact_glyph_library as library
import glyph_learning_service as learning
from tests.test_actual_operation_snapshots import make_source


def entry(oid="one", **changes):
    return dict(occurrence_id=oid, pdf_name="book.pdf", printed_page="1", char="字",
                stable_key="k", x0="10", y0="20", source_record={"TTF字形SHA256": "a" * 64},
                **changes)


def override(row, reading="ㄅ", **changes):
    result = dict(pdf_contains=Path(row["pdf_name"]).stem, pdf_excludes="",
                  page=str(row["printed_page"]), target_char=row["char"], stable_key=row["stable_key"],
                  x0=str(row["x0"]), y0=str(row["y0"]), actual_reading=reading, source="visual", note="")
    result.update(changes)
    return result


def event(row, reading="ㄅ", **changes):
    result = dict(kind="TTF_GLYF_SHA256", style_group="", glyph_sha256=row["source_record"]["TTF字形SHA256"],
                  bopomofo=reading, event_type="OCCURRENCE_OVERRIDE", occurrence_ids=row["occurrence_id"],
                  source="visual", note=" =literal note ")
    result.update(changes)
    # Match the persisted reader's text stripping before independently sealing.
    result = {key: value.strip() for key, value in result.items()}
    raw = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return dict(result, event_id="gpe_" + hashlib.sha256(raw).hexdigest()[:24], created_at="fixture")


def write_sources(root, overrides=(), events=()):
    actual.ensure_user_evidence_files(root)
    actual._write_csv(root / actual.OCCURRENCE_OVERRIDE_FILE, actual.OVERRIDE_HEADERS, overrides)
    actual._write_csv(root / actual.GLYPH_PROVENANCE_FILE, actual.GLYPH_PROVENANCE_HEADERS, events)


@contextmanager
def query(root):
    with actual._direct_occurrence_actual_queries(root) as owner:
        owner._seed(actual.load_dynamic_actual_snapshot(root, read_only=True))
        yield owner


def test_exact_predicate_duplicates_readings_sources_identity_and_event_order(tmp_path, monkeypatch):
    row = entry()
    good = event(row)
    cases = [
        ([], [good], False),
        ([override(row)] * 2, [good], False),
        ([override(row, "ㄆ"), override(row)], [good], True),
        ([override(row)], [dict(good, event_id="wrong"), good], True),
        ([override(row)], [dict(good, event_id="wrong")], False),
        ([override(row)], [event(row, occurrence_ids="one|two")], False),
        ([override(row)], [event(row, occurrence_ids="one-more")], False),
        ([override(row)], [event(row, event_type="GROUP_OVERRIDE")], False),
        ([override(row)], [event(row, source="other")], False),
        ([override(row)], [event(row, kind="CFF_GLYPH_SHA256")], False),
        ([override(row)], [event(row, style_group="other")], False),
        ([override(row)], [event(row, glyph_sha256="b" * 64)], False),
        ([override(row)], [event(row, reading="ㄆ")], False),
        ([override(row)], [dict(good, note="changed")], False),
    ]
    for overrides, events, expected in cases:
        write_sources(tmp_path, overrides, events)
        assert actual.has_direct_occurrence_actual(tmp_path, row, "ㄅ") is expected
        with query(tmp_path) as owner:
            assert owner.has_direct(tmp_path, row, "ㄅ") is expected
            # No bool memoization: the same key with a different identity/oid
            # must still run the original independent exact-proof checks.
            assert not owner.has_direct(tmp_path, dict(row, occurrence_id="unviewed"), "ㄅ")
            changed = copy.deepcopy(row)
            changed["source_record"]["TTF字形SHA256"] = "c" * 64
            assert not owner.has_direct(tmp_path, changed, "ㄅ")
    write_sources(tmp_path, [override(row)], [good, dict(good, event_id="unused invalid tail")])
    hashes, original = [], actual._hash_payload
    def hashed(payload):
        hashes.append(payload)
        return original(payload)
    monkeypatch.setattr(actual, "_hash_payload", hashed)
    with query(tmp_path) as owner:
        assert owner.has_direct(tmp_path, row, "ㄅ")
    assert len(hashes) == 1  # first valid event still returns immediately


def test_lazy_short_circuits_corrupt_absent_and_tail_parser_errors(tmp_path, monkeypatch):
    row = entry()
    write_sources(tmp_path)
    provenance = tmp_path / actual.GLYPH_PROVENANCE_FILE
    for overrides in ([], [override(row)] * 2):
        actual._write_csv(tmp_path / actual.OCCURRENCE_OVERRIDE_FILE, actual.OVERRIDE_HEADERS, overrides)
        provenance.write_bytes(b"bad header\n\xff")
        calls, identity = [], actual.actual_group_identity
        def identified(*args):
            calls.append(1)
            return identity(*args)
        with monkeypatch.context() as patch:
            patch.setattr(actual, "actual_group_identity", identified)
            with query(tmp_path) as owner:
                assert not owner.has_direct(tmp_path, row, "invalid")
                assert not owner.has_direct(tmp_path, row, "ㄅ")
                assert actual.GLYPH_PROVENANCE_FILE not in owner.raw
        assert calls == []
    actual._write_csv(tmp_path / actual.OCCURRENCE_OVERRIDE_FILE, actual.OVERRIDE_HEADERS, [override(row)])
    for broken, error in ((b"bad\n", ValueError), (b"bad\n\xff", UnicodeDecodeError)):
        provenance.write_bytes(broken)
        with pytest.raises(error):
            actual.has_direct_occurrence_actual(tmp_path, row, "ㄅ")
        with pytest.raises(error), query(tmp_path) as owner:
            owner.has_direct(tmp_path, row, "ㄅ")
        assert owner.closed and not owner.raw and not owner.indexes
    actual._write_csv(provenance, actual.GLYPH_PROVENANCE_HEADERS, [event(row)])
    provenance.write_bytes(provenance.read_bytes() + b"\xff")
    with pytest.raises(UnicodeDecodeError), query(tmp_path) as owner:
        owner.has_direct(tmp_path, row, "ㄅ")  # entire CSV must parse, including tail
    provenance.unlink()
    assert not actual.has_direct_occurrence_actual(tmp_path, row, "ㄅ")
    with pytest.raises(ValueError, match="證據缺失"):
        actual.load_dynamic_actual_snapshot(tmp_path, read_only=True)


def test_snapshot_rows_are_private_and_cleanup_rejects_foreign_context_thread_and_closed(tmp_path):
    row = entry()
    write_sources(tmp_path, [override(row)], [event(row)])
    with pytest.raises(ValueError, match="collector scope"):
        actual._DirectOccurrenceActualQuery(tmp_path)
    with actual._direct_occurrence_actual_queries(tmp_path) as owner:
        with pytest.raises(ValueError, match="validated dynamic"):
            owner._seed({"rows": [override(row)]})
        snapshot = actual.load_dynamic_actual_snapshot(tmp_path, read_only=True)
        owner._seed(snapshot)
        public_rows = snapshot.rows(actual.OCCURRENCE_OVERRIDE_FILE)
        public_rows[0]["source"] = "public mutation"
        assert owner.has_direct(tmp_path, row, "ㄅ")
        with pytest.raises(TypeError):
            next(iter(owner.indexes[actual.OCCURRENCE_OVERRIDE_FILE].values()))[0]["source"] = "mutate"
        with pytest.raises(ValueError, match="foreign"):
            owner.has_direct(tmp_path / "foreign", row, "ㄅ")
        with pytest.raises(ValueError, match="foreign context"):
            copy_context().run(owner.has_direct, tmp_path, row, "ㄅ")
        errors = []
        def other_thread():
            try:
                owner.has_direct(tmp_path, row, "ㄅ")
            except ValueError as exc:
                errors.append(str(exc))
        thread = Thread(target=other_thread)
        thread.start()
        thread.join()
        assert errors and "owner" in errors[0]
        with actual._direct_occurrence_actual_queries(tmp_path) as other:
            with pytest.raises(ValueError, match="foreign"):
                owner.has_direct(tmp_path, row, "ㄅ")
            other._seed(snapshot)
        assert owner.has_direct(tmp_path, row, "ㄅ")
    assert owner.closed and not owner.raw and not owner.indexes and not owner.used
    with pytest.raises(ValueError, match="closed"):
        owner.has_direct(tmp_path, row, "ㄅ")
    assert actual._direct_occurrence_query.get() is None


def same_metadata_change(path):
    before = path.stat()
    raw = path.read_bytes()
    # Only a same-length persisted source value changes; reading/schema stay legal.
    changed = raw.replace(b"visual", b"mutate")
    assert changed != raw and len(changed) == len(raw)
    path.write_bytes(changed)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert path.stat().st_size == before.st_size and path.stat().st_mtime_ns == before.st_mtime_ns


def test_initial_subsequent_and_exit_changes_reject_without_refresh(tmp_path, monkeypatch):
    row = entry()
    for name in (actual.OCCURRENCE_OVERRIDE_FILE, actual.GLYPH_PROVENANCE_FILE):
        for moment in ("initial", "subsequent", "exit"):
            write_sources(tmp_path, [override(row)], [event(row)])
            target = tmp_path / name
            with pytest.raises(ValueError, match="source changed"):
                with query(tmp_path) as owner:
                    if moment != "initial":
                        assert owner.has_direct(tmp_path, row, "ㄅ")
                    if name == actual.GLYPH_PROVENANCE_FILE and moment == "initial":
                        # Deterministic mutation after first captured bytes,
                        # before the immediate post-parser check.
                        parser = actual._read_csv_bytes
                        def parsed(raw, filename, headers):
                            result = parser(raw, filename, headers)
                            if filename == name:
                                same_metadata_change(target)
                            return result
                        with monkeypatch.context() as patch:
                            patch.setattr(actual, "_read_csv_bytes", parsed)
                            owner.has_direct(tmp_path, row, "ㄅ")
                    else:
                        same_metadata_change(target)
                        if moment != "exit":
                            owner.has_direct(tmp_path, row, "ㄅ")
            assert owner.closed and not owner.raw
            # A new operation sees current bytes rather than inheriting rejection.
            with query(tmp_path) as fresh:
                assert not fresh.has_direct(tmp_path, row, "ㄅ")


def test_absence_transitions_lazy_first_need_and_observed_change(tmp_path):
    row = entry()
    write_sources(tmp_path, [override(row)], [event(row)])
    override_path = tmp_path / actual.OCCURRENCE_OVERRIDE_FILE
    provenance = tmp_path / actual.GLYPH_PROVENANCE_FILE
    override_path.unlink()
    with pytest.raises(ValueError, match="source changed"), query(tmp_path) as owner:
        assert not owner.has_direct(tmp_path, row, "ㄅ")
        actual._write_csv(override_path, actual.OVERRIDE_HEADERS, [override(row)])
        owner.has_direct(tmp_path, row, "ㄅ")
    # Required common capture still sees a file. Before the first actual need,
    # a missing provenance uses the original [] behavior, not a new error.
    with query(tmp_path) as owner:
        provenance.unlink()
        assert not owner.has_direct(tmp_path, row, "ㄅ")
        assert owner.raw[actual.GLYPH_PROVENANCE_FILE] is None
    actual._write_csv(provenance, actual.GLYPH_PROVENANCE_HEADERS, [])
    with pytest.raises(ValueError, match="source changed"), query(tmp_path) as owner:
        provenance.unlink()
        assert not owner.has_direct(tmp_path, row, "ㄅ")
        actual._write_csv(provenance, actual.GLYPH_PROVENANCE_HEADERS, [event(row)])
        owner.has_direct(tmp_path, row, "ㄅ")
    with pytest.raises(ValueError, match="source changed"), query(tmp_path) as owner:
        assert owner.has_direct(tmp_path, row, "ㄅ")
        provenance.unlink()
    actual._write_csv(provenance, actual.GLYPH_PROVENANCE_HEADERS, [])
    with query(tmp_path) as owner:
        actual._write_csv(provenance, actual.GLYPH_PROVENANCE_HEADERS, [event(row)])
        assert owner.has_direct(tmp_path, row, "ㄅ")  # first need captures new bytes


def test_fresh_root_path_binding_and_unused_provenance_remain_lazy(tmp_path, monkeypatch):
    row = entry()
    write_sources(tmp_path, [override(row)] * 2, [event(row)])
    provenance = tmp_path / actual.GLYPH_PROVENANCE_FILE
    with query(tmp_path) as owner:
        assert not owner.has_direct(tmp_path, row, "ㄅ")
        provenance.write_bytes(b"unused corrupt\xff")
    assert owner.closed
    resolve = Path.resolve
    for rebound_target in (tmp_path, tmp_path / actual.OCCURRENCE_OVERRIDE_FILE):
        with pytest.raises(ValueError, match="root changed|path changed"):
            with query(tmp_path) as owner:
                def resolved(path, *args, **kwargs):
                    return tmp_path / "foreign" / path.name if path == rebound_target else resolve(path, *args, **kwargs)
                with monkeypatch.context() as patch:
                    patch.setattr(Path, "resolve", resolved)
                    owner.has_direct(tmp_path, row, "ㄅ")


def collect_fixture(root):
    rows = make_source(root / "pdf", n=3)
    for index, row in enumerate(rows):
        row["actual"] = "" if index == 4 else "ㄅ"
        row["state"] = "PASS" if index % 2 == 0 else "ACTUAL_DECODE_ERROR"
    evidence = root / "evidence"
    overrides = [override(row) for row in rows if row["actual"]]
    events = [event(row) for row in rows[:3]]
    write_sources(evidence, overrides, events)
    pdf = Path(rows[0]["pdf"])
    context = {"session_id": "pa04-fixture", "pdfs": {
        pdf.name: {"pdf": str(pdf), "pdf_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest()}}}
    return rows, context, evidence, library.GlobalExactGlyphRepository(root / "global.sqlite3")


def test_complete_collector_real_work_counts_and_target_peers(tmp_path, monkeypatch):
    rows, context, root, repository = collect_fixture(tmp_path)
    counts, reads, retained = Counter(), Counter(), []
    parser, read_bytes = actual._read_csv_bytes, Path.read_bytes
    matched, hashed = actual._direct_override_matches, actual._hash_payload
    indexed = actual._DirectOccurrenceActualQuery._index
    source = actual._DirectOccurrenceActualQuery._read_source
    provenance = actual._DirectOccurrenceActualQuery._provenance
    finished = actual._DirectOccurrenceActualQuery._finish
    state = {"exit": False}
    def parsed(raw, name, headers):
        result = parser(raw, name, headers)
        counts["parse:" + name] += 1
        counts["materialized:" + name] += len(result)
        return result
    def read(path):
        raw = read_bytes(path)
        if path.parent == root:
            reads["all:" + path.name] += 1
            reads["bytes:" + path.name] += len(raw)
        return raw
    def checked(owner, name):
        lane = "exit" if state["exit"] else "capture" if name not in owner.raw else "query"
        reads[lane + ":" + name] += 1
        return source(owner, name)
    def index(values, fields):
        counts["indexed:" + fields[0]] += len(values)
        return indexed(values, fields)
    def match(*args):
        counts["searched:override"] += 1
        return matched(*args)
    def events(owner, oid):
        for item in provenance(owner, oid):
            counts["searched:provenance"] += 1
            yield item
    def hash_payload(payload):
        if "event_type" in payload:
            counts["event_hash"] += 1
        return hashed(payload)
    def finish(owner):
        retained.append({"raw_bytes": sum(len(raw or b"") for raw in owner.raw.values()),
                         "visible_object_bytes_lower_bound": sum(sys.getsizeof(raw) for raw in owner.raw.values())
                             + sum(sys.getsizeof(index) + sum(sys.getsizeof(key) + sys.getsizeof(bucket)
                                 + sum(sys.getsizeof(row) for row in bucket) for key, bucket in index.items())
                                 for index in owner.indexes.values()),
                         "files": sorted(owner.raw),
                         "row_references": {name: sum(len(bucket) for bucket in index.values())
                                            for name, index in owner.indexes.items()}})
        state["exit"] = True
        try:
            return finished(owner)
        finally:
            state["exit"] = False
    monkeypatch.setattr(actual, "_read_csv_bytes", parsed)
    monkeypatch.setattr(Path, "read_bytes", read)
    monkeypatch.setattr(actual._DirectOccurrenceActualQuery, "_read_source", checked)
    monkeypatch.setattr(actual._DirectOccurrenceActualQuery, "_index", staticmethod(index))
    monkeypatch.setattr(actual._DirectOccurrenceActualQuery, "_provenance", events)
    monkeypatch.setattr(actual._DirectOccurrenceActualQuery, "_finish", finish)
    monkeypatch.setattr(actual, "_direct_override_matches", match)
    monkeypatch.setattr(actual, "_hash_payload", hash_payload)
    result = learning.collect_learning_candidates(rows, context, repository=repository, evidence_root=root)
    assert len(result["groups"]) == 2
    assert result["suppressed"] == [dict(occurrence_id=row["occurrence_id"], reason="DIRECT_POSITION_ALREADY_CONFIRMED")
                                    for row in rows[:3]]
    assert counts["parse:" + actual.OCCURRENCE_OVERRIDE_FILE] == 1  # common capture only, extra Q -> 0
    assert counts["parse:" + actual.GLYPH_PROVENANCE_FILE] == 1  # U=5 -> 1
    assert counts["searched:override"] == 5 and counts["searched:provenance"] == 3
    assert counts["event_hash"] == 3
    assert counts["materialized:" + actual.OCCURRENCE_OVERRIDE_FILE] == 5
    assert counts["indexed:pdf_contains"] == 5 and counts["indexed:event_type"] == 3
    for name in (actual.OCCURRENCE_OVERRIDE_FILE, actual.GLYPH_PROVENANCE_FILE):
        assert reads["query:" + name] == 5 and reads["exit:" + name] == 1
    assert reads["capture:" + actual.GLYPH_PROVENANCE_FILE] == 1
    assert retained[0]["files"] == sorted([actual.OCCURRENCE_OVERRIDE_FILE, actual.GLYPH_PROVENANCE_FILE])
    assert retained[0]["raw_bytes"] == sum((root / name).stat().st_size for name in retained[0]["files"])
    full_counts = dict(counts=dict(counts), reads=dict(reads), retained=retained[:])
    counts.clear()
    reads.clear()
    retained.clear()
    # Keep all peers even when only some candidates survive the position filter.
    target_id = result["groups"][0]["group_id"]
    target = learning.collect_learning_candidates(rows, context, repository=repository, evidence_root=root,
                                                  target_group_id=target_id)
    assert target["groups"] == [result["groups"][0]]
    assert len(target["groups"][0]["members"]) == 2
    assert counts["parse:" + actual.OCCURRENCE_OVERRIDE_FILE] == 1
    assert counts["parse:" + actual.GLYPH_PROVENANCE_FILE] == 1
    assert counts["searched:override"] == 2 and counts["searched:provenance"] == 1
    for name in (actual.OCCURRENCE_OVERRIDE_FILE, actual.GLYPH_PROVENANCE_FILE):
        assert reads["query:" + name] == 2 and reads["exit:" + name] == 1
    assert reads["capture:" + actual.GLYPH_PROVENANCE_FILE] == 1
    assert not repository.path.exists()
    if folder := os.environ.get("PA04_EVIDENCE_DIR"):
        with (Path(folder) / "candidate-real-work-counts.json").open("x", encoding="utf-8") as stream:
            json.dump(dict(full=full_counts, target=dict(counts=dict(counts), reads=dict(reads), retained=retained),
                           memory_limit="Visible object sizes are a lower bound; proxy backing dicts and allocator peak excluded."),
                      stream, indent=2)


def test_collector_exit_change_rejects_complete_result_and_cleans_owner(tmp_path, monkeypatch):
    rows, context, root, repository = collect_fixture(tmp_path)
    original, owners = actual._DirectOccurrenceActualQuery.has_direct, []
    def checked(owner, *args):
        result = original(owner, *args)
        owners.append(owner)
        if len(owners) == 5:
            same_metadata_change(root / actual.OCCURRENCE_OVERRIDE_FILE)
        return result
    monkeypatch.setattr(actual._DirectOccurrenceActualQuery, "has_direct", checked)
    with pytest.raises(ValueError, match="source changed"):
        learning.collect_learning_candidates(rows, context, repository=repository, evidence_root=root)
    assert len(owners) == 5 and all(owner.closed and not owner.raw and not owner.indexes for owner in owners)
    assert actual._direct_occurrence_query.get() is None


def test_prepared_collector_reads_dirty_rows_and_never_changes_or_flushes_state(tmp_path, monkeypatch):
    rows, context, root, repository = collect_fixture(tmp_path)
    # Disk says first three direct. Dirty candidate says only the last direct.
    state = actual._PreparedActualCSV(root)
    state.write(root / actual.OCCURRENCE_OVERRIDE_FILE, actual.OVERRIDE_HEADERS, [override(rows[-1], source=" visual ")])
    state.write(root / actual.GLYPH_PROVENANCE_FILE, actual.GLYPH_PROVENANCE_HEADERS, [event(rows[-1])])
    before = copy.deepcopy((state.rows, state.dirty, state.original, state.initialized, state.write_phase))
    disk = {path.name: path.read_bytes() for path in root.iterdir() if path.is_file()}
    foreign = tmp_path / "foreign"
    write_sources(foreign, [override(rows[-1])], [event(rows[-1])])
    calls, public = [], actual.has_direct_occurrence_actual
    finished, candidate_owners = actual._DirectOccurrenceActualQuery._finish, []
    def finish(owner):
        if actual._prepared_actual_csv.get() is state:
            assert owner.raw == {} and owner.indexes == {} and owner.used == set()
            candidate_owners.append(owner)
        return finished(owner)
    monkeypatch.setattr(actual._DirectOccurrenceActualQuery, "_finish", finish)
    def direct(*args):
        calls.append(args[1]["occurrence_id"])
        return public(*args)
    monkeypatch.setattr(actual, "has_direct_occurrence_actual", direct)
    def forbidden(*args, **kwargs):
        raise AssertionError("readonly collector must never flush")
    monkeypatch.setattr(actual._PreparedActualCSV, "flush", forbidden)
    token = actual._prepared_actual_csv.set(state)
    try:
        result = learning.collect_learning_candidates(rows, context, repository=repository, evidence_root=root)
        assert result["suppressed"] == [dict(occurrence_id=rows[-1]["occurrence_id"], reason="DIRECT_POSITION_ALREADY_CONFIRMED")]
        assert len(calls) == 5
        assert (state.rows, state.dirty, state.original, state.initialized, state.write_phase) == before
        assert {path.name: path.read_bytes() for path in root.iterdir() if path.is_file()} == disk
        with pytest.raises(ValueError, match="header"):
            state.read(root / actual.OCCURRENCE_OVERRIDE_FILE, ["wrong"])
        # Foreign-root guard executes through the unchanged public API.
        with pytest.raises(ValueError, match="foreign root"):
            actual.has_direct_occurrence_actual(foreign, rows[-1], "ㄅ")
        with pytest.raises(ValueError, match="foreign root"):
            learning.collect_learning_candidates(rows, context, repository=repository, evidence_root=foreign)
    finally:
        actual._prepared_actual_csv.reset(token)
    assert candidate_owners and all(owner.closed for owner in candidate_owners)
    result = learning.collect_learning_candidates(rows, context, repository=repository, evidence_root=root)
    assert [row["occurrence_id"] for row in result["suppressed"]] == [row["occurrence_id"] for row in rows[:3]]
