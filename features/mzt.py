"""功能：妹子图（kkmzt / 美之图）写真与潮拍。

对应原 ``mzt.js``，但抓取链路已收敛为：指令 ID → APP 接口（mTLS）→
内置解密模块 → 按解密结果下载，不再访问站点 HTML 页面。

* ``/写真馆 <ID或链接>``  —— 获取指定写真（支持多个 ID 逐个获取）
* ``/潮拍馆 <ID或链接>``  —— 获取潮拍馆图集（APP 接口为全量）
* ``/随机写真``          —— 随机取一个已保存的 ID
* ``/更新写真ID``        —— 已停用（不再直连列表页），保留指令用于提示

指令 handler 由主类 main.py 注册（与 get_px 同构），
本文件保留业务方法。
"""

from __future__ import annotations

import re

from astrbot.api import logger # type: ignore
from astrbot.api.event import AstrMessageEvent # type: ignore

from ..app_core.http import HttpError, fetch_text
from ..app_core.mzt import (
    fetch_album,
    format_fetch_error,
)
from ..app_core.storage import load_json, save_json
from ._base import spFeature

# 列表页链接形如 {mzt_site}/photo/172742、{mzt_site}/beauty/191778
# （仅 /更新写真ID 使用；图片获取仍走 APP 接口，不访问文章页）。
# 同时兼容相对链接 /photo/172742（页面在不同模板下会混用）。
MZT_LIST_PATHS: tuple[str, ...] = ("photo", "beauty")
MZT_ID_LINK_PATTERN = re.compile(
    r"(?:https?://[^\"'\s]+)?/(?:photo|beauty)/(\d+)(?:\.html)?"
)


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
        """保存写真 ID 列表。"""
        save_json(self.paths.mzt_ids_file, ids)
        logger.info(f"[涩批DEBUG] save_mzt_ids：已保存 {len(ids)} 个ID")

    async def fetch_site_mzt_ids(self, *, timeout: int = 30) -> tuple[list[str], str]:
        """抓取写真馆/潮拍馆列表页的最新 ID。

        列表页在 2026-10 仍可直接访问（Cloudflare 返回 200 静态 HTML），
        因此不需要浏览器；解析失败时返回空列表和原因，由调用方决定是否
        只用内置种子。返回 ``(ids, error)``。
        """
        site = str(self.settings.mzt_site or "").rstrip("/")
        if not site.startswith(("http://", "https://")):
            return [], "mzt_site 配置不合法"
        found: list[str] = []
        errors: list[str] = []
        for kind in MZT_LIST_PATHS:
            url = f"{site}/{kind}/"
            try:
                text = await fetch_text(
                    url,
                    timeout=timeout,
                    referer=f"{site}/",
                    headers={"Accept-Language": "zh-CN,zh;q=0.9"},
                    proxy=str(getattr(self.settings, "mzt_proxy", "") or "").strip() or None,
                )
            except HttpError as exc:
                errors.append(f"{kind}: {exc}")
                logger.info(f"[涩批DEBUG] fetch_site_mzt_ids：{url} 抓取失败：{exc}")
                continue
            # 页面里同时存在绝对/相对链接；统一提取数字 ID 并去重
            page_ids = MZT_ID_LINK_PATTERN.findall(text or "")
            logger.info(
                f"[涩批DEBUG] fetch_site_mzt_ids：{url} 解析到 {len(page_ids)} 个ID"
            )
            found.extend(page_ids)
        ids = [item for item in dict.fromkeys(found) if item]
        return ids, "；".join(errors)

    async def update_mzt_ids(self) -> dict[str, int | str]:
        """更新写真 ID：站点最新 + 内置种子 + 现有数据，去重合并后原子落盘。

        该命令只做增量合并，不会删除已有 ID；站点抓取失败时仍会同步
        内置种子，保证离线也能把出厂数据补齐。
        """
        existing = self.mzt_ids()
        seed = load_json(self.paths.asset("mztids.json"), [])
        seed_ids = [str(item) for item in seed] if isinstance(seed, list) else []
        site_ids, error = await self.fetch_site_mzt_ids()
        merged = list(
            dict.fromkeys(
                [str(item) for item in existing if str(item).strip()]
                + [str(item) for item in seed_ids if str(item).strip()]
                + site_ids
            )
        )
        added = max(0, len(merged) - len(existing))
        if added > 0:
            self.save_mzt_ids(merged)
        logger.info(
            f"[涩批DEBUG] update_mzt_ids：现有 {len(existing)}，站点 {len(site_ids)}，"
            f"种子 {len(seed_ids)}，合并后 {len(merged)}（新增 {added}）"
        )
        return {
            "existing": len(existing),
            "site": len(site_ids),
            "seed": len(seed_ids),
            "total": len(merged),
            "added": added,
            "error": error,
        }

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
            yield event.plain_result(f"获取图集失败，请稍后再试（{summary}）")
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
        # 图片下载默认直连；设置 SP_MZT_PROXY 后与 APP 接口共用同一代理出口。
        proxy = str(getattr(self.settings, "mzt_proxy", "") or "").strip() or None
        route = "proxy" if proxy else "direct"
        logger.info(
            f"[涩批DEBUG] _send_mzt_album：文章ID={article_id} 共 "
            f"{len(album.image_urls)} 张图片（总数 {album.total or '未知'}，"
            f"数据源={album.source}，预览截断={album.truncated}），"
            f"referer={referer!r}，route={route}，开始下载"
        )
        # 与 /随机美图吧 相同的节奏：先告诉用户找到多少张，再开始下载
        yield self.quoted_text_result(
            event, f"共找到 {len(album.image_urls)} 张图片，正在下载..."
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
        yield event.plain_result(
            f"下载完成：{len(paths)}/{len(album.image_urls)} 张，正在发送..."
        )
        if self.settings.forward_as_node:
            nodes = self.text_nodes(event, [album.header])
            nodes.extend(self.image_nodes(event, paths))
            batches = self.build_batches(nodes)
            for index, batch in enumerate(batches, start=1):
                yield event.plain_result(
                    f"正在发送第 {index}/{len(batches)} 批图片"
                    f"（共 {len(paths)} 张）..."
                )
                yield event.chain_result([self.wrap_nodes(batch)])
        else:
            from astrbot.api.message_components import Plain # type: ignore

            yield event.plain_result(f"正在发送 {len(paths)} 张图片...")
            chain = [Plain(text=album.header)] + self.image_chain(paths)
            yield event.chain_result(chain)
        # 完成回执：明确告诉用户本次图集已经结束
        yield event.plain_result(
            f"✅ 图集发送完成：{album.title}（ID {album.article_id}，"
            f"{len(paths)}/{len(album.image_urls)} 张）"
        )


__all__ = ["MztFeature"]
