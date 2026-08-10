$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Push-Location $projectRoot
try {
    & .\.venv\Scripts\python.exe -m cloak_auth_bridge pair
} finally {
    Pop-Location
}
