"""功能：撤回开关、撤回时间、R18 模式、图片偏好。

对应原 ``recall.js``。原插件把设置写入 config/recall.yaml，
AstrBot 版直接写入插件配置（WebUI 可实时看到）。
"""

from __future__ import annotations

from astrbot.api.event import AstrMessageEvent, filter # type: ignore
from astrbot.api import logger  # type: ignore

from ._base import spFeature
from ..app_core.settings import ORDER_LABEL, ORDER_MAP, R18_MODE_LABEL, R18_MODE_MAP

TOGGLE_TRIGGER = r"^#?(?:开启|关闭)(?:sp|涩批|色胚|色批|色皮)撤回$"
TIME_TRIGGER = r"^#?设置(?:sp|涩批|色皮|色批)撤回(\d+)$"
R18_TRIGGER = r"^#?设置R18模式(0|1|2)$"
PREFERENCE_TRIGGER = r"^#?设置图片偏好(0|1|2)$"


class RecallFeature(spFeature):
    """设置类指令。"""

    @filter.regex(TOGGLE_TRIGGER, priority=40)
    async def sp_toggle_recall(self, event: AstrMessageEvent):
        """开启/关闭涩批消息撤回"""
        if not await self.guard(event):
            return

        if not await self.require_admin(event):
            return
        message = event.get_message_str()
        enabled = message.startswith("#开启") or message.startswith("开启")
        self.settings.recall = enabled
        if enabled:
            yield event.plain_result(
                f"已开启撤回功能（当前撤回时间 {self.settings.recall_time} 秒）"
            )
        else:
            yield event.plain_result("已关闭撤回功能")

    @filter.regex(TIME_TRIGGER, priority=40)
    async def sp_set_recall_time(self, event: AstrMessageEvent):
        """设置涩批消息撤回时间（10-120 秒）"""
        if not await self.guard(event):
            return

        if not await self.require_admin(event):
            return
        raw = self.extract_number(event.get_message_str(), TIME_TRIGGER)
        if raw is None:
            return
        seconds = int(raw)
        if seconds < 10 or seconds > 120:
            yield event.plain_result("建议设置为10-120秒哦")
            return
        self.settings.recall_time = seconds
        yield event.plain_result(f"已设置撤回时间为{seconds}秒")

    @filter.regex(R18_TRIGGER, priority=40)
    async def sp_set_r18_mode(self, event: AstrMessageEvent):
        """设置 R18 模式（0 全部 / 1 非R18 / 2 R18）"""
        if not await self.guard(event):
            return

        if not await self.require_admin(event):
            return
        raw = self.extract_number(event.get_message_str(), R18_TRIGGER)
        if raw is None:
            return
        mode = R18_MODE_MAP.get(raw, "all")
        self.settings.r18_mode = mode
        yield event.plain_result(
            f"已设置R18模式为{mode}（{R18_MODE_LABEL.get(mode, mode)}）\n"
            "0:全部    1:非R18    2:R18"
        )

    @filter.regex(PREFERENCE_TRIGGER, priority=40)
    async def sp_set_image_preference(self, event: AstrMessageEvent):
        """设置图片偏好（0 无偏好 / 1 男性 / 2 女性）"""
        if not await self.guard(event):
            return

        if not await self.require_admin(event):
            return
        raw = self.extract_number(event.get_message_str(), PREFERENCE_TRIGGER)
        if raw is None:
            return
        order = ORDER_MAP.get(raw, "popular_d")
        self.settings.image_preference = order
        yield event.plain_result(
            f"已设置图片偏好为{order}（{ORDER_LABEL.get(order, order)}）\n"
            "0:无偏好    1:男性偏好    2:女性偏好"
        )

    @filter.regex(r"^#?sp状态$", priority=40)
    async def sp_status(self, event: AstrMessageEvent):
        """查看涩批插件运行状态"""
        if not await self.guard(event):
            return

        lines = [
            "【涩批插件状态】",
            f"撤回：{'开启' if self.settings.recall else '关闭'}"
            f"（{self.settings.recall_time} 秒）",
            f"R18模式：{self.settings.r18_mode}"
            f"（{R18_MODE_LABEL.get(self.settings.r18_mode, '')}）",
            f"图片偏好：{self.settings.image_preference}"
            f"（{ORDER_LABEL.get(self.settings.image_preference, '')}）",
            f"合并转发：{'开启' if self.settings.forward_as_node else '关闭'}",
            f"图片噪点：{'开启' if self.settings.track_pixel else '关闭'}",
            f"数据目录：{self.paths.root}",
        ]
        scheduler = getattr(self, "scheduler", None)
        if scheduler is not None:
            hints = scheduler.next_run_hint()
            lines.append("定时任务：" + ("；".join(hints) if hints else "未启用"))
        yield event.plain_result("\n".join(lines))
