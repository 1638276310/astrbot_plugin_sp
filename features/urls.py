"""功能：多类型网址导航。

对应原 ``multiUrl.js``：``#写真网址`` / ``#福利网址`` / ``#吃瓜网址`` /
``#导航网址`` / ``#福利App`` / ``#TG电报``。

网址列表全部来自插件配置 ``url_groups``，可以在 WebUI 里增删。
"""

from __future__ import annotations

from astrbot.api.event import AstrMessageEvent, filter

from .features._base import spFeature

TRIGGER = r"^#?(?:写真网址|福利网址|吃瓜网址|导航网址|福利(?:App|APP|app)|TG电报)$"


class UrlFeature(spFeature):
    """网址导航指令。"""

    @filter.regex(TRIGGER, priority=30)
    async def sp_send_urls(self, event: AstrMessageEvent):
        """获取各类网站地址（写真/福利/吃瓜/导航/福利App/TG电报）"""
        if not await self.guard(event):
            return

        command = event.get_message_str().strip().lstrip("#")
        group = self._resolve_group(command)
        if group is None:
            yield event.plain_result("未识别的网址类型。")
            return

        urls = self.settings.url_group(group)
        if not urls:
            yield event.plain_result(
                f"「{group}」的网址列表为空，请在插件配置 url_groups 中补充。"
            )
            return

        header = [
            f"📱 {group}网址集合",
            f"📊 共 {len(urls)} 个{group}网站",
            "",
        ]
        body = [f"{index}. {url}" for index, url in enumerate(urls, start=1)]
        footer = [
            "",
            "⚠️ 提示：网址仅供参考，请谨慎访问",
            f"#{group}网址 可再次获取",
            "请复制链接到浏览器打开，切勿直接点击",
        ]
        text = "\n".join(header + body + footer)

        if not self.settings.forward_as_node:
            yield event.plain_result(text)
            return

        try:
            nodes = self.text_nodes(
                event, header + body + footer
            )
            yield event.chain_result([self.wrap_nodes(nodes)])
        except Exception:
            yield event.plain_result(text)

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
        # 配置里可能自定义了分组名，做一次宽松匹配
        for name in self.settings.group_names:
            if command == f"{name}网址":
                return name
        return None
