"""妹子图（kkmzt）解析。

对应原 ``mzt.js``：

* ``#写真馆<ID>``  —— 解析写真详情页，最多取 20 张图
* ``#更新写真ID``   —— 增量爬取写真 ID 列表
* ``#随机写真``     —— 随机取一个 ID 再解析
"""

from __future__ import annotations

import re

from core.browser import (
    BrowserError,
    browser_context,
    goto,
    query_first_text,
    random_delay,
)
from core.settings import PluginSettings

MAX_IMAGES = 20
ID_PATTERN = re.compile(r"/photo/(\d+)/?$")
NEXT_BUTTON = (
    "div.uk-position-center-right.uk-overlay.uk-overlay-default.f-swich[action='next']"
)


class AlbumInfo:
    """一个写真页的解析结果。"""

    def __init__(
        self,
        article_id: str,
        title: str = "未知标题",
        publish_time: str = "未知时间",
        image_urls: list[str] | None = None,
    ) -> None:
        self.article_id = article_id
        self.title = title
        self.publish_time = publish_time
        self.image_urls = image_urls or []

    @property
    def header(self) -> str:
        return (
            f"📌 文章标题: {self.title}\n"
            f"⏰ 发布时间: {self.publish_time}\n"
            f"🆔 文章ID: {self.article_id}"
        )


def parse_article_id(text: str) -> str | None:
    """从 ``#写真馆12345`` 中取 ID。"""
    match = re.match(r"^#?写真馆\s*(\d+)$", (text or "").strip())
    return match.group(1) if match else None


def album_url(settings: PluginSettings, article_id: str | int) -> str:
    return f"{settings.mzt_site}/photo/{article_id}"


async def fetch_album(
    settings: PluginSettings,
    article_id: str | int,
    *,
    timeout: int = 60,
) -> AlbumInfo:
    """解析单个写真页，最多收集 20 张图片。

    解析失败会抛出 BrowserError。
    """
    base_url = album_url(settings, article_id)
    images: list[str] = []
    seen: set[str] = set()
    title = "未知标题"
    publish_time = "未知时间"

    async with browser_context(headless=True, timeout_ms=timeout * 1000) as (
        _browser,
        _context,
        page,
    ):
        await goto(page, base_url, timeout_ms=timeout * 1000)

        title = await query_first_text(
            page, "h1.uk-article-title.uk-text-truncate", title
        )
        publish_time = await query_first_text(page, "time", publish_time)

        images = await _collect_images(page, seen, images)
        while len(images) < MAX_IMAGES:
            try:
                next_button = await page.query_selector(NEXT_BUTTON)
            except Exception:
                next_button = None
            if next_button is None:
                break
            try:
                await next_button.click()
            except Exception:
                break
            await random_delay(600, 1400)
            images = await _collect_images(page, seen, images)

    if not title:
        title = "未知标题"
    return AlbumInfo(str(article_id), title, publish_time, images)


async def _collect_images(page, seen: set[str], images: list[str]) -> list[str]:
    """收集 ``img[referrerpolicy="origin"]`` 的图片地址。"""
    try:
        elements = await page.query_selector_all('img[referrerpolicy="origin"]')
    except Exception:
        return images
    for element in elements:
        if len(images) >= MAX_IMAGES:
            break
        try:
            src = await element.get_attribute("src")
        except Exception:
            src = None
        if not src or src in seen:
            continue
        seen.add(src)
        images.append(src)
    return images


async def collect_article_ids(
    settings: PluginSettings,
    existing: set[str],
    *,
    full: bool = False,
    timeout: int = 60,
) -> list[str]:
    """爬取写真 ID 列表。

    ``full=False`` 为增量模式：某一页的 ID 全部已存在时停止翻页。
    返回新发现的 ID（按页面顺序，去重）。
    """
    base_url = f"{settings.mzt_site}/photo/"
    discovered: list[str] = []
    found: set[str] = set(existing)
    page_no = 1

    async with browser_context(headless=True, timeout_ms=timeout * 1000) as (
        _browser,
        _context,
        page,
    ):
        while True:
            url = base_url if page_no == 1 else f"{base_url}page/{page_no}/"
            try:
                await goto(page, url, timeout_ms=timeout * 1000)
            except BrowserError:
                break

            try:
                elements = await page.query_selector_all("a.uk-inline.u-thumb-v")
            except Exception:
                elements = []

            page_ids: list[str] = []
            for element in elements:
                try:
                    href = await element.get_attribute("href")
                except Exception:
                    href = None
                if not href:
                    continue
                match = ID_PATTERN.search(href)
                if match:
                    page_ids.append(match.group(1))

            if not page_ids:
                break

            if not full and all(item in found for item in page_ids):
                break

            for item in page_ids:
                if item not in found:
                    found.add(item)
                    discovered.append(item)

            await random_delay(600, 1200)
            page_no += 1
            if page_no > 500:  # 安全上限
                break

    return discovered
