"""功能：撤回开关、撤回时间、R18 模式、图片偏好。

对应原 ``recall.js``。原插件把设置写入 config/recall.yaml，
AstrBot 版直接写入插件配置（WebUI 可实时看到）。
"""

from __future__ import annotations

import re

from astrbot.api.event import AstrMessageEvent, filter # type: ignore
from astrbot.api import logger  # type: ignore

from ._base import spFeature
from ..app_core.settings import ORDER_LABEL, ORDER_MAP, R18_MODE_LABEL, R18_MODE_MAP

# 内部解析正则（兼容 / 前缀 + 连写数字）
RECALL_TIME_PATTERN = re.compile(r"设置(?:sp|涩批|色皮|色批)撤回(\d+)")
R18_PATTERN = re.compile(r"设置R18模式(0|1|2)")
PREFERENCE_PATTERN = re.compile(r"设置图片偏好(0|1|2)")


class RecallFeature(spFeature):
    """设置类指令。"""

    # ------------------------------------------------------------------ #
    # /开启sp撤回 / /关闭sp撤回
    # ------------------------------------------------------------------ #
    @filter.command(
        "开启sp撤回",
        alias={
            "关闭sp撤回",
            "开启涩批撤回",
            "关闭涩批撤回",
            "开启色胚撤回",
            "关闭色胚撤回",
            "开启色批撤回",
            "关闭色批撤回",
            "开启色皮撤回",
            "关闭色皮撤回",
        },
        priority=40,
    )
    async def sp_toggle_recall(self, event: AstrMessageEvent):
        """开启/关闭涩批消息撤回（/开启sp撤回 / /关闭sp撤回）"""
        self.stop_event_if_needed(event)
        if not await self.guard(event):
            return

        if not await self.require_admin(event):
            return
        message = event.get_message_str()
        enabled = message.startswith("/开启") or message.startswith("开启")
        self.settings.recall = enabled
        if enabled:
            yield event.plain_result(
                f"已开启撤回功能（当前撤回时间 {self.settings.recall_time} 秒）"
            )
        else:
            yield event.plain_result("已关闭撤回功能")

    # ------------------------------------------------------------------ #
    # /设置sp撤回X
    # ------------------------------------------------------------------ #
    @filter.command(
        "设置sp撤回",
        alias={"设置涩批撤回", "设置色皮撤回", "设置色批撤回"},
        priority=40,
    )
    async def sp_set_recall_time(self, event: AstrMessageEvent, seconds: int = 0):
        """设置涩批消息撤回时间（10-120 秒）"""
        self.stop_event_if_needed(event)
        if not await self.guard(event):
            return

        if not await self.require_admin(event):
            return

        # 优先用空格传参（/设置sp撤回 60），否则连写解析（/设置sp撤回60）
        # 0 为合法入参（表示关闭撤回），不能用 "if not seconds" 判空
        if isinstance(seconds, int) and seconds >= 0:
            raw = str(seconds)
        else:
            m = RECALL_TIME_PATTERN.search(event.get_message_str())
            if not m:
                yield event.plain_result("用法：/设置sp撤回 60（10-120 秒）")
                return
            raw = m.group(1)
        seconds = int(raw)
        if seconds < 10 or seconds > 120:
            yield event.plain_result("建议设置为10-120秒哦")
            return
        self.settings.recall_time = seconds
        yield event.plain_result(f"已设置撤回时间为{seconds}秒")

    # ------------------------------------------------------------------ #
    # /设置R18模式X
    # ------------------------------------------------------------------ #
    @filter.command(
        "设置R18模式",
        alias={"设置r18模式"},
        priority=40,
    )
    async def sp_set_r18_mode(self, event: AstrMessageEvent, mode: int = -1):
        """设置 R18 模式（0 全部 / 1 非R18 / 2 R18）"""
        self.stop_event_if_needed(event)
        if not await self.guard(event):
            return

        if not await self.require_admin(event):
            return

        if mode >= 0 and mode in (0, 1, 2):
            raw = str(mode)
        else:
            m = R18_PATTERN.search(event.get_message_str())
            if not m:
                yield event.plain_result("用法：/设置R18模式 2（0:全部 1:非R18 2:R18）")
                return
            raw = m.group(1)
        mode = R18_MODE_MAP.get(raw, "all")
        self.settings.r18_mode = mode
        yield event.plain_result(
            f"已设置R18模式为{mode}（{R18_MODE_LABEL.get(mode, mode)}）\n"
            "0:全部    1:非R18    2:R18"
        )

    # ------------------------------------------------------------------ #
    # /设置图片偏好X
    # ------------------------------------------------------------------ #
    @filter.command("设置图片偏好", priority=40)
    async def sp_set_image_preference(self, event: AstrMessageEvent, pref: int = -1):
        """设置图片偏好（0 无偏好 / 1 男性 / 2 女性）"""
        self.stop_event_if_needed(event)
        if not await self.guard(event):
            return

        if not await self.require_admin(event):
            return

        if pref >= 0 and pref in (0, 1, 2):
            raw = str(pref)
        else:
            m = PREFERENCE_PATTERN.search(event.get_message_str())
            if not m:
                yield event.plain_result("用法：/设置图片偏好 1（0:无 1:男 2:女）")
                return
            raw = m.group(1)
        order = ORDER_MAP.get(raw, "popular_d")
        self.settings.image_preference = order
        yield event.plain_result(
            f"已设置图片偏好为{order}（{ORDER_LABEL.get(order, order)}）\n"
            "0:无偏好    1:男性偏好    2:女性偏好"
        )

    # ------------------------------------------------------------------ #
    # /sp状态
    # ------------------------------------------------------------------ #
    @filter.command("sp状态", priority=40)
    async def sp_status(self, event: AstrMessageEvent):
        """查看涩批插件运行状态"""
        self.stop_event_if_needed(event)
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
