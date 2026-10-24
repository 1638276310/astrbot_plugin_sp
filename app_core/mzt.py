"""妹子图（kkmzt / 美之图）解析。

支持两类文章（对应站点两个栏目）：

* ``/photo/{id}``   —— 写真馆
* ``/beauty/{id}``  —— 潮拍馆

对应原 ``mzt.js`` 的 ``/写真馆<ID>``、``/更新写真ID``、``/随机写真``，
并新增 ``/潮拍馆<ID>``。

图片列表有两套接口（详见 ``app_core/mzt_crypto.py``）：

* **APP 端（优先，全量）**：``GET {APP接口}/post/p?id={文章ID}``
  （默认 ``https://api.iimzt.com/app/``），必须携带 APP 内置的 mTLS 客户端证书
  （``mzt_client.pem``，从 APK 的 PKCS#12 提取），返回的 ``data`` 是 **hex**
  编码的 AES-128-CBC 密文，解密后得到带 ``?sign=`` 的完整图片 URL 和 ``count``
  （即全量张数，写真馆也是全量）。
* **网页端（兜底）**：``GET {站点}/app/post/p?id={文章ID}`` → base64 + AES-256-GCM
  解密 → 图片文件名数组 + 文章页首图目录；写真馆网页接口被服务端限制为**前 20 张预览**
  （页面上的 "预览结束，安装美之图APP，免费观赏全片" 即此限制）。

当两套直连接口都被 Cloudflare 拦截时，还会回退 Playwright 在页面内请求网页接口
并用 WebCrypto 解密；此时写真馆同样只有 20 张预览。
"""

from __future__ import annotations

import html as _html
import re
import ssl
from pathlib import Path
from typing import Awaitable, Callable, Literal

from .browser import BrowserError, browser_context, goto, random_delay
from .http import HttpError, fetch_json, fetch_text
from .mzt_crypto import (
    KkmztDecryptError,
    decode_api_payload,
    hd_beauty_image_url,
    hd_image_url,
)
from .settings import PluginSettings

ArticleKind = Literal["photo", "beauty"]

KIND_LABEL: dict[str, str] = {"photo": "写真馆", "beauty": "潮拍馆"}
DEFAULT_KIND: ArticleKind = "photo"

# 官方 APP 使用的 API 基址与 UA（逆向自解密后的 widget JS / res/ua.txt）
APP_API_DEFAULT = "https://api.iimzt.com/app/"
APP_UA = "MztApp For Android Version/3.8.7"
# APP 内 mTLS 客户端证书（从 APK 的 assets/widget/res/config 的 PKCS#12 提取，
# 密码在解密后的 res/key.xml；没有它 api.iimzt.com 会被 Cloudflare 直接 403）
APP_CLIENT_CERT_PATH = Path(__file__).with_name("mzt_client.pem")

_APP_SSL_CONTEXT: ssl.SSLContext | None = None

# 兼容旧代码/旧注释：网页端(写真馆)预览上限，实际抓取不再使用该上限
MAX_IMAGES = 20

URL_ID_PATTERN = re.compile(r"/(photo|beauty)/(\d+)", re.I)
PHOTO_CMD_PATTERN = re.compile(r"写真馆\s*(\d{1,12})")
BEAUTY_CMD_PATTERN = re.compile(r"潮拍馆\s*(\d{1,12})")
BARE_ID_PATTERN = re.compile(r"(?<![\d/])(\d{2,12})(?![\d/])")

ARTICLE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
API_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}


def _dbg(message: str) -> None:
    """统一的控制台 debug 日志输出（失败静默，不影响主流程）。"""
    try:
        from astrbot.api import logger  # type: ignore

        logger.info(f"[涩批DEBUG] {message}")
    except Exception:
        pass


class AlbumInfo:
    """一个写真/潮拍页的解析结果。"""

    def __init__(
        self,
        article_id: str,
        title: str = "未知标题",
        publish_time: str = "未知时间",
        image_urls: list[str] | None = None,
        *,
        kind: str = DEFAULT_KIND,
        total: int = 0,
        truncated: bool = False,
        referrer_policy: str = "origin",
        image_referer: str | None = None,
        page_url: str = "",
        source: str = "web",
    ) -> None:
        self.article_id = article_id
        self.title = title
        self.publish_time = publish_time
        self.image_urls = image_urls or []
        self.kind = kind
        self.total = total
        self.truncated = truncated
        self.referrer_policy = referrer_policy
        # 下载图片时实际发送的 Referer（APP 潮拍馆的新浪图床需要 weibo.com）
        self.image_referer = image_referer
        self.page_url = page_url
        self.source = source

    @property
    def kind_label(self) -> str:
        return KIND_LABEL.get(self.kind, self.kind)

    @property
    def source_label(self) -> str:
        return {
            "app": "美之图 APP 接口（全量）",
            "web": "网页接口",
            "browser": "网页（浏览器兜底）",
        }.get(self.source, self.source)

    @property
    def header(self) -> str:
        lines = [
            f"📌 文章标题: {self.title}",
            f"⏰ 发布时间: {self.publish_time}",
            f"🆔 文章ID: {self.article_id}",
            f"🗂️ 图集类型: {self.kind_label}",
        ]
        if self.total > 0:
            lines.append(f"🖼️ 图片数量: {len(self.image_urls)}/{self.total}")
        else:
            lines.append(f"🖼️ 图片数量: {len(self.image_urls)}")
        lines.append(f"🔗 数据源: {self.source_label}")
        if self.truncated:
            lines.append(
                "⚠️ 网页接口仅提供前 20 张预览（APP 接口暂时不可用），"
                "完整图集需“美之图”APP"
            )
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 文章 ID / 类型解析
# --------------------------------------------------------------------------- #
def parse_article_ref(text: str) -> tuple[str, ArticleKind] | None:
    """从用户消息中解析 ``(文章ID, 类型)``。

    支持：
    * ``/写真馆 171317``、``/潮拍馆 191566``
    * 完整链接 ``https://kkmzt.com/photo/171317`` / ``.../beauty/191566``
    * 裸 ID（默认按写真馆处理）
    """
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
    if match:
        return match.group(1), DEFAULT_KIND
    return None


def parse_article_id(text: str) -> str | None:
    """兼容旧接口：只返回文章 ID（默认写真馆）。"""
    ref = parse_article_ref(text)
    return ref[0] if ref else None


def album_url(
    settings: PluginSettings, article_id: str | int, kind: str = DEFAULT_KIND
) -> str:
    kind_path = "beauty" if str(kind).lower() == "beauty" else "photo"
    return f"{settings.mzt_site}/{kind_path}/{article_id}"


# 进度回调类型别名：(阶段, 当前进度, 总数)，异步调用。
ProgressCallback = Callable[[str, int, int], Awaitable[None]]


# --------------------------------------------------------------------------- #
# HTML 解析
# --------------------------------------------------------------------------- #
def _first_match(pattern: re.Pattern[str], text: str, group: int = 1) -> str:
    match = pattern.search(text)
    return match.group(group).strip() if match else ""


def _parse_article_html(page_html: str, fallback_kind: str) -> dict:
    """从文章页 HTML 中提取标题/时间/首图/目录前缀/总数/真实栏目。"""
    title = _first_match(
        re.compile(r"<h1[^>]*>(.*?)</h1>", re.S | re.I), page_html
    )
    title = _html.unescape(re.sub(r"<[^>]+>", "", title)).strip()

    publish_time = _first_match(
        re.compile(r"<time[^>]*>(.*?)</time>", re.S | re.I), page_html
    )
    publish_time = _html.unescape(re.sub(r"<[^>]+>", "", publish_time)).strip()

    # 首图 + referrerpolicy：<figure ...><img width height referrerpolicy=... src=...>
    first_image = ""
    referrer_policy = "origin"
    figure_match = re.search(r"<figure[^>]*>(.*?)</figure>", page_html, re.S | re.I)
    figure_html = figure_match.group(1) if figure_match else page_html
    img_match = re.search(r"<img[^>]*>", figure_html, re.I)
    if img_match:
        img_tag = img_match.group(0)
        src_match = re.search(r'\bsrc="([^"]+)"', img_tag, re.I)
        ref_match = re.search(r'\breferrerpolicy="([^"]+)"', img_tag, re.I)
        if src_match:
            first_image = _html.unescape(src_match.group(1)).strip()
        if ref_match:
            referrer_policy = ref_match.group(1).strip().lower() or "origin"

    total = 0
    progress_match = re.search(
        r'<progress[^>]*\bmax="(\d+)"[^>]*>', page_html, re.I
    ) or re.search(
        r'<progress[^>]*\bid="progressBar"[^>]*\bmax="(\d+)"[^>]*>',
        page_html,
        re.I,
    )
    if progress_match:
        try:
            total = int(progress_match.group(1))
        except ValueError:
            total = 0

    canonical = _first_match(
        re.compile(
            r'<link[^>]+rel="canonical"[^>]+href="([^"]+)"', re.I
        ),
        page_html,
    ) or _first_match(
        re.compile(
            r'<link[^>]+href="([^"]+)"[^>]+rel="canonical"', re.I
        ),
        page_html,
    )
    kind = fallback_kind
    if "/beauty/" in canonical:
        kind = "beauty"
    elif "/photo/" in canonical:
        kind = "photo"
    elif "/beauty/" in page_html[:4000]:
        kind = "beauty"
    elif "/photo/" in page_html[:4000]:
        kind = "photo"

    return {
        "title": title or "未知标题",
        "publish_time": publish_time or "未知时间",
        "first_image": first_image,
        "referrer_policy": referrer_policy,
        "total": total,
        "kind": kind,
    }


def _dir_prefix(image_url: str) -> str:
    url = (image_url or "").strip()
    if not url:
        return ""
    return url.rsplit("/", 1)[0] + "/" if "/" in url else ""


def _apply_limit(urls: list[str], settings: PluginSettings) -> list[str]:
    limit = getattr(settings, "mzt_max_images", 0)
    if isinstance(limit, int) and limit > 0:
        return urls[:limit]
    return urls


# --------------------------------------------------------------------------- #
# HTTP 路径：直连站点/APP 接口（快，无需浏览器）
# --------------------------------------------------------------------------- #
APP_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}


def _app_ssl_context() -> ssl.SSLContext | None:
    """加载 APP 内置 mTLS 客户端证书；不可用时返回 None（接口会 403 并走网页兜底）。"""
    global _APP_SSL_CONTEXT
    if _APP_SSL_CONTEXT is None:
        try:
            context = ssl.create_default_context()
            context.load_cert_chain(str(APP_CLIENT_CERT_PATH))
            _APP_SSL_CONTEXT = context
            _dbg(f"mzt._app_ssl_context：已加载客户端证书 {APP_CLIENT_CERT_PATH.name}")
        except (OSError, ssl.SSLError) as exc:
            _dbg(f"mzt._app_ssl_context：加载客户端证书失败（{exc!r}），APP 接口可能被 403 拦截")
            return None
    return _APP_SSL_CONTEXT


def _app_api_url(settings: PluginSettings, article_id: str) -> str:
    base = (getattr(settings, "mzt_app_api", APP_API_DEFAULT) or "").strip()
    if not base:
        return ""
    return base.rstrip("/") + f"/post/p?id={article_id}"


async def _fetch_app_album(
    settings: PluginSettings,
    article_id: str,
    kind: str,
    *,
    timeout: int,
) -> AlbumInfo | None:
    """调用官方 APP 接口（默认 api.iimzt.com），成功返回全量图集，失败返回 None。

    APP 接口与网页接口同路径（``post/p?id=``），但：

    * 必须携带 APP 内置的 mTLS 客户端证书（``mzt_client.pem``），否则 Cloudflare 403；
    * 响应 ``data`` 是 **hex** 编码的 AES-128-CBC 密文；
    * 解密后是 ``{"images":[完整URL...],"count":N,"title","date","thumbs"}``，
      其中 ``count`` 即全量张数，``images`` 已带 ``?sign=``。
    """
    api_url = _app_api_url(settings, article_id)
    if not api_url:
        _dbg("mzt._fetch_app_album：未配置 mzt_app_api，跳过 APP 接口")
        return None
    ssl_context = _app_ssl_context()
    _dbg(
        f"mzt._fetch_app_album：GET {api_url}"
        f"（mTLS={'on' if ssl_context is not None else 'off'}）"
    )
    try:
        payload = await fetch_json(
            api_url,
            timeout=timeout,
            ua=APP_UA,
            headers=APP_HEADERS,
            ssl=ssl_context,
        )
        if not isinstance(payload, dict) or not payload.get("data"):
            raise HttpError(f"APP 接口返回格式异常: {str(payload)[:200]}")
        decoded = decode_api_payload(str(payload["data"]), article_id)
    except (HttpError, KkmztDecryptError) as exc:
        _dbg(f"mzt._fetch_app_album：APP 接口失败（{exc!r}）")
        return None

    if decoded.get("scheme") != "app":
        _dbg("mzt._fetch_app_album：接口返回的是网页端 GCM 格式，改走网页接口")
        return None

    images = [url for url in decoded.get("images") or [] if url]
    if not images:
        _dbg("mzt._fetch_app_album：APP 接口没有返回图片")
        return None
    if getattr(settings, "mzt_hd", False):
        if decoded.get("thumbs"):
            # 潮拍馆（新浪图床）：mw1024 -> mw2000
            images = [hd_beauty_image_url(url) for url in images]
        else:
            # 写真馆：ap.iimzt.com -> ax.iimzt.com + sign
            images = [hd_image_url(url) for url in images]
    total = int(decoded.get("count") or 0) or len(images)
    _dbg(
        f"mzt._fetch_app_album：成功，共 {len(images)} 张"
        f"（接口 count={decoded.get('count')}，"
        f"HD={bool(getattr(settings, 'mzt_hd', False))}，thumbs={decoded.get('thumbs')}）"
    )
    return AlbumInfo(
        str(article_id),
        str(decoded.get("title") or "未知标题"),
        str(decoded.get("date") or "未知时间"),
        _apply_limit(images, settings),
        kind=kind,
        total=total,
        truncated=total > len(images),
        referrer_policy="no-referrer",
        # 潮拍馆图片在新浪图床（tva1.sinaimg.cn），有防盗链，需要 weibo.com Referer；
        # 写真馆 ap/ax.iimzt.com 不需要 Referer。
        image_referer=("https://weibo.com/" if decoded.get("thumbs") else None),
        page_url=album_url(settings, article_id, kind),
        source="app",
    )


async def _fetch_album_http(
    settings: PluginSettings,
    article_id: str,
    kind: str,
    *,
    timeout: int,
    progress: "ProgressCallback | None" = None,
) -> AlbumInfo:
    page_url = album_url(settings, article_id, kind)

    # 1) 优先 APP 接口：全量图片，且不依赖文章页
    app_album = await _fetch_app_album(settings, article_id, kind, timeout=timeout)

    # 2) 文章页元信息（标题/时间/首图目录/总张数）；失败不致命，APP 接口仍可用
    meta: dict | None = None
    try:
        _dbg(f"mzt._fetch_album_http：GET 文章页 {page_url}")
        page_html = await fetch_text(
            page_url,
            timeout=timeout,
            referer=settings.mzt_site + "/",
            headers=ARTICLE_HEADERS,
        )
        meta = _parse_article_html(page_html, kind)
        _dbg(
            f"mzt._fetch_album_http：标题={meta['title']!r} "
            f"时间={meta['publish_time']!r} 类型={meta['kind']} 总数={meta['total']}"
        )
    except Exception as exc:
        _dbg(f"mzt._fetch_album_http：文章页获取失败（{exc!r}），继续尝试接口")

    actual_kind = (meta["kind"] if meta else "") or kind

    if app_album is not None:
        if meta:
            # 文章页的标题更完整，优先使用；页面拿不到时保留 APP 返回的标题
            if meta["title"]:
                app_album.title = meta["title"]
            if meta["publish_time"] and meta["publish_time"] != "未知时间":
                app_album.publish_time = meta["publish_time"]
            app_album.kind = actual_kind
        if progress is not None:
            try:
                await progress(
                    "解析",
                    len(app_album.image_urls),
                    app_album.total or len(app_album.image_urls),
                )
            except Exception:
                _dbg("mzt._fetch_album_http：progress 回调执行异常（已忽略）")
        return app_album

    # 3) 网页接口兜底（写真馆只有前 20 张预览）
    if meta is None:
        raise HttpError("APP 接口与文章页均不可用，无法解析图片列表")
    api_url = f"{settings.mzt_site}/app/post/p?id={article_id}"
    _dbg(f"mzt._fetch_album_http：GET 网页图片接口 {api_url}")
    payload = await fetch_json(
        api_url,
        timeout=timeout,
        referer=page_url,
        headers=API_HEADERS,
    )
    if not isinstance(payload, dict) or not payload.get("data"):
        raise HttpError(f"图片接口返回格式异常: {str(payload)[:200]}")
    decoded = decode_api_payload(str(payload["data"]), article_id)

    if decoded.get("scheme") == "app":
        # 理论上不会在网页域出现；若出现则直接使用完整 URL
        urls = [url for url in decoded.get("images") or [] if url]
        referrer_policy = "no-referrer"
        total = int(decoded.get("count") or 0) or len(urls)
    else:
        prefix = _dir_prefix(meta["first_image"])
        urls = [prefix + name for name in decoded.get("images") or [] if name]
        referrer_policy = meta["referrer_policy"]
        total = int(meta["total"] or 0)
    _dbg(f"mzt._fetch_album_http：网页接口解密得到 {len(urls)} 张（页面总数 {total}）")

    truncated = bool(total and len(urls) < total)
    if truncated:
        _dbg(
            f"mzt._fetch_album_http：网页端预览限制，仅 {len(urls)}/{total} 张"
            "（APP 接口不可用时写真馆只能拿到前 20 张）"
        )

    album = AlbumInfo(
        str(article_id),
        meta["title"],
        meta["publish_time"],
        _apply_limit(urls, settings),
        kind=actual_kind,
        total=total,
        truncated=truncated,
        referrer_policy=referrer_policy,
        image_referer=(None if referrer_policy == "no-referrer" else settings.mzt_site),
        page_url=page_url,
        source="web",
    )
    if progress is not None:
        try:
            await progress("解析", len(album.image_urls), total or len(album.image_urls))
        except Exception:
            _dbg("mzt._fetch_album_http：progress 回调执行异常（已忽略）")
    return album


# --------------------------------------------------------------------------- #
# 浏览器兜底：页面内 fetch + WebCrypto 解密（Cloudflare 拦直连时使用）
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


async def _fetch_album_browser(
    settings: PluginSettings,
    article_id: str,
    kind: str,
    *,
    timeout: int,
    progress: "ProgressCallback | None" = None,
) -> AlbumInfo:
    page_url = album_url(settings, article_id, kind)
    _dbg(f"mzt._fetch_album_browser：打开 {page_url}")
    async with browser_context(headless=True, timeout_ms=timeout * 1000) as (
        _browser,
        _context,
        page,
    ):
        await goto(page, page_url, timeout_ms=timeout * 1000)
        result = await page.evaluate(_BROWSER_FETCH_JS, str(article_id))
    if not isinstance(result, dict):
        raise BrowserError("浏览器内解密返回格式异常")
    meta = result.get("meta") or {}
    names = result.get("names") or []
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise BrowserError("浏览器内解密结果不是字符串数组")

    actual_kind = kind
    path = str(meta.get("path") or "")
    if path.startswith("/beauty/"):
        actual_kind = "beauty"
    elif path.startswith("/photo/"):
        actual_kind = "photo"

    prefix = _dir_prefix(str(meta.get("first_image") or ""))
    urls = [prefix + name for name in names if name]
    total = int(meta.get("total") or 0)
    truncated = bool(total and len(urls) < total)
    browser_policy = str(meta.get("referrer_policy") or "origin")
    album = AlbumInfo(
        str(article_id),
        str(meta.get("title") or "未知标题"),
        str(meta.get("publish_time") or "未知时间"),
        _apply_limit(urls, settings),
        kind=actual_kind,
        total=total,
        truncated=truncated,
        referrer_policy=browser_policy,
        image_referer=(None if browser_policy == "no-referrer" else settings.mzt_site),
        page_url=page_url,
        source="browser",
    )
    _dbg(f"mzt._fetch_album_browser：浏览器解密得到 {len(urls)} 张")
    if progress is not None:
        try:
            await progress("解析", len(album.image_urls), total or len(album.image_urls))
        except Exception:
            _dbg("mzt._fetch_album_browser：progress 回调执行异常（已忽略）")
    return album


# --------------------------------------------------------------------------- #
# 对外：解析一个图集
# --------------------------------------------------------------------------- #
async def fetch_album(
    settings: PluginSettings,
    article_id: str | int,
    *,
    kind: str = DEFAULT_KIND,
    timeout: int = 60,
    progress: "ProgressCallback | None" = None,
) -> AlbumInfo:
    """解析单个写真/潮拍文章，返回全部可获取的图片 URL。

    优先走 HTTP 直连接口（无需浏览器）；失败时自动回退到 Playwright
    浏览器（页面内 fetch + WebCrypto 解密），以绕过 Cloudflare 对
    非浏览器流量的拦截。解析失败会抛出 :class:`BrowserError`。
    """
    article_id = str(article_id)
    kind = "beauty" if str(kind).lower() == "beauty" else "photo"
    _dbg(
        f"mzt.fetch_album：开始解析 id={article_id} kind={kind}（timeout={timeout}s）"
    )
    http_error: Exception | None = None
    try:
        album = await _fetch_album_http(
            settings, article_id, kind, timeout=timeout, progress=progress
        )
        if not album.image_urls:
            raise HttpError("接口没有返回任何图片")
        _dbg(f"mzt.fetch_album：HTTP 路径成功，共 {len(album.image_urls)} 张")
        return album
    except (HttpError, KkmztDecryptError, OSError) as exc:
        http_error = exc
        _dbg(f"mzt.fetch_album：HTTP 路径失败（{exc!r}），尝试浏览器兜底")
    except Exception as exc:  # 未预期异常也走兜底，避免功能直接不可用
        http_error = exc
        _dbg(f"mzt.fetch_album：HTTP 路径异常（{exc!r}），尝试浏览器兜底")

    if not getattr(settings, "mzt_browser_fallback", True):
        raise BrowserError(f"HTTP 解析失败且已禁用浏览器兜底：{http_error}")

    try:
        album = await _fetch_album_browser(
            settings, article_id, kind, timeout=timeout, progress=progress
        )
    except BrowserError:
        raise
    except Exception as exc:
        raise BrowserError(
            f"解析失败：直连接口错误（{http_error}），浏览器兜底也失败（{exc}）"
        ) from exc
    if not album.image_urls:
        raise BrowserError("没有解析到任何图片")
    _dbg(f"mzt.fetch_album：浏览器路径成功，共 {len(album.image_urls)} 张")
    return album


# --------------------------------------------------------------------------- #
# 文章 ID 列表采集（/更新写真ID）
# --------------------------------------------------------------------------- #
_THUMB_ID_PATTERN = re.compile(
    r'<a[^>]+class="[^"]*u-thumb-v[^"]*"[^>]*href="([^"]+)"', re.I
)
_THUMB_ID_PATTERN_REVERSED = re.compile(
    r'<a[^>]+href="([^"]+)"[^>]+class="[^"]*u-thumb-v[^"]*"', re.I
)


async def _collect_article_ids_http(
    settings: PluginSettings,
    existing: set[str],
    *,
    full: bool,
    timeout: int,
) -> list[str]:
    base_url = f"{settings.mzt_site}/photo/"
    discovered: list[str] = []
    found: set[str] = set(existing)
    page_no = 1
    while True:
        url = base_url if page_no == 1 else f"{base_url}page/{page_no}/"
        _dbg(f"mzt._collect_article_ids_http：采集第 {page_no} 页 {url}")
        page_html = await fetch_text(
            url,
            timeout=timeout,
            referer=settings.mzt_site + "/",
            headers=ARTICLE_HEADERS,
        )
        hrefs = [m.group(1) for m in _THUMB_ID_PATTERN.finditer(page_html)]
        if not hrefs:
            hrefs = [
                m.group(1) for m in _THUMB_ID_PATTERN_REVERSED.finditer(page_html)
            ]
        page_ids: list[str] = []
        for href in hrefs:
            match = URL_ID_PATTERN.search(href)
            if match and match.group(1).lower() == "photo":
                page_ids.append(match.group(2))
        _dbg(
            f"mzt._collect_article_ids_http：第 {page_no} 页 {len(page_ids)} 个ID"
        )
        if not page_ids:
            break
        if not full and all(item in found for item in page_ids):
            _dbg("mzt._collect_article_ids_http：整页已存在，增量模式停止")
            break
        for item in page_ids:
            if item not in found:
                found.add(item)
                discovered.append(item)
        page_no += 1
        if page_no > 500:
            _dbg("mzt._collect_article_ids_http：达到安全上限 500 页，停止")
            break
    return discovered


async def _collect_article_ids_browser(
    settings: PluginSettings,
    existing: set[str],
    *,
    full: bool,
    timeout: int,
) -> list[str]:
    base_url = f"{settings.mzt_site}/photo/"
    discovered: list[str] = []
    found: set[str] = set(existing)
    async with browser_context(headless=True, timeout_ms=timeout * 1000) as (
        _browser,
        _context,
        page,
    ):
        page_no = 1
        while True:
            url = base_url if page_no == 1 else f"{base_url}page/{page_no}/"
            _dbg(f"mzt._collect_article_ids_browser：采集第 {page_no} 页 {url}")
            try:
                await goto(page, url, timeout_ms=timeout * 1000)
            except BrowserError as exc:
                _dbg(f"mzt._collect_article_ids_browser：第 {page_no} 页打开失败：{exc}")
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
            _dbg(
                f"mzt._collect_article_ids_browser：第 {page_no} 页 {len(page_ids)} 个ID"
            )
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
    settings: PluginSettings,
    existing: set[str],
    *,
    full: bool = False,
    timeout: int = 60,
) -> list[str]:
    """爬取写真 ID 列表（先走 HTTP，失败回退 Playwright）。

    ``full=False`` 为增量模式：某一页的 ID 全部已存在时停止翻页。
    返回新发现的 ID（按页面顺序，去重）。
    """
    _dbg(
        f"mzt.collect_article_ids：开始 existing={len(existing)} full={full}"
    )
    try:
        return await _collect_article_ids_http(
            settings, existing, full=full, timeout=timeout
        )
    except Exception as exc:
        _dbg(f"mzt.collect_article_ids：HTTP 采集失败（{exc!r}），尝试浏览器兜底")

    try:
        return await _collect_article_ids_browser(
            settings, existing, full=full, timeout=timeout
        )
    except Exception as exc:
        raise BrowserError(f"采集写真ID失败：{exc}") from exc
