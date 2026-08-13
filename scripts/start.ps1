$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$mcpConfig = Join-Path $projectRoot ".cursor\mcp.json"

Write-Host "Cloak Browser Auth — run the auth bridge independently from MCP."
Write-Host ""
Write-Host "Long-running auth bridge (one instance only):"
Write-Host "  & `"$python`" -m cloak_browser_auth serve"
Write-Host "  Chrome extension WebSocket: ws://127.0.0.1:17321"
Write-Host ""
Write-Host "IDE config example: $mcpConfig"
Write-Host "MCP client (started by the IDE, or manually):"
Write-Host "  & `"$python`" -m cloak_browser_auth mcp"
Write-Host ""
Write-Host "Pairing token (clipboard only):"
Write-Host "  & `"$projectRoot\scripts\pair.ps1`""
Write-Host ""
Write-Host "Doctor:"
Write-Host "  & `"$python`" -m cloak_browser_auth doctor"
Write-Host ""

if (-not (Test-Path $python)) {
    throw "Missing venv python: $python  (run scripts\setup.ps1 first)"
}

& $python -m cloak_browser_auth doctor
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
