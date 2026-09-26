# ==============================================================================
# run_scan.ps1 - 六家报社数字报每日监测启动脚本（Windows / PowerShell）
#
# 用途：Windows 任务计划程序（scripts\install_task_scheduler.ps1）或手动调用。
#       默认以「无状态」方式运行（daily_scan.py --no-state），不读写 state\ 与 hits\。
#
# 关键词来源（任选其一）：
#   位置参数：  .\scripts\run_scan.ps1 人工智能 生成式人工智能
#   显式选项：  .\scripts\run_scan.ps1 --kw 人工智能 --kw 生成式人工智能
#   环境变量：  $env:NEWSPAPER_KW = "人工智能,生成式人工智能"; .\scripts\run_scan.ps1
#
# 其余选项原样透传给 daily_scan.py，例如：
#   .\scripts\run_scan.ps1 --days 3 --workers 4 --only 湛江日报,南方日报
#   .\scripts\run_scan.ps1 --since 2026-09-20 --until 2026-09-25   # 显式区间（与 --days 互斥）
#   .\scripts\run_scan.ps1 --export-dir .\exports --export-format json
#
# 可配置环境变量：
#   NEWSPAPER_PYTHON  Python 解释器绝对路径（默认：项目 .venv\Scripts\python.exe → PATH 中的 python）
#   NEWSPAPER_DAYS    回溯天数（默认 2＝今天+昨天，与 daily_scan.py 的默认一致）
#   NEWSPAPER_KW      关键词，逗号或分号分隔
#
# 日志：追加写入 logs\scan-YYYY-MM-DD.log
# ==============================================================================

# 说明：本脚本刻意不声明 param() 块 —— 全部参数按顺序进入自动变量 $args，
#       从而把 --kw / --days / ... 等原样透传给 daily_scan.py（与 run_scan.sh 行为一致）。

#Requires -Version 5.1

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $PSCommandPath
$ProjectDir = Split-Path -Parent $ScriptDir
Set-Location -LiteralPath $ProjectDir

function Die([string]$Message) {
    [Console]::Error.WriteLine("错误：$Message")
    exit 2
}

# ---------------------------------------------------------------- Python 解释器
# 不写死任何用户绝对路径：优先显式指定 → 项目虚拟环境 → PATH
function Resolve-Python {
    if ($env:NEWSPAPER_PYTHON) {
        if (Test-Path -LiteralPath $env:NEWSPAPER_PYTHON -PathType Leaf) {
            return $env:NEWSPAPER_PYTHON
        }
        Die "NEWSPAPER_PYTHON 不可用：$($env:NEWSPAPER_PYTHON)"
    }
    $venvPython = Join-Path $ProjectDir '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
        return $venvPython
    }
    $found = @(Get-Command python -CommandType Application -ErrorAction SilentlyContinue)
    if ($found.Count -gt 0) {
        return $found[0].Source
    }
    Die '未找到可用的 python，请安装 Python 或设置 NEWSPAPER_PYTHON'
}

$PythonBin = Resolve-Python

# ---------------------------------------------------------------- 参数解析
$Days = if ($env:NEWSPAPER_DAYS) { [string]$env:NEWSPAPER_DAYS } else { '2' }
$KwArgs = @()
$PassArgs = @()
# --days 与 --since/--until 在 daily_scan.py 里互斥：给了显式区间就不再注入 --days，
# 两者同时显式给出则直接报错，避免「静默忽略其中一个」。
$HasDays = $false
$HasRange = $false

$rawArgs = @($args)
for ($i = 0; $i -lt $rawArgs.Count; $i++) {
    $a = [string]$rawArgs[$i]
    if ($a -eq '--') {
        # 其后全部按裸关键词处理
        for ($j = $i + 1; $j -lt $rawArgs.Count; $j++) {
            $KwArgs += @('--kw', [string]$rawArgs[$j])
        }
        break
    }
    elseif ($a -eq '--kw' -or $a -eq '-k') {
        if ($i + 1 -ge $rawArgs.Count) { Die "$a 缺少参数" }
        $i++
        $KwArgs += @('--kw', [string]$rawArgs[$i])
    }
    elseif ($a -like '--kw=*') {
        $KwArgs += @('--kw', $a.Substring(5))
    }
    elseif ($a -eq '--days') {
        if ($i + 1 -ge $rawArgs.Count) { Die '--days 缺少参数' }
        $i++
        $Days = [string]$rawArgs[$i]
        $HasDays = $true
    }
    elseif ($a -like '--days=*') {
        $Days = $a.Substring(7)
        $HasDays = $true
    }
    elseif ($a -eq '--since' -or $a -eq '--until') {
        if ($i + 1 -ge $rawArgs.Count) { Die "$a 缺少参数" }
        $i++
        $PassArgs += @($a, [string]$rawArgs[$i])
        $HasRange = $true
    }
    elseif ($a -like '--since=*' -or $a -like '--until=*') {
        $PassArgs += $a
        $HasRange = $true
    }
    elseif ($a -eq '--workers' -or $a -eq '--only' -or $a -eq '--data-dir' -or $a -eq '--export-dir' -or $a -eq '--export-format' -or $a -eq '--archive-keep-days') {
        if ($i + 1 -ge $rawArgs.Count) { Die "$a 缺少参数" }
        $i++
        $PassArgs += @($a, [string]$rawArgs[$i])
    }
    elseif ($a -like '--workers=*' -or $a -like '--only=*' -or $a -like '--data-dir=*' -or $a -like '--export-dir=*' -or $a -like '--export-format=*' -or $a -like '--archive-keep-days=*') {
        $PassArgs += $a
    }
    elseif ($a -eq '--dry' -or $a -eq '--full' -or $a -eq '--json' -or $a -eq '--csv' -or $a -eq '--version' -or $a -eq '-h' -or $a -eq '--help') {
        $PassArgs += $a
    }
    elseif ($a -eq '--no-state') {
        # 本脚本始终以 --no-state 运行；重复传入直接忽略，避免 argv 重复
    }
    elseif ($a -like '-*') {
        Die "未知选项：$a（如需传给 daily_scan.py 的新选项，请在此脚本中登记）"
    }
    else {
        # 裸关键词（向后兼容旧用法）
        $KwArgs += @('--kw', $a)
    }
}

if ($HasDays -and $HasRange) {
    Die "--days 与 --since/--until 不能同时使用（与 daily_scan.py 一致）"
}

# 位置参数与显式 --kw 均未给出时，退回环境变量
if ($KwArgs.Count -eq 0 -and $env:NEWSPAPER_KW) {
    foreach ($item in ($env:NEWSPAPER_KW -split '[,;]')) {
        $kw = $item.Trim()
        if ($kw) {
            $KwArgs += @('--kw', $kw)
        }
    }
}

# ---------------------------------------------------------------- 组命令并执行
$LogsDir = Join-Path $ProjectDir 'logs'
New-Item -ItemType Directory -Force -Path $LogsDir | Out-Null
$LogFile = Join-Path $LogsDir ('scan-{0}.log' -f (Get-Date -Format 'yyyy-MM-dd'))
$env:PYTHONIOENCODING = 'utf-8'

$CmdArgs = @('daily_scan.py', '--no-state')
if (-not $HasRange) {
    $CmdArgs += @('--days', $Days)
}
$CmdArgs += $KwArgs + $PassArgs

# 日志统一用 UTF-8（不带 BOM）追加写入
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
function Write-Log([string]$Text) {
    [System.IO.File]::AppendAllText($LogFile, $Text + [Environment]::NewLine, $Utf8NoBom)
}

Write-Log "=== 开始监测扫描：$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ==="
Write-Log ("命令：{0} {1}" -f $PythonBin, ($CmdArgs -join ' '))

# 让 PowerShell 按 UTF-8 解码 Python 的输出，避免中文乱码；
# 无控制台宿主（如计划任务）下设置失败也不影响运行。
$prevOutputEncoding = $null
try {
    $prevOutputEncoding = [Console]::OutputEncoding
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
} catch {
    $prevOutputEncoding = $null
}

$ErrorActionPreference = 'Continue'
$output = @(& $PythonBin @CmdArgs 2>&1 | ForEach-Object { [string]$_ })
$ExitCode = $LASTEXITCODE
$ErrorActionPreference = 'Stop'

if ($null -ne $prevOutputEncoding) {
    try { [Console]::OutputEncoding = $prevOutputEncoding } catch { }
}

if ($output.Count -gt 0) {
    Write-Log (($output -join [Environment]::NewLine))
}
Write-Log "=== 扫描完成：$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')，退出码：$ExitCode ==="

exit $ExitCode
