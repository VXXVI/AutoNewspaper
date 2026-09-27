# ==============================================================================
# build_windows.ps1 - 构建 Windows 单文件便携版（PyInstaller）
#
# 在 Windows 上执行（仓库根）：
#   powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
#
# 产物：
#   dist\NewspaperAuto.exe           无窗口版：双击即用，零控制台窗口
#   dist\NewspaperAuto-CLI.exe       控制台版：全部命令行用法，.cmd 启动器调它
#   dist\NewspaperAuto-<版本>-windows-x64.zip   （两个 exe + 双击启动器 + 使用说明）
#   dist\NewspaperAuto-<版本>-setup.exe         （安装包；需 Inno Setup，缺失则跳过）
#
# 两个 exe 共用 packaging\NewspaperAuto.spec，靠 NEWSPAPERAUTO_CONSOLE 环境变量
# 切换 console 属性；静态资源/图标/排除项只有一处真相。
#
# 说明：PyInstaller 无法跨平台交叉编译（只能在 Windows 上产出 Windows 产物）。
#       macOS / Linux 上请改用 GitHub Actions（.github/workflows/build-windows.yml）。
# ==============================================================================

#Requires -Version 5.1

[CmdletBinding()]
param(
    [string]$Version = '',
    [switch]$SkipInstall,
    [switch]$SkipSmoke,
    [switch]$SkipInstaller
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
# 两个 exe 共用同一份 spec：NewspaperAuto.exe 无窗口（双击即用），
# NewspaperAuto-CLI.exe 带控制台（全部命令行用法 + .cmd 启动器调它）。
# 注意不要把 PyInstaller 的输出接进管道：PowerShell 会把函数里的 stdout 也当成
# 返回值，$exePath 会变成一整串日志。所以这里让函数只写主机、路径在调用处算。
$specPath = 'packaging\NewspaperAuto.spec'

function Invoke-PyInstaller([string]$Name, [switch]$Console) {
    # 用 [switch] 而不是把空串传给 [string]：PowerShell 里 '' 绑定到 [string] 参数
    # 在不同版本上语义不一致，容易把 windowed 版意外做成控制台版。
    if ($Console) { $env:NEWSPAPERAUTO_CONSOLE = '1' }
    else { Remove-Item Env:\NEWSPAPERAUTO_CONSOLE -ErrorAction SilentlyContinue }
    try {
        Write-Host "运行 PyInstaller（$Name）…"
        python -m PyInstaller --clean --noconfirm $specPath
        if ($LASTEXITCODE -ne 0) { Die "PyInstaller 构建失败（$Name）" }
    } finally {
        Remove-Item Env:\NEWSPAPERAUTO_CONSOLE -ErrorAction SilentlyContinue
    }
    $out = Join-Path $ProjectDir "dist\$Name.exe"
    if (-not (Test-Path -LiteralPath $out)) { Die "未生成 $out" }
    Write-Host "  已生成 $out"
}

Invoke-PyInstaller 'NewspaperAuto'
Invoke-PyInstaller 'NewspaperAuto-CLI' -Console

$exePath = Join-Path $ProjectDir 'dist\NewspaperAuto.exe'
$cliExePath = Join-Path $ProjectDir 'dist\NewspaperAuto-CLI.exe'

# ---------------------------------------------------------------- 冒烟测试
# 单测抓不到「静态资源没随包」「无窗口版一启动就崩」这类问题，只能在产物上验：
#   - 控制台版 --version 必须**打印出版本号**。只看退出码会漏掉「无窗口版打不出字」
#     这种假绿：console=False 时 stdout 是 None，print 会崩或静默。
#   - 两个 exe 都要能起看板，并且取得到 /api/health 与 static/store.js。
if (-not $SkipSmoke) {
    Write-Host '冒烟测试：控制台版 --version 输出…'
    $versionOut = (& $cliExePath --version 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) { Die 'NewspaperAuto-CLI.exe --version 失败' }
    if ($versionOut -notmatch '\d+\.\d+\.\d+') {
        Die "NewspaperAuto-CLI.exe --version 没有输出版本号（实际：'$versionOut'）"
    }
    Write-Host "  输出：$versionOut"

    $smokePort = 8799
    $targets = @(
        [pscustomobject]@{ Exe = $cliExePath; Label = 'NewspaperAuto-CLI'; Port = $smokePort },
        [pscustomobject]@{ Exe = $exePath; Label = 'NewspaperAuto'; Port = ($smokePort + 1) }
    )
    foreach ($target in $targets) {
        Write-Host "冒烟测试：$($target.Label) 起看板 + 静态资源…"
        $smokeData = Join-Path $ProjectDir ("build\smoke-" + $target.Label)
        New-Item -ItemType Directory -Force -Path $smokeData | Out-Null
        $port = $target.Port
        $proc = Start-Process -FilePath $target.Exe `
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
            if (-not $ready) { Die "$($target.Label) 未在预期时间内就绪（/api/health）" }

            $staticResp = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port/static/store.js" -TimeoutSec 5
            if ($staticResp.StatusCode -ne 200 -or $staticResp.Content -notmatch 'createStore') {
                Die "$($target.Label) 的静态资源未随包（static/store.js 不可达）"
            }
        } finally {
            if ($proc -and -not $proc.HasExited) { Stop-Process -Id $proc.Id -Force }
        }
    }
    Write-Host '冒烟测试通过。'
}

# ---------------------------------------------------------------- 组装 zip
$stage = Join-Path $ProjectDir ('build\package-{0}' -f $Version)
if (Test-Path -LiteralPath $stage) { Remove-Item -Recurse -Force -LiteralPath $stage }
New-Item -ItemType Directory -Force -Path $stage | Out-Null
Copy-Item -LiteralPath $exePath -Destination $stage
Copy-Item -LiteralPath $cliExePath -Destination $stage
# 只拷贝面向用户的文件：显式列举，避免 Copy-Item 通配把 packaging\windows\ 下的
# 构建脚本（如 installer.iss）一起装进分发给最终用户的 zip。
$userFiles = @('README.txt', 'Run-Scan.cmd', 'Start-Dashboard.cmd')
foreach ($name in $userFiles) {
    Copy-Item -LiteralPath (Join-Path $ProjectDir ('packaging\windows\' + $name)) -Destination $stage -Force
}

$zipPath = Join-Path $ProjectDir ('dist\NewspaperAuto-{0}-windows-x64.zip' -f $Version)
if (Test-Path -LiteralPath $zipPath) { Remove-Item -Force -LiteralPath $zipPath }
Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $zipPath -Force

# ---------------------------------------------------------------- 安装包（Inno Setup，可选）
# 便携 zip 直接可用；有 Inno Setup 时再产出「安装版」：装到 %LOCALAPPDATA%\Programs，
# 建开始菜单/桌面快捷方式、带卸载，用户不必解压也不必挑 .cmd。
$setupPath = $null
if (-not $SkipInstaller) {
    Write-Host ''
    Write-Host '构建 Windows 安装包（Inno Setup）…'

    $iscc = $null
    $candidates = @(
        (Get-Command ISCC.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue),
        'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
        'C:\Program Files\Inno Setup 6\ISCC.exe',
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe')
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) { $iscc = $candidate; break }
    }

    if (-not $iscc) {
        Write-Host '未找到 Inno Setup（ISCC.exe），跳过安装包构建。'
        Write-Host '如需构建：winget install JRSoftware.InnoSetup（CI 里由 chocolatey 安装）。'
    } else {
        Write-Host "使用 ISCC：$iscc"
        # Inno 要求 VersionInfoVersion 是 x.y.z.w 四段纯数字；三段（如 0.5.0）在
        # Inno Setup 6.7.1 上会直接报 invalid（CI 用的就是 6.7.1）。
        # 注意 $Version 可能是 tag 名（v0.5.0）：先去掉前缀 v，否则会算成 5.0.0.0。
        $numeric = @(($Version -replace '^[vV]', '') -split '[.\-+]' | Where-Object { $_ -match '^\d+$' })
        while ($numeric.Count -lt 4) { $numeric += '0' }
        $versionInfo = ($numeric[0..3] -join '.')

        $issPath = Join-Path $ProjectDir 'packaging\windows\installer.iss'
        # 安装包自身与 exe / 快捷方式共用同一份品牌图标（NewspaperAuto.spec 也用这个文件）。
        $iconPath = Join-Path $ProjectDir 'src\newspaper_auto\static\favicon.ico'
        if (-not (Test-Path -LiteralPath $iconPath)) { Die "缺少品牌图标 $iconPath" }
        & $iscc "/DAppVersion=$Version" "/DVersionInfoVersion=$versionInfo" "/DSourceDir=$stage" "/DIconFile=$iconPath" $issPath
        if ($LASTEXITCODE -ne 0) { Die 'Inno Setup 编译失败' }

        $setupPath = Join-Path $ProjectDir ('dist\NewspaperAuto-{0}-setup.exe' -f $Version)
        if (-not (Test-Path -LiteralPath $setupPath)) { Die "未生成安装包 $setupPath" }
    }
}

Write-Host ''
Write-Host "已生成：$zipPath"
if ($setupPath) { Write-Host "已生成：$setupPath" }
Write-Output $zipPath
