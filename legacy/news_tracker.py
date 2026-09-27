#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
示例机构 · 五家报社报道追踪器
==================================================
只做一件事：在指定关键词下，从这五家报社抓取相关报道。

五家报社 / 数据路径（均为实测可用）：
  1. 光明日报   gmw.cn            -> Google News site: 限定
  2. 南方日报   southcn.com       -> Google News site: 限定
  3. 羊城晚报   ycwb.com          -> Google News site: 限定
  4. 中国教育报 jyb.cn            -> Google News site: 限定
  5. 湛江日报   gdzjdaily.com.cn  -> 湛江新闻网站内搜索 /s?wd=（Google 收录停滞，必须走站内）

用法：
  python3 news_tracker.py                        # 默认关键词「示例大学」
  python3 news_tracker.py --kw 示例大学 --days 90
  python3 news_tracker.py --pages 5              # 湛江站内搜索翻页数

输出：result.json / result.csv / report.html （脚本同目录）
"""
import argparse, csv, html, json, os, re, subprocess, sys, time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
OUT_DIR = os.path.dirname(os.path.abspath(__file__))
CST = timezone(timedelta(hours=8))

# ---------- 五家报社配置 ----------
PAPERS = [
    {"name": "光明日报",   "domain": "gmw.cn",            "home": "https://www.gmw.cn"},
    {"name": "南方日报",   "domain": "southcn.com",       "home": "https://www.southcn.com"},
    {"name": "羊城晚报",   "domain": "ycwb.com",          "home": "https://www.ycwb.com"},
    {"name": "中国教育报", "domain": "jyb.cn",            "home": "https://www.jyb.cn"},
    {"name": "湛江日报",   "domain": "gdzjdaily.com.cn",  "home": "https://www.gdzjdaily.com.cn"},
]
# FeedX 全文源（只有光明日报有）
FEEDX = {"光明日报": "https://feedx.net/rss/guangmingribao.xml"}


def fetch(url, ref=None, retries=2):
    """带重试的抓取。返回文本；失败返回 None。"""
    for i in range(retries + 1):
        cmd = ["curl", "-sL", "-m", "25", "-A", UA, "--compressed"]
        if ref:
            cmd += ["-H", f"Referer: {ref}"]
        cmd.append(url)
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=35)
            if r.returncode == 0 and r.stdout:
                return r.stdout.decode("utf-8", "ignore")
        except Exception:
            pass
        if i < retries:
            time.sleep(1.5)
    return None


def strip_tags(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def parse_rfc822(s):
    """把 'Mon, 21 Sep 2026 12:23:10 GMT' 转成 CST datetime。"""
    if not s:
        return None
    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z"):
        try:
            dt = datetime.strptime(s.strip(), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(CST)
        except ValueError:
            continue
    return None


# ---------- 通道 1：Google News 按站点限定 ----------
def google_news(kw, domain, home):
    """Google News RSS：q="关键词" site:域名"""
    q = urllib.parse.quote(f'"{kw}"').replace("%20", "+")
    url = (f"https://news.google.com/rss/search?q={q}+site:{domain}"
           f"&hl=zh-CN&gl=CN&ceid=CN:zh-Hans")
    b = fetch(url)
    if not b:
        return []
    try:
        root = ET.fromstring(b)
    except ET.ParseError:
        return []
    out = []
    for item in root.findall(".//item"):
        title = strip_tags(item.findtext("title") or "")
        # Google News 标题尾部带 " - 来源名"，剥掉
        for sep in (" - ", " — "):
            if sep in title:
                title = title.rsplit(sep, 1)[0].strip()
                break
        out.append({
            "title": title,
            "link": (item.findtext("link") or "").strip(),
            "date": parse_rfc822(item.findtext("pubDate")),
            "summary": strip_tags(item.findtext("description") or "")[:300],
            "channel": "Google News",
        })
    return out


# ---------- 通道 2：湛江新闻网站内搜索 ----------
def zhanjiang_search(kw, pages=3):
    """湛江新闻网 /s?wd= 。注意：该站搜索是分词匹配，需二次过滤。"""
    out, seen = [], set()
    for p in range(1, pages + 1):
        url = f"https://www.gdzjdaily.com.cn/s?wd={urllib.parse.quote(kw)}"
        if p > 1:
            url += f"&page={p}"
        b = fetch(url, ref="https://www.gdzjdaily.com.cn/")
        if not b:
            break
        blocks = re.findall(r'<p class="title">(.*?)</p>', b, re.S)
        if not blocks:
            blocks = re.findall(r'<li>(.*?)</li>', b, re.S)
        got = 0
        for blk in blocks:
            a = re.search(r'href="([^"]+)"[^>]*>(.*?)</a>', blk, re.S)
            if not a:
                continue
            link, title = a.group(1).strip(), strip_tags(a.group(2))
            if not title or len(title) < 6 or link in seen:
                continue
            seen.add(link)
            got += 1
            if not link.startswith("http"):
                link = "https://www.gdzjdaily.com.cn" + ("" if link.startswith("/") else "/") + link
            out.append({
                "title": title, "link": link, "date": None,
                "summary": strip_tags(blk)[:300],
                "channel": "湛江新闻网 站内搜索",
            })
        if got == 0:
            break
        time.sleep(1.0)
    return out


# ---------- 二次过滤 / 置信度分级 ----------
# 关键词 → 公认简称（按需补充；默认只把关键词本身当作匹配目标）
KEYWORD_VARIANTS = {
    "示例大学": {"示例大", "例大学"},
}


def kw_variants(kw):
    """关键词及其公认简称。"""
    return {kw} | set(KEYWORD_VARIANTS.get(kw, ()))


def grade(items, kw):
    """
    给每条打置信度：
      high —— 标题里直接出现关键词或其简称，可直接采用
      low  —— Google/站内匹配到了，但标题未出现；多为页面侧栏关联或泛报道，需人工复核
    注意：Google News 的 site: 查询匹配偏宽松，实测会混入同站点无关文章，
          所以必须分级，不能一股脑当成结果。
    """
    vs = kw_variants(kw)
    for it in items:
        t = it.get("title") or ""
        it["confidence"] = "high" if any(v in t for v in vs) else "low"
    return items


# ---------- 主流程 ----------
def run(kw, days, pages):
    all_rows, stats = [], []
    cutoff = datetime.now(CST) - timedelta(days=days)

    for p in PAPERS:
        name, dom = p["name"], p["domain"]
        rows = []

        if name == "湛江日报":
            # 双通道：Google News 只能捞到较旧的历史（收录停滞在 2023），
            # 站内搜索能拿到最新的，两者合并才完整。
            rows = google_news(kw, dom, p["home"])
            for r in rows:
                r["paper"] = name
                r["date_str"] = r["date"].strftime("%Y-%m-%d") if r["date"] else "—"
            site = zhanjiang_search(kw, pages=pages)
            for r in site:
                r["paper"] = name
                r["date_str"] = "—"
            rows += site
            rows = grade(rows, kw)
            note = f"GoogleNews {len(rows) - len(site)} 条 + 站内搜索 {len(site)} 条"
        else:
            rows = google_news(kw, dom, p["home"])
            for r in rows:
                r["paper"] = name
                r["date_str"] = r["date"].strftime("%Y-%m-%d") if r["date"] else "—"
            rows = grade(rows, kw)
            note = f"Google News site:{dom}"

        # 时间过滤（有日期的才过滤）
        if days > 0:
            rows = [r for r in rows if (r["date"] is None) or (r["date"] >= cutoff)]

        all_rows += rows
        stats.append({"paper": name, "count": len(rows),
                      "high": sum(1 for r in rows if r["confidence"] == "high"),
                      "note": note})

    # 去重
    seen, uniq = set(), []
    for r in all_rows:
        k = r["title"][:40]
        if k in seen:
            continue
        seen.add(k)
        uniq.append(r)
    # 排序：高置信优先，其次按时间倒序
    uniq.sort(key=lambda r: (r["confidence"] != "high",
                             -(r["date"].timestamp() if r["date"] else 0)))
    return uniq, stats


def write_outputs(kw, rows, stats, days):
    hi = [r for r in rows if r["confidence"] == "high"]
    lo = [r for r in rows if r["confidence"] != "high"]

    # JSON
    with open(os.path.join(OUT_DIR, "result.json"), "w", encoding="utf-8") as f:
        json.dump({"keyword": kw, "generated": datetime.now(CST).isoformat(),
                   "window_days": days, "stats": stats,
                   "items": [{**r, "date": r["date"].isoformat() if r["date"] else None} for r in rows]},
                  f, ensure_ascii=False, indent=2)

    # CSV
    with open(os.path.join(OUT_DIR, "result.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["置信度", "报社", "日期", "标题", "链接", "来源通道"])
        for r in rows:
            w.writerow(["高" if r["confidence"] == "high" else "待复核",
                        r["paper"], r["date_str"], r["title"], r["link"], r["channel"]])

    # HTML
    def esc(s):
        return html.escape(str(s) if s is not None else "")

    cards = []
    for s in stats:
        cards.append(
            f'<div class="stat"><b>{esc(s["paper"])}</b>'
            f'<span class="n">{s["count"]}</span>'
            f'<span class="nt">其中高置信 {s["high"]} 条<br>{esc(s["note"])}</span></div>')

    def table(rs):
        if not rs:
            return '<p class="empty">（无）</p>'
        out = ['<table><thead><tr><th style="width:88px">报社</th>'
               '<th style="width:100px">日期</th><th>标题</th></tr></thead><tbody>']
        for r in rs:
            out.append(
                f'<tr><td class="pp">{esc(r["paper"])}</td><td class="dt">{esc(r["date_str"])}</td>'
                f'<td><a href="{esc(r["link"])}" target="_blank" rel="noopener">{esc(r["title"])}</a>'
                f'<div class="ch">{esc(r["channel"])}</div></td></tr>')
        out.append('</tbody></table>')
        return ''.join(out)

    doc = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(kw)} · 五家报社报道汇总</title>
<style>
 body{{margin:0;background:#f6f7f9;color:#1b1f24;
   font:15px/1.7 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}}
 .wrap{{max-width:1000px;margin:0 auto;padding:40px 22px 70px}}
 h1{{font-size:24px;margin:0 0 6px}}
 .meta{{color:#8a94a3;font-size:13px;margin-bottom:24px}}
 .grid{{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:30px}}
 .stat{{flex:1 1 170px;background:#fff;border:1px solid #e3e7ec;border-radius:11px;padding:14px 16px}}
 .stat b{{display:block;font-size:13.5px;color:#4a5462}}
 .stat .n{{font-size:26px;font-weight:700;color:#1a6ef5;line-height:1.3}}
 .stat .nt{{display:block;font-size:11.5px;color:#8a94a3;line-height:1.55}}
 h2{{font-size:17px;margin:34px 0 12px;padding-left:11px;border-left:4px solid #1a6ef5}}
 h2 small{{font-weight:400;color:#8a94a3;font-size:13px;margin-left:8px}}
 table{{width:100%;border-collapse:collapse;background:#fff;
   border:1px solid #e3e7ec;border-radius:12px;overflow:hidden}}
 th,td{{padding:12px 14px;text-align:left;border-bottom:1px solid #eef1f5;font-size:14px;vertical-align:top}}
 th{{background:#fafbfc;font-size:12.5px;color:#4a5462}}
 tr:last-child td{{border-bottom:none}}
 td.pp{{white-space:nowrap;font-weight:600;font-size:13px;color:#4a5462}}
 td.dt{{white-space:nowrap;color:#8a94a3;font-size:12.5px}}
 a{{color:#1a6ef5;text-decoration:none}} a:hover{{text-decoration:underline}}
 .ch{{font-size:11.5px;color:#8a94a3;margin-top:3px}}
 .empty{{color:#8a94a3;font-size:13.5px}}
 .note{{background:#fff4e0;border:1px solid #f5dcae;border-radius:10px;
   padding:14px 18px;font-size:13.5px;margin-top:22px;color:#4a5462}}
 .note b{{color:#b26a00}}
 code{{font-family:ui-monospace,Menlo,monospace;font-size:12.5px;background:#f1f3f6;
   border:1px solid #e3e7ec;border-radius:5px;padding:1px 6px}}
</style></head><body><div class="wrap">
<h1>「{esc(kw)}」· 五家报社报道汇总</h1>
<div class="meta">生成时间 {datetime.now(CST).strftime('%Y-%m-%d %H:%M')} ·
 {esc('时间窗不限' if days == 0 else '时间窗 ' + str(days) + ' 天')} ·
 共 {len(rows)} 条（高置信 {len(hi)} 条）</div>
<div class="grid">{''.join(cards)}</div>

<h2>高置信结果 <small>标题中直接出现「{esc(kw)}」或其简称，可直接采用</small></h2>
{table(hi)}

<h2>待复核 <small>检索命中但标题未出现关键词，多为该站页面关联内容，需人工判断</small></h2>
{table(lo)}

<div class="note">
<b>使用须知</b><br>
· Google News 的链接是 <code>news.google.com/rss/articles/…</code> <b>跳转链接</b>，点开会跳到原文。<br>
· Google News 的 <code>site:</code> 匹配偏宽松，会混入同站点无关文章——所以本报告做了<b>置信度分级</b>，只看「高置信」这一组通常就够。<br>
· 湛江日报因 Google 收录停滞（最新停在 2023），走的是<b>湛江新闻网站内搜索</b>；该站搜索是分词匹配，噪音天然较多。<br>
· 需要光明日报<b>全文</b>：<code>{esc(FEEDX['光明日报'])}</code>（FeedX，仅此一家有全文源）。
</div>
</div></body></html>"""
    with open(os.path.join(OUT_DIR, "report.html"), "w", encoding="utf-8") as f:
        f.write(doc)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kw", default="示例大学")
    ap.add_argument("--days", type=int, default=0, help="时间窗天数，0=不限")
    ap.add_argument("--pages", type=int, default=3, help="湛江站内搜索翻页数")
    a = ap.parse_args()

    print(f"关键词：{a.kw} | 时间窗：{'不限' if a.days == 0 else str(a.days) + '天'}")
    print("-" * 72)
    rows, stats = run(a.kw, a.days, a.pages)
    for s in stats:
        print(f"  {s['paper']:<8} {s['count']:>4} 条（高置信 {s['high']:>3}）  {s['note']}")
    print("-" * 72)
    hi = sum(1 for r in rows if r["confidence"] == "high")
    print(f"  合计 {len(rows)} 条（去重后），其中高置信 {hi} 条")
    write_outputs(a.kw, rows, stats, a.days)
    print(f"  已输出：result.json / result.csv / report.html -> {OUT_DIR}")


if __name__ == "__main__":
    main()
