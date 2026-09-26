"""自定义报社：项目根 ``papers.custom.json`` 的读取、校验、合并与写回。

设计要点：

- **配置跟随项目根**：文件位置是 ``<项目根>/papers.custom.json``（由
  ``paths.find_project_root()`` 决定），**不跟 ``--data-dir`` 走**——
  ``--data-dir`` 只是数据落点（``state/`` / ``hits/``），而自定义报社属于「配置」层。
- **零 I/O 约束**：``config.py`` 不含 I/O，故读写逻辑集中在本模块，``config`` 只放常量。
- **同名覆盖语义**：custom 里的 ``name`` 与内置重复时**覆盖内置那条**（可用来改内置
  报社的 URL / 超时）；不重复则**追加**。合并后内置原有顺序在前，纯新增按文件顺序
  追加在后。
- **停用不是删除**：顶层 ``disabled`` 保存暂时不参与扫描的报社名称。这样内置报社无需
  修改源代码也能安全停用，并可随时恢复；旧文件没有该字段时自动视为空列表。
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
from dataclasses import dataclass
from pathlib import Path
from string import Formatter
from typing import Any
from urllib.parse import urlsplit

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
PAPER_FIELDS = frozenset(
    {
        "name",
        "style",
        "tpl",
        "date_format",
        "timeout",
        "retries",
        "node_exclude",
        "node_pattern",
        "article_pattern",
    }
)
# 自定义日期路径只允许年月日指令与常见路径分隔符，避免平台相关的 strftime
# 指令产生不可预测结果。例：%Y/%m/%d、%Y%m%d、%y-%m/%d。
_DATE_FORMAT_RE = re.compile(r"^(?:%[Yymd]|[-_./])+$")


class CustomPapersError(ValueError):
    """自定义报社文件无法安全读改写。"""


@dataclass(frozen=True)
class PaperSettings:
    """``papers.custom.json`` 的规范化内容。"""

    custom: tuple[dict[str, Any], ...] = ()
    disabled: tuple[str, ...] = ()


def _custom_path(root: Path) -> Path:
    return Path(root) / config.CUSTOM_PAPERS_FILENAME


def _warn(message: str) -> None:
    print(f"[警告] {config.CUSTOM_PAPERS_FILENAME}：{message}", file=sys.stderr)


def _clean_name(value: Any) -> tuple[str | None, str | None]:
    """校验并规范化报社名称，供报社对象与 ``disabled`` 共用。"""
    if not isinstance(value, str) or not value.strip():
        return None, "name 必须是非空字符串"
    name = value.strip()
    if len(name) > MAX_NAME_LEN:
        return None, f"name 不能超过 {MAX_NAME_LEN} 个字符"
    if "," in name or any(ord(char) < 32 or ord(char) == 127 for char in name):
        return None, "name 不能包含英文逗号或控制字符"
    return name, None


def validate_paper(
    obj: Any, *, allow_unknown_fields: bool = False
) -> tuple[dict[str, Any] | None, str | None]:
    """逐条校验一条自定义报社，返回 ``(归一化后的 paper, 错误说明)``。

    合法时错误为 None；否则 paper 为 None、错误是给用户看的中文说明。可选字段
    （``timeout`` / ``retries`` / ``node_exclude``）缺省则不写入归一化结果。
    """
    if not isinstance(obj, dict):
        return None, "每条报社必须是一个 JSON 对象"
    unknown = set(obj) - PAPER_FIELDS
    if unknown and not allow_unknown_fields:
        return None, f"不支持的字段：{'、'.join(sorted(unknown))}"

    name, name_error = _clean_name(obj.get("name"))
    if name_error:
        return None, name_error
    assert name is not None

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
        sample_url = tpl.format(a="202609/24")
    except (KeyError, IndexError, ValueError) as exc:
        return None, f"tpl 无法正常生成日期链接：{exc}"
    if any(char.isspace() for char in sample_url):
        return None, "tpl 生成的链接不能包含空白字符"
    try:
        parsed_url = urlsplit(sample_url)
        # 访问 ``port`` 会顺带验证端口是否合法（例如拒绝 :abc / :99999）。
        port = parsed_url.port
    except ValueError as exc:
        return None, f"tpl 生成的链接不合法：{exc}"
    if (
        parsed_url.scheme not in ("http", "https")
        or not parsed_url.hostname
        or (port is not None and not 1 <= port <= 65535)
    ):
        return None, "tpl 必须生成带有效主机名的 http/https 链接"

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
        if not config.is_int(timeout) or not MIN_TIMEOUT <= timeout <= MAX_TIMEOUT:
            return None, f"timeout 必须是 {MIN_TIMEOUT}~{MAX_TIMEOUT} 之间的整数"
        paper["timeout"] = timeout

    retries = obj.get("retries")
    if retries is not None:
        if not config.is_int(retries) or not MIN_RETRIES <= retries <= MAX_RETRIES:
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


def _parse_settings(data: Any, *, strict: bool, path_name: str) -> PaperSettings:
    """解析配置；只读扫描容错，写接口严格拒绝歧义配置。"""
    if not isinstance(data, dict):
        message = '顶层必须是对象（形如 {"version": 1, "papers": [], "disabled": []}）'
        if strict:
            raise CustomPapersError(f"{path_name} {message}")
        _warn(f"{message}，已忽略")
        return PaperSettings()
    version = data.get("version", 1)
    if version != 1:
        message = f"不支持 version={version!r}"
        if strict:
            raise CustomPapersError(f"{path_name} 使用{message}")
        _warn(f"{message}，已忽略整个文件")
        return PaperSettings()
    raw_papers = data.get("papers")
    if not isinstance(raw_papers, list):
        message = "papers 字段必须是数组"
        if strict:
            raise CustomPapersError(f"{path_name} 的 {message}")
        _warn(f"{message}，已忽略")
        return PaperSettings()

    custom: list[dict[str, Any]] = []
    names: set[str] = set()
    for index, item in enumerate(raw_papers, 1):
        # version=1 的历史语义是忽略额外字段。文件读取保持这一兼容性；
        # Web 新增/编辑接口仍会严格拒绝未知字段，防止拼写错误被静默忽略。
        paper, error = validate_paper(item, allow_unknown_fields=True)
        if error:
            if strict:
                raise CustomPapersError(f"{path_name} 第 {index} 条无效：{error}")
            _warn(f"第 {index} 条无效（{error}），已跳过")
            continue
        assert paper is not None
        if paper["name"] in names:
            if strict:
                raise CustomPapersError(f"{path_name} 第 {index} 条报社名称重复：{paper['name']}")
            _warn(f"第 {index} 条与前文报社同名（{paper['name']}），已跳过")
            continue
        names.add(paper["name"])
        custom.append(paper)

    raw_disabled = data.get("disabled", [])
    if not isinstance(raw_disabled, list):
        message = "disabled 字段必须是字符串数组"
        if strict:
            raise CustomPapersError(f"{path_name} 的 {message}")
        _warn(f"{message}，已忽略该字段")
        raw_disabled = []
    disabled: list[str] = []
    for index, value in enumerate(raw_disabled, 1):
        name, error = _clean_name(value)
        if error:
            if strict:
                raise CustomPapersError(f"{path_name} disabled 第 {index} 项无效：{error}")
            _warn(f"disabled 第 {index} 项无效（{error}），已跳过")
            continue
        assert name is not None
        if name in disabled:
            if strict:
                raise CustomPapersError(f"{path_name} disabled 第 {index} 项名称重复：{name}")
            _warn(f"disabled 第 {index} 项名称重复（{name}），已跳过")
            continue
        disabled.append(name)
    return PaperSettings(tuple(custom), tuple(disabled))


def load_settings(root: Path) -> PaperSettings:
    """容错读取完整报社设置；配置损坏时警告并回落安全默认。"""
    path = _custom_path(root)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return PaperSettings()
    except OSError as exc:  # 权限 / 编码等
        _warn(f"读取失败（{exc}），已忽略")
        return PaperSettings()

    try:
        data = json.loads(text)
    except ValueError as exc:
        _warn(f"JSON 解析失败（{exc}），已忽略整个文件")
        return PaperSettings()
    return _parse_settings(data, strict=False, path_name=path.name)


def load_custom(root: Path) -> list[dict[str, Any]]:
    """兼容接口：只返回 ``papers`` 列表。"""
    return [dict(paper) for paper in load_settings(root).custom]


def load_settings_for_update(root: Path) -> PaperSettings:
    """严格读取设置，供 Web 写接口执行“读—改—写”。

    与容错读取 :func:`load_settings` 不同，已有文件只要损坏或含非法条目就抛出
    :class:`CustomPapersError`。这样用户手工编辑出错时，界面保存不会把原文件静默覆盖。
    文件不存在仍表示尚无自定义设置。
    """
    path = _custom_path(root)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return PaperSettings()
    except OSError as exc:
        raise CustomPapersError(f"无法读取 {path.name}：{exc}") from exc

    try:
        data = json.loads(text)
    except ValueError as exc:
        raise CustomPapersError(f"{path.name} JSON 解析失败：{exc}") from exc
    return _parse_settings(data, strict=True, path_name=path.name)


def load_custom_for_update(root: Path) -> list[dict[str, Any]]:
    """兼容接口：严格读取后只返回 ``papers`` 列表。"""
    return [dict(paper) for paper in load_settings_for_update(root).custom]


def merge_papers(
    builtin: Sequence[dict[str, Any]],
    custom: Iterable[dict[str, Any]],
    disabled: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """把内置与自定义合并，再排除停用项：同名覆盖、不重复则追加。

    内置原有顺序在前；纯新增按 ``custom`` 的给出顺序追加在后。返回的是「浅拷贝」，
    调用方修改结果不会影响入参。
    """
    custom_list = list(custom)
    disabled_names = set(disabled)
    custom_by_name: dict[str, dict[str, Any]] = {}
    for paper in custom_list:
        custom_by_name.setdefault(paper["name"], paper)

    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for paper in builtin:
        name = paper["name"]
        seen.add(name)
        override = custom_by_name.get(name)
        if name not in disabled_names:
            merged.append(dict(override if override is not None else paper))
    for paper in custom_list:
        name = paper["name"]
        if name in seen:
            continue
        seen.add(name)
        if name not in disabled_names:
            merged.append(dict(paper))
    return merged


def save_settings(
    root: Path,
    custom_papers: Iterable[dict[str, Any]],
    disabled: Iterable[str] = (),
) -> None:
    """原子写回完整设置（缩进 JSON，用户可直接手编）。"""
    normalized_papers: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for index, item in enumerate(custom_papers, 1):
        paper, error = validate_paper(item)
        if error:
            raise CustomPapersError(f"待保存的第 {index} 条报社无效：{error}")
        assert paper is not None
        if paper["name"] in seen_names:
            raise CustomPapersError(f"待保存的报社名称重复：{paper['name']}")
        seen_names.add(paper["name"])
        normalized_papers.append(paper)

    normalized_disabled: list[str] = []
    for index, value in enumerate(disabled, 1):
        name, error = _clean_name(value)
        if error:
            raise CustomPapersError(f"待保存的 disabled 第 {index} 项无效：{error}")
        assert name is not None
        if name not in normalized_disabled:
            normalized_disabled.append(name)

    path = _custom_path(root)
    payload = {"version": 1, "papers": normalized_papers, "disabled": normalized_disabled}
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    report.write_text(path, text)


def save_custom(root: Path, custom_papers: Iterable[dict[str, Any]]) -> None:
    """兼容接口：保存自定义报社，并保留现有停用列表。"""
    disabled = load_settings_for_update(root).disabled
    save_settings(root, custom_papers, disabled)


def custom_names(root: Path) -> set[str]:
    """自定义文件里出现的报社名集合（供「内置 / 自定义 / 覆盖」标注用）。"""
    return {paper["name"] for paper in load_custom(root)}
