"""功能：按 PID 获取 P 站作品。

原 ``pid.js``：``#pid123456`` —— 取作品详情、下载全部图片、合并转发。
"""

from __future__ import annotations

from astrbot.api.event import AstrMessageEvent, filter

from cmd._pixiv_base import PixivBase

TRIGGER = r"^#?pid(\d+)$"


class PixivPidFeature(PixivBase):
    """单作品获取指令。"""

    @filter.regex(TRIGGER, priority=20)
    async def sp_pixiv_pid(self, event: AstrMessageEvent):
        """按 PID 获取 P 站作品"""
        if not await self.guard(event):
            return

        pid = self.extract_number(event.get_message_str(), TRIGGER)
        if not pid:
            return

        yield event.plain_result("正在搜索，请稍等...")

        try:
            details = await self.pixiv.fetch_illust(pid)
            if not details or not details.get("body"):
                yield event.plain_result("请输入正确的pid")
                return

            body = details["body"]
            urls = self.image_urls(body)
            if not urls:
                yield event.plain_result("该作品没有可用的图片地址")
                return

            paths = await self.download_images(urls)
            if not paths:
                yield event.plain_result("图片下载失败，请稍后再试")
                return

            text = self.work_text(body)
            for result in await self.send_works(event, [(text, paths)]):
                yield result
        except Exception as exc:
            yield event.plain_result(f"发生错误：{exc}")
