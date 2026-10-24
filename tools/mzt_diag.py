#!/usr/bin/env python3
"""妹子图（kkmzt / 美之图 APP）链路诊断（独立运行，不依赖 AstrBot / aiohttp / requests）。
    python tools/mzt_diag.py --id 171317
    python tools/mzt_diag.py --id 171317 --proxy http://127.0.0.1:7890 --json
检查出口 IP / DNS / 时间偏差 / mTLS 证书 / 文章页 / 网页接口 / APP 接口 / 图片 CDN
（状态码、cf-ray、耗时、解密张数）/ Playwright headless 是否被拦；输出脱敏 JSON。
"""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
PLUGIN_ROOT = Path(__file__).resolve().parent.parent
CERT_PATHS = (PLUGIN_ROOT / "assets" / "mzt_client.pem", PLUGIN_ROOT / "app_core" / "mzt_client.pem")
SITE, APP_API = "https://kkmzt.com", "https://api.iimzt.com/app/"
APP_UA = "MztApp For Android Version/3.8.7"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
LANG = "zh-CN,zh;q=0.9,en;q=0.8"
HTML = "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
JSON = "application/json, text/javascript, */*; q=0.01"
SECRET_RE = re.compile(r"(?i)\b(sign|token|key|auth|session|cookie|cf_clearance|expires|sig|secret)"
                       r"\s*=\s*([^&\s;\"'<>]+)")
TAG_RE, SCRIPT_RE = re.compile(r"(?s)<[^>]+>"), re.compile(r"(?is)<(script|style)\b.*?</\1>")
RAY_RE = re.compile(r"Ray ID:?\s*([0-9a-fA-F]{16})", re.I)
def clean(value: Any, limit: int = 200) -> str:
    text = SECRET_RE.sub(r"\1=***", SCRIPT_RE.sub(" ", str(value or "")))
    text = re.sub(r"\s+", " ", TAG_RE.sub(" ", text)).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"
def safe_url(url: str | None, limit: int = 160) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    p = urlsplit(raw)
    host = f"{p.hostname or ''}{':' + str(p.port) if p.port else ''}"
    ids = "&".join(f"id={v}" for k, v in (i.split("=", 1) for i in p.query.split("&") if "=" in i) if k.lower() == "id")
    out = (f"{p.scheme}://{host}{p.path}" if host else p.path) + (f"?{ids}" if ids else "")
    return out if len(out) <= limit else out[: limit - 1] + "…"
def mask_proxy(proxy: str | None) -> str:
    raw = str(proxy or "").strip()
    if not raw:
        return ""
    p = urlsplit(raw if "://" in raw else "http://" + raw)
    host = f"{p.hostname or ''}{':' + str(p.port) if p.port else ''}"
    return f"{p.scheme}://***:***@{host}" if p.username else f"{p.scheme}://{host}"
def hget(headers: Any, name: str) -> str:
    try:
        return next((str(v) for k, v in headers.items() if str(k).lower() == name.lower()), "")
    except Exception:
        return ""
def ray(headers: Any = None, body: str = "") -> str:
    match = re.search(r"[0-9a-fA-F]{16}", hget(headers, "cf-ray")) or RAY_RE.search(str(body or "")[:100_000])
    return match.group(0) if match else ""
def opener(proxy: str | None, context: ssl.SSLContext | None = None):
    handlers: list[Any] = [urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {})]
    handlers.append(urllib.request.HTTPSHandler(context=context or ssl.create_default_context()))
    return urllib.request.build_opener(*handlers)
def probe(url: str, *, headers: dict[str, str] | None = None, timeout: float = 15.0,
          proxy: str | None = None, context: ssl.SSLContext | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"url": safe_url(url), "status": None, "ok": False, "cf_ray": "", "elapsed_ms": 0,
                           "bytes": 0, "error": "", "_raw": b"", "_date": ""}
    started = time.monotonic()
    try:
        with opener(proxy, context).open(urllib.request.Request(url, headers=headers or {}),
                                         timeout=timeout) as resp:
            raw = resp.read(65536)
            out.update(status=int(getattr(resp, "status", 0) or 0), bytes=len(raw), _raw=raw, _date=hget(resp.headers, "date"),
                       cf_ray=ray(resp.headers, raw.decode("utf-8", "ignore")))
    except urllib.error.HTTPError as exc:
        raw = exc.read(65536) if hasattr(exc, "read") else b""
        out.update(status=int(exc.code), bytes=len(raw), _raw=raw, error=f"HTTP {exc.code}",
                   cf_ray=ray(exc.headers, raw.decode("utf-8", "ignore")))
    except (TimeoutError, socket.timeout):        out["error"] = "网络超时"
    except urllib.error.URLError as exc:
        out["error"] = clean(getattr(exc, "reason", exc), 160)
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {clean(exc, 120)}"
    out["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    out["ok"] = bool(out["status"] and 200 <= out["status"] < 300)
    return out
def shown(result: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in result.items() if not k.startswith("_")}
def headers(kind: str, site: str, referer: str = "") -> dict[str, str]:
    base = {"Accept-Language": LANG, "Referer": referer or site + "/"}
    if kind == "app":
        return {**base, "User-Agent": APP_UA, "Accept": JSON, "X-Requested-With": "XMLHttpRequest", "Sec-Fetch-Site": "cross-site"}
    if kind == "web":
        return {**base, "User-Agent": UA, "Accept": JSON, "X-Requested-With": "XMLHttpRequest"}
    return {**base, "User-Agent": UA, "Accept": HTML}
def check_cert(cert_arg: str) -> dict[str, Any]:
    candidates: list[tuple[str, Path]] = []
    if cert_arg:
        path = Path(cert_arg).expanduser()
        candidates.append(("--cert", path if path.is_absolute() else PLUGIN_ROOT / path))
    candidates += [("assets", CERT_PATHS[0]), ("旧路径 app_core", CERT_PATHS[1])]
    rows, selected = [], None
    for label, path in candidates:
        row = {"source": label, "path": str(path), "exists": path.is_file(), "size": 0, "has_certificate": False, "has_private_key": False, "state": "missing"}
        if path.is_file():
            try:
                data = path.read_bytes()
                row.update(size=len(data), has_certificate=b"BEGIN CERTIFICATE" in data,
                           has_private_key=b"PRIVATE KEY" in data)
                if row["has_certificate"] and row["has_private_key"]:
                    ssl.create_default_context().load_cert_chain(str(path))
                    row["state"] = "ok"
                    selected = selected or {"path": str(path), "source": label}
                else:
                    row["state"] = "cert-invalid"
            except (OSError, ssl.SSLError, ValueError) as exc:
                row["state"] = f"cert-load-failed:{type(exc).__name__}"
        rows.append(row)
        if selected:
            break
    return {"candidates": rows, "selected": selected, "ok": selected is not None}
def decode_count(result: dict[str, Any], article_id: str) -> dict[str, Any]:
    try:
        spec = importlib.util.spec_from_file_location(
            "mzt_crypto_diag", str(PLUGIN_ROOT / "app_core" / "mzt_crypto.py"))
        module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        decoded = module.decode_api_payload(
            str(json.loads(result["_raw"].decode("utf-8", "ignore")).get("data") or ""), article_id)
        images = [str(i) for i in (decoded.get("images") or [])]
        return {"scheme": decoded.get("scheme"), "images": len(images), "count": decoded.get("count"), "thumbs": bool(decoded.get("thumbs")), "_first": images[:1]}
    except Exception as exc:  # noqa: BLE001
        return {"decode_error": f"{type(exc).__name__}: {clean(exc, 80)}"} if result.get("_raw") else {}
def check_chain(site: str, app_api: str, article_id: str, kind: str,
                proxy: str | None, cert: dict[str, Any], timeout: float) -> dict[str, Any]:
    page_url = f"{site}/{'beauty' if kind == 'beauty' else 'photo'}/{article_id}"
    article = probe(page_url, headers=headers("article", site), timeout=timeout, proxy=proxy)
    body = article["_raw"].decode("utf-8", "ignore")
    title = re.search(r"<h1[^>]*>(.*?)</h1>", body, re.S | re.I)
    figure = re.search(r"<figure[^>]*>(.*?)</figure>", body, re.S | re.I)
    img = re.search(r"<img[^>]*>", figure.group(1) if figure else body, re.I)
    src = re.search(r'\bsrc="([^"]+)"', img.group(0), re.I) if img else None
    progress = re.search(r'<progress[^>]*\bmax="(\d+)"[^>]*>', body, re.I)
    first_image = src.group(1) if src else ""
    meta = {"title": clean(title.group(1), 80) if title else "", "total_from_progress": int(progress.group(1)) if progress else 0}
    web = probe(f"{site}/app/post/p?id={article_id}", headers=headers("web", site, page_url),
                timeout=timeout, proxy=proxy)
    web_decoded = decode_count(web, article_id)
    app_result: dict[str, Any] = {"skipped": True, "reason": "mzt_app_api 为空"}
    app_decoded: dict[str, Any] = {}
    if app_api and cert.get("selected"):
        mtls = ssl.create_default_context()
        mtls.load_cert_chain(cert["selected"]["path"])
        app_result = probe(f"{app_api}/post/p?id={article_id}", headers=headers("app", site),
                           timeout=timeout, proxy=proxy, context=mtls)
        app_decoded = decode_count(app_result, article_id)
    elif app_api:
        app_result = {"skipped": True, "reason": "客户端证书不可用"}
    image_url = next(iter(app_decoded.get("_first") or web_decoded.get("_first") or [first_image]), "")
    if image_url:
        referer = "https://weibo.com/" if app_decoded.get("thumbs") else site + "/"
        image_result = {**shown(probe(image_url, headers={"User-Agent": UA, "Referer": referer,
                                                          "Accept": "image/*,*/*;q=0.8"},
                                       timeout=timeout, proxy=proxy)), "referer": referer}
    else:
        image_result = {"skipped": True, "reason": "没有可用图片 URL"}
    for decoded in (app_decoded, web_decoded):
        decoded.pop("_first", None)
    return {"article": {**shown(article), "meta": meta}, "web_api": {**shown(web), "decode": web_decoded},
            "app_api": app_result if app_result.get("skipped") else {**shown(app_result), "decode": app_decoded},
            "image_cdn": image_result}
def check_egress(proxy: str | None, timeout: float) -> dict[str, Any]:
    trace = probe("https://www.cloudflare.com/cdn-cgi/trace", headers={"User-Agent": UA},
                  timeout=timeout, proxy=proxy)
    data: dict[str, Any] = {"trace": shown(trace), "ip": "", "loc": "", "colo": "", "warp": "", "server_time_skew_seconds": None}
    for line in trace["_raw"].decode("utf-8", "ignore").splitlines():
        key, _, value = line.partition("=")
        if key in ("ip", "loc", "colo", "warp"):
            data[key] = value.strip()
    if not data["ip"]:
        fallback = probe("https://api.ipify.org?format=json", headers={"User-Agent": UA},
                         timeout=timeout, proxy=proxy)
        try:
            data["ip"] = str(json.loads(fallback["_raw"].decode("utf-8", "ignore")).get("ip") or "")
        except Exception:  # noqa: BLE001
            pass
    try:
        server = parsedate_to_datetime(trace["_date"]) if trace["_date"] else None
        if server:
            data["server_time_skew_seconds"] = round((server.replace(
                tzinfo=server.tzinfo or timezone.utc) - datetime.now(timezone.utc)).total_seconds(), 2)
    except Exception:  # noqa: BLE001
        pass
    return data
def check_dns(hosts: list[str]) -> dict[str, Any]:
    def one(host: str) -> dict[str, Any]:
        try:
            infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            return {"ok": True, "addresses": sorted({str(i[4][0]) for i in infos})[:6]}
        except OSError as exc:
            return {"ok": False, "error": clean(exc, 120)}
    return {host: one(host) for host in hosts if host}
def check_playwright(page_url: str, proxy: str | None, timeout: float) -> dict[str, Any]:
    out: dict[str, Any] = {"available": False, "launched": False, "status": None, "title": "", "blocked": None, "challenge": False, "cf_ray": "", "elapsed_ms": 0, "error": ""}
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"playwright 不可用（{type(exc).__name__}）"
        return out
    out["available"] = True
    started = time.monotonic()
    try:
        with sync_playwright() as pw:
            launch: dict[str, Any] = {"headless": True, "timeout": min(30_000, int(timeout * 1000)), "args": [
                "--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled", "--lang=zh-CN"]}
            if proxy:
                launch["proxy"] = {"server": proxy}
            browser = pw.chromium.launch(**launch)
            context = browser.new_context(user_agent=UA, locale="zh-CN", ignore_https_errors=True,
                                          extra_http_headers={"Accept-Language": LANG})
            page = context.new_page()
            page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
            response = page.goto(page_url, wait_until="domcontentloaded", timeout=int(timeout * 1000))
            title, content = page.title(), str(page.content() or "")[:300_000]
            lower, tl = content.lower(), str(title or "").lower()
            out.update(launched=True, status=getattr(response, "status", None), title=clean(title, 80),
                       cf_ray=ray(getattr(response, "headers", None) if response else None, content))
            out["challenge"] = "just a moment" in tl or "challenge-platform" in lower
            out["blocked"] = ("you have been blocked" in lower or "cf-error" in lower
                              or (out["status"] in (403, 503) and bool(out["cf_ray"])))
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {clean(exc, 160)}"
    out["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    return out
def likely_cause(report: dict[str, Any]) -> str:
    article, app_api = report["http_chain"]["article"], report["http_chain"]["app_api"]
    status = article.get("status")
    cause = ("直连链路正常" if status == 200 and app_api.get("status") == 200 else
             "文章页被 Cloudflare 拦截，优先配置 mzt_proxy（住宅/可用代理）" if status in (403, 503) or article.get("cf_ray") else
             "APP 接口被 Cloudflare 403：检查 mzt_app_cert 证书是否随包上传/路径正确" if status == 200 and app_api.get("status") in (403, 503) else
             "链路异常，请结合各阶段 error/status 判断")
    cause += "" if report["certificates"].get("ok") else "；客户端证书未成功加载"
    if report["playwright"].get("available") and report["playwright"].get("blocked"):
        cause += "；headless Chromium 也被 Cloudflare 拦截（浏览器兜底不可用）"
    return cause
def run(argv: list[str] | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser(description="妹子图 403 / Cloudflare / 代理 / 证书诊断")
    parser.add_argument("--id", default="171317", help="文章 ID")
    parser.add_argument("--kind", choices=("photo", "beauty"), default="photo")
    parser.add_argument("--site", default=SITE)
    parser.add_argument("--app-api", default=APP_API, help="APP 接口基址；空字符串跳过")
    parser.add_argument("--proxy", default=os.environ.get("MZT_PROXY") or "", help="http://127.0.0.1:7890")
    parser.add_argument("--cert", default="", help="mTLS 客户端证书路径（默认自动查找）")
    parser.add_argument("--timeout", type=float, default=15.0, help="单项请求超时（秒）")
    parser.add_argument("--no-browser", action="store_true", help="跳过 Playwright 探测")
    parser.add_argument("--json", action="store_true", help="输出 JSON（默认即 JSON）")
    args = parser.parse_args(argv)
    site = str(args.site or SITE).strip().rstrip("/") or SITE
    app_api = str(args.app_api or "").strip().rstrip("/")
    proxy = str(args.proxy or "").strip() or None
    page_url = f"{site}/{args.kind}/{args.id}"
    cert = check_cert(args.cert or "")
    now = datetime.now(timezone.utc)
    report: dict[str, Any] = {
        "generated_at": now.isoformat(timespec="seconds"),
        "target": {"id": str(args.id), "kind": args.kind, "site": site, "page_url": safe_url(page_url),
                   "app_api": safe_url(app_api), "proxy": mask_proxy(proxy), "proxy_enabled": bool(proxy)},
        "environment": {"python": sys.version.split()[0], "platform": sys.platform, "plugin_root": str(PLUGIN_ROOT),
                        "clock": {"utc": now.isoformat(timespec="seconds"),
                                  "local": datetime.now().astimezone().isoformat(timespec="seconds")},
                        "proxy_environment": {k: mask_proxy(os.environ.get(k)) for k in (
                            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY") if os.environ.get(k)}},
        "dns": check_dns([urlsplit(site).hostname or "", urlsplit(app_api).hostname if app_api else "", "ap.iimzt.com"]),
        "egress": check_egress(proxy, args.timeout), "certificates": cert}
    report["http_chain"] = check_chain(site, app_api, str(args.id), args.kind, proxy, cert, args.timeout)
    report["playwright"] = ({"skipped": True, "reason": "--no-browser"} if args.no_browser
                            else check_playwright(page_url, proxy, args.timeout))
    report["summary"] = {"egress_ip": report["egress"].get("ip", ""), "article_status": report["http_chain"]["article"].get("status"),
                         "web_api_status": report["http_chain"]["web_api"].get("status"),
                         "app_api_status": report["http_chain"]["app_api"].get("status", "skipped"),
                         "cert_ok": bool(cert.get("ok")), "playwright_blocked": report["playwright"].get("blocked")}
    report["summary"]["likely_cause"] = likely_cause(report)
    return report
def main(argv: list[str] | None = None) -> int:
    try:
        print(json.dumps(run(argv), ensure_ascii=False, indent=2))
        return 0
    except SystemExit as exc:  # argparse 的 --help / 参数错误
        return int(exc.code or 0)
    except Exception as exc:  # noqa: BLE001
        print(f"诊断脚本内部错误（{type(exc).__name__}）：{clean(exc, 300)}", file=sys.stderr)
        return 2
if __name__ == "__main__":
    raise SystemExit(main())
