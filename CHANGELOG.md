# 变更记录

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与
[语义化版本](https://semver.org/lang/zh-CN/)。条目面向使用者，描述「能感知到的变化」，
不再记录函数级实现细节。

## [Unreleased]

### Changed

- Windows 7 包（**Python 3.8.10** 构建）的兼容性改为由 CI 保证：新增
  `tests/test_py38_compat.py` 静态扫描 `src/`，出现只在 3.9+ 能跑的**运行期**语法
  （`dict[str, int]`、`int | str` 等）时 CI 直接失败，不再等到手动构建 Win7 包才暴露。

### Fixed

- 定时扫描期间日志写入失败（磁盘满、日志所在盘被拔出、被杀毒软件独占）不再中断扫描：
  日志是旁路，写不进去只丢日志，增量状态与报告照常落盘。
- 增量状态的过期记录清理改为按 URL 里的日期目录**精确到日**比较：此前只按整月比较，
  实际保留窗口是 `keep_days + (cutoff.day - 1)` 天（120 天配置下会在 120～150 天之间浮动），
  现在正好是 `keep_days` 天；URL 里只有年月时仍按整月保守处理。
- 看板后台状态文件（`state/dashboard.json`）改用统一的原子写：此前用的是**固定**的
  `dashboard.json.tmp`，两个看板进程几乎同时启动时会抢同一个临时文件，后一个进程会以
  「启动失败」告终。

## [1.0.1] - 2026-09-28

### Fixed

- 「保存到文件夹」失败时的提示不再重复前缀：此前服务端和前端各加一次，界面显示成
  「导出失败：导出失败：…」，现在由前端统一加。
- 「保存到文件夹」失败时给出可读原因，不再直接把 OS 原文抛给使用者。以前是
  `[Errno 1] Operation not permitted: '/…/.x.json.abc.tmp' -> '/…/x.json'`，现在是
  「无法写入文件夹“…”：系统拒绝了写入 —— 目录只读、受管控（沙箱/安全软件）环境或
  文件系统限制（EPERM）」，并覆盖权限、只读挂载、磁盘满等常见 errno
  （新增 `fsutil.explain_oserror` 与 `exporter.ExportWriteError`）。

## [1.0.0] - 2026-09-28

1.0 的含义：**数据格式与命令行契约冻结**。`hits/archive.json`（v2）、`state/seen.json`、
`papers.custom.json` 与 `NewspaperAuto.exe` / `run.py` 的子命令与参数自本版起保持向后兼容，
后续版本只允许**新增**字段与子命令。

### Added

- 导出对话框新增「浏览…」：让**看板所在设备**弹出系统原生的文件夹选择窗口，选中的路径
  直接回填输入框（新增 `POST /api/export/browse` 与 `newspaper_auto/dialogs.py`）。
  Windows 用 `SHBrowseForFolderW`、macOS 用 `osascript` 的 Finder 面板、Linux 用
  `zenity`/`kdialog`，全部零第三方依赖。看板跑在没有图形界面的设备上（VPS / SSH /
  无桌面会话）时接口返回 `501`，界面会把按钮收起来并提示手动填写；原生窗口是模态的，
  同一时刻只允许一个（重复请求 `409`），用户取消不算失败。

### Changed

- 浏览器下载的 CSV：`上下文` 列由「两条片段直接拼接」（每行 150～400 字）改为**折叠成
  单行并截断到 60 字**（`format.js` 的 `CONTEXT_MAX_CHARS`），片段里的换行与连续空格会
  压成单个空格，避免整张表被撑得没法看。

## [0.10.0] - 2026-09-28

### Added

- **Windows 7 支持：Release 直接提供单文件 `NewspaperAuto.exe`**。Python 官方从 **3.9 起
  不再支持 Windows 7**，主线的 exe（Python 3.12）在 Win7 上会直接报「不是有效的 Win32
  程序」，所以 Release 现在额外挂一份 `NewspaperAuto-<版本>-win7-x64.zip`：用
  **Python 3.8.10** 构建，双击即用、**不弹命令行窗口**，用法与主线完全一致
  （`scan` 仍把输出写进 `logs\scan-<日期>.log`）。
- **Windows 7 免安装便携版**（`packaging/win7/`，不用 exe 时的零依赖备选）。内置官方
  Python 3.8.10 embeddable + 项目源码，`python3 packaging/win7/make_package.py` 产出
  `NewspaperAuto-<版本>-win7-x64-portable.zip`，解压即用、不需要 pip / PyInstaller。
  `run.py` 是与 exe 对齐的统一入口：由 `pythonw.exe` 启动时**不会弹出命令行窗口**
  （`pythonw` 下 `stdout/stderr` 为 `None`，入口会接回父控制台或指向 `devnull`）。
  另附 `start-dashboard.vbs`（无窗口开看板）、`scan-now.cmd`、`stop-dashboard.cmd`、
  `start-dashboard.cmd`（保留窗口的备用启动器）。
- exe 使用说明补充 Windows 7 运行前提（SP1 + KB2533623 + UCRT / KB2999226），
  win7 包另附 `README-win7.txt`（CRLF + UTF-8 BOM，Win7 记事本可直接阅读）。

### Fixed

- **模块级类型别名在 Python 3.8 上直接崩**：`from __future__ import annotations` 只延迟
  **注解**，`Fetcher = Callable[[str], tuple[int, str]]`、`SeenMap = dict[str, dict[str, int]]`
  这类模块级赋值仍在导入时求值，Python 3.8 会抛
  `TypeError: 'type' object is not subscriptable` —— Win7 版 exe 一启动就崩，而**无窗口版
  看不到 traceback**（只表现为 `/api/health` 迟迟起不来）。两个别名都只在注解里使用，
  已改为字符串常量，3.8 与 3.9+ 通用。

### Changed

- `build-windows` 工作流新增两个**可选**输入 `python-version`（默认 `3.12`）与 `target`
  （默认 `windows`），默认行为与之前完全一致；`build_windows.ps1` 新增 `-Target` 控制产物名，
  非 `windows` 目标跳过 Inno 安装包。目标 Python 非 3.12 时会先跑一遍**源码冒烟**，
  把无窗口 exe 会吞掉的 traceback 暴露到 CI 日志里。
- `.gitattributes` 把 `*.vbs` 也钉成 CRLF 签出；`MANIFEST.in` 的 `packaging` 收录加入 `*.vbs`。
- README 新增「重启电脑后要重新启动吗」：**看板是本地进程，重启后要再启动一次**；
  计划任务 / launchd 与 GitHub Actions 不受影响。并新增「Windows 7」一节。

## [0.9.0] - 2026-09-27

### Changed

- **Windows 便携版收拢成一个 `NewspaperAuto.exe`**。上一版分了「无窗口版 + 控制台版」两个
  exe，包体积翻倍，用户还要猜该双击哪个。现在只有一个**无窗口** exe：双击零窗口起看板 +
  自动开浏览器。代价是 PE 的子系统字段只能二选一（GUI 双击不弹窗，但 Windows 的 shell
  不会等它跑完、`>` 重定向也拿不到内容），所以命令行侧补了两件事：入口把输出接回父控制台；
  `scan` 把本次输出**同时**追加到 `logs/scan-<日期>.log`（与 `run_scan.sh` / `run_scan.ps1`
  同名同位置）。因此**任务计划程序里直接填 `NewspaperAuto.exe scan --days 2 --no-state`
  即可**，不再需要任何 `.cmd` 包装，也不会闪黑窗口。
  `Run-Scan.cmd` / `Start-Dashboard.cmd` 两个启动器与安装包的「扫描一次」快捷方式一并删除。
- **内置六家与自定义报社一视同仁**：都能改抓取规则（保存后生成可撤销的「覆盖」）、停用
  （可随时恢复），以及**彻底删除**。删除写进 `papers.custom.json` 的新字段 `removed`，
  删后不再出现在列表里、也不再有恢复入口（要恢复得手工编辑该文件）；删除时会一并清掉同名
  覆盖与停用记录，避免残留的覆盖把这家报社又「复活」。删掉内置后若又想自己加一家同名报社，
  直接新增即可 —— 新增时会把名字自动从 `removed` 里摘掉。

### Added

- `papers.custom.json` 的新字段 `removed`（可选，旧文件兼容）与
  `POST /api/papers/remove`（彻底删除，内置也可删）；`GET /api/papers` 新增 `removed` 字段，
  已删除的报社不再出现在 `items` 里。

### Fixed

- **扫描时弹出大量控制台窗口**（v0.8.0 引入的回归）：Windows 便携版 exe 改成无控制台窗口
  之后，抓取层的 **curl 兜底**每被派生一次，系统就会新建一个控制台窗口；而扫描是逐页调用
  （默认 8 并发、一次几百个页面），只要走到兜底路径就会刷出成百上千个黑窗口。
  现在把「抑制控制台窗口」抽成 `procutil.hidden_console()` / `detached_console()`，
  运行时**每一处** `subprocess` 派生都显式带上，并由 `tests/test_procutil.py`
  用 AST 扫描守住这条不变量（以后新增派生点忘了带参数会直接测试失败）。

## [0.8.0] - 2026-09-27

### Changed

- **看板：版面位置图改到每一条命中里**。此前它是命中列表右边的独立一栏，看一条报道要在
  列表与图之间来回找；现在**一条命中对应一格缩略位置图**（红框是本条、灰块是同版其他报道），
  就在标题右侧，点缩略图即「只看本版报道」。整版的非命中块会先压成**一条 SVG `path`**
  按版面缓存复用，每行只多一个命中矩形 —— 默认一次 50 行、每版几十个块也不会拖慢渲染。
  右侧独立栏与顶栏「版面位置」导航项一并删除；移动端（≤720px）缩略图与文字改为上下堆叠。
- **看板：筛选收进结果卡右上角的浮层**。原先左侧是常驻 274px 筛选栏、收起后仍占一列；
  现在是一个「筛选」按钮 + 浮层，收起时**完全不占版面**，整行宽度留给命中列表。
  按钮角标显示当前启用了几项筛选；Esc 或点击面板外部关闭，展开状态仍记在浏览器本地；
  窄屏改回文档流。结果列表不再被左右分栏包裹。
- **Windows 分发包：一个包两个 exe**。`NewspaperAuto.exe` 改为**无控制台窗口**，
  双击只起看板并打开浏览器（此前会弹出一个停在「按回车键关闭本窗口」的命令行窗口）；
  `scan` / `dashboard` / `--help` 等全部命令行用法改由新的 `NewspaperAuto-CLI.exe` 承担，
  `Run-Scan.cmd` / `Start-Dashboard.cmd` 与安装包快捷方式都指向正确的那个（安装包的
  「打开看板」直接指向无窗口版）。
- **品牌图标重做**：`logo.svg` 换成一套 16px 也站得住的画法（品牌蓝底 + 白纸 + 琥珀命中块）；
  `favicon.ico` 的 16/24/32/48/64/128/256 七档**全部用同一套画**（此前小尺寸与 48 以上是
  两套画风，Windows 在不同尺寸下会显示不同图案）；`logo-small.svg` 因不再需要而删除。
  顶栏品牌位不再套白底与描边，图标自身的蓝底已提供足够对比度。

### Added

- `scripts/make_icons.py`：品牌图标生成器，**只用标准库**（`zlib` + `struct`），
  几何是唯一真源、SVG 与各尺寸位图都由它渲染；`--check` 可校验已提交的图标是否与几何漂移。

### Fixed

- **网页里的品牌图标「看着很奇怪」**：顶栏此前用的是为大幅面画的 `logo.svg`（白纸 +
  0.65px 细线 + 柔和阴影），缩到 42px 又放在浅色底上几乎看不见。现改为高对比画法，
  并补测试断言 16px 下蓝底 / 白纸 / 琥珀块都还占得住。
- **无窗口版 exe 不再因 `print` 崩溃**：`console=False` 时 `sys.stdout/stderr` 可能是 `None`，
  任何 `print` 都会 `AttributeError`。入口现在先兜底：从 cmd 调用时接回父控制台（输出照常
  可见），否则指向 `os.devnull`；启动失败改用原生对话框提示，不再「双击没反应」。
- Windows 构建的冒烟测试不再只看 `--version` 的退出码（无窗口版打不出字也会显示「绿」），
  改为断言控制台版真的打印出版本号，并让**两个** exe 各跑一遍 `/api/health` 与静态资源。

## [0.7.2] - 2026-09-27

### Changed

- **看板：版面位置图移到命中列表右侧**。此前「03 位置与时间趋势」整节排在结果下方，要往下
  翻很远才看得到版面位置图；现在它作为命中列表**右边一栏**并随滚动吸附（栏内自成滚动区，
  命中多时也不会把整页撑长），窄屏（<1440px）自动折回列表下方并恢复两列排版。页面最大宽度
  由 1380px 提升到 1560px，宽屏下留白更少；时间趋势独占一节，并给单列柱宽设上限，
  只有几天数据时不再被拉得稀稀拉拉。
- **Windows 分发：便携版成为默认推荐**，安装包降级为可选的「快捷方式壳子」。
  `NewspaperAuto-<version>-windows-x64.zip` 解压后双击 `NewspaperAuto.exe` 即用
  （不需要 Python、不需要管理员权限、不写注册表）。

### Fixed

- **Windows 安装包自身没有品牌图标**：`setup.exe` 与「应用和功能」列表此前是 Inno 的通用
  图标，现与 exe / 快捷方式统一使用 `static/favicon.ico`（`installer.iss` 的 `SetupIconFile`）。
  exe / 快捷方式的图标自 0.7.0 起已是品牌图标；若仍看到旧图标，多为升级前安装的旧版本或
  Windows 图标缓存未刷新。

## [0.7.1] - 2026-09-27

### Changed

- **去标识化收尾**：变更记录里描述旧脚本更名时仍带着学校缩写文件名，现改为只描述结果。
  至此**工作树内**（源码、文档、测试 fixture、静态资源）已无任何具体高校 / 机构名的残留。
  不在改动范围的三处：本机运行日志 `logs/`、git 历史、以及历史 tag —— 后两者要改必须重写
  历史，本项目明确禁止。

## [0.7.0] - 2026-09-27

### Added

- **品牌图标**：新增 `static/logo.svg`（纸张 + 命中高亮）、`static/logo-small.svg`（小尺寸简化画法）、
  `static/favicon.ico`（16→256 多尺寸，其中 16/24/32 用简化画法、48 以上用完整版）与
  `static/apple-touch-icon.png`。看板顶栏品牌位与浏览器标签页改用该图标，Windows exe / 快捷方式
  也以同一个 `.ico` 作为图标（不再用 PyInstaller 默认图标）。图标已纳入 wheel / sdist 静态资源
  清单与看板静态白名单。

### Changed

- **`hits/archive.json` 改为 v2 格式：版面布局按版面只存一份**。同一个版面的 `layout`
  此前会跟着该版**每一条**命中写进归档，等于把同一份坐标重复 N 次（一个 40 篇文章的版面，
  实测归档 204 KB 里 90% 是重复坐标，`data` 分支会随天数持续膨胀）。v2 把布局提到顶层
  `layouts` 表按 `paper␁date␁section_no␁section` 去重，记录只保留自身很小的 `geometry`：
  同场景 **204 KB → 19 KB（−90.6%）**。读取**完全兼容**旧版数组格式（旧文件被读到时会
  自动把逐条布局抽出来去重），下次写入即自动升级为 v2；被 `--archive-keep-days` 剪掉的
  日期也不会留下孤儿布局。
- **仓库去标识化**：源码、文档与测试 fixture 里出现的具体高校 / 机构名统一替换为中性占位
  （`示例大学` / `示例实验室` 等），示例与回归测试的语义不变；`legacy/` 下的旧 Google News
  脚本更名为 `legacy/news_tracker.py`，其中写死的关键词默认值与简称表改为可配置。

## [0.6.0] - 2026-09-27

### Added

- **看板：版面位置移到命中报道右侧**。命中列表的列序改为「报社 / 日期 / 标题与命中上下文 /
  版面位置」，一条报道对应右侧一格位置信息（版名 + `版内 k/m` + `上版 xx%`），扫读时
  不必在行内来回找。
- **看板：筛选面板可收起**。结果区右上角新增「隐藏筛选 / 显示筛选」，收起后结果表占满
  整行、信息密度更高；选择记忆在浏览器本地（`localStorage`），重开看板保持。

### Changed

- **看板响应体大幅瘦身**：同一个版面的 `layout` 此前会跟着该版**每一条**命中记录重复下发，
  一个 40 篇文章的版面实测占 `/api/hits` 响应体的 90%。现在 layout 抽到顶层 `layouts`
  表按版面去重（记录只保留 `geometry`），同场景响应体由 152 KB 降到 18 KB（**−88%**）。
  前端按相同分组键取回，版面位置图渲染结果不变。
- **部分抓取失败不再被当成「全绿」**：单家报社单日的抓取失败占比达到
  `config.PARTIAL_FAILURE_RATIO`（默认 50%）时，CLI 以退出码 1 结束，让 CI 步骤变红，
  避免「多数文章没抓到」却报成一次正常扫描。未达阈值的零星失败仍只告警，不影响退出码；
  日志里每家报社的统计行也会直接标出「⚠️ 版面失败N / 文章失败M」。

### Fixed

- **命中摘录会把全角字形折叠成半角**：上个版本为修全角漏匹配引入的 NFKC 归一化，导致
  摘录直接从归一化文本切片，正文里的「ＡＩ」在报告里显示成「AI」。现在归一化只用于
  **定位**，摘录回到原文切片，保留原始字形；极少数跨字符组合场景自动退化（匹配不受影响）。
- **从仓库外调用时读不到自定义报社**：`daily_scan.py` / `dashboard.py` 现在把「项目根」
  钉死为脚本所在目录（内部设置环境变量 `NEWSPAPER_APP_DIR`，后台看板子进程一并继承），
  `papers.custom.json` 不再跟随当前工作目录解析。此前从仓库外执行（如
  `python3 /path/AutoNewspaper/daily_scan.py`）会静默忽略仓库里已提交的自定义报社，
  看板新增报社时也会写回错误的目录。
- **macOS 双击入口不可执行**：`scripts/scan.command`、`scripts/dashboard.command` 与
  `scripts/run_scan.sh` 此前以 100644 入库，Finder 双击会直接 `permission denied`，
  文档里 `./scripts/run_scan.sh ...` 的用法同样报错。现已改为 100755，并加了防回归测试。
- **关键词打成全角就漏匹配**：正文里写「ＡＩ」、关键词写「AI」（或反过来）此前一律不命中，
  属静默漏报。关键词与正文现统一做 NFKC 归一化（全角字母/数字/括号折叠为半角）再匹配。
- **Windows 便携 zip 夹带构建脚本**：`NewspaperAuto-<版本>-windows-x64.zip` 此前用
  `Copy-Item packaging\windows\*` 打包，把 Inno Setup 脚本 `installer.iss` 一起发给了
  最终用户。现改为显式列举 4 个用户文件（exe + 两个 `.cmd` + 使用说明）。

## [0.5.0] - 2026-09-27

### Added

- **Windows 安装包**：`NewspaperAuto-<版本>-setup.exe`。双击安装到
  `%LOCALAPPDATA%\Programs\NewspaperAuto`（用户可写，**不需要管理员权限**），自动创建
  开始菜单快捷方式（打开看板 / 扫描一次 / 使用说明 / 卸载）与可选的桌面快捷方式；
  卸载**不会删除** `state/`、`hits/` 里的扫描数据。
- **双击即用**：直接双击 `NewspaperAuto.exe`（不带参数）就会打开看板并自动打开浏览器；
  以前无参数只打印帮助，双击后窗口一闪而过，容易被当成「程序坏了」。
- **看板后台运行**：新增 `dashboard --detach` / `--stop` / `--status`。服务脱离控制台运行，
  运行状态（host/port/pid）记录在 `state/dashboard.json`，重复启动会**复用**已在运行的实例。
- **安装/升级/卸载不再被"看板正在运行"挡住**：安装包会在替换文件前自动停掉后台看板
  （`--stop` 也会等到 exe 的文件句柄真正释放），不必让用户先手动关闭。
- **页面自助恢复**：连不上服务时，页面给出明确的恢复指引，并提供「重新连接看板」按钮，
  不再只提示「请确认看板仍在运行」。

### Changed

- **看板默认后台运行**：双击启动器（`Start-Dashboard.cmd` / `dashboard.command` / `dashboard.cmd`）
  与 `NewspaperAuto.exe` 都走 `--detach`。此前 `dashboard --open` 跑在**前台控制台**里，
  用户扫完一次就把黑窗口关掉 —— 那正是服务进程本体，于是浏览器里再点「开始扫描」只会看到
  「无法连接本地服务」。现在关掉窗口不会停止服务，停止请用 `dashboard --stop`。
  源码模式下 `make web` / `make start` 仍为前台（开发/排障场景，属预期行为）。
- **端口回退更利索**：改为启动前先探测端口，被占用时立即改用系统分配的空闲端口，
  不再白等一个就绪超时；实际地址以启动输出与 `state/dashboard.json` 为准。

### Fixed

- Windows 上 `dashboard --stop` 在需要强制结束进程时会因 `signal.SIGKILL` 不存在而报错
  （Windows 无该信号，现退回 `SIGTERM`）。
- 后台运行时日志 `logs/dashboard.log` 改为行缓冲，排障时不再「什么都没有」。

## [0.4.0] - 2026-09-27

### Added

- **文档重组**：`README.md` 精简为面向首次使用者的入口，把部署、调度、方案原理、看板
  与自定义报社等长章节迁入 `docs/`（`production-deployment.md`、`scheduling.md`、
  `architecture.md`、`dashboard.md`、`custom-papers.md`），并从 README 链接过去。
- **`data` 分支归档**：`scan.yml` 每日扫描结果（`hits/`、`state/`）改存到专用 `data`
  分支，供下载与审计。

### Changed

- **运行产物不再进仓库**：`hits/` 与 `state/` 加入 `.gitignore`；每日扫描不再向 `main`
  提交，而是推送到 `data` 分支（运行前自动恢复），消除 `main` 上每天整体重写的巨量 diff
  与两方抢推冲突。`papers.custom.json` 仍作为配置入库。
- **CI 供应链加固**：`actions/upload-artifact`、`actions/download-artifact` 由浮动
  `@v4` 固定到 commit SHA（与 `checkout` / `setup-python` 对齐）；`python -m build`
  未锁版本的情况已在 workflow 内注明。
- **启动脚本去重**：`run_scan.sh` / `run_scan.ps1` 改为薄封装，只解析解释器、`cd` 项目根、
  补默认（`--no-state`，未给日期时补 `--days 2`）并**原样转发**其余参数，删除重复维护的
  CLI 选项白名单；`scan.cmd` / `scan.command` 改为薄封装；看板端口回退逻辑从 `Makefile`
  收敛到 `dashboard.command` / `dashboard.cmd`，`make web` / `make start` 直接调用脚本。
- **前端测试的 Node 清单移入 `tests/frontend/`**（`package.json` / `package-lock.json`），
  仓库根不再像一个 Node 项目；CI 的 `npm ci` 与前端测试相应改到该目录运行。
- **源码归档与分发口径统一**：`docs/` 不再 `export-ignore`（GitHub Source zip 现在包含文档），
  Release 便携包与 `MANIFEST.in` 同步包含 `docs/`。
- `legacy/README.md` 更新为只保留旧脚本 + 说明。

### Fixed

- **`main` 上的运行数据冲突**：修复每日 CI 与本地提交争抢 `hits/`、`state/` 导致的
  巨大 diff 与合并冲突；`main` 现在只承载源码与配置。
- **文档链接**：README 指向 docs/ 的链接改为新的 ASCII 文件名（原先的文档名含乱码中文，
  Source zip 用户无法阅读）。

### Removed

- `legacy/report.html`、`legacy/result.csv`、`legacy/result.json`——`legacy/news_tracker.py`
  可随时再生成；保留脚本与 `legacy/README.md`。
- `run_scan.sh` / `run_scan.ps1` 中的 CLI 选项白名单与「未知选项」拒绝逻辑。
- `docs/` 的 `export-ignore`（`legacy/**`、`.github/**` 仍忽略）。

## [0.3.2] - 2026-09-26

### Added

- Windows 单文件便携版：`newspaper-auto.exe`（`scan` / `dashboard` 子命令）、
  PyInstaller 打包、双击启动器与使用说明；构建产物为可分发的
  `NewspaperAuto-<版本>-windows-x64.zip`。exe 只捆绑标准库，运行时依旧零第三方依赖。
- `--archive-keep-days N`：可选地只保留 `hits/archive.json` 最近 N 天（0 = 不限，默认）。

### Changed

- 启动脚本补齐参数透传（`--since/--until/--export-dir/--export-format/--version` 等），
  默认回溯天数统一为 2（今天 + 昨天）。
- 看板「取消扫描」细化到单个在途请求粒度（排队任务立即空转结束）。

### Fixed

- 版面几何的 `index/total` 不再被非本版 `<area>` 撑大；无坐标时正确回退到 mapping / order。

## [0.3.1] - 2026-09-25

### Fixed

- 看板前端在真实浏览器中不可用（静态资源白名单漏掉 `store.js`）；`dashboard.py`
  从仓库外启动时数据目录落错位置；`--host ::1` 绑定失败；静态 `index.html` 链接未校验协议。
- 扫描回溯天数统一为 2，修复只扫 1 天时「当天未上线」被永久漏报的问题。

### Changed

- 便携源码包不再携带维护者的 `state/` 与 `hits/`；`.gitignore` 忽略看板任务历史。

### Added

- 静态资源可达性、`--host` 归一化、CSV 转义等回归测试。

## [0.3.0] - 2026-09-25

### Added

- **分发与克隆体验**：`release.yml` 产出 sdist / wheel / 便携源码 zip；README 给出浅克隆与
  直接下压缩包的加速方式。
- **前端自动化测试**：Node 内置 test runner 的纯函数 / reducer 测试，以及 jsdom DOM 测试。
- **抓取层标准库优先、系统 `curl` 兜底**：默认走 `urllib`（HTTP/1.1），规避光明日报在
  HTTP/2 下断连，同时不再强制依赖系统 `curl`。
- **看板进度 SSE + 协作式取消 + 任务历史**。
- **完整报社生命周期管理**：新增 / 编辑 / 试抓 / 停用 / 恢复 / 删除与同名覆盖。
- **指定目录导出**：CLI `--export-dir` / `--export-format`，看板「导出结果」。
- **版面位置（几何）**：`<area>` / `mappingOriginal` 归一化为版内位置与「上版 xx%」。
- CLI 显式日期范围 `--since` / `--until`（两端都含，与 `--days` 互斥）。
- 新增人民日报；本地「点击即用」双击启动器（macOS / Windows）。

### Fixed

- 防止 HTTP 200 但解析不到内容时的静默漏报（改以非零退出）。
- 状态与归档改为原子写入；自定义链接模板校验收紧；CSV 导出防护公式注入。
- 修正「launchd 与 Actions 必须二选一」的错误结论（本机路径无状态，可与 Actions 并存）。

## [0.2.0] - 2026-09-22

### Added

- 拆分为 `src/newspaper_auto/` 包，`daily_scan.py` 保留为兼容入口；新增本地只读看板。
- 定时任务运行时间可自定义（`install [HH:MM ...]` / `NEWSPAPER_TIMES`）。
- `pyproject.toml`、`Makefile`、ruff、GitHub Actions CI、MIT License；离线单元测试与
  可注入 fetcher 的扫描/CLI。

### Fixed

- `scripts/run_scan.sh` 位置参数与 `--kw` 的解析 Bug；去掉写死的 Python 绝对路径；
  `state/seen.json` 改为原子写入。

## [0.1.0] - 2026-09-22

### Added

- 五家报社数字报直采（光明日报 / 南方日报 / 羊城晚报 / 中国教育报 / 湛江日报），
  标题 + 正文全文匹配。
- 增量状态与 Markdown / HTML 报告；无状态运行 `--no-state` 与 `--json` / `--csv` 输出。
- macOS `launchd` 每日定时任务；`curl` 强制 HTTP/1.1，修复光明日报在 HTTP/2 下断连。
