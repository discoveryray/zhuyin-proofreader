# 人工應標確認：效能與正確性驗證

目前候選效能請讀文末「第五輪最終 source：final-v5」；前方 B→H 與 final-v4 均保留為各自來源的歷史紀錄。

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
- 已存在 DB 的 JSON 根節點必須先確認為物件，才進入既有 normalization；`[]`、`null`、`false`、`0`、`""` 一律拒絕，不得視為缺少 DB。missing DB 與合法空物件 `{}` 仍沿用原初始化契約；版本轉換及不相容 schema／legacy 的既有判斷不變。
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
| 0 組暫存 | 按下至畫面更新完成 | 368.00 / 432.37 | 303.58 / 348.37 |
| 0 組暫存 | 每筆最長 Tk 心跳間隔 | 354.35 / 417.23 | 107.60 / 142.74 |
| 8 組暫存 | 按下至畫面更新完成 | 1018.35 / 1098.98 | 436.13 / 471.82 |
| 8 組暫存 | 每筆最長 Tk 心跳間隔 | 1005.07 / 1086.12 | 105.36 / 117.82 |
| 8 組暫存 | 按鈕 callback | 1004.90 / 1085.96 | 1.55 / 2.12 |
| 兩種情境 | 保存成功後防連點 | 500 / 500 | 500 / 500 |

8 組暫存的分段結果：

| 分段 | 原版中位數 / P95 | 修正版中位數 / P95 |
| --- | ---: | ---: |
| 全冊 materialize（含 nested staging calls） | 563.33 / 622.66；每筆 3 次 | 穩態 0 次；單筆 replay 0.43 / 0.66 |
| actual 暫存驗證（inclusive） | 645.53 / 695.82 | 135.91 / 143.01 |
| JSON 保存 | 9.16 / 10.95 | 50.98 / 55.46 |
| 待辦更新 | 13.25 / 18.25 | 12.12 / 19.16（worker） |
| 預覽渲染 | 89.16 / 93.81 | 89.67 / 98.45（主執行緒） |
| 完整內容 hash 核對（新增明確分段） | 原路徑未獨立分段 | 101.09 / 109.52（worker） |

JSON 保存現在包含 flush/fsync 及寫入前 DB hash guard，因此這一段較慢；整體改善來自消除重複計算及背景處理，沒有刪除防連點。UI heartbeat gap 仍包含約 90 ms 的主執行緒預覽，不能宣稱完全沒有停頓。

冷 cache 第一次確認各只有一筆樣本，不能提供有意義 P95：8 組暫存總時間原版 1033.08、修正版 813.82；無暫存原版 370.53、修正版 688.23。無暫存首次保存因建立完整可驗證 snapshot、內容 hash 與持久化檢查而較慢，但其 Tk 最大心跳間隔由 357.24 降至 124.47；後續操作才使用增量快取。本次不隱藏這項首次操作成本。

上表已在使用者額外授權的第四次 corrective cycle 完成 DB 根節點驗證修正後，以相同 fixture 重新量測；前三輪修正與審查歷史保留，未重設次數。四份原始 run 共 84 筆 samples 的中位數、nearest-rank P95、fixture digest、環境與防連點值已重新核對。先前量測與失敗紀錄保留在 task evidence，沒有覆寫原審查報告。

## 回歸範圍與查核命令

新增 backend regression 涵蓋增量／完整等價、覆寫／撤銷、actual unresolved 下 expected 獨立、暫存三態、同大小同 mtime 檔案竄改、外部 session／DB 變更、保存期間依賴變更、failed write、重開、規則／actual 失效、重入與 runtime guard。新增 async regression 涵蓋 stale generation/review_id、worker/main thread 分工、queue failure、undo 與互斥操作；另驗證同一失效 anchor 跨重試／defer／revisit／reload 保留、普通 I/O retry、既有 actual transaction refresh 後恢復，以及 save／actual poll 的 owner destruction 與完成清理。

第四輪補上五種非物件 DB 根節點的 cold cache、保存後撤銷至空事件的 warm cache，以及重試／invalidate／service replacement 回歸。Headless GUI 使用真實非同步保存路徑，確認損壞 bytes 不被覆寫、DB／current／待辦不 publish 或前進、成功旗標維持 false，恢復有效 bytes 後才能成功重試；missing DB、合法空物件與既有版本／schema 行為另有測試。局部驗證為 21 tests／20.806 秒及 27 tests／166.928 秒通過，原始輸出為 task evidence 的 `corrective4-db-root-targeted.log` 與 `corrective4-headless-regression.log`；這兩次執行有重疊測試，不代表唯一測試總數或新 HEAD 的完整 suite 結果。

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
- `tests/test_review_visual_corrective_v580.py`
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

## 第五輪先前 source：final-v4（已由下方 final-v5 取代）

本節只保留修正原生祖先 destroy 清理前的 source 與樣本。該 source 不再是交審候選；最終效能以文末 final-v5 的全新20程序為準。

前面各節保留原 B→H 第四輪的歷史數字；本節才是指定 D 與本輪整合版本的新比較。

- D：502414b3b38e004a6d8d9cb693cf21b65148765a，由 Git archive 匯出。
- 新版量測時尚未固定交審 commit，標為 round5-integrated-uncommitted-source-manifest。交審候選須與被測 application／fixture constructor／harness bytes 相同，不能把後來產生的 SHA 偽稱為當時已存在。
- Harness SHA256：c8e2b5e92c50f3e3a890ff9d1760a054e2e6992b30340795202ed5d7ccb92b0d。
- 新版 source 清冊 SHA256：41a3526bee8485b1ae89bacbb6fc556b961189259c3f393a1dd12e0433ff94bf。JSON保存各檔案raw-byte SHA256；它不是Git blob SHA，另核對checkout與LF-normalized blob的對應。
- [本輪完整 benchmark JSON](evidence/review_confirm_round5_benchmark.json) 保存20份逐筆原始結果、22次程序（含兩次fixture準備）的命令／exit／log/result hashes、環境與全檔清冊，可跨電腦查阅。stdout/stderr、原fixture bytes、失敗批次及獨立重算audit另見PR本輪evidence索引。

環境：Windows 11 build 26200、Python 3.13.0 AMD64、Tcl/Tk 8.6.14、PyMuPDF 1.26.7、openpyxl 3.1.5、fonttools 4.63.0；螢幕2194×1234、Tk scaling 1.334473。全部依賴版本見JSON。两版本共用interpreter與harness，LOCALAPPDATA／Global隔離，不用正式資料。

各情境2000 rows、500初始事件、0或8 staging groups。先以獨立程序建立fixture，再跑5對fresh processes，奇數對D→新版、偶數對新版→D。每次完整還原DB、staging、PDF、XLSX、session與正式delivery lock的初始bytes。20個量測程序全exit 0，420次真實按鈕保存；10ms Tk heartbeat、預覽、500ms防連點均保留。其他代理重負荷測試暫停，未宣稱控制所有OS背景工作。
- 0-group fixture全檔清冊SHA256：46e0ed6a83b926dbdcb2d5ce0d7762a09c65ae297a51f6ffba42eda13db1b81b。
- 8-group fixture全檔清冊SHA256：43710b6b983cc1175cbcbd9acaa00d4ef8d02fef4fa922cdc19d0c3081c4883e。

首次＝fresh service第一筆（n=5），不是清空OS filesystem cache；穩態＝每程序第2～21筆（n=100，但同程序資料相關，非100個獨立實驗）。P95用nearest rank；首次n=5的P95就是最大值，尾端估計有限。單位ms，表格為median / P95。

| 暫存 | 階段 | 指標 | D | 本輪整合 |
| --- | --- | --- | ---: | ---: |
| 0 | 首次 | 按下至保存及畫面完成 | 165.06 / 170.05 | 304.25 / 311.46 |
| 0 | 首次 | 按鈕callback | 151.89 / 154.92 | 1.56 / 1.77 |
| 0 | 首次 | 每筆最長Tk heartbeat gap | 152.31 / 155.10 | 140.69 / 142.67 |
| 0 | 穩態 | 按下至保存及畫面完成 | 215.19 / 237.33 | 166.51 / 188.08 |
| 0 | 穩態 | 按鈕callback | 197.84 / 220.13 | 1.80 / 2.35 |
| 0 | 穩態 | 每筆最長Tk heartbeat gap | 198.22 / 220.36 | 67.56 / 78.76 |
| 8 | 首次 | 按下至保存及畫面完成 | 413.38 / 424.09 | 353.87 / 365.53 |
| 8 | 首次 | 按鈕callback | 399.29 / 407.88 | 1.55 / 1.72 |
| 8 | 首次 | 每筆最長Tk heartbeat gap | 399.43 / 408.27 | 167.29 / 175.92 |
| 8 | 穩態 | 按下至保存及畫面完成 | 434.60 / 572.32 | 224.38 / 255.24 |
| 8 | 穩態 | 按鈕callback | 421.70 / 553.80 | 1.75 / 2.16 |
| 8 | 穩態 | 每筆最長Tk heartbeat gap | 421.84 / 553.98 | 62.92 / 77.38 |

主要8-group情境，五對各自穩態median的新版／D總耗時比0.428～0.551（每對都改善），heartbeat比0.124～0.160。合併100筆的total median減少48.4%、heartbeat median減少85.1%。這支持主要暫存情境保有加速並減少卡頓，不代表所有情境或真教材都有相同比例。

**無暫存首次仍退化：165.06→304.25ms，增加139.19ms（約84%）。** Callback 151.89→1.56ms、heartbeat 152.31→140.69ms；按鈕較快歸還，但完成保存並不更快，不能宣稱全情境加速。

已對照ReviewSaveService._save冷路徑與逐筆timing：首次解析／驗證sealed manifest、schema、DB，建立baseline/ledger/index，核對完整內容依賴並於寫入前再核對，再作含DB hash guard的flush/fsync/atomic replacement。0-group新版首次backend median 228.12ms，其中full rebuild86.72、dependency hashing53.71、JSON save25.14ms；D首次full materialize85.43、JSON save4.20ms。這說明新增冷驗證／持久化及初始化成本；inclusive階段不能直接相加，未獨立分段的解析時間也未被臆測為精確歸因。

後20筆新版full rebuild全部為0；0-group incremental replay median 0.31ms、hashing37.65ms。8-group穩態actual staging inclusive median258.64→59.20ms，D full materialize218.64ms，新版incremental replay0.26ms。8-group預覽median45.68→49.73ms，仍在主執行緒且計入總耗時。沒有扣掉預覽、防連點、來源驗證或durability換數字；本輪保留無暫存首次成本，不為降低139ms放寬安全檢查。

### 記憶體與視窗生命週期

使用一次初始化的Windows GetProcessMemoryInfo，包含native Tk/PyMuPDF配置。每筆開始／完成與heartbeat採樣working set、private bytes，另保存OS process peak working set。Private-byte peak只是採樣值；同步D阻塞期間没有Tk heartbeat，不能冒稱精確瞬間峰值。下表為操作完成後median / P95，單位MiB。

| 暫存 | 階段 | 指標 | D | 本輪整合 |
| --- | --- | --- | ---: | ---: |
| 0 | 首次完成 | Working set | 152.94 / 153.71 | 167.97 / 168.05 |
| 0 | 首次完成 | Private bytes | 137.88 / 139.06 | 153.23 / 154.44 |
| 0 | 穩態完成 | Working set | 191.96 / 223.62 | 205.09 / 237.10 |
| 0 | 穩態完成 | Private bytes | 176.56 / 208.51 | 190.17 / 221.99 |
| 8 | 首次完成 | Working set | 163.99 / 164.45 | 169.69 / 170.20 |
| 8 | 首次完成 | Private bytes | 147.45 / 147.86 | 152.90 / 153.35 |
| 8 | 穩態完成 | Working set | 204.88 / 237.06 | 206.26 / 239.02 |
| 8 | 穩態完成 | Private bytes | 188.48 / 220.58 | 190.46 / 222.38 |

0-group穩態private median增加13.61MiB；8-group增加1.98MiB。新服務保留baseline/ledger/index與完整依賴hash，是相對D的額外狀態；未精確歸因每個native allocation，不能把全部OS保留量都宣称為這些資料結構。

每程序另跑6次三位置actual dialog（submit／cancel／owner destroy各2次），共120次dialog、360次可見後checkbox invoke。使用真PDF render，畫出target、捲進viewport、等待checkbox啟用後才invoke；不寫viewed flags、不預勾A，同時影像不得超過A＋一張peer。這是display-only合成群組，不寫reusable truth或staging，也不是人工閱讀驗收。

| 暫存 | 時點／指標（五程序median，MiB） | D | 本輪整合 |
| --- | --- | ---: | ---: |
| 0 | root/window destroy後working set | 405.39 | 411.50 |
| 0 | root/window destroy後private bytes | 387.82 | 393.58 |
| 0 | 全程序peak working set | 446.07 | 436.34 |
| 8 | root/window destroy後working set | 443.32 | 413.53 |
| 8 | root/window destroy後private bytes | 423.95 | 393.74 |
| 8 | 全程序peak working set | 500.38 | 438.70 |

**兩側都有OS記憶體保留上升，未證明没有native leak。** 8-group第1～6次dialog關閉後，D private median為266.48、308.16、350.44、392.12、434.15、475.84MiB；新版為257.65、287.19、316.73、346.27、375.81、405.35MiB。新版增幅較低，但仍上升；root destroy後OS記憶體也不必立即歸還。六次短跑不能推斷長期穩態，或分辨allocator retention與leak。Tk物件及owner-thread釋放另由原生資源清理回歸查核，本表不能取代該測試。

### 量測工具修正、排除與重現

Smoke原先查詢不存在的optional pytest-subtests metadata失敗，改列真實installed distributions。第一批在0-group第二對開始前發現新增delivery lock，改由正式context manager先acquire/release，再快照全部初始bytes，仍拒絕未知檔案。其後自行發現逐次動態ctypes Structure/POINTER定義可能污染記憶體，停止該部分批次，改為一次初始化API/type；10000-call probe的pointer-type數21→21，working set/private bytes均不變。

上述smoke／失敗／中止資料全保留，不混入本表。最終paired-final-v4全部重測；harness/fixture/environment/source hashes一致，另從原始run檔獨立重算所有cold/steady median/P95、核對22次程序exit/log/result hashes；native logs未見Traceback、Exception ignored、Tcl_AsyncDelete或main-thread錯誤。這是量測一致性查核，不是正式PR審查。

當前harness新增必填--revision-label；前方B→H命令屬當時工具。以下新工具對D及整合版共用同一全新fixture；準備使用獨立程序且不計入cold。舊B若只重跑保存量測須明寫--dialog-cycles 0，因其沒有PR31三位置介面，不會拿它取代本輪D比較。

~~~powershell
python scripts/benchmark_review_confirm.py --repository <D-checkout> --revision-label 502414b3b38e004a6d8d9cb693cf21b65148765a --fixture <isolated-fixture-8> --rows 2000 --events 500 --groups 8 --operations 21 --dialog-cycles 6 --prepare-fixture-only --output <prepare.json>
python scripts/benchmark_review_confirm.py --repository <D-checkout> --revision-label 502414b3b38e004a6d8d9cb693cf21b65148765a --fixture <isolated-fixture-8> --rows 2000 --events 500 --groups 8 --operations 21 --dialog-cycles 6 --output <D-pair1.json>
python scripts/benchmark_review_confirm.py --repository <integrated-checkout> --revision-label <verified-source-label> --fixture <isolated-fixture-8> --rows 2000 --events 500 --groups 8 --operations 21 --dialog-cycles 6 --output <integrated-pair1.json>
~~~

先設定該interpreter的TCL_LIBRARY／TK_LIBRARY及隔離LOCALAPPDATA。重複5對、交錯次序；0-group用另一新fixture。未知／缺失fixture檔案會拒絕重置，不可忽略。原fixture含絕對路徑；跨電腦重現須生成該電腦自己的共同fixture，再於兩版本間重用相同bytes，不能稱重建後等於本次原始bytes。

未測真教材、大型複雜PDF、打包EXE、長時間記憶體穩態；不補造這些驗收，也不代替完整Windows／runtime／compile／diff／CI或兩輪獨立審查。

## 第五輪最終 source：final-v5（原生 destroy 修正後）

首次正式交審前，新增原生Tcl祖先destroy probe發現兩個owned after callbacks仍pending。實作者將ActualReadingDialog的preview／visibility取消收斂至共用方法，令Python destroy及Destroy事件release都執行。這改變review_gui.py bytes且涉及dialog生命週期，因此沒有把final-v4樣本換標成新候選；使用同一harness、相同實際fixture目錄／全檔bytes，完整另跑20個fresh processes。

- 最終被測 application/fixture constructor 清冊SHA256：0f1c9b99f03b8ed48b4d688aa2244025905891c10211663d49db8b2149707bd3。
- review_gui.py實際被測bytes SHA256：6944ab054f18e16900ee34723c871310a57ec53be7af16a65649b62ff2013458。
- Harness仍為SHA256 c8e2b5e92c50f3e3a890ff9d1760a054e2e6992b30340795202ed5d7ccb92b0d；D仍為502414b3b38e004a6d8d9cb693cf21b65148765a。
- 相對final-v4，被測source清冊只有review_gui.py變更；fixture、interpreter、依賴、螢幕及harness一致。量測時仍是未提交的working source，交審N須由相同bytes綁定。
- 本輪JSON的active_batch=paired-final-v5、raw_runs是20份新原始結果；previous_source_measurements保留完整final-v4聚合及source，沒有改寫其原樣本hash。

兩組各5對、每程序21 saves及6 dialogs；兩次fixture重置準備及20個量測程序全exit 0。所有median/P95從新raw檔独立重算，log/result hashes與native-error掃描通過。統計／採樣限制沿用上節：首次n=5、穩態n=100相關觀測、nearest-rank P95、500ms防連點及真實預覽均保留。

| 暫存 | 階段 | 指標（median / P95，ms） | D | 修正後整合版 |
| --- | --- | --- | ---: | ---: |
| 0 | 首次 | 按下至保存及畫面完成 | 163.30 / 169.79 | 307.24 / 317.89 |
| 0 | 首次 | 按鈕callback | 150.57 / 155.26 | 1.54 / 1.66 |
| 0 | 首次 | 每筆最長Tk heartbeat gap | 150.73 / 155.47 | 126.44 / 138.62 |
| 0 | 穩態 | 按下至保存及畫面完成 | 168.11 / 217.87 | 149.64 / 173.22 |
| 0 | 穩態 | 按鈕callback | 154.91 / 200.55 | 1.59 / 2.11 |
| 0 | 穩態 | 每筆最長Tk heartbeat gap | 155.07 / 200.94 | 55.44 / 71.77 |
| 8 | 首次 | 按下至保存及畫面完成 | 415.20 / 424.29 | 368.83 / 379.33 |
| 8 | 首次 | 按鈕callback | 401.07 / 410.70 | 1.66 / 1.91 |
| 8 | 首次 | 每筆最長Tk heartbeat gap | 401.24 / 410.91 | 167.80 / 174.44 |
| 8 | 穩態 | 按下至保存及畫面完成 | 425.08 / 587.30 | 205.10 / 237.76 |
| 8 | 穩態 | 按鈕callback | 412.59 / 571.25 | 1.58 / 1.98 |
| 8 | 穩態 | 每筆最長Tk heartbeat gap | 412.76 / 571.44 | 59.87 / 67.61 |

主要8-group五對各自穩態median總耗時比為0.466～0.490，heartbeat比為0.136～0.144，每對都改善。合併資料total median減少51.8%、heartbeat median減少85.5%。因此主要暫存情境仍保有加速及減少卡頓；不是全情境皆加速的宣稱。

**0-group首次完成延遲增加143.93ms，仍明列為退化。** 新版首次backend median 228.22ms，full rebuild 87.45ms、dependency hashing 56.18ms、JSON save 24.97ms。對照前節所述冷路徑，保留manifest/schema/DB驗證、內容hash前後重驗、snapshot/index建立及fsync/atomic guard，沒有為追速度跳過。後20筆full rebuild全部為0；首次callback及heartbeat仍較D短。分段inclusive且解析等仍有未細分成本，不虛稱精確分配了全部差額。

| 暫存 | 階段 | 記憶體（median / P95，MiB） | D | 修正後整合版 |
| --- | --- | --- | ---: | ---: |
| 0 | 首次完成 | Working set | 155.96 / 156.47 | 169.73 / 170.05 |
| 0 | 首次完成 | Private bytes | 138.92 / 139.33 | 152.80 / 153.15 |
| 0 | 穩態完成 | Working set | 193.64 / 225.99 | 206.67 / 239.90 |
| 0 | 穩態完成 | Private bytes | 177.61 / 209.16 | 190.18 / 222.74 |
| 8 | 首次完成 | Working set | 164.14 / 165.73 | 169.78 / 170.20 |
| 8 | 首次完成 | Private bytes | 147.18 / 149.03 | 152.62 / 153.27 |
| 8 | 穩態完成 | Working set | 205.27 / 237.10 | 206.97 / 240.14 |
| 8 | 穩態完成 | Private bytes | 188.56 / 221.19 | 190.67 / 222.86 |

穩態private median差額：0-group +12.57MiB，8-group +2.11MiB。服務額外保留baseline/ledger/index；沒有精確allocation歸因，因此不將所有OS保留量都歸入cache。

| 暫存 | destroy後／全程（五程序median，MiB） | D | 修正後整合版 |
| --- | --- | ---: | ---: |
| 0 | root/window destroy後working set | 407.34 | 414.18 |
| 0 | root/window destroy後private bytes | 387.46 | 394.33 |
| 0 | 全程序peak working set | 448.54 | 439.31 |
| 8 | root/window destroy後working set | 443.50 | 414.20 |
| 8 | root/window destroy後private bytes | 423.85 | 394.09 |
| 8 | 全程序peak working set | 500.40 | 439.13 |

8-group D第1～6次dialog close後private median：266.11、308.05、350.00、391.92、433.96、475.65MiB。

8-group 新版第1～6次dialog close後private median：257.95、287.49、317.04、346.57、376.11、405.90MiB。

兩側OS記憶體仍有保留上升，不能宣稱沒有native leak或已達長期穩態。此benchmark的owner close使用Python owner.destroy；新增Tcl-native ancestor destroy分支由另行原生回歸驗證，不冒稱benchmark直接走過它。仍無真教材、EXE或長期記憶體驗收。

### 可攜原始fixture與重現

任務evidence提供benchmark-replay-v5.zip及逐entry SHA256清冊：包含兩個原始marker（全檔initial bytes）、解碼後原始fixture、固定harness、runner/index、stdout/stderr與獨立audit；20份大raw JSON已在本文件連結的candidate JSON，不再重複打包。原fixture含絕對PDF/artifact路徑，exact replay必須重建原路徑及原bytes；若改路徑重新生成fixture，應重新標示新fixture，不得稱與本次bytes相同。ZIP也不包含正式Global資料。
