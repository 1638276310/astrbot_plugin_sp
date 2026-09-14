"""Pixiv 相关接口封装（图片搜索 / 画师作品 / 订阅推送）。

对应原插件的 ``config/api.js`` 以及各文件里散落的 axios 请求。
"""

from __future__ import annotations

from urllib.parse import quote

from .app_core.http import HttpError, fetch_json
from .app_core.settings import PluginSettings


def parse_cookie_header(raw: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for part in str(raw or "").split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip()
        if key:
            cookies[key] = value.strip()
    return cookies


class PixivClient:
    """Pixiv 第三方接口客户端。"""

    def __init__(self, settings: PluginSettings, cookie: str = "") -> None:
        self.settings = settings
        self.base = settings.pixiv_api_base.rstrip("/")
        self.cookie = parse_cookie_header(cookie)

    # ------------------------------------------------------------------ #
    # URL 构造
    # ------------------------------------------------------------------ #
    def pid_url(self, pid: str | int) -> str:
        return f"{self.base}/pixiv?pid={pid}"

    def user_url(self, artist_id: str | int) -> str:
        return f"{self.base}/user?user={artist_id}"

    def tag_url(self, tag: str, mode: str = "all", order: str = "popular_d") -> str:
        """构造标签搜索 URL（与原 api.js 的 `&mode=&order=` 拼接方式一致）。"""
        encoded = quote(str(tag), safe="")
        return f"{self.base}/tag?tag={encoded}&mode={mode}&order={order}"

    # ------------------------------------------------------------------ #
    # 数据请求
    # ------------------------------------------------------------------ #
    async def fetch_illust(self, pid: str | int) -> dict | None:
        """获取单张作品详情。失败返回 None。"""
        try:
            data = await fetch_json(
                self.pid_url(pid),
                timeout=30,
                cookies=self.cookie or None,
            )
        except HttpError:
            return None
        if not isinstance(data, dict):
            return None
        return data

    async def fetch_artist(self, artist_id: str | int) -> dict | None:
        """获取画师详情。失败返回 None。"""
        try:
            data = await fetch_json(self.user_url(artist_id), timeout=30)
        except HttpError:
            return None
        if not isinstance(data, dict):
            return None
        return data

    async def fetch_tag_ids(
        self,
        tag: str,
        *,
        mode: str = "all",
        order: str = "popular_d",
    ) -> list[str]:
        """按标签搜索作品 ID 列表。"""
        url = self.tag_url(tag, mode=mode, order=order)
        try:
            data = await fetch_json(url, timeout=30)
        except HttpError:
            return []
        if not isinstance(data, dict):
            return []
        body = data.get("body") or {}
        items = body.get("data") if isinstance(body, dict) else None
        if not isinstance(items, list):
            return []
        ids: list[str] = []
        for item in items:
            if isinstance(item, dict) and item.get("id") is not None:
                ids.append(str(item["id"]))
        return ids

    # ------------------------------------------------------------------ #
    # 订阅推送
    # ------------------------------------------------------------------ #
    async def fetch_updates(self, artist_ids: list[str]) -> dict[str, list[str]]:
        """调用订阅接口查询画师更新，返回 {画师ID: [新作品ID, ...]}。"""
        if not artist_ids:
            return {}
        try:
            data = await fetch_json(
                self.settings.dingyue_api,
                timeout=60,
                method="POST",
                json_body={
                    "key": self.settings.dingyue_key,
                    "user": artist_ids,
                },
            )
        except HttpError:
            return {}
        if not isinstance(data, dict):
            return {}
        response = data.get("response")
        if not isinstance(response, dict):
            return {}
        result: dict[str, list[str]] = {}
        for artist_id, works in response.items():
            if isinstance(works, list):
                result[str(artist_id)] = [str(work) for work in works]
        return result
