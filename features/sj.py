"""功能：随机短视频（骚鸡/烧鸡/sj）。

对应原 ``sj.js``：从配置的视频直链里随机取一个，下载后发送。
AstrBot 的 Video 组件可以直接吃网络 URL，但快手 CDN 需要 Referer，
所以这里先下载到临时目录再用本地文件发送。
"""

from __future__ import annotations

import random

from astrbot.api.event import AstrMessageEvent, filter

from .features._base import spFeature
from .app_core.http import fetch_bytes

TRIGGER = r"^#?(?:骚鸡|烧鸡|sj)$"

REFERER = "https://www.kuaishou.com/"


class VideoFeature(spFeature):
    """随机短视频指令。"""

    @filter.regex(TRIGGER, priority=20)
    async def sp_random_video(self, event: AstrMessageEvent):
        """随机发送一个涩批视频"""
        if not await self.guard(event):
            return

        urls = self.settings.sp_video_urls
        if not urls:
            yield event.plain_result("视频列表为空，请在插件配置 sp_video_urls 中补充。")
            return

        url = random.choice(urls)
        target = self.temp_path("sp_video.mp4")
        try:
            data = await fetch_bytes(
                url,
                timeout=90,
                referer=REFERER,
                max_size=200 * 1024 * 1024,
            )
            target.write_bytes(data)
        except Exception as exc:
            yield event.plain_result(f"视频发送失败，请稍后再试（{exc}）")
            return

        try:
            yield event.chain_result([self.video_component(target)])
        except Exception:
            yield event.plain_result("视频发送失败，请稍后再试")
        finally:
            self.safe_unlink(target)
