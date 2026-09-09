# zhuyin-proofreader v5.8 Master Development & Independent Review Plan

文件版本：1.2（保留既有檔名以維持引用）  
修訂日期：2026-09-09  
Repository：`discoveryray/zhuyin-proofreader`

本文件供使用者採納為專案長期規範；階段狀態與 SHA 另列於附錄，均不得當作即時 GitHub 狀態。本次修訂加入兩輪獨立審查、自動接續及使用者採納的持續授權；第 3～4 節的 actual / expected、identity、quorum、交易、legacy-v0、fingerprint 安全契約原文保留。文件修訂本身不代表程式碼已修正或重新通過審查。

## 1. 使用方式、角色與授權

專案指令保留精簡版核心規則，並指定本文件為完整規範。新的相關工作開始時，先讀本文件與該次任務的適用條款；同一任務內已讀取且未改版的內容無須反覆全文重讀。來源缺失、內容不完整或版本衝突時，明確指出缺口，不得假裝已讀取或據此給出無證據的 PASS。

本專案建立安全、可稽核、actual / expected 完全獨立的臺灣國小教材 PDF 注音校對系統。v5.8 的主要目標是 Global Exact Glyph Library，讓跨教材、跨專案的「實際字形 → 實際注音」只在嚴格 evidence contract 下重用，防止錯誤 learning 污染後續教材。

正式 reviewer 可以是 Work，或符合本文件相同獨立性、scope、證據與 verdict 契約的獨立審查代理；Work 不再是唯一正式 reviewer。角色分工如下：

- 協調者：鎖定任務範圍及 baseline，安排代理、核對證據、保存紀錄，執行已授權的下一步。
- 實作代理：修改受授權的程式、文件、設定及測試，執行驗證與新增 corrective commits。
- 第一輪 reviewer：獨立審查 task baseline → feature HEAD 的完整 cumulative diff。
- 第二輪 reviewer：另一個獨立 agent session，親自審查 PR 完整差異、base / head / merge-base、整合情境及適用 CI。

Reviewer 均不得參與本任務實作，或修改受審檔案；可以讀取固定 SHA 的隔離 checkout、執行測試及重現案例。修改文件或設定的協調者也屬實作者，不能自任正式 reviewer。兩輪提供乾淨的審查 context，不把實作摘要、前一輪 PASS 或 CI 綠燈當成審查結論；每輪均須親自核對原始差異、規範與必要呼叫路徑。

使用者明確交付的開發任務，預設授權該任務 repository、scope 與階段內的檔案修改、代理委派、測試、commit、push、建立／更新 PR，以及通過兩輪適用 PASS 與必要 CI 後，以 Create a merge commit 合併至 develop 並執行 post-merge 驗證。不必逐項重問或要求使用者轉貼。使用者可隨時收窄、暫停或撤銷；較窄的當次指示優先。純討論、唯讀審查及規劃不等於交付開發任務。

持續授權不延伸到其他任務、未交付階段、直接 push develop、修改 main、squash／rebase merge、force push、降低保護規則、tag／release 或 live DB mutation。Review PASS、流程規劃及交接紀錄本身不創造或擴大 write authorization。必要安全門檻不因持續授權而省略。

下一步確實超出有效授權時，先完成可安全執行的準備，交付具體結果並說明缺口；不得重新詢問已授權範圍。

## 2. 規範與事實的判定

- 以使用者當次明確指示、已採納的架構契約與適用的 repository 指令判定需求和範圍。
- 以實際 GitHub commit、parent、branch、PR、diff 與對應 SHA 的 CI 判定目前開發事實。
- Implementation summary、新增文件、既有 production code、既有 tests 都是待核對的證據，不能單獨證明契約正確。既有 code 和 tests 本身也可能有錯。
- 舊 SHA、測試數量、完成狀態、PR 狀態都是快照，不得永久寫死為當前事實。
- 新增文件不得自行改寫前階段安全契約。若規格互相矛盾，指出具體衝突、影響範圍與需要決定的方案；不得暗中弱化 validator 或擴大 scope。
- 事實無法查證時，標示「未核對」及原因，繼續完成不依賴該缺口的工作。

## 3. 不可破壞的核心原則

### 3.1 Actual / expected 獨立

actual 只能來自符合其既有安全契約的：

- PDF embedded font / glyph decoding。
- Direct visual confirmation。
- Occurrence-specific manual actual override。
- 已通過 exact-glyph evidence contract 的 reusable actual truth。

expected 只能來自 project rules、統一用字手冊、國語辭典簡編本、一字多音資料及 approved expected rules。

禁止用 actual 反推 expected、用 expected 修正 actual，或為了讓兩者一致而更改任何一方。字典、語境及 expected 結果不得參與 actual reading decision。actual 未解決不能成為省略 expected 判定的理由；修復其中一條資料路徑時，不得順便改變另一條路徑的語義。

Actual / expected fingerprint 與依賴也必須維持隔離，禁止透過 cache 或 import chain 偷渡另一方的答案。

### 3.2 Occurrence-specific direct actual 優先

有效且仍對應該 occurrence 的直接人工 actual 確認，優先於 reusable glyph truth。Reusable exact identity 發生 conflict 時，保留該 occurrence 的有效 direct override。

沿用、傳播或複製來的 reading，不得被重新標示為 fresh direct visual evidence。

### 3.3 Global fail closed

Corrupt DB、schema incompatibility、identity uncertainty、contradictory evidence、stale source、incomplete proof，不得被當作 trusted Global truth。

Absent DB、不可讀取的 DB、不相容 DB、損毀 DB 必須區分。唯讀查詢不得自動建立、修復、覆寫或重建正式 DB，也不得將錯誤偽裝成成功讀取空資料庫。

Global 無法安全使用時，其他獨立 evidence 是否可用，仍由其原有契約判定；不能把失敗的 Global 結果包裝成可信 fallback。

### 3.4 Conflict 與稽核

同一 exact identity 出現互斥且須保留的 reading，禁止 last-writer-wins。必須依安全契約 suppression／quarantine；成功保存 conflict 可以是有效交易結果，不能與 transaction failure 混為一談。

持久化的 Global conflict 必須保存所有相關 reading，清除 active reading、撤銷適用的 approval，並留下 revision / generation、provenance 與 receipt。不能因後續同讀音證據增加，自動解除 quarantine。

Global reusable truth 不只保存答案，還必須保留可驗證的 evidence、provenance、approval、conflict history、transaction receipt 與 revision / generation。不得偽造來源、確認時間、decision snapshot 或 receipt。

## 4. v5.8 六階段契約

### Phase 1 — Exact identity contract hardening

先定義何者才是完全相同且允許 Global reuse 的字形。

TTF 只允許 `GLOBAL_ELIGIBLE_SIMPLE_GLYF_V1`：合法、可見、完整的 simple glyf；`numberOfContours > 0`；非 composite、非 malformed、非 truncated。Identity 使用完整 raw glyf record 的 SHA-256。

CFF identity 使用 canonical `style_group` 加上 complete resolved glyph recording 的 SHA-256。相同 recording SHA、不同 style group，仍是不同 Global identity。

Font name、font index、annotation signature、normalized outline、partial component signature 均不能單獨作 exact identity 證明。Global eligibility 的限制，不代表可以無關地刪除既有 project-local 合法功能。

### Phase 2 — Global repository foundation

建立 strict SQLite repository。正式 production path：

```text
%LOCALAPPDATA%\DiscoveryRay\ZhuyinProofreader\GlobalExactGlyphLibrary\library.sqlite3
```

測試使用隔離的暫存路徑。不得因路徑解析失敗，偷偷改用 cwd 或其他未指定位置。

必要基礎包括 strict schema 與 row validation、WAL、`BEGIN IMMEDIATE`、bounded lock/retry、revision CAS、monotonic generation、`processed_intent`、idempotency、backup、diagnostics、conflict/quarantine、approval、provenance 與一致的 snapshot semantics。

此階段僅建立 repository foundation，不提前加入 decoder reuse、project promotion、legacy migration 或 automatic Global evidence writes。

### Phase 3 — Read-only exact reuse + per-PDF fingerprint

Exact evidence precedence：

1. 有效的 occurrence-specific manual actual override。
2. Union exact conflict gate。
3. 通過 conflict gate 的 project-local `VERIFIED_EXACT_GLYPH`。
4. 通過 conflict gate 的 Global `VERIFIED_GLOBAL`。
5. 通過 conflict gate 的 static TTF exact。
6. 符合自身契約的 independent decoder evidence。
7. Unresolved。

先檢查全部適用的 reusable exact sources，再決定優先序。Project / Global / static 對同一 identity disagreement 時，所有相關 reusable exact donors 都必須 suppression。有效 occurrence-specific direct override 仍保留。

唯讀 conflict detection 不得自行寫入正式 DB；持久化 quarantine 由既有安全寫入流程處理。Independent decoder 的結果不能自動解除已存在的 Global quarantine。

Global 只有 `VERIFIED_GLOBAL` 可以重用。`CANDIDATE`、`PROMOTION_READY`、migration candidate、legacy evidence、provenance 或 source_count 均不是可重用 truth。

Per-PDF Global dependency fingerprint 只包含該 PDF 實際依賴、且會影響其有效結果的 logical Global subset。不得 hash whole DB；Global generation 僅供 audit，不得當 cache key。

不影響有效 state / reading 的 audit、provenance、matching legacy import 等更新，不應使相關 logical subset digest 改變。與 conflict 相關的可用性改變，必須由既有 dependency contract 正確反映。

### Phase 4 — Transactional promotion + conflict writes

只有新產生且可驗證的 `DIRECT_VISUAL_ACTUAL` 可以 `counts_toward_global_quorum = 1`。其他 evidence 一律不得計入。

Global promotion 至少需要兩個同讀音、correlation-resistant independent sources。實際構成 quorum 的 sources 必須同時通過全部獨立性 axes：

- Project / session ID。
- PDF SHA-256。
- Embedded font-program SHA-256。
- Occurrence ID。
- Review ID。

不得只分別統計各欄位 distinct 數量來假裝存在合格的 independent source pair，也不得用重命名或偽造 ID 製造獨立性。舊 source_count 不等於 quorum。

Quorum 成立不等於已獲 approval。Promotion 必須經既有 explicit approval service；approval 綁定其適用的 identity、reading、evidence roster / digest、policy 與 revision。Stale approval 必須拒絕。

可靠交付流程：project-local actual transaction → durable outbox → Global idempotent delivery → processed_intent receipt → project ack → refresh。

Local actual 已提交、Global delivery 待完成、ack 待完成、refresh 待完成，必須能區分。Crash recovery 依 durable state 與已提交的 recovery plan 執行，不得重複計 evidence 或偽造已完成狀態。

新的 direct canonical reading 與 retained reading 矛盾時，必須 `QUARANTINED_CONFLICT`、`active_reading = NULL`、revoke approval、保留全部 readings，並更新適用的 revision / generation。禁止自動 resolution。

Conflict resolution 需要 explicit adjudication 加上 fresh quorum；這是安全門檻，不能據此宣稱目前已存在 adjudication API，或在未授權階段自行實作。

### Phase 5 — Auditable legacy candidate migration

#### 5.1 範圍與來源

唯一目標：將舊 project-local learning 匯入為 non-reusable migration candidate、insufficient audit candidate 或 durable conflict evidence。

正式 legacy reading donors 只有：

- `user_verified_glyf_fingerprints.csv`。
- `user_verified_cff_glyph_fingerprints.csv`。
- `glyph_truth_conflicts.csv` 的 retained readings。

舊 `USER_VERIFIED_SINGLE`、`VERIFIED_EXACT_GLYPH` 不得直接成為 `PROMOTION_READY`、`VERIFIED_GLOBAL` 或 approval。全部 legacy evidence 的 quorum contribution 永遠是 0，不因來源數量增加而改變。

Manual occurrence override、actual workbook decoder result、provenance、full_signature、font name/index、expected/dictionary/context 都不能單獨成為 Global legacy candidate donor。

#### 5.2 Explicit selection 與唯讀 project

明確選取 project，例如 `--project-output <absolute path>`，必要時明確指定 `--pdf-root <absolute path>`。禁止 broad filesystem scan、自動找其他 project、parent recursion、掃 Desktop 或 drive。

Migration 對舊 project 必須完全 read-only：不建立缺少的 CSV、不修 legacy override、不 refresh、不改 manifest、workbook 或 evidence。

不得直接使用會建立檔案或進行 historical override migration 的 validator。不得以 `standalone_proofread.materialize_ledger()` 建立 index，因其可能執行 expected-related logic。

應使用專用 actual-only occurrence index，依 sealed manifest、current actual workbook 與 occurrence / review / PDF / page / bbox / char / exact identity metadata 作 identity validation、source_examples mapping 及 reconfirmation target。此 index 不創造新的 authoritative truth。

Migration production module 不得透過直接或間接 import 執行 expected resolver、dictionary 或 reusable expected rules。Structural manifest validation 不等於 expected evidence，但 reading decision 必須 actual-only。

#### 5.3 Strict validation 與 exact identity 重證

驗證 exact headers、canonical lowercase SHA-256、canonical Bopomofo、CFF non-empty canonical style_group、enum、duplicate canonical row rejection。

`USER_VERIFIED_SINGLE` 的 source_count 必須等於 1；`VERIFIED_EXACT_GLYPH` 必須至少 2；source_count 必須等於 unique source_examples 數量。歷史計數只用於驗證歷史 row，不能轉成 Global quorum。

`QUARANTINED_CONFLICT` learning row 不得因 conflict registry 已有同 identity 而提前跳過適用的 validation。必須先核對 retained reading 與 registry 的對應關係，以及 source_examples 的結構、映射與 identity；不能遺失 registry 未包含的 reading，也不能隱藏 stale / contradictory mapping。

不能只信任舊 CSV 的 SHA。必須由所選 project 當前 PDF embedded font bytes 重新證明 TTF / CFF exact identity。

已明確證明 `NON_GLOBAL_ELIGIBLE` 的字形可以 skip，並提供 diagnostic。PDF missing、PDF SHA mismatch、stale identity、workbook identity 不符或 bytes 無法證明 identity，必須在 Global mutation 前阻止整批 APPLY，不能默默跳過後回報成功。

可靠映射不足與 identity 驗證失敗必須區分：只有 exact identity 已獨立完整重證、且沒有已知不一致時，才可能使用下述 `MIGRATION_INSUFFICIENT`。

#### 5.4 Schema 與 deterministic import identity

維持 `GLOBAL_LIBRARY_SCHEMA_VERSION = "1.0"` 與 `GLOBAL_LIBRARY_SQLITE_USER_VERSION = 1`。不增刪或修改 SQLite tables / columns，沿用既有 `migration_candidate` table。

另定 `GLOBAL_MIGRATION_IMPORT_CONTRACT_VERSION = "1.0"`；migration identity 的語義不得繼續綁在 SQLite schema version。

Legacy row key 使用 canonical JSON `{glyph_id, reading, old_verification_level}` 的 SHA-256。

新版 import ID 至少綁定 migration contract version、sealed project/session ID、manifest/source project SHA、source evidence CSV SHA、glyph identity、canonical reading、old verification level 與 legacy row key。

CSV row number、absolute path、timestamp、notes 不得作額外 identity 欄位。同 source bytes 加同 canonical row，產生同 import ID；path 改變不能改 ID；source CSV bytes 改變，產生新的 auditable import ID。舊 import 不得被覆寫。

#### 5.5 Storage、status 與雙向 conflict gate

Legacy 使用 `migration_candidate` 及必要的 `glyph_truth`、`glyph_conflict`、`provenance_event`、`processed_intent`。不得為填滿結構而偽造 `source_evidence`、全零 SHA、occurrence ID、review ID、font hash、decision snapshot 或 confirmed_at。

Status：

- `MIGRATION_CANDIDATE`：exact identity 完整重證，且至少一個舊 source_example 可可靠映射 current actual-only occurrence。
- `MIGRATION_INSUFFICIENT`：exact identity 已重證，但舊 source_examples 無法可靠映射；只能作 non-reusable historical candidate。
- `MIGRATION_CONFLICT`：explicit imported conflict，或 migration reading 已造成／參與 contradiction quarantine。

三種 status 全部不計 quorum。首次建立 glyph_truth 時只能是無 active reading 的 candidate；direct / independent counts 只能反映真正的 direct evidence。

Migration readings 參與 conflict safety gate，永不參與 quorum。以下順序都要成立：

- Legacy ㄅ → fresh direct ㄆ：quarantine。
- Fresh direct ㄆ → legacy ㄅ：quarantine。
- Legacy project A ㄅ → legacy project B ㄆ：quarantine。
- `VERIFIED_GLOBAL` ㄅ → legacy ㄆ：revoke approval、quarantine、active reading 清空。
- `VERIFIED_GLOBAL` ㄅ → matching legacy ㄅ：若其他有效條件不變，保留 verified 與 logical reusable result。
- 已 quarantine → 新 legacy / direct：不得自動恢復 reusable truth。

Explicit legacy conflict 必須保存所有 retained readings 與對應 imports、conflict provenance，適用時撤銷 approval。不得用 legacy imports 灌大 direct / independent counts。

#### 5.6 Legacy-v0 compatibility：可讀取與可重用分開判定

相容性例外僅限 frozen legacy-v0 `migration_candidate` 歷史格式與 non-reusable historical candidate 的舊狀態，不適用於 reusable truth safety contract。

**辨識與驗證：**

- 必須以 exact legacy-v0 algorithm 重算 import_id，不能用寬鬆 prefix 或任意舊格式判定。
- 必須檢查完整 frozen v0 contract，包括當時適用的 schema、欄位、canonical values、identity、關聯完整性與 evidence / approval 安全條件。只核對 ID 與 SQLite structural contract 不足夠。
- 舊 validator 回傳 VALID 是相容性證據之一，不足以單獨證明歷史狀態安全；不得把舊 validator 漏驗的不安全資料合法化。
- 對符合上述契約的 non-reusable v0 candidates，不得僅因同 glyph 存在不同 candidate readings，就以 Phase 5 新增的 candidate-conflict invariant 將整個 DB 判為 corrupt。

**Pure read / snapshot：**

- 合法的歷史候選資料必須保持 strict-readable。Strict-readable 不等於 reusable。
- 讀取不得 mutation、rewrite 舊 rows、改 ID、建立 receipt，或為修正歷史狀態而寫入 quarantine。
- 所有適用的 retained v0 readings 必須參與讀取時的 conflict safety 判定。若已存在互斥候選讀音，或 v0 reading 與 active / direct reading 矛盾，不得讓相關 Global identity 繼續被 decoder trusted reuse。
- 不能把安全檢查延後到下次寫入。即使 DB 儲存的 state 仍是 `VERIFIED_GLOBAL`，也不能僅依該欄位放行。
- 應透過不改正式資料的 snapshot / read-side safety gate，阻止不安全 identity 重用並提供 diagnostic；不得偽稱已完成 durable quarantine 或 approval revocation。
- 有效的 occurrence-specific direct override 仍依原有優先序保留。Read-side safety 的實作須接入既有 conflict / dependency contract，不得另造繞過 cache 或 decoder 的第二條信任路徑。

**新的 mutation / approval：**

- 任何新的 Phase 4 direct evidence、Phase 5 migration mutation，以及 explicit promotion approval，只要處理同一 glyph，都必須納入所有適用、尚未被有效裁決排除的 retained v0 readings。
- 若 union readings 矛盾，必須經既有交易流程 durable quarantine、清除 active reading、撤銷適用 approval 並保存證據；不得授予新 approval。
- 若原有 v0 candidates 互相矛盾，新 reading 即使吻合其中一個，仍不能忽略另一個。
- Matching v0 evidence 不得貢獻 quorum、觸發 promotion 或產生 approval；單純新增同讀音 audit 也不應無故破壞原本安全的 verified result。

**相容性不得接受：**

- Contradictory `DIRECT_VISUAL_ACTUAL` 卻未依既有契約 quarantine。
- `VERIFIED_GLOBAL` 與 retained direct evidence 矛盾。
- Invalid / stale approval、corrupt schema、fabricated source evidence。
- 其他原本就違反 reusable truth safety contract 的狀態。

Production 新寫入只能使用新版 migration contract ID。不 rewrite 舊 migration rows、不批次修改 live DB、不偽造 receipts。若無法同時維持合法 v0 可讀與 Global reuse 安全，必須指出具體設計缺口，不得藉相容性暗中弱化安全契約。

#### 5.7 Dry-run、APPLY 與原子性

CLI 預設 dry-run；真正 mutation 必須 explicit `--apply`。Dry-run 可 read-only inspect Global DB，不得初始化 absent DB，不寫 Global DB，也不寫 project。

Empty migration APPLY 也必須 strict read / validate 已存在的 Global DB。Absent DB 維持 absent；corrupt、incompatible 或不可讀取時明確失敗，不能直接回報 APPLIED 掩蓋錯誤。

Non-empty APPLY：完整驗證 project → 建立 canonical plan → canonicalize 全部 intents → 在 first Global mutation 前完成可預先判定的 validation → 一個 `BEGIN IMMEDIATE` transaction → 在 transaction 內核對當前狀態與全部 replay receipts → mutation → receipts → commit。

任何失敗都要 rollback 整批，禁止 partial import。每個 effective batch 最多 bump generation 一次。

同 intent_id / 同 payload：idempotent no-op 或 existing receipt。相同 ID / 不同 payload：hard error。No-op 不得重複 provenance 或 bump generation。

有效 import 有 `MIGRATION_CANDIDATE_IMPORTED` provenance；conflict transition 有對應 provenance。`evidence_id` / `approval_id` 可依契約為 NULL，不得為連結完整而偽造 evidence。

#### 5.8 Reconfirmation 與 runtime boundary

Phase 5 只產生 deterministic reconfirmation targets，包含可取得且可驗證的 import、glyph、project/session、occurrence、review、PDF、physical page、bbox / char 與 legacy status。缺少的歷史欄位不得編造。

Legacy reading 可以是 audit metadata，但不得自動 stage、approve 或建立 `DIRECT_VISUAL_ACTUAL`。真正重新確認後，仍走 Phase 4 direct visual flow。

Phase 5 不改 actual / expected fingerprint schema、Global dependency contract、decoder pronunciation precedence、semantics epochs、VERSION、runtime assets 或 runtime asset manifest。Global DB 不得加入 runtime asset manifest。

Same-reading legacy import 若未改變有效可用性，相關 per-PDF logical subset digest 必須穩定；contradiction 導致的有效結果變更，須由既有 contract 正確使相關依賴失效。

### Phase 6 — GUI observability + metrics

呈現 Phase 1～5 backend contracts：Global state、candidate / ready / verified / quarantine、evidence / source、promotion approval、conflict、migration diagnostics、delivery / recovery status、metrics 與 operator-facing diagnostics。

GUI 只能呼叫既有安全 service / API，不能另造 approval、quorum、identity 或 conflict 判斷邏輯。不得藉 GUI 重新定義 Phase 1～5 truth contract，或偷偷加入尚未核准的 adjudication / automatic recovery 功能。

## 5. 階段依賴與 Git 流程

每個 major objective 使用獨立 feature branch；既有任務分支不能挪用。Codex 執行期間由協調者自動接續：

1. 讀取規範及實際 Codex 設定，fetch，確認 working tree、最新 develop、task baseline、範圍與有效授權。既有未知修改先停止；新 branch 從已核對的基礎建立。
2. 委派實作代理完成範圍內變更、必要測試及 commit。協調者本身有修改時也列入實作者名單。
3. 第一輪 reviewer 對固定 baseline → feature HEAD 進行完整 cumulative review，回傳 exact SHA、scope、verdict 及原始證據。`READY FOR REVIEW` 是中間交接狀態，不是要求使用者接力的停點。
4. 第一輪 PASS 後，先查詢同 repository / base / compare 的 PR；無 PR 才建立，已有唯一 PR 就更新。已合併則驗證原 merge；closed-unmerged 或多個不明匹配先停止，不另建重複 PR。
5. 等待必要 PR CI，第二輪 reviewer 獨立核對 PR 全部差異、整合情境及 CI。CI event、run attempt、tested SHA、全部必要 jobs / steps 均須匹配；synthetic merge 必須核對 base / head parents 及檔案樹。
6. 任一輪 BLOCKED：協調者直接交回實作代理新增 corrective commit，重跑必要測試，重新取得兩輪對最新版本的完整 PASS。原 review 可保留作歷史證據，不可套用新 HEAD。每個 task 最多三輪自動 corrective implementation；第三輪後仍有 blocker 或須再修正時，停止並回報原因及下一步。計數跨重啟保存，不能藉另開 session 重設。
7. 代理不可用、report 缺失／不完整、CI pending／failed／cancelled／必要 job 被 skip 時，不得合併。可自動補證據或調查故障，但不能偽裝 PASS 或改驗證規則。
8. 兩輪適用 PASS、必要 CI 全通過且授權有效後，立即重讀 PR、base、head、findings、GitHub 保護規則及最新 CI。以 merge API 的 expected head SHA 綁定 reviewed HEAD，只使用 Create a merge commit；不用 squash、rebase、auto-merge，不降低保護規則。不代 reviewer approve、不自行 resolve discussions 或刪除 branch。
9. 不確定 API 是否成功時先讀遠端狀態，不盲目重送副作用。核對實際 merge SHA、兩個 parents、預期檔案樹；不能假設它等於 synthetic SHA。Fetch 並核對 develop，再驗證實際 merge SHA 觸發的 develop push CI（Windows Python 3.12／3.13 及當次必要 gates），不得拿 PR CI 代替。
10. 所有門檻均通過才回報該任務 COMPLETE。Develop 後續前進時分開報告已驗證 merge SHA 與目前 HEAD。Phase N 全部完成也不授權自行開始未交付的下一 Phase。

Phase N 只有 implementation、兩輪 independent review、必要 PR CI、merge commit、develop post-merge CI 全部完成，才算通過階段門檻。此流程建置任務不等於 Phase 6，也不重新宣告歷史 Phase 狀態。

若 HEAD 或 PR base 改變，停止合併，保留 task baseline，重新取得兩輪對新組合的適用 PASS 與整合 CI。不得自行 rebase、改 baseline 或將 develop 合併進 feature；若需要未授權的 scope 變更，具體回報。GitHub head CAS 無法原子綁定 base，因此需緊接 merge 前再次核對 base，合併後核對實際 parents；若競態仍發生，停止完成宣告並補 integration review／驗證，不自行 revert 或 force push。

詳細執行方式見 [兩輪審查執行手冊](PR_REVIEW_AUTOMATION.md)；本機 gate 見 [gate 契約](PR_REVIEW_GATE.md)。Gate 是已查證證據的結構與轉移檢查，不是 GitHub 保護規則或獨立審查的替代品。

## 6. Review scope、SHA 與 HEAD 變動

首次 Phase review：指定 Phase baseline → reviewed feature HEAD。

Corrective commit 後：仍審 Phase baseline → latest authorized feature HEAD 的完整 cumulative diff，不能只審上一個 HEAD → corrective commit。可沿用未受影響且仍有效的證據，但必須重新檢查修正對整體契約的影響。

PR review：核對當前 PR base、head、merge-base 與 GitHub 實際顯示的 cumulative diff。明確記錄比較方式；develop 已前進時，不得把單純兩棵 tree 的差異錯當 PR diff。

### 審查期間 feature HEAD 改變

- 審查一開始就記錄 baseline、reviewed SHA、parents 與當時 branch HEAD。
- 使用者指定固定 SHA：完成該 SHA 的審查，並回報 branch 已前進。不得自行把 scope 改成新 SHA。
- 使用者要求 latest HEAD：重新取得最新 SHA，更新 baseline → 最新 SHA cumulative review，並補做受影響的關鍵檢查。
- 舊完整 review 對舊 SHA 仍有效；不能套用到新 SHA。舊審查若尚未完成，其既有結果只屬 partial evidence。
- Tests / CI 必須對應聲稱已驗證的版本。不得把舊 SHA 的通過結果當成新 SHA 已通過。
- 結論前再核對 target HEAD。若已再次變動，明確列出實際完成審查的 SHA 與未審的新 HEAD；不得宣稱未審的 latest HEAD PASS。

`REVIEW: PASS` 必須綁定 exact reviewed SHA、baseline 與 scope；不等於整個 branch 永久通過，也不等於 Phase 已完成。

PR base / integration context 改變時，評估並重做受影響的整合檢查。不能只因舊 SHA code review 仍有效，就跳過新版 PR / merge context 的必要驗證。

## 7. Independent review 與測試標準

必須實際核對 GitHub metadata、parents、cumulative diff、全部 changed production code 與 tests，並交叉追蹤適用的前階段架構和呼叫路徑。

至少檢查 failure paths、state transition、transaction atomicity、idempotency、concurrency、backward compatibility、runtime boundary、import side effects、來源驗證與 actual / expected 隔離。

檢查 tests 是否用獨立 expected value / invariant，避免只照 production code 重算同一錯誤。Test 數量與綠燈不能單獨證明 correctness。B1 / B2 / B3 這類會改變安全狀態的修正，必須有可重現失敗情境及有效 regression coverage。

依當次要求執行 targeted tests、必要的 full unittest / pytest、runtime integrity、compile 與 diff check；已充分驗證後不任意擴張測試。數量以當次 discovery 和輸出為準，不沿用歷史數字。

明確區分本次實際執行、已閱讀 repository tests、已核對 GitHub CI、實作者回報，以及未驗證事項。

Linux 執行結果不得宣稱為 Windows 已驗證。Windows-only GUI、locking、multiprocessing 等，以實際平台證據或符合要求的 CI 核對。缺少資產、權限或工具不等於已證明程式有 bug；但關鍵安全門檻缺乏證據時，不能給無條件 PASS。

CI 必須核對 workflow、required jobs、run attempt、event、tested SHA 與結論。若測試 synthetic merge commit，核對其對應 base / head；不能把舊 run、其他 branch、skip、cancel、pending 或缺少 run 當 PASS。

Feature 未觸發 CI 時，先辨識 workflow trigger，不臆測為測試失敗；PR / post-merge 必要 gates 仍需實際完成。

Post-merge 必須核對 merge commit 與 develop push CI 的對應版本。Develop 後續又前進時，分開報告已驗證的 merge SHA 與尚未驗證的新 HEAD。

## 8. Verdict、紀錄與自動交接

正式 code review verdict 只能是：`REVIEW: PASS` 或 `REVIEW: BLOCKED`，並列出 reviewed SHA / baseline / scope。

PASS：沒有 blocker / high correctness issue，且當次必需的安全驗證門檻已具備證據。不得將 PASS 描述成新 write authorization 或整個 Phase 已完成。

BLOCKED：分清已證實的程式缺陷、需決定的契約衝突，以及尚未完成的必要驗證。一般命名、可讀性或個人重構偏好不得冒充 blocker。

每個 blocker 必須包含 exact file / function / code region、具體 failure scenario、violated contract、現有 tests 為何未抓到、minimum fix 及 regression test requirement。

報告先說 verdict 與最重要影響，再提供足夠 Git / architecture / tests 證據。採繁體中文，技術名詞與識別字保留原文。使用者指定格式時依指定格式。

**每輪 reviewer 必須提供自足的下一步交接紀錄，由協調者直接接續，不要求使用者逐次轉貼。只有能力、授權或三輪修正上限形成實際阻礙時，才交回使用者。**

- BLOCKED：Corrective Implementation Prompt，列出全部有效 blockers、最小修正、安全與 scope 限制、必要 regression、同 branch 新 corrective commit，以及交回 cumulative review 的條件。
- PASS：依實際已達階段提供下一個 workflow action，例如準備 PR、核對 PR / CI、執行已授權 merge，或核對 post-merge CI。不能跳過尚未完成的門檻。
- 驗證受阻：提供具體補證據 / 重現指令，不強迫實作者改動尚未證實有錯的 production code。

交接 prompt 必須自足：repository、phase、branch、baseline / reviewed SHA、任務、authorization scope、禁止事項、必要測試、停止條件與交回資料。尚未獲授權的 write 必須清楚標為待授權，不能在 prompt 中偽造已授權。

當新 commit 已存在時，先依當次 scope 核對其狀態，不得盲目要求重做舊修正。對規範文字的討論不冒充正式 code review，也不捏造 reviewed SHA。

### 證據保存與恢復

每個 task 保存唯一 task ID、原始需求／授權、固定 baseline、目前 base/head、全部實作者與 reviewer session IDs、每輪 scope / verdict / findings / 原始 report、測試命令與結果／限制、CI URL / run ID / attempt / event / tested SHA / parents / jobs / steps、corrective round 計數、PR URL、merge API 結果、實際 parents / tree / develop HEAD、post-merge CI 及下一步。

紀錄置於明確 task evidence 目錄；兩輪完整 report 與 CI、merge 證據摘要同步保存至既有 PR description 的 evidence 區，保留舊 BLOCKED 紀錄供稽核。可更新 evidence metadata，不可偷偷更換需求、scope、findings 或 SHA。不得為了把目前 SHA 的 PASS 放進受審 commit，再新增 commit 造成自我引用循環；受審程式碼、測試、設定或規範改動均須重新審查。

中斷恢復時先讀原紀錄及即時遠端，重建狀態再決定下一步。遺失 review 不能自行補寫 PASS；遺失 correction history 不能重設上限。代理／工具缺失時如實列出，禁止以實作者自審冒充獨立 review。此任務僅配置 Codex 執行期間協調，不新增背景 GitHub Actions AI、額外 AI API、API key、計費設定或排程。

## 附錄：歷史進度快照，執行前須重新核對

下列資料來自本次對話提供的 Phase 5 狀態，僅供接續工作定位；本文件修訂不代表重新查證 GitHub。

- Phase 1～4：歷史記錄為 COMPLETE；不得把此記錄當作新的逐階段驗證。
- Phase 5：IN REVIEW / CORRECTIVE DEVELOPMENT。
- Phase 6：NOT STARTED。
- Phase 5 branch：`feat/v58-legacy-candidate-migration`。
- Phase baseline：`d2d558808bd2902857fad30f824d8bdc352beb73`。
- 首版 implementation：`7b59a7aa1a0e163f99f2bac5bca083bb5ad31bc6`。
- 首版審查：BLOCKED；當時記錄為尚未建立 Phase 5 PR。

待確認修正議題：

- B1：quarantined legacy learning row 提前跳過適用的 validation，可能漏掉 retained reading 或無效 source mapping。
- B2：legacy-v0 相容性需要依本版 5.6 節判定；不能只用舊 validator VALID 證明安全，也不能為保留歷史 candidates 而放行 contradictory reusable truth。
- B3：empty migration APPLY 不能跳過 existing Global DB strict validation。

下一次實際審查必須核對最新 task scope 與 GitHub 狀態，不能假設上述 commit 仍是 branch HEAD，或原 blockers 尚未被修正。
