"""静态配置：报社入口、清洗规则与默认参数。

本模块不含任何 I/O 与可变全局状态，便于被 CLI、扫描器与测试共同复用。
"""

from __future__ import annotations

# ---------------------------------------------------------------- 默认值
DEFAULT_KEYWORDS: tuple[str, ...] = ("广东海洋大学",)
DEFAULT_DAYS = 2
DEFAULT_WORKERS = 8
# --since/--until 显式区间的跨度上限：防止误扫把六家报社站点打爆
MAX_RANGE_DAYS = 62

# ---------------------------------------------------------------- HTTP
# 这些报社站点对 curl 友好；并发压力下偶发空响应，靠重试兜底。
FETCH_TIMEOUT = 25
FETCH_RETRIES = 2
RETRY_BACKOFF = 0.8
# 低于该字节数视为「内容异常」（正常版面页远大于此）。
MIN_HTML_BYTES = 300

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# ---------------------------------------------------------------- 报社配置
# style 决定日期目录格式：
#   ymd  -> 202609/22    dash -> 2026-09/22
PAPERS: tuple[dict, ...] = (
    {"name": "光明日报", "style": "ymd", "tpl": "https://epaper.gmw.cn/gmrb/html/layout/{a}/node_01.html"},
    {"name": "南方日报", "style": "ymd", "tpl": "https://epaper.nfnews.com/nfdaily/html/{a}/node_A01.html"},
    # 羊城晚报：GitHub runner (Chicago) 原始 curl 超时 30s 才返回 HTTP 000；
    # 靠现有 25s×2 重试勉强通过，属临界状态。单独提高超时与重试次数兜底。
    {
        "name": "羊城晚报",
        "style": "dash",
        "tpl": "http://ep.ycwb.com/epaper/ycwb/html/{a}/node_1.htm",
        "timeout": 60,
        "retries": 4,
    },
    {"name": "中国教育报", "style": "dash", "tpl": "https://paper.jyb.cn/zgjyb/html/{a}/node_1.htm"},
    {"name": "湛江日报", "style": "dash", "tpl": "http://paper.gdzjdaily.com.cn/html/{a}/node_1.htm"},
    # 人民日报（人民网数字报，pc 版）：版面页里的「手机版」链接指向 /rmrb/pad/…，
    # 会与本版内容重复，故用 node_exclude 排除，只走 pc 版。
    {
        "name": "人民日报",
        "style": "ymd",
        "tpl": "http://paper.people.com.cn/rmrb/pc/layout/{a}/node_01.html",
        "node_exclude": r"/rmrb/pad/",
    },
)

# 标题里夹带的站点后缀，需剔除
SITE_JUNK: tuple[str, ...] = (
    r"《?中国教育报》?电子版\s*-?\s*中国教育新闻网.*",
    r"-?\s*南方日报数字报\s*-?\s*南方plus.*",
    r"羊城晚报\s*[-－]\s*",
    r"湛江日报数字报\s*[-－]\s*",
    r"《?光明日报》?\s*[-－]?\s*",
    r"南方日报\s*[-－]\s*",
    # 人民日报版面页的 <title> 是站点通用名（不含任何版面信息）。不剥掉的话，
    # extract_title 会返回它当版面名，看起来「非空」实则是错的。
    r"人民日报\s*[-－]\s*人民网.*",
)

# ---------------------------------------------------------------- 数据目录
STATE_DIRNAME = "state"
HITS_DIRNAME = "hits"
SEEN_FILENAME = "seen.json"
ARCHIVE_FILENAME = "archive.json"
INDEX_FILENAME = "index.html"
# 增量记录保留天数，防止 seen.json 无限膨胀
SEEN_KEEP_DAYS = 120

# 供 --data-dir 自动探测使用的根目录标志
ROOT_MARKERS: tuple[str, ...] = (".git", "pyproject.toml")
