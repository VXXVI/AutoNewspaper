<img src="src/newspaper_auto/static/logo.svg" alt="数字报监测" width="96" align="right">

# 数字报 · 关键词每日监测

**内置媒体**：光明日报 / 南方日报 / 羊城晚报 / 中国教育报 / 湛江日报 / 人民日报（可自定义扩展）
**数据源**：各报社**官网数字报**（报纸电子版）—— 不是 Google/Bing News

一个**本地单机监测工具**：既可用 CLI，也可在浏览器看板中一键抓取。项目会扫描当日全部版面
文章，按关键词做**标题 + 正文全文匹配**，正文里提一句也能命中。运行时**零第三方依赖**
（仅 Python 标准库；系统 `curl` 为可选兜底，抓取默认走标准库 HTTP/1.1）。默认关键词是
「人工智能」，可在命令行或看板中随时修改；改词不会清空已有的历史归档。

> 运行产物（`hits/` 报告与历史命中、`state/` 增量状态）不再入库：本地由工具自行创建，
> CI 则归档到专用的 `data` 分支。详见 [docs/production-deployment.md](docs/production-deployment.md)。

## 需要服务器吗？不需要

| 项 | 实现 | 是否需额外服务器 |
|---|---|---|
| 数据存储 | 本地文件 `state/seen.json`、`hits/*` | 否（无需数据库） |
| 抓取 | 标准库 HTTP 直连六家报社官网（`curl` 兜底） | 否，仅需外网出站 |
| 运行 | **GitHub Actions**（免费 runner，生产路径）或本机 Python | 否 |
| 调度 | 生产 = GitHub Actions + 外部触发；本机 = macOS `launchd` / Windows 任务计划程序 | 否 |
| 依赖 | Python 3.9+，**0 个第三方运行时包**（`curl` 可选） | 否 |

本机调度以 `--no-state` 无状态运行（只打印结果、写 `logs/`，不读写 `state/`、不产 `hits/`），
因此可与 GitHub Actions **同时启用、互不覆盖**。调度与部署细节见
[docs/scheduling.md](docs/scheduling.md) 与 [docs/production-deployment.md](docs/production-deployment.md)。

## 安装

项目支持 **macOS / Linux / Windows 10 1803+**。从 GitHub 获取源码需要 **Git**；
程序运行时只需要 **Python 3.9+**，不需要 `pip install`，也不需要先创建虚拟环境。
抓取默认走标准库，**系统 `curl` 只是可选兜底**。

macOS / Linux：

```bash
git clone https://github.com/VXXVI/AutoNewspaper.git
cd AutoNewspaper
python3 --version
python3 daily_scan.py --version
python3 daily_scan.py --dry          # 只探测入口与版面，不抓正文
python3 daily_scan.py                # 扫今天+昨天，只报「新增」命中
```

Windows（命令提示符或 PowerShell）：把上面的 `python3` 换成 `python` 即可。

开发（跑测试 / lint）：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"     # Windows: .venv\Scripts\python.exe
.venv/bin/python -m pytest
```

装有 `uv` 与 `make` 的 macOS / Linux 开发机也可用 `make install && make check`。

## 首次运行

```bash
python3 dashboard.py --open --scan   # 打开看板并按默认配置抓取一次
```

只想确认程序能启动，用 `python3 daily_scan.py --dry`。

### 看板能做什么

打开看板后可以：

- **一键扫描**：填关键词、选时间范围，点「开始扫描」；进度经 SSE 实时推送，可中途取消。
- **浏览命中**：列表列序是「报社 / 日期 / 标题与命中上下文 / **版面位置**」—— 版面位置放在
  **最右列**，一条报道对应右侧一格（版名 + `版内 k/m`，网站提供坐标时另有 `上版 xx%`）。
- **收起筛选栏**：结果区右上角「隐藏筛选」可收起左侧筛选面板，让结果表占满整行；该选择会
  记在浏览器本地，重开看板保持。
- **图形看板**：报社排名、关键词排名、日期 × 报社热力图、最近扫描历史。
- **版面位置图**：按版面画出高亮块，点「查看本版 N 篇报道」与列表联动筛选。
- **导出**：把当前筛选结果导出为 CSV / JSON 到指定文件夹，或改用浏览器下载。

页面为原生 HTML/CSS/JS，**无框架、无构建、无 CDN**，数据全部来自本机文件。

### 跨平台快速入口

| 平台 | 手动扫描 | 本地看板 | 定时运行 |
|---|---|---|---|
| macOS | `python3 daily_scan.py` 或双击 `scripts/scan.command` | 双击 `scripts/dashboard.command` | `scripts/install_launchd.sh` |
| Linux | `python3 daily_scan.py` | `python3 dashboard.py --open` | cron / GitHub Actions |
| Windows | `python daily_scan.py` 或双击 `scripts\scan.cmd` | 双击 `scripts\dashboard.cmd` | `scripts/install_task_scheduler.ps1` |

## CLI 参考

```bash
python3 daily_scan.py                  # 扫今天+昨天，只报「新增」命中（日常用这个）
python3 daily_scan.py --days 7         # 回溯最近 7 天
python3 daily_scan.py --since 2026-09-20 --until 2026-09-23   # 显式日期范围（两端都含）
python3 daily_scan.py --since 2026-09-20                      # 只给起点：扫到今天
python3 daily_scan.py --until 2026-09-20                      # 只给终点：只扫这一天
python3 daily_scan.py --kw 人工智能 --kw 生成式人工智能      # 多个关键词
python3 daily_scan.py --only 湛江日报,南方日报                # 只扫指定报社
python3 daily_scan.py --full           # 忽略增量记录，全量重报
python3 daily_scan.py --workers 8      # 并发数（1~16，默认 8）
python3 daily_scan.py --export-dir ./exports                    # 本次命中另存为 CSV
python3 daily_scan.py --export-dir ./exports --export-format json
python3 daily_scan.py --archive-keep-days 730  # 归档只保留最近 730 天（0 = 不限，默认）
python3 daily_scan.py --dry            # 只探测入口和版面，不抓文章
python3 daily_scan.py --version        # 版本号
```

- `--days` 与 `--since` / `--until` **互斥**（同时给会以退出码 2 报错），区间跨度上限 62 天。
- `--no-state`：不读写 `state/seen.json`、不产 `hits/*`，只扫描并输出；可与 `--json` / `--csv`
  叠加（此时人类可读日志分流到 stderr，保证 stdout 纯净）。
- 数据目录优先级：`--data-dir` > 环境变量 `NEWSPAPER_DATA_DIR` > 自动探测项目根。
- 自定义报社配置 `papers.custom.json` 跟随**项目根**（源码入口 `daily_scan.py` /
  `dashboard.py` 钉死为脚本目录，可用环境变量 `NEWSPAPER_APP_DIR` 覆盖），与
  `--data-dir` 无关。
- 命中会给出「第几版 + 版名」与**版内位置**（`版内 k/m`，有坐标时附 `上版 xx%`）。

更完整的用法（无状态、数据目录、导出、看板、自定义报社）见
[docs/dashboard.md](docs/dashboard.md) 与 [docs/custom-papers.md](docs/custom-papers.md)。

## 运行数据与输出

| 路径 | 内容 |
|---|---|
| `hits/<时间戳>.md` | 每次运行的报告（各家扫描情况 + 新增命中 + 上下文） |
| `hits/index.html` | 历史全部命中汇总，可直接浏览 |
| `hits/archive.json` | 命中归档（结构化，默认只增不删，可用 `--archive-keep-days N` 裁剪） |
| `state/seen.json` | 增量状态（已判定过的文章 URL），保证只报新增 |
| `logs/scan-YYYY-MM-DD.log` | 定时任务运行日志 |
| `exports/newspaper-hits-<时间戳>-<随机标识>.<格式>` | 可选 CSV / JSON 导出（目录可自定义） |

这些目录均**不入库**，由程序按需创建。看板与导出细节见 [docs/dashboard.md](docs/dashboard.md)。

## 项目结构

```
.
├── daily_scan.py                 # 命令行兼容入口（推荐直接用它）
├── dashboard.py                  # 看板兼容入口（无需先安装包）
├── pyproject.toml                # 打包/依赖/测试/ruff 配置
├── Makefile                      # 常用任务（make help）
├── src/newspaper_auto/           # 实现（config / papers / paths / http / parse /
│                                 #   scanner / state / report / exporter / web /
│                                 #   web_tasks / cli / exe + static/ 前端）
├── packaging/                    # Windows exe 打包（PyInstaller spec + 启动器 + 说明）
├── papers.custom.json            # 自定义报社配置（项目根，有意入库）
├── scripts/                      # 启动与定时脚本（见「点击即用启动器」）
├── docs/                         # 部署、调度、架构、看板、自定义报社说明
├── legacy/                       # 已废弃的 Google News 旧方案（仅作参考）
└── tests/                        # 离线回归测试 + HTML fixture
```

运行产物 `hits/`、`state/`、`logs/` 不在此列出（按需自动创建）。

## 测试与自检

```bash
make test          # pytest（全部离线，不访问网络）
make lint          # ruff check（缓存写入临时目录，不在仓库落 .ruff_cache）
make fmt           # ruff 自动格式化 + 修复
make fmt-check     # ruff format --check（与 CI 的格式检查一致）
make check         # lint + 格式检查 + test（提交前自检，等价于 CI）
make papers-check  # 校验 papers.custom.json 并打印合并后的生效报社清单
make unlock        # 删除沙箱/异常中断可能残留的 .git/index.lock
```

前端另有两层**离线**测试（仅开发用，运行时不引入任何依赖），Node 清单在
`tests/frontend/`：

```bash
cd tests/frontend && npm ci          # 仅 jsdom（devDependency）
node --test *.test.mjs               # 纯函数 + 纯 reducer（Node 内置 test runner）
node run-dom.mjs                     # app.js 的 DOM 行为（jsdom）
```

CI 在 Python 3.9 / 3.12 / 3.13 上跑 lint、格式检查和测试，并做前端脚本语法 / 单测与
Shell 启动脚本语法检查；Windows job 另外验证 PowerShell 解析与 UTF-8 BOM。
详见 [docs/production-deployment.md](docs/production-deployment.md)。

## 分发给别人用

- **Windows 单文件 exe（无需 Python）**：打 `v*` tag 后 Release 会挂出
  `NewspaperAuto-<tag>-windows-x64.zip`；也可在 Actions 手动触发 `build-windows`。
- **源码 / Python 用户**：`portable.zip`（含 docs，解压即用）或
  `.whl` / `.tar.gz`（`pip install <whl>`）。

完整分发方式、exe 用法与 CI 细节见 [docs/production-deployment.md](docs/production-deployment.md)。

## 文档索引

| 文档 | 内容 |
|---|---|
| [docs/scheduling.md](docs/scheduling.md) | macOS `launchd`、Windows 任务计划程序、VPS crontab 触发 |
| [docs/production-deployment.md](docs/production-deployment.md) | GitHub Actions、`data` 分支、分发与 CI 供应链 |
| [docs/architecture.md](docs/architecture.md) | 三段式抓取原理、版面几何、六家入口、已知边界 |
| [docs/dashboard.md](docs/dashboard.md) | 本地看板、路由、导出、故障排查 |
| [docs/custom-papers.md](docs/custom-papers.md) | 自定义报社配置与字段说明 |

## 许可

[MIT](LICENSE)
