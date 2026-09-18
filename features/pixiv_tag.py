"""功能：按标签搜索 P 站图片。

原 ``tag.js``：``/来10张白丝图``（X 为数量，最大 60）。
AstrBot 版指令名为 ``/来图``（``/来图 10 张 白丝图``），避免把单字"来"注册成
指令导致与日常口语（"来一杯奶茶"等）发生贪婪截胡。

R18 模式与图片偏好取自插件配置（由 ``/设置R18模式`` / ``/设置图片偏好`` 修改）。
指令 handler 由主类 main.py 注册（与 get_px 同构），
本文件保留解析正则，供 main.py 导入。
"""

from __future__ import annotations

import re

from ._pixiv_base import PixivBase

TAG_PATTERN = re.compile(r"来\s*(\d+)\s*张\s*(.*?)\s*图")


class PixivTagFeature(PixivBase):
    """P 站标签搜索相关业务方法（指令 handler 由主类 main.py 注册）。

    随机抽取逻辑统一由主类 ``spPlugin._random_ids`` 提供，本 Mixin 不再
    重复实现（MRO 上主类定义优先，此处的重复定义已移除）。
    """
