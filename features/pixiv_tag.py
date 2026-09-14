"""功能：按标签搜索 P 站图片。

原 ``tag.js``：``#来10张白丝图``（X 为数量，最大 60）。

R18 模式与图片偏好取自插件配置（由 ``#设置R18模式`` / ``#设置图片偏好`` 修改）。
"""

from __future__ import annotations

import re

from astrbot.api.event import AstrMessageEvent, filter # type: ignore

from ._pixiv_base import PixivBase

TRIGGER = r"^#?来(\d+)张(.*?)图$"
MAX_COUNT = 60
MAX_IMAGES_PER_PID = 5
GROUP_SIZE = 10


class PixivTagFeature(PixivBase):
    """标签搜索指令。"""

    @filter.regex(TRIGGER, priority=20)
    async def sp_pixiv_tag(self, event: AstrMessageEvent):
        """按标签搜索 P 站图片（#来X张XX图，X ≤ 60）"""
        if not await self.guard(event):
            return

        parsed = re.match(TRIGGER, event.get_message_str().strip())
        if not parsed:
            return
        count = int(parsed.group(1))
        tag = (parsed.group(2) or "").strip()

        if count <= 0:
            yield event.plain_result("张数需要大于 0 哦")
            return
        if count > MAX_COUNT:
            yield event.plain_result("你想冲死吗？")
            return

        yield event.plain_result("正在搜索，请稍等...")

        try:
            ids = await self.pixiv.fetch_tag_ids(
                tag,
                mode=self.settings.r18_mode,
                order=self.settings.image_preference,
            )
            if not ids:
                yield event.plain_result("没有这种图啊，涩批！")
                return

            selected = self._random_ids(ids, count)
            works: list[tuple[str, list[str]]] = []
            for pid in selected:
                details = await self.pixiv.fetch_illust(pid)
                if not details or not details.get("body"):
                    continue
                body = details["body"]
                urls = self.image_urls(body)[:MAX_IMAGES_PER_PID]
                if not urls:
                    continue
                paths = await self.download_images(urls)
                if not paths:
                    continue
                works.append((self.work_text(body), paths))

            if not works:
                yield event.plain_result("没有获取到图片，请稍后再试")
                return

            for result in await self.send_works(event, works):
                yield result
            yield event.plain_result("所有图片发送完毕！")
        except Exception as exc:
            yield event.plain_result(f"发生错误：{exc}")

    @staticmethod
    def _random_ids(ids: list[str], count: int) -> list[str]:
        import random

        pool = list(ids)
        random.shuffle(pool)
        return pool[:count]
