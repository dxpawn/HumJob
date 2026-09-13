<#
  Redeploy HumJob's current committed state to the Hugging Face Space.

  Prerequisite (one-time, see DEPLOY.md): the Space exists, the DEEPSEEK_API_KEY
  secret is set, and the `hf` git remote is configured
  (git remote add hf https://huggingface.co/spaces/<user>/humjob).

  Usage: commit your changes to main, then from the project root run:
      ./deploy/hf/redeploy.ps1

  It rebuilds a throwaway `hf-space` branch from your current branch with the Space's
  README + requirements at the root, force-pushes it to the Space, and returns you to
  your branch. HF rebuilds automatically. Your GitHub main README/requirements stay clean.
#>
Set-Location -Path (Join-Path $PSScriptRoot "..\..")

if (-not ((git remote) -contains "hf")) {
    Write-Host "[!] No 'hf' git remote yet. Do the one-time setup in deploy/hf/DEPLOY.md first:" -ForegroundColor Yellow
    Write-Host "    git remote add hf https://huggingface.co/spaces/<your-username>/humjob" -ForegroundColor Yellow
    exit 1
}

if (git status --porcelain) {
    Write-Host "[!] You have uncommitted changes. Commit them to your branch first so they deploy:" -ForegroundColor Yellow
    git status --short
    exit 1
}

$branch = (git rev-parse --abbrev-ref HEAD)
if ($branch -eq "hf-space") {
    Write-Host "[!] You are on the hf-space branch. Switch to main first." -ForegroundColor Yellow
    exit 1
}

Write-Host "Rebuilding hf-space from '$branch' and pushing to the Space..." -ForegroundColor Cyan
git branch -D hf-space 2>$null | Out-Null
git checkout -b hf-space
Copy-Item (Join-Path $PSScriptRoot "README.md") "README.md" -Force
Copy-Item (Join-Path $PSScriptRoot "requirements-hf.txt") "requirements.txt" -Force
git add -A
git commit -m "deploy update" | Out-Null
git push hf hf-space:main --force
git checkout $branch

Write-Host ""
Write-Host "  Pushed. Watch the Space rebuild on its Hugging Face page." -ForegroundColor Green
