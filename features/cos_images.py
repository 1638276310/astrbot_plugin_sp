"""功能：二/三次元图包。

对应原 ``tu.js``：``/2图``（二次元）与 ``/3图``（三次元/现实），
从配置的接口随机拉取 10 张图。

指令 handler 由主类 main.py 注册（与 get_px 同构），
本文件保留常量与常量映射，供 main.py 导入。
"""

from __future__ import annotations

from ._base import spFeature

IMAGE_COUNT = 10
CATEGORY_MAP = {"2图": "acg", "3图": "reality"}
TYPE_NAME = {"2图": "二次元图片", "3图": "现实图片"}


class CosImageFeature(spFeature):
    """2图 / 3图 相关业务方法（指令 handler 由主类 main.py 注册）。"""
