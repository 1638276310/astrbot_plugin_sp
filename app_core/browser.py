"""Playwright 无头浏览器封装（统一 Chrome 指纹 + Cloudflare 识别/等待）。

* 统一 Chrome UA（``http.BROWSER_UA``），不再随机混用 Firefox/Safari；
* ``locale=zh-CN``、``Accept-Language``、``Sec-*``；``ignore_https_errors``；
* 启动参数 ``--disable-blink-features=AutomationControlled``；
* ``page.add_init_script`` 隐藏 ``navigator.webdriver``；
* ``detect_cloudflare()`` / ``wait_cloudflare_clear()`` 识别并等待 CF 挑战。

Playwright 需额外安装内核：``playwright install chromium``。
"""

from __future__ import annotations

import asyncio
import inspect
import random
import time
from contextlib import asynccontextmanager
from typing import Any, Mapping
from urllib.parse import unquote, urlsplit

from .http import (
    ACCEPT_LANGUAGE,
    BROWSER_UA,
    cloudflare_ray,
    looks_like_cloudflare,
    sanitize_error_text,
)

try:
    from playwright.async_api import (  # type: ignore
        Browser,
        BrowserContext,
        Page,
        async_playwright,
    )

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
    try:
        from astrbot.api import logger  # type: ignore

        logger.info(f"[涩批DEBUG] {message}")
    except Exception:
        pass


LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--window-size=1920,1080",
    "--lang=zh-CN",
    "--disable-blink-features=AutomationControlled",
]

STEALTH_JS = """
(() => {
  try {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
    Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
  } catch (e) {}
})();
"""

CF_CHALLENGE_TITLES = (
    "just a moment", "attention required", "checking your browser",
    "请稍候", "ddos protection",
)
CF_BLOCK_TITLES = ("you have been blocked", "access denied")
CF_CHALLENGE_MARKERS = (
    "challenge-platform", "cf-chl", "challenges.cloudflare.com",
    "enable javascript and cookies", "cf-browser-verification",
    "_cf_chl_opt", "cf_challenge",
)
CF_BLOCK_MARKERS = (
    "you have been blocked", "sorry, you have been blocked",
    "cf-error-details", "cf-error-overview", "error 1020", "error code 1020",
)


def ensure_playwright() -> None:
    """确认 playwright 可用，否则抛出带安装提示的错误。"""
    if not PLAYWRIGHT_AVAILABLE:
        _dbg(f"ensure_playwright：playwright 不可用，{PLAYWRIGHT_IMPORT_ERROR}")
        raise BrowserError(
            "未安装 playwright，请先执行 `pip install playwright` 与 "
            f"`playwright install chromium`。（{PLAYWRIGHT_IMPORT_ERROR}）"
        )


def _playwright_proxy(proxy: str | None) -> dict[str, Any] | None:
    """``http://user:pass@host:port`` → Playwright proxy 配置。"""
    raw = str(proxy or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = "http://" + raw
    try:
        parts = urlsplit(raw)
    except ValueError:
        return {"server": raw}
    host = parts.hostname or ""
    if not host:
        return {"server": raw}
    server = f"{parts.scheme or 'http'}://{host}"
    try:
        if parts.port:
            server = f"{server}:{parts.port}"
    except ValueError:
        pass
    options: dict[str, Any] = {"server": server}
    if parts.username:
        options["username"] = unquote(parts.username)
    if parts.password:
        options["password"] = unquote(parts.password)
    return options


async def apply_stealth(page: Any) -> None:
    """页面加载前隐藏自动化特征（``navigator.webdriver`` 等）。"""
    try:
        add_script = getattr(page, "add_init_script", None)
        if callable(add_script):
            result = add_script(STEALTH_JS)
            if inspect.isawaitable(result):
                await result
            return
        legacy = getattr(page, "evaluate_on_new_document", None)
        if callable(legacy):
            result = legacy(STEALTH_JS)
            if inspect.isawaitable(result):
                await result
    except Exception as exc:
        _dbg(f"apply_stealth：注入失败（{type(exc).__name__}）")


@asynccontextmanager
async def browser_context(
    *, headless: bool = True, user_agent: str | None = None, locale: str = "zh-CN",
    extra_headers: Mapping[str, str] | None = None, timeout_ms: int = 60_000,
    proxy: str | None = None,
):
    """启动浏览器并返回 ``(browser, context, page)``（新增参数均有默认值）。"""
    _dbg(f"browser_context：headless={headless} timeout_ms={timeout_ms} proxy={'on' if proxy else 'off'}")
    ensure_playwright()
    playwright = None
    browser = None
    try:
        playwright = await async_playwright().start()  # type: ignore
        launch_kwargs: dict[str, Any] = {
            "headless": headless, "args": list(LAUNCH_ARGS), "timeout": timeout_ms,
        }
        proxy_options = _playwright_proxy(proxy)
        if proxy_options:
            launch_kwargs["proxy"] = proxy_options
        browser = await playwright.chromium.launch(**launch_kwargs)
    except Exception as exc:
        _dbg(f"browser_context：Chromium 启动失败（{type(exc).__name__}）")
        try:
            if playwright is not None:
                await playwright.stop()
        except Exception:
            pass
        raise BrowserError(
            f"Chromium 启动失败（{sanitize_error_text(str(exc), max_len=80)}）"
        ) from exc
    try:
        headers = {"Accept-Language": ACCEPT_LANGUAGE}
        if extra_headers:
            headers.update({str(k): str(v) for k, v in extra_headers.items()})
        context = await browser.new_context(
            user_agent=(user_agent or BROWSER_UA).strip() or BROWSER_UA,
            locale=locale,
            extra_http_headers=headers,
            viewport={"width": 1920, "height": 1080},
            ignore_https_errors=True,
        )
        context.set_default_timeout(timeout_ms)
        context.set_default_navigation_timeout(timeout_ms)
        page = await context.new_page()
        await apply_stealth(page)
        yield browser, context, page
    finally:
        for closer in (
            getattr(browser, "close", None) if browser is not None else None,
            getattr(playwright, "stop", None) if playwright is not None else None,
        ):
            if closer is None:
                continue
            try:
                result = closer()
                if inspect.isawaitable(result):
                    await result
            except Exception:
                pass
        _dbg("browser_context：playwright 已停止")


async def goto(
    page: Page, url: str, *, timeout_ms: int = 60_000, wait_until: str = "domcontentloaded"
) -> Any:
    """打开页面，返回 Playwright Response（失败抛 BrowserError）。"""
    try:
        response = await page.goto(url, wait_until=wait_until, timeout=timeout_ms)
    except Exception as exc:
        _dbg(f"goto：打开 {sanitize_error_text(url, max_len=80)} 失败（{type(exc).__name__}）")
        raise BrowserError(
            f"打开页面失败（{sanitize_error_text(str(exc), max_len=100)}）"
        ) from exc
    return response


async def page_content(page: Page) -> str:  # type: ignore
    try:
        return await page.content()
    except Exception:
        return ""


async def page_title(page: Page) -> str:  # type: ignore
    try:
        return await page.title()
    except Exception:
        return ""


async def query_all_text(page: Page, selector: str) -> list[str]:  # type: ignore
    """取所有匹配元素的 innerText。"""
    try:
        elements = await page.query_selector_all(selector)
    except Exception:
        return []
    texts: list[str] = []
    for element in elements:
        try:
            texts.append((await element.inner_text()) or "")
        except Exception:
            continue
    return texts


async def query_all_attr(page: Page, selector: str, attr: str) -> list[str]:  # type: ignore
    """取所有匹配元素的指定属性。"""
    try:
        elements = await page.query_selector_all(selector)
    except Exception:
        return []
    values: list[str] = []
    for element in elements:
        try:
            value = await element.get_attribute(attr)
        except Exception:
            value = None
        if value:
            values.append(value)
    return values


async def query_first_text(page: Page, selector: str, default: str = "") -> str:  # type: ignore
    try:
        element = await page.query_selector(selector)
        return ((await element.inner_text()) or default) if element is not None else default
    except Exception:
        return default


async def query_first_attr(
    page: Page, selector: str, attr: str, default: str = ""  # type: ignore
) -> str:
    try:
        element = await page.query_selector(selector)
        if element is None:
            return default
        return (await element.get_attribute(attr)) or default
    except Exception:
        return default


async def random_delay(low_ms: int = 300, high_ms: int = 1200) -> None:
    await asyncio.sleep(random.randint(low_ms, max(low_ms, high_ms)) / 1000)


# --------------------------------------------------------------------------- #
# Cloudflare 识别与等待
# --------------------------------------------------------------------------- #
def _headers_lower(response: Any) -> dict[str, str]:
    raw = getattr(response, "headers", None) if response is not None else None
    if not raw:
        return {}
    try:
        return {str(k).lower(): str(v) for k, v in dict(raw).items()}
    except Exception:
        return {}


async def detect_cloudflare(page: Page, response: Any = None) -> dict[str, Any]:  # type: ignore
    """识别页面是否被 Cloudflare 拦截/挑战。

    返回 ``{"kind": none|challenge|blocked|http-error, "ray": str, "status": int|None,
    "title": str}``。cf-ray 只用于展示；普通页面（哪怕带 cf-ray）不会判为拦截。
    """
    status: int | None = None
    headers: dict[str, str] = {}
    if response is not None:
        raw_status = getattr(response, "status", None)
        status = raw_status if isinstance(raw_status, int) else None
        headers = _headers_lower(response)

    title = await page_title(page)
    text = str(await page_content(page) or "")[:300_000]
    text_lower = text.lower()
    title_lower = str(title or "").strip().lower()
    ray = cloudflare_ray(headers, text)

    # 注意：正常文章页也可能注入 /cdn-cgi/challenge-platform/... 脚本，
    # 所以正文弱标记只在 403/503 时用于判断挑战页。
    challenge = any(m in title_lower for m in CF_CHALLENGE_TITLES) or (
        status in (403, 503) and any(m in text_lower for m in CF_CHALLENGE_MARKERS)
    )
    blocked = any(m in title_lower for m in CF_BLOCK_TITLES) or any(
        m in text_lower for m in CF_BLOCK_MARKERS
    )
    try:
        if await page.query_selector("#cf-error-details"):
            blocked = True
    except Exception:
        pass
    try:
        if await page.query_selector(
            "#challenge-form, #challenge-running, #cf-challenge-running"
        ):
            challenge = True
    except Exception:
        pass

    if blocked:
        kind = "blocked"
    elif challenge:
        kind = "challenge"
    elif status in (403, 503):
        kind = "blocked" if looks_like_cloudflare(status, headers, text) else "http-error"
    elif looks_like_cloudflare(status, headers, text):
        kind = "blocked"
    else:
        kind = "none"
    return {
        "kind": kind, "ray": ray, "status": status,
        "title": sanitize_error_text(title, max_len=120),
    }


async def article_ready(page: Page, state: Mapping[str, Any] | None = None) -> bool:  # type: ignore
    """页面是否已加载出真实文章（挑战通过条件：文章标题 / figure img / #progressBar）。"""
    if await query_first_attr(page, "figure img", "src"):
        return True
    if await query_first_attr(page, "#progressBar", "max"):
        return True
    title = str(
        (await query_first_text(page, "h1.uk-article-title"))
        or (await query_first_text(page, "h1"))
        or ""
    ).strip()
    if not title:
        return False
    title_lower = title.lower()
    return not any(
        m in title_lower for m in CF_CHALLENGE_TITLES + CF_BLOCK_TITLES
    )


async def wait_cloudflare_clear(
    page: Page, cf_wait: float = 8.0, *, poll_ms: int = 700  # type: ignore
) -> bool:
    """轮询等待 CF 挑战通过，最多 ``cf_wait`` 秒。

    挑战页轮询到超时；明确的硬拦截（Sorry, you have been blocked）两次探测
    后即返回 False，避免每次失败都空等。
    """
    try:
        wait_seconds = max(0.0, float(cf_wait))
    except (TypeError, ValueError):
        wait_seconds = 0.0
    deadline = time.monotonic() + wait_seconds
    polls = 0
    while True:
        state = await detect_cloudflare(page)
        polls += 1
        if await article_ready(page, state):
            return True
        if state.get("kind") == "blocked" and polls >= 2:
            return False
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(max(100, int(poll_ms)) / 1000)
