from __future__ import annotations

import copy
from contextlib import closing
import hashlib
import io
import json
import multiprocessing
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import actual_review as review
import global_exact_glyph_library as library
import global_glyph_promotion as promotion
import standalone_proofread as pipeline
from test_exact_glyph_identity_v580 import simple_glyph, composite_glyph, synthetic_sfnt


def sha(value):
    return hashlib.sha256(str(value).encode()).hexdigest()


def intent(index=1, reading="ㄅ", *, identity=None, **overrides):
    identity = identity or {
        "kind": library.TTF_GLYF_SHA256, "style_group": "", "glyph_sha256": sha("glyph"),
        "identity_eligibility": library.GLOBAL_ELIGIBLE_SIMPLE_GLYF_V1,
    }
    source = {
        "reading": reading, "source_project_id": f"project-{index}",
        "source_pdf_sha256": sha(f"pdf-{index}"),
        "source_font_program_sha256": sha(f"font-{index}"),
        "source_occurrence_id": "occ_" + sha(f"occ-{index}"), "source_review_id": f"review-{index}",
        "decision_snapshot_sha256": sha(f"decision-{index}"),
    }
    source.update(overrides)
    return library.make_direct_evidence_intent(identity, **source)


def sql_rows(repo, table):
    with closing(sqlite3.connect(repo.path)) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute("SELECT * FROM " + table)]


def approve(repo, **overrides):
    glyph = sql_rows(repo, "glyph_truth")[0]
    evidence = sql_rows(repo, "source_evidence")
    ids = sorted(row["evidence_id"] for row in evidence)
    args = {
        "glyph_id": glyph["glyph_id"], "reading": evidence[0]["reading"],
        "glyph_revision": glyph["revision"], "quorum_evidence_ids": ids,
        "quorum_digest": library.compute_quorum_digest(glyph["glyph_id"], evidence[0]["reading"], ids),
        "promotion_policy_version": library.GLOBAL_PROMOTION_POLICY_VERSION,
        "approval_source": library.EXPLICIT_MANUAL_APPROVAL,
    }
    args.update(overrides)
    return library.approve_global_exact_glyph(repo, **args)


def deliver_worker(root, items, ready, queue, crash=False):
    repo = library.GlobalExactGlyphRepository.resolved(root, busy_timeout_ms=5000, write_retry_limit=2)
    ready.wait(20)
    try:
        if crash:
            original = library._ingest_direct_evidence
            def interrupted(transaction, item):
                original(transaction, item)
                os._exit(17)
            library._ingest_direct_evidence = interrupted
        receipts = library.deliver_global_direct_evidence(repo, items)
        queue.put(("ok", [r.receipt_digest for r in receipts]))
    except Exception as exc:
        queue.put(("error", type(exc).__name__, str(exc)))


def project_crash_worker(root):
    with promotion.direct_visual_project_transaction(Path(root)):
        (Path(root) / promotion.PROJECT_FILES[0]).write_bytes(b"uncommitted")
        promotion.enqueue_promotion_intents(Path(root), [intent()])
        os._exit(19)


class GlobalPromotionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="phase4-global-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = library.GlobalExactGlyphRepository.resolved(self.root / "store")

    def deliver(self, *items):
        return library.deliver_global_direct_evidence(self.repo, list(items))

    def test_first_source_candidate_and_retry_receipt_no_extra_effect(self):
        first = self.deliver(intent())[0]
        again = self.deliver(intent())[0]
        self.assertEqual(first.receipt_digest, again.receipt_digest)
        self.assertTrue(again.already_processed)
        self.assertEqual(len(sql_rows(self.repo, "source_evidence")), 1)
        self.assertEqual(self.repo.load_snapshot().generation, 1)
        self.assertEqual(sql_rows(self.repo, "glyph_truth")[0]["state"], library.CANDIDATE)
        self.assertFalse(self.repo.load_snapshot().trusted_identities)

    def test_each_required_independence_axis_is_a_gate(self):
        for axis, value in (
            ("source_project_id", "project-1"),
            ("source_pdf_sha256", sha("pdf-1")),
            ("source_font_program_sha256", sha("font-1")),
            ("source_occurrence_id", "occ_" + sha("occ-1")),
            ("source_review_id", "review-1"),
        ):
            with self.subTest(axis=axis):
                repo = library.GlobalExactGlyphRepository.resolved(self.root / axis)
                library.deliver_global_direct_evidence(repo, [intent(), intent(2, **{axis: value})])
                self.assertEqual(sql_rows(repo, "glyph_truth")[0]["state"], library.CANDIDATE)

    def test_same_occurrence_reconfirmed_cannot_form_quorum(self):
        self.deliver(intent(), intent(1, decision_snapshot_sha256=sha("second-decision")))
        self.assertEqual(sql_rows(self.repo, "glyph_truth")[0]["state"], library.CANDIDATE)

    def test_complete_independence_ready_without_auto_approval(self):
        self.deliver(intent(), intent(2))
        self.assertEqual(sql_rows(self.repo, "glyph_truth")[0]["state"], library.PROMOTION_READY)
        self.assertFalse(self.repo.load_snapshot().trusted_identities)
        self.assertEqual(sql_rows(self.repo, "promotion_approval"), [])
        self.assertEqual(self.repo.load_snapshot().generation, 1)
        approved = approve(self.repo)
        self.assertEqual(approved["state"], library.VERIFIED_GLOBAL)
        self.assertEqual(len(self.repo.load_snapshot().trusted_identities), 1)

    def test_stale_or_invalid_explicit_approval_is_atomic(self):
        self.deliver(intent(), intent(2))
        ids = sorted(row["evidence_id"] for row in sql_rows(self.repo, "source_evidence"))
        glyph = sql_rows(self.repo, "glyph_truth")[0]
        for change in (
            {"glyph_revision": 0}, {"quorum_digest": sha("stale")}, {"reading": "ㄆ"},
            {"quorum_evidence_ids": [ids[0], sha("absent")]},
            {"promotion_policy_version": "future"},
            {"approval_source": library.MIGRATION_REVIEW_APPROVAL},
            {"quorum_evidence_ids": [ids[0], ids[0]]},
            {"quorum_evidence_ids": ids[::-1]},
        ):
            with self.subTest(change=change), self.assertRaises(library.GlobalLibraryError):
                approve(self.repo, **change)
            self.assertEqual(sql_rows(self.repo, "glyph_truth")[0], glyph)
            self.assertEqual(sql_rows(self.repo, "promotion_approval"), [])
        self.deliver(intent(3))
        with self.assertRaises(library.GlobalLibraryError):
            approve(self.repo, quorum_evidence_ids=ids,
                    quorum_digest=library.compute_quorum_digest(glyph["glyph_id"], "ㄅ", ids))

    def test_contradiction_quarantines_retains_and_revokes_without_auto_resolution(self):
        self.deliver(intent(), intent(2))
        approve(self.repo)
        self.deliver(intent(3, "ㄆ"))
        glyph = sql_rows(self.repo, "glyph_truth")[0]
        self.assertEqual(glyph["state"], library.QUARANTINED_CONFLICT)
        self.assertIsNone(glyph["active_reading"])
        self.assertEqual({row["reading"] for row in sql_rows(self.repo, "source_evidence")}, {"ㄅ", "ㄆ"})
        self.assertEqual({row["status"] for row in sql_rows(self.repo, "promotion_approval")},
                         {library.APPROVED, library.REVOKED})
        self.assertFalse(self.repo.load_snapshot().trusted_identities)
        self.deliver(intent(4, "ㄇ"), intent(5, "ㄅ"))
        conflict = sql_rows(self.repo, "glyph_conflict")
        self.assertEqual(len(conflict), 1)
        self.assertEqual(json.loads(conflict[0]["conflicting_readings_json"]), ["ㄅ", "ㄆ", "ㄇ"])
        self.assertEqual(conflict[0]["status"], library.OPEN)
        with self.assertRaises(library.GlobalLibraryError):
            approve(self.repo)
        self.repo.load_snapshot()

    def test_same_reading_growth_preserves_approved_revision_and_logical_digest(self):
        first = intent()
        self.deliver(first, intent(2))
        approve(self.repo)
        exact = library.canonical_global_exact_identity(**first["payload"]["identity"])
        before = library.global_exact_glyph_evidence_hashes(self.repo.load_snapshot(), (exact,))
        revision = sql_rows(self.repo, "glyph_truth")[0]["revision"]
        self.deliver(intent(3))
        self.assertEqual(revision, sql_rows(self.repo, "glyph_truth")[0]["revision"])
        self.assertEqual(before, library.global_exact_glyph_evidence_hashes(self.repo.load_snapshot(), (exact,)))

    def test_batch_prevalidation_precedes_initialization(self):
        for bad in (
            dict(intent(), unexpected=True),
            dict(intent(), intent_id="bad"),
            dict(intent(), payload_digest=sha("wrong")),
        ):
            with self.assertRaises(library.GlobalLibraryError):
                self.deliver(intent(2), bad)
            self.assertFalse(self.repo.path.parent.exists())
        with self.assertRaises(library.GlobalLibraryError):
            self.deliver(intent(), intent())
        self.assertFalse(self.repo.path.parent.exists())

    def test_same_id_different_payload_rejected_and_preserves_store(self):
        original = intent()
        self.deliver(original)
        changed = copy.deepcopy(original)
        changed["payload"]["evidence"]["reading"] = "ㄆ"
        with self.assertRaises(library.GlobalLibraryError):
            self.deliver(changed)
        # Existing processed_intent conflict is also checked before mutations.
        with self.repo.write_transaction() as transaction:
            transaction.record_processed_intent(
                intent_id=intent(2)["intent_id"], payload_digest=sha("different"),
                committed_generation=1, result_state=library.INTENT_NO_EFFECT,
            )
        with self.assertRaises(library.GlobalLibraryIntentConflictError):
            self.deliver(intent(2), intent(3))
        self.assertEqual(len(sql_rows(self.repo, "source_evidence")), 1)

    def test_late_mutation_exception_rolls_back_entire_batch(self):
        self.repo.initialize()
        original = library._ingest_direct_evidence
        calls = []
        def fail_second(transaction, item):
            calls.append(item)
            result = original(transaction, item)
            if len(calls) == 2:
                raise RuntimeError("injected late mutation failure")
            return result
        with patch.object(library, "_ingest_direct_evidence", side_effect=fail_second):
            with self.assertRaisesRegex(RuntimeError, "late mutation"):
                self.deliver(intent(), intent(2))
        self.assertEqual(self.repo.load_snapshot().generation, 0)
        for table in ("source_evidence", "glyph_truth", "processed_intent", "provenance_event"):
            self.assertEqual(sql_rows(self.repo, table), [])

    def test_absent_read_does_not_initialize_but_explicit_delivery_does(self):
        self.assertEqual(self.repo.load_snapshot().store_status, library.ABSENT)
        self.assertFalse(self.repo.path.parent.exists())
        self.deliver(intent())
        self.assertTrue(self.repo.path.is_file())

    def test_existing_invalid_store_is_never_reinitialized(self):
        self.repo.path.parent.mkdir()
        self.repo.path.write_bytes(b"invalid store")
        with self.assertRaises(library.GlobalLibraryError):
            self.deliver(intent())
        self.assertEqual(self.repo.path.read_bytes(), b"invalid store")

    def test_result_affecting_subset_only_and_snapshot_remains_immutable(self):
        item = intent()
        exact = library.canonical_global_exact_identity(**item["payload"]["identity"])
        other = library.canonical_global_exact_identity(
            library.TTF_GLYF_SHA256, "", sha("other"), library.GLOBAL_ELIGIBLE_SIMPLE_GLYF_V1,
        )
        absent = self.repo.load_snapshot()
        initial = library.global_exact_glyph_evidence_hashes(absent, (exact,))
        other_initial = library.global_exact_glyph_evidence_hashes(absent, (other,))
        self.deliver(item, intent(2))
        self.assertEqual(initial, library.global_exact_glyph_evidence_hashes(self.repo.load_snapshot(), (exact,)))
        approve(self.repo)
        verified = self.repo.load_snapshot()
        verified_hash = library.global_exact_glyph_evidence_hashes(verified, (exact,))
        self.assertNotEqual(initial, verified_hash)
        self.deliver(intent(3, "ㄆ"))
        self.assertNotEqual(verified_hash, library.global_exact_glyph_evidence_hashes(self.repo.load_snapshot(), (exact,)))
        self.assertEqual(verified_hash, library.global_exact_glyph_evidence_hashes(verified, (exact,)))
        self.assertEqual(other_initial, library.global_exact_glyph_evidence_hashes(self.repo.load_snapshot(), (other,)))

    def concurrent(self, left, right):
        context = multiprocessing.get_context("spawn")
        barrier, queue = context.Barrier(2), context.Queue()
        workers = [context.Process(target=deliver_worker, args=(str(self.repo.path.parent), items, barrier, queue))
                   for items in (left, right)]
        for worker in workers:
            worker.start()
        outcomes = [queue.get(timeout=40) for _ in workers]
        for worker in workers:
            worker.join(40)
            self.assertFalse(worker.is_alive())
            self.assertEqual(worker.exitcode, 0)
        queue.close()
        self.assertTrue(all(outcome[0] == "ok" for outcome in outcomes), outcomes)
        return outcomes

    def test_multiprocess_independent_same_reading_serializes_to_ready(self):
        self.concurrent([intent()], [intent(2)])
        self.assertEqual(len(sql_rows(self.repo, "source_evidence")), 2)
        self.assertEqual(sql_rows(self.repo, "glyph_truth")[0]["state"], library.PROMOTION_READY)
        self.repo.load_snapshot()

    def test_multiprocess_different_reading_always_quarantines(self):
        self.concurrent([intent()], [intent(2, "ㄆ")])
        self.assertEqual(sql_rows(self.repo, "glyph_truth")[0]["state"], library.QUARANTINED_CONFLICT)
        self.assertEqual(len(sql_rows(self.repo, "source_evidence")), 2)
        self.repo.load_snapshot()

    def test_multiprocess_same_intent_preserves_one_effect_and_receipt(self):
        results = self.concurrent([intent()], [intent()])
        self.assertEqual(results[0][1], results[1][1])
        self.assertEqual(len(sql_rows(self.repo, "source_evidence")), 1)
        self.assertEqual(self.repo.load_snapshot().generation, 1)

    def test_process_crash_before_sqlite_commit_is_retryable(self):
        self.repo.initialize()
        context = multiprocessing.get_context("spawn")
        barrier, queue = context.Barrier(1), context.Queue()
        worker = context.Process(target=deliver_worker,
                                 args=(str(self.repo.path.parent), [intent()], barrier, queue, True))
        worker.start()
        worker.join(30)
        self.assertEqual(worker.exitcode, 17)
        queue.close()
        self.assertEqual(self.repo.load_snapshot().generation, 0)
        self.assertEqual(sql_rows(self.repo, "source_evidence"), [])
        self.deliver(intent())
        self.assertEqual(len(sql_rows(self.repo, "source_evidence")), 1)


class OutboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="phase4-outbox-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        self.repo = library.GlobalExactGlyphRepository.resolved(Path(self.temp.name) / "global")

    def test_refresh_marker_survives_delivery_and_blocks_newer_transaction(self):
        with promotion.direct_visual_project_transaction(self.root) as bind:
            bind({"schema_version": "1.0", "affected_occurrence_ids": [], "checked_postconditions": []})
            promotion.enqueue_promotion_intents(self.root, [intent()])
        token = promotion.project_refresh_token(self.root)
        self.assertEqual(promotion.deliver_pending_promotion_outbox(self.root, self.repo)["status"], "DELIVERED")
        before = (self.root / promotion.PROJECT_TRANSACTION_FILE).read_bytes()
        with self.assertRaises(library.GlobalLibraryValidationError):
            with promotion.direct_visual_project_transaction(self.root):
                self.fail("new transaction must not start")
        self.assertEqual((self.root / promotion.PROJECT_TRANSACTION_FILE).read_bytes(), before)
        promotion.acknowledge_project_refresh(self.root, token)
        self.assertIsNone(promotion.project_refresh_token(self.root))

    def test_prepared_and_malformed_journal_read_paths_fail_without_mutation(self):
        with promotion.direct_visual_project_transaction(self.root) as bind:
            bind({"schema_version": "1.0", "affected_occurrence_ids": [], "checked_postconditions": []})
            with self.assertRaises(library.GlobalLibraryValidationError):
                review.validate_dynamic_actual_evidence(self.root)
            self.assertFalse((self.root / promotion.PROJECT_FILES[0]).exists())
        path = self.root / promotion.PROJECT_TRANSACTION_FILE
        journal = json.loads(path.read_text())
        journal["files"][promotion.PROJECT_FILES[0]] = {"base64": "eA==", "sha256": sha("wrong")}
        raw = json.dumps(journal).encode()
        path.write_bytes(raw)
        with self.assertRaises(library.GlobalLibraryValidationError):
            promotion.project_refresh_token(self.root)
        self.assertEqual(path.read_bytes(), raw)

    def test_strict_schema_rejects_every_malformed_item_without_repair(self):
        promotion.enqueue_promotion_intents(self.root, [intent()])
        path = self.root / promotion.GLOBAL_PROMOTION_OUTBOX_FILE
        original = promotion.load_promotion_outbox(self.root)
        bad_documents = []
        for key, value in (("schema_version", "future"), ("unknown", 1), ("items", {})):
            bad_documents.append(dict(original, **{key: value}))
        for change in (
            {"unknown": True}, {"intent_id": "malformed"}, {"payload_digest": "bad"},
            {"status": "unknown"}, {"status": "DELIVERED"}, {"receipt": {}},
        ):
            changed = copy.deepcopy(original)
            changed["items"][0].update(change)
            bad_documents.append(changed)
        doubled = copy.deepcopy(original)
        doubled["items"].append(copy.deepcopy(doubled["items"][0]))
        bad_documents.append(doubled)
        for document in bad_documents:
            raw = json.dumps(document, ensure_ascii=False).encode()
            path.write_bytes(raw)
            with self.subTest(document=document), self.assertRaises(library.GlobalLibraryError):
                promotion.load_promotion_outbox(self.root)
            self.assertEqual(path.read_bytes(), raw)
        path.write_text('{"schema_version":"1.0","items":[],"items":[]}', encoding="utf-8")
        with self.assertRaises(library.GlobalLibraryError):
            promotion.load_promotion_outbox(self.root)

    def test_outbox_ack_failure_restart_reuses_original_receipt(self):
        promotion.enqueue_promotion_intents(self.root, [intent(), intent(2)])
        with patch.object(promotion, "acknowledge_promotion_delivery", side_effect=OSError("ack crash")):
            result = promotion.deliver_pending_promotion_outbox(self.root, self.repo)
        self.assertEqual(result["status"], "PENDING_RETRY")
        before = sql_rows(self.repo, "processed_intent")
        self.assertEqual({item["status"] for item in promotion.load_promotion_outbox(self.root)["items"]}, {"PENDING"})
        result = promotion.deliver_pending_promotion_outbox(self.root, self.repo)
        self.assertEqual(result["status"], "DELIVERED")
        self.assertEqual(before, sql_rows(self.repo, "processed_intent"))
        self.assertEqual(len(sql_rows(self.repo, "source_evidence")), 2)
        self.assertEqual(self.repo.load_snapshot().generation, 1)
        self.assertEqual({item["status"] for item in promotion.load_promotion_outbox(self.root)["items"]}, {"DELIVERED"})

    def test_global_failure_keeps_project_committed_and_pending(self):
        with promotion.direct_visual_project_transaction(self.root) as bind:
            bind({"schema_version": "1.0", "affected_occurrence_ids": [], "checked_postconditions": []})
            (self.root / promotion.PROJECT_FILES[0]).write_bytes(b"local correction")
            promotion.enqueue_promotion_intents(self.root, [intent()])
        with patch.object(library, "deliver_global_direct_evidence", side_effect=OSError("locked")):
            result = promotion.deliver_pending_promotion_outbox(self.root, self.repo)
        self.assertEqual(result["status"], "PENDING_RETRY")
        self.assertEqual((self.root / promotion.PROJECT_FILES[0]).read_bytes(), b"local correction")
        self.assertFalse(self.repo.path.exists())

    def test_project_six_file_rollback_and_global_untouched(self):
        for name in promotion.PROJECT_FILES[:-1]:
            (self.root / name).write_bytes(b"before " + name.encode())
        promotion.enqueue_promotion_intents(self.root, [intent()])
        before = {name: (self.root / name).read_bytes() for name in promotion.PROJECT_FILES}
        with self.assertRaisesRegex(RuntimeError, "project failure"):
            with promotion.direct_visual_project_transaction(self.root) as bind:
                bind({"schema_version": "1.0", "affected_occurrence_ids": [], "checked_postconditions": []})
                for name in promotion.PROJECT_FILES[:-1]:
                    (self.root / name).write_bytes(b"after")
                promotion.enqueue_promotion_intents(self.root, [intent(2)])
                raise RuntimeError("project failure")
        self.assertEqual(before, {name: (self.root / name).read_bytes() for name in promotion.PROJECT_FILES})
        self.assertFalse(self.repo.path.exists())

    def test_project_crash_journal_recovers_before_delivery(self):
        (self.root / promotion.PROJECT_FILES[0]).write_bytes(b"before")
        context = multiprocessing.get_context("spawn")
        worker = context.Process(target=project_crash_worker, args=(str(self.root),))
        worker.start()
        worker.join(30)
        self.assertEqual(worker.exitcode, 19)
        self.assertTrue((self.root / promotion.PROJECT_TRANSACTION_FILE).exists())
        result = promotion.deliver_pending_promotion_outbox(self.root, self.repo)
        self.assertEqual(result["status"], "NO_PENDING")
        self.assertEqual((self.root / promotion.PROJECT_FILES[0]).read_bytes(), b"before")
        self.assertFalse(self.repo.path.exists())
        self.assertFalse((self.root / promotion.GLOBAL_PROMOTION_OUTBOX_FILE).exists())

    def test_stale_ack_token_cannot_erase_newer_queue(self):
        promotion.enqueue_promotion_intents(self.root, [intent()])
        receipts = library.deliver_global_direct_evidence(self.repo, [intent()])
        promotion.enqueue_promotion_intents(self.root, [intent(2)])
        promotion.acknowledge_promotion_delivery(self.root, [intent()], receipts)
        rows = promotion.load_promotion_outbox(self.root)["items"]
        self.assertEqual({row["status"] for row in rows}, {"PENDING", "DELIVERED"})
        path = self.root / promotion.GLOBAL_PROMOTION_OUTBOX_FILE
        document = promotion.load_promotion_outbox(self.root)
        document["items"] = [row for row in document["items"] if row["intent_id"] != intent()["intent_id"]]
        promotion._write_json(path, document)
        with self.assertRaises(library.GlobalLibraryIntentConflictError):
            promotion.acknowledge_promotion_delivery(self.root, [intent()], receipts)

    def test_outbox_and_receipts_are_not_dynamic_actual_truth(self):
        review.ensure_user_evidence_files(self.root)
        before = review.dynamic_actual_hashes(self.root)
        promotion.enqueue_promotion_intents(self.root, [intent()])
        self.assertEqual(before, review.dynamic_actual_hashes(self.root))
        promotion.deliver_pending_promotion_outbox(self.root, self.repo)
        self.assertEqual(before, review.dynamic_actual_hashes(self.root))


class FakeDocument:
    def __init__(self, font, extension="ttf", name="synthetic"):
        self.font, self.extension, self.name = font, extension, name
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def __getitem__(self, index):
        if index != 0:
            raise IndexError(index)
        return self
    def get_fonts(self, full=True):
        return [(7, self.extension, "", self.name, "", "")]
    def get_texttrace(self):
        gid = 1 if self.extension == "cff" else 0
        return [{"font": self.name, "chars": [
            (ord("字"), gid, (10 + 20 * i, 10), (10 + 20 * i, 10, 20 + 20 * i, 30))
            for i in range(3)
        ]}]
    def extract_font(self, xref):
        if xref != 7:
            raise ValueError("wrong font")
        return self.name, self.extension, "", self.font


def cff_program():
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.t2CharStringPen import T2CharStringPen
    builder = FontBuilder(1000, isTTF=False)
    builder.setupGlyphOrder([".notdef", "cid00001"])
    pen = T2CharStringPen(600, None)
    pen.moveTo((0, 0))
    pen.lineTo((200, 0))
    pen.lineTo((200, 300))
    pen.closePath()
    empty = T2CharStringPen(600, None)
    builder.setupCFF("DFBiaoKaiZhuIn-W5", {}, {
        ".notdef": empty.getCharString(), "cid00001": pen.getCharString(),
    }, {})
    return builder.font["CFF "].compile(builder.font)


class AdmissionAndProjectIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="phase4-admit-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "actual"
        self.pdf = self.base / "book.pdf"
        self.pdf.write_bytes(b"sealed synthetic PDF fixture")
        self.context = {"session_id": "sealed-session", "pdfs": {
            self.pdf.name: {"pdf": str(self.pdf), "pdf_sha256": hashlib.sha256(self.pdf.read_bytes()).hexdigest()},
        }}

    def group(self, record=None, count=3):
        record = simple_glyph() if record is None else record
        font = synthetic_sfnt([record], font_name_marker=b"same-font")
        digest = hashlib.sha256(record).hexdigest()
        members = [{
            "occurrence_id": "occ_" + sha(f"occ-{index}"), "review_id": f"review-{index}",
            "pdf": str(self.pdf), "pdf_name": self.pdf.name, "physical_page": 1,
            "char": "字", "x0": 10 + index * 20, "y0": 10, "x1": 20 + index * 20, "y1": 30,
            "actual": "", "actual_evidence": "unresolved", "state": "ACTUAL_DECODE_ERROR",
            "stable_key": f"key-{index}",
            "source_record": {"TTF字形SHA256": digest, "font_xref": 7, "注音元件ID": 0},
        } for index in range(count)]
        group = review.build_actual_group_for_entry(members, members[0])
        return group, font

    def test_real_embedded_pdf_ttf_admission_and_locator_rejection(self):
        from fontTools.fontBuilder import FontBuilder
        from fontTools.pens.ttGlyphPen import TTGlyphPen
        builder = FontBuilder(1000, isTTF=True)
        builder.setupGlyphOrder([".notdef", "testGlyph"])
        builder.setupCharacterMap({ord("字"): "testGlyph"})
        empty = TTGlyphPen(None)
        pen = TTGlyphPen(None)
        pen.moveTo((0, 0)); pen.lineTo((200, 0)); pen.lineTo((200, 300)); pen.closePath()
        builder.setupGlyf({".notdef": empty.glyph(), "testGlyph": pen.glyph()})
        builder.setupHorizontalMetrics({".notdef": (600, 0), "testGlyph": (600, 0)})
        builder.setupHorizontalHeader(ascent=800, descent=-200)
        builder.setupNameTable({"familyName": "PhaseFourTest", "styleName": "Regular"})
        builder.setupOS2(sTypoAscender=800, sTypoDescender=-200, usWinAscent=800, usWinDescent=200)
        builder.setupPost()
        data = io.BytesIO(); builder.save(data)
        with promotion.fitz.open() as document:
            page = document.new_page()
            xref = page.insert_font(fontname="PhaseFour", fontbuffer=data.getvalue())
            page.insert_text((60, 80), "字", fontname="PhaseFour", fontsize=16)
            document.save(self.pdf)
        with promotion.fitz.open(self.pdf) as document:
            char = document[0].get_texttrace()[0]["chars"][0]
            font = document.extract_font(xref)[3]
        self.context["pdfs"][self.pdf.name]["pdf_sha256"] = hashlib.sha256(self.pdf.read_bytes()).hexdigest()
        identity = promotion.TrueTypeGlyphInspector(font).global_exact_identity(char[1])
        group, _ = self.group(count=1)
        member = group["members"][0]
        member.update(x0=char[3][0], y0=char[3][1], glyph_id=char[1])
        member["source_record"] = {"TTF字形SHA256": identity["glyph_sha256"],
                                   "font_xref": xref, "注音元件ID": char[1]}
        group = review.build_actual_group_for_entry([member], member)
        items, admissions = promotion.direct_visual_intents(group, "ㄅ", ["occ_" + sha("occ-0")], self.context)
        self.assertEqual(len(items), 1, admissions)
        group["members"][0]["x0"] += 20
        items, admissions = promotion.direct_visual_intents(group, "ㄅ", ["occ_" + sha("occ-0")], self.context)
        self.assertEqual(items, [])
        self.assertIn("UNPROVABLE", admissions[0]["reason"])

    def test_only_checked_current_ttf_creates_deterministic_intents(self):
        group, font = self.group()
        with patch.object(promotion.fitz, "open", return_value=FakeDocument(font)):
            first, admissions = promotion.direct_visual_intents(group, "ㄅ", ["occ_" + sha("occ-0"), "occ_" + sha("occ-1")], self.context)
            second, _ = promotion.direct_visual_intents(group, "ㄅ", ["occ_" + sha("occ-0"), "occ_" + sha("occ-1")], self.context)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 2)
        self.assertTrue(all(row["status"] == "ELIGIBLE" for row in admissions))
        self.assertEqual({item["payload"]["evidence"]["source_occurrence_id"] for item in first}, {"occ_" + sha("occ-0"), "occ_" + sha("occ-1")})
        self.assertTrue(all(item["payload"]["evidence"]["source_font_program_sha256"] ==
                            hashlib.sha256(font).hexdigest() for item in first))
        repo = library.GlobalExactGlyphRepository.resolved(self.base / "global")
        library.deliver_global_direct_evidence(repo, first)
        self.assertEqual(sql_rows(repo, "glyph_truth")[0]["state"], library.CANDIDATE)

    def test_ineligible_ttf_and_sha_mismatch_preserve_local_correction(self):
        for index, record in enumerate((composite_glyph(), b"\0" * 10, b"\0", simple_glyph())):
            group, font = self.group(record)
            if index == 3:
                font = synthetic_sfnt([simple_glyph(50)], font_name_marker=b"different")
            root = self.base / str(index)
            with patch.object(promotion.fitz, "open", return_value=FakeDocument(font)):
                result = review.apply_direct_visual_actual_batch(root, [{
                    "group": group, "reading": "ㄅ", "checked_occurrence_ids": ["occ_" + sha("occ-0")], "source": "visual",
                }], source_context=self.context)
            self.assertEqual(result["project_actual_commit"], "COMMITTED")
            self.assertEqual(result["group_results"][0]["global_admissions"][0]["status"], "NON_GLOBAL_ELIGIBLE")
            self.assertFalse((root / promotion.GLOBAL_PROMOTION_OUTBOX_FILE).exists())
            self.assertTrue((root / review.OCCURRENCE_OVERRIDE_FILE).exists())

    def test_pdf_font_page_and_missing_context_fail_global_admission_only(self):
        group, font = self.group()
        cases = [None, {"session_id": "sealed-session", "pdfs": {}}, copy.deepcopy(self.context)]
        cases[2]["pdfs"][self.pdf.name]["pdf_sha256"] = sha("wrong")
        for context in cases:
            with patch.object(promotion.fitz, "open", return_value=FakeDocument(font)):
                items, admissions = promotion.direct_visual_intents(group, "ㄅ", ["occ_" + sha("occ-0")], context)
            self.assertEqual(items, [])
            self.assertEqual(admissions[0]["status"], "NON_GLOBAL_ELIGIBLE")
        group["members"][0]["source_record"]["font_xref"] = 999
        with patch.object(promotion.fitz, "open", return_value=FakeDocument(font)):
            items, admissions = promotion.direct_visual_intents(group, "ㄅ", ["occ_" + sha("occ-0")], self.context)
        self.assertEqual(items, [])
        self.assertIn("FONT_NOT_ON", admissions[0]["reason"])

    def test_real_cff_parser_complete_recording_and_style_separation(self):
        data = cff_program()
        inspector = promotion.CFFZhuyinInspector(data, "DFBiaoKaiZhuIn-W5", {})
        identity = inspector.global_exact_identity(1)
        group, _ = self.group(count=1)
        member = group["members"][0]
        member["source_record"] = {
            "CFF樣式群組": "BIAOKAI_W5", "CFF整字字形SHA256": identity["glyph_sha256"],
            "font_xref": 7, "注音元件ID": 1,
        }
        group = review.build_actual_group_for_entry([member], member)
        with patch.object(promotion.fitz, "open", return_value=FakeDocument(data, "cff", "DFBiaoKaiZhuIn-W5")):
            items, admissions = promotion.direct_visual_intents(group, "ㄅ", ["occ_" + sha("occ-0")], self.context)
        self.assertEqual(admissions[0]["status"], "ELIGIBLE")
        other = intent(2, identity=dict(items[0]["payload"]["identity"], style_group="YUAN_W7"))
        self.assertNotEqual(items[0]["payload"]["evidence"]["glyph_id"], other["payload"]["evidence"]["glyph_id"])
        member["source_record"]["CFF整字字形SHA256"] = sha("untrusted workbook")
        with patch.object(promotion.fitz, "open", return_value=FakeDocument(data, "cff", "DFBiaoKaiZhuIn-W5")):
            items, admissions = promotion.direct_visual_intents(group, "ㄅ", ["occ_" + sha("occ-0")], self.context)
        self.assertEqual(items, [])

    def test_staged_ack_failure_rolls_back_six_and_retains_staging(self):
        group, font = self.group()
        review.stage_manual_actual_group(self.root, group, "ㄅ", checked_occurrence_ids=["occ_" + sha("occ-0")], source="visual")
        staging = (self.root / review.MANUAL_ACTUAL_STAGING_FILE).read_bytes()
        with (patch.object(promotion.fitz, "open", return_value=FakeDocument(font)),
              patch.object(review, "remove_staged_manual_actual_groups", side_effect=RuntimeError("ack failure")),
              self.assertRaisesRegex(RuntimeError, "ack failure")):
            review.apply_staged_manual_actual_batch(self.root, [group], source_context=self.context)
        self.assertEqual((self.root / review.MANUAL_ACTUAL_STAGING_FILE).read_bytes(), staging)
        self.assertTrue(all(not (self.root / name).exists() for name in promotion.PROJECT_FILES))

    def test_propagated_peers_commit_locally_but_never_become_direct_sources(self):
        group, font = self.group()
        with patch.object(promotion.fitz, "open", return_value=FakeDocument(font)):
            result = review.apply_direct_visual_actual_batch(self.root, [{
                "group": group, "reading": "ㄅ", "checked_occurrence_ids": ["occ_" + sha("occ-0"), "occ_" + sha("occ-1")],
                "source": "visual",
            }], source_context=self.context)
        self.assertTrue(result["group_results"][0]["propagated_to_group"])
        rows = promotion.load_promotion_outbox(self.root)["items"]
        self.assertEqual(len(rows), 2)
        self.assertNotIn("occ_" + sha("occ-2"), {row["payload"]["evidence"]["source_occurrence_id"] for row in rows})

    def test_low_level_unsealed_entry_is_explicitly_excluded(self):
        group, _ = self.group()
        result = review.apply_direct_visual_actual_batch(self.root, [{
            "group": group, "reading": "ㄅ", "checked_occurrence_ids": ["occ_" + sha("occ-0")], "source": "legacy caller",
        }])
        admission = result["group_results"][0]["global_admissions"][0]
        self.assertEqual(admission["status"], "NON_GLOBAL_ELIGIBLE")
        self.assertEqual(admission["reason"], "NO_SEALED_SESSION_CONTEXT")
        self.assertEqual(promotion.load_promotion_outbox(self.root)["items"], [])

    def test_delivery_failure_does_not_prevent_controller_refresh(self):
        output = self.base / "output"
        result = {"group_results": [{"affected_occurrence_ids": ["occ_" + sha("occ-0")]}]}
        with (patch.object(pipeline, "deliver_pending_promotion_outbox", return_value={"status": "PENDING_RETRY"}),
              patch.object(pipeline, "_clear_actual_dependent_events", return_value=1) as clear,
              patch.object(pipeline, "refresh_actual_project", return_value=output / "report.xlsx") as refresh):
            removed, _ = pipeline._finish_direct_actual_commit(output, [], result)
        clear.assert_called_once()
        refresh.assert_called_once()
        self.assertEqual(removed, 1)
        self.assertEqual(result["project_actual_commit"], "COMMITTED")
        self.assertEqual(result["global_promotion_delivery"]["status"], "PENDING_RETRY")
        self.assertEqual(result["project_refresh"], "SUCCESS")

    def test_successful_global_delivery_then_refresh_failure_is_structured(self):
        output = self.base / "output"
        root = pipeline.project_actual_evidence_root(output)
        promotion.enqueue_promotion_intents(root, [intent()])
        repo = library.GlobalExactGlyphRepository.resolved(self.base / "global")
        original = promotion.deliver_pending_promotion_outbox
        result = {"group_results": [{"affected_occurrence_ids": ["occ_" + sha("occ-0")]}]}
        with (patch.object(pipeline, "deliver_pending_promotion_outbox", side_effect=lambda root: original(root, repo)),
              patch.object(pipeline, "_clear_actual_dependent_events", return_value=1),
              patch.object(pipeline, "refresh_actual_project", side_effect=RuntimeError("refresh failure")),
              self.assertRaises(pipeline.ManualActualPostApplyError) as raised):
            pipeline._finish_direct_actual_commit(output, [], result)
        self.assertEqual(raised.exception.project_actual_commit, "COMMITTED")
        self.assertEqual(raised.exception.global_promotion_delivery["status"], "DELIVERED")
        self.assertEqual(raised.exception.project_refresh, "FAILED")
        self.assertEqual(len(sql_rows(repo, "source_evidence")), 1)
        self.assertEqual(promotion.load_promotion_outbox(root)["items"][0]["status"], "DELIVERED")


if __name__ == "__main__":
    unittest.main()
