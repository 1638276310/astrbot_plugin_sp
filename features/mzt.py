"""功能：妹子图（kkmzt / 美之图）写真与潮拍。

对应原 ``mzt.js``：

* ``/写真馆 <ID或链接>``  —— 获取指定写真（也支持 /beauty 链接）
* ``/潮拍馆 <ID或链接>``  —— 获取潮拍馆图集（网页端为全量图片）
* ``/随机写真``          —— 随机取一个已保存的 ID
* ``/更新写真ID``        —— 增量更新 ID 列表（仅主人可用）

指令 handler 由主类 main.py 注册（与 get_px 同构），
本文件保留业务方法。
"""

from __future__ import annotations

from astrbot.api import logger # type: ignore
from astrbot.api.event import AstrMessageEvent, filter # type: ignore

from ._base import spFeature
from ..app_core.mzt import (
    collect_article_ids,
    fetch_album,
    format_fetch_error,
)
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
    async def _send_mzt_album(
        self, event: AstrMessageEvent, article_id: str, kind: str = "photo"
    ):
        """解析并发送一个写真/潮拍图集。"""
        logger.info(
            f"[涩批DEBUG] _send_mzt_album：开始解析文章ID={article_id} kind={kind}"
        )
        progress = self.make_console_progress(f"{kind} {article_id}")
        try:
            album = await fetch_album(
                self.settings,
                article_id,
                kind=kind,
                progress=progress["parse"],
            )
        except Exception as exc:
            # 只记录异常类型 + 脱敏后的摘要；错误文案前缀保持与旧版一致，
            # 括号内是结构化阶段错误（如 APP接口:HTTP 403；浏览器:Cloudflare 拦截 Ray ID ...）。
            summary = format_fetch_error(exc)
            logger.error(
                f"[涩批DEBUG] _send_mzt_album：解析文章ID={article_id} "
                f"失败（{type(exc).__name__}）：{summary}"
            )
            yield event.plain_result(f"连接网页失败，请稍后再试（{summary}）")
            return

        if not album.image_urls:
            logger.warning(f"[涩批DEBUG] _send_mzt_album：文章ID={article_id} 没有图片")
            yield event.plain_result("没有找到任何图片，请稍后再试。")
            return

        # 图片 Referer 策略（由 AlbumInfo.image_referer 决定）：
        # * 网页端写真馆：带站点 Referer；
        # * 网页端/APP 潮拍馆：网页端 f.meizitu.net 不需要 Referer，
        #   APP 端新浪图床（tva1.sinaimg.cn）需要 https://weibo.com/ 防盗链 Referer；
        # * APP 端写真馆（ap/ax.iimzt.com）：不需要 Referer。
        referer = getattr(album, "image_referer", None)
        # 图片 CDN 与站点同样可能被 Cloudflare 按 IP 拦截：与抓取共用 mzt_proxy
        proxy = self.settings.mzt_proxy or None
        logger.info(
            f"[涩批DEBUG] _send_mzt_album：文章ID={article_id} 共 "
            f"{len(album.image_urls)} 张图片（总数 {album.total or '未知'}，"
            f"数据源={album.source}，预览截断={album.truncated}），"
            f"referer={referer!r}，proxy={'on' if proxy else 'off'}，开始下载"
        )
        paths = await self.download_images(
            album.image_urls,
            referer=referer,
            prefix="mzt",
            progress=progress["download"],
            proxy=proxy,
        )
        if not paths and album.image_urls:
            # 图床 Referer 策略可能随站点调整，失败时用相反策略重试一次
            alt_referer = (
                None
                if referer == self.settings.mzt_site
                else self.settings.mzt_site
            )
            logger.info(
                f"[涩批DEBUG] _send_mzt_album：文章ID={article_id} 首次下载全部失败，"
                f"改用 referer={alt_referer!r} 重试"
            )
            paths = await self.download_images(
                album.image_urls,
                referer=alt_referer,
                prefix="mzt",
                progress=progress["download"],
                proxy=proxy,
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
