# 变更记录

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与
[语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

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
