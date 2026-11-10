@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
if not errorlevel 1 (
  py -3 "04_本地进度网页\server.py" --open
  goto end
)
python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
if not errorlevel 1 (
  python "04_本地进度网页\server.py" --open
  goto end
)
if exist "%USERPROFILE%\.codex\tools\runtime\python.ps1" (
  powershell -NoProfile -File "%USERPROFILE%\.codex\tools\runtime\python.ps1" -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
  if errorlevel 1 goto unavailable
  powershell -NoProfile -File "%USERPROFILE%\.codex\tools\runtime\python.ps1" "04_本地进度网页\server.py" --open
  goto end
)
:unavailable
echo Python 3.10+ is required. Ask your AI assistant to help install Python.
echo Please read 01_客户指南\07_第一次打不开怎么办.md
:end
pause
