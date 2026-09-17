"""功能：美图吧（ku1373）套图。

对应原 ``mtb.js``：

* ``/随机美图吧``        —— 从已保存列表随机抽一套
* ``/套图详情 <URL>``    —— 解析指定套图（URL 用空格分隔）
* ``/更新套图列表``      —— 增量采集（列表为空时自动转全量，仅主人可用）
* ``/全量更新套图列表``  —— 全量采集（仅主人可用）

指令 handler 由主类 main.py 注册（与 get_px 同构），
本文件保留业务方法。
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
    """美图吧相关业务方法（指令 handler 由主类 main.py 注册）。"""

    def album_urls(self) -> list[str]:
        data = load_json(self.paths.jg_urls_file, [])
        if isinstance(data, list):
            return [str(item) for item in data]
        return []

    def save_album_urls(self, urls: list[str]) -> None:
        save_json(self.paths.jg_urls_file, urls)

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
