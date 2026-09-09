# 兩輪獨立審查與自動接續

此手冊供 Codex 執行期間的協調者使用。任務來源、授權及安全邊界以使用者指示、AGENTS.md 和 [完整規範](V58_MASTER_DEVELOPMENT_REVIEW_PLAN_v1.1.md) 為準。它不啟動背景 AI、不呼叫額外 AI API，也不變更 GitHub 保護規則。

## 角色與實際可用能力

專案角色位於 `.codex/agents/`：`coordinator`、`implementer`、`reviewer_cumulative`、`reviewer_pr`。不覆寫使用者的 model、reasoning effort 或 approval policy。協調者是目前主 session；角色檔不會自動把主 session 換成另一種角色，AGENTS.md 負責指示其協調義務。

啟動時先取得 Codex 版本、實際設定及目前可呼叫的 delegation tools。Custom agent 可用時以對應角色執行；若目前工具未暴露角色選擇，協調者讀取角色檔並將完整指令交給原生 subagent，不臆造 `agent_type` 等欄位。記錄實際使用方式，不能宣稱角色設定已熱載入當前 session。

Reviewer 應使用全新 context（例如本環境 `fork_turns="none"`），提供完整需求、適用規範、baseline、base、HEAD、repository／PR 與原始證據位置。不要傳入實作者的結論或另一輪 verdict 作為判斷依據。兩位 reviewer 不同 session，且均未參與本任務任何受審檔案的編寫。

支援角色 sandbox 的入口以 `read-only` 執行 reviewer；如果當前 subagent API 繼承主 session 權限且無 sandbox override，必須明確記錄此限制，以唯讀任務約束、固定 SHA、隔離 checkout 及前後 Git tree／status 核對來保護受審來源，不能聲稱 OS 已強制唯讀。需要寫測試暫存資料時使用隔離路徑，不能操作 live DB。若能力不足以維持 reviewer 與實作者分離，回報 `BLOCKED / capability`，不得偽造獨立 review。

## 本機能力驗證紀錄

2026-09-09 實測 Windows Codex CLI `0.153.4`、desktop package `OpenAI.Codex 26.903.8094.0`；版本分別由 `codex --version` 及 Windows PowerShell `Get-AppxPackage *Codex*` 取得。依[官方 custom agents 文件](https://learn.chatgpt.com/docs/agent-configuration/subagents#custom-agents)，使用 `.codex/agents/*.toml` 的 `name`、`description`、`developer_instructions`，reviewer 加 `sandbox_mode = "read-only"`；專案 `[agents]` 設定 `enabled = true`、`max_concurrent_threads_per_session = 4`。不覆寫 model、reasoning effort 或 approval。

在 repository 根目錄執行 `codex doctor --json`，`config.load=ok` 且沒有角色 startup warnings。另以 `codex --strict-config app-server --stdio`，依 CLI 產生的 schema 呼叫 `initialize`、`config/read`（`cwd` 為 repository、`includeLayers=true`），確認正式 project layer `disabledReason=null`、effective enabled／concurrency 分別為 `true`／`4`。隔離 fixture 中逐一移除四角色的 `description`，每次均明確拒絕對應角色；非法 reviewer sandbox 值也被拒絕，證明角色檔確實經 discovery 及型別檢查。全程未呼叫模型 turn。`doctor` 整體 fail 來自工具管線的 `TERM=dumb`／非 TTY；Defender exclusions 未驗證另列 warning，均未以變更防護設定處理。

此 session 的 `collaboration.spawn_agent` 無角色選擇或 sandbox 參數，實際委派採完整角色指令及 fresh context；以上設定載入證據不代表已熱套用至既有 session，也不證明 child 有 OS 強制唯讀。官方另指出 parent live sandbox／approval overrides 會重新套用；因此仍須執行前述隔離來源與前後 tree／status 核對。原始設定驗證 logs 保存於本次 task 的 `tmp/pr-review-automation/agent-config-probe/`，正式兩輪審查證據另依下述規則保存至 PR。

## 一次任務的執行步驟

1. Fetch，核對 origin/develop、branch、working tree、現有 PR 與舊任務。建立獨立 task branch。保存固定 task baseline、原始需求與驗收條件，使用者指定固定 base 時不得改動。
2. 建立 `tmp/pr-review-automation/<task-id>/` evidence 目錄；保存角色分工及 correction round（初始 0）。只有協調者安排 Git mutation；同一時間不得有兩個代理操作同一 index／checkout。可平行編輯明確互不重疊的檔案；跨帳號仍須 clone／worktree 隔離。
3. 委派實作代理，完成必要修改及測試，以新 commit 固定審查 HEAD。Coordinator 若也修改檔案，將自己記為實作者。停止寫入受審來源，產生 baseline→HEAD changed-files／patch、parent chain、測試 logs。
4. 委派第一輪 reviewer 親自審完整 cumulative diff。保存完整報告、session ID、`blocker_kind`、findings 及唯一 `report_ref`。BLOCKED 先依下述分類與 gate 核對，只有 confirmed code finding 交回實作代理新增 corrective commit，使用一次同 task corrective round，再對新 HEAD 取得兩輪完整適用 PASS。Evidence 補證據、capability／contract STOP；解決後保留 HEAD 與 count，以 append-only 完整補審接續。最多三輪程式修正，不因 CI 重跑或重開 session／task 清零，不新增空 commit。
5. 第一輪 PASS 後，先查詢 repository/base/compare 的 PR。不存在才建立；存在唯一 open PR 則更新；已 merged 則轉入實際 merge 驗證；closed-unmerged 或多個不明結果停止。建立 API timeout 時先重新查詢，不重複送出。PR description 清楚寫問題、行為、範圍及驗證，附完整第一輪 evidence。
6. 核對最新適用 CI：workflow path、event、run ID、run attempt、API head SHA 與 log 實際 checkout SHA、完整 jobs／必要 steps。查詢需完整分頁；不能只憑一頁或合併 status success。Synthetic merge 需取得 Git object，驗證兩個 parents 為實際 base、HEAD，並記錄 tree。新 attempt／新 run 不能用舊 success 代替。
7. 委派第二輪 reviewer 親自審 PR 的完整檔案差異、baseline cumulative context、最新 findings、merge-base、合併情境及對應 CI。第二輪 PASS 必須綁 exact base/head 與其核對的 CI run／attempt。BLOCKED 使用與步驟 4 相同的分類及補審契約；程式修正使 HEAD 變更時兩輪都重做，不能只審修正片段。
8. 協調者從原始代理輸出及即時遠端建立 gate state，依 [gate 契約](PR_REVIEW_GATE.md) 執行 `validate` 和 `next-action`。不能自行寫入 PASS 來取得 merge proposal。Gate 僅驗證提供資料的一致性，不能認證 reviewer 身分或取代 GitHub 的保護規則；需留存原始來源供追溯。
9. Merge 前緊接著重讀 base/head、PR open／mergeability、最新 review findings、保護規則與最新 CI；差異先使現有 proposal 失效。確認授權仍有效後，只呼叫 merge method `merge`，綁 `expected_head_sha`。不得自行 approve／resolve／刪 branch，或在 API 被保護規則拒絕後繞過。
10. 保存實際 merge 回傳，再從 GitHub／Git object 交叉核對 SHA、兩個 parents、tree。預期 tree 取自通過整合檢查的 synthetic merge；若 base 是 feature 的 ancestor，也可核對 feature tree。GitHub 沒有 base SHA CAS，若合併瞬間 base 競態發生，記錄已發生的實際 merge，停止完成宣告並交回受影響 integration review，不自行 revert／force push。
11. Fetch 後讀取 origin/develop；取得實際 merge SHA 的 `push` CI，逐項核對 Windows Python 3.12／3.13 的完整 unittest／pytest、runtime integrity、compile、push whitespace 及 clean-tree。缺失或 pending 就繼續適度等待，failed 調查並如實報告，不能用 PR CI 代替。已知 post-merge code blocker 先 STOP 並保留實際 merge／task／count；確認仍在授權 scope 及三輪上限內，才可另以 corrective branch／PR 走兩輪流程，不再次 merge 舊 PR、直接 push develop 或自動 revert。Non-code 限制僅補證據／STOP 與同 HEAD 補審，不另開 branch 或消耗修正輪次。
12. 所有 gates 通過才宣告該 task COMPLETE。附 merge SHA、parents、目前 develop HEAD、post-merge CI run／attempt／tested SHA／jobs 與 working tree；未交付的下一階段不自動開始。

## BLOCKED 分類與同 HEAD 補審

兩輪使用相同 [gate v2 契約](PR_REVIEW_GATE.md)，在安排 corrective write 或更新 count 前先查核分類與原始證據：

- `code`：已證實違反明確採納契約的程式、測試、設定或指令缺陷；只有此類新增 corrective commit、使用同 task 最多三輪修正並對新 HEAD 重做兩輪完整適用審查。
- `evidence`：必要驗證或原始證據不足，先 `REFRESH_EVIDENCE`；不猜成程式錯誤。
- `capability`／`contract`：能力不可用或契約尚未釐清，先 STOP，交回限制及解除條件；不要求未證實的程式修改。

BLOCKED 必須有明確 `blocker_kind`、非空 findings、唯一 `report_ref`；PASS 的 kind=null、findings 為空。`pr.new_blockers` 只放協調者已查明、與目前 PR snapshot 同 scope 的 confirmed code findings。未確認的 CI failure 只調查；non-code 缺口留在分類的正式報告。

解除 non-code 限制後，保持 HEAD、baseline、corrective count，以新獨立 full-diff 報告 append 補審。新報告的 `supersedes_report_ref` 指向較早、尚未被取代、相同 round／baseline／base／head／scope 的 non-code BLOCKED，`resolution_evidence_ref` 指向協調者已查核並保存的原始 resolution artifact；沒有補審時兩欄均 null。保留所有舊原文、單向 relation、分類與證據，不覆寫、刪除或製造空 commit。第三輪程式修正已用完也可合法補證據，不藉重啟或另開 task 重設上限。

原／新 reviewer 均須獨立於實作者，第二輪 reviewer 不得參與同 code scope 第一輪；補審仍親自審完整適用差異，第二輪 PASS 綁最新適用 CI。未知／較晚／自身 target、叉分、跨 scope、缺 resolution、非獨立或未 full-diff 的補審均 fail closed。Code BLOCKED 不可同 HEAD supersede，CI rerun 或無關 PASS 不能清除。JSON reference 不是原始證據真偽的認證。

## 證據與中斷恢復

Evidence 至少包含：

- Task ID、原始需求及授權、scope、固定 baseline、base/head、全部實作者 session、兩位 reviewer session。
- 完整 cumulative patch／changed files 與 parent chain；隔離環境、實際測試命令、結果、未執行事項及原因。
- 每次 review 的 scope、SHA、verdict、blocker_kind、findings、report_ref、supersedes_report_ref、resolution_evidence_ref、原始依據及下一步；保留全部原 BLOCKED、補審及 corrective round 歷史。
- PR URL、即時 metadata、review findings、CI raw metadata／logs／必要 steps、gate input／decision、合併請求與回應、實際 merge parents/tree、develop HEAD、post-merge CI。

本機 logs 保存於 task evidence 目錄。兩輪完整 review 報告、session ID、SHA、scope、verdict 及必要 CI／merge 證據同步到 PR description 的 evidence 區；這是保存 reviewer 原文，不能寫成協調者或 PR 作者的 GitHub APPROVED review。永久記錄不得僅有本機絕對路徑或一句 PASS。較大型 test logs 可引用相同 SHA 的 GitHub Actions logs。

不要將當前 HEAD 的 review PASS 提交到受審 branch 造成自我引用循環。PR evidence 區可更新稽核資料；若需求、scope 或程式碼改變，必須重新審查。中斷後從現有 task evidence、PR 及即時 Git 狀態恢復；已建立 PR 不重建、已合併不再合併。不確定副作用先查遠端；紀錄缺失不能冒充已通過或將 correction count 歸零。

流程只在 Codex 執行時運作。關閉／停止 session 不保證背景繼續；日後接續同一 task 時重讀紀錄即可，無須使用者轉貼 reviewer 結果。能力／契約限制、必要驗證暫不可得或三輪後仍需程式修正時，依上述分類交回具體限制、已完成工作與最小下一步，不將 non-code 補證據誤算為第四輪。

## 隔離驗證

`tests/test_pr_review_gate.py` 使用虛構狀態及假服務驗證轉移與副作用決策，不連線 GitHub，不將 synthetic fixture verdict 當正式審查。必須覆蓋 PASS 接續、BLOCKED 分類、同 HEAD append-only 補審及無效取代、CI failure 禁止 merge、HEAD／base 改變失效、PR／merge replay、代理不可用／report 缺失及三輪程式修正上限。另逐一核對所有角色及規範入口的指令一致性，不能以 gate tests 取代。

本次流程建置本身另走真實兩輪審查及既有 `.github/workflows/ci.yml`，不增加背景 AI workflow。即使隔離 tests 通過，仍不能省略正式 reviewer 或實際 post-merge CI。

## 日後啟動

`請依 repository 的兩輪獨立審查自動流程，完成：〈任務與驗收條件〉。`

若需限制授權，直接加上例如「只到 PR，不合併」或指定固定 baseline／階段；當次明確限制優先。[ChatGPT 專案指令精簡版](CHATGPT_PROJECT_INSTRUCTIONS.md) 可貼入專案長期指令。
