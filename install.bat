@echo off
chcp 65001 >nul
setlocal
set "PS=powershell"
where pwsh >nul 2>nul
if errorlevel 1 goto useps
set "PS=pwsh"
:useps
%PS% -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
set "CODE=%ERRORLEVEL%"
echo.
if "%CODE%"=="0" goto ok
echo Install failed, exit code %CODE%.
goto end
:ok
echo CapRouter installed. You can close this window.
:end
pause
exit /b %CODE%
