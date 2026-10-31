"""妹子图（kkmzt / 美之图）抓取链路。

唯一链路（不再直接访问站点 HTML 页面）：

1. 从用户指令解析文章 ID（:func:`parse_article_refs`，支持一次多个 ID）；
2. 用 ID 拼出官方 APP 接口地址（基址内置为 :data:`MZT_APP_API_DEFAULT`）；
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
import datetime
import hashlib
import json as jsonlib
from pathlib import Path
import re
import ssl
import time
from typing import Any, Awaitable, Callable, Literal, Sequence

from .browser import BrowserError
from .http import (
    ACCEPT_JSON,
    HttpError,
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
from .settings import MZT_APP_API_DEFAULT, PluginSettings

ArticleKind = Literal["photo", "beauty"]
KIND_LABEL = {"photo": "写真馆", "beauty": "潮拍馆"}
DEFAULT_KIND: ArticleKind = "photo"
# 兼容旧导入：APP 接口基址的唯一来源是 settings.MZT_APP_API_DEFAULT
APP_API_DEFAULT = MZT_APP_API_DEFAULT
APP_UA = "MztApp For Android Version/3.8.7"
# 与 APP/本地脚本逐字节一致；不要加 en，也不要加浏览器化请求头（见 _app_headers 注释）。
APP_ACCEPT_LANGUAGE = "zh-CN,zh;q=0.9"

# 出站请求默认 direct（设备本机默认网络）；只有显式设置 SP_MZT_PROXY 才走代理。
_LEGACY_WARNED = False

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


def _proxy_route(proxy: str | None) -> str:
    """日志用的出口标签：只显示 direct/proxy，绝不输出代理 URL/账号密码。"""
    return "proxy" if proxy else "direct"


def _header_value(headers: Any, name: str) -> str:
    """大小写无关取响应头（用于 cf-mitigated 等 Cloudflare 诊断头）。"""
    lowered = name.lower()
    try:
        for key, value in (headers or {}).items():
            if str(key).lower() == lowered:
                return str(value)
    except Exception:
        return ""
    return ""


def _log_stage(
    stage: str, url: str, status: int | None = None, *, headers: Any = None,
    elapsed_ms: int = 0, challenge: str = "", error: str = "", route: str = "",
) -> None:
    ray = cloudflare_ray(headers) if headers else ""
    parts = [
        f"[mzt] stage={stage}", f"url={sanitize_url(url)}",
        f"status={status if status is not None else 'ERR'}", f"cf-ray={ray or '-'}",
    ]
    if route:
        parts.append(f"route={route}")
    # cf-mitigated: challenge 表示 Cloudflare 在跑人机挑战，block 表示硬拦截；
    # 两者都返回 403，但排障方向完全不同，必须落日志。
    mitigated = _header_value(headers, "cf-mitigated")
    if mitigated:
        parts.append(f"cf-mitigated={mitigated}")
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


def _warn_legacy_config(settings: PluginSettings) -> None:
    """旧 WebUI 配置键已废弃：只提示一次，避免用户误以为仍在生效。"""
    global _LEGACY_WARNED
    if _LEGACY_WARNED:
        return
    keys = list(getattr(settings, "legacy_mzt_keys", []) or [])
    if not keys:
        return
    _LEGACY_WARNED = True
    _log(
        "[mzt] config=legacy state=ignored "
        f"keys={','.join(keys)} hint=已改为内置默认值，可用环境变量 SP_MZT_* 覆盖"
    )


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
        """图集类型的中文标签。"""
        return KIND_LABEL.get(self.kind, self.kind)

    @property
    def source_label(self) -> str:
        """数据源的中文标签。"""
        return {"app": "美之图 APP 接口（mTLS 解密，全量）"}.get(self.source, self.source)

    @property
    def header(self) -> str:
        """渲染文章信息头的多行文本。"""
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
        """按出现顺序去重后收集一个文章 ID。"""
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
# APP mTLS 客户端证书：内置/环境变量（包内相对路径）→ assets → app_core 旧路径
# --------------------------------------------------------------------------- #
def _cert_candidates(settings: PluginSettings) -> list[Path]:
    """证书候选（按序）：内置/环境变量相对路径 → ``assets/mzt_client.pem`` → 旧 app_core 路径。

    默认值由 :data:`app_core.settings.MZT_APP_CERT_DEFAULT` 提供（随包证书），
    用户无需配置。只接受**插件目录内的相对路径**：绝对路径、``~`` 展开和
    ``..`` 越界一律拒绝并记录 ``stage=cert state=rejected``，避免部署后加载到
    插件包外的文件。
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


def _cert_source(path: Path) -> str:
    """证书来源标签：assets / app_core（旧路径）/ env（环境变量覆盖）。"""
    try:
        if path == ASSETS_CLIENT_CERT_PATH:
            return "assets"
        if path == APP_CLIENT_CERT_PATH:
            return "app_core"
        return "configured"
    except Exception:
        return "unknown"


def _cert_fingerprint(path: Path) -> str:
    """PEM 整文件 sha256 前 16 位（只用于比对，不泄露密钥内容）。

    本地已验证可用的证书：``673b01d9f1692d08``。部署日志里这串必须一致，
    否则说明加载的不是随包证书（例如被环境变量 SP_MZT_APP_CERT 指到别处）。
    """
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return "-"


def _cert_not_after(path: Path) -> str:
    """证书到期日（``YYYY-MM-DD``）；无法解析返回 ``-``。"""
    try:
        decoded = ssl._ssl._test_decode_cert(str(path))  # type: ignore[attr-defined]
        raw = str(decoded.get("notAfter") or "")
        parsed = datetime.datetime.strptime(raw, "%b %d %H:%M:%S %Y %Z")
        return parsed.strftime("%Y-%m-%d")
    except Exception:
        return "-"


def _app_ssl_context(settings: PluginSettings) -> ssl.SSLContext | None:
    """按候选顺序加载 mTLS 证书；失败只记 ``stage=cert``，不打印证书内容。

    成功日志同时输出 **绝对路径 / 来源 / sha256 前 16 位 / 到期日**：
    旧实现只打印 ``path.name``，而 ``assets/mzt_client.pem`` 与旧候选
    ``app_core/mzt_client.pem`` 同名，导致线上无法判断到底加载了哪一份。
    """
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
            _log(
                f"[mzt] stage=cert source={_cert_source(path)} path={path} "
                f"state={reason}"
            )
            continue
        try:
            # 显式补全 TLS 证书链路：
            # 1) 服务端证书按系统/内置 CA 链严格校验（verify_mode=CERT_REQUIRED）；
            # 2) 校验主机名（api.iimzt.com），防中间人；
            # 3) 载入随包 APP 客户端证书（PEM 同时含证书与私钥）完成 mTLS。
            # 如未来官方要求自定义 CA，可在此追加 context.load_verify_locations(...)。
            context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
            context.verify_mode = ssl.CERT_REQUIRED
            context.check_hostname = True
            context.load_default_certs(ssl.Purpose.SERVER_AUTH)
            context.load_cert_chain(str(path))
        except (OSError, ssl.SSLError, ValueError) as exc:
            _CERT_CACHE[key] = (signature, None)
            last_reason = f"cert-load-failed:{type(exc).__name__}"
            _log(
                f"[mzt] stage=cert source={_cert_source(path)} path={path} "
                f"state=cert-load-failed reason={type(exc).__name__}"
            )
            continue
        _CERT_CACHE[key] = (signature, context)
        _CERT_STATE.update(
            {
                "reason": "ok",
                "path": str(path),
                "source": _cert_source(path),
                "sha256": _cert_fingerprint(path),
                "not_after": _cert_not_after(path),
            }
        )
        _log(
            f"[mzt] stage=cert source={_CERT_STATE['source']} path={path} "
            f"sha256={_CERT_STATE['sha256']} not_after={_CERT_STATE['not_after']} "
            f"state=ok"
        )
        return context
    _CERT_STATE["reason"] = last_reason
    _log(f"[mzt] stage=cert state=cert-unavailable reason={last_reason}")
    return None


def _app_api_url(settings: PluginSettings, article_id: str) -> str:
    """拼 APP 接口地址；基址内置为 :data:`MZT_APP_API_DEFAULT`，环境变量可覆盖。"""
    base = (
        str(getattr(settings, "mzt_app_api", "") or "").strip() or APP_API_DEFAULT
    )
    if not base.lower().startswith(("http://", "https://")):
        return ""
    return base.rstrip("/") + f"/post/p?id={article_id}"


def _app_headers(settings: PluginSettings) -> dict[str, str]:
    """APP 接口请求头：与本地脚本 / 真实 APP 逐字节一致，只有 3 个。

    旧实现用 ``browser_headers()`` 拼出 8 个头发出去：
    ``Referer: https://kkmzt.com/`` + ``Sec-Fetch-Site: cross-site`` +
    ``X-Requested-With: XMLHttpRequest`` + ``Sec-Fetch-Dest/Mode``。
    这个组合真实 APP（OkHttp）永远不会发出，属于「伪装没伪装全」的矛盾指纹，
    在数据中心出口（部署机）会抬高 Cloudflare Bot Management 评分。

    实测（``.repro/probe_lead/matrix.json``）：本机出口下 8 头与 3 头都是 200，
    所以它**不是**本机 403 的原因；但为了让「插件发出的请求」与「已验证成功的
    本地脚本」完全同构，消除这一变量，只保留 APP 真实会发的 3 个头。

    注意：``fetch_response`` 内部还会用 ``_headers()`` 补默认值，
    这里给的 3 个 key 会将其全部覆盖，最终上线就是这 3 个。
    """
    return {
        "User-Agent": APP_UA,
        "Accept": ACCEPT_JSON,
        "Accept-Language": APP_ACCEPT_LANGUAGE,
    }


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
    stage: str, url: str, *, headers: dict[str, str], timeout: int,
    stage_errors: list[str], ssl_context: ssl.SSLContext | None = None,
    proxy: str | None = None,
) -> tuple[Any, int | None]:
    """发一个阶段请求：成功返回 ``(FetchResponse, status)``，失败返回 ``(None, status)``。

    默认直连；``proxy`` 非空时走显式 HTTP(S) CONNECT 代理（SP_MZT_PROXY）。
    返回状态码是为了让调用方能针对 403/503 触发「出口探针」（见 :func:`_probe_egress`）。
    """
    started = time.monotonic()
    route = _proxy_route(proxy)
    try:
        resp = await fetch_response(
            url, timeout=timeout, headers=headers, ssl=ssl_context, proxy=proxy,
        )
    except HttpError as exc:
        _log_stage(
            stage, url, exc.status, headers=exc.headers,
            elapsed_ms=exc.elapsed_ms or _elapsed_ms(started),
            route=route,
            # 旧实现只打 ``type(exc).__name__``（永远等于 HttpError），把
            # 「TLS 证书错误（SSLError）」这类真实子类型淹没了。
            error=f"{type(exc).__name__}:{exc}",
        )
        stage_errors.append(_stage_error(_STAGE_LABEL[stage], _http_stage_detail(exc)))
        return None, getattr(exc, "status", None)
    if looks_like_cloudflare(resp.status, resp.headers, resp.text):
        _log_stage(
            stage, url, resp.status, headers=resp.headers,
            elapsed_ms=resp.elapsed_ms, challenge="blocked", route=route,
        )
        stage_errors.append(
            _stage_error(
                _STAGE_LABEL[stage],
                describe_http_error(resp.status, headers=resp.headers, body=resp.text),
            )
        )
        return None, resp.status
    _log_stage(
        stage, url, resp.status, headers=resp.headers,
        elapsed_ms=resp.elapsed_ms, route=route,
    )
    return resp, resp.status


async def _probe_egress(
    settings: PluginSettings, article_id: str, *, kind: str = "photo",
    timeout: int, report: dict[str, str], proxy: str | None = None,
) -> str:
    """用**不需要客户端证书**的文章页判断「出口 IP 是否被 Cloudflare 拦」。

    这是区分两大根因的唯一低成本判据：

    * 文章页 200 → 出口能正常过 CF，APP 接口 403 只能用「证书没送到 / 被拒」解释；
    * 文章页 403 → 连不带证书的普通页面都被拦，说明是**出口 IP/区域策略**，
      此时换证书、改请求头都没有意义，只能更换设备网络出口/代理。

    探针与主链路使用同一出口（直连或 SP_MZT_PROXY），只在 APP 阶段返回
    403/503 时触发，不增加正常路径的请求数。
    """
    site = str(getattr(settings, "mzt_site", "") or "").strip().rstrip("/")
    report["probe"] = "unknown"
    if not site.lower().startswith(("http://", "https://")):
        return "unknown"
    kind_path = "beauty" if str(kind).lower() == "beauty" else "photo"
    url = f"{site}/{kind_path}/{article_id}"
    started = time.monotonic()
    route = _proxy_route(proxy)
    verdict = "unknown"
    try:
        resp = await fetch_response(
            url, timeout=max(5, min(int(timeout), 15)), retries=0, proxy=proxy,
        )
        verdict = "egress-ok"
        _log(
            f"[mzt] stage=probe url={sanitize_url(url)} status={resp.status} "
            f"cf-ray={resp.cf_ray or '-'} route={route} verdict={verdict} "
            f"elapsed={_elapsed_ms(started)}ms"
        )
    except HttpError as exc:
        status = getattr(exc, "status", None)
        ray = cloudflare_ray(getattr(exc, "headers", None)) or ""
        if status == 403:
            # 不带证书的文章页也 403：出口/区域被 CF 拦，与 mTLS 无关。
            verdict = "egress-blocked"
        elif status is None:
            verdict = "unknown"  # 超时 / TLS / DNS：无法判断
        else:
            # 404/5xx 说明 CF 边缘已正常响应，只是页面本身有问题
            verdict = "egress-ok"
        _log(
            f"[mzt] stage=probe url={sanitize_url(url)} "
            f"status={status if status is not None else 'ERR'} cf-ray={ray or '-'} "
            f"route={route} verdict={verdict} "
            f"error={sanitize_error_text(type(exc).__name__, max_len=40)} "
            f"elapsed={_elapsed_ms(started)}ms"
        )
    except Exception as exc:  # noqa: BLE001 - 探针失败绝不影响主链路错误
        _log(
            f"[mzt] stage=probe state=error route={route} "
            f"error={sanitize_error_text(type(exc).__name__, max_len=40)}"
        )
        verdict = "unknown"
    report["probe"] = verdict
    return verdict


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
    stage_errors: list[str], report: dict[str, str] | None = None,
) -> AlbumInfo | None:
    """唯一抓取阶段：APP 接口（mTLS，全量）→ 内置解密；默认直连/可选显式代理。"""
    report = report if report is not None else {}
    proxy = str(getattr(settings, "mzt_proxy", "") or "").strip() or None
    report["proxy"] = _proxy_route(proxy)
    api_url = _app_api_url(settings, article_id)
    if not api_url:
        _dbg("mzt._fetch_app_album：内置 APP 接口基址不合法，跳过")
        stage_errors.append(_stage_error("APP接口", "APP 接口基址不合法"))
        return None
    ssl_context = _app_ssl_context(settings)
    if ssl_context is None:
        reason = str(_CERT_STATE.get("reason") or "cert-missing")
        _log_stage("app", api_url, None, error=f"cert-unavailable:{reason}")
        stage_errors.append(
            _stage_error("APP接口", f"客户端证书不可用（{_cert_problem_label(reason)}）")
        )
        return None
    resp, status = await _request_stage(
        "app", api_url, headers=_app_headers(settings), timeout=timeout,
        stage_errors=stage_errors, ssl_context=ssl_context, proxy=proxy,
    )
    if resp is None:
        # 403/503：立刻用「不带证书的文章页」探针区分「出口被拦」与「证书没生效」
        if status in (403, 503):
            await _probe_egress(
                settings, article_id, kind=kind, timeout=timeout,
                report=report, proxy=proxy,
            )
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
            f"error={sanitize_error_text(str(exc), max_len=80)}"
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


def _build_hint(
    stage_errors: Sequence[str], report: dict[str, str] | None = None
) -> str:
    """失败提示：按「出口探针」结论给出可执行方向，而不是一律甩锅给证书。

    旧实现只要错误里出现 ``Cloudflare`` 就提示「先确认 mTLS 证书有效」，
    把「出口 IP 被拦」这种**换证书永远修不好**的场景也引向证书，属于误导。
    """
    report = report or {}
    joined = " ".join(stage_errors)
    probe = str(report.get("probe") or "")
    cf = "Cloudflare" in joined

    if probe == "egress-blocked" and str(report.get("proxy") or "") == "proxy":
        return (
            "代理出口同样被 Cloudflare 拦截（非证书/配置问题）；"
            "请更换干净代理节点，或改用住宅/海外出口后重试"
        )
    if probe == "egress-blocked":
        # 默认直连：不做自动代理回退；提示换出口或显式配置 SP_MZT_PROXY。
        return (
            "文章页同样被 Cloudflare 拦截（出口 IP/区域策略，非证书/配置问题）；"
            "请更换设备网络出口，或设置 SP_MZT_PROXY 走干净代理后重试"
        )
    if probe == "egress-ok" and cf:
        sha = str(_CERT_STATE.get("sha256") or "")
        head = "文章页可访问但 APP 接口被拒：客户端证书未生效"
        if sha:
            head = f"{head}（sha256={sha}）"
        return f"{head}，核对 stage=cert 的路径与指纹"
    if probe == "egress-ok":
        return "文章页可访问，APP 接口仍失败，请查看 stage= 日志定位状态码"
    if cf:
        return "APP 接口被 Cloudflare 拒绝：先核对随包证书，再查出口 IP 是否被拦截"
    if "证书" in joined or "cert" in joined.lower():
        return "检查随包 assets/mzt_client.pem 是否为官方 APP 证书且随插件一起上传"
    return ""


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

    链路固定为：ID → 内置 APP 接口基址 ``/post/p?id={id}`` → 默认本机 IP 直连
    （或显式 ``SP_MZT_PROXY`` 代理）+ mTLS 客户端证书 →
    :func:`app_core.mzt_crypto.decode_api_payload` 解密 → 完整图片 URL。
    失败直接抛 :class:`MztFetchError`，不降级到网页/浏览器/首图，
    也不做自动代理回退。
    """
    article_id = str(article_id).strip()
    kind = "beauty" if str(kind).lower() == "beauty" else "photo"
    if not article_id:
        raise MztFetchError(["文章ID:为空"], "")
    await _rate_limit(article_id)
    _warn_legacy_config(settings)
    stage_errors: list[str] = []
    report: dict[str, str] = {}
    _dbg(
        f"mzt.fetch_album：id={article_id} kind={kind} timeout={timeout}s "
        f"route={_proxy_route(str(getattr(settings, 'mzt_proxy', '') or '').strip() or None)} "
        "链路=APP接口(mTLS)+内置解密"
    )
    album = await _fetch_app_album(
        settings, article_id, kind, timeout=timeout,
        stage_errors=stage_errors, report=report,
    )
    if album is not None and album.image_urls:
        return await _finish_album(album, progress)
    if not stage_errors:
        stage_errors.append(_stage_error("APP接口", "未返回任何图片"))
    _log("[mzt] result source=none images=0 total=0 truncated=true")
    raise MztFetchError(stage_errors, _build_hint(stage_errors, report))


__all__ = [
    "AlbumInfo", "BrowserError", "KIND_LABEL", "MAX_IDS_PER_COMMAND", "MztFetchError",
    "album_url", "fetch_album", "format_fetch_error",
    "parse_article_id", "parse_article_ref", "parse_article_refs",
]
