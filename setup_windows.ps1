$ErrorActionPreference = 'Stop'
$sheepProject = $PSScriptRoot
$sheepPython = Join-Path $sheepProject 'venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $sheepPython)) {
    python -m venv (Join-Path $sheepProject 'venv')
    if ($LASTEXITCODE -ne 0) { throw '创建虚拟环境失败' }
}
& $sheepPython -m pip install -r (Join-Path $sheepProject 'requirements-windows.txt') -r (Join-Path $sheepProject 'requirements-constraints.txt')
if ($LASTEXITCODE -ne 0) { throw '安装依赖失败' }
& $sheepPython -m pip check
if ($LASTEXITCODE -ne 0) { throw '依赖校验失败' }
Write-Host '配置完成。运行 .\build_clicker.ps1 构建程序；测试：venv\Scripts\python.exe -m unittest discover -s tests'
