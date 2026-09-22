# ==============================================================================
# install_task_scheduler.ps1 - Windows 任务计划程序安装与管理脚本（幂等）
#
# 注册一个名为 NewsPaperAuto 的每日计划任务，动作是调用 scripts\run_scan.ps1
# （该脚本以 --no-state 无状态运行，不读写 state\ 与 hits\，可与 GitHub Actions 并存）。
#
# 用法：
#   .\scripts\install_task_scheduler.ps1 install [HH:mm ...]   注册并启用（默认 07:00）
#   .\scripts\install_task_scheduler.ps1 uninstall             注销
#   .\scripts\install_task_scheduler.ps1 status                查看状态与最近日志
#   .\scripts\install_task_scheduler.ps1 run-now               立即触发一次
#   .\scripts\install_task_scheduler.ps1 -Help                 查看帮助
#
# 运行时间自定义（可指定多个，每天各跑一次）：
#   .\scripts\install_task_scheduler.ps1 install 06:30
#   .\scripts\install_task_scheduler.ps1 install 07:00 12:30 19:00
#   $env:NEWSPAPER_TIMES = "07:00,12:30"; .\scripts\install_task_scheduler.ps1 install
#
# ⚠ 任务计划程序使用「机器本地时区」，请用 Get-TimeZone 自行确认。
# ==============================================================================

#Requires -Version 5.1

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Action = 'install',

    [Parameter(Position = 1, ValueFromRemainingArguments = $true)]
    [string[]]$Times,

    [Alias('h')]
    [switch]$Help
)

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $PSCommandPath
$ProjectDir = Split-Path -Parent $ScriptDir
$TaskName = 'NewsPaperAuto'
$DefaultTime = '07:00'

function Die([string]$Message) {
    [Console]::Error.WriteLine("错误：$Message")
    exit 1
}

function Show-Help {
    @"
用法: install_task_scheduler.ps1 {install|uninstall|status|run-now} [HH:mm ...]
  install [HH:mm ...] : 注册（并启用）每日计划任务（默认 07:00，可给多个时间）
  uninstall           : 注销计划任务
  status              : 查看注册状态与最近一次日志
  run-now             : 立即触发一次任务执行
  -Help               : 显示本帮助

示例:
  .\install_task_scheduler.ps1 install 06:30
  .\install_task_scheduler.ps1 install 07:00 12:30 19:00
  `$env:NEWSPAPER_TIMES = "07:00,12:30"; .\install_task_scheduler.ps1 install

注意: 任务计划程序使用「机器本地时区」，请自行确认系统时区（Get-TimeZone）。
"@
}

# ---------------------------------------------------------------- 时间解析
function Get-TaskTimes([string[]]$RawTimes) {
    $source = @()
    if ($RawTimes) {
        $source = @($RawTimes | Where-Object { $_ -and $_.Trim() -ne '' })
    }
    elseif ($env:NEWSPAPER_TIMES) {
        $source = @($env:NEWSPAPER_TIMES -split '[,;]' | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' })
    }
    if ($source.Count -eq 0) { $source = @($DefaultTime) }

    $valid = @()
    foreach ($t in $source) {
        if ($t -notmatch '^([0-9]{1,2}):([0-9]{1,2})$') {
            Die "时间格式应为 HH:mm，实际：'$t'"
        }
        $h = [int]$Matches[1]
        $m = [int]$Matches[2]
        if ($h -gt 23) { Die "小时超出范围（0-23）：'$t'" }
        if ($m -gt 59) { Die "分钟超出范围（0-59）：'$t'" }
        $valid += ('{0:00}:{1:00}' -f $h, $m)
    }
    return $valid
}

# ---------------------------------------------------------------- 安装
function Install-Task([string[]]$RawTimes) {
    $times = @(Get-TaskTimes $RawTimes)
    $runScript = Join-Path $ScriptDir 'run_scan.ps1'
    if (-not (Test-Path -LiteralPath $runScript)) {
        Die "未找到运行脚本：$runScript"
    }
    New-Item -ItemType Directory -Force -Path (Join-Path $ProjectDir 'logs') | Out-Null

    $psExe = (Get-Process -Id $PID).Path
    $argLine = '-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $runScript
    $taskAction = New-ScheduledTaskAction -Execute $psExe -Argument $argLine -WorkingDirectory $ProjectDir

    $triggers = @()
    foreach ($t in $times) {
        $triggers += New-ScheduledTaskTrigger -Daily -At $t
    }

    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

    # 幂等：同名任务已存在则先注销，再注册
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }

    Register-ScheduledTask -TaskName $TaskName `
        -Action $taskAction -Trigger $triggers -Settings $settings `
        -Description 'AutoNewspaper 每日数字报关键词扫描（无状态运行；日志见 logs\scan-*.log）' | Out-Null

    Write-Host "✓ 已注册计划任务：$TaskName"
    Write-Host "  运行时间：每天 $($times -join ', ')（共 $($times.Count) 次）"
    Write-Host "  执行动作：$psExe -NoProfile -ExecutionPolicy Bypass -File `"$runScript`""
    Write-Host ""
    Write-Host "⚠ 提示：任务计划程序使用【机器本地时区】（既不是 UTC，也不一定是北京时区）。"
    Write-Host "  请执行 Get-TimeZone 确认；若与预期不符，请按本地时区重新 install。"
    Write-Host "  任务默认「仅在当前用户登录时运行」；错过的触发点可用下方 run-now 手动补跑。"
    Write-Host "  可用 '$(Split-Path -Leaf $PSCommandPath) status' 查看、'... run-now' 立即测试。"
}

# ---------------------------------------------------------------- 状态
function Show-Status {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Write-Host "=== 计划任务状态（$TaskName）==="
    if ($task) {
        $info = Get-ScheduledTaskInfo -TaskName $TaskName
        $starts = @($task.Triggers | ForEach-Object { $_.StartBoundary })
        Write-Host "  状态     : $($task.State)"
        Write-Host "  触发时间 : $($starts -join ', ')"
        Write-Host "  上次运行 : $($info.LastRunTime)"
        Write-Host "  上次结果 : $($info.LastTaskResult)（0 = 正常）"
        Write-Host "  下次运行 : $($info.NextRunTime)"
    }
    else {
        Write-Host "未注册 $TaskName（可执行 'install' 安装）"
    }
    Write-Host ""
    Write-Host "=== 最近日志 ==="
    $logsDir = Join-Path $ProjectDir 'logs'
    $latest = Get-ChildItem -LiteralPath $logsDir -Filter 'scan-*.log' -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($latest) {
        Write-Host "--- $($latest.FullName) ---"
        Get-Content -LiteralPath $latest.FullName -Tail 12
    }
    else {
        Write-Host "（暂无 logs\scan-*.log）"
    }
}

# ---------------------------------------------------------------- 立即运行 / 卸载
function Invoke-RunNow {
    if (-not (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)) {
        Die "尚未注册，请先执行 'install'"
    }
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "✓ 已触发，稍后查看：$(Join-Path $ProjectDir 'logs')"
}

function Uninstall-Task {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "✓ 已移除计划任务：$TaskName"
    }
    else {
        Write-Host "未发现已注册的计划任务：$TaskName"
    }
}

# ---------------------------------------------------------------- 分发
if ($Help) {
    Show-Help
    exit 0
}

switch ($Action.ToLowerInvariant()) {
    'install' { Install-Task $Times }
    'uninstall' { Uninstall-Task }
    'status' { Show-Status }
    'run-now' { Invoke-RunNow }
    default {
        Show-Help
        exit 1
    }
}
