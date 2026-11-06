@echo off
setlocal
REM One-click pull of the Windows clone containing this file.
REM This updates THIS PC's clone, not a separate Raspberry Pi.
cd /d "%~dp0"
echo Robot code: pulling latest main from GitHub
echo Folder: %CD%
echo.
where git >nul 2>nul
if errorlevel 1 (
  echo ERROR: Git not found in PATH.
  goto failed
)
for /f "delims=" %%B in ('git branch --show-current 2^>nul') do set "CURRENT_BRANCH=%%B"
if not "%CURRENT_BRANCH%"=="main" (
  echo ERROR: You must be on main. No update attempted.
  goto failed
)
git pull --ff-only origin main
if errorlevel 1 goto failed
echo.
echo SUCCESS: current commit:
git log -1 --format="%%h %%s"
echo.
pause
exit /b 0
:failed
echo.
echo Pull stopped safely. No forced reset or overwrite attempted.
pause
exit /b 1
