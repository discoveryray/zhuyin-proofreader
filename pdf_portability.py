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
PROOF_VERSION = 1
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


def _load_project(output_dir: Path):
    import standalone_proofread as sp

    output_dir = Path(output_dir).resolve()
    manifest = sp.json_load_strict(output_dir / "校對工作階段.json")
    sp.validate_manifest_integrity(manifest)
    for info in manifest.get("pdfs", []):
        _artifact(output_dir, info, "actual")
        _artifact(output_dir, info, "candidate")
    db = sp.normalize_db(sp.json_load_strict(output_dir / "人工判定資料庫.json"))
    ledger = sp.materialize_ledger(manifest, db)
    stale = [row["review_id"] for row in ledger if row.get("review_event_replay_status")]
    if stale:
        raise ValueError(f"來源/目標工作階段已有失效判定，不能當有效成果轉用：{stale[:10]}")
    return manifest, db


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
    return {
        "source_session_id": manifest["session_id"],
        "source_manifest_integrity_sha256": manifest["manifest_integrity_sha256"],
        "staging": staging,
        "status": "RECHECK_LOCAL_PDF_REQUIRED" if staging["staged_groups"] else "NONE",
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


def _match_pdfs(proof: Mapping[str, Any], target_manifest: Mapping[str, Any], target_dir: Path):
    source_pdfs = list(proof["pdfs"])
    target_pdfs = list(target_manifest.get("pdfs", []))
    if len(source_pdfs) != len(target_pdfs):
        raise ValueError("PDF 數量不同；不支援部分教材成果移轉")
    by_visual = {}
    for target in target_pdfs:
        path = _source_pdf(target_manifest, target, target_dir)
        signature = _canonical(_page_signatures(path))
        by_visual.setdefault(signature, []).append(target)
    mapping = {}
    for source in source_pdfs:
        candidates = by_visual.get(_canonical(source["pages"]), [])
        if len(candidates) != 1:
            raise ValueError(
                f"PDF 頁面內容/圖片/文字/注音字形或頁面位置無法唯一對應：{source['pdf_name']}；"
                "請確認是同版完整教材"
            )
        target = candidates.pop()
        mapping[source["pdf_sha256"]] = target["pdf_sha256"]
    return mapping


def _anchor(entry: Mapping[str, Any]):
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
    return (page, *coordinates, str(entry.get("char") or ""))


def _map_reviews(source_manifest: Mapping[str, Any], target_manifest: Mapping[str, Any], pdf_map):
    target_by_anchor = {}
    for entry in target_manifest.get("records", []):
        key = (str(entry.get("pdf_sha256") or ""), _anchor(entry))
        if key in target_by_anchor:
            raise ValueError(f"目標位置不唯一：{key}")
        target_by_anchor[key] = entry
    mapped = {}
    used = set()
    for entry in source_manifest.get("records", []):
        key = (pdf_map.get(str(entry.get("pdf_sha256") or "")), _anchor(entry))
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
            if key not in {"portability_source", "portability_duplicate_sources"}}


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


def import_project_decisions(source_dir: Path, target_dir: Path) -> dict[str, Any]:
    """Import only proven mapped review events; conflicts remain untouched."""
    import standalone_proofread as sp

    source_dir, target_dir = Path(source_dir).resolve(), Path(target_dir).resolve()
    if source_dir == target_dir:
        raise ValueError("來源與目標專案必須不同")
    source_manifest, source_db = _load_project(source_dir)
    target_manifest, _ = _load_project(target_dir)
    source_actual = _actual_transfer_state(source_dir, source_manifest)
    _actual_transfer_state(target_dir, target_manifest)
    proof = _load_proof(source_dir, source_manifest)
    pdf_map = _match_pdfs(proof, target_manifest, target_dir)
    reviews = _map_reviews(source_manifest, target_manifest, pdf_map)
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
        imported, duplicates, provenance_updates, conflicts = 0, 0, 0, []
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
        if source_actual["staging"]["staged_groups"]:
            # A receipt preserves original staging only. It never becomes a
            # fresh target direct visual check or a Global quorum vote.
            source_receipt = {
                **source_actual,
                "target_session_id": target_manifest["session_id"],
                "source_pdf_sha256": [info["pdf_sha256"] for info in source_manifest["pdfs"]],
                "mapped_occurrences": {
                    source["occurrence_id"]: target["occurrence_id"]
                    for source, target in reviews.values()
                },
            }
            receipt_path = target_dir / PENDING_ACTUAL_FILE
            existing = (sp.json_load_strict(receipt_path) if receipt_path.exists()
                        else {"status": "RECHECK_LOCAL_PDF_REQUIRED", "sources": []})
            if (existing.get("status") != "RECHECK_LOCAL_PDF_REQUIRED"
                    or not isinstance(existing.get("sources"), list)):
                raise ValueError("目標來源 actual 暫存紀錄格式無法驗證；未覆寫")
            sources = existing["sources"]
            same_identity = [item for item in sources if (
                item.get("source_session_id"), item.get("source_manifest_integrity_sha256")
            ) == (
                source_receipt["source_session_id"], source_receipt["source_manifest_integrity_sha256"]
            )]
            if same_identity and same_identity != [source_receipt]:
                raise ValueError("同一來源 actual 暫存與既存紀錄不同；未覆寫")
            if not same_identity:
                sp.json_save(receipt_path, {
                    "status": "RECHECK_LOCAL_PDF_REQUIRED",
                    "sources": [*sources, source_receipt],
                }, expected_sha256=_sha(receipt_path) if receipt_path.exists() else None)
        if imported or provenance_updates:
            sp.json_save(path, candidate, expected_sha256=before)
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
        if local_pages != proof["pdfs"][0]["pages"]:
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
        target_manifest, _ = _load_project(target_dir)
        mapped_by_key = {}
        for source_dir in source_dirs:
            source_manifest, _ = _load_project(source_dir)
            proof = _load_proof(source_dir, source_manifest)
            reviews = _map_reviews(source_manifest, target_manifest, _match_pdfs(proof, target_manifest, target_dir))
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
            result = import_project_decisions(source_dir, target_dir)
            results.append(result)
            if result["conflicts"]:
                source_manifest, source_db = _load_project(source_dir)
                conflicts.extend({
                    **conflict, "source_session_id": source_manifest["session_id"],
                    "source_event": source_db["events"][conflict["source_review_id"]],
                    "target_event": sp.json_load_strict(target_dir / "人工判定資料庫.json")["events"][conflict["target_review_id"]],
                } for conflict in result["conflicts"])
        if conflicts:
            sp.json_save(target_dir / CONFLICT_FILE, {"conflicts": conflicts})
        marker.unlink()
        return {"sources": results, "conflicts": conflicts,
                "imported": sum(item["imported"] for item in results),
                "duplicates": sum(item["duplicates"] for item in results),
                "matched_actual_overrides": len(mapped_overrides)}
    except Exception:
        # The source remains unchanged. Partial target files are retained with
        # an explicit marker for forensic inspection, never shown as success.
        raise
