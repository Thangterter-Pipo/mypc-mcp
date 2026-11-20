<#
.SYNOPSIS
    Configure Tailscale Funnel (public HTTPS) or Serve (private Tailnet HTTPS) for MyPC MCP.
.PARAMETER Mode
    "funnel" (default — required for ChatGPT/Claude/Gemini Web) or "serve" (private Tailnet only).
.PARAMETER Port
    Local MCP server port (default 3080).
#>
param(
    [ValidateSet("funnel", "serve")]
    [string]$Mode = "funnel",
    [int]$Port = 3080
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $Root ".env"

$ts = Get-Command tailscale -ErrorAction SilentlyContinue
if (-not $ts) {
    $fallback = "C:\Program Files\Tailscale\tailscale.exe"
    if (Test-Path $fallback) { $ts = $fallback }
    else {
        Write-Error "Tailscale CLI not found. Install Tailscale first: https://tailscale.com/download"
        exit 1
    }
} else {
    $ts = $ts.Source
}

Write-Host "Checking Tailscale status..." -ForegroundColor Cyan
$statusJson = & $ts status --json | ConvertFrom-Json
$dnsName = $statusJson.Self.DNSName.TrimEnd(".")
if (-not $dnsName) {
    Write-Error "Tailscale is not logged in or MagicDNS is disabled. Run 'tailscale up' and enable MagicDNS in Tailscale Admin Console."
    exit 1
}

$baseUrl = "https://$dnsName"
Write-Host "Detected Tailscale URL: $baseUrl" -ForegroundColor Green

Write-Host "Enabling 'tailscale $Mode --bg $Port'..." -ForegroundColor Cyan
& $ts $Mode --bg $Port

if (Test-Path $EnvFile) {
    $content = Get-Content $EnvFile -Raw
    if ($content -match "(?m)^MYPC_MCP_BASE_URL=.*$") {
        $content = $content -replace "(?m)^MYPC_MCP_BASE_URL=.*$", "MYPC_MCP_BASE_URL=$baseUrl"
    } else {
        $content += "`nMYPC_MCP_BASE_URL=$baseUrl`n"
    }
    if ($content -match "(?m)^MYPC_MCP_TAILSCALE_MODE=.*$") {
        $content = $content -replace "(?m)^MYPC_MCP_TAILSCALE_MODE=.*$", "MYPC_MCP_TAILSCALE_MODE=$Mode"
    }
    Set-Content -Path $EnvFile -Value $content -Encoding UTF8
    Write-Host "Updated MYPC_MCP_BASE_URL in .env" -ForegroundColor Green
}

Write-Host "`nYour MCP endpoint is ready:" -ForegroundColor Green
Write-Host "  $baseUrl/mcp" -ForegroundColor Yellow
Write-Host "`nStart the server with: .\start_mypc.bat start" -ForegroundColor Cyan
