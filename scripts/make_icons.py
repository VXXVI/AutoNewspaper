#!/usr/bin/env python3
"""生成品牌图标：``favicon.ico`` / ``apple-touch-icon.png`` / ``logo.svg``。

**单一真源**：本文件里的 :data:`MARK` 几何。SVG 与各尺寸位图都由它渲染，
所以「网页顶栏看板标」「浏览器标签页」「Windows exe / 安装包」永远是同一张画。
改图标只需要改 :data:`MARK`，然后重跑本脚本。

设计约束（踩过的坑，别再犯）：

- **必须在 16px 站得住**。上一版把 16/24/32 画成简化版、48 以上画成详细版，
  同一个图标两套画风，Windows 在不同尺寸下会「一会儿一个样」。
  现在只有一套画，且只依赖**大色块**（蓝底 / 白纸 / 琥珀命中块）承担辨识度，
  细线只是大尺寸下的纹理，丢了也不影响认出是什么。
- **不要浅色主体配浅色底**。上一版 `logo.svg` 是白纸 + 0.65px 灰线，缩到 42px
  放在浅色顶栏上几乎看不见（所以被当成「图标坏了」）。这里用品牌蓝托底，
  任何浅色背景上都有对比度。
- 颜色 ≤ 8 种、最细线条远大于 4px（见 ``MARK`` 里的注释）。

只用标准库（``zlib`` / ``struct``）：不引入 Pillow / cairosvg 之类构建期依赖，
保证任何人、任何 CI 上重跑都得到同一张图。

用法::

    python scripts/make_icons.py            # 就地重写 static/ 下的图标
    python scripts/make_icons.py --check    # 只校验已提交的文件与几何一致（测试用）
"""

from __future__ import annotations

import argparse
import math
import struct
import sys
import zlib
from collections.abc import Iterable
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "src" / "newspaper_auto" / "static"

CANVAS = 1024  # 设计画布边长；几何一律用这套坐标写

# ICO 内嵌的尺寸：16/24/32 是任务栏与标签页实际用的，48 以上是桌面大图标与安装包。
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
APPLE_TOUCH_SIZE = 180


class Rect(NamedTuple):
    """轴对齐圆角矩形（设计画布坐标）。"""

    x: float
    y: float
    w: float
    h: float
    r: float  # 圆角半径
    fill: str  # #rrggbb


BLUE = "#2855d9"  # 品牌主蓝，与 style.css 的 --primary 一致
PAPER = "#ffffff"
AMBER = "#ffc53d"  # 命中高亮色：比旧版 #fef08a 饱和，白底上也看得见
BROWN = "#7a3e12"  # 命中块里的「正文行」
RULE = "#c9d3e4"  # 版面里的常规分栏线（仅作大尺寸纹理）

# 从下往上叠：底 → 纸 → 分栏线 → 命中块 → 命中块里的正文行
MARK: tuple[Rect, ...] = (
    Rect(0, 0, 1024, 1024, 232, BLUE),  # 蓝底：16px 下 3.6px 圆角，辨识度全靠它
    Rect(176, 128, 672, 768, 72, PAPER),  # 纸面：16px 下是 10.5×12 的白块
    Rect(256, 224, 464, 56, 28, RULE),
    Rect(256, 312, 336, 56, 28, RULE),
    Rect(240, 432, 544, 320, 64, AMBER),  # 命中块：16px 下 8.5×5，只有它必须活下来
    Rect(328, 508, 368, 64, 32, BROWN),
    Rect(328, 612, 368, 64, 32, BROWN),
    Rect(256, 800, 464, 56, 28, RULE),
)

_SUBROWS = 4  # 垂直方向的超采样数；水平方向用解析覆盖，不必采样


# ---------------------------------------------------------------- 颜色
def _rgba(color: str, alpha: float = 1.0) -> tuple[float, float, float, float]:
    color = color.lstrip("#")
    return (
        int(color[0:2], 16) / 255.0,
        int(color[2:4], 16) / 255.0,
        int(color[4:6], 16) / 255.0,
        alpha,
    )


# ---------------------------------------------------------------- 光栅化
def _row_span(rect: Rect, y: float, scale: float, square_bg: bool) -> tuple[float, float] | None:
    """返回该扫描线在设备坐标下被矩形覆盖的 ``[x_left, x_right]``；不覆盖则 None。"""
    x0 = rect.x * scale
    y0 = rect.y * scale
    x1 = (rect.x + rect.w) * scale
    y1 = (rect.y + rect.h) * scale
    if y < y0 or y >= y1:
        return None
    radius = rect.r * scale
    if square_bg and rect.y == 0 and rect.h == CANVAS:
        radius = 0.0  # apple-touch-icon 用满幅直角底，交给 iOS 自己裁圆角
    dx = 0.0
    if radius > 0:
        if y < y0 + radius:
            dx = radius - math.sqrt(max(0.0, radius**2 - (y0 + radius - y) ** 2))
        elif y >= y1 - radius:
            dx = radius - math.sqrt(max(0.0, radius**2 - (y - (y1 - radius)) ** 2))
    return (x0 + dx, x1 - dx)


def render(size: int, *, square_bg: bool = False) -> list[tuple[float, float, float, float]]:
    """渲染为 ``size × size`` 的 RGBA 像素（行优先，非预乘）。

    每个形状先累加出自己的**覆盖率**再整体混合一次：分 4 次按 25% 透明度叠加
    只会得到 ``1-0.75**4 ≈ 0.68`` 的不透明度，图标会整体发灰（踩过）。
    """
    scale = size / CANVAS
    pixels: list[tuple[float, float, float, float]] = [(0.0, 0.0, 0.0, 0.0)] * (size * size)
    for rect in MARK:
        coverage = [0.0] * (size * size)
        for py in range(size):
            base = py * size
            # 4 条子扫描线平均，得到垂直方向的抗锯齿；水平方向用解析覆盖
            for sub in range(_SUBROWS):
                y = py + (sub + 0.5) / _SUBROWS
                span = _row_span(rect, y, scale, square_bg)
                if span is None:
                    continue
                left, right = span
                weight = 1.0 / _SUBROWS
                for px in range(max(0, math.floor(left)), min(size, math.ceil(right))):
                    hit = min(right, px + 1.0) - max(left, float(px))
                    if hit > 0.0:
                        coverage[base + px] += min(1.0, hit) * weight
        if not any(coverage):
            continue
        src = _rgba(rect.fill)
        for index, alpha in enumerate(coverage):
            if alpha <= 0.0:
                continue
            alpha = min(1.0, alpha)
            dst = pixels[index]
            out_alpha = alpha + dst[3] * (1.0 - alpha)
            if out_alpha <= 0.0:
                continue
            # source-over 的**非预乘**形式：颜色分量要再除掉 out_alpha
            pixels[index] = (
                (src[0] * alpha + dst[0] * dst[3] * (1.0 - alpha)) / out_alpha,
                (src[1] * alpha + dst[1] * dst[3] * (1.0 - alpha)) / out_alpha,
                (src[2] * alpha + dst[2] * dst[3] * (1.0 - alpha)) / out_alpha,
                out_alpha,
            )
    return pixels


def _quantize(pixel: tuple[float, float, float, float]) -> bytes:
    return bytes(min(255, max(0, round(channel * 255))) for channel in pixel)


def render_rgba(size: int, *, square_bg: bool = False) -> bytes:
    """渲染为可直接写进 PNG 的 RGBA 字节串（未做预乘，PNG 也不预乘）。"""
    out = bytearray()
    for pixel in render(size, square_bg=square_bg):
        out += _quantize(pixel)
    return bytes(out)


# ---------------------------------------------------------------- PNG / ICO
def encode_png(rgba: bytes, size: int) -> bytes:
    """把 RGBA 字节串编码为 8 位真彩 PNG（全 0 过滤，便于 :func:`decode_png` 校验）。"""
    expected = size * size * 4
    if len(rgba) != expected:
        raise ValueError(f"RGBA 长度 {len(rgba)} 与 {size}×{size} 不符（应为 {expected}）")
    raw = bytearray()
    stride = size * 4
    for row in range(size):
        raw.append(0)  # 过滤类型 0（None）
        raw += rgba[row * stride : (row + 1) * stride]

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def decode_png(png: bytes) -> tuple[int, int, bytes]:
    """只解本脚本写出的那种 PNG（8 位 RGBA、过滤 0、非隔行），返回 ``(宽, 高, RGBA)``。"""
    if png[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("不是 PNG")
    pos = 8
    width = height = 0
    idat = bytearray()
    while pos < len(png):
        (length,) = struct.unpack(">I", png[pos : pos + 4])
        tag = png[pos + 4 : pos + 8]
        payload = png[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if tag == b"IHDR":
            width, height, depth, color, _, _, interlace = struct.unpack(">IIBBBBB", payload)
            if (depth, color, interlace) != (8, 6, 0):
                raise ValueError(f"只支持 8 位 RGBA 非隔行 PNG，得到 depth={depth} color={color}")
        elif tag == b"IDAT":
            idat += payload
        elif tag == b"IEND":
            break
    raw = zlib.decompress(bytes(idat))
    stride = width * 4
    out = bytearray()
    for row in range(height):
        start = row * (stride + 1)
        if raw[start] != 0:
            raise ValueError("只支持过滤类型 0 的 PNG")
        out += raw[start + 1 : start + 1 + stride]
    return width, height, bytes(out)


def encode_ico(frames: Iterable[tuple[int, bytes]]) -> bytes:
    """把若干 PNG 帧封成 ICO。PNG 压缩帧由 Windows Vista+ 与 PyInstaller 6.x 支持。"""
    frames = list(frames)
    header = struct.pack("<HHH", 0, 1, len(frames))
    offset = 6 + 16 * len(frames)
    directory = bytearray()
    payload = bytearray()
    for size, png in frames:
        directory += struct.pack(
            "<BBBBHHII",
            0 if size >= 256 else size,  # 256 用 0 表示
            0 if size >= 256 else size,
            0,  # 调色板色数（真彩为 0）
            0,  # 保留
            1,  # 色彩平面
            32,  # 位深
            len(png),
            offset,
        )
        payload += png
        offset += len(png)
    return header + bytes(directory) + bytes(payload)


def read_ico(png_only: bool = True) -> dict[int, bytes]:
    """解出 ICO 各帧；默认只接受 PNG 帧（本脚本的产物形态）。"""
    data = (STATIC / "favicon.ico").read_bytes()
    _, kind, count = struct.unpack("<HHH", data[:6])
    if kind != 1:
        raise ValueError("不是 ICO")
    frames: dict[int, bytes] = {}
    for index in range(count):
        entry = data[6 + 16 * index : 22 + 16 * index]
        width, height, _, _, _, _, length, offset = struct.unpack("<BBBBHHII", entry)
        size, tall = width or 256, height or 256
        if size != tall:
            raise ValueError(f"ICO 帧不是正方形：{size}×{tall}")
        blob = data[offset : offset + length]
        if png_only and blob[:8] != b"\x89PNG\r\n\x1a\n":
            raise ValueError(f"{size}px 帧不是 PNG")
        frames[size] = blob
    return frames


# ---------------------------------------------------------------- SVG
def render_svg(*, full_bleed: bool = False) -> str:
    """由同一份几何生成 SVG，供看板顶栏内联使用。"""
    lines = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" width="1024" height="1024"'
        ' role="img" aria-label="数字报监测中心">',
        "  <title>数字报监测中心</title>",
    ]
    for rect in MARK:
        radius = 0 if (full_bleed and rect.y == 0) else rect.r
        lines.append(
            f'  <rect x="{rect.x:g}" y="{rect.y:g}" width="{rect.w:g}" height="{rect.h:g}"'
            f' rx="{radius:g}" fill="{rect.fill}"/>'
        )
    lines.append("</svg>")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- 入口
def build() -> dict[Path, bytes]:
    """生成全部产物（路径 → 内容）。"""
    frames = [(size, encode_png(render_rgba(size), size)) for size in ICO_SIZES]
    return {
        STATIC / "favicon.ico": encode_ico(frames),
        STATIC / "apple-touch-icon.png": encode_png(
            render_rgba(APPLE_TOUCH_SIZE, square_bg=True), APPLE_TOUCH_SIZE
        ),
        STATIC / "logo.svg": render_svg().encode("utf-8"),
    }


def write() -> None:
    for path, blob in build().items():
        path.write_bytes(blob)
        print(f"已写入 {path.relative_to(ROOT)}（{len(blob)} 字节）")


def check() -> int:
    """校验已提交的图标与当前几何一致；不一致返回非 0。

    比较**像素**而不是文件字节：deflate 的输出可能随 zlib 版本变化，
    拿文件哈希当验收标准会在别的机器上假报警。
    """
    problems: list[str] = []

    committed_frames = read_ico()
    if set(committed_frames) != set(ICO_SIZES):
        problems.append(f"favicon.ico 尺寸集合为 {sorted(committed_frames)}，期望 {sorted(ICO_SIZES)}")
    for size, blob in committed_frames.items():
        width, height, rgba = decode_png(blob)
        if (width, height) != (size, size):
            problems.append(f"favicon.ico 的 {size}px 帧实际是 {width}×{height}")
            continue
        if rgba != render_rgba(size):
            problems.append(f"favicon.ico 的 {size}px 帧与几何不一致")

    apple = STATIC / "apple-touch-icon.png"
    width, height, rgba = decode_png(apple.read_bytes())
    if (width, height) != (APPLE_TOUCH_SIZE, APPLE_TOUCH_SIZE):
        problems.append(f"apple-touch-icon.png 实际是 {width}×{height}")
    elif rgba != render_rgba(APPLE_TOUCH_SIZE, square_bg=True):
        problems.append("apple-touch-icon.png 与几何不一致")

    expected_svg = render_svg().encode("utf-8")
    if (STATIC / "logo.svg").read_bytes() != expected_svg:
        problems.append("logo.svg 与几何不一致")

    for problem in problems:
        print(f"✗ {problem}", file=sys.stderr)
    if problems:
        print("重跑 `python scripts/make_icons.py` 修复。", file=sys.stderr)
        return 1
    print("✓ 图标与 scripts/make_icons.py 的几何一致")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成品牌图标（纯标准库，可复现）")
    parser.add_argument("--check", action="store_true", help="只校验，不写文件")
    args = parser.parse_args(argv)
    if args.check:
        return check()
    write()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
