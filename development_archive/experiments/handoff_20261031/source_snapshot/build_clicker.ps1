$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'build_prop_solver.ps1')
$sheepCompiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $sheepCompiler)) { throw '没有找到 .NET Framework C# 编译器' }
$sheepSource = Join-Path $PSScriptRoot 'SheepClicker.cs'
$sheepOutput = Join-Path $PSScriptRoot 'SheepClicker.exe'
& $sheepCompiler /nologo /target:winexe /optimize+ /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Web.Extensions.dll "/out:$sheepOutput" $sheepSource
if ($LASTEXITCODE -ne 0) { throw '编译失败' }
$sheepDiagnostics = Join-Path $PSScriptRoot 'SheepClicker.Diagnostics.exe'
& $sheepCompiler /nologo /target:exe /optimize+ /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Web.Extensions.dll "/out:$sheepDiagnostics" $sheepSource
if ($LASTEXITCODE -ne 0) { throw '诊断程序编译失败' }
$sheepPlayback = Join-Path $PSScriptRoot 'SolverPlayback.exe'
& $sheepCompiler /nologo /target:exe /main:SolverPlayback /optimize+ /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Web.Extensions.dll "/out:$sheepPlayback" $sheepSource (Join-Path $PSScriptRoot 'SolverPlayback.cs') (Join-Path $PSScriptRoot 'ColorRules.cs') (Join-Path $PSScriptRoot 'EnergyRules.cs') (Join-Path $PSScriptRoot 'RollingPlanner.cs')
if ($LASTEXITCODE -ne 0) { throw '完整答案执行器编译失败' }
& $sheepCompiler /nologo /target:exe /main:RollingPlanner /optimize+ /reference:System.Web.Extensions.dll "/out:$(Join-Path $PSScriptRoot 'RollingPlanner.exe')" (Join-Path $PSScriptRoot 'RollingPlanner.cs') (Join-Path $PSScriptRoot 'EnergyRules.cs') (Join-Path $PSScriptRoot 'ColorRules.cs')
if ($LASTEXITCODE -ne 0) { throw '动态牌槽规划器编译失败' }
& $sheepCompiler /nologo /target:exe /main:EnergySearch /optimize+ /reference:System.Web.Extensions.dll "/out:$(Join-Path $PSScriptRoot 'EnergySearch.exe')" (Join-Path $PSScriptRoot 'EnergySearch.cs') (Join-Path $PSScriptRoot 'EnergyRules.cs') (Join-Path $PSScriptRoot 'ColorRules.cs')
if ($LASTEXITCODE -ne 0) { throw '电量规则求解器编译失败' }
$sheepCompletion = Join-Path $PSScriptRoot 'CompletionCheck.exe'
& $sheepCompiler /nologo /target:exe /main:CompletionCheck /optimize+ /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Web.Extensions.dll "/out:$sheepCompletion" $sheepSource (Join-Path $PSScriptRoot 'CompletionCheck.cs')
if ($LASTEXITCODE -ne 0) { throw '结算动画执行器编译失败' }
$sheepFastSolver = Join-Path $PSScriptRoot 'FastOriginalSolver.exe'
& $sheepCompiler /nologo /target:exe /optimize+ /reference:System.Web.Extensions.dll "/out:$sheepFastSolver" (Join-Path $PSScriptRoot 'FastOriginalSolver.cs') (Join-Path $PSScriptRoot 'ColorRules.cs') (Join-Path $PSScriptRoot 'BeamSearch.cs') (Join-Path $PSScriptRoot 'StaticBeamSearch.cs') (Join-Path $PSScriptRoot 'RolloutSearch.cs') (Join-Path $PSScriptRoot 'GuidedSearch.cs') (Join-Path $PSScriptRoot 'CrushPlanning.cs') (Join-Path $PSScriptRoot 'SearchFrontier.cs') (Join-Path $PSScriptRoot 'FreezeSearch.cs') (Join-Path $PSScriptRoot 'TightSearch.cs') (Join-Path $PSScriptRoot 'DeadStateCache.cs') (Join-Path $PSScriptRoot 'MacroSearch.cs')
if ($LASTEXITCODE -ne 0) { throw '原策略加速器编译失败' }
Copy-Item -LiteralPath $sheepFastSolver -Destination (Join-Path $PSScriptRoot 'StaticFastSolver.exe') -Force
$sheepSessionLauncher = Join-Path $PSScriptRoot 'DesktopSessionLauncher.exe'
& $sheepCompiler /nologo /target:exe /optimize+ /reference:Microsoft.CSharp.dll "/out:$sheepSessionLauncher" (Join-Path $PSScriptRoot 'DesktopSessionLauncher.cs')
if ($LASTEXITCODE -ne 0) { throw 'Windows 会话启动器编译失败' }
$sheepApplication = Join-Path $PSScriptRoot '羊羊助手.exe'
$sheepAppPaths = @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -like '羊羊助手*' } | Select-Object -ExpandProperty Path)
foreach ($sheepAppName in @('羊羊助手.exe','羊羊助手.更新.exe','羊羊助手.容量版.exe','羊羊助手.修复版.exe','羊羊助手.稳定版.exe','羊羊助手.原版算法.exe')) {
    $sheepCandidate = Join-Path $PSScriptRoot $sheepAppName
    if ($sheepAppPaths -notcontains $sheepCandidate) { $sheepApplication = $sheepCandidate; break }
}
if ($sheepAppPaths -contains $sheepApplication) { throw '应用的编译目标正在运行，请先暂停托管并更新应用' }
& $sheepCompiler /nologo /target:winexe /main:SheepAutoApp /optimize+ /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Web.Extensions.dll "/win32icon:$(Join-Path $PSScriptRoot 'assets\sheep.ico')" "/out:$sheepApplication" $sheepSource (Join-Path $PSScriptRoot 'SheepAutoApp.cs')
if ($LASTEXITCODE -ne 0) { throw '应用编译失败' }
$sheepShortcutPath = Join-Path ([Environment]::GetFolderPath('Desktop')) '羊羊助手.lnk'
if (Test-Path -LiteralPath $sheepShortcutPath) {
    $sheepShell = New-Object -ComObject WScript.Shell
    $sheepShortcut = $sheepShell.CreateShortcut($sheepShortcutPath)
    $sheepShortcut.TargetPath = $sheepApplication
    $sheepShortcut.WorkingDirectory = $PSScriptRoot
    $sheepShortcut.Save()
}
Write-Host "已生成 $sheepOutput"
