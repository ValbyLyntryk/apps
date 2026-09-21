@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>&1
if %ERRORLEVEL%==0 (
  py -3 build_exe.py %*
  goto :finish
)

where python >nul 2>&1
if %ERRORLEVEL%==0 (
  python build_exe.py %*
  goto :finish
)

echo.
echo Python 3 was not found.
echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
echo Then double-click this file again.
pause
exit /b 1

:finish
if errorlevel 1 (
  echo.
  echo Build failed.
  pause
  exit /b 1
)
echo.
echo Done. Your portable program is in the dist folder ^(EmailArchive.exe^).
echo You can copy that file to another Windows PC. Python is not required there.
pause
