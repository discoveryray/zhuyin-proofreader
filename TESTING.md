# Testing

正式 runtime dependencies 定義於 `requirements.txt`。開發與 CI dependencies 定義於 `requirements-dev.txt`；後者會引用正式 dependencies，並加入完整測試所需的 `pytest`。

## Windows 本機測試環境

在 repository root 執行：

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
```

## 正式 merge 前的本機驗證

```powershell
python -m unittest discover -s tests -p "test_*.py"
python -m pytest -q
python -m compileall -q .
git diff --check
git status --short
```

即使 unittest suite 通過，也不得省略 pytest suite，因為 repository 另有 pytest-style function tests。

Runtime asset integrity failure 不得以重算 SHA 或用目前檔案內容覆寫 manifest 的方式解決；必須先查明 asset 與 manifest 的正確版本。

缺少真實教材或大型外部 fixture 時，不得聲稱已完成 real textbook acceptance。GitHub CI 只提供開發測試基線，不能取代正式 release 前的 real textbook acceptance。
