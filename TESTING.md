# Testing

正式 runtime dependencies 定義於 `requirements.txt`。例行驗證使用 `requirements-ci.txt` 的直接依賴版本；來源是實測 Windows launcher Python 3.13.0 環境，加上測試需要的 python-docx／pytest。原專案 `.venv` 不等於 launcher 環境。完整版本來源、案例盤點、證據沿用及制度過渡見 [驗證政策](docs/VALIDATION_POLICY.md)。`requirements-dev.txt` 仍可作一般開發安裝入口，但不能據此宣稱與固定驗證環境相同。

## Windows 本機測試環境

在 repository root 執行：

```powershell
py -3.13 --version  # 必須確認實際為 3.13.0，不能只由 selector 推定
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-ci.txt
python -m pip freeze
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
```

## GitHub hosted CI

GitHub hosted CI 例行使用 Windows runner 與 Python 3.13.0，並為所有 Python validation steps 設定 UTF-8 Python I/O environment。CI 執行 runtime asset integrity、逐一測試 identity collection audit、完整 pytest、GUI 執行證據檢查、compileall、committed whitespace validation 與 working-tree cleanliness check。

`ReviewGuiVisibleFooterTests` 與其他實際建立 Tk root 的案例必須真正執行；JUnit 缺案例、skip、error 或 failure 不放行。隔離 venv 若找不到既有 Tcl 初始化檔，先核對原安裝，僅對測試 process 設定 `TCL_LIBRARY`／`TK_LIBRARY` 指向實際 `tcl/tcl8.6`／`tcl/tk8.6`；詳見政策中的本機實測，不修改原 Python 或 GUI skip 條件。

本次 `codex/simplify-validation` 在固定 baseline 的 PR 使用 bounded transition，仍執行舊 Python 3.12／3.13、full unittest／pytest 與舊 gate v2／兩輪 full-diff 審查，不能拿新制度降低自身驗收。未收尾舊任務保留其凍結契約，不自動套用新規則。

## Interactive Windows 本機 merge／release gate

在可互動的 Windows desktop 執行完整測試，包括 `ReviewGuiVisibleFooterTests`：

```powershell
python scripts/test_entrypoint_audit.py collect tmp/ci-test-inventory.json
python -m pytest tests/ -q -rs --junitxml=tmp/ci-pytest.xml
python scripts/test_entrypoint_audit.py verify-gui tmp/ci-test-inventory.json --junit tmp/ci-pytest.xml
python -m compileall -q -x "(^|[\\/])(\.venv|tmp)([\\/]|$)" .
git diff --check
git status --short
```

pytest 是唯一例行完整入口，涵蓋 unittest cases 與 pytest-only functions。修正期间先受影響 tests，固定版本後完成必要完整測試；完整失敗必須調查。未受影響、可追溯證據可依政策沿用，每次重跑須有修改影響、具體失敗線索或明確 gate，不因等待 CI、重開 session 或整理報告重跑。若出現 runner 特有風險，保留具體 targeted unittest／order reproduction。

Runtime asset integrity failure 不得以重算 SHA 或用目前檔案內容覆寫 manifest 的方式解決；必須先查明 asset 與 manifest 的正確版本。

缺少真實教材或大型外部 fixture 時，不得聲稱已完成 real textbook acceptance。GitHub CI 只提供開發測試基線，不能取代正式 release 前的 real textbook acceptance。
