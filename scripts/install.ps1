<#
.SYNOPSIS
    One-click setup for MyPC MCP (creates .venv, installs dependencies, generates .env).
#>
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "[1/3] Creating Python virtual environment (.venv)..." -ForegroundColor Cyan
if (-not (Test-Path ".venv")) {
    python -m venv .venv
}

Write-Host "[2/3] Installing dependencies..." -ForegroundColor Cyan
& ".venv\Scripts\python.exe" -m pip install --upgrade pip
& ".venv\Scripts\python.exe" -m pip install -r requirements.txt

Write-Host "[3/3] Preparing .env..." -ForegroundColor Cyan
if (-not (Test-Path ".env")) {
    $apiKey = & ".venv\Scripts\python.exe" -c "import secrets; print(secrets.token_urlsafe(32))"
    $pin    = & ".venv\Scripts\python.exe" -c "import secrets; print(f'{secrets.randbelow(900000)+100000}')"
    $tpl = Get-Content ".env.example" -Raw
    $tpl = $tpl -replace "MYPC_MCP_API_KEY=change-me-to-a-long-random-string", "MYPC_MCP_API_KEY=$apiKey"
    $tpl = $tpl -replace "MYPC_MCP_CONSENT_PIN=change-me-pin", "MYPC_MCP_CONSENT_PIN=$pin"
    Set-Content -Path ".env" -Value $tpl -Encoding UTF8
    Write-Host "Generated .env with random API key and Consent PIN: $pin" -ForegroundColor Green
} else {
    Write-Host ".env already exists — keeping existing configuration." -ForegroundColor Yellow
}

Write-Host "`nDone! Next step: run .\scripts\tailscale_setup.ps1" -ForegroundColor Green
