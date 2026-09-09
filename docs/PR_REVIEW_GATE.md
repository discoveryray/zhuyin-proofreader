# 兩輪審查流程的證據 gate

`scripts/pr_review_gate.py` 是 Codex 執行期間使用的純 Python、唯讀決策工具。
它不啟動代理、不連網、不持有 credentials、不修改 Git、不建立 PR、不執行 merge，
也不建立 GitHub Actions 背景 AI 工作。協調者負責取得真實證據、執行已授權下一步，
再將遠端結果寫入下一份證據 snapshot。

```powershell
python scripts/pr_review_gate.py validate <state.json>
python scripts/pr_review_gate.py next-action <state.json>
python -m unittest discover -s tests -p test_pr_review_gate.py
python -m pytest tests/test_pr_review_gate.py -q
```

`validate` 只回報 `schema_valid`，並明確回報 `authenticity_verified: false`。
`next-action` 回傳 JSON decision；`STOP` exit code 為 2，其餘為 0。
**Exit code 0 不表示 PASS、merge 授權或完成**，必須讀取 `action`。
Python 介面為 `validate_state(state)` 與 `next_action(state)`；後者不修改輸入。

## 信任邊界與留存

JSON 裡自行填入 `PASS`、agent ID、URL、SHA 或 `protection_satisfied: true`，
並不證明 review、CI 或授權真的存在。Gate 驗證結構及內部一致性，無法認證其真偽，
也不能證明文字形式的兩個 agent ID 是兩個獨立代理。
協調者只能從下列原始來源收集欄位，不得採信實作者自行宣稱的 PASS：

1. 使用者任務及有效、未撤銷的持續授權，連同本次收窄限制。
2. 獨立 reviewer 的實際代理／Work 回覆與完整報告；確認兩位 reviewer
   都沒有參與本次程式或文件實作、没有以另一份 PASS 或 CI 取代自行審查。
3. Git fetch／commit objects／檔案樹，以及 GitHub PR、review、保護規則、
   workflow run、run attempt、jobs metadata 和各 job 原始 logs。

`evidence_ref`／`report_ref` 是可回查原始 artifact 的識別，不是簽章。
必須保存原始報告、工具輸出、範圍 SHA、CI URL、交接及每次 decision。
缺少來源、代理不可用或不能證明獨立性時，停在收證據／BLOCKED，不得填預設 PASS。
測試中的 `fixture://`、重複字元 SHA、`evidence_state()` 及 `FakeService` 都是隔離資料，
不得用作本次或日後真實 merge 的證據。

Task ledger 必須保留所有舊 review 與 corrective round，不得刪除 BLOCKED、
重設計數或更改原始 baseline 來繞過三輪上限。每次 snapshot 應留存為獨立 artifact，
連同代理交接原文保存於本次 Codex task 的交接資料／持久證據目錄。
不要為了將當前 HEAD 的 review 報告提交到同一個受審分支而偷偷改變 HEAD；
任何此類新 commit 都需要適用的重新審查。

## 決策與協調者動作

| `action` | 必須執行的接續 |
|---|---|
| `REQUEST_REVIEW_1` | 委派独立 reviewer，直接審查原始 baseline → current HEAD，包含當前 base 整合影響。 |
| `ENSURE_PR` | 第一輪已 PASS；先依 repository＋base/head branch pair 查找既有 PR，再建立或採用既有 PR。 |
| `WAIT_PR_CI` | 等待並取得 current scope 的 PR CI，不能使用舊 SHA 綠燈。 |
| `REQUEST_REVIEW_2` | 委派另一位独立 reviewer，直接審查 cumulative diff、PR 完整差異及指定 CI run/attempt/logs。 |
| `CORRECT_IMPLEMENTATION` | 未合併的目前 scope 有 confirmed code finding；即使 CI failed／pending 也交回實作代理，新增 corrective commit；保留計數後重新取得兩輪適用 PASS。 |
| `REFRESH_EVIDENCE` | 補齊／重新取得遠端證據。SHA、run、attempt、job／step 證據不符本身不是程式 bug。 |
| `INVESTIGATE_CI` | 調查實際 CI failure／cancelled／skipped；未證實程式問題前不要求 corrective commit、不消耗修正輪次。 |
| `MERGE_PROPOSAL` | 核對有效 merge 授權、遠端 base/head、最新 findings／CI／保護規則後，才可呼叫 merge API。 |
| `VERIFY_MERGE` | PR 已 merged，僅讀取實際 merge commit、parents、tree；不可再次 merge。 |
| `WAIT_PUSH_CI` | 等待實際 merge SHA 的 develop push CI。 |
| `COMPLETE` | 兩輪 review、PR CI、實際 merge 驗證、該 merge 的 push CI 及目前適用 findings 查核全部具備，且無未解除 blocker；分開回報 merge SHA 與最新 develop HEAD。 |
| `STOP` | 缺乏授權、違反契約、能力／契約限制、第三次修正仍有 code blocker、post-merge 證據不符等；回報具體原因。 |

CI 調查確認程式 blocker 後，應取得可回查的 finding（例如 reviewer 明確分類為 code 的 BLOCKED，
或經協調者查明並記錄到 `pr.new_blockers` 的問題），再進入修正輪。
Gate 先核對 branch pair 與 current base/head，再讀取適用且獨立性／scope／原始報告
欄位合法的 code BLOCKED；此修正路徑不以 CI success 為前提。`pr.new_blockers` 只放
經協調者查明的 confirmed code findings，必須屬於本筆已核對的 PR snapshot，且
`findings_checked` 為 true；evidence／capability／contract 缺口留在分類的正式 review。
Scope drift 先補證據，
不能把舊 finding 文字直接移貼到新 base/head。Failed CI 沒有 confirmed code finding
時仍是調查，不自動要求 corrective commit，更不放行 merge。
CI 環境或權限問題不能以偽造資產、關閉驗證、新增 skip、反覆空 commit 解決。
三輪限制是同一任務的 corrective cycle，涵蓋兩輪 reviewer，並非各自三輪。

已合併 scope 的 confirmed code blocker 一律 `STOP`，附明確 post-merge handoff，
保留 `task_id`、原始 `baseline`、已知的實際 `merge_sha`、`corrections_used` 及
`correction_limit: 3`；不能再次 merge 舊 PR、直接 push develop 或自動 revert。
已用完三輪時回報上限耗盡，不能另開同義 task 重設計數。Evidence BLOCKED 或 findings
尚未查核時先 `REFRESH_EVIDENCE`；capability／contract BLOCKED 仍 STOP 並保留交接。
補證據不消耗 corrective count，已用三輪也不妨礙合法補證據及同 HEAD 補審。
即使歷史 PASS 與所有 CI 都成功，未解決的 BLOCKED 也不能宣告 COMPLETE。
這些 decision 不修改原始 merge、review 或 corrective ledger。

`MERGE_PROPOSAL` 包含 `merge_method: "merge"`、`expected_base_sha`、
`expected_head_sha` 及 PR number。GitHub merge API 的 HEAD compare-and-swap
必須使用 `expected_head_sha`；base 沒有同等 API CAS 保證，因此寫入前立即重讀 base，
發現 drift 即停止，取得受影響的新審查／整合驗證，不自行 rebase 或改 baseline。
寫入後仍必須驗證實際兩個 parents 與 tested integration tree；若 base 在最後時刻前進，
不得把不相符的 merge 宣稱完成，須明確交回受影響的 integration review。

`idempotency_key` 只是 deterministic 本機決策識別，不是 GitHub 的冪等保證。
建立 PR 前先查既有 branch pair；不因本機記錄缺失或回應不明而重建。
Merge 前重新查 PR 是否已 merged；回應不明先查遠端結果，不盲目重送。
關閉未 merge 的既有 PR 預設 STOP；不得擅自建立替代 PR。
Gate 本身不能防止協調者忽略查詢或直接呼叫寫入工具；隔離 fake-service tests
驗證的是協調者遵守此查詢／重核對契約時的 replay 與競態處理。

## Closed schema v2

Top-level 與列出的 nested objects 必須恰好包含指定欄位。未知欄位、未知 schema、
缺欄位、重複 JSON keys、NaN／Infinity、短 SHA、零 SHA、boolean 冒充整數均拒絕。
所有 Git commit/tree 欄位使用完整 40 位小寫十六進位 SHA。

以下初始 snapshot **只是格式示範，沒有真實授權或 review 證據**：

```json
{
  "schema": "zhuyin-pr-review-gate/2",
  "task": {
    "id": "example-task",
    "repository": "discoveryray/zhuyin-proofreader",
    "baseline": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "base_branch": "develop",
    "head_branch": "feat/example"
  },
  "authorization": {
    "task_id": "example-task",
    "active": false,
    "operations": [],
    "source_ref": "REPLACE_WITH_ORIGINAL_USER_INSTRUCTION"
  },
  "current": {
    "base": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "head": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "working_tree_clean": true,
    "evidence_ref": "REPLACE_WITH_FRESH_GIT_AND_REMOTE_SNAPSHOT"
  },
  "implementers": ["REPLACE_WITH_ALL_CODE_AND_DOC_WRITING_ACTOR_IDS"],
  "corrections": [],
  "reviews": [],
  "unavailable_review_rounds": [],
  "pr": null,
  "pr_ci": null,
  "merge": null,
  "push_ci": null,
  "handoffs": []
}
```

- `authorization.active` 表示協調者已查核該任務授權未撤銷，不代表所有操作都被授權。
  `operations` 是實際授權的 allowlist：`implement`、`delegate`、`test`、`commit`、
  `push`、`pr`、`merge`。使用者限定「只到 PR」時必須省略 `merge`；Review PASS
  不會擴張 allowlist。各 decision 會檢查所需操作，缺少時 STOP。
- `current.base/head` 來自新鮮的 fetch＋GitHub 核對；`task.baseline` 留存任務原始起點。
  HEAD/base 改變會使舊 review 不適用；不可覆寫歷史報告的 SHA。
- `implementers` 必須包含所有改過受審程式或文件的 actor，包括有修改的協調者。
- `unavailable_review_rounds` 只允許 1、2；某輪無適用報告且代理不可用時 STOP。
- `corrections` 的每筆欄位：`number`（從 1 連續，最多 3）、`from_head`、`to_head`、
  `evidence_ref`；相鄰輪次首尾須連續，且每輪產生新 commit。
- `handoffs` 的每筆欄位：`from`、`to`、`head`、`evidence_ref`。

Review record 欄位：

```json
{
  "round": 1,
  "reviewer": "REPLACE_WITH_INDEPENDENT_AGENT_ID",
  "baseline": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "base": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "head": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "scope": "baseline_to_head",
  "verdict": "BLOCKED",
  "blocker_kind": "evidence",
  "independent": true,
  "full_diff_reviewed": true,
  "findings": ["REPLACE_WITH_ACTUAL_FINDING"],
  "report_ref": "REPLACE_WITH_ORIGINAL_REVIEW_REPORT",
  "supersedes_report_ref": null,
  "resolution_evidence_ref": null,
  "ci": null
}
```

第二輪 `scope` 必須為 `cumulative_and_full_pr_with_ci`，`ci` 包含 `run_id`、
`attempt`、`tested_sha` 並與本次 PR CI 相同。第二輪僅在 non-code BLOCKED 且 CI metadata
不可得時允許 `ci: null`；不據此放行。第一輪 `ci` 一律 null。
Reviewer 不得存在於 implementers，第二輪 reviewer 也不得參與同 code scope 的第一輪。
`report_ref` 全域唯一，必須指向各自原始報告。PASS 的 `blocker_kind` 必須是 null，
findings 必須空；BLOCKED 必須有非空 findings 並明確分類：

| `blocker_kind` | 意義與動作 |
|---|---|
| `code` | 已證實需要程式修正；只有此類使用三輪 corrective implementation 上限。 |
| `evidence` | 必要驗證／原始證據不足；補證據，保留 BLOCKED，不能要求空 commit。 |
| `capability` | 代理／平台／權限能力不可用；STOP，解決能力缺口後補審。 |
| `contract` | 尚未釐清的契約限制；STOP，取得明確依據後補審，不能猜測需修改程式。 |

Nonblocking 建議另留原始報告。未知分類、空 BLOCKED、PASS 同時帶 blocker 均 fail closed。
若確定存在 code finding 且同時缺部分證據，分類為 code 並在原始報告列出兩者；
不得將未證實的問題升格為 code 以消耗修正輪次。

### 同 HEAD 補審與 append-only 歷史

未補審的報告兩個 relation 欄位均為 null。補齊非 code 缺口後，獨立 reviewer 必須重新
審查完整適用差異及原始 resolution 證據，產生新報告並 append，不覆寫／刪除舊報告：

```json
{
  "supersedes_report_ref": "ORIGINAL_NON_CODE_BLOCKED_REPORT_REF",
  "resolution_evidence_ref": "RETAINED_ORIGINAL_RESOLUTION_ARTIFACT_REF"
}
```

這是附加到新完整 review record 的兩個欄位，不是可單獨宣告 PASS 的資料。
Target 必須是 list 中更早、尚未被取代的 non-code BLOCKED；round、baseline、base、
head、scope 必須完全相同，原／新報告均需有效獨立性及 full-diff 審查。
第二輪補審可以 attestation 同一或新的 CI run/attempt，但其 PASS 仍須匹配最新適用 CI。
Resolution 必須是協調者從原始平台驗證、logs、代理能力或契約依據取得並留存的 artifact；
任意自填 reference 不證明缺口已解除，gate 不認證文字或 URL 的真實性。

不存在／較晚／自身 target、同一 target 叉分兩份補審、跨 scope 或跨 round、
缺 resolution、非獨立或未 full-diff 的原／新報告均拒絕。合法補審可形成單向 append-only
鏈；每份舊原文持續保留。沒有明確 relation 的同 scope／CI 新報告是歧義，拒絕接受；
只換 CI attempt 的無關 PASS 也不清除任何尚未解決的 BLOCKED。
Confirmed code BLOCKED 不允許同 HEAD supersession，必須新增 corrective commit 並對
新 HEAD 取得完整兩輪適用審查；CI rerun 不會修復 code finding。

舊 `zhuyin-pr-review-gate/1` 不自動相容。協調者須保留全部原 v1 snapshots 與原始報告，
另建 v2 snapshot，逐份核對原報告後明確分類；PASS 加入三個 null 欄位，BLOCKED
不得從 verdict 或 CI failure 猜成 code。既有三輪計數、task ID、baseline、merge 歷史
逐項保留；沒有原始分類／resolution 證據時，留在補證據／STOP，不生成新 PASS。

PR record 欄位：`number`、`url`、`state`（open/closed/merged）、`base_branch`、
`head_branch`、`base_sha`、`head_sha`、`mergeable`、`protection_satisfied`、
`findings_checked`、`new_blockers`、`evidence_ref`。
後三個安全欄位必須從最新 PR findings、必需 checks 及規則核對，不能因 CI 綠燈自動設 true。
已 merged 時 `base_sha/head_sha` 保留合併前實際受審、已核對的 pair，
`current.base` 才是 fetch 後最新 develop，不能用移動後的 develop 覆蓋歷史 pair。

PR／push CI 共用欄位：

| 欄位 | 證據來源及契約 |
|---|---|
| `event`, `branch`, `workflow` | PR 必須 pull_request＋feature branch；post-merge 必須 push＋develop；workflow 為 `.github/workflows/ci.yml`。 |
| `run_id`, `attempt` | 原始 run metadata 的 ID、run_attempt，均為正整數。 |
| `latest_run_id`, `latest_attempt` | 重新列出 current scope 適用 runs／attempts 後取得，須與本筆一致。 |
| `head_sha` | PR run 為 reviewed feature HEAD；push run 為 actual merge SHA。 |
| `tested_sha` | 各 job logs 證明的實際 checkout；PR 為 synthetic merge，push 為 actual merge。 |
| `parents`, `tree` | tested commit Git object 的完整、有順序 parents 與 tree SHA；parents 必須是 reviewed base、head。 |
| `status`, `conclusion` | 完成門檻為 completed、success；pending 不放行。 |
| `url`, `evidence_ref` | Run URL 與保留原始 metadata／logs／Git object 證據。 |
| `jobs` | 全部 workflow jobs；不能只挑成功的 job。 |

每個 job 欄位：`name`（Python 3.12／Python 3.13）、`runner`（實際 windows-* image）、
`python_version`（實際 3.12.x／3.13.x）、`run_id`、`attempt`、`tested_sha`、
`conclusion`、`steps`、`evidence_ref`。每個 job 必須綁相同 run／attempt／checkout；
steps 是 `{ "原始 step name": "原始 conclusion" }`，不能把 skipped 改成 success。
必要 steps 對齊現有 workflow，包括 checkout/setup/dependencies、runtime integrity、
完整 unittest／pytest、compile、event 適用 whitespace 與 clean-tree。
非本 event 的 whitespace step 可按原狀保留 skipped。
增加／變更 workflow gates 時，須同步更新此工具的 bounded contract 並受審，不能靜默省略。

Actual merge record 欄位：`sha`、`parents`、`tree`、`develop_head`、
`develop_contains_merge`、`evidence_ref`。從實際 merge response、Git object、fetch
及 ancestry 檢查取得；兩個 parents 須等於受審 pair，tree 須等於 PR tested integration tree。
不可假設 actual merge SHA 等於 synthetic SHA。`push_ci` 必須另行取證；PR CI 不能代替。
Develop 後續前進時，可在已證明保留此 merge 的條件下完成該 merge 的驗證，
但必須分別回報 actual merge SHA 與新的 develop HEAD，不宣稱新 HEAD 也受本次驗證。
