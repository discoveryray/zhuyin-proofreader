# Windows 驗證政策與制度過渡

本政策適用採用後的新任務。未收尾的任務維持原 task baseline 凍結的 gate、審查、CI、授權與修正計數，不自動套用新制度。本次制度變更自身的驗收見下方 transition，不能以受審規則替自己放行。

## 支援環境與來源

2026-09-22 唯讀核對 `C:\Work\zhuyin-proofreader-phase4` 的 `啟動_單機注音校對.bat` 與 Python launcher：實際啟動命令使用 `py -3`，解析至 `C:\Users\discoveryray\AppData\Local\Programs\Python\Python313\python.exe`，Python **3.13.0 AMD64**、Windows 11、Tk 8.6。這是當次實測快照；日後啟動器或環境變更須重新確認，不從文件推測。

| 套件 | 實際 launcher 環境 | 隔離測試／CI |
|---|---|---|
| PyMuPDF | 1.26.7 | 1.26.7 |
| openpyxl | 3.1.5 | 3.1.5 |
| fonttools | 4.63.0 | 4.63.0 |
| python-docx | 未安裝 | 1.2.0，補齊既有測試需求 |
| pytest | 未安裝 | 9.1.1，測試工具 |

原專案 `.venv` 不在已查核 launch chain，雖同 Python 3.13.0，其 PyMuPDF 1.28.2／fonttools 4.64.0 不能冒充正式環境。本次於 managed worktree 建立獨立 `.venv`，以 `requirements-ci.txt` 固定直接依賴，不修改原專案、正式 interpreter 或其 `.venv`。間接依賴以當次 `pip freeze`／CI 安裝 logs 記錄，不聲稱完全重現原環境缺少的套件。`requirements.txt` 的使用者安裝範圍不受更動。

本機隔離 `.venv` 首次 GUI 執行出現 `Can't find a usable init.tcl`。唯讀核對原安裝已有 `Python313/tcl/tcl8.6` 和 `Python313/tcl/tk8.6`，僅在驗證 process 設定 `TCL_LIBRARY`／`TK_LIBRARY` 指向這兩個實際目錄後，真實 Tk root 建立成功（8.6.14）。首次失敗／skip logs 保留；後續必要完整驗證採此 process-local 解法及 `PYTHONUTF8=1`／`PYTHONIOENCODING=utf-8`。不複製或偽造 Tcl assets，不修改原 Python、系統環境或 GUI skip 條件，也不將這個本機路徑硬寫到 GitHub runner。

原始證據保存在 task evidence 的 `original-launcher.bat`、`python-launcher.txt`、`actual-launch-python.json`、`original-venv-python.json`，由協調者於 PR evidence 區保存可回查內容。CI 例行只使用 Windows、Python 3.13.0；其他 Python 版本不再是新任務例行門檻。這不是宣布其他版本不相容，也不取消 fingerprint／schema／architecture compatibility tests。

## 測試入口與案例盤點

完整測試入口為 `python -m pytest tests/ -q -rs --junitxml=tmp/ci-pytest.xml`。不得把局部成功當成完整通過；完整失敗須調查，不得以刪 tests／skip／修改資產 truth 取得成功。

`scripts/test_entrypoint_audit.py collect tmp/ci-test-inventory.json` 分別啟動乾淨 Python process 執行 unittest discovery 和 pytest collection，將每個 module／class／method 正規化為同一 identity，保存兩者有序完整清單、pytest-only IDs、順序差異及 GUI IDs。缺少任一 unittest ID、重複 pytest ID、空集合或 discovery error 均失敗；不是只比數量。新增 `load_tests` 會停止要求針對 runner 契約調查，不能靜默略過。

本次盤點 pytest 涵蓋所有 unittest 案例，另有 67 個函式案例，分布於 company semantics、cross-version compatibility、dual evidence、fingerprint、glyph conflict、historical locator、path recovery、semantics epoch、visual context tests。完整 ID 清單是 evidence，案例數不是永久成功標準。

初始化／清理與順序核對：

- 現有 unittest `TestCase` 經 pytest 的 unittest adapter 執行，保留 `setUp`／`tearDown`、`setUpClass`／`tearDownClass`、`addCleanup`／`addClassCleanup` 及 `subTest`。沒有自訂 `load_tests`、module setup/teardown、pytest fixture 或參數化 marker。pytest-only 函式以函式本身的 context managers／臨時路徑處理清理。
- unittest 依 loader 的 module／class／method 排序；pytest 依 collection 與宣告順序管理 class，`TestCase` 方法仍經 unittest loader。共同案例順序確實不同，不宣稱等價順序。pytest 的 setup/call/teardown 報告分段也不同，subtests 額外統計不能當成遺漏或新增 top-level 案例。
- 特別追查 frozen legacy fixture 的 `sys.modules` 登錄與 `addClassCleanup`、approval／inspector 的繼承與顯式 fixture 清理、project repair 的 class-level patches／temporary cleanup、Tk root destroy、reader thread release。這些仍執行原 hooks；本次舊契約的兩個完整入口實跑提供排序／清理差異證據。沒有已證明只有 unittest 才能暴露、而 pytest 漏掉的案例；日後若有具體失敗線索則保留該情境的 targeted unittest／order reproduction，不能靠集合包含推論一切 runner 行為相同。
- GUI classes 從真實 `tk.Tk()` 建立處盤點；`verify-gui` 對照 full pytest JUnit，逐一確認必要 GUI 案例恰好一次成功。所有已收集 pytest identity 也須與 JUnit testcase 精確一對一且全部成功，未知、missing、重複、skip、failure、error 均失敗，沒有一般豁免。Windows Tk 不可用須 BLOCKED，不把 collection 當成執行。GUI 測試內容及既有 skip 行為本身未修改。

## 保留、移除與條件執行

| 驗證 | 原規則 | 新規則／理由 |
|---|---|---|
| 平台／版本 | Windows Python 3.12、3.13 | Windows Python 3.13.0，對應真實 launcher；其他版本有明確相容性任務才執行 |
| 完整入口 | full unittest + full pytest | full pytest；collection identity audit 防止案例遺漏，保留 pytest-only 案例 |
| 修正中的測試 | 必要測試但容易重複 full suite | 優先受影響案例；固定版本後必要完整測試；具體風險才針對性補跑 |
| GUI／runtime／compile／diff | 必要門檻 | 保留；GUI 增加執行證據檢查，runtime manifest truth 不可改寫 |
| 兩位 reviewer | 兩個獨立 session | 保留；新 HEAD 各自對 baseline→HEAD 完整 scope 負責 |
| 未改版補證據 | 每次 full-diff 重讀 | 原 reviewer 核對缺口、原始補充 evidence、既有完整結論；換 reviewer 自行讀足原始材料 |
| 已通過測試／效能證據 | 可沿用但容易無條件重跑 | 未受影響且可追溯才沿用；重跑須有修改影響、失敗線索或明確 gate |
| PR／post-merge CI | 精確 scope 與 merge SHA | 保留；PR synthetic merge 不能代替 actual merge push CI |
| code blocker／修正上限 | 新 commit、最多三輪 | 保留；不能 CI rerun／補證據清除，也不改 task 或 count |

沿用證據須記錄原 tested SHA、命令、環境／依賴、結果、原始 logs、涵蓋範圍，以及新 HEAD／base 的修改影響與仍適用理由。不得把舊測試標成新 SHA 已跑。完整必要 CI 仍綁定當前 integration SHA；跨 HEAD 的沿用不能代替此明確 gate。未受影響的本機 tests／效能 measurements 可供 reviewer 沿用，缺必要證據仍 BLOCKED。

等待 CI、重開 session、整理報告本身不是重跑理由。程式、base、HEAD、scope 全未變且只是補證據時，同原獨立 reviewer 可使用 gate v3 `review_mode=evidence_gap`，保留完整初始 review 鏈及原始 resolution。換 reviewer 使用 `full_diff`，讀足足以自行負責完整 scope 的材料。舊 code BLOCKED 不可在相同 HEAD 被任何 supplement 取代。新 HEAD 的兩輪審查仍涵蓋固定 baseline 的整體影響。

## 本次 transition：不得自降驗收

Task branch：`codex/simplify-validation`；固定 baseline：`1593e7af65596d320b4427f1b15bb2bc0bdc949c`。本次只授權交付未合併 PR，gate authorization operations 為 implement／delegate／test／commit／push／pr，**排除 merge**。

協調者在修改前逐檔保存 baseline 的 AGENTS、完整規範、角色、CI、gate v2 與 hash manifest，存於 `tmp/pr-review-automation/simplify-validation/baseline-contract/`；Git baseline 也可復原同份 bytes。兩輪真實審查、補審與驗收使用凍結 v2 契約，不能用新 v3 的 evidence_gap 放行自身。

CI 僅在 `pull_request`、上述精確 head branch 且 base SHA 恰等於固定 baseline 時，保留 Python 3.12／3.13 及 full unittest／full pytest，維持舊 job／step 名稱供舊 gate 核對。3.13 實際固定 3.13.0；新增 coverage／GUI steps 也必須成功。此有限 transition 不適用其他 branch 或不同 base，不是永久雙矩陣。若本次 base 前進致舊 CI 契約不具證據，STOP／REFRESH_EVIDENCE 交回具體限制，不自行改 baseline、豁免舊驗收或以新 gate 宣稱 PASS。

新 v3 不默認接受 v1／v2 snapshot。其他尚未完成的舊任務維持凍結契約；若未來 CI 變更讓舊必要證據無法取得，應明確 BLOCKED 並交回契約／能力解決條件，不能靜默套用本任務 transition。

外部採用限制：2026-09-22 從 GitHub `GET /repos/discoveryray/zhuyin-proofreader/rules/branches/develop` 核對，ruleset `21894326` 的 strict required status checks 仍要求 `Python 3.12`、`Python 3.13`，並要求 review threads resolution、只允許 merge commit。原始 JSON 保存在 task evidence `develop-rules-before-pr.json`。本次有限雙 job transition 可提供這兩個 context；未來例行單 3.13 的新任務仍會被外部 `Python 3.12` 必要 context 阻擋。Gate 必須保持 `protection_satisfied=false` 並 STOP，不能用 dummy 3.12 成功、改寫證據或改 CI 名稱冒充真正舊 gate。使用者未授權本次修改 GitHub 保護規則，因此只交付此採用限制；要全面啟用新單版本制度，需另外取得明確保護規則遷移授權及驗證，或交回具體 contract／capability resolution，不能宣稱外部門檻已同步。

本次本機須依舊制度完成 full unittest + full pytest、runtime、GUI、compile、diff；3.12 證據由 transition CI 提供。實測命令、耗時及限制保存在 task evidence 並由 PR 回報；未實測的節省不估算或捏造。此文件不是測試 PASS，兩輪審查與 CI 仍須取得原始證據。

## 可同步文件

`docs/CHATGPT_PROJECT_INSTRUCTIONS.md` 是可完整貼入的專案指令；`docs/V58_MASTER_DEVELOPMENT_REVIEW_PLAN_v1.1.md` 是更新後完整規範副本（版本 1.3，保留檔名）。同步時兩者與本政策／gate v3 同版保存；第 3～4 節安全契約不變。只到 PR 的交付須清楚標示未合併，不宣稱已完成 merge／post-merge。
