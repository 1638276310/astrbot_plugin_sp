"""功能：随机获取某画师的若干作品。

原 ``PixivArtistWorksFetcher.js``：``/随机3张123456作品``
（X 为张数，最大 20；Y 为画师 ID）。

指令 handler 由主类 main.py 注册（与 get_px 同构），
本文件保留解析正则，供 main.py 导入。
"""

from __future__ import annotations

import random
import re

from ._pixiv_base import PixivBase

ARTIST_PATTERN = re.compile(r"随机\s*(\d+)\s*张\s*(\d+)\s*作品")


class PixivArtistFeature(PixivBase):
    """P 站画师作品随机抽取相关业务方法（指令 handler 由主类 main.py 注册）。"""
