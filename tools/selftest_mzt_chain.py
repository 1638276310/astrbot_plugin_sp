#!/usr/bin/env python3
"""妹子图新链路自检（不启动 AstrBot、不联网）。

覆盖 9 项：
1. 指令文本 → 文章 ID（单个 / 多个 / 链接 / 未命中）
2. ID → APP 接口 URL 模板（内置基址 + 环境变量覆盖）
3. 包内 mTLS 证书与内置解密模块按相对路径可解析
4. TLS 服务端证书严格校验 + 系统 CA 链加载
5. WebUI 已移除必填项、代码内置默认值正确、默认直连且代理仅由环境变量显式开启
6. 解密入口被调用、图片 URL 数量正确（离线 fixture 真实解密）
7. 下载只使用解密返回的图片 URL（默认直连；显式 SP_MZT_PROXY 可透传）
8. 失败语义：Cloudflare 403 / 挑战页 / 空列表不误判成功
9. 源码无直连残留（HTML 页面、page.goto、裸 requests/httpx/aiohttp、绝对证书路径）

运行：python tools/selftest_mzt_chain.py
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import ssl
import sys
import types

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_ROOT = PLUGIN_ROOT.parent
sys.path.insert(0, str(WORKSPACE_ROOT))

FAILURES: list[str] = []


def _install_stubs() -> None:
    """裸 Python 下自检用最小桩：只补当前环境缺失的 astrbot / aiohttp 模块。"""
    try:
        import aiohttp  # type: ignore  # noqa: F401
    except Exception:
        aiohttp = types.ModuleType("aiohttp")

        class _ClientError(Exception):
            pass

        class _Dummy:
            def __init__(self, *args, **kwargs):
                pass

        aiohttp.ClientError = _ClientError
        aiohttp.ClientSession = _Dummy
        aiohttp.ClientTimeout = _Dummy
        aiohttp.TCPConnector = _Dummy
        sys.modules["aiohttp"] = aiohttp

    need_stub = False
    try:
        import astrbot.api.event  # type: ignore  # noqa: F401
        import astrbot.api.message_components  # type: ignore  # noqa: F401
    except Exception:
        need_stub = True
    if not need_stub:
        return

    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")

    class _Logger:
        def info(self, message, *args, **kwargs):
            """记录 info 级日志（桩实现，丢弃输出）。"""
            pass

        def warning(self, message, *args, **kwargs):
            """记录 warning 级日志（桩实现，丢弃输出）。"""
            pass

        def error(self, message, *args, **kwargs):
            """记录 error 级日志（桩实现，丢弃输出）。"""
            pass

    class _Filter:
        def command(self, *args, **kwargs):
            """返回装饰器桩，原样返回被装饰函数。"""
            def decorator(func):
                """原样返回被装饰的 handler。"""
                return func

            return decorator

    event_mod = types.ModuleType("astrbot.api.event")
    event_mod.AstrMessageEvent = type("AstrMessageEvent", (), {})
    event_mod.filter = _Filter()

    components = types.ModuleType("astrbot.api.message_components")
    for name in ("Image", "Node", "Nodes", "Plain", "Reply", "Video"):
        setattr(components, name, type(name, (), {"__init__": lambda self, *a, **k: None}))

    api.logger = _Logger()
    api.event = event_mod
    api.message_components = components
    astrbot.api = api
    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = api
    sys.modules["astrbot.api.event"] = event_mod
    sys.modules["astrbot.api.message_components"] = components


def _import_features_mzt():
    """跳过 features/__init__.py 的聚合导入，只加载 features/mzt.py。"""
    features_pkg = types.ModuleType("astrbot_plugin_sp.features")
    features_pkg.__path__ = [str(PLUGIN_ROOT / "features")]
    sys.modules.setdefault("astrbot_plugin_sp.features", features_pkg)
    import astrbot_plugin_sp.features.mzt as features_mzt  # type: ignore

    return features_mzt


def check(name: str, ok: bool, detail: str = "") -> None:
    """记录并打印单项自检结果，失败时计入 FAILURES。"""
    print(f"[selftest] {'PASS' if ok else 'FAIL'} {name}" + (f" | {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def _with_env(overrides: dict[str, str], fn):
    """临时设置环境变量执行 fn，结束后恢复（避免污染其它用例）。"""
    old = {key: os.environ.get(key) for key in overrides}
    try:
        for key, value in overrides.items():
            os.environ[key] = value
        return fn()
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_parse_ids(mzt) -> None:
    """用例 1：指令文本解析为文章 ID。"""
    cases = [
        ("写真馆 171317", [("171317", "photo")]),
        ("写真馆 171317 171318 共 2 张", [("171317", "photo"), ("171318", "photo")]),
        ("潮拍馆 191566", [("191566", "beauty")]),
        ("写真馆 171317,171318、171319", [("171317", "photo"), ("171318", "photo"), ("171319", "photo")]),
        (
            "写真馆 https://example.com/photo/1 https://example.com/beauty/2",
            [("1", "photo"), ("2", "beauty")],
        ),
        ("没有数字的指令", []),
    ]
    for text, expected in cases:
        got = mzt.parse_article_refs(text)
        check(f"提取ID {text!r}", got == expected, f"got={got}")
    check("裸 ID 兼容 parse_article_ref", mzt.parse_article_ref("171317") == ("171317", "photo"))
    check("空文本返回空列表", mzt.parse_article_refs("") == [])


def test_url_template(mzt) -> None:
    """用例 2：ID 映射到 APP 接口 URL 模板。"""
    settings = mzt.PluginSettings({})
    url = mzt._app_api_url(settings, "171317")
    expected = "https://api.iimzt.com/app/post/p?id=171317"
    check("ID → APP 接口 URL 模板", url == expected, url)
    custom = _with_env(
        {"SP_MZT_APP_API": "https://mirror.example/app/"},
        lambda: mzt.PluginSettings({}),
    )
    check(
        "环境变量可覆盖 APP 接口基址（尾斜杠归一）",
        mzt._app_api_url(custom, "1") == "https://mirror.example/app/post/p?id=1",
        mzt._app_api_url(custom, "1"),
    )


def test_builtin_defaults(mzt) -> None:
    """4 个原 WebUI 必填项必须已移除，且代码内置默认值可用。"""
    schema = json.loads((PLUGIN_ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    removed = ["mzt_app_api", "mzt_hd", "mzt_proxy", "mzt_app_cert"]
    check(
        "WebUI 已移除 4 个必填项",
        all(key not in schema for key in removed),
        f"remaining={[key for key in removed if key in schema]}",
    )
    settings = mzt.PluginSettings({})
    check(
        "内置 APP 接口基址",
        settings.mzt_app_api == "https://api.iimzt.com/app",
        settings.mzt_app_api,
    )
    check("内置高清开关默认关闭", settings.mzt_hd is False, str(settings.mzt_hd))
    check(
        "内置证书路径 assets/mzt_client.pem",
        settings.mzt_app_cert == "assets/mzt_client.pem",
        settings.mzt_app_cert,
    )
    check(
        "默认无代理（直连）",
        settings.mzt_proxy == "" and settings.mtb_proxy == "",
        f"mzt={settings.mzt_proxy!r} mtb={settings.mtb_proxy!r}",
    )
    override = _with_env(
        {
            "SP_MZT_APP_API": "https://mirror.example/app/",
            "SP_MZT_HD": "1",
            "SP_MZT_APP_CERT": "assets/mzt_client.pem",
            "SP_MZT_PROXY": "http://127.0.0.1:7897",
        },
        lambda: mzt.PluginSettings({}),
    )
    check(
        "环境变量可覆盖 APP/高清/证书/代理（可选，非必填）",
        override.mzt_app_api == "https://mirror.example/app"
        and override.mzt_hd is True
        and override.mzt_app_cert == "assets/mzt_client.pem"
        and override.mzt_proxy == "http://127.0.0.1:7897"
        and override.mtb_proxy == "http://127.0.0.1:7897",
        f"{override.mzt_app_api} hd={override.mzt_hd} "
        f"proxy={override.mzt_proxy!r} mtb={override.mtb_proxy!r}",
    )
    mtb_only = _with_env(
        {"SP_MTB_PROXY": "http://127.0.0.1:7898"},
        lambda: mzt.PluginSettings({}),
    )
    check(
        "SP_MTB_PROXY 可单独覆盖美图吧浏览器",
        mtb_only.mzt_proxy == "" and mtb_only.mtb_proxy == "http://127.0.0.1:7898",
        f"mzt={mtb_only.mzt_proxy!r} mtb={mtb_only.mtb_proxy!r}",
    )
    # 旧 WebUI 里残留的值必须不生效，避免“填错/忘填继续导致失败”。
    legacy = mzt.PluginSettings(
        {
            "mzt_app_api": "https://wrong.example/app/",
            "mzt_hd": True,
            "mzt_proxy": "http://wrong.example:8080",
            "mzt_app_cert": "../outside.pem",
        }
    )
    check(
        "旧 WebUI 配置已忽略（不参与运行）",
        legacy.mzt_app_api == "https://api.iimzt.com/app"
        and legacy.mzt_hd is False
        and legacy.mzt_proxy == ""
        and legacy.mzt_app_cert == "assets/mzt_client.pem"
        and sorted(legacy.legacy_mzt_keys)
        == sorted(["mzt_app_api", "mzt_hd", "mzt_proxy", "mzt_app_cert"]),
        f"api={legacy.mzt_app_api} hd={legacy.mzt_hd} "
        f"proxy={legacy.mzt_proxy!r} cert={legacy.mzt_app_cert} "
        f"legacy={legacy.legacy_mzt_keys}",
    )


def test_resources(mzt) -> None:
    """用例 3/4：证书、CA 链与解密模块可解析。"""
    ok, reason = mzt._validate_client_pem(mzt.ASSETS_CLIENT_CERT_PATH)
    check("mTLS 证书按包内相对路径可读", ok and reason == "ok", f"{mzt.ASSETS_CLIENT_CERT_PATH} {reason}")
    context = mzt._app_ssl_context(mzt.PluginSettings({}))
    check("mTLS 证书可加载为 SSLContext", context is not None)
    check(
        "TLS 服务端证书严格校验 + 加载系统 CA 链",
        context is not None
        and context.verify_mode == ssl.CERT_REQUIRED
        and context.check_hostname is True
        and len(context.get_ca_certs()) > 0,
        f"verify={getattr(context, 'verify_mode', None)} "
        f"check_hostname={getattr(context, 'check_hostname', None)} "
        f"ca={len(context.get_ca_certs()) if context else 0}",
    )
    check("内置解密模块存在", (PLUGIN_ROOT / "app_core" / "mzt_crypto.py").is_file())
    # 绝对路径配置必须被拒绝，候选仍然落在插件目录内
    absolute = _with_env(
        {"SP_MZT_APP_CERT": str(mzt.ASSETS_CLIENT_CERT_PATH)},
        lambda: mzt.PluginSettings({}),
    )
    candidates = mzt._cert_candidates(absolute)
    inside = all(str(path).startswith(str(PLUGIN_ROOT)) for path in candidates)
    check("证书候选拒绝绝对路径（仍在包内）", inside, f"candidates={[str(p) for p in candidates]}")


def test_decrypt_and_album(mzt) -> None:
    """用例 6：离线 fixture 真实解密并由 fetch_album 复用。"""
    fixture_path = PLUGIN_ROOT / "tools" / "fixtures" / "app_post_172250.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    decoded = mzt.decode_api_payload(fixture["data"], fixture["article_id"])
    check("离线 fixture 解密成功", decoded.get("scheme") == "app")
    images = decoded.get("images") or []
    check(
        "解密图片 URL 数量与 count 一致",
        bool(images) and len(images) == int(decoded.get("count") or 0),
        f"images={len(images)} count={decoded.get('count')}",
    )
    check("解密结果是完整图片 URL", str(images[0]).startswith("http"), str(images[0]).split("/")[2])

    calls: list[tuple[str, dict]] = []

    class FakeResponse:
        """成功响应桩，属性与真实抓取结果一致。"""
        status = 200
        headers = {"cf-ray": "0000000000000001", "content-type": "application/json"}
        text = json.dumps({"data": fixture["data"]})
        cf_ray = "0000000000000001"
        elapsed_ms = 1

    async def fake_fetch_response(url, **kwargs):
        """记录抓取调用并返回成功响应桩。"""
        calls.append((url, kwargs))
        return FakeResponse()

    mzt.fetch_response = fake_fetch_response
    mzt._app_ssl_context = lambda settings: object()
    mzt._LAST_FETCH_AT.clear()
    album = asyncio.run(mzt.fetch_album(mzt.PluginSettings({}), fixture["article_id"]))
    check("解密入口被调用且图片 URL 一致", album.image_urls == images, f"images={len(album.image_urls)}")
    check(
        "请求地址走 APP 接口模板",
        [url for url, _ in calls] == [f"https://api.iimzt.com/app/post/p?id={fixture['article_id']}"],
        str([url for url, _ in calls]),
    )
    check(
        "默认全链路 proxy=None（直连）",
        all(kwargs.get("proxy") is None for _, kwargs in calls),
        str(calls),
    )
    check("AlbumInfo 标记全量来源", album.source == "app" and album.total == int(decoded.get("count") or 0))

    # 显式设置 SP_MZT_PROXY 后，APP 接口请求必须带上同一个代理。
    proxy_value = "http://127.0.0.1:7897"
    proxy_settings = _with_env(
        {"SP_MZT_PROXY": proxy_value}, lambda: mzt.PluginSettings({})
    )
    calls.clear()
    mzt._LAST_FETCH_AT.clear()
    asyncio.run(mzt.fetch_album(proxy_settings, fixture["article_id"]))
    check(
        "SP_MZT_PROXY 透传到 APP 接口",
        len(calls) == 1 and calls[0][1].get("proxy") == proxy_value,
        str(calls),
    )


def test_download_uses_decrypted_urls(mzt) -> None:
    """用例 7：下载只使用解密 URL；默认直连，显式代理可透传。"""
    features_mzt = _import_features_mzt()
    fake_urls = ["https://ap.iimzt.com/a.jpg", "https://ap.iimzt.com/b.jpg?sign=x"]
    album = mzt.AlbumInfo("171317", "标题", "2026-01-01", list(fake_urls), total=2)
    captured: list[str] = []
    download_kwargs: list[dict] = []

    class FakeEvent:
        """消息事件桩，只提供文本/消息链结果方法。"""
        def plain_result(self, text):
            """返回文本消息桩元组。"""
            return ("plain", text)

        def chain_result(self, chain):
            """返回消息链桩元组。"""
            return ("chain", chain)

    feature = features_mzt.MztFeature()
    feature.settings = mzt.PluginSettings({"forward_as_node": False})
    feature.make_console_progress = lambda name: {"parse": None, "download": None}
    feature.image_chain = lambda paths: list(paths)

    async def fake_download(urls, **kwargs):
        """记录下载参数并返回本地路径桩。"""
        captured.extend(urls)
        download_kwargs.append(kwargs)
        return [f"/tmp/mzt_{index}.jpg" for index in range(len(urls))]

    async def fake_fetch_album(*args, **kwargs):
        """返回固定相册桩，绕过网络抓取。"""
        return album

    feature.download_images = fake_download
    features_mzt.fetch_album = fake_fetch_album

    async def consume():
        """收集 _send_mzt_album 的异步生成结果。"""
        return [item async for item in feature._send_mzt_album(FakeEvent(), "171317")]

    results = asyncio.run(consume())
    check("下载 URL = 解密返回的图片 URL", captured == fake_urls, str(captured))
    check(
        "默认图片下载 proxy=None（直连）",
        all(kwargs.get("proxy") is None for kwargs in download_kwargs),
        str(download_kwargs),
    )
    check("下载后发送消息链", any(item[0] == "chain" for item in results))

    # 显式代理：下载必须与 APP 接口使用同一个代理出口。
    proxy_value = "http://127.0.0.1:7897"
    feature.settings = _with_env(
        {"SP_MZT_PROXY": proxy_value},
        lambda: mzt.PluginSettings({"forward_as_node": False}),
    )
    download_kwargs.clear()
    captured.clear()
    asyncio.run(consume())
    check(
        "SP_MZT_PROXY 透传到图片下载",
        len(download_kwargs) == 1
        and download_kwargs[0].get("proxy") == proxy_value,
        str(download_kwargs),
    )


def test_failure_semantics(mzt) -> None:
    """用例 8：403/挑战页/空列表的失败语义。"""
    settings = mzt.PluginSettings({})
    mzt._app_ssl_context = lambda config: object()

    # 6.1 HTTP 403 + cf-ray：必须带 Cloudflare / Ray ID，并触发「出口探针」
    #     探针用的是不需要客户端证书的文章页；本用例里探针同样 403
    #     → 结论应是「出口 IP/区域被拦」，提示必须指向网络出口而不是证书/配置。
    calls: list[str] = []

    async def fetch_403(url, **kwargs):
        """模拟 HTTP 403 并记录请求地址。"""
        calls.append(url)
        raise mzt.HttpError(
            "HTTP 403", 403, url=url, cf_ray="a4f7cd28bf6de196",
            headers={"cf-ray": "a4f7cd28bf6de196", "content-type": "application/json"},
            elapsed_ms=380,
        )

    mzt.fetch_response = fetch_403
    mzt._LAST_FETCH_AT.clear()
    try:
        asyncio.run(mzt.fetch_album(settings, "171318"))
        text = ""
    except mzt.MztFetchError as exc:
        text = str(exc)
    check(
        "Cloudflare 403 → 结构化文案 + 出口探针指向网络出口/显式代理",
        "Cloudflare" in text
        and "a4f7cd28bf6de196" in text
        and len(calls) == 2  # APP 接口 1 次 + 文章页探针 1 次
        and any("/photo/" in url for url in calls)
        and "出口" in text
        and "SP_MZT_PROXY" in text,  # 指向环境变量，而不是 WebUI 配置
        f"text={text!r} calls={calls}",
    )

    # 6.1a 显式代理出口同样 403：提示必须让用户换代理节点，而不是继续换证书。
    calls.clear()
    proxy_settings = _with_env(
        {"SP_MZT_PROXY": "http://127.0.0.1:7897"},
        lambda: mzt.PluginSettings({}),
    )
    mzt._LAST_FETCH_AT.clear()
    try:
        asyncio.run(mzt.fetch_album(proxy_settings, "171322"))
        proxy_text = ""
    except mzt.MztFetchError as exc:
        proxy_text = str(exc)
    check(
        "代理出口同样被拦 → 提示更换干净节点",
        "代理出口同样被 Cloudflare 拦截" in proxy_text and len(calls) == 2,
        f"text={proxy_text!r} calls={calls}",
    )

    # 6.1b 探针 200（文章页正常、只有 APP 接口 403）→ 提示必须指向证书，
    #      不能再让人去配代理。这是「出口被拦」与「证书没生效」的二分点。
    calls.clear()

    class _SiteOk:
        status = 200
        headers = {"content-type": "text/html; charset=utf-8"}
        text = "<html><head><title>ok</title></head><body>article</body></html>"
        cf_ray = ""
        elapsed_ms = 50

    async def fetch_app403_site200(url, **kwargs):
        """模拟 APP 接口 403、文章页探针 200。"""
        calls.append(url)
        if "/photo/" in url:
            return _SiteOk()
        raise mzt.HttpError(
            "HTTP 403", 403, url=url, cf_ray="a4f7cd28bf6de196",
            headers={"cf-ray": "a4f7cd28bf6de196", "content-type": "text/html"},
            elapsed_ms=380,
        )

    mzt.fetch_response = fetch_app403_site200
    mzt._LAST_FETCH_AT.clear()
    try:
        asyncio.run(mzt.fetch_album(settings, "171321"))
        text = ""
    except mzt.MztFetchError as exc:
        text = str(exc)
    check(
        "探针 200 → 提示指向证书而非代理",
        "证书" in text and "SP_MZT_PROXY" not in text and len(calls) == 2,
        f"text={text!r} calls={calls}",
    )

    # 6.2 200 挑战页：不能被当作成功，也不能二次直连重试
    calls.clear()

    class FakeChallenge:
        """Cloudflare 挑战页响应桩。"""
        status = 200
        headers = {"cf-ray": "00000000000000aa", "content-type": "text/html"}
        text = "<html><head><title>Just a moment...</title></head><body>challenge</body></html>"
        cf_ray = "00000000000000aa"
        elapsed_ms = 120
    async def fetch_challenge(url, **kwargs):
        """模拟返回 200 Cloudflare 挑战页。"""
        calls.append(url)
        return FakeChallenge()

    mzt.fetch_response = fetch_challenge
    mzt._LAST_FETCH_AT.clear()
    try:
        asyncio.run(mzt.fetch_album(settings, "171319"))
        text = ""
    except mzt.MztFetchError as exc:
        text = str(exc)
    check(
        "挑战页不误判成功 / 不重复轮询",
        "Cloudflare" in text and len(calls) == 1,
        f"text={text!r} calls={len(calls)}",
    )

    # 6.3 解密返回空列表：明确报错，绝不静默改走其它链路
    calls.clear()

    async def fetch_ok(url, **kwargs):
        """模拟成功响应，供空列表用例使用。"""
        return _ok_response(url, calls, "dummy")

    mzt.fetch_response = fetch_ok
    mzt.decode_api_payload = lambda payload, article_id: {
        "scheme": "app", "images": [], "count": 0, "title": "", "date": "",
        "hd": False, "thumbs": False,
    }
    mzt._LAST_FETCH_AT.clear()
    try:
        asyncio.run(mzt.fetch_album(settings, "171320"))
        text = ""
    except mzt.MztFetchError as exc:
        text = str(exc)
    check(
        "解密空列表 → 明确报错且不降级直连",
        "解密返回图片列表为空" in text and len(calls) == 1,
        f"text={text!r} calls={len(calls)}",
    )


def _ok_response(url: str, calls: list[str], data: str):
    calls.append(url)

    class _Resp:
        status = 200
        headers = {"content-type": "application/json"}
        text = json.dumps({"data": data})
        cf_ray = ""
        elapsed_ms = 1

    return _Resp()


def test_app_request_shape(mzt) -> None:
    """APP 接口请求头必须与本地脚本 / 真实 APP 逐字节一致。

    回归点：历史实现用 ``browser_headers()`` 拼出 8 个头（Referer + 3×Sec-Fetch +
    X-Requested-With），是真实 APP 永远不会发生的组合；本用例锁死为 3 个头。
    """
    settings = mzt.PluginSettings({})
    headers = mzt._app_headers(settings)
    expected = {
        "User-Agent": "MztApp For Android Version/3.8.7",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    check(
        "APP 请求头 = 本地脚本 3 个头（无 Referer/Sec-Fetch/XHR）",
        headers == expected
        and "Referer" not in headers
        and "X-Requested-With" not in headers,
        f"headers={headers!r}",
    )


def test_no_direct_residue() -> None:
    """用例 9：源码无直连与自动代理/自动回退残留。"""
    scan_files = ["app_core/mzt.py", "features/mzt.py", "main.py", "tools/mzt_diag.py"]
    patterns = {
        "HTML 页面直连": re.compile(r"kkmzt\.com/photo"),
        "浏览器 page.goto": re.compile(r"page\.goto|\.goto\("),
        "裸 http 客户端": re.compile(r"(?<![\w.])requests\.|(?<![\w.])httpx\.|(?<![\w.])aiohttp\."),
        "绝对证书路径": re.compile(r"C:\\|/home/|/Users/|Path\(\s*[\"']/"),
    }
    hits: list[str] = []
    for relative in scan_files:
        text = (PLUGIN_ROOT / relative).read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), 1):
            for label, pattern in patterns.items():
                if pattern.search(line):
                    hits.append(f"{relative}:{number}: [{label}] {line.strip()}")
    check("源码无直连残留", not hits, "\n" + "\n".join(hits) if hits else "no hits")

    # 质量门禁：运行路径不得出现自动代理回退/固定出口 IP 痕迹。
    # 显式 SP_MZT_PROXY 是用户主动配置的兜底，不属于“自动代理”，允许存在。
    proxy_files = [
        "app_core/settings.py", "app_core/mzt.py", "features/mzt.py",
        "tools/mzt_diag.py",
    ]
    proxy_patterns = {
        "自动代理函数": re.compile("resolve_" + "auto_proxy|AUTO_" + "PROXY"),
        "固定出口IP": re.compile("171[.]121[.]254[.]" + "6"),
    }
    proxy_hits: list[str] = []
    for relative in proxy_files:
        text = (PLUGIN_ROOT / relative).read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), 1):
            for label, pattern in proxy_patterns.items():
                if pattern.search(line):
                    proxy_hits.append(f"{relative}:{number}: [{label}] {line.strip()}")
    check(
        "运行路径无自动代理/自动回退/固定出口IP痕迹",
        not proxy_hits,
        "\n" + "\n".join(proxy_hits) if proxy_hits else "no hits",
    )


def main() -> int:
    """运行全部自检组并返回退出码。"""
    _install_stubs()
    import astrbot_plugin_sp.app_core.mzt as mzt  # type: ignore

    # 打印插件自己的结构化日志，便于回归时看到解密链与 Cloudflare 关键信息
    mzt._log = lambda message: print(message)
    mzt._dbg = lambda message: print(f"[dbg] {message}")

    test_parse_ids(mzt)
    test_url_template(mzt)
    test_builtin_defaults(mzt)
    test_resources(mzt)
    test_app_request_shape(mzt)
    test_decrypt_and_album(mzt)
    test_download_uses_decrypted_urls(mzt)
    test_failure_semantics(mzt)
    test_no_direct_residue()

    if FAILURES:
        print(f"\n[selftest] FAILED {len(FAILURES)} 项: {FAILURES}")
        return 1
    print("\n[selftest] ALL 9 GROUPS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
