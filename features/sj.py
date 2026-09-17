"""功能：随机短视频（骚鸡/烧鸡/sj）。

对应原 ``sj.js``：从持久化的视频直链列表里随机取一个，下载后发送。
AstrBot 的 Video 组件可以直接吃网络 URL，但快手 CDN 需要 Referer，
所以这里先下载到临时目录再用本地文件发送。
"""

from __future__ import annotations

import random

from astrbot.api.event import AstrMessageEvent, filter # type: ignore

from ._base import spFeature
from ..app_core.http import fetch_bytes
from ..app_core.storage import load_json, save_json

REFERER = "https://www.kuaishou.com/"


class VideoFeature(spFeature):
    """随机短视频指令。"""

    def video_urls(self) -> list[str]:
        """读取骚鸡短视频直链列表。

        按照 AstrBot 插件规范，所有持久化数据存放在
        ``data/plugin_data/<plugin_name>/sp_video_urls.json`` 下，
        避免插件更新时被覆盖。
        若持久化文件不存在，会自动从 assets/sp_video_urls.json 初始化。
        """
        self.paths.ensure_video_urls()
        data = load_json(self.paths.video_urls_file, [])
        if isinstance(data, list) and data:
            return [str(item) for item in data]
        # 兜底：尝试从 assets 资源目录读取
        asset_file = self.paths.asset("sp_video_urls.json")
        if asset_file.exists():
            data = load_json(asset_file, [])
            if isinstance(data, list) and data:
                return [str(item) for item in data]
        # 兼容旧配置项兜底
        return getattr(self.settings, "sp_video_urls", [])

    def save_video_urls(self, urls: list[str]) -> bool:
        """保存视频列表到持久化目录。"""
        return save_json(self.paths.video_urls_file, urls)

    @filter.command("骚鸡", alias={"烧鸡", "sj"}, priority=20)
    async def sp_random_video(self, event: AstrMessageEvent):
        """随机发送一个涩批视频"""
        if not await self.guard(event):
            return

        urls = self.video_urls()
        if not urls:
            yield event.plain_result(
                f"视频列表为空，请在数据目录 {self.paths.video_urls_file} 中补充视频直链。"
            )
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
