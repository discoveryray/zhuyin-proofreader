@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 -X utf8 standalone_gui.py
) else (
  python -X utf8 standalone_gui.py
)
if errorlevel 1 pause
endlocal
