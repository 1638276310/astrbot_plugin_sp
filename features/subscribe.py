"""画师订阅与推送。

对应原 ``dingyue.js`` + ``dingyue_Auto_update.js``：

* ``/订阅画师 <ID>`` / ``/取消订阅 <ID>`` / ``/订阅列表``
* ``/sp推送`` / ``/关闭sp推送``
* 定时检查更新并推送到开启了推送的会话

原插件用群号作为 key（Yunzai 只能在群里用），AstrBot 版同时支持群聊与私聊，
key 为群号或私聊用户 ID。

指令 handler 由主类 main.py 注册（与 get_px 同构），
本文件保留解析正则与存储 / 推送逻辑。
"""

from __future__ import annotations

import asyncio
import random
import re
from typing import Any

from astrbot.api import logger  # 使用 astrbot 提供的 logger 接口 # type: ignore

from ._base import spFeature
from ..app_core.imaging import add_noise, save_bytes
from ..app_core.storage import JsonStore

SUBSCRIBE_PAT = re.compile(r"订阅画师\s*(\d+)")
UNSUBSCRIBE_PAT = re.compile(r"取消订阅\s*(\d+)")

PUSH_MAX_WORKS = 3
PUSH_INTERVAL_SECONDS = 10


def empty_session() -> dict[str, Any]:
    return {"pushEnabled": False, "artists": {}, "umo": ""}


class SubscribeFeature(spFeature):
    """订阅相关存储与推送逻辑（指令 handler 由主类 main.py 注册）。"""

    # ------------------------------------------------------------------ #
    # 存储
    # ------------------------------------------------------------------ #
    def store(self) -> JsonStore:
        store = getattr(self, "_subscribe_store", None)
        if store is None:
            store = JsonStore(self.paths.subscribe_file, {})
            self._subscribe_store = store
        return store

    def load_data(self) -> dict[str, Any]:
        data = self.store().load()
        return data if isinstance(data, dict) else {}

    def save_data(self, data: dict[str, Any]) -> None:
        self.store().set(data, persist=True)

    # ------------------------------------------------------------------ #
    # 定时推送
    # ------------------------------------------------------------------ #
    async def push_updates(self) -> None:
        """检查所有订阅画师的更新并推送（供调度器调用）。"""
        data = self.load_data()
        if not data:
            return

        artist_ids: list[str] = []
        for entry in data.values():
            artists = entry.get("artists") or {}
            artist_ids.extend(str(key) for key in artists)

        unique_ids = list(dict.fromkeys(artist_ids))
        if not unique_ids:
            return

        updates = await self.pixiv.fetch_updates(unique_ids)
        if not updates:
            return

        for session, entry in data.items():
            if not entry.get("pushEnabled"):
                continue
            umo = entry.get("umo") or ""
            if not umo:
                continue
            artists = entry.get("artists") or {}
            for artist_id, artist_name in artists.items():
                new_works = updates.get(str(artist_id)) or []
                if not new_works:
                    continue
                for work_id in new_works[:PUSH_MAX_WORKS]:
                    try:
                        await self._push_work(umo, artist_id, artist_name, work_id)
                    except Exception as exc:
                        logger.warning(
                            f"[涩批] 推送作品 {work_id} 失败: {exc}"
                        )
                    await asyncio.sleep(PUSH_INTERVAL_SECONDS)

    async def _push_work(
        self,
        umo: str,
        artist_id: str,
        artist_name: str,
        work_id: str,
    ) -> None:
        """推送单个新作品到指定会话。"""
        from astrbot.api.event import MessageChain # type: ignore
        from astrbot.api.message_components import Image, Plain # type: ignore

        details = await self.pixiv.fetch_illust(work_id)
        if not details or not details.get("body"):
            return
        body = details["body"]
        urls = self._image_urls(body)
        if not urls:
            return

        paths = await self._download_push_images(urls)
        if not paths:
            return

        text = "\n".join(
            [
                f"爷爷，您关注的画师：{body.get('userName', artist_name)}"
                f"（{body.get('userId', artist_id)}）更新了",
                f"作品：{body.get('illustTitle', '')}",
                f"PID：{body.get('illustId', work_id)}",
                f"是否AI：{'是' if body.get('aiType') == 2 else '否'}",
                f"上传时间：{body.get('createDate', '')}",
                f"♥：{body.get('likeCount', 0)} "
                f"😊：{body.get('bookmarkCount', 0)} "
                f"👁：{body.get('viewCount', 0)}",
                f"标签：{self._tags(body)}",
            ]
        )
        chain = MessageChain().message(text)
        for path in paths:
            chain.chain.append(Image.fromFileSystem(path))
        await self.context.send_message(umo, chain)  # type: ignore[attr-defined]

    # ------------------------------------------------------------------ #
    # 工具
    # ------------------------------------------------------------------ #
    async def _download_push_images(self, urls: list[str]) -> list[str]:
        semaphore = asyncio.Semaphore(self.settings.max_concurrent_download)
        results: list[str | None] = [None] * len(urls)

        async def worker(index: int, url: str) -> None:
            async with semaphore:
                data = await self.file_cache.get(
                    url, timeout=self.settings.download_timeout
                )
                if not data:
                    return
                if self.settings.track_pixel:
                    data = add_noise(data)
                target = self.temp_path(
                    f"push_{index}_{random.randint(1000, 9999)}.jpg"
                )
                try:
                    save_bytes(target, data)
                except OSError:
                    return
                results[index] = str(target)

        await asyncio.gather(*(worker(i, url) for i, url in enumerate(urls)))
        return [path for path in results if path]

    @staticmethod
    def _image_urls(body: dict) -> list[str]:
        urls = body.get("urls")
        if not isinstance(urls, dict):
            return []
        return [str(value) for value in urls.values() if value]

    @staticmethod
    def _tags(body: dict) -> str:
        container = body.get("tags") or {}
        if not isinstance(container, dict):
            return ""
        items = container.get("tags") or []
        return ", ".join(
            str(item.get("tag"))
            for item in items
            if isinstance(item, dict) and item.get("tag")
        )

    @property
    def pixiv(self):
        from ..app_core.pixiv import PixivClient

        client = getattr(self, "_subscribe_pixiv_client", None)
        if client is None:
            client = PixivClient(self.settings)
            self._subscribe_pixiv_client = client
        return client
