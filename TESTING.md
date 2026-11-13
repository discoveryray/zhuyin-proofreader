# Testing

開發、審查與驗證版本先由[唯一程序入口](docs/DEVELOPMENT_REVIEW.md#applicability)選定；本文件提供命令、依賴及 artifact 操作。PR #71 未啟用首版已完成驗證整合，本啟用候選仍未生效。只有[生效及新 task 採納契約](docs/DEVELOPMENT_REVIEW.md#activation-contract)成立的 `/6` task 才適用新分級流程，舊 `/5` 永 pending，其餘 task 及本啟用任務保持舊規。全部 PR full、actual merge short 不因 docs 分類豁免；本輪只做已授權定向檢查，不開始 full 或遠端 CI。

現行驗證政策是 [`validation-flow/current-evidence/1`](docs/VALIDATION_POLICY.md)，永久取消 CI 歷史自動重播前置門檻，適用本次未結案任務及後續一般工作。舊原件/失敗/判定/計數保持，歷史工具退出正常自動路徑，不以舊成功代替當前測試。所有自動化入口繼續取消真 Tk，包括 hidden root、虛擬顯示器、fixture/import/子程序及 configure/preflight/retry；產品 GUI/Tk/正式 .bat 保留。當前 actual/expected、identity、封印、資料、交易、保存/恢復安全測試仍全部必要。

## 依賴與隔離環境

| 用途 | 來源與入口 |
| --- | --- |
| 一般開發 | `requirements-dev.txt` 引用 `requirements.txt` 並加入 pytest；未凍結完整解析，不能據此宣稱正式 CI 環境相同。 |
| 正式測試／CI | `requirements-ci-lock.txt` 固定直接、間接測試依賴及 pip；`requirements-ci.txt` 是直接依賴來源，不能代替完整 lock。實際 Windows Python 必須是 3.13.0。 |
| v5.8.0 使用者發布包 | `requirements-release-lock.txt` 固定 runtime 直接、間接依賴及 pip，透過現有 `安裝依賴.bat` 安裝；不含 pytest 等測試工具。正式啟動沿用 `啟動_單機注音校對.bat`。 |

原專案 `.venv` 不等於 launcher 或正式 CI 環境。需要新的隔離 CI 測試環境時，在 repository root 使用以下入口；先核對實際 interpreter 版本，不安裝到日常 Python 環境。本段建立環境不授權開始全集測試：

```powershell
py -3.13 --version  # 必須實際為 3.13.0，不能只由 selector 推定
py -3.13 -m venv tmp/testing-ci-venv
& .\tmp\testing-ci-venv\Scripts\Activate.ps1
python -m pip install --disable-pip-version-check pip==24.2
python -m pip install --disable-pip-version-check -r requirements-ci-lock.txt
python -m pip check
python -m pip freeze --all
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTEST_ADDOPTS = ""
```

所有新正常 PR／develop short／main 完整 job 使用隔離 CI 環境、bootstrap pip24.2及同一 requirements-ci-lock.txt。精確legacy原依賴/capture/雙Python僅保留於舊快照、reader及明確歷史測試fixture，不是新live event的當次證據豁免；不能以新指令覆寫歷史原件。

## 開發模式

預設只做每輪預估五分鐘內的必要定向 regression、compile／diff。只有使用者明確說「開始驗證」或已有等效明確時段授權，才固定候選進入正式驗證。以下是限定改動的範例，不是要求例行執行本機全集：

```powershell
python -m pytest tests/test_ci_validation_workflow.py -q
python -m compileall -q scripts
git diff --check
git status --short
```

<a id="pending-pr-flow-ci"></a>
## 待啟用首版：全 PR full，既有 actual merge short

依[最新首版裁定](docs/DEVELOPMENT_FLOW_DECISIONS.md#flow-first-edition-20261112)及[單一程序入口](docs/DEVELOPMENT_REVIEW.md#pending-checks-routes)，符合啟用／新 task 採納資格後，docs／local／core 審查人數為 1／1／2，但正式檢查路徑全部是既有完整非視窗 CI／當次 coverage `/3`。Develop actual merge 仍用既有 short `/3`；main push 保留完整驗證。新政策未啟用，本次本機檢查不是正式全集或 hosted 結果。

必要文件內容、連結、anchor 或 render 由 reviewer 親自確認，透過既有 canonical `impact_summary`／`review_checks` 記錄範圍、方法、原件與限制；full pytest 不代表這些確認已完成。原件仍須可取回、hash／candidate 相符，來源由協調者獨立核對；作者 JSON、label、hash 或布林值不認證 reviewer 身分或授權。

首版 workflow／summary 不要求文件專屬 checks、capture、producer、plan 或 artifact；沒有 capture／producer 亦走正常 full。Gate 直接核 `validation_evidence.verify_current_coverage`，post-merge 直接沿既有 `verify_reuse(current_only=True)`，核來源 PR／run／attempt／tested SHA、actual ordered parents／tree、環境等價及短驗證。六項短命令仍為 runtime、非視窗 import、CLI、compile、diff、clean；缺任一必要項、來源不符或失敗不能完成。Full 的十四項凍結依賴不放寬，本機缺六項套件不能改填已安裝。

以下僅定位既有介面與純診斷，不授權本輪開始正式驗證、連網或 merge：

```powershell
python scripts/pr_review_gate.py validate state.json
python scripts/pr_review_gate.py diagnose-lifecycle state.json
python scripts/pr_review_gate.py next-action state.json
python scripts/validation_evidence.py verify-coverage current-coverage.json
python scripts/validation_evidence.py verify-reuse current-coverage.json short.json
```

`pr_flow_ci.py` 僅保留 full／short 的有限相容 router，不能產生 docs 豁免或 parser 證據；正式 workflow 不依賴它。V5／V6 只接受 `checks_route=full`、`documentation_checks=null`。舊 V5 公開 `next-action` 永 pending／STOP；V6 只有通過 [activation／adoption 原件核對](docs/PR_REVIEW_GATE.md#activation-v6)才進入原生命週期，沒有 state／環境開關。`diagnose-lifecycle` 外層仍 STOP，不能當公開放行。

### 延後研究與原件

[PR_FLOW_DEFERRED_822d494.zip](docs/research/PR_FLOW_DEFERRED_822d494.zip) 保存固定來源的九件程式／測試／workflow 原 bytes，包含原 assertions；ZIP SHA256 `a33a9a69236e40b66646f384360d7e0b90a70d5c14340b5917f90bb1870d9679`，成員清單與 hash 見[決策稿](docs/DEVELOPMENT_FLOW_DECISIONS.md#flow-first-edition-20261112)及原 task INDEX。它不會被 import、解包執行或 active pytest 收集，也不是可獨立啟動的工具。退出 active inventory 是整組能力延期；原方法及失敗 oracle 仍可從該附件／822d494 Git object 核對，不能把退出說成測試通過。

自製 Markdown／HTML parser、Actions 文件免測 producer／capture、文件專屬 artifact／short 均不在首版。RA5-F1／RB5-F1 保留原 `BLOCKED/code`，只能說含缺陷能力已延後並退出首版，不能說已修復。原兩個錯誤成功 packet 不能充當 full `/3`，也不能藉研究開關取得首版授權。

本機 fixture／模型原件只驗接線與拒絕行為，與真實 full／hosted／actual merge push 證據分列。PR #71 當時的六項本機缺件仍未安裝，其 hosted 完整 lock、正式審查及 actual short 已有真實原件；使用者採納的 task 限定歷史過渡只處理 `752→3b` 功能續作與第四輪表示，沒有宣稱舊 gate 通過。原 task 以 4/4 結案，全部舊報告保持；本啟用候選另按起點舊規驗收，不能沿用其正式 PASS 或取得第五輪。

## PR 的當前完整非視窗驗證

一般 PR→develop、PR→main 都由 GitHub hosted Windows Python3.13.0 與 requirements-ci-lock.txt 執行全部有效非視窗 pytest/verify-active。正常入口不呼叫 restore-history，不要求舊CI artifact或hand-off/ledger/continuation新版閉包；保留原 task/baseline/source_ref/封存/失敗，不bootstrap/unseal/換目錄歸零。以下為功能入口，不可本機自填 CI 身分或把短測稱正式CI：

```powershell
python scripts/test_entrypoint_audit.py collect tmp/ci-test-inventory.json
python -m pytest tests/ -q -rs --junitxml=tmp/ci-pytest.xml
python scripts/test_entrypoint_audit.py verify-active tmp/ci-test-inventory.json --junit tmp/ci-pytest.xml
```

CI依workflow保存當次環境與命令/退出碼，使用 `validation_evidence.py current-coverage --evidence-root tmp --output tmp/current-coverage.json` 及 `verify-coverage tmp/current-coverage.json` 產生/驗證真實 `/3` bundle；這不是本機冒用CI身分的入口。正常PR/main完整驗證與develop短驗證使用同一隔離CI lock解析。

PR 另核 runtime、compile、PR diff、clean、repo/base/head/tested SHA/parents/tree、當次環境/命令/退出碼/raw hashes與有限附件。新版 coverage `/3` 明列current政策；required summary核全部必要真實outcomes。當次失敗/漏跑/skip/錯SHA/缺附件仍失敗，不能造空coverage。舊 `/1`、`/2` 原義只讀保持；collection、upload 或green CI都不能取代兩輪完整獨立PASS或解除產品安全blocker。

## develop 實際 merge 的短驗證

一般 develop push 執行 actual merge SHA 的短驗證，核對 ordered parents／tree、PR coverage 原件、Python／依賴／capture／scope 等價性，再做 runtime、非視窗 import／CLI、compile、push diff 與 clean-tree；不例行再跑 full pytest。PR tested SHA 保持原義，不能改稱在 merge SHA 重跑。入口需要真實 develop push context：

```powershell
python scripts/validation_evidence.py post-merge --current-only --evidence-root tmp/post-merge-evidence
```

PR CI不能代替actual merge push。只讀對應PR的必要當次artifact，不能遞迴全部歷史；當次原件缺失、等價性不足、wrong ordered parents/tree 或短驗證失敗仍停止沿用。新版short為 `/3`，舊 `/1`、`/2` 保留原義。

## main 完整非視窗驗證

main 實際 merge push 依現行 workflow 執行完整有效非視窗 pytest 與 `verify-active`，不能用 develop short 或 PR CI 代替；不追加例行完整 unittest。以下是 main 完整 job 的功能入口示意，CI 仍負責真實 event／SHA／依賴／原件保存與 summary：

```powershell
python -m pytest tests/test_runtime_asset_manifest_integrity_v562.py -q
python scripts/test_entrypoint_audit.py collect tmp/ci-test-inventory.json
python -m pytest tests/ -q -rs --junitxml=tmp/ci-pytest.xml
python scripts/test_entrypoint_audit.py verify-active tmp/ci-test-inventory.json --junit tmp/ci-pytest.xml
python -m compileall -q -x "(^|[\\/])(\.venv|tmp)([\\/]|$)" .
git diff --check
git status --short
```

main CI 同時核對 committed push diff 與 clean-tree，限定保存 inventory、JUnit、執行輸出及環境／依賴；失敗也保存可取得原件，上傳成功不得蓋過失敗。active IDs 必須恰好成功一次；unknown、missing、duplicate、skip、failure、error 或錯 SHA／execution 均拒絕。`verify-gui` 已退役，不是有效入口。

## 歷史契約與人工驗收

原 simplify fixed-baseline transition、雙Python、full unittest、gate v2/Tcl-Tk操作及PR29精確legacy條件都是 historical 契約與證據。原schema/readers與明確歷史fixture保留其原判斷；新live event均採current-evidence/1，不能以legacy身份略過當次lock、原件或驗證。原baseline/capture/修正計數/失敗不改寫，不把舊Tk指令作新要求或追認舊PASS。

v5.8.0 人工操作只依已採納的 [六項有限驗收](docs/RELEASE_ACCEPTANCE_V580.md)。本發布任務的 `USER_REPORTED_PASS` 是使用者回報，並非代理執行、自動化 GUI PASS 或其他教材完成證據；原件與適用候選由發布 task evidence 記錄。涉及寫入只使用副本與隔離 Global，備份／回復見 [升級說明](docs/UPGRADE_V580.md)。整份教材校對完成不是軟體發布門檻，教材自己的 completion gate 與待辦仍保留。

Runtime asset integrity failure 必須查明原件與 manifest，不能批次重算 SHA、降低 verifier 或用當前檔案內容覆寫 truth。每次重跑須有修改影響、具體失敗線索或明確門檻；等待、session 恢復、整理報告都不新增測試額度。本文件不授權 merge／tag／Release，也不是測試或審查 PASS。
