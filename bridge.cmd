@echo off
setlocal DisableDelayedExpansion
where py.exe >nul 2>nul
if errorlevel 1 (
  python -B "%~dp0bridge.py" %*
) else (
  py -3 -B "%~dp0bridge.py" %*
)
exit /b %errorlevel%
