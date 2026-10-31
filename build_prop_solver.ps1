$ErrorActionPreference = 'Stop'
# 移出候选求解器：与 FastOriginalSolver 共用 native\solver 源码，仅打开 PROP_CHECKPOINT
# （检查点允许暂时多出 3 张虚拟牌，用于移出归约；真实规则仍由 Python 端双回放验收）。
$root = $PSScriptRoot
$csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$sources = @(Get-ChildItem -LiteralPath (Join-Path $root 'native\solver') -Filter '*.cs' | Select-Object -ExpandProperty FullName) +
    (Join-Path $root 'native\rules\ColorRules.cs')
& $csc /nologo /target:exe /optimize+ /define:PROP_CHECKPOINT /reference:System.Web.Extensions.dll "/out:$(Join-Path $root 'PropFastSolver.exe')" @sources
if ($LASTEXITCODE -ne 0) { throw '移出候选求解器编译失败' }
