"""基于 aiohttp 的异步 HTTP 小工具。

AstrBot 明确要求插件使用异步网络库（aiohttp / httpx），不要使用 requests。
"""

from __future__ import annotations

import asyncio
import json as jsonlib
import random
from typing import Any

import aiohttp

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# 与原 mtb.js / mzt.js 相同的 UA 池
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/16.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:115.0) Gecko/20100101 Firefox/115.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/118.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]


class HttpError(Exception):
    """HTTP 请求失败。"""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def random_ua() -> str:
    return random.choice(USER_AGENTS)


def _headers(
    ua: str | None = None,
    referer: str | None = None,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    headers = {
        "User-Agent": ua or random_ua(),
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if referer:
        headers["Referer"] = referer
    if extra:
        headers.update(extra)
    return headers


async def fetch_bytes(
    url: str,
    *,
    timeout: int = 30,
    referer: str | None = None,
    ua: str | None = None,
    headers: dict[str, str] | None = None,
    max_size: int = 64 * 1024 * 1024,
    proxy: str | None = None,
) -> bytes:
    """下载二进制内容。"""
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    try:
        async with aiohttp.ClientSession(timeout=client_timeout) as session:
            async with session.get(
                url,
                headers=_headers(ua, referer, headers),
                proxy=proxy,
                allow_redirects=True,
            ) as resp:
                if resp.status != 200:
                    raise HttpError(f"HTTP {resp.status}", resp.status)
                body = await resp.content.read(max_size)
                return body
    except aiohttp.ClientError as exc:
        raise HttpError(f"网络请求失败: {exc}") from exc
    except asyncio.TimeoutError as exc:
        raise HttpError("网络请求超时") from exc


async def fetch_text(
    url: str,
    *,
    timeout: int = 30,
    referer: str | None = None,
    ua: str | None = None,
    encoding: str | None = None,
    headers: dict[str, str] | None = None,
) -> str:
    """下载文本内容。"""
    data = await fetch_bytes(
        url, timeout=timeout, referer=referer, ua=ua, headers=headers
    )
    for candidate in filter(None, [encoding, "utf-8", "gbk", "latin-1"]):
        try:
            return data.decode(candidate)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="ignore")


async def fetch_json(
    url: str,
    *,
    timeout: int = 30,
    referer: str | None = None,
    ua: str | None = None,
    headers: dict[str, str] | None = None,
    method: str = "GET",
    json_body: dict[str, Any] | None = None,
    cookies: dict[str, str] | None = None,
) -> Any:
    """请求并解析 JSON。"""
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    req_headers = _headers(ua, referer, headers)
    if json_body is not None:
        req_headers.setdefault("Content-Type", "application/json")
    try:
        async with aiohttp.ClientSession(timeout=client_timeout) as session:
            async with session.request(
                method.upper(),
                url,
                headers=req_headers,
                json=json_body,
                cookies=cookies,
                allow_redirects=True,
            ) as resp:
                text = await resp.text()
                if resp.status != 200:
                    raise HttpError(f"HTTP {resp.status}: {text[:200]}", resp.status)
                try:
                    return jsonlib.loads(text)
                except jsonlib.JSONDecodeError as exc:
                    raise HttpError(f"返回内容不是合法 JSON: {text[:200]}") from exc
    except aiohttp.ClientError as exc:
        raise HttpError(f"网络请求失败: {exc}") from exc
    except asyncio.TimeoutError as exc:
        raise HttpError("网络请求超时") from exc


async def fetch_many(
    urls: list[str],
    *,
    concurrency: int = 5,
    timeout: int = 20,
    referer: str | None = None,
    ua: str | None = None,
) -> list[tuple[str, bytes | None]]:
    """并发下载多个 URL，返回 [(url, bytes|None)]，保持输入顺序。"""
    semaphore = asyncio.Semaphore(max(1, concurrency))
    results: list[tuple[str, bytes | None]] = [None] * len(urls)  # type: ignore[list-item]

    async def worker(index: int, url: str) -> None:
        async with semaphore:
            try:
                data = await fetch_bytes(url, timeout=timeout, referer=referer, ua=ua)
                results[index] = (url, data)
            except Exception:
                results[index] = (url, None)

    await asyncio.gather(*(worker(i, url) for i, url in enumerate(urls)))
    return [item for item in results if item is not None]
