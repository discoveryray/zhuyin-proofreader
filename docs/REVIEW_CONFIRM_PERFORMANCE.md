# 人工應標確認：效能與正確性驗證

任務基準為 develop `1593e7af65596d320b4427f1b15bb2bc0bdc949c`，修正分支為 `codex/review-confirm-responsive`。本次不合併、不發布版本。

## 瓶頸與變更

原本每筆保存會完整套用規則、重播全部人工事件及驗證 ledger。有 actual 暫存時，暫存摘要再讀取工作階段與 DB、完整 materialize；有 expected 人工事件時，又 materialize 一次無事件的基準。在合成基準中，8 組暫存的每次確認有三次完整 materialize。JSON 寫入與預覽並非主要瓶頸。

`prepare_review_ledger` 從原本 `materialize_ledger` 提取共同實作，同一次計算回傳套用規則後的基準和完整結果。`ReviewSaveService` 在已驗證且內容版本不變時，只從原基準重播被修改的 review_id，再使用既有 row validator；事件覆寫及撤銷也由原基準開始。未知或已變更的 cache 依賴會觸發完整計算，與畫面不一致的工作階段／DB／來源則拒絕保存。

暫存驗證使用同一份已驗證 snapshot 與基準，保留既有群組 snapshot、checked roster、actual/expected 獨立性與來源 hash 驗證，不再次讀取並重建全冊。暫存損毀或失效會留下 `staging_error` 並清空可信 checked roster，因此不會隱藏待處理項目。

保存、驗證與待辦排序在單一 worker 執行；Tk 主執行緒以 queue/after 收取結果，負責元件及預覽。未新增圖片預載或圖片快取。[PyMuPDF 官方文件](https://pymupdf.readthedocs.io/en/latest/recipes-multiprocessing.html) 說明它不支援多執行緒操作；本次 worker 不呼叫 PDF renderer 或 `PhotoImage`。

## 快取與保存契約

- Cache 僅在本次 `ReviewSaveService` 生命週期存在，保存一份基準、當前 ledger、review_id 索引與依賴內容雜湊；沒有歷史版本無限累積。
- 每次保存核對完整工作階段／DB bytes、來源 PDF、manifest 所列 actual/candidate artifacts、runtime 資產、可重用 expected 規則與 project actual/staging/recovery 依賴。大小與 mtime 不用作內容有效的證據。保存前再次核對，DB 原子替換前另核對預期 SHA。
- runtime byte guard 使用共用資產清單 registry。它是內容異動檢查，不宣稱取代正式 pipeline 的完整 runtime schema validation；校對保存只重播 sealed ledger，不重新解析字典或 decoder assets。
- `version_token` 是整次人工操作的 snapshot 識別，不是 actual/expected pipeline fingerprint。fingerprint、schema、identity、semantics epochs、JSON 格式及 runtime manifest truth 都未遷移。
- 規則、actual 套用、重新載入等操作丟棄舊計算基準與索引，但同一已驗證工作階段的 evidence anchor 會保留。已偵測的外部 actual／規則異動不能因保存失敗後重試、稍後處理或 queue reload 而被清除；須恢復原證據或經既有 refresh 流程確立有效新工作階段。
- 保留同字分組、lane/source 順序、稍後處理與 actual 暫存導覽。待辦計算共用同一函式，worker 不操作 Tk。
- JSON 以獨立暫存檔、flush/fsync、原子替換保存。失敗保留舊 DB、當前項目與記憶體。成功後才 publish DB/待辦並移下一筆；已保存但 queue/show 失敗顯示不同訊息，阻擋繼續操作，關閉重開由 durable DB 恢復。
- 背景 request 綁 generation、專案路徑、manifest/DB snapshot 與 review_id，過期結果不 publish。保存期間阻擋重入及導覽，完成後仍有 0.5 秒防連點。只有當前預覽成功對應該 review_id 時才能快捷確認。
- 保存與 actual 套用的輪詢 timer 由視窗管理；視窗銷毀時只取消自身 callbacks，禁止後續 UI publication。已啟動的持久化 worker 不操作 Tk，保存結果可由重新開啟恢復。
- Dialog 與 preview 銷毀時，在 Tk 主執行緒釋放其 `Variable`、`PhotoImage`、pixmap、callbacks 及子元件引用；canvas、按鈕列與輸入框也釋放保留子元件的引用。輪詢改用方法與參數，避免遞迴閉包保留視窗；actual worker 只捕捉專案路徑。這些清理避免已銷毀 GUI 物件留在 Python reference cycle，之後由保存 worker 觸發 GC 而在錯誤執行緒解構。沒有修改全域 GC 設定，也沒有把每次全量 `gc.collect()` 移到主執行緒。
- 匯出／更新報告及全冊完成仍走既有完整驗證與 completion gate。

## 可重現量測

使用 `scripts/benchmark_review_confirm.py`，在相同 Windows/Python/PyMuPDF 環境執行原版及修正版。`--fixture` 保留並復原初始 DB 的 exact bytes，工作階段／PDF／XLSX／暫存共用；結果的 `fixture_content_sha256` 必須一致。

```powershell
git archive --format=zip -o tmp/review-baseline.zip 1593e7af65596d320b4427f1b15bb2bc0bdc949c
Expand-Archive -LiteralPath tmp/review-baseline.zip -DestinationPath tmp/review-baseline
python scripts/benchmark_review_confirm.py --repository tmp/review-baseline --fixture tmp/review-bench-staged --rows 2000 --events 500 --groups 8 --operations 21 --output tmp/before-staged.json
python scripts/benchmark_review_confirm.py --repository . --fixture tmp/review-bench-staged --rows 2000 --events 500 --groups 8 --operations 21 --output tmp/after-staged.json
```

無暫存情境改用另一個 fixture 目錄及 `--groups 0`。本機 sandbox 的 Tcl 初始化不可用，已在原生 Windows 環境實際執行 native Tk 自動測試與量測；未透過 skip 假裝 GUI 通過。

每組 2,000 筆 ledger、初始 500 筆人工事件，21 次連續確認；第 1 次冷 cache 另外保留，穩態統計為第 2～21 次，P95 使用 nearest rank。10 ms Tk heartbeat 的每筆最長間隔用來量測主迴圈停止回應的時間。分段為 inclusive，不能直接相加；背景處理時間、介面 heartbeat gap 與 500 ms cooldown 分開記錄。

實測環境：Windows 10 build 19045、Python 3.13.5、Tcl 8.6.15、PyMuPDF 1.26.4。以下單位為 ms，欄位為「中位數 / P95」。原始逐筆結果見 [benchmark JSON](evidence/review_confirm_benchmark.json)。

| 情境 | 指標 | 原版 | 修正版 |
| --- | --- | ---: | ---: |
| 0 組暫存 | 按下至畫面更新完成 | 368.00 / 432.37 | 303.93 / 342.10 |
| 0 組暫存 | 每筆最長 Tk 心跳間隔 | 354.35 / 417.23 | 104.36 / 131.96 |
| 8 組暫存 | 按下至畫面更新完成 | 1018.35 / 1098.98 | 423.92 / 446.10 |
| 8 組暫存 | 每筆最長 Tk 心跳間隔 | 1005.07 / 1086.12 | 105.70 / 110.90 |
| 8 組暫存 | 按鈕 callback | 1004.90 / 1085.96 | 1.53 / 1.79 |
| 兩種情境 | 保存成功後防連點 | 500 / 500 | 500 / 500 |

8 組暫存的分段結果：

| 分段 | 原版中位數 / P95 | 修正版中位數 / P95 |
| --- | ---: | ---: |
| 全冊 materialize（含 nested staging calls） | 563.33 / 622.66；每筆 3 次 | 穩態 0 次；單筆 replay 0.43 / 0.45 |
| actual 暫存驗證（inclusive） | 645.53 / 695.82 | 128.90 / 137.44 |
| JSON 保存 | 9.16 / 10.95 | 48.85 / 51.70 |
| 待辦更新 | 13.25 / 18.25 | 11.46 / 13.32（worker） |
| 預覽渲染 | 89.16 / 93.81 | 89.34 / 100.92（主執行緒） |
| 完整內容 hash 核對（新增明確分段） | 原路徑未獨立分段 | 99.89 / 104.28（worker） |

JSON 保存現在包含 flush/fsync 及寫入前 DB hash guard，因此這一段較慢；整體改善來自消除重複計算及背景處理，沒有刪除防連點。UI heartbeat gap 仍包含約 90 ms 的主執行緒預覽，不能宣稱完全沒有停頓。

冷 cache 第一次確認各只有一筆樣本，不能提供有意義 P95：8 組暫存總時間原版 1033.08、修正版 816.17；無暫存原版 370.53、修正版 700.05。無暫存首次保存因建立完整可驗證 snapshot、內容 hash 與持久化檢查而較慢，但其 Tk 最大心跳間隔由 357.24 降至 124.32；後續操作才使用增量快取。本次不隱藏這項首次操作成本。

上表已在第二次 corrective cycle 的 GUI 資源生命週期修正後，以相同 fixture 重新量測；保留前次 evidence anchor、保存失敗且預覽失效的控制項保護，以及視窗輪詢管理。四份原始 run 共 84 筆 samples 的中位數、nearest-rank P95、fixture digest、環境與防連點值已重新核對。先前量測與失敗紀錄保留在 task evidence，沒有覆寫原審查報告。

## 回歸範圍與查核命令

新增 backend regression 涵蓋增量／完整等價、覆寫／撤銷、actual unresolved 下 expected 獨立、暫存三態、同大小同 mtime 檔案竄改、外部 session／DB 變更、保存期間依賴變更、failed write、重開、規則／actual 失效、重入與 runtime guard。新增 async regression 涵蓋 stale generation/review_id、worker/main thread 分工、queue failure、undo 與互斥操作；另驗證同一失效 anchor 跨重試／defer／revisit／reload 保留、普通 I/O retry、既有 actual transaction refresh 後恢復，以及 save／actual poll 的 owner destruction 與完成清理。

原生 Tk 回歸實際 invoke 按鈕與 Ctrl+Enter 事件，檢查重複提交、show failure 後重開、下一筆預覽失敗，以及保存期間預覽失效後不得恢復快捷按鈕；既有同字分組、處理順序、defer 與 actual staging 導覽測試也配合非同步完成時點繼續檢查。Headless tests 與 native Tk tests 分開留存，未把前者稱作人工視覺驗收。

GUI 生命週期的 controlled probe 在舊 HEAD `89e0297bd6e5b9b08d077b2216029fb6224976fc` 使用真實 dialog：submit／destroy 後返回，再讓實際保存 worker 執行 GC。四個主執行緒建立的 `StringVar` 均在 worker 解構，產生四次 `main thread is not in main loop`，約增加 4.38 秒等待。新的 native regressions 檢查 submit、cancel、owner destruction、`Variable`／`PhotoImage` 的主執行緒釋放，以及沒有外部 root 引用時關閉視窗、worker 完成保存／actual transaction 後重開恢復。控制 GC 時點只用於測試，production 不新增強制 collection。

這項可重現的 Tk 資源所有權問題，不能證明另一次 CI `init.tcl` 讀取失敗的原因；該 failed CI、獨立 evidence BLOCKED 與調查原文繼續保留。局部回歸與本節 benchmark 不等於新 HEAD 的完整 suite 或 CI PASS，完整結果須查核對應 commit 的原始輸出。

完整驗證命令如下；具體本次輸出、測試數量與兩輪獨立審查／CI 證據保存在 PR evidence 及 `tmp/pr-review-automation/review-confirm-responsive/`，以實際輸出為準：

```powershell
python -m unittest discover -s tests -p 'test_*.py'
python -m pytest tests/ -q -rs
python -m unittest discover -s tests -p 'test_runtime_asset_manifest_integrity_v562.py'
python -m compileall -q -x '(^|[\\/])(\.venv|tmp)([\\/]|$)' .
git diff --check
git status --short
```

完整修改清單：

- `review_gui.py`
- `review_display.py`
- `standalone_proofread.py`
- `review_save_service.py`
- `scripts/benchmark_review_confirm.py`
- `tests/review_save_test_support.py`
- `tests/test_review_save_service.py`
- `tests/test_review_async_save.py`
- `tests/test_hotfix_tone_and_followup_v531.py`
- `tests/test_manual_actual_gui_batch_v570.py`
- `tests/test_manual_review_usability_v580.py`
- `tests/test_review_visual_usability_v580.py`
- `tests/test_staged_review_navigation_v580.py`
- `docs/REVIEW_CONFIRM_PERFORMANCE.md`
- `docs/evidence/review_confirm_benchmark.json`

`review_display.py` 僅增加銷毀時的資源與子元件引用清理，未新增預載、圖片快取或預覽渲染重構；`sources/`、runtime assets/manifest、正式 main/develop、資料格式與版本號未修改。

## 限制與人工驗收

量測使用符合目前資料契約的合成工作階段、小型 PDF 及有 hash 的 XLSX，沒有使用真實教材；未驗證真實大型／複雜 PDF 的端到端耗時、打包後 EXE 或其他作業系統。保留完整內容 hash 與 JSON 重寫，極大來源檔案或人工事件 DB 仍可能增加背景等待時間。預覽仍在主執行緒，其真實教材耗時需另測。

人工檢查步驟：

1. 用測試專案開啟人工校對，等原頁預覽出現後連按確認；應顯示儲存狀態，只記一筆，保存期間不能跳到別筆，成功後按原分組順序前進。
2. 分別用無 actual 暫存、有效暫存與失效暫存測試；失效時不得把該群組視為已處理。檢查稍後處理及 actual 批次套用導覽。
3. 在專案副本中修改來源 PDF／session bytes 或使 DB 無法寫入，再保存；應留在原筆、顯示原因且不覆寫既有 DB。先成功保存一次後修改 project actual／可重用規則，確認重試、稍後處理再返回仍持續拒絕；恢復原證據或正常 refresh 後再確認可恢復。
4. 關閉重開確認已保存事件；更新報告時仍執行完整驗證。若畫面提示「已保存，但畫面更新失敗」，先重開恢復，不重複輸入判定。
