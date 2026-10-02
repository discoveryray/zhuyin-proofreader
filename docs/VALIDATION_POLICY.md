# Windows 分階段驗證政策與制度過渡

本政策是現行驗證策略的主要來源，分階段契約版本為 `validation-flow/1`，搭配明確採用的 gate v4。只適用採用後的新任務；未收尾任務維持原 task baseline 凍結的 gate、審查、CI、授權與修正計數，不自動升級。`validation-flow-reduction` 的驗收來自使用者事前採納的過渡契約，不由受審的新 gate 自行創造授權。

## 支援環境與來源（歷史快照）

2026-09-22 唯讀核對 `C:\Work\zhuyin-proofreader-phase4` 的 `啟動_單機注音校對.bat` 與 Python launcher：實際啟動命令使用 `py -3`，解析至 `C:\Users\discoveryray\AppData\Local\Programs\Python\Python313\python.exe`，Python **3.13.0 AMD64**、Windows 11、Tk 8.6。這是當次實測快照；日後啟動器或環境變更須重新確認，不從文件推測。

| 套件 | 實際 launcher 環境 | 隔離測試／CI |
|---|---|---|
| PyMuPDF | 1.26.7 | 1.26.7 |
| openpyxl | 3.1.5 | 3.1.5 |
| fonttools | 4.63.0 | 4.63.0 |
| python-docx | 未安裝 | 1.2.0，補齊既有測試需求 |
| pytest | 未安裝 | 9.1.1，測試工具 |

原專案 `.venv` 不在已查核 launch chain，雖同 Python 3.13.0，其 PyMuPDF 1.28.2／fonttools 4.64.0 不能冒充正式環境。本次於 managed worktree 建立獨立 `.venv`，以 `requirements-ci.txt` 固定直接依賴，不修改原專案、正式 interpreter 或其 `.venv`。間接依賴以當次 `pip freeze`／CI 安裝 logs 記錄，不聲稱完全重現原環境缺少的套件。`requirements.txt` 的使用者安裝範圍不受更動。

以下 Tcl 路徑處理僅記錄當時 `simplify-validation` 的已授權歷史操作，不授權新任務在失敗後更換 capture 或修補 interpreter。

本機隔離 `.venv` 首次 GUI 執行出現 `Can't find a usable init.tcl`。唯讀核對原安裝已有 `Python313/tcl/tcl8.6` 和 `Python313/tcl/tk8.6`，僅在驗證 process 設定 `TCL_LIBRARY`／`TK_LIBRARY` 指向這兩個實際目錄後，真實 Tk root 建立成功（8.6.14）。首次失敗／skip logs 保留；後續必要完整驗證採此 process-local 解法及 `PYTHONUTF8=1`／`PYTHONIOENCODING=utf-8`。不複製或偽造 Tcl assets，不修改原 Python、系統環境或 GUI skip 條件，也不將這個本機路徑硬寫到 GitHub runner。

原始證據保存在 task evidence 的 `original-launcher.bat`、`python-launcher.txt`、`actual-launch-python.json`、`original-venv-python.json`，由協調者於 PR evidence 區保存可回查內容。CI 例行只使用 Windows、Python 3.13.0；其他 Python 版本不再是新任務例行門檻。這不是宣布其他版本不相容，也不取消 fingerprint／schema／architecture compatibility tests。

## 開發與驗證兩階段

預設為 **開發模式**：修改工具、CI、測試及直接相關規範，保存本機 commit；每輪只做預估五分鐘內的語法／compile、diff 和最小相關回歸或隔離故障注入。不啟動 full pytest、整套 GUI、真實教材 PDF／Excel 整合驗證，不建立 PR，也不 push 會觸發昂貴 workflow 的更新。無 PR 分支只有核對不會觸發昂貴 workflow 且已獲授權才可備份 push。累積中間 commit 不要求逐個跑完整套件。可先做第一輪完整程式差異審查，交付狀態為「已修改、待正式驗證」。

只有使用者明確說 **「開始驗證」** 才進入驗證模式；不是排程，也不因時間、session 恢復或等待而自動切換。重新核對原 task ledger、HEAD、工作樹、base 與已有結果，固定最後候選後停止修改受測來源。另行開發使用隔離分支／checkout。依原授權接續唯一 draft PR、兩輪獨立審查、merge commit 和短驗證；「只測試、不合併」等較窄限制優先。

第一位 reviewer 審固定 baseline→HEAD 完整累積差異後，可記 `CODE_REVIEWED` 中間狀態。驗證模式且無 confirmed code blocker 才可建立 draft PR 取得 CI，不需先偽填正式 PASS。第二位 reviewer 可在 CI 執行期間審完整 PR／整合路徑。功能證據齊備後，兩位各自補核必要證據，才可產生正式 `REVIEW: PASS`。中間狀態、tests passing、CI 上傳成功均不允許 merge，也不能當正式 PASS。保持兩位獨立 reviewer，沒有第三輪正式 reviewer。

## 實際 collection、核心與 GUI

pytest 仍是唯一例行完整功能測試入口，正式覆蓋改成 **一次核心全集＋全部必要 GUI** 的兩個獨立 pytest process；不另跑混合 full pytest 或完整 unittest。本機定向測試 → PR 一次正式分組功能驗證 → 實際 merge SHA 的 post-merge 短檢查。第一版不選擇性省略 GUI、不用 xdist、不重寫大量測試或共用可變 DB fixture。

`scripts/test_entrypoint_audit.py` 保留 unittest／pytest identity 對照，collection 不代表測試已執行。分類從實際收集案例與真實 Tk 建立路徑產生，核對繼承、函式、helper；不能只看檔名／GUI 字樣。核心與 GUI 必須 disjoint，聯集精確等於全集；不明的動態 helper、fixture 或 discovery contract 必須停止釐清，不能猜成核心或省略。既有純邏輯確認、保存、binding、actual／expected 案例照常保留。測試數以當次清冊為準，top-level cases 與 subtests 分開記錄。

各組使用隔離 temp、JUnit 與原始 log，逐一核對每個必要 identity 恰好成功一次。unknown、missing、duplicate、skip、failure、error 都拒絕；缺失、舊 execution、其他 SHA、損毀 JUnit 不能替代。失敗後不得製造空白成功報告。只跑 collection／preflight 不代表 GUI 驗收。具體跨測試順序線索才使用最小 targeted order regression，不能因集合相等宣稱執行順序完全等價。

## GUI 預檢、重試與停止

正式 GUI 前以相同 interpreter、權限、cwd、必要環境與 default capture 執行預檢，核對 Python／實際依賴版本、evidence/temp 自建探測檔的建立寫讀刪除，以及 Tk root、ttk Combobox／Spinbox 建立更新銷毀。保存 Tcl/Tk 版本與實際載入路徑。失敗便不啟動 GUI 套件，但核心 process 可完成，已取得的核心證據繼續保存。預檢成功不等於 GUI 通過。

只有原始 log／JUnit 明確證明 Tk 初始化資源讀取或 runner 啟動問題，且無 confirmed code blocker 時，同候選、同設定 GUI 可再啟動一次独立 process。最多初次＋一次重試；額度依 task／候選歷史跨 job、run、attempt、session 計算，不因換目錄、空 commit 或重開 session 重設。先保存首次退出碼、log、JUnit、node ID／traceback，再記 retry relation。任意 TclError、assertion、資料／保存錯誤、未知根因不能自動當環境例外；重試仍失敗即停止必要 GUI 驗收。已有成功核心不重跑。歷史缺失／不可回取時 fail closed，不假設首次執行。

預算起始值為核心 90 分鐘、GUI 每次 20 分鐘、預檢 60 秒，開跑前核對，不是耗時保證。環境定向診斷最多 10 分鐘；不研究 Tk 根因、不安裝修復、不切換 sys capture、不下載／複製／混搭 Tcl/Tk、不修改系統 Python 或全域環境。超時保存紀錄，只終止自己啟動的程序，不重啟全套。環境限制不消耗 code corrective count；原始失敗保留，未知根因不得標已解決或 flaky。

## CI、證據與受控沿用

CI 保留 Windows Python **3.13.0** 及 required check **Python 3.13**。該 check 必須彙總真實必要 group／runtime／compile／diff／clean-tree 結果；缺 job、skip、cancel、timeout、缺 artifact 都不能成功。GUI 必須以 preflight 成功為前提，不能用 `!cancelled()` 繞過。上傳與 summary 成功不能蓋掉 pytest／verifier 的退出狀態。成功／失敗都上傳實際存在的驗證紀錄，排除教材、live DB、整個 tmp 與秘密。

每 execution 使用拒絕覆寫的唯一目錄，保存 SHA／tree、命令、實際 Python／完整安裝依賴、必要環境與 capture、起訖時間、退出碼、清冊、JUnit、raw log 與失敗事件。各組明列原 run／attempt／job／tested SHA；不得混版拼接。中止後保留已寫事件與失敗，不得以新執行遮掉較新的未解失敗。相同 dependency resolution 須可核對，單看 requirements 檔未變不足以证明。沒有適用成功證據即不得 merge。

PR 功能覆蓋沿用到 merge 必須同時證明：

1. 實際 merge 的兩個 ordered parents 恰為受審 base/head。
2. merge tree 等於通過的 PR integration tree，包含來源、tests、workflow、runtime assets。
3. Python、實際依賴、capture、命令及必要範圍一致，或有可回查的明確等價理由；不預設未知等價。
4. log／JUnit／清冊／metadata 的原始檔可回取且完整，hash 與 execution 綁定可驗。
5. 沒有較新的未解失敗或 confirmed code blocker；合法 GUI retry 保留失敗前件、relation 和跨 run 額度。
6. 各組來源分開記錄，不能把 PR tested SHA 改稱已在 actual merge SHA 重跑。

實際 merge SHA 仍必須有 `push` CI，執行 merge／parents／tree／PR run artifact 核對、實際依賴和資產核對、runtime integrity、非視窗基本 import／CLI smoke、compile、push diff、clean-tree。這是獨立的短檢查證據，不例行再跑 full pytest／真實視窗。任一等價性不足先停止沿用，列出缺口，由具體影響決定補驗範圍；不得自動擴成全套或宣告 COMPLETE。

每次 rerun／效能量測必須有修改影響、具體失敗線索或明確 gate；等待 CI、重開 session、整理報告不是理由。舊證據保留原 tested SHA、raw logs、環境與適用範圍，不能重新標記為新版本執行。相同 baseline/base/head/scope 的補審保留 append-only 原報告與 resolution relation；同 reviewer 可 evidence_gap，replacement reviewer 必須 full_diff。Confirmed code BLOCKED 只透過新 corrective commit 處理，同 task 最多三輪；新 HEAD 兩輪仍完整審查，non-code 證據補充不耗此計數。

## 可複製入口與執行限制

本機開發短回歸（使用已核對的 Python 3.13.0 環境；這不是正式核心／GUI驗收）：

```powershell
python -m pytest tests/test_pr_review_gate.py tests/test_validation_evidence.py tests/test_validation_runner.py tests/test_validation_local_history.py tests/test_ci_validation_workflow.py tests/test_test_entrypoint_audit.py -q -k "not test_pytest_only_tk_functions_are_mandatory_even_when_skipped and not test_cross_module_tk_helper_skip_cannot_escape_junit_gate"
python -m compileall -q scripts
New-Item -ItemType Directory -Force tmp | Out-Null
python scripts/test_entrypoint_audit.py collect-groups tmp/group-inventory.json
git diff --check
```

短回歸排除的兩個既有工具 fixture 會在子程序中建立真實 Tk；它們仍在正式 GUI 清冊中，不是免驗或永久 skip。`collect-groups` 只收集而不執行案例；不可把 collection 成功報成 GUI 成功。 Callable aliases 只沿可證明的區域別名、literal list／tuple、明確 callback 及標準 contextmanager 的唯一 nested-callable yield 追蹤；未知 lookup／容器仍 fail closed。同次 collection 逐 identity 保留未知診斷，任一未知即不產生成功清冊。任何 callee AST 形態（包括 instance Attribute、returned Call）及未有證明的 native／external provider 都不得默認 core；receiver／return 只接受有限來源證明，不執行 constructor、不由 annotation 或套件名稱猜純邏輯。四個限定動態 alias 契約僅處理 fixture_path_alias 的 kernel32.GetShortPathNameW、MigrationInspectionTests 的 GlobalExactGlyphRepository.load_snapshot，AsyncReviewSaveTests 的 ReviewSaveService.save_event／已證 busy guard 的 operation loop，以及 test_bundle_actual_late_marker_prevents_commit 的精確 mock forward；精確 path／qualname／callsite／caller與初始化來源 hash 失配即停止，兩個 instance adapter 仍遞迴核對實際 target；save adapter 另綁定 headless_app、QueuedRoot、reload_records 與 _invalidate_save_snapshot 的初始化來源。Literal dictionary 分支與固定 key 寫入採有限聯集，未知 key、opaque 逃逸或未證明的 splat callback 仍拒絕；forward adapter 必須綁定真正入口／callsite，遞迴檢查 actual callback targets；busy loop 的 caller、等待／release、guards 與所有 method entry／closure 均受來源綁定。明確 positional callback 不被未知 splat 覆蓋。修改這些來源必須重新獨立審查 adapter，不能自動重算 hash 或推廣到其他 native API。

本機分組是可選的昂貴驗證，也只能在「開始驗證」後按具體驗證需求執行。先以原 task、baseline、原始需求 reference bootstrap 一次 canonical ledger；`git-common-dir` 的共同 repository 旁 `tmp/validation-task-ledgers/<task-id>` 是持久來源，跨 worktree、session 或 `--evidence-root` 共用，不因輸出目錄改變重設額度。缺失／損毀 ledger、未知 task、已封存 handoff 均停止，不能自行重新初始化。 Local ledger／handoff v2 另保存固定 store identity、兩份 store marker 及獨立 append-only execution declarations；每次啟動前先 durable 寫入宣告。read／export／import 必須證明全部宣告與 execution 目錄一一對應，且原始 manifest／log 等檔案完整；整個 store、單次 execution 或宣告遺失都不能當成空 history。真正空 handoff 仍包含 ledger 和兩份 marker；舊 v1 不自動遷移或追認。以下為本 task 的具體命令：

```powershell
python scripts/validation_runner.py bootstrap-local --task-id validation-flow-reduction --source-ref tmp/pr-review-automation/validation-flow-reduction/original-request.txt --baseline 457707b4c4109c1b10a0da76d8f8a884aca10341
# 只有當下驗證需求確實包含本機分組時執行；預設仍用PR一次正式功能覆蓋。
python scripts/validation_runner.py run --group core --mode validation --task-id validation-flow-reduction --evidence-root tmp/local-validation-results
python scripts/validation_runner.py run --group gui --mode validation --task-id validation-flow-reduction --evidence-root tmp/local-validation-results
python scripts/validation_runner.py export-local-history --task-id validation-flow-reduction --output tmp/local-validation-handoff.json
```

直接進 PR 的路徑也須 bootstrap 後 export 真正的空 history，但不跑上述本機 core／GUI。空 history archive 仍含原 task/baseline/source_ref，不是預填成功或臆測零次。Export 在發布前 durable seal，之後禁止任何本機新嘗試；換輸出目錄或候選微變不能解封。協調者將原 export JSON 原樣放入唯一 PR description 的下列資料區塊，CI 由只讀 PR API 回取並校驗 hash／ZIP allowlist／完整原始 manifests：

```text
<!-- validation-local-history
{原樣的 local-validation-handoff.json JSON}
-->
```

不是程式指令，不執行 block 內容；缺少或多個 block、損毀、跨 task、不完整失敗紀錄均拒絕。壓縮資料的 JSON 上限 40,000 bytes、展開上限 16 MiB，超限保留原始 evidence 並 STOP，交回可回取原始 artifact 的能力缺口，不丟棄 history。只匯出驗證檔案 allowlist，不含整個 tmp、basetemp、教材或 DB。

本機與 PR 的 tested SHA 不同時不默認成功等價；本機 raw records可參與相同 task/tree 的失敗／retry budget 核對，正式 PR selected core／GUI仍必須是可驗證的 PR 執行。若本機必要 GUI 已成功而再次在 PR 初次執行會違反一次政策，初版停止並交回明確證據適用採納缺口，不自動把 local success 當 PR PASS，也不再跑一次洗掉歷史。這是沿用 adapter 的限制，沒有新增免驗規則。

以下是 PR CI 的正式分組入口，必須先收到「開始驗證」、使用乾淨固定來源、`requirements-ci-lock.txt` 同一解析版本，以及真實 CI context／完整遠端歷史。不能自行填 CI 身分或以新目錄假設零次重試。任一 history 欠缺先停止，保留已得證據。

```powershell
python scripts/validation_runner.py restore-history --evidence-root tmp/validation-evidence
python scripts/validation_runner.py run --group core --mode validation --evidence-root tmp/validation-evidence
python scripts/validation_runner.py run --group gui --mode validation --evidence-root tmp/validation-evidence
python scripts/validation_runner.py aggregate --evidence-root tmp/validation-evidence --output tmp/validation-evidence/coverage.json
python scripts/validation_evidence.py verify-coverage tmp/validation-evidence/coverage.json
```

初版以同一 CI job 的獨立 processes依序執行核心／GUI；尚未宣稱跨 job 並行隔離。必要 GUI 只在內建 preflight 成功後啟動。CI rerun 必須先回取同 task branch 的原始歷史，成功 core 沿用、GUI 額度沿用；不無條件重跑兩組。實際 develop push 使用 `python scripts/validation_evidence.py post-merge --evidence-root tmp/validation-evidence`，缺正確 PR provenance的 main push／workflow_dispatch 不會產生新流程成功或 COMPLETE。

## validation-flow-reduction 明確過渡驗收

Task `validation-flow-reduction`，branch `chore/validation-flow-reduction`，baseline `457707b4c4109c1b10a0da76d8f8a884aca10341`。使用者事前指定：本機短工具 regression／故障注入 → 第一輪完整程式 review → 等「開始驗證」→ draft PR → Windows Python 3.13.0 一次核心全集＋全部必要 GUI → 第二輪可與 CI 重疊 → 兩位補核正式 PASS → 必要 CI／保護規則通過後 merge commit → actual merge 短驗證成功才 COMPLETE。

此明確 transition 優先於舊文件直接衝突的本機 full 前置、第一輪正式 PASS 前不能建 PR、post-merge full；其他安全規則保留。保存 baseline 舊 gate 原始判定和使用者原指令，不偽填舊 gate PASS；新 gate 自身也是受審實作。開發模式只交付本機 commit 與第一輪程式 review，狀態「已修改、待正式驗證」，不聲稱 COMPLETE。

PR40 已 merge 的 `457707b4c4109c1b10a0da76d8f8a884aca10341` 允許作本 task 起點，但保留原 task/baseline、0/3 與 run `37000834763` 的兩次失敗（各 1127 passed／1 skipped；strict GUI verifier 拒絕）。根因未确认；不觸發第三次完整重跑、不重新 merge、不據此推斷 P2 產品缺陷。新流程完成後只唯讀整理最小收尾方案；沒有另行明確採納限定遷移，不將 PR40 宣告 COMPLETE。PR39 的 COMPLETE／6/6 歷史保留。

## 歷史 simplify-validation transition（凍結契約）

Task branch：`codex/simplify-validation`；固定 baseline：`1593e7af65596d320b4427f1b15bb2bc0bdc949c`。本次只授權交付未合併 PR，gate authorization operations 為 implement／delegate／test／commit／push／pr，**排除 merge**。

協調者在修改前逐檔保存 baseline 的 AGENTS、完整規範、角色、CI、gate v2 與 hash manifest，存於 `tmp/pr-review-automation/simplify-validation/baseline-contract/`；Git baseline 也可復原同份 bytes。兩輪真實審查、補審與驗收使用凍結 v2 契約，不能用新 v3 的 evidence_gap 放行自身。

CI 僅在 `pull_request`、上述精確 head branch 且 base SHA 恰等於固定 baseline 時，保留 Python 3.12／3.13 及 full unittest／full pytest，維持舊 job／step 名稱供舊 gate 核對。3.13 實際固定 3.13.0；新增 coverage／GUI steps 也必須成功。此有限 transition 不適用其他 branch 或不同 base，不是永久雙矩陣。若本次 base 前進致舊 CI 契約不具證據，STOP／REFRESH_EVIDENCE 交回具體限制，不自行改 baseline、豁免舊驗收或以新 gate 宣稱 PASS。

新 v3 不默認接受 v1／v2 snapshot。其他尚未完成的舊任務維持凍結契約；若未來 CI 變更讓舊必要證據無法取得，應明確 BLOCKED 並交回契約／能力解決條件，不能靜默套用本任務 transition。

外部採用限制：2026-09-22 從 GitHub `GET /repos/discoveryray/zhuyin-proofreader/rules/branches/develop` 核對，ruleset `21894326` 的 strict required status checks 仍要求 `Python 3.12`、`Python 3.13`，並要求 review threads resolution、只允許 merge commit。原始 JSON 保存在 task evidence `develop-rules-before-pr.json`。本次有限雙 job transition 可提供這兩個 context；未來例行單 3.13 的新任務仍會被外部 `Python 3.12` 必要 context 阻擋。Gate 必須保持 `protection_satisfied=false` 並 STOP，不能用 dummy 3.12 成功、改寫證據或改 CI 名稱冒充真正舊 gate。使用者未授權本次修改 GitHub 保護規則，因此只交付此採用限制；要全面啟用新單版本制度，需另外取得明確保護規則遷移授權及驗證，或交回具體 contract／capability resolution，不能宣稱外部門檻已同步。

本次本機須依舊制度完成 full unittest + full pytest、runtime、GUI、compile、diff；3.12 證據由 transition CI 提供。實測命令、耗時及限制保存在 task evidence 並由 PR 回報；未實測的節省不估算或捏造。此文件不是測試 PASS，兩輪審查與 CI 仍須取得原始證據。

## PR29 第五輪限定接續

使用者於 2026-09-25 明確採納 [PR29 接續附約](PR29_CONTINUATION_CONTRACT.md)。僅原 PR29、同 repository/head repository、`codex/review-confirm-responsive`、base `develop` SHA `502414b3b38e004a6d8d9cb693cf21b65148765a`，保留 Windows Python 3.12／3.13、full unittest＋full pytest，另保留本政策的 test identity audit／GUI execution verifier。3.13 固定3.13.0；本機亦依凍結契約取得新候選完整雙入口、Tk、runtime、compile、diff證據。

僅上述精確 PR29 條件的 CI job 設 `PYTEST_ADDOPTS=--capture=sys`，供 runtime pytest、清冊收集的 pytest 子程序及 full pytest 使用；PR30、其他 PR、push／dispatch 不套用此 capture 例外。保留全部測試、JUnit GUI execution verifier 與失敗／跳過證據，不以 capture 模式宣稱 Tcl 根因已確定。對照結果及限制見接續附約；新候選仍須取得自己的完整本機與適用 CI 證據。

一般 gate 三輪限制不變；限定 adapter 保留已知四輪再追加第五輪，採納原文及歷史缺件有可回取副本。不把新任務單版本規則套到舊任務，不追認缺失舊授權／PASS。未授權 merge/release，不新增 post-merge push例外；其他任務及不同base不適用。

## 可同步文件

`docs/CHATGPT_PROJECT_INSTRUCTIONS.md` 是可完整貼入的專案指令；`docs/V58_MASTER_DEVELOPMENT_REVIEW_PLAN_v1.1.md` 是更新後完整規範副本（版本 1.4，保留檔名）。同步時兩者與本政策／gate v4 同版保存；第 3～4 節安全契約不變。只到 PR 的交付須清楚標示未合併，不宣稱已完成 merge／post-merge。
