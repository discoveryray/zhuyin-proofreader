# Windows 無視窗分階段驗證政策

本政策是現行驗證策略的主要來源，現行版本為 `validation-flow/no-real-tk/1`，搭配明確採用的 gate v4。2026-10-07 使用者明確採納完全取消真視窗驗證；本次 `no-real-tk-validation` 本身及後續驗證均適用。尚未結案的既有任務只遷移真視窗義務，其餘 task／baseline／授權／修正計數／凍結契約與全部失敗歷史保留。政策修改完成不會解除其他 code／evidence blocker，也不會自動使舊功能任務 COMPLETE。

取消範圍涵蓋本機短測、完整 pytest／unittest、fixture／import／子程序、PR、main／develop push、legacy branch、preflight、post-merge；立即隱藏的 Tk root 或虛擬顯示器中的視窗同樣取消。Tcl/Tk configure、初始化能力門檻、真視窗 retry／supplement 均退場，不執行 fd／sys 對照或修補 Tcl/Tk。產品 GUI、啟動器與正常使用所需 Tk 依賴保持原功能。

保留按鈕命令組裝、參數傳遞、背景狀態、成功／失敗回報，以及 actual／expected、identity、fingerprint、匯入／匯出、交易、保存與恢復的有效非視窗驗證。簡單替身可以隔離元件，但必須執行受測行為，不能 mock 受測功能為成功。

新 run／coverage／inventory 分別為 `zhuyin-validation-run/2`、`zhuyin-validation-coverage/2`、`zhuyin-test-groups/2`，明列 policy 與「真視窗驗證已依政策取消」。新成功證據不要求 GUI manifest，不產生 GUI PASS／空成功報告，不宣稱完整介面覆蓋。舊 `/1`、`validation-flow/1` 原件及原判定只讀保留；原 Tk 失敗不是通過、skip-success 或根因已修復，原 retry 額度不重設。

Gate v3／v4 snapshot 以明確 optional `validation_policy: "validation-flow/no-real-tk/1"` 選取本次採納；缺少旗標仍維持舊判定。新 v4 必須使用 policy 相符的 `/2` coverage。旗標不創造授權、不新增正式驗證額度、不變更其他凍結契約，詳見 [gate 契約](PR_REVIEW_GATE.md)。

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

預設 **開發模式**：修改範圍內工具、CI、測試及指令，保存本機 commit；每輪只做預估五分鐘內的必要定向 regression／隔離故障注入、compile／diff。不啟動有效非視窗全集、教材 PDF／Excel 驗收，不建立 PR 或 push 觸發昂貴 CI。第一輪固定 baseline→HEAD 完整 cumulative review 可記 `CODE_REVIEWED`，交付「已修改、待正式驗證」，不是正式 PASS。

只有使用者明確 **「開始驗證」** 才固定候選並進入驗證模式，不因等待、session 恢復或政策修改自動新增正式時段。若已有明確安排的正式驗證時段，依原安排接續一次無視窗完整驗證。重新核對 task ledger、HEAD、working tree、base 與已有結果；第一輪無 confirmed code blocker 才查找／建立唯一 draft PR。第二輪可與 CI 重疊，兩位獨立 reviewer 各自核對必要原始證據後才正式 PASS。較窄授權、三輪 corrective 限制、完整補審與 merge commit 規則保留。

## 有效 collection 與執行對帳

pytest 是唯一例行完整功能入口，一次 `core` 包含全部有效非視窗 cases。沿用既有有限、精確 case identity 登記與來源審查；逐項退役純視窗／layout／native widget cases，不依 GUI 檔名整批排除。混合案例必要的資料及流程保障移到真正執行邏輯的非視窗案例。Fixture／import／子程序的 Tk 路徑同樣退出；不能先嘗試 Tk 再 skip。

`scripts/test_entrypoint_audit.py` 與 `scripts/test_group_registry.json` 核對當次 collection：明確 active core IDs 與 `cancelled_window_ids` 分開，沒有 class wildcard、unknown-default-core 或檔名推定。來源清單及 SHA256 綁定 repository 根目錄、tests 與 scripts `.py`（source review 只正規化 CRLF→LF；physical bytes/hash 另存 evidence）。來源增刪或 hash 改變須明確重新來源審查，不自動刷 hash。有限 Tk 正向防漏核對 case、MRO、fixture closure、loaded helper／alias；已證 Tk 不能納入 active core。不新增一般 callee／provider framework。

Default pytest collection 與 unittest discovery 均不執行退役的真視窗 cases。`collect`／`collect-groups` 只是清冊；正式 core 或保留的歷史 full pytest 使用實際 JUnit，必要 active IDs 必須恰好成功一次。Unknown、missing、duplicate、skip、failure、error、損毀或錯 SHA／舊 execution 全部拒絕；上傳成功或 collection 成功不等於測試成功。`verify-active` 核對 main／legacy full pytest 的 active inventory；`verify-gui` 與真視窗 launcher 不再是有效自動化入口。

## CI、證據與受控沿用

一般 PR：restore-history → core → aggregate → verify-coverage，另保留 runtime integrity、compile、PR diff 與 clean-tree。一般 `refs/heads/develop` push：真正 merge SHA 的短驗證。`refs/heads/main` push：原 Python job 的完整有效非視窗 pytest、active execution verifier、runtime、compile、push diff、clean，不追加 unittest。PR29／simplify 精確 legacy 條件保留雙 Python、原 full unittest／pytest 入口及 capture 窄例外，只取消真視窗義務；unsupported workflow_dispatch 仍拒絕成功。

Windows **Python 3.13.0** 及 required check **Python 3.13** 保留。Summary 核對適用 job 的真實結果及 main／legacy 全部必要 step outcomes；非視窗 failure、missing、skip、cancel、timeout、缺 artifact 均不能綠。Nonapplicable conditional job 原樣 skipped，不偽造 GUI 或 Python 3.12 成功。上傳實際 logs／JUnit／inventory／manifest／events，不能蓋掉測試或 verifier 退出狀態，不上傳教材、live DB、整個 tmp 或秘密。

每 execution 用拒絕覆寫唯一目錄，保存 policy／SHA／tree、命令、實際 Python／完整依賴、必要環境／capture、時間／退出碼、清冊、raw log／JUnit／events。新旧政策不得混版拼成成功；保留原 run／attempt／job／tested SHA。舊 GUI 失敗與原 retry relation／額度供歷史查核，不要求新 Tk 補驗，不清除其他較新未解失敗或 confirmed blocker。

PR coverage 沿用到 merge 必須證明 actual ordered parents 等於 reviewed base/head、merge tree 等於 PR integration tree，來源／tests／workflow／runtime assets 全部一致；實際 Python／解析依賴／capture／命令／必要非視窗範圍相符或有明確可驗等價理由，raw artifacts 完整且 hash/execution 綁定可核對。不能把 PR tested SHA 改稱在 merge SHA 重跑。

實際 merge SHA 必須另取得 develop `push` CI：merge／parents／tree／PR artifact、實際依賴／資產、runtime integrity、非視窗 import／CLI smoke、compile、push diff、clean-tree。PR CI 不代替短 CI，不例行 post-merge full。等價性不足或必要證據缺失先停止沿用，具體列缺口，不自動擴成全套或宣告 COMPLETE。

重跑須有修改影響、具體失敗線索或明確 gate；等待 CI、重開 session、整理報告不是理由。原 report／BLOCKED／correction count 與 append-only supersedes／finalizes relation 保留；non-code 補證據不新增空 commit、不消耗 code corrective 次數。

## 可複製入口與執行限制

開發模式按具體改動選最小短回歸，以下僅為本政策 routing 的定向範例；不能把工具 fixture 宣稱正式核心／review PASS。使用實際 Python 版本並揭露與正式 3.13.0 的差異。

```powershell
python -m pytest tests/test_ci_validation_workflow.py -q
python -m compileall -q scripts
python scripts/test_entrypoint_audit.py collect-groups tmp/group-inventory.json
git diff --check
```

本機完整 core 仍屬可選昂貴驗證，只有「開始驗證」後按具體需求執行。Canonical ledger 必須以原 task/baseline/source_ref bootstrap 一次；`git-common-dir` 旁 `tmp/validation-task-ledgers/<task-id>` 跨 worktree/session/output 共用。缺失／損毀 ledger、未知 task 或 sealed handoff 停止，不能自行重新初始化。Local ledger／handoff 保留 store identity、兩份 marker、append-only declarations；啟動前 durable 宣告，read/export/import 核對宣告與 execution 一一對應及完整原 artifacts。舊 v1 不自動升級、追認或清空 history。

```powershell
python scripts/validation_runner.py bootstrap-local --task-id no-real-tk-validation --source-ref tmp/pr-review-automation/no-real-tk-validation/original-request.md --baseline 8eab9a1de34b59115658a2c7d3565adb347e59ab
# 只在明確正式需求包含本機 core 時執行；預設 PR 一次正式全集。
python scripts/validation_runner.py run --group core --mode validation --task-id no-real-tk-validation --evidence-root tmp/local-validation-results
python scripts/validation_runner.py export-local-history --task-id no-real-tk-validation --output tmp/local-validation-handoff.json
```

直接 PR 路徑 bootstrap 後 export 真正空 history，仍含 task/baseline/source_ref／ledger／marker，不能預填成功或臆測零次。Export durable seal 後禁止新本機嘗試，換目錄或候選不能解封；原樣放入唯一 PR description 資料區塊：

```text
<!-- validation-local-history
{原樣的 local-validation-handoff.json JSON}
-->
```

CI 只讀 PR API 回取，核對 hash／ZIP allowlist／完整 manifests。缺少、多個、損毀、跨 task、不完整歷史拒絕；JSON 上限 40,000 bytes、展開 16 MiB，超限保留 evidence 並 STOP，不丟 history。Archive 只含 allowlist 驗證 artifacts，不含 temp／教材／DB。本機結果不默認等於正式 PR PASS，原失敗照常參與適用歷史核對。

正式 PR 入口要求「開始驗證」、乾淨固定來源、`requirements-ci-lock.txt` 同一解析版本、真實 CI context 及完整遠端歷史；不能自行填 CI 身分或用新目錄假設零次：

```powershell
python scripts/validation_runner.py restore-history --evidence-root tmp/validation-evidence
python scripts/validation_runner.py run --group core --mode validation --evidence-root tmp/validation-evidence
python scripts/validation_runner.py aggregate --evidence-root tmp/validation-evidence --output tmp/validation-evidence/coverage.json
python scripts/validation_evidence.py verify-coverage tmp/validation-evidence/coverage.json
```

實際 develop push 使用 `python scripts/validation_evidence.py post-merge --evidence-root tmp/validation-evidence`；沒有正確 PR provenance 的 main push／dispatch 不產生新流程 COMPLETE。所有 event/ref 自動入口均無 Tcl/Tk configure、preflight、GUI group 或 GUI verifier。

## no-real-tk-validation 明確採納與外部設定

Task `no-real-tk-validation`，branch `codex/no-real-tk-validation`，固定 baseline/base `8eab9a1de34b59115658a2c7d3565adb347e59ab`。原始 2026-10-07 採納保存於 task evidence `original-request.md`。先保存既有 bbox `4/4` 與 CFF `2/3` 的審查／失敗／BLOCKED，不修改其 worktree、baseline、count 或判定。本次短回歸→第一輪 cumulative CODE_REVIEWED→原正式時段或「開始驗證」→一次無視窗全集→第二輪及兩正式 PASS→適用 CI／保護規則→merge commit→actual 短驗證。未安排正式時段時開發模式停在「已修改、待正式驗證」，不自行開始昂貴驗證。

開工唯讀核對 ruleset `21894326` 原件位於本 task evidence `develop-ruleset.json`：strict required check 為 `Python 3.13`，仍要求 thread resolution、merge commit only、no bypass，沒有專屬 GUI check。本次不修改外部保護；不能把歷史雙 required check 快照當成當前設定。

本政策不重跑 108 份教材、不執行 56 檔正式還原，不寫正式教材／session／live DB，不另造一般測試框架。交付明列退役 cases、仍由非視窗測試保護的行為及未再驗證的版面／原生元件／互動視窗行為。其他舊功能任務須分別核對剩餘門檻，不能自動 COMPLETE。

## 歷史契約原文與限定窗口義務遷移

下列 `validation-flow/1`／PR29／PR41／simplify 文字保留歷史原義與原判定。2026-10-07 新採納僅使其中要求 configure／Tk preflight／GUI case／GUI retry／GUI supplement 的未來執行義務退場；原失敗、原 retry 額度、baseline、雙 Python／full 入口、capture 窄例外、修正次數、其他授權與所有 non-window 安全門檻不變。這些歷史 GUI 命令不得再複製執行，不將舊證據改標 `/2`，不追認 PASS。

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

## PR41 第 4 輪限定接續

[限定授權摘要](VALIDATION_FLOW_CORRECTION4.md) 只適用原 task／baseline／starting HEAD／branch／PR41 及原始授權 hash，保留前三輪，以一次追加至 `4/4`。此次新候選一 core＋一 GUI；early preflight 或正式 CI 失敗即 STOP，不自動重試。一般三輪與既有 retry allowlist 不變，不沿用舊 HEAD core。

## PR41 第 5 輪限定接續

[第 5 輪原 task 授權摘要](VALIDATION_FLOW_CORRECTION5.md) 追加一輪至 `5/5`，保留第 4 輪原文與全部歷史。只修正成功 early preflight 與 group start 紀錄混用的 F1，先取得極小真 CLI producer／history／core 串接及 fail-closed 回歸；未使用的一次 hosted core＋GUI 額度不增加，其他 task 仍三輪，沒有 sixth round／自動 CI retry。

## PR41 第 7 輪限定接續

[第7輪原task授權摘要](VALIDATION_FLOW_CORRECTION7.md) 只限main push routing及實際summary直接回歸，保留前六輪，追加至7/7。先有限定向workflow事件資料／真PowerShell summary，不啟本機全套／GUI或真mainpush；原first完整累積review後新候選一次正式CI。舊成功不冒充新HEAD、CI失敗STOP不retry、仍需兩輪完整適用PASS才能merge/actual短驗證。其他task一般3輪及所有歷史adapter不變。

## PR42 精確原 producer 失敗讀取例外

2026-10-08 人類明確採納兩份 CFF 限定提案及唯一第四輪，採納原 bytes
SHA256 `7bf99a4fa2ef4446fb8c0490af60ab0ca8014fe35ddf1025329d3ca6fdc5624b`。
`cff-legacy-failure-read/1` 僅允許新無視窗政策的歷史讀取保留一份原 producer
合法產生的失敗，不以後來 classifier 追認原 eligibility。唯一 manifest SHA256
為 `9b0635cbcb149cf4c272c0c51d28478d66c07d355ffc57117cb0d90b32c0793a`；
它綁定完整 artifact map、原 task/context、PR42、run37293413665/attempt1、
execution `0a56d940d31c4b48865cbda948494a7a`、原 baseline、feature、producer
candidate/parents/tree、命令及原 started。原 producer 為
`4453a35dc7d66b9d9a2f0cca43f49087b4a4cdbc`，runner Git blob
`3d33f83055c2c1cff366a1cee415cb81ef609c8d`，blob SHA256
`30452368f477dfafb093b0c8d93f3b340ce57a3b80a785ae75f4caa53bab75e5`。
這是凍結原件例外，不執行下載的歷史 source，也不新增通用版本 dispatcher。

`read_verified_manifest` 預設仍 strict；只有 `history_policy` 等於現行政策且
`task_id=cff-batch-fingerprint` 才可接受唯一原件。完整常規欄位、時間、command、
retry key、inventory、start/finish 和每份 raw artifact hash 驗證仍先執行；
相同 execution 在新政策下改 bytes／身份／eligibility 即拒絕。
`restore_history` 另核 repository、branch、PR42 與 handoff task；history
拒絕跨 task 混入，`validate_history_context`、PR core 前的 history 及新 `/2`
aggregate 傳遞選取。`verify_coverage` 依真 `/2` policy 重算該 aggregate；舊 `/1`
aggregate、未 opt-in reader、本機 ledger 不取得例外。只有原 failed history
或舊 `/1` core 仍因缺當前 `/2` core 而拒絕 coverage。

返回原字典仍是 `failed / exit_code=1 / retry_eligible=false / retry_of=null`，
不得修改 bytes、重啟 Tk、使用新 retry、充當新 coverage 或 PASS。同 execution
跨 bundle 原样保留且只計一次；全部其他歷史、unfinished、raw corruption、
同候選 code/core failure 仍核對。原 run37763361887/attempt1 restore-history
失敗永久保留，不能因這次 reader 修正宣稱該次已通過。原未知 Tk 根因未解決。
本例外與 [精確 gate 第四輪規則](PR_REVIEW_GATE.md) 同屬原任務唯一 4/4；
新 HEAD 必須兩輪完整獨立審查，正式 CI、coverage、merge 仍有原門檻。
