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

from . import __version__, config, papers, parse, paths, report, scanner, state


def _parse_date(text: str) -> datetime:
    """解析 ``YYYY-MM-DD``；非法输入交给 argparse 报错（退出码 2）。"""
    try:
        return datetime.strptime(text.strip(), "%Y-%m-%d")
    except (ValueError, AttributeError):
        raise argparse.ArgumentTypeError(f"非法日期 {text!r}，应形如 2026-09-23") from None


def _bounded_int(name: str, minimum: int, maximum: int) -> Callable[[str], int]:
    """构造 argparse 整数校验器，让错误在联网前以退出码 2 暴露。"""

    def parse_value(text: str) -> int:
        try:
            value = int(text)
        except (TypeError, ValueError):
            raise argparse.ArgumentTypeError(f"{name} 必须是整数") from None
        if not minimum <= value <= maximum:
            raise argparse.ArgumentTypeError(f"{name} 必须在 {minimum}~{maximum} 之间")
        return value

    return parse_value


def _parse_keyword(text: str) -> str:
    value = text.strip()
    if not value:
        raise argparse.ArgumentTypeError("关键词不能为空")
    if len(value) > config.MAX_KEYWORD_LEN:
        raise argparse.ArgumentTypeError(f"单个关键词不能超过 {config.MAX_KEYWORD_LEN} 个字符")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="daily_scan.py", description="数字报关键词每日监测")
    parser.add_argument(
        "--kw",
        action="append",
        type=_parse_keyword,
        default=None,
        help=f"关键词，可多次指定（最多 {config.MAX_KEYWORDS} 个；默认：广东海洋大学）",
    )
    # --days 与 --since/--until 语义互斥；后者两者可共存，故不用 argparse 的
    # mutually exclusive group（它无法表达「days ⊕ (since ∨ until)」），改在 main 里显式判定。
    parser.add_argument(
        "--days",
        type=_bounded_int("--days", 1, config.MAX_RANGE_DAYS),
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
        "--workers",
        type=_bounded_int("--workers", 1, config.MAX_WORKERS),
        default=config.DEFAULT_WORKERS,
        help=f"并发数（1~{config.MAX_WORKERS}，默认 {config.DEFAULT_WORKERS}）",
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
        span = args.days if args.days is not None else config.DEFAULT_DAYS
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


def select_papers(
    only: str, all_papers: Sequence[dict[str, Any]] | None = None
) -> tuple[list[dict[str, Any]], list[str]]:
    """按 ``--only`` 过滤报社，返回 ``(命中的报社列表, 未识别的名字列表)``。

    语义边界（与 ``main`` 的退出行为一致）：

    - ``only`` 为空 / 全空白 → 返回全部报社，未识别为空（与不传 ``--only`` 等价）；
    - **部分匹配** → 返回命中的报社，未识别的名字放进第二个返回值，由调用方**警告后继续**；
    - **零匹配** → 命中的报社为空列表、未识别为全部名字，由调用方 **报错退出（退出码 2）**，
      绝不静默回退全部。

    ``all_papers`` 缺省时退回内置 ``config.PAPERS``（兼容既有调用与测试）；传入
    「内置 + 自定义」合并后的列表时，自定义报社也能被 ``--only`` 选中。
    """
    source = list(all_papers) if all_papers is not None else list(config.PAPERS)
    if not only or not only.strip():
        return source, []
    wanted: list[str] = []
    for name in only.split(","):
        stripped = name.strip()
        if stripped and stripped not in wanted:
            wanted.append(stripped)
    if not wanted:
        return source, []
    wanted_set = set(wanted)
    known = {paper["name"] for paper in source}
    selected = [paper for paper in source if paper["name"] in wanted_set]
    unmatched = [name for name in wanted if name not in known]
    return selected, unmatched


def update_archive(data_dir: os.PathLike[str] | str, all_hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把本次命中并入 ``archive.json``，返回合并后的归档。

    旧归档中的局部脏数据会被跳过；同 URL 再次命中时更新标题、版面与
    几何信息，并对 ``matched`` 取有序并集，避免新关键词永远不进看板。
    """
    archive_path = paths.archive_file(data_dir)
    try:
        with open(archive_path, encoding="utf-8") as handle:
            raw_archive = json.load(handle)
    except (OSError, ValueError):
        raw_archive = []
    archive = (
        [item for raw in raw_archive if (item := _normalize_archive_item(raw)) is not None]
        if isinstance(raw_archive, list)
        else []
    )
    by_url = {item["url"]: index for index, item in enumerate(archive)}
    for hit in all_hits:
        normalized_hit = _normalize_archive_item(hit)
        if normalized_hit is None:
            continue
        url = normalized_hit["url"]
        if url not in by_url:
            by_url[url] = len(archive)
            archive.append(normalized_hit)
            continue
        index = by_url[url]
        previous = archive[index]
        merged = {**previous, **normalized_hit}
        merged["matched"] = list(
            dict.fromkeys([*(previous.get("matched") or []), *(normalized_hit.get("matched") or [])])
        )
        archive[index] = merged
    report.write_text(archive_path, json.dumps(archive, ensure_ascii=False, indent=1))
    return archive


def _normalize_archive_item(item: Any) -> dict[str, Any] | None:
    """把归档条目收敛为报告渲染必需的安全形状。"""
    if not isinstance(item, dict):
        return None
    url = item.get("url")
    if not isinstance(url, str) or not url:
        return None
    normalized = dict(item)
    normalized.update(
        {
            "paper": str(item.get("paper") or ""),
            "date": str(item.get("date") or ""),
            "section": str(item.get("section") or ""),
            "section_no": str(item.get("section_no") or ""),
            "title": str(item.get("title") or ""),
            "url": url,
            "snippets": [str(value) for value in item.get("snippets", []) if value]
            if isinstance(item.get("snippets"), list)
            else [],
            "matched": [str(value) for value in item.get("matched", []) if value]
            if isinstance(item.get("matched"), list)
            else [],
        }
    )
    try:
        normalized["text_len"] = int(item.get("text_len") or 0)
    except (TypeError, ValueError):
        normalized["text_len"] = 0
    return normalized


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

    keywords = list(dict.fromkeys(args.kw or config.DEFAULT_KEYWORDS))
    if len(keywords) > config.MAX_KEYWORDS:
        parser.error(f"关键词最多 {config.MAX_KEYWORDS} 个")
    kw_res = {k: parse.make_kw_re(k) for k in keywords}
    kw_label = " / ".join(keywords)

    # 自定义报社跟随**项目根**（配置层），与 --data-dir（数据层）无关
    all_papers = papers.merge_papers(config.PAPERS, papers.load_custom(paths.find_project_root()))
    selected, unmatched = select_papers(args.only, all_papers)
    # 部分匹配 = 警告后继续；零匹配 = 报错退出码 2，绝不开扫（避免静默回退全部）
    if unmatched:
        available = "、".join(paper["name"] for paper in all_papers)
        log(f"⚠️ --only 未识别：{'、'.join(unmatched)}（可选：{available}）")
        if not selected:
            parser.error(f"--only 未匹配到任何报社：{'、'.join(unmatched)}（可选：{available}）")
    data_dir = paths.resolve_data_dir(args.data_dir, default_data_dir)

    dates = resolve_dates(args, parser)

    log(f"关键词：{kw_label}")
    log(f"日期范围：{dates[-1]:%Y-%m-%d} ~ {dates[0]:%Y-%m-%d}（{len(dates)} 天）")
    log(f"报社：{'、'.join(p['name'] for p in selected)}")
    log("-" * 72)

    all_stats: list[dict[str, Any]] = []
    all_hits: list[dict[str, Any]] = []
    all_articles: list[dict[str, Any]] = []
    started = time.time()
    for paper in selected:
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

    md_path: Any = None
    persistence_error: str | None = None
    if args.no_state:
        new_hits = state.compute_new_hits(all_hits, {}, full=args.full, no_state=True)
    else:
        state_path = paths.state_file(data_dir)
        try:
            # 扫描可以并行进行；只在快速的「重读→合并→提交」阶段持有跨进程锁。
            with state.exclusive_lock(state_path):
                seen = state.load_seen(state_path)
                new_hits = state.compute_new_hits(all_hits, seen, full=args.full)
                archive = update_archive(data_dir, all_hits)
                stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
                md_path = paths.hits_dir(data_dir) / f"{stamp}.md"
                report.write_md(
                    md_path,
                    kw_label,
                    dates,
                    all_stats,
                    new_hits,
                    all_hits,
                    "全量重报" if args.full else "增量",
                )
                report.write_html(paths.index_file(data_dir), kw_label, archive)
                # seen.json 最后写：它是本次数据提交的标志。
                # 中途任何写入失败时，下次仍会把命中视为新增，不会静默丢报。
                if not args.full:
                    state.record_hits(seen, all_hits)
                    dropped = state.prune_seen(seen)
                    state.save_seen(state_path, seen)
                    if dropped:
                        log(f"  （已清理 {dropped} 条过期增量记录）")
        except (OSError, TimeoutError) as exc:
            new_hits = state.compute_new_hits(all_hits, {}, full=True)
            persistence_error = str(exc) or exc.__class__.__name__

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

    if persistence_error:
        log(f"\n  ⚠️  结果写入失败：{persistence_error}")
        return 1

    # 入口失败 → 非零退出，让 Actions 步骤变红；但增量状态/报告已在上面写完，
    # Actions 的"提交结果"步骤应设  if: always()  确保提交仍然发生。
    entry_failures = [s for s in all_stats if not s.get("ok", s.get("entry_code", 200) == 200)]
    if entry_failures:
        names = "、".join(s["paper"] for s in entry_failures)
        log(f"\n  ⚠️  {len(entry_failures)} 个报社/日期出现入口失败或扫描异常（{names}），结果可能漏报！")
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
