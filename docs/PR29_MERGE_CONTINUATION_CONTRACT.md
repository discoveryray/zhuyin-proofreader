# PR #29 限定第六輪與合併接續附約

本文件記錄使用者於 2026-09-26 在原 `review-confirm-responsive` 任務明確採納的限定授權；它不回溯改寫第五輪。逐字轉錄保存在 [第六輪採納文字](evidence/pr29_round6_user_adopted_contract.md)，UTF-8 LF Git blob 內容的 SHA256 為 `1ab7e0e1e17dfa0a5ebddf0c0f697ca4056b7f090cc011ecc479b070a872a335`，`source_ref` 為 `pr29-round6/user-adopted-contract`。此檔是對話授權的逐字轉錄，不宣稱是 ChatGPT 平台原始訊息 bytes；協調者仍須親核有效採納來源。Windows checkout 可能把 LF 轉為 CRLF，故原內容的逐 byte 核對應使用固定 commit 的 `git show C:docs/evidence/pr29_round6_user_adopted_contract.md`，不能把 checkout 轉換當作授權變更。

## 固定範圍與不變歷史

唯一 repository 為 `discoveryray/zhuyin-proofreader`，task `review-confirm-responsive`，原 [PR #29](https://github.com/discoveryray/zhuyin-proofreader/pull/29)，base/compare `develop`／`codex/review-confirm-responsive`。固定 B=`1593e7af65596d320b4427f1b15bb2bc0bdc949c`、H=`a38bdf1c84889c52e7281a0c23c9458e3762afa8`、D=`502414b3b38e004a6d8d9cb693cf21b65148765a`、第五輪 N=`0249c44636857739a262d10bd7f4e7ed1f713d1c`。第六輪候選 C 由本輪新增 commit 決定，不預寫入受審 workflow/gate。

原四輪提交鏈、第五輪 N、兩份完整第五輪報告、14 項歷史缺件及原 gate STOP 原樣保留。既存第五輪 gate state 原始 SHA256 為 `45b5d52d53385a89e8759022bb691ffbe55d049c56791d7ebda694bec4bffff8`；原 STOP 決策 SHA256 為 `33efa9af7012d50a88a4b1f21103a550b89c7a868328cfb6e95927ffed63f182`。這些雜湊只引用私人原件；去識別化公開副本不得稱為原始 bytes。早期遺失原件仍標缺失，不補造授權、PASS 或 gate decision。新候選若證實早於本輪已有超過五次修正、或已知重大問題無法判定，停止並更新真實計數。

第五輪 `zhuyin-pr29-continuation/1` schema、原輸入與 STOP 不改。第六輪僅用 `scripts/pr29_review_gate.py` 的新 `zhuyin-pr29-merge-continuation/1` closed schema；一般任務的 `scripts/pr_review_gate.py` 與三輪限制不改。第六輪累計上限六輪。首次 C 正式交 R1 後如 confirmed code finding 仍需改任何受審程式、測試、設定或規範，屬第七輪，立即 STOP。純補原始證據、理由明確的 CI 重跑或 PR evidence 更新不加輪，也不提交空 commit。

## 證據輸入與信任邊界

第六輪 state 的 top-level 欄位恰為 `schema task authorization current implementers corrections reviews unavailable_review_rounds pr pr_ci handoffs continuation remote local_validation prior_fifth ready_transition merge post_merge_ancestry push_event push_ci`。`validate` 只檢查形狀與內部一致性，回報 `authenticity_verified=false`；`next-action` 不讀網路、不修改 Git/PR、不執行 merge。自填 PASS、授權 SHA、source ref、布林、run ID、agent ID 或 gate 決策都不是原始證據。協調者須保存並親核使用者採納原文、Git objects/direct refs、GitHub 原 PR API 與 merge 紀錄、兩個真正不同且未參與實作的 fresh reviewer 完整報告、完整 CI run/attempt/jobs/steps/logs、保護規則及每次 gate 原輸入/輸出。

- `task` 精確綁上述 repo/task/B/branch；`authorization` 精確綁本次採納 ref/hash、有效性與操作 allowlist。沒有任意 override 或一般輪次擴充。
- `corrections` 必須恰六筆連續，原五輪固定提交鏈不可改，第六筆 N→C。`current` 為 D/C/C tree 與 clean-tree 實證；`continuation` 保留重建標籤、14 缺件及 KF-01～KF-07、RC-01～RC-04 至少全部已知 finding，各項須有 C 的具體 verification ref。缺失報告未知內容仍未知。
- `prior_fifth` 引用原第五輪 state/STOP 原始雜湊、兩份完整報告 ref 與原缺件清單；它不是把第五輪 STOP 改為 PASS。歷史原件與公開去識別化副本應分別索引。
- `local_validation` 綁 Windows Python 3.13.0 的 C 與原始 logs，必須包含 full unittest、full pytest、GUI、runtime、compile、B→C diff、D→C diff、測試清冊、clean tree。產品程式及效能 harness 未改時，可標記沿用 N 效能結果，附來源、逐檔影響分析，不能說成 C 重測。
- `reviews` 只記 C 的正式新報告；R1 檢 B→C 全部 cumulative diff 及 D 整合，R2 檢完整 PR、當前 base/head/merge-base、CI 原始資料。兩者不得為實作者或彼此同一 session。PASS 不得帶 blocker；BLOCKED 必須有 kind/findings；只有同 HEAD 非 code BLOCKED 可按原凍結契約、獨立完整補審和 append-only relation 處理。Code BLOCKED 不可同 HEAD 補審洗除。
- `pr_ci` 與 `push_ci` 分開記錄 workflow/event/branch/run/attempt/latest attempt/head/tested SHA/ordered parents/tree/完整 Windows jobs 和必要 steps。PR CI 需 synthetic `[D,C]` 且 tree=C；push CI 需實際 merge SHA 的 `push` event、`develop`、`[D,C]`、tree=C。任何 skip/failure、錯 run/attempt/checkout 均不得 COMPLETE。
- `remote.head_ref_exists` 明確區分原 feature ref 尚在（SHA 必為 C）與合併後被 GitHub 自動刪除（`head=null`）；合併前不存在一律停止。PR API 仍須保留 head C。若 develop 已由實際 merge SHA 後續前進，`post_merge_ancestry` 必須逐筆列出從當前 develop tip 沿 first parent 回到 merge SHA 的真實 Git commit 物件與可回查原始證據；單一 `contains_merge` 布林不夠。直接停在 merge SHA 時此欄為 null。

測試中的 `fixture://`、字元重複 SHA、虛構 reviewer、CI 與 gate output 只是對抗測試，不可作真實證據。測試 fixture 的完整建構可見 `tests/test_pr29_review_gate.py` 的 `sixth_state(stage)`；協調者要用真實原始來源獨立製作私人的 append-only state，不複製 fixture 當 PASS。

## 第六輪階段與授權操作

| 實際階段 | 最低真實條件與 gate 輸出 | 協調者下一步 |
|---|---|---|
| 本機 C，直接遠端 D/N，原 draft PR head N | R1 PASS、全部必要 C 本機證據、已知 finding 核對；`PUSH_CANDIDATE` | 再讀 direct refs 與原 PR，只以普通 push 更新原 branch，保留 draft。 |
| 遠端 D/C，原 PR 仍 draft | 最新 C PR CI 的 Windows 3.12/3.13 雙完整入口及兩位 reviewer scope；缺 R2 時 `REQUEST_REVIEW_2`，不足時 `WAIT_PR_CI`／`REFRESH_EVIDENCE`／`INVESTIGATE_CI` | 保留 CI raw metadata/logs，由另一 fresh reviewer 親讀完整 PR。 |
| 遠端 D/C、draft、R1/R2 PASS 與 CI 全齊 | `READY_FOR_REVIEW`，**絕不是** `MERGE_PROPOSAL` | 將唯一原 PR 由 draft 標為 ready，保存 API 回讀。 |
| ready 後 | 再讀 PR、direct refs、findings、rules、最新 CI，記錄 ready 時點和較晚重核時點，base D/head C/保護規則/可合併真實滿足；`MERGE_PROPOSAL` | 再立即重核遠端，使用 expected head SHA=C 的 Create a merge commit；未知 API 結果先查，不重送。 |
| PR 實際 merged | GitHub merged 紀錄、實際 merge SHA、ordered parents `[D,C]`、tree=C、develop direct ref；`VERIFY_MERGE` 或 `WAIT_PUSH_CI` | 取得該 merge SHA 的 develop push event、run/attempt/jobs/steps/logs。 |
| 實際 merge 與其 push CI 通過 | `COMPLETE` | 分別回報 merge SHA 與當前 develop HEAD；若 HEAD 後續前進，保留已驗證 merge 事實並分別說明。 |

Gate 讀的是由協調者保存的外部證據快照，不會自行驗證人類授權真偽、API、Git 樹或真人獨立性。任何外部 base/HEAD 漂移、錯合併 parents/tree、未解 finding、缺權限、超出六輪、未取得真實 run/attempt 或保護規則不滿足均不得繞過。`READY_FOR_REVIEW` 只對 draft→ready 生效；draft 狀態永不輸出即時合併決策，避免循環要求 merge proposal 才能解除草稿。合併後不自行 revert、重複 merge 或直接 push develop。

## CI 排程與最後驗收

`.github/workflows/ci.yml` 保留原 PR29 精確條件：同 repository、head repository、PR number 29、原 head branch、base `develop` D。新增的 develop push 條件僅 `repository`、`refs/heads/develop`、`event.before=D` 的有限**排程窗口**；它會在該事件執行 Windows 3.12/3.13、full unittest、full pytest、GUI 清冊及實際執行核對、runtime、compile、push diff、clean tree 與 Python 3.13.0/Tk 所需 capture。條件本身不證明事件來自 PR29；實際合併須另外由 GitHub PR 紀錄、Git objects、ordered parents/tree 與 push run 的 checkout SHA 證明。不能以 PR CI、workflow_dispatch 或假成功 context 代替 post-merge push CI。一般任務、PR30 原過渡及未符合窗口的 push 行為維持原樣。

實作階段先跑受影響 gate/workflow 測試。固定 C 後取得本任務凍結契約要求的完整 Windows Python 3.13.0 本機 unittest/pytest、GUI、runtime、compile、B→C/D→C diff 與 clean-tree；PR CI 取得雙版本／雙入口。受審候選固定後不得為存 PASS 加新受審 commit。公開證據先從私人原件製作去識別化副本，檢查壓縮包內容、路徑、文字、清冊、逐檔 SHA256 與兩輪報告/CI/gate 引用，PR evidence 區保留兩輪全文及可跨電腦索引。原件保持私人保存；公開副本明示非原始 bytes。實際 merge/post-merge 證據生成後再 append。

真教材、真人操作、長時間使用及打包 EXE 尚未驗收，按本次採納保留為後續事項，不虛稱完成，也不新增為本輪 merge 前置門檻。actual/expected、exact identity、quorum、交易、per-PDF fingerprint、runtime integrity、fail-closed、repair/completion 契約永久不變；不修改產品功能、main、一般輪次、保護規則，不 force/rebase/squash/tag/release、直接 push develop、操作正式 DB 或加入背景 AI/額外 API 計費。
