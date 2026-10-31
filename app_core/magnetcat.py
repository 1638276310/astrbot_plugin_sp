"""磁力猫搜索。

对应原 ``MagnetLinkMao.js``：依次尝试多个镜像站，
解析 ``.ssbox`` 列表得到标题 / 磁力链接 / 添加时间 / 大小 / 最近下载 / 热度。
"""

from __future__ import annotations

import re
from urllib.parse import quote, urlsplit, urlunsplit

from .browser import BrowserError, browser_context, goto
from .settings import FILE_TYPE_MAP, ORDER_TYPE_MAP, PluginSettings


def _dbg(message: str) -> None:
    """统一的控制台 debug 日志输出（失败静默，不影响主流程）。"""
    try:
        from astrbot.api import logger # type: ignore
        logger.info(f"[涩批DEBUG] {message}")
    except Exception:
        pass


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
        """把条目渲染为多行文本。"""
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
    """解析 ``/磁力猫 关键词 [类型] [排序] [数量]``。

    兼容带 ``/`` 前缀（被 AstrBot 唤醒前缀机制去掉前）和不带前缀两种情况。
    返回 (关键词, 文件类型, 排序, 数量) 或 None。
    """
    match = re.match(
        r"磁力猫\s*(\S+)(?:\s+(\S+))?(?:\s+(\S+))?(?:\s+(\d+))?$",
        (text or "").strip().lstrip("/"),
    )
    if not match:
        return None
    keyword = match.group(1)
    file_type = match.group(2) or ""
    order = match.group(3) or ""
    count = int(match.group(4)) if match.group(4) else 10
    return keyword, file_type, order, count


def _ascii_site(site: str) -> str:
    """把配置里的站点转成可请求的 ASCII URL（支持 ``魔法猫咪.lol`` 这类 IDN）。

    浏览器本身能打开 Unicode 域名，但统一转 punycode 后日志/重试/回退都更稳定。
    """
    text = str(site or "").strip()
    if not text:
        return ""
    if not text.startswith(("http://", "https://")):
        text = f"https://{text}"
    try:
        parts = urlsplit(text)
    except ValueError:
        return ""
    host = parts.hostname or ""
    if not host:
        return ""
    try:
        # 仅对非 ASCII 主机名做 IDNA，避免影响普通域名
        host_ascii = (
            host.encode("idna").decode("ascii")
            if any(ord(ch) > 127 for ch in host)
            else host
        )
    except UnicodeError:
        host_ascii = host
    netloc = host_ascii
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    path = (parts.path or "").rstrip("/")
    return urlunsplit((parts.scheme, netloc, path, parts.query, parts.fragment))


def build_search_urls(
    settings: PluginSettings,
    keyword: str,
    file_type: str = "",
    order: str = "",
) -> list[str]:
    """按镜像站列表构造搜索 URL（自动跳过非法/重复站点）。"""
    encoded = quote(keyword, safe="")
    type_code = FILE_TYPE_MAP.get(file_type, 0)
    order_code = ORDER_TYPE_MAP.get(order, 0)
    urls: list[str] = []
    for site in settings.magnet_cat_sites:
        base = _ascii_site(str(site))
        if not base:
            _dbg(f"magnetcat.build_search_urls：跳过非法站点 {site!r}")
            continue
        url = f"{base}/search-{encoded}-{type_code}-{order_code}-1.html"
        if url not in urls:
            urls.append(url)
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
    """在多个镜像站上搜索，返回 (结果列表, 错误信息)。

    站点按配置顺序尝试；单个站点打不开/被 Cloudflare 拦截时自动换下一个。
    每个站点最多等 30 秒，避免一个失效域名拖死整条命令。
    """
    urls = build_search_urls(settings, keyword, file_type, order)
    _dbg(
        f"magnetcat.search_magnet：关键词={keyword!r} 类型={file_type!r} "
        f"排序={order!r} 数量={count}，共 {len(urls)} 个镜像站"
    )
    if not urls:
        _dbg("magnetcat.search_magnet：镜像站列表为空")
        return [], "磁力猫站点列表为空，请在插件配置中填写。"

    last_error = ""
    site_timeout = max(10, min(int(timeout), 30))
    async with browser_context(headless=True, timeout_ms=site_timeout * 1000) as (
        _browser,
        _context,
        page,
    ):
        for index, url in enumerate(urls, start=1):
            _dbg(f"magnetcat.search_magnet：尝试镜像站 {index}/{len(urls)} {url}")
            try:
                await goto(page, url, timeout_ms=site_timeout * 1000)
                boxes = []
                try:
                    await page.wait_for_selector(".ssbox", timeout=6_000)
                    boxes = await page.query_selector_all(".ssbox")
                except Exception:
                    # 新模板可能不再使用 .ssbox，转入通用 magnet 链接解析
                    boxes = []

                results: list[MagnetResult] = []
                parser = ""
                if boxes:
                    parser = "ssbox"
                    for box in boxes[: max(1, count)]:
                        result = await _parse_box(box)
                        if result is not None:
                            results.append(result)

                if not results:
                    # 兼容改版后的页面结构：直接找 magnet 链接 + 最近容器文本
                    results = await _parse_generic(page, count)
                    parser = "generic" if results else parser

                if results:
                    _dbg(
                        f"magnetcat.search_magnet：{url} 成功 parser={parser or 'unknown'}，"
                        f"共 {len(results)} 条结果"
                    )
                    return results, ""
                last_error = "解析结果为空" if boxes else "该镜像站没有返回结果"
                _dbg(f"magnetcat.search_magnet：{url} {last_error}")
            except BrowserError as exc:
                last_error = str(exc)
                _dbg(f"magnetcat.search_magnet：{url} 浏览器错误 {exc}")
                continue
            except Exception as exc:  # pragma: no cover - 网络/页面异常
                last_error = str(exc)
                _dbg(f"magnetcat.search_magnet：{url} 异常 {exc!r}")
                continue

    _dbg(f"magnetcat.search_magnet：所有镜像站均无结果：{last_error}")
    return [], last_error or "所有镜像站均无搜索结果"


def _metadata_from_text(text: str) -> dict[str, str]:
    """从结果容器文本中提取元数据（兼容多个镜像的不同字段顺序）。"""
    data: dict[str, str] = {}
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if lines:
        data["title"] = lines[0]
    patterns = {
        "addedTime": r"(?:添加时间|发布时间)[:：]\s*([^\n]+)",
        "size": r"大小[:：]\s*([^\n]+)",
        "recentDownload": r"最近下载[:：]\s*([^\n]+)",
        "heat": r"热度[:：]\s*([^\n]+)",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, text or "")
        if match:
            data[key] = match.group(1).strip()
    return data


async def _parse_generic(page, count: int) -> list[MagnetResult]:
    """通用解析：不依赖 .ssbox，直接找 magnet 链接并读取最近容器文本。

    新域名/新版模板可能改掉 ``.ssbox`` 类名；只要页面仍有 ``magnet:``
    链接，本函数就能给出可用结果，属于域名失效回退的一部分。
    """
    try:
        links = await page.query_selector_all("a[href^='magnet:']")
    except Exception:
        return []
    results: list[MagnetResult] = []
    seen: set[str] = set()
    for link in links:
        try:
            magnet = (await link.get_attribute("href")) or ""
            if not magnet.startswith("magnet:") or magnet in seen:
                continue
            seen.add(magnet)
            text = await link.evaluate(
                "el => {"
                " const box = el.closest('.ssbox, li, tr, .item, .result, article, .card');"
                " return box ? box.innerText : (el.parentElement ? el.parentElement.innerText : '');"
                "}"
            )
            meta = _metadata_from_text(str(text or ""))
            title = meta.pop("title", "").strip()
            results.append(
                MagnetResult(
                    title=title or "未知标题",
                    magnet=magnet,
                    added_time=meta.get("addedTime", ""),
                    size=meta.get("size", ""),
                    recent_download=meta.get("recentDownload", ""),
                    heat=meta.get("heat", ""),
                )
            )
        except Exception:
            continue
        if len(results) >= max(1, count):
            break
    return results


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
]
