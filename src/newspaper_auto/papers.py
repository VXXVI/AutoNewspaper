"""自定义报社：项目根 ``papers.custom.json`` 的读取、校验、合并与写回。

设计要点：

- **配置跟随项目根**：文件位置是 ``<项目根>/papers.custom.json``（由
  ``paths.find_project_root()`` 决定），**不跟 ``--data-dir`` 走**——
  ``--data-dir`` 只是数据落点（``state/`` / ``hits/``），而自定义报社属于「配置」层。
- **零 I/O 约束**：``config.py`` 不含 I/O，故读写逻辑集中在本模块，``config`` 只放常量。
- **同名覆盖语义**：custom 里的 ``name`` 与内置重复时**覆盖内置那条**（可用来改内置
  报社的 URL / 超时）；不重复则**追加**。合并后内置原有顺序在前，纯新增按文件顺序
  追加在后。
- **容错**：文件缺失 / JSON 非法 / 单条非法 → 忽略并向 stderr 打一行警告，
  绝不让整个扫描崩掉。
- **SSRF 边界（如实说明，不试图"修"）**：``tpl`` 是用户自己填的 URL，扫描器会用系统
  ``curl`` 去抓它。这里**只校验协议前缀为 http / https**，**不封私网 IP 段**——用户可能
  就是想扫内网报社。这是本机单人工具，风险可接受。
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from string import Formatter
from typing import Any

from . import config, report

MAX_NAME_LEN = 30
STYLES = ("ymd", "dash", "custom")
URL_PREFIXES = ("http://", "https://")
MIN_TIMEOUT, MAX_TIMEOUT = 5, 120
MIN_RETRIES, MAX_RETRIES = 0, 6
MAX_PATTERN_LEN = 300
MAX_DATE_FORMAT_LEN = 40
DATE_FORMATS = {
    "ymd": "%Y%m/%d",
    "dash": "%Y-%m/%d",
}
# 自定义日期路径只允许年月日指令与常见路径分隔符，避免平台相关的 strftime
# 指令产生不可预测结果。例：%Y/%m/%d、%Y%m%d、%y-%m/%d。
_DATE_FORMAT_RE = re.compile(r"^(?:%[Yymd]|[-_./])+$")


def _custom_path(root: Path) -> Path:
    return Path(root) / config.CUSTOM_PAPERS_FILENAME


def _warn(message: str) -> None:
    print(f"[警告] {config.CUSTOM_PAPERS_FILENAME}：{message}", file=sys.stderr)


def _is_int(value: Any) -> bool:
    """严格的 int 判定（``bool`` 是 int 的子类，必须排除）。"""
    return isinstance(value, int) and not isinstance(value, bool)


def validate_paper(obj: Any) -> tuple[dict[str, Any] | None, str | None]:
    """逐条校验一条自定义报社，返回 ``(归一化后的 paper, 错误说明)``。

    合法时错误为 None；否则 paper 为 None、错误是给用户看的中文说明。可选字段
    （``timeout`` / ``retries`` / ``node_exclude``）缺省则不写入归一化结果。
    """
    if not isinstance(obj, dict):
        return None, "每条报社必须是一个 JSON 对象"

    name = obj.get("name")
    if not isinstance(name, str) or not name.strip():
        return None, "name 必须是非空字符串"
    name = name.strip()
    if len(name) > MAX_NAME_LEN:
        return None, f"name 不能超过 {MAX_NAME_LEN} 个字符"

    style = obj.get("style")
    if style not in STYLES:
        return None, "style 必须是 ymd、dash 或 custom"

    tpl = obj.get("tpl")
    if not isinstance(tpl, str) or not tpl.strip():
        return None, "tpl 必须是非空字符串"
    tpl = tpl.strip()
    if not tpl.startswith(URL_PREFIXES):
        return None, "tpl 必须以 http:// 或 https:// 开头"
    try:
        fields = [
            (field, format_spec, conversion)
            for _, field, format_spec, conversion in Formatter().parse(tpl)
            if field
        ]
    except ValueError as exc:
        return None, f"tpl 花括号格式非法：{exc}"
    if not fields or not any(field == "a" for field, _, _ in fields):
        return None, "tpl 必须包含 {a} 日期占位符"
    if any(field != "a" or format_spec or conversion for field, format_spec, conversion in fields):
        return None, "tpl 只允许使用不带格式的 {a} 日期占位符"
    try:
        tpl.format(a="202609/24")
    except (KeyError, IndexError, ValueError) as exc:
        return None, f"tpl 无法正常生成日期链接：{exc}"

    paper: dict[str, Any] = {"name": name, "style": style, "tpl": tpl}

    date_format = obj.get("date_format")
    if style == "custom":
        if not isinstance(date_format, str) or not date_format.strip():
            return None, "style 为 custom 时必须填写 date_format"
        date_format = date_format.strip()
        if len(date_format) > MAX_DATE_FORMAT_LEN:
            return None, f"date_format 不能超过 {MAX_DATE_FORMAT_LEN} 个字符"
        if not _DATE_FORMAT_RE.fullmatch(date_format) or "%d" not in date_format:
            return None, "date_format 只允许 %Y/%y/%m/%d 与 -_./，并且必须包含 %d"
        paper["date_format"] = date_format
    elif date_format not in (None, ""):
        return None, "只有 custom 日期格式可以填写 date_format"

    timeout = obj.get("timeout")
    if timeout is not None:
        if not _is_int(timeout) or not MIN_TIMEOUT <= timeout <= MAX_TIMEOUT:
            return None, f"timeout 必须是 {MIN_TIMEOUT}~{MAX_TIMEOUT} 之间的整数"
        paper["timeout"] = timeout

    retries = obj.get("retries")
    if retries is not None:
        if not _is_int(retries) or not MIN_RETRIES <= retries <= MAX_RETRIES:
            return None, f"retries 必须是 {MIN_RETRIES}~{MAX_RETRIES} 之间的整数"
        paper["retries"] = retries

    node_exclude = obj.get("node_exclude")
    if node_exclude is not None:
        if not isinstance(node_exclude, str) or not node_exclude:
            return None, "node_exclude 必须是非空字符串"
        try:
            re.compile(node_exclude)
        except re.error as exc:
            return None, f"node_exclude 不是合法正则：{exc}"
        paper["node_exclude"] = node_exclude

    for field, label in (
        ("node_pattern", "版面链接正则"),
        ("article_pattern", "文章链接正则"),
    ):
        value = obj.get(field)
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            return None, f"{field} 必须是非空字符串"
        value = value.strip()
        if len(value) > MAX_PATTERN_LEN:
            return None, f"{field} 不能超过 {MAX_PATTERN_LEN} 个字符"
        try:
            re.compile(value)
        except re.error as exc:
            return None, f"{label}不是合法正则：{exc}"
        paper[field] = value

    return paper, None


def format_date_path(paper: dict[str, Any], day: Any) -> str:
    """根据报社配置生成日期路径；旧的 ymd/dash 配置保持完全兼容。"""
    style = paper.get("style")
    date_format = paper.get("date_format") if style == "custom" else DATE_FORMATS.get(style)
    if not isinstance(date_format, str):
        raise ValueError(f"未知日期路径格式：{style}")
    return day.strftime(date_format)


def load_custom(root: Path) -> list[dict[str, Any]]:
    """读取 ``<root>/papers.custom.json`` 并逐条校验。

    缺失 / 读取失败 / JSON 非法 / 顶层结构不对 → 返回 ``[]``（并打警告）；
    单条非法则跳过该条、其余照常返回。绝不抛异常。
    """
    path = _custom_path(root)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as exc:  # 权限 / 编码等
        _warn(f"读取失败（{exc}），已忽略")
        return []

    try:
        data = json.loads(text)
    except ValueError as exc:
        _warn(f"JSON 解析失败（{exc}），已忽略整个文件")
        return []
    if not isinstance(data, dict):
        _warn('顶层必须是对象（形如 {"version": 1, "papers": [...]}），已忽略')
        return []
    raw_papers = data.get("papers")
    if not isinstance(raw_papers, list):
        _warn("papers 字段必须是数组，已忽略")
        return []

    result: list[dict[str, Any]] = []
    for index, item in enumerate(raw_papers, 1):
        paper, error = validate_paper(item)
        if error:
            _warn(f"第 {index} 条无效（{error}），已跳过")
            continue
        assert paper is not None
        result.append(paper)
    return result


def merge_papers(builtin: Sequence[dict[str, Any]], custom: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """把内置与自定义合并：同名覆盖、不重复则追加。

    内置原有顺序在前；纯新增按 ``custom`` 的给出顺序追加在后。返回的是「浅拷贝」，
    调用方修改结果不会影响入参。
    """
    custom_list = list(custom)
    custom_by_name: dict[str, dict[str, Any]] = {}
    for paper in custom_list:
        custom_by_name.setdefault(paper["name"], paper)

    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for paper in builtin:
        name = paper["name"]
        seen.add(name)
        override = custom_by_name.get(name)
        merged.append(dict(override if override is not None else paper))
    for paper in custom_list:
        name = paper["name"]
        if name in seen:
            continue
        seen.add(name)
        merged.append(dict(paper))
    return merged


def save_custom(root: Path, custom_papers: Iterable[dict[str, Any]]) -> None:
    """原子写回 ``<root>/papers.custom.json``（缩进 JSON，用户可直接手编）。"""
    path = _custom_path(root)
    text = json.dumps({"version": 1, "papers": list(custom_papers)}, ensure_ascii=False, indent=2) + "\n"
    report.write_text(path, text)


def custom_names(root: Path) -> set[str]:
    """自定义文件里出现的报社名集合（供「内置 / 自定义 / 覆盖」标注用）。"""
    return {paper["name"] for paper in load_custom(root)}
