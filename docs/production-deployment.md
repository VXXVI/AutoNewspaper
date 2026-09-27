# 生产部署与分发

> 本文由原 `README.md` 的部署/分发/CI 章节整理而来，并在文末保留早期交付记录。
> 安装与日常用法见仓库根 [README.md](../README.md)；调度见 [scheduling.md](scheduling.md)。

## 需要服务器吗？不需要

| 项 | 实现 | 是否需额外服务器 |
|---|---|---|
| 数据存储 | 本地文件 `state/seen.json`、`hits/*` | 否（无需数据库） |
| 抓取 | 标准库 HTTP 直连六家报社官网（`curl` 兜底） | 否，仅需外网出站 |
| 运行 | **GitHub Actions**（免费 runner，生产路径）或本机 Python | 否 |
| 调度 | 生产 = GitHub Actions + 外部触发；本机 = macOS `launchd` / Windows 任务计划程序 | 否 |
| 依赖 | Python 3.9+，**0 个第三方运行时包**（`curl` 可选） | 否 |

唯一的"服务器"角色是**被采集方**——六家报社官网本身，这是数据源，无法避免。
需要注意的边界：**本机**定时任务要求本机在触发时刻处于唤醒/登录状态
（休眠会顺延到唤醒后触发）；Actions 路径则不受本机开关机影响。
若想 7×24 稳定，可以是"常开本机"，也可以是任意一台便宜 VPS 跑 cron，
但**都不需要安装数据库或常驻服务框架**。仓库内置的 Web 看板只面向单用户、本机访问；
只有要做公网多用户、登录权限或消息推送时，才需要引入正式服务端架构。

**是否需要打包 exe：源码分发不需要；Windows 用户可选。** 项目**零第三方运行时依赖**，
有 Python 环境时直接跑源码即可。但把工具发给**没装 Python 的 Windows 用户**时，单文件
exe 最省事：CI 在打 `v*` tag 时构建 `NewspaperAuto-<tag>-windows-x64.zip`（内含
`NewspaperAuto.exe` + 双击启动器），也可在仓库 Actions 手动触发 `build-windows` 构建并下载。
exe 只捆绑标准库，**运行时仍零第三方依赖**。

> exe 解决不了「谁每天来触发」——它自己不会定时启动；本机定时仍需 Windows 任务计划程序
> （见 [scheduling.md](scheduling.md)）。exe 的定位是**便于手动使用与分发**。

## 持续集成（GitHub Actions）

| workflow | 触发 | 作用 |
|---|---|---|
| `ci.yml` | push / PR | Py3.9/3.12/3.13 上 ruff、格式检查、pytest；`node --check` 前端脚本 + 前端单测；Shell 启动脚本语法；Windows job 验证 PowerShell 解析与 UTF-8 BOM |
| `scan.yml` | schedule / dispatch / 手动 | 每日扫描，结果归档到 `data` 分支（见下） |
| `build-windows.yml` | 手动 / 被 release 复用 | 在 `windows-latest` 构建单文件 exe 并产出可分发的 zip |
| `release.yml` | 打 `v*` tag | 构建 sdist / wheel / 便携源码 zip + Windows exe，统一发布到 GitHub Release |

### `main` 不再接收运行数据（`data` 分支）

`hits/` 与 `state/` 是每次运行都会整体重写的产物，曾长期提交回 `main`，导致巨大 diff、
无限增长与两方抢推冲突。现在：

- `.gitignore` 忽略 `hits/` 与 `state/`；`main` 只承载源码与配置（`papers.custom.json` 仍入库）。
- `scan.yml` 每次运行前从专用的 **`data` 分支**恢复 `hits/`、`state/`，扫描后再用独立
  `git worktree` 把结果提交并推送回 `data` 分支（`fetch-depth: 0`、`contents: write`）。
- 推送带**有界重试**：并发推送被拒时先 `pull --rebase --autostash` 再重试，最多 3 次。
- 提交步骤用 `if: always()`，扫描即使部分失败也会把已落盘的结果推送到 `data`，不丢数据。

选择 `data` 分支而非 artifact + `actions/cache`：cache 不可变、受容量上限影响且可能被逐出，
一旦丢失就会把旧文章当「新增」重复报告；`data` 分支可靠、可审计、无容量上限问题，且天然
支持并发（见上）。任何仍需要推 `main` 的场景，也应使用同样的「push 失败则 rebase 重试」模式。

### CI 供应链

- `actions/checkout`、`actions/setup-python`、`actions/upload-artifact`、
  `actions/download-artifact` 全部固定到完整 commit SHA（而不是浮动大版本标签）。
- `release.yml` 中 `python -m pip install --upgrade build` 与 `python -m build`
  的构建工具链**未固定版本**（注释已标明）；如需可复现构建，应显式锁定版本。

## 分发给别人用（推荐方式）

按目标用户从易到难：

1. **Windows 便携版（默认推荐，点击即用、无需安装）**：打 `v*` tag 后，
   `.github/workflows/release.yml` 会在 `windows-latest` 上构建并挂出
   `NewspaperAuto-<tag>-windows-x64.zip`（内含 `NewspaperAuto.exe`、`Run-Scan.cmd`、
   `Start-Dashboard.cmd` 与使用说明）。**解压到任意可写目录，双击 `NewspaperAuto.exe`
   即可**（起后台看板 + 自动开浏览器）——不需要 Python、不需要管理员权限、不写注册表，
   整个文件夹可整体拷贝换机（数据默认写在 exe 旁边）。勿放 `C:\Program Files`（无写权限）。
   也可在仓库 **Actions → build-windows → Run workflow** 手动构建并下载 artifact，无需打 tag。
   本机自行构建：在 Windows 上执行 `scripts\build_windows.ps1`
   （`pip install pyinstaller` 属构建期依赖，不进入运行时）。
2. **Windows 安装包（可选的便捷壳子）**：同一份构建产出的
   `NewspaperAuto-<tag>-setup.exe`，双击安装，装到
   `%LOCALAPPDATA%\Programs\NewspaperAuto`（**用户可写**，无需管理员权限），
   自动创建开始菜单快捷方式（打开看板 / 扫描一次 / 使用说明 / 卸载）与可选的桌面快捷方式，
   装完可直接开看板。它只是给「想要开始菜单/桌面快捷方式」的人用的；
   **不装安装包一样能用上面的便携 zip**。卸载**不删除** `state/`、`hits/`（扫描数据是用户资产）。
   便携 zip 与安装包共用同一个 `NewspaperAuto.exe`；exe、快捷方式与 `setup.exe` 自身
   都使用 `src/newspaper_auto/static/favicon.ico` 这一份品牌图标
   （`NewspaperAuto.spec` 的 `icon=` 与 `installer.iss` 的 `SetupIconFile`）。
   构建需要 Inno Setup（CI 由 chocolatey 安装）；缺 Inno 时脚本只跳过 `setup.exe`，
   便携 zip 照常产出。
3. **复制整个项目目录**：数据（`hits/`、`state/`）与配置（`papers.custom.json`）都在目录内，
   换机即用；目标机只需装 Python 3.9+。
4. **从 Release 下载压缩包**（推荐给国内 / Python 用户）：打 `v*` tag 会触发
   `.github/workflows/release.yml`，在 GitHub Release 里同时提供
   - `NewspaperAuto-<tag>-portable.zip`：源码精简包（已剔除 `tests/`、`legacy/`、
     `.github/`、`state/`、`hits/`，**包含 `docs/`**），**解压即用**，无需 git；
   - `.whl` / `.tar.gz`：`pip install <whl>` 或 `uv tool install <whl>`（`uv` 会自动准备 Python）。
5. **便携 Python（无 Python 环境、又不想用 exe）**：Windows 用官方 *embeddable* Python 解压到
   项目内 + 一个启动脚本；macOS 用 python.org 安装包。

**Windows exe 用法**：双击 `NewspaperAuto.exe`（或 `Start-Dashboard.cmd`，两者等效）打开看板，
`Run-Scan.cmd` 扫一次。看板服务默认**后台运行** —— 关掉命令行窗口不会停止它，
停止用 `NewspaperAuto.exe dashboard --stop`，查状态用 `dashboard --status`。
命令行还可用 `NewspaperAuto.exe scan --days 7`、`NewspaperAuto.exe dashboard --port 9000 --open`
（`NewspaperAuto.exe --help` 查看全部子命令）。`state/`、`hits/` 与 `papers.custom.json`
默认写在 **exe 所在目录**，整个文件夹可整体拷贝换机。

> 为什么把看板放到后台：早先 `dashboard --open` 跑在前台控制台里，用户扫完一次就把黑窗口
> 关掉 —— 那正是服务进程本体，于是浏览器里再点「开始扫描」只会看到「无法连接本地服务」。
> 现在服务与控制台解耦，并把 `host/port/pid` 落在 `state/dashboard.json`：
> 启动器据此复用已在运行的实例（也顺带解决「端口变了、旧标签页连不上」）。
> 前端在连不上时会给出明确的恢复指引与「重新连接看板」按钮。

> exe 未做代码签名，首次运行可能触发 Windows SmartScreen：点「更多信息 → 仍要运行」即可；
> 若要做正式签名分发，需自行准备 Windows 代码签名证书。exe 用 PyInstaller **单文件**构建、
> 只捆绑标准库；对体积/启动更敏感时可在 `packaging/NewspaperAuto.spec` 里改成 onedir。

### 获取源码

当前 GitHub 仓库是私有库：**有访问权限且已完成 GitHub 认证的账号可以正常 clone**；
未登录或没有权限时 GitHub 会拒绝。可使用 GitHub Credential Manager、`gh auth login`
或已配置的 SSH 密钥完成认证。没有权限时，也可直接使用上面的 `portable.zip` / Source zip。

### 关于 `git clone` 慢

> ⚠️ **`git clone` 慢 ≠ 仓库大**。仓库本身很小、克隆慢几乎总是**到 GitHub 的网络往返延迟/抖动**，
> 而不是体积。加速办法（任选）：
> - **浅克隆**：`git clone --depth 1 https://github.com/VXXVI/AutoNewspaper.git`（只取最新一次提交）；
> - **直接下压缩包**：到 Releases 下载 Source code (zip) 或上面的 `portable.zip`，解压即用，**完全不碰 git**；
> - 维护者在本地可 `git gc` 压缩 `.git`，但对克隆速度影响有限。

---

## 附：历史交付记录（v0.2 工程化改造）

> 以下为项目早期的交付说明存档，数量与界面描述可能不随主线更新；

# 交付说明：v0.2 工程化改造

> 本文是 v0.2 阶段的历史交付记录，部分数量与界面描述不会随主线更新；当前安装与使用方式
> 以仓库根目录的 `README.md` 为准。

- 项目：五家报社数字报关键词监测（NewsPaperAuto）
- 日期：2026-09-22
- 范围：脚本 Bug 修复 + 包结构重构 + 测试/打包/CI + 文档
- 状态：已完成，单元测试与真实抓取均已复跑通过
- 前置文档：[`scheduling.md`](scheduling.md)（v0.1 能力，本文档在其之上改造）

---

## 一、本次要解决的问题

v0.1 功能可用，但存在工程化缺口：

1. **`scripts/run_scan.sh` 真实 Bug**：位置参数一律当关键词，`--kw 关键词` 会被展开成
   `--kw --kw --kw 关键词`，argparse 报 `argument --kw: expected one argument`。
   `logs/scan-2026-09-22.log` 里那次失败即此原因。
2. **硬编码绝对路径**：`run_scan.sh` 曾写死某台开发机专用的 Python 路径，plist 也曾
   写死项目路径 —— 换机即坏。
3. **无测试、无打包配置、无 lint、无 CI、无 License**。
4. 单文件 530 行，解析与网络逻辑难以单测。

目标：在**保持「零第三方运行时依赖 + 单机 CLI」定位不变**的前提下补齐工程配套。

---

## 二、变更清单

### 2.1 修复

| 文件 | 变更 |
|---|---|
| `scripts/run_scan.sh` | 重写参数解析：支持 `--kw X` / 裸关键词 / `--`；未知选项明确报错；`--days/--workers/--only/--data-dir` 透传；关键词去空白由 `xargs` 改 `sed`（避免误解析引号）；解释器改为 `NEWSPAPER_PYTHON` → 项目 `.venv` → `PATH` 依次探测，**不再写死用户路径** |
| `src/newspaper_auto/state.py` | `seen.json` 改为**原子写入**（临时文件 + `os.replace`），避免中断产生半截 JSON |
| `src/newspaper_auto/scanner.py` | 入口失败分类逻辑集中为 `_classify_entry_failure`，行为不变 |

> 兼容性：`seen.json` 格式未变（仍为 `{关键词: {URL: 0/1}}`），旧状态可直接沿用。
> 「新增」判定保持**基于 URL 是否已出现**的语义，与 v0.1 一致。

### 2.2 重构为包

```
daily_scan.py                 # 同名兼容入口（补 src/ 到 sys.path 后委托 cli.main）
src/newspaper_auto/
    config.py    报社入口 / 清洗规则 / 默认值（纯数据）
    paths.py     数据目录解析（--data-dir → NEWSPAPER_DATA_DIR → 项目根 → cwd）
    http.py      curl 抓取（强制 HTTP/1.1 + 重试）
    parse.py     HTML 解析与关键词匹配（纯函数，无 I/O）
    scanner.py   扫描编排（入口→版面→文章）
    state.py     增量状态
    report.py    Markdown / HTML / JSON / CSV 渲染
    cli.py       参数解析与主流程
```

- 所有网络访问可经 `fetcher` 注入替换，使扫描与 CLI 可**完全离线**测试。
- 新增 `--data-dir`、`--version`；`--json` / `--csv` / `--no-state` 行为与 v0.1 一致。

### 2.3 定时任务可移植化

- 新增 `scripts/com.quark.newspaperauto.plist.template`（占位符 `{{PROJECT_DIR}}`），
  删除原来的硬编码 plist。
- `scripts/install_launchd.sh` 安装时替换真实路径，并用 `plutil -lint` 校验；
  新增 `print` 子命令（不落盘预览）。
- **运行时间可自定义**：`install [HH:MM ...]` 支持一天多个时间点，或 `NEWSPAPER_TIMES`
  环境变量；模板中的 `{{START_CALENDAR_INTERVAL}}` 由安装脚本生成（单个 `dict` /
  多个 `array`），非法时间格式（`25:00`、`7am`）直接报错退出。

### 2.4 工程配套

| 文件 | 作用 |
|---|---|
| `pyproject.toml` | 打包元数据（`dependencies = []`）、pytest、ruff 配置 |
| `Makefile` | `install / test / lint / fmt / check / dry / scan / json / csv / plist / clean` |
| `.github/workflows/ci.yml` | Python 3.9 / 3.12 / 3.13 上跑 ruff、format check、pytest、`bash -n` |
| `tests/` | 76 个离线单元测试 + `tests/fixtures/*.htm(l)` |
| `LICENSE` | MIT |
| `CHANGELOG.md` | 变更记录（Keep a Changelog） |
| `.gitignore` | 追加 `.venv/`、`.pytest_cache/`、`.ruff_cache/`、`build/`、`dist/` |

### 2.5 本地只读看板

- `src/newspaper_auto/web.py`：基于标准库 `ThreadingHTTPServer` 的只读看板，
  路由 `/`、`/static/*`、`/api/hits`、`/api/health`。
- `src/newspaper_auto/static/`：原生 HTML/CSS/JS 单页（无构建、无 CDN），
  提供概览统计、按日期分布柱状图、报社/关键词/日期筛选、搜索、排序、跳原文。
- **只读**：只读 `hits/archive.json` 与 `state/seen.json`，不写文件、不触发抓取。
- **默认只监听 `127.0.0.1`**；静态资源走白名单匹配，天然杜绝路径穿越；
  前端一律用 `textContent` 渲染抓取内容，且链接只允许 `http/https`。
- 端口占用不再抛裸 traceback，而是给出中文提示并返回退出码 1。

---

## 三、验证记录

### 3.1 静态检查与单元测试（离线）

```
$ make check
ruff check .            → All checks passed!
ruff format --check .   → 21 files already formatted
pytest                  → 76 passed（含 12 个看板/回环 HTTP 测试）
```

测试覆盖：解析（标签/实体/全角空格/标题回退/链接过滤与绝对化/关键词跨标签容错/摘要截断）、
状态（缺失/损坏/非字典兜底、原子写、过期清理、新增判定）、扫描（正常、dry、入口三类失败、
版面失败、文章失败、跨日期过滤）、报告（Markdown/HTML 转义/JSON/CSV）、CLI（JSON 纯净性、
CSV、互斥、增量两次运行、`--no-state`、`--full`、dry）。

### 3.2 脚本行为（不联网）

```
./scripts/run_scan.sh --kw 示例大学 --help
  → 命令：... daily_scan.py --days 1 --no-state --kw 示例大学 --help   （旧 Bug 已修复）
./scripts/run_scan.sh 示例大学 示大 --help        → --kw 示例大学 --kw 示大
NEWSPAPER_KW="示例大学, 示大 ;例大" ./scripts/run_scan.sh --help
  → --kw 示例大学 --kw 示大 --kw 例大
./scripts/run_scan.sh --bogus
  → 错误：未知选项：--bogus（退出码 2）
bash -n scripts/*.sh                                  → OK
./scripts/install_launchd.sh print | plutil -lint -- -  → OK
```

### 3.3 真实抓取（联网）

| 项 | 命令 | 结果 |
|---|---|---|
| 入口探测 | `python3 daily_scan.py --days 1 --dry` | 光明 17 / 南方 36 / 羊城 9 / 教育 13 / 湛江 9 版面，五家全 ✓ |
| 无状态 JSON | `python3 daily_scan.py --days 1 --no-state --json --kw 示例大学 --kw 示大` | `json.loads` 通过，records = 2（均为湛江日报）；stdout 纯 JSON，日志在 stderr |
| 无状态证据 | 跑前 → 跑后 | `state/seen.json` md5 恒为 `ef0c2b57b6e512434e6b678e2196ad06`；`hits/` 清单不变 ✓ |
| 增量兼容 | 以 **v0.1 生成的 540 条旧状态**副本跑 `--data-dir` | 扫 312 篇 / 命中 2 / **新增 0**，无重复误报 ✓ |

命中的两条与 v0.1 复跑结果一致：
`content_146377_3863845.htm`（红树林）、`content_146378_3863866.htm`（五一劳动奖状公示）。

---

## 四、兼容性

- `python3 daily_scan.py <原有参数>` 用法不变；`--no-state` / `--json` / `--csv` 语义不变。
- `scripts/run_scan.sh` 调用方式不变（位置参数仍被当关键词），并新增 `--kw` / 透传选项。
- `state/seen.json`、`hits/*` 格式不变，历史数据可直接继续使用。
- launchd：**需重新执行一次** `./scripts/install_launchd.sh install` 以使用新模板
  （原 `scripts/com.quark.newspaperauto.plist` 已删除）。

---

## 五、未决风险

1. **定时任务未安装**：本次未改动 `~/Library/LaunchAgents`，确认需要再执行 `install`。
2. **休眠顺延**：Mac 深度睡眠时任务顺延到唤醒后触发；长期稳定建议常开机器。
3. **curl 依赖**：运行时仍需系统 `curl`；缺失时 `http.py` 会抛出带中文提示的
   `CurlNotFoundError`。刻意未引入 `requests`，因为光明日报的 HTTP/2 断连问题需要
   curl 的 `--http1.1` 才能稳定规避（详见 README「已知边界」）。
4. **未提交**：本轮变更按逻辑分步提交，具体见 `git log`。

---

## 六、补充（部署形态定稿与失败可见性）

> 本节为同日后续补充，覆盖前文与定时方式、失败可见性相关的表述。

1. **生产定时改为外部触发**：私有仓库下 GitHub 原生 `schedule` 实测**零触发**
   （`*/5` 探测运行约 36 分钟，`gh run list` 零条记录），故 `scan.yml` 中的
   `schedule: "0 2 * * *"`（UTC 02:00 = 北京 10:00）仅保留不动，**生产改由 VPS crontab 调
   `POST /repos/VXXVI/AutoNewspaper/dispatches {"event_type":"scan"}` 触发**。
   触发用 PAT 为 fine-grained，**仅需 `Contents: Read and write`**，只存于 VPS 本机，
   **不得写入仓库、日志或提交信息**（README 中只写 `<PAT>` 占位符）。
   完整 crontab 行、时区换算与排查步骤见 `README.md`「外部定时（VPS crontab）」。
2. **本机调度与 Actions/VPS 可以并存**：`scripts/run_scan.sh` 自诞生起即无条件携带
   `--no-state`，本机调度路径**不读写 `state/seen.json`、不产 `hits/*`**，只把结果打到
   控制台并写 `logs/`；Actions 负责 `hits/*`、`hits/archive.json` 与 `state/seen.json`
   （归档 + 增量）。两者产出物不同、互不覆盖。旧的「两者共写 `state/seen.json`，必须
   二选一」说法已被证伪，不再适用；**不再需要本机任务时**可选执行
   `./scripts/install_launchd.sh uninstall` 卸载。
3. **抓取失败不再静默**：
   - 扫描统计新增 `sections_failed` / `articles_failed` / `failed_urls`；
   - Markdown 报告新增「抓取失败（缺报）」章节（明确「无命中 ≠ 真的没有」），
     「各家扫描情况」表新增「失败」列；
   - CLI 在**入口失败**、或**抓取失败占比达到 `config.PARTIAL_FAILURE_RATIO`（默认 50%）**时
     返回退出码 1，让 Actions 步骤变红；未达阈值的零星失败只在日志/报告里告警，不影响退出码；
   - `scan.yml` 提交步骤改为 `if: always()`，失败也提交已落盘的增量结果，
     提交信息追加 `[含失败]` 后缀；
   - 羊城晚报在 GitHub runner（Chicago）上原始请求 30s 才返回 `HTTP 000`，属临界，
     已在 `config.PAPERS` 单独覆盖为 `timeout=60 / retries=4`。
4. **测试与检查**：`pytest` 82 passed；`ruff check .` / `ruff format --check .` 全过。
