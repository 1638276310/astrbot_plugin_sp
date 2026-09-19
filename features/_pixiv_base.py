"""P 站相关指令的公共逻辑（下载图片、组装合并转发）。"""

from __future__ import annotations

from astrbot.api.event import AstrMessageEvent # type: ignore

from ._base import spFeature
from ..app_core.pixiv import PixivClient


class PixivBase(spFeature):
    """Pixiv 指令共用能力。"""

    _pixiv_client: PixivClient | None = None

    @property
    def pixiv(self) -> PixivClient:
        if self._pixiv_client is None:
            self._pixiv_client = PixivClient(self.settings)
        return self._pixiv_client

    # ------------------------------------------------------------------ #
    # 图片下载
    # ------------------------------------------------------------------ #
    async def download_images(
    self,
    urls: list[str],
    *,
    referer: str | None = None,
    prefix: str = "pixiv",
    ) -> list[str]:
        from astrbot.api import logger # type: ignore
        logger.info(
            f"[涩批DEBUG] pixiv.download_images：共 {len(urls)} 个URL，"
            f"prefix={prefix!r}，referer={referer!r}"
        )
        return await super().download_images(
        urls, referer=referer, prefix=prefix
    )

    @staticmethod
    def work_text(body: dict) -> str:
        """拼装作品信息文本（与原插件字段一致）。"""
        tags = ""
        tag_container = body.get("tags") or {}
        if isinstance(tag_container, dict):
            tag_items = tag_container.get("tags") or []
            tags = ", ".join(
                str(item.get("tag"))
                for item in tag_items
                if isinstance(item, dict) and item.get("tag")
            )
        return "\n".join(
            [
                f"id：{body.get('illustId', '')}",
                f"画师：{body.get('userName', '')}（{body.get('userId', '')}）",
                f"是否ai：{'是' if body.get('aiType') == 2 else '否'}",
                f"标题：{body.get('illustTitle', '')}",
                f"上传时间：{body.get('createDate', '')}",
                f"♥：{body.get('likeCount', 0)}",
                f"😊：{body.get('bookmarkCount', 0)}",
                f"👁：{body.get('viewCount', 0)}",
                f"tag：{tags}",
            ]
        )

    @staticmethod
    def image_urls(body: dict) -> list[str]:
        """取作品中所有图片地址（原图/大图/小图都会返回）。"""
        urls = body.get("urls")
        if not isinstance(urls, dict):
            return []
        return [str(value) for value in urls.values() if value]

    # ------------------------------------------------------------------ #
    # 发送
    # ------------------------------------------------------------------ #
    async def send_works(
        self,
        event: AstrMessageEvent,
        works: list[tuple[str, list[str]]],
    ) -> list:
        """把 [(文本, 图片路径列表)] 组装成可 yield 的消息结果列表。"""
        from astrbot.api.message_components import Node, Plain # type: ignore

        results: list = []
        sender_name = event.get_sender_name() or "涩批"
        self_id = str(event.get_self_id() or "0")

        if not self.settings.forward_as_node:
            for text, paths in works:
                chain = [Plain(text=text)] + self.image_chain(paths)
                results.append(event.chain_result(chain))
            return results

        nodes = []
        for text, paths in works:
            nodes.append(
                Node(content=[Plain(text=text)], name=sender_name, uin=self_id)
            )
            nodes.extend(self.image_nodes(event, paths))
        if not nodes:
            return results

        for batch in self.build_batches(nodes):
            results.append(event.chain_result([self.wrap_nodes(batch)]))
        return results
