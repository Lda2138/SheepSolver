$ErrorActionPreference = 'Stop'
$propCompiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$propSources = Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot 'prop_native') -Filter '*.cs' | Select-Object -ExpandProperty FullName
& $propCompiler /nologo /target:exe /optimize+ /reference:System.Web.Extensions.dll "/out:$(Join-Path $PSScriptRoot 'PropFastSolver.exe')" $propSources
if ($LASTEXITCODE -ne 0) { throw '移出候选求解器编译失败' }
