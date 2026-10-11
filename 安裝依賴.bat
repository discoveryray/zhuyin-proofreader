@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 -X utf8 -m pip install --disable-pip-version-check -r requirements-release-lock.txt
  if errorlevel 1 exit /b 1
) else (
  python -X utf8 -m pip install --disable-pip-version-check -r requirements-release-lock.txt
  if errorlevel 1 exit /b 1
)
pause
endlocal
