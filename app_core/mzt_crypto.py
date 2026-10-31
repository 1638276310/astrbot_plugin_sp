"""kkmzt（美之图）图片接口解密。

站点有两套图片接口（同一路径、不同域名、不同加密）：

1. **网页端** ``GET {站点}/app/post/p?id=<文章ID>``（kkmzt.com）
   * ``raw = base64decode(data)``
   * ``salt = raw[0:16]``，``iv = raw[16:28]``，``密文 + 16 字节 GCM tag = raw[28:]``
   * ``key = HKDF-SHA256(ikm = str(文章ID), salt, info=b"", length=32)``
   * ``AES-256-GCM`` 解密 → JSON **数组**（图片文件名）；写真馆只返回前 20 张预览。
   * 图片完整 URL = 文章页第一张图的目录 + 文件名。

2. **APP 端** ``GET https://api.iimzt.com/app/post/p?id=<文章ID>``
   * 逆向自官方 Android APP（美之图 3.8.7，APICloud 混合应用，widget JS 已解出）
   * **必须使用 APP 内置的 mTLS 客户端证书**（APK 内 ``assets/widget/res/config`` 是
     PKCS#12，密码在解密后的 ``res/key.xml`` 的 ``key`` 字段；APP 启动时通过
     ``api.applyCertificates`` 安装）。没有客户端证书时 Cloudflare 直接返回 403。
   * 响应同样是 ``{"data": "<payload>"}``，但 payload 是 **hex 字符串**（不是 base64），
     hex 解码后为 AES-128-CBC/PKCS7 密文：
     * ``key = md5(str(文章ID)).hexdigest()[8:24].lower()``（16 个 ASCII 字符）
     * ``iv = b"0809060801020609"``（来自 APP 解密后的 ``res/key.xml``）
   * 明文是 JSON **对象**：
     ``{"id", "date", "title", "count", "thumb", "thumbs", "images", ...}``，
     ``images`` 是带 ``?sign=`` 的完整图片 URL（如 ``https://ap.iimzt.com/...jpg?sign=...``），
     ``count`` 即全量张数（写真馆也是全量 —— 这就是网页端 20 张限制的根因）。
   * 高清图（APP 的 toggleHD）：
     * 写真馆：``https://ap.iimzt.com/....jpg`` → ``https://ax.iimzt.com/....jpg``
       + ``?sign=base64url_nopad(md5("auavax" + path))``
     * 潮拍馆（响应带 ``thumbs`` 列表）：``mw1024`` → ``mw2000``
   * 图片 CDN（ap/ax.iimzt.com）本身不需要客户端证书。

本模块优先使用 ``cryptography``（若运行环境已安装），否则回退到内置的
纯 Python AES 实现，保证插件在只装了 requirements.txt 的环境里也能直接使用，
不需要额外依赖。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
from urllib.parse import urlsplit

try:  # 可选加速：AstrBot 部署环境不一定装了 cryptography
    from cryptography.hazmat.primitives import hashes as _c_hashes  # type: ignore
    from cryptography.hazmat.primitives import padding as _c_padding  # type: ignore
    from cryptography.hazmat.primitives.ciphers import Cipher as _C_Cipher  # type: ignore
    from cryptography.hazmat.primitives.ciphers import algorithms as _c_algorithms  # type: ignore
    from cryptography.hazmat.primitives.ciphers import modes as _c_modes  # type: ignore
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM as _C_AESGCM  # type: ignore
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF as _C_HKDF  # type: ignore

    _HAVE_CRYPTOGRAPHY = True
except Exception:  # pragma: no cover - 取决于部署环境
    _HAVE_CRYPTOGRAPHY = False
    _c_hashes = None  # type: ignore[assignment]
    _c_padding = None  # type: ignore[assignment]
    _C_Cipher = None  # type: ignore[assignment]
    _c_algorithms = None  # type: ignore[assignment]
    _c_modes = None  # type: ignore[assignment]
    _C_AESGCM = None  # type: ignore[assignment]
    _C_HKDF = None  # type: ignore[assignment]


class KkmztDecryptError(Exception):
    """接口数据解密失败。"""


# APP 端固定参数（逆向自官方 APP 解密后的资源，见 README「妹子图图片接口逆向说明」）
APP_IV = b"0809060801020609"
APP_AXKEY = b"auavax"
APP_HIGH_RES_MARK = "/ap"


def app_cbc_key(article_id: str | int) -> bytes:
    """APP 端 AES-128-CBC 密钥：md5(str(id))[8:24]（16 个 ASCII 字符）。"""
    digest = hashlib.md5(str(article_id).encode("utf-8")).hexdigest()
    return digest[8:24].lower().encode("ascii")


# --------------------------------------------------------------------------- #
# HKDF-SHA256（RFC 5869）
# --------------------------------------------------------------------------- #
def hkdf_sha256(
    ikm: bytes, salt: bytes, info: bytes = b"", length: int = 32
) -> bytes:
    """HKDF-SHA256 派生密钥（站点只用到一次 Expand，长度 32）。"""
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    okm = b""
    block = b""
    counter = 1
    while len(okm) < length:
        block = hmac.new(
            prk, block + info + bytes([counter]), hashlib.sha256
        ).digest()
        okm += block
        counter += 1
    return okm[:length]


# --------------------------------------------------------------------------- #
# 纯 Python AES-256（只实现加密方向，GCM 解密只需要这个方向）
# --------------------------------------------------------------------------- #
def _xtime(value: int) -> int:
    value <<= 1
    if value & 0x100:
        value ^= 0x11B
    return value & 0xFF


def _gf_mul(a: int, b: int) -> int:
    result = 0
    for _ in range(8):
        if b & 1:
            result ^= a
        a = _xtime(a)
        b >>= 1
    return result


def _rotl8(value: int, shift: int) -> int:
    return ((value << shift) | (value >> (8 - shift))) & 0xFF


def _build_sbox() -> list[int]:
    inverse = [0] * 256
    for a in range(1, 256):
        for b in range(1, 256):
            if _gf_mul(a, b) == 1:
                inverse[a] = b
                break

    sbox = [0] * 256
    for x in range(256):
        y = inverse[x]
        sbox[x] = (
            y
            ^ _rotl8(y, 1)
            ^ _rotl8(y, 2)
            ^ _rotl8(y, 3)
            ^ _rotl8(y, 4)
            ^ 0x63
        )
    return sbox


_SBOX = _build_sbox()


def _expand_key(key: bytes) -> list[list[int]]:
    """AES 密钥扩展，返回 (轮数+1) 个 16 字节轮密钥。"""
    if len(key) not in (16, 24, 32):
        raise KkmztDecryptError(f"AES 密钥长度非法: {len(key)}")
    nk = len(key) // 4
    nr = nk + 6
    words = [list(key[4 * i : 4 * i + 4]) for i in range(nk)]
    rcon = 1
    for i in range(nk, 4 * (nr + 1)):
        temp = list(words[i - 1])
        if i % nk == 0:
            temp = temp[1:] + temp[:1]
            temp = [_SBOX[b] for b in temp]
            temp[0] ^= rcon
            rcon = _xtime(rcon)
        elif nk > 6 and i % nk == 4:
            temp = [_SBOX[b] for b in temp]
        words.append([words[i - nk][j] ^ temp[j] for j in range(4)])
    return [sum(words[4 * r : 4 * r + 4], []) for r in range(nr + 1)]


def _add_round_key(state: list[int], round_key: list[int]) -> None:
    for i in range(16):
        state[i] ^= round_key[i]


def _sub_bytes(state: list[int]) -> None:
    for i in range(16):
        state[i] = _SBOX[state[i]]


def _shift_rows(state: list[int]) -> None:
    for row in range(1, 4):
        values = [state[row + 4 * column] for column in range(4)]
        values = values[row:] + values[:row]
        for column in range(4):
            state[row + 4 * column] = values[column]


def _mix_columns(state: list[int]) -> None:
    for column in range(4):
        i = 4 * column
        a0, a1, a2, a3 = state[i], state[i + 1], state[i + 2], state[i + 3]
        state[i] = _gf_mul(a0, 2) ^ _gf_mul(a1, 3) ^ a2 ^ a3
        state[i + 1] = a0 ^ _gf_mul(a1, 2) ^ _gf_mul(a2, 3) ^ a3
        state[i + 2] = a0 ^ a1 ^ _gf_mul(a2, 2) ^ _gf_mul(a3, 3)
        state[i + 3] = _gf_mul(a0, 3) ^ a1 ^ a2 ^ _gf_mul(a3, 2)


def _encrypt_block(round_keys: list[list[int]], block: bytes) -> bytes:
    if len(block) != 16:
        raise KkmztDecryptError("AES 分组长度必须为 16 字节")
    state = list(block)
    _add_round_key(state, round_keys[0])
    nr = len(round_keys) - 1
    for round_index in range(1, nr):
        _sub_bytes(state)
        _shift_rows(state)
        _mix_columns(state)
        _add_round_key(state, round_keys[round_index])
    _sub_bytes(state)
    _shift_rows(state)
    _add_round_key(state, round_keys[nr])
    return bytes(state)


# --------------------------------------------------------------------------- #
# 纯 Python AES 解密方向 + CBC（APP 端接口使用 AES-128-CBC）
# --------------------------------------------------------------------------- #
def _build_inv_sbox() -> list[int]:
    inverse = [0] * 256
    for index, value in enumerate(_SBOX):
        inverse[value] = index
    return inverse


_INV_SBOX = _build_inv_sbox()


def _inv_sub_bytes(state: list[int]) -> None:
    for i in range(16):
        state[i] = _INV_SBOX[state[i]]


def _inv_shift_rows(state: list[int]) -> None:
    for row in range(1, 4):
        values = [state[row + 4 * column] for column in range(4)]
        values = values[-row:] + values[:-row]  # 向右循环移位
        for column in range(4):
            state[row + 4 * column] = values[column]


def _inv_mix_columns(state: list[int]) -> None:
    for column in range(4):
        i = 4 * column
        a0, a1, a2, a3 = state[i], state[i + 1], state[i + 2], state[i + 3]
        state[i] = (
            _gf_mul(a0, 14) ^ _gf_mul(a1, 11) ^ _gf_mul(a2, 13) ^ _gf_mul(a3, 9)
        )
        state[i + 1] = (
            _gf_mul(a0, 9) ^ _gf_mul(a1, 14) ^ _gf_mul(a2, 11) ^ _gf_mul(a3, 13)
        )
        state[i + 2] = (
            _gf_mul(a0, 13) ^ _gf_mul(a1, 9) ^ _gf_mul(a2, 14) ^ _gf_mul(a3, 11)
        )
        state[i + 3] = (
            _gf_mul(a0, 11) ^ _gf_mul(a1, 13) ^ _gf_mul(a2, 9) ^ _gf_mul(a3, 14)
        )


def _decrypt_block(round_keys: list[list[int]], block: bytes) -> bytes:
    if len(block) != 16:
        raise KkmztDecryptError("AES 分组长度必须为 16 字节")
    state = list(block)
    rounds = len(round_keys) - 1
    _add_round_key(state, round_keys[rounds])
    for round_index in range(rounds - 1, 0, -1):
        _inv_shift_rows(state)
        _inv_sub_bytes(state)
        _add_round_key(state, round_keys[round_index])
        _inv_mix_columns(state)
    _inv_shift_rows(state)
    _inv_sub_bytes(state)
    _add_round_key(state, round_keys[0])
    return bytes(state)


def _cbc_decrypt(round_keys: list[list[int]], data: bytes, iv: bytes) -> bytes:
    if len(data) % 16:
        raise KkmztDecryptError(f"AES-CBC 密文长度不是 16 的倍数：{len(data)}")
    plaintext = bytearray()
    previous = iv
    for offset in range(0, len(data), 16):
        block = data[offset : offset + 16]
        plaintext += _xor_bytes(_decrypt_block(round_keys, block), previous)
        previous = block
    return bytes(plaintext)


def _pkcs7_unpad(data: bytes, block_size: int = 16) -> bytes:
    if not data:
        raise KkmztDecryptError("PKCS7 填充数据为空")
    pad = data[-1]
    if pad < 1 or pad > block_size or data[-pad:] != bytes([pad]) * pad:
        raise KkmztDecryptError("PKCS7 填充非法（密钥或数据不匹配）")
    return data[:-pad]


# --------------------------------------------------------------------------- #
# 纯 Python GCM（GHASH 128 位乘法）
# --------------------------------------------------------------------------- #
_GF_R = 0xE1000000000000000000000000000000


def _gf128_mul(x: int, y: int) -> int:
    """GF(2^128) 乘法，模数 x^128 + x^7 + x^2 + x + 1（GCM 规范）。"""
    result = 0
    value = y
    for bit in range(128):
        if (x >> (127 - bit)) & 1:
            result ^= value
        if value & 1:
            value = (value >> 1) ^ _GF_R
        else:
            value >>= 1
    return result


def _xor_bytes(left: bytes, right: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(left, right))


def _ghash(hash_subkey: int, aad: bytes, ciphertext: bytes) -> int:
    """GHASH：对 AAD 与密文分别按 16 字节分块（末块右补零），再追加长度块。"""
    result = 0
    for data in (aad, ciphertext):
        for offset in range(0, len(data), 16):
            chunk = data[offset : offset + 16].ljust(16, b"\x00")
            result = _gf128_mul(
                result ^ int.from_bytes(chunk, "big"), hash_subkey
            )
    length_block = (len(aad) * 8).to_bytes(8, "big") + (
        len(ciphertext) * 8
    ).to_bytes(8, "big")
    return _gf128_mul(result ^ int.from_bytes(length_block, "big"), hash_subkey)


def _gcm_decrypt(
    round_keys: list[list[int]], iv: bytes, ciphertext: bytes, tag: bytes
) -> bytes:
    if len(iv) != 12:
        raise KkmztDecryptError(f"不支持非 12 字节 GCM IV（实际 {len(iv)}）")
    hash_subkey = int.from_bytes(_encrypt_block(round_keys, b"\x00" * 16), "big")
    j0 = int.from_bytes(iv + b"\x00\x00\x00\x01", "big")
    plaintext = bytearray()
    counter = j0
    prefix = counter & ~0xFFFFFFFF  # J0 的高 96 位保持不变，只自增低 32 位
    for offset in range(0, len(ciphertext), 16):
        counter = prefix | ((counter + 1) & 0xFFFFFFFF)
        keystream = _encrypt_block(round_keys, counter.to_bytes(16, "big"))
        plaintext += _xor_bytes(ciphertext[offset : offset + 16], keystream)

    # GHASH 输入：AAD（本站为空）|| 密文（各自按 16 字节分块补齐）|| 长度块
    expected_tag = _xor_bytes(
        _encrypt_block(round_keys, j0.to_bytes(16, "big")),
        _ghash(hash_subkey, b"", ciphertext).to_bytes(16, "big"),
    )
    if not hmac.compare_digest(expected_tag[: len(tag)], tag):
        raise KkmztDecryptError("AES-GCM 认证标签校验失败（密钥或数据不匹配）")
    return bytes(plaintext)


# --------------------------------------------------------------------------- #
# 对外接口
# --------------------------------------------------------------------------- #
def _decode_base64(data: str) -> bytes:
    text = (data or "").strip()
    if not text:
        raise KkmztDecryptError("接口返回的 data 为空")
    padded = text + "=" * (-len(text) % 4)
    try:
        return base64.b64decode(padded, validate=False)
    except (binascii.Error, ValueError):
        try:
            return base64.urlsafe_b64decode(padded)
        except (binascii.Error, ValueError) as exc:
            raise KkmztDecryptError(f"data 不是合法 base64：{exc}") from exc


def _decrypt_web_names(raw: bytes, article_id: str | int) -> list[str]:
    """网页端格式：salt16 + iv12 + (密文+tag)，AES-256-GCM → 文件名数组。"""
    if len(raw) < 45:  # 16 salt + 12 iv + 1 密文 + 16 tag
        raise KkmztDecryptError(f"网页端加密数据长度异常：{len(raw)}")
    salt, iv, encrypted = raw[:16], raw[16:28], raw[28:]
    ciphertext, tag = encrypted[:-16], encrypted[-16:]
    key = hkdf_sha256(str(article_id).encode("utf-8"), salt)

    if _HAVE_CRYPTOGRAPHY:
        assert _C_AESGCM is not None
        try:
            # WebCrypto / cryptography 的 AES-GCM 接口都要求 tag 拼在密文尾部
            plaintext = _C_AESGCM(key).decrypt(iv, encrypted, None)
        except Exception as exc:  # 标签校验失败等
            raise KkmztDecryptError(f"AES-GCM 解密失败：{exc}") from exc
    else:
        plaintext = _gcm_decrypt(_expand_key(key), iv, ciphertext, tag)

    try:
        names = json.loads(plaintext.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise KkmztDecryptError(f"网页端解密结果不是合法 JSON：{exc}") from exc
    if not isinstance(names, list) or not all(isinstance(item, str) for item in names):
        raise KkmztDecryptError("网页端解密结果不是字符串数组")
    return names


def _decrypt_app_object(raw: bytes, article_id: str | int) -> dict:
    """APP 端格式：AES-128-CBC/PKCS7 → {"images": [...], "count": N, ...}。"""
    if len(raw) < 16 or len(raw) % 16:
        raise KkmztDecryptError(f"APP 端加密数据长度异常：{len(raw)}")
    key = app_cbc_key(article_id)
    if _HAVE_CRYPTOGRAPHY:
        assert _C_Cipher is not None
        assert _c_algorithms is not None
        assert _c_modes is not None
        assert _c_padding is not None
        try:
            decryptor = _C_Cipher(
                _c_algorithms.AES(key), _c_modes.CBC(APP_IV)
            ).decryptor()
            padded = decryptor.update(raw) + decryptor.finalize()
            unpadder = _c_padding.PKCS7(128).unpadder()
            plaintext = unpadder.update(padded) + unpadder.finalize()
        except Exception as exc:  # 填充错误等
            raise KkmztDecryptError(f"AES-CBC 解密失败：{exc}") from exc
    else:
        plaintext = _pkcs7_unpad(_cbc_decrypt(_expand_key(key), raw, APP_IV))

    try:
        data = json.loads(plaintext.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise KkmztDecryptError(f"APP 端解密结果不是合法 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise KkmztDecryptError("APP 端解密结果不是 JSON 对象")
    images = data.get("images") or []
    if not isinstance(images, list) or not all(isinstance(item, str) for item in images):
        raise KkmztDecryptError("APP 端解密结果缺少合法的 images 数组")
    try:
        count = int(data.get("count") or 0)
    except (TypeError, ValueError):
        count = 0
    return {
        "images": [image for image in images if image],
        "count": count or len(images),
        "title": str(data.get("title") or ""),
        "date": str(data.get("date") or ""),
        "hd": bool(data.get("hd")),
        # 潮拍馆响应带 thumbs 列表，此时高清是 mw1024 -> mw2000，而不是 ax 签名
        "thumbs": bool(data.get("thumbs")),
    }


def _payload_candidates(payload: str) -> list[tuple[str, bytes]]:
    """把接口 ``data`` 字段还原成密文字节。

    * APP 端是 **hex** 字符串；
    * 网页端是 **base64** 字符串。

    先按“看起来像 hex”尝试 hex，再尝试 base64，避免把恰好由 hex 字符组成的
    base64 误判成 hex（反之亦然）。
    """
    text = (payload or "").strip()
    candidates: list[tuple[str, bytes]] = []
    if text and len(text) % 2 == 0 and re.fullmatch(r"[0-9a-fA-F]+", text):
        try:
            candidates.append(("hex", bytes.fromhex(text)))
        except ValueError:  # pragma: no cover - 正则已排除
            pass
    try:
        candidates.append(("base64", _decode_base64(text)))
    except (KkmztDecryptError, binascii.Error):
        pass
    if not candidates:
        raise KkmztDecryptError("接口 data 既不是合法的 hex 也不是合法的 base64")
    return candidates


def decrypt_api_payload(payload_b64: str, article_id: str | int) -> list[str]:
    """兼容旧接口：按网页端格式（base64 + GCM）解密，返回图片文件名列表。"""
    return _decrypt_web_names(_decode_base64(payload_b64), str(article_id))


def decode_api_payload(payload: str, article_id: str | int) -> dict:
    """自动识别网页端（base64 + GCM）/ APP 端（hex + AES-CBC）两种格式并解密。

    返回统一结构::

        {
            "scheme": "app" | "web",
            "images": [图片文件名(web) 或 完整URL(app), ...],
            "count": int,     # 总张数；web 端未知时为 0
            "title": str,
            "date": str,
            "hd": bool,
            "thumbs": bool,   # APP 端潮拍馆标记（高清用 mw2000）
        }
    """
    article_id = str(article_id)
    errors: list[str] = []
    for encoding, raw in _payload_candidates(payload):
        # APP 端 CBC 密文长度必为 16 的倍数；网页端 GCM 偶尔也可能碰巧是，
        # 所以先按长度猜顺序，失败再换另一种格式重试。
        schemes = ["app", "web"] if len(raw) % 16 == 0 else ["web", "app"]
        for scheme in schemes:
            try:
                if scheme == "app":
                    data = _decrypt_app_object(raw, article_id)
                    return {
                        "scheme": "app",
                        "images": data["images"],
                        "count": data["count"],
                        "title": data["title"],
                        "date": data["date"],
                        "hd": data["hd"],
                        "thumbs": data["thumbs"],
                    }
                names = _decrypt_web_names(raw, article_id)
                return {
                    "scheme": "web",
                    "images": names,
                    "count": 0,
                    "title": "",
                    "date": "",
                    "hd": False,
                    "thumbs": False,
                }
            except KkmztDecryptError as exc:
                errors.append(f"{encoding}/{scheme}: {exc}")
    raise KkmztDecryptError("无法识别/解密接口数据（" + "；".join(errors[:6]) + "）")


def _b64url_md5(data: bytes) -> str:
    """与 APP 中 ``b64_md5().replace(...)`` 等价的 base64url(无填充)。"""
    sign = base64.b64encode(hashlib.md5(data).digest()).decode("ascii")
    return sign.replace("=", "").replace("+", "-").replace("/", "_")


def hd_image_url(normal_url: str) -> str:
    """APP 端写真馆高清图：``ap.iimzt.com`` → ``ax.iimzt.com`` + 新 sign。

    与 ``pages/page/view.js`` 的 ``toggleHD`` 等价（正则 ``/https.*\\.com(.*\\.jpg)/i``）::

        https://ap.iimzt.com/2026/04/01p01hp9xk.jpg?sign=xxx
        → https://ax.iimzt.com/2026/04/01p01hp9xk.jpg?sign=base64url_nopad(md5("auavax" + "/2026/04/01p01hp9xk.jpg"))
    """
    match = re.match(r"(https.*\.com)(.*\.jpg)", normal_url or "", re.I)
    if not match:
        # 兜底：没有 .com 的域名（理论上不会出现）
        split = urlsplit(normal_url or "")
        if not split.scheme or not split.netloc or not split.path.lower().endswith(".jpg"):
            return normal_url
        base, path = f"{split.scheme}://{split.netloc}", split.path
    else:
        base, path = match.group(1), match.group(2)
    if APP_HIGH_RES_MARK not in base.lower() and APP_HIGH_RES_MARK not in path.lower():
        return normal_url
    return base.replace("/ap", "/ax", 1) + path + "?sign=" + _b64url_md5(
        APP_AXKEY + path.encode("utf-8")
    )


def hd_beauty_image_url(normal_url: str) -> str:
    """APP 端潮拍馆高清图：``mw1024`` → ``mw2000``（无需签名）。"""
    return (normal_url or "").replace("mw1024", "mw2000")


__all__ = [
    "KkmztDecryptError",
    "app_cbc_key",
    "decode_api_payload",
    "decrypt_api_payload",
    "hd_beauty_image_url",
    "hd_image_url",
    "hkdf_sha256",
]
