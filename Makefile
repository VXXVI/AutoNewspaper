# 可扩展数字报关键词监测 —— 常用任务
#
# 优先使用项目虚拟环境 .venv 中的 Python，未创建时退回系统 python3。

SHELL := /bin/bash
PY := $(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; else echo python3; fi)

# ruff 需要可写缓存目录；agent 沙箱禁止在仓库里落 .ruff_cache，故默认指向临时目录。
# 允许外部覆盖：make lint RUFF_CACHE_DIR=/path/to/cache
export RUFF_CACHE_DIR := $(if $(RUFF_CACHE_DIR),$(RUFF_CACHE_DIR),$(shell mktemp -d))
# 测试不落 __pycache__（避免污染工作区）
export PYTHONDONTWRITEBYTECODE := 1

# 看板端口：NEWSPAPER_PORT 环境变量优先，否则用 web.py 的默认 8788。
WEB_PORT ?= $(if $(NEWSPAPER_PORT),$(NEWSPAPER_PORT),8788)

.DEFAULT_GOAL := help

.PHONY: help install test lint fmt check dry scan json csv web start run-now plist papers-check unlock clean

help:  ## 显示本帮助
	@grep -E '^[a-zA-Z_-]+:.*## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*## "}; {printf "  %-13s %s\n", $$1, $$2}'

install:  ## 创建 .venv 并安装开发依赖（需要 uv）
	uv venv
	uv pip install -e ".[dev]"

test:  ## 运行单元测试
	$(PY) -m pytest

lint:  ## 代码检查（ruff，缓存写入临时目录）
	$(PY) -m ruff check .

fmt:  ## 自动格式化并修复可自动修复的问题
	$(PY) -m ruff format .
	$(PY) -m ruff check . --fix

check: lint test  ## 提交前自检（lint + test）

dry:  ## 只探测已启用报社的入口与版面，不抓文章
	$(PY) daily_scan.py --dry

scan:  ## 无状态扫描（今天，不产生历史文件）
	$(PY) daily_scan.py --days 1 --no-state

json:  ## 无状态扫描并以 JSON 输出（配 jq 使用）
	$(PY) daily_scan.py --days 1 --no-state --json

csv:  ## 无状态扫描并以 CSV 输出
	$(PY) daily_scan.py --days 1 --no-state --csv

web:  ## 启动本地看板（可在页面一键触发扫描；默认 http://127.0.0.1:8788，被占用时自动改用空闲端口）
	@$(PY) dashboard.py --port $(WEB_PORT) --open || { \
		echo; \
		echo "端口 $(WEB_PORT) 不可用，改用系统自动分配的空闲端口重试…"; \
		$(PY) dashboard.py --port 0 --open; \
	}

start:  ## 打开看板，并按上次保存的页面设置自动扫描
	@$(PY) dashboard.py --port $(WEB_PORT) --open --scan || { \
		echo; \
		echo "端口 $(WEB_PORT) 不可用，改用系统自动分配的空闲端口重试…"; \
		$(PY) dashboard.py --port 0 --open --scan; \
	}

plist:  ## 打印将要安装的 launchd plist
	./scripts/install_launchd.sh print

papers-check:  ## 校验 papers.custom.json 并打印合并后的生效报社清单
	@$(PY) -c "from pathlib import Path; from newspaper_auto import config, papers; \
root = Path('.').resolve(); \
custom = papers.load_custom_for_update(root); \
merged = papers.merge_papers(config.PAPERS, custom); \
builtin = {p['name'] for p in config.PAPERS}; \
custom_names = {p['name'] for p in custom}; \
print('生效报社清单（共 %d 家）：' % len(merged)); \
[print('  - %-6s [%s]' % (p['name'], '覆盖' if p['name'] in builtin else '自定义') if p['name'] in custom_names else '  - %-6s [内置]' % p['name']) for p in merged]"

unlock:  ## 删除 agent 沙箱可能残留的 .git/index.lock（git 的 unlink 被沙箱拒绝时会留下）
	@if [ -e .git/index.lock ]; then \
		echo "发现残留锁：$$(pwd)/.git/index.lock"; \
		rm -f .git/index.lock && echo "已删除，git add/commit 可正常工作。"; \
	else \
		echo "未发现残留锁（.git/index.lock 不存在）。"; \
	fi

clean:  ## 清理缓存与构建产物（不动 hits/ state/）
	rm -rf .pytest_cache .ruff_cache build dist src/*.egg-info newspaper_auto-*
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
