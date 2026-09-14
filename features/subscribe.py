"""画师订阅与推送。

对应原 ``dingyue.js`` + ``dingyue_Auto_update.js``：

* ``#订阅画师<ID>`` / ``#取消订阅<ID>`` / ``#订阅列表``
* ``#sp推送`` / ``#关闭sp推送``
* 定时检查更新并推送到开启了推送的会话

原插件用群号作为 key（Yunzai 只能在群里用），AstrBot 版同时支持群聊与私聊，
key 为群号或私聊用户 ID。
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

from astrbot.api.event import AstrMessageEvent, filter # type: ignore
from astrbot.api import logger  # 使用 astrbot 提供的 logger 接口 # type: ignore

from ._base import spFeature
from ..app_core.imaging import add_noise, save_bytes
from ..app_core.storage import JsonStore

SUBSCRIBE_TRIGGER = r"^#?订阅画师(\d+)$"
UNSUBSCRIBE_TRIGGER = r"^#?取消订阅(\d+)$"
LIST_TRIGGER = r"^#?订阅列表$"
ENABLE_TRIGGER = r"^#?sp推送$"
DISABLE_TRIGGER = r"^#?关闭sp推送$"

PUSH_MAX_WORKS = 3
PUSH_INTERVAL_SECONDS = 10


def empty_session() -> dict[str, Any]:
    return {"pushEnabled": False, "artists": {}, "umo": ""}


class SubscribeFeature(spFeature):
    """订阅相关指令与推送逻辑。"""

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
    # #订阅画师<ID>
    # ------------------------------------------------------------------ #
    @filter.regex(SUBSCRIBE_TRIGGER, priority=20)
    async def sp_subscribe(self, event: AstrMessageEvent):
        """订阅画师更新（#订阅画师<ID>）"""
        if not await self.guard(event):
            return

        artist_id = self.extract_number(event.get_message_str(), SUBSCRIBE_TRIGGER)
        if not artist_id:
            return

        session = self.session_key(event)
        data = self.load_data()

        if (
            self.settings.enable_group_limit
            and session not in data
            and len(data) >= self.settings.max_subscribe_sessions
        ):
            yield event.plain_result("已达到会话订阅上限！")
            return

        entry = data.setdefault(session, empty_session())
        artists = entry.setdefault("artists", {})
        if (
            self.settings.enable_group_limit
            and len(artists) >= self.settings.max_artists_per_session
        ):
            yield event.plain_result("该会话已达到画师订阅上限！")
            return

        if artist_id in artists:
            yield event.plain_result(f"已经订阅了{artist_id}")
            return

        yield event.plain_result("正在检查画师ID，请稍等...")

        artist = await self.pixiv.fetch_artist(artist_id)
        if not artist or artist.get("error"):
            yield event.plain_result("该画师id不存在")
            return

        artist_name = artist_id
        body = artist.get("body")
        if isinstance(body, dict):
            pickup = body.get("pickup")
            if isinstance(pickup, list) and pickup:
                first = pickup[0]
                if isinstance(first, dict) and first.get("userName"):
                    artist_name = str(first["userName"])

        artists[artist_id] = artist_name
        entry["umo"] = event.unified_msg_origin
        self.save_data(data)
        yield event.plain_result(f"成功订阅画师{artist_id}（{artist_name}）")

    # ------------------------------------------------------------------ #
    # #取消订阅<ID>
    # ------------------------------------------------------------------ #
    @filter.regex(UNSUBSCRIBE_TRIGGER, priority=20)
    async def sp_unsubscribe(self, event: AstrMessageEvent):
        """取消订阅画师（#取消订阅<ID>）"""
        if not await self.guard(event):
            return

        artist_id = self.extract_number(event.get_message_str(), UNSUBSCRIBE_TRIGGER)
        if not artist_id:
            return
        session = self.session_key(event)
        data = self.load_data()
        entry = data.get(session)
        if not entry or artist_id not in (entry.get("artists") or {}):
            yield event.plain_result(f"还未订阅{artist_id}哦")
            return
        entry["artists"].pop(artist_id, None)
        self.save_data(data)
        yield event.plain_result(f"成功取消订阅{artist_id}")

    # ------------------------------------------------------------------ #
    # #订阅列表
    # ------------------------------------------------------------------ #
    @filter.regex(LIST_TRIGGER, priority=20)
    async def sp_subscribe_list(self, event: AstrMessageEvent):
        """查看本会话已订阅的画师（#订阅列表）"""
        if not await self.guard(event):
            return

        session = self.session_key(event)
        data = self.load_data()
        entry = data.get(session) or {}
        artists = entry.get("artists") or {}
        if not artists:
            yield event.plain_result("当前没有订阅任何画师")
            return
        lines = ["订阅列表："]
        for artist_id, artist_name in artists.items():
            lines.append(f"{artist_name}  {artist_id}")
        lines.append(f"推送状态：{'已开启' if entry.get('pushEnabled') else '已关闭'}")
        yield event.plain_result("\n".join(lines))

    # ------------------------------------------------------------------ #
    # #sp推送 / #关闭sp推送
    # ------------------------------------------------------------------ #
    @filter.regex(ENABLE_TRIGGER, priority=20)
    async def sp_enable_push(self, event: AstrMessageEvent):
        """开启画师更新推送（#sp推送）"""
        if not await self.guard(event):
            return

        session = self.session_key(event)
        data = self.load_data()
        entry = data.setdefault(session, empty_session())
        if entry.get("pushEnabled"):
            yield event.plain_result("已经开启了sp推送。")
            return
        entry["pushEnabled"] = True
        entry["umo"] = event.unified_msg_origin
        self.save_data(data)
        yield event.plain_result("已开启sp推送。")

    @filter.regex(DISABLE_TRIGGER, priority=20)
    async def sp_disable_push(self, event: AstrMessageEvent):
        """关闭画师更新推送（#关闭sp推送）"""
        if not await self.guard(event):
            return

        session = self.session_key(event)
        data = self.load_data()
        entry = data.get(session)
        if not entry:
            yield event.plain_result("尚未开启sp推送，无需关闭。")
            return
        if not entry.get("pushEnabled"):
            yield event.plain_result("尚未开启sp推送，无需关闭。")
            return
        entry["pushEnabled"] = False
        self.save_data(data)
        yield event.plain_result("已关闭sp推送。")

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
        import random

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
