"""Read-only presentation of one validated Phase 5 plan and Global snapshot.

No evidence parsing, identity/quorum decisions, or migration writes live here.
"""
from __future__ import annotations

from dataclasses import dataclass
import json

import global_exact_glyph_library as library
import legacy_global_migration as migration


ITEM_LABELS = {
    "CANDIDATE": "可匯入候選（計畫）",
    "INSUFFICIENT": "映射證據不足（計畫）",
    "CONFLICT": "來源衝突 MIGRATION_CONFLICT（計畫）",
    "NON_GLOBAL_ELIGIBLE": "不符合全域字形資格",
    "DURABLE_QUARANTINE": "Global 已持久化隔離",
    "READ_SIDE_SUPPRESSION": "legacy-v0 讀取時抑制",
}
ITEM_EXPLANATIONS = {
    "CANDIDATE": "字形識別已由目前 PDF 重證，且有舊樣本可映射。這是待匯入的歷史候選；如需新的直接證據，請另外查看原頁並重新確認。",
    "INSUFFICIENT": "字形識別已重證，但沒有舊樣本可可靠映射。目前只能列為不可重用的歷史候選；請先查明原始樣本及位置。",
    "CONFLICT": "來源保留互斥讀音，計畫標記為 MIGRATION_CONFLICT。請查閱每個保留讀音及原頁；本次未寫入 Global quarantine，也不裁決讀音。",
    "NON_GLOBAL_ELIGIBLE": "目前 PDF 字形已被判定不符合 Global exact identity 資格，未列入 import 項目。請查閱詳細原因；不能用名稱或局部輪廓替代 exact proof。",
    "DURABLE_QUARANTINE": "Global 快照已有持久化的 QUARANTINED_CONFLICT。此字形不能重用，須依明確的衝突處理契約另行處理；本視窗不解除隔離。",
    "READ_SIDE_SUPPRESSION": "合法 legacy-v0 歷史候選有互斥讀音，既有 read-side safety 已抑制重用。這是讀取診斷，不能宣稱已持久化隔離或撤銷核准。",
}
SCOPE_NOTE = (
    "唯讀預檢只驗證來源計畫與當次 Global 讀取，不模擬完整 APPLY 交易。\n"
    "通過不代表已匯入、已核准或可直接重用，也不保證未來 APPLY 成功或已排除所有匯入衝突。\n"
    "所有 legacy 證據 quorum 貢獻永遠為 0；待確認位置不會產生 staging、manual actual 或直接證據。"
)


@dataclass(frozen=True)
class MigrationInspectionItem:
    status: str
    source_file: str
    reading: str
    identity_text: str
    explanation: str
    targets_text: str
    details: str


@dataclass(frozen=True)
class MigrationInspectionReport:
    project_output: str
    pdf_roots: tuple[str, ...]
    status: str
    explanation: str
    global_status: str = "UNCONFIRMED"
    items: tuple[MigrationInspectionItem, ...] = ()
    # None means no validated whole-batch statistics, never a zero-item success.
    counts: tuple[int, int, int, int, int] | None = None
    source_input_hashes: tuple[tuple[str, str | None], ...] = ()
    details: str = ""


def _json(value):
    return json.dumps(value, ensure_ascii=False, indent=2)


def blocked_report(project, roots, exc):
    """Translate failure categories for operators; never change their verdict."""
    raw = f"{type(exc).__name__}: {exc}"
    message = str(exc)
    global_status = exc.status if isinstance(exc, library.GlobalLibraryError) else "UNCONFIRMED"
    if isinstance(exc, library.GlobalLibraryError):
        reason = {
            library.BUSY: "Global 資料庫忙碌；請待其他交易完成後，再手動開始預檢。",
            library.CORRUPT: "Global 資料庫損毀或無法驗證；請保留原庫並交由維護者檢查。",
            library.SCHEMA_INCOMPATIBLE: "Global 資料庫版本不相容；請使用符合該資料庫契約的工具。",
            library.PERMISSION_DENIED: "無法讀取 Global 資料庫；請確認讀取權限後再預檢。",
        }.get(global_status, "來源或 Global 資料未通過安全驗證；請依詳細錯誤核對資料並交由維護者檢查。")
        # Phase 5 also uses the library's strict canonical field validators
        # while reading source CSVs. Their exception class alone does not prove
        # a database failure; keep the original detail without misattribution.
        if "already-canonical Bopomofo" in message:
            reason = "資料讀音不符合標準注音格式；請依詳細錯誤核對原始資料，整批不能略過此錯誤。"
    elif "source changed" in message:
        reason = "來源在查閱期間變動，舊結果已失效；請等待來源穩定後重新按「開始預檢」。"
    elif "pending project transaction" in message:
        reason = "專案有尚待處理的 actual 交易；請先查閱「交付與恢復狀態」，依既有流程處理後再預檢。"
    elif "PDF missing or SHA mismatch" in message:
        reason = "找不到符合封存 SHA 的原始 PDF；請選擇含有原檔的 PDF 來源資料夾，再開始預檢。"
    elif isinstance(exc, FileNotFoundError):
        reason = "必要來源檔不存在；請確認所選校對專案及其原始檔案是否完整。預檢不建立或修復缺檔。"
    elif "duplicate" in message:
        reason = "來源有重複資料或識別；整批未通過。請將詳細錯誤交由來源維護者核對。"
    elif "bbox" in message or "locator" in message:
        reason = "目前 PDF 位置或完整 bbox 無法驗證；請核對原 PDF 與封存專案，不能以猜測的位置重新確認。"
    elif "identity" in message or "glyph" in message or "source example" in message:
        reason = "目前字形識別或舊樣本映射與來源不一致；請核對原 PDF 與專案證據，整批不能略過此錯誤。"
    elif "SHA" in message or "seal" in message:
        reason = "來源檔內容或封存完整性不符；請保留現有檔案並核對正確來源，不要重算 seal 或 SHA 來通過預檢。"
    elif "incompatible" in message:
        reason = "專案資料契約不相容；請核對詳細版本資訊及工具支援範圍。"
    else:
        reason = "來源未通過完整驗證；請查看詳細錯誤，確認所選專案與原始資料，並交由維護者核對。"
    return MigrationInspectionReport(str(project), tuple(map(str, roots)), "BLOCKED", reason,
                                     global_status=global_status, details=raw)


def _identity_text(identity):
    if identity["kind"] == library.TTF_GLYF_SHA256:
        return "TTF 完整 glyf"
    return "CFF 完整 recording／樣式 " + identity["style_group"]


def _targets_text(targets, resolved_pdfs):
    if not targets:
        return "沒有可查證的待確認位置；未提供字形預覽，不補造 PDF、頁碼或 bbox。"
    lines = ["下列為已驗證的原頁位置，尚未重新確認；PDF 文字字元僅供定位，不是字形預覽。"]
    for target in targets:
        lines.append(
            f"\nPDF：{target['pdf_name']}\n原檔：{resolved_pdfs[target['pdf_name']]}\n"
            f"實體頁碼：{target['physical_page']}；定位字元：{target['character']}\n"
            f"bbox (x0, y0, x1, y1)：{tuple(target['bbox'])}\n"
            "occurrence／review 識別與 PDF SHA 請見詳細資訊。")
    return "\n".join(lines)


def inspect_legacy_migration(project_output, *, pdf_roots=(), global_library_root=None):
    """Only a fresh, explicitly requested dry-run can produce a valid report."""
    project, roots = str(project_output), tuple(map(str, pdf_roots))
    try:
        plan = migration.migrate_project(project_output, pdf_roots=roots,
                                         global_library_root=global_library_root, apply=False)
        items = []
        targets_by_import = {}
        for target in plan["reconfirmation_targets"]:
            targets_by_import.setdefault(target["import_id"], []).append(target)
        for intent in plan["intents"]:
            payload = intent["payload"]
            targets = targets_by_import.get(payload["import_id"], [])
            status = payload["status"]
            items.append(MigrationInspectionItem(
                status, plan["import_sources"][payload["import_id"]], payload["reading"],
                _identity_text(payload["identity"]), ITEM_EXPLANATIONS[status],
                _targets_text(targets, plan["resolved_pdfs"]),
                _json({"intent": intent, "reconfirmation_targets": targets})))
        for skipped in plan["skipped"]:
            items.append(MigrationInspectionItem(
                "NON_GLOBAL_ELIGIBLE", skipped["source_file"], skipped["reading"],
                _identity_text(skipped), ITEM_EXPLANATIONS["NON_GLOBAL_ELIGIBLE"],
                _targets_text([], {}), _json(skipped)))
        for field, status in (("global_quarantined_identities", "DURABLE_QUARANTINE"),
                              ("global_read_side_conflicts", "READ_SIDE_SUPPRESSION")):
            for record in plan[field]:
                items.append(MigrationInspectionItem(
                    status, "Global 資料庫（既有紀錄）", "／".join(record["conflicting_readings"]),
                    _identity_text(record["identity"]), ITEM_EXPLANATIONS[status],
                    "此 Global 診斷未提供可查證的原頁位置或字形預覽。", _json(record)))
        targets = plan["reconfirmation_targets"]
        counts = (len(plan["intents"]), len({item["payload"]["glyph_id"] for item in plan["intents"]}),
                  len(targets), len({(item["pdf_sha256"], item["occurrence_id"]) for item in targets}),
                  len(plan["skipped"]))
        return MigrationInspectionReport(
            project, roots, "VALID", "來源計畫與 Global 讀取驗證通過（唯讀）。",
            plan["global_store_status"], tuple(items), counts,
            tuple(plan["source_input_hashes"].items()), _json(plan))
    except Exception as exc:
        return blocked_report(project, roots, exc)


def filter_items(report, status="全部", query=""):
    if report is None or report.status != "VALID":
        return ()
    needle = query.strip().casefold()
    return tuple(item for item in report.items
                 if (status == "全部" or ITEM_LABELS[item.status] == status) and
                 (not needle or needle in " ".join((item.source_file, item.reading,
                                                    item.identity_text, item.details)).casefold()))


def report_summary(report):
    if report is None:
        return "請明確選擇校對專案，必要時選 PDF 來源資料夾，再按「開始預檢」。"
    if report.status != "VALID":
        return "BLOCKED｜整批預檢未通過，沒有可用的成功統計。\n" + report.explanation
    imports, glyphs, targets, locations, skipped = report.counts
    global_text = "Global 尚未建立，維持 absent。" if report.global_status == library.ABSENT else "Global 已存在且可嚴格讀取。"
    return (
        "預檢通過｜" + report.explanation + "\n" + global_text + "\n"
        f"本次計畫：import 項目 {imports} 筆；其中不同字形 {glyphs} 個；"
        f"待確認目標 {targets} 筆（import × occurrence），對應 {locations} 個不同位置。\n"
        f"NON_GLOBAL_ELIGIBLE 診斷 {skipped} 筆；Global 既有診斷另列，不計入上述來源統計。\n"
        "這些是項目／字形／位置的計數，不是獨立來源數；legacy quorum 貢獻固定為 0。")
