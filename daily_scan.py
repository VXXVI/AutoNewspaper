#!/usr/bin/env python3
"""六家报社数字报 · 关键词每日增量监测（兼容入口）。

实现已拆分至 ``src/newspaper_auto/`` 包；本文件仅保留同名命令行入口，
以确保既有用法（README、``scripts/run_scan.sh``、launchd 定时任务）继续可用。

原理
----
六家报社的数字报（= 报纸电子版）结构统一（五家为方正系，人民日报属人民网系）：

    第 1 版 页   →  含当日全部版面链接   (node_*.htm / node_*.html)
    单个版面页   →  含本版全部文章链接   (content_*.htm / content_*.html)
    单篇文章页   →  含标题 + 正文（<founder-content> 或 <!--enpcontent--> 内）

所以可以直接做到「当日全版面 → 全文章 → 标题 + 正文全文匹配关键词」，
正文里提一句校名也能命中，不依赖 Google/Bing 的收录与延迟。

六家入口（第 1 版）：
    光明日报   https://epaper.gmw.cn/gmrb/html/layout/{YYYYMM/DD}/node_01.html
    南方日报   https://epaper.nfnews.com/nfdaily/html/{YYYYMM/DD}/node_A01.html
    羊城晚报   http://ep.ycwb.com/epaper/ycwb/html/{YYYY-MM/DD}/node_1.htm
    中国教育报 https://paper.jyb.cn/zgjyb/html/{YYYY-MM/DD}/node_1.htm
    湛江日报   http://paper.gdzjdaily.com.cn/html/{YYYY-MM/DD}/node_1.htm
    人民日报   http://paper.people.com.cn/rmrb/pc/layout/{YYYYMM/DD}/node_01.html

用法
----
    python3 daily_scan.py                    # 扫今天+昨天，只报「新增」命中
    python3 daily_scan.py --days 7           # 往前回溯 7 天
    python3 daily_scan.py --kw 人工智能          # 换关键词（可多次指定）
    python3 daily_scan.py --full             # 忽略增量记录，全量重扫重报
    python3 daily_scan.py --workers 8        # 并发数（默认 8）
    python3 daily_scan.py --only 光明日报,南方日报
    python3 daily_scan.py --dry              # 只探测入口与版面，不抓文章
    python3 daily_scan.py --no-state         # 无状态运行，不读写状态与报告文件
    python3 daily_scan.py --json             # 命中结果以 JSON 数组输出到 stdout
    python3 daily_scan.py --csv              # 命中结果以 CSV 格式输出到 stdout
    python3 daily_scan.py --export-dir ./exports              # 本次命中另存 CSV
    python3 daily_scan.py --export-dir ./exports --export-format json
    python3 daily_scan.py --archive-keep-days 730             # 归档只保留最近 730 天（默认 0 = 不限）

输出
----
    state/seen.json        增量状态（记录已判定过的文章 URL）
    hits/<日期>.md         每次运行的报告
    hits/index.html        历史命中汇总（可浏览）
    <导出目录>/newspaper-hits-*.csv|json  可选的本次命中导出
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "src")
if os.path.isdir(_SRC) and _SRC not in sys.path:
    sys.path.insert(0, _SRC)

# 把「项目根」（配置层）钉死为本文件所在目录：从仓库外调用时（如
# `python3 /path/AutoNewspaper/daily_scan.py`）cwd 并不是项目根，若不显式指定，
# papers.custom.json 会跟随 cwd 解析，静默忽略仓库里已提交的自定义报社。
# 已显式设置 NEWSPAPER_APP_DIR 时不覆盖；后台看板子进程会继承该变量。
os.environ.setdefault("NEWSPAPER_APP_DIR", _HERE)

from newspaper_auto.cli import main  # noqa: E402  (需先补 sys.path)

if __name__ == "__main__":
    # 显式传入本文件所在目录，保证无论从何处调用，state/ 与 hits/ 都落在仓库内
    raise SystemExit(main(default_data_dir=_HERE))
