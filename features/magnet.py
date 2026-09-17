"""功能：磁力链接。

对应原 ``MagnetLinkFetcher.js``（验车）与 ``MagnetLinkMao.js``（磁力猫搜索）。

* ``/验车 magnet:...``                       —— 查询磁力链接详情并发送截图
* ``/磁力猫 <关键词> [类型] [排序] [数量]``    —— 磁力猫搜索

指令 handler 由主类 main.py 注册（与 get_px 同构），
本文件保留常量，供 main.py 导入。
"""

from __future__ import annotations

import re

from ._base import spFeature

MAGNET_LINK_PATTERN = re.compile(r"验车\s*(magnet:\S+)")
VERIFY_RETRY = 3
VERIFY_RETRY_DELAY = 2.0
MAX_SCREENSHOTS = 9


class MagnetFeature(spFeature):
    """磁力相关业务方法（指令 handler 由主类 main.py 注册）。"""
