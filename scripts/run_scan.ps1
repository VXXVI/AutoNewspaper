# ==============================================================================
# run_scan.ps1 - 数字报每日监测启动脚本（Windows / PowerShell）
#
# 职责刻意保持很薄：
#   1. 解析 Python 解释器（NEWSPAPER_PYTHON → 项目 .venv → PATH 中的 python）；
#   2. cd 到项目根；
#   3. 补默认值：始终无状态（--no-state）；未显式给日期时补 --days；
#   4. 把其余参数**原样**透传给 daily_scan.py。
#
# 本脚本**不再维护 daily_scan.py 的选项白名单**：任何 `--xxx` 都直接转发，
# 因此 cli.py 新增选项时无需同步修改这里（历史上白名单滞后已多次导致定时任务报错）。
# 显式给出 --days 或 --since/--until 时，不再注入默认 --days（CLI 里两者互斥）。
#
# 用法示例（全部原样透传）：
#   .\scripts\run_scan.ps1 --days 3 --workers 4 --only 湛江日报,南方日报
#   .\scripts\run_scan.ps1 --since 2026-09-20 --until 2026-09-25
#   .\scripts\run_scan.ps1 --kw 人工智能 --export-dir .\exports --export-format json
#
# 环境变量：
#   NEWSPAPER_PYTHON  Python 解释器绝对路径（默认：项目 .venv\Scripts\python.exe → PATH 中的 python）
#   NEWSPAPER_DAYS    回溯天数默认值（默认 2 = 今天 + 昨天，与 daily_scan.py 一致）
#
# 日志：追加写入 logs\scan-YYYY-MM-DD.log，并同时回显到标准输出（便于双击启动器查看）。
# ==============================================================================

# 说明：本脚本刻意不声明 param() 块 —— 全部参数按顺序进入自动变量 $args，
#       从而把新增的 CLI 选项原样透传给 daily_scan.py（与 run_scan.sh 行为一致）。

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

# ---------------------------------------------------------------- 默认参数
# 只在用户没有显式给出任何日期参数时注入默认天数，避免与 --since/--until 互斥。
$DefaultDays = if ($env:NEWSPAPER_DAYS) { [string]$env:NEWSPAPER_DAYS } else { '2' }
$HasDateArg = $false
foreach ($rawArg in @($args)) {
    $a = [string]$rawArg
    if ($a -eq '--days' -or $a -like '--days=*' -or
        $a -eq '--since' -or $a -like '--since=*' -or
        $a -eq '--until' -or $a -like '--until=*') {
        $HasDateArg = $true
    }
}

$CmdArgs = @('daily_scan.py', '--no-state')
if (-not $HasDateArg) {
    $CmdArgs += @('--days', $DefaultDays)
}
# 其余参数原样透传（位置参数 / 未知选项都交给 daily_scan.py 判断）。
$CmdArgs += @($args)

# ---------------------------------------------------------------- 执行并记录日志
$LogsDir = Join-Path $ProjectDir 'logs'
New-Item -ItemType Directory -Force -Path $LogsDir | Out-Null
$LogFile = Join-Path $LogsDir ('scan-{0}.log' -f (Get-Date -Format 'yyyy-MM-dd'))
$env:PYTHONIOENCODING = 'utf-8'

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
    # 同时回显到控制台（供双击启动器查看）并写入日志
    $output | ForEach-Object { Write-Host $_ }
    Write-Log (($output -join [Environment]::NewLine))
}
Write-Log "=== 扫描完成：$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')，退出码：$ExitCode ==="

exit $ExitCode
