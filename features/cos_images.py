"""功能：二/三次元图包。

对应原 ``tu.js``：``/2图``（二次元）与 ``/3图``（三次元/现实），
从配置的接口随机拉取 10 张图。
"""

from __future__ import annotations

from astrbot.api.event import AstrMessageEvent, filter # type: ignore

from ._base import spFeature
from ..app_core.http import fetch_bytes

IMAGE_COUNT = 10
CATEGORY_MAP = {"2图": "acg", "3图": "reality"}
TYPE_NAME = {"2图": "二次元图片", "3图": "现实图片"}


class CosImageFeature(spFeature):
    """2图 / 3图指令。"""

    @filter.command("2图", priority=60)
    async def sp_cos_images(self, event: AstrMessageEvent):
        """获取二次元图包（/2图）"""
        self.stop_event_if_needed(event)
        if not await self.guard(event):
            return

        command = "2图"
        category = CATEGORY_MAP[command]
        type_name = TYPE_NAME[command]

        yield event.plain_result(f"正在获取{type_name}，请稍等...")

        base = self.settings.cos_api
        separator = "&" if "?" in base else "?"
        url = f"{base}{separator}category={category}"

        paths = await self._download_cos_images(url, prefix=category)
        if not paths:
            yield event.plain_result("未能获取到图片，请稍后再试。")
            return

        if self.settings.forward_as_node:
            nodes = self.image_nodes(event, paths)
            for batch in self.build_batches(nodes):
                yield event.chain_result([self.wrap_nodes(batch)])
        else:
            yield event.chain_result(self.image_chain(paths))

    @filter.command("3图", priority=60)
    async def sp_cos_images_3d(self, event: AstrMessageEvent):
        """获取三次元/现实图包（/3图）"""
        self.stop_event_if_needed(event)
        if not await self.guard(event):
            return

        command = "3图"
        category = CATEGORY_MAP[command]
        type_name = TYPE_NAME[command]

        yield event.plain_result(f"正在获取{type_name}，请稍等...")

        base = self.settings.cos_api
        separator = "&" if "?" in base else "?"
        url = f"{base}{separator}category={category}"

        paths = await self._download_cos_images(url, prefix=category)
        if not paths:
            yield event.plain_result("未能获取到图片，请稍后再试。")
            return

        if self.settings.forward_as_node:
            nodes = self.image_nodes(event, paths)
            for batch in self.build_batches(nodes):
                yield event.chain_result([self.wrap_nodes(batch)])
        else:
            yield event.chain_result(self.image_chain(paths))

    async def _download_cos_images(self, url: str, prefix: str) -> list[str]:
        """从接口抓取若干张图片并保存到本地。"""
        import asyncio
        import random

        from ..app_core.imaging import add_noise, save_bytes

        semaphore = asyncio.Semaphore(self.settings.max_concurrent_download)
        results: list[str | None] = [None] * IMAGE_COUNT

        async def worker(index: int) -> None:
            async with semaphore:
                try:
                    data = await fetch_bytes(
                        url,
                        timeout=self.settings.download_timeout,
                        referer=self.settings.cos_api,
                    )
                except Exception:
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

        await asyncio.gather(*(worker(i) for i in range(IMAGE_COUNT)))
        return [path for path in results if path]
