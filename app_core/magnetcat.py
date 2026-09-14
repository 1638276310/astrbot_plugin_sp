"""磁力猫搜索。

对应原 ``MagnetLinkMao.js``：依次尝试多个镜像站，
解析 ``.ssbox`` 列表得到标题 / 磁力链接 / 添加时间 / 大小 / 最近下载 / 热度。
"""

from __future__ import annotations

import re
from urllib.parse import quote

from .app_core.browser import BrowserError, browser_context, goto, query_all_text
from .app_core.settings import FILE_TYPE_MAP, ORDER_TYPE_MAP, PluginSettings


class MagnetResult:
    """单条磁力搜索结果。"""

    def __init__(
        self,
        title: str,
        magnet: str,
        added_time: str = "",
        size: str = "",
        recent_download: str = "",
        heat: str = "",
    ) -> None:
        self.title = title
        self.magnet = magnet
        self.added_time = added_time
        self.size = size
        self.recent_download = recent_download
        self.heat = heat

    def describe(self) -> str:
        parts = [self.title, "", self.magnet, ""]
        if self.added_time:
            parts.append(f"添加时间：{self.added_time}")
        if self.size:
            parts.append(f"大小：{self.size}")
        if self.recent_download:
            parts.append(f"最近下载：{self.recent_download}")
        if self.heat:
            parts.append(f"热度：{self.heat}")
        return "\n".join(parts)


def parse_command(text: str) -> tuple[str, str, str, int] | None:
    """解析 ``#磁力猫 关键词 [类型] [排序] [数量]``。

    返回 (关键词, 文件类型, 排序, 数量) 或 None。
    """
    match = re.match(
        r"^#?磁力猫\s*(\S+)(?:\s+(\S+))?(?:\s+(\S+))?(?:\s+(\d+))?$",
        (text or "").strip(),
    )
    if not match:
        return None
    keyword = match.group(1)
    file_type = match.group(2) or ""
    order = match.group(3) or ""
    count = int(match.group(4)) if match.group(4) else 10
    return keyword, file_type, order, count


def build_search_urls(
    settings: PluginSettings,
    keyword: str,
    file_type: str = "",
    order: str = "",
) -> list[str]:
    """按镜像站列表构造搜索 URL。"""
    encoded = quote(keyword, safe="")
    type_code = FILE_TYPE_MAP.get(file_type, 0)
    order_code = ORDER_TYPE_MAP.get(order, 0)
    urls: list[str] = []
    for site in settings.magnet_cat_sites:
        base = str(site).rstrip("/")
        urls.append(
            f"{base}/search-{encoded}-{type_code}-{order_code}-1.html"
        )
    return urls


async def search_magnet(
    settings: PluginSettings,
    keyword: str,
    *,
    file_type: str = "",
    order: str = "",
    count: int = 10,
    timeout: int = 60,
) -> tuple[list[MagnetResult], str]:
    """在多个镜像站上搜索，返回 (结果列表, 错误信息)。"""
    urls = build_search_urls(settings, keyword, file_type, order)
    if not urls:
        return [], "磁力猫站点列表为空，请在插件配置中填写。"

    last_error = ""
    async with browser_context(headless=True, timeout_ms=timeout * 1000) as (
        _browser,
        _context,
        page,
    ):
        for url in urls:
            try:
                await goto(page, url, timeout_ms=timeout * 1000)
                try:
                    await page.wait_for_selector(".ssbox", timeout=8_000)
                except Exception:
                    last_error = "该镜像站没有返回结果"
                    continue

                boxes = await page.query_selector_all(".ssbox")
                if not boxes:
                    last_error = "该镜像站没有返回结果"
                    continue

                results: list[MagnetResult] = []
                for box in boxes[: max(1, count)]:
                    result = await _parse_box(box)
                    if result is not None:
                        results.append(result)
                if results:
                    return results, ""
                last_error = "解析结果为空"
            except BrowserError as exc:
                last_error = str(exc)
                continue
            except Exception as exc:  # pragma: no cover - 网络/页面异常
                last_error = str(exc)
                continue

    return [], last_error or "所有镜像站均无搜索结果"


async def _parse_box(box) -> MagnetResult | None:
    """解析单个 .ssbox 元素。"""
    try:
        title = ""
        title_el = await box.query_selector(".title h3 a")
        if title_el is not None:
            title = ((await title_el.inner_text()) or "").strip()

        magnet = ""
        magnet_el = await box.query_selector('.sbar a[href^="magnet:"]')
        if magnet_el is not None:
            magnet = (await magnet_el.get_attribute("href")) or ""

        if not magnet:
            return None

        meta = await _parse_metadata(box)
        return MagnetResult(
            title=title,
            magnet=magnet,
            added_time=meta.get("addedTime", ""),
            size=meta.get("size", ""),
            recent_download=meta.get("recentDownload", ""),
            heat=meta.get("heat", ""),
        )
    except Exception:
        return None


async def _parse_metadata(box) -> dict[str, str]:
    """提取 .sbar span 中的元数据。"""
    data: dict[str, str] = {}
    try:
        spans = await box.query_selector_all(".sbar span")
    except Exception:
        return data
    for span in spans:
        try:
            text = (await span.inner_text()) or ""
        except Exception:
            continue
        if "添加时间" in text:
            data["addedTime"] = await _inner_text(span, "b")
        elif "大小" in text:
            data["size"] = await _inner_text(span, ".yellow-pill")
        elif "最近下载" in text:
            data["recentDownload"] = await _inner_text(span, "b")
        elif "热度" in text:
            data["heat"] = await _inner_text(span, "b")
    return data


async def _inner_text(parent, selector: str) -> str:
    try:
        child = await parent.query_selector(selector)
        if child is None:
            return ""
        return ((await child.inner_text()) or "").strip()
    except Exception:
        return ""


def describe_results(results: list[MagnetResult]) -> list[str]:
    """把结果列表转成合并转发用的文本列表。"""
    return [result.describe() for result in results]


__all__ = [
    "MagnetResult",
    "parse_command",
    "build_search_urls",
    "search_magnet",
    "describe_results",
    "query_all_text",
]
