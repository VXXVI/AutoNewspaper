# ==============================================================================
# build_windows.ps1 - 构建 Windows 单文件便携版（PyInstaller）
#
# 在 Windows 上执行（仓库根）：
#   powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
#
# 产物：
#   dist\NewspaperAuto.exe
#   dist\NewspaperAuto-<版本>-windows-x64.zip   （exe + 双击启动器 + 使用说明）
#
# 说明：PyInstaller 无法跨平台交叉编译（只能在 Windows 上产出 Windows 产物）。
#       macOS / Linux 上请改用 GitHub Actions（.github/workflows/build-windows.yml）。
# ==============================================================================

#Requires -Version 5.1

[CmdletBinding()]
param(
    [string]$Version = '',
    [switch]$SkipInstall,
    [switch]$SkipSmoke
)

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $PSCommandPath
$ProjectDir = Split-Path -Parent $ScriptDir
Set-Location -LiteralPath $ProjectDir

function Die([string]$Message) {
    [Console]::Error.WriteLine("错误：$Message")
    exit 1
}

# ---------------------------------------------------------------- 版本号
if (-not $Version) {
    $Version = (& python -c "import sys; sys.path.insert(0, 'src'); import newspaper_auto; print(newspaper_auto.__version__)").Trim()
}
if (-not $Version) { Die '无法获取版本号（可显式传 -Version x.y.z）' }
Write-Host "构建版本：$Version"

# ---------------------------------------------------------------- 依赖（仅构建期）
if (-not $SkipInstall) {
    Write-Host '安装 PyInstaller（仅构建期依赖，不进入运行时）…'
    python -m pip install --upgrade pip pyinstaller
    if ($LASTEXITCODE -ne 0) { Die 'pip 安装失败' }
}

# ---------------------------------------------------------------- 构建
Write-Host '运行 PyInstaller…'
python -m PyInstaller --clean --noconfirm packaging\NewspaperAuto.spec
if ($LASTEXITCODE -ne 0) { Die 'PyInstaller 构建失败' }

$exePath = Join-Path $ProjectDir 'dist\NewspaperAuto.exe'
if (-not (Test-Path -LiteralPath $exePath)) { Die "未生成 $exePath" }

# ---------------------------------------------------------------- 冒烟测试
# --version 验证包可启动；起看板并请求 /api/health 与 static/store.js，
# 验证「静态资源确实随包」——这正是历史上最容易漏、且单测抓不到的一类问题。
if (-not $SkipSmoke) {
    Write-Host '冒烟测试：--version 与看板静态资源…'
    & $exePath --version
    if ($LASTEXITCODE -ne 0) { Die 'exe --version 失败' }

    $smokeData = Join-Path $ProjectDir 'build\smoke-data'
    New-Item -ItemType Directory -Force -Path $smokeData | Out-Null
    $port = 8799
    $proc = Start-Process -FilePath $exePath `
        -ArgumentList @('dashboard', '--port', "$port", '--data-dir', $smokeData) `
        -PassThru -WindowStyle Hidden
    try {
        $ready = $false
        for ($i = 0; $i -lt 40; $i++) {
            Start-Sleep -Milliseconds 500
            try {
                $health = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port/api/health" -TimeoutSec 2
                if ($health.StatusCode -eq 200) { $ready = $true; break }
            } catch { }
        }
        if (-not $ready) { Die '看板未在预期时间内就绪（/api/health）' }

        $staticResp = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port/static/store.js" -TimeoutSec 5
        if ($staticResp.StatusCode -ne 200 -or $staticResp.Content -notmatch 'createStore') {
            Die '静态资源未随包（static/store.js 不可达）'
        }
        Write-Host '冒烟测试通过。'
    } finally {
        if ($proc -and -not $proc.HasExited) { Stop-Process -Id $proc.Id -Force }
    }
}

# ---------------------------------------------------------------- 组装 zip
$stage = Join-Path $ProjectDir ('build\package-{0}' -f $Version)
if (Test-Path -LiteralPath $stage) { Remove-Item -Recurse -Force -LiteralPath $stage }
New-Item -ItemType Directory -Force -Path $stage | Out-Null
Copy-Item -LiteralPath $exePath -Destination $stage
Copy-Item -Path (Join-Path $ProjectDir 'packaging\windows\*') -Destination $stage -Force

$zipPath = Join-Path $ProjectDir ('dist\NewspaperAuto-{0}-windows-x64.zip' -f $Version)
if (Test-Path -LiteralPath $zipPath) { Remove-Item -Force -LiteralPath $zipPath }
Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $zipPath -Force

Write-Host ''
Write-Host "已生成：$zipPath"
Write-Output $zipPath
