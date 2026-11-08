@echo off
rem ======================================================================
rem  SILM Ground Station - double-click this file to start it (Windows).
rem  The first start sets up its own Python environment (needs internet
rem  once, takes a minute). It then opens in your web browser.
rem ======================================================================
title SILM Ground Station
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (where python >nul 2>nul && set "PY=python")
if not defined PY goto nopython

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo  First start: setting up the Ground Station. This needs internet once
  echo  and takes about a minute...
  echo.
  %PY% -m venv .venv || goto nopython
  ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
  ".venv\Scripts\python.exe" -m pip install -r groundstation\requirements.txt || goto failed
)

".venv\Scripts\python.exe" -m groundstation %*
if errorlevel 1 goto failed
goto end

:nopython
echo.
echo  Python was not found. Install Python 3 from https://www.python.org/downloads/
echo  (tick "Add python.exe to PATH" during the install), then double-click this file again.
echo.
pause
goto end

:failed
echo.
echo  The Ground Station stopped with an error (see above).
echo  If the setup failed, delete the ".venv" folder here and try again with internet.
echo.
pause

:end
