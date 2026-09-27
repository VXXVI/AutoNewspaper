"""公开文档的可移植性与隐私回归测试。"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_DOCS = (
    ROOT / "README.md",
    ROOT / "CHANGELOG.md",
    ROOT / "legacy" / "README.md",
    *sorted((ROOT / "docs").glob("*.md")),
)
# legacy/ 只保留旧脚本 + 说明；以下产出物可由 news_tracker.py 随时重新生成，不应入库。
LEGACY_GENERATED_ARTIFACTS = ("report.html", "result.csv", "result.json")
MACHINE_PATH_PATTERNS = {
    "macOS 用户目录": re.compile(r"/Users/"),
    "Linux 用户目录": re.compile(r"/home/"),
    "Windows 用户目录": re.compile(r"(?i)\b[A-Z]:[\\/]Users[\\/]"),
    "macOS 临时目录": re.compile(r"/private/var/folders/"),
    "开发机专用运行时": re.compile(r"\.workbuddy[\\/]"),
}


def test_public_docs_do_not_contain_machine_specific_user_paths():
    offenders: list[str] = []
    for path in PUBLIC_DOCS:
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for label, pattern in MACHINE_PATH_PATTERNS.items():
                if pattern.search(line):
                    offenders.append(f"{path.relative_to(ROOT)}:{line_no}: {label}")

    assert not offenders, "公开文档包含本机路径：\n" + "\n".join(offenders)


def test_legacy_generated_artifacts_are_not_kept():
    leftovers = [name for name in LEGACY_GENERATED_ARTIFACTS if (ROOT / "legacy" / name).exists()]
    assert not leftovers, f"legacy/ 不应保留可再生的产出物：{leftovers}"
