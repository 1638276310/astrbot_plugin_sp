"""本地图片渲染工具。

用 Pillow 在内存中把文本直接画成一张长图，不依赖任何外部服务
（不需要 playwright / 截图 API），也无需预先生成静态图片。

渲染结果随调用即时生成：帮助文本（``build_help_text``）改一处，
下次调 ``render_text_help_image`` 出来的图就跟着变，无需重新打包。

字体解析顺序：
1. ``plugins/assets/fonts/*.ttf|*.otf``（插件目录内可放自定义中文字体）
2. AstrBot 安装目录及常见系统字体目录
3. Pillow 默认字体（非中文会缺字，仅作最后兜底）
"""

from __future__ import annotations

import io
import os
import shutil
from pathlib import Path
from typing import Iterable

from PIL import Image as PILImage, ImageDraw, ImageFont # type: ignore[import]

from .imaging import save_bytes

# ---------------------------------------------------------------------- #
# 布局与配色（与原 HTML 版 HELP_TEMPLATE 保持一致的观感）
# ---------------------------------------------------------------------- #

PAGE_WIDTH = 760
LEFT_PAD = 36
RIGHT_PAD = 36
TOP_PAD = 36
BOTTOM_PAD = 44
GROUP_SPACING = 22
HEADING_SIZE = 34
TITLE_SIZE = 42
SUBTITLE_SIZE = 20
ITEM_SIZE = 22
LINE_HEIGHT_FACTOR = 1.35

BG = (253, 253, 250)
FG = (32, 34, 36)
MUTED = (102, 107, 112)
ACCENT = (47, 134, 189)
ACCENT_DARK = (24, 92, 145)
HEADING_RULE = (224, 227, 231)

# 插件自带字体目录（assets/fonts，随插件 zip 一起分发；
# 用户往这里丢一个中文字体文件即可，渲染时自动优先命中，
# 若系统缺字体还会自动尝试把它安装到系统字体目录）
_ASSET_FONTS_DIR = os.path.join(os.path.dirname(__file__), "..", "assets", "fonts")

# 常见中文字体候选（按平台与常见安装位置），找不到就用 Pillow 默认字体
_FONT_CANDIDATES: list[str] = [
    # 插件自带字体目录（优先级最高，便于把思源黑体等放进去）
    _ASSET_FONTS_DIR,
    # 系统字体目录
    "/usr/share/fonts",
    "/usr/share/fonts/noto-cjk",
    "/usr/share/fonts/opentype/noto",
    "/usr/local/share/fonts",
    "/usr/share/fonts/wqy-microhei",
    "/usr/share/fonts/wqy-zenhei",
    "/usr/share/fonts/ARPL",
    "C:/Windows/Fonts",
    "C:\\Windows\\Fonts",
]

_FONT_FILE_NAMES: list[str] = [
    "NotoSansCJK-Regular.ttc",
    "NotoSansCJKsc-Regular.otf",
    "NotoSansCJK-Regular.ttc",
    "NotoSerifCJK-Regular.ttc",
    "SourceHanSansSC-Regular.otf",
    "SourceHanSans-Regular.otf",
    "wqy-microhei.ttc",
    "wqy-microhei.ttf",
    "wqy-zenhei.ttc",
    "DengXian.ttf",
    "msyh.ttc",
    "msyh.ttf",
    "simhei.ttf",
    "SimHei.ttf",
    "SimSun.ttf",
    "ARPLUCIGN.ttf",
    "PingFang.ttc",
    "PingFangSC-Regular.otf",
]


def _find_chinese_font() -> Path | None:
    """在候选目录里找一个可用的中文字体文件。"""
    for base in _FONT_CANDIDATES:
        if not base or not os.path.isdir(base):
            continue
        # 先按常见文件名精确匹配
        for name in _FONT_FILE_NAMES:
            p = Path(base) / name
            if p.is_file():
                return p
        # 再按扩展名粗匹配（按目录深度）
        try:
            files = sorted(Path(base).rglob("*"))
        except OSError:
            continue
        for f in files:
            if f.suffix.lower() in {".ttf", ".ttc", ".otf"} and f.is_file():
                return f
    return None


# 缓存键：(size, bold)；值为字体对象或 None。
# None 表示该字号探测过但没有可用 truetype 字体，下次直接走 load_default，
# 避免重复探测，同时不缓存兜底字体（防止缓存投毒）。
_font_cache: dict[tuple[int, int], ImageFont.FreeTypeFont | None] = {}

# 记录哪些字号"实际走了兜底默认字体"（而非真正的中文字体），
# 供 meta 输出诊断：若某字号因缺字体而画成方块，这里会列出它。
_fallback_sizes: list[int] = []

_MIS = object()  # 哨兵，区分"键不存在"和"键存在但值为 None"


def get_font(size: int, bold: bool = False):
    """加载指定大小的字体，带缓存。

    - 按顺序探测各候选目录：先精确匹配常见中文字体文件名，再按扩展名粗匹配；
    - 找不到时回退到 Pillow 默认字体（无法正确显示中文，但渲染不抛错）；
    - 找到后缓存该路径，下次调用直接复用，避免重复扫目录。
    """
    key = (size, int(bold))
    cached = _font_cache.get(key, _MIS)
    if cached is not _MIS:
        return cached if cached is not None else _load_default(size)

    found = _find_chinese_font()
    if found is not None:
        try:
            font = ImageFont.truetype(str(found), size)
            _font_cache[key] = font
            return font
        except Exception:
            pass
    _font_cache[key] = None
    if size not in _fallback_sizes:
        _fallback_sizes.append(size)
    return _load_default(size)


def _load_default(size: int):
    """Pillow 默认字体兜底。"""
    try:
        return ImageFont.load_default()
    except Exception:  # pragma: no cover
        return ImageFont.load_default(size=max(12, size // 2))


def wrap_text(text: str, font, max_width: int, draw: ImageDraw.ImageDraw) -> list[str]:
    """把文本按像素宽度折行（中英混排按字符累加宽度）。"""
    if not text:
        return []
    lines: list[str] = []
    current = ""
    for ch in text:
        if ch == "\n":
            if current:
                lines.append(current)
            current = ""
            continue
        trial = current + ch
        width = _text_width(draw, trial, font)
        if width <= max_width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = ch
    if current:
        lines.append(current)
    return lines


def _text_width(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    try:
        # 新版 Pillow：bbox 带 (0,0) 起点
        l, t, r, b = draw.textbbox((0, 0), text, font=font)
        return r - l
    except Exception:
        return int(draw.textlength(text, font=font))


def _make_space_prefix(target_px: int, font, draw: ImageDraw.ImageDraw) -> str:
    """生成若干空格，使其总像素宽度尽量接近 target_px（用于续行缩进对齐）。"""
    if target_px <= 0:
        return ""
    space_w = _text_width(draw, " ", font)
    if space_w <= 0:
        return " " * 4
    n = max(1, round(target_px / space_w))
    return " " * n


def _render_groups(
    groups: Iterable[dict],
    author: str,
    version: str,
) -> tuple[PILImage.Image, dict]:
    """把结构化帮助内容渲染成一张 JPEG 长图。

    返回 ``(image, meta)``；meta 含实际使用的字体文件路径与渲染尺寸。
    """
    draw_probe = ImageDraw.Draw(PILImage.new("RGB", (PAGE_WIDTH, 10)))
    f_title = get_font(TITLE_SIZE, bold=True)
    f_subtitle = get_font(SUBTITLE_SIZE)
    f_group = get_font(HEADING_SIZE, bold=True)
    f_item = get_font(ITEM_SIZE)

    # 预折行。每个分组渲染成一个 dict：
    #   title_lines: list[str]  分组标题（已按版宽折行）
    #   items:       list[str]  条目（已折行，首行已带上"· "前缀，续行已带上
    #                            与"· "等像素宽的空格前缀 —— 量宽与画宽使用完全
    #                            相同的字符串，符号列与文本列像素级对齐、不越界）
    # 每个条目折行后渲染成一个元组：(text, prefix, is_note)
    #   text:    该行的实际文字（不含前缀）
    #   prefix:  该行文字前的前缀（"· " / 等宽空格 / 说明行的双空格）
    #   is_note: True = 说明行（用浅灰渲染，退后一级），False = 指令行（正常黑字）
    # 每个 block 固定两个 key：
    #   "title_lines": list[str]
    #   "items":       list[tuple[str, str, bool]]
    # （dict 的 value 类型保持并集即可；下面的 BlockTitleLines 别名只在
    # 需要单独收紧某个 key 的类型时使用，用于消除类型检查器的误报。）
    BlockTitleLines = list[str]
    blocks: list[dict[str, BlockTitleLines | list[tuple[str, str, bool]]]] = []
    # 计算"· "在该字号下的真实像素宽度，续行用等宽空格对齐
    bullet_px = _text_width(draw_probe, "· ", f_item)
    # 说明行（note）前缀：比指令行多退一级（两个全角空格 ≈ 2 个字符宽）
    note_prefix = "\u3000\u3000"
    note_prefix_px = _text_width(draw_probe, note_prefix, f_item)
    # 折行预留宽度必须取"实际会被用到的最大前缀宽"（指令行的"· " 与
    # 说明行的 note_prefix 两者取大），否则说明行会比指令行更靠右、
    # 长行容易越出 RIGHT_PAD。
    max_prefix_px = max(bullet_px, note_prefix_px)
    item_wrap_w = PAGE_WIDTH - LEFT_PAD - RIGHT_PAD - max_prefix_px
    # 指令行续行缩进前缀（与"· "等像素宽），整张图共用一个，算一次即可
    cont_prefix = _make_space_prefix(bullet_px, f_item, draw_probe)
    for group in groups:
        title = group.get("title", "")
        items = group.get("items", [])
        g_lines = wrap_text(title, f_group, PAGE_WIDTH - LEFT_PAD - RIGHT_PAD, draw_probe)
        g_items: list[tuple[str, str, bool]] = []
        for item in items:
            # item 可能是 dict（含 text/bullet 标记）或纯 str（旧格式兼容）
            if isinstance(item, dict):
                item_text = str(item.get("text", ""))
                bullet = bool(item.get("bullet", False))
            else:
                item_text, bullet = str(item), False
            is_note = not bullet
            prefix_first = ("· " if bullet else note_prefix)
            prefix_cont = (cont_prefix if bullet else note_prefix)
            item_lines = wrap_text(item_text, f_item, item_wrap_w, draw_probe)
            for i, ln in enumerate(item_lines):
                g_items.append((ln, prefix_first if i == 0 else prefix_cont, is_note))
        blocks.append({
            "title_lines": g_lines,
            "items": g_items,
        })

    title_lines = wrap_text("涩批 AstrBot 插件 · 指令手册", f_title,
                            PAGE_WIDTH - LEFT_PAD - RIGHT_PAD, draw_probe)
    subtitle = f"AstrBot 版 · 作者：{author} · {version}"

    # ------------------------------------------------------------------ #
    # 高度计算与绘制共用同一套步进逻辑（先 dry-run 算高度，再实际绘制）。
    # 这样"要占多高"和"y 实际走多少"永远一致，改间距时只改一处。
    # ------------------------------------------------------------------ #
    f_h = _line_h(f_title)
    f_sh = _line_h(f_subtitle)
    f_gh = _line_h(f_group)
    f_ih = _line_h(f_item)

    def _layout_y(
        start_y: int,
        real: bool,
        draw: ImageDraw.ImageDraw | None = None,
    ) -> int:
        """从 start_y 开始排布标题/副标题/各分组，返回画完后的 y。

        - ``real=False``（必须不传 ``draw``）：只按步进累加高度、不写像素，
          用于先算出图高；
        - ``real=True``（必须传 ``draw``）：把文本/横线真正画到 ``draw`` 上。

        显式传参而不是闭包引用外层 ``draw``，避免 dry-run 时外层变量
        尚未赋值导致的 NameError 隐患。
        """
        if real and draw is None:
            raise ValueError("real=True 必须传入 draw")
        y = start_y
        # 主标题（比分组标题更大，视觉分层）
        for ln in title_lines:
            if real:
                assert draw is not None
                draw.text((LEFT_PAD, y), ln, font=f_title, fill=FG)
            y += f_h
        y += 4
        # 副标题
        if real:
            assert draw is not None
            draw.text((LEFT_PAD, y), subtitle, font=f_subtitle, fill=MUTED)
        y += f_sh + 18

        for blk in blocks:
            # 分组标题
            for tl in blk["title_lines"]:
                if real:
                    assert draw is not None
                    draw.text((LEFT_PAD, y), tl, font=f_group, fill=ACCENT_DARK)
                y += f_gh
            y += 4
            # 标题下的短横线
            if real:
                assert draw is not None
                assert isinstance(blk["title_lines"], BlockTitleLines)  # 收紧类型
                first_title = blk["title_lines"][0] if blk["title_lines"] else ""
                rule_w = min(60, _text_width(draw, first_title, f_group))
                draw.line((LEFT_PAD, y, LEFT_PAD + rule_w, y),
                          fill=HEADING_RULE, width=2)
            y += 8
            # 条目（每项是 (text, prefix, is_note) 元组）
            for text, prefix, is_note in blk["items"]:
                if real:
                    assert draw is not None
                    fill_color = MUTED if is_note else FG
                    draw.text((LEFT_PAD, y), prefix + text, font=f_item, fill=fill_color)
                y += f_ih
            y += GROUP_SPACING
        return y

    # dry-run：只按步进累加高度（real=False 不写像素），算出正文实际占用高度
    content_h = _layout_y(TOP_PAD, real=False) - TOP_PAD

    # 正文后预留"底部签名线 + 署名"的高度，再加底边距
    footer_gap = 18          # 正文 -> 签名线
    footer_line_h = 1        # 签名线本身
    footer_text_gap = 8      # 签名线 -> 署名字
    footer_text_h = f_sh     # 署名一行
    height = (TOP_PAD + content_h
              + footer_gap + footer_line_h + footer_text_gap + footer_text_h
              + BOTTOM_PAD)

    width = PAGE_WIDTH
    img = PILImage.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(img)

    # 实际绘制
    y_end = _layout_y(TOP_PAD, real=True, draw=draw)   # 正文画完的 y（= TOP_PAD + content_h）
    footer_line_y = y_end + footer_gap      # 签名线位置（锚定到正文，不再落在组间距中间）
    draw.line((LEFT_PAD, footer_line_y, width - RIGHT_PAD, footer_line_y),
              fill=HEADING_RULE, width=1)
    footer_text_y = footer_line_y + footer_line_h + footer_text_gap
    footer = f"作者：{author} · {version}"
    draw.text((width - RIGHT_PAD - _text_width(draw, footer, f_subtitle),
               footer_text_y), footer, font=f_subtitle, fill=MUTED)

    meta = {
        "font_file": str(_find_chinese_font() or "builtin"),
        "height": height,
        "width": width,
        "fallback_sizes": list(_fallback_sizes),
    }
    return img, meta


def _line_h(font) -> int:
    """按字号估算行高。

    FreeTypeFont / ImageFont 暴露 ``size``（即加载时的字号）；
    中文字符渲染高度更接近字号本身，故用 1.35 系数比 1.7 更紧凑。
    """
    try:
        return int(font.size * LINE_HEIGHT_FACTOR)
    except Exception:
        return 36


# ---------------------------------------------------------------------- #
# 对外接口
# ---------------------------------------------------------------------- #

def render_text_help_image(
    groups: Iterable[dict],
    author: str,
    version: str,
    out_path: Path | str,
) -> Path:
    """渲染帮助内容并落盘为 JPEG，返回本地路径。

    每次调用都会重新渲染，保证与最新帮助内容一致；不会做静态缓存。
    """
    img, _meta = _render_groups(groups, author, version)
    out = Path(out_path)
    save_bytes(out, _to_jpeg_bytes(img, quality=92))
    return out


def _to_jpeg_bytes(img: PILImage.Image, quality: int = 92) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def find_asset_fonts() -> list[Path]:
    """列出 plugins/assets/fonts 下可用的字体文件（供诊断用）。"""
    d = Path(__file__).resolve().parent.parent / "assets" / "fonts"
    if not d.is_dir():
        return []
    out: list[Path] = []
    for f in sorted(d.iterdir()):
        if f.suffix.lower() in {".ttf", ".ttc", ".otf"} and f.is_file():
            out.append(f)
    return out


# ---------------------------------------------------------------------- #
# 字体缺失诊断与自动修复
# ---------------------------------------------------------------------- #

# 各平台把"插件自带字体"安装到系统字体目录的目标位置（按优先级排列）
_SYSTEM_FONT_INSTALL_DIRS: list[str] = [
    # Linux: 用户级字体目录（无需 root，fc-cache 自动刷新）
    os.path.expanduser("~/.local/share/fonts"),
    # Linux: 系统级（需要写权限，通常是 root 部署 AstrBot 时才有效）
    "/usr/local/share/fonts",
    "/usr/share/fonts",
    # Windows: 系统字体目录（一般可写；若不可写则跳过）
    "C:/Windows/Fonts",
]


def diagnose_font_missing() -> dict:
    """诊断当前环境中中文字体是否缺失，返回诊断结果 dict。

    返回结构::

        {
            "missing": True,            # 是否缺失中文字体（走 Pillow 兜底则 True）
            "asset_fonts": [..],        # assets/fonts 下可用的字体文件
            "system_font_found": None, # 系统里已找到的字体路径（找不到为 None）
            "install_dir": None,       # 推荐安装目录
            "suggestion": "…",         # 给人看的修复建议
        }
    """
    asset_fonts = find_asset_fonts()
    system_font = _find_chinese_font()

    # 系统里找到了字体（含 assets/fonts），不存在缺失
    if system_font is not None:
        return {
            "missing": False,
            "asset_fonts": asset_fonts,
            "system_font_found": str(system_font),
            "install_dir": None,
            "suggestion": "字体正常：使用中文字体 "
                          f"{system_font}，无需处理。",
        }

    missing: dict = {
        "missing": True,
        "asset_fonts": asset_fonts,
        "system_font_found": None,
        "install_dir": _first_writable_install_dir(),
    }
    if asset_fonts:
        missing["suggestion"] = (
            f"系统缺中文字体，但插件 assets/fonts 下已有 {len(asset_fonts)} 个字体文件"
            f"（{asset_fonts[0].name} 等），可直接渲染；"
            "如需永久生效，可将它们安装到系统字体目录。"
        )
    else:
        missing["suggestion"] = (
            "系统缺中文字体，且插件 assets/fonts 目录为空。"
            "请往 assets/fonts/ 放一个中文字体文件（如 SimHei.ttf / "
            "SourceHanSansSC-Regular.otf / wqy-microhei.ttc），"
            "或手动安装系统字体："
            "Linux: sudo apt install fonts-wqy-microhei；"
            "Windows: 系统自带 SimHei.ttf，确认 C:/Windows/Fonts 可访问。"
        )
    return missing


def ensure_chinese_font() -> dict:
    """确保中文字体可用：系统缺字体时，自动把 assets/fonts 下的字体
    拷贝到系统字体目录，然后清空字体缓存重新探测。

    与 ``diagnose_font_missing`` 的区别：这个函数**会动手修**，
    修完立即让后续 ``get_font`` 命中新装好的字体。
    返回诊断结果 dict（同 ``diagnose_font_missing`` 的结构）。
    """
    diag = diagnose_font_missing()
    if not diag["missing"]:
        return diag

    if not diag["asset_fonts"]:
        return diag  # 没有自带字体可装，只能给建议

    install_dir = diag["install_dir"]
    if not install_dir:
        return diag

    copied: list[Path] = []
    for font_file in diag["asset_fonts"]:
        target = Path(install_dir) / font_file.name
        try:
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(font_file, target)
            copied.append(target)
        except OSError:
            continue

    if copied:
        # 让 Linux 的 fontconfig 立即感知新字体（不依赖重启动）
        _run_font_cache_refresh()
        # 清掉 render.py 内部的字体缓存，强制重新探测（含刚装的系统字体）
        _font_cache.clear()
        _fallback_sizes.clear()
        diag["system_font_found"] = str(_find_chinese_font())
        diag["missing"] = _find_chinese_font() is None
        diag["suggestion"] = (
            f"已自动把 {len(copied)} 个字体文件安装到 {install_dir}："
            f"{', '.join(str(p) for p in copied)}。"
            + ("" if not diag["missing"] else " 但仍未探测到可用字体，请手动检查。")
        )
    return diag


def _first_writable_install_dir() -> str | None:
    """按优先级找到第一个**可写**的系统字体安装目录。"""
    for d in _SYSTEM_FONT_INSTALL_DIRS:
        try:
            p = Path(d)
            p.mkdir(parents=True, exist_ok=True)
            probe = p / "._astrbot_sp_font_probe"
            probe.write_text("probe")
            probe.unlink()
            return d
        except (OSError, PermissionError):
            continue
    return None


def _run_font_cache_refresh() -> None:
    """触发 Linux 的 fc-cache 刷新（Windows 不需要，静默忽略失败）。"""
    if os.name != "posix":
        return
    import subprocess
    for cmd in (["fc-cache", "-fv"], ["fc-cache", "-f"]):
        try:
            subprocess.run(cmd, timeout=30, check=False,
                           capture_output=True)
            if cmd[1] == "-fv" or True:
                break
        except (OSError, subprocess.SubprocessError):
            continue
