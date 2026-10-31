$ErrorActionPreference = 'Stop'
# 构建全部本地程序。输出 exe 放在项目根目录（Python 端按根目录查找）。
$root = $PSScriptRoot
$csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $csc)) { throw '没有找到 .NET Framework C# 编译器' }
$native = Join-Path $root 'native'
# 始终返回数组（单个文件也不展开成字符串），避免 + 变成字符串拼接
function Src([string[]]$names) { ,@($names | ForEach-Object { Join-Path $native $_ }) }
function Build([string]$label, [string[]]$options, [string[]]$sources) {
    & $csc /nologo /optimize+ @options @sources
    if ($LASTEXITCODE -ne 0) { throw "$label 编译失败" }
}
$ui = @('/reference:System.Windows.Forms.dll', '/reference:System.Drawing.dll', '/reference:System.Web.Extensions.dll')
$rules = Src 'rules\ColorRules.cs', 'rules\EnergyRules.cs'
$clicker = Src 'clicker\SheepClicker.cs'
$solver = Src 'solver\FastOriginalSolver.cs', 'solver\BeamSearch.cs', 'solver\StaticBeamSearch.cs', 'solver\RolloutSearch.cs',
    'solver\GuidedSearch.cs', 'solver\CrushPlanning.cs', 'solver\SearchFrontier.cs', 'solver\FreezeSearch.cs',
    'solver\TightSearch.cs', 'solver\DeadStateCache.cs', 'solver\MacroSearch.cs'

& (Join-Path $root 'build_prop_solver.ps1')

Build '点击器' (@('/target:winexe') + $ui + "/out:$(Join-Path $root 'SheepClicker.exe')") $clicker
Build '诊断程序' (@('/target:exe') + $ui + "/out:$(Join-Path $root 'SheepClicker.Diagnostics.exe')") $clicker
Build '完整答案执行器' (@('/target:exe', '/main:SolverPlayback') + $ui + "/out:$(Join-Path $root 'SolverPlayback.exe')") `
    ($clicker + (Src 'clicker\SolverPlayback.cs', 'planner\RollingPlanner.cs') + $rules)
Build '动态牌槽规划器' @('/target:exe', '/main:RollingPlanner', '/reference:System.Web.Extensions.dll', "/out:$(Join-Path $root 'RollingPlanner.exe')") `
    ((Src 'planner\RollingPlanner.cs') + $rules)
Build '电量规则求解器' @('/target:exe', '/main:EnergySearch', '/reference:System.Web.Extensions.dll', "/out:$(Join-Path $root 'EnergySearch.exe')") `
    ((Src 'planner\EnergySearch.cs') + $rules)
Build '结算动画执行器' (@('/target:exe', '/main:CompletionCheck') + $ui + "/out:$(Join-Path $root 'CompletionCheck.exe')") `
    ($clicker + (Src 'clicker\CompletionCheck.cs'))
$fast = Join-Path $root 'FastOriginalSolver.exe'
Build '原策略加速器' @('/target:exe', '/reference:System.Web.Extensions.dll', "/out:$fast") ($solver + (Src 'rules\ColorRules.cs'))
Copy-Item -LiteralPath $fast -Destination (Join-Path $root 'StaticFastSolver.exe') -Force
Build 'Windows 会话启动器' @('/target:exe', '/reference:Microsoft.CSharp.dll', "/out:$(Join-Path $root 'DesktopSessionLauncher.exe')") `
    (Src 'clicker\DesktopSessionLauncher.cs')

$app = Join-Path $root '羊羊助手.exe'
$running = @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -like '羊羊助手*' } | Select-Object -ExpandProperty Path)
if ($running -contains $app) { throw '羊羊助手.exe 正在运行，请先暂停托管并关闭应用再构建' }
Build '应用' (@('/target:winexe', '/main:SheepAutoApp') + $ui + "/win32icon:$(Join-Path $root 'assets\sheep.ico')" + "/out:$app") `
    ($clicker + (Src 'app\SheepAutoApp.cs'))

$shortcut = Join-Path ([Environment]::GetFolderPath('Desktop')) '羊羊助手.lnk'
if (Test-Path -LiteralPath $shortcut) {
    $link = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcut)
    $link.TargetPath = $app
    $link.WorkingDirectory = $root
    $link.Save()
}
Write-Host '构建完成'
