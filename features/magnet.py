"""功能：磁力链接。

对应原 ``MagnetLinkFetcher.js``（验车）与 ``MagnetLinkMao.js``（磁力猫搜索）。

* ``/验车 magnet:...``                       —— 查询磁力链接详情并发送截图
* ``/磁力猫 <关键词> [类型] [排序] [数量]``    —— 磁力猫搜索
"""

from __future__ import annotations

import asyncio
import re

from astrbot.api.event import AstrMessageEvent, filter # type: ignore
from astrbot.api.message_components import Image, Plain # type: ignore

from ._base import spFeature
from ..app_core.magnetcat import describe_results, parse_command, search_magnet
from ..app_core.verify import fetch_magnet_info

MAGNET_LINK_PATTERN = re.compile(r"验车\s*(magnet:\S+)")
VERIFY_RETRY = 3
VERIFY_RETRY_DELAY = 2.0
MAX_SCREENSHOTS = 9


class MagnetFeature(spFeature):
    """磁力相关指令。"""

    # ------------------------------------------------------------------ #
    # /验车
    # ------------------------------------------------------------------ #
    @filter.command("验车", priority=20)
    async def sp_verify_magnet(self, event: AstrMessageEvent, magnet: str = ""):
        """查询磁力链接详情（/验车 magnet:...）"""
        if not await self.guard(event):
            return

        # 优先用空格传参（/验车 magnet:...），否则连写解析（/验车magnet:...）
        if not magnet:
            m = MAGNET_LINK_PATTERN.search(event.get_message_str())
            if not m:
                yield event.plain_result("用法：/验车 <magnet:...>")
                return
            magnet = m.group(1)
        if not magnet.startswith("magnet:"):
            yield event.plain_result("用法：/验车 <magnet:...>")
            return

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
            from ..app_core.imaging import add_noise, save_bytes

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
            from astrbot.api.message_components import Node # type: ignore

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
    # /磁力猫
    # ------------------------------------------------------------------ #
    @filter.command("磁力猫", priority=20)
    async def sp_magnet_cat(self, event: AstrMessageEvent, keyword: str = "", file_type: str = "", order: str = "", count: int = 10):
        """磁力猫搜索（/磁力猫 关键词 [类型] [排序] [数量]）"""
        if not await self.guard(event):
            return

        # 优先用空格传参，否则回退到连写解析
        if not keyword:
            parsed = parse_command(event.get_message_str())
            if not parsed:
                yield event.plain_result("用法：/磁力猫 <关键词> [类型] [排序] [数量]")
                return
            keyword, file_type, order, count = parsed
        else:
            # 空格传参：AstrBot 自动解析，但 count 可能仍是字符串
            try:
                count = int(count) if str(count).isdigit() else 10
            except (ValueError, TypeError):
                count = 10
            file_type = str(file_type or "")
            order = str(order or "")

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
