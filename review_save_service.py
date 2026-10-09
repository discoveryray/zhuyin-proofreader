"""Content-verified, occurrence-local review saves; no Tk or PDF rendering.

The cache is a process-local optimization of a sealed session, not a new actual
or expected fingerprint. Every operation hashes the complete input bytes. An
unknown version falls back to a full materialization; an external edit that no
longer matches the displayed manifest/DB is rejected, never silently rebased.
Returned snapshots are read-only by convention: callers replace, never mutate,
their DB/ledger. Export/completion continue through the full existing gates.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import actual_review as ar
import standalone_proofread as sp
from runtime_source_validation import ASSET_MANIFEST_SCHEMA_VERSION, REQUIRED_ASSET_CHAINS


class StaleReviewProjectError(ValueError):
    """The operation no longer belongs to the project the user reviewed."""


@dataclass(frozen=True)
class ReviewEvidenceAnchor:
    """Previously verified inputs for one sealed session, independent of cache.

    This process-local guard preserves known evidence drift across retries and
    queue reloads. It neither replaces the pipeline fingerprint nor introduces
    a new first-open validation contract.
    """
    manifest_seal: str
    frozen_inputs: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class SaveResult:
    """Read-only GUI snapshot; version_token is NOT a pipeline fingerprint.

    It binds this whole operation to input bytes for asynchronous publication.
    It is never persisted in either actual or expected evidence/fingerprints.
    """
    db: dict[str, Any]
    ledger: list[dict[str, Any]]
    resolved_entry: dict[str, Any]
    staging_summary: dict[str, Any]
    timings: dict[str, float]
    version_token: str
    resume_snapshot: ReviewResumeSnapshot | None = None


@dataclass(frozen=True)
class ReviewResumeSnapshot:
    """Private operation-owned data, never mutated by the GUI or persisted."""
    manifest: dict[str, Any]
    db: dict[str, Any]
    baseline: list[dict[str, Any]]
    ledger: list[dict[str, Any]]
    content: dict[Path, bytes | None]
    hashes: dict[str, str]
    timings: dict[str, float]


def _digest(raw: bytes | None) -> str:
    return hashlib.sha256(raw).hexdigest() if raw is not None else ""


def _read(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _json(raw: bytes | None, path: Path):
    if raw is None:
        raise FileNotFoundError(path)

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"JSON 重複 key：{key}")
            result[key] = value
        return result

    return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique)


class ReviewSaveService:
    """One worker at a time. Constructor and invalidation do no heavy work."""

    def __init__(self, output_dir: Path, *, evidence_anchor: ReviewEvidenceAnchor | None = None):
        self.output_dir = Path(output_dir).resolve()
        self._lock = threading.Lock()
        self._save_lock = threading.Lock()
        self._evidence_anchor = evidence_anchor
        self.invalidate()

    @property
    def evidence_anchor(self):
        return self._evidence_anchor

    def with_invalidated_cache(self):
        """Replace a GUI worker cache without forgetting verified evidence."""
        return ReviewSaveService(self.output_dir, evidence_anchor=self._evidence_anchor)

    def invalidate(self):
        # Materialization is disposable. The independently retained anchor is
        # not: retrying a failed save is not proof that changed evidence is safe.
        self._manifest = self._db = self._baseline = self._ledger = None
        self._manifest_sha = self._db_sha = None
        self._index = {}
        self._dependencies = None
        self._runtime_paths = None
        self._source_specs = None
        self._artifact_specs = None

    def _runtime(self, timings):
        root = Path(sp.__file__).resolve().parent
        manifest_path = root / "runtime_asset_manifest.json"
        if self._runtime_paths is None:
            started = time.perf_counter()
            raw = _read(manifest_path)
            manifest = _json(raw, manifest_path)
            assets = manifest.get("assets")
            # This is a content-change guard, not a substitute for the runtime
            # schema validator used by the pipeline/report. Review replay uses
            # the sealed ledger; it does not parse dictionary/decoder assets.
            if (not sp.schema_compatible(manifest.get("schema_version"), ASSET_MANIFEST_SCHEMA_VERSION)
                    or not isinstance(assets, list) or not all(isinstance(item, dict) for item in assets)
                    or len(assets) != len(REQUIRED_ASSET_CHAINS)
                    or {item.get("name"): item.get("chain") for item in assets} != REQUIRED_ASSET_CHAINS):
                raise sp.SourceValidationError("runtime asset manifest schema／固定核心資產清單不符")
            paths = [(manifest_path, _digest(raw))]
            seen = {manifest_path}
            for item in assets:
                path = (root / str(item.get("path") or "")).resolve()
                wanted = str(item.get("sha256") or "").lower()
                if (not path.is_relative_to(root) or path in seen
                        or not re.fullmatch(r"[0-9a-f]{64}", wanted)):
                    raise sp.SourceValidationError("runtime asset manifest 路徑／SHA 不符")
                seen.add(path)
                paths.append((path, wanted))
            self._runtime_paths = paths
            timings["runtime_manifest_validation"] = time.perf_counter() - started
        return self._runtime_paths

    def _specs(self, manifest):
        sources, artifacts = {}, {}
        # Include records as well: old sessions and synthetic contract fixtures
        # may have a partial PDF inventory. Conflicting identities fail closed.
        seen = set()
        for item in [*manifest.get("pdfs", []), *manifest.get("records", [])]:
            wanted = str(item.get("pdf_sha256") or "")
            key = (str(item.get("pdf") or ""), str(item.get("pdf_name") or ""), wanted)
            if key in seen:
                continue
            seen.add(key)
            path = sp._resolve_pdf_path(item, self.output_dir).resolve()
            if not wanted or (path in sources and sources[path] != wanted):
                raise ValueError("SOURCE_INVALID：同一 PDF 的來源 hash 缺失或不一致")
            sources[path] = wanted
        for item in manifest.get("pdfs", []):
            for kind in ("actual", "candidate"):
                path = Path(str(item.get(f"{kind}_workbook") or "")).resolve()
                wanted = str(item.get(f"{kind}_workbook_sha256") or "")
                if not wanted or (path in artifacts and artifacts[path] != wanted):
                    raise ValueError("DATA_INTEGRITY_ERROR：輸出資產 hash 缺失或不一致")
                artifacts[path] = wanted
        self._source_specs, self._artifact_specs = sources, artifacts

    def _capture(self, timings, *, initial_documents=None):
        """Read every dependency by bytes, with no size/mtime trust shortcuts."""
        started = time.perf_counter()
        root = sp.project_actual_evidence_root(self.output_dir)
        paths = {path for path, _ in self._runtime(timings)}
        paths.update(self._source_specs)
        paths.update(self._artifact_specs)
        paths.update({self.output_dir / "校對工作階段.json", self.output_dir / "人工判定資料庫.json",
                      sp.REUSABLE_EXPECTED_RULES,
                      root / "manual_actual_staging.json", root / "global_exact_glyph_project_transaction.json",
                      root / "global_exact_glyph_promotion_outbox.json"})
        paths.update(root / name for name in (sp.OCCURRENCE_OVERRIDE_FILE, sp.USER_GLYF_FILE,
                                              sp.USER_CFF_FILE, sp.GLYPH_CONFLICT_FILE, sp.GLYPH_PROVENANCE_FILE))
        documents = {self.output_dir / "校對工作階段.json", self.output_dir / "人工判定資料庫.json",
                     root / "manual_actual_staging.json"}
        initial_documents = initial_documents or {}
        content = {path: (initial_documents[path] if path in initial_documents else _read(path))
                   for path in documents}
        hashes = {str(path): (_digest(content[path]) if path in documents else
                             sp.sha256_file(path) if path.exists() else "") for path in paths}
        for path, wanted in [*self._runtime_paths, *self._source_specs.items(), *self._artifact_specs.items()]:
            if hashes[str(path)] != wanted:
                raise ValueError(f"SOURCE_INVALID／DATA_INTEGRITY_ERROR：檔案內容已變更：{path}")
        timings["dependency_hashing"] = timings.get("dependency_hashing", 0.0) + time.perf_counter() - started
        return content, hashes

    def save_event(self, review_id, event, *, expected_manifest=None, expected_db=None,
                   defer_staging_validation=False, progress=None):
        def notify(phase):
            # Advisory callbacks never turn a durable save into a failure.
            if progress is not None:
                try:
                    progress(phase)
                except Exception:
                    pass
        # Admission is separate from waiting on a read-only staging capture.
        if not self._save_lock.acquire(blocking=False):
            raise RuntimeError("人工判定正在保存中，請勿重複操作")
        try:
            # Use the same project lock as actual apply/recovery. It prevents
            # those transactions racing a save of a now-obsolete expected row.
            notify("等待專案儲存鎖（尚未寫入）")
            with self._lock, sp.project_delivery_lock(sp.project_actual_evidence_root(self.output_dir)):
                return self._save(review_id, event, expected_manifest, expected_db,
                                  defer_staging_validation=defer_staging_validation, notify=notify)
        finally:
            self._save_lock.release()

    def _prepare_snapshot(self, timings, expected_manifest, expected_db, notify=lambda phase: None):
        if (self.output_dir / "跨電腦接續未完成.json").exists():
            raise StaleReviewProjectError("跨電腦接續未完成；未保存人工判定")
        manifest_path = self.output_dir / "校對工作階段.json"
        db_path = self.output_dir / "人工判定資料庫.json"
        notify("讀取並驗證工作階段（尚未寫入）")
        manifest_raw = _read(manifest_path)
        manifest_sha = _digest(manifest_raw)
        if self._manifest is None or manifest_sha != self._manifest_sha:
            manifest = _json(manifest_raw, manifest_path)
            sp.validate_manifest_integrity(manifest)
            if (not sp.schema_compatible(manifest.get("session_schema_version"), sp.SESSION_SCHEMA_VERSION)
                    or not sp.schema_compatible(manifest.get("ledger_schema_version"), sp.LEDGER_SCHEMA_VERSION)
                    or not sp.schema_compatible(manifest.get("workbook_schema_version"), sp.WORKBOOK_SCHEMA_VERSION)
                    or not sp.review_id_schema_compatible(manifest.get("review_id_schema_version"), sp.REVIEW_ID_SCHEMA_VERSION)):
                raise ValueError("SESSION_SCHEMA_INCOMPATIBLE：需明確轉接器才能保存")
            if expected_manifest is not None and manifest != expected_manifest:
                raise StaleReviewProjectError("工作階段已變更；未保存，請重新開啟人工校對畫面")
            self._specs(manifest)
        else:
            manifest = self._manifest
        if expected_manifest is not None and manifest != expected_manifest:
            raise StaleReviewProjectError("畫面工作階段與目前專案不同；未保存")
        notify("完整核對來源與依賴內容（尚未寫入）")
        content, hashes = self._capture(timings, initial_documents={manifest_path: manifest_raw})
        if hashes[str(manifest_path)] != manifest_sha:
            raise StaleReviewProjectError("驗證期間工作階段已變更；未保存")
        db_sha = hashes[str(db_path)]
        if self._db is not None and db_sha == self._db_sha:
            db = self._db
        else:
            raw_db = _json(content[db_path], db_path) if content[db_path] is not None else {}
            # Match the durable loader's root contract before normalization:
            # falsy JSON scalars/lists are corruption, not an absent database.
            if not isinstance(raw_db, dict):
                raise ValueError("DATA_INTEGRITY_ERROR：現版人工判定資料庫根節點必須是物件")
            db = sp.normalize_db(raw_db)
        if expected_db is not None and db != expected_db:
            raise StaleReviewProjectError("人工判定資料庫已由其他操作變更；未覆寫，請重新載入")
        evidence_root = sp.project_actual_evidence_root(self.output_dir)
        frozen_paths = [sp.REUSABLE_EXPECTED_RULES] + [evidence_root / name for name in (
            sp.OCCURRENCE_OVERRIDE_FILE, sp.USER_GLYF_FILE, sp.USER_CFF_FILE, sp.GLYPH_CONFLICT_FILE,
            sp.GLYPH_PROVENANCE_FILE)]
        candidate_anchor = ReviewEvidenceAnchor(
            manifest_seal=str(manifest["manifest_integrity_sha256"]),
            frozen_inputs=tuple(sorted((str(path), hashes[str(path)]) for path in frozen_paths)))
        if (self._evidence_anchor is not None
                and self._evidence_anchor.manifest_seal == candidate_anchor.manifest_seal
                and self._evidence_anchor != candidate_anchor):
            raise StaleReviewProjectError("actual 證據或應標規則已變更；請先重新產生並載入工作階段")
        # A changed dependency invalidates the baseline and indexes together.
        # Session/DB changes additionally have to match the displayed snapshot.
        full = (self._dependencies != hashes or self._manifest is None or self._db is None)
        phase = time.perf_counter()
        notify("驗證人工判定與校對項目（尚未寫入）")
        if full:
            baseline, ledger = sp.prepare_review_ledger(manifest, db)
            index = {row["review_id"]: n for n, row in enumerate(baseline)}
        else:
            baseline, ledger, index = self._baseline, self._ledger, self._index
        timings["ledger_full_rebuild"] = time.perf_counter() - phase if full else 0.0
        return manifest, db, baseline, ledger, index, content, hashes, candidate_anchor

    def load_resume_snapshot(self, *, expected_manifest=None, expected_db=None):
        """Verify once, retaining content for queue and staging in this operation.

        The second capture proves input stability after ledger preparation. It
        is a boundary check, not another parse/materialization. No staging
        checked IDs are trusted until validate_resume_staging succeeds.
        """
        timings = {}
        with self._lock, sp.project_delivery_lock(sp.project_actual_evidence_root(self.output_dir)):
            values = self._prepare_snapshot(timings, expected_manifest, expected_db)
            manifest, db, baseline, ledger, index, content, hashes, anchor = values
            _, final_hashes = self._capture(timings)
            if final_hashes != hashes:
                raise StaleReviewProjectError("載入期間專案來源或暫存已變更；請重新載入")
            # Reload verifies the candidate without replacing an established
            # anchor. A subsequent verified save retains the existing reanchor
            # boundary for a newly sealed actual/rule refresh.
            if self._evidence_anchor is None:
                self._evidence_anchor = anchor
            self._manifest, self._db = manifest, db
            self._baseline, self._ledger, self._index = baseline, ledger, index
            self._manifest_sha = hashes[str(self.output_dir / "校對工作階段.json")]
            self._db_sha = hashes[str(self.output_dir / "人工判定資料庫.json")]
            self._dependencies = hashes
        # The worker owns these objects; the GUI receives separate plain data.
        return ReviewResumeSnapshot(manifest, db, baseline, ledger, content, hashes, timings)

    def validate_resume_staging(self, snapshot, *, cancelled=None):
        """Reuse verified bytes/ledger, then reject drift before publication."""
        phase = time.perf_counter()
        staging_path = sp.project_actual_evidence_root(self.output_dir) / "manual_actual_staging.json"
        try:
            if cancelled is not None and cancelled():
                raise InterruptedError("actual 暫存核對已由新操作取代")
            raw = snapshot.content[staging_path]
            staging = (ar._validate_manual_actual_staging_document(_json(raw, staging_path))
                       if raw is not None else ar._empty_manual_actual_staging())
            summary = sp._manual_actual_summary_from_verified_snapshot(
                self.output_dir, staging, snapshot.manifest, snapshot.db, snapshot.ledger,
                base_ledger=snapshot.baseline, source_hashes=snapshot.hashes, cancelled=cancelled)
            if cancelled is not None and cancelled():
                raise InterruptedError("actual 暫存核對已由新操作取代")
            with self._lock, sp.project_delivery_lock(sp.project_actual_evidence_root(self.output_dir)):
                _, final_hashes = self._capture(snapshot.timings)
            if final_hashes != snapshot.hashes:
                raise StaleReviewProjectError("actual 暫存核對期間專案內容已變更；請重新載入")
        except Exception as exc:
            summary = {"staging_error": str(exc), "staged_checked_occurrence_ids": []}
        snapshot.timings["actual_staging_validation"] = time.perf_counter() - phase
        return summary

    def _save(self, review_id, event, expected_manifest, expected_db, *, defer_staging_validation=False,
              notify=lambda phase: None):
        started = time.perf_counter()
        timings = {}
        (manifest, db, baseline, ledger, index, content, hashes,
         candidate_anchor) = self._prepare_snapshot(timings, expected_manifest, expected_db, notify)
        manifest_sha = hashes[str(self.output_dir / "校對工作階段.json")]
        db_path = self.output_dir / "人工判定資料庫.json"
        db_sha = hashes[str(db_path)]
        if review_id not in index:
            raise ValueError("找不到目前 review_id；已取消寫入")
        phase = time.perf_counter()
        staged_db = {**db, "events": dict(db.get("events", {}))}
        if event is None:
            staged_db["events"].pop(review_id, None)
            resolved = dict(baseline[index[review_id]])
        else:
            if not isinstance(event, dict):
                raise ValueError("review event 格式錯誤")
            import pdf_portability
            unresolved_conflicts = pdf_portability.validate_conflict_state(
                self.output_dir, manifest, db)
            if review_id in unresolved_conflicts and event.get("portability_source") is not None:
                raise ValueError("衝突位置只接受本地重新核對的人工裁決；未保存匯入事件")
            staged_db["events"][review_id] = copy.deepcopy(event)
            staged_db["events"][review_id].pop("portability_conflict_resolution", None)
            resolution = None
            if event.get("action") != "保留待人工" and event.get("portability_source") is None:
                resolution = pdf_portability.conflict_resolution_evidence(
                    self.output_dir, manifest, db, review_id)
                if resolution is None:
                    resolution = (db.get("events", {}).get(review_id) or {}).get(
                        "portability_conflict_resolution")
            if resolution is not None:
                staged_db["events"][review_id]["portability_conflict_resolution"] = copy.deepcopy(resolution)
            staged_event = staged_db["events"][review_id]
            if (staged_event.get("action") in sp.EXPECTED_RESOLUTION_ACTIONS
                    and "manual_expected_decision" not in staged_event
                    and "expected_resolution_binding" not in staged_event
                    and manifest.get("expected_asset_fingerprint")):
                staged_event = sp.bind_expected_resolution(baseline[index[review_id]], staged_event, manifest)
                staged_db["events"][review_id] = staged_event
            if staged_event.get("action") == "確認現版差異":
                prior_binding = (db["events"].get(review_id) or {}).get("expected_resolution_binding")
                if staged_event.get("expected_resolution_binding") != prior_binding:
                    raise ValueError("GUI 確認必須保留既有已驗證 expected binding；不得新增／丟棄來源")
            resolved = sp._apply_review_event(baseline[index[review_id]], staged_event, expected_manifest=manifest)
            if (staged_event.get("action") == "確認現版差異"
                    and (resolved.get("state") != "TEXTBOOK_ERROR_CONFIRMED"
                         or resolved.get("review_event_replay_status"))):
                # Replay may legitimately retain a pending row for an invalid
                # confirmation. It is not a successful new decision: replacing
                # the prior event would discard its still-valid expected source.
                raise ValueError("差異確認已失效；未保存，原判定及 expected 證據仍保留。請重新核對目前證據。")
        original = baseline[index[review_id]]
        if (resolved.get("review_id"), resolved.get("occurrence_id")) != (
                original.get("review_id"), original.get("occurrence_id")):
            raise ValueError("人工事件不得改變 occurrence／review identity")
        # Unique IDs were proven over the full roster. Only one row changed,
        # IDs are unchanged, and this is the same per-row validator as full replay.
        sp.validate_occurrence_ledger([resolved])
        staged_ledger = list(ledger)
        staged_ledger[index[review_id]] = resolved
        timings["ledger_incremental"] = time.perf_counter() - phase
        phase = time.perf_counter()
        if defer_staging_validation:
            # Only an independent explicit expected decision may commit while
            # actual staging is pending. All write-boundary checks still run.
            decision = event.get("manual_expected_decision", {}) if isinstance(event, dict) else {}
            if (not isinstance(decision, dict) or decision.get("operation") != "ENTER_EXPECTED"
                    or event.get("action") not in sp.EXPECTED_RESOLUTION_ACTIONS
                    or not sp.valid_manual_expected_decision(resolved)
                    or resolved.get("review_event_replay_status")):
                raise ValueError("actual 暫存核對中只可保存獨立輸入的應標判定")
            summary = {"staging_validation_pending": True, "staged_checked_occurrence_ids": []}
        else:
            try:
                notify("核對 actual 暫存（尚未寫入）")
                staging_path = sp.project_actual_evidence_root(self.output_dir) / "manual_actual_staging.json"
                staging = (ar._validate_manual_actual_staging_document(_json(content[staging_path], staging_path))
                           if content[staging_path] is not None else ar._empty_manual_actual_staging())
                summary = sp._manual_actual_summary_from_verified_snapshot(
                    self.output_dir, staging, manifest, staged_db, staged_ledger, base_ledger=baseline,
                    source_hashes=hashes)
            except Exception as exc:
                summary = {"staging_error": str(exc), "staged_checked_occurrence_ids": []}
        timings["actual_staging_validation"] = time.perf_counter() - phase
        # Recheck every dependency immediately before the atomic replacement;
        # no side effect has happened yet if any input changed during validation.
        notify("寫入前再次完整核對依賴（尚未寫入）")
        _, final_hashes = self._capture(timings)
        if final_hashes != hashes:
            raise StaleReviewProjectError("驗證期間專案來源或暫存已變更；未保存，請重新載入")
        # Accept a new seal only after its schema, source/artifact bytes, full
        # ledger and stable inputs have passed. Existing actual/rule refresh
        # paths produce that new sealed snapshot. I/O failure after this point
        # may evict the calculation cache, but cannot erase the verified anchor.
        self._evidence_anchor = candidate_anchor
        phase = time.perf_counter()
        notify("正在原子寫入人工判定")
        saved_sha = sp.json_save(db_path, staged_db, expected_sha256=db_sha)
        timings["json_save"] = time.perf_counter() - phase
        notify("人工判定已寫入")
        # No further fallible UI/staging work may turn this into a failed save.
        # The next operation verifies durable bytes again before reusing cache.
        hashes[str(db_path)] = saved_sha
        self._manifest, self._db = manifest, staged_db
        self._baseline, self._ledger, self._index = baseline, staged_ledger, index
        self._manifest_sha, self._db_sha, self._dependencies = manifest_sha, saved_sha, hashes
        token = hashlib.sha256(json.dumps(sorted(hashes.items())).encode("utf-8")).hexdigest()
        timings["backend_total"] = time.perf_counter() - started
        resume = (ReviewResumeSnapshot(manifest, staged_db, baseline, staged_ledger, content, hashes, {})
                  if defer_staging_validation else None)
        return SaveResult(staged_db, staged_ledger, resolved, summary, timings, token, resume)
