"""Playwright 无头浏览器封装。

原插件使用 puppeteer 抓取妹子图 / 美图吧 / 磁力猫 / 验车页面，
这里改用 AstrBot 同生态的 Playwright（异步 API），并保持相同的选择器与UA池。

注意：Playwright 需要额外安装浏览器内核，首次使用请执行：
    playwright install chromium
"""

from __future__ import annotations

import asyncio
import random
from contextlib import asynccontextmanager
from typing import Any

from .http import USER_AGENTS

try:  # Playwright 为可选依赖，缺失时给出明确提示
    from playwright.async_api import Browser, BrowserContext, Page, async_playwright# type: ignore

    PLAYWRIGHT_AVAILABLE = True
    PLAYWRIGHT_IMPORT_ERROR = ""
except Exception as exc:  # pragma: no cover
    Browser = Any  # type: ignore[assignment,misc]
    BrowserContext = Any  # type: ignore[assignment,misc]
    Page = Any  # type: ignore[assignment,misc]
    async_playwright = None  # type: ignore[assignment]
    PLAYWRIGHT_AVAILABLE = False
    PLAYWRIGHT_IMPORT_ERROR = str(exc)


class BrowserError(Exception):
    """浏览器相关错误。"""


def _dbg(message: str) -> None:
    """统一的控制台 debug 日志输出（失败静默，不影响主流程）。"""
    try:
        from astrbot.api import logger # type: ignore
        logger.info(f"[涩批DEBUG] {message}")
    except Exception:
        pass


LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--window-size=1920,1080",
]


def ensure_playwright() -> None:
    """确认 playwright 可用，否则抛出带安装提示的错误。"""
    if not PLAYWRIGHT_AVAILABLE:
        _dbg(f"ensure_playwright：playwright 不可用，{PLAYWRIGHT_IMPORT_ERROR}")
        raise BrowserError(
            "未安装 playwright，请先执行 `pip install playwright` 与 "
            f"`playwright install chromium`。（{PLAYWRIGHT_IMPORT_ERROR}）"
        )


@asynccontextmanager
async def browser_context(
    *,
    headless: bool = True,
    user_agent: str | None = None,
    extra_headers: dict[str, str] | None = None,
    timeout_ms: int = 60_000,
    proxy: str | None = None,
):
    """启动浏览器并返回 (browser, context, page)。"""
    _dbg(
        f"browser_context：开始 headless={headless} timeout_ms={timeout_ms} "
        f"proxy={proxy!r}"
    )
    ensure_playwright()
    playwright = await async_playwright().start() # type: ignore
    _dbg("browser_context：playwright 已启动")
    launch_kwargs: dict[str, Any] = {
        "headless": headless,
        "args": LAUNCH_ARGS,
        "timeout": timeout_ms,
    }
    if proxy:
        launch_kwargs["proxy"] = {"server": proxy}
    browser = None
    try:
        browser = await playwright.chromium.launch(**launch_kwargs)
        _dbg("browser_context：chromium 已启动")
        context = await browser.new_context(
            user_agent=user_agent or random.choice(USER_AGENTS),
            extra_http_headers=extra_headers or {},
            viewport={"width": 1920, "height": 1080},
            ignore_https_errors=True,
        )
        context.set_default_timeout(timeout_ms)
        context.set_default_navigation_timeout(timeout_ms)
        page = await context.new_page()
        _dbg("browser_context：浏览器上下文与页面已就绪")
        yield browser, context, page
    except Exception as exc:
        _dbg(f"browser_context：浏览器启动/运行失败：{exc!r}")
        raise
    finally:
        try:
            if browser is not None:
                await browser.close()
        finally:
            await playwright.stop()
            _dbg("browser_context：playwright 已停止")


async def goto(
    page: Page, # type: ignore
    url: str,
    *,
    timeout_ms: int = 60_000,
    wait_until: str = "domcontentloaded",
) -> None:
    """打开页面，忽略等待策略上的兼容性差异。"""
    _dbg(f"goto：打开 {url[:120]}... timeout_ms={timeout_ms}")
    try:
        await page.goto(url, wait_until=wait_until, timeout=timeout_ms)
        _dbg(f"goto：成功打开 {url[:120]}")
    except Exception as exc:
        _dbg(f"goto：打开 {url[:120]} 失败：{exc!r}")
        raise BrowserError(f"打开页面失败 {url}: {exc}") from exc


async def query_all_text(page: Page, selector: str) -> list[str]: # type: ignore
    """取所有匹配元素的 innerText。"""
    try:
        elements = await page.query_selector_all(selector)
    except Exception as exc:
        _dbg(f"query_all_text：{selector} 查询失败：{exc!r}")
        return []
    texts: list[str] = []
    for element in elements:
        try:
            texts.append((await element.inner_text()) or "")
        except Exception:
            continue
    _dbg(f"query_all_text：{selector} 取到 {len(texts)} 段文本")
    return texts


async def query_all_attr(page: Page, selector: str, attr: str) -> list[str]: # type: ignore
    """取所有匹配元素的指定属性。"""
    try:
        elements = await page.query_selector_all(selector)
    except Exception as exc:
        _dbg(f"query_all_attr：{selector} 查询失败：{exc!r}")
        return []
    values: list[str] = []
    for element in elements:
        try:
            value = await element.get_attribute(attr)
        except Exception:
            value = None
        if value:
            values.append(value)
    _dbg(f"query_all_attr：{selector}[{attr}] 取到 {len(values)} 个值")
    return values


async def query_first_text(page: Page, selector: str, default: str = "") -> str: # type: ignore
    """取第一个匹配元素的 innerText。"""
    try:
        element = await page.query_selector(selector)
        if element is None:
            _dbg(f"query_first_text：{selector} 未匹配到元素，返回默认值")
            return default
        return (await element.inner_text()) or default
    except Exception as exc:
        _dbg(f"query_first_text：{selector} 取文本失败：{exc!r}")
        return default


async def query_first_attr(
    page: Page, selector: str, attr: str, default: str = "" # type: ignore
) -> str:
    """取第一个匹配元素的属性。"""
    try:
        element = await page.query_selector(selector)
        if element is None:
            _dbg(f"query_first_attr：{selector} 未匹配到元素，返回默认值")
            return default
        value = await element.get_attribute(attr)
        _dbg(f"query_first_attr：{selector}[{attr}] = {value!r}")
        return value or default
    except Exception as exc:
        _dbg(f"query_first_attr：{selector}[{attr}] 取属性失败：{exc!r}")
        return default


async def page_content(page: Page) -> str: # type: ignore
    """取页面 HTML 内容。"""
    try:
        return await page.content()
    except Exception:
        return ""


async def page_title(page: Page) -> str: # type: ignore
    try:
        return await page.title()
    except Exception:
        return ""


async def sleep_ms(milliseconds: int) -> None:
    """随机抖动等待，降低被风控概率。"""
    await asyncio.sleep(max(0, milliseconds) / 1000)


async def random_delay(low_ms: int = 300, high_ms: int = 1200) -> None:
    await sleep_ms(random.randint(low_ms, max(low_ms, high_ms)))


def script_for_hidden_json(selector: str) -> str:
    """生成一个用于读取隐藏 JSON 容器的 JS 片段。"""
    return (
        "(() => { const el = document.querySelector("
        f"{selector!r}); return el ? el.textContent : null; }})()"
    )
