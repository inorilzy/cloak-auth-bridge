$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$mcpConfig = Join-Path $projectRoot ".cursor\mcp.json"

Write-Host "Cloak Auth Bridge — recommended entry is MCP (extension bridge included)."
Write-Host ""
Write-Host "Do NOT keep a long-running independent 'serve' process if your IDE already runs MCP."
Write-Host "One process provides both:"
Write-Host "  - MCP stdio tools"
Write-Host "  - Chrome extension WebSocket at ws://127.0.0.1:17321"
Write-Host ""
Write-Host "IDE config example: $mcpConfig"
Write-Host "Manual MCP launch (only if your client needs a foreground process):"
Write-Host "  & `"$python`" -m cloak_auth_bridge mcp"
Write-Host ""
Write-Host "Pairing token (clipboard only):"
Write-Host "  & `"$projectRoot\scripts\pair.ps1`""
Write-Host ""
Write-Host "Doctor:"
Write-Host "  & `"$python`" -m cloak_auth_bridge doctor"
Write-Host ""

if (-not (Test-Path $python)) {
    throw "Missing venv python: $python  (run scripts\setup.ps1 first)"
}

& $python -m cloak_auth_bridge doctor
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "Independent 'serve' remains available for emergencies only:"
Write-Host "  & `"$python`" -m cloak_auth_bridge serve"
Write-Host "Stop it before starting MCP mode to avoid port 17321 conflicts."
