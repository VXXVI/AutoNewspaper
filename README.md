# 数字报 · 关键词每日监测

**内置媒体**：光明日报 / 南方日报 / 羊城晚报 / 中国教育报 / 湛江日报 / 人民日报（可自定义扩展）
**数据源**：各报社**官网数字报**（报纸电子版）—— 不是 Google/Bing News

一个**本地单机监测工具**：既可用 CLI，也可在浏览器看板中一键抓取。项目会扫描当日
全部版面文章，按关键词做**标题 + 正文全文匹配**，正文里提一句也能命中。运行时
**零第三方依赖**（仅 Python 标准库 + 系统 `curl`）。

---

## 一、需要服务器吗？不需要

| 项 | 实现 | 是否需额外服务器 |
|---|---|---|
| 数据存储 | 本地文件 `state/seen.json`、`hits/*` | 否（无需数据库） |
| 抓取 | `curl` 直连六家报社官网 | 否，仅需外网出站 |
| 运行 | **GitHub Actions**（免费 runner，生产路径）或本机 Python | 否 |
| 调度 | 生产 = **GitHub Actions + 外部触发**；本机 = macOS `launchd` / Windows 任务计划程序 | 否 |
| 依赖 | Python 3 + 系统 `curl`，**0 个第三方运行时包** | 否 |

**调度说明**：生产路径是 **GitHub Actions + 外部 cron 触发**（见下方「调度方案对照表」）。
macOS `launchd` 与 Windows 任务计划程序是**本机替代路径**，与 Actions **可以并存**——
本机脚本一律以 `--no-state` 无状态运行，只把结果打到控制台并写 `logs/`，
**不读写 `state/seen.json`、不产生 `hits/*`**，因此不会与 Actions 互相覆盖。
旧的「两边共写 `state/seen.json`，必须二选一」说法已被证伪，不再适用
（两种路径的产出物差异见「定时任务（macOS launchd）」一节）。

**是否需要打包 exe：不需要。** 两条理由：

1. 项目**零第三方运行时依赖**（仅 Python 标准库 + 系统 `curl`），exe 想解决的
   「用户装不上依赖」问题在本项目根本不存在；
2. 真正的问题是**「谁每天来触发」**——exe 自己不会定时启动，打包并不能解决调度；
   反而会引入体积、杀软误报、升级需重新分发等新麻烦。

唯一的"服务器"角色是**被采集方**——六家报社官网本身，这是数据源，无法避免。
需要注意的边界：**本机**定时任务要求本机在触发时刻处于唤醒/登录状态
（休眠会顺延到唤醒后触发）；Actions 路径则不受本机开关机影响。
若想 7×24 稳定，可以是"常开本机"，也可以是任意一台便宜 VPS 跑 cron，
但**都不需要安装数据库或常驻服务框架**。仓库内置的 Web 看板只面向单用户、本机访问；
只有要做公网多用户、登录权限或消息推送时，才需要引入正式服务端架构。

### 调度方案对照表

| 方案 | 成本 | 需仓库转公开？ | 本机需常开？ | 适合场景 |
|---|---|---|---|---|
| GitHub Actions 原生 `schedule` | 私有库消耗 Actions 额度 | 否 | 否 | 部署最简，允许延迟 |
| 外部 cron 打 `repository_dispatch` | 取决于 cron 服务 | 否 | 否（只需 cron 侧在线） | **当前生产方案**，适合更看重准点 |
| macOS `launchd` 本机 | 免费 | 否 | 需唤醒/登录 | 手边是 Mac、想当场看结果；无状态，可与 Actions 并存 |
| Windows 任务计划程序 | 免费 | 否 | 需开机/登录 | 手边是 Windows；无状态，可与 Actions 并存 |

**日级精度下四种都可接受**——默认扫「今天 + 昨天」，漏掉一天第二天会自动补回来。

---

## 二、项目结构

```
.
├── daily_scan.py                 # 命令行兼容入口（推荐直接用它）
├── dashboard.py                  # 看板兼容入口（无需先安装包）
├── pyproject.toml                # 打包/依赖/测试/ruff 配置
├── Makefile                      # 常用任务（make help）
├── src/newspaper_auto/           # 实现
│   ├── config.py                 #   报社入口、清洗规则、默认值（不含 I/O）
│   ├── papers.py                 #   自定义报社：papers.custom.json 读写/校验/合并
│   ├── paths.py                  #   数据目录解析
│   ├── http.py                   #   curl 抓取（含 HTTP/1.1 强制与重试）
│   ├── parse.py                  #   HTML 解析与关键词匹配（纯函数）
│   ├── scanner.py                #   扫描编排（入口→版面→文章）
│   ├── state.py                  #   增量状态读写与「新增」判定
│   ├── report.py                 #   Markdown / HTML / JSON / CSV 渲染
│   ├── web.py                    #   本地看板 + 一键触发扫描（stdlib http.server）
│   ├── static/                   #   看板前端（原生 HTML/CSS/JS，无构建）
│   └── cli.py                    #   参数解析与主流程
├── papers.custom.json            # 自定义报社配置（项目根，有意入库，见「自定义报社」）
├── .github/workflows/            # CI 与每日扫描 workflow（生产调度入口）
├── tests/                        # 248 个离线测试 + HTML fixture
├── scripts/                      # 定时任务
│   ├── run_scan.sh               #   macOS/Linux 启动脚本（无状态运行，写日志）
│   ├── com.quark.newspaperauto.plist.template  # launchd 模板
│   ├── install_launchd.sh        #   macOS 幂等安装/状态/立即运行/卸载
│   ├── run_scan.ps1              #   Windows 启动脚本（对标 run_scan.sh）
│   └── install_task_scheduler.ps1 #  Windows 任务计划程序幂等安装/状态/立即运行/卸载
├── docs/                         # 交付说明
├── legacy/                       # 已废弃的 Google News 旧方案（仅作参考）
├── hits/                         # 运行产出：报告与历史命中（有意入库）
└── state/seen.json               # 增量状态（有意入库）
```

---

## 三、安装

项目支持 **macOS / Linux / Windows 10 1803+**。运行时只需要
**Python 3.9+** 和 **curl**（macOS 与新版 Windows 自带），不装任何第三方包也能跑：

```bash
cd /Users/quark/Codex/Project/NewsPaperAuto
python3 daily_scan.py --dry        # 直接可用
```

### 跨平台快速入口

| 平台 | 手动扫描 | 本地看板 | 定时运行 |
|---|---|---|---|
| macOS | `python3 daily_scan.py` 或双击 `scripts/scan.command` | 双击 `scripts/dashboard.command` | `scripts/install_launchd.sh` |
| Linux | `python3 daily_scan.py` | `python3 dashboard.py --open` | cron / GitHub Actions |
| Windows | `python daily_scan.py` 或双击 `scripts\scan.cmd` | 双击 `scripts\dashboard.cmd` | `scripts\install_task_scheduler.ps1` |

希望“打开页面并立即抓取”时使用一条命令（读取页面上次保存的关键词、范围和报社选择）：

```bash
make start                                  # macOS / Linux
python3 dashboard.py --open --scan          # 不使用 make 时
python dashboard.py --open --scan           # Windows
```

Windows 上把文档示例中的 `python3` 换成 `python`即可。启动脚本会按
`NEWSPAPER_PYTHON` → `.venv` → PATH 中的 Python 顺序自动选择解释器。

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
python3 daily_scan.py --since 2026-09-20 --until 2026-09-23   # 显式日期范围（两端都含）
python3 daily_scan.py --since 2026-09-20                      # 只给起点：扫到「今天」为止
python3 daily_scan.py --until 2026-09-20                      # 只给终点：只扫这一天
python3 daily_scan.py --kw 广东海洋大学 --kw 海大   # 多个关键词
python3 daily_scan.py --only 湛江日报,南方日报       # 只扫指定报社
python3 daily_scan.py --full           # 忽略增量记录，全量重报
python3 daily_scan.py --workers 8      # 并发数（默认 8）
python3 daily_scan.py --dry            # 只探测入口和版面，不抓文章
python3 daily_scan.py --version        # 版本号
```

> `--days` 与 `--since` / `--until` **互斥**（同时给会以退出码 2 报错）。
> 显式区间跨度上限 62 天，超过会被拒绝，避免误扫把六家报社站点打爆。

命中结果除「第几版 + 版名」外，还会给出**版内位置**（形如
`[湛江日报 · 第1版：要闻 · 版内 2/7 · 上版 38%]`）：`index/total` 是该版按纵坐标排序的
第几条，`上版 xx%` 是文章顶边在版面图上的纵向占比。几何定位采用三级互斥兜底：
1. **`<area>` 图像映射（`basis="area"`）**：湛江日报等站点直接从 HTML `<area>` 提取多边形或矩形包围盒坐标；
2. **内嵌 JS 映射（`basis="mapping"`）**：南方日报等新版数字报从内嵌 `window.layoutData.mappingOriginal` 解析多边形顶点并按 `(top, left, 出现顺序)` 排序，同样输出真实纵向百分比 `top`/`bottom`（形如 `[南方日报 · QD01版：阳春视窗 · 版内 2/4 · 上版 8%]`）；
3. **顺序兜底（`basis="order"`）**：无任何几何数据的纯链接版面按版面链接出现顺序给出 `版内 k/m`，**不带纵向百分比**（形如 `[南方日报 · A01版：要闻 · 版内 3/9]`）。

旧归档缺少该字段也能正常渲染。

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

本机定时通过 `scripts/run_scan.sh` 以 `--no-state` **无状态**运行：
不读写 `state/seen.json`，也不产生 `hits/*.md` / `hits/index.html` / `hits/archive.json`，
只把命中打到控制台并追加日志到 `logs/scan-YYYY-MM-DD.log`。

因此本机路径与 GitHub Actions **可以同时启用、互不覆盖**，两者产出物不同：

| 路径 | 产出物 | 作用 |
|---|---|---|
| 本机 launchd / 任务计划程序 | 控制台 + `logs/scan-*.log`（无状态） | 当场看一眼今天有没有命中 |
| GitHub Actions | `hits/*`、`hits/archive.json`、`state/seen.json`（并提交回仓库） | 维护报告归档与增量历史 |

换句话说：本机跑是「即时可见」，Actions 跑是「归档 + 增量」。若你只看 Actions 结果，
不装本机任务也完全没问题；不再需要本机任务时，`./scripts/install_launchd.sh uninstall` 移除即可。
plist 由模板生成，**路径与运行时间都自动替换，无需手工编辑**：

```bash
./scripts/install_launchd.sh install 10:00              # 每天 10:00（当前设置；不传时间默认也是 10:00）
./scripts/install_launchd.sh install 06:30              # 自定义为每天 06:30
./scripts/install_launchd.sh install 10:00 12:30 19:00  # 一天多次
NEWSPAPER_TIMES="10:00,12:30" ./scripts/install_launchd.sh install

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

### 定时任务（Windows 任务计划程序）

Windows 上有一对标脚本，行为与 macOS 侧一致（同样无状态、同样写 `logs\scan-YYYY-MM-DD.log`）：

- `scripts\run_scan.ps1`：启动脚本（对标 `run_scan.sh`，参数透传、环境变量、退出码一致）
- `scripts\install_task_scheduler.ps1`：注册 / 卸载 / 查看 / 立即运行（任务名 `NewsPaperAuto`）

**1. 准备 Python 与 curl**

- Python：从 [python.org](https://www.python.org/downloads/windows/) 安装，
  安装时**务必勾选 `Add python.exe to PATH`**。验证：`python --version`。
- curl：**Windows 10 1803+ 自带** `curl.exe`（位于 `C:\Windows\System32`）。验证：`curl --version`。
  无需另装，脚本也不会绑定 curl 的绝对路径。

> 没建 `.venv` 也没关系：脚本按 `NEWSPAPER_PYTHON` → `.venv\Scripts\python.exe` → PATH
> 中的 `python` 顺序探测，未建虚拟环境时自动用系统 `python`（前提是它在 PATH 里）。

**2. 手动先跑一次**（确认能出结果）

```bat
cd /d <项目目录>
python daily_scan.py --dry
```

**3. 注册每日定时任务**

```bat
cd /d <项目目录>
powershell -ExecutionPolicy Bypass -File scripts\install_task_scheduler.ps1 install 10:00
```

- 默认 `10:00`，可给多个时间点：`... install 10:00 12:30 19:00`（每天各跑一次）；
  也可用环境变量 `NEWSPAPER_TIMES`（逗号或分号分隔）。
- **幂等**：重复 `install` 会先注销同名任务再注册，不会报错，也不会重复堆积。
- **⚠ 需要管理员权限**：`Register-ScheduledTask` 在**非提权的 PowerShell** 下会报
  `Access is denied`。请用「**以管理员身份运行**」重开 PowerShell 再执行 `install`；
  `status` / `run-now` / `uninstall` 不需要提权。
- 其它子命令：`status`（注册状态 + 最近日志）、`run-now`（立即触发）、`uninstall`（注销）。
- **⚠ 时区**：任务计划程序使用**机器本地时区**，请用 `Get-TimeZone` 确认；
  与北京时间不一致时，请按本地时区填写上面的时间点。

**4. 不想用脚本时的兜底（等价 `schtasks` 一行）**

```bat
schtasks /Create /TN NewsPaperAuto /SC DAILY /ST 10:00 /F ^
  /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File \"<项目目录>\scripts\run_scan.ps1\""
```

（把 `<项目目录>` 换成实际路径；需要多个时间点就分别用不同 `/TN` 再建几条。）
注册后可 `schtasks /Query /TN NewsPaperAuto /V` 查看，`schtasks /Delete /TN NewsPaperAuto /F` 删除。

**5. 常见坑**

- **中文乱码**：脚本已设 `PYTHONIOENCODING=utf-8`，并在调用 Python 前把控制台输出编码
  切到 UTF-8；若手动运行时仍乱码，先执行 `chcp 65001` 再运行。
- **`curl` 找不到**：确认系统是 Windows 10 1803 以上；更老的系统请自行安装 curl 并加入 PATH。
- **任务没触发**：任务默认「仅在当前用户登录时运行」，且机器需处于开机/唤醒状态。
- **`.ps1` 中文乱码**：两个脚本均以 **UTF-8 with BOM** 保存，Windows PowerShell 5.1
  可正确识别；若被编辑器另存为无 BOM，请改回 UTF-8 with BOM。
- **行尾**：仓库用 `.gitattributes` 钉死了行尾 —— `*.cmd` / `*.bat` / `*.ps1` 签出为 **CRLF**，
  `*.sh` / `*.command` 签出为 **LF**。**不要手工把 `.cmd` 改成 LF**（`goto` / 多行 `for` 块可能失败），
  也不要让编辑器把 `.sh` 存成 CRLF（bash 会报 `\r: command not found`）。
  自查：`git ls-files --eol scripts/`。

### 外部定时（VPS crontab）

不想让本机常开时，可用任意一台 VPS 的 crontab 触发 GitHub Actions。
这是本项目**生产环境当前采用**的方式。GitHub 官方支持私有仓库的
`schedule`，但定时任务可能延迟，高负载时甚至丢弃；本项目曾出现原生定时
长时间无记录，因此保留 `repository_dispatch` 作为更准点的生产入口。
两者都配置时可能同日执行两次；建议选一个作为主触发器。

**部署形态**

crontab 只负责**调用一个独立脚本**，PAT 存在**单独的文件**里而不是 crontab 中 ——
这样 `crontab -l` 不会泄露凭据，脚本自身还能带日志与错误分类：

```
~/.newspaperauto/
├── dispatch.sh    # 触发器脚本（本仓库 scripts/vps_dispatch.sh，权限 700）
├── pat            # PAT 单行纯文本（权限 600）—— 唯一需要你自己填的东西
└── dispatch.log   # 每次触发的结果（成功 http=204 / 失败带错误码与中文提示）
```

**1. 创建 PAT（细粒度，最小权限）**

GitHub → Settings → Developer settings → **Fine-grained tokens** → 新建，仅授予：

- Repository access：只勾 `VXXVI/AutoNewspaper`
- Permissions：**仅需 `Contents: Read and write`**（触发 dispatch 即用此权限）
- 有效期按需设置（到期换一个，只需覆盖 `pat` 文件，不用动 crontab）

> 🔒 PAT **只存在 VPS 本机**，绝不写入仓库、日志或提交信息。

**2. 装脚本与 crontab**

```bash
# 建目录（700：同机其他用户读不到）
ssh vps-ts 'mkdir -p ~/.newspaperauto && chmod 700 ~/.newspaperauto'

# 部署脚本（仓库里的就是部署版本，天然保持一致）
scp scripts/vps_dispatch.sh vps-ts:.newspaperauto/dispatch.sh
ssh vps-ts 'chmod 700 ~/.newspaperauto/dispatch.sh'

# 追加 crontab（先备份；已存在则跳过，避免重复堆积）
ssh vps-ts 'TMP=$(mktemp); crontab -l > "$TMP" 2>/dev/null || true; \
  cp "$TMP" ~/crontab.bak-$(date +%F); \
  grep -q "newspaperauto/dispatch.sh" "$TMP" || \
  printf "# AutoNewspaper 每日扫描触发\n0 2 * * * %s/.newspaperauto/dispatch.sh\n" "$HOME" >> "$TMP"; \
  crontab "$TMP"; rm -f "$TMP"; crontab -l'
```

上面 `0 2 * * *` 是按 **VPS 为 UTC** 写的（UTC 02:00 = 北京当日 10:00）。
若你的 VPS 已是 `Asia/Shanghai`，改成 `0 10 * * *`；判断方法见下方「时区说明」。

**3. 注入 PAT**（唯一需要凭据的一步）

```bash
# 在你自己机器上执行。PAT 从键盘读入：不回显、不进 shell 历史、不落命令行参数
ssh -t vps-ts 'umask 077; read -rsp "粘贴 PAT 后回车: " P; echo; \
  printf "%s" "$P" > ~/.newspaperauto/pat; unset P; ls -l ~/.newspaperauto/pat'
```

**4. 立即验证一次**（不必等到明天早上）

```bash
ssh vps-ts '~/.newspaperauto/dispatch.sh; echo "exit=$?"; tail -n 2 ~/.newspaperauto/dispatch.log'
```

日志里出现 `OK http=204 已受理` 即成功。再确认 Actions 冒出新运行：

```bash
gh run list -R VXXVI/AutoNewspaper --workflow=scan.yml --limit 3
```

**时区说明**：crontab 用的是 **VPS 机器的本地时区**，而 workflow 内部固定
`TZ=Asia/Shanghai`（10:00 北京 = 当日 02:00 UTC）。因此：

- VPS 时区为 `Asia/Shanghai`：写 `0 10 * * *`；
- VPS 时区为 `UTC`（多数云主机默认）：写 `0 2 * * *`（= 当日北京 10:00）；
- 不确定时先 `date`（或 `timedatectl`）确认，也可
  `sudo timedatectl set-timezone Asia/Shanghai` 统一为北京时间。

**5. 日志与失败排查**

- 日志在 `~/.newspaperauto/dispatch.log`。**不写 `/var/log`**：多数发行版该目录属
  `root:syslog`，普通用户无写权限。
- `204 No Content` 才是成功——GitHub 的 dispatch 成功只表示「事件已受理」，
  不代表扫描已完成；随后到 **Actions** 页确认 `daily-scan` 出现运行记录。
- `401` PAT 无效或过期 · `403` 权限不足或未勾选该仓库（需 `Contents: Read and write`）
  · `404` 仓库名大小写写错 · `000` 网络不可达。脚本会把 HTTP 码与 GitHub 返回体
  一并写进日志，并附一句中文提示。
- `concurrency: daily-scan` 会挡住重复并发：重叠触发时后者**排队**而非并跑。

**6. 回滚**

```bash
crontab -e            # 删掉调用 dispatch.sh 的那一行
rm -rf ~/.newspaperauto
```

> 本机 `launchd` / Windows 任务计划程序与 Actions **可并存**：本机脚本以 `--no-state` 运行，
> 不读写 `state/seen.json`、不产 `hits/*`，不会与 Actions 互相覆盖。
> 不再需要本机路径时，`./scripts/install_launchd.sh uninstall`（Windows 用
> `install_task_scheduler.ps1 uninstall`）移除即可。

### 输出

| 路径 | 内容 |
|---|---|
| `hits/<时间戳>.md` | 每次运行的报告（各家扫描情况 + 新增命中 + 上下文） |
| `hits/index.html` | 历史全部命中汇总，可直接浏览 |
| `hits/archive.json` | 命中归档（结构化，便于二次加工） |
| `state/seen.json` | 增量状态（已判定过的文章 URL），保证只报新增 |
| `logs/scan-YYYY-MM-DD.log` | 定时任务运行日志 |

### 本地看板（可配置 + 可触发扫描的 Web 界面）

用标准库起一个看板，零第三方依赖、无构建、无 CDN：

```bash
python3 dashboard.py               # http://127.0.0.1:8788（默认端口）
python3 dashboard.py --port 8799 --open
make web                           # 只打开看板
make start                         # 打开看板 + 按上次页面设置自动抓取
```

> 端口可用 `--port` 或环境变量 `NEWSPAPER_PORT` 覆盖（`scripts/dashboard.command` /
> `scripts/dashboard.cmd` 会读取后者）；默认端口被占用时自动回退到系统分配的空闲端口
> （等价 `--port 0`），无需手动改端口。

- **数据来源**：`hits/archive.json`（历史命中）与 `state/seen.json`（已判定文章数）。
- **版面位置图**：扫描器会把网站提供的 `<area>` 或 `mappingOriginal` 坐标统一为 0~1
  的二维布局；看板按报社 / 日期 / 版面分组绘制，红色标出当前筛选命中。网站只给文章
  顺序、不提供二维坐标时，只显示 `版内 k/m`，不会伪造版面图。旧归档会在下一次扫描
  同一文章时自动补齐布局元数据。
- **功能**：概览统计卡片、按日期分布柱状图；按报社 / 命中关键词 / **日期区间**
  （含「全部 / 今天 / 近 7 天 / 近 30 天」**快捷区间**）筛选；标题与上下文搜索、排序；
  命中列表展示**版面**（报纸中的相对位置）与**版内位置**（`版内 k/m`，无 `<area>` 的报社
  按版面页链接顺序给出；`上版 xx%` 仅在网站提供 `<area>` / mapping 坐标时出现）并可点击标题跳原文；
  支持**按当前筛选导出 CSV**。
- **开始一次监测（会写数据）**：页面顶部保留最常用的**关键词 / 扫描范围 / 开始扫描**；
  并发数与报社选择收进「高级扫描设置」，日常使用不再被技术字段干扰。扫描由看板进程
  **直接触发**，页面显示真实的「报社 × 日期」总体进度，日志按游标增量加载，完成后自动刷新命中列表。
  **扫描范围**为二选一：
  - `按天数`：1 ~ 62 天（默认 2 = 今天 + 昨天）；
  - `按日期范围`：两个 `<input type="date">`（默认 `until` = 今天、`since` = 今天减 1 天），
    两端都含、跨度 ≤ 62 天，与后端 `--since/--until` 完全一致。
  **只扫选中报社**：`GET /api/papers` 的 `effective` 列表渲染为多选 chips，**默认全选**；
  取消勾选即只扫其中几家（全选时也显式发送 `only`，不依赖「空 = 全部」的隐含语义）。
  选中数为 0 时「开始扫描」被禁用。该选择持久化在 `state/web_config.json` 的 `only`
  字段（**逗号分隔字符串**；空串 = 全选）。
  抓取结果（`hits/`、`state/`）写回**看板正在读取的那个数据目录**；上次用的配置
  （含 `mode` / `since` / `until` / `only`）持久化在 `state/web_config.json`。
  ⓘ 看板已**不再是纯只读**——扫描会写 `state/` 与 `hits/`；**扫描进行中不可中断**，
  如需强制停止请关闭看板进程（Ctrl+C / 关窗口）。
  当命中为 0（空归档）时，命中列表区会给出**空状态引导**：显示当前数据目录并提示点
  「开始扫描」；若是**全新环境**（该目录下还没有 `state/web_config.json`），还会提示
  默认关键词与默认扫「今天 + 昨天」。
- **筛选卡片里的两个日期框**（`#from` / `#to`）**只筛选已抓到的命中列表，不影响扫描范围**，
  这一点最容易误解，故界面上已标注「仅筛选已抓到列表」。
- **报社管理**：页面底部「报社与抓取规则」默认折叠，列出内置 / 自定义 / 覆盖三类来源；
  展开后可新增、编辑、试抓、删除，扫描期间暂时锁定修改，
  详见下方「自定义报社」。存在自定义 / 覆盖报社时，卡片会显示「复制提交命令」按钮，
  一键复制 `git add papers.custom.json && git commit -m "chore(papers): 更新自定义报社" && git push`
  （剪贴板不可用时回退为选中命令文本、提示手动复制）。
- **新增路由**（`src/newspaper_auto/web.py`）：
  - `POST /api/scan`：启动扫描。`202` 已启动 / `409` 已有任务在跑 / `400` 参数非法 /
    `403` 来源校验失败 / `413` body 过大；同一时刻最多一个任务。可选 `only` 字段
    （逗号分隔字符串或字符串数组）只扫指定报社。
  - `GET /api/scan`：读当前/最近一次任务状态，无任务时返回 `{"status":"idle"}`；可传
    `?after=<日志游标>` 只取新增日志，响应含任务 ID、总体进度和下一游标。
  - `POST /api/config` / `GET /api/config`：保存 / 读取配置；文件不存在时回落到
    `config.DEFAULT_KEYWORDS` / `DEFAULT_DAYS` / `DEFAULT_WORKERS`，并补 `mode="days"` /
    `since=null` / `until=null` / `only=""`。`only` 为可选字段（逗号分隔字符串，也接受字符串
    数组），缺省 / 空串 = 全选。
  - `GET /api/papers`：返回 `{"builtin":[名称…], "custom":[完整对象…], "effective":[名称…]}`。
  - `POST /api/papers`：新增或**同名覆盖**一条自定义报社，返回新的列表；非法 → `400`。
  - `POST /api/papers/delete`：body `{"name":"…"}`；自定义/覆盖可删，**内置且未被覆盖 → `400`**，
    不存在 → `404`，成功 → `200` + 新列表。
  - `POST /api/papers/test`：body 为一条 paper（或只给内置/已有报社的 `{"name":"…"}`）→
    最多读取入口与一个样例版面，返回入口 URL、HTTP 状态、版面数、样例文章数和说明；
    不抓正文、不写文件。
- **安全**：只允许监听 `127.0.0.1` / `localhost`，显式拒绝 `0.0.0.0` 等公网监听；
  所有请求校验 `Host` 必须是环回主机（防 DNS
  rebinding），带 `Origin` 时必须为环回 origin（防跨站触发抓取），`POST` 必须声明
  `application/json` 且 body ≤ 16 KiB。关键词只作为 argv 元素传给 `cli.main`，
  **绝不拼 shell 字符串**；不返回任何 CORS 头。页面上的数据与扫描日志全部用
  `textContent` 渲染，不会执行内容里的 HTML/脚本；响应还带 CSP、禁止 iframe、禁止 MIME
  猜测与来源泄露等浏览器安全头。VPS 上需要远程查看时，请使用 SSH 隧道：先在 VPS 启动
  看板，再从自己的电脑执行 `ssh -L 8788:127.0.0.1:8788 user@vps`，最后访问
  `http://127.0.0.1:8788`，不要把看板端口直接暴露到公网。
- **端口**：默认 `8788`；被占用时自动回退到系统分配的空闲端口（等价 `--port 0`），
  也可用 `--port` / `NEWSPAPER_PORT` 指定。

### 自定义报社

除内置六家外，可**在看板「报社与抓取规则」里新增自定义报社**，也可直接手编配置文件。
如果手编后的 JSON 损坏，界面的保存/删除会返回冲突提示并保留原文件，不会用空配置覆盖。

**文件位置**：`<项目根>/papers.custom.json`（**进 git**）。⚠️ 它跟随**项目根**
（`paths.find_project_root()`），**不跟 `--data-dir` 走**——`--data-dir` 只是数据落点
（`state/` / `hits/`），自定义报社属于**配置层**。所以用 `--data-dir /tmp/x` 跑看板时，
新增的报社仍写回项目根。

```json
{
  "version": 1,
  "papers": [
    {
      "name": "某某日报",
      "style": "ymd",
      "tpl": "https://example.com/epaper/html/layout/{a}/node_01.html",
      "node_pattern": "node_\\w+\\.html?$",
      "article_pattern": "content_\\w+\\.html?$",
      "timeout": 30,
      "retries": 2,
      "node_exclude": "/pad/"
    }
  ]
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `name` | ✅ | 报社名，非空、≤ 30 字符，中文即可 |
| `style` | ✅ | `ymd` → `202609/22`；`dash` → `2026-09/22`；`custom` → 使用 `date_format` |
| `date_format` | custom 时必填 | 例如 `%Y/%m/%d`、`%Y%m%d`；只允许年月日指令与路径分隔符 |
| `tpl` | ✅ | 第 1 版链接模板，**必须含 `{a}`**，且以 `http://` / `https://` 开头 |
| `node_pattern` | ❌ | 版面链接正则；默认 `node_\w+\.html?$` |
| `article_pattern` | ❌ | 文章链接正则；默认 `content_\w+\.html?$` |
| `timeout` | ❌ | 单次抓取超时秒数，5 ~ 120（缺省用全局默认） |
| `retries` | ❌ | 重试次数，0 ~ 6 |
| `node_exclude` | ❌ | 正则，用于排除手机版镜像等重复版面链接（须能编译通过） |

**同名覆盖语义**：`name` 与内置重复时**覆盖内置那条**（可用来改内置报社的 URL / 超时）；
不重复则**追加**。合并后**内置原有顺序在前**，纯新增按文件顺序追加在后。
看板里对应标注：`内置` / `覆盖` / `自定义`。

**⚠️ 关键提醒**：新增后写的是本机的 `papers.custom.json`。**要让每日 CI（GitHub Actions）
扫描也包含这家，必须把它 `git add` 后提交并推送**——本机加完并不等于 CI 会抓。
看板新增成功后的状态行也会显式提示这一点。

**容错**：文件缺失 / JSON 非法 / 单条非法 → **忽略该条并向 stderr 打一行警告**，
绝不让整个扫描崩掉。

默认解析按方正系 / 人民网系的三段式 `node_* → content_*` 工作；路径命名不同的站点可用
`node_pattern` / `article_pattern` 覆盖链接规则，日期目录不同可用 `style=custom` +
`date_format`。正文仍需能被通用标题/正文提取器识别；如果 HTTP 200 但解析不到版面或文章，
扫描会明确报「结构异常」并以非零退出，不会静默当成无命中。**新增后先用“试抓”探测**。

**SSRF 能力边界（如实说明，不封）**：`tpl` 是用户自己填的 URL，扫描器会用系统 `curl`
去抓。这里**只允许 `http` / `https` 前缀**，**不封私网 IP 段**——因为用户很可能就是想扫
内网报社。这是本机单人工具，风险可接受；但请勿在多用户/公网暴露的场景使用。

### 点击即用启动器（零命令行）

不想敲命令时，直接双击即可：

| 平台 | 扫一次（无状态） | 打开本地看板 |
|---|---|---|
| macOS | `scripts/scan.command` | `scripts/dashboard.command` |
| Windows | `scripts/scan.cmd` | `scripts/dashboard.cmd` |

- `scan.*`：跑一次「今天 + 昨天」的**无状态**扫描，结果直接打印在窗口里，结束前暂停等你查看。
- `dashboard.*`：启动本地看板并自动打开浏览器（可在页面里一键触发扫描）；**端口被占用时自动改用空闲端口**。

> macOS 首次双击 `.command` 若提示「无法打开，因为它来自身份不明的开发者」，
> 右键 → **打开**，并在弹窗里确认一次即可；之后可正常双击。

---

## 五、方案原理

六家报社的数字报结构统一，三段式（五家基于**方正系电子报系统**；人民日报属
**人民网系**，正文标签不同，解析器一并支持）：

```
第 1 版页  node_01.htm(l)   →  含当日全部版面链接（node_*）
单个版面页 node_*.htm(l)    →  含本版全部文章链接（content_*）
单篇文章页 content_*.htm(l) →  含标题 + 正文（`<founder-content>` 或 `<!--enpcontent-->`）
```

流程：**第 1 版 → 全部版面 → 全部文章 → 标题 + 正文全文匹配关键词**。

版面页里还有一层可选的 `<area coords="…" href="content_…">` **图像映射**（方正系 / 人民网系
用于标注每篇文章在版面图上的位置）。解析器从中取包围盒并归一化出「版内第 k 条 / 共 m 条 +
纵向占比」，**无需下载版面图**。实测 2026-09-23：光明日报、羊城晚报、中国教育报、湛江日报、
人民日报五个站点的版面页都有该映射，南方日报没有。没有 `<area>` 时不再静默省略，而是退一步
按**版面页链接的出现顺序**给出 `版内 k/m`（只写 `index` / `total` / `basis="order"`，不编造
任何坐标），因此该字段是「有则给出坐标与纵向占比、无则至少给出顺序」的增强信息。

### 六家入口（第 1 版地址模板）

| 报社 | 第 1 版 URL | 日期目录格式 |
|---|---|---|
| 光明日报 | `https://epaper.gmw.cn/gmrb/html/layout/{YYYYMM/DD}/node_01.html` | `202609/22` |
| 南方日报 | `https://epaper.nfnews.com/nfdaily/html/{YYYYMM/DD}/node_A01.html` | `202609/22` |
| 羊城晚报 | `http://ep.ycwb.com/epaper/ycwb/html/{YYYY-MM/DD}/node_1.htm` | `2026-09/22` |
| 中国教育报 | `https://paper.jyb.cn/zgjyb/html/{YYYY-MM/DD}/node_1.htm` | `2026-09/22` |
| 湛江日报 | `http://paper.gdzjdaily.com.cn/html/{YYYY-MM/DD}/node_1.htm` | `2026-09/22` |
| 人民日报 | `http://paper.people.com.cn/rmrb/pc/layout/{YYYYMM/DD}/node_01.html` | `202609/22` |

> 人民日报版面页还会链到 `/rmrb/pad/…`（手机版镜像），`config.PAPERS` 用可选的
> `node_exclude` 把它排除，只扫 pc 版，避免同一篇文章被重复计入。

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
make help          # 查看所有任务
make test          # pytest
make lint          # ruff check（缓存写入临时目录，不在仓库落 .ruff_cache）
make fmt           # ruff 自动格式化 + 修复
make check         # lint + test（提交前自检）
make papers-check  # 校验 papers.custom.json 并打印合并后的生效报社清单
make unlock        # 删除沙箱/异常中断可能残留的 .git/index.lock
```

测试全部**离线**：通过注入 fake fetcher 驱动扫描，HTML 样例在 `tests/fixtures/`。
CI 在 Python 3.9 / 3.12 / 3.13 上跑 lint、格式检查、测试与 shell 语法检查。

### 故障排查

1. **`fatal: Unable to create '.../.git/index.lock': File exists.`**
   - 成因：agent 沙箱（对 `git` 程序的 `unlink` 被拒绝）或异常中断留下的 stale lock。
   - 处理：确认没有 `git` 进程在跑后，`rm -f .git/index.lock`，或直接 `make unlock`。
   - 预防：`make check` 之后若发现锁，清掉再提交。
2. **端口被占用**：看板默认 `8788`；被占用时启动器与 `make web` 会自动回退到系统分配的
   空闲端口并打印实际地址。也可用 `--port` / `NEWSPAPER_PORT` 显式指定。
3. **看板不是只读的**：看板会写 `state/`（含 `web_config.json`、`seen.json`）与 `hits/`；
   「报社」卡片里的「试抓」会真实联网。
4. **`--only` 的语义**：CLI 部分匹配（如 `--only 湛江日报,打错的报`）会警告后只扫命中的报社；
   零匹配会以退出码 2 拒绝。看板采用更严格的交互校验，只要包含未知报社就会在启动前提示
   刷新列表，不会先启动后台任务再失败。
5. **扫描进行中无法停止**：看板是在自身进程的线程里跑扫描，无法中途强杀；如需强制停止请
   关闭看板进程（Ctrl+C / 关窗口）。

---

## 七、已知边界

- **数字报 ≠ 即时新闻**。报社官网的即时新闻频道（光明网、南方网等）更新更快、条数更多，
  但不等于报纸版面。如需纳入，可在此基础上追加一路采集。
- **当天报纸上线时间不固定**（一般凌晨到上午）。默认扫「今天 + 昨天」，
  当天没上线也不会漏，第二天会自动补上。
- **图片版正文抓不到**。若某版整版图片排版（少见），文字提取会失败；六家实测均有文字版。
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
- **看板暂无「停止扫描」按钮**：扫描在看板进程内的线程里直接调用 `cli.main`，Python 线程
  无法安全强杀（改成子进程属设计变更，收益不抵风险）。需要强制停止时，关闭看板进程
  （Ctrl+C / 关窗口）即可；界面上已标注「本次扫描不可中断」。

---

## 八、许可

[MIT](LICENSE)
