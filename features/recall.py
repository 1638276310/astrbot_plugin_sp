"""功能：撤回开关、撤回时间、R18 模式、图片偏好。

对应原 ``recall.js``。原插件把设置写入 config/recall.yaml，
AstrBot 版直接写入插件配置（WebUI 可实时看到）。

指令 handler 全部由主类 ``main.py`` 注册（与 get_px 同构），
本文件只保留解析正则常量，供 main.py 导入使用。
"""

from __future__ import annotations

import re

from ._base import spFeature

# 内部解析正则（兼容 / 前缀 + 空格/连写数字，如 /设置sp撤回 60 或 /设置sp撤回60）
RECALL_TIME_PATTERN = re.compile(r"设置(?:sp|涩批|色皮|色批)撤回\s*(\d+)")
R18_PATTERN = re.compile(r"设置R18模式(0|1|2)")
PREFERENCE_PATTERN = re.compile(r"设置图片偏好(0|1|2)")


class RecallFeature(spFeature):
    """设置类业务方法（指令 handler 由主类 main.py 注册）。"""


__all__ = [
    "RECALL_TIME_PATTERN",
    "R18_PATTERN",
    "PREFERENCE_PATTERN",
    "RecallFeature",
]
