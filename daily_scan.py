#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
五家报社数字报 · 关键词每日增量监测（官网直采，不依赖搜索引擎）

原理
----
五家报社的数字报（= 报纸电子版）全部是方正系电子报系统，结构统一：

    第 1 版 页   →  含当日全部版面链接   (node_*.htm / node_*.html)
    单个版面页   →  含本版全部文章链接   (content_*.htm / content_*.html)
    单篇文章页   →  含标题 + 正文（正文多在自定义标签 <founder-content> 内）

所以可以直接做到「当日全版面 → 全文章 → 标题 + 正文全文匹配关键词」，
正文里提一句校名也能命中，不依赖 Google/Bing 的收录与延迟。

五家入口（第 1 版）：
    光明日报   https://epaper.gmw.cn/gmrb/html/layout/{YYYYMM/DD}/node_01.html
    南方日报   https://epaper.nfnews.com/nfdaily/html/{YYYYMM/DD}/node_A01.html
    羊城晚报   http://ep.ycwb.com/epaper/ycwb/html/{YYYY-MM/DD}/node_1.htm
    中国教育报 https://paper.jyb.cn/zgjyb/html/{YYYY-MM/DD}/node_1.htm
    湛江日报   http://paper.gdzjdaily.com.cn/html/{YYYY-MM/DD}/node_1.htm

用法
----
    python3 daily_scan.py                    # 扫今天+昨天，只报「新增」命中
    python3 daily_scan.py --days 7           # 往前回溯 7 天
    python3 daily_scan.py --kw 广东海洋大学    # 换关键词（可多次指定）
    python3 daily_scan.py --full             # 忽略增量记录，全量重扫重报
    python3 daily_scan.py --workers 8        # 并发数（默认 8）
    python3 daily_scan.py --only 光明日报,南方日报
    python3 daily_scan.py --dry              # 只探测入口与版面，不抓文章
    python3 daily_scan.py --no-state         # 无状态运行，不读写状态与报告文件
    python3 daily_scan.py --json             # 命中结果以 JSON 数组输出到 stdout
    python3 daily_scan.py --csv              # 命中结果以 CSV 格式输出到 stdout

输出
----
    state/seen.json        增量状态（记录已判定过的文章 URL）
    hits/<日期>.md         每次运行的报告
    hits/index.html        历史命中汇总（可浏览）
"""

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from html import unescape
from urllib.parse import urljoin

BASE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(BASE, "state")
HITS_DIR = os.path.join(BASE, "hits")
SEEN_FILE = os.path.join(STATE_DIR, "seen.json")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# ---------------------------------------------------------------- 报社配置
PAPERS = [
    dict(name="光明日报",   tpl="https://epaper.gmw.cn/gmrb/html/layout/{a}/node_01.html",  style="ymd"),
    dict(name="南方日报",   tpl="https://epaper.nfnews.com/nfdaily/html/{a}/node_A01.html", style="ymd"),
    dict(name="羊城晚报",   tpl="http://ep.ycwb.com/epaper/ycwb/html/{a}/node_1.htm",       style="dash"),
    dict(name="中国教育报", tpl="https://paper.jyb.cn/zgjyb/html/{a}/node_1.htm",           style="dash"),
    dict(name="湛江日报",   tpl="http://paper.gdzjdaily.com.cn/html/{a}/node_1.htm",         style="dash"),
]

SITE_JUNK = [
    r"《?中国教育报》?电子版\s*-?\s*中国教育新闻网.*",
    r"-?\s*南方日报数字报\s*-?\s*南方plus.*",
    r"羊城晚报\s*[-－]\s*",
    r"湛江日报数字报\s*[-－]\s*",
    r"《?光明日报》?\s*[-－]?\s*",
    r"南方日报\s*[-－]\s*",
]


# ---------------------------------------------------------------- HTTP
def fetch(url, timeout=25, retries=2):
    """用 curl 抓取（实测这些站点对 curl 友好），返回 (status, text)。

    带重试：报社站点在并发压力下偶发空响应，单次失败不代表当天报纸缺失。
    """
    code, body = 0, ""
    for attempt in range(retries + 1):
        # --http1.1 是必须的，不是优化：光明日报数字报的服务器（epaper.gmw.cn）
        # 在 HTTP/2 下会直接断连（curl 报 52 Empty reply from server），
        # 降级到 HTTP/1.1 才稳定。curl 默认会经 ALPN 协商到 h2，所以必须显式指定。
        cmd = ["curl", "-sL", "--http1.1", "--compressed", "-m", str(timeout), "-A", UA,
               "-H", "Accept-Language: zh-CN,zh;q=0.9",
               "-w", "\n@@HTTP:%{http_code}", url]
        try:
            p = subprocess.run(cmd, capture_output=True, timeout=timeout + 8)
            raw = p.stdout.decode("utf-8", "ignore")
        except Exception:
            raw = ""
        m = re.search(r"@@HTTP:(\d+)\s*$", raw)
        code = int(m.group(1)) if m else 0
        body = raw[:raw.rfind("\n@@HTTP:")] if "\n@@HTTP:" in raw else raw
        if code == 200 and len(body) >= 300:
            return code, body
        if attempt < retries:
            time.sleep(0.8 * (attempt + 1))
    return code, body


# ---------------------------------------------------------------- 解析
def clean(s):
    if not s:
        return ""
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = unescape(s).replace("\u3000", " ").replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def tidy_title(t):
    for p in SITE_JUNK:
        t = re.sub(p, "", t)
    return t.strip(" -－_|·　")


def links(html, base, suffix_re):
    """抽取匹配 suffix_re 的链接并绝对化、去重、保持顺序。"""
    out = []
    for h in re.findall(r'href="([^"]+)"', html):
        h = h.split("?")[0].split("#")[0].strip()
        if not h or h.startswith("javascript:"):
            continue
        if re.search(suffix_re, h):
            u = urljoin(base, h)
            if u not in out:
                out.append(u)
    return out


def extract_title(html):
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S | re.I)
    if m:
        t = tidy_title(clean(m.group(1)))
        if len(t) >= 4:
            return t
    m = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
    return tidy_title(clean(m.group(1))) if m else ""


def extract_text(html):
    """正文：优先 <founder-content>，否则退到 <body>。"""
    m = re.search(r"<founder-content[^>]*>(.*?)</founder-content>", html, re.S | re.I)
    seg = m.group(1) if m else None
    if not seg:
        m = re.search(r"<body[^>]*>(.*?)</body>", html, re.S | re.I)
        seg = m.group(1) if m else html
    return clean(seg)


def make_kw_re(kw):
    """关键词匹配：允许字符间出现空白（防止标签切碎中文）。"""
    return re.compile(r"\s*".join(re.escape(ch) for ch in kw), re.I)


def snippet(text, m, span=70):
    a = max(0, m.start() - span)
    b = min(len(text), m.end() + span)
    return ("…" if a > 0 else "") + text[a:b] + ("…" if b < len(text) else "")


# ---------------------------------------------------------------- 扫描
def date_path(d, style):
    return d.strftime("%Y%m/%d") if style == "ymd" else d.strftime("%Y-%m/%d")


def scan_paper(paper, d, kw_res, workers, dry=False):
    """扫描某家报社某一天的报纸，返回 (hits, articles_all, stats)。"""
    dp = date_path(d, paper["style"])
    entry = paper["tpl"].format(a=dp)
    stats = dict(paper=paper["name"], date=d.strftime("%Y-%m-%d"), entry=entry,
                 entry_code=0, sections=0, articles=0, scanned=0, note="")

    code, html = fetch(entry)
    stats["entry_code"] = code
    if code == 0:
        stats["note"] = "网络层失败：curl 无响应（代理/网络异常，或该站已改地址）"
        return [], [], stats
    if code != 200:
        stats["note"] = f"入口 HTTP {code}（站点侧拒绝，或当天报纸尚未上线）"
        return [], [], stats
    if len(html) < 300:
        stats["note"] = f"入口内容异常（仅 {len(html)} 字节）"
        return [], [], stats

    base = entry.rsplit("/", 1)[0] + "/"
    nodes_all = links(html, base, r"node_\w+\.html?$")
    # 只保留当日版面；若站点改了 URL 结构导致全被滤掉，则放宽，避免整期漏扫
    nodes = [n for n in nodes_all if dp in n] or nodes_all
    if entry not in nodes:
        nodes.insert(0, entry)
    nodes = list(dict.fromkeys(nodes))
    stats["sections"] = len(nodes)
    if dry:
        stats["note"] = "dry-run"
        return [], [], stats

    # 版面页 → 收集文章链接 + 版面名
    art_info = {}   # article_url -> section_title

    def grab_section(n):
        c, h = fetch(n)
        if c != 200 or len(h) < 300:
            return n, "", []
        nb = n.rsplit("/", 1)[0] + "/"
        sec = extract_title(h)[:40]
        return n, sec, [a for a in links(h, nb, r"content_\w+\.html?$") if dp in a]

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for n, sec, arts in ex.map(grab_section, nodes):
            for a in arts:
                art_info.setdefault(a, sec)

    stats["articles"] = len(art_info)

    # 文章页 → 标题 + 正文 → 匹配
    hits, all_arts = [], []

    def grab_article(a):
        c, h = fetch(a)
        if c != 200 or len(h) < 300:
            return None
        title = extract_title(h)
        text = extract_text(h)
        blob = title + " " + text
        found = [kr.search(blob) for kr in kw_res.values()]
        found = [m for m in found if m]
        rec = dict(paper=paper["name"], date=d.strftime("%Y-%m-%d"),
                   section=art_info.get(a, ""), title=title, url=a,
                   text_len=len(text))
        if found:
            rec["snippets"] = [snippet(blob, m) for m in found[:3]]
            rec["matched"] = [k for k, kr in kw_res.items() if kr.search(blob)]
        return rec

    arts = list(art_info.keys())
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for rec in ex.map(grab_article, arts):
            if not rec:
                continue
            all_arts.append(rec)
            if "matched" in rec:
                hits.append(rec)

    stats["scanned"] = len(all_arts)
    hits.sort(key=lambda r: r["url"])
    return hits, all_arts, stats


# ---------------------------------------------------------------- 状态
def load_seen():
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def prune_seen(seen, keep_days=120):
    """丢掉过老的增量记录，防止 seen.json 无限膨胀。

    文章 URL 里含日期目录（如 `/2026-09/22/` 或 `/202609/22/`），
    直接从中读日期，只保留最近 keep_days 天的记录。
    """
    cutoff = (datetime.now() - timedelta(days=keep_days)).strftime("%Y-%m")
    dropped = 0
    for bucket in seen.values():
        for url in list(bucket):
            m = re.search(r"/(\d{4})-?(\d{2})/", url)
            if m and f"{m.group(1)}-{m.group(2)}" < cutoff:
                del bucket[url]
                dropped += 1
    return dropped


def save_seen(seen):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False, indent=1)


# ---------------------------------------------------------------- 输出
def write_md(path, kw, dates, stats, new_hits, total_hits, mode):
    lines = [f"# 「{kw}」数字报监测报告", "",
             f"- 运行时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
             f"- 扫描日期：{', '.join(d.strftime('%Y-%m-%d') for d in dates)}",
             f"- 模式：{mode}",
             f"- 合计命中：{total_hits} 条（其中新增 {len(new_hits)} 条）", ""]
    lines += ["## 各家扫描情况", "",
              "| 报社 | 日期 | 版面 | 文章 | 命中 |", "|---|---|---|---|---|"]
    hit_by_paper = {}
    for h in total_hits:
        hit_by_paper[(h["paper"], h["date"])] = hit_by_paper.get((h["paper"], h["date"]), 0) + 1
    for s in stats:
        n = hit_by_paper.get((s["paper"], s["date"]), 0)
        note = f" *{s['note']}*" if s["note"] else ""
        lines.append(f"| {s['paper']} | {s['date']} | {s['sections']} | "
                     f"{s['scanned']}/{s['articles']} | {n} |" + (note and f" {note}"))
    lines.append("")
    if new_hits:
        lines += ["## 新增命中", ""]
        for i, h in enumerate(new_hits, 1):
            lines.append(f"### {i}. [{h['paper']}] {h['title']}")
            lines.append("")
            lines.append(f"- 版面：{h.get('section') or '—'}")
            lines.append(f"- 日期：{h['date']}")
            lines.append(f"- 链接：{h['url']}")
            for s in h.get("snippets", [])[:2]:
                lines.append(f"- 上下文：{s}")
            lines.append("")
    else:
        lines += ["## 新增命中", "", "无新增命中。", ""]
    if total_hits and len(total_hits) != len(new_hits):
        lines += ["## 其余已知命中（历史）", ""]
        for h in total_hits:
            if h in new_hits:
                continue
            lines.append(f"- [{h['paper']}] {h['title']} — {h['url']}")
        lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def write_html(path, kw, archive):
    import html as _h
    rows = []
    for h in sorted(archive, key=lambda x: (x["date"], x["paper"]), reverse=True):
        rows.append(
            "<tr>"
            f'<td class="p">{_h.escape(h["paper"])}</td>'
            f'<td class="d">{_h.escape(h["date"])}</td>'
            f'<td class="s">{_h.escape(h.get("section") or "—")}</td>'
            f'<td><a href="{_h.escape(h["url"])}" target="_blank" rel="noopener">'
            f'{_h.escape(h["title"])}</a>'
            + "".join(f'<div class="sn">{_h.escape(s)}</div>' for s in h.get("snippets", [])[:1])
            + "</td></tr>")
    doc = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>「{_h.escape(kw)}」五家报社监测</title>
<style>
 body{{margin:0;background:#f6f7f9;color:#1b1f24;
   font:15px/1.7 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}}
 .wrap{{max-width:1020px;margin:0 auto;padding:38px 22px 70px}}
 h1{{font-size:23px;margin:0 0 4px}}
 .meta{{color:#8a94a3;font-size:13px;margin-bottom:22px}}
 table{{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e3e7ec;border-radius:12px;overflow:hidden}}
 th,td{{padding:11px 13px;text-align:left;border-bottom:1px solid #eef1f5;font-size:14px;vertical-align:top}}
 th{{background:#fafbfc;font-size:12.5px;color:#4a5462}}
 tr:last-child td{{border-bottom:none}}
 td.p{{white-space:nowrap;font-weight:600;font-size:13px;color:#4a5462}}
 td.d{{white-space:nowrap;color:#8a94a3;font-size:12.5px}}
 td.s{{color:#8a94a3;font-size:12.5px}}
 a{{color:#1a6ef5;text-decoration:none}} a:hover{{text-decoration:underline}}
 .sn{{font-size:12px;color:#8a94a3;margin-top:4px}}
</style></head><body><div class="wrap">
<h1>「{_h.escape(kw)}」· 五家报社数字报监测</h1>
<div class="meta">共 {len(archive)} 条命中 · 更新于 {datetime.now().strftime('%Y-%m-%d %H:%M')}</div>
<table><thead><tr><th>报社</th><th>日期</th><th>版面</th><th>标题</th></tr></thead>
<tbody>{''.join(rows) or '<tr><td colspan="4">暂无命中</td></tr>'}</tbody></table>
</div></body></html>"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(doc)


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser(description="五家报社数字报关键词每日监测")
    ap.add_argument("--kw", action="append", default=None,
                    help="关键词，可多次指定（默认：广东海洋大学）")
    ap.add_argument("--days", type=int, default=2, help="回溯天数（默认 2 = 今天+昨天）")
    ap.add_argument("--workers", type=int, default=8, help="并发数（默认 8）")
    ap.add_argument("--only", default="", help="只扫指定报社，逗号分隔")
    ap.add_argument("--full", action="store_true", help="忽略增量记录，全量重报")
    ap.add_argument("--dry", action="store_true", help="只探测入口与版面，不抓文章")
    ap.add_argument("--no-state", action="store_true",
                    help="无状态模式：不读写 seen.json，不写 archive.json、index.html 及 markdown 报告")
    fmt_group = ap.add_mutually_exclusive_group()
    fmt_group.add_argument("--json", action="store_true",
                           help="命中结果以 JSON 数组输出到 stdout（日志转至 stderr）")
    fmt_group.add_argument("--csv", action="store_true",
                           help="命中结果以 CSV 格式输出到 stdout（日志转至 stderr）")
    a = ap.parse_args()

    # 结构化输出模式下，人类可读日志重定向至 stderr，保证 stdout 纯净
    log_file = sys.stderr if (a.json or a.csv) else sys.stdout

    def log(msg=""):
        print(msg, file=log_file)

    kws = a.kw or ["广东海洋大学"]
    kw_res = {k: make_kw_re(k) for k in kws}
    kws_label = " / ".join(kws)

    papers = PAPERS
    if a.only:
        want = {x.strip() for x in a.only.split(",") if x.strip()}
        papers = [p for p in PAPERS if p["name"] in want]

    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    dates = [today - timedelta(days=i) for i in range(max(1, a.days))]

    log(f"关键词：{kws_label}")
    log(f"日期范围：{dates[-1]:%Y-%m-%d} ~ {dates[0]:%Y-%m-%d}（{len(dates)} 天）")
    log(f"报社：{'、'.join(p['name'] for p in papers)}")
    log("-" * 72)

    seen = {} if a.no_state else load_seen()
    seen_buckets = {k: seen.setdefault(k, {}) for k in kws}

    all_stats, all_hits, all_arts = [], [], []
    t0 = time.time()
    for p in papers:
        for d in dates:
            hits, arts, st = scan_paper(p, d, kw_res, a.workers, dry=a.dry)
            all_stats.append(st)
            all_hits += hits
            all_arts += arts
            flag = "✓" if st["entry_code"] == 200 else "✗"
            log(f"  {flag} {p['name']:<5} {d:%m-%d}  版面{st['sections']:>3}  "
                f"文章{st['scanned']:>4}  命中{len(hits):>2}  {st['note']}")

    if a.dry:
        log("-" * 72)
        log("dry-run 结束（未抓取文章）")
        if a.json:
            sys.stdout.write("[]\n")
        elif a.csv:
            writer = csv.DictWriter(sys.stdout, fieldnames=["paper", "date", "section", "title", "url", "text_len", "matched"])
            writer.writeheader()
        return

    # 增量：本次才算「新增」的命中
    new_hits = []
    for h in all_hits:
        bucket = seen_buckets.get(h.get("matched", [""])[0], None)
        if bucket is None:
            for b in seen_buckets.values():
                if h["url"] not in b:
                    bucket = b
                    break
        if bucket is None:
            bucket = next(iter(seen_buckets.values()))
        if h["url"] not in bucket or a.full or a.no_state:
            new_hits.append(h)

    # 记录状态（仅非 --full 且非 --no-state 时写状态）
    if not a.full and not a.no_state:
        for art in all_arts:
            for k, kr in kw_res.items():
                bucket = seen.setdefault(k, {})
                blob = art["title"]
                bucket.setdefault(art["url"], 1 if kr.search(blob) else 0)
        dropped = prune_seen(seen)
        save_seen(seen)
        if dropped:
            log(f"  （已清理 {dropped} 条过期增量记录）")

    # 历史归档与报告输出（--no-state 时跳过所有文件写入）
    if not a.no_state:
        os.makedirs(HITS_DIR, exist_ok=True)
        archive_file = os.path.join(HITS_DIR, "archive.json")
        archive = []
        if os.path.exists(archive_file):
            try:
                with open(archive_file, encoding="utf-8") as f:
                    archive = json.load(f)
            except Exception:
                archive = []
        known = {x["url"] for x in archive}
        for h in all_hits:
            if h["url"] not in known:
                archive.append(h)
                known.add(h["url"])
        with open(archive_file, "w", encoding="utf-8") as f:
            json.dump(archive, f, ensure_ascii=False, indent=1)

        stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
        md = os.path.join(HITS_DIR, f"{stamp}.md")
        write_md(md, kws_label, dates, all_stats, new_hits, all_hits,
                 "全量重报" if a.full else "增量")
        write_html(os.path.join(HITS_DIR, "index.html"), kws_label, archive)

    # 结构化输出到 stdout
    if a.json:
        sys.stdout.write(json.dumps(all_hits, ensure_ascii=False, indent=2) + "\n")
    elif a.csv:
        fieldnames = ["paper", "date", "section", "title", "url", "text_len", "matched"]
        writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
        writer.writeheader()
        for h in all_hits:
            writer.writerow({
                "paper": h.get("paper", ""),
                "date": h.get("date", ""),
                "section": h.get("section", ""),
                "title": h.get("title", ""),
                "url": h.get("url", ""),
                "text_len": h.get("text_len", 0),
                "matched": ";".join(h.get("matched", [])),
            })

    log("-" * 72)
    log(f"  扫描文章 {len(all_arts)} 篇 | 命中 {len(all_hits)} 篇 | "
        f"新增 {len(new_hits)} 篇 | 耗时 {time.time() - t0:.1f}s")
    if new_hits:
        log("\n  ★ 新增命中：")
        for h in new_hits:
            log(f"    · [{h['paper']}] {h['title']}")
            log(f"      {h['url']}")
    if not a.no_state:
        log(f"\n  报告：{md}")
        log(f"  汇总：{os.path.join(HITS_DIR, 'index.html')}")


if __name__ == "__main__":
    main()
