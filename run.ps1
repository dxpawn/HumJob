<#
  MouthTranscriber launcher (PowerShell).
  Run from a terminal:   ./run.ps1
  (If blocked by execution policy:  powershell -ExecutionPolicy Bypass -File run.ps1)

  Starts the web app at http://localhost:8000 and opens your browser.
  Press Ctrl+C to stop the server.
#>
Set-Location -Path $PSScriptRoot

$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "[!] .venv not found - falling back to system 'python'." -ForegroundColor Yellow
    Write-Host "    If this fails:  python -m venv .venv ; .venv\Scripts\pip install -r requirements.txt"
    $py = "python"
}

Write-Host ""
Write-Host "  MouthTranscriber  ->  http://localhost:8000" -ForegroundColor Green
Write-Host "  (Ctrl+C to stop)" -ForegroundColor DarkGray
Write-Host ""

# Open the browser once the port has had a moment to come up.
Start-Job { Start-Sleep -Seconds 2; Start-Process "http://localhost:8000" } | Out-Null

# --reload = pick up code edits without restarting this window (needs watchfiles).
& $py -m uvicorn server.app:app --port 8000 --reload
