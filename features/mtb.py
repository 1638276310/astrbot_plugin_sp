"""功能：美图吧（ku1373）套图。

对应原 ``mtb.js``：

* ``/随机美图吧``        —— 从已保存列表随机抽一套
* ``/套图详情 <URL>``    —— 解析指定套图（URL 用空格分隔）
* ``/更新套图列表``      —— 增量采集（列表为空时自动转全量，仅主人可用）
* ``/全量更新套图列表``  —— 全量采集（仅主人可用）
"""

from __future__ import annotations

import re
import random

from astrbot.api.event import AstrMessageEvent, filter # type: ignore

from ._base import spFeature
from ..app_core.mtb import collect_album_urls, fetch_album_detail, parse_detail_url
from ..app_core.storage import load_json, save_json

# 内部解析正则：兼容 /套图详情 <URL> 空格格式
DETAIL_URL_PATTERN = re.compile(r"套图详情\s+(https?://\S+)")


class MtbFeature(spFeature):
    """美图吧相关指令。"""

    def album_urls(self) -> list[str]:
        data = load_json(self.paths.jg_urls_file, [])
        if isinstance(data, list):
            return [str(item) for item in data]
        return []

    def save_album_urls(self, urls: list[str]) -> None:
        save_json(self.paths.jg_urls_file, urls)

    # ------------------------------------------------------------------ #
    # /随机美图吧
    # ------------------------------------------------------------------ #
    @filter.command("随机美图吧", priority=25)
    async def sp_mtb_random(self, event: AstrMessageEvent):
        """随机解析一套美图吧套图（/随机美图吧）"""
        if not await self.guard(event):
            return

        urls = self.album_urls()
        if not urls:
            yield event.plain_result("套图URL列表为空，请先使用 /更新套图列表")
            return
        url = random.choice(urls)
        yield event.plain_result("正在随机抽取一套美图，请稍等...")
        async for result in self._send_album(event, url):
            yield result

    # ------------------------------------------------------------------ #
    # /套图详情 <URL>
    # ------------------------------------------------------------------ #
    @filter.command("套图详情", priority=25)
    async def sp_mtb_detail(self, event: AstrMessageEvent, url: str = ""):
        """解析指定美图吧套图链接（/套图详情 <URL>）"""
        if not await self.guard(event):
            return

        # 优先用空格传参（/套图详情 https://...），否则连写解析
        if not url:
            m = DETAIL_URL_PATTERN.search(event.get_message_str())
            url = m.group(1) if m else ""
        if not url.startswith("http"):
            yield event.plain_result("用法：/套图详情 <URL>")
            return
        parsed = parse_detail_url(url)
        if not parsed:
            yield event.plain_result("URL 格式不正确")
            return
        yield event.plain_result("正在解析套图，请稍等...")
        async for result in self._send_album(event, parsed):
            yield result

    # ------------------------------------------------------------------ #
    # /更新套图列表
    # ------------------------------------------------------------------ #
    @filter.command("更新套图列表", priority=25)
    async def sp_mtb_incremental_update(self, event: AstrMessageEvent):
        """增量更新套图列表（仅主人可用）"""
        if not await self.guard(event):
            return

        if not await self.require_admin(event):
            return
        existing = self.album_urls()
        if not existing:
            yield event.plain_result("套图URL列表为空，自动转为全量更新...")
            async for result in self._full_update(event):
                yield result
            return

        yield event.plain_result("开始增量更新套图URL列表（只采集最新页面）...")
        try:
            merged, total_pages = await collect_album_urls(
                self.settings, existing=existing, incremental=True
            )
        except Exception as exc:
            yield event.plain_result(f"增量更新失败: {exc}")
            return

        if total_pages is None:
            yield event.plain_result("无法获取总页数，增量更新终止")
            return

        added = len(merged) - len(existing)
        self.save_album_urls(merged)
        yield event.plain_result(
            f"增量更新完成！本次新增 {max(0, added)} 个套图"
            f"（现有总计 {len(merged)} 个，原为 {len(existing)} 个）"
        )

    # ------------------------------------------------------------------ #
    # /全量更新套图列表
    # ------------------------------------------------------------------ #
    @filter.command("全量更新套图列表", priority=25)
    async def sp_mtb_full_update(self, event: AstrMessageEvent):
        """全量更新套图列表（仅主人可用）"""
        if not await self.guard(event):
            return

        if not await self.require_admin(event):
            return
        async for result in self._full_update(event):
            yield result

    # ------------------------------------------------------------------ #
    # 内部实现
    # ------------------------------------------------------------------ #
    async def _full_update(self, event: AstrMessageEvent):
        previous = self.album_urls()
        yield event.plain_result(
            "开始全量更新套图URL列表（从第一页向最后一页顺序采集），"
            "这可能需要几分钟时间..."
        )
        try:
            urls, total_pages = await collect_album_urls(
                self.settings, existing=previous, incremental=False
            )
        except Exception as exc:
            yield event.plain_result(f"全量更新失败: {exc}")
            return
        if total_pages is None:
            yield event.plain_result("无法获取总页数，全量更新终止")
            return
        self.save_album_urls(urls)
        yield event.plain_result(
            f"全量更新完成！共获取 {len(urls)} 个套图URL (原有 {len(previous)} 个)"
        )

    async def _send_album(self, event: AstrMessageEvent, url: str):
        """解析并分批发送套图。"""
        try:
            detail = await fetch_album_detail(self.settings, url)
        except Exception as exc:
            yield event.plain_result(f"连接网页失败，请稍后再试（{exc}）")
            return

        if not detail.image_urls:
            yield event.plain_result("没有找到任何图片，请稍后再试。")
            return

        yield event.plain_result(
            f"共找到 {len(detail.image_urls)} 张图片，正在下载..."
        )
        paths = await self.download_images(
            detail.image_urls,
            referer=url,
            prefix="mtb",
        )
        if not paths:
            yield event.plain_result("图片下载失败，请稍后再试。")
            return

        if self.settings.forward_as_node:
            nodes = [self.merged_text_node(event, detail.header_lines())]
            nodes.extend(self.image_nodes(event, paths))
            batches = self.build_batches(nodes)
            for index, batch in enumerate(batches):
                if index > 0:
                    yield event.plain_result(
                        f"--- 第 {index + 1} 批图片 (共 {len(batches)} 批) ---"
                    )
                yield event.chain_result([self.wrap_nodes(batch)])
        else:
            from astrbot.api.message_components import Plain # type: ignore

            yield event.chain_result(
                [Plain(text="\n".join(detail.header_lines()))]
                + self.image_chain(paths)
            )
