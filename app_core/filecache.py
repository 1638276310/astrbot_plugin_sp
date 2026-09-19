"""图片下载缓存。

原插件每次请求都重新下载图片；AstrBot 侧改为按内容哈希缓存，
既可以复用（如磁力截图、tag 图重复命中），也方便定期清理。
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from .http import fetch_bytes
from .imaging import guess_suffix, sha1_of


def _dbg(message: str) -> None:
    """统一的控制台 debug 日志输出（失败静默，不影响主流程）。"""
    try:
        from astrbot.api import logger # type: ignore
        logger.info(f"[涩批DEBUG] {message}")
    except Exception:
        pass


class FileCache:
    """基于 URL 哈希的两级缓存（内存 + 磁盘）。"""

    def __init__(self, cache_dir: Path, max_age_seconds: int = 24 * 3600) -> None:
        self.cache_dir = cache_dir
        self.max_age_seconds = max_age_seconds
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._memory: dict[str, bytes] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _path_for(self, key: str, suffix: str = "") -> Path:
        """统一用 ``{key}{suffix}`` 作为磁盘文件名。

        命中检查与落盘必须用**同一套**后缀规则：落盘时先 ``guess_suffix``
        猜出真实扩展名写盘，命中检查时枚举常见扩展名 + 无扩展名兜底，
        避免"写的是 .jpg、查的却是 .bin"导致磁盘缓存永远 miss。
        """
        return self.cache_dir / f"{key}{suffix}"

    _DISK_SUFFIXES: tuple[str, ...] = (".jpg", ".jpeg", ".png", ".gif", ".webp", "")

    def _find_disk_file(self, key: str) -> Path | None:
        """按已知后缀顺序找该 key 的磁盘文件，找不到返回 None。"""
        for suffix in self._DISK_SUFFIXES:
            path = self._path_for(key, suffix)
            if path.exists():
                return path
        return None

    def _lock_for(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    async def get(
        self,
        url: str,
        *,
        referer: str | None = None,
        timeout: int = 20,
        use_memory: bool = True,
    ) -> bytes | None:
        """获取 URL 内容，命中缓存则直接返回。失败返回 None。"""
        key = sha1_of(url.encode("utf-8"))
        if use_memory and key in self._memory:
            _dbg(f"filecache.get：内存缓存命中 {url[:80]}")
            return self._memory[key]

        # 命中检查：枚举已知后缀找磁盘文件
        path = self._find_disk_file(key)
        if path is not None:
            try:
                if time.time() - path.stat().st_mtime <= self.max_age_seconds:
                    data = path.read_bytes()
                    _dbg(f"filecache.get：磁盘缓存命中 {url[:80]} -> {path.name}")
                    if use_memory:
                        self._memory[key] = data
                    return data
            except OSError:
                pass

        _dbg(f"filecache.get：未命中，开始下载 {url[:80]}")
        async with self._lock_for(key):
            # 双重检查，避免同一 URL 并发重复下载
            path = self._find_disk_file(key)
            if path is not None:
                try:
                    data = path.read_bytes()
                    if use_memory:
                        self._memory[key] = data
                    return data
                except OSError:
                    pass
            try:
                data = await fetch_bytes(url, timeout=timeout, referer=referer)
                _dbg(f"filecache.get：下载成功 {url[:80]} 共 {len(data)} 字节")
            except Exception as exc:
                _dbg(f"filecache.get：下载失败 {url[:80]}：{exc!r}")
                return None
            suffix = guess_suffix(data)
            try:
                target = self._path_for(key, suffix)
                target.write_bytes(data)
            except OSError as exc:
                _dbg(f"filecache.get：写盘失败 {url[:80]}：{exc!r}")
            if use_memory:
                # 内存缓存限制在 64MB 左右，避免图片太多撑爆内存
                if len(self._memory) > 64:
                    self._memory.clear()
                self._memory[key] = data
            return data

    def store_local(self, path: Path) -> None:
        """标记本地文件已缓存（占位，保持接口统一）。"""

    def cleanup(self) -> int:
        """清理过期缓存，返回删除数量。"""
        now = time.time()
        removed = 0
        for child in self.cache_dir.glob("*"):
            try:
                if child.is_file() and now - child.stat().st_mtime > self.max_age_seconds:
                    child.unlink()
                    removed += 1
            except OSError:
                continue
        self._memory.clear()
        return removed
