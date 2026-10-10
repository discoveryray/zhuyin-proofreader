@echo off
setlocal
chcp 65001 >nul
set "TASK_ROOT=C:\Work\zhuyin-proofreader-phase4\tmp\excel-content-proof-capacity-worktree\"
set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
if not exist "%PYTHON_EXE%" (
  echo 找不到既有 Python 3.13.0：%PYTHON_EXE%
  pause
  exit /b 1
)
if not exist "%TASK_ROOT%standalone_gui.py" (
  echo 修正版程式不在此啟動器的資料夾內：%TASK_ROOT%
  pause
  exit /b 1
)
"%PYTHON_EXE%" -X utf8 -c "import sys; sys.exit(0 if sys.version_info[:3] == (3, 13, 0) else 1)"
if errorlevel 1 (
  echo 此啟動器需要 Python 3.13.0。
  pause
  exit /b 1
)
cd /d "%TASK_ROOT%"
if errorlevel 1 (
  echo 無法進入修正版程式資料夾。
  pause
  exit /b 1
)
echo 修正版程式目錄：%CD%
echo 本機修正版：單筆人工判定保存效能修正；保留來源核對及衝突裁決。
"%PYTHON_EXE%" -X utf8 "%TASK_ROOT%standalone_gui.py"
set "RUN_RESULT=%ERRORLEVEL%"
if not "%RUN_RESULT%"=="0" pause
exit /b %RUN_RESULT%
