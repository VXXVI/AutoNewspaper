# 变更记录

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与
[语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### 新增

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

### 修复

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

### 变更

- **每日触发时间统一为 10:00**：本机安装脚本默认值（`install_launchd.sh` /
  `install_task_scheduler.ps1`）、`scan.yml` 的 `schedule`（`0 2 * * *` UTC = 北京 10:00）
  与 README 的 VPS crontab 示例（UTC `0 2 * * *` / 北京 `0 10 * * *`）全部对齐；
  `docs/交付说明-*.md` 同步。

### 文档

- `README.md`：launchd 章节明确「本机与 Actions 可并存 + 本机无状态运行」，并给出
  不再需要本机任务时的 `./scripts/install_launchd.sh uninstall` 卸载命令，新增
  「外部定时（VPS crontab）」章节（完整 crontab 行、时区换算、PAT 最小权限、
  日志落盘与排查步骤）。
- `docs/交付说明-v0.2-工程化改造.md`、`docs/交付说明-无状态模式与定时任务.md`：
  同步外部定时路径、本机与 Actions 可并存（本机无状态运行）、抓取失败不再静默等结论。
- `README.md` 第一节表格补齐「运行 / 调度」两行，明确生产 = GitHub Actions + 外部触发，
  并回答「是否需要打包 exe：不需要」及两条理由；新增「调度方案对照表」与
  「Windows 本机运行」章节。

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
