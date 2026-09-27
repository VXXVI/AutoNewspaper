# 变更记录

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与
[语义化版本](https://semver.org/lang/zh-CN/)。条目面向使用者，描述「能感知到的变化」，
不再记录函数级实现细节。

## [Unreleased]

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
