"""各功能模块共用的基类。

插件主类同时继承 Star 与下面这些 Mixin，每个 Mixin 负责一组指令，
仍然满足"Handler 必须写在继承自 Star 的插件类中"的要求。
"""

from __future__ import annotations

import asyncio
import random
from typing import TYPE_CHECKING

from core.filecache import FileCache
from core.imaging import add_noise, save_bytes
from core.message import AdminMixin, MessageMixin
from core.paths import PluginPaths
from core.settings import PluginSettings

if TYPE_CHECKING:  # pragma: no cover
    from core.scheduler import TimeBasedScheduler


class spFeature(MessageMixin, AdminMixin):
    """单个功能模块的基类。

    宿主插件类需要提供 ``settings`` 与 ``paths`` 两个属性。
    """

    settings: PluginSettings
    paths: PluginPaths
    scheduler: "TimeBasedScheduler | None" = None
    """宿主插件的定时任务调度器（由 plugin.py 注入）。"""

    def setup_feature(self, settings: PluginSettings, paths: PluginPaths) -> None:
        """在插件初始化时注入运行时依赖。"""
        self.settings = settings
        self.paths = paths

    @property
    def file_cache(self) -> FileCache:
        """图片下载缓存（惰性创建，全插件共用一份）。"""
        cache = getattr(self, "_file_cache_instance", None)
        if cache is None:
            cache = FileCache(self.paths.cache)
            self._file_cache_instance = cache
        return cache

    # ------------------------------------------------------------------ #
    # 访问控制
    # ------------------------------------------------------------------ #
    async def guard(self, event) -> bool:
        """会话白名单校验。

        启用 ``enable_id_whitelist`` 时，只有白名单内的群号/QQ号可以使用插件，
        不在白名单内的事件会被静默忽略（与原插件"仅主人可用"不同的地方在于
        这里按会话维度控制）。
        """
        if not self.settings.enable_id_whitelist:
            return True
        if not self.settings.id_whitelist:
            return True
        if self.whitelist_ok(event):
            return True
        await event.send(
            event.plain_result(
                "本插件未对该会话开放。管理员可在插件配置的 id_whitelist 中添加"
                f"当前会话 ID（群：{event.get_group_id() or '私聊'}，"
                f"用户：{event.get_sender_id()}）。"
            )
        )
        return False

    # ------------------------------------------------------------------ #
    # 通用图片下载
    # ------------------------------------------------------------------ #
    async def download_images(
        self,
        urls: list[str],
        *,
        referer: str | None = None,
        prefix: str = "img",
    ) -> list[str]:
        """并发下载图片到临时目录，返回本地路径列表（失败项跳过）。

        referer 用于部分图床的防盗链校验。
        """
        if not urls:
            return []

        semaphore = asyncio.Semaphore(self.settings.max_concurrent_download)
        results: list[str | None] = [None] * len(urls)

        async def worker(index: int, url: str) -> None:
            async with semaphore:
                data = await self.file_cache.get(
                    url,
                    referer=referer,
                    timeout=self.settings.download_timeout,
                )
                if not data:
                    return
                if self.settings.track_pixel:
                    data = add_noise(data)
                target = self.temp_path(
                    f"{prefix}_{index}_{random.randint(1000, 9999)}.jpg"
                )
                try:
                    save_bytes(target, data)
                except OSError:
                    return
                results[index] = str(target)

        await asyncio.gather(*(worker(i, url) for i, url in enumerate(urls)))
        return [path for path in results if path]
