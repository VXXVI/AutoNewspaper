# 方案原理与已知边界

> 本文由原 `README.md` 迁出，供需要理解抓取链路、版面定位与能力边界的读者参考。
> 安装与日常用法见仓库根 [README.md](../README.md)。

## 三段式抓取

六家报社的数字报结构统一，三段式（五家基于**方正系电子报系统**；人民日报属**人民网系**，
正文标签不同，解析器一并支持）：

```
第 1 版页  node_01.htm(l)   →  含当日全部版面链接（node_*）
单个版面页 node_*.htm(l)    →  含本版全部文章链接（content_*）
单篇文章页 content_*.htm(l) →  含标题 + 正文（`<founder-content>` 或 `<!--enpcontent-->`）
```

流程：**第 1 版 → 全部版面 → 全部文章 → 标题 + 正文全文匹配关键词**。

## 版面位置（几何）三级互斥兜底

命中结果除「第几版 + 版名」外，还会给出**版内位置**
（形如 `[湛江日报 · 第1版：要闻 · 版内 2/7 · 上版 38%]`）：`index/total` 是该版按纵坐标
排序的第几条，`上版 xx%` 是文章顶边在版面图上的纵向占比。几何定位采用三级互斥兜底：

1. **`<area>` 图像映射（`basis="area"`）**：湛江日报等站点直接从 HTML `<area>` 提取多边形
   或矩形包围盒坐标；
2. **内嵌 JS 映射（`basis="mapping"`）**：南方日报等新版数字报从内嵌
   `window.layoutData.mappingOriginal` 解析多边形顶点并按 `(top, left, 出现顺序)` 排序，
   同样输出真实纵向百分比 `top`/`bottom`（形如 `[南方日报 · QD01版：阳春视窗 · 版内 2/4 · 上版 8%]`）；
3. **顺序兜底（`basis="order"`）**：无任何几何数据的纯链接版面按版面链接出现顺序给出
   `版内 k/m`，**不带纵向百分比**（形如 `[南方日报 · A01版：要闻 · 版内 3/9]`）。

解析器从版面页取包围盒并归一化，**无需下载版面图**。实测 2026-09-23：光明日报、羊城晚报、
中国教育报、湛江日报、人民日报五个站点的版面页都有该映射，南方日报没有。没有 `<area>` 时
不静默省略，而是退一步按链接顺序给出 `版内 k/m`（只写 `index` / `total` / `basis="order"`，
不编造任何坐标）。旧归档缺少该字段也能正常渲染。

## 六家入口（第 1 版地址模板）

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

## 为什么不用 Google News

Google News 是**标题级检索**，正文提及全漏。实测某日的 4 条真报道，
**标题里一个都没有目标机构名**——机构名全在正文里（如「来自示例大学、
示例实验室的专家提出意见建议」）。且 Google 对湛江日报的收录早已停滞，
而湛江日报恰是命中最多的报纸。官网数字报没有这些问题：报纸上线即可抓、
链接就是原文、全文匹配不漏正文。

（更早的 Google News 方案保留在 `legacy/`，仅作历史参考。）

## 已知边界

- **数字报 ≠ 即时新闻**。报社官网的即时新闻频道（光明网、南方网等）更新更快、条数更多，
  但不等于报纸版面。如需纳入，可在此基础上追加一路采集。
- **当天报纸上线时间不固定**（一般凌晨到上午）。默认扫「今天 + 昨天」，
  当天没上线也不会漏，第二天会自动补上。
- **图片版正文抓不到**。若某版整版图片排版（少见），文字提取会失败；六家实测均有文字版。
- **只匹配字面关键词**。默认精确匹配「人工智能」；覆盖同义词或相关表述可用 `--kw`
  多传几个。改词不会清除 `hits/archive.json`，看板可用「查看全部历史」回看旧关键词结果。
- ⚠️ **光明日报必须走 HTTP/1.1**：`epaper.gmw.cn` 的服务器在 HTTP/2 下会直接断连
  （curl 报 `52 Empty reply from server`）。`http.py` 现在的**主路径是标准库 `urllib`
  （默认 HTTP/1.1）**，天然规避该问题；系统 `curl` 仅作兜底且显式加 `--http1.1`。
  **若把抓取逻辑移植到 requests / aiohttp / Node fetch，务必先验证该站 h2 是否可用**，
  否则会静默漏掉光明日报（其余站点不受影响）。
  - 诊断手法：同一 URL 分别用 `curl --http1.1`、`curl --http2`、
    `python3 -c "import urllib.request;..."` 各抓一次对比。
- **对报社站点是轻量访问**：每天每站约 100~200 个请求、并发 8，属正常阅读量级。
  默认并发 8；若某家不稳可降到 4~5（`--workers 5`）。
- **历史归档默认只增不删**：`hits/archive.json` 是审计用的全量历史，默认不做滚动清理；
  长期运行后可用 `--archive-keep-days N` 只保留最近 N 天（0 = 不限，默认）。看板
  `GET /api/hits` 会一次性下发整份归档，记录极大时首屏会变慢，届时应按需裁剪或改为按月分片。
- **看板「取消扫描」是协作式**：扫描在看板进程内的线程里直接调用 `cli.main`，Python
  线程无法安全强杀。取消后**排队中的版面/文章抓取会立即停止**，**在途的单个 HTTP 请求
  无法打断**；整体**不写入** `state/` 与 `hits/`，任务状态标记为 `cancelled`（退出码 130）。
  要立即停止整个进程时，Ctrl+C / 关窗口即可。详见 [dashboard.md](dashboard.md)。

## 项目结构与实现职责

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
│   ├── http.py                   #   抓取层：标准库优先（HTTP/1.1）+ curl 兜底
│   ├── parse.py                  #   HTML 解析与关键词匹配（纯函数）
│   ├── scanner.py                #   扫描编排（入口→版面→文章）
│   ├── state.py                  #   增量状态读写与「新增」判定
│   ├── report.py                 #   Markdown / HTML / JSON / CSV 渲染
│   ├── exporter.py               #   指定目录导出（CSV / JSON）
│   ├── web.py                    #   本地看板 + 一键触发扫描（stdlib http.server）
│   ├── web_tasks.py              #   看板后台任务状态（线程安全快照 / 取消 / 日志游标）
│   ├── static/                   #   看板前端（原生 HTML/CSS/JS ES Module，无构建）
│   │   ├── app.js                #     ES Module 入口（渲染 / 事件 / 扫描编排）
│   │   ├── api.js                #     统一 JSON 请求层
│   │   ├── store.js              #     action + 纯 reducer + store（单向数据流）
│   │   └── format.js             #     纯函数工具（日期 / CSV / 关键词 / 位置文本）
│   ├── cli.py                    #   参数解析与主流程
│   └── exe.py                    #   单文件 exe / newspaper-auto 统一入口（子命令分发）
├── packaging/                    # Windows exe 打包（PyInstaller spec + 双击启动器 + 使用说明）
├── papers.custom.json            # 自定义报社配置（项目根，有意入库，见 custom-papers.md）
├── .github/workflows/            # CI 与每日扫描 workflow（见 production-deployment.md）
├── tests/                        # 离线回归测试 + HTML fixture（前端测试在 tests/frontend/）
├── scripts/                      # 启动与定时脚本
│   ├── run_scan.sh / run_scan.ps1        #   macOS/Linux 与 Windows 的无状态启动脚本（自动选解释器、写日志）
│   ├── scan.command / scan.cmd           #   双击「扫一次」薄封装（转调 run_scan.*）
│   ├── dashboard.command / dashboard.cmd #   双击「打开看板」薄封装（转调 launch_dashboard.py）
│   ├── launch_dashboard.py               #   看板端口回退逻辑的唯一实现
│   ├── install_launchd.sh                #   macOS launchd 幂等安装/状态/立即运行/卸载
│   ├── com.quark.newspaperauto.plist.template  # launchd 模板
│   ├── install_task_scheduler.ps1        #   Windows 任务计划程序幂等安装/状态/立即运行/卸载
│   ├── vps_dispatch.sh                   #   VPS crontab 触发器（打 repository_dispatch）
│   └── build_windows.ps1                 #   Windows 单文件 exe 构建（PyInstaller + 冒烟 + 打包 zip）
├── docs/                         # 部署、调度、架构、看板、自定义报社说明
└── legacy/                       # 已废弃的 Google News 旧方案（仅作参考）
```

运行产物 `hits/`、`state/`、`logs/` 不入库，由程序按需创建（见
[production-deployment.md](production-deployment.md) 的 `data` 分支说明）。

