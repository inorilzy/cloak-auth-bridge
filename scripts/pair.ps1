$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Push-Location $projectRoot
try {
    & .\.venv\Scripts\python.exe -m cloak_browser_auth pair
} finally {
    Pop-Location
}
