@echo off
REM ---------------------------------------------------------------------------
REM MouthTranscriber launcher (Windows). Double-click, or run:  run.bat
REM Starts the web app at http://localhost:8000 and opens your browser.
REM Press Ctrl+C in this window to stop the server.
REM ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo [!] .venv not found - falling back to system "python".
    echo     If this fails, create the venv:  python -m venv .venv  ^&^&  .venv\Scripts\pip install -r requirements.txt
    set "PY=python"
)

echo.
echo   MouthTranscriber  ->  http://localhost:8000
echo   (Ctrl+C to stop)
echo.

REM Open the browser shortly after the server has had a moment to bind the port.
REM (PowerShell one-liner avoids the nested-quote parsing bug that cmd's `start` hits.)
start "" /min powershell -NoProfile -Command "Start-Sleep -Seconds 2; Start-Process 'http://localhost:8000'"

REM --reload = pick up code edits without restarting this window (needs watchfiles).
"%PY%" -m uvicorn server.app:app --port 8000 --reload
echo.
echo Server stopped.
pause
