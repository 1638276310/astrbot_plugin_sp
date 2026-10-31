"""功能：按 PID 获取 P 站作品。

原 ``pid.js``：``/pid123456`` —— 取作品详情、下载全部图片、合并转发。
指令 handler 由主类 main.py 注册（与 get_px 同构），
本文件保留 PID 解析正则，供 main.py 导入。
"""

from __future__ import annotations

import re

from ._pixiv_base import PixivBase

PID_PATTERN = re.compile(r"pid\s*(\d+)")


class PixivPidFeature(PixivBase):
    """P 站 pid 相关业务方法（指令 handler 由主类 main.py 注册）。"""
