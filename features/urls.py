"""功能：多类型网址导航。

对应原 ``multiUrl.js``：``/写真网址`` / ``/福利网址`` / ``/吃瓜网址`` /
``/导航网址`` / ``/福利App`` / ``/TG电报``。

网址列表全部来自插件配置 ``url_groups``，可以在 WebUI 里增删。
指令 handler 由主类 main.py 注册（与 get_px 同构）。
"""

from __future__ import annotations

from ._base import spFeature


class UrlFeature(spFeature):
    """网址导航相关业务方法（指令 handler 与分组解析均在主类 main.py 上）。

    本网页导航功能逻辑极简（纯配置读取 + 消息发送），全部由
    ``main.py`` 的 ``sp_send_urls`` 与 ``_resolve_url_group`` 直接实现，
    本 Mixin 不保留独立方法，避免与 main.py 出现两套同义实现漂移。
    """
