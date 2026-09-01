<#
  HumJob demo tunnel (PowerShell).

  Puts the LOCAL app (http://localhost:8000) on a temporary public HTTPS URL using a
  Cloudflare quick tunnel, so you can open the demo on a phone or any other device,
  from any network. The HTTPS is what lets the phone's browser grant microphone
  access for the vocal tools (plain http on a LAN IP is blocked by the browser).

  Usage:
    1. Start the app in another window:   ./run.ps1   (or run.bat)
    2. In this folder, run:               ./run-tunnel.ps1
       (If blocked by execution policy:   powershell -ExecutionPolicy Bypass -File run-tunnel.ps1)
    3. Open the https://<something>.trycloudflare.com URL it prints, on your phone.

  Ctrl+C stops the tunnel; the app keeps running. No Cloudflare account or login is
  needed for a quick tunnel. The URL is temporary and changes every run.
#>
Set-Location -Path $PSScriptRoot

$port = 8000

# Warn early if the app is not up yet (the tunnel would just 502 until it is).
$listening = $false
try {
    $listening = [bool](Test-NetConnection -ComputerName 127.0.0.1 -Port $port `
        -InformationLevel Quiet -WarningAction SilentlyContinue)
} catch {}
if (-not $listening) {
    Write-Host "[!] Nothing is listening on http://localhost:$port yet." -ForegroundColor Yellow
    Write-Host "    Start the app first (./run.ps1 or run.bat), then re-run this script." -ForegroundColor Yellow
    Write-Host ""
}

# Find cloudflared: on PATH, or a local copy, else download the official Windows build.
$cf = (Get-Command cloudflared -ErrorAction SilentlyContinue).Source
if (-not $cf) {
    $local = Join-Path $PSScriptRoot "cloudflared.exe"
    if (-not (Test-Path $local)) {
        Write-Host "cloudflared.exe not found; downloading it (~20 MB) from Cloudflare's official release..." -ForegroundColor Cyan
        $url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
        try {
            Invoke-WebRequest -Uri $url -OutFile $local
        } catch {
            Write-Host "[!] Download failed: $($_.Exception.Message)" -ForegroundColor Red
            Write-Host "    Grab it manually from https://github.com/cloudflare/cloudflared/releases/latest" -ForegroundColor Yellow
            Write-Host "    (the cloudflared-windows-amd64.exe asset), save it here as cloudflared.exe, and re-run." -ForegroundColor Yellow
            exit 1
        }
    }
    $cf = $local
}

Write-Host ""
Write-Host "  Opening a public HTTPS tunnel to http://localhost:$port ..." -ForegroundColor Green
Write-Host "  Find the https://<...>.trycloudflare.com line below and open it on your phone." -ForegroundColor Green
Write-Host "  (Ctrl+C to stop the tunnel; the app keeps running.)" -ForegroundColor DarkGray
Write-Host ""

& $cf tunnel --url "http://localhost:$port"
