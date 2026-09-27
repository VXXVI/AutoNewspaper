# 定时任务与调度

> 本文由原 `README.md` 的调度章节整理而来，并在文末保留早期交付记录。
> 安装与日常用法见仓库根 [README.md](../README.md)。

生产路径是 **GitHub Actions + 外部 cron 触发**；macOS `launchd` 与 Windows 任务计划程序是
**本机替代路径**，与 Actions **可以并存**——本机脚本一律以 `--no-state` 无状态运行，
只把结果打到控制台并写 `logs/`，**不读写 `state/seen.json`、不产生 `hits/*`**，因此不会与
Actions 互相覆盖。

| 路径 | 产出物 | 作用 |
|---|---|---|
| 本机 launchd / 任务计划程序 | 控制台 + `logs/scan-*.log`（无状态） | 当场看一眼今天有没有命中 |
| GitHub Actions | `hits/*`、`hits/archive.json`、`state/seen.json`（归档到 `data` 分支） | 维护报告归档与增量历史 |

### 调度方案对照表

| 方案 | 成本 | 需仓库转公开？ | 本机需常开？ | 适合场景 |
|---|---|---|---|---|
| GitHub Actions 原生 `schedule` | 私有库消耗 Actions 额度 | 否 | 否 | 部署最简，允许延迟 |
| 外部 cron 打 `repository_dispatch` | 取决于 cron 服务 | 否 | 否（只需 cron 侧在线） | **当前生产方案**，适合更看重准点 |
| macOS `launchd` 本机 | 免费 | 否 | 需唤醒/登录 | 手边是 Mac、想当场看结果；无状态，可与 Actions 并存 |
| Windows 任务计划程序 | 免费 | 否 | 需开机/登录 | 手边是 Windows；无状态，可与 Actions 并存 |

**日级精度下四种都可接受**——默认扫「今天 + 昨天」，漏掉一天第二天会自动补回来。

## 定时任务（macOS launchd）

本机定时通过 `scripts/run_scan.sh` 以 `--no-state` **无状态**运行：
不读写 `state/seen.json`，也不产生 `hits/*.md` / `hits/index.html` / `hits/archive.json`，
只把命中打到控制台并追加日志到 `logs/scan-YYYY-MM-DD.log`。

若你只看 Actions 结果，不装本机任务也完全没问题；不再需要本机任务时，
`./scripts/install_launchd.sh uninstall` 移除即可。
plist 由模板生成，**路径与运行时间都自动替换，无需手工编辑**：

```bash
./scripts/install_launchd.sh install 10:00              # 每天 10:00（不传时间默认也是 10:00）
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
- 自定义回溯天数：设置环境变量 `NEWSPAPER_DAYS`（默认 2＝今天+昨天，与 `daily_scan.py` 一致），
  或在模板 `EnvironmentVariables` 中加入同名键。

自定义关键词：直接给启动脚本传参（会原样透传给 `daily_scan.py`）：

```bash
./scripts/run_scan.sh --kw 人工智能 --days 3 --only 湛江日报
./scripts/run_scan.sh --since 2026-09-20 --until 2026-09-25   # 显式区间（与 --days 互斥）
```

> `run_scan.sh` / `run_scan.ps1` 会把**所有**参数（`--since` / `--until` / `--export-dir` /
> `--export-format` / `--archive-keep-days` 以及未来新增的选项）原样透传给 `daily_scan.py`；
> 脚本本身固定追加 `--no-state`，并在给了 `--days` / `--since` / `--until` 时不再注入
> 默认的 `--days`（两者在 CLI 里互斥）。

## 定时任务（Windows 任务计划程序）

> **用打包好的 `NewspaperAuto.exe` 的话，不需要下面这一节**：它是无窗口程序，
> 在任务计划程序里直接填
> `程序: <目录>\NewspaperAuto.exe`、`参数: scan --days 2 --no-state`
> 就行 —— 不需要任何 `.cmd` 包装（也就不会闪黑窗口），扫描日志由程序自己写到
> `logs\scan-YYYY-MM-DD.log`。下面这一节讲的是**源码用户**的路径（需要本机装 Python）。

Windows 上有一对标脚本，行为与 macOS 侧一致（同样无状态、同样写 `logs\scan-YYYY-MM-DD.log`）：

- `scripts\run_scan.ps1`：启动脚本（对标 `run_scan.sh`，参数透传、环境变量、退出码一致）
- `scripts\install_task_scheduler.ps1`：注册 / 卸载 / 查看 / 立即运行（任务名 `NewsPaperAuto`）

**1. 准备 Python 与 curl**

- Python：从 [python.org](https://www.python.org/downloads/windows/) 安装，
  安装时**务必勾选 `Add python.exe to PATH`**。验证：`python --version`。
- curl：**Windows 10 1803+ 自带** `curl.exe`（位于系统目录）。验证：`curl --version`。
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
- **`.ps1` 中文乱码**：脚本均以 **UTF-8 with BOM** 保存，Windows PowerShell 5.1
  可正确识别；若被编辑器另存为无 BOM，请改回 UTF-8 with BOM。
- **行尾**：仓库用 `.gitattributes` 钉死了行尾 —— `*.cmd` / `*.bat` / `*.ps1` 签出为 **CRLF**，
  `*.sh` / `*.command` 签出为 **LF**。**不要手工把 `.cmd` 改成 LF**（`goto` / 多行 `for` 块可能失败），
  也不要让编辑器把 `.sh` 存成 CRLF（bash 会报 `\r: command not found`）。
  自查：`git ls-files --eol scripts/`。

## 外部定时（VPS crontab）

不想让本机常开时，可用任意一台 VPS 的 crontab 触发 GitHub Actions。
这是本项目**生产环境当前采用**的方式。GitHub 官方支持私有仓库的 `schedule`，但定时任务
可能延迟，高负载时甚至丢弃；本项目曾出现原生定时长时间无记录，因此保留
`repository_dispatch` 作为更准点的生产入口。两者都配置时可能同日执行两次；建议选一个作为主触发器。

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

以下示例用 `user@your-vps` 表示你的 VPS SSH 地址，请替换为真实用户名和主机名：

```bash
# 建目录（700：同机其他用户读不到）
ssh user@your-vps 'mkdir -p ~/.newspaperauto && chmod 700 ~/.newspaperauto'

# 部署脚本（仓库里的就是部署版本，天然保持一致）
scp scripts/vps_dispatch.sh user@your-vps:.newspaperauto/dispatch.sh
ssh user@your-vps 'chmod 700 ~/.newspaperauto/dispatch.sh'

# 追加 crontab（先备份；已存在则跳过，避免重复堆积）
ssh user@your-vps 'TMP=$(mktemp); crontab -l > "$TMP" 2>/dev/null || true; \
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
ssh -t user@your-vps 'umask 077; read -rsp "粘贴 PAT 后回车: " P; echo; \
  printf "%s" "$P" > ~/.newspaperauto/pat; unset P; ls -l ~/.newspaperauto/pat'
```

**4. 立即验证一次**（不必等到明天早上）

```bash
ssh user@your-vps '~/.newspaperauto/dispatch.sh; echo "exit=$?"; tail -n 2 ~/.newspaperauto/dispatch.log'
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

- 日志在 `~/.newspaperauto/dispatch.log`。**不写系统日志目录**：多数发行版该目录属
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

---

## 附：历史交付记录（v0.1 无状态与定时任务）

> 以下为项目早期的交付说明存档，数量与界面描述可能不随主线更新；

# 交付说明：无状态监测模式与定时任务

> 本文是早期版本的历史交付记录，保留当时的验证结果；当前报社数量、安装方式与命令
> 以仓库根目录的 `README.md` 为准。

- 项目：五家报社数字报关键词监测（NewsPaperAuto）
- 日期：2026-09-22
- 范围：`daily_scan.py` 无状态/结构化输出改造 + macOS 定时任务脚本 + 文档
- 状态：已完成并通过独立复跑验证
- 说明：本次改动未使用任何 git 命令，变更全部留在磁盘上，由使用者自行提交

---

## 一、背景与前置结论

### 1.1 为什么要放弃第三方 RSS

针对五家报社（光明日报、南方日报、羊城晚报、中国教育报、湛江日报）排查了所有公开 RSS 渠道，结论是**没有可直接订阅的第三方 RSS**：

| 渠道 | 结论 |
|---|---|
| 官方自带 RSS | 全部没有（`gmw.cn/rss/`、`gdzjdaily.com.cn/rss/`、`ycwb.com/rss/` 均 404；`jyb.cn/rss/` 为空壳） |
| RSSHub | 五家电子版 **0 路由**。仅有 `/southcn/nfapp/*`（南方+ App）与 `/ycwb/:node`（金羊网新闻站），均非数字报版面；且公共实例 `rsshub.app`(403 挑战页)、`rsshub.rssforever.com`(503)、`rss.shab.fun`(无响应) 全部不可用 |
| 通用网页转 RSS（openrss.org / rss.app） | `openrss.org` 对五个域名无现成 feed；`rss.app` 属「固定 URL 监控」，而这五个入口是 **JS 跳转页**、文章 URL **带日期目录**（如 `/202609/22/`），固定 URL 每天必断 |

### 1.2 采用方案：直接爬取官网数字报

`daily_scan.py` 已实现的链路，绕开搜索引擎收录延迟与第三方 RSS 空白：

```
当日第 1 版入口 → 全版面 node_*.htm → 全文章 content_*.htm → 标题 + <founder-content> 正文全文匹配
```

- 已解决光明日报服务器 HTTP/2 断连问题（curl 加 `--http1.1`）
- 带重试 + 并发（默认 8），入口失败细分为「网络层失败 / HTTP 非 200 / 内容异常」三类
- 使用者明确**不需要历史记录**，故本次新增「无状态」运行方式

---

## 二、本次交付内容

### 2.1 `daily_scan.py` 新增三个参数（向后兼容）

| 参数 | 作用 |
|---|---|
| `--no-state` | 本次运行完全不碰历史：不读也不写 `state/seen.json`，不写 `hits/archive.json`、`hits/index.html`、`hits/*.md`，即不产生任何新历史文件；仍正常扫描并输出命中。优先级高于 `--full`，可与 `--dry` 叠加 |
| `--json` | 命中结果以 JSON 数组打印到 stdout，可被 `json.loads` 直接解析；元素字段沿用 hits：`paper/date/section/title/url/text_len/snippets/matched`。启用时进度日志全部转 stderr，保证 stdout 为纯 JSON |
| `--csv` | 命中结果以 CSV 打印到 stdout（列：`paper,date,section,title,url,text_len,matched`，`matched` 用分号连接）；与 `--json` 互斥，同时给出报错退出 |

**不带任何新参数时，行为与改造前完全一致。**

### 2.2 新增 `scripts/` 定时任务

| 文件 | 作用 |
|---|---|
| `scripts/run_scan.sh` | `set -euo pipefail`；cd 到仓库根；用固定 Python 执行 `daily_scan.py --days 1 --no-state`；关键词支持位置参数或 `NEWSPAPER_KW` 环境变量；日志追加到 `logs/scan-YYYY-MM-DD.log` |
| `scripts/com.quark.newspaperauto.plist` | launchd 模板，每天 10:00（`StartCalendarInterval Hour=10 Minute=0`）运行 `run_scan.sh` |
| `scripts/install_launchd.sh` | 幂等安装：拷贝 plist 到 `~/Library/LaunchAgents/` 并重载；提供 `install` / `status` / `run-now` / `uninstall` |

### 2.3 其他

- `README.md` 新增「无状态运行（不产生历史文件）」「定时任务（launchd）」两节
- `.gitignore` 追加 `logs/`

---

## 三、使用方法

```bash
cd /path/to/AutoNewspaper

# 纯结果（无状态，不产生历史文件）
python3 daily_scan.py --days 1 --no-state --kw 示例大学

# JSON 输出，管道喂给其他程序
python3 daily_scan.py --days 1 --no-state --json --kw 示例大学

# CSV 输出
python3 daily_scan.py --days 1 --no-state --csv --kw 示例大学

# 多关键词
python3 daily_scan.py --days 1 --no-state --kw 示例大学 --kw 示大 --kw 例大

# 定时任务
./scripts/install_launchd.sh install     # 启用每天 10:00 自动运行
./scripts/install_launchd.sh status      # 查看状态
./scripts/install_launchd.sh run-now     # 立即跑一次
./scripts/install_launchd.sh uninstall   # 卸载
```

---

## 四、验证记录（独立复跑）

```
1. 回归基线
   python3 daily_scan.py --days 1 --dry
   ✓ 光明日报 版面17   ✓ 南方日报 版面36   ✓ 羊城晚报 版面9
   ✓ 中国教育报 版面13  ✓ 湛江日报 版面9

2. 无状态 + JSON（stdout 交 python 解析）
   python3 daily_scan.py --days 1 --no-state --json --kw 示例大学 2>/dev/null | python3 -c 'import json,sys; ...'
   → JSON OK, records = 2
     · [湛江日报] 扎实抓好中央环保督察问题整改 用心用情用力守护"国宝"红树林
       http://paper.gdzjdaily.com.cn/html/2026-09/22/content_146377...
     · [湛江日报] 湛江市2026年广东省五一劳动奖状、五一劳动奖章、工人先锋号...
       http://paper.gdzjdaily.com.cn/html/2026-09/22/content_146378...

3. 无状态证据（跑前 → 跑后）
   state/seen.json md5 = ef0c2b57b6e512434e6b678e2196ad06  （前后一致 ✓）
   hits/ 文件清单 = 2026-09-22_1823.md 2026-09-22_1824.md archive.json index.html （前后一致 ✓）

4. 语法
   bash -n scripts/*.sh  ✓        python3 -m py_compile daily_scan.py  ✓

5. CSV
   表头：paper,date,section,title,url,text_len,matched  → 2 行数据 ✓

6. 互斥
   python3 daily_scan.py --json --csv
   → error: argument --csv: not allowed with argument --json（退出码 2）✓
```

---

## 五、变更文件清单

```
daily_scan.py                                   # 新增 --no-state / --json / --csv
README.md                                       # 新增两节文档
.gitignore                                      # 追加 logs/
scripts/run_scan.sh                             # 新增
scripts/com.quark.newspaperauto.plist           # 新增
scripts/install_launchd.sh                      # 新增
docs/scheduling.md            # 本文档
```

---

## 六、注意事项与未决风险

1. **定时任务尚未安装**：本次只创建脚本，未改动 `~/Library/LaunchAgents`。确认要启用再执行 `./scripts/install_launchd.sh install`。
2. **发布延迟**：报纸通常凌晨上线，建议 07:00 后运行；过早跑可能版面不全。
3. **网络偶发**：个别报社可能出现 0 版面或超时，脚本已有重试与兜底；并发默认 8，若某家不稳可降到 4~5。
4. **launchd 与休眠**：Mac 深度睡眠或注销时，任务可能顺延到唤醒后触发；长期稳定运行建议常开机器，或考虑 `caffeinate`。
5. **未提交**：以上改动均未执行 git 提交，请按需自行分步提交。

---

## 七、补充（部署形态定稿：外部定时 + 抓取失败不再静默）

> 本节为同日后续补充，更正前文与定时方式、失败可见性相关的表述。

### 7.1 生产触发方式：VPS crontab → repository_dispatch

- **私有仓库下 GitHub 原生 `schedule` 实测零触发**：以 `*/5` 探测运行约 36 分钟，
  `gh run list` 中零条记录。因此 `scan.yml` 里的 `schedule: "0 2 * * *"` 仅保留不动，
  生产改由 **VPS crontab 调 `POST /repos/VXXVI/AutoNewspaper/dispatches`**
  （`{"event_type":"scan"}`）触发。
- 触发用 PAT 为 fine-grained，权限**仅需 `Contents: Read and write`**，
  只存在于 VPS 本机，**不得写入仓库、日志或提交信息**。
- `concurrency: daily-scan` 已能挡住重复并发（重叠触发排队而非并跑）。
- 完整 crontab 行、时区换算与失败排查步骤见 `README.md`「外部定时（VPS crontab）」。

### 7.2 本机调度与 Actions/VPS 可以并存

本机 launchd 与 Actions **可以并存、互不覆盖**：`scripts/run_scan.sh` 自诞生起即无条件携带
`--no-state`，本机调度路径**不读写 `state/seen.json`、不产 `hits/*`**，只把结果打到控制台
并写 `logs/scan-*.log`；Actions 负责 `hits/*`、`hits/archive.json` 与 `state/seen.json`
（归档 + 增量）。两者产出物不同，不会互相覆盖，也不会导致重复报或漏报。

旧的「两边共写 `state/seen.json`，必须二选一」说法已被证伪，不再适用。
**不再需要本机任务时**（例如只看 Actions 结果，本机路径本就是可选的）可选执行：

```bash
./scripts/install_launchd.sh uninstall
```

### 7.3 抓取失败不再静默

- `scanner` 统计新增 `sections_failed` / `articles_failed` / `failed_urls`；
- Markdown 报告新增「抓取失败（缺报）」章节，明确警示「无命中 ≠ 真的没有」；
- CLI 在**入口失败**、或**抓取失败占比达到 `config.PARTIAL_FAILURE_RATIO`（默认 50%）**时
  以退出码 1 退出，让 Actions 步骤变红；未达阈值的零星失败只在日志/报告里告警；
- Actions 提交步骤改为 `if: always()`，失败时也提交已落盘的增量结果，
  提交信息追加 `[含失败]` 后缀；
- 羊城晚报在 GitHub runner（Chicago）上原本 30s 才返回 `HTTP 000`，属临界状态，
  已在 `config.PAPERS` 单独覆盖为 `timeout=60 / retries=4`。
