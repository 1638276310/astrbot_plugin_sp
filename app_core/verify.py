"""「验车」——通过磁力链接查询文件详情。

对应原 ``MagnetLinkFetcher.js``：访问 whatslink.info 的 API 页面，
拿到 name / file_type / count / size / screenshots。
原实现用 puppeteer 抓隐藏 div 里的 JSON，这里先用 aiohttp 直接请求，
失败时再退回 Playwright。
"""

from __future__ import annotations

import json
from urllib.parse import quote

from .browser import BrowserError, browser_context, goto
from .http import HttpError, fetch_text
from .settings import PluginSettings

API_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://whatslink.info/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0"
    ),
}


class MagnetInfo:
    """验车结果。"""

    def __init__(self, payload: dict) -> None:
        self.raw = payload
        self.name: str = str(payload.get("name") or "未知")
        self.file_type: str = str(payload.get("file_type") or "未知")
        self.count: int = int(payload.get("count") or 0)
        self.size: int = int(payload.get("size") or 0)
        screenshots = payload.get("screenshots") or []
        self.screenshots: list[str] = [
            str(item.get("screenshot"))
            for item in screenshots
            if isinstance(item, dict) and item.get("screenshot")
        ]

    @property
    def size_gb(self) -> str:
        return f"{self.size / 1e9:.1f}"

    def describe(self, magnet: str) -> str:
        lines = [
            f"磁力链接：{magnet}",
            "",
            f"文件名字：{self.name}",
            f"文件类型：{self.file_type}",
            f"文件数量：{self.count}",
            f"文件大小：{self.size_gb}GB",
        ]
        return "\n".join(lines)


def build_magnet_url(settings: PluginSettings, magnet: str) -> str:
    """按原 api.js 的逻辑拼接查询 URL。"""
    base = settings.magnet_api
    separator = "&" if "?" in base else "?"
    return f"{base}{separator}url={quote(magnet, safe='')}"


async def fetch_magnet_info(
    settings: PluginSettings,
    magnet: str,
    *,
    timeout: int = 40,
) -> MagnetInfo | None:
    """查询磁力详情，失败返回 None。"""
    url = build_magnet_url(settings, magnet)

    # 1) 先尝试直接 HTTP 请求（更快）
    try:
        text = await fetch_text(url, timeout=timeout, headers=API_HEADERS)
        payload = _try_parse(text)
        if payload:
            return MagnetInfo(payload)
    except HttpError:
        pass

    # 2) 回退到 Playwright（仅捕获浏览器相关错误；_fetch_with_browser
    # 内部已把 HTTP/JSON 解析等异常归一化为 BrowserError 或返回 None）
    try:
        payload = await _fetch_with_browser(url, timeout=timeout)
    except BrowserError:
        payload = None
    if payload:
        return MagnetInfo(payload)
    return None


def _try_parse(text: str) -> dict | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # 页面里可能包着 HTML，尝试截取第一个 { 到最后一个 }
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    if isinstance(data, dict) and data.get("error"):
        return None
    return data if isinstance(data, dict) else None


async def _fetch_with_browser(url: str, *, timeout: int = 40) -> dict | None:
    async with browser_context(
        headless=True,
        extra_headers=API_HEADERS,
        timeout_ms=timeout * 1000,
    ) as (_browser, _context, page):
        await goto(page, url, timeout_ms=timeout * 1000)
        # 原实现优先读取隐藏的 div，其次读取 body 文本
        try:
            hidden = await page.eval_on_selector(
                'div[hidden="true"]', "el => el.textContent"
            )
            payload = _try_parse(hidden or "")
            if payload:
                return payload
        except Exception:
            pass
        try:
            body_text = await page.inner_text("body")
        except Exception:
            return None
        return _try_parse(body_text)
