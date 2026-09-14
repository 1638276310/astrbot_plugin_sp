"""图片处理工具。

对应原插件的 ``lib/sharp-pixel.js``：
在图片上随机撒一些彩色噪点，并把结果写回文件 / 返回 bytes。
另外提供 bytes <-> base64 以及本地文件落盘的能力。
"""

from __future__ import annotations

import base64
import hashlib
import io
import random
from pathlib import Path

from PIL import Image as PILImage # type: ignore


def to_base64(data: bytes) -> str:
    """bytes -> base64 字符串（不带前缀）。"""
    return base64.b64encode(data).decode("ascii")


def from_base64(text: str) -> bytes:
    """base64 字符串 -> bytes，自动兼容 data URI 与 base64:// 前缀。"""
    if text.startswith("data:") and "," in text:
        text = text.split(",", 1)[1]
    if text.startswith("base64://"):
        text = text[len("base64://") :]
    return base64.b64decode(text)


def sha1_of(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def load_image(data: bytes) -> PILImage.Image:
    """从 bytes 打开图片（统一转成 RGB）。"""
    image = PILImage.open(io.BytesIO(data))
    if image.mode != "RGB":
        image = image.convert("RGB")
    return image


def _add_fast_noise(image: PILImage.Image, points: int) -> PILImage.Image:
    """用缩放小图叠加的方式快速撒噪点。

    原 JS 实现是直接改像素点，PIL 逐个 putpixel 在 Python 下太慢，
    这里先生成一张随机彩色小图再放大叠加，效果一致且快得多。
    """
    width, height = image.size
    if width <= 0 or height <= 0:
        return image

    small_w = max(1, width // 100)
    small_h = max(1, height // 100)
    noise = PILImage.new("RGB", (small_w, small_h))
    total = small_w * small_h
    hole_ratio = min(0.9, points / max(1, total))
    pixels = [
        (
            random.randint(0, 255),
            random.randint(0, 255),
            random.randint(0, 255),
        )
        if random.random() < hole_ratio
        else (0, 0, 0)
        for _ in range(total)
    ]
    noise.putdata(pixels)
    noise = noise.resize((width, height), PILImage.Resampling.NEAREST)

    # 用噪点图覆盖少量不透明区域，其余保持原图
    mask = PILImage.new("L", (width, height), 0)
    mask_pixels = [255 if random.random() < hole_ratio else 0 for _ in range(width * height)]
    mask.putdata(mask_pixels)
    return PILImage.composite(noise, image, mask)


def add_noise(data: bytes, min_points: int = 10, max_points: int = 60) -> bytes:
    """给图片叠加随机彩色噪点，返回 JPEG bytes。失败时原样返回。"""
    try:
        image = load_image(data)
        points = random.randint(min_points, max_points)
        result = _add_fast_noise(image, points)
        buffer = io.BytesIO()
        result.save(buffer, format="JPEG", quality=92)
        return buffer.getvalue()
    except Exception:
        return data


def save_bytes(path: Path, data: bytes) -> Path:
    """把 bytes 写入文件（自动建目录）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def guess_suffix(data: bytes, default: str = ".jpg") -> str:
    """根据文件头猜扩展名。"""
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return default
