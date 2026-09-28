# v5.7.0 Implementation / Release Audit

## 1. Release baseline

- 正式 `main` v5.6.2：`c55f6f36bd6a6a8e00d70e47330933c05bbbd614`。
- 本次 release-prep 的 `develop` base：`3131c1942b1883b6567f989b22cd9fa7b92b66fe`。
- `origin/main...origin/develop`：develop ahead 26、behind 0。
- Release-prep branch：`release/v5.7.0-prep`；只整理 release metadata、使用者文件、implementation audit 與窄型 version consistency test。
- 26 commits 的 release inventory 以 Git history 與 diff 逐項核對，涵蓋 fingerprint contract、semantics epoch、Windows CI、安全指引、manual actual Phase 1／2A／2B／3A 及其修正；本次不以聊天摘要取代 repository evidence。

主要 implementation commits 包括：

- `a9e6afb`／`3af68e0`：hash/fingerprint architecture audit 與 required compatibility contract。
- `802c8d8`／`8ddef76`：actual／expected semantics epochs 與 exact-known legacy adapters。
- `76af2fe`／`9af8bdb`／`f8901fd`：Windows hosted CI baseline 與修正。
- `025aa1b`／`847762f`：durable manual actual staging 與 restart isolation。
- `7e9e046`：transactional staged batch apply。
- `957bbcb`／`995f8dd`：GUI batch orchestration 與 checked-occurrence blocker 修正。
- `3d85b50`：staged checked occurrence 的 actionable queue progression。

## 2. Release objectives

本次將所有 current tool release metadata 統一為 `5.7.0`，補齊 main v5.6.2 到 develop 的 release notes，更新一般文編實際操作的 manual actual 批次流程，並保存可追溯的 release validation。這不是功能開發，不修改校對規則、decoder/resolver、transaction、fingerprint compatibility、schema、semantics epoch 或 runtime asset truth。

歷史 `5.6.2` literal 維持原意，包括舊 CHANGELOG／audit、upgrade notes、released-v5.6.2 compatibility profiles、歷史測試名稱、fixture 與註解；沒有全 repository blind replace。

## 3. Fingerprint compatibility contract

- Producer 與 comparator 共用 required fingerprint contract registry；actual／expected 各自要求對應 schema、reuse policy、semantics epoch 與 evidence components。
- 缺少必要欄位、未知 future contract 或未列入 allowlist 的 legacy payload 均 fail closed。
- Tool version 與 implementation source hash 保留為 audit metadata，不作 semantic reuse boundary。
- Dynamic actual evidence 維持 per-PDF exact dependency scope；未退回全域粗粒度 invalidation，也沒有弱化 cache fingerprint。
- Release-prep 未修改 `cross_version_compat.py`、fingerprint producer/comparator 邏輯或 contract registry。

## 4. Semantics epoch contract

- `ACTUAL_DECODER_SEMANTICS_EPOCH = "1"`，未修改。
- `EXPECTED_RESOLVER_SEMANTICS_EPOCH = "1"`，未修改。
- Released v5.6.2、Phase 0B1 transitional 與 v5.5.1 adapters 只接受各自 exact known contract；unknown legacy/future fingerprint 仍 fail closed。
- `5.7.0` 是 release counter，不是相同 evidence 產生不同讀音的語意變更，因此不 bump epoch。

## 5. CI baseline

Develop 已建立 GitHub hosted `windows-latest` matrix，涵蓋 Python 3.12／3.13，並依序執行 runtime asset integrity、full unittest、full pytest、compileall、committed whitespace 與 clean-working-tree checks。GUI layout tests 使用 deterministic Tk event synchronization，不使用 sleep 規避 race。本次 release-prep 未修改 `.github/workflows/ci.yml`；本 audit 的 validation results 是本機實測，不冒充尚未執行的 hosted CI run。

## 6. Manual actual Phase 1 — durable staging

- 人工 visual actual 先寫入 project-owned `_專案證據/actual/manual_actual_staging.json`。
- Staging durable、restart-safe，並以 `group_id`、`group_snapshot` 與 exact identity deterministic upsert／dedupe。
- Staged decision 不是 authoritative actual evidence，不改五個 actual evidence files，不加入 `dynamic_actual_hashes()`，也不觸發 decode、refresh 或報告重建。
- Stage orchestration 每次重載 manifest／DB、驗證 artifact integrity、materialize current ledger，再由 current entry 重建 live group；不信任 GUI cached group。

## 7. Phase 2A — transactional batch apply

- 正式套用前對整批 current live groups 進行 deterministic order 與 whole-batch revalidation。
- 一次 transaction 更新 occurrence overrides、TTF/CFF verified truth、glyph conflicts 與 provenance 五個 authoritative files。
- Apply／acknowledgement 失敗時，還原每一檔原始 bytes 或原始不存在狀態。
- Staging acknowledgement 使用 atomic compare-and-remove；stale/newer upsert 不會被誤清除，unrelated concurrent staging 會保留。
- Exact glyph 的互斥人工讀音維持 conflict quarantine：直接 checked occurrence 可使用 local correction，衝突 glyph 不提升為 reusable global truth，亦不轉成 expected 問題。

## 8. Phase 2B — GUI staging and one-shot refresh

- `ActualReadingDialog` 的動作改為「暫存這筆 actual」；只呈現 actual lane，不顯示 expected 或字典答案。
- `ReviewApp.correct_actual()` submit 後只呼叫 staging service，不立即開 refresh progress、不啟動 decode thread、不 reload authoritative ledger/workbook。
- 主畫面由 durable staging summary 顯示「套用 actual 修正（N）」；N 是 staged exact-group count，restart 後直接由 staging file 恢復。
- Final apply 從 current manifest／DB／ledger 重新 build live groups，只呼叫一次 Phase 2A batch apply、一次 `_clear_actual_dependent_events()`、一次 `refresh_actual_project()`。
- Refresh 仍交付完整 session PDF roster 給既有 pipeline，以保留 whole-book manifest、occurrence ledger、reconciliation、completion gate、historical regression 與 CFF cross-file evidence pool。未受影響 PDF 由 per-PDF fingerprint/cache reuse；同一 invalidated PDF 在單次 refresh pass 最多 decode 一次。
- Refresh 後對 batch frozen decisions 的所有直接 `checked_occurrence_ids` 驗證 canonical actual 等於 staged reading；不讀 expected 來決定 pass/fail。
- 若 authoritative evidence 已正式套用但後續 clear／refresh／postcondition 失敗，錯誤會明確區分「evidence 已套用，project refresh／驗證未完成」，不對可能已部分改寫的 workbook 做危險 rollback。

## 9. Phase 3A — actionable queue progression

- GUI actionable queue 是 authoritative non-terminal entries 減去 durable `staged_checked_occurrence_ids`。
- 一次直接核對多個 occurrences 後，checked rows 立即從待人工操作畫面隱藏、denominator 下降並前進下一筆。
- `staged_member_occurrence_ids` 只描述 group roster；未直接 checked 的 peer 不會被誤當成人工完成，仍保留在 queue。
- Last-item index 會安全 clamp；reload 與後續 save-event 都套用同一 filter。
- 所有 actionable rows 都已 staged、但尚未 batch apply 時，GUI 顯示仍有 N 組等待套用，button 保持 enabled；不把 authoritative pending／completion gate 假裝成完成。

## 10. Real textbook acceptance

《115國小健體2上》已由使用者以真實 GUI 互動驗收：連續 durable staging 多筆 actual、一次勾選同 group 多個 occurrences、queue 正確下降／前進、transactional batch apply 與 one-shot incremental refresh 均完成；「課本其實正確但 actual decoder 辨識錯」亦由「更多…→實際注音辨識有誤…」修正，完成後目前未再發現其他異常。

這是使用者對真實教材的操作驗收，不是 repository fixture，也不是 GitHub CI 結果；CI 不取代此證據，audit 亦不捏造教材檔案或自動測試計數。

## 11. Safety boundaries

- Actual／expected 證據鏈完全獨立；expected 不決定 manual actual，actual 也不反推 expected。
- 未因 release bump 修改 semantics epoch、fingerprint schema、ledger/session/workbook/review identity schema。
- 未修改 `actual_review.py`、`cross_version_compat.py` semantic logic、`review_gui.py` logic、`occurrence_ledger.py`、pronunciation rules、regression assets、CI workflow 或 `AGENTS.md`。
- 未修改 `可重用expected規則.json`，亦未加入 repository 外備份中的「籃球／籃」或「體驗／驗」規則。
- 未降低 source validation、artifact integrity、cache compatibility、regression gate、reconciliation 或 completion gate。

## 12. Compatibility impact

Release-prep 只把 current metadata 從 `5.6.2` 同步為 `5.7.0`。既有 compatibility 仍由明確 schema、semantics epoch、PDF/evidence fingerprints 與 exact legacy adapters 決定；tool version drift 本身不使安全相容 evidence 失效，也不使不完整 payload 被接受。

保留的 schema baseline：

| Contract | Version |
|---|---:|
| `ASSET_MANIFEST_SCHEMA_VERSION` | `2.6.2` |
| `ACTUAL_FINGERPRINT_SCHEMA_VERSION` | `2.9.0` |
| `EXPECTED_FINGERPRINT_SCHEMA_VERSION` | `2.7.0` |
| `LEDGER_SCHEMA_VERSION` | `2.6.0` |
| `SESSION_SCHEMA_VERSION` | `2.6.0` |
| `WORKBOOK_SCHEMA_VERSION` | `2.6.1` |
| `REVIEW_ID_SCHEMA_VERSION` | `2.5.0` |
| `ACTUAL_REVIEW_SCHEMA_VERSION` | `1.0` |
| `MANUAL_ACTUAL_STAGING_SCHEMA_VERSION` | `1.0` |

## 13. Runtime asset integrity

- `runtime_asset_manifest.json` 只把 `tool_version` 從 `5.6.2` 改為 `5.7.0`。
- Manifest `schema_version`、20 個 asset 的 `path`／`sha256`／`chain`／row/schema constraints 全部未改。
- 沒有重新計算 SHA，也沒有修改任何 manifest-protected CSV/XLSX bytes。
- Production `validate_asset_manifest()` 實測：`ok=True`、`errors=[]`、`warnings=[]`、`required_asset_roster_ok=True`、actual／expected chain roster 均為 true、20/20 assets 通過。

## 14. Validation results

本次 validation 使用 Python 3.13.5，於 `release/v5.7.0-prep` working tree 實際執行：

| Validation | Result |
|---|---|
| release version consistency | 1 test，PASS |
| runtime asset manifest historical integrity | 4 tests，PASS；production report `ok=True`、0 errors、0 warnings |
| fingerprint compatibility contract | 11 passed |
| semantics epoch contract | repository-local basetemp：24 passed |
| cross-version compatibility | repository-local basetemp：5 passed |
| architecture | 42 passed |
| Phase 1 staging | 18 tests，PASS |
| Phase 2A batch apply | 15 tests，PASS |
| Phase 2B／3A GUI batch | 29 tests，PASS；visible-layout class 因本機 Tcl/Tk `init.tcl` 不可用而 skipped |
| GUI layout | 2 tests，PASS；visible-footer class 因相同 Tcl/Tk 缺件而 skipped |
| full unittest discovery | `Ran 217 tests`，`OK (skipped=2)`；兩個 class-level skips 均為上述 Tcl/Tk 環境限制 |
| full pytest（預設 TEMP） | 274 passed、4 skipped、10 setup errors、27 subtests passed；10 errors 全為 `%TEMP%\\pytest-of-E04069` ACL `WinError 5`，無 assertion failure |
| full pytest（全新 repository-local basetemp） | 284 passed、4 skipped、5 warnings、27 subtests passed；exit 0 |
| compileall | `python -m compileall -q .`，PASS |
| whitespace | `git diff --check`，PASS |

補充：full pytest 的 4 個 skips 是兩個 visible GUI classes 內的方法因本機 Python 安裝缺少可用 Tcl/Tk 而未執行；full unittest 將相同條件各記為一個 class-level skip。5 個 warnings 是既有 SWIG types 的 `DeprecationWarning`。預設 TEMP 的 10 errors 已用全新 repository-local basetemp 完整重跑並取得 exit 0；basetemp 隨後安全刪除。這些環境限制沒有被描述成 test PASS，也沒有透過 skip、expectedFailure 或 assertion weakening 修改測試。
