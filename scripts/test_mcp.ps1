<#
.SYNOPSIS
    Quick smoke test against local MyPC MCP server (/health + /mcp initialize).
#>
param([int]$Port = 3080)
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $Root ".env"
$ApiKey = ""
if (Test-Path $EnvFile) {
    foreach ($line in Get-Content $EnvFile) {
        if ($line -match "^MYPC_MCP_API_KEY=(.+)$") { $ApiKey = $Matches[1].Trim() }
    }
}

Write-Host "[1/2] Checking http://127.0.0.1:$Port/health ..." -ForegroundColor Cyan
$health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -Method Get
Write-Host ($health | ConvertTo-Json -Compress) -ForegroundColor Green

Write-Host "[2/2] Calling /mcp initialize ..." -ForegroundColor Cyan
$headers = @{
    "Content-Type" = "application/json"
    "Accept"       = "application/json, text/event-stream"
}
if ($ApiKey) { $headers["Authorization"] = "Bearer $ApiKey" }

$body = @{
    jsonrpc = "2.0"
    id      = 1
    method  = "initialize"
    params  = @{
        protocolVersion = "2025-03-26"
        capabilities    = @{}
        clientInfo      = @{ name = "mypc-smoke-test"; version = "1.0" }
    }
} | ConvertTo-Json -Depth 5

$resp = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/mcp" -Method Post -Headers $headers -Body $body
Write-Host "HTTP $($resp.StatusCode) | Mcp-Session-Id: $($resp.Headers['Mcp-Session-Id'])" -ForegroundColor Green
