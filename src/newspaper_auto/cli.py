"""命令行入口与主流程编排。"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any, Callable

from . import __version__, config, parse, paths, report, scanner, state


def _parse_date(text: str) -> datetime:
    """解析 ``YYYY-MM-DD``；非法输入交给 argparse 报错（退出码 2）。"""
    try:
        return datetime.strptime(text.strip(), "%Y-%m-%d")
    except (ValueError, AttributeError):
        raise argparse.ArgumentTypeError(f"非法日期 {text!r}，应形如 2026-09-23") from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="daily_scan.py", description="六家报社数字报关键词每日监测")
    parser.add_argument(
        "--kw", action="append", default=None, help="关键词，可多次指定（默认：广东海洋大学）"
    )
    # --days 与 --since/--until 语义互斥；后者两者可共存，故不用 argparse 的
    # mutually exclusive group（它无法表达「days ⊕ (since ∨ until)」），改在 main 里显式判定。
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help=f"回溯天数（默认 {config.DEFAULT_DAYS} = 今天+昨天）；与 --since/--until 互斥",
    )
    parser.add_argument(
        "--since",
        type=_parse_date,
        default=None,
        metavar="YYYY-MM-DD",
        help="起始日期（含），缺省取 --until；与 --days 互斥",
    )
    parser.add_argument(
        "--until",
        type=_parse_date,
        default=None,
        metavar="YYYY-MM-DD",
        help="结束日期（含），缺省为今天；与 --days 互斥",
    )
    parser.add_argument(
        "--workers", type=int, default=config.DEFAULT_WORKERS, help=f"并发数（默认 {config.DEFAULT_WORKERS}）"
    )
    parser.add_argument("--only", default="", help="只扫指定报社，逗号分隔")
    parser.add_argument("--full", action="store_true", help="忽略增量记录，全量重报")
    parser.add_argument("--dry", action="store_true", help="只探测入口与版面，不抓文章")
    parser.add_argument(
        "--no-state",
        action="store_true",
        help="无状态模式：不读写 seen.json，不写 archive.json、index.html 及 markdown 报告",
    )
    parser.add_argument(
        "--data-dir", default=None, help="数据目录（存放 state/ 与 hits/），默认自动探测项目根"
    )
    fmt_group = parser.add_mutually_exclusive_group()
    fmt_group.add_argument(
        "--json", action="store_true", help="命中结果以 JSON 数组输出到 stdout（日志转至 stderr）"
    )
    fmt_group.add_argument(
        "--csv", action="store_true", help="命中结果以 CSV 格式输出到 stdout（日志转至 stderr）"
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def resolve_dates(args: argparse.Namespace, parser: argparse.ArgumentParser | None = None) -> list[datetime]:
    """把 ``--days`` / ``--since`` / ``--until`` 收敛成**从新到旧**的日期列表。

    - ``--days N``：维持原语义（今天往前 N 天，含今天），CI/launchd 都在用；
    - ``--since`` / ``--until``：显式区间，两端都含；只给 ``--until`` 时 since=until，
      只给 ``--since`` 时 until=今天；跨度上限 ``config.MAX_RANGE_DAYS`` 天；
    - ``--days`` 与 ``--since``/``--until`` 同时给 → argparse 报错退出码 2。

    ``dates[0]`` 恒为最新，``dates[-1]`` 恒为最旧，日志与报告依赖该顺序。
    """
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    use_range = args.since is not None or args.until is not None
    if args.days is not None and use_range:
        p = parser or build_parser()
        p.error("--days 不能与 --since/--until 同时使用")
    if not use_range:
        span = max(1, args.days or config.DEFAULT_DAYS)
        return [today - timedelta(days=i) for i in range(span)]

    until = args.until or today
    since = args.since or until
    if since > until:
        p = parser or build_parser()
        p.error(f"--since（{since:%Y-%m-%d}）不能晚于 --until（{until:%Y-%m-%d}）")
    span = (until - since).days + 1
    if span > config.MAX_RANGE_DAYS:
        p = parser or build_parser()
        p.error(
            f"日期跨度 {span} 天超过上限 {config.MAX_RANGE_DAYS} 天，请缩小范围（避免误扫把六家报社站点打爆）"
        )
    return [until - timedelta(days=i) for i in range(span)]


def select_papers(only: str) -> list[dict[str, str]]:
    """按 ``--only`` 过滤报社；未匹配到任何报社时原样返回（不静默清空）。"""
    if not only:
        return list(config.PAPERS)
    wanted = {name.strip() for name in only.split(",") if name.strip()}
    return [p for p in config.PAPERS if p["name"] in wanted] or list(config.PAPERS)


def update_archive(data_dir: os.PathLike[str] | str, all_hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把本次命中并入 ``archive.json``，返回合并后的归档。"""
    archive_path = paths.archive_file(data_dir)
    try:
        with open(archive_path, encoding="utf-8") as handle:
            archive = json.load(handle)
        if not isinstance(archive, list):
            archive = []
    except (OSError, ValueError):
        archive = []
    known = {item["url"] for item in archive}
    for hit in all_hits:
        if hit["url"] not in known:
            archive.append(hit)
            known.add(hit["url"])
    report.write_text(archive_path, json.dumps(archive, ensure_ascii=False, indent=1))
    return archive


def main(
    argv: Sequence[str] | None = None,
    default_data_dir: str | os.PathLike[str] | None = None,
    fetcher: Callable[[str], tuple[int, str]] | None = None,
) -> int:
    """程序入口。``default_data_dir`` 供兼容入口传入仓库根目录，``fetcher`` 供测试注入。"""
    parser = build_parser()
    args = parser.parse_args(argv)

    # 结构化输出模式下，人类可读日志重定向至 stderr，保证 stdout 纯净
    log: Callable[..., None] = _make_logger(sys.stderr if (args.json or args.csv) else sys.stdout)

    keywords = args.kw or list(config.DEFAULT_KEYWORDS)
    kw_res = {k: parse.make_kw_re(k) for k in keywords}
    kw_label = " / ".join(keywords)

    papers = select_papers(args.only)
    data_dir = paths.resolve_data_dir(args.data_dir, default_data_dir)

    dates = resolve_dates(args, parser)

    log(f"关键词：{kw_label}")
    log(f"日期范围：{dates[-1]:%Y-%m-%d} ~ {dates[0]:%Y-%m-%d}（{len(dates)} 天）")
    log(f"报社：{'、'.join(p['name'] for p in papers)}")
    log("-" * 72)

    seen = {} if args.no_state else state.load_seen(paths.state_file(data_dir))

    all_stats: list[dict[str, Any]] = []
    all_hits: list[dict[str, Any]] = []
    all_articles: list[dict[str, Any]] = []
    started = time.time()
    for paper in papers:
        for day in dates:
            hits, articles, stats = scanner.scan_paper(
                paper, day, kw_res, args.workers, dry=args.dry, fetcher=fetcher
            )
            all_stats.append(stats)
            all_hits += hits
            all_articles += articles
            flag = "✓" if stats["entry_code"] == 200 else "✗"
            log(
                f"  {flag} {paper['name']:<5} {day:%m-%d}  版面{stats['sections']:>3}  "
                f"文章{stats['scanned']:>4}  命中{len(hits):>2}  {stats['note']}"
            )

    if args.dry:
        log("-" * 72)
        log("dry-run 结束（未抓取文章）")
        _emit_structured(args, [])
        return 0

    new_hits = state.compute_new_hits(all_hits, seen, full=args.full, no_state=args.no_state)

    if not args.full and not args.no_state:
        state.record_hits(seen, all_hits)
        dropped = state.prune_seen(seen)
        state.save_seen(paths.state_file(data_dir), seen)
        if dropped:
            log(f"  （已清理 {dropped} 条过期增量记录）")

    md_path: Any = None
    if not args.no_state:
        archive = update_archive(data_dir, all_hits)
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
        md_path = paths.hits_dir(data_dir) / f"{stamp}.md"
        report.write_md(
            md_path, kw_label, dates, all_stats, new_hits, all_hits, "全量重报" if args.full else "增量"
        )
        report.write_html(paths.index_file(data_dir), kw_label, archive)

    _emit_structured(args, all_hits)

    log("-" * 72)
    log(
        f"  扫描文章 {len(all_articles)} 篇 | 命中 {len(all_hits)} 篇 | "
        f"新增 {len(new_hits)} 篇 | 耗时 {time.time() - started:.1f}s"
    )
    if new_hits:
        log("\n  ★ 新增命中：")
        for hit in new_hits:
            section = hit.get("section") or "—"
            label = f"{hit['paper']} · {section}"
            position = report.geometry_label(hit.get("geometry"))
            if position:
                label += f" · {position}"
            log(f"    · [{label}] {hit['title']}")
            log(f"      {hit['url']}")
    if md_path is not None:
        log(f"\n  报告：{md_path}")
        log(f"  汇总：{paths.index_file(data_dir)}")

    # 入口失败 → 非零退出，让 Actions 步骤变红；但增量状态/报告已在上面写完，
    # Actions 的"提交结果"步骤应设  if: always()  确保提交仍然发生。
    entry_failures = [s for s in all_stats if s.get("entry_code", 200) != 200]
    if entry_failures:
        names = "、".join(s["paper"] for s in entry_failures)
        log(f"\n  ⚠️  {len(entry_failures)} 家报社入口失败（{names}），结果可能漏报！")
        return 1
    return 0


def _make_logger(stream) -> Callable[..., None]:
    def log(message: str = "") -> None:
        print(message, file=stream)

    return log


def _emit_structured(args: argparse.Namespace, hits: list[dict[str, Any]]) -> None:
    if args.json:
        sys.stdout.write(report.hits_to_json(hits) + "\n")
    elif args.csv:
        sys.stdout.write(report.hits_to_csv(hits))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
