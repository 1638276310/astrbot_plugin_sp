"""功能：妹子图（kkmzt）写真。

对应原 ``mzt.js``：

* ``/写真馆 <ID>``  —— 获取指定写真（建议空格分隔，连写也兼容）
* ``/随机写真``    —— 随机取一个已保存的 ID
* ``/更新写真ID``  —— 增量更新 ID 列表（仅主人可用）

指令 handler 由主类 main.py 注册（与 get_px 同构），
本文件保留业务方法。
"""

from __future__ import annotations

from astrbot.api import logger # type: ignore
from astrbot.api.event import AstrMessageEvent, filter # type: ignore

from ._base import spFeature
from ..app_core.mzt import collect_article_ids, fetch_album, parse_article_id
from ..app_core.storage import load_json, save_json


class MztFeature(spFeature):
    """妹子图相关业务方法（指令 handler 由主类 main.py 注册）。"""

    def mzt_ids(self) -> list[str]:
        """读取写真 ID 列表。"""
        data = load_json(self.paths.mzt_ids_file, [])
        if isinstance(data, list):
            ids = [str(item) for item in data]
            logger.info(f"[涩批DEBUG] mzt_ids：读到 {len(ids)} 个ID")
            return ids
        logger.info("[涩批DEBUG] mzt_ids：文件不存在或损坏，返回空列表")
        return []

    def save_mzt_ids(self, ids: list[str]) -> None:
        save_json(self.paths.mzt_ids_file, ids)
        logger.info(f"[涩批DEBUG] save_mzt_ids：已保存 {len(ids)} 个ID")

    # ------------------------------------------------------------------ #
    # 内部实现
    # ------------------------------------------------------------------ #
    async def _send_mzt_album(self, event: AstrMessageEvent, article_id: str):
        """解析并发送一个写真页。"""
        logger.info(f"[涩批DEBUG] _send_mzt_album：开始解析文章ID={article_id}")
        try:
            album = await fetch_album(self.settings, article_id)
        except Exception as exc:
            logger.error(
                f"[涩批DEBUG] _send_mzt_album：解析文章ID={article_id} 失败：{exc!r}"
            )
            yield event.plain_result(f"连接网页失败，请稍后再试（{exc}）")
            return

        if not album.image_urls:
            logger.warning(f"[涩批DEBUG] _send_mzt_album：文章ID={article_id} 没有图片")
            yield event.plain_result("没有找到任何图片，请稍后再试。")
            return

        logger.info(
            f"[涩批DEBUG] _send_mzt_album：文章ID={article_id} 共 {len(album.image_urls)} 张图片，开始下载"
        )
        paths = await self.download_images(
            album.image_urls,
            referer=self.settings.mzt_site,
            prefix="mzt",
        )
        if not paths:
            logger.warning(f"[涩批DEBUG] _send_mzt_album：文章ID={article_id} 图片全部下载失败")
            yield event.plain_result("图片下载失败，请稍后再试。")
            return

        logger.info(
            f"[涩批DEBUG] _send_mzt_album：文章ID={article_id} 成功下载 {len(paths)} 张，"
            f"forward_as_node={self.settings.forward_as_node}"
        )
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
