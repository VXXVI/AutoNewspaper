; ==============================================================================
; NewspaperAuto · Windows 安装包（Inno Setup）
;
; 由 scripts/build_windows.ps1 调用（CI 见 .github/workflows/build-windows.yml）：
;
;   ISCC.exe /DAppVersion=0.5.0 /DSourceDir=<打包暂存目录> packaging\windows\installer.iss
;
; 产物：dist\NewspaperAuto-<版本>-setup.exe
;
; 设计要点：
; - 装到 %LOCALAPPDATA%\Programs\NewspaperAuto（**用户可写**）：本程序把 state/、hits/、
;   papers.custom.json 放在自己的安装目录里，装到 Program Files 会因为无写权限而无法保存数据；
; - PrivilegesRequired=lowest：不需要管理员权限，不弹 UAC；
; - 卸载**不删除**扫描数据（state/、hits/、logs/）：这些是用户资产，且不是安装程序创建的，
;   Inno 默认就只删自己装的文件；如要彻底清理需手动删除安装目录；
; - 需要 Inno Setup 6.3+（用到 x64compatible）。
; ==============================================================================

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#ifndef SourceDir
  ; 单独调试编译时给个默认值（正常由构建脚本传入）
  #define SourceDir "..\..\dist"
#endif

#ifndef VersionInfoVersion
  ; VersionInfoVersion 必须是 x.y.z.w 四段纯数字：
  ; Inno Setup 6.7.1（CI 用 choco 装的就是它）遇到 "0.5.0" 会直接报
  ; 「Value of [Setup] section directive "VersionInfoVersion" is invalid」。
  ; 构建脚本会传入更精确的值，这里是手动编译时的兜底。
  #define VersionInfoVersion AppVersion + ".0"
#endif

#ifndef IconFile
  ; 安装包自身（setup.exe 与「应用和功能」列表）的图标：直接用看板的品牌 ico，
  ; 与 exe / 快捷方式同一份，避免三处图标漂移。构建脚本会传绝对路径覆盖。
  #define IconFile "..\..\src\newspaper_auto\static\favicon.ico"
#endif

#define AppName "NewspaperAuto"
#define AppTitle "NewspaperAuto · 数字报关键词监测"
#define AppPublisher "VXXVI"
#define AppURL "https://github.com/VXXVI/AutoNewspaper"

[Setup]
; AppId 一经发布就不能变，否则会被当成另一个程序（升级/卸载都会错乱）
AppId={{B4E2F0C7-6E1D-4B3A-9C2E-7A5D1F8B0C33}
AppName={#AppTitle}
AppVersion={#AppVersion}
AppVerName={#AppTitle} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
VersionInfoVersion={#VersionInfoVersion}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\..\dist
OutputBaseFilename=NewspaperAuto-{#AppVersion}-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#AppTitle} {#AppVersion}
UninstallDisplayIcon={app}\NewspaperAuto.exe
; 安装包自身也带品牌图标（默认是 Inno 的通用图标）
SetupIconFile={#IconFile}
; 压缩包/安装包都未做代码签名，首次运行会有 SmartScreen 提示（README 已说明）

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式（打开看板）"; GroupDescription: "附加任务："; Flags: unchecked

[Files]
; 两个 exe：无窗口版负责双击即用，控制台版负责全部命令行用法（.cmd 启动器调它）。
Source: "{#SourceDir}\NewspaperAuto.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\NewspaperAuto-CLI.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\Start-Dashboard.cmd"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\Run-Scan.cmd"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\README.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; 「打开看板」直接指向无窗口版 exe：双击零控制台窗口（用户明确要求别弹终端）。
Name: "{group}\打开看板"; Filename: "{app}\NewspaperAuto.exe"; WorkingDir: "{app}"; IconFilename: "{app}\NewspaperAuto.exe"
; 「扫描一次」需要看进度，走 .cmd（它调控制台版，自带窗口）。
Name: "{group}\扫描一次"; Filename: "{app}\Run-Scan.cmd"; WorkingDir: "{app}"; IconFilename: "{app}\NewspaperAuto-CLI.exe"
Name: "{group}\使用说明"; Filename: "{app}\README.txt"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\打开看板"; Filename: "{app}\NewspaperAuto.exe"; WorkingDir: "{app}"; IconFilename: "{app}\NewspaperAuto.exe"; Tasks: desktopicon

[Run]
; 装完立刻开看板（无窗口版：后台运行 + 自动开浏览器）
Filename: "{app}\NewspaperAuto.exe"; Description: "立即打开看板"; Flags: postinstall nowait skipifsilent runascurrentuser

[UninstallDelete]
; 刻意只清运行时产物（日志、看板运行状态），不碰 state/ 与 hits/：
; 那是用户的扫描数据，卸载不应连带删除。
Type: filesandordirs; Name: "{app}\logs"
Type: files; Name: "{app}\state\dashboard.json"

[Code]
procedure StopDashboard(const AppDir: String);
var
  StopExe: String;
  ResultCode: Integer;
begin
  { 优先用控制台版：无窗口版同样支持 dashboard --stop，留作兜底（旧版本可能只有它） }
  StopExe := AppDir + '\NewspaperAuto-CLI.exe';
  if not FileExists(StopExe) then
    StopExe := AppDir + '\NewspaperAuto.exe';
  if not FileExists(StopExe) then
    Exit;
  { 停掉后台看板并等它退出：否则 exe 被占用，装不上/也卸不干净 }
  Exec(StopExe, 'dashboard --stop', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  { 再缓一拍：确保 PyInstaller 引导进程的文件句柄也彻底释放 }
  Sleep(1500);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  NeedsRestart := False;
  { 升级/重装：此时文件还没被替换，先停掉正在运行的后台看板 }
  StopDashboard(ExpandConstant('{app}'));
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  { usUninstall = 即将开始删除文件；必须在这里停，晚一步状态文件就被删掉、--stop 找不到目标 }
  if CurUninstallStep = usUninstall then
    StopDashboard(ExpandConstant('{app}'));
end;
