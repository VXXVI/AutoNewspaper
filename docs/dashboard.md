# 本地看板、导出与数据目录

> 本文由原 `README.md` 迁出。安装与日常 CLI 用法见仓库根 [README.md](../README.md)。

## 无状态运行（不产生历史文件）

指定 `--no-state`：不读写 `state/seen.json`，也不产生任何 `hits/*.md`、`hits/index.html`、
`hits/archive.json`，仅扫描并输出命中。该模式优先级高于 `--full`，可与 `--json` / `--csv`
叠加（此时人类可读日志自动分流到 stderr，保证 stdout 纯净）。

```bash
# 纯无状态运行
python3 daily_scan.py --days 1 --no-state

# JSON 数组到 stdout，可直接管道处理
python3 daily_scan.py --days 1 --no-state --json | jq .

# CSV 到 stdout
python3 daily_scan.py --days 1 --no-state --csv > today_hits.csv
```

## 数据目录

默认自动探测项目根（含 `.git` / `pyproject.toml` 的最近上级目录）。
也可显式指定，优先级：`--data-dir` > 环境变量 `NEWSPAPER_DATA_DIR` > 自动探测。

```bash
python3 daily_scan.py --data-dir /tmp/scan-out --days 1 --no-state
NEWSPAPER_DATA_DIR=/tmp/scan-out python3 daily_scan.py --days 1
```

## 导出到指定文件夹

CLI 使用 `--export-dir <文件夹>` 把**本次扫描的全部命中**另存为新文件；
目录不存在时会自动创建，且不会改变 `--data-dir` 的状态/归档语义。默认格式是
CSV，可用 `--export-format json` 改为 JSON。文件名由程序生成，同秒多次导出也不会覆盖。

```bash
# macOS / Linux；相对路径以当前工作目录为基准
python3 daily_scan.py --days 1 --no-state --export-dir ./exports

# Windows
python daily_scan.py --days 1 --no-state --export-dir .\exports --export-format json
```

看板中点击「导出结果」，可选 CSV / JSON 并填写保存文件夹。看板会在服务端
重新应用当前的报社、关键词、日期、搜索和排序条件，再原子写入文件。默认目录是
`<数据目录>/exports`（仓库内默认目录已被 `.gitignore` 忽略）。这里的目录属于**运行看板的设备**：

- 本机启动看板：写入本机文件夹；
- VPS 上启动并通过 SSH 隧道访问：写入 VPS 文件夹。

「改为浏览器下载 CSV」仍可作为兼容兜底，其保存位置由浏览器下载设置决定。

## 输出

| 路径 | 内容 |
|---|---|
| `hits/<时间戳>.md` | 每次运行的报告（各家扫描情况 + 新增命中 + 上下文） |
| `hits/index.html` | 历史全部命中汇总，可直接浏览 |
| `hits/archive.json` | 命中归档，**v2 格式**：`{version, records, layouts}` —— 记录在 `records`，版面布局按版面去重在顶层 `layouts` 只存一份（v1 的数组格式仍可读，读到即自动升级）。默认只增不删，可用 `--archive-keep-days N` 按天保留 |
| `state/seen.json` | 增量状态（已判定过的文章 URL），保证只报新增 |
| `logs/scan-YYYY-MM-DD.log` | 定时任务运行日志 |
| `exports/newspaper-hits-<时间戳>-<随机标识>.<格式>` | 可选 CSV / JSON 导出文件（目录可自定义） |

## 本地看板（可配置 + 可触发扫描的 Web 界面）

用标准库起一个看板，零第三方依赖、无构建、无 CDN：

```bash
python3 dashboard.py                      # 前台运行，http://127.0.0.1:8788（默认端口）
python3 dashboard.py --port 8799 --open   # 前台运行并自动打开浏览器
python3 dashboard.py --open --detach      # 后台运行（关掉终端也不会停）+ 打开浏览器
python3 dashboard.py --status             # 看板是否在运行
python3 dashboard.py --stop               # 停止后台看板
make web                                  # 只打开看板（前台，开发用）
make start                                # 打开看板 + 按上次页面设置自动抓取
```

> **日常使用建议 `--detach`**：服务会脱离当前终端运行，把 `host/port/pid` 写进
> `state/dashboard.json`；重复执行只会复用已在运行的实例（顺带解决「换了端口、
> 旧标签页连不上」）。前台模式（`make web`）仍然可用 —— 那是开发/排障场景，
> 关掉终端就停止服务，属预期行为。

> 端口可用 `--port` 或环境变量 `NEWSPAPER_PORT` 覆盖（`scripts/dashboard.command` /
> `scripts/dashboard.cmd` 会读取后者）。`make web` / `make start` 和双击启动器在默认端口
> 被占用时会自动改用系统分配的空闲端口；直接运行 `dashboard.py` 时请显式传
> `--port 0` 或换一个端口。以 `--detach` 启动时，端口回退在服务内部完成（先探测、
> 被占用就直接改用系统分配），实际地址以启动输出与 `state/dashboard.json` 为准。

- **数据来源**：`hits/archive.json`（历史命中）与 `state/seen.json`（已判定文章数）。修改
  关键词只影响当前筛选和后续扫描，**不会删除历史归档**；可随时点击「查看全部历史」清除
  当前关键词聚焦并回看所有关键词的旧记录。
- **版面位置图**：扫描器会把网站提供的 `<area>` 或 `mappingOriginal` 坐标统一为 0~1
  的二维布局；看板按报社 / 日期 / 版面分组绘制，红色标出当前筛选命中。网站只给文章
  顺序、不提供二维坐标时，只显示 `版内 k/m`，不会伪造版面图。旧归档会在下一次扫描
  同一文章时自动补齐布局元数据。同一版面的 `layout` 由后端抽到响应的顶层 `layouts` 表
  按版面去重（记录只带 `geometry`），前端按同一分组键取回，不改变渲染结果。
- **功能**：首屏是「扫描配置 + 实时任务状态」双区工作台；概览指标会跟随筛选变化；
  结果列表使用独立筛选栏和 50 条一批的渐进加载，避免长期归档一次性渲染卡顿；
  按报社 / 命中关键词 / **日期区间**（含「全部 / 今天 / 近 7 天 / 近 30 天」**快捷区间**）筛选；
  标题与上下文搜索、排序；命中列表的列序为「报社 / 日期 / 标题与命中上下文 / **版面位置**」，
  版面位置固定在**最右列**、与报道一一对应，展示**版面**（报纸中的相对位置）与**版内位置**
  （`版内 k/m`，无 `<area>` 的报社按版面页链接顺序给出；`上版 xx%` 仅在网站提供
  `<area>` / mapping 坐标时出现）并可点击标题跳原文；支持**把当前筛选结果导出为
  CSV / JSON 到指定文件夹**，并保留浏览器 CSV 下载兜底。
  结果区右上角的**「隐藏筛选 / 显示筛选」**可整块收起左侧筛选栏，收起后结果表占满整行；
  该选择记在浏览器 `localStorage`，重开看板后保持。
  趋势图的日期柱和版面图的「查看本版报道」都是可切换筛选按钮：点击后**趋势、版面与报道列表**
  会使用同一结果集同步刷新，再点一次或点击清除即可恢复完整结果。
- **开始一次监测（会写数据）**：页面顶部保留最常用的**关键词 / 扫描范围 / 开始扫描**；
  并发数与报社选择收进「高级扫描设置」。扫描由看板进程**直接触发**，进度经 **SSE**
  （`GET /api/scan/events`）实时推送，页面显示真实的「报社 × 日期」总体进度与增量日志
  （SSE 不可用时自动回退轮询），完成后自动刷新命中列表。扫描进行中会出现**「取消扫描」**
  按钮，点击后为**协作式取消**（见 [architecture.md](architecture.md) 已知边界）。
  **扫描范围**为二选一：
  - `按天数`：1 ~ 62 天（默认 2 = 今天 + 昨天）；
  - `按日期范围`：两个 `<input type="date">`（默认 `until` = 今天、`since` = 今天减 1 天），
    两端都含、跨度 ≤ 62 天，与后端 `--since/--until` 完全一致。
  **只扫选中报社**：`GET /api/papers` 的 `effective` 列表渲染为多选 chips，**默认全选**；
  取消勾选即只扫其中几家（全选时也显式发送 `only`）。该选择持久化在
  `state/web_config.json` 的 `only` 字段（逗号分隔字符串；空串 = 全选）。
  抓取结果（`hits/`、`state/`）写回**看板正在读取的那个数据目录**；上次用的配置
  （含 `mode` / `since` / `until` / `only`）持久化在 `state/web_config.json`。
  页面首次加载会聚焦**当前保存的关键词**；每轮扫描完成后自动切换为该次扫描关键词并刷新报道。
- **筛选卡片里的两个日期框**（`#from` / `#to`）**只筛选已抓到的命中列表，不影响扫描范围**，
  这一点最容易误解，故界面上已标注「仅筛选已抓到列表」。
- **报社管理**：页面底部「报社与抓取规则」默认折叠，列出内置 / 自定义 / 覆盖三类来源；
  展开后可新增、编辑、试抓、停用、恢复和删除，扫描期间暂时锁定修改。内置报社不能被
  破坏性删除：可安全停用，或在编辑生成覆盖配置后再「恢复默认」；纯自定义报社才会真正删除，
  详见 [custom-papers.md](custom-papers.md)。存在自定义 / 覆盖报社时，卡片会显示
  「复制提交命令」按钮，命令会先同步 `origin/main`，再只提交 `papers.custom.json`，推送后
  读取远端主干便于复核。该快捷命令为 macOS/Linux Shell 语法；Windows 请按本节相同顺序在
  PowerShell 逐条执行。剪贴板不可用时会回退为选中命令文本、提示手动复制。
- **新增路由**（`src/newspaper_auto/web.py`）：
  - `GET /api/hits`：看板首屏数据。`records` 里每条命中含 `geometry`（版内位置）但**不含**
    `layout`；版面布局统一放在顶层 `layouts` 表，键为
    `paper\u0001date\u0001section_no\u0001section`（与前端 `render.js` 的分组键一致），
    因此同一版面只下发一份坐标。另有 `paper_counts` / `keywords` / `date_counts` /
    `configured_keywords` / `papers` 等汇总字段。
  - `POST /api/scan`：启动扫描。`202` 已启动 / `409` 已有任务在跑 / `400` 参数非法 /
    `403` 来源校验失败 / `413` body 过大；同一时刻最多一个任务。可选 `only` 字段
    （逗号分隔字符串或字符串数组）只扫指定报社。
  - `GET /api/scan`：读当前/最近一次任务状态，无任务时返回 `{"status":"idle"}`；可传
    `?after=<日志游标>` 只取新增日志，响应含任务 ID、总体进度和下一游标。
  - `GET /api/scan/events`：SSE（`text/event-stream`）持续推送任务快照与日志增量，到达终态自动
    关闭；前端优先用它接收进度，不可用或断开时回退到 `GET /api/scan` 轮询。
  - `POST /api/scan/cancel`：请求取消当前扫描（**协作式**，排队中的版面/文章抓取立即停止，
    在途的单个 HTTP 请求不打断，不写 `state/` 与 `hits/`）。`202` 已受理 / `409` 无运行中任务；
    任务状态变为 `cancelled`（退出码 130）。
  - `GET /api/scan/history`：最近任务摘要（`state/tasks.json`，最多 20 条），含状态、耗时、命中概览。
  - `POST /api/config` / `GET /api/config`：保存 / 读取配置；文件不存在时回落到
    `config.DEFAULT_KEYWORDS` / `DEFAULT_DAYS` / `DEFAULT_WORKERS`，并补 `mode="days"` /
    `since=null` / `until=null` / `only=""`。`only` 为可选字段（逗号分隔字符串，也接受字符串
    数组），缺省 / 空串 = 全选。
  - `POST /api/export`：接收指定目录、`csv|json` 格式和受控筛选条件；服务端只从
    `hits/archive.json` 重新筛选，不接收前端上传的任意记录。`201` 返回实际文件路径、条数和格式；
    参数非法返回 `400`，写入失败返回 `500`。相对目录以数据目录为基准。
  - `GET /api/papers`：保留 `builtin` / `custom` / `effective`，并返回 `disabled` 与统一的
    `items`；每个 item 含 `name`、`source`（`builtin|custom|override`）、`enabled` 和完整
    `config`。`effective` 只含当前启用报社，旧客户端仍可继续读取。
  - `POST /api/papers`：新增或**同名覆盖**一条自定义报社，返回新的列表；非法 → `400`。
  - `POST /api/papers/status`：body `{"name":"…", "enabled":false}` 停用，改为 `true`
    即恢复；重复提交幂等，不删除规则或历史记录。
  - `POST /api/papers/delete`：body `{"name":"…"}`；删除纯自定义报社，或删除同名覆盖以
    恢复内置默认；**内置且未被覆盖 → `400`**，不存在 → `404`，成功 → `200` + 新列表。
  - `POST /api/papers/test`：body 为一条 paper（或只给内置/已有报社的 `{"name":"…"}`）→
    最多读取入口与一个样例版面，返回入口 URL、HTTP 状态、版面数、样例文章数和说明；
    不抓正文、不写文件。
- **安全**：只允许监听 `127.0.0.1` / `localhost`，显式拒绝 `0.0.0.0` 等公网监听；
  所有请求校验 `Host` 必须是环回主机（防 DNS rebinding），带 `Origin` 时必须为环回 origin
  （防跨站触发抓取），`POST` 必须声明 `application/json` 且 body ≤ 16 KiB。关键词只作为
  argv 元素传给 `cli.main`，**绝不拼 shell 字符串**；不返回任何 CORS 头。页面上的数据与
  扫描日志全部用 `textContent` 渲染，不会执行内容里的 HTML/脚本；响应还带 CSP、禁止
  iframe、禁止 MIME 猜测与来源泄露等浏览器安全头。VPS 上需要远程查看时，请使用 SSH 隧道：
  先在 VPS 启动看板，再从自己的电脑执行 `ssh -L 8788:127.0.0.1:8788 user@vps`，最后访问
  `http://127.0.0.1:8788`，不要把看板端口直接暴露到公网。
- **端口**：默认 `8788`；`make web` / `make start` 和双击启动器会在端口冲突时
  自动回退到系统分配的空闲端口（`--detach` 模式下由服务内部先探测再回退，不会白等）。
  直接运行 `dashboard.py` 可用 `--port 0`，或用 `--port` / `NEWSPAPER_PORT` 指定其他端口。
- **进程与终端解耦**：`dashboard --detach` 启动的服务脱离控制台，关掉窗口不会停；
  运行状态落在 `state/dashboard.json`（`host/port/pid`），`--status` / `--stop` 据此工作。
  页面在连不上服务时会给出明确的恢复指引与「重新连接看板」按钮，而不是只报一句错。

## 点击即用启动器（零命令行）

不想敲命令时，直接双击即可：

| 平台 | 扫一次（无状态） | 打开本地看板 |
|---|---|---|
| macOS | `scripts/scan.command` | `scripts/dashboard.command` |
| Windows | `scripts/scan.cmd` | `scripts/dashboard.cmd` |

- `scan.*`：调用 `scripts/run_scan.*` 跑一次「今天 + 昨天」的**无状态**扫描，结果打印在
  窗口里，结束前暂停等你查看。
- `dashboard.*`：启动本地看板并自动打开浏览器（可在页面里一键触发扫描）；**后台运行**
  （关掉窗口不会停止服务），**端口被占用时自动改用空闲端口**。
  停止看板：`python3 dashboard.py --stop`（Windows 便携版：
  `NewspaperAuto.exe dashboard --stop`）。

> macOS 首次双击 `.command` 若提示「无法打开，因为它来自身份不明的开发者」，
> 右键 → **打开**，并在弹窗里确认一次即可；之后可正常双击。

## 故障排查

1. **`fatal: Unable to create '.../.git/index.lock': File exists.`**
   - 成因：agent 沙箱（对 `git` 程序的 `unlink` 被拒绝）或异常中断留下的 stale lock。
   - 处理：确认没有 `git` 进程在跑后，`rm -f .git/index.lock`，或直接 `make unlock`。
2. **端口被占用**：看板默认 `8788`；被占用时启动器与 `make web` 会自动回退到系统分配的
   空闲端口并打印实际地址。也可用 `--port` / `NEWSPAPER_PORT` 显式指定。
3. **页面提示「无法连接看板服务」**：说明看板服务已经不在了（电脑重启、服务被关、
   或页面停在旧端口）。重新运行启动器（Windows 双击 `NewspaperAuto.exe`），
   再点页面里的「重新连接看板」。用 `dashboard --status` 可确认服务状态，
   `dashboard --stop` 可停止后台服务。
4. **看板不是只读的**：看板会写 `state/`（含 `web_config.json`、`seen.json`）与 `hits/`；
   「报社」卡片里的「试抓」会真实联网。
5. **`--only` 的语义**：CLI 部分匹配（如 `--only 湛江日报,打错的报`）会警告后只扫命中的报社；
   零匹配会以退出码 2 拒绝。看板采用更严格的交互校验，只要包含未知报社就会在启动前提示
   刷新列表，不会先启动后台任务再失败。
6. **扫描进行中无法停止**：看板是在自身进程的线程里跑扫描，无法中途强杀；如需强制停止请
   关闭看板进程（Ctrl+C / 关窗口）。
