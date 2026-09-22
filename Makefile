# 五家报社数字报关键词监测 —— 常用任务
#
# 优先使用项目虚拟环境 .venv 中的 Python，未创建时退回系统 python3。

SHELL := /bin/bash
PY := $(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; else echo python3; fi)

.DEFAULT_GOAL := help

.PHONY: help install test lint fmt check dry scan json csv run-now plist clean

help:  ## 显示本帮助
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  %-10s %s\n", $$1, $$2}'

install:  ## 创建 .venv 并安装开发依赖（需要 uv）
	uv venv
	uv pip install -e ".[dev]"

test:  ## 运行单元测试
	$(PY) -m pytest

lint:  ## 代码检查（ruff）
	$(PY) -m ruff check .

fmt:  ## 自动格式化并修复可自动修复的问题
	$(PY) -m ruff format .
	$(PY) -m ruff check . --fix

check: lint test  ## 提交前自检（lint + test）

dry:  ## 只探测五家入口与版面，不抓文章
	$(PY) daily_scan.py --dry

scan:  ## 无状态扫描（今天，不产生历史文件）
	$(PY) daily_scan.py --days 1 --no-state

json:  ## 无状态扫描并以 JSON 输出（配 jq 使用）
	$(PY) daily_scan.py --days 1 --no-state --json

csv:  ## 无状态扫描并以 CSV 输出
	$(PY) daily_scan.py --days 1 --no-state --csv

plist:  ## 打印将要安装的 launchd plist
	./scripts/install_launchd.sh print

clean:  ## 清理缓存与构建产物（不动 hits/ state/）
	rm -rf .pytest_cache .ruff_cache build dist src/*.egg-info
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
