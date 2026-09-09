# zhuyin-proofreader Codex 專案規範

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
- 使用者明確交付的開發任務，預設授權該任務範圍及階段內的檔案修改、代理委派、測試、commit、push、建立／更新 PR，以及通過第 17 節全部門檻後以 Create a merge commit 合併至 `develop`、執行 post-merge 驗證。使用者可另行收窄或撤銷，不必逐次重問有效授權。
- 上述授權不包含直接 push `develop`、修改 `main`、squash／rebase merge、force push、降低 GitHub 保護規則、tag／release、live DB mutation，或未交付的下一階段。
- 不得自行建立 tag 或 release。
- 不得自行 force push。
- 已經提交給外部審查的 commit，不得自行 amend 或改寫歷史；需要修改受審檔案的修正應新增 commit，除非使用者明確要求其他方式。純補證據不新增空 commit。

若任務指示要求新 branch，先核對 working tree clean，再從指定基礎建立，不挪用其他任務分支。除此以外，branch/base 與任務不一致時停止寫入並回報。

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

依第 17 節取得兩輪適用的獨立 PASS 及必要 CI 後，協調者自動執行已授權的 merge 與 post-merge 驗證。`READY FOR REVIEW` 是中間狀態，不是要求使用者轉貼或逐步授權的停點。

只有實際 merge 與對應 develop push CI 也通過，才可回報該任務 `COMPLETE`；pending／failed／缺失時不得宣稱完成。審查 PASS 本身不創造或擴大授權。

實作者不得同時充當唯一 merge approver。

---

## 15. Codex 回覆方式

- 回報以繁體中文為主。
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

## 17. 兩輪獨立審查與 Codex 執行期間自動接續

適用完整規範為 [v5.8 完整規範](docs/V58_MASTER_DEVELOPMENT_REVIEW_PLAN_v1.1.md)，其中第 1、5～8 節及 [執行手冊](docs/PR_REVIEW_AUTOMATION.md) 定義自動協調契約；安全契約仍依本文件及完整規範第 3～4 節。

- 協調者必須委派實作代理與兩位獨立 reviewer，並收集結果、核對原始證據及接續下一步。Work 或符合相同契約的獨立代理均可擔任正式 reviewer，不再限定 Work。
- 兩輪 reviewer 是不同 agent session，均不得為本任務實作者（包含修改文件、測試、設定的協調者），不得修改受審檔案。以乾淨 context 提供任務、規範、固定 SHA、範圍與證據位置，不能把實作摘要或前一輪 PASS 當審查依據。測試可在隔離 checkout 執行。
- 第一輪實際審查固定 task baseline → feature HEAD 的完整 cumulative diff。PASS 後自動查找並建立／更新同 repo/base/compare 的唯一 PR。
- 第二輪自行審查 PR 全部差異、當前 base/head/merge-base、整合情境與適用 CI。第二輪 PASS 且必要 CI 全通過才可提出 merge；立即重新核對遠端及 findings，綁定 reviewed HEAD 合併。
- BLOCKED 必須明確回報 `blocker_kind` 與非空 findings。只有 `code`（已證實的程式、測試、設定或指令缺陷）交回實作代理，新增 corrective commit、重跑必要測試並計入同一 task 最多三輪自動修正；新 HEAD 兩輪均須重新取得完整適用 PASS，不能只審最後一個 commit。第三輪後仍需程式修正則停止回報，不降低標準，也不重設 task／計數。
- `evidence` BLOCKED 先 `REFRESH_EVIDENCE`；`capability`／`contract` BLOCKED 先 STOP 並交回能力缺口／尚待釐清契約。解決 non-code 限制後，在原 HEAD append 完整適用補審，不改 corrective count、不新增空 commit；三輪已用完也不妨礙合法補證據。不得把缺證據、代理不可用或未釐清契約猜成 code finding。
- 每份報告使用唯一 `report_ref`；PASS 的 `blocker_kind=null`、findings 為空。補審依 [gate v2 契約](docs/PR_REVIEW_GATE.md) 填 `supersedes_report_ref` 與原始 `resolution_evidence_ref`，只能指向較早、尚未被取代、相同 round／baseline／base／head／scope 的 non-code BLOCKED；原／新報告均須獨立及 full-diff。保留舊原文與單向補審歷史，不允許未知／跨 scope／叉分或無 resolution 的取代。Code BLOCKED 不可同 HEAD supersede，CI rerun 或無關 PASS 不能清除它；第二輪補審仍須核對最新適用 CI。
- HEAD 或 PR base 改變會使當前兩輪結果失效；保留原 task baseline，重做受影響的完整審查與整合驗證。固定 SHA 任務不得自行改 scope；不自行 rebase 或改 baseline。Base 前進本身不授權合併 develop 到 feature。
- 代理不可用、必要結果缺失／不完整、CI 失敗或未完成，一律不視為 PASS。已完成的測試與 CI 僅是審查證據，不能取代 reviewer 實際讀取差異及追蹤契約。
- 協調者使用 `scripts/pr_review_gate.py` 核對已收集的狀態，再執行副作用；gate 不提供新授權、不證明輸入真實性。每次寫入前重新讀取遠端；不確定結果先查詢，不盲目重試建立 PR 或 merge。
- 審查與交接紀錄保留 task、角色/session、baseline、base、head、scope、verdict、blocker_kind、findings、report_ref、補審 relation／resolution、測試、CI run/attempt/event/tested SHA、原始證據及下一步；無補審時兩個 relation 欄位均為 null。放在任務 evidence 目錄，並將兩輪完整紀錄保存於 PR description 的 evidence 區，避免為記錄 PASS 新增 commit 再改 HEAD。
- 合併後核對實際 merge SHA 的兩個 parents、預期檔案樹、fetch 後 develop HEAD，及該 merge SHA 的 `push` CI；PR CI 不可取代 post-merge CI。Develop 再前進時分開報告。
- 此流程僅於 Codex 任務執行期間協調，不新增 GitHub Actions 背景 AI、外部 AI API、排程或額外計費設定。中斷後由同一 task evidence 恢復；無法執行時如實回報，不能偽稱背景仍在工作。
