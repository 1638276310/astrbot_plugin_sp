"""功能：多类型网址导航。

对应原 ``multiUrl.js``：``/写真网址`` / ``/福利网址`` / ``/吃瓜网址`` /
``/导航网址`` / ``/福利App`` / ``/TG电报``。

网址列表全部来自插件配置 ``url_groups``，可以在 WebUI 里增删。
指令 handler 由主类 main.py 注册（与 get_px 同构）。
"""

from __future__ import annotations

from ._base import spFeature


class UrlFeature(spFeature):
    """网址导航相关业务方法（指令 handler 由主类 main.py 注册）。"""

    def _resolve_group(self, command: str) -> str | None:
        """把指令名映射到配置中的分组名。"""
        command = command.strip()
        if command == "写真网址":
            return "写真"
        if command == "福利网址":
            return "福利"
        if command == "吃瓜网址":
            return "吃瓜"
        if command == "导航网址":
            return "导航"
        if command.lower().startswith("福利app"):
            return "福利App"
        if command == "TG电报":
            return "TG电报"
        for name in self.settings.group_names:
            if command == f"{name}网址":
                return name
        return None
