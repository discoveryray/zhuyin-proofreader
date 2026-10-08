# 兩輪審查流程的證據 gate

2026-10-07 使用者明確採納 `validation-flow/no-real-tk/1`，完全取消所有自動化入口的真視窗驗證（含 hidden root、虛擬顯示器、fixture／import／子程序）、Tcl/Tk configure／preflight／初始化能力門檻及 GUI retry／補驗。新政策適用本身與後續驗證；尚未結案任務只遷移 window 義務，其餘凍結契約、baseline、修正計數、原 Tk 失敗及 retry 額度保留，其他 blocker 不自動解除。產品 GUI／Tk／launcher 保留；有效非視窗資料／交易／恢復與功能測試、Python 3.13.0、runtime／compile／diff、兩輪審查與 actual merge 短驗證仍必要。詳見 [驗證政策](VALIDATION_POLICY.md)。

舊 v3／PR29／PR41 schema、原授權及判定保留；下列原 GUI／preflight 歷史 contract 不授權未來再執行真視窗，也不追認舊 PASS。新政策證據與舊原件必須明確區分。

新政策採納使用有限 optional 頂層 `validation_policy: "validation-flow/no-real-tk/1"`，適用 v3／v4；未填時舊 snapshot 保持原 contract 與原判定，不自動遷移。旗標只表示已有人類明確採納的 window 義務遷移，不創造 write／merge 授權、不新增正式 CI 時段或 retry 額度、不清除其他 blocker。只有該精確值可取消 GUI／configure／preflight 必要成功條件；有效 non-window gates 仍全部必須成功，main／legacy 改核對 `Verify active test execution`。v4 新採納旗標必須同時使用精確新 policy 的 `/2` coverage，舊 `/1` GUI 成功不能冒充新正式 coverage。

`scripts/pr_review_gate.py` 是 Codex 執行期間使用的純 Python、唯讀決策工具。
它不啟動代理、不連網、不持有 credentials、不修改 Git、不建立 PR、不執行 merge，
也不建立 GitHub Actions 背景 AI 工作。協調者負責取得真實證據、執行已授權下一步，
再將遠端結果寫入下一份證據 snapshot。

```powershell
python scripts/pr_review_gate.py validate <state.json>
python scripts/pr_review_gate.py next-action <state.json>
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

## Closed schema v3

Top-level 與列出的 nested objects 必須恰好包含指定欄位。未知欄位、未知 schema、
缺欄位、重複 JSON keys、NaN／Infinity、短 SHA、零 SHA、boolean 冒充整數均拒絕。
所有 Git commit/tree 欄位使用完整 40 位小寫十六進位 SHA。

以下初始 snapshot **只是格式示範，沒有真實授權或 review 證據**：

```json
{
  "schema": "zhuyin-pr-review-gate/3",
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
  "review_mode": "full_diff",
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

未補審的報告兩個 relation 欄位均為 null，review_mode 必須 full_diff。補齊非 code 缺口後，獨立 reviewer 產生新報告並 append，不覆寫／刪除舊報告。若程式、baseline、base、head、scope 完全未變，原 reviewer 可以 evidence_gap 核對缺口、原始 resolution 證據及既有完整結論，無須從頭重讀所有差異；換 reviewer 則必須讀足以自行負責整個 scope 的原始材料，以 full_diff 作結論，不能只採信舊 PASS。

```json
{
  "supersedes_report_ref": "ORIGINAL_NON_CODE_BLOCKED_REPORT_REF",
  "resolution_evidence_ref": "RETAINED_ORIGINAL_RESOLUTION_ARTIFACT_REF"
}
```

這是附加到新 review record 的兩個欄位，不是可單獨宣告 PASS 的資料。
Target 必須是 list 中更早、尚未被取代的 non-code BLOCKED；round、baseline、base、
head、scope 必須完全相同，原／新報告均需有效獨立性。review_mode=full_diff 對應 full_diff_reviewed=true；evidence_gap 必須 false，不能虛構重新讀完。Gate 只允許相同 reviewer 連到保留的合法 full_diff 起始鏈；無 relation、換 reviewer、scope 改變或未知 mode 均拒絕。
第二輪補審可以 attestation 同一或新的 CI run/attempt，但其 PASS 仍須匹配最新適用 CI。
Resolution 必須是協調者從原始平台驗證、logs、代理能力或契約依據取得並留存的 artifact；
任意自填 reference 不證明缺口已解除，gate 不認證文字或 URL 的真實性。

不存在／較晚／自身 target、同一 target 叉分兩份補審、跨 scope 或跨 round、
缺 resolution、非獨立或不符合 review_mode 契約的原／新報告均拒絕。合法補審可形成單向 append-only
鏈；每份舊原文持續保留。沒有明確 relation 的同 scope／CI 新報告是歧義，拒絕接受；
只換 CI attempt 的無關 PASS 也不清除任何尚未解決的 BLOCKED。
Confirmed code BLOCKED 不允許同 HEAD supersession，必須新增 corrective commit 並對
新 HEAD 取得完整兩輪適用審查；CI rerun 不會修復 code finding。

舊 v1／v2 snapshots 不自動相容。新制度只由新任務採用，未收尾任務保持其凍結舊 gate、報告、CI 與修正次數；不得為取得 PASS 自行遷移。本次制度變更自身使用 baseline 的 v2 gate、兩輪 full-diff 舊審查及雙 Python／雙入口 transition CI，詳見 [驗證政策](VALIDATION_POLICY.md)。若使用者日後明確授權遷移個別舊任務，須逐份核對原始報告、保留全部歷史、task ID、baseline、修正計數、實際 merge，另建 v3 snapshot；禁止默認填入 review_mode 或捏造新 PASS。

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

每個 job 欄位：`name`（必要 job 為 Python 3.13）、`runner`（實際 windows-* image）、
`python_version`（實際 3.13.0）、`run_id`、`attempt`、`tested_sha`、
`conclusion`、`steps`、`evidence_ref`。每個 job 必須綁相同 run／attempt／checkout；
steps 是 `{ "原始 step name": "原始 conclusion" }`，不能把 skipped 改成 success。
必要 steps 對齊現有 workflow，包括 checkout/setup/dependencies、runtime integrity、
entrypoint collection identity audit、完整 pytest、GUI execution proof、compile、event 適用 whitespace 與 clean-tree。
非本 event 的 whitespace step 可按原狀保留 skipped。
必要步驟包括 `Audit test entrypoint coverage` 與 `Verify GUI test execution`；後者必須核對 full pytest JUnit 的每個必要 GUI case，missing／skipped／failed／error 均不放行。依賴固定於 requirements-ci.txt，支援界線與沿用證據見 VALIDATION_POLICY.md。
增加／變更 workflow gates 時，須同步更新此工具的 bounded contract 並受審，不能靜默省略。

Actual merge record 欄位：`sha`、`parents`、`tree`、`develop_head`、
`develop_contains_merge`、`evidence_ref`。從實際 merge response、Git object、fetch
及 ancestry 檢查取得；兩個 parents 須等於受審 pair，tree 須等於 PR tested integration tree。
不可假設 actual merge SHA 等於 synthetic SHA。`push_ci` 必須另行取證；PR CI 不能代替。
Develop 後續前進時，可在已證明保留此 merge 的條件下完成該 merge 的驗證，
但必須分別回報 actual merge SHA 與新的 develop HEAD，不宣稱新 HEAD 也受本次驗證。

## Gate v4：分階段驗證（明確採用的新任務）

`zhuyin-pr-review-gate/4` 明確區分開發、驗證及實際 merge 的短檢查。
本節只適用明確採用 [VALIDATION_POLICY.md](VALIDATION_POLICY.md) 新流程的任務，
及使用者事前指定的 `validation-flow-reduction` transition。原 v3 snapshot、
PR29 限定條件與未完成舊 task 維持原義；工具不自動升級 schema、不追認 PASS。
前述 v3 的正式 PASS 前開 PR／完整 push CI 規則，對 v4 改依本節；其餘
獨立性、code blocker、三輪 correction、原始歷史及授權規則全部保留。

v4 在原頂層欄位新增：

- `execution: {mode, validation_authorization_ref}`：`mode=development` 為預設工作方式，
  authorization reference 必須 null；`validation` 必須指向使用者明確「開始驗證」原文。
- `coverage`：可讀取的 `coverage.json` 路徑，尚未完成時 null。
- `short_validation`：實際 merge push 的 `short.json` 路徑，尚未執行時 null。

每份 v4 review 增加 `coverage_sha256` 與 `finalizes_report_ref`，未使用時 null。
`CODE_REVIEWED` 是完整差異已審、無 confirmed blocker、等待必要測試的中間 verdict，
其 `blocker_kind=null`、findings 空、coverage digest null；它不是正式 `PASS`。
第一輪完整 cumulative code review 後，開發模式回傳 `WAIT_VALIDATION_AUTHORIZATION`。 v4 開發模式的 confirmed-code 修正僅要求 implement／delegate／test／commit，回傳 `CORRECT_IMPLEMENTATION` 與 `delivery=local_commit_only`，不要求或授權 push。v3 凍結語意及 v4 驗證模式的既有授權門檻不變。
只有 validation mode 才能回傳 `ENSURE_DRAFT_PR`，先查找同 repo/base/head 唯一 PR；
第二輪 `REQUEST_REVIEW_2` 可與 CI 同時進行。任何 confirmed code blocker 仍優先處理，
不以等待測試遮蔽，也不因此重設 task 或 corrective count。

正式 PASS 必須由原独立 reviewer 補核或新獨立 reviewer 完整審查，並綁定實際驗證過的
coverage file SHA-256。中間報告不改寫；append 的報告以 `finalizes_report_ref`
指向較早、未 finalized、相同 round/baseline/base/head/scope 的 `CODE_REVIEWED`，
並以 `resolution_evidence_ref` 保存原始證據。這是 v4 專用 finalization，
不假稱中間報告原本是 BLOCKED；`supersedes_report_ref` 仍專供 non-code BLOCKED，
兩種 relation 不可同時使用。相同 reviewer 的 `evidence_gap` 必須連到原完整 review；
換 reviewer 必須 `full_diff`。Code BLOCKED 不可在同 HEAD 被任一 relation 清除。
兩正式 PASS 必須綁同 coverage digest，第二輪另綁最新 PR run/attempt/tested SHA。

### 原始功能證據與短驗證

`scripts/validation_runner.py` 現行產生 `zhuyin-validation-run/2` execution 與
`zhuyin-validation-coverage/2` bundle，inventory 為 `zhuyin-test-groups/2`，
policy=`validation-flow/no-real-tk/1`，明列「真視窗驗證已依政策取消」，
只要求全部 active core；沒有 gui_manifest 或 GUI PASS。舊 `/1` reader
保留全部 raw 原件與原 GUI 判定，不允許改標新 policy、洗掉 failures 或 retry 額度。Gate 透過 `scripts/validation_evidence.py`
重新讀取所有 retained manifests、SHA-256 核對的 raw log／inventory／JUnit／events，
重新計算全部有效非視窗 identity coverage、環境一致性及適用原始歷史，不接受一個
`status: success` 或上傳成功就代替實際執行。每組原 run/attempt/job/tested SHA
保留在各自 manifest；不得把沿用 core 宣稱在後續 run 或 merge SHA 重跑。

coverage 的 exact candidate、ordered parents、tree、原 run/attempt 必須符合當前 PR CI。
Windows Python 3.13.0、完整實際 installed dependencies、default fd capture、必要
環境與 test inventory 必須可核對。必要 job `Grouped validation`、required summary
`Python 3.13` 及 `Verify required validation results` 必須成功；summary 只彙總真實
必要結果。失敗／skip／cancel／缺失不變成功。非本 event 的精確 conditional job
可 skip，不能把適用的非視窗 core 作為 skip 例外。所有適用 active case failure／missing／skip／error、artifact 損毀仍 fail closed。

`post-merge` 是真正 push CI 入口，從實際 merge SHA 找唯一已 merge PR、精確 feature
head 最新 PR run/attempt 與未過期 `validation-evidence-<run>-<attempt>` artifact，
保存原始 PR／run 清單／commit／artifact metadata、下載 archive 與 digest。
Zip 路徑不得逃出獨立 evidence 目錄、不得重複或覆寫。缺失、過期、損壞、最新 run
未完成或失敗時停止沿用。它核對 actual merge ordered parents/tree、實際 Python／
依賴／capture／環境與 PR，並執行 runtime integrity、非視窗 import／CLI smoke、
compile、push diff、clean-tree；不跑 full pytest 或真實 Tk。

短驗證每次使用新 UUID 目錄，保存開始紀錄、每條實際命令／退出碼／raw log，
最終結果先完整驗證後才能寫 success；失敗與中止資料保留，不覆寫旧結果。
任何 merge／依賴／資產／raw artifact 不等價時不能產生 COMPLETE，需具體列缺口。
Gate 還要求兩正式獨立 PASS、PR 功能證據、實際 merge 與 develop ancestry、該 merge
自己的 push CI 和 `Merge short validation` 全部成立。PR CI 不代替 push CI，
post-merge BLOCKED 保留已發生 merge/task/count，不重 merge、不直接 push develop。

```powershell
# 以下是工具入口；昂貴正式分組僅在使用者開始驗證後執行。
python scripts/validation_evidence.py verify-coverage tmp/validation-evidence/coverage.json
python scripts/validation_evidence.py verify-reuse <coverage.json> <short.json>
# GitHub actual develop push CI 使用，要求有效只讀 GITHUB_TOKEN／完整run環境。
python scripts/validation_evidence.py post-merge --evidence-root tmp/merge-evidence
# 隔離小型工具回歸，不建立真實 Tk、不呼叫 GitHub。
python -m pytest tests/test_pr_review_gate.py tests/test_validation_evidence.py -q
```

JSON / artifact hash 仍不是授權或 reviewer 身分簽章。協調者須核對原始遠端資料、
獨立報告、最新 findings 與保護規則；`validate` 成功僅代表 schema 有效。
此版本不提供任意環境等價 adapter；環境不一致即停止並交回具體補驗條件。

Windows 的 `runtime_asset_manifest.json` 可能依既有 Git checkout policy 呈 CRLF，
Git tree 的 text blob 則為 LF。短入口以 `git cat-file --filters <tree>:runtime_asset_manifest.json`
取得該已驗證 tree 的正式 checkout 表示，再和實體 manifest bytes 比較並保留兩份 raw
metadata；不對 runtime assets 自行 normalize、不重算 truth、不修改 manifest。
所有短命令完成後另重新核對 SHA/tree/parents 與實際環境，執行期漂移會保留 failure，
不能產生 success。

以下保存歷史契約原文；其中 window 義務已依 `validation-flow/no-real-tk/1` 取消，不再執行 configure／preflight／GUI／retry。原失敗、baseline、count、其他精確 adapter／授權／正式額度與所有 non-window 門檻保持。

## PR41 task-bound 第 4 輪例外

一般上限仍為三輪；僅 [限定授權摘要](VALIDATION_FLOW_CORRECTION4.md) 的原 task 可在 v3／v4 加入 optional `correction_exception`，沒有此欄位的舊 snapshots 保持原義。欄位存在時不可為 null，closed object 恰含 `schema`、`task_id`、`baseline`、`starting_head`、`head_branch`、`pr_number`、`extra_rounds`、`limit`、`scope`、`authorization_ref`、`authorization_sha256`。

除 `authorization_ref` 是可讀 saved raw authorization 路徑外，值必須逐一等於 `scripts/pr_review_gate.py` 的有限 `CORRECTION4_EXCEPTION`：schema `validation-flow-correction-exception/1`、task `validation-flow-reduction`、baseline `457707b4c4109c1b10a0da76d8f8a884aca10341`、starting HEAD `a0e647952ae5d973ea30130264294eee4e6982fa`、branch `chore/validation-flow-reduction`、PR `41`、extra `1`、limit `4`、授權 hash `29b2869d5e6584bab8efbf022f697c8ee7331b7e1241ef0c18df7f4a647ec7fc`，scope 必須為 `same-installation Tcl/Tk wiring, early hosted fd preflight, directly related tests/policy and task-bound gate exception`。Gate 實際讀取 ref bytes 核對 hash，要求原 PR41/base/branch 與完整連續前三輪，其第 3 輪 to HEAD 等於 starting HEAD；第 4 輪 from HEAD 亦必須相同。未知欄位、改 task／base／branch／PR／起點／hash、ref 缺失、任意 limit 5／extra 2 或 history 清空均拒絕。

例外只將該 task 的 correction schema／decision／post-merge handoff 上限一致改為四；不允許第五輪，不授權更改來源 scope，不清除歷史 code findings，不代替兩輪新 HEAD 完整審查。原 code／evidence／capability／contract 分類與 append-only 補審規則不變。協調者仍須核對原人類授權及所有操作 allowlist，gate 不創造 scope、測試、push 或 merge 授權。

v4 新接線的適用 PR job 必須有成功的 `Configure same-installation Tcl/Tk` 與 `Early hosted Tk preflight`；適用 push job 必須有成功的 configure。Missing／failed／skipped／cancelled 不能由 summary success 蓋掉。Hosted preflight 的 raw command／fd／60 秒／root／Combobox／Spinbox／實際資源路徑版本由 reviewer 核對，gate 的 step 字串不證明真實執行。

## PR41 task-bound 第 5 輪新授權

[第 5 輪限定摘要](VALIDATION_FLOW_CORRECTION5.md) 與原第四輪 snapshot/history 分開保留；schema `validation-flow-correction-exception/1` 仍維持第四輪原精確契約。只有新 saved 人類授權可在相同 optional `correction_exception` 選擇 `validation-flow-correction-exception/2`，closed fields 恰為 `CORRECTION5_EXCEPTION` 的所有欄位，加 `authorization_ref`、`previous_authorization_ref`。

Fixed 值為 task `validation-flow-reduction`、原 baseline/base `457707b4c4109c1b10a0da76d8f8a884aca10341`、starting HEAD `90b408794415509dd919a6d7a91c911f3724fd1f`、branch `chore/validation-flow-reduction`、PR41、extra `1`、limit `5`、authorization hash `46b2855d3fe7cd873ce1c4aaf9a93a8afa320b4f67e17aa14405f74d06f296d0`、previous authorization hash `29b2869d5e6584bab8efbf022f697c8ee7331b7e1241ef0c18df7f4a647ec7fc`、scope `independent early-preflight records, genuine small CLI regressions, directly related policy and task-bound fifth-round gate exception`。兩 ref 都實際讀原 bytes 核 hash，不能缺／改原第四輪授權。

保留連續第 1～4 輪，第三輪 to HEAD／第四輪 from HEAD 須為原 `a0e647952ae5d973ea30130264294eee4e6982fa`，第四輪 to HEAD 等於新 starting HEAD。Len4 時 current HEAD 必須精確90b40879，才可安排第5輪；len5 時第五輪 from HEAD 須90b40879，current HEAD 須等於其 to HEAD。其餘原 contiguous/new-commit schema檢查全部保留。未知 fields/schema、錯 task/base/branch/PR/current/start/scope/hash/ref、遺失history、limit6／extra2／boolean 均拒絕。5輪用完的 corrective／post-merge handoff 一律STOP，保留實際merge／count；不產生第6輪、不重設task。无 exception 的舊 v3/v4 schema仍一般三輪。

此adapter不清除原90b40879的codeBLOCKED；新HEAD兩輪均完整scope審查，原報告不能同HEAD supersede。合法 non-code supplements與原append-only關係保留。未使用的一次正式CI額度不增加，任何正式測試失敗保存並STOP；gate不創造新的scope/測試/push/merge授權。


## validation-flow-reduction 第六輪精確例外

[第 6 輪限定摘要](VALIDATION_FLOW_CORRECTION6.md) 保存這一次人類授權邊界。Optional `correction_exception` 可選 schema `validation-flow-correction-exception/3`，closed fields 恰為 `CORRECTION6_EXCEPTION` 加 `authorization_ref`、`previous_authorization_ref`、`fourth_authorization_ref`；fixed task/baseline/branch/PR/starting HEAD/scope/hashes/extra1/limit6 全部精確核對。三份原文實讀 bytes/hash；原第五輪 validator 重驗連續前五輪與第四／第五原授權。Len5 current 須起始811ff56f；len6 第六輪 from 須同起始、current 須該 to HEAD。原 contiguous/new-commit schema 不變。未知／跨 task／遺失歷史／改 hash/ref/scope／limit7 一律拒絕；用完6輪的 correction 及 post-merge handoff STOP、保留 merge/history/count，不產生第七輪。原第四／第五 adapter 仍保留當時上限；無 exception 仍一般三輪。Gate 不認證證據真實性、不創造操作授權，協調者仍須核對原授權與兩轮新 HEAD 完整審查。


## validation-flow-reduction 第七輪精確例外

[第7輪限定摘要](VALIDATION_FLOW_CORRECTION7.md) 保留本次人類授權。Optional `correction_exception` schema `validation-flow-correction-exception/4` closed fields 恰為 `CORRECTION7_EXCEPTION` 加 `authorization_ref`、`previous_authorization_ref`、`fifth_authorization_ref`、`fourth_authorization_ref`；固定task/baseline/branch/PR41/starting adcee81c/scope/hashes/extra1/limit7全核對。實讀新第7及第6/5/4原文bytes/hash，nested原第6validator重驗連續前六輪，不改任何舊adapter或通用3。Len6 current須精確起始；len7第7from須起始、current等於其to。原contiguous/newcommit檢查不變。未知fields、錯identity/ref/hash/scope/history/limit8均拒絕；7用完correction/postmerge code handoff STOP，保留merge/history/count，不产生第8。Gate不創造操作授權、來源scope或獨立審查證據。


## validation-flow-reduction 第八輪精確例外

[第8輪限定摘要](VALIDATION_FLOW_CORRECTION8.md) 保留新原人類授權及原第7 formal code BLOCKED。Optional `correction_exception` schema `validation-flow-correction-exception/5` closed fields 恰為 `CORRECTION8_EXCEPTION` 加 `authorization_ref`、`previous_authorization_ref`、`sixth_authorization_ref`、`fifth_authorization_ref`、`fourth_authorization_ref`；固定task/B/branch/PR41/starting d974ec5c/scope/hashes/extra1/limit8全核對。實讀第8原文hash `c156319030dc54f549e6479b369ef718fecdd494aee1817fee515e683032e05a` 及原7/6/5/4字節，nested原7validator重驗連續前七輪；原所有舊adapter与general3不改。Len7 current須起始；len8第8from须起始/current等於to。原contiguous/newcommit／未知closedfields检查不變；錯identity/ref/hash/history/scope或limit9拒絕；8用完correction与postmergecode handoff STOP，保留actualmerge/history/count，不产生9。Gate不創造來源scope、操作權限、審查PASS或同HEAD清code finding。


## PR43 原任務第五輪 closed gate

2026-10-08 明確採納限定補充（原件 SHA256 `5519099dd0d20aefe752bf7fec2cfa85998e0c70850617a8b7949a5a79219f10`），僅支援 `actual-gpt-bbox-export`／PR43／`codex/actual-gpt-bbox-export`／原 baseline `8eab9a1de34b59115658a2c7d3565adb347e59ab`。不改一般三輪或 PR41，不增加第六輪。

Optional `correction_exception` 恰含 `schema: pr43-correction-exception/1`、整數 `limit`（4 或 5）、`sources`、`integration_commits`、`corrective_head`。`sources` 的每個 key 必須恰等於程式 `PR43_SOURCES`，value 為可讀原件路徑，逐 bytes 核對固定 SHA256；limit5 另要求 `fifth_authorization`，固定原採納 hash `7bf99a4fa2ef4446fb8c0490af60ab0ca8014fe35ddf1025329d3ca6fdc5624b`。保留 canonical ledger、第四輪兩份人類授權及收據、原 two-round BLOCKED gate、舊二輪 projection proposal/adoption，以及本次完整 proposal/adoption，不修改原件。

Operational correction1 保留原區間；correction2 僅採已核准 a951→d593 累積區間，evidence_ref 指原 projection proposal；correction3 為 d593→7b5，evidence_ref 指本次 closed gate proposal；correction4 保留 7b5→6ce 與原 receipt reference。canonical 實際第二／三輪起點仍為 f4／ea944，原非corrective transitions、classification、parents 和全部 reports 留在 hash-bound ledger，不冒稱 operational 起點是 direct parent。四筆狀態只接受原 base／6ce、空 integration list、corrective_head=null，limit4 保留4/4；limit5 表示明確第五輪授權已讀，不表示候選已固定。

第五筆只能是 6ce→固定 final candidate；`integration_commits` 保存實際 first-parent 順序的 `{sha, object_ref}`（原始 `git cat-file commit` bytes，無換行轉換），gate 重算 Git commit object SHA1 並讀 ordered parents。每筆 ordinary merge 的 first parent 接前一筆；corrective_head 精確指定鏈中唯一第五輪實作 commit，可與 merge 合一，或在先前 integration 後是單 parent commit。第五輪之後只准已授權 ordinary merge，例如保留現有 6ce＋476 merge 作第五輪，再 merge 新 develop；其餘單 parent code commit 拒絕，不偷算成 noncorrective。不接受獨立無 integration 修正、octopus、缺件、錯序、改 bytes。最後一次 integration 的第二 parent 必須等於本次 PR base，末端必須等於 current／PR head；第五筆 evidence_ref 指該末端 object_ref。base 不硬鎖舊 develop 或未存在的未來 SHA，協調者仍須從真實 Git／遠端核對每筆 integration 的授權、scope 與來源，commit bytes 本身不認證其人類授權。PR 已 merge 時保留原 reviewed pair，current develop 可前進，仍走既有 actual merge 門檻。

本 adapter 只處理 count／有限表示與實際父代綁定，不清除 BLOCKED、不產生 PASS、不更動 supersedes／finalizes。第五輪用盡遇 code finding 必須 STOP；non-code 仍按原分類補證據或 STOP。新 HEAD／base 仍須兩輪完整適用獨立審查、正式 coverage／CI、保護規則及實際 merge 短驗證。
## PR42 的限定歷史並存 adapter

使用者明確採納 `cff-review-history/1`，只處理原 `cff-batch-fingerprint` 的
兩對同 scope `CODE_REVIEWED`／後續 evidence `BLOCKED`。v4 可選欄位
`cff_review_history_adapter` 恰含 `contract` 與 `sources`；後者以
`scripts/pr_review_gate.py` 的 `CFF_HISTORY_DIGESTS` 六個精確 keys 指向
保留的 snapshot、授權及四份原文。逐件實讀原 bytes 並核對有限 SHA allowlist；
原件放本機證據，不能為 CI 把私人報告／session／路徑公開加入 fixture。

適用 task／repository／branch／baseline／歷史 HEAD／report_ref 均封閉核對，
原八份 review 與兩輪 corrections 必須仍為原樣 ordered prefix。只在歷史
duplicate-key 判斷區分程式審查中間紀錄與後續證據阻擋；兩者原文、null relations、
findings 與 BLOCKED 效力不變，不偽造 supersedes／finalizes，不產生 PASS。
未知 task／future contract／損毀原件／cross-scope／第三份重複或 duplicate PASS
仍拒絕。原 code finding、兩輪審查、CI、教材驗收、三輪上限與合併門檻不放寬。
本修正屬原 CFF 第三輪，不建立另一本 task 或額外額度。

CI 僅使用明確 synthetic 技術資料測結構與故障路徑，替換 fixture digest allowlist
的測試不宣稱驗過原件；另有未替換真實 allowlist 拒絕假原件的測試。本機保存
六份真原件 hash 與實際 gate 核對證據。缺少 adapter 欄位仍按舊歧義判定拒絕。


## PR42 唯一第四輪：明示 R1 投影及 v2 並存

2026-10-08 人類採納原 decision-proposal 及 gate addendum，授權原 CFF
任務一次第四輪。採納原 bytes SHA256 為
`7bf99a4fa2ef4446fb8c0490af60ab0ca8014fe35ddf1025329d3ca6fdc5624b`。
`cff-review-history/1` 原六原件及通用三輪規則保持原義；不從舊授權推定新例外。

`cff-review-history/2` 的 closed `sources` 為原六 keys 加上
`CFF_HISTORY_V2_DIGESTS` 的七 keys：`snapshot3`、`authorization4`、
`code_md`、`code_json`、`blocked_md`、`blocked_json`、`projection`。
逐件實讀固定 hash 的原件，包括原不合 schema 的 R1 JSON；原 JSON 不可直接
放入 reviews。另存明示 projection 的唯一差異是 `ci=null`，其 SHA256 為
`2daef874f07da9421e3b7aabe2b989fb127c6d5dcb63d78c6af7755a14f0d331`。
Projection 並非原件，也不是 CI attestation；全域 round1 ci 必须 null 不變。
所有其他欄位必須逐欄等於原 JSON。原 CI 原件及報告敘述的格式錯誤仍保留。

僅增認固定 ec40c510 R1 CODE_REVIEWED／contract BLOCKED 這一對，
原 snapshot 全部 reviews 以該一欄投影為明示例外保持 ordered prefix，前三輪
corrections 也必須完全相同。原六原件、八 review／兩 corrections prefix
仍重驗。原 findings、reviewer、scope、null relations、BLOCKED 效力不變；
`_review` 仍選 BLOCKED，沒有 supersedes、finalizes 或 PASS。第三 duplicate、
非法 relation、改 hash／report／scope／identity、缺新授權、未知 future contract
仍拒絕。原件與 private sessions 只存本機；CI fixtures 明示 synthetic。

Optional `correction_exception` 可選 `cff-correction-exception/1`，closed
fields 恰為 `CFF_CORRECTION4_EXCEPTION` 加 `authorization_ref`。
固定 task/repository/branch/baseline、PR42、base
`476a9823e7ad512f5aa62e388a88fd2b15c357ed`、starting HEAD
`ec40c51057caa75983d2c3d6948d8b7d9ef8cd18`、extra1/limit4/scope/授權
hash 均精確核對，實讀原採納 bytes。必須搭配 v2 原件及前三輪 prefix。
Len3 current/PR HEAD 必須 starting HEAD；len4 的第四輪 from 必須 starting
HEAD、to 必須 current/PR HEAD，仍需 contiguous/new-commit schema。
開工 base 漂移拒絕；actual merge 後 current base 依既有 merge 語義核對。
第五輪、其他 task／PR43／PR41 例外冒用、變更 prefix 或授權均拒絕。

本例外只承載原任務同一第四輪，不自動解除原 BLOCKED、不增加正式 CI 或
retry 額度、不提供獨立審查或 merge 授權。第四輪新 HEAD 仍需兩輪完整適用
獨立審查與必要原始證據；4/4 後再有 confirmed code finding 必須 STOP。


## PR43 唯一第六輪與原 R1 CODE／code BLOCKED 並存

2026-10-09 人類採納原 proposal SHA256
`937717ab4c55591b1c4c611595c2cc6ad216f7444dc45e0bb445dc85d22248e7`
及 history addendum `7f92f232e35126811de3484b6da7b35e10589d04a6b6df28b7bc680101075c28`；
採納原件 hash `a9e605fab7cc52a6159a64528c6f8bd60cbde5989e48d29884aac7ece1a46ccf`。
僅限原 `actual-gpt-bbox-export`、PR43、`codex/actual-gpt-bbox-export`、
baseline `8eab9a1de34b59115658a2c7d3565adb347e59ab`。原 `/1` 仍只接受四／五輪。

Optional `pr43_review_history_adapter` 恰有 `contract=pr43-review-history/1`、
`sources`；來源 keys／原 bytes SHA256 完整列於 `PR43_SIXTH_SOURCES`，不得缺件或改 hash。
包含原第五失敗 snapshot、receipt、3ade raw object、四份報告及作者投影／聲明、
原 CI manifest/raw/JUnit/events、proposal/addendum/adoption。先重驗原 `/1` 的五輪，
固定 historical base `1417ac7ef08c7024f5940b3b9192abb4029405c1`、HEAD
`3ade65a9e94017c7eede5488508b9c91767f7874`、true fifth corrective
`f66d67a4d6c0cbf7895c456cc2791a5b8bdfa2b5` 及 raw tree/ordered parents。
全部六份 review（包括更早兩份）與五 corrections 必須保持完整 ordered prefix。
只有該 R1 CODE_REVIEWED／code BLOCKED 原 pair 可並存；null relations、findings、
report refs、作者 projection 原值不變。R2 原 CI tuple 不同，沒有額外 R2 例外。
舊第五 `/1` 保持 STOP5/5，CI37789520018/1 仍 failed、exit1、retry false。
不得新增同原 R1 scope 的第三份 report／PASS，即使變更 CI／coverage 也拒絕。

`correction_exception` 的 `/2` 恰有 `schema=pr43-correction-exception/2`、
`limit=6`、`sixth_corrective_head`、`integration_commits`、`candidate_tree`，並要求上述 adapter。
只接受原五筆後追加恰一筆 number6/from3ade/to final candidate；第五 to/evidence_ref 不變。
新鏈從 3ade 開始，唯一單 parent 節點必須是 sixth_corrective_head；其餘只能是已授權
ordinary two-parent develop merges。每筆恰有 `sha/object_ref/develop_observation_ref`，
實讀原 commit bytes 驗 Git object SHA、唯一合法 tree header、ordered parents。
修正節點的 observation 為 null；merge 節點引用 closed JSON
`repository/ref/sha/evidence_ref`，固定 repository、`refs/heads/develop`，sha 必須等於
第二 parent。evidence_ref 實讀當時 `git rev-parse origin/develop` 原始 SHA 行（LF／CRLF）；
協調者另保留命令、時間與遠端 API 原件，gate 不自稱驗證來源真實性。
最後 tree 必須等於 candidate_tree，HEAD/base 與當次 scope 相同，number6 evidence_ref
必須引用最後 raw object。未發生的 merge 不得預填 SHA。

新 HEAD 仍需兩輪完整適用獨立審查與必要 CI；原報告不提供新候選 PASS。
第六輪 code finding 保持 STOP6/6；額外單 parent 修正、第七輪、壞 hash/tree/parents、
刪改 prefix、未知 task/schema 或偽造 relations 均拒絕。一般／PR41／CFF 契約不變。
Synthetic 回歸僅替換 fixture pins／歷史 Git identities，另驗真實 allowlist 拒絕假來源；
不把 fixture 成功當正式原件、review 或 CI PASS。
