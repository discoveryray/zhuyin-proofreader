"""Explicit, fail-closed transfer of a sealed review project to another PDF.

This module never changes a source session, its workbooks, or its identities.
The transfer receipt is local project evidence, not Global glyph evidence.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import fitz


PROOF_FILE = "PDF內容對應證據.json"
PROOF_VERSION = 2
PENDING_ACTUAL_FILE = "來源actual待重新核對.json"
INCOMPLETE_FILE = "跨電腦接續未完成.json"
CONFLICT_FILE = "跨專案判定衝突.json"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _page_signatures(path: Path) -> list[dict[str, Any]]:
    """Bind all visible page content and page geometry, including images and glyphs.

    PyMuPDF renders embedded fonts and images, rather than relying on a PDF's
    text layer. Exact raster equality intentionally refuses uncertain transfers.
    """
    signatures = []
    with fitz.open(path) as document:
        if not document.page_count:
            raise ValueError("PDF 沒有頁面")
        for page in document:
            if page.rect.is_empty:
                raise ValueError("PDF 頁面尺寸無效")
            pix = page.get_pixmap(matrix=fitz.Matrix(3, 3), colorspace=fitz.csRGB, alpha=False, annots=True)
            signatures.append({
                "width": round(page.rect.width, 4),
                "height": round(page.rect.height, 4),
                "rotation": page.rotation,
                "display_matrix": [round(value, 6) for value in page.rotation_matrix],
                "pixels_width": pix.width,
                "pixels_height": pix.height,
                "rgb_sha256": hashlib.sha256(pix.samples).hexdigest(),
            })
    return signatures


def _source_pdf(manifest: Mapping[str, Any], info: Mapping[str, Any], output_dir: Path) -> Path:
    """Only exact source bytes may create a portable proof."""
    wanted = str(info.get("pdf_sha256") or "")
    name = str(info.get("pdf_name") or "")
    candidates = [Path(str(info.get("pdf") or "")), output_dir.parent / name]
    matching = [path.resolve() for path in candidates if path.is_file() and _sha(path) == wanted]
    if not matching:
        raise FileNotFoundError(f"無法建立跨電腦證據：原 PDF 不存在或 SHA 不符：{name}")
    return matching[0]


def _artifact(output_dir: Path, info: Mapping[str, Any], kind: str) -> Path:
    stored = Path(str(info.get(f"{kind}_workbook") or ""))
    subdir = "01_實際注音" if kind == "actual" else "02_候選報告"
    local = output_dir / subdir / stored.name
    wanted = str(info.get(f"{kind}_workbook_sha256") or "")
    candidates = {str(path.resolve()): path for path in (stored, local) if path.is_file()}
    matches = [path for path in candidates.values() if wanted and _sha(path) == wanted]
    if not matches:
        raise ValueError(f"DATA_INTEGRITY_ERROR：來源工作簿遺失或 SHA 不符：{stored.name}")
    return matches[0]


def _load_project(output_dir: Path, *, allow_incomplete: bool = False,
                  allow_unresolved_conflict: bool = False):
    import standalone_proofread as sp

    output_dir = Path(output_dir).resolve()
    if not allow_incomplete and (output_dir / INCOMPLETE_FILE).exists():
        raise ValueError(f"跨電腦接續未完成，不能當作完整來源或匯入目標：{output_dir}")
    manifest = sp.json_load_strict(output_dir / "校對工作階段.json")
    sp.validate_manifest_integrity(manifest)
    for info in manifest.get("pdfs", []):
        _artifact(output_dir, info, "actual")
        _artifact(output_dir, info, "candidate")
    db = sp.normalize_db(sp.json_load_strict(output_dir / "人工判定資料庫.json"))
    unresolved = validate_conflict_state(output_dir, manifest, db)
    if unresolved and not allow_unresolved_conflict:
        raise ValueError(f"來源專案有未裁決的同位置判定衝突，不能再轉送：{unresolved}")
    ledger = sp.materialize_ledger(manifest, db)
    stale = [row["review_id"] for row in ledger if row.get("review_event_replay_status")]
    if stale:
        raise ValueError(f"來源/目標工作階段已有失效判定，不能當有效成果轉用：{stale[:10]}")
    return manifest, db


def validate_conflict_state(output_dir: Path, manifest: Mapping[str, Any], db: Mapping[str, Any]):
    """A conflicted row is inactive until a fresh local event cites its receipt."""
    import standalone_proofread as sp

    path = Path(output_dir) / CONFLICT_FILE
    if not path.exists():
        return []
    receipt = sp.json_load_strict(path)
    if (not isinstance(receipt, dict) or receipt.get("version") != 1
            or not isinstance(receipt.get("conflicts"), list) or not receipt["conflicts"]):
        raise ValueError("跨專案判定衝突紀錄格式無法驗證")
    valid_ids = {entry["review_id"] for entry in manifest.get("records", [])}
    unresolved = []
    for conflict in receipt["conflicts"]:
        if (not isinstance(conflict, dict)
                or conflict.get("target_review_id") not in valid_ids
                or not isinstance(conflict.get("source_event"), dict)
                or not isinstance(conflict.get("target_event"), dict)
                or not conflict.get("source_pdf_sha256")
                or not conflict.get("target_pdf_sha256")):
            raise ValueError("跨專案判定衝突來源或位置識別不完整")
        review_id = conflict["target_review_id"]
        event = db["events"].get(review_id)
        if event is None:
            unresolved.append(review_id)
        else:
            resolution = event.get("portability_conflict_resolution")
            if (not isinstance(resolution, dict)
                    or hashlib.sha256(_canonical(conflict)).hexdigest()
                    not in resolution.get("conflict_sha256", [])
                    or conflict not in resolution.get("original_conflicts", [])):
                raise ValueError(f"衝突位置有未經本地裁決的有效事件：{review_id}")
    return unresolved


def conflict_resolution_evidence(output_dir: Path, manifest: Mapping[str, Any],
                                 db: Mapping[str, Any], review_id: str):
    unresolved = validate_conflict_state(output_dir, manifest, db)
    if review_id not in unresolved:
        return None
    import standalone_proofread as sp

    path = Path(output_dir) / CONFLICT_FILE
    receipt = sp.json_load_strict(path)
    selected = [copy.deepcopy(item) for item in receipt["conflicts"]
                if item["target_review_id"] == review_id]
    return {
        "conflict_sha256": [hashlib.sha256(_canonical(item)).hexdigest() for item in selected],
        "original_conflicts": selected,
    }


def _actual_transfer_state(output_dir: Path, manifest: Mapping[str, Any]):
    """Preserve unresolved actual intent without creating new human evidence."""
    import actual_review as ar
    import standalone_proofread as sp
    from global_glyph_promotion import PROJECT_TRANSACTION_FILE, load_promotion_outbox

    root = sp.project_actual_evidence_root(output_dir)
    transaction = root / PROJECT_TRANSACTION_FILE
    if transaction.exists():
        # PREPARED must roll back; COMMITTED must complete its source-side
        # postconditions. Neither journal can be replayed under a new session.
        raise ValueError(
            f"來源 actual 交易尚待原專案恢復：{transaction}；先在來源完成 recovery，"
            "再準備/匯入跨電腦成果。未搬移交易或增加 Global quorum"
        )
    # An outbox can be pending even after project evidence committed.
    # Do not transplant it into another project or deliver it as fresh proof.
    if any(item["status"] == "PENDING" for item in load_promotion_outbox(root)["items"]):
        raise ValueError("來源 Global promotion outbox 尚有未交付意圖；先在來源完成恢復")
    staging = ar.load_manual_actual_staging(root)
    source_ids = {str(entry.get("occurrence_id") or "") for entry in manifest.get("records", [])}
    for group in staging["staged_groups"]:
        if not set(group["member_occurrence_ids"]) <= source_ids:
            raise ValueError("來源 actual 暫存含工作階段不存在的 occurrence；未轉接")
    prior = []
    receipt_path = output_dir / PENDING_ACTUAL_FILE
    if receipt_path.exists():
        receipt = sp.json_load_strict(receipt_path)
        if (not isinstance(receipt, dict)
                or receipt.get("status") != "RECHECK_LOCAL_PDF_REQUIRED"
                or not isinstance(receipt.get("sources"), list)):
            raise ValueError("前次來源 actual 暫存紀錄格式無法驗證；未轉接")
        for item in receipt["sources"]:
            if not isinstance(item, dict) or not isinstance(item.get("mapped_occurrences"), dict):
                raise ValueError("前次來源 actual 暫存位置映射缺失；未轉接")
            original_staging = ar._validate_manual_actual_staging_document(item.get("staging"))
            if (not item.get("source_session_id") or not item.get("source_manifest_integrity_sha256")
                    or not item.get("source_pdf_sha256")
                    or not original_staging["staged_groups"]):
                raise ValueError("前次來源 actual 暫存原始身分不完整；未轉接")
            original_mapping = item["mapped_occurrences"]
            staged_ids = {occurrence_id for group in original_staging["staged_groups"]
                          for occurrence_id in group["member_occurrence_ids"]}
            if not staged_ids <= set(original_mapping):
                raise ValueError("前次來源 actual 暫存 occurrence 原位置未對應；未轉接")
            hops = item.get("forwarded_via", [])
            if not isinstance(hops, list) or any(not isinstance(hop, dict) for hop in hops):
                raise ValueError("前次來源 actual 暫存轉送鏈無效；未轉接")
            previous_session = item.get("target_session_id")
            previous_ids = set(original_mapping.values())
            if not previous_session or len(previous_ids) != len(original_mapping):
                raise ValueError("前次來源 actual 暫存首跳位置不唯一；未轉接")
            for hop in hops:
                hop_mapping = hop.get("mapped_occurrences")
                if (hop.get("source_session_id") != previous_session
                        or not hop.get("target_session_id")
                        or not isinstance(hop_mapping, dict)
                        or set(hop_mapping) != previous_ids
                        or len(set(hop_mapping.values())) != len(hop_mapping)):
                    raise ValueError("前次來源 actual 暫存轉送鏈位置無法逐跳核對；未轉接")
                previous_session = hop["target_session_id"]
                previous_ids = set(hop_mapping.values())
            if previous_session != manifest["session_id"] or previous_ids != source_ids:
                raise ValueError("前次來源 actual 暫存與目前工作階段不符；未轉接")
            prior.append(item)
    return {
        "source_session_id": manifest["session_id"],
        "source_manifest_integrity_sha256": manifest["manifest_integrity_sha256"],
        "staging": staging,
        "prior_receipts": prior,
        "status": "RECHECK_LOCAL_PDF_REQUIRED" if staging["staged_groups"] or prior else "NONE",
    }


def _proof_payload(output_dir: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    pdfs = []
    for info in manifest.get("pdfs", []):
        path = _source_pdf(manifest, info, output_dir)
        pdfs.append({
            "pdf_sha256": str(info["pdf_sha256"]),
            "pdf_name": str(info["pdf_name"]),
            "pages": _page_signatures(path),
        })
    if not pdfs or len({item["pdf_sha256"] for item in pdfs}) != len(pdfs):
        raise ValueError("來源工作階段 PDF 清單空白或 SHA 不唯一")
    return {
        "version": PROOF_VERSION,
        "session_id": str(manifest.get("session_id") or ""),
        "manifest_integrity_sha256": str(manifest["manifest_integrity_sha256"]),
        "pdfs": pdfs,
    }


def prepare_portable_project(output_dir: Path) -> Path:
    """Capture source-page proof while original bytes are still available."""
    import standalone_proofread as sp

    output_dir = Path(output_dir).resolve()
    manifest, _ = _load_project(output_dir)
    _actual_transfer_state(output_dir, manifest)
    payload = _proof_payload(output_dir, manifest)
    proof = {**payload, "integrity_sha256": hashlib.sha256(_canonical(payload)).hexdigest()}
    path = output_dir / PROOF_FILE
    sp.json_save(path, proof)
    return path


def _load_proof(output_dir: Path, manifest: Mapping[str, Any]):
    import standalone_proofread as sp

    path = output_dir / PROOF_FILE
    if path.exists():
        proof = sp.json_load_strict(path)
        payload = {key: value for key, value in proof.items() if key != "integrity_sha256"}
        if (proof.get("version") != PROOF_VERSION
                or proof.get("integrity_sha256") != hashlib.sha256(_canonical(payload)).hexdigest()
                or proof.get("manifest_integrity_sha256") != manifest.get("manifest_integrity_sha256")
                or proof.get("session_id") != manifest.get("session_id")):
            raise ValueError("跨電腦 PDF 內容證據損壞或不屬於來源工作階段")
        expected = [(info.get("pdf_sha256"), info.get("pdf_name")) for info in manifest.get("pdfs", [])]
        observed = [(info.get("pdf_sha256"), info.get("pdf_name")) for info in proof.get("pdfs", [])]
        if observed != expected:
            raise ValueError("跨電腦 PDF 內容證據與來源 PDF 清單不符")
        return proof
    # Source bytes may still be present without a previously exported proof.
    return _proof_payload(output_dir, manifest)


def _pages_equivalent(source_pages, target_pages):
    if len(source_pages) != len(target_pages):
        return False
    for source, target in zip(source_pages, target_pages):
        # Rotation and its matrix describe PDF storage. Reviewed positions are
        # separately transformed into the displayed page coordinates.
        fields = ("width", "height", "pixels_width", "pixels_height", "rgb_sha256")
        if any(source.get(field) != target.get(field) for field in fields):
            return False
    return True


def _match_pdfs(proof: Mapping[str, Any], source_manifest: Mapping[str, Any],
                target_manifest: Mapping[str, Any], target_dir: Path):
    source_pdfs = list(proof["pdfs"])
    target_pdfs = list(target_manifest.get("pdfs", []))
    if len(source_pdfs) != len(target_pdfs):
        raise ValueError("PDF 數量不同；不支援部分教材成果移轉")
    rendered = []
    for target in target_pdfs:
        path = _source_pdf(target_manifest, target, target_dir)
        rendered.append((target, _page_signatures(path)))
    mapping = {}
    geometry = {}
    for source in source_pdfs:
        candidates = [(target, pages) for target, pages in rendered
                      if _pages_equivalent(source["pages"], pages)]
        if len(candidates) != 1:
            raise ValueError(
                f"PDF 頁面內容/圖片/文字/注音字形或頁面位置無法唯一對應：{source['pdf_name']}；"
                "請確認是同版完整教材"
            )
        target, target_pages = candidates[0]
        rendered.remove(candidates[0])
        mapping[source["pdf_sha256"]] = target["pdf_sha256"]
        geometry[source["pdf_sha256"]] = (source["pages"], target_pages)
    return mapping, geometry


def _anchor(entry: Mapping[str, Any], pages):
    """Position is mandatory; text alone cannot select an occurrence."""
    coordinates = []
    for field in ("x0", "y0", "x1", "y1"):
        value = entry.get(field)
        if value is None or isinstance(value, bool):
            raise ValueError(f"occurrence 位置缺少 {field}")
        coordinates.append(round(float(value), 2))
    page = int(entry.get("physical_page") or 0)
    if page < 1 or coordinates[2] <= coordinates[0] or coordinates[3] <= coordinates[1]:
        raise ValueError("occurrence 頁碼或 bbox 無效")
    if page > len(pages) or len(pages[page - 1].get("display_matrix", [])) != 6:
        raise ValueError("occurrence 顯示頁面座標轉換資料缺失")
    displayed = fitz.Rect(coordinates) * fitz.Matrix(*pages[page - 1]["display_matrix"])
    if displayed.is_empty:
        raise ValueError("occurrence 顯示位置無效")
    return (page, *(round(value, 2) for value in displayed), str(entry.get("char") or ""))


def _map_reviews(source_manifest: Mapping[str, Any], target_manifest: Mapping[str, Any], pdf_map, geometry):
    source_by_target = {target_sha: source_sha for source_sha, target_sha in pdf_map.items()}
    target_by_anchor = {}
    for entry in target_manifest.get("records", []):
        target_sha = str(entry.get("pdf_sha256") or "")
        source_sha = source_by_target.get(target_sha)
        if source_sha is None:
            raise ValueError("目標 occurrence PDF 無來源內容對應")
        key = (target_sha, _anchor(entry, geometry[source_sha][1]))
        if key in target_by_anchor:
            raise ValueError(f"目標位置不唯一：{key}")
        target_by_anchor[key] = entry
    mapped = {}
    used = set()
    for entry in source_manifest.get("records", []):
        source_sha = str(entry.get("pdf_sha256") or "")
        if source_sha not in pdf_map:
            raise ValueError("來源 occurrence PDF 無目標內容對應")
        key = (pdf_map[source_sha], _anchor(entry, geometry[source_sha][0]))
        target = target_by_anchor.get(key)
        if target is None:
            raise ValueError(f"來源 occurrence 無法對應目標位置：{entry.get('review_id')}")
        if target["review_id"] in used:
            raise ValueError("多個來源 occurrence 對應同一目標位置")
        used.add(target["review_id"])
        mapped[entry["review_id"]] = (entry, target)
    if len(mapped) != len(target_by_anchor):
        raise ValueError("來源與目標 occurrence 數量/位置不同；不支援部分移轉")
    return mapped


def _event_payload(event: Mapping[str, Any]):
    return {key: value for key, value in event.items()
            if key not in {"portability_source", "portability_duplicate_sources",
                           "portability_conflict_resolution"}}


def _source_identity(source_manifest, source, source_id, target_manifest, target, event):
    return {
        "session_id": source_manifest["session_id"],
        "pdf_sha256": source["pdf_sha256"],
        "occurrence_id": source["occurrence_id"],
        "review_id": source_id,
        "target_session_id": target_manifest["session_id"],
        "target_pdf_sha256": target["pdf_sha256"],
        "target_occurrence_id": target["occurrence_id"],
        "target_review_id": target["review_id"],
        "prior": copy.deepcopy(event.get("portability_source")),
        "prior_duplicate_sources": copy.deepcopy(event.get("portability_duplicate_sources", [])),
    }


def _mapped_occurrence_overrides(source_dir: Path, source_manifest, target_manifest, reviews):
    """Carry occurrence-local actual only; never mint Global glyph evidence."""
    import actual_review as ar
    import standalone_proofread as sp

    source_root = sp.project_actual_evidence_root(source_dir)
    # Verify the exact per-PDF dynamic evidence dependency captured in the
    # sealed actual workbook. This read-only mode never initializes/migrates
    # source evidence, even when the original PDF bytes are no longer present.
    for info in source_manifest.get("pdfs", []):
        workbook = _artifact(source_dir, info, "actual")
        metadata = sp.workbook_metadata(workbook)
        if metadata.get("pdf_sha256") != info.get("pdf_sha256"):
            raise ValueError("來源 actual workbook PDF SHA 不符")
        try:
            components = json.loads(str(metadata["actual_asset_fingerprint_components"]))
            sealed_dynamic = components["dynamic_actual_evidence_hashes"]
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("來源 actual workbook 缺少可核對的動態 evidence fingerprint") from exc
        dependency_roster = ar.actual_workbook_dynamic_dependencies(workbook)
        current_dynamic = ar.dynamic_actual_hashes(
            source_root, pdf_path=Path(str(info["pdf_name"])),
            dependencies=dependency_roster, read_only=True,
        )
        if sealed_dynamic != current_dynamic:
            raise ValueError(f"來源動態 actual 證據與封印 workbook fingerprint 不符：{info['pdf_name']}")
    rows = ar._read_csv(source_root / ar.OCCURRENCE_OVERRIDE_FILE, ar.OVERRIDE_HEADERS)
    source_names = [Path(str(info["pdf_name"])) for info in source_manifest.get("pdfs", [])]
    rows = [row for row in rows if any(ar._override_applies_to_pdf(row, name) for name in source_names)]
    if not rows:
        return []
    by_source_key = {}
    for source, target in reviews.values():
        key = ar._override_key_from_entry(source)[2:]
        if key in by_source_key:
            raise ValueError("來源 actual occurrence override 位置不唯一")
        by_source_key[key] = (source, target)
    mapped = []
    seen_target = set()
    for row in rows:
        key = tuple(row.get(field, "") for field in ("page", "target_char", "stable_key", "x0", "y0"))
        pair = by_source_key.get(key)
        if pair is None:
            raise ValueError(f"來源 actual occurrence override 無法唯一對應：{key}")
        source, target = pair
        reading = ar._canon(row.get("actual_reading"))
        if not reading or reading != row["actual_reading"]:
            raise ValueError("來源 actual occurrence override 讀音不是 canonical")
        target_key = ar._override_key_from_entry(target)
        if target_key in seen_target:
            raise ValueError("多個來源 actual override 對應同一目標位置")
        seen_target.add(target_key)
        mapped.append(dict(zip(
            ("pdf_contains", "pdf_excludes", "page", "target_char", "stable_key", "x0", "y0"),
            target_key,
        )) | {
            "actual_reading": reading,
            "source": "portable occurrence-local actual (no Global quorum)",
            "note": (
                f"source_session={source_manifest['session_id']};source_pdf_sha256={source['pdf_sha256']};"
                f"source_occurrence_id={source['occurrence_id']};source={row['source']};note={row['note']}"
            ),
        })
    return mapped


def _publish_portable_outputs(target_dir: Path, manifest, db):
    """Rebuild every derived user view from the committed target DB."""
    import standalone_proofread as sp

    sp.save_pending_json(target_dir, manifest, db)
    sp.generate_report(target_dir, manifest, db)


def import_project_decisions(source_dir: Path, target_dir: Path, *,
                             _allow_incomplete_target: bool = False) -> dict[str, Any]:
    """Import only proven mapped review events; conflicts remain untouched."""
    import standalone_proofread as sp

    source_dir, target_dir = Path(source_dir).resolve(), Path(target_dir).resolve()
    if source_dir == target_dir:
        raise ValueError("來源與目標專案必須不同")
    source_manifest, source_db = _load_project(source_dir)
    target_manifest, _ = _load_project(target_dir, allow_incomplete=_allow_incomplete_target)
    source_actual = _actual_transfer_state(source_dir, source_manifest)
    _actual_transfer_state(target_dir, target_manifest)
    proof = _load_proof(source_dir, source_manifest)
    pdf_map, geometry = _match_pdfs(proof, source_manifest, target_manifest, target_dir)
    reviews = _map_reviews(source_manifest, target_manifest, pdf_map, geometry)
    mapped_overrides = _mapped_occurrence_overrides(source_dir, source_manifest, target_manifest, reviews)
    if mapped_overrides:
        import actual_review as ar
        target_actual_root = sp.project_actual_evidence_root(target_dir)
        target_overrides = ar._read_csv(target_actual_root / ar.OCCURRENCE_OVERRIDE_FILE, ar.OVERRIDE_HEADERS)
        fields = ("pdf_contains", "pdf_excludes", "page", "target_char", "stable_key", "x0", "y0")
        existing = {tuple(row[field] for field in fields): row for row in target_overrides}
        absent = [row for row in mapped_overrides if tuple(row[field] for field in fields) not in existing]
        different = [row for row in mapped_overrides if (
            (old := existing.get(tuple(row[field] for field in fields))) is not None
            and old["actual_reading"] != row["actual_reading"]
        )]
        if absent or different:
            raise ValueError(
                f"來源有已保存 occurrence-local actual 判定，目標缺少 {len(absent)} 筆、"
                f"衝突 {len(different)} 筆；需先在隔離的新接續專案安全重建 actual，未匯入任何判定"
            )
    # The same project lock protects the read, replay check, candidate and
    # compare-and-swap write. A concurrent ReviewSaveService save is therefore
    # included in the candidate rather than lost by a stale earlier read.
    with sp.project_delivery_lock(sp.project_actual_evidence_root(target_dir)):
        live_manifest = sp.json_load_strict(target_dir / "校對工作階段.json")
        sp.validate_manifest_integrity(live_manifest)
        if live_manifest["manifest_integrity_sha256"] != target_manifest["manifest_integrity_sha256"]:
            raise ValueError("目標工作階段於內容對應後已變動；未匯入")
        path = target_dir / "人工判定資料庫.json"
        before = _sha(path)
        target_db = sp.normalize_db(sp.json_load_strict(path))
        candidate = copy.deepcopy(target_db)
        imported, duplicates, provenance_updates, conflicts, conflict_records = 0, 0, 0, [], []
        for source_id, event in source_db["events"].items():
            if source_id not in reviews:
                raise ValueError(f"來源 event 無法對應 occurrence：{source_id}")
            source, target = reviews[source_id]
            target_id = target["review_id"]
            current = candidate["events"].get(target_id)
            identity = _source_identity(source_manifest, source, source_id, target_manifest, target, event)
            if current is not None:
                if _event_payload(current) == _event_payload(event):
                    duplicates += 1
                    known = [current.get("portability_source"),
                             *current.get("portability_duplicate_sources", [])]
                    if identity not in known:
                        current.setdefault("portability_duplicate_sources", []).append(identity)
                        provenance_updates += 1
                else:
                    conflicts.append({"source_review_id": source_id, "target_review_id": target_id})
                    conflict_records.append({
                        "source_review_id": source_id,
                        "target_review_id": target_id,
                        "source_session_id": source_manifest["session_id"],
                        "source_pdf_sha256": source["pdf_sha256"],
                        "source_occurrence_id": source["occurrence_id"],
                        "target_session_id": target_manifest["session_id"],
                        "target_pdf_sha256": target["pdf_sha256"],
                        "target_occurrence_id": target["occurrence_id"],
                        "source_identity": identity,
                        "target_identity": copy.deepcopy(current.get("portability_source")),
                        "source_event": copy.deepcopy(event),
                        "target_event": copy.deepcopy(current),
                    })
                    candidate["events"].pop(target_id)
                continue
            transferred = copy.deepcopy(event)
            transferred["portability_source"] = identity
            candidate["events"][target_id] = transferred
            imported += 1
        # Existing replay validators are the final authority. A changed actual
        # or expected lane can invalidate a conclusion, never guessed valid.
        ledger = sp.materialize_ledger(target_manifest, candidate)
        invalid = [entry["review_id"] for entry in ledger if entry.get("review_event_replay_status")]
        if invalid:
            raise ValueError(f"目標 actual/expected 證據已改變，判定不能直接沿用：{invalid[:10]}")
        mutations = bool(imported or provenance_updates or conflict_records
                         or source_actual["status"] == "RECHECK_LOCAL_PDF_REQUIRED")
        if mutations and not _allow_incomplete_target:
            sp.json_save(target_dir / INCOMPLETE_FILE, {
                "status": "PRESENTATION_PENDING",
                "reason": "匯入後待辦、狀態與報告須由正式資料庫重新產生",
            })
        if conflict_records:
            conflict_path = target_dir / CONFLICT_FILE
            existing_conflicts = (sp.json_load_strict(conflict_path) if conflict_path.exists()
                                  else {"version": 1, "conflicts": []})
            if (not isinstance(existing_conflicts, dict)
                    or existing_conflicts.get("version") != 1
                    or not isinstance(existing_conflicts.get("conflicts"), list)):
                raise ValueError("目標既有衝突紀錄格式無法驗證；未覆寫")
            sp.json_save(conflict_path, {
                "version": 1,
                "conflicts": [*existing_conflicts["conflicts"], *conflict_records],
            }, expected_sha256=_sha(conflict_path) if conflict_path.exists() else None)
        if source_actual["status"] == "RECHECK_LOCAL_PDF_REQUIRED":
            # Every original group remains a read-only receipt across hops.
            # No checked ID becomes a fresh local direct visual or Global vote.
            hop_mapping = {source["occurrence_id"]: target["occurrence_id"]
                           for source, target in reviews.values()}
            incoming = []
            if source_actual["staging"]["staged_groups"]:
                incoming.append({
                    "source_session_id": source_actual["source_session_id"],
                    "source_manifest_integrity_sha256": source_actual["source_manifest_integrity_sha256"],
                    "staging": source_actual["staging"],
                    "status": "RECHECK_LOCAL_PDF_REQUIRED",
                    "source_pdf_sha256": [info["pdf_sha256"] for info in source_manifest["pdfs"]],
                    "target_session_id": target_manifest["session_id"],
                    "mapped_occurrences": hop_mapping,
                })
            for prior in source_actual["prior_receipts"]:
                forwarded = copy.deepcopy(prior)
                forwarded.setdefault("forwarded_via", []).append({
                    "source_session_id": source_manifest["session_id"],
                    "target_session_id": target_manifest["session_id"],
                    "mapped_occurrences": hop_mapping,
                })
                incoming.append(forwarded)
            receipt_path = target_dir / PENDING_ACTUAL_FILE
            existing = (sp.json_load_strict(receipt_path) if receipt_path.exists()
                        else {"status": "RECHECK_LOCAL_PDF_REQUIRED", "sources": []})
            if (existing.get("status") != "RECHECK_LOCAL_PDF_REQUIRED"
                    or not isinstance(existing.get("sources"), list)):
                raise ValueError("目標來源 actual 暫存紀錄格式無法驗證；未覆寫")
            sources = list(existing["sources"])
            for source_receipt in incoming:
                same_origin = [item for item in sources if (
                    item.get("source_session_id"), item.get("source_manifest_integrity_sha256")
                ) == (
                    source_receipt["source_session_id"], source_receipt["source_manifest_integrity_sha256"]
                )]
                if any(item.get("staging") != source_receipt["staging"] for item in same_origin):
                    raise ValueError("同一來源 actual 暫存原文衝突；未覆寫")
                if source_receipt not in sources:
                    sources.append(source_receipt)
            if sources != existing["sources"]:
                sp.json_save(receipt_path, {
                    "status": "RECHECK_LOCAL_PDF_REQUIRED",
                    "sources": sources,
                }, expected_sha256=_sha(receipt_path) if receipt_path.exists() else None)
        if imported or provenance_updates or conflict_records:
            sp.json_save(path, candidate, expected_sha256=before)
    if not _allow_incomplete_target and mutations:
        _publish_portable_outputs(target_dir, target_manifest, candidate)
        (target_dir / INCOMPLETE_FILE).unlink()
    return {"imported": imported, "duplicates": duplicates, "conflicts": conflicts,
            "provenance_updates": provenance_updates,
            "matched_actual_overrides": len(mapped_overrides),
            "actual_staging": source_actual["status"],
            "source_session_id": source_manifest["session_id"],
            "target_session_id": target_manifest["session_id"], "pdf_sha_map": pdf_map}


def continue_project(source_dir: Path, local_pdf: Path, target_dir: Path):
    """Create a new sealed project from local bytes, then import proven events."""
    return merge_projects([source_dir], local_pdf, target_dir)


def merge_projects(source_dirs, local_pdf: Path, target_dir: Path):
    """Build a new project from local bytes and merge one or two source projects.

    The existing projects are read-only. The first source's decision wins only
    when the second source has no decision at that position; differing decisions
    are retained in an explicit conflict receipt for human resolution.
    """
    import standalone_proofread as sp

    source_dirs = [Path(path).resolve() for path in source_dirs]
    local_pdf, target_dir = Path(local_pdf).resolve(), Path(target_dir).resolve()
    if not 1 <= len(source_dirs) <= 2 or len(set(source_dirs)) != len(source_dirs):
        raise ValueError("跨專案合併需要一至兩個不同來源專案")
    if target_dir.exists() and any(target_dir.iterdir()):
        raise ValueError("接續目標資料夾必須為空，避免覆寫既有成果")
    if not local_pdf.is_file() or local_pdf.suffix.lower() != ".pdf":
        raise FileNotFoundError(f"找不到當地 PDF：{local_pdf}")
    local_pages = _page_signatures(local_pdf)
    for source_dir in source_dirs:
        source_manifest, _ = _load_project(source_dir)
        _actual_transfer_state(source_dir, source_manifest)
        proof = _load_proof(source_dir, source_manifest)
        if len(proof["pdfs"]) != 1:
            raise ValueError("此入口目前僅支援單一 PDF 專案；多 PDF 請先建立目標專案再匯入")
        if not _pages_equivalent(proof["pdfs"][0]["pages"], local_pages):
            raise ValueError(f"當地 PDF 頁面內容/位置與來源不同：{source_dir}；未建立目標專案")
    target_dir.mkdir(parents=True, exist_ok=True)
    marker = target_dir / INCOMPLETE_FILE
    sp.json_save(marker, {
        "status": "INCOMPLETE",
        "source_projects": [str(path) for path in source_dirs],
        "local_pdf": str(local_pdf),
        "reason": "只有建立目標封印、匯入映射與核對均成功後才移除此標記",
    })
    try:
        sp.run_pipeline_pdfs([local_pdf], target_dir)
        target_manifest, _ = _load_project(target_dir, allow_incomplete=True)
        mapped_by_key = {}
        for source_dir in source_dirs:
            source_manifest, _ = _load_project(source_dir)
            proof = _load_proof(source_dir, source_manifest)
            pdf_map, geometry = _match_pdfs(proof, source_manifest, target_manifest, target_dir)
            reviews = _map_reviews(source_manifest, target_manifest, pdf_map, geometry)
            for row in _mapped_occurrence_overrides(source_dir, source_manifest, target_manifest, reviews):
                key = tuple(row[field] for field in (
                    "pdf_contains", "pdf_excludes", "page", "target_char", "stable_key", "x0", "y0"
                ))
                old = mapped_by_key.get(key)
                if old is not None and old["actual_reading"] != row["actual_reading"]:
                    raise ValueError(f"兩來源同位置 actual 判定衝突；原專案均保留，未套用：{key}")
                if old is None:
                    mapped_by_key[key] = row
                else:
                    old["note"] += "；同讀音另一來源：" + row["note"]
        mapped_overrides = list(mapped_by_key.values())
        if mapped_overrides:
            import actual_review as ar
            actual_root = sp.project_actual_evidence_root(target_dir)
            if ar._read_csv(actual_root / ar.OCCURRENCE_OVERRIDE_FILE, ar.OVERRIDE_HEADERS):
                raise ValueError("目標已有 actual occurrence override；未覆寫")
            ar._write_csv(actual_root / ar.OCCURRENCE_OVERRIDE_FILE, ar.OVERRIDE_HEADERS, mapped_overrides)
            sp.refresh_actual_project(target_dir)
        results = []
        conflicts = []
        for source_dir in source_dirs:
            result = import_project_decisions(source_dir, target_dir, _allow_incomplete_target=True)
            results.append(result)
            conflicts.extend(result["conflicts"])
        _publish_portable_outputs(
            target_dir, target_manifest,
            sp.normalize_db(sp.json_load_strict(target_dir / "人工判定資料庫.json")),
        )
        marker.unlink()
        return {"sources": results, "conflicts": conflicts,
                "imported": sum(item["imported"] for item in results),
                "duplicates": sum(item["duplicates"] for item in results),
                "matched_actual_overrides": len(mapped_overrides)}
    except Exception:
        # The source remains unchanged. Partial target files are retained with
        # an explicit marker for forensic inspection, never shown as success.
        raise
