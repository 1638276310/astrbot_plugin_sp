"""各功能模块共用的基类。

插件主类同时继承 Star 与下面这些 Mixin，每个 Mixin 负责一组指令，
仍然满足"Handler 必须写在继承自 Star 的插件类中"的要求。
"""

from __future__ import annotations

import asyncio
import random
from typing import TYPE_CHECKING, Awaitable, Callable

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
    # 控制台实时进度（不向群里发消息）
    # ------------------------------------------------------------------ #
    def make_console_progress(self, label: str):
        """返回一组异步进度回调，把进度打到控制台日志（不发群消息）。

        用于套图"解析中 x/y 张"、"下载 x/y 张"这类场景：用户希望在
        控制台实时看到当前进度，又不想让群里刷一堆进度消息。

        返回一个 dict，包含两个可用的回调：

        * ``parse``    —— 与 ``app_core.mtb.fetch_album_detail(progress=)``
          及 ``app_core.mzt.fetch_album(progress=)`` 的签名一致：
          ``await parse(阶段, 当前值, 总数)``，其中 阶段 固定为 "解析"。
        * ``download`` —— 与 ``download_images(progress=)`` 的签名一致：
          ``await download(已完成数, 总数)``。

        ``label`` 一般传入套图/文章的简短标识（如套图标题），用于在日志
        里区分并发时来自不同任务。
        """
        prefix = f"[涩批进度] {label}"

        async def _parse(phase: str, current: int, total: int) -> None:
            if total > 0:
                logger.info(f"{prefix}：解析 {current}/{total} 张")
            else:
                logger.info(f"{prefix}：解析已 {current} 张")

        async def _download(done: int, total: int) -> None:
            logger.info(f"{prefix}：下载 {done}/{total} 张")

        return {"parse": _parse, "download": _download}

    # ------------------------------------------------------------------ #
    # 通用图片下载
    # ------------------------------------------------------------------ #
    async def download_images(
        self,
        urls: list[str],
        *,
        referer: str | None = None,
        prefix: str = "img",
        progress: Callable[[int, int], Awaitable[None]] | None = None,
    ) -> list[str]:
        """并发下载图片到临时目录，返回本地路径列表（失败项跳过）。

        referer 用于部分图床的防盗链校验。
        传入 ``progress`` 回调时，每当下载进度跨过一个新的 10% 里程碑
        （10%、20% ... 100%）时调用一次 ``await progress(已完成数, 总数)``，
        用于控制台实时输出下载进度；不传则行为与原来完全一致。
        """
        if not urls:
            logger.info(f"[涩批DEBUG] download_images({prefix})：URL列表为空")
            return []

        logger.info(
            f"[涩批DEBUG] download_images({prefix})：共 {len(urls)} 个URL，"
            f"并发上限 {self.settings.max_concurrent_download}，"
            f"timeout {self.settings.download_timeout}s"
        )
        logger.info(
            f"[涩批DEBUG] download_images({prefix})：即将启动并发下载（{len(urls)} 张）"
        )
        semaphore = asyncio.Semaphore(self.settings.max_concurrent_download)
        results: list[str | None] = [None] * len(urls)
        total = len(urls)
        done_count = 0
        last_milestone = 0
        progress_lock = asyncio.Lock()

        async def _on_done() -> None:
            nonlocal done_count, last_milestone
            should_report = False
            async with progress_lock:
                done_count += 1
                if progress is not None and total > 0:
                    # 每个 10% 里程碑报一次，避免并发下载时刷爆日志
                    milestone = int(done_count / total * 10)
                    if milestone > last_milestone:
                        last_milestone = milestone
                        should_report = True
            if not should_report:
                return
            try:
                await progress(done_count, total)
            except Exception:
                logger.info(
                    f"[涩批DEBUG] download_images({prefix})：progress 回调执行异常（已忽略）"
                )

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
                    await _on_done()
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
                    await _on_done()
                    return
                results[index] = str(target)
                await _on_done()

        logger.info(
            f"[涩批DEBUG] download_images({prefix})：等待所有下载完成（asyncio.gather）"
        )
        await asyncio.gather(*(worker(i, url) for i, url in enumerate(urls)))
        logger.info(
            f"[涩批DEBUG] download_images({prefix})：并发下载全部结束"
        )
        paths = [path for path in results if path]
        logger.info(
            f"[涩批DEBUG] download_images({prefix})：成功下载 {len(paths)}/{len(urls)} 张，即将返回路径列表"
        )
        return paths
