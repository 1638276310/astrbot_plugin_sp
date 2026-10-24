"""妹子图（kkmzt / 美之图）抓取链路。

唯一链路（不再直接访问站点 HTML 页面）：

1. 从用户指令解析文章 ID（:func:`parse_article_refs`，支持一次多个 ID）；
2. 用 ID 拼出官方 APP 接口地址 ``{mzt_app_api}/post/p?id={id}``；
3. 用随包证书 ``assets/mzt_client.pem`` 建立 mTLS 连接请求该接口；
4. 调用内置解密模块 :mod:`app_core.mzt_crypto` 的 ``decode_api_payload``
   解出完整图片 URL 列表（APP 端为全量，不受网页端 20 张预览限制）；
5. 由 ``features/mzt.py`` 按解密结果逐张下载。

失败时抛 :class:`MztFetchError`，``str()`` 形如
``APP接口:HTTP 403`` / ``APP接口:Cloudflare 拦截 Ray ID ...`` /
``APP接口:客户端证书不可用（...）``。本模块不访问文章页、不请求网页接口、
也不启动浏览器，因此既不会把 Cloudflare 挑战页误判为成功，也没有直连兜底。
"""

from __future__ import annotations

import asyncio
import json as jsonlib
import re
import ssl
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal, Sequence

from .browser import BrowserError
from .http import (
    ACCEPT_JSON,
    HttpError,
    browser_headers,
    cloudflare_ray,
    describe_http_error,
    fetch_response,
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
MAX_IDS_PER_COMMAND = 10
RATE_LIMIT_SECONDS = 3.0
_LAST_FETCH_AT: dict[str, float] = {}

# 指令里的引用：优先整条链接，其次“写真馆/潮拍馆 + 一串数字”。
_REF_PATTERN = re.compile(
    r"https?://\S*?/(?P<link_kind>photo|beauty)/(?P<link_id>\d{1,12})"
    r"|(?P<cmd_kind>写真馆|潮拍馆)(?P<ids>(?:[ \t,，、;；/]*\d{1,12})+)",
    re.I,
)
_DIGIT_PATTERN = re.compile(r"\d{1,12}")
# 兼容旧用法：整条消息只有裸 ID 时的兜底（与旧版 BARE_ID_PATTERN 一致）
_BARE_ID_PATTERN = re.compile(r"(?<![\d/])(\d{2,12})(?![\d/])")
_STAGE_LABEL = {"app": "APP接口"}
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


def _http_stage_detail(exc: HttpError) -> str:
    """HTTP 异常 → 失败原因（403/503 带 cf-ray 时明确标注 Cloudflare）。"""
    detail = describe_http_error(exc)
    status = getattr(exc, "status", None)
    ray = cloudflare_ray(getattr(exc, "headers", None)) or str(
        getattr(exc, "cf_ray", "") or ""
    )
    if status in (403, 503) and ray and "Cloudflare" not in detail:
        return f"Cloudflare 拦截 HTTP {status}（Ray ID {ray}）"
    return detail


def _proxy(settings: PluginSettings) -> str | None:
    return str(getattr(settings, "mzt_proxy", "") or "").strip() or None


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
        return {"app": "美之图 APP 接口（mTLS 解密，全量）"}.get(self.source, self.source)

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
            lines.append(
                f"⚠️ 已按配置 mzt_max_images 截断，仅发送前 {len(self.image_urls)} 张"
            )
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 指令文本 → 文章 ID（支持多个 ID，逐个处理）
# --------------------------------------------------------------------------- #
def _normalize_kind(value: str | None, default: str = DEFAULT_KIND) -> ArticleKind:
    text = str(value or "").strip().lower()
    if text in ("beauty", "潮拍馆"):
        return "beauty"
    if text in ("photo", "写真馆"):
        return "photo"
    return "beauty" if str(default).lower() == "beauty" else "photo"


def parse_article_refs(
    text: str, *, default_kind: str = DEFAULT_KIND
) -> list[tuple[str, ArticleKind]]:
    """从指令文本提取全部 ``(文章ID, 类型)``，按出现顺序去重。

    支持三种写法（与旧版保持兼容，但一次可命中多个 ID）：

    * 链接：``.../photo/171317``、``.../beauty/191566``（只解析路径里的 ID）；
    * 指令关键字：``写真馆 171317 171318``、``潮拍馆 191566``，
      数字之间可用空格/逗号/顿号/分号/斜杠分隔；
    * 裸 ID：整条消息没有链接和关键字时，兼容旧版按 ``default_kind`` 解析。

    未命中返回空列表；命中多个时由调用方逐个处理。
    """
    raw = (text or "").strip()
    if not raw:
        return []
    default = _normalize_kind(default_kind)
    refs: list[tuple[str, ArticleKind]] = []
    seen: set[str] = set()

    def add(article_id: str, kind: str) -> None:
        value = str(article_id or "").strip()
        if not value or value in seen:
            return
        seen.add(value)
        refs.append((value, _normalize_kind(kind)))

    for match in _REF_PATTERN.finditer(raw):
        link_id = match.group("link_id")
        if link_id:
            add(link_id, match.group("link_kind") or default)
            continue
        command_kind = "beauty" if match.group("cmd_kind") == "潮拍馆" else "photo"
        for article_id in _DIGIT_PATTERN.findall(match.group("ids") or ""):
            add(article_id, command_kind)

    if refs:
        return refs

    # 兼容旧用法：整条消息只有一个裸 ID（例如上游把指令关键字去掉时）
    for match in _BARE_ID_PATTERN.finditer(raw):
        add(match.group(1), default)
    return refs


def parse_article_ref(text: str) -> tuple[str, ArticleKind] | None:
    """兼容旧接口：返回第一个 ``(文章ID, 类型)``。"""
    refs = parse_article_refs(text)
    return refs[0] if refs else None


def parse_article_id(text: str) -> str | None:
    """兼容旧接口：只返回文章 ID。"""
    ref = parse_article_ref(text)
    return ref[0] if ref else None


def album_url(settings: PluginSettings, article_id: str | int, kind: str = DEFAULT_KIND) -> str:
    """生成展示用页面地址（仅字符串拼接，抓取链路不会访问它）。"""
    kind_path = "beauty" if str(kind).lower() == "beauty" else "photo"
    return f"{settings.mzt_site}/{kind_path}/{article_id}"


ProgressCallback = Callable[[str, int, int], Awaitable[None]]


def _apply_limit(urls: list[str], settings: PluginSettings) -> list[str]:
    limit = getattr(settings, "mzt_max_images", 0)
    return urls[:limit] if isinstance(limit, int) and limit > 0 else urls


# --------------------------------------------------------------------------- #
# APP mTLS 客户端证书：配置（包内相对路径）→ assets → app_core 旧路径
# --------------------------------------------------------------------------- #
def _cert_candidates(settings: PluginSettings) -> list[Path]:
    """证书候选（按序）：配置相对路径 → ``assets/mzt_client.pem`` → 旧 app_core 路径。

    只接受**插件目录内的相对路径**：绝对路径、``~`` 展开和 ``..`` 越界一律拒绝并
    记录 ``stage=cert state=rejected``，避免部署后加载到插件包外的文件。
    """
    candidates: list[Path] = []
    configured = str(getattr(settings, "mzt_app_cert", "") or "").strip()
    if configured:
        rejected = ""
        raw_path = Path(configured)
        if raw_path.is_absolute() or configured.startswith("~"):
            rejected = "absolute-path"
        else:
            resolved = (_PLUGIN_ROOT / raw_path).resolve()
            try:
                resolved.relative_to(_PLUGIN_ROOT)
            except ValueError:
                rejected = "path-escape"
            if not rejected:
                candidates.append(resolved)
        if rejected:
            _log(f"[mzt] stage=cert path=<configured> state=rejected reason={rejected}")
    candidates.extend([ASSETS_CLIENT_CERT_PATH, APP_CLIENT_CERT_PATH])

    unique: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key not in seen:
            seen.add(key)
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
            _log(
                f"[mzt] stage=cert path={path.name} "
                f"state=cert-load-failed reason={type(exc).__name__}"
            )
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
    if not base.lower().startswith(("http://", "https://")):
        return ""
    return base.rstrip("/") + f"/post/p?id={article_id}"


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
    page_url: str, *, source: str = "app",
) -> AlbumInfo | None:
    """解密结果 → AlbumInfo（只接受 APP 端格式：完整图片 URL + 全量张数）。"""
    if not isinstance(decoded, dict) or decoded.get("scheme") != "app":
        return None
    images = [str(url) for url in (decoded.get("images") or []) if url]
    if not images:
        return None
    total = int(decoded.get("count") or 0) or len(images)
    # APP 接口返回的 sign 自带鉴权：写真馆不需要 Referer；潮拍馆（thumbs，新浪图床）需要微博 Referer
    image_referer = "https://weibo.com/" if decoded.get("thumbs") else None
    if getattr(settings, "mzt_hd", False):
        urls = (
            [hd_beauty_image_url(url) for url in images]
            if decoded.get("thumbs") else [hd_image_url(url) for url in images]
        )
    else:
        urls = images
    limited = _apply_limit(urls, settings)
    if not limited:
        return None
    return AlbumInfo(
        str(article_id),
        str(decoded.get("title") or "未知标题"),
        str(decoded.get("date") or "未知时间"),
        limited,
        kind=str(kind),
        total=total,
        truncated=bool(total and len(limited) < total),
        referrer_policy="no-referrer",
        image_referer=image_referer,
        page_url=page_url,
        source=source,
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


# --------------------------------------------------------------------------- #
# 直连 APP 接口（mTLS）→ 内置解密
# --------------------------------------------------------------------------- #
async def _request_stage(
    stage: str, url: str, *, headers: dict[str, str], timeout: int, proxy: str | None,
    stage_errors: list[str], ssl_context: ssl.SSLContext | None = None,
) -> Any:
    """发一个阶段请求：成功返回 FetchResponse，失败记录状态码/cf-ray 并返回 None。"""
    started = time.monotonic()
    try:
        resp = await fetch_response(
            url, timeout=timeout, headers=headers, proxy=proxy, ssl=ssl_context,
        )
    except HttpError as exc:
        _log_stage(
            stage, url, exc.status, headers=exc.headers,
            elapsed_ms=exc.elapsed_ms or _elapsed_ms(started),
            error=type(exc).__name__,
        )
        stage_errors.append(_stage_error(_STAGE_LABEL[stage], _http_stage_detail(exc)))
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
    """校验接口响应是合法 JSON 且带 ``data``；HTML 挑战页会在这里被拒绝。"""
    try:
        payload = jsonlib.loads(resp.text)
    except ValueError:
        _log(
            f"[mzt] stage=decrypt state=invalid-json status={resp.status} "
            f"cf-ray={resp.cf_ray or '-'}"
        )
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
    """唯一抓取阶段：APP 接口（mTLS，全量）→ 内置解密。"""
    api_url = _app_api_url(settings, article_id)
    if not api_url:
        _dbg("mzt._fetch_app_album：未配置合法的 mzt_app_api，跳过")
        stage_errors.append(_stage_error("APP接口", "未配置 mzt_app_api"))
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
    except KkmztDecryptError as exc:
        _log(
            f"[mzt] stage=decrypt id={article_id} state=failed "
            f"status={resp.status} cf-ray={resp.cf_ray or '-'} "
            f"error={sanitize_error_text(str(exc), 80)}"
        )
        stage_errors.append(_stage_error("APP接口", "数据解密失败"))
        return None

    images = [str(url) for url in (decoded.get("images") or []) if url]
    count = int(decoded.get("count") or 0)
    _log(
        f"[mzt] stage=decrypt id={article_id} state=ok scheme={decoded.get('scheme')} "
        f"count={count} images={len(images)} status={resp.status} "
        f"cf-ray={resp.cf_ray or '-'}"
    )
    if not images:
        # 解密成功但列表为空/数量异常：记录 kind（scheme）/状态码/ray，明确报错，绝不改走直连
        _log(
            f"[mzt] stage=decrypt id={article_id} state=empty kind={decoded.get('scheme')} "
            f"count={count} status={resp.status} cf-ray={resp.cf_ray or '-'}"
        )
        stage_errors.append(_stage_error("APP接口", "解密返回图片列表为空"))
        return None
    if count and len(images) != count:
        _log(
            f"[mzt] stage=decrypt id={article_id} state=count-mismatch count={count} "
            f"images={len(images)} status={resp.status} cf-ray={resp.cf_ray or '-'}"
        )

    album = _album_from_decoded(
        settings, article_id, kind, decoded, api_url, source="app"
    )
    if album is None:
        stage_errors.append(_stage_error("APP接口", "没有解析到图片"))
        return None
    _log(
        f"[mzt] chain=app+decrypt id={article_id} kind={album.kind} "
        f"images={len(album.image_urls)} total={album.total}"
    )
    return album


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
    """抓取链路失败时的结构化错误（同时兼容旧的 BrowserError / HttpError 捕获）。"""

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
    if "证书" in joined or "cert" in joined.lower():
        hints.append("检查 assets/mzt_client.pem 是否为官方 APP 证书且随插件一起上传")
    if "Cloudflare" in joined:
        hints.append("APP 接口被 Cloudflare 拒绝时，先确认 mTLS 证书有效")
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
    """解析单个写真/潮拍文章。

    链路固定为：ID → ``{mzt_app_api}/post/p?id={id}`` → mTLS 请求 →
    :func:`app_core.mzt_crypto.decode_api_payload` 解密 → 完整图片 URL。
    失败直接抛 :class:`MztFetchError`，不降级到网页/浏览器/首图。
    """
    article_id = str(article_id).strip()
    kind = "beauty" if str(kind).lower() == "beauty" else "photo"
    if not article_id:
        raise MztFetchError(["文章ID:为空"], "")
    await _rate_limit(article_id)
    proxy = _proxy(settings)
    stage_errors: list[str] = []
    _dbg(
        f"mzt.fetch_album：id={article_id} kind={kind} timeout={timeout}s "
        f"proxy={'on' if proxy else 'off'} 链路=APP接口(mTLS)+内置解密"
    )
    album = await _fetch_app_album(
        settings, article_id, kind, timeout=timeout, proxy=proxy,
        stage_errors=stage_errors,
    )
    if album is not None and album.image_urls:
        return await _finish_album(album, progress)
    if not stage_errors:
        stage_errors.append(_stage_error("APP接口", "未返回任何图片"))
    _log("[mzt] result source=none images=0 total=0 truncated=true")
    raise MztFetchError(stage_errors, _build_hint(stage_errors))


__all__ = [
    "AlbumInfo", "BrowserError", "KIND_LABEL", "MAX_IDS_PER_COMMAND", "MztFetchError",
    "album_url", "fetch_album", "format_fetch_error",
    "parse_article_id", "parse_article_ref", "parse_article_refs",
]
