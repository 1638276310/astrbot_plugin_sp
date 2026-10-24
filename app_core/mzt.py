"""妹子图（kkmzt / 美之图）解析。

支持 ``/photo/{id}``（写真馆）与 ``/beauty/{id}``（潮拍馆）。降级顺序：

1. APP 接口（mTLS 客户端证书，全量）；
2. 网页接口（**同一个 HTTP 会话**先 GET 文章页、再 GET ``/app/post/p?id=``，共享 Cookie）；
3. 浏览器兜底（CF 挑战等待 → 页面内 fetch 解密 → ``context.request`` 兜底）；
4. 文章页首图兜底（``source="page"``、``truncated=True``，只发一张）。

各阶段失败保留 HTTP 状态码 / Cloudflare Ray ID，全部失败抛 :class:`MztFetchError`
（``str()`` 形如 ``APP接口:HTTP 403；网页接口:HTTP 403；浏览器:Cloudflare 拦截 Ray ID ...``）。
"""

from __future__ import annotations

import asyncio
import html as _html
import json as jsonlib
import re
import ssl
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal, Sequence

from .browser import (
    BrowserError,
    browser_context,
    detect_cloudflare,
    goto,
    query_first_attr,
    query_first_text,
    random_delay,
    wait_cloudflare_clear,
)
from .http import (
    ACCEPT_HTML,
    ACCEPT_JSON,
    BROWSER_UA,
    HttpError,
    browser_headers,
    client_session,
    cloudflare_ray,
    describe_http_error,
    fetch_response,
    fetch_text,
    looks_like_cloudflare,
    sanitize_error_text,
    sanitize_url,
)
from .mzt_crypto import (
    KkmztDecryptError,
    decode_api_payload,
    hd_beauty_image_url,
    hd_image_url,
)
from .settings import PluginSettings

ArticleKind = Literal["photo", "beauty"]
KIND_LABEL = {"photo": "写真馆", "beauty": "潮拍馆"}
DEFAULT_KIND: ArticleKind = "photo"
APP_API_DEFAULT = "https://api.iimzt.com/app/"
APP_UA = "MztApp For Android Version/3.8.7"

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
ASSETS_CLIENT_CERT_PATH = _PLUGIN_ROOT / "assets" / "mzt_client.pem"
APP_CLIENT_CERT_PATH = Path(__file__).resolve().parent / "mzt_client.pem"  # 旧路径
_CERT_CACHE: dict[str, tuple[tuple[int, int] | None, ssl.SSLContext | None]] = {}
_CERT_STATE = {"reason": "cert-missing"}
MAX_IMAGES = 20
RATE_LIMIT_SECONDS = 3.0
_LAST_FETCH_AT: dict[str, float] = {}

URL_ID_PATTERN = re.compile(r"/(photo|beauty)/(\d+)", re.I)
PHOTO_CMD_PATTERN = re.compile(r"写真馆\s*(\d{1,12})")
BEAUTY_CMD_PATTERN = re.compile(r"潮拍馆\s*(\d{1,12})")
BARE_ID_PATTERN = re.compile(r"(?<![\d/])(\d{2,12})(?![\d/])")
ARTICLE_HEADERS = {"Accept": ACCEPT_HTML}
_STAGE_LABEL = {"app": "APP接口", "article": "文章页", "web": "网页接口"}
_STAGE_MAX = 90


def _dbg(message: str) -> None:
    try:
        from astrbot.api import logger  # type: ignore

        logger.info(f"[涩批DEBUG] {message}")
    except Exception:
        pass


def _log(message: str) -> None:
    """结构化日志（``[mzt] stage=...``），info 级、失败静默。"""
    try:
        from astrbot.api import logger  # type: ignore

        logger.info(message)
    except Exception:
        pass


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _log_stage(
    stage: str, url: str, status: int | None = None, *, headers: Any = None,
    elapsed_ms: int = 0, challenge: str = "", error: str = "",
) -> None:
    ray = cloudflare_ray(headers) if headers else ""
    parts = [
        f"[mzt] stage={stage}", f"url={sanitize_url(url)}",
        f"status={status if status is not None else 'ERR'}", f"cf-ray={ray or '-'}",
    ]
    if challenge:
        parts.append(f"challenge={challenge}")
    if error:
        parts.append(f"error={sanitize_error_text(error, max_len=60)}")
    parts.append(f"elapsed={int(elapsed_ms)}ms")
    _log(" ".join(parts))


def _stage_error(stage: str, detail: str) -> str:
    return sanitize_error_text(f"{stage}:{detail}", max_len=_STAGE_MAX)


def _short_error(exc: BaseException, *, max_len: int = 90) -> str:
    return sanitize_error_text(str(exc), max_len=max_len) or type(exc).__name__


def _user_agent(settings: PluginSettings) -> str:
    return str(getattr(settings, "mzt_ua", "") or "").strip() or BROWSER_UA


def _proxy(settings: PluginSettings) -> str | None:
    return str(getattr(settings, "mzt_proxy", "") or "").strip() or None


def _cf_wait(settings: PluginSettings) -> float:
    try:
        value = float(getattr(settings, "mzt_cf_wait", 8) or 8)
    except (TypeError, ValueError):
        value = 8.0
    return max(0.0, min(60.0, value))


class AlbumInfo:
    """一个写真/潮拍页的解析结果。"""

    def __init__(
        self, article_id: str, title: str = "未知标题", publish_time: str = "未知时间",
        image_urls: list[str] | None = None, *, kind: str = DEFAULT_KIND,
        total: int = 0, truncated: bool = False, referrer_policy: str = "origin",
        image_referer: str | None = None, page_url: str = "", source: str = "web",
    ) -> None:
        self.article_id = article_id
        self.title = title
        self.publish_time = publish_time
        self.image_urls = image_urls or []
        self.kind = kind
        self.total = total
        self.truncated = truncated
        self.referrer_policy = referrer_policy
        self.image_referer = image_referer
        self.page_url = page_url
        self.source = source

    @property
    def kind_label(self) -> str:
        return KIND_LABEL.get(self.kind, self.kind)

    @property
    def source_label(self) -> str:
        return {
            "app": "美之图 APP 接口（全量）", "web": "网页接口",
            "browser": "网页（浏览器兜底）", "page": "网页首图（兜底）",
        }.get(self.source, self.source)

    @property
    def header(self) -> str:
        lines = [
            f"📌 文章标题: {self.title}", f"⏰ 发布时间: {self.publish_time}",
            f"🆔 文章ID: {self.article_id}", f"🗂️ 图集类型: {self.kind_label}",
        ]
        if self.total > 0:
            lines.append(f"🖼️ 图片数量: {len(self.image_urls)}/{self.total}")
        else:
            lines.append(f"🖼️ 图片数量: {len(self.image_urls)}")
        lines.append(f"🔗 数据源: {self.source_label}")
        if self.truncated:
            if self.source == "page":
                lines.append(
                    "⚠️ 网页首图兜底：完整图集暂时获取失败，仅返回网页首图，"
                    "可稍后重试或配置可用代理"
                )
            else:
                lines.append(
                    "⚠️ 网页接口仅提供前 20 张预览（APP 接口暂时不可用），"
                    "完整图集需“美之图”APP"
                )
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 文章 ID / HTML 解析
# --------------------------------------------------------------------------- #
def parse_article_ref(text: str) -> tuple[str, ArticleKind] | None:
    """从消息中解析 ``(文章ID, 类型)``：支持链接、``/写真馆 123``、裸 ID。"""
    raw = (text or "").strip()
    if not raw:
        return None
    match = URL_ID_PATTERN.search(raw)
    if match:
        kind = "beauty" if match.group(1).lower() == "beauty" else "photo"
        return match.group(2), kind  # type: ignore[return-value]
    match = PHOTO_CMD_PATTERN.search(raw)
    if match:
        return match.group(1), "photo"
    match = BEAUTY_CMD_PATTERN.search(raw)
    if match:
        return match.group(1), "beauty"
    match = BARE_ID_PATTERN.search(raw)
    return (match.group(1), DEFAULT_KIND) if match else None


def parse_article_id(text: str) -> str | None:
    """兼容旧接口：只返回文章 ID。"""
    ref = parse_article_ref(text)
    return ref[0] if ref else None


def album_url(settings: PluginSettings, article_id: str | int, kind: str = DEFAULT_KIND) -> str:
    kind_path = "beauty" if str(kind).lower() == "beauty" else "photo"
    return f"{settings.mzt_site}/{kind_path}/{article_id}"


ProgressCallback = Callable[[str, int, int], Awaitable[None]]


def _first_match(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text)
    return match.group(1).strip() if match else ""


def _parse_article_html(page_html: str, fallback_kind: str) -> dict:
    """提取标题/时间/首图/referrerpolicy/总数/真实栏目。"""
    title = _html.unescape(
        re.sub(r"<[^>]+>", "", _first_match(re.compile(r"<h1[^>]*>(.*?)</h1>", re.S | re.I), page_html))
    ).strip()
    publish_time = _html.unescape(
        re.sub(r"<[^>]+>", "", _first_match(re.compile(r"<time[^>]*>(.*?)</time>", re.S | re.I), page_html))
    ).strip()
    first_image = ""
    referrer_policy = "origin"
    figure = re.search(r"<figure[^>]*>(.*?)</figure>", page_html, re.S | re.I)
    scope = figure.group(1) if figure else page_html
    img = re.search(r"<img[^>]*>", scope, re.I)
    if img:
        src = re.search(r'\bsrc="([^"]+)"', img.group(0), re.I)
        ref = re.search(r'\breferrerpolicy="([^"]+)"', img.group(0), re.I)
        first_image = _html.unescape(src.group(1)).strip() if src else ""
        referrer_policy = (ref.group(1).strip().lower() or "origin") if ref else "origin"
    total = 0
    progress = re.search(r'<progress[^>]*\bmax="(\d+)"[^>]*>', page_html, re.I)
    if progress:
        try:
            total = int(progress.group(1))
        except ValueError:
            total = 0
    canonical = _first_match(
        re.compile(r'<link[^>]+rel="canonical"[^>]+href="([^"]+)"', re.I), page_html
    )
    kind = fallback_kind
    if "/beauty/" in canonical:
        kind = "beauty"
    elif "/photo/" in canonical:
        kind = "photo"
    return {
        "title": title or "未知标题", "publish_time": publish_time or "未知时间",
        "first_image": first_image, "referrer_policy": referrer_policy,
        "total": total, "kind": kind,
    }


def _dir_prefix(image_url: str) -> str:
    url = (image_url or "").strip()
    return url.rsplit("/", 1)[0] + "/" if "/" in url else ""


def _apply_limit(urls: list[str], settings: PluginSettings) -> list[str]:
    limit = getattr(settings, "mzt_max_images", 0)
    return urls[:limit] if isinstance(limit, int) and limit > 0 else urls


# --------------------------------------------------------------------------- #
# APP mTLS 客户端证书：配置 → assets → app_core 旧路径
# --------------------------------------------------------------------------- #
def _cert_candidates(settings: PluginSettings) -> list[Path]:
    candidates: list[Path] = []
    configured = str(getattr(settings, "mzt_app_cert", "") or "").strip()
    if configured:
        path = Path(configured).expanduser()
        candidates.append(path if path.is_absolute() else _PLUGIN_ROOT / path)
    candidates.extend([ASSETS_CLIENT_CERT_PATH, APP_CLIENT_CERT_PATH])
    unique: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        if str(path) not in seen:
            seen.add(str(path))
            unique.append(path)
    return unique


def _file_signature(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
        return (int(stat.st_mtime_ns), int(stat.st_size))
    except OSError:
        return None


def _validate_client_pem(path: Path) -> tuple[bool, str]:
    """加载前校验 PEM 同时含证书与私钥（只判断标记，不打印内容）。"""
    try:
        if not path.is_file():
            return False, "cert-missing"
        size = path.stat().st_size
        if size <= 0:
            return False, "cert-invalid:empty"
        if size > 256 * 1024:
            return False, "cert-invalid:too-large"
        data = path.read_bytes()
    except OSError as exc:
        return False, f"cert-unreadable:{type(exc).__name__}"
    if b"BEGIN CERTIFICATE" not in data:
        return False, "cert-invalid:no-certificate"
    if b"PRIVATE KEY" not in data:
        return False, "cert-invalid:no-private-key"
    return True, "ok"


def _cert_problem_label(reason: str) -> str:
    if reason.startswith("cert-missing"):
        return "未找到证书文件"
    if "no-certificate" in reason:
        return "PEM 缺少 CERTIFICATE"
    if "no-private-key" in reason:
        return "PEM 缺少 PRIVATE KEY"
    if reason.startswith("cert-load-failed"):
        return "证书加载失败"
    if reason.startswith("cert-unreadable"):
        return "证书文件不可读"
    return "证书不可用"


def _app_ssl_context(settings: PluginSettings) -> ssl.SSLContext | None:
    """按候选顺序加载 mTLS 证书；失败只记 ``stage=cert``，不打印证书内容。"""
    last_reason = "cert-missing"
    for path in _cert_candidates(settings):
        key = str(path)
        signature = _file_signature(path)
        cached = _CERT_CACHE.get(key)
        if cached is not None and cached[0] == signature:
            if cached[1] is not None:
                _CERT_STATE["reason"] = "ok"
                return cached[1]
            continue
        ok, reason = _validate_client_pem(path)
        if not ok:
            _CERT_CACHE[key] = (signature, None)
            last_reason = reason
            _log(f"[mzt] stage=cert path={path.name} state={reason}")
            continue
        try:
            context = ssl.create_default_context()
            context.load_cert_chain(str(path))
        except (OSError, ssl.SSLError, ValueError) as exc:
            _CERT_CACHE[key] = (signature, None)
            last_reason = f"cert-load-failed:{type(exc).__name__}"
            _log(f"[mzt] stage=cert path={path.name} state=cert-load-failed reason={type(exc).__name__}")
            continue
        _CERT_CACHE[key] = (signature, context)
        _CERT_STATE["reason"] = "ok"
        _log(f"[mzt] stage=cert path={path.name} state=ok")
        return context
    _CERT_STATE["reason"] = last_reason
    _log(f"[mzt] stage=cert state=cert-unavailable reason={last_reason}")
    return None


def _app_api_url(settings: PluginSettings, article_id: str) -> str:
    base = (getattr(settings, "mzt_app_api", APP_API_DEFAULT) or "").strip()
    return base.rstrip("/") + f"/post/p?id={article_id}" if base else ""


def _app_headers(settings: PluginSettings) -> dict[str, str]:
    headers = browser_headers(
        ua=APP_UA, referer=settings.mzt_site + "/", accept=ACCEPT_JSON,
        json_api=True, site="cross-site",
    )
    for key in ("Sec-Ch-Ua", "Sec-Ch-Ua-Mobile", "Sec-Ch-Ua-Platform",
                "Upgrade-Insecure-Requests"):
        headers.pop(key, None)
    return headers


# --------------------------------------------------------------------------- #
# 结果组装
# --------------------------------------------------------------------------- #
def _album_from_decoded(
    settings: PluginSettings, article_id: str, kind: str, decoded: dict,
    meta: dict | None, page_url: str, *, source: str,
) -> AlbumInfo | None:
    """解密结果 + 文章页元信息 → AlbumInfo。"""
    if not isinstance(decoded, dict):
        return None
    images = [str(url) for url in (decoded.get("images") or []) if url]
    if not images:
        return None
    meta = meta or {}
    if decoded.get("scheme") == "app":
        urls = images
        total = int(decoded.get("count") or 0) or len(urls)
        referrer_policy = "no-referrer"
        image_referer = "https://weibo.com/" if decoded.get("thumbs") else None
        title = str(decoded.get("title") or meta.get("title") or "未知标题")
        publish_time = str(decoded.get("date") or meta.get("publish_time") or "未知时间")
        actual_kind = str(meta.get("kind") or kind)
        if getattr(settings, "mzt_hd", False):
            urls = (
                [hd_beauty_image_url(url) for url in urls]
                if decoded.get("thumbs") else [hd_image_url(url) for url in urls]
            )
    else:
        prefix = _dir_prefix(str(meta.get("first_image") or ""))
        if not prefix:
            return None
        urls = [prefix + name for name in images]
        total = int(meta.get("total") or 0)
        referrer_policy = str(meta.get("referrer_policy") or "origin")
        image_referer = None if referrer_policy == "no-referrer" else settings.mzt_site
        title = str(meta.get("title") or "未知标题")
        publish_time = str(meta.get("publish_time") or "未知时间")
        actual_kind = str(meta.get("kind") or kind)
    limited = _apply_limit(urls, settings)
    if not limited:
        return None
    return AlbumInfo(
        str(article_id), title, publish_time, limited, kind=actual_kind, total=total,
        truncated=bool(total and len(limited) < total), referrer_policy=referrer_policy,
        image_referer=image_referer, page_url=page_url, source=source,
    )


async def _finish_album(album: AlbumInfo, progress: "ProgressCallback | None") -> AlbumInfo:
    total = album.total or len(album.image_urls)
    _log(
        f"[mzt] result source={album.source} images={len(album.image_urls)} "
        f"total={total} truncated={str(bool(album.truncated)).lower()}"
    )
    if progress is not None:
        try:
            await progress("解析", len(album.image_urls), total)
        except Exception:
            _dbg("mzt._finish_album：progress 回调执行异常（已忽略）")
    return album


def _first_image_album(
    settings: PluginSettings, article_id: str, kind: str, meta: dict | None
) -> AlbumInfo | None:
    """文章页首图兜底：所有接口失败但直连拿到了 HTML。"""
    first_image = str((meta or {}).get("first_image") or "").strip()
    if not first_image:
        return None
    urls = _apply_limit([first_image], settings)
    if not urls:
        return None
    meta = meta or {}
    total = int(meta.get("total") or 0) or 1
    referrer_policy = str(meta.get("referrer_policy") or "origin")
    return AlbumInfo(
        str(article_id), str(meta.get("title") or "未知标题"),
        str(meta.get("publish_time") or "未知时间"), urls,
        kind=str(meta.get("kind") or kind), total=total, truncated=True,
        referrer_policy=referrer_policy,
        image_referer=None if referrer_policy == "no-referrer" else settings.mzt_site,
        page_url=album_url(settings, article_id, kind), source="page",
    )


# --------------------------------------------------------------------------- #
# 直连：APP / 文章页 / 网页接口
# --------------------------------------------------------------------------- #
async def _request_stage(
    stage: str, url: str, *, headers: dict[str, str], timeout: int, proxy: str | None,
    stage_errors: list[str], session: Any | None = None,
    ssl_context: ssl.SSLContext | None = None,
) -> Any:
    """发一个阶段请求：成功返回 FetchResponse，失败记录状态码/cf-ray 并返回 None。"""
    started = time.monotonic()
    try:
        resp = await fetch_response(
            url, timeout=timeout, headers=headers, proxy=proxy,
            session=session, ssl=ssl_context,
        )
    except HttpError as exc:
        _log_stage(
            stage, url, exc.status, headers=exc.headers,
            elapsed_ms=exc.elapsed_ms or _elapsed_ms(started),
            error=type(exc).__name__,
        )
        stage_errors.append(_stage_error(_STAGE_LABEL[stage], describe_http_error(exc)))
        return None
    if looks_like_cloudflare(resp.status, resp.headers, resp.text):
        _log_stage(
            stage, url, resp.status, headers=resp.headers,
            elapsed_ms=resp.elapsed_ms, challenge="blocked",
        )
        stage_errors.append(
            _stage_error(
                _STAGE_LABEL[stage],
                describe_http_error(resp.status, headers=resp.headers, body=resp.text),
            )
        )
        return None
    _log_stage(stage, url, resp.status, headers=resp.headers, elapsed_ms=resp.elapsed_ms)
    return resp


def _json_payload(resp: Any, stage: str, stage_errors: list[str]) -> dict | None:
    try:
        payload = jsonlib.loads(resp.text)
    except jsonlib.JSONDecodeError:
        stage_errors.append(_stage_error(_STAGE_LABEL[stage], "返回内容不是合法 JSON"))
        return None
    if not isinstance(payload, dict) or not payload.get("data"):
        stage_errors.append(_stage_error(_STAGE_LABEL[stage], "接口返回格式异常"))
        return None
    return payload


async def _fetch_app_album(
    settings: PluginSettings, article_id: str, kind: str, *, timeout: int,
    proxy: str | None, stage_errors: list[str],
) -> AlbumInfo | None:
    """APP 接口（mTLS，全量）：证书不可用或 403 时记录原因并返回 None。"""
    api_url = _app_api_url(settings, article_id)
    if not api_url:
        _dbg("mzt._fetch_app_album：未配置 mzt_app_api，跳过 APP 接口")
        return None
    ssl_context = _app_ssl_context(settings)
    if ssl_context is None:
        reason = str(_CERT_STATE.get("reason") or "cert-missing")
        _log_stage("app", api_url, None, error=f"cert-unavailable:{reason}")
        stage_errors.append(
            _stage_error("APP接口", f"客户端证书不可用（{_cert_problem_label(reason)}）")
        )
        return None
    resp = await _request_stage(
        "app", api_url, headers=_app_headers(settings), timeout=timeout,
        proxy=proxy, stage_errors=stage_errors, ssl_context=ssl_context,
    )
    if resp is None:
        return None
    payload = _json_payload(resp, "app", stage_errors)
    if payload is None:
        return None
    try:
        decoded = decode_api_payload(str(payload["data"]), article_id)
    except KkmztDecryptError:
        stage_errors.append(_stage_error("APP接口", "数据解密失败"))
        return None
    if decoded.get("scheme") != "app":
        stage_errors.append(_stage_error("APP接口", "返回了网页端数据格式"))
        return None
    album = _album_from_decoded(
        settings, article_id, kind, decoded, None, album_url(settings, article_id, kind),
        source="app",
    )
    if album is None:
        stage_errors.append(_stage_error("APP接口", "没有解析到图片"))
        return None
    _dbg(f"mzt._fetch_app_album：成功 {len(album.image_urls)} 张（count={decoded.get('count')}）")
    return album


async def _fetch_article_http(
    session: Any, settings: PluginSettings, article_id: str, kind: str, *,
    timeout: int, proxy: str | None, stage_errors: list[str],
) -> tuple[dict | None, str]:
    page_url = album_url(settings, article_id, kind)
    resp = await _request_stage(
        "article", page_url,
        headers=browser_headers(
            ua=_user_agent(settings), referer=settings.mzt_site + "/",
            accept=ACCEPT_HTML, site="same-origin",
        ),
        timeout=timeout, proxy=proxy, stage_errors=stage_errors, session=session,
    )
    if resp is None:
        return None, page_url
    meta = _parse_article_html(resp.text, kind)
    _dbg(
        f"mzt._fetch_article_http：标题={meta['title']!r} 类型={meta['kind']} "
        f"总数={meta['total']}"
    )
    return meta, page_url


async def _fetch_web_album(
    session: Any, settings: PluginSettings, article_id: str, kind: str,
    meta: dict, page_url: str, *, timeout: int, proxy: str | None,
    stage_errors: list[str],
) -> AlbumInfo | None:
    api_url = f"{settings.mzt_site}/app/post/p?id={article_id}"
    resp = await _request_stage(
        "web", api_url,
        headers=browser_headers(
            ua=_user_agent(settings), referer=page_url, accept=ACCEPT_JSON,
            json_api=True, site="same-origin",
        ),
        timeout=timeout, proxy=proxy, stage_errors=stage_errors, session=session,
    )
    if resp is None:
        return None
    payload = _json_payload(resp, "web", stage_errors)
    if payload is None:
        return None
    try:
        decoded = decode_api_payload(str(payload["data"]), article_id)
    except KkmztDecryptError:
        stage_errors.append(_stage_error("网页接口", "数据解密失败"))
        return None
    album = _album_from_decoded(
        settings, article_id, kind, decoded, meta, page_url, source="web"
    )
    if album is None:
        stage_errors.append(_stage_error("网页接口", "没有解析到图片"))
        return None
    _dbg(f"mzt._fetch_web_album：解密得到 {len(album.image_urls)} 张")
    return album


async def _fetch_album_http(
    settings: PluginSettings, article_id: str, kind: str, *, timeout: int,
    proxy: str | None, stage_errors: list[str],
) -> tuple[AlbumInfo | None, dict | None]:
    """APP →（同一会话）文章页 → 网页接口；返回 ``(album, article_meta)``。"""
    app_album = await _fetch_app_album(
        settings, article_id, kind, timeout=timeout, proxy=proxy,
        stage_errors=stage_errors,
    )
    article_meta: dict | None = None
    web_album: AlbumInfo | None = None
    async with client_session(timeout=timeout) as session:
        article_meta, page_url = await _fetch_article_http(
            session, settings, article_id, kind, timeout=timeout, proxy=proxy,
            stage_errors=stage_errors,
        )
        if app_album is None and article_meta is not None:
            web_album = await _fetch_web_album(
                session, settings, article_id, kind, article_meta, page_url,
                timeout=timeout, proxy=proxy, stage_errors=stage_errors,
            )
    if app_album is not None:
        if article_meta:
            if article_meta.get("title"):
                app_album.title = str(article_meta["title"])
            if article_meta.get("publish_time") not in (None, "", "未知时间"):
                app_album.publish_time = str(article_meta["publish_time"])
            app_album.kind = str(article_meta.get("kind") or app_album.kind)
            app_album.page_url = page_url
        return app_album, article_meta
    return web_album, article_meta


# --------------------------------------------------------------------------- #
# 浏览器兜底
# --------------------------------------------------------------------------- #
_BROWSER_FETCH_JS = r"""
async (articleId) => {
  const q = (sel) => document.querySelector(sel);
  const img = q('figure img');
  const h1 = q('h1.uk-article-title') || q('h1');
  const time = q('time');
  const pb = q('#progressBar');
  const meta = {
    title: h1 ? (h1.textContent || '').trim() : '',
    publish_time: time ? (time.textContent || '').trim() : '',
    first_image: img ? (img.getAttribute('src') || '') : '',
    referrer_policy: img ? (img.getAttribute('referrerpolicy') || 'origin') : 'origin',
    total: pb ? parseInt(pb.getAttribute('max') || '0', 10) || 0 : 0,
    path: location.pathname,
  };
  const resp = await fetch('/app/post/p?id=' + encodeURIComponent(String(articleId)), {
    credentials: 'same-origin',
    headers: {'Accept': 'application/json, text/javascript, */*; q=0.01',
              'X-Requested-With': 'XMLHttpRequest'},
  });
  if (!resp.ok) throw new Error('图片接口 HTTP ' + resp.status);
  const payload = await resp.json();
  if (!payload || !payload.data) throw new Error('图片接口没有 data 字段');
  const raw = Uint8Array.from(atob(payload.data), (c) => c.charCodeAt(0));
  const salt = raw.slice(0, 16);
  const iv = raw.slice(16, 28);
  const encrypted = raw.slice(28);
  const keyMaterial = await crypto.subtle.importKey(
    'raw', new TextEncoder().encode(String(articleId)), 'HKDF', false, ['deriveKey']);
  const key = await crypto.subtle.deriveKey(
    {name: 'HKDF', hash: 'SHA-256', salt, info: new Uint8Array(0)},
    keyMaterial, {name: 'AES-GCM', length: 256}, false, ['decrypt']);
  const plaintext = await crypto.subtle.decrypt({name: 'AES-GCM', iv}, key, encrypted);
  const names = JSON.parse(new TextDecoder().decode(plaintext));
  return {meta, names};
}
"""


def _normalize_browser_meta(raw: dict, fallback_kind: str) -> dict:
    path = str(raw.get("path") or "")
    kind = "beauty" if path.startswith("/beauty/") else (
        "photo" if path.startswith("/photo/") else fallback_kind
    )
    return {
        "title": str(raw.get("title") or "未知标题"),
        "publish_time": str(raw.get("publish_time") or "未知时间"),
        "first_image": str(raw.get("first_image") or ""),
        "referrer_policy": str(raw.get("referrer_policy") or "origin"),
        "total": int(raw.get("total") or 0),
        "kind": kind,
    }


async def _browser_evaluate_payload(page: Any, article_id: str) -> tuple[dict | None, dict | None]:
    """页面内 fetch + WebCrypto 解密（原逻辑）。"""
    try:
        result = await page.evaluate(_BROWSER_FETCH_JS, str(article_id))
    except Exception as exc:
        _dbg(f"mzt._browser_evaluate_payload：失败（{type(exc).__name__}）")
        return None, None
    if not isinstance(result, dict):
        return None, None
    raw_meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    names = result.get("names") or []
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        return None, raw_meta
    return {
        "scheme": "web", "images": names, "count": 0,
        "title": str(raw_meta.get("title") or ""),
        "date": str(raw_meta.get("publish_time") or ""), "hd": False, "thumbs": False,
    }, raw_meta


async def _read_page_meta(page: Any) -> dict:
    total_attr = await query_first_attr(page, "#progressBar", "max")
    path = ""
    try:
        path = str(await page.evaluate("() => location.pathname") or "")
    except Exception:
        pass
    return {
        "title": str((await query_first_text(page, "h1.uk-article-title"))
                     or (await query_first_text(page, "h1")) or "未知标题"),
        "publish_time": str((await query_first_text(page, "time")) or "未知时间"),
        "first_image": str((await query_first_attr(page, "figure img", "src")) or ""),
        "referrer_policy": str(
            (await query_first_attr(page, "figure img", "referrerpolicy")) or "origin"
        ),
        "total": int(total_attr) if total_attr.isdigit() else 0,
        "path": path,
    }


async def _browser_request_payload(
    settings: PluginSettings, context: Any, page_url: str, article_id: str
) -> dict | None:
    """evaluate 失败后用浏览器上下文（含 Cookie）直接请求网页接口。"""
    api_url = f"{settings.mzt_site}/app/post/p?id={article_id}"
    try:
        response = await context.request.get(
            api_url,
            headers=browser_headers(
                ua=_user_agent(settings), referer=page_url, accept=ACCEPT_JSON,
                json_api=True, site="same-origin",
            ),
        )
        status = getattr(response, "status", None)
        text = await response.text()
    except Exception as exc:
        _dbg(f"mzt._browser_request_payload：请求失败（{type(exc).__name__}）")
        return None
    if status != 200:
        _dbg(f"mzt._browser_request_payload：HTTP {status}")
        return None
    try:
        payload = jsonlib.loads(text)
    except (jsonlib.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict) or not payload.get("data"):
        return None
    try:
        return decode_api_payload(str(payload["data"]), article_id)
    except KkmztDecryptError:
        return None


async def _fetch_album_browser(
    settings: PluginSettings, article_id: str, kind: str, *, timeout: int,
    cf_wait: float, proxy: str | None, ua: str, stage_errors: list[str],
) -> AlbumInfo | None:
    """浏览器兜底：打开文章页 → 等 CF 挑战 → 页面内解密 → context.request 兜底。"""
    page_url = album_url(settings, article_id, kind)
    _dbg(f"mzt._fetch_album_browser：打开 {sanitize_url(page_url)}")
    try:
        async with browser_context(
            headless=True, user_agent=ua, locale="zh-CN",
            extra_headers={"Referer": settings.mzt_site + "/"},
            timeout_ms=timeout * 1000, proxy=proxy,
        ) as (_browser, context, page):
            started = time.monotonic()
            try:
                response = await goto(page, page_url, timeout_ms=timeout * 1000)
            except BrowserError as exc:
                _log_stage("browser", page_url, None, elapsed_ms=_elapsed_ms(started),
                           error=type(exc).__name__)
                stage_errors.append(
                    _stage_error("浏览器", f"打开页面失败（{type(exc).__name__}）")
                )
                return None
            response_headers = getattr(response, "headers", None) if response is not None else None
            state = await detect_cloudflare(page, response)
            state_status = state.get("status") if isinstance(state.get("status"), int) else None
            if state.get("kind") not in (None, "none"):
                if not await wait_cloudflare_clear(page, cf_wait):
                    if state.get("ray"):
                        description = f"Cloudflare 拦截 Ray ID {state['ray']}"
                    elif state.get("kind") == "challenge":
                        description = "Cloudflare 挑战未通过"
                    elif state.get("kind") == "http-error":
                        description = f"HTTP {state_status}"
                    else:
                        description = "Cloudflare 拦截"
                    _log_stage("browser", page_url, state_status, headers=response_headers,
                               challenge="blocked", elapsed_ms=_elapsed_ms(started))
                    stage_errors.append(_stage_error("浏览器", description))
                    return None
                _log_stage("browser", page_url, state_status, headers=response_headers,
                           challenge="passed", elapsed_ms=_elapsed_ms(started))
            else:
                _log_stage("browser", page_url, state_status, headers=response_headers,
                           elapsed_ms=_elapsed_ms(started))

            decoded, raw_meta = await _browser_evaluate_payload(page, article_id)
            meta = _normalize_browser_meta(raw_meta, kind) if isinstance(raw_meta, dict) else None
            if decoded is None:
                decoded = await _browser_request_payload(
                    settings, context, page_url, article_id
                )
                meta = _normalize_browser_meta(await _read_page_meta(page), kind)
            if decoded is None:
                stage_errors.append(_stage_error("浏览器", "页面内接口请求失败"))
                return None
            album = _album_from_decoded(
                settings, article_id, kind, decoded, meta, page_url, source="browser"
            )
            if album is None:
                stage_errors.append(_stage_error("浏览器", "没有解析到图片"))
                return None
            _dbg(f"mzt._fetch_album_browser：解密得到 {len(album.image_urls)} 张")
            return album
    except BrowserError as exc:
        _log_stage("browser", page_url, None, error=type(exc).__name__)
        stage_errors.append(
            _stage_error("浏览器", sanitize_error_text(str(exc), max_len=70))
        )
        return None
    except Exception as exc:
        _log_stage("browser", page_url, None, error=type(exc).__name__)
        stage_errors.append(_stage_error("浏览器", f"运行异常（{type(exc).__name__}）"))
        return None


# --------------------------------------------------------------------------- #
# 限速 / 结构化错误 / 对外入口
# --------------------------------------------------------------------------- #
async def _rate_limit(article_id: str) -> None:
    """进程内同一文章两次抓取最小间隔 3s；命中时等待并记录日志。"""
    remaining = RATE_LIMIT_SECONDS - (time.monotonic() - _LAST_FETCH_AT.get(article_id, 0.0))
    if article_id in _LAST_FETCH_AT and remaining > 0:
        _log(f"[mzt] rate-limit wait={int(remaining * 1000)}ms")
        await asyncio.sleep(remaining)
    _LAST_FETCH_AT[article_id] = time.monotonic()


class MztFetchError(BrowserError, HttpError):
    """所有降级路径都失败时的结构化错误（同时兼容旧的 BrowserError 捕获）。"""

    def __init__(self, stage_errors: Sequence[str], hint: str = "") -> None:
        self.stage_errors = [
            sanitize_error_text(item, max_len=_STAGE_MAX)
            for item in stage_errors if str(item or "").strip()
        ]
        self.hint = sanitize_error_text(hint, max_len=120)
        summary = "；".join(self.stage_errors)
        if self.hint:
            summary = f"{summary}；{self.hint}" if summary else self.hint
        super().__init__(summary or "未知错误")
        self.status = None
        self.cf_ray = ""
        self.headers = {}


def _build_hint(stage_errors: Sequence[str]) -> str:
    joined = " ".join(stage_errors)
    hints: list[str] = []
    if "Cloudflare" in joined:
        hints.append("可在配置 mzt_proxy 使用可用代理后重试")
    if "证书" in joined or "cert" in joined.lower():
        hints.append("APP 接口持续 403 时请检查 mzt_app_cert 客户端证书")
    return "；".join(hints)


def format_fetch_error(exc: BaseException) -> str:
    """功能层展示用摘要（脱敏、定长，无 HTML/Cookie/证书/sign）。"""
    if isinstance(exc, MztFetchError):
        return sanitize_error_text(str(exc), max_len=520) or "未知错误"
    return sanitize_error_text(str(exc), max_len=300) or type(exc).__name__


async def fetch_album(
    settings: PluginSettings, article_id: str | int, *, kind: str = DEFAULT_KIND,
    timeout: int = 60, progress: "ProgressCallback | None" = None,
) -> AlbumInfo:
    """解析单个写真/潮拍文章：APP → 网页 → 浏览器 → 文章页首图兜底。"""
    article_id = str(article_id).strip()
    kind = "beauty" if str(kind).lower() == "beauty" else "photo"
    if not article_id:
        raise MztFetchError(["文章ID:为空"], "")
    await _rate_limit(article_id)
    proxy = _proxy(settings)
    ua = _user_agent(settings)
    stage_errors: list[str] = []
    _dbg(
        f"mzt.fetch_album：id={article_id} kind={kind} timeout={timeout}s "
        f"proxy={'on' if proxy else 'off'}"
    )
    article_meta: dict | None = None
    album: AlbumInfo | None = None
    try:
        album, article_meta = await _fetch_album_http(
            settings, article_id, kind, timeout=timeout, proxy=proxy,
            stage_errors=stage_errors,
        )
    except Exception as exc:
        _dbg(f"mzt.fetch_album：直连路径异常（{type(exc).__name__}）")
        if not stage_errors:
            stage_errors.append(_stage_error("直连", _short_error(exc)))

    if album is not None and album.image_urls:
        return await _finish_album(album, progress)

    if getattr(settings, "mzt_browser_fallback", True):
        browser_album = await _fetch_album_browser(
            settings, article_id, kind, timeout=timeout,
            cf_wait=_cf_wait(settings), proxy=proxy, ua=ua,
            stage_errors=stage_errors,
        )
        if browser_album is not None and browser_album.image_urls:
            return await _finish_album(browser_album, progress)
    else:
        stage_errors.append(_stage_error("浏览器", "已禁用（mzt_browser_fallback=false）"))

    fallback = _first_image_album(settings, article_id, kind, article_meta)
    if fallback is not None:
        _dbg("mzt.fetch_album：使用文章页首图兜底")
        return await _finish_album(fallback, progress)

    _log("[mzt] result source=none images=0 total=0 truncated=true")
    raise MztFetchError(stage_errors, _build_hint(stage_errors))


# --------------------------------------------------------------------------- #
# 文章 ID 列表采集（/更新写真ID）
# --------------------------------------------------------------------------- #
_THUMB_ID_PATTERN = re.compile(
    r'<a[^>]+class="[^"]*u-thumb-v[^"]*"[^>]*href="([^"]+)"', re.I
)
_THUMB_ID_PATTERN_REVERSED = re.compile(
    r'<a[^>]+href="([^"]+)"[^>]*class="[^"]*u-thumb-v[^"]*"', re.I
)


def _extract_page_ids(page_html: str) -> list[str]:
    hrefs = [m.group(1) for m in _THUMB_ID_PATTERN.finditer(page_html)]
    if not hrefs:
        hrefs = [m.group(1) for m in _THUMB_ID_PATTERN_REVERSED.finditer(page_html)]
    page_ids: list[str] = []
    for href in hrefs:
        match = URL_ID_PATTERN.search(href)
        if match and match.group(1).lower() == "photo":
            page_ids.append(match.group(2))
    return page_ids


async def _collect_article_ids_http(
    settings: PluginSettings, existing: set[str], *, full: bool, timeout: int
) -> list[str]:
    base_url = f"{settings.mzt_site}/photo/"
    discovered: list[str] = []
    found: set[str] = set(existing)
    page_no = 1
    while True:
        url = base_url if page_no == 1 else f"{base_url}page/{page_no}/"
        page_html = await fetch_text(
            url, timeout=timeout, referer=settings.mzt_site + "/",
            ua=_user_agent(settings), headers=ARTICLE_HEADERS, proxy=_proxy(settings),
        )
        page_ids = _extract_page_ids(page_html)
        if not page_ids:
            break
        if not full and all(item in found for item in page_ids):
            break
        for item in page_ids:
            if item not in found:
                found.add(item)
                discovered.append(item)
        page_no += 1
        if page_no > 500:
            break
    return discovered


async def _collect_article_ids_browser(
    settings: PluginSettings, existing: set[str], *, full: bool, timeout: int
) -> list[str]:
    base_url = f"{settings.mzt_site}/photo/"
    discovered: list[str] = []
    found: set[str] = set(existing)
    async with browser_context(
        headless=True, user_agent=_user_agent(settings), locale="zh-CN",
        timeout_ms=timeout * 1000, proxy=_proxy(settings),
    ) as (_browser, _context, page):
        page_no = 1
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
                match = URL_ID_PATTERN.search(href or "")
                if match and match.group(1).lower() == "photo":
                    page_ids.append(match.group(2))
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
            if page_no > 500:
                break
    return discovered


async def collect_article_ids(
    settings: PluginSettings, existing: set[str], *, full: bool = False, timeout: int = 60
) -> list[str]:
    """爬取写真 ID 列表（先 HTTP，失败回退 Playwright）；返回新发现的 ID。"""
    try:
        return await _collect_article_ids_http(
            settings, existing, full=full, timeout=timeout
        )
    except Exception as exc:
        _dbg(f"mzt.collect_article_ids：HTTP 失败（{type(exc).__name__}），尝试浏览器")
    try:
        return await _collect_article_ids_browser(
            settings, existing, full=full, timeout=timeout
        )
    except Exception as exc:
        raise BrowserError(f"采集写真ID失败（{type(exc).__name__}）") from exc


__all__ = [
    "AlbumInfo", "BrowserError", "KIND_LABEL", "MztFetchError", "album_url",
    "collect_article_ids", "fetch_album", "format_fetch_error",
    "parse_article_id", "parse_article_ref",
]
