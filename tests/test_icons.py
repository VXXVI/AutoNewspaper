"""品牌图标的**可复现性**与**小尺寸可读性**守护。

图标是二进制资源，改了 `MARK` 几何却忘了重跑生成脚本，或者手改 `favicon.ico`，
都是「肉眼一时看不出来、上线后在 Windows 上才发现」的问题。这里锁两件事：

1. 已提交的 `favicon.ico` / `apple-touch-icon.png` / `logo.svg` 必须与
   ``scripts/make_icons.py`` 的几何**逐像素一致**（比较像素而非文件字节：
   deflate 输出可能随 zlib 版本变化，拿哈希当验收会在别的机器上假报警）；
2. 16px 那一档必须还认得出是什么 —— 上一版图标就是在这里糊成一团的
   （白纸 + 0.65px 细线画在浅色底上，缩到 42px 几乎看不见）。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "make_icons.py"


def _load_generator():
    """按路径加载生成脚本（``scripts/`` 不是包，也不该为了测试变成包）。"""
    spec = importlib.util.spec_from_file_location("make_icons", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["make_icons"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def icons():
    return _load_generator()


def test_committed_icons_match_the_generator(icons):
    """图标与几何漂移时必须失败，并提示重跑生成脚本。"""
    assert icons.check() == 0


def test_check_tolerates_a_windows_crlf_checkout(icons, monkeypatch, tmp_path):
    """Windows 上 ``core.autocrlf=true`` 会把文本资源签出成 CRLF。

    回归：``check()`` 曾经拿 ``logo.svg`` 的**原始字节**与生成结果比较，于是 Windows CI
    稳定误报「图标与几何不一致」。行尾不该参与图标内容的比较。
    """
    for name in ("favicon.ico", "apple-touch-icon.png"):
        (tmp_path / name).write_bytes((icons.STATIC / name).read_bytes())
    svg = (icons.STATIC / "logo.svg").read_bytes().replace(b"\n", b"\r\n")
    (tmp_path / "logo.svg").write_bytes(svg)

    monkeypatch.setattr(icons, "STATIC", tmp_path)
    assert icons.check() == 0


def test_every_ico_frame_is_a_square_png_of_the_expected_size(icons):
    frames = icons.read_ico()
    assert sorted(frames) == list(icons.ICO_SIZES)
    for size, blob in frames.items():
        width, height, rgba = icons.decode_png(blob)
        assert (width, height) == (size, size)
        assert len(rgba) == size * size * 4
    # 16/24/32 是 Windows 任务栏与浏览器标签页真正会用的三档，一个都不能少
    assert {16, 24, 32} <= set(frames)


def _palette(icons):
    return {
        "blue": icons.BLUE,
        "paper": icons.PAPER,
        "amber": icons.AMBER,
        "brown": icons.BROWN,
        "rule": icons.RULE,
    }


def _rgb(value):
    return tuple(int(value.lstrip("#")[index : index + 2], 16) for index in (0, 2, 4))


def _shares(pixels, palette):
    """按「最接近的调色板颜色」分类统计占比。

    不比较精确色值：小尺寸下抗锯齿会把绝大多数边缘像素变成混合色，精确匹配会
    把「看得出来」误判成「没有」。
    """
    reference = {name: _rgb(value) for name, value in palette.items()}
    tally = dict.fromkeys(reference, 0)
    opaque = 0
    for pixel in pixels:
        if pixel[3] <= 0.0:
            continue
        opaque += 1
        rgb = tuple(round(channel * 255) for channel in pixel[:3])
        best = min(reference, key=lambda name: sum((rgb[i] - reference[name][i]) ** 2 for i in range(3)))
        tally[best] += 1
    return {name: count / opaque for name, count in tally.items()}


def test_mark_stays_readable_at_16px(icons):
    """16px 下必须还能看出「品牌蓝底 + 白纸 + 琥珀命中块」三件事。

    这是上一版翻车的那一档：白纸 + 0.65px 细线放在浅色底上，缩到 42px 就没了对比度。
    """
    shares = _shares(icons.render(16), _palette(icons))
    assert shares["blue"] > 0.35, "品牌蓝底在 16px 下必须占得住"
    assert shares["paper"] > 0.15, "白纸在 16px 下必须看得出来"
    assert shares["amber"] > 0.07, "琥珀命中块是唯一的语义焦点，16px 下不能消失"


def test_mark_uses_one_drawing_for_every_size(icons):
    """同一套几何覆盖 16→256：不再有「小尺寸简化版 / 大尺寸详细版」两套画风。

    回归：上一版把 16/24/32 画成一套、48 以上画成另一套，Windows 在不同尺寸下
    （任务栏 vs 桌面大图标 vs 安装包）会呈现不同图案。
    """
    palette = _palette(icons)
    for size in (16, 32, 64, 256):
        shares = _shares(icons.render(size), palette)
        for name in ("blue", "paper", "amber", "brown"):
            assert shares[name] > 0.01, f"{size}px 应包含 {name}（同一套画）"
        # 各尺寸的构成比例应当接近，而不是「小尺寸换了一套图」
        assert 0.3 < shares["blue"] < 0.65, f"{size}px 的蓝底占比应落在合理区间"


def test_apple_touch_icon_is_fully_opaque(icons):
    """iOS 自己裁圆角，touch icon 不能自带透明角（会在主屏上露出背景）。"""
    pixels = icons.render(180, square_bg=True)
    assert all(pixel[3] == 1.0 for pixel in pixels)
    _, _, rgba = icons.decode_png(icons.build()[icons.STATIC / "apple-touch-icon.png"])
    assert all(alpha == 255 for alpha in rgba[3::4])


def test_svg_is_generated_from_the_same_geometry(icons):
    svg = icons.render_svg()
    assert svg.startswith("<svg") and 'viewBox="0 0 1024 1024"' in svg
    assert len(svg.splitlines()) == len(icons.MARK) + 3  # 首尾两行 + title
    for rect in icons.MARK:
        assert rect.fill in svg
