"""功能：妹子图（kkmzt）写真。

对应原 ``mzt.js``：

* ``/写真馆 <ID>``  —— 获取指定写真（建议空格分隔，连写也兼容）
* ``/随机写真``    —— 随机取一个已保存的 ID
* ``/更新写真ID``  —— 增量更新 ID 列表（仅主人可用）
"""

from __future__ import annotations

import random

from astrbot.api.event import AstrMessageEvent, filter # type: ignore

from ._base import spFeature
from ..app_core.mzt import collect_article_ids, fetch_album, parse_article_id
from ..app_core.storage import load_json, save_json


class MztFeature(spFeature):
    """妹子图相关指令。"""

    def mzt_ids(self) -> list[str]:
        """读取写真 ID 列表。"""
        data = load_json(self.paths.mzt_ids_file, [])
        if isinstance(data, list):
            return [str(item) for item in data]
        return []

    def save_mzt_ids(self, ids: list[str]) -> None:
        save_json(self.paths.mzt_ids_file, ids)

    # ------------------------------------------------------------------ #
    # /写真馆<ID>
    # ------------------------------------------------------------------ #
    @filter.command("写真馆", priority=20)
    async def sp_mzt_album(self, event: AstrMessageEvent):
        """获取妹子图写真（/写真馆 <ID>）"""
        self.stop_event_if_needed(event)
        if not await self.guard(event):
            return

        article_id = parse_article_id(event.get_message_str())
        if not article_id:
            yield event.plain_result("用法：/写真馆 <ID>，例如 /写真馆 12345")
            return
        yield event.plain_result("正在搜索，请稍等...")
        async for result in self._send_mzt_album(event, article_id):
            yield result

    # ------------------------------------------------------------------ #
    # /随机写真
    # ------------------------------------------------------------------ #
    @filter.command("随机写真", priority=20)
    async def sp_mzt_random(self, event: AstrMessageEvent):
        """随机获取妹子图（/随机写真）"""
        self.stop_event_if_needed(event)
        if not await self.guard(event):
            return

        ids = self.mzt_ids()
        if not ids:
            yield event.plain_result("写真ID列表为空，请先使用 /更新写真ID")
            return
        article_id = random.choice(ids)
        yield event.plain_result(f"写真ID：{article_id} 正在搜索，请稍等...")
        async for result in self._send_mzt_album(event, article_id):
            yield result

    # ------------------------------------------------------------------ #
    # /更新写真ID
    # ------------------------------------------------------------------ #
    @filter.command("更新写真ID", alias={"更新写真id"}, priority=20)
    async def sp_mzt_update_ids(self, event: AstrMessageEvent):
        """增量更新写真ID列表（仅主人可用）"""
        self.stop_event_if_needed(event)
        if not await self.guard(event):
            return

        if not await self.require_admin(event):
            return
        yield event.plain_result("开始增量更新写真ID列表，这可能需要几分钟时间...")

        existing = set(self.mzt_ids())
        try:
            discovered = await collect_article_ids(self.settings, existing)
        except Exception as exc:
            yield event.plain_result(f"更新失败: {exc}")
            return

        if discovered:
            # 新 ID 放最前面，与原插件一致
            merged = list(dict.fromkeys(discovered + list(existing)))
        else:
            merged = list(existing)
        self.save_mzt_ids(merged)
        yield event.plain_result(
            f"增量更新完成！新增 {len(discovered)} 个ID，当前总计 {len(merged)} 个"
        )

    # ------------------------------------------------------------------ #
    # 内部实现
    # ------------------------------------------------------------------ #
    async def _send_mzt_album(self, event: AstrMessageEvent, article_id: str):
        """解析并发送一个写真页。"""
        try:
            album = await fetch_album(self.settings, article_id)
        except Exception as exc:
            yield event.plain_result(f"连接网页失败，请稍后再试（{exc}）")
            return

        if not album.image_urls:
            yield event.plain_result("没有找到任何图片，请稍后再试。")
            return

        paths = await self.download_images(
            album.image_urls,
            referer=self.settings.mzt_site,
            prefix="mzt",
        )
        if not paths:
            yield event.plain_result("图片下载失败，请稍后再试。")
            return

        if self.settings.forward_as_node:
            nodes = self.text_nodes(event, [album.header])
            nodes.extend(self.image_nodes(event, paths))
            for batch in self.build_batches(nodes):
                yield event.chain_result([self.wrap_nodes(batch)])
        else:
            from astrbot.api.message_components import Plain # type: ignore

            chain = [Plain(text=album.header)] + self.image_chain(paths)
            yield event.chain_result(chain)


__all__ = ["MztFeature"]
