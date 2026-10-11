# Testing

現行驗證政策是 [`validation-flow/no-real-tk/1`](docs/VALIDATION_POLICY.md)。所有自動化入口均取消真 Tk 視窗，包括 hidden root、虛擬顯示器、fixture／import／子程序；不執行 Tcl/Tk configure、preflight、初始化能力門檻或 GUI retry／補驗。產品 GUI、Tk 依賴及正式 `.bat` 入口保留。有效非視窗 actual／expected、identity、fingerprint、資料、交易、保存與恢復測試仍須執行；取消視窗不能寫成 GUI PASS。

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

一般 PR／develop short 使用現有 lock 安裝路徑；main 完整 job 另固定 bootstrap pip24.2，再安裝同一 `requirements-ci-lock.txt`。精確 legacy 條件的原依賴／capture 行為依 workflow 保留，不能將本段命令覆寫到歷史原件。

## 開發模式

預設只做每輪預估五分鐘內的必要定向 regression、compile／diff。只有使用者明確說「開始驗證」或已有等效明確時段授權，才固定候選進入正式驗證。以下是限定改動的範例，不是要求例行執行本機全集：

```powershell
python -m pytest tests/test_ci_validation_workflow.py -q
python -m compileall -q scripts
git diff --check
git status --short
```

## PR 的非視窗 core／coverage

一般 PR 由 GitHub hosted Windows Python3.13.0 執行全部有效非視窗 core；保留原 task／baseline／source_ref、sealed handoff 與失敗 attempt。缺失或損毀 ledger／artifact 停止，不 bootstrap／unseal／換目錄重設歷史。以下是既有 CI context 內的入口，不可在本機自填 CI 身分冒稱正式證據：

```powershell
python scripts/validation_runner.py restore-history --evidence-root tmp/validation-evidence
python scripts/validation_runner.py run --group core --mode validation --evidence-root tmp/validation-evidence
python scripts/validation_runner.py aggregate --evidence-root tmp/validation-evidence --output tmp/validation-evidence/coverage.json
python scripts/validation_evidence.py verify-coverage tmp/validation-evidence/coverage.json
```

PR 另核 runtime integrity、compile、PR diff 與 clean-tree；required summary 必須核對真實 step outcomes。新 `/2` run／coverage／inventory 明列政策及視窗取消；舊 `/1` logs、Tk 失敗與 retry 額度只讀保留。collection、artifact 上傳或 green CI 不能取代兩輪獨立審查，也不能解除 code blocker。

## develop 實際 merge 的短驗證

一般 develop push 執行 actual merge SHA 的短驗證，核對 ordered parents／tree、PR coverage 原件、Python／依賴／capture／scope 等價性，再做 runtime、非視窗 import／CLI、compile、push diff 與 clean-tree；不例行再跑 full pytest。PR tested SHA 保持原義，不能改稱在 merge SHA 重跑。入口需要真實 develop push context：

```powershell
python scripts/validation_evidence.py post-merge --evidence-root tmp/post-merge-evidence
```

PR CI 不能代替此 actual merge push 證據。原件缺失、等價性不足或任何失敗均停止沿用。

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

原 `codex/simplify-validation` 的 fixed-baseline transition、Python3.12／3.13、full unittest／pytest、gate v2 及 Tcl/Tk 操作是 **historical** 契約與證據。其後採納 no-real-tk/1，取消全部真視窗、Tcl/Tk 修補與 GUI retry 義務；其他凍結 baseline、exact legacy 條件、capture、修正計數、失敗與 blocker 保留。PR29 等精確 legacy adapter 仍依現行 [驗證政策](docs/VALIDATION_POLICY.md) 與 workflow 判斷，不能把舊 Tk 指令當作新任務要求，或追認舊失敗為 PASS。

v5.8.0 人工操作只依已採納的 [六項有限驗收](docs/RELEASE_ACCEPTANCE_V580.md)。本發布任務的 `USER_REPORTED_PASS` 是使用者回報，並非代理執行、自動化 GUI PASS 或其他教材完成證據；原件與適用候選由發布 task evidence 記錄。涉及寫入只使用副本與隔離 Global，備份／回復見 [升級說明](docs/UPGRADE_V580.md)。整份教材校對完成不是軟體發布門檻，教材自己的 completion gate 與待辦仍保留。

Runtime asset integrity failure 必須查明原件與 manifest，不能批次重算 SHA、降低 verifier 或用當前檔案內容覆寫 truth。每次重跑須有修改影響、具體失敗線索或明確門檻；等待、session 恢復、整理報告都不新增測試額度。本文件不授權 merge／tag／Release，也不是測試或審查 PASS。
