# 变更记录

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与
[语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [0.3.1] - 2026-09-25

### 修复

- **看板前端在真实浏览器里完全不可用**：`web.STATIC_TYPES` 白名单漏收 `store.js`，
  `app.js` 的静态 `import "./store.js"` 拿到 404，整个 ES Module 不执行 —— 页面停在
  「正在读取数据…」，命中列表 / 扫描 / 导出 / 报社管理全部失效。jsdom 测试直接从文件系统
  `import()`，绕过 HTTP 层所以 7 个用例全绿漏检（`web.py`、`tests/test_web.py`）。
- **`dashboard.py` 未传脚本自身目录**：从仓库外启动时 `state/`、`hits/`、
  `papers.custom.json` 会落到当前工作目录，与 README「数据目录自动探测项目根」不符；
  现与 `daily_scan.py` 对齐（`web.main()` 新增 `default_data_dir`，`dashboard.py`）。
- **`--host ::1` 在绑定阶段失败**并给出「端口不可用」的误导提示：服务器以 `AF_INET`
  绑定，现把 `::1` 一并归一为 `127.0.0.1`（`web.py`）。
- **静态 `hits/index.html` 的链接未校验协议**：`data:` 等非 http/https 的 URL 现降级为
  纯文本，与前端 `safeUrl()` 口径一致（`report.py`）。
- **`hits_to_csv` 对非数值 `text_len` 不做公式转义**：数值原样输出（避免 `-42` 被误加撇号），
  只有字符串才走转义兜底（`report.py`）。

### 变更

- **统一扫描回溯天数为 2（今天 + 昨天）**：`.github/workflows/scan.yml` 与
  `scripts/run_scan.sh` 原先固定 `--days 1`，与 README「默认扫今天+昨天，漏掉一天第二天会
  自动补回来」的承诺相反 —— 只扫 1 天时，若某报入口当天失败或报纸当天未上线，该日文章不会
  被后续运行覆盖、`seen.json` 也不记录，属**永久漏报**。现两处与 CLI 默认 `DEFAULT_DAYS = 2` 对齐。
- **便携包不再携带本机运行数据**：`release.yml` 的 `git archive` 排除 `state/` 与 `hits/`
  （含维护者的增量状态与历史归档），避免接收方首次扫描显示「0 新增」。
- **`.gitignore` 增加 `state/tasks.json`**：看板任务历史含本机关键词与 `only` 报社列表，
  与 `web_config.json` 同等对待，避免被日常 `git add -A` 带进 main。
- **前端 DOM 测试的版本桩改用哨兵值 `0.0.0-stub`**：原先硬编码 `0.2.0`，容易被误读成
  「版本漂移漏检」，实际只是桩数据（`tests/frontend/harness.mjs`、`app.dom.mjs`）。

### 测试

- 新增 `test_all_static_assets_reachable_over_http`、`test_app_js_imports_are_served`：
  在 HTTP 层逐个请求 `static/` 下的资源并遍历 `app.js` 的本地 import，守护白名单同步
  （本轮 P0 的回归测试）。
- 新增 `test_create_server_normalizes_ipv6_loopback`、`test_create_server_rejects_non_loopback`。
- 新增 `test_hits_to_csv_escapes_non_numeric_text_len_only`、
  `test_render_md_lists_failed_entries`（抓取失败可见化此前零覆盖）、
  `test_render_html_links_only_http_and_https`。

## [0.3.0] - 2026-09-25

### 新增

- **源码分发与克隆体验**：`.gitattributes` 增加 `export-ignore`（`legacy/`、`docs/`、`.github/`），
  Release workflow 额外产出约 180 KB 的 `NewspaperAuto-<tag>-portable.zip`（解压即用、无需 git）；
  README 说明「clone 慢是网络而非体积」并给出 `--depth 1` 浅克隆与直接下压缩包两种加速方式
  （`.gitattributes`、`.github/workflows/release.yml`、`README.md`）。
- **前端自动化测试**：新增 `tests/frontend/format.test.mjs`（日期计算、CSV 公式注入防护、
  关键词切分、版内位置文本等纯函数）与 `tests/frontend/store.test.mjs`（reducer 的 11 个用例：
  action → 新 state、Set 不可变、`LOAD_DATA` 的 reset/preserve 等），均用 Node 内置 `node:test`
  （零第三方依赖），CI 以 `node --test tests/frontend/*.test.mjs` 运行
  （`tests/frontend/`、`.github/workflows/ci.yml`、`README.md`）。
- **前端 DOM 测试（jsdom，仅 dev 依赖）**：新增 `tests/frontend/harness.mjs`（把真实
  `index.html` + `api.js` + `app.js` 装进 jsdom、注入 fetch 桩，**不联网**）、
  `app.dom.mjs`（首屏渲染 / 报社列表 / 搜索过滤 / 报社 chip 切换 / 扫描模式切换 / 快捷区间 /
  提交扫描并轮询共 7 例）、`run-dom.mjs`（极简驱动，规避 jsdom 句柄导致的 node:test 挂起）。
  新增根 `package.json` 与 `package-lock.json`（**仅前端测试用的 devDependency**，Python 侧零依赖
  不变），CI 用 `npm ci` + `node tests/frontend/run-dom.mjs` 运行。
- **抓取层改为标准库优先、系统 curl 兜底**：主路径用 `urllib`（HTTP/1.1）直连，天然绕开
  光明日报在 HTTP/2 下断连的问题，同时去掉「必须装有系统 curl」的硬依赖、少一次 fork/exec；
  仅当标准库网络层失败且系统存在 curl 时才兜底重试（站点侧 4xx/5xx 不触发兜底）。
  （`http.py`、`tests/test_http.py`）
- **看板扫描进度改 SSE + 协作式取消 + 任务历史**：新增 `GET /api/scan/events`
  （`text/event-stream`，按日志游标增量推送，终态自动关闭；前端不可用/断开时回退轮询）、
  `POST /api/scan/cancel`（在下一个「报社 × 日期」单元边界退出，退出码 130，不写 state/hits，
  任务状态 `cancelled`）、`GET /api/scan/history`（`state/tasks.json`，最近 20 条摘要）。
  取消回调经 ContextVar 传递，不改可注入的 runner 签名（`cli.py`、`web.py`、`web_tasks.py`、
  `static/*`、`config.py`、`paths.py`）。
- **前端抽 ES Module**：新增 `static/format.js`（日期 / CSV / 关键词 / 位置文本等纯函数），
  `static/app.js` 改为 ES Module 入口并 `import` 之；`index.html` 以 `<script type="module">`
  加载，`static/package.json`（type=module）让 Node/CI 的 `node --check` 覆盖模块。
- **发布与分发**：新增 `.github/workflows/release.yml`，打 `v*` tag 时构建 sdist/wheel、
  在全新 venv 冒烟验证 `newspaper-scan --version` 与 `static/` 随包安装，再用 `gh` 发布到
  GitHub Release；README 增补「分发给别人用」的三种推荐方式。
- **默认主题与历史查看联动**：默认关键词调整为「人工智能」。看板首次加载聚焦已配置关键词，
  每轮扫描完成后自动聚焦本轮关键词；更换关键词不会清除 `hits/archive.json`，可用「查看全部历史」
  一键回看旧关键词结果。趋势日期柱和版面「查看本版报道」按钮会让趋势、版面与报道使用同一结果集，
  并支持再次点击取消筛选；静态 `hits/index.html` 明确标注为历史汇总。
- **完整报社生命周期管理**：看板支持新增、编辑、试抓、停用、恢复、删除自定义报社，以及
  安全停用内置报社、编辑同名覆盖和恢复内置默认。`papers.custom.json` 新增向后兼容的顶层
  `disabled` 名称数组；`GET /api/papers` 新增 `items` / `disabled`，并增加幂等的
  `POST /api/papers/status`，停用不删除规则与历史归档。
- **指定目录导出**：CLI 新增 `--export-dir` / `--export-format csv|json`，看板新增
  「导出结果」对话框与 `POST /api/export`。服务端从归档重新应用受控筛选，以原子写入、
  安全唯一文件名和 CSV 公式注入防护保存 CSV / JSON；浏览器 CSV 下载作为兼容兜底。
- **看板工作台与结果阅读二次重构**：首屏改为「扫描配置 + 实时任务状态」，
  结果区改为独立筛选侧栏和渐进加载列表，版面图/趋势收敛到分析区；新增粘性导航、
  手机结果卡、扫描表单语义、更完整的键盘/读屏支持与网络错误提示（`static/*`）。
- **看板信息架构与任务状态重构**：常用的关键词、日期和「开始扫描」置顶，结果列表成为
  页面主体，并发/报社选择、筛选细项、运行日志和报社正则配置改为渐进展开；提升文字
  对比度、键盘焦点与进度条语义。新增统一前端 JSON 请求层。后台扫描任务改为线程安全
  快照，提供任务 ID、真实「报社 × 日期」进度、耗时和增量日志游标（`web_tasks.py`、
  `web.py`、`static/*`、`tests/test_web_tasks.py`）。

- **可复用报社适配 + 可视化版面图 + 一键启动抓取**：自定义报社配置新增 `custom`
  日期格式以及 `node_pattern` / `article_pattern` 链接规则；“试抓”同时验证入口、版面和
  样例文章。扫描器将 `<area>` / `mappingOriginal` 归一化为二维 `layout`，看板按版面
  绘制并高亮命中，缺坐标时明确降级而不伪造。新增 `dashboard.py --open --scan` 与
  `make start`，按 `state/web_config.json` 的上次设置打开页面并自动扫描；前端会恢复并
  跟踪启动时任务进度（`papers.py`、`scanner.py`、`web.py`、`static/*`、`scripts/*`、
  `tests/*`）。

- **看板「只扫选中报社」**：`/api/scan` 与 `/api/config` 新增可选字段 `only`（逗号分隔
  字符串，也接受字符串数组）；`validate_web_config` / `load_web_config` 缺省回落空串
  （= 全选），**旧配置文件仍可读**。看板「扫描设置」按 `GET /api/papers` 的 `effective`
  渲染多选 chips（默认全选、全选时也显式发送 `only`、0 家时禁用「立即扫描」），选择持久化进
  `state/web_config.json`（`web.py`、`static/*`、`tests/test_web.py`）。
- **看板空状态引导与「复制提交命令」**：`GET /api/hits` 新增 `has_web_config` /
  `default_keywords`；命中为 0 时命中列表显示引导（当前数据目录 + 一键扫描入口），
  全新环境额外提示默认关键词与默认扫「今天 + 昨天」。存在自定义 / 覆盖报社时「报社」卡片
  显示「复制提交命令」按钮（`navigator.clipboard` 不可用时回退为选中文本 + 手动复制提示）。
- **Makefile 新增 `unlock` / `papers-check`**：`unlock` 清理沙箱残留的 `.git/index.lock`；
  `papers-check` 校验 `papers.custom.json` 并打印合并后生效报社清单（标注 内置/覆盖/自定义）。

- **看板扫描支持「按日期范围」**：`/api/config` 新增可选字段 `mode`（`days` | `range`，缺省
  `days`）、`since` / `until`（`YYYY-MM-DD` 或 `null`）；`mode=days` 时给 `since/until`
  直接 400，`mode=range` 时校验两端合法、`since <= until`、跨度 ≤ `MAX_RANGE_DAYS`。
  `load_web_config` 对缺 `mode` 的旧配置文件回落 `days`，**向后兼容**。看板「扫描设置」
  改为「按天数 / 按日期范围」二选一 + 条件显示（日期默认 `until`=今天、`since`=今天减 1 天），
  前端就地校验且与后端一致（`web.py`、`static/*`）。
- **自定义报社**：新增 `src/newspaper_auto/papers.py`，读写项目根 `papers.custom.json`
  （配置含 `papers` 与顶层 `disabled`；加载、合并、校验和原子保存均集中管理），
  支持 `name` / `style` / `tpl` / `timeout` / `retries` / `node_exclude` 六字段校验，
  **同名覆盖内置、不重复则追加**，文件缺失/损坏/单条非法只警告不崩。`cli.select_papers`
  接受合并后的报社列表（未匹配仍回退全部）；`cli.main` 用
  `merge_papers(config.PAPERS, load_custom(find_project_root()))`。看板新增
  `GET /api/papers`、`POST /api/papers`、`POST /api/papers/status`、
  `POST /api/papers/delete`、`POST /api/papers/test` 五条路由与「报社」卡片（列表 + 来源标注 +
  新增/编辑/试抓/停用/恢复/删除，零 `innerHTML`）；
  试抓探测 `paper_probe` 可注入，默认实现直接 `scanner.scan_paper(dry=True)`，不写文件。
  `create_server` 增加 `paper_probe` / `papers_root` 供测试隔离。
- **本地看板支持配置与一键触发扫描**：`python3 -m newspaper_auto.web` 新增「扫描设置」卡片
  （关键词 / 天数 / 并发 + 「立即扫描」），由看板进程**进程内调用 `cli.main`** 触发一次真实扫描，
  日志实时回显、完成后自动刷新命中列表；扫描结果写回看板正在读的数据目录，配置持久化到
  `state/web_config.json`。新增四条路由：`POST /api/scan`（`202` / `409` / `400` / `403` / `413`）、
  `GET /api/scan`、`POST /api/config`、`GET /api/config`（缺失回落 `config.DEFAULT_*`）。
  扫描执行器 `scan_runner` 可注入（测试离线驱动），默认实现通过显式日志回调逐行推送并
  兜住 argparse 的 `SystemExit(2)`，不修改进程全局 stdout/stderr，也不走 `subprocess`。
  安全上落实 Host / Origin / Content-Type / body 大小四道校验，关键词只作 argv
  传递、绝不拼 shell，且不返回任何 CORS 头（`web.py`、`static/*`、`config.py`、`paths.py`、
  `tests/test_web.py`）。
- **南方日报 layoutData.mappingOriginal 多边形坐标解析与三级几何兜底**：
  新增 `parse.layout_mapping`，从版面页内嵌 `window.layoutData.mappingOriginal` 反引号模板提取多边形顶点，
  计算包围盒并归一化为百分比坐标；升级 `scanner.merge_geometry` 为三级互斥逻辑：
  1. `basis="area"`：来自 `<area>` 映射；
  2. `basis="mapping"`：来自 `mappingOriginal`（按 `(top, left, 出现顺序)` 排序编号，写 `top` / `bottom` / `index` / `total`，绝不编造 `x1..y2` 像素坐标）；
  3. `basis="order"`：前两者均无时的纯链接顺序兜底（写 `index` / `total`，不带 `top` / `bottom`）。
  控制台和报告可为南方日报带坐标版面正常输出 `上版 xx%`。
- **羊城晚报 per-paper 抓取参数覆盖**：`config.PAPERS` 支持可选的 `timeout` / `retries`
  键；羊城晚报设为 `timeout=60 / retries=4`，规避 GitHub runner（海外）上原始请求
  30s 才返回 `HTTP 000` 的临界失败（`config.py`；`scanner.py` 新增 `_paper_fetch`，
  无覆盖键或测试注入时原样透传）。
- **抓取失败可见化**：扫描统计新增 `sections_failed` / `articles_failed` / `failed_urls`
  （含代表性失败样本，最多 3 条）；Markdown 报告新增「抓取失败（缺报）」章节，
  并在「各家扫描情况」表新增「失败」列（`scanner.py`、`report.py`）。
- 部署文档新增「外部定时（VPS crontab）」：私有仓库下 GitHub 原生 `schedule`
  实测零触发，生产改由 crontab 调 `repository_dispatch` 触发
  （`README.md`、`docs/交付说明-*.md`）。
- **Windows 本机调度**：新增 `scripts/run_scan.ps1`（对标 `run_scan.sh`：同样的参数
  透传、位置参数裸关键词、`NEWSPAPER_PYTHON` / `NEWSPAPER_DAYS` / `NEWSPAPER_KW`
  环境变量、`logs\scan-YYYY-MM-DD.log` 日志、`NEWSPAPER_PYTHON → .venv → PATH python`
  解释器解析顺序与 `--no-state` 无状态语义）与 `scripts/install_task_scheduler.ps1`
  （`install [HH:mm ...]` / `uninstall` / `status` / `run-now`，任务名 `NewsPaperAuto`，
  幂等，含 `-Help`）。
- `README.md` 新增「调度方案对照表」与「Windows 本机运行」章节（含不依赖 `.ps1` 的
  `schtasks` 兜底命令）。
- **新增人民日报**：`config.PAPERS` 增加人民日报（人民网 pc 数字报，
  `http://paper.people.com.cn/rmrb/pc/layout/{YYYYMM/DD}/node_01.html`）。解析器新增
  `<!--enpcontent-->` 正文通道（人民网系），并支持可选的 per-paper `node_exclude`
  过滤（排除版面页里 `/rmrb/pad/` 手机版镜像，避免重复扫描）。
- **本地「点击即用」启动器**：`scripts/scan.command` / `dashboard.command`（macOS 双击）
  与 `scripts/scan.cmd` / `dashboard.cmd`（Windows），一键扫一次或打开本地看板。
- **本地看板筛选增强**：新增「全部 / 今天 / 近 7 天 / 近 30 天」**日期区间快捷按钮**、
  起止日期倒置提示，以及**按当前筛选导出 CSV**（含报社/日期/版面/标题/链接/关键词，
  带 BOM 可直开 Excel，并对 `=+-@` 开头的单元格做公式注入防护）。
- **命中输出补「版面」**：命令行控制台的命中行与报告「其余已知命中」列表都带上版面
  （报纸中的相对位置）；`--json` / `--csv` 原本已含 `section` 字段。
- **CLI 显式日期范围**：新增 `--since` / `--until`（`YYYY-MM-DD`，两端都含），与 `--days`
  互斥（同时给报错退出码 2）；只给 `--since` 时终点取今天，只给 `--until` 时起点等于终点；
  区间倒置或跨度超过 62 天都以退出码 2 拒绝，不再静默扫出空结果。`dates` 仍保持从新到旧，
  `--days` 行为不变（CI / launchd 继续可用）。口径：`cli.resolve_dates`。
- **版本内位置（area coords）**：新增 `parse.area_rects`，从版面页 `<area>` 图像映射取文章的
  包围盒坐标（兼容 `coords="x1,y1,x2,y2"` 矩形与站点实际使用的 `shape="polygon"` 多边形，
  标签大小写/小数坐标均容错）；`scanner` 用该版所有 area 的 `max(y2)` 归一化，产出
  `geometry = {x1,y1,x2,y2,top,bottom,index,total,basis:"area"}`，不下载版面图。
  旧 `hits/archive.json` 无该字段也能正常渲染。控制台命中行/ Markdown 报告 / HTML 汇总 /
  本地看板四处都展示 `版内 k/m · 上版 xx%`，缺字段静默降级。
- **无 `<area>` 版面的顺序兜底**：南方日报等纯链接式版面（页面 0 个 `<area>`）此前拿不到
  任何位置信息，现在按**版面页链接出现顺序**给出 `版内 k/m`（`scanner.merge_geometry` 产出
  `geometry = {index,total,basis:"order"}`，**绝不编造** `top` / `bottom` / `x1..y2`）；有
  `<area>` 的版面行为不变，仅额外标记 `basis:"area"`。看板 `web._normalize_geometry` 的必需
  字段由 `{index,total,top}` 放宽为 `{index,total}` 并透出可选 `basis`，旧归档的无/残缺
  geometry 仍返回 `None`；展示上有坐标才带 `上版 xx%`，顺序兜底只显示 `版内 k/m`。
- **版面号归一化**：新增 `section_no` 字段（`第02版` / `VD02` / `A01` 等），原 `section`
  字符串保持不变（向后兼容），供排序/统计使用。

### 修复

- **看板后端二次加固**：扫描任务终态改为幂等、忽略迟到日志，成功任务保证显示
  100% 进度；在任务锁内二次校验报社并深拷贝入参，消除配置竞态。未知路由/方法统一
  返回带安全头的 JSON 错误；自定义报社拒绝重名、未知配置版本、非法名称与无效 URL。
- **CSV 导出安全**：CLI 与看板导出均防护表格公式注入，对以 `= + - @` 或制表/
  回车开头的文本单元格前置单引号，数值字段保持原语义（`report.py`）。
- **CI 前端/启动器防回归**：Linux 矩阵增加 `api.js` / `app.js` 语法检查，Linux 与
  Windows job 都将 macOS 双击启动器纳入 Shell 语法检查（`.github/workflows/ci.yml`）。
- **看板后端与配置写入加固**：非零扫描退出不再显示为成功；单边日期范围先归一化再计算
  进度；拒绝未知报社后再启动；扫描启动异常会释放任务锁。自定义报社写接口改用严格读取，
  原 JSON 损坏时返回冲突并保留文件；扫描期间禁止修改规则。看板拒绝非环回监听，并新增
  CSP / 防 iframe / MIME 与 Referrer 安全头；远程查看应使用 SSH 隧道。
- **看板筛选与轮询一致性**：修复刷新后 chip 外观与实际筛选状态不一致、取消全部关键词
  反而显示全部结果、轮询重叠导致日志重复/游标倒退，以及跨任务沿用旧游标的问题；搜索
  增加轻量防抖，大结果明确标注 500 条展示上限并限制一次渲染的版面/趋势数量。

- **防止 HTTP 200 静默漏报**：入口页未解析到 `node_*` / `content_*`、
  所有版面均无文章，或所有文章抓取/解析失败时，现会记录结构异常并
  以非零退出，不再把反爬挑战页或站点改版误报为「无命中」。
- **CLI 参数校验收紧**：`--days` 限定 1~62，`--workers` 限定 1~16，
  空关键词与超过 10 个关键词在联网前直接拒绝，重复关键词自动去重。
- **自定义链接模板校验**：`tpl` 只允许使用不带 format spec/conversion 的
  `{a}`，未闭合花括号或 `{other}` 等额外字段会在保存时被拒绝。
- **状态与归档一致性**：JSON / HTML / Markdown 改为同目录临时文件原子替换；
  状态提交阶段新增跨平台跨进程锁，`seen.json` 最后写入，避免报告写失败后
  文章却被提前标记为已见。损坏的内层状态/归档记录会被安全跳过。
- **HTML 属性解析增强**：链接与 `<area>` 改用标准库 `HTMLParser`，兼容
  单引号、大小写与无引号属性。
- **看板零安装入口**：新增根目录 `dashboard.py`，与 `daily_scan.py` 一样自动
  加载 `src/`；Makefile 与 macOS / Windows 双击启动器统一改用该入口，
  未执行 `pip install -e .` 也能打开看板。
- **Actions 运维修正**：原生 schedule 避开整点高峰；修正「私有仓库不支持
  schedule」的过度概括，并将 `checkout` / `setup-python` 固定到完整 commit SHA。
- **CLI 入口失败不再伪装成功**：存在 `entry_code != 200` 时 `cli.main()` 返回退出码 1，
  让 GitHub Actions 步骤变红；文章级失败不影响退出码（`cli.py`）。
- **Actions 失败时不再丢结果**：`scan.yml` 的「提交结果」步骤改为 `if: always()`，
  扫描失败也提交已落盘的 `hits/`、`state/`；提交信息在失败时追加 `[含失败]` 后缀。
- 报告表格新增「失败」列，修正此前失败不可见、失败时仍显示「无命中」的问题（`report.py`）。
- **纠正「launchd 与 Actions 必须二选一」的错误结论**：`scripts/run_scan.sh` 自诞生起
  即无条件携带 `--no-state`，本机调度路径**不读写 `state/seen.json`、不产 `hits/*`**，
  因此与 Actions 互不覆盖、**可以并存**（本机管「即时可见」，Actions 管「归档 + 增量」）。
  同步修正 `README.md` 与 `.github/workflows/scan.yml` 注释中的旧说法。
- `http.py` 的 curl 缺失提示改为跨平台表述（Windows 10 1803+ 自带 `curl.exe`）。
- **⚠️ 修复测试夹具里硬编码的机器相关临时路径（Linux CI 因此连红三次）**：
  `tests/conftest.py` 的 `tmp_path` 覆盖夹具曾把根目录写死成某台 macOS 开发机的
  临时目录，Linux runner 无法创建，导致 41 个 `test_web.py` 用例全部 error
  （Windows runner 以管理员运行时曾侥幸通过）。现改为
  `Path(os.environ.get("NEWSPAPER_PYTEST_TMP") or tempfile.gettempdir()) / "pytest-np"`：
  默认走**各平台的系统临时目录**，受限环境（如 Sandbox）可用 `NEWSPAPER_PYTEST_TMP`
  显式指定可写目录，**不再出现任何机器相关绝对路径**。
- **新增 `.gitattributes`，修正 Windows 侧脚本的行尾（此前 `scripts/*.cmd` 是纯 LF）**：
  `cmd.exe` 对 LF-only 批处理「通常能跑」，但 `goto` 标号与 `for`/`if` 多行块在 LF 下可能失败，
  属平时看不出、一改就翻车的坑。现约定：
  - `*.cmd` / `*.bat` / `*.ps1` → `text eol=crlf`（仓库内仍以 LF 存储，签出到工作区才是 CRLF）；
  - `*.sh` / `*.command` → `text eol=lf`（CRLF 会让 bash 报 `\r: command not found`，
    Git Bash / WSL / launchd 全中招）；
  - **故意不加 `* text=auto`**：`tests/fixtures/rmrb_article_30182606.html` 与
    `legacy/result.csv` 存的是真实站点的原始 CRLF 快照，全局规则会把它们改写掉。
  验证：`git ls-files --eol scripts/` 应显示 4 个 Windows 脚本为 `i/lf w/crlf attr/text eol=crlf`，
  两个 Unix 脚本为 `w/lf`。
- **`--only` 未匹配不再静默回退全部**：`cli.select_papers` 改为返回 `(选中, 未识别)`；
  部分匹配时打印 `⚠️ --only 未识别：…（可选：…）` 后继续，**零匹配时以退出码 2 报错退出**、
  绝不开扫（此前 `--only 光明日` 这类打错会静默扫全部六家，白等且无任何提示）。
- **测试稳定性**：`test_scan_post_runs_to_done_and_logs` 用 `threading.Event` 阻塞 runner，
  消除「runner 先于 202 响应跑完」的竞态（`tests/test_web.py`）。

### 变更

- **前端状态改为严格「action + reducer」单向数据流**：新增 `static/store.js`——`A.*` action 常量、
  **纯** `reducer(state, action)`（不读写外部变量，Set 一律产生新实例）与 `createStore`
  （`getState / dispatch / subscribe`）。`app.js` 只经 `store.dispatch(action)` 变更状态，
  事件处理器不再直接改 `state`、也不再各自散落 `render()`；默认 140ms 防抖渲染
  （`{immediate:true}` 同步、`{silent:true}` 由调用方渲染）。数据加载 `LOAD_DATA` 的
  reset / preserve 分支也移入 reducer。纯 reducer 单测 `tests/frontend/store.test.mjs`（11 例）、
  DOM 回归 `tests/frontend/run-dom.mjs`（7 例）守护。
- **看板装配加内存缓存**：`build_dashboard` 以归档 / `seen.json` / `web_config.json` 的
  `(mtime_ns, size)` 为指纹缓存装配结果，未变化时不再每次请求都全量解析 + 排序；显式传入
  `now` 时跳过缓存（`web.py`）。archive/seen 完整迁移到 `sqlite3` 列为可选后续项。
- **静态资源 ETag / Last-Modified / gzip**：`/static/*` 与首页返回弱 ETag，命中
  `If-None-Match` 回 `304`；大于 1KB 且客户端接受时返回 gzip（尊重 `q=0`，带 `Vary`），
  静态缓存改 `no-cache`（轻量校验），API 仍 `no-store`（`web.py`）。
- **本地看板不再是纯只读**：`web.py` 模块 docstring、`--help` 描述与看板 footer 里「只读、
  不触发抓取」的说法全部更正；看板新增触发抓取能力，会写入 `state/` 与 `hits/`。
- **每日触发时间统一为 10:00**：本机安装脚本默认值（`install_launchd.sh` /
  `install_task_scheduler.ps1`）、`scan.yml` 的 `schedule`（`0 2 * * *` UTC = 北京 10:00）
  与 README 的 VPS crontab 示例（UTC `0 2 * * *` / 北京 `0 10 * * *`）全部对齐；
  `docs/交付说明-*.md` 同步。
- **看板默认端口 8787 → 8788**：8787 常被本机其他开发服务占用；`make web`、
  `scripts/dashboard.command` / `.cmd` 在默认端口被占时自动回退到系统分配的空闲端口并打印
  实际地址。端口可用 `--port` / `NEWSPAPER_PORT` 覆盖。
- **`make lint` / `fmt` / `check` 不再往仓库写 `.ruff_cache`**：`RUFF_CACHE_DIR` 默认指向
  临时目录（可外部覆盖），`pytest` 加 `PYTHONDONTWRITEBYTECODE=1`（`Makefile`）。
- **Windows 启动器对齐 macOS**：`scripts/scan.cmd` / `dashboard.cmd` 优先
  `.venv\Scripts\python.exe`（可 `NEWSPAPER_PYTHON` 覆盖），看板端口占用时回退 `--port 0`，
  删除空转的 `set NEWSPAPER_PYTHON=…`，失败时 `pause` 留痕（保持 CRLF 行尾）。

### 文档

- `README.md` 补齐全新 clone 后 macOS / Linux / Windows 的零安装启动步骤、运行前检查与
  标准 `venv + pip` 开发环境命令；CI 在 Linux / Windows 安装依赖前先验证两个
  零安装入口。VPS 示例改用通用 SSH 占位主机。历史交付文档移除开发机用户名、
  绝对路径与私有运行时痕迹，并新增公开文档机器路径回归测试。
- `README.md` 新增「自定义报社」章节（文件位置、字段表、同名覆盖语义、**必须提交推送才进
  CI**、解析兜底对换站点的诚实提醒、SSRF 能力边界），并更新看板「扫描设置」（日期范围模式、
  筛选日期框只筛列表）与路由表（+4 条 papers 路由）。
- `README.md`：launchd 章节明确「本机与 Actions 可并存 + 本机无状态运行」，并给出
  不再需要本机任务时的 `./scripts/install_launchd.sh uninstall` 卸载命令，新增
  「外部定时（VPS crontab）」章节（完整 crontab 行、时区换算、PAT 最小权限、
  日志落盘与排查步骤）。
- `docs/交付说明-v0.2-工程化改造.md`、`docs/交付说明-无状态模式与定时任务.md`：
  同步外部定时路径、本机与 Actions 可并存（本机无状态运行）、抓取失败不再静默等结论。
- `README.md` 第一节表格补齐「运行 / 调度」两行，明确生产 = GitHub Actions + 外部触发，
  并回答「是否需要打包 exe：不需要」及两条理由；新增「调度方案对照表」与
  「Windows 本机运行」章节。
- 「五家」表述更新为「六家」（`README.md`、`daily_scan.py`、`cli.py`、`__init__.py`、
  `web.py`、`report.py`、`pyproject.toml`、`Makefile`、`scripts/run_scan.*`、看板前端静态页），
  README 入口表补入人民日报。
- `README.md` 新增「故障排查」（`index.lock` / 端口占用 / 看板可写 / `--only` 三态 /
  扫描不可中断），更新默认端口与覆盖方式、看板 `only` 配置说明与「停止扫描暂不支持」的边界说明。

## [0.2.0] - 2026-09-22

### 新增

- **定时任务运行时间可自定义**：`install_launchd.sh install [HH:MM ...]`
  支持指定一个或多个时间点（每天各跑一次），也可用 `NEWSPAPER_TIMES` 环境变量；
  非法时间格式直接报错退出。
- 拆分为 `src/newspaper_auto/` 包（`config` / `paths` / `http` / `parse` /
  `scanner` / `state` / `report` / `cli`），`daily_scan.py` 保留为同名兼容入口，
  既有命令与定时任务无需改动。
- **本地只读看板**：`python3 -m newspaper_auto.web`（stdlib `http.server`，零依赖、
  无构建、无 CDN，默认只监听 `127.0.0.1`）。支持概览统计、按日期分布柱状图、
  按报社 / 关键词 / 日期筛选与搜索、点击跳原文；只读，不写文件、不触发抓取。
- 76 个**离线**单元测试（`tests/`，含 HTML fixture 与 127.0.0.1 回环 HTTP 测试），
  不访问网络。
- `pyproject.toml`（运行时依赖刻意保持为 0）、`Makefile`、ruff 配置、
  GitHub Actions CI（Python 3.9 / 3.12 / 3.13）、`LICENSE`。
- 新增 `--data-dir` 与 `--version`；数据目录支持 `NEWSPAPER_DATA_DIR` 环境变量。

### 修复

- `scripts/run_scan.sh`：`--kw 关键词` 不再被误当作裸关键词展开成
  `--kw --kw …`，从而不再报 `argument --kw: expected one argument`
  （`logs/scan-2026-09-22.log` 里的那次失败即此问题）。
- `scripts/run_scan.sh`：去掉写死的 Python 绝对路径，改为
  `NEWSPAPER_PYTHON` → 项目 `.venv` → `PATH` 依次探测，换机可用。
- `scripts/run_scan.sh`：关键词去首尾空白改用 `sed`，避免 `xargs` 误解析引号。
- `state/seen.json` 改为原子写入（临时文件 + `os.replace`），避免中断产生半截 JSON。

### 变更

- launchd 配置改为模板 `com.quark.newspaperauto.plist.template`，由
  `install_launchd.sh` 在安装时替换真实路径，无需手工改 plist。

## [0.1.0] - 2026-09-22

### 新增

- 五家报社（光明日报 / 南方日报 / 羊城晚报 / 中国教育报 / 湛江日报）官网数字报
  直采，标题 + 正文全文匹配，绕开搜索引擎收录延迟与第三方 RSS 空白。
- 增量状态（`state/seen.json`）与 Markdown / HTML 报告。
- 无状态运行 `--no-state` 与结构化输出 `--json` / `--csv`（向后兼容）。
- 基于 macOS `launchd` 的每日 07:00 定时任务。
- curl 强制 HTTP/1.1，修复光明日报数字报在 HTTP/2 下断连导致的抓取失败。
