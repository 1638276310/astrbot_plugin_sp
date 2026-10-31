"""基于 aiohttp 的异步 HTTP 小工具（统一 UA / 脱敏 / Cloudflare 识别 / 单次重试）。

AstrBot 要求插件使用异步网络库（aiohttp / httpx），不要使用 requests。
原 ``fetch_bytes`` / ``fetch_text`` / ``fetch_json`` 的调用方式保持不变，只新增
可选关键字参数（session / proxy / ssl / retries）。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import html as _htmlmod
import json as jsonlib
import random
import re
import ssl
import time
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlsplit, urlunsplit

import aiohttp  # type: ignore[import]

# 单一 Chrome UA：浏览器兜底是 Chromium，不能再随机混用 Firefox/Safari UA。
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
ACCEPT_LANGUAGE = "zh-CN,zh;q=0.9,en;q=0.8"
ACCEPT_HTML = (
    "text/html,application/xhtml+xml,application/xml;q=0.9,"
    "image/avif,image/webp,*/*;q=0.8"
)
ACCEPT_JSON = "application/json, text/javascript, */*; q=0.01"
RETRY_STATUSES = frozenset({403, 429, 500, 502, 503, 504})
RETRY_DELAY_RANGE = (0.8, 1.5)
_SAFE_QUERY_KEYS = frozenset({"id"})
# kkmzt 的正常 200 也带 cf-ray/server: cloudflare，甚至注入
# <script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js">，
# 因此 200 绝不能按弱标记（challenge-platform / cf-chl）判定被拦截。
_CF_BLOCK_MARKERS = (
    "you have been blocked",
    "cf-error-details",
    "cf-error-overview",
)
_CF_TITLE_MARKERS = ("just a moment", "attention required")
_TITLE_RE = re.compile(r"(?is)<title[^>]*>(.*?)</title>")
_CF_RAY_RE = re.compile(r"Ray ID:?\s*([0-9a-fA-F]{16})", re.I)
_CF_RAY_HEX_RE = re.compile(r"[0-9a-fA-F]{16}")
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.S
)
_SECRET_RE = re.compile(
    r"(?i)\b(sign|token|key|auth|session|cookie|cf_clearance|expires|sig|secret)"
    r"\s*=\s*([^&\s;\"'<>]+)"
)
_SCRIPT_STYLE_RE = re.compile(r"(?is)<(script|style)\b.*?</\1>")
_TAG_RE = re.compile(r"(?s)<[^>]+>")
_SSL_ERROR = ssl.SSLError  # fetch_bytes 的 ssl 参数会遮蔽 ssl 模块


def _dbg(message: str) -> None:
    try:
        from astrbot.api import logger  # type: ignore

        logger.info(f"[涩批DEBUG] {message}")
    except Exception:
        pass


def _headers(
    ua: str | None = None,
    referer: str | None = None,
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    headers = {
        "User-Agent": (ua or BROWSER_UA).strip() or BROWSER_UA,
        "Accept": "*/*",
        "Accept-Language": ACCEPT_LANGUAGE,
    }
    if referer:
        headers["Referer"] = referer
    if extra:
        headers.update({str(k): str(v) for k, v in extra.items()})
    return headers


def browser_headers(
    *, ua: str | None = None, referer: str | None = None, accept: str | None = None,
    json_api: bool = False, site: str = "same-origin",
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """统一浏览器请求头。``site`` 对应 ``Sec-Fetch-Site``。"""
    user_agent = (ua or BROWSER_UA).strip() or BROWSER_UA
    match = re.search(r"(?:Chrome|Chromium)/(\d+)", user_agent)
    major = match.group(1) if match else "124"
    headers = {
        "User-Agent": user_agent,
        "Accept-Language": ACCEPT_LANGUAGE,
        "Accept": accept or (ACCEPT_JSON if json_api else ACCEPT_HTML),
        "Sec-Ch-Ua": f'"Chromium";v="{major}", "Google Chrome";v="{major}", "Not-A.Brand";v="99"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "empty" if json_api else "document",
        "Sec-Fetch-Mode": "cors" if json_api else "navigate",
        "Sec-Fetch-Site": site,
        "Upgrade-Insecure-Requests": "1",
    }
    if json_api:
        headers["X-Requested-With"] = "XMLHttpRequest"
    if referer:
        headers["Referer"] = referer
    if extra:
        headers.update({str(k): str(v) for k, v in extra.items()})
    return headers


def _header_get(headers: Mapping[str, Any] | None, name: str) -> str:
    if not headers:
        return ""
    lowered = name.lower()
    try:
        for key, value in headers.items():
            if str(key).lower() == lowered:
                return str(value)
    except Exception:
        return ""
    return ""


def sanitize_url(url: str | None, *, max_len: int = 160) -> str:
    """日志用 URL：只保留 scheme/host/path 与白名单 query（仅 ``id``），去掉 sign。"""
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return sanitize_error_text(raw, max_len=max_len)
    host = parts.hostname or ""
    try:
        port = parts.port
    except ValueError:
        port = None
    # 只有 IPv6 才加方括号；IPv4/域名加端口不能写成 [127.0.0.1:8080]
    netloc = f"[{host}]" if ":" in host and not host.startswith("[") else host
    if port:
        netloc = f"{netloc}:{port}"
    query = "&".join(
        f"{key}={value}" for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() in _SAFE_QUERY_KEYS
    )
    out = urlunsplit((parts.scheme, netloc, parts.path, query, ""))
    return out if len(out) <= max_len else out[: max_len - 1] + "…"


def sanitize_error_text(text: Any, *, max_len: int = 120) -> str:
    """异常/HTML 片段 → 单行、无敏感信息、定长文本。"""
    value = "" if text is None else str(text)
    if not value:
        return ""
    value = _PRIVATE_KEY_RE.sub("[私钥已隐藏]", value)
    value = _SCRIPT_STYLE_RE.sub(" ", value)
    value = _TAG_RE.sub(" ", value)
    value = _htmlmod.unescape(value)
    value = _SECRET_RE.sub(r"\1=***", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value if len(value) <= max_len else value[: max_len - 1].rstrip() + "…"


def cloudflare_ray(headers: Mapping[str, Any] | None = None, body: Any = None) -> str:
    """提取 Ray ID（仅用于日志/展示）：优先 cf-ray 响应头，再正文 ``Ray ID:``。"""
    header_ray = _header_get(headers, "cf-ray").strip()
    if header_ray:
        match = _CF_RAY_HEX_RE.search(header_ray)
        return match.group(0) if match else sanitize_error_text(header_ray, max_len=24)
    match = _CF_RAY_RE.search(str(body or "")[:50_000])
    return match.group(1) if match else ""


def looks_like_cloudflare(
    status: int | None = None,
    headers: Mapping[str, Any] | None = None,
    body: Any = None,
) -> bool:
    """是否为 Cloudflare 拦截页/挑战页。

    **kkmzt 正常 200 也会带 cf-ray / server: cloudflare，甚至注入
    ``/cdn-cgi/challenge-platform/...`` 脚本，因此这些弱标记绝不能用于 200 判定。**

    * 200：只有正文 blocked / cf-error-details，或 ``<title>`` 为
      ``Just a moment`` / ``Attention Required`` 才算；
    * 403/503：强特征，或“CF 响应头 + HTML”（CF 拦截页必带 cf-ray / server: cloudflare）；
    * 其它状态：不算。
    """
    text = str(body or "")[:50_000]
    lower = text.lower()
    title_match = _TITLE_RE.search(text)
    title = _htmlmod.unescape(title_match.group(1)).strip().lower() if title_match else ""
    blocked = any(marker in lower for marker in _CF_BLOCK_MARKERS) or any(
        marker in title for marker in _CF_TITLE_MARKERS
    )
    if status == 200:
        return blocked
    if status in (403, 503):
        content_type = _header_get(headers, "content-type").lower()
        is_html = "text/html" in content_type or "<html" in lower or "<!doctype" in lower
        cf_header = bool(cloudflare_ray(headers, text)) or "cloudflare" in _header_get(
            headers, "server"
        ).lower()
        return blocked or (cf_header and is_html)
    return False


def cf_mitigation(headers: Mapping[str, Any] | None = None) -> str:
    """Cloudflare 处置类型：``challenge``（人机挑战）/ ``block``（硬拦截）/ 空串。

    ``cf-mitigated`` 是 Cloudflare 在 403 上附带的可选响应头。两种取值都表现为
    HTTP 403 + 拦截页，但排障方向相反：

    * ``challenge``：CF 只是要求过挑战，出口本身没被拉黑；
    * ``block``：规则直接拒绝，换请求头/重试都没用，只能换出口。

    取不到该头时返回空串（旧版 CF 配置或不触发处置）。
    """
    value = _header_get(headers, "cf-mitigated").strip().lower()
    return value


def describe_http_error(
    value: Any = None, *, status: int | None = None,
    headers: Mapping[str, Any] | None = None, body: Any = None, max_len: int = 90,
) -> str:
    """HTTP 响应/异常 → 一行结构化描述：``HTTP 403`` / ``Cloudflare 拦截 Ray ID ...``。"""
    exc = value if isinstance(value, BaseException) else None
    if isinstance(value, int) and status is None:
        status = value
    if status is None and exc is not None:
        raw_status = getattr(exc, "status", None)
        status = raw_status if isinstance(raw_status, int) else None
    if headers is None and exc is not None:
        raw_headers = getattr(exc, "headers", None)
        headers = raw_headers if isinstance(raw_headers, Mapping) else None
    if body is None and exc is not None:
        body = getattr(exc, "body_preview", None)
    if status is not None:
        if looks_like_cloudflare(status, headers, body):
            ray = cloudflare_ray(headers, body) or str(getattr(exc, "cf_ray", "") or "")
            # challenge 与 block 都返回 403，但前者只是挑战、后者是硬拦，必须区分
            label = "Cloudflare 挑战" if cf_mitigation(headers) == "challenge" else "Cloudflare 拦截"
            text = f"{label} Ray ID {ray}" if ray else f"{label} HTTP {status}"
        else:
            text = f"HTTP {status}"
        return sanitize_error_text(text, max_len=max_len)
    if exc is None:
        return sanitize_error_text(value if value is not None else "请求失败", max_len=max_len)
    name = type(exc).__name__
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "网络超时"
    if isinstance(exc, ssl.SSLError) or "Certificate" in name or "SSL" in name:
        return sanitize_error_text(f"TLS 证书错误（{name}）", max_len=max_len)
    message = sanitize_error_text(str(exc), max_len=max(10, max_len - len(name) - 8))
    label = "网络错误" if ("Client" in name or isinstance(exc, OSError)) else "请求失败"
    text = f"{label}（{name}）"
    if message and message not in (name, "None"):
        text += f"：{message}"
    return sanitize_error_text(text, max_len=max_len)


class HttpError(Exception):
    """HTTP 请求失败（保留状态码/响应头/cf-ray/耗时等诊断信息）。"""

    def __init__(
        self, message: str, status: int | None = None, *, url: str = "",
        cf_ray: str = "", headers: Mapping[str, Any] | None = None,
        elapsed_ms: int = 0, body_preview: str = "",
    ) -> None:
        super().__init__(message)
        self.status = status
        self.url = url
        self.cf_ray = cf_ray or ""
        self.headers = {str(k): str(v) for k, v in dict(headers).items()} if headers else {}
        self.elapsed_ms = int(elapsed_ms)
        self.body_preview = sanitize_error_text(body_preview, max_len=200) if body_preview else ""


@dataclass
class FetchResponse:
    """一次 2xx 请求的响应快照。"""

    url: str
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    text: str = ""
    bytes_length: int = 0
    elapsed_ms: int = 0
    cf_ray: str = ""


def _decode_text(data: bytes, content_type: str, encoding: str | None = None) -> str:
    match = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    charset = match.group(1) if match else ""
    for candidate in filter(None, [encoding, charset, "utf-8", "gbk", "latin-1"]):
        try:
            return data.decode(candidate)  # type: ignore[arg-type]
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="ignore")


async def _retry_sleep() -> None:
    await asyncio.sleep(random.uniform(*RETRY_DELAY_RANGE))


def _new_session(timeout: int, ssl_context: ssl.SSLContext | None) -> Any:
    connector = aiohttp.TCPConnector(ssl=ssl_context) if ssl_context is not None else None
    # 默认直连：显式关闭 trust_env，忽略 HTTP(S)_PROXY / ALL_PROXY 等环境代理；
    # 调用方仍可通过 fetch_* 的 proxy 参数显式指定代理（SP_MZT_PROXY 即走这里）。
    return aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=timeout),
        connector=connector,
        trust_env=False,
    )


@asynccontextmanager
async def client_session(*, timeout: int = 30, ssl: ssl.SSLContext | None = None):
    """创建可复用的 aiohttp 会话（共享 Cookie；可挂 mTLS 证书）。"""
    session = _new_session(timeout, ssl)
    try:
        yield session
    finally:
        try:
            await session.close()
        except Exception:  # pragma: no cover
            pass


async def _request_once(
    method: str, url: str, *, timeout: int, headers: dict[str, str],
    proxy: str | None, session: Any | None, ssl_context: ssl.SSLContext | None,
    encoding: str | None, json_body: Mapping[str, Any] | None,
    cookies: Mapping[str, str] | None, allow_redirects: bool,
) -> FetchResponse:
    started = time.monotonic()
    owns_session = session is None
    if owns_session:
        session = _new_session(timeout, ssl_context)
    assert session is not None
    kwargs: dict[str, Any] = {"headers": headers, "allow_redirects": allow_redirects}
    if proxy:
        kwargs["proxy"] = proxy
    if json_body is not None:
        kwargs["json"] = dict(json_body)
    if cookies:
        kwargs["cookies"] = dict(cookies)
    if owns_session:
        kwargs["timeout"] = aiohttp.ClientTimeout(total=timeout)
    try:
        async with session.request(method.upper(), url, **kwargs) as resp:
            data = await resp.read()
            resp_headers = {str(k): str(v) for k, v in resp.headers.items()}
            return FetchResponse(
                url=url, status=int(resp.status), headers=resp_headers,
                text=_decode_text(data, resp_headers.get("Content-Type", ""), encoding),
                bytes_length=len(data),
                elapsed_ms=int((time.monotonic() - started) * 1000),
                cf_ray=cloudflare_ray(resp_headers, data[:20_000]),
            )
    except (asyncio.TimeoutError, TimeoutError) as exc:
        raise HttpError(
            "网络超时", None, url=sanitize_url(url),
            elapsed_ms=int((time.monotonic() - started) * 1000),
        ) from exc
    except _SSL_ERROR as exc:
        raise HttpError(f"TLS 证书错误（{type(exc).__name__}）", None, url=sanitize_url(url)) from exc
    except aiohttp.ClientError as exc:
        raise HttpError(describe_http_error(exc), None, url=sanitize_url(url)) from exc
    finally:
        if owns_session:
            try:
                await session.close()
            except Exception:  # pragma: no cover
                pass


def _status_error(resp: FetchResponse) -> HttpError:
    return HttpError(
        describe_http_error(resp.status, headers=resp.headers, body=resp.text),
        resp.status, url=sanitize_url(resp.url), cf_ray=resp.cf_ray,
        headers=resp.headers, elapsed_ms=resp.elapsed_ms, body_preview=resp.text,
    )


async def _with_retries(url: str, *, method: str, timeout: int, headers: dict[str, str],
                        proxy: str | None, session: Any | None,
                        ssl_context: ssl.SSLContext | None, encoding: str | None,
                        json_body: Mapping[str, Any] | None,
                        cookies: Mapping[str, str] | None,
                        allow_redirects: bool, retries: int) -> FetchResponse:
    attempts = max(0, int(retries)) + 1
    for attempt in range(attempts):
        try:
            resp = await _request_once(
                method, url, timeout=timeout, headers=headers, proxy=proxy,
                session=session, ssl_context=ssl_context, encoding=encoding,
                json_body=json_body, cookies=cookies, allow_redirects=allow_redirects,
            )
        except HttpError:
            if attempt >= attempts - 1:
                raise
            await _retry_sleep()
            continue
        if resp.status in RETRY_STATUSES and attempt < attempts - 1:
            _dbg(f"fetch：{sanitize_url(url)} HTTP {resp.status}，准备重试")
            await _retry_sleep()
            continue
        if 200 <= resp.status < 300:
            return resp
        raise _status_error(resp)
    raise HttpError("请求失败", None, url=sanitize_url(url))  # pragma: no cover


async def fetch_response(
    url: str, *, method: str = "GET", timeout: int = 30, referer: str | None = None,
    ua: str | None = None, headers: Mapping[str, str] | None = None,
    encoding: str | None = None, proxy: str | None = None, session: Any | None = None,
    ssl: ssl.SSLContext | None = None, json_body: Mapping[str, Any] | None = None,
    cookies: Mapping[str, str] | None = None, allow_redirects: bool = True,
    retries: int = 1,
) -> FetchResponse:
    """请求并返回 :class:`FetchResponse`；非 2xx 抛 :class:`HttpError`。

    403/429/5xx 与网络错误按 800~1500ms 抖动重试（默认 1 次）。
    ``session`` 用于复用连接/共享 Cookie；``ssl`` 用于 mTLS 客户端证书。
    """
    request_headers = _headers(ua, referer, headers)
    if json_body is not None:
        request_headers.setdefault("Content-Type", "application/json")
    return await _with_retries(
        url, method=method, timeout=timeout, headers=request_headers, proxy=proxy,
        session=session, ssl_context=ssl, encoding=encoding, json_body=json_body,
        cookies=cookies, allow_redirects=allow_redirects, retries=retries,
    )


async def fetch_bytes(
    url: str, *, timeout: int = 30, referer: str | None = None, ua: str | None = None,
    headers: Mapping[str, str] | None = None, max_size: int = 64 * 1024 * 1024,
    proxy: str | None = None, ssl: ssl.SSLContext | None = None,
    session: Any | None = None, retries: int = 1,
) -> bytes:
    """流式下载二进制内容（图片），避免一次性读入超大响应；``proxy`` 可走代理。"""
    request_headers = _headers(ua, referer, headers)
    attempts = max(0, int(retries)) + 1
    for attempt in range(attempts):
        owns = session is None
        active = _new_session(timeout, ssl) if owns else session
        assert active is not None
        try:
            async with active.get(url, headers=request_headers, proxy=proxy, allow_redirects=True) as resp:
                if resp.status in RETRY_STATUSES and attempt < attempts - 1:
                    try:
                        await resp.read()
                    except Exception:
                        pass
                    await _retry_sleep()
                    continue
                if resp.status != 200:
                    try:
                        preview = _decode_text((await resp.read())[:4096], "")
                    except Exception:
                        preview = ""
                    rh = {str(k): str(v) for k, v in resp.headers.items()}
                    raise HttpError(
                        describe_http_error(resp.status, headers=rh, body=preview),
                        resp.status, url=sanitize_url(url),
                        cf_ray=cloudflare_ray(rh, preview), headers=rh,
                        body_preview=preview,
                    )
                chunks: list[bytes] = []
                total = 0
                async for chunk in resp.content.iter_chunked(64 * 1024):
                    total += len(chunk)
                    if total > max_size:
                        raise HttpError(f"内容超过大小上限 {max_size} 字节")
                    chunks.append(chunk)
                return b"".join(chunks)
        except HttpError:
            raise
        except (asyncio.TimeoutError, TimeoutError) as exc:
            if attempt >= attempts - 1:
                raise HttpError("网络超时", None, url=sanitize_url(url)) from exc
            await _retry_sleep()
        except _SSL_ERROR as exc:
            raise HttpError(f"TLS 证书错误（{type(exc).__name__}）", None, url=sanitize_url(url)) from exc
        except aiohttp.ClientError as exc:
            if attempt >= attempts - 1:
                raise HttpError(describe_http_error(exc), None, url=sanitize_url(url)) from exc
            await _retry_sleep()
        finally:
            if owns:
                try:
                    await active.close()
                except Exception:  # pragma: no cover
                    pass
    raise HttpError("请求失败", None, url=sanitize_url(url))  # pragma: no cover


async def fetch_text(
    url: str, *, timeout: int = 30, referer: str | None = None, ua: str | None = None,
    encoding: str | None = None, headers: Mapping[str, str] | None = None,
    proxy: str | None = None, session: Any | None = None,
    ssl: ssl.SSLContext | None = None, retries: int = 1,
) -> str:
    """下载文本内容。"""
    resp = await fetch_response(
        url, timeout=timeout, referer=referer, ua=ua, encoding=encoding,
        headers=headers, proxy=proxy, session=session, ssl=ssl, retries=retries,
    )
    return resp.text


async def fetch_json(
    url: str, *, timeout: int = 30, referer: str | None = None, ua: str | None = None,
    headers: Mapping[str, str] | None = None, method: str = "GET",
    json_body: Mapping[str, Any] | None = None, cookies: Mapping[str, str] | None = None,
    ssl: ssl.SSLContext | None = None, proxy: str | None = None,
    session: Any | None = None, retries: int = 1,
) -> Any:
    """请求并解析 JSON；``ssl`` 可传入 mTLS 客户端证书上下文。"""
    resp = await fetch_response(
        url, method=method, timeout=timeout, referer=referer, ua=ua, headers=headers,
        proxy=proxy, session=session, ssl=ssl, json_body=json_body, cookies=cookies,
        retries=retries,
    )
    try:
        return jsonlib.loads(resp.text)
    except jsonlib.JSONDecodeError as exc:
        raise HttpError(
            "返回内容不是合法 JSON", resp.status, url=sanitize_url(url),
            cf_ray=resp.cf_ray, headers=resp.headers,
            elapsed_ms=resp.elapsed_ms, body_preview=resp.text,
        ) from exc
