"""功能：磁力链接。

对应原 ``MagnetLinkFetcher.js``（验车）与 ``MagnetLinkMao.js``（磁力猫搜索）。

* ``#验车<magnet:...>``                       —— 查询磁力链接详情并发送截图
* ``#磁力猫 <关键词> [类型] [排序] [数量]``    —— 磁力猫搜索
"""

from __future__ import annotations

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image, Plain

from .features._base import spFeature
from .app_core.magnetcat import describe_results, parse_command, search_magnet
from .app_core.verify import fetch_magnet_info

VERIFY_TRIGGER = r"^#验车(magnet:.+)$"
# 与原 `^#?磁力猫(.*)$` 等价，但要求后面必须有非空关键词，
# 避免抢占其他插件的消息。
MAGNETCAT_TRIGGER = r"^#?磁力猫\s*\S+"

VERIFY_RETRY = 3
VERIFY_RETRY_DELAY = 2.0
MAX_SCREENSHOTS = 9


class MagnetFeature(spFeature):
    """磁力相关指令。"""

    # ------------------------------------------------------------------ #
    # #验车
    # ------------------------------------------------------------------ #
    @filter.regex(VERIFY_TRIGGER, priority=20)
    async def sp_verify_magnet(self, event: AstrMessageEvent):
        """查询磁力链接详情（#验车<magnet:...>）"""
        if not await self.guard(event):
            return

        import asyncio
        import re

        match = re.match(VERIFY_TRIGGER, event.get_message_str().strip())
        if not match:
            return
        magnet = match.group(1)

        yield event.plain_result("正在验车，请稍等...")

        info = None
        last_error = ""
        for attempt in range(1, VERIFY_RETRY + 1):
            try:
                info = await fetch_magnet_info(self.settings, magnet)
                if info is not None:
                    break
                last_error = "无效的响应数据"
            except Exception as exc:
                last_error = str(exc)
            if attempt < VERIFY_RETRY:
                await asyncio.sleep(VERIFY_RETRY_DELAY)

        if info is None:
            yield event.plain_result(f"查询失败: {last_error or '未知错误'}")
            return

        yield event.plain_result(info.describe(magnet))

        screenshots = info.screenshots[:MAX_SCREENSHOTS]
        if not screenshots:
            return

        paths: list[str] = []
        for index, shot_url in enumerate(screenshots):
            data = await self.file_cache.get(
                shot_url,
                referer="https://whatslink.info/",
                timeout=self.settings.download_timeout,
                use_memory=False,
            )
            if not data:
                continue
            from .app_core.imaging import add_noise, save_bytes

            if self.settings.track_pixel:
                data = add_noise(data)
            target = self.temp_path(f"verify_{index}.jpg")
            try:
                save_bytes(target, data)
            except OSError:
                continue
            paths.append(str(target))

        if not paths:
            return

        if self.settings.forward_as_node:
            from astrbot.api.message_components import Node

            sender_name = event.get_sender_name() or "涩批"
            self_id = str(event.get_self_id() or "0")
            merged: list = []
            for index, path in enumerate(paths, start=1):
                merged.append(
                    Node(
                        content=[Plain(text=f"截图 {index}")],
                        name=sender_name,
                        uin=self_id,
                    )
                )
                merged.append(
                    Node(
                        content=[Image.fromFileSystem(path)],
                        name=sender_name,
                        uin=self_id,
                    )
                )
            for batch in self.build_batches(merged):
                yield event.chain_result([self.wrap_nodes(batch)])
        else:
            yield event.chain_result(self.image_chain(paths))

    # ------------------------------------------------------------------ #
    # #磁力猫
    # ------------------------------------------------------------------ #
    @filter.regex(MAGNETCAT_TRIGGER, priority=20)
    async def sp_magnet_cat(self, event: AstrMessageEvent):
        """磁力猫搜索（#磁力猫 关键词 [类型] [排序] [数量]）"""
        if not await self.guard(event):
            return

        parsed = parse_command(event.get_message_str())
        if not parsed:
            return
        keyword, file_type, order, count = parsed
        if count <= 0:
            count = 10
        count = min(count, 50)

        yield event.plain_result("正在搜索，请稍等...")

        try:
            results, error = await search_magnet(
                self.settings,
                keyword,
                file_type=file_type,
                order=order,
                count=count,
            )
        except Exception as exc:
            yield event.plain_result(f"搜索失败: {exc}")
            return

        if not results:
            yield event.plain_result(error or "所有链接均无搜索结果")
            return

        texts = describe_results(results)
        if self.settings.forward_as_node:
            nodes = self.text_nodes(event, texts)
            for batch in self.build_batches(nodes):
                yield event.chain_result([self.wrap_nodes(batch)])
        else:
            for text in texts:
                yield event.plain_result(text)
