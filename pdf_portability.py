"""Explicit, fail-closed transfer of a sealed review project to another PDF.

This module never changes a source session, its workbooks, or its identities.
The transfer receipt is local project evidence, not Global glyph evidence.
"""

from __future__ import annotations

import copy
import base64
import hashlib
import json
import os
import tempfile
import uuid
import zlib
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

import fitz


PROOF_FILE = "PDF內容對應證據.json"
PROOF_VERSION = 2
PENDING_ACTUAL_FILE = "來源actual待重新核對.json"
INCOMPLETE_FILE = "跨電腦接續未完成.json"
CONFLICT_FILE = "跨專案判定衝突.json"
EXCEL_PROOF_SHEET = "跨電腦內容證據"
EXCEL_PROOF_VERSION = 1


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
    raw_db = sp.json_load_strict(output_dir / "人工判定資料庫.json")
    if not isinstance(raw_db, dict):
        raise ValueError("DATA_INTEGRITY_ERROR：人工判定資料庫根節點必須是物件")
    db = sp.normalize_db(raw_db)
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
        if event is None or event.get("action") == "保留待人工":
            unresolved.append(review_id)
        else:
            resolution = event.get("portability_conflict_resolution")
            if (not isinstance(resolution, dict)
                    or event.get("portability_source") is not None
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


def _excel_bound_rows(workbook, kind: str):
    """Bind every source row while leaving only the documented answer cells editable."""
    import standalone_proofread as sp

    sheet_name = "待判定候選" if kind == "expected" else "actual待判定"
    editable = ({"action", "proposed_expected_set", "proposed_expected_evidence",
                 "proposed_context_evidence", "exclusion_reason", "exclusion_evidence",
                 "note", *sp.CONFIRMATION_GATES}
                if kind == "expected" else
                {"decision", "actual_reading", "confidence", "sample_a_checked",
                 "sample_b_checked", "note"})
    sheet = workbook[sheet_name]
    rows = list(sheet.values)
    if not rows:
        raise ValueError("Excel 來源判定工作表空白")
    headers = [str(value or "") for value in rows[0]]
    if not all(headers) or len(headers) != len(set(headers)):
        raise ValueError("Excel 來源判定欄位無效")
    return [{header: ("" if value is None else value)
             for header, value in zip(headers, row) if header not in editable}
            for row in rows[1:]]


def write_excel_content_proof(workbook, output_dir: Path, manifest, db, *, kind: str,
                              snapshot_db=None, attestations=None,
                              allow_internal_incomplete: bool = False) -> None:
    """Embed a sealed source snapshot and all-page proof in an invisible sheet."""
    import standalone_proofread as sp

    if kind not in {"expected", "actual"} or EXCEL_PROOF_SHEET in workbook.sheetnames:
        raise ValueError("Excel 跨電腦內容證據種類或工作表無效")
    if not manifest.get("pdfs"):
        # Historical synthetic/session-only exports remain locally importable;
        # they cannot claim portable content evidence without any PDF bytes.
        return
    source_manifest, source_db = _load_project(
        output_dir, allow_incomplete=allow_internal_incomplete,
        allow_unresolved_conflict=allow_internal_incomplete)
    if source_manifest != manifest or source_db != sp.normalize_db(db):
        raise ValueError("Excel 匯出期間來源專案判定或封印已改變")
    if snapshot_db is None:
        snapshot_db = source_db
    else:
        snapshot_db = sp.normalize_db(snapshot_db)
        if any(row.get("review_event_replay_status") for row in
               sp.materialize_ledger(source_manifest, snapshot_db)):
            raise ValueError("Excel 補證來源快照判定失效")
    payload = {"version": EXCEL_PROOF_VERSION, "kind": kind,
               "exported_at": datetime.now().astimezone().isoformat(timespec="microseconds"),
               "manifest": source_manifest, "db": snapshot_db,
               "proof": _proof_payload(Path(output_dir), source_manifest),
               "bound_rows": _excel_bound_rows(workbook, kind),
               "attestations": attestations or {}}
    raw = _canonical(payload)
    encoded = base64.b64encode(zlib.compress(raw, level=9)).decode("ascii")
    chunks = [encoded[index:index + 30000] for index in range(0, len(encoded), 30000)]
    sheet = workbook.create_sheet(EXCEL_PROOF_SHEET)
    sheet.append(["version", EXCEL_PROOF_VERSION])
    sheet.append(["sha256", hashlib.sha256(raw).hexdigest()])
    sheet.append(["chunk_count", len(chunks)])
    for index, chunk in enumerate(chunks, 1):
        sheet.append([index, chunk])
    sheet.sheet_state = "veryHidden"


def load_excel_content_proof(xlsx: Path, target_dir: Path, target_manifest, *,
                             kind: str, workbook_session: str):
    """Validate the carried source and map every occurrence by displayed position."""
    import standalone_proofread as sp
    from openpyxl import load_workbook

    workbook = load_workbook(xlsx, read_only=True, data_only=True)
    try:
        if EXCEL_PROOF_SHEET not in workbook.sheetnames:
            return _legacy_exact_excel_mapping(xlsx, target_dir, target_manifest,
                                               kind=kind, workbook_session=workbook_session)
        rows = list(workbook[EXCEL_PROOF_SHEET].values)
        bound_rows = _excel_bound_rows(workbook, kind)
    finally:
        workbook.close()
    if (len(rows) < 4 or rows[0][:2] != ("version", EXCEL_PROOF_VERSION)
            or rows[1][0] != "sha256" or rows[2][0] != "chunk_count"
            or type(rows[2][1]) is not int or rows[2][1] < 1
            or len(rows) != rows[2][1] + 3
            or any(row[0] != index or not isinstance(row[1], str)
                   for index, row in enumerate(rows[3:], 1))):
        raise ValueError("Excel 跨電腦內容證據格式損壞")
    try:
        packed = base64.b64decode("".join(row[1] for row in rows[3:]), validate=True)
        decompressor = zlib.decompressobj()
        raw = decompressor.decompress(packed, 128 * 1024 * 1024 + 1)
        if (len(raw) > 128 * 1024 * 1024 or not decompressor.eof
                or decompressor.unconsumed_tail or decompressor.unused_data):
            raise ValueError("Excel 跨電腦內容證據超過大小上限或壓縮串流不完整")
        payload = json.loads(raw)
    except (ValueError, zlib.error, TypeError) as exc:
        raise ValueError("Excel 跨電腦內容證據無法解碼") from exc
    if (len(raw) > 128 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != rows[1][1]
            or not isinstance(payload, dict) or payload.get("version") != EXCEL_PROOF_VERSION
            or payload.get("kind") != kind or _canonical(payload) != raw):
        raise ValueError("Excel 跨電腦內容證據完整性或種類不符")
    source_manifest, source_db, proof = (payload.get(key) for key in ("manifest", "db", "proof"))
    if payload.get("bound_rows") != bound_rows:
        raise ValueError("Excel 來源判定列的原始識別、快照或位置已被修改")
    if not isinstance(source_manifest, dict) or not isinstance(source_db, dict) or not isinstance(proof, dict):
        raise ValueError("Excel 來源專案證據格式不完整")
    sp.validate_manifest_integrity(source_manifest)
    if str(source_manifest.get("session_id")) != workbook_session:
        raise ValueError("Excel 來源 session 識別與內容證據不符")
    source_db = sp.normalize_db(source_db)
    source_ledger = sp.materialize_ledger(source_manifest, source_db)
    if any(row.get("review_event_replay_status") for row in source_ledger):
        raise ValueError("Excel 來源專案含失效判定")
    if (proof.get("version") != PROOF_VERSION
            or proof.get("session_id") != source_manifest["session_id"]
            or proof.get("manifest_integrity_sha256") != source_manifest["manifest_integrity_sha256"]
            or [(item.get("pdf_sha256"), item.get("pdf_name")) for item in proof.get("pdfs", [])]
            != [(item.get("pdf_sha256"), item.get("pdf_name")) for item in source_manifest.get("pdfs", [])]):
        raise ValueError("Excel 來源全頁內容證據與封印不符")
    pdf_map, geometry = _match_pdfs(proof, source_manifest, target_manifest, Path(target_dir))
    reviews = _map_reviews(source_manifest, target_manifest, pdf_map, geometry)
    return source_manifest, source_db, source_ledger, reviews, pdf_map, payload["exported_at"], payload


def _legacy_exact_excel_mapping(xlsx: Path, target_dir: Path, target_manifest, *,
                                kind: str, workbook_session: str):
    """Narrow adapter for proofless files whose original IDs and snapshots match."""
    import actual_review as ar
    import standalone_proofread as sp

    if not workbook_session or workbook_session == target_manifest["session_id"]:
        raise ValueError("舊 Excel 缺少可驗證的跨 session 來源識別")
    for info in target_manifest.get("pdfs", []):
        _source_pdf(target_manifest, info, Path(target_dir))
    source_db = sp.normalize_db({})
    source_ledger = sp.materialize_ledger(target_manifest, source_db)
    by_review = {entry["review_id"]: entry for entry in source_ledger}
    if kind == "expected":
        rows = sp.workbook_rows(xlsx, "待判定候選", ["occurrence_id", "review_id",
                          "review_snapshot", "action", "expected_target_snapshot"])
        sp.assert_unique_ids(rows, "occurrence_id")
        sp.assert_unique_ids(rows, "review_id")
        checked = 0
        for number, row in enumerate(rows, 2):
            if not str(row.get("action") or "").strip():
                continue
            entry = by_review.get(str(row.get("review_id") or ""))
            if (entry is None or str(row.get("occurrence_id") or "") != entry["occurrence_id"]
                    or str(row.get("review_snapshot") or "") != sp._review_snapshot(entry)
                    or not sp._expected_target_unchanged(row, entry)):
                raise ValueError(f"舊 Excel row {number} 沒有全頁證據，且 exact ID/快照不符；不同 SHA 或檔名需由 A 補證")
            checked += 1
    else:
        _, rows = ar._load_sheet_rows(xlsx, "actual待判定")
        groups = {group["group_id"]: group for group in ar.build_actual_review_groups(source_ledger)}
        checked = 0
        for number, row in enumerate(rows, 2):
            if str(row.get("decision") or "").strip().upper() != "VERIFIED":
                continue
            group = groups.get(str(row.get("group_id") or ""))
            if (group is None or str(row.get("group_snapshot") or "") != group["group_snapshot"]
                    or str(row.get("sample_a_occurrence_id") or "") != group["members"][0]["occurrence_id"]
                    or str(row.get("sample_b_occurrence_id") or "") !=
                       (group["members"][1]["occurrence_id"] if len(group["members"]) > 1 else "")):
                raise ValueError(f"舊 actual row {number} 沒有全頁證據，且 exact group/快照不符；不同 SHA 需由 A 補證")
            checked += 1
    if not checked:
        raise ValueError("舊 Excel 沒有可核對的已填判定；無法跨 session 匯入")
    # This is an observed exact-ID relationship, not a recovered source seal.
    source_context = {
        "session_id": workbook_session,
        "manifest_integrity_sha256": "",
        "expected_asset_fingerprint": target_manifest.get("expected_asset_fingerprint"),
        "pdfs": copy.deepcopy(target_manifest["pdfs"]),
        "records": copy.deepcopy(target_manifest["records"]),
    }
    reviews = {entry["review_id"]: (entry, entry) for entry in source_ledger}
    pdf_map = {item["pdf_sha256"]: item["pdf_sha256"] for item in target_manifest["pdfs"]}
    payload = {"attestations": {"basis": "LEGACY_EXACT_IDS_NO_SOURCE_SEAL"}}
    return (source_context, source_db, source_ledger, reviews, pdf_map,
            datetime.now().astimezone().isoformat(timespec="microseconds"), payload)


def attach_filled_excel_proof(source_dir: Path, xlsx: Path) -> Path:
    """Attest an old filled workbook against the *current* sealed A project.

    This does not assert that the workbook caused an earlier A decision.  It
    verifies every filled answer against A's current evidence, then carries
    that present-time attestation and the original visible answers together.
    """
    import actual_review as ar
    import standalone_proofread as sp
    from openpyxl import load_workbook

    source_dir, xlsx = Path(source_dir).resolve(), Path(xlsx).resolve()
    manifest, current_db = _load_project(source_dir)
    workbook = load_workbook(xlsx)
    temporary = None
    try:
        if EXCEL_PROOF_SHEET in workbook.sheetnames:
            raise ValueError("Excel 已有跨電腦內容證據；不得重新封裝既有來源")
        kinds = [kind for kind, sheet in (("expected", "待判定候選"),
                                          ("actual", "actual待判定")) if sheet in workbook.sheetnames]
        if len(kinds) != 1:
            raise ValueError("舊 Excel 判定工作表種類無法唯一辨識")
        kind = kinds[0]
        metadata = sp.workbook_metadata(xlsx, "匯入中繼資料") if kind == "expected" else ar._load_sheet_rows(xlsx, "actual待判定")[0]
        if str(metadata.get("session_id") or "") != str(manifest["session_id"]):
            raise ValueError("舊 Excel session 與 A 現行封印不符，不能補證")
        if kind == "expected":
            rows = sp.workbook_rows(xlsx, "待判定候選", [
                "occurrence_id", "review_id", "review_snapshot", "action",
                "proposed_expected_set", "proposed_expected_evidence", "proposed_context_evidence",
                "exclusion_reason", "exclusion_evidence", *sp.CONFIRMATION_GATES, "note"])
            if str(metadata.get("expected_asset_fingerprint") or "") != str(manifest.get("expected_asset_fingerprint") or ""):
                raise ValueError("舊 Excel expected fingerprint 與 A 封印不符")
            snapshot_db = copy.deepcopy(current_db)
            attestations = {"basis": "CURRENT_SEALED_PROJECT_AT_ATTACHMENT", "expected_events": {}}
            for number, row in enumerate(rows, 2):
                action = str(row.get("action") or "").strip()
                if not action or action == "保留待人工":
                    continue
                review_id = str(row.get("review_id") or "")
                saved = current_db["events"].get(review_id)
                if saved is not None:
                    compared = {
                        "action": action,
                        "expected_set": str(row.get("proposed_expected_set") or "").strip(),
                        "expected_evidence": str(row.get("proposed_expected_evidence") or "").strip(),
                        "context_evidence": str(row.get("proposed_context_evidence") or "").strip(),
                        "exclusion_reason": str(row.get("exclusion_reason") or "").strip(),
                        "exclusion_evidence": str(row.get("exclusion_evidence") or "").strip(),
                        "note": str(row.get("note") or "").strip(),
                    }
                    compared.update({gate: str(row.get(gate) or "").strip().upper() == "Y"
                                     for gate in sp.CONFIRMATION_GATES})
                    if any(saved.get(key) != value for key, value in compared.items()):
                        raise ValueError(f"舊 Excel row {number} 判定與 A 目前保存事件不同，不能補證")
                    attestations["expected_events"][review_id] = copy.deepcopy(saved)
                    snapshot_db["events"].pop(review_id)
            ledger = sp.materialize_ledger(manifest, snapshot_db)
            by_id = {entry["review_id"]: entry for entry in ledger}
            for number, row in enumerate(rows, 2):
                action = str(row.get("action") or "").strip()
                if not action or action == "保留待人工":
                    continue
                entry = by_id.get(str(row.get("review_id") or ""))
                if (entry is None or str(row.get("occurrence_id") or "") != entry["occurrence_id"]
                        or str(row.get("review_snapshot") or "") != sp._review_snapshot(entry)
                        or not sp._expected_target_unchanged(row, entry)):
                    raise ValueError(f"舊 Excel row {number} 原始識別/快照/位置無法從 A 封印重建")
        else:
            metadata, rows = ar._load_sheet_rows(xlsx, "actual待判定")
            if str(metadata.get("actual_review_schema_version")) != ar.ACTUAL_REVIEW_SCHEMA_VERSION:
                raise ValueError("舊 Excel actual schema 與 A 不相容")
            snapshot_db = current_db
            attestations = {"basis": "CURRENT_SEALED_PROJECT_AT_ATTACHMENT", "actual_groups": {}}
            ledger = sp.materialize_ledger(manifest, current_db)
            by_occ = {entry["occurrence_id"]: entry for entry in ledger}
            current_pending = {group["group_id"]: group for group in ar.build_actual_review_groups(ledger)}
            override_path = sp.project_actual_evidence_root(source_dir) / ar.OCCURRENCE_OVERRIDE_FILE
            overrides = ar._read_csv(override_path, ar.OVERRIDE_HEADERS)
            fields = ("pdf_contains", "pdf_excludes", "page", "target_char", "stable_key", "x0", "y0")
            override_by_key = {tuple(item[field] for field in fields): item for item in overrides}
            for number, row in enumerate(rows, 2):
                if str(row.get("decision") or "").strip().upper() != "VERIFIED":
                    continue
                sample = by_occ.get(str(row.get("sample_a_occurrence_id") or ""))
                if sample is None:
                    raise ValueError(f"舊 actual row {number} 樣本 occurrence 已無法在 A 找到")
                group = current_pending.get(str(row.get("group_id") or ""))
                if group is None:
                    group = ar.build_actual_group_for_entry(ledger, sample)
                members = group["members"]
                reading = ar._canon(row.get("actual_reading"))
                if (str(row.get("group_id") or "") != group["group_id"]
                        or int(row.get("occurrence_count") or 0) != len(members)
                        or str(row.get("group_kind") or "") != group["kind"]
                        or str(row.get("exact_key") or "") != group["exact_key"]
                        or str(row.get("sample_a_occurrence_id") or "") != members[0]["occurrence_id"]
                        or str(row.get("sample_b_occurrence_id") or "") !=
                           (members[1]["occurrence_id"] if len(members) > 1 else "")
                        or not reading or str(row.get("actual_reading") or "").strip() != reading
                        or str(row.get("sample_a_checked") or "").upper() != "Y"
                        or (len(members) > 1 and str(row.get("sample_b_checked") or "").upper() != "Y")):
                    raise ValueError(f"舊 actual row {number} 群組/樣本/判定無法與 A 現況核對")
                if str(row.get("group_snapshot") or "") != group["group_snapshot"]:
                    for member in members:
                        local = override_by_key.get(ar._override_key_from_entry(member))
                        if local is None or ar._canon(local["actual_reading"]) != reading or member.get("actual") != reading:
                            raise ValueError(f"舊 actual row {number} A 目前 occurrence-local 判定不足以補證")
                    attestations["actual_groups"][str(number)] = {
                        "old_group_snapshot": str(row.get("group_snapshot") or ""),
                        "current_group_snapshot": group["group_snapshot"],
                        "group_id": group["group_id"], "reading": reading,
                        "member_occurrence_ids": [item["occurrence_id"] for item in members],
                        "override_rows": [copy.deepcopy(override_by_key[ar._override_key_from_entry(item)])
                                          for item in members],
                    }
        write_excel_content_proof(workbook, source_dir, manifest, current_db, kind=kind,
                                  snapshot_db=snapshot_db, attestations=attestations)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{xlsx.stem}.proof-", suffix=".xlsx", dir=xlsx.parent)
        os.close(descriptor)
        workbook.save(temporary)
        destination = xlsx.with_name(f"{xlsx.stem}_跨電腦_{uuid.uuid4().hex[:8]}.xlsx")
        os.replace(temporary, destination)
        temporary = None
        return destination
    finally:
        workbook.close()
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


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


def _proof_from_explicit_local(output_dir: Path, manifest: Mapping[str, Any],
                               local_pdf: Path, local_pages):
    try:
        return _load_proof(output_dir, manifest)
    except FileNotFoundError:
        infos = manifest.get("pdfs", [])
        if len(infos) != 1 or _sha(local_pdf) != infos[0].get("pdf_sha256"):
            raise
        # The selected file has the sealed source bytes; no path search or
        # inferred equivalence is needed to obtain its complete page proof.
        return {"pdfs": [{"pdf_sha256": infos[0]["pdf_sha256"],
                           "pdf_name": infos[0]["pdf_name"], "pages": local_pages}]}


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


def _excel_expected_decision(event: Mapping[str, Any]):
    import standalone_proofread as sp

    fields = ("action", "expected_set", "expected_evidence", "context_evidence",
              "exclusion_reason", "exclusion_evidence", "note", *sp.CONFIRMATION_GATES)
    return {key: event.get(key) for key in fields}


def _restore_exact_file(path: Path, original: bytes | None) -> None:
    if original is None:
        path.unlink(missing_ok=True)
        return
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.restore-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(original)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def import_expected_excel(target_dir: Path, xlsx: Path, *, dry_run: bool = False):
    """Import filled expected rows after full-page and occurrence-local mapping."""
    import standalone_proofread as sp

    target_dir, xlsx = Path(target_dir).resolve(), Path(xlsx).resolve()
    target_manifest, target_db = _load_project(target_dir)
    metadata = sp.workbook_metadata(xlsx, "匯入中繼資料")
    source_manifest, _, source_ledger, reviews, _, exported_at, _ = load_excel_content_proof(
        xlsx, target_dir, target_manifest, kind="expected",
        workbook_session=str(metadata.get("session_id") or ""))
    if str(metadata.get("expected_asset_fingerprint") or "") != str(source_manifest.get("expected_asset_fingerprint") or ""):
        raise ValueError("Excel expected fingerprint 與來源封印不符")
    allowed = {str(target_manifest.get("expected_asset_fingerprint") or ""),
               *map(str, target_manifest.get("compatible_expected_asset_fingerprints") or [])}
    if str(metadata.get("expected_asset_fingerprint") or "") not in allowed:
        raise ValueError("Excel 與目標 expected 資產 fingerprint 不相容")
    required = ["occurrence_id", "review_id", "review_snapshot", "state", "action",
                "proposed_expected_set", "proposed_expected_evidence", "proposed_context_evidence",
                "exclusion_reason", "exclusion_evidence", *sp.CONFIRMATION_GATES, "note"]
    rows = sp.workbook_rows(xlsx, "待判定候選", required)
    sp.assert_unique_ids(rows, "occurrence_id")
    sp.assert_unique_ids(rows, "review_id")
    source_by_id = {entry["review_id"]: entry for entry in source_ledger}
    target_ledger = sp.materialize_ledger(target_manifest, target_db)
    target_by_id = {entry["review_id"]: entry for entry in target_ledger}
    target_base_by_id = {entry["review_id"]: entry for entry in
                         sp.materialize_ledger(target_manifest, sp.normalize_db({}))}
    actions = []
    skipped = 0
    for number, row in enumerate(rows, 2):
        action = str(row.get("action") or "").strip()
        if not action or action == "保留待人工":
            skipped += 1
            continue
        source = source_by_id.get(str(row.get("review_id") or ""))
        if source is None or str(row.get("occurrence_id") or "") != source["occurrence_id"]:
            raise ValueError(f"Excel row {number} 來源 occurrence/review 識別無法驗證")
        if (str(row.get("review_snapshot") or "") != sp._review_snapshot(source)
                or not sp._expected_target_unchanged(row, source)):
            raise ValueError(f"Excel row {number} 來源 snapshot/文字位置已變更")
        pair = reviews.get(source["review_id"])
        if pair is None:
            raise ValueError(f"Excel row {number} 來源位置無法映射")
        target = target_by_id[pair[1]["review_id"]]
        prior_event = target_db["events"].get(target["review_id"]) or {}
        target_for_validation = target_base_by_id[target["review_id"]] if prior_event else target
        prior_sources = [prior_event.get("portability_source"),
                         *prior_event.get("portability_duplicate_sources", [])]
        if any(isinstance(item, dict) and item.get("source_excel_sha256") == _sha(xlsx)
               and item.get("review_id") == source["review_id"]
               and item.get("source_excel_row") == number for item in prior_sources):
            skipped += 1
            continue
        source_context = source.get("source_record") or {}
        target_context = target.get("source_record") or {}
        if (str(source_context.get("所在行") or "") != str(target_context.get("所在行") or "")
                or str(source_context.get("局部詞境") or "") != str(target_context.get("局部詞境") or "")):
            raise ValueError(f"Excel row {number} 教材詞境不同，不能沿用判定")
        expected_only = action in {"補建expected證據", "解決expected證據"}
        if action not in sp.DECISIONS:
            raise ValueError(f"Excel row {number} action 無效")
        if not expected_only:
            semantic = ("state", "actual", "actual_evidence", "expected_set",
                        "expected_evidence", "context_evidence")
            if any(source.get(key) != target_for_validation.get(key) for key in semantic):
                raise ValueError(f"Excel row {number} 目標 actual/expected 證據或狀態不同；須重新核對")
            if action == "確認現版差異" and any(
                    source.get(key) != target.get(key) for key in
                    ("actual", "actual_evidence", "expected_set", "expected_evidence", "context_evidence")):
                raise ValueError(f"Excel row {number} 目標當前 actual/expected 證據不同；六個確認 gate 不可沿用")
        proposed = list(sp.normalize_expected_set(row.get("proposed_expected_set")))
        if (not prior_event and action == "補建expected證據" and sp.infer_expected_status(target) == "RESOLVED"
                and proposed != list(sp.normalize_expected_set(target.get("expected_set")))):
            raise ValueError(f"Excel row {number} 目標已有不同正式 expected，須明確解決衝突")
        event = {
            "action": action, "expected_set": str(row.get("proposed_expected_set") or "").strip(),
            "expected_evidence": str(row.get("proposed_expected_evidence") or "").strip(),
            "context_evidence": str(row.get("proposed_context_evidence") or "").strip(),
            "exclusion_reason": str(row.get("exclusion_reason") or "").strip(),
            "exclusion_evidence": str(row.get("exclusion_evidence") or "").strip(),
            "note": str(row.get("note") or "").strip(),
            "source": "跨電腦 Excel expected 判定轉接", "updated_at": exported_at,
            "_safe_expected_rebase": expected_only and sp._review_snapshot(source) != sp._review_snapshot(target),
        }
        for gate in sp.CONFIRMATION_GATES:
            value = str(row.get(gate) or "").strip().upper()
            if value not in {"", "Y", "N"}:
                raise ValueError(f"Excel row {number} gate {gate} 必須是 Y/N")
            event[gate] = value == "Y"
        for entry, label in ((source, "來源"), (target_for_validation, "目標")):
            replay = sp._apply_review_event(entry, event)
            if replay.get("review_event_replay_status"):
                raise ValueError(f"Excel row {number} {label}判定失效：{replay['review_event_replay_status']}")
        identity = _source_identity(source_manifest, source, source["review_id"],
                                    target_manifest, target, event)
        identity["source_excel_sha256"] = _sha(xlsx)
        identity["source_excel_row"] = number
        identity["source_excel_decision"] = copy.deepcopy(row)
        actions.append((target, event, identity))
    if dry_run:
        return len(actions), skipped, target_dir / "注音校對_最終報告.xlsx"

    written_candidate_sha = None
    with sp.project_delivery_lock(sp.project_actual_evidence_root(target_dir)):
        live_manifest = sp.json_load_strict(target_dir / "校對工作階段.json")
        sp.validate_manifest_integrity(live_manifest)
        if live_manifest["manifest_integrity_sha256"] != target_manifest["manifest_integrity_sha256"]:
            raise ValueError("Excel 匯入期間目標封印已變動")
        db_path = target_dir / "人工判定資料庫.json"
        before = _sha(db_path)
        raw_db = sp.json_load_strict(db_path)
        if not isinstance(raw_db, dict):
            raise ValueError("目標判定資料庫根節點不是物件")
        if sp.normalize_db(raw_db) != target_db:
            raise ValueError("Excel 匯入期間目標判定資料庫已變動")
        candidate = copy.deepcopy(target_db)
        imported, duplicates, conflicts = 0, 0, []
        for target, event, identity in actions:
            review_id = target["review_id"]
            current = candidate["events"].get(review_id)
            if current is not None:
                known = [current.get("portability_source"),
                         *current.get("portability_duplicate_sources", [])]
                if identity in known:
                    duplicates += 1
                    continue
                if _excel_expected_decision(current) == _excel_expected_decision(event):
                    current.setdefault("portability_duplicate_sources", []).append(identity)
                    duplicates += 1
                    continue
                conflicts.append({"target_review_id": review_id,
                                  "source_review_id": identity["review_id"],
                                  "source_session_id": source_manifest["session_id"],
                                  "source_pdf_sha256": identity["pdf_sha256"],
                                  "source_occurrence_id": identity["occurrence_id"],
                                  "target_session_id": target_manifest["session_id"],
                                  "target_pdf_sha256": target["pdf_sha256"],
                                  "target_occurrence_id": target["occurrence_id"],
                                  "source_identity": identity,
                                  "target_identity": copy.deepcopy(current.get("portability_source")),
                                  "source_event": {**event, "portability_source": identity},
                                  "target_event": copy.deepcopy(current)})
                candidate["events"].pop(review_id)
                continue
            candidate["events"][review_id] = {**event, "portability_source": identity}
            imported += 1
        ledger = sp.materialize_ledger(target_manifest, candidate)
        if any(row.get("review_event_replay_status") for row in ledger):
            raise ValueError("Excel 匯入後判定無法在目標安全重播")
        if imported or duplicates or conflicts:
            marker_path = target_dir / INCOMPLETE_FILE
            receipt_path = target_dir / CONFLICT_FILE
            presentation_paths = [target_dir / name for name in
                                  ("待人工確認.json", "注音校對_最終報告.xlsx", "pipeline_status.json")]
            original_files = {path: path.read_bytes() if path.exists() else None
                              for path in [db_path, receipt_path, marker_path, *presentation_paths]}
            marker_before = marker_path.read_bytes() if marker_path.exists() else None
            receipt_before = receipt_path.read_bytes() if receipt_path.exists() else None
            db_committed = False
            try:
                sp.json_save(marker_path,
                             {"status": "PRESENTATION_PENDING", "source_excel_sha256": _sha(xlsx)})
                if conflicts:
                    sp.json_save(receipt_path, {"version": 1, "conflicts": conflicts})
                sp.json_save(db_path, candidate, expected_sha256=before)
                db_committed = True
                written_candidate_sha = _sha(db_path)
            except Exception:
                if not db_committed and _sha(db_path) == before:
                    _restore_exact_file(receipt_path, receipt_before)
                    _restore_exact_file(marker_path, marker_before)
                raise
    if imported or duplicates or conflicts:
        try:
            _publish_portable_outputs(target_dir, target_manifest, candidate)
        except Exception:
            with sp.project_delivery_lock(sp.project_actual_evidence_root(target_dir)):
                if (_sha(target_dir / "人工判定資料庫.json") != written_candidate_sha
                        or not (target_dir / INCOMPLETE_FILE).exists()):
                    raise ValueError("Excel expected 發布失敗且目標資料已再變動；保留未完成標記待查核")
                # Restore the original user views, conflict receipt and DB;
                # leave the fail-closed marker in place until every restore succeeds.
                for path in [*presentation_paths, target_dir / CONFLICT_FILE,
                             target_dir / "人工判定資料庫.json"]:
                    _restore_exact_file(path, original_files[path])
                _restore_exact_file(target_dir / INCOMPLETE_FILE,
                                    original_files[target_dir / INCOMPLETE_FILE])
            raise
        (target_dir / INCOMPLETE_FILE).unlink()
    if conflicts:
        raise ValueError(f"Excel expected 同位置有 {len(conflicts)} 筆不同判定；兩份來源已保留於 {CONFLICT_FILE}，目標位置已標待人工裁決（不是寫入回滾）")
    return imported, skipped + duplicates, target_dir / "注音校對_最終報告.xlsx"


def import_actual_excel(target_dir: Path, xlsx: Path):
    """Carry source visual decisions as local occurrence evidence, never a vote."""
    import actual_review as ar
    import standalone_proofread as sp
    from global_glyph_promotion import (
        direct_visual_project_transaction, load_promotion_outbox,
        post_commit_recovery_plan_from_results,
    )

    target_dir, xlsx = Path(target_dir).resolve(), Path(xlsx).resolve()
    target_manifest, target_db = _load_project(target_dir)
    target_actual_state = _actual_transfer_state(target_dir, target_manifest)
    if target_actual_state["status"] != "NONE":
        raise ValueError("目標尚有未核對的 actual 暫存/來源轉送；先完成本地恢復")
    metadata, rows = ar._load_sheet_rows(xlsx, "actual待判定")
    from openpyxl import load_workbook
    workbook = load_workbook(xlsx, read_only=True)
    try:
        legacy = EXCEL_PROOF_SHEET not in workbook.sheetnames
    finally:
        workbook.close()
    if legacy and _legacy_actual_already_imported(target_dir, target_manifest, target_db, xlsx, rows):
        return sp.ActualImportResult(0, 0, target_dir / "注音校對_最終報告.xlsx",
                                     {"project_actual_commit": "NO_CHANGES",
                                      "global_promotion_delivery": "NOT_ATTEMPTED"})
    source_manifest, _, source_ledger, reviews, _, _, proof_payload = load_excel_content_proof(
        xlsx, target_dir, target_manifest, kind="actual",
        workbook_session=str(metadata.get("session_id") or ""))
    if (str(metadata.get("actual_review_schema_version")) != ar.ACTUAL_REVIEW_SCHEMA_VERSION
            or str(metadata.get("session_id")) != str(source_manifest["session_id"])):
        raise ValueError("Excel actual group/session 來源契約不符")
    groups = {group["group_id"]: group for group in ar.build_actual_review_groups(source_ledger)}
    source_by_occ = {entry["occurrence_id"]: entry for entry in source_ledger}
    attached_groups = (proof_payload.get("attestations") or {}).get("actual_groups") or {}
    target_ledger = sp.materialize_ledger(target_manifest, target_db)
    by_target_occ = {entry["occurrence_id"]: entry for entry in target_ledger}
    workbook_sha = _sha(xlsx)
    decisions = []
    seen_groups = set()
    for number, row in enumerate(rows, 2):
        group_id = str(row.get("group_id") or "").strip()
        decision = str(row.get("decision") or "").strip().upper()
        if not group_id and not decision:
            continue
        if group_id in seen_groups:
            raise ValueError(f"Excel actual row {number} group_id 重複")
        seen_groups.add(group_id)
        group = groups.get(group_id)
        attached = attached_groups.get(str(number))
        if attached is not None:
            sample = source_by_occ.get(str(row.get("sample_a_occurrence_id") or ""))
            if sample is None or not isinstance(attached, dict):
                raise ValueError(f"Excel actual row {number} 補證樣本無效")
            group = ar.build_actual_group_for_entry(source_ledger, sample)
            members_for_check = group["members"]
            supplied_overrides = attached.get("override_rows")
            if (attached.get("group_id") != group_id
                    or attached.get("old_group_snapshot") != str(row.get("group_snapshot") or "")
                    or attached.get("current_group_snapshot") != group["group_snapshot"]
                    or attached.get("member_occurrence_ids") !=
                       [entry["occurrence_id"] for entry in members_for_check]
                    or attached.get("reading") != ar._canon(row.get("actual_reading"))
                    or not isinstance(supplied_overrides, list)
                    or len(supplied_overrides) != len(members_for_check)):
                raise ValueError(f"Excel actual row {number} 補證群組/判定與來源不符")
            for member, override in zip(members_for_check, supplied_overrides):
                fields = ("pdf_contains", "pdf_excludes", "page", "target_char", "stable_key", "x0", "y0")
                if (not isinstance(override, dict)
                        or tuple(override.get(field) for field in fields) != ar._override_key_from_entry(member)
                        or ar._canon(override.get("actual_reading")) != attached["reading"]
                        or member.get("actual") != attached["reading"]):
                    raise ValueError(f"Excel actual row {number} 補證 occurrence-local 判定無效")
        elif group is None or str(row.get("group_snapshot") or "") != group["group_snapshot"]:
            raise ValueError(f"Excel actual row {number} 來源 group/snapshot 失效")
        members = group["members"]
        if (str(row.get("group_kind") or "") != group["kind"]
                or str(row.get("exact_key") or "") != group["exact_key"]
                or int(row.get("occurrence_count") or 0) != len(members)
                or str(row.get("sample_a_pdf") or "") != str(members[0].get("pdf_name") or "")
                or str(row.get("sample_a_page") or "") != str(members[0].get("printed_page") or "")):
            raise ValueError(f"Excel actual row {number} 來源 group 內容/位置欄位已變更")
        if (str(row.get("sample_a_occurrence_id") or "") != members[0]["occurrence_id"]
                or str(row.get("sample_b_occurrence_id") or "")
                != (members[1]["occurrence_id"] if len(members) > 1 else "")):
            raise ValueError(f"Excel actual row {number} 樣本 occurrence 與來源 group 不符")
        if decision not in {"", "VERIFIED", "UNRESOLVED"}:
            raise ValueError(f"Excel actual row {number} decision 無效")
        if decision != "VERIFIED":
            continue
        reading = ar._canon(row.get("actual_reading"))
        if not reading or str(row.get("actual_reading") or "").strip() != reading:
            raise ValueError(f"Excel actual row {number} 注音不是 canonical")
        a_checked = str(row.get("sample_a_checked") or "").upper() == "Y"
        b_checked = str(row.get("sample_b_checked") or "").upper() == "Y"
        if (not a_checked or (len(members) > 1 and not b_checked)
                or str(row.get("confidence") or "") not in {"高", "中"}):
            raise ValueError(f"Excel actual row {number} 視覺核對樣本/信心不足")
        # The source's direct visual decision would cover its exact peers only
        # after both source samples were checked. Every target position is still
        # independently mapped from the source's complete occurrence roster.
        selected = members if len(members) <= 1 or b_checked else members[:1]
        target_entries = []
        for member in selected:
            pair = reviews.get(member["review_id"])
            if pair is None:
                raise ValueError(f"Excel actual row {number} 來源 occurrence 無位置映射")
            target_entries.append(by_target_occ[pair[1]["occurrence_id"]])
        provenance = {
            "source_session_id": source_manifest["session_id"],
            "source_pdf_sha256": [item["pdf_sha256"] for item in source_manifest["pdfs"]],
            "source_group_id": group_id, "source_group_snapshot": group["group_snapshot"],
            "source_occurrence_ids": [item["occurrence_id"] for item in selected],
            "source_review_ids": [item["review_id"] for item in selected],
            "target_session_id": target_manifest["session_id"],
            "target_occurrence_ids": [item["occurrence_id"] for item in target_entries],
            "source_excel_sha256": workbook_sha, "source_excel_row": number,
            "source_decision": copy.deepcopy(row),
        }
        decisions.append((group_id, reading, target_entries, provenance))

    root = sp.project_actual_evidence_root(target_dir)
    outbox = load_promotion_outbox(root)
    if any(item["status"] == "PENDING" for item in outbox["items"]):
        raise ValueError("目標尚有待配送的原有 Global outbox；先完成來源交易恢復")
    override_path = root / ar.OCCURRENCE_OVERRIDE_FILE
    original_rows = ar._read_csv(override_path, ar.OVERRIDE_HEADERS)
    fields = ("pdf_contains", "pdf_excludes", "page", "target_char", "stable_key", "x0", "y0")
    by_key = {tuple(row[field] for field in fields): row for row in original_rows}
    changed = False
    conflict_records = []
    recovery_results = []
    for group_id, reading, entries, provenance in decisions:
        target_ids = []
        for entry in entries:
            key = ar._override_key_from_entry(entry)
            old = by_key.get(key)
            if old is not None and ar._canon(old["actual_reading"]) not in {"", reading}:
                conflict_records.append({"target_occurrence_id": entry["occurrence_id"],
                                         "target_review_id": entry["review_id"],
                                         "target_record": copy.deepcopy(old),
                                         "source_record": copy.deepcopy(provenance),
                                         "source_reading": reading})
                continue
            audit = json.dumps(provenance, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if old is not None and audit in old.get("note", ""):
                continue
            new_row = dict(zip(fields, key)) | {
                "actual_reading": reading,
                "source": "portable Excel occurrence-local actual (no Global quorum)",
                "note": ((old.get("note", "") + "；") if old else "") + audit,
            }
            by_key[key] = new_row
            target_ids.append(entry["occurrence_id"])
            changed = True
        if target_ids:
            recovery_results.append({"group_id": group_id, "reading": reading,
                                     "verified_occurrence_ids": sorted(target_ids),
                                     "affected_occurrence_ids": sorted(target_ids)})
    if conflict_records:
        receipt = target_dir / "跨Excel_actual衝突.json"
        with sp.project_delivery_lock(root):
            if ar._read_csv(override_path, ar.OVERRIDE_HEADERS) != original_rows:
                raise ValueError("目標 actual 證據於衝突登記前已變動；未覆寫任何紀錄")
            previous_sha = _sha(receipt) if receipt.exists() else None
            previous = sp.json_load_strict(receipt) if receipt.exists() else {"version": 1, "conflicts": []}
            if (not isinstance(previous, dict) or previous.get("version") != 1
                    or not isinstance(previous.get("conflicts"), list)):
                raise ValueError("既有 Excel actual 衝突紀錄格式無效；未覆寫")
            all_conflicts = copy.deepcopy(previous["conflicts"])
            for conflict in conflict_records:
                source = conflict["source_record"]
                key = (conflict["target_occurrence_id"], source["source_excel_sha256"],
                       source["source_excel_row"], conflict["source_reading"])
                matching = [item for item in all_conflicts if isinstance(item, dict)
                            and isinstance(item.get("source_record"), dict)
                            and (item.get("target_occurrence_id"),
                                 item["source_record"].get("source_excel_sha256"),
                                 item["source_record"].get("source_excel_row"),
                                 item.get("source_reading")) == key]
                if matching:
                    if len(matching) != 1 or matching[0] != conflict:
                        raise ValueError("既有 Excel actual 衝突來源識別有矛盾；未覆寫")
                else:
                    all_conflicts.append(conflict)
            if all_conflicts != previous["conflicts"]:
                sp.json_save(receipt, {"version": 1, "conflicts": all_conflicts},
                             expected_sha256=previous_sha)
        raise ValueError("Excel actual 同位置已有不同判定；既有與本次來源均保留於跨Excel_actual衝突.json，目標未覆寫")
    if not changed:
        return sp.ActualImportResult(0, 0, target_dir / "注音校對_最終報告.xlsx",
                                     {"project_actual_commit": "NO_CHANGES", "global_promotion_delivery": "NOT_ATTEMPTED"})
    marker = target_dir / INCOMPLETE_FILE
    sp.json_save(marker, {"status": "ACTUAL_EXCEL_REFRESH_PENDING",
                          "source_excel_sha256": workbook_sha,
                          "pre_state": _actual_excel_file_snapshot(target_dir)})
    committed = False
    try:
        with direct_visual_project_transaction(root) as bind_recovery_plan:
            if any(item["status"] == "PENDING" for item in load_promotion_outbox(root)["items"]):
                raise ValueError("目標 Global outbox 於匯入期間變動；未寫入 actual")
            live_manifest = sp.json_load_strict(target_dir / "校對工作階段.json")
            sp.validate_manifest_integrity(live_manifest)
            if live_manifest["manifest_integrity_sha256"] != target_manifest["manifest_integrity_sha256"]:
                raise ValueError("目標封印於 actual Excel 匯入期間變動；未寫入")
            if ar._read_csv(override_path, ar.OVERRIDE_HEADERS) != original_rows:
                raise ValueError("目標 actual occurrence 證據於匯入期間變動；未覆寫")
            ar._write_csv(override_path, ar.OVERRIDE_HEADERS,
                          sorted(by_key.values(), key=lambda row: tuple(row[field] for field in fields)))
            bind_recovery_plan(post_commit_recovery_plan_from_results(recovery_results))
        committed = True
    except Exception:
        if not committed:
            marker.unlink(missing_ok=True)
        raise
    recovered = resume_actual_excel_project(target_dir)
    if recovered.get("project_actual_commit") != "COMMITTED":
        raise ValueError("Excel actual 交易未取得 COMMITTED 恢復紀錄")
    return sp.ActualImportResult(len(decisions), recovered["cleared_actual_dependent_event_count"],
                                 Path(recovered["refresh_report"]),
                                 {"project_actual_commit": "COMMITTED",
                                  "global_promotion_delivery": recovered["global_promotion_delivery"],
                                  "project_refresh": recovered["project_refresh"]})


def _legacy_actual_already_imported(target_dir, manifest, db, xlsx, rows):
    """Recognize the exact old workbook already recorded on every local member."""
    import actual_review as ar
    import standalone_proofread as sp

    sha = _sha(xlsx)
    ledger = sp.materialize_ledger(manifest, db)
    by_occ = {entry["occurrence_id"]: entry for entry in ledger}
    override_path = sp.project_actual_evidence_root(target_dir) / ar.OCCURRENCE_OVERRIDE_FILE
    fields = ("pdf_contains", "pdf_excludes", "page", "target_char", "stable_key", "x0", "y0")
    overrides = {tuple(item[field] for field in fields): item
                 for item in ar._read_csv(override_path, ar.OVERRIDE_HEADERS)}
    checked = 0
    for row in rows:
        if str(row.get("decision") or "").strip().upper() != "VERIFIED":
            continue
        sample = by_occ.get(str(row.get("sample_a_occurrence_id") or ""))
        if sample is None:
            return False
        group = ar.build_actual_group_for_entry(ledger, sample)
        members = group["members"]
        reading = ar._canon(row.get("actual_reading"))
        if (not reading or group["group_id"] != str(row.get("group_id") or "")
                or len(members) != int(row.get("occurrence_count") or 0)
                or members[0]["occurrence_id"] != str(row.get("sample_a_occurrence_id") or "")
                or (members[1]["occurrence_id"] if len(members) > 1 else "") !=
                   str(row.get("sample_b_occurrence_id") or "")):
            return False
        for member in members:
            override = overrides.get(ar._override_key_from_entry(member))
            if (override is None or ar._canon(override["actual_reading"]) != reading
                    or member.get("actual") != reading):
                return False
            note = override.get("note", "")
            decoder = json.JSONDecoder()
            audits = []
            for index, char in enumerate(note):
                if char == "{":
                    try:
                        item, _ = decoder.raw_decode(note[index:])
                    except (ValueError, TypeError):
                        continue
                    audits.append(item)
            if not any(isinstance(audit, dict) and audit.get("source_excel_sha256") == sha
                       and audit.get("source_group_id") == group["group_id"] for audit in audits):
                return False
        checked += 1
    return checked > 0


def _actual_excel_file_snapshot(output_dir: Path):
    import actual_review as ar
    import standalone_proofread as sp

    output_dir = Path(output_dir)
    root = sp.project_actual_evidence_root(output_dir)
    paths = {
        "manifest": output_dir / "校對工作階段.json",
        "db": output_dir / "人工判定資料庫.json",
        "pending": output_dir / "待人工確認.json",
        "report": output_dir / "注音校對_最終報告.xlsx",
        "status": output_dir / "pipeline_status.json",
        "override": root / ar.OCCURRENCE_OVERRIDE_FILE,
    }
    return {name: _sha(path) if path.exists() else None for name, path in paths.items()}


def resume_actual_excel_project(output_dir: Path):
    """Resolve only the durable actual-Excel marker, never a publication marker."""
    import standalone_proofread as sp
    from global_glyph_promotion import (
        acknowledge_project_refresh, committed_project_recovery,
        recover_pending_project_actual_write,
    )

    output_dir = Path(output_dir).resolve()
    marker_path = output_dir / INCOMPLETE_FILE
    marker = sp.json_load_strict(marker_path)
    if (not isinstance(marker, dict)
            or marker.get("status") not in {"ACTUAL_EXCEL_REFRESH_PENDING", "ACTUAL_EXCEL_RECOVERED"}
            or not isinstance(marker.get("source_excel_sha256"), str)
            or len(marker["source_excel_sha256"]) != 64):
        raise ValueError("此未完成標記不是可恢復的 actual Excel 交易；不得清除")
    root = sp.project_actual_evidence_root(output_dir)
    recover_pending_project_actual_write(root)
    pending = committed_project_recovery(root)
    if marker["status"] == "ACTUAL_EXCEL_RECOVERED":
        if marker.get("post_state") != _actual_excel_file_snapshot(output_dir):
            raise ValueError("actual Excel 已恢復標記與現存封印/報告不符；未清除")
        if pending is not None:
            if pending[0] != marker.get("recovery_token"):
                raise ValueError("actual Excel COMMITTED journal 與已恢復標記不符")
            acknowledge_project_refresh(root, pending[0])
        marker_path.unlink()
        return {"project_actual_commit": "COMMITTED", "project_refresh": "SUCCESS",
                "cleared_actual_dependent_event_count": 0,
                "refresh_report": str(output_dir / "注音校對_最終報告.xlsx"),
                "global_promotion_delivery": {"status": "NO_PENDING"}}
    if pending is None:
        if marker.get("pre_state") != _actual_excel_file_snapshot(output_dir):
            raise ValueError("actual Excel 未提交交易的原資料狀態已變動；保留標記待人工查核")
        marker_path.unlink()
        return {"project_actual_commit": "ROLLED_BACK", "project_refresh": "NOT_STARTED"}
    recovered = sp.recover_committed_actual_project(output_dir, acknowledge=False)
    if recovered is None or recovered.get("project_refresh") != "SUCCESS":
        raise ValueError("actual Excel COMMITTED 交易未完成安全恢復")
    sp.json_save(marker_path, {"status": "ACTUAL_EXCEL_RECOVERED",
                               "source_excel_sha256": marker["source_excel_sha256"],
                               "recovery_token": pending[0],
                               "post_state": _actual_excel_file_snapshot(output_dir)})
    acknowledge_project_refresh(root, pending[0])
    marker_path.unlink()
    return recovered


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
        "source_conflict_resolution": copy.deepcopy(event.get("portability_conflict_resolution")),
        "source_manual_expected_decision": copy.deepcopy(event.get("manual_expected_decision")),
    }


def _transfer_event(event, source, target):
    """Rebind a validated human expected target to the proven local occurrence."""
    from occurrence_ledger import manual_expected_target, valid_manual_expected_decision

    transferred = copy.deepcopy(event)
    if "manual_expected_decision" in event:
        if not valid_manual_expected_decision({**source, **event}):
            raise ValueError("來源人工 expected 判定位置或語境無法驗證")
        transferred["manual_expected_decision"]["target"] = manual_expected_target(target)
        if not valid_manual_expected_decision({**target, **transferred}):
            raise ValueError("目標人工 expected 判定位置或語境無法驗證")
    return transferred


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
                             _allow_incomplete_target: bool = False,
                             _selected_local_pdf: Path | None = None) -> dict[str, Any]:
    """Import only proven mapped review events; conflicts remain untouched."""
    import standalone_proofread as sp

    source_dir, target_dir = Path(source_dir).resolve(), Path(target_dir).resolve()
    if source_dir == target_dir:
        raise ValueError("來源與目標專案必須不同")
    source_manifest, source_db = _load_project(source_dir)
    target_manifest, _ = _load_project(target_dir, allow_incomplete=_allow_incomplete_target)
    source_actual = _actual_transfer_state(source_dir, source_manifest)
    _actual_transfer_state(target_dir, target_manifest)
    if _selected_local_pdf is None:
        proof = _load_proof(source_dir, source_manifest)
    else:
        local_pdf = Path(_selected_local_pdf).resolve()
        target_pdfs = target_manifest.get("pdfs", [])
        if (len(target_pdfs) != 1 or not local_pdf.is_file()
                or _sha(local_pdf) != target_pdfs[0].get("pdf_sha256")):
            raise ValueError("明確指定的本地 PDF 與目標封印 SHA 不符")
        proof = _proof_from_explicit_local(source_dir, source_manifest,
                                            local_pdf, _page_signatures(local_pdf))
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
        raw_db = sp.json_load_strict(path)
        if not isinstance(raw_db, dict):
            raise ValueError("DATA_INTEGRITY_ERROR：目標人工判定資料庫根節點必須是物件")
        target_db = sp.normalize_db(raw_db)
        candidate = copy.deepcopy(target_db)
        imported, duplicates, provenance_updates, conflicts, conflict_records = 0, 0, 0, [], []
        for source_id, event in source_db["events"].items():
            if source_id not in reviews:
                raise ValueError(f"來源 event 無法對應 occurrence：{source_id}")
            source, target = reviews[source_id]
            target_id = target["review_id"]
            current = candidate["events"].get(target_id)
            identity = _source_identity(source_manifest, source, source_id, target_manifest, target, event)
            transferred = _transfer_event(event, source, target)
            if current is not None:
                if _event_payload(current) == _event_payload(transferred):
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
        proof = _proof_from_explicit_local(source_dir, source_manifest, local_pdf, local_pages)
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
            proof = _proof_from_explicit_local(source_dir, source_manifest, local_pdf, local_pages)
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
            existing_rows = ar._read_csv(actual_root / ar.OCCURRENCE_OVERRIDE_FILE, ar.OVERRIDE_HEADERS)
            fields = ("pdf_contains", "pdf_excludes", "page", "target_char", "stable_key", "x0", "y0")
            existing_keys = {tuple(row[field] for field in fields) for row in existing_rows}
            if any(tuple(row[field] for field in fields) in existing_keys for row in mapped_overrides):
                raise ValueError("目標已有同位置 actual occurrence override；未覆寫")
            ar._write_csv(actual_root / ar.OCCURRENCE_OVERRIDE_FILE, ar.OVERRIDE_HEADERS,
                          [*existing_rows, *mapped_overrides])
            sp.refresh_actual_project(target_dir)
            refreshed_manifest, _ = _load_project(target_dir, allow_incomplete=True)
            if (refreshed_manifest.get("session_id") != target_manifest.get("session_id")
                    or {(row["review_id"], row["occurrence_id"]) for row in refreshed_manifest["records"]}
                    != {(row["review_id"], row["occurrence_id"]) for row in target_manifest["records"]}):
                raise ValueError("actual 重建後目標工作階段或 occurrence 清單改變；未匯入")
            target_manifest = refreshed_manifest
        results = []
        conflicts = []
        for source_dir in source_dirs:
            result = import_project_decisions(source_dir, target_dir,
                                              _allow_incomplete_target=True,
                                              _selected_local_pdf=local_pdf)
            results.append(result)
            conflicts.extend(result["conflicts"])
        final_manifest, final_db = _load_project(target_dir, allow_incomplete=True,
                                                  allow_unresolved_conflict=True)
        if final_manifest.get("session_id") != target_manifest.get("session_id"):
            raise ValueError("匯入期間目標工作階段改變；未發布")
        _publish_portable_outputs(target_dir, final_manifest, final_db)
        if any(sp.needs_expected_review(row) for row in sp.materialize_ledger(final_manifest, final_db)):
            sp.export_pending_for_gpt(target_dir, _allow_internal_portable=True)
        marker.unlink()
        return {"sources": results, "conflicts": conflicts,
                "imported": sum(item["imported"] for item in results),
                "duplicates": sum(item["duplicates"] for item in results),
                "matched_actual_overrides": len(mapped_overrides)}
    except Exception:
        # The source remains unchanged. Partial target files are retained with
        # an explicit marker for forensic inspection, never shown as success.
        raise
