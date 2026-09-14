"""插件数据目录管理。

按照 AstrBot 的插件开发规范，所有需要持久化的数据都放在
``data/plugin_data/<plugin_name>/`` 下，避免插件更新时被覆盖。
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

try:  # AstrBot >= 4.x
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path
    # from astrbot.core.utils.astrbot_path import get_astrbot_data_path
except Exception:  # pragma: no cover - 兼容极旧版本
    get_astrbot_plugin_data_path = None

PLUGIN_NAME = "astrbot_plugin_sp"


class PluginPaths:
    """负责解析并创建插件所需的全部目录。"""

    def __init__(self, plugin_name: str = PLUGIN_NAME) -> None:
        self.plugin_name = plugin_name or PLUGIN_NAME
        self.root = self._resolve_root() / self.plugin_name
        self.temp = self.root / "temp"
        self.cache = self.root / "cache"
        self.assets = Path(__file__).resolve().parent.parent / "assets"
        self._ensure()

    # ------------------------------------------------------------------ #
    # 目录解析
    # ------------------------------------------------------------------ #
    def _resolve_root(self) -> Path:
        if get_astrbot_plugin_data_path is not None:
            try:
                return Path(get_astrbot_plugin_data_path())
            except Exception:  # pragma: no cover
                pass
        # 兜底：AstrBot 根目录下的 data/plugin_data
        env_root = os.environ.get("ASTRBOT_ROOT")
        base = Path(env_root) if env_root else Path(os.getcwd())
        return base / "data" / "plugin_data"

    def _ensure(self) -> None:
        for path in (self.root, self.temp, self.cache):
            path.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ #
    # 常用文件
    # ------------------------------------------------------------------ #
    @property
    def mzt_ids_file(self) -> Path:
        """妹子图写真 ID 列表。"""
        return self.root / "mztids.json"

    @property
    def jg_urls_file(self) -> Path:
        """美图吧套图 URL 列表。"""
        return self.root / "jg.json"

    @property
    def subscribe_file(self) -> Path:
        """画师订阅数据。"""
        return self.root / "dingyue.json"

    @property
    def runtime_file(self) -> Path:
        """运行时状态（上次推送检查时间等）。"""
        return self.root / "runtime.json"

    def temp_file(self, name: str) -> Path:
        return self.temp / name

    # ------------------------------------------------------------------ #
    # 临时文件清理
    # ------------------------------------------------------------------ #
    def cleanup_temp(self, max_age_seconds: int = 3600) -> int:
        """删除超过指定时间的临时文件，返回删除数量。"""
        now = time.time()
        removed = 0
        for child in self.temp.glob("*"):
            try:
                if child.is_file() and now - child.stat().st_mtime > max_age_seconds:
                    child.unlink()
                    removed += 1
            except OSError:
                continue
        return removed

    def clear_cache(self) -> None:
        """清空下载缓存目录。"""
        try:
            shutil.rmtree(self.cache, ignore_errors=True)
        finally:
            self.cache.mkdir(parents=True, exist_ok=True)

    def asset(self, *parts: str) -> Path:
        """插件自带静态资源路径，例如 assets/help.jpg。"""
        return self.assets.joinpath(*parts)
