"""Read-only per-PDF rechecks and a finite, explicitly registered usage index.

The index is advisory and rebuildable. It is never glyph truth, a dependency
fingerprint, a source of quorum, or permission to write an unopened project.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from glyph_learning_images import learning_data_root, _plain_path, _storage_lock, _atomic_write

REGISTRY_CONTRACT = "glyph-learning-usage/1"
_SHA = re.compile(r"[0-9a-f]{64}")


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("學習使用索引含重複 JSON key")
            result[key] = value
        return result
    return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique)


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _source_path(output_dir, info):
    """Use the selected project's existing portability/source binding rules."""
    import standalone_proofread as sp
    wanted = info.get("pdf_sha256")
    if not isinstance(wanted, str) or _SHA.fullmatch(wanted) is None:
        raise ValueError("SOURCE_INVALID：PDF SHA 缺失")
    path = sp._resolve_pdf_path(info, output_dir)
    if not path.is_file() or _sha(path) != wanted:
        raise ValueError("SOURCE_INVALID：所選 PDF 來源遺失或已變更")
    return path.resolve()


def recheck_project_dependencies(output_dir, *, repository=None, manifest=None):
    """Re-prove sealed PDF rosters and compare existing logical Global subsets.

    CURRENT only concerns these actual dependencies, not overall completion.
    Missing historical contracts are UNPROVABLE rather than compatible. No
    project, Global DB, or usage index is created or changed by this function.
    """
    import actual_review as ar
    import standalone_proofread as sp
    import global_exact_glyph_library as library
    from cross_version_compat import parse_components
    from sealed_workbook_paths import bind_workbooks, owned_path, trusted_root

    output_dir = trusted_root(output_dir)
    manifest_path = output_dir / "校對工作階段.json"
    manifest_raw = manifest_path.read_bytes()
    current = _json(manifest_raw)
    sp.validate_manifest_integrity(current)
    if manifest is not None and manifest != current:
        raise ValueError("DATA_INTEGRITY_ERROR：依賴重核的工作階段已變更")
    if not isinstance(current.get("pdfs"), list) or not current["pdfs"]:
        raise ValueError("SOURCE_INVALID：沒有封印 PDF inventory")
    bindings = {(i, kind): (relative, wanted) for i, kind, relative, wanted
                in bind_workbooks(output_dir, current, require_candidate=False)}
    repository = repository or library.GlobalExactGlyphRepository.resolved()
    snapshot = repository.load_snapshot()
    rows = []
    for index, info in enumerate(current["pdfs"]):
        relative, wanted_workbook = bindings[index, "actual"]
        workbook = owned_path(output_dir, relative)
        pdf = _source_path(output_dir, info)
        dependencies = ar.actual_workbook_global_exact_dependencies(workbook, pdf)
        try:
            metadata = sp.workbook_metadata(workbook)
            components = parse_components(metadata.get("actual_asset_fingerprint_components"))
            recorded = library.canonical_global_exact_glyph_evidence_hashes(
                components.get("global_exact_glyph_evidence_hashes"))
            if (metadata.get("pdf_sha256") != info["pdf_sha256"]
                    or recorded["scope_mode"] != library.GLOBAL_EXACT_PER_PDF_SCOPE_MODE):
                raise ValueError("來源/依賴 contract 不符")
        except (ValueError, TypeError, library.GlobalLibraryValidationError):
            recorded = None
        fresh = (library.global_exact_glyph_evidence_hashes(snapshot, dependencies)
                 if dependencies is not None else None)
        status = ("UNPROVABLE" if recorded is None or fresh is None else
                  "CURRENT" if recorded == fresh else "STALE")
        identities = []
        if dependencies is not None:
            for identity in dependencies:
                if identity.identity_eligibility == library.NON_GLOBAL_ELIGIBLE:
                    continue
                identities.append({**asdict(identity), "glyph_id": library.compute_global_glyph_id(
                    identity.kind, identity.style_group, identity.glyph_sha256,
                    identity_eligibility=identity.identity_eligibility)})
        rows.append({"pdf_name": info.get("pdf_name") or pdf.name,
                     "pdf_sha256": info["pdf_sha256"], "source_path": str(pdf),
                     "actual_workbook": str(relative), "actual_workbook_sha256": wanted_workbook,
                     "status": status, "dependencies": identities,
                     "recorded_global": recorded, "current_global": fresh})
        if _sha(pdf) != info["pdf_sha256"] or _sha(workbook) != wanted_workbook:
            raise ValueError("SOURCE_INVALID：依賴重核期間來源或 workbook 變動")
    if manifest_path.read_bytes() != manifest_raw:
        raise ValueError("DATA_INTEGRITY_ERROR：依賴重核期間工作階段變動")
    status = ("UNPROVABLE" if any(row["status"] == "UNPROVABLE" for row in rows) else
              "STALE" if any(row["status"] == "STALE" for row in rows) else "CURRENT")
    return {"status": status, "output_dir": str(output_dir), "session_id": current["session_id"],
            "manifest_sha256": current["manifest_integrity_sha256"], "pdfs": rows}


def refresh_stale_project_dependencies(output_dir, *, repository=None):
    """Refresh only the explicitly opened project after a proven Global drift."""
    import standalone_proofread as sp
    check = recheck_project_dependencies(output_dir, repository=repository)
    if check["status"] == "UNPROVABLE":
        raise ValueError("actual Global 依賴無法核實；未自動改寫教材")
    if check["status"] == "CURRENT":
        return check
    # Surface-only projection retains old decoder seals. A proven Global
    # change must go through the existing staged decoder/publication path.
    sp.refresh_actual_project(output_dir, _force_decoder_refresh=True)
    current = recheck_project_dependencies(output_dir, repository=repository)
    if current["status"] != "CURRENT":
        raise ValueError("actual 依賴更新後仍無法證明相容；請保留教材待辦並重新核對")
    return current


def _registry_path(path=None):
    return _plain_path(Path(path) if path is not None else learning_data_root() / "usage.json")


def _validate_registry(value):
    import global_exact_glyph_library as library
    if (not isinstance(value, dict) or set(value) != {"contract", "projects"}
            or value["contract"] != REGISTRY_CONTRACT or not isinstance(value["projects"], list)):
        raise ValueError("學習使用索引 contract 不相容")
    seen = set()
    for project in value["projects"]:
        if (not isinstance(project, dict) or set(project) != {
                "output_dir", "session_id", "manifest_sha256", "registered_at", "pdfs"}
                or not isinstance(project["output_dir"], str) or not Path(project["output_dir"]).is_absolute()
                or not isinstance(project["session_id"], str) or not project["session_id"]
                or not isinstance(project["manifest_sha256"], str)
                or _SHA.fullmatch(project["manifest_sha256"]) is None
                or not isinstance(project["registered_at"], str)
                or not project["registered_at"].endswith("Z")
                or not isinstance(project["pdfs"], list)):
            raise ValueError("學習使用索引 project 欄位不符")
        datetime.fromisoformat(project["registered_at"].replace("Z", "+00:00"))
        key = str(Path(project["output_dir"]).absolute()).casefold()
        if key in seen:
            raise ValueError("學習使用索引 project 重複")
        seen.add(key)
        pdfs = set()
        for row in project["pdfs"]:
            if (not isinstance(row, dict) or set(row) != {
                    "pdf_name", "pdf_sha256", "source_path", "actual_workbook", "actual_workbook_sha256",
                    "status", "dependencies", "recorded_global", "current_global"}
                    or row["status"] not in {"CURRENT", "STALE", "UNPROVABLE"}
                    or not isinstance(row["pdf_sha256"], str) or _SHA.fullmatch(row["pdf_sha256"]) is None
                    or not isinstance(row["actual_workbook_sha256"], str)
                    or _SHA.fullmatch(row["actual_workbook_sha256"]) is None
                    or not isinstance(row["pdf_name"], str) or not row["pdf_name"]
                    or not isinstance(row["source_path"], str) or not Path(row["source_path"]).is_absolute()
                    or not isinstance(row["actual_workbook"], str)
                    or not isinstance(row["dependencies"], list)):
                raise ValueError("學習使用索引 PDF 欄位不符")
            if row["pdf_sha256"] in pdfs:
                raise ValueError("學習使用索引 PDF 重複")
            pdfs.add(row["pdf_sha256"])
            for component in (row["recorded_global"], row["current_global"]):
                if component is not None:
                    library.canonical_global_exact_glyph_evidence_hashes(component)
            glyphs = set()
            for identity in row["dependencies"]:
                if not isinstance(identity, dict) or set(identity) != {
                        "kind", "style_group", "glyph_sha256", "identity_eligibility", "glyph_id"}:
                    raise ValueError("學習使用索引 identity 欄位不符")
                glyph_id = library.compute_global_glyph_id(identity["kind"], identity["style_group"],
                    identity["glyph_sha256"], identity_eligibility=identity["identity_eligibility"])
                if glyph_id != identity["glyph_id"] or glyph_id in glyphs:
                    raise ValueError("學習使用索引 identity 不符或重複")
                glyphs.add(glyph_id)
    return value


def _load_registry(path):
    if not path.exists():
        return {"contract": REGISTRY_CONTRACT, "projects": []}
    raw = path.read_bytes()
    if len(raw) > 16 * 1024 * 1024:
        raise ValueError("學習使用索引超出容量限制")
    return _validate_registry(_json(raw))


def register_usage(output_dir, *, repository=None, registry_path=None):
    """Call only for a project explicitly opened or successfully recognized."""
    check = recheck_project_dependencies(output_dir, repository=repository)
    if check["status"] == "UNPROVABLE":
        raise ValueError("依賴無法核實；未把不完整 roster 登記為已知使用專案")
    project = {key: check[key] for key in ("output_dir", "session_id", "manifest_sha256", "pdfs")}
    project["registered_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    path = _registry_path(registry_path)
    with _storage_lock(path.parent):
        value = _load_registry(path)
        key = str(Path(project["output_dir"]).absolute()).casefold()
        value["projects"] = [row for row in value["projects"]
                             if str(Path(row["output_dir"]).absolute()).casefold() != key]
        value["projects"].append(project)
        value["projects"].sort(key=lambda row: row["output_dir"].casefold())
        _validate_registry(value)
        raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
        if len(raw) > 16 * 1024 * 1024:
            raise ValueError("學習使用索引超出容量限制；未覆寫")
        _atomic_write(path, raw)
    return project


def known_impacts(glyph_ids, *, registry_path=None):
    """List known usage only; never scan for projects or rewrite their data."""
    if isinstance(glyph_ids, str):
        glyph_ids = [glyph_ids]
    requested = set(glyph_ids)
    if any(not isinstance(value, str) or _SHA.fullmatch(value) is None for value in requested):
        raise ValueError("影響回查需要完整 glyph_id")
    registry = _load_registry(_registry_path(registry_path))
    projects = []
    for project in registry["projects"]:
        matches = [row for row in project["pdfs"]
                   if requested.intersection(identity["glyph_id"] for identity in row["dependencies"])]
        if not matches:
            continue
        status = "REGISTERED"
        try:
            path = _plain_path(Path(project["output_dir"]) / "校對工作階段.json")
            current = _json(path.read_bytes())
            import standalone_proofread as sp
            sp.validate_manifest_integrity(current)
            if current.get("manifest_integrity_sha256") != project["manifest_sha256"]:
                status = "PROJECT_CHANGED"
            else:
                # Inspect only these registered finite paths. Do not invoke
                # the project's relocation search for unopened projects.
                from sealed_workbook_paths import owned_path
                for row in matches:
                    pdf = _plain_path(Path(row["source_path"]))
                    workbook = owned_path(project["output_dir"], row["actual_workbook"])
                    if _sha(pdf) != row["pdf_sha256"] or _sha(workbook) != row["actual_workbook_sha256"]:
                        status = "SOURCE_CHANGED"
                        break
        except (OSError, ValueError, TypeError):
            status = "UNAVAILABLE"
        projects.append({"output_dir": project["output_dir"], "session_id": project["session_id"],
                         "registered_at": project["registered_at"], "status": status, "pdfs": matches})
    return {"scope": "REGISTERED_PROJECTS_ONLY", "projects": projects,
            "notice": "僅列出明確登記的已知使用專案；未開啟教材不會背景改寫，未登記舊專案不在涵蓋範圍。"}
