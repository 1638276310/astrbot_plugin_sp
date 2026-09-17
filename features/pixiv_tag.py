"""功能：按标签搜索 P 站图片。

原 ``tag.js``：``/来10张白丝图``（X 为数量，最大 60）。

R18 模式与图片偏好取自插件配置（由 ``/设置R18模式`` / ``/设置图片偏好`` 修改）。
指令 handler 由主类 main.py 注册（与 get_px 同构），
本文件保留解析正则，供 main.py 导入。
"""

from __future__ import annotations

import re

from ._pixiv_base import PixivBase

TAG_PATTERN = re.compile(r"来\s*(\d+)\s*张\s*(.*?)\s*图")


class PixivTagFeature(PixivBase):
    """P 站标签搜索相关业务方法（指令 handler 由主类 main.py 注册）。"""

    @staticmethod
    def _random_ids(ids: list[str], count: int) -> list[str]:
        import random

        pool = list(ids)
        random.shuffle(pool)
        return pool[:count]
