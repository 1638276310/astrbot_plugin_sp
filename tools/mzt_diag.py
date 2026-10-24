#!/usr/bin/env python3
"""妹子图 APP 接口（mTLS + 内置解密）链路诊断（独立运行，不依赖 AstrBot）。

    python tools/mzt_diag.py --id 171317
    python tools/mzt_diag.py --id 171317 --proxy http://127.0.0.1:7890 --json

检查出口 IP / DNS / 包内证书（相对路径）/ APP 接口状态与内置解密张数 / 图片 CDN，
输出脱敏 JSON。插件运行时不再访问站点 HTML 页面，因此这里也不探测网页/浏览器链路。
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
CERT_CANDIDATES = (
    PLUGIN_ROOT / "assets" / "mzt_client.pem",
    PLUGIN_ROOT / "app_core" / "mzt_client.pem",
)
SITE = "https://kkmzt.com"
APP_API = "https://api.iimzt.com/app/"
APP_UA = "MztApp For Android Version/3.8.7"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
LANG = "zh-CN,zh;q=0.9,en;q=0.8"
JSON_ACCEPT = "application/json, text/javascript, */*; q=0.01"
SECRET_RE = re.compile(
    r"(?i)\b(sign|token|key|auth|session|cookie|cf_clearance|expires|sig|secret)"
    r"\s*=\s*([^&\s;\"'<>]+)"
)
TAG_RE = re.compile(r"(?s)<[^>]+>")
SCRIPT_RE = re.compile(r"(?is)<(script|style)\b.*?</\1>")
RAY_RE = re.compile(r"Ray ID:?\s*([0-9a-fA-F]{16})", re.I)


def clean(value: Any, limit: int = 200) -> str:
    text = SECRET_RE.sub(r"\1=***", SCRIPT_RE.sub(" ", str(value or "")))
    text = re.sub(r"\s+", " ", TAG_RE.sub(" ", text)).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def safe_url(url: str | None, limit: int = 160) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    parts = urlsplit(raw)
    host = f"{parts.hostname or ''}{':' + str(parts.port) if parts.port else ''}"
    ids = "&".join(
        f"{key}={value}" for key, value in (item.split("=", 1) for item in parts.query.split("&") if "=" in item)
        if key.lower() == "id"
    )
    out = (f"{parts.scheme}://{host}{parts.path}" if host else parts.path) + (f"?{ids}" if ids else "")
    return out if len(out) <= limit else out[: limit - 1] + "…"


def mask_proxy(proxy: str | None) -> str:
    raw = str(proxy or "").strip()
    if not raw:
        return ""
    parts = urlsplit(raw if "://" in raw else "http://" + raw)
    host = f"{parts.hostname or ''}{':' + str(parts.port) if parts.port else ''}"
    return f"{parts.scheme}://***:***@{host}" if parts.username else f"{parts.scheme}://{host}"


def hget(headers: Any, name: str) -> str:
    try:
        return next((str(value) for key, value in headers.items() if str(key).lower() == name.lower()), "")
    except Exception:
        return ""


def ray(headers: Any = None, body: str = "") -> str:
    match = re.search(r"[0-9a-fA-F]{16}", hget(headers, "cf-ray")) or RAY_RE.search(str(body or "")[:100_000])
    return match.group(0) if match else ""


def opener(proxy: str | None, context: ssl.SSLContext | None = None):
    handlers: list[Any] = [urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {})]
    handlers.append(urllib.request.HTTPSHandler(context=context or ssl.create_default_context()))
    return urllib.request.build_opener(*handlers)


def probe(
    url: str, *, headers: dict[str, str] | None = None, timeout: float = 15.0,
    proxy: str | None = None, context: ssl.SSLContext | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "url": safe_url(url), "status": None, "ok": False, "cf_ray": "",
        "elapsed_ms": 0, "bytes": 0, "error": "", "_raw": b"", "_date": "",
    }
    started = time.monotonic()
    try:
        with opener(proxy, context).open(urllib.request.Request(url, headers=headers or {}), timeout=timeout) as resp:
            raw = resp.read(65536)
            out.update(
                status=int(getattr(resp, "status", 0) or 0), bytes=len(raw), _raw=raw,
                _date=hget(resp.headers, "date"),
                cf_ray=ray(resp.headers, raw.decode("utf-8", "ignore")),
            )
    except urllib.error.HTTPError as exc:
        raw = exc.read(65536) if hasattr(exc, "read") else b""
        out.update(
            status=int(exc.code), bytes=len(raw), _raw=raw, error=f"HTTP {exc.code}",
            cf_ray=ray(exc.headers, raw.decode("utf-8", "ignore")),
        )
    except (TimeoutError, socket.timeout):
        out["error"] = "网络超时"
    except urllib.error.URLError as exc:
        out["error"] = clean(getattr(exc, "reason", exc), 160)
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {clean(exc, 120)}"
    out["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    out["ok"] = bool(out["status"] and 200 <= out["status"] < 300)
    return out


def shown(result: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in result.items() if not key.startswith("_")}


def app_headers() -> dict[str, str]:
    return {
        "User-Agent": APP_UA,
        "Accept": JSON_ACCEPT,
        "Accept-Language": LANG,
        "Referer": SITE + "/",
        "X-Requested-With": "XMLHttpRequest",
        "Sec-Fetch-Site": "cross-site",
    }


def check_cert() -> dict[str, Any]:
    """只检查包内相对路径候选；不读取证书内容、不支持绝对路径。"""
    rows, selected = [], None
    for label, path in (("assets", CERT_CANDIDATES[0]), ("旧路径 app_core", CERT_CANDIDATES[1])):
        row: dict[str, Any] = {
            "source": label, "path": str(path.relative_to(PLUGIN_ROOT)), "exists": path.is_file(),
            "size": 0, "has_certificate": False, "has_private_key": False, "state": "missing",
        }
        if path.is_file():
            try:
                data = path.read_bytes()
                row.update(
                    size=len(data),
                    has_certificate=b"BEGIN CERTIFICATE" in data,
                    has_private_key=b"PRIVATE KEY" in data,
                )
                if row["has_certificate"] and row["has_private_key"]:
                    ssl.create_default_context().load_cert_chain(str(path))
                    row["state"] = "ok"
                    selected = selected or {"path": str(path.relative_to(PLUGIN_ROOT)), "source": label}
                else:
                    row["state"] = "cert-invalid"
            except (OSError, ssl.SSLError, ValueError) as exc:
                row["state"] = f"cert-load-failed:{type(exc).__name__}"
        rows.append(row)
        if selected:
            break
    return {"candidates": rows, "selected": selected, "ok": selected is not None}


def decode_result(result: dict[str, Any], article_id: str) -> dict[str, Any]:
    try:
        spec = importlib.util.spec_from_file_location(
            "mzt_crypto_diag", str(PLUGIN_ROOT / "app_core" / "mzt_crypto.py")
        )
        module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        decoded = module.decode_api_payload(
            str(json.loads(result["_raw"].decode("utf-8", "ignore")).get("data") or ""), article_id
        )
        images = [str(url) for url in (decoded.get("images") or [])]
        return {
            "scheme": decoded.get("scheme"), "images": len(images),
            "count": decoded.get("count"), "thumbs": bool(decoded.get("thumbs")),
            "_first": images[:1],
        }
    except Exception as exc:  # noqa: BLE001
        return {"decode_error": f"{type(exc).__name__}: {clean(exc, 80)}"} if result.get("_raw") else {}


def check_app_api(app_api: str, article_id: str, proxy: str | None, cert: dict[str, Any], timeout: float) -> dict[str, Any]:
    if not app_api:
        return {"skipped": True, "reason": "app_api 为空"}
    if not cert.get("selected"):
        return {"skipped": True, "reason": "客户端证书不可用"}
    mtls = ssl.create_default_context()
    mtls.load_cert_chain(str(PLUGIN_ROOT / cert["selected"]["path"]))
    result = probe(f"{app_api}/post/p?id={article_id}", headers=app_headers(), timeout=timeout, proxy=proxy, context=mtls)
    decoded = decode_result(result, article_id)
    first = next(iter(decoded.pop("_first", []) or []), "")
    if first:
        referer = "https://weibo.com/" if decoded.get("thumbs") else ""
        image_headers = {"User-Agent": UA, "Accept": "image/*,*/*;q=0.8"}
        if referer:
            image_headers["Referer"] = referer
        image = {**shown(probe(first, headers=image_headers, timeout=timeout, proxy=proxy)), "referer": referer or "无"}
    else:
        image = {"skipped": True, "reason": "没有可用图片 URL"}
    return {**shown(result), "decode": decoded, "image_cdn": image}


def check_egress(proxy: str | None, timeout: float) -> dict[str, Any]:
    trace = probe("https://www.cloudflare.com/cdn-cgi/trace", headers={"User-Agent": UA}, timeout=timeout, proxy=proxy)
    data: dict[str, Any] = {
        "trace": shown(trace), "ip": "", "loc": "", "colo": "", "warp": "",
        "server_time_skew_seconds": None,
    }
    for line in trace["_raw"].decode("utf-8", "ignore").splitlines():
        key, _, value = line.partition("=")
        if key in ("ip", "loc", "colo", "warp"):
            data[key] = value.strip()
    if not data["ip"]:
        fallback = probe("https://api.ipify.org?format=json", headers={"User-Agent": UA}, timeout=timeout, proxy=proxy)
        try:
            data["ip"] = str(json.loads(fallback["_raw"].decode("utf-8", "ignore")).get("ip") or "")
        except Exception:  # noqa: BLE001
            pass
    try:
        server = parsedate_to_datetime(trace["_date"]) if trace["_date"] else None
        if server:
            data["server_time_skew_seconds"] = round(
                (server.replace(tzinfo=server.tzinfo or timezone.utc) - datetime.now(timezone.utc)).total_seconds(), 2
            )
    except Exception:  # noqa: BLE001
        pass
    return data


def check_dns(hosts: list[str]) -> dict[str, Any]:
    def one(host: str) -> dict[str, Any]:
        try:
            infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            return {"ok": True, "addresses": sorted({str(info[4][0]) for info in infos})[:6]}
        except OSError as exc:
            return {"ok": False, "error": clean(exc, 120)}

    return {host: one(host) for host in hosts if host}


def likely_cause(report: dict[str, Any]) -> str:
    app = report["app_api"]
    status = app.get("status")
    decode = app.get("decode") or {}
    if status == 200 and decode.get("images"):
        cause = "APP 接口（mTLS）+ 内置解密链路正常"
    elif app.get("skipped"):
        cause = f"APP 接口未探测：{app.get('reason')}"
    elif status in (403, 503) or app.get("cf_ray"):
        cause = "APP 接口被 Cloudflare 拦截：确认 mTLS 证书有效，必要时配置 mzt_proxy 更换出口"
    else:
        cause = "APP 接口异常，请结合 status/error 判断"
    if not report["certificates"].get("ok"):
        cause += "；客户端证书未成功加载"
    return cause


def run(argv: list[str] | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser(description="妹子图 APP 接口（mTLS + 内置解密）诊断")
    parser.add_argument("--id", default="171317", help="文章 ID")
    parser.add_argument("--app-api", default=APP_API, help="APP 接口基址")
    parser.add_argument("--proxy", default=os.environ.get("MZT_PROXY") or "", help="http://127.0.0.1:7890")
    parser.add_argument("--timeout", type=float, default=15.0, help="单项请求超时（秒）")
    parser.add_argument("--json", action="store_true", help="输出 JSON（默认即 JSON）")
    args = parser.parse_args(argv)
    app_api = str(args.app_api or "").strip().rstrip("/")
    proxy = str(args.proxy or "").strip() or None
    cert = check_cert()
    app_host = urlsplit(app_api).hostname if app_api else ""
    now = datetime.now(timezone.utc)
    report: dict[str, Any] = {
        "generated_at": now.isoformat(timespec="seconds"),
        "target": {
            "id": str(args.id), "app_api": safe_url(app_api), "proxy": mask_proxy(proxy),
            "proxy_enabled": bool(proxy),
        },
        "environment": {
            "python": sys.version.split()[0], "platform": sys.platform,
            "plugin_root": str(PLUGIN_ROOT),
            "clock": {"utc": now.isoformat(timespec="seconds"),
                      "local": datetime.now().astimezone().isoformat(timespec="seconds")},
            "proxy_environment": {key: mask_proxy(os.environ.get(key)) for key in (
                "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY") if os.environ.get(key)},
        },
        "dns": check_dns([app_host, "ap.iimzt.com", "ax.iimzt.com"]),
        "egress": check_egress(proxy, args.timeout),
        "certificates": cert,
    }
    report["app_api"] = check_app_api(app_api, str(args.id), proxy, cert, args.timeout)
    report["summary"] = {
        "egress_ip": report["egress"].get("ip", ""),
        "app_api_status": report["app_api"].get("status", "skipped"),
        "decrypt_images": (report["app_api"].get("decode") or {}).get("images"),
        "cert_ok": bool(cert.get("ok")),
    }
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
