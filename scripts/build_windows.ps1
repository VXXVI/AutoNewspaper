# ==============================================================================
# build_windows.ps1 - 构建 Windows 单文件便携版（PyInstaller）
#
# 在 Windows 上执行（仓库根）：
#   powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
#
# 产物：
#   dist\NewspaperAuto.exe                             无窗口版：双击即用，零控制台窗口
#   dist\NewspaperAuto-<版本>-<Target>-x64.zip         （exe + 使用说明）
#   dist\NewspaperAuto-<版本>-setup.exe                （安装包；仅 -Target windows，需 Inno Setup）
#
# -Target 决定 zip 名里的平台标签（默认 windows）。Windows 7 用 -Target win7，
# 且必须在 Python 3.8 下构建（3.9+ 的运行时在 Win7 上直接报「不是有效的 Win32 程序」），
# 见 .github/workflows/build-windows.yml 的 python-version / target 输入。非 windows
# 目标一律跳过 Inno 安装包。
#
# 只出一个 exe：PE 的 Subsystem 字段做不到「双击不弹窗」+「终端里 shell 会等它」两全。
# 取舍是优先双击即用；命令行侧靠入口的 AttachConsole 回显 + scan 自己写
# logs/scan-<日期>.log 兜底，因此不再需要额外的控制台版 exe 或 .cmd 启动器。
#
# 说明：PyInstaller 无法跨平台交叉编译（只能在 Windows 上产出 Windows 产物）。
#       macOS / Linux 上请改用 GitHub Actions（.github/workflows/build-windows.yml）。
# ==============================================================================

#Requires -Version 5.1

[CmdletBinding()]
param(
    [string]$Version = '',
    [string]$Target = 'windows',
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

# 平台标签只用来拼产物名，必须干净（避免路径穿越 / 非法字符）
if ($Target -notmatch '^[a-z0-9][a-z0-9-]*$') { Die "非法的 -Target：$Target（只允许小写字母、数字与连字符）" }
# 安装包只服务主线 Windows 目标；win7 等目标不需要，也不该强依赖 Inno Setup
if ($Target -ne 'windows') { $SkipInstaller = $true }

# ---------------------------------------------------------------- 版本号
$sourceVersion = (& python -c "import sys; sys.path.insert(0, 'src'); import newspaper_auto; print(newspaper_auto.__version__)").Trim()
if (-not $Version) { $Version = $sourceVersion }
if (-not $Version) { Die '无法获取版本号（可显式传 -Version x.y.z）' }

# CI 传进来的是 **tag 名**（带 v 前缀，如 v0.9.0），而 /api/health 报的是包内版本号
# （0.9.0）。必须归一化后再比，否则冒烟测试会永远匹配不上（v0.9.0 就这么挂过一次）。
$packageVersion = $Version -replace '^[vV]', ''
if ($sourceVersion -ne $packageVersion) {
    Die "版本号不一致：构建标签 '$Version'（归一化后 '$packageVersion'） vs 源码 __version__ '$sourceVersion'"
}
Write-Host "构建版本：$Version（包内版本号 $packageVersion）"

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

# ---------------------------------------------------------------- 构建内容自检
# 出过「源码修好了、但没进包」的事故：修复只停在本地没推送，CI 从远程代码构建，
# 于是 v1.0.2 的 exe 里既没有 newspaper_auto.tls 模块、也没有随包根证书，而两边
# 都没人发现。中国教育报因此在 Win7 上整家漏报。
#
# 这里直接读 exe 内 PyInstaller 的 PYZ 模块目录表（明文 marshal，见下）确认修复
# 模块确实在包里。注意「数据文件」不能这样查：CArchive 的目录名不是明文，只能靠
# 下面那次看板冒烟（/static/store.js 可达）间接证明 datas 机制有效。
$inspect = @'
import marshal
import struct
import sys

data = open(sys.argv[1], "rb").read()
i = data.find(b"PYZ\x00")
if i < 0:
    sys.exit("找不到 PYZ 目录表，无法校验 exe 的模块清单")
offset = struct.unpack(">I", data[i + 8 : i + 12])[0]
mods = {
    entry[0]
    for entry in marshal.loads(data[i + offset :])
    if isinstance(entry, tuple) and isinstance(entry[0], str)
}
if "newspaper_auto.http" not in mods:
    sys.exit("解析出的模块清单里连 newspaper_auto.http 都没有，校验本身失效")
missing = sorted(m for m in ("newspaper_auto.tls",) if m not in mods)
if missing:
    sys.exit("exe 缺少模块：" + "、".join(missing) + "（构建输入不是这份源码？）")
print("exe 模块清单校验通过：%d 个模块，含 newspaper_auto.tls" % len(mods))
'@
python -c $inspect $exePath
if ($LASTEXITCODE -ne 0) { Die 'exe 缺少 TLS 修复模块：打出来的是不含本次修复的代码' }
if (-not (Test-Path -LiteralPath (Join-Path $ProjectDir 'src\newspaper_auto\cacert.pem'))) {
    Die '缺少 src\newspaper_auto\cacert.pem：系统根库缺锚点的机器（Win7）会整家抓不到中国教育报'
}

# ---------------------------------------------------------------- 冒烟测试
# 单测抓不到「静态资源没随包」「无窗口版一启动就崩」这类问题，只能在产物上验。
# 无窗口版的 stdout 是 None（打不出字），所以**不看 --version 的输出**，改为起服务后
# 断言 /api/health 里的 version 与源码版本号一致 —— 比看输出字符串更硬，也不会假绿。
if (-not $SkipSmoke) {
    $smokeData = Join-Path $ProjectDir 'build\smoke-data'
    New-Item -ItemType Directory -Force -Path $smokeData | Out-Null
    $port = 8799
    Write-Host '冒烟测试：起看板 + /api/health + 静态资源…'
    $proc = Start-Process -FilePath $exePath `
        -ArgumentList @('dashboard', '--port', "$port", '--data-dir', $smokeData) `
        -PassThru -WindowStyle Hidden
    try {
        $ready = $false
        $health = $null
        for ($i = 0; $i -lt 40; $i++) {
            Start-Sleep -Milliseconds 500
            try {
                $health = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port/api/health" -TimeoutSec 2
                if ($health.StatusCode -eq 200) { $ready = $true; break }
            } catch { }
        }
        if (-not $ready) { Die '看板未在预期时间内就绪（/api/health）' }
        if ($health.Content -notmatch [regex]::Escape($packageVersion)) {
            Die "/api/health 未报告版本 $packageVersion（实际：$($health.Content)）"
        }

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
$stage = Join-Path $ProjectDir ('build\package-{0}-{1}' -f $Version, $Target)
if (Test-Path -LiteralPath $stage) { Remove-Item -Recurse -Force -LiteralPath $stage }
New-Item -ItemType Directory -Force -Path $stage | Out-Null
Copy-Item -LiteralPath $exePath -Destination $stage
# 只拷贝面向用户的文件：显式列举，避免 Copy-Item 通配把 packaging\windows\ 下的
# 构建脚本（如 installer.iss）一起装进分发给最终用户的 zip。
$userFiles = @('README.txt')
# 非主线目标（如 win7）额外带一份针对该目标的使用说明（存在才带）。
# 该文件在仓库里就以 CRLF + UTF-8 BOM 存储：老版本 Windows 记事本对 LF-only 与
# 无 BOM 的 UTF-8 都会显示错乱（Win7 记事本尤其明显），这里原样拷贝即可。
$targetReadme = 'README-{0}.txt' -f $Target
if (Test-Path -LiteralPath (Join-Path $ProjectDir ('packaging\windows\' + $targetReadme))) {
    $userFiles += $targetReadme
}
foreach ($name in $userFiles) {
    Copy-Item -LiteralPath (Join-Path $ProjectDir ('packaging\windows\' + $name)) -Destination $stage -Force
}

$zipPath = Join-Path $ProjectDir ('dist\NewspaperAuto-{0}-{1}-x64.zip' -f $Version, $Target)
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
