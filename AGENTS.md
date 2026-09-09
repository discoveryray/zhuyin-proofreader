# zhuyin-proofreader Codex 專案規範

## 0. 完整規範入口與任務角色

v5.8 完整開發／獨立審查規範：
[V58_MASTER_DEVELOPMENT_REVIEW_PLAN_v1.1.md](docs/V58_MASTER_DEVELOPMENT_REVIEW_PLAN_v1.1.md)。

這是使用者採納的「zhuyin-proofreader_v5.8_完整規範_v1.1.md」之 repository 副本。
新的相關任務開始時，先讀此完整文件與當次適用條款；同一任務已讀且未改版的內容不必反覆全文重讀。
完整文件不會因為被連結就等同已讀取，必須實際開啟。若文件缺失或讀取不完整，明確回報缺口，完成不依賴缺口的工作，不得假裝已讀或給無證據的 PASS。

在 CLI / IDE 使用時，完整文件所稱「專案來源／Sources」由上述 repository 路徑提供，不要求存取另一個 ChatGPT 專案。
文件附錄的階段狀態、SHA、PR 與測試數量都是歷史快照；執行前以當次指定範圍及實際 GitHub 狀態核對。

依當次任務決定角色：implementation agent 負責實作與自我驗證，完成後交付 `READY FOR REVIEW`；independent reviewer 負責獨立審查，不能自行修改受審程式。使用者本次明確要求的文件修改可以實作，但實作者不得同時充當該修改的唯一獨立審查者。

使用者最新明確指示優先於專案文件；本文件與完整規範共同適用。若有實質契約衝突，指出衝突與影響，不得暗中弱化安全邊界。

---

## 1. 專案目的

本 repository 是臺灣國小教材 PDF 注音校對工具。

核心優先順序：

1. 正確性
2. actual / expected 證據鏈獨立
3. 可追溯性與 artifact integrity
4. fail-closed
5. regression safety
6. 最後才是便利性與效能

不得為了讓測試通過、減少重算或加快開發而降低上述安全邊界。

---

## 2. Git / Branch 工作方式

- `main` 是正式穩定／release 分支。
- `develop` 是整合分支。
- 所有 production 修改原則上從最新 `develop` 建立 task branch。
- 一個 task branch 只處理一個主要 objective。
- 發現 unrelated issue 時，只記錄，不得順手一起修。
- 不得直接修改或 push 到 `main`。
- 除非使用者明確要求，不得直接修改或 push 到 `develop`。
- 不得自行 merge task branch。
- 不得自行建立 tag 或 release。
- 不得自行 force push。
- 已經提交給外部審查的 commit，不得自行 amend 或改寫歷史；修正應新增 commit，除非使用者明確要求其他方式。

若任務指示與目前 branch/base 不一致，先停止並回報。

---

## 3. Actual / Expected 永久分離原則

`actual` 與 `expected` 必須完全獨立。

### Actual 可以來自

- PDF 本身的注音字型／字形解碼
- TTF / CFF / glyph 結構證據
- 已驗證的 project/user actual evidence
- occurrence-local manual actual override
- 專案允許的 exact reusable glyph truth

### Expected 可以來自

- 專案注音規則
- 《統一用字手冊》
- 《國語辭典簡編本》
- 一字多音／詞條資料
- approved expected assets
- regression rules
- 其他明確授權的 expected evidence

### 永久禁止

- 用 actual 反推 expected
- 用 expected 修改 actual
- 為了讓 actual == expected 而修改任一證據鏈
- 將 actual evidence 混入 expected fingerprint
- 將 expected evidence 混入 actual fingerprint

PDF text layer、OCR、font name、font index、語義或詞性若只是定位／候選證據，不得越權取代正式證據鏈。

### 永久 independence invariants

- 即使 actual decoder unresolved、失敗或沒有可用 actual，若 expected evidence 本身完整，expected resolver 仍必須獨立執行。
- 不得以「actual 尚未解析」作為跳過 expected resolution 的理由。
- 重建、refresh 或修復 actual artifact 時，不得刪除、覆寫或反推出已有效的 expected evidence。
- 重建、refresh 或修復 expected artifact 時，不得修改 actual evidence。
- actual / expected 的比較、reconciliation 或候選判斷只能建立在兩條證據鏈各自獨立產生的結果上。

---

## 4. Fail-closed 原則

資訊不足、contract 不完整、來源無法驗證或 compatibility 無法證明時：

- unresolved
- incompatible
- blocked
- 或明確要求人工確認

不得猜測成功。

不得建立「未知情況預設相容」的 fallback。

Legacy compatibility 必須：

- 明確命名
- 有有限 allowlist / adapter
- 有 regression tests
- 能證明對應的歷史 contract

缺少必要欄位或未知 future contract 預設 fail closed。

---

## 5. Fingerprint / Cache Compatibility

Fingerprint compatibility 必須使用明確 contract。

Producer 與 comparator 應共享同一份 required contract registry，不得各自維護容易漂移的平行規則。

目前語意相容邊界包括：

- `actual_decoder_semantics_epoch`
- `expected_resolver_semantics_epoch`

Semantics epoch：

- 是「結果可能因演算法語意改變」的 compatibility boundary。
- 不是 tool version。
- 不是 source hash。
- 不是 release counter。
- 不得每次版本發布自動 bump。

只有可能使相同 evidence 輸出不同 pronunciation result 的語意變更，才應 bump 對應 epoch。

Tool version 與 implementation source hash 可作 audit metadata；不得用它們冒充 semantic compatibility boundary。

Actual epoch 與 expected epoch 必須保持獨立。

---

## 6. Runtime Assets / Manifest

Runtime asset integrity 是正式安全邊界。

不得因為 validation 失敗就直接：

- 批次重算 SHA
- 更新 `runtime_asset_manifest.json`
- 用目前檔案內容覆蓋 manifest truth

「檔案現在存在」不代表它就是正確版本。

遇到 SHA mismatch 必須先判斷：

- asset 是正確的新版本、manifest 未同步
- manifest 正確、asset 被污染／改動
- 或需要更多證據

不得為了取得 PASS 而修改 manifest 或 asset。

避免會無意改變 bytes 的操作，例如未經確認的 CRLF/LF 轉換。

---

## 7. Dynamic Actual Evidence

Project/user-maintained actual evidence 與 immutable runtime assets 必須區分。

Dynamic actual evidence 的 hash 必須維持適當 scope。

若目前架構使用 per-PDF exact dependency，不得退回不必要的全域粗粒度 invalidation，除非任務明確要求並有設計與測試證明。

Exact glyph truth、occurrence override、conflict 與 provenance 的安全邊界不得因效能需求而放寬。

未知或 conflict 狀態不得被提升為 reusable truth。

---

## 8. Identity / Review

除非任務明確就是 identity migration，否則不得修改：

- occurrence identity
- review identity
- occurrence_id schema
- review_id schema

不得為了修 cache、repair、import 或 regression 問題順手改 identity。

若需要 migration，必須另立 objective、adapter、tests 與 compatibility plan。

---

## 9. Repair / Regression / Completion Gates

不得為了使流程完成而：

- 弱化 repair validation
- 跳過 artifact integrity
- 放寬 regression applicability
- 跳過 mandatory regression
- 偽造 completion
- 將未執行測試寫成 PASS

Repair、regression、reconciliation、completion gate 若失敗，必須調查原因，不得直接繞過。

歷史 regression 修正屬於永久 regression constraint，不應被當成可隨意移除的舊程式碼。

---

## 10. 修改範圍

每次任務只處理使用者指定的主要 objective。

開始修改前：

- `git fetch origin`，取得最新遠端 refs
- 確認 task branch 的 base 是否仍符合任務指定的 `develop` / commit
- 確認 branch
- 確認 base / HEAD
- 確認 working tree
- 讀取與 objective 直接相關的 production code 與 tests

注意：

fetch 只更新遠端 refs，不代表可以自行 merge、rebase 或 pull unrelated changes。
若 base 已改變且任務要求精確 base，停止並回報。

若發現完成 objective 必須跨越使用者明確禁止的 subsystem：

停止並回報。

不要自行擴大 scope。

不要做「順便重構」。

---

## 11. 測試原則

Production 修改至少應依風險執行：

1. objective-specific tests
2. 受影響 subsystem regression tests
3. cross-version / architecture tests（若適用）
4. integration tests（若適用）
5. runtime asset validation（若涉及正式 runtime）
6. full unittest / pytest-style suite（正式 merge 前依專案現況執行）
7. `compileall`
8. `git diff --check`
9. `git status`

不要把固定 test count 寫成永久成功標準；測試數量會隨 repository 成長。

真正標準是：

- 所有應執行的現有 tests 通過
- 沒有未說明的 failure / error
- skipped 有合理原因
- 未執行項目明確揭露

如果缺少大型 asset、真實教材、fixture 或環境依賴，不得捏造 PASS。

應明確寫：

- 未執行
- 原因
- 替代驗證
- 是否因此構成 merge blocker

---

## 12. Stop Conditions

遇到以下任一情況，停止並回報，不得自行繼續擴張：

- 任務需要直接修改 `main`
- 任務需要修改未授權的 `develop`
- 需要 bump schema，但 objective 未授權
- 需要修改 unrelated subsystem
- 需要批次重算 runtime SHA 才能通過
- actual / expected separation 會被破壞
- artifact integrity 無法證明
- regression failure 原因未釐清
- compatibility 只能靠猜測
- 必要 fixture 不存在但任務要求真實驗收
- working tree 有來源不明的既有修改
- branch/base 與任務不一致

---

## 13. 跨 Codex 帳號交接

兩個 Codex 帳號若可能交替或平行使用，應使用：

- 不同的 local clone；或
- 明確分離的 Git worktree

不得讓兩個 Codex 帳號同時操作同一個 local working directory。

即使兩者處理不同 branch，也不得共用同一 working directory 同時工作，
因為 checkout、未提交修改、index 與 working tree 狀態會互相影響。

同一 task branch 仍不得由兩個 Codex 帳號同時修改。

如果由帳號 A 交接給帳號 B：

A 必須先停止修改並 push 可交接 commit；
B 必須 fetch 並確認指定 branch / HEAD 後才能繼續。

帳號切換前應盡量完成：

- 儲存修改
- 執行已能執行的測試
- commit
- push
- working tree clean

交接資訊至少包含：

- repository
- branch
- base commit
- latest commit SHA
- 已完成內容
- 尚未完成內容
- tests 已執行結果
- tests 未執行項目
- blocker
- working tree 狀態

接手帳號必須先 fetch，再確認 branch 與 HEAD SHA，才能繼續修改。

不得依前一個聊天帳號的記憶猜測 repository 狀態。

Git repository / commit / tests 才是交接依據。

---

## 14. 完成回報格式

完成 production task 時，至少回報：

- Branch
- Base commit
- Latest commit SHA
- 修改檔案完整清單
- 實作摘要
- Compatibility / architecture 影響
- Tests executed
- Tests not executed
- Runtime validation
- `compileall`
- `git diff --check`
- Working tree
- Blockers
- 明確列出未做事項

### 完成狀態

Codex 完成 implementation 與所有可執行驗證後，
若 blocker = 無，只能回報：

`READY FOR REVIEW`

Codex 不得僅依自己的 implementation、tests 或自我 code review
自行宣告：

`READY TO MERGE`

`READY TO MERGE` 必須由獨立於本次 implementation 的審查程序確認，
例如：

- 使用者指定的獨立 reviewer
- 另一個獨立 ChatGPT / Codex review workflow
- 明確要求的 GitHub diff / PR review

只有獨立審查明確判定通過後，才能進入 merge。

實作者不得同時充當唯一 merge approver。

---

## 15. Codex 回覆方式

- 回報以繁體中文為主。
- 回覆精簡，先說結果與影響，再給必要證據。
- 內容若需要程式、Excel 技術結構或自動化邏輯知識才能理解，先提供不懂代碼的文編也能看懂的白話說明，再提供技術內容。
- 程式名稱、branch、commit、function、field、status 等保留原始英文。
- 不要用模糊的「應該沒問題」「看起來通過」代替實際驗證結果。
- 不得把沒有執行的測試描述為已通過。
- 優先提供具體檔案、function、commit SHA 與測試證據。

---

## 16. 任務指示與本文件

任務 prompt 可以對本文件增加更嚴格的 scope、驗證或禁止事項。

若任務要求明確變更本文件所保護的架構本身，例如：

- identity migration
- fingerprint schema migration
- semantics epoch bump
- repair architecture redesign
- regression applicability redesign

應把它視為獨立 architecture objective，先確認設計、migration、compatibility 與 regression plan。

不得把一般 bug fix 當成理由默默突破永久安全邊界。

---

## 17. v5.8 必要安全與審查補充

本節保留完整規範中最容易誤用的門檻；未列出細節不表示解除完整規範。

### Global actual truth

- 有效的 occurrence-specific direct actual 優先於 reusable truth；reusable conflict 不抹除該直接確認，傳播 reading 不得冒充 fresh direct evidence。
- TTF Global exact identity 只接受合法、完整、可見且 `numberOfContours > 0` 的 simple raw glyf SHA-256，排除 composite / malformed / truncated。CFF 使用 canonical style_group 加 complete resolved glyph recording SHA-256。
- 先通過 union exact conflict gate，再套用 project / Global / static reusable precedence。同 identity disagreement suppression 全部相關 reusable exact donors。
- 只有 `VERIFIED_GLOBAL` 可以 Global reuse；candidate、legacy、source_count 或 provenance 都不能代替 trusted truth。
- 只有 fresh `DIRECT_VISUAL_ACTUAL` 能計 Global quorum；實際合格 sources 必須同時通過 project/session、PDF SHA、font-program SHA、occurrence、review 的獨立性驗證。Legacy 永不計 quorum，也不自動 promotion / approval。
- Conflict 禁止 last-writer-wins；durable quarantine 清除 active reading、撤銷適用 approval、保留所有相關 evidence / readings 及稽核歷史，不自動解除。
- 區分 absent、corrupt、incompatible 與 unreadable DB；唯讀不得初始化、修復或改寫正式 DB。Empty migration APPLY 仍需 strict validate existing Global DB。
- Per-PDF fingerprint 僅涵蓋實際依賴的 logical Global subset；generation 僅供 audit，不作 cache key。Matching legacy 若不改有效結果，不應改變 logical digest。

### Legacy-v0 可讀取與可重用

- 相容性只適用 frozen v0 non-reusable migration candidates。Exact 舊演算法 ID 與完整 frozen v0 contract 都要驗證；僅有 structural schema 或舊 validator VALID 不足。
- 合法歷史 candidates 不得僅因互斥候選讀音，就被新增 invariant 判定整個 DB corrupt；strict-readable 不等於 reusable。
- Pure read / snapshot 不 mutation、不 rewrite、不改 ID、不偽造 receipt 或 durable quarantine。所有適用 retained v0 readings 必須立即參與 read-side conflict gate，有矛盾就不能 trusted Global reuse，不能等下一次寫入才檢查。
- 新 direct evidence、migration mutation 或 explicit approval 處理同 glyph 時，納入所有適用 retained v0 readings。有 contradiction 就經安全交易流程 quarantine / revoke，不授予新 approval；只吻合互斥候選之一仍是 conflict。
- 不得藉相容性接受 contradictory direct evidence 未 quarantine、verified 與 retained direct 矛盾、invalid approval、corrupt schema 或 fabricated source evidence。不批次改寫 live DB。
- Phase 5 對所選 project 完全 read-only；禁止 broad scan、expected/dictionary inference 或有寫入副作用的 validator。Quarantined learning row 仍須完成適用 reading、mapping 與 identity validation。

### 授權與階段流程

- 既有授權只在原 operation、repository、branch、scope 與階段內持續有效；同一授權範圍不重複確認。原授權明確涵蓋多項操作或階段時，依其原範圍執行。
- 不把一次 implementation / push / PR / merge / tag / release 授權擴張為其他操作或後續階段。Review PASS、流程規劃與產生 prompt 本身不構成新的 write authorization。
- 每個 major objective 獨立 branch；corrective commit 不改寫已審歷史。禁止 squash / rebase merge。
- Phase N 必須完成 implementation、independent review PASS、PR cumulative review PASS、必要 CI PASS、merge commit 與 develop post-merge CI PASS，才正式開始 Phase N+1。
- Phase 6 GUI 只使用既有安全 service/API，不另造 truth、approval、quorum 或 conflict 邏輯。

### 審查範圍與 HEAD 變動

- 審查記錄 repository、baseline、reviewed SHA、parents、branch HEAD 與 cumulative scope。Code、tests、新文件及 implementation summary 都是待核對的證據。
- Corrective 複審仍審 Phase baseline → 最新受審 HEAD 的完整 cumulative diff，不能只審 corrective diff。
- 固定 SHA 任務：完成指定版本的審查，branch / develop 前進如實回報，不擅改 scope。就此類唯讀審查，第 2、10、12 節的 branch/base 停止條件適用於受審對象與指定 scope 不符；不因 develop 單純前進就中止對指定 baseline / SHA 的既定審查。
- Latest HEAD 任務：更新 cumulative review 並補受影響檢查。舊完整 review 對舊 SHA 仍有效；未完成 review 只屬 partial evidence。兩者都不能套用新 SHA。
- 結論前重新核對 target HEAD；`REVIEW: PASS` 必須綁定 exact reviewed SHA / baseline / scope，不表示未審新 HEAD 或整個 Phase 通過。
- PR 核對實際 base、head、merge-base 與 GitHub diff。CI 核對 workflow、required jobs、event、attempt 與 tested SHA；synthetic merge SHA 須核對 base / head。舊綠燈、skip、cancel、pending 或 missing 不當 PASS。
- 明確區分本次執行、閱讀既有 tests、GitHub CI、實作者回報及未驗證。Linux 不冒充 Windows；缺資產與環境限制不冒充 correctness bug，但必要安全門檻缺證據不能無條件 PASS。

### 每次正式審核後的交接

正式 verdict 使用 `REVIEW: PASS` 或 `REVIEW: BLOCKED`，綁定 reviewed SHA / baseline / scope。
BLOCKED 區分確定缺陷、契約衝突與必要驗證缺口；每項提供 exact file / function / region、failure scenario、violated contract、test gap、minimum fix 及 regression requirement。

每次正式審核完成後，直接附上可複製交給 Codex 的完整下一步指令，不必再問使用者是否需要：

- BLOCKED：corrective implementation 或補證據 prompt。
- PASS：當前已達門檻下的下一個 workflow action。
- Prompt 包含 repository、phase/objective、branch、SHA、scope、授權範圍、禁止事項、必要驗證、停止條件與交回資料。
- 不偽造 write 授權，不跳過必要門檻；實作者自我檢查不冒充 independent review。
