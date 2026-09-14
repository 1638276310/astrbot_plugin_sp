"""功能：随机获取某画师的若干作品。

原 ``PixivArtistWorksFetcher.js``：``#随机3张123456作品``
（X 为张数，最大 20；Y 为画师 ID）。
"""

from __future__ import annotations

import random
import re

from astrbot.api.event import AstrMessageEvent, filter

from .features._pixiv_base import PixivBase

TRIGGER = r"^#?随机(\d+)张(\d+)作品$"
MAX_COUNT = 20


class PixivArtistFeature(PixivBase):
    """画师作品随机抽取指令。"""

    @filter.regex(TRIGGER, priority=20)
    async def sp_pixiv_random_artist(self, event: AstrMessageEvent):
        """随机获取画师作品（#随机X张Y作品，X ≤ 20）"""
        if not await self.guard(event):
            return

        parsed = re.match(TRIGGER, event.get_message_str().strip())
        if not parsed:
            return
        count = int(parsed.group(1))
        artist_id = parsed.group(2)

        if count <= 0:
            yield event.plain_result("张数需要大于 0 哦")
            return
        if count > MAX_COUNT:
            yield event.plain_result("一次最多看20张哦")
            return

        yield event.plain_result("正在搜索，请稍等...")

        try:
            artist = await self.pixiv.fetch_artist(artist_id)
            if not artist or artist.get("error") or not artist.get("body"):
                yield event.plain_result("请输入正确的画师ID")
                return

            body = artist.get("body") or {}
            illusts = body.get("illusts")
            if not isinstance(illusts, dict) or not illusts:
                yield event.plain_result("该画师没有可用的作品")
                return

            work_ids = list(illusts.keys())
            random.shuffle(work_ids)
            selected = work_ids[:count]

            works: list[tuple[str, list[str]]] = []
            for work_id in selected:
                details = await self.pixiv.fetch_illust(work_id)
                if not details or not details.get("body"):
                    continue
                work_body = details["body"]
                urls = self.image_urls(work_body)
                if not urls:
                    continue
                paths = await self.download_images(urls)
                if not paths:
                    continue
                works.append((self.work_text(work_body), paths))

            if not works:
                yield event.plain_result("没有获取到作品，请稍后再试")
                return

            for result in await self.send_works(event, works):
                yield result
        except Exception as exc:
            yield event.plain_result(f"发生错误：{exc}")
