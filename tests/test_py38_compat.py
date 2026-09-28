"""静态闸门：``src/`` 与 ``packaging/`` 不得使用只在 3.9+ 才有的**运行期**语法。

为什么需要它
------------
Windows 7 便携包是用 **Python 3.8.10** 构建、并把项目源码一起打进去的
（见 ``packaging/win7/``、README「Windows 7」）。但 ``pyproject.toml`` 声明
``requires-python = ">=3.9"``、ruff ``target-version = "py39"``、CI 矩阵也只有
3.9/3.12/3.13 —— **没有任何自动化环节检查 3.8 兼容性**：

- 在 3.9 上跑测试，``dict[str, int]`` 一切正常，测试当然全绿；
- ruff 的 pyupgrade 规则只管注解写法，不管运行期求值的新语法（实测
  ``x = dict[str, int]`` / ``y = int | str`` / ``"ab".removeprefix("a")``
  在 ``--target-version py39`` 与 ``py38`` 下都是零告警）。

所以只能静态扫 AST，只禁止**注解之外**的两类写法：

1. PEP 585 泛型下标：``dict[str, int]`` / ``list[int]`` / ``tuple[str, ...]`` ……
2. PEP 604 联合：``int | str`` / ``str | None`` / ``Callable[[], bool] | None`` ……

注解本身不受影响：全项目都有 ``from __future__ import annotations``，注解是字符串、
永不求值 —— 这正是 ``state.SeenMap`` 与 ``scanner.Fetcher`` 写成字符串常量的原因
（模块级别名在 3.8 上会直接 ``TypeError``）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TARGETS = (ROOT / "src", ROOT / "packaging")

# 运行期下标即报：这些内置类型在 3.8 上不可下标（PEP 585 是 3.9 引入的）
_BUILTIN_GENERICS = frozenset({"dict", "list", "set", "tuple", "frozenset", "type"})

# PEP 604 联合的判定：只有两侧都「像类型」才算，否则会把运行期的**值**或运算误判进来。
# 实际踩过的误报：``os.O_CREAT | os.O_EXCL | os.O_WRONLY``、``re.S | re.I``（整型位或，
# 3.8 完全合法）。区分办法是「全大写的名字是常量，不是类型」：
#   - ``os.O_CREAT`` / ``re.S``：属性名全大写 → 常量 → 不算类型；
#   - ``os.PathLike`` / ``Path``：混合大小写 → 类型名 → 算；
#   - ``disabled_names | set(x)``：左侧是小写变量名、右侧是 Call → 都不算类型。
_TYPE_NAMES = frozenset({"int", "str", "bytes", "bool", "float", "complex", "object", "None", "NoneType"})


def _looks_like_type(name: str) -> bool:
    """名字「像类型」：内置类型名，或以大写开头的驼峰名（``Path`` / ``Callable``）。

    全大写的（``O_CREAT`` / ``S`` / ``FLAGS``）一律当常量排除。
    """
    return name in _TYPE_NAMES or (name[:1].isupper() and not name.isupper())


def _python_files() -> list[Path]:
    files: list[Path] = []
    for target in TARGETS:
        files.extend(sorted(target.rglob("*.py")))
    return files


class _RuntimeScan(ast.NodeVisitor):
    """收集「不在注解里」的 PEP 585 / PEP 604 使用点。"""

    def __init__(self) -> None:
        self._annotations: list[tuple[int, int]] = []
        self.hits: list[tuple[int, str]] = []

    def collect(self, tree: ast.AST) -> None:
        """先收齐注解的行范围，再扫其余部分。"""
        for node in ast.walk(tree):
            if isinstance(node, ast.AnnAssign):
                self._annotations.append((node.annotation.lineno, node.annotation.end_lineno))
            if isinstance(node, ast.arg) and node.annotation is not None:
                self._annotations.append((node.annotation.lineno, node.annotation.end_lineno))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns is not None:
                self._annotations.append((node.returns.lineno, node.returns.end_lineno))
        self.visit(tree)

    def _in_annotation(self, node: ast.AST) -> bool:
        return any(start <= node.lineno <= end for start, end in self._annotations)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        value = node.value
        if isinstance(value, ast.Name) and value.id in _BUILTIN_GENERICS and not self._in_annotation(node):
            self.hits.append((node.lineno, f"`{value.id}[...]` 是运行期泛型下标，Python 3.8 会抛 TypeError"))
        self.generic_visit(node)

    def visit_BinOp(self, node: ast.BinOp) -> None:
        if isinstance(node.op, ast.BitOr) and not self._in_annotation(node) and self._is_type_union(node):
            self.hits.append((node.lineno, "`A | B` 运行期类型联合需要 Python 3.10+，3.8 会抛 TypeError"))
        self.generic_visit(node)

    @staticmethod
    def _is_type_union(node: ast.BinOp) -> bool:
        def type_like(side: ast.expr) -> bool:
            if isinstance(side, ast.Constant) and side.value is None:
                return True
            if isinstance(side, ast.Name) and _looks_like_type(side.id):
                return True
            if isinstance(side, ast.Attribute) and _looks_like_type(side.attr):
                return True
            return isinstance(side, ast.Subscript)

        return type_like(node.left) and type_like(node.right)


def test_scan_targets_are_not_empty() -> None:
    """路径写错时不能让这个闸门静默通过。"""
    assert len(_python_files()) >= 20


@pytest.mark.parametrize("path", _python_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_no_py38_incompatible_runtime_syntax(path: Path) -> None:
    """Win7 包在 Python 3.8 上跑源码：运行期不能出现 3.9+/3.10+ 才有的新语法。"""
    source = path.read_text(encoding="utf-8")
    scan = _RuntimeScan()
    scan.collect(ast.parse(source))
    if not scan.hits:
        return
    lines = source.splitlines()
    details = "\n".join(
        f"  L{line}: {message}\n      {lines[line - 1].strip()}" for line, message in scan.hits
    )
    pytest.fail(f"{path.relative_to(ROOT)} 出现会在 Python 3.8 上崩溃的运行期语法：\n{details}")
