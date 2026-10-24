#!/usr/bin/env python3
"""妹子图新链路自检（不启动 AstrBot、不联网）。

覆盖 7 项：
1. 指令文本 → 文章 ID（单个 / 多个 / 链接 / 未命中）
2. ID → APP 接口 URL 模板
3. 包内 mTLS 证书与内置解密模块按相对路径可解析
4. 解密入口被调用、图片 URL 数量正确（离线 fixture 真实解密）
5. 下载只使用解密返回的图片 URL
6. 失败语义：Cloudflare 403 / 挑战页 / 空列表不误判成功、不改走直连
7. 源码无直连残留（HTML 页面、page.goto、裸 requests/httpx/aiohttp、绝对证书路径）

运行：python tools/selftest_mzt_chain.py
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import types
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_ROOT = PLUGIN_ROOT.parent
sys.path.insert(0, str(WORKSPACE_ROOT))

FAILURES: list[str] = []


def _install_stubs() -> None:
    """裸 Python 下自检用最小桩：只补当前环境缺失的 astrbot / aiohttp 模块。"""
    try:
        import aiohttp  # noqa: F401
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
        import astrbot.api.event  # noqa: F401
        import astrbot.api.message_components  # noqa: F401
    except Exception:
        need_stub = True
    if not need_stub:
        return

    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")

    class _Logger:
        def info(self, message, *args, **kwargs):
            pass

        def warning(self, message, *args, **kwargs):
            pass

        def error(self, message, *args, **kwargs):
            pass

    class _Filter:
        def command(self, *args, **kwargs):
            def decorator(func):
                return func

            return decorator

    event_mod = types.ModuleType("astrbot.api.event")
    event_mod.AstrMessageEvent = type("AstrMessageEvent", (), {})
    event_mod.filter = _Filter()

    components = types.ModuleType("astrbot.api.message_components")
    for name in ("Image", "Node", "Nodes", "Plain", "Video"):
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
    print(f"[selftest] {'PASS' if ok else 'FAIL'} {name}" + (f" | {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def test_parse_ids(mzt) -> None:
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
    settings = mzt.PluginSettings({})
    url = mzt._app_api_url(settings, "171317")
    expected = "https://api.iimzt.com/app/post/p?id=171317"
    check("ID → APP 接口 URL 模板", url == expected, url)
    custom = mzt.PluginSettings({"mzt_app_api": "https://api.iimzt.com/app/"})
    check("配置基址尾斜杠归一", mzt._app_api_url(custom, "1").endswith("/app/post/p?id=1"))


def test_resources(mzt) -> None:
    ok, reason = mzt._validate_client_pem(mzt.ASSETS_CLIENT_CERT_PATH)
    check("mTLS 证书按包内相对路径可读", ok and reason == "ok", f"{mzt.ASSETS_CLIENT_CERT_PATH} {reason}")
    context = mzt._app_ssl_context(mzt.PluginSettings({}))
    check("mTLS 证书可加载为 SSLContext", context is not None)
    check("内置解密模块存在", (PLUGIN_ROOT / "app_core" / "mzt_crypto.py").is_file())
    # 绝对路径配置必须被拒绝，候选仍然落在插件目录内
    absolute = mzt.PluginSettings({"mzt_app_cert": str(mzt.ASSETS_CLIENT_CERT_PATH)})
    candidates = mzt._cert_candidates(absolute)
    inside = all(str(path).startswith(str(PLUGIN_ROOT)) for path in candidates)
    check("证书候选拒绝绝对路径（仍在包内）", inside, f"candidates={[str(p) for p in candidates]}")


def test_decrypt_and_album(mzt) -> None:
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

    calls: list[str] = []

    class FakeResponse:
        status = 200
        headers = {"cf-ray": "0000000000000001", "content-type": "application/json"}
        text = json.dumps({"data": fixture["data"]})
        cf_ray = "0000000000000001"
        elapsed_ms = 1

    async def fake_fetch_response(url, **kwargs):
        calls.append(url)
        return FakeResponse()

    mzt.fetch_response = fake_fetch_response
    mzt._app_ssl_context = lambda settings: object()
    mzt._LAST_FETCH_AT.clear()
    album = asyncio.run(mzt.fetch_album(mzt.PluginSettings({}), fixture["article_id"]))
    check("解密入口被调用且图片 URL 一致", album.image_urls == images, f"images={len(album.image_urls)}")
    check("请求地址走 APP 接口模板", calls == [f"https://api.iimzt.com/app/post/p?id={fixture['article_id']}"], str(calls))
    check("AlbumInfo 标记全量来源", album.source == "app" and album.total == int(decoded.get("count") or 0))


def test_download_uses_decrypted_urls(mzt) -> None:
    features_mzt = _import_features_mzt()
    fake_urls = ["https://ap.iimzt.com/a.jpg", "https://ap.iimzt.com/b.jpg?sign=x"]
    album = mzt.AlbumInfo("171317", "标题", "2026-01-01", list(fake_urls), total=2)
    captured: list[str] = []

    class FakeEvent:
        def plain_result(self, text):
            return ("plain", text)

        def chain_result(self, chain):
            return ("chain", chain)

    feature = features_mzt.MztFeature()
    feature.settings = mzt.PluginSettings({"forward_as_node": False, "mzt_proxy": ""})
    feature.make_console_progress = lambda name: {"parse": None, "download": None}
    feature.image_chain = lambda paths: list(paths)

    async def fake_download(urls, **kwargs):
        captured.extend(urls)
        return [f"/tmp/mzt_{index}.jpg" for index in range(len(urls))]

    async def fake_fetch_album(*args, **kwargs):
        return album

    feature.download_images = fake_download
    features_mzt.fetch_album = fake_fetch_album

    async def consume():
        return [item async for item in feature._send_mzt_album(FakeEvent(), "171317")]

    results = asyncio.run(consume())
    check("下载 URL = 解密返回的图片 URL", captured == fake_urls, str(captured))
    check("下载后发送消息链", any(item[0] == "chain" for item in results))


def test_failure_semantics(mzt) -> None:
    settings = mzt.PluginSettings({})
    mzt._app_ssl_context = lambda config: object()

    # 6.1 HTTP 403 + cf-ray：必须带 Cloudflare / Ray ID 文案，且只请求一次
    calls: list[str] = []

    async def fetch_403(url, **kwargs):
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
        "Cloudflare 403 → 结构化失败文案",
        "Cloudflare" in text and "a4f7cd28bf6de196" in text and len(calls) == 1,
        f"text={text!r} calls={len(calls)}",
    )

    # 6.2 200 挑战页：不能被当作成功，也不能二次直连重试
    calls.clear()

    class FakeChallenge:
        status = 200
        headers = {"cf-ray": "00000000000000aa", "content-type": "text/html"}
        text = "<html><head><title>Just a moment...</title></head><body>challenge</body></html>"
        cf_ray = "00000000000000aa"
        elapsed_ms = 120
    async def fetch_challenge(url, **kwargs):
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


def test_no_direct_residue() -> None:
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


def main() -> int:
    _install_stubs()
    import astrbot_plugin_sp.app_core.mzt as mzt

    # 打印插件自己的结构化日志，便于回归时看到解密链与 Cloudflare 关键信息
    mzt._log = lambda message: print(message)
    mzt._dbg = lambda message: print(f"[dbg] {message}")

    test_parse_ids(mzt)
    test_url_template(mzt)
    test_resources(mzt)
    test_decrypt_and_album(mzt)
    test_download_uses_decrypted_urls(mzt)
    test_failure_semantics(mzt)
    test_no_direct_residue()

    if FAILURES:
        print(f"\n[selftest] FAILED {len(FAILURES)} 项: {FAILURES}")
        return 1
    print("\n[selftest] ALL 7 GROUPS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
