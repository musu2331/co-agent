@echo off
setlocal DisableDelayedExpansion
wscript.exe "%~dp0launch-webui.vbs"
exit /b %errorlevel%
