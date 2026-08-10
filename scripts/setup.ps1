$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$codexPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
$python = if (Test-Path -LiteralPath $codexPython) {
    $codexPython
} elseif ($pythonCommand) {
    $pythonCommand.Source
} else {
    ""
}
if (-not (Test-Path -LiteralPath $python)) {
    throw "未找到 Python 3.11+ 运行时"
}

Push-Location $projectRoot
try {
    & $python -m venv --clear .venv
    if ($LASTEXITCODE -ne 0) {
        throw "创建 Python 虚拟环境失败"
    }
    & .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
    if ($LASTEXITCODE -ne 0) {
        throw "安装 Python 依赖失败"
    }
    Write-Host "安装完成。运行 scripts\start.ps1 启动 daemon。"
} finally {
    Pop-Location
}
