"""各功能模块共用的基类。

插件主类同时继承 Star 与下面这些 Mixin，每个 Mixin 负责一组指令，
仍然满足"Handler 必须写在继承自 Star 的插件类中"的要求。
"""

from __future__ import annotations

import asyncio
import random
from typing import TYPE_CHECKING

from astrbot.api import logger # type: ignore

from ..app_core.filecache import FileCache
from ..app_core.imaging import add_noise, save_bytes
from ..app_core.message import AdminMixin, MessageMixin
from ..app_core.paths import PluginPaths
from ..app_core.settings import PluginSettings

if TYPE_CHECKING:  # pragma: no cover
    from ..app_core.scheduler import TimeBasedScheduler


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

    @staticmethod
    def stop_event_if_needed(event) -> None:
        """命中指令后立刻停止事件。

        AstrBot 中，一个 handler 若没有调用 ``stop_event()``，事件会继续
        流转到后续的 handler；当没有任何 handler 拦截时，消息最终会被
        默认的 LLM / 聊天回复接管。get_px 等插件的所有指令 handler 都
        在入口处调用 ``event.stop_event()``，本插件的 Mixin 指令统一
        通过这个入口做同样的事，保证指令被自己消费、不会"漏"给 LLM。

        同时打印一行命中日志，便于在服务器日志里确认指令是否被
        本插件接住（28 个 handler 全部经过此入口）。
        """
        try:
            logger.info(f"[涩批] 命中指令：{event.get_message_str()!r}")
        except Exception:
            pass
        try:
            event.stop_event()
        except Exception:
            pass

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
            logger.info("[涩批DEBUG] guard：未启用白名单，直接放行")
            return True
        if not self.settings.id_whitelist:
            logger.info("[涩批DEBUG] guard：白名单为空，直接放行")
            return True
        if self.whitelist_ok(event):
            logger.info("[涩批DEBUG] guard：白名单校验通过")
            return True
        logger.warning(
            f"[涩批DEBUG] guard：白名单校验失败，群={event.get_group_id()!r} "
            f"用户={event.get_sender_id()!r} 不在白名单 {self.settings.id_whitelist} 中"
        )
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
            logger.info(f"[涩批DEBUG] download_images({prefix})：URL列表为空")
            return []

        logger.info(
            f"[涩批DEBUG] download_images({prefix})：共 {len(urls)} 个URL，"
            f"并发上限 {self.settings.max_concurrent_download}，"
            f"timeout {self.settings.download_timeout}s"
        )
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
                    logger.info(
                        f"[涩批DEBUG] download_images({prefix})：第 {index + 1}/{len(urls)} 张下载失败：{url[:80]}"
                    )
                    return
                if self.settings.track_pixel:
                    data = add_noise(data)
                target = self.temp_path(
                    f"{prefix}_{index}_{random.randint(1000, 9999)}.jpg"
                )
                try:
                    save_bytes(target, data)
                except OSError as exc:
                    logger.info(
                        f"[涩批DEBUG] download_images({prefix})：第 {index + 1}/{len(urls)} 张保存失败：{exc!r}"
                    )
                    return
                results[index] = str(target)

        await asyncio.gather(*(worker(i, url) for i, url in enumerate(urls)))
        paths = [path for path in results if path]
        logger.info(
            f"[涩批DEBUG] download_images({prefix})：成功下载 {len(paths)}/{len(urls)} 张"
        )
        return paths
