# 五家报社数字报 · 关键词每日监测

**监测媒体**：光明日报 / 南方日报 / 羊城晚报 / 中国教育报 / 湛江日报
**数据源**：各报社**官网数字报**（报纸电子版）—— 不是 Google/Bing News

一个**本地单机 CLI 工具**：抓取当日全部版面文章，按关键词做**标题 + 正文全文匹配**，
正文里提一句也能命中。运行时**零第三方依赖**（仅 Python 标准库 + 系统 `curl`）。

---

## 一、需要服务器吗？不需要

| 项 | 实现 | 是否需额外服务器 |
|---|---|---|
| 数据存储 | 本地文件 `state/seen.json`、`hits/*` | 否（无需数据库） |
| 抓取 | `curl` 直连五家报社官网 | 否，仅需外网出站 |
| 调度 | macOS `launchd` 本地定时 | 否 |
| 依赖 | Python 3 + 系统 `curl`，**0 个第三方运行时包** | 否 |

唯一的"服务器"角色是**被采集方**——五家报社官网本身，这是数据源，无法避免。
需要注意的边界：定时任务要求**本机在 07:00 处于唤醒/登录状态**（休眠会顺延到唤醒后触发）。
若想 7×24 稳定，可以是"常开本机"，也可以是任意一台便宜 VPS 跑 cron，
但**都不需要安装任何服务端软件**。只有当将来要做多用户、Web 看板或消息推送时，
才需要引入服务端。

---

## 二、项目结构

```
.
├── daily_scan.py                 # 命令行兼容入口（推荐直接用它）
├── pyproject.toml                # 打包/依赖/测试/ruff 配置
├── Makefile                      # 常用任务（make help）
├── src/newspaper_auto/           # 实现
│   ├── config.py                 #   报社入口、清洗规则、默认值
│   ├── paths.py                  #   数据目录解析
│   ├── http.py                   #   curl 抓取（含 HTTP/1.1 强制与重试）
│   ├── parse.py                  #   HTML 解析与关键词匹配（纯函数）
│   ├── scanner.py                #   扫描编排（入口→版面→文章）
│   ├── state.py                  #   增量状态读写与「新增」判定
│   ├── report.py                 #   Markdown / HTML / JSON / CSV 渲染
│   └── cli.py                    #   参数解析与主流程
├── tests/                        # 65 个离线单元测试 + HTML fixture
├── scripts/                      # 定时任务
│   ├── run_scan.sh               #   启动脚本（无状态运行，写日志）
│   ├── com.quark.newspaperauto.plist.template  # launchd 模板
│   └── install_launchd.sh        #   幂等安装/状态/立即运行/卸载
├── docs/                         # 交付说明
├── legacy/                       # 已废弃的 Google News 旧方案（仅作参考）
├── hits/                         # 运行产出：报告与历史命中（有意入库）
└── state/seen.json               # 增量状态（有意入库）
```

---

## 三、安装

运行时只需要 **Python 3.9+** 和 **curl**（macOS 自带）。不装任何第三方包也能跑：

```bash
cd /Users/quark/Codex/Project/NewsPaperAuto
python3 daily_scan.py --dry        # 直接可用
```

如需开发（跑测试 / lint）：

```bash
make install        # 等价于 uv venv && uv pip install -e ".[dev]"
make check          # ruff + pytest
```

---

## 四、用法

```bash
python3 daily_scan.py                  # 扫今天+昨天，只报「新增」命中（日常用这个）
python3 daily_scan.py --days 7         # 回溯最近 7 天
python3 daily_scan.py --kw 广东海洋大学 --kw 海大   # 多个关键词
python3 daily_scan.py --only 湛江日报,南方日报       # 只扫指定报社
python3 daily_scan.py --full           # 忽略增量记录，全量重报
python3 daily_scan.py --workers 8      # 并发数（默认 8）
python3 daily_scan.py --dry            # 只探测入口和版面，不抓文章
python3 daily_scan.py --version        # 版本号
```

### 无状态运行（不产生历史文件）

指定 `--no-state`：不读写 `state/seen.json`，也不产生任何 `hits/*.md`、
`hits/index.html`、`hits/archive.json`，仅扫描并输出命中。该模式优先级高于 `--full`，
可与 `--json` / `--csv` 叠加（此时人类可读日志自动分流到 stderr，保证 stdout 纯净）。

```bash
# 纯无状态运行
python3 daily_scan.py --days 1 --no-state

# JSON 数组到 stdout，可直接管道处理
python3 daily_scan.py --days 1 --no-state --json | jq .

# CSV 到 stdout
python3 daily_scan.py --days 1 --no-state --csv > today_hits.csv
```

### 数据目录

默认自动探测项目根（含 `.git` / `pyproject.toml` 的最近上级目录）。
也可显式指定，优先级：`--data-dir` > 环境变量 `NEWSPAPER_DATA_DIR` > 自动探测。

```bash
python3 daily_scan.py --data-dir /tmp/scan-out --days 1 --no-state
NEWSPAPER_DATA_DIR=/tmp/scan-out python3 daily_scan.py --days 1
```

### 定时任务（macOS launchd）

每天自动执行无状态扫描，日志追加到 `logs/scan-YYYY-MM-DD.log`。
plist 由模板生成，**路径与运行时间都自动替换，无需手工编辑**：

```bash
./scripts/install_launchd.sh install                    # 默认每天 07:00
./scripts/install_launchd.sh install 06:30              # 自定义为每天 06:30
./scripts/install_launchd.sh install 07:00 12:30 19:00  # 一天多次
NEWSPAPER_TIMES="07:00,12:30" ./scripts/install_launchd.sh install

./scripts/install_launchd.sh status      # 查看注册状态与最近日志
./scripts/install_launchd.sh run-now     # 立即触发一次
./scripts/install_launchd.sh print 06:30 # 只打印将要安装的 plist（含时间）
./scripts/install_launchd.sh uninstall   # 卸载
```

- 时间格式 `HH:MM`（24 小时制），可给多个，**每个时间点每天各跑一次**；
  非法格式（如 `25:00`、`7am`）会直接报错退出。
- 修改时间后**重新执行一次 `install`** 即生效（幂等，会自动卸载旧的）。
- 建议 07:00 之后：报纸一般凌晨上线，过早跑可能版面不全。
- 自定义回溯天数：在模板里给 `EnvironmentVariables` 加
  `NEWSPAPER_DAYS`（默认 1）。

自定义关键词：编辑 `scripts/com.quark.newspaperauto.plist.template`，取消
`NEWSPAPER_KW` 注释后重新 `install`；或直接用启动脚本传参：

```bash
./scripts/run_scan.sh 广东海洋大学 广海 海大
./scripts/run_scan.sh --kw 广东海洋大学 --days 3 --only 湛江日报
NEWSPAPER_KW="广东海洋大学,广海" ./scripts/run_scan.sh
```

### 输出

| 路径 | 内容 |
|---|---|
| `hits/<时间戳>.md` | 每次运行的报告（各家扫描情况 + 新增命中 + 上下文） |
| `hits/index.html` | 历史全部命中汇总，可直接浏览 |
| `hits/archive.json` | 命中归档（结构化，便于二次加工） |
| `state/seen.json` | 增量状态（已判定过的文章 URL），保证只报新增 |
| `logs/scan-YYYY-MM-DD.log` | 定时任务运行日志 |

---

## 五、方案原理

五家报社的数字报全部基于**方正系电子报系统**，结构高度统一，三段式：

```
第 1 版页  node_01.htm(l)   →  含当日全部版面链接（node_*）
单个版面页 node_*.htm(l)    →  含本版全部文章链接（content_*）
单篇文章页 content_*.htm(l) →  含标题 + 正文（正文在 <founder-content> 标签内）
```

流程：**第 1 版 → 全部版面 → 全部文章 → 标题 + 正文全文匹配关键词**。

### 五家入口（第 1 版地址模板）

| 报社 | 第 1 版 URL | 日期目录格式 |
|---|---|---|
| 光明日报 | `https://epaper.gmw.cn/gmrb/html/layout/{YYYYMM/DD}/node_01.html` | `202609/22` |
| 南方日报 | `https://epaper.nfnews.com/nfdaily/html/{YYYYMM/DD}/node_A01.html` | `202609/22` |
| 羊城晚报 | `http://ep.ycwb.com/epaper/ycwb/html/{YYYY-MM/DD}/node_1.htm` | `2026-09/22` |
| 中国教育报 | `https://paper.jyb.cn/zgjyb/html/{YYYY-MM/DD}/node_1.htm` | `2026-09/22` |
| 湛江日报 | `http://paper.gdzjdaily.com.cn/html/{YYYY-MM/DD}/node_1.htm` | `2026-09/22` |

### 为什么不用 Google News

Google News 是**标题级检索**，正文提及全漏。2026-09-22 实测的 4 条真报道，
**标题里一个都没有"广东海洋大学"**——校名全在正文里（如"来自广东海洋大学、
湛江湾实验室的专家提出意见建议"）。且 Google 对湛江日报的收录早已停滞，
而湛江日报恰是命中最多的报纸。官网数字报没有这些问题：报纸上线即可抓、
链接就是原文、全文匹配不漏正文。

（更早的 Google News 方案保留在 `legacy/`，仅作历史参考。）

---

## 六、开发

```bash
make help      # 查看所有任务
make test      # pytest
make lint      # ruff check
make fmt       # ruff 自动格式化 + 修复
make check     # lint + test（提交前自检）
```

测试全部**离线**：通过注入 fake fetcher 驱动扫描，HTML 样例在 `tests/fixtures/`。
CI 在 Python 3.9 / 3.12 / 3.13 上跑 lint、格式检查、测试与 shell 语法检查。

---

## 七、已知边界

- **数字报 ≠ 即时新闻**。报社官网的即时新闻频道（光明网、南方网等）更新更快、条数更多，
  但不等于报纸版面。如需纳入，可在此基础上追加一路采集。
- **当天报纸上线时间不固定**（一般凌晨到上午）。默认扫「今天 + 昨天」，
  当天没上线也不会漏，第二天会自动补上。
- **图片版正文抓不到**。若某版整版图片排版（少见），文字提取会失败；五家实测均有文字版。
- **只匹配字面关键词**。默认精确匹配「广东海洋大学」；覆盖简称用 `--kw` 多传几个。
- ⚠️ **光明日报必须走 HTTP/1.1**：`epaper.gmw.cn` 的服务器在 HTTP/2 下会直接断连
  （curl 报 `52 Empty reply from server`），而 curl 默认会经 ALPN 协商到 h2，
  所以 `http.py` 显式加了 `--http1.1`。**若把抓取逻辑移植到 requests / aiohttp /
  Node fetch，务必先验证该站 h2 是否可用**，否则会静默漏掉光明日报（其余四家不受影响）。
  - 诊断手法：同一 URL 分别用 `curl --http1.1`、`curl --http2`、
    `python3 -c "import urllib.request;..."` 各抓一次对比。urllib 默认走 h1.1，
    若它成功而 curl 默认失败，即可锁定是 h2 问题。
- **对报社站点是轻量访问**：每天每站约 100~200 个请求、并发 8，属正常阅读量级。
  默认并发 8；若某家不稳可降到 4~5（`--workers 5`）。

---

## 八、许可

[MIT](LICENSE)
