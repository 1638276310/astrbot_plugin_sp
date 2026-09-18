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
# 布局与配色（卡片式风格：浅灰底 + 白色圆角卡片 + 彩色标题栏）
# ---------------------------------------------------------------------- #

PAGE_WIDTH = 760
MARGIN = 28            # 页面左右外边距
CARD_MARGIN_X = MARGIN # 卡片与页面边缘的水平间距
TOP_PAD = 30
BOTTOM_PAD = 34
CARD_SPACING = 22      # 卡片之间间距
CARD_RADIUS = 14       # 卡片圆角
CARD_PAD_X = 22        # 卡片内部左右边距
CARD_PAD_TOP = 14      # 卡片标题栏与卡片顶部
CARD_PAD_BOTTOM = 16   # 卡片内容与卡片底部
HEADBAR_H = 48         # 彩色标题栏高度
GROUP_SPACING = 14     # 卡片内 标题栏 -> 条目 间距
HEADING_SIZE = 30      # 分组标题（标题栏内白字）
TITLE_SIZE = 40        # 顶部主标题
SUBTITLE_SIZE = 20
ITEM_SIZE = 21
NOTE_SIZE = 18
LINE_HEIGHT_FACTOR = 1.42

# 背景与卡片
BG_TOP = (236, 240, 248)     # 背景上（浅蓝灰）
BG_BOTTOM = (224, 228, 238)  # 背景下（略深，形成渐变）
CARD_BG = (255, 255, 255)    # 卡片白
CARD_BORDER = (226, 230, 238)
CARD_SHADOW = (180, 190, 205)

FG = (32, 34, 40)            # 正文
MUTED = (122, 128, 140)      # 说明/副文字
BORDER_LINE = (228, 231, 238)

# 顶部横幅
HERO_FROM = (47, 134, 189)
HERO_TO = (72, 60, 140)
HERO_FG = (255, 255, 255)
HERO_MUTED = (222, 232, 245)

# 每个分组的主题色（循环使用）：标题栏底色 + 圆点色
_GROUP_THEMES: list[tuple[tuple[int, int, int], tuple[int, int, int]]] = [
    ((47, 134, 189), (24, 92, 145)),    # 蓝
    ((146, 84, 146), (110, 54, 110)),   # 紫
    ((38, 150, 94), (26, 110, 70)),      # 绿
    ((220, 120, 40), (170, 88, 24)),     # 橙
    ((196, 58, 84), (150, 40, 62)),      # 红
    ((30, 140, 150), (20, 100, 110)),    # 青
    ((120, 96, 60), (90, 70, 44)),       # 棕
    ((90, 96, 110), (66, 70, 82)),       # 灰蓝
]

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


# ---------------------------------------------------------------------- #
# 辅助绘制
# ---------------------------------------------------------------------- #

def _draw_rounded_rect(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int],
                       radius: int, fill=None, outline=None, width: int = 1) -> None:
    """画圆角矩形（兼容 Pillow 各版本：优先 rounded_rectangle，退回四角圆+矩形拼接）。"""
    x0, y0, x1, y1 = box
    try:
        draw.rounded_rectangle((x0, y0, x1, y1), radius=radius,
                               fill=fill, outline=outline, width=width)
        return
    except Exception:
        pass
    # 退回方案：四角画扇形圆 + 中间矩形（老版 Pillow 没有 rounded_rectangle）
    if fill is not None:
        r = radius
        draw.rectangle((x0 + r, y0, x1 - r, y1), fill=fill)
        draw.rectangle((x0, y0 + r, x1, y1 - r), fill=fill)
        draw.pieslice((x0, y0, x0 + 2 * r, y0 + 2 * r), 180, 270, fill=fill)
        draw.pieslice((x1 - 2 * r, y0, x1, y0 + 2 * r), 270, 360, fill=fill)
        draw.pieslice((x0, y1 - 2 * r, x0 + 2 * r, y1), 90, 180, fill=fill)
        draw.pieslice((x1 - 2 * r, y1 - 2 * r, x1, y1), 0, 90, fill=fill)
    if outline is not None:
        r = radius
        draw.arc((x0, y0, x0 + 2 * r, y0 + 2 * r), 180, 270, fill=outline, width=width)
        draw.arc((x1 - 2 * r, y0, x1, y0 + 2 * r), 270, 360, fill=outline, width=width)
        draw.arc((x0, y1 - 2 * r, x0 + 2 * r, y1), 90, 180, fill=outline, width=width)
        draw.arc((x1 - 2 * r, y1 - 2 * r, x1, y1), 0, 90, fill=outline, width=width)
        draw.line((x0 + r, y0, x1 - r, y0), fill=outline, width=width)
        draw.line((x0 + r, y1, x1 - r, y1), fill=outline, width=width)
        draw.line((x0, y0 + r, x0, y1 - r), fill=outline, width=width)
        draw.line((x1, y0 + r, x1, y1 - r), fill=outline, width=width)


def _draw_soft_shadow(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int],
                      radius: int, offset: int = 3,
                      color: tuple[int, int, int] = CARD_SHADOW) -> None:
    """在 box 位置画一层柔和阴影（多层半透明偏移，模拟扩散效果）。

    直接用纯色叠层（JPEG 保存会把 alpha 压平，用 3 层同色不同透明度模拟）。
    """
    x0, y0, x1, y1 = box
    for i, op in enumerate((26, 44, 70)):  # 由浅到深三层
        off = offset + i * 2
        # 把阴影色与背景色按 op/100 混合
        bgc = CARD_BG
        mix = tuple(int(b * op / 100 + c * (100 - op) / 100)
                    for b, c in zip(color, bgc))  # type: ignore[operator]
        _draw_rounded_rect(
            draw,
            (x0 - 2 + off, y0 - 2 + off, x1 - 2 + off, y1 - 2 + off),
            radius=radius + 2,
            fill=mix,
        )


def _vgrad(color_a: tuple[int, int, int], color_b: tuple[int, int, int],
           h: int) -> list[tuple[int, int, int]]:
    """生成 h 行的纵向渐变色。"""
    out: list[tuple[int, int, int]] = []
    for i in range(h):
        t = i / max(1, h - 1)
        out.append(tuple(int(a + (b - a) * t) for a, b in zip(color_a, color_b)))  # type: ignore[arg-type]
    return out


def _hgrad(color_a: tuple[int, int, int], color_b: tuple[int, int, int],
           w: int) -> list[tuple[int, int, int]]:
    """生成 w 列的横向渐变色。"""
    out: list[tuple[int, int, int]] = []
    for i in range(w):
        t = i / max(1, w - 1)
        out.append(tuple(int(a + (b - a) * t) for a, b in zip(color_a, color_b)))  # type: ignore[arg-type]
    return out


def _draw_horizontal_gradient(draw: ImageDraw.ImageDraw, x0: int, y0: int, x1: int, y1: int,
                              color_a: tuple[int, int, int], color_b: tuple[int, int, int]) -> None:
    """把 (x0,y0)-(x1,y1) 区域填成横向渐变。"""
    cols = _hgrad(color_a, color_b, x1 - x0 + 1)
    for i, c in enumerate(cols):
        draw.line((x0 + i, y0, x0 + i, y1), fill=c)


# ---------------------------------------------------------------------- #
# 主渲染
# ---------------------------------------------------------------------- #

def _render_groups(
    groups: Iterable[dict],
    author: str,
    version: str,
) -> tuple[PILImage.Image, dict]:
    """把结构化帮助内容渲染成一张卡片风格 JPEG 长图。

    设计：浅灰渐变背景 + 顶部彩色横幅（主标题/副标题/版本徽标）+
    每分组一张白色圆角卡片（彩色标题栏 + 圆点条目 + 指令名高亮）+
    底部作者署名。返回 ``(image, meta)``；meta 含实际使用的字体文件
    路径与渲染尺寸。
    """
    # 字体
    f_title = get_font(TITLE_SIZE, bold=True)
    f_subtitle = get_font(SUBTITLE_SIZE)
    f_badge = get_font(16, bold=True)
    f_group = get_font(HEADING_SIZE, bold=True)
    f_item = get_font(ITEM_SIZE)
    f_item_bold = get_font(ITEM_SIZE, bold=True)
    f_note = get_font(NOTE_SIZE)

    # 用于量宽的探针画布
    probe = PILImage.new("RGB", (PAGE_WIDTH, 10), CARD_BG)
    draw_probe = ImageDraw.Draw(probe)

    # 行高
    f_h = _line_h(f_title)
    f_sh = _line_h(f_subtitle)
    f_gh = _line_h(f_group)
    f_ih = _line_h(f_item)
    f_nh = _line_h(f_note)

    # 卡片内文本可写宽度
    card_w = PAGE_WIDTH - 2 * CARD_MARGIN_X
    text_x0 = CARD_MARGIN_X + CARD_PAD_X
    text_w = card_w - 2 * CARD_PAD_X

    # 指令名（/xxx）高亮所需：条目行宽 = 圆点列 + 文本
    dot_gap = 14           # 圆点与文本间距
    dot_r = 5             # 圆点半径
    bullet_col = dot_gap + dot_r * 2 + 4
    item_wrap_w = text_w - bullet_col
    # 说明行（note）与指令行"续行"对齐到同一列（bullet_col），
    # 保证整块条目左右视觉整齐；note 不加圆点、字号更小、颜色更浅。
    note_indent = bullet_col

    blocks: list[dict] = []
    for idx, group in enumerate(groups):
        title = str(group.get("title", ""))
        items = group.get("items", [])
        theme = _GROUP_THEMES[idx % len(_GROUP_THEMES)]
        # 标题栏内文字量宽（标题栏可用宽 = 卡片宽 - 左右 pad）
        g_lines = wrap_text(title, f_group, text_w - 8, draw_probe)
        # 条目：把每条拆成 (text, is_note, wrapped_lines, first_line_x_extra)
        g_items: list[tuple[list[str], bool]] = []
        for item in items:
            if isinstance(item, dict):
                item_text = str(item.get("text", ""))
                bullet = bool(item.get("bullet", False))
            else:
                item_text, bullet = str(item), False
            is_note = not bullet
            wfont = f_note if is_note else f_item
            wrapped = wrap_text(item_text, wfont, item_wrap_w, draw_probe)
            g_items.append((wrapped, is_note))
        blocks.append({
            "theme": theme,
            "title_lines": g_lines,
            "items": g_items,
        })

    # 顶部横幅内容
    hero_title_lines = wrap_text("涩批 AstrBot 插件", f_title,
                                 PAGE_WIDTH - 2 * CARD_MARGIN_X - 40, draw_probe)
    hero_subtitle = f"指令手册 · {author}"

    # 版本徽标尺寸（固定 30px 高，保证小字号下也够看）
    badge_text = version
    badge_w = _text_width(draw_probe, badge_text, f_badge) + 24
    badge_h = 30

    # ------------------------------------------------------------------ #
    # 高度计算（先 dry-run 把总高算出来，再开画布实际绘制）
    # ------------------------------------------------------------------ #
    hero_h = (TOP_PAD + f_h * len(hero_title_lines) + 8 + f_sh + 14)
    card_heights: list[int] = []
    for blk in blocks:
        h = CARD_PAD_TOP + HEADBAR_H + GROUP_SPACING
        for lines, is_note in blk["items"]:
            if not lines:
                continue
            lh = f_nh if is_note else f_ih
            h += len(lines) * lh + 6
        h += CARD_PAD_BOTTOM
        card_heights.append(h)
    cards_h = sum(card_heights) + CARD_SPACING * (len(blocks) - 1)
    footer_h = 18 + f_sh + BOTTOM_PAD  # 分隔 + 署名
    height = hero_h + cards_h + footer_h + 6

    width = PAGE_WIDTH
    img = PILImage.new("RGB", (width, height), BG_TOP)
    draw = ImageDraw.Draw(img)

    # 背景：纵向渐变
    for i, c in enumerate(_vgrad(BG_TOP, BG_BOTTOM, height)):
        draw.line((0, i, width, i), fill=c)

    # ------------------------------------------------------------------ #
    # 顶部横幅
    # ------------------------------------------------------------------ #
    y = TOP_PAD
    hero_box_x0 = CARD_MARGIN_X - 4
    hero_box_x1 = width - CARD_MARGIN_X + 4
    hero_box_y0 = y - 12
    hero_box_y1 = y + f_h * len(hero_title_lines) + 8 + f_sh + 14 - 12
    _draw_soft_shadow(draw, (hero_box_x0, hero_box_y0, hero_box_x1, hero_box_y1),
                      radius=CARD_RADIUS, offset=4, color=(120, 130, 150))
    _draw_rounded_rect(draw, (hero_box_x0, hero_box_y0, hero_box_x1, hero_box_y1),
                       radius=CARD_RADIUS, fill=HERO_FROM)
    _draw_horizontal_gradient(
        draw, hero_box_x0 + 1, hero_box_y0 + 1,
        hero_box_x1 - 1, hero_box_y1 - 1, HERO_FROM, HERO_TO)
    # 主标题
    ty = y
    for ln in hero_title_lines:
        draw.text((text_x0, ty), ln, font=f_title, fill=HERO_FG)
        ty += f_h
    ty += 8
    # 副标题
    draw.text((text_x0, ty), hero_subtitle, font=f_subtitle, fill=HERO_MUTED)
    ty += f_sh + 14
    # 版本徽标（右上角）
    badge_x = hero_box_x1 - badge_w - 14
    badge_y = hero_box_y0 + 14
    _draw_rounded_rect(draw, (badge_x, badge_y, badge_x + badge_w, badge_y + badge_h),
                       radius=badge_h // 2, fill=(255, 255, 255))
    draw.text((badge_x + 12, badge_y + 5), badge_text, font=f_badge, fill=HERO_FROM)

    # ------------------------------------------------------------------ #
    # 卡片
    # ------------------------------------------------------------------ #
    y = hero_box_y1 + CARD_SPACING
    for bi, blk in enumerate(blocks):
        c_top = y
        c_h = card_heights[bi]
        c_box = (CARD_MARGIN_X, c_top, width - CARD_MARGIN_X, c_top + c_h)
        # 阴影 + 卡片底
        _draw_soft_shadow(draw, c_box, radius=CARD_RADIUS)
        _draw_rounded_rect(draw, c_box, radius=CARD_RADIUS, fill=CARD_BG,
                           outline=CARD_BORDER, width=1)
        # 标题栏（彩色渐变，只圆上角）
        theme_a, theme_b = blk["theme"]
        head_box = (CARD_MARGIN_X, c_top, width - CARD_MARGIN_X, c_top + CARD_PAD_TOP + HEADBAR_H)
        # 用圆角矩形画整个头部再裁：简单做法是画圆角矩形（整张头部圆角），
        # 与卡片圆角一致，视觉自然。
        _draw_rounded_rect(draw, head_box, radius=CARD_RADIUS, fill=theme_a)
        _draw_horizontal_gradient(draw, head_box[0] + 1, head_box[1] + 1,
                                  head_box[2] - 1, head_box[3] - 1,
                                  theme_a, theme_b)
        # 把标题栏下沿"接平"（盖住卡片内部圆角，使其与卡片身无缝衔接）
        _draw_rounded_rect(draw,
                           (CARD_MARGIN_X + 1, c_top + CARD_PAD_TOP + HEADBAR_H - 14,
                            width - CARD_MARGIN_X - 1, c_top + CARD_PAD_TOP + HEADBAR_H),
                           radius=0, fill=theme_b)
        # 标题栏内文字（白字，垂直居中于标题栏）
        ty = c_top + CARD_PAD_TOP + (HEADBAR_H - f_gh) // 2
        for tl in blk["title_lines"]:
            draw.text((text_x0, ty), tl, font=f_group, fill=(255, 255, 255))
            ty += f_gh

        # 条目
        iy = c_top + CARD_PAD_TOP + HEADBAR_H + GROUP_SPACING
        for lines, is_note in blk["items"]:
            if not lines:
                continue
            lh = f_nh if is_note else f_ih
            first = lines[0]
            if is_note:
                # 说明行：浅灰，无圆点，缩进
                nx = text_x0 + note_indent
                draw.text((nx, iy), first, font=f_note, fill=MUTED)
                iy += lh
                for cont in lines[1:]:
                    draw.text((nx, iy), cont, font=f_note, fill=MUTED)
                    iy += lh
                iy += 6
                continue
            # 指令行：彩色圆点 + 指令名高亮（/xxx 部分用主题深色加粗，其余正常）
            cy = iy + lh // 2
            dot_cx = text_x0 + dot_r
            draw.ellipse((dot_cx - dot_r, cy - dot_r, dot_cx + dot_r, cy + dot_r),
                         fill=theme_a)
            tx = text_x0 + bullet_col
            # 把指令行拆成"指令名"与"说明"两段高亮
            seg_text, seg_rest, split_x = _split_command(first, draw, f_item, f_item_bold, tx, ty=iy)
            draw.text((tx, iy), seg_text, font=f_item_bold, fill=theme_b)
            if seg_rest:
                rx = tx + split_x
                draw.text((rx, iy), seg_rest, font=f_item, fill=FG)
            iy += lh
            for cont in lines[1:]:
                # 续行缩进对齐到 bullet_col
                cx = text_x0 + bullet_col
                draw.text((cx, iy), cont, font=f_item, fill=FG)
                iy += lh
            iy += 6

        y = c_top + c_h + CARD_SPACING

    # ------------------------------------------------------------------ #
    # 底部署名
    # ------------------------------------------------------------------ #
    fy = y + 4
    draw.line((CARD_MARGIN_X + 8, fy, width - CARD_MARGIN_X - 8, fy),
              fill=BORDER_LINE, width=1)
    footer = f"作者：{author} · {version} · 渲染于本地（无需外部服务）"
    draw.text((CARD_MARGIN_X + 8, fy + 10), footer, font=f_subtitle, fill=MUTED)

    meta = {
        "font_file": str(_find_chinese_font() or "builtin"),
        "height": height,
        "width": width,
        "fallback_sizes": list(_fallback_sizes),
    }
    return img, meta


def _split_command(text: str, draw: ImageDraw.ImageDraw, font, bold_font,
                   x0: int, ty: int) -> tuple[str, str, int]:
    """把指令行拆成"指令名（/xxx）"与"其余说明"两段，返回
    (指令段文字, 剩余段文字, 指令段像素宽度)。

    规则：若文本以 "/" 开头，取到第一个空格为止作为指令段（加粗高亮），
    其余作为说明段；若不是 "/..."（说明行已在外部过滤），整段作指令段处理。
    """
    if text.startswith("/") and len(text) > 1:
        sp = text.find(" ")
        if sp > 0:
            cmd = text[:sp]
            rest = text[sp + 1:].lstrip()
            w = _text_width(draw, cmd, bold_font)
            return cmd, rest, w
    return text, "", _text_width(draw, text, bold_font)


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
