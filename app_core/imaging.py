"""图片处理工具。

对应原插件的 ``lib/sharp-pixel.js``，并提供发送前的图片压缩：

* ``shrink_for_send`` —— 发送前把图片长边缩到 1280、按 JPEG 质量 80 重新
  压缩。肉眼几乎无感，但体积能降一个数量级，避免几十张原图塞进合并转发
  时超出 OneBot 的负载上限导致连接被断开；
* ``add_noise`` —— 在图片上叠加极轻微的像素噪点（对应原插件的
  sharp-pixel，用于规避部分图床/风控）；
* 另外提供 bytes <-> base64、本地文件落盘、扩展名识别等能力。
"""

from __future__ import annotations

import base64
import hashlib
import io
from pathlib import Path

from PIL import Image as PILImage # type: ignore

MAX_SEND_SIDE = 1280
"""发送前图片长边上限（像素）。"""

SEND_JPEG_QUALITY = 80
"""发送前重新压缩使用的 JPEG 质量。"""


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


def shrink_for_send(
    data: bytes,
    *,
    max_side: int = MAX_SEND_SIDE,
    quality: int = SEND_JPEG_QUALITY,
) -> bytes:
    """把图片缩到适合发送的尺寸并重新压缩。

    只做有损压缩：长边超过 ``max_side`` 时等比缩小，然后按 ``quality``
    存成渐进式 JPEG。这是人眼很难察觉、但体积能掉一个数量级的处理，
    不发原图就不会把合并转发撑到超出 OneBot 的负载上限。

    输入本身很小（长边不超过 ``max_side``）时不做放大，只重新压缩；
    处理失败（损坏图片、缺少 Pillow 等）时原样返回 bytes。
    """
    try:
        image = load_image(data)
        if max_side and max(image.size) > max_side:
            image.thumbnail((max_side, max_side), PILImage.Resampling.LANCZOS)
        buffer = io.BytesIO()
        image.save(
            buffer,
            format="JPEG",
            quality=quality,
            optimize=True,
            progressive=True,
        )
        return buffer.getvalue()
    except Exception:
        return data


def add_noise(
    data: bytes,
    strength: int = 3,
    *,
    max_side: int = 0,
    quality: int = SEND_JPEG_QUALITY,
) -> bytes:
    """给图片叠加极轻微的像素噪点并重新压缩，返回 JPEG bytes。

    以前这里是把噪点小图放大后整块 composite 盖上去，会出现一百像素宽
    的彩色色块，图直接废掉；而且按 quality=92 重存，几百 KB 的图能涨到
    2.5MB。现在改成每个像素随机加减 ±strength，肉眼基本看不出来，但足以
    让同一张图在不同次发送时哈希不同。

    仅当明确传入 ``max_side`` 时才会顺便缩放（默认 0 表示不缩放，因为调用
    方通常已经用 :func:`shrink_for_send` 缩过一次，避免重复缩放）。
    处理失败时原样返回 bytes。
    """
    try:
        import numpy as np  # type: ignore

        image = load_image(data)
        if max_side and max(image.size) > max_side:
            image.thumbnail((max_side, max_side), PILImage.Resampling.LANCZOS)
        arr = np.asarray(image).astype(np.int16)
        noise = np.random.randint(-strength, strength + 1, arr.shape, dtype=np.int16)
        arr = np.clip(arr + noise, 0, 255).astype(np.uint8)
        result = PILImage.fromarray(arr)
        buffer = io.BytesIO()
        result.save(
            buffer,
            format="JPEG",
            quality=quality,
            optimize=True,
            progressive=True,
        )
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
