"""美图吧（ku1373）套图解析。

对应原 ``mtb.js``：

* ``/随机美图吧``      —— 从已保存的套图链接里随机抽一套
* ``/套图详情 <URL>``  —— 解析指定套图并发送全部图片
* ``/更新套图列表``    —— 增量采集前 N 页
* ``/全量更新套图列表``—— 从第一页采到最后一页
"""

from __future__ import annotations

import re

from .browser import (
    BrowserError,
    browser_context,
    goto,
    query_all_attr,
    query_all_text,
    query_first_text,
    random_delay,
)
from .settings import PluginSettings


def _dbg(message: str) -> None:
    """统一的控制台 debug 日志输出（失败静默，不影响主流程）。"""
    try:
        from astrbot.api import logger # type: ignore
        logger.info(f"[涩批DEBUG] {message}")
    except Exception:
        pass


DEFAULT_LIST_URL = "/b/1/"
INCREMENTAL_MAX_PAGES = 2
COLLECT_RETRY = 3
PAGE_INFO_SELECTOR = "div.list div.w1200 div.page span.pageinfo"
ERROR_PAGE_PATTERN = re.compile(r"共\s*(\d+)\s*页")
DETAIL_URL_PATTERN = re.compile(r"套图详情\s+(https?://\S+)")


class AlbumDetail:
    """套图详情解析结果。"""

    def __init__(
        self,
        url: str,
        title: str = "未知标题",
        organization: str = "未知机构",
        publish_date: str = "未知时间",
        image_urls: list[str] | None = None,
    ) -> None:
        self.url = url
        self.title = title
        self.organization = organization
        self.publish_date = publish_date
        self.image_urls = image_urls or []

    def header_lines(self) -> list[str]:
        return [
            f"📌 标题: {self.title}",
            f"🏷️ 机构: {self.organization}",
            f"⏰ 发布时间: {self.publish_date}",
        ]


def parse_detail_url(text: str) -> str | None:
    match = DETAIL_URL_PATTERN.search((text or "").strip())
    return match.group(1) if match else None


def list_url(settings: PluginSettings, page: int = 1) -> str:
    base = settings.mtb_site.rstrip("/")
    if page <= 1:
        return f"{base}{DEFAULT_LIST_URL}"
    return f"{base}/b/1/list_1_{page}.html"


def detail_page_url(url: str, page_no: int) -> str:
    if page_no <= 1:
        return url
    base = re.sub(r"\.html$", "", url)
    return f"{base}_{page_no}.html"


# ---------------------------------------------------------------------- #
# 总页数 / 列表页采集
# ---------------------------------------------------------------------- #
async def get_total_pages(
    settings: PluginSettings, *, timeout: int = 60
) -> int | None:
    """获取套图列表总页数，失败返回 None。"""
    _dbg(f"mtb.get_total_pages：获取总页数（timeout={timeout}s）")
    async with browser_context(headless=True, timeout_ms=timeout * 1000) as (
        _browser,
        _context,
        page,
    ):
        try:
            await goto(page, list_url(settings, 1), timeout_ms=timeout * 1000)
        except BrowserError as exc:
            _dbg(f"mtb.get_total_pages：第一页打开失败：{exc}")
            return None

        info = await query_first_text(page, PAGE_INFO_SELECTOR, "")
        _dbg(f"mtb.get_total_pages：pageinfo 文本={info!r}")
        match = ERROR_PAGE_PATTERN.search(info or "")
        if match:
            total = int(match.group(1))
            _dbg(f"mtb.get_total_pages：总页数 = {total}")
            return total

        numbers = await query_all_text(page, "div.page a")
        pages = [int(text) for text in numbers if text.strip().isdigit()]
        if pages:
            total = max(pages)
            _dbg(f"mtb.get_total_pages：翻页按钮最大页 = {total}")
            return total
        _dbg("mtb.get_total_pages：未找到总页数")
        return None


async def fetch_page_urls(
    settings: PluginSettings,
    page_num: int,
    *,
    timeout: int = 45,
) -> list[str]:
    """采集某一列表页中的所有套图详情链接。"""
    target = list_url(settings, page_num)
    _dbg(f"mtb.fetch_page_urls：开始采集第 {page_num} 页 {target}")
    async with browser_context(headless=True, timeout_ms=timeout * 1000) as (
        _browser,
        _context,
        page,
    ):
        for attempt in range(1, COLLECT_RETRY + 1):
            try:
                await goto(page, target, timeout_ms=timeout * 1000)
                links = await query_all_attr(
                    page, "div.m-list.ml1 ul.cl li a", "href"
                )
                if not links:
                    links = await query_all_attr(page, "div.m-list ul.cl li a", "href")
                urls = [normalize_url(settings, href) for href in links]
                urls = [url for url in urls if url]
                unique = list(dict.fromkeys(urls))
                _dbg(
                    f"mtb.fetch_page_urls：第 {page_num} 页第 {attempt} 次尝试"
                    f" 采集到 {len(unique)} 个URL"
                )
                if unique:
                    return unique
            except BrowserError as exc:
                _dbg(f"mtb.fetch_page_urls：第 {page_num} 页第 {attempt} 次尝试失败：{exc}")
            if attempt < COLLECT_RETRY:
                await random_delay(800 * attempt, 1200 * attempt)
    _dbg(f"mtb.fetch_page_urls：第 {page_num} 页 {COLLECT_RETRY} 次尝试均失败")
    return []


def normalize_url(settings: PluginSettings, href: str) -> str:
    if not href:
        return ""
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return f"{settings.mtb_site.rstrip('/')}{href}"
    return ""


async def collect_album_urls(
    settings: PluginSettings,
    *,
    existing: list[str] | None = None,
    incremental: bool = True,
) -> tuple[list[str], int | None]:
    """采集套图链接。

    返回 (合并后的链接列表, 总页数)。
    incremental=True 时只采集前 INCREMENTAL_MAX_PAGES 页并与已有列表合并；
    否则从第一页采到最后一页并覆盖。
    """
    _dbg(f"mtb.collect_album_urls：开始 增量模式={incremental} 现有URL={len(existing or [])}")
    total_pages = await get_total_pages(settings)
    if total_pages is None:
        _dbg("mtb.collect_album_urls：无法获取总页数，返回现有列表")
        return list(existing or []), None

    if incremental:
        pages_to_fetch = min(INCREMENTAL_MAX_PAGES, total_pages)
    else:
        pages_to_fetch = total_pages
    _dbg(f"mtb.collect_album_urls：总页数 {total_pages}，需采集 {pages_to_fetch} 页")

    collected: list[str] = []
    for page_num in range(1, pages_to_fetch + 1):
        urls = await fetch_page_urls(settings, page_num)
        collected.extend(urls)
        _dbg(f"mtb.collect_album_urls：第 {page_num} 页完成，累计 {len(collected)} 个")
        await random_delay(600, 1200)

    if incremental:
        merged = list(dict.fromkeys(list(existing or []) + collected))
        _dbg(
            f"mtb.collect_album_urls：增量合并后共 {len(merged)} 个"
            f"（新增 {len(merged) - len(existing or [])}）"
        )
        return merged, total_pages
    _dbg(f"mtb.collect_album_urls：全量去重后共 {len(collected)} 个")
    return list(dict.fromkeys(collected)), total_pages


# ---------------------------------------------------------------------- #
# 套图详情
# ---------------------------------------------------------------------- #
async def fetch_album_detail(
    settings: PluginSettings,
    url: str,
    *,
    timeout: int = 60,
) -> AlbumDetail:
    """解析套图详情页，收集全部图片地址。"""
    _dbg(f"mtb.fetch_album_detail：开始解析 {url}（timeout={timeout}s）")
    image_urls: list[str] = []
    seen: set[str] = set()
    organization = "未知机构"
    publish_date = "未知时间"
    title = "未知标题"

    async with browser_context(headless=True, timeout_ms=timeout * 1000) as (
        _browser,
        _context,
        page,
    ):
        await goto(page, url, timeout_ms=timeout * 1000)

        organization = await _read_organization(page) or organization
        _dbg(f"mtb.fetch_album_detail：机构={organization!r}")
        breadcrumb = await query_first_text(page, "div.position div.w1200", "")
        match = re.search(r"\b(\d{4}\.\d{2}\.\d{2})\b", breadcrumb or "")
        if match:
            publish_date = match.group(1)
        if breadcrumb:
            parts = [part.strip() for part in breadcrumb.split(">") if part.strip()]
            if parts:
                title = parts[-1]
        _dbg(f"mtb.fetch_album_detail：标题={title!r} 发布时间={publish_date!r}")

        total_pages = await _read_total_pages(page)
        total_pages = min(max(1, total_pages), settings.max_pages_per_album)
        _dbg(f"mtb.fetch_album_detail：详情页共 {total_pages} 页（上限 {settings.max_pages_per_album}）")

        for page_no in range(1, total_pages + 1):
            if page_no > 1:
                try:
                    await goto(
                        page,
                        detail_page_url(url, page_no),
                        timeout_ms=timeout * 1000,
                    )
                except BrowserError as exc:
                    _dbg(f"mtb.fetch_album_detail：第 {page_no} 页打开失败：{exc}")
                    continue
            try:
                sources = await query_all_attr(
                    page, "div.content img.tupian_img", "src"
                )
            except Exception:
                sources = []
            new_count = 0
            for src in sources:
                if src and src.startswith("http") and src not in seen:
                    seen.add(src)
                    image_urls.append(src)
                    new_count += 1
            _dbg(
                f"mtb.fetch_album_detail：第 {page_no} 页新增 {new_count} 张，"
                f"累计 {len(image_urls)} 张"
            )
            await random_delay(400, 900)

    _dbg(f"mtb.fetch_album_detail：解析完成，共 {len(image_urls)} 张图片")
    return AlbumDetail(url, title, organization, publish_date, image_urls)


async def _read_organization(page) -> str:
    for selector in (
        "div.position div.w1200 a:nth-child(3)",
        "div.position div.w1200 a:last-child",
    ):
        try:
            element = await page.query_selector(selector)
        except Exception:
            element = None
        if element is None:
            continue
        try:
            title_attr = await element.get_attribute("title")
        except Exception:
            title_attr = None
        if title_attr:
            return title_attr.strip()
        try:
            text = (await element.inner_text()) or ""
        except Exception:
            text = ""
        if text.strip():
            return text.strip()
    return ""


async def _read_total_pages(page) -> int:
    texts = await query_all_text(page, "div.page a")
    numbers = [int(text) for text in texts if text.strip().isdigit()]
    if numbers:
        return max(numbers)
    page_text = await query_first_text(page, "div.page", "")
    match = ERROR_PAGE_PATTERN.search(page_text or "")
    if match:
        return int(match.group(1))
    return 1
