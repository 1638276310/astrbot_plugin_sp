"""插件数据目录管理。

按照 AstrBot 的插件开发规范，所有需要持久化的数据都放在
``data/plugin_data/<plugin_name>/`` 下，避免插件更新时被覆盖。
"""

from __future__ import annotations
# from astrbot.api.all import get_astrbot_plugin_data_path # type: ignore

import os
import shutil
import time
from pathlib import Path

try:  # AstrBot >= 4.x
    from astrbot.core.utils.astrbot_path import (  # type: ignore
        get_astrbot_data_path,
        get_astrbot_plugin_data_path,
    )
except Exception:  # pragma: no cover - 兼容极旧版本
    get_astrbot_plugin_data_path = None
    get_astrbot_data_path = None

PLUGIN_NAME = "astrbot_plugin_sp"


def _dbg(message: str) -> None:
    """统一的控制台 debug 日志输出（失败静默，不影响主流程）。"""
    try:
        from astrbot.api import logger # type: ignore
        logger.info(f"[涩批DEBUG] {message}")
    except Exception:
        pass


class PluginPaths:
    """负责解析并创建插件所需的全部目录。

    数据目录统一通过 ``StarTools.get_data_dir(plugin_name)`` 获取
    （AstrBot 官方规范路径，与 get_px 等成熟插件一致），
    不再依赖 ``context.get_data_dir()``。
    """

    def __init__(self, context=None, plugin_name: str = PLUGIN_NAME) -> None:
        self.plugin_name = plugin_name or PLUGIN_NAME
        self.root = self._resolve_root()
        self.temp = self.root / "temp"
        self.cache = self.root / "cache"
        self.assets = Path(__file__).resolve().parent.parent / "assets"
        _dbg(f"PluginPaths.__init__：root={self.root}, temp={self.temp}, cache={self.cache}")
        # 持久化数据（套图URL列表）放在 AstrBot 主数据目录下，
        # 避免随 ``data/plugin_data/<plugin>/`` 在卸载/重装时被删除
        self.persistent_dir = self._resolve_persistent_dir()
        _dbg(f"PluginPaths.__init__：persistent_dir={self.persistent_dir}")
        self._ensure()

    # ------------------------------------------------------------------ #
    # 目录解析
    # ------------------------------------------------------------------ #
    def _resolve_root(self) -> Path:
        # 首选：AstrBot 官方 StarTools（get_px 同款路径，插件数据规范位置）
        try:
            from astrbot.core.star.star_tools import StarTools  # type: ignore

            return Path(StarTools.get_data_dir(self.plugin_name))
        except Exception:  # pragma: no cover - 兼容极旧版本
            pass
        if get_astrbot_plugin_data_path is not None:
            try:
                return Path(get_astrbot_plugin_data_path())
            except Exception:  # pragma: no cover
                pass
        if get_astrbot_data_path is not None:
            try:
                return Path(get_astrbot_data_path()) / "plugin_data"
            except Exception:  # pragma: no cover
                pass
        # 兜底：AstrBot 根目录下的 data/plugin_data
        env_root = os.environ.get("ASTRBOT_ROOT")
        base = Path(env_root) if env_root else Path(os.getcwd())
        return base / "data" / "plugin_data" / self.plugin_name

    def _resolve_persistent_dir(self) -> Path:
        """套图URL等大体积数据的持久化目录。

        放在 ``data/<plugin_name>/``（与 ``data/plugin_data/`` 平级），
        不属于插件 data 目录，AstrBot 卸载/重装插件时不会被清除，
        避免每次重装都要全量重新采集几万条套图 URL。
        """
        if get_astrbot_data_path is not None:
            try:
                base = Path(get_astrbot_data_path())
                _dbg(f"PluginPaths._resolve_persistent_dir：使用 AstrBot data 目录 {base}")
                return base / self.plugin_name
            except Exception as exc:  # pragma: no cover
                _dbg(f"PluginPaths._resolve_persistent_dir：get_astrbot_data_path 失败：{exc!r}")
        # 兜底：AstrBot 根目录下的 data
        env_root = os.environ.get("ASTRBOT_ROOT")
        base = Path(env_root) if env_root else Path(os.getcwd())
        persistent = base / "data" / self.plugin_name
        _dbg(f"PluginPaths._resolve_persistent_dir：兜底使用 {persistent}")
        return persistent

    def _ensure(self) -> None:
        for path in (self.root, self.temp, self.cache, self.persistent_dir):
            path.mkdir(parents=True, exist_ok=True)
        self.ensure_video_urls()
        self.migrate_persistent_data()
        _dbg(f"PluginPaths._ensure：目录已就绪，temp 文件数 {len(list(self.temp.glob('*')))}")

    # ------------------------------------------------------------------ #
    # 常用文件
    # ------------------------------------------------------------------ #
    @property
    def mzt_ids_file(self) -> Path:
        """妹子图写真 ID 列表。"""
        return self.root / "mztids.json"

    @property
    def jg_urls_file(self) -> Path:
        """美图吧套图 URL 列表（存放在持久化目录，避免随插件卸载被删除）。"""
        return self.persistent_dir / "jg.json"

    @property
    def _legacy_jg_urls_file(self) -> Path:
        """旧的套图 URL 列表位置（在插件 data 目录下，卸载会被删）。"""
        return self.root / "jg.json"

    def migrate_persistent_data(self) -> None:
        """把旧位置（插件 data 目录）的套图 URL 列表迁移到持久化目录。

        只在持久化目录还没有文件、而旧位置存在文件时执行一次，
        避免用户已经积累了几万条 URL 却因为路径变更而丢失。
        """
        persistent = self.jg_urls_file
        legacy = self._legacy_jg_urls_file
        if persistent.exists():
            _dbg(f"PluginPaths.migrate_persistent_data：持久化目录已有 {persistent}，跳过迁移")
            return
        if not legacy.exists():
            _dbg(f"PluginPaths.migrate_persistent_data：旧位置 {legacy} 不存在，无需迁移")
            return
        try:
            persistent.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(legacy, persistent)
            _dbg(
                f"PluginPaths.migrate_persistent_data：已将 {legacy} 迁移到 {persistent}，"
                f"大小 {persistent.stat().st_size} 字节"
            )
        except OSError as exc:
            _dbg(f"PluginPaths.migrate_persistent_data：迁移失败：{exc!r}")

    @property
    def subscribe_file(self) -> Path:
        """画师订阅数据。"""
        return self.root / "dingyue.json"

    @property
    def video_urls_file(self) -> Path:
        """骚鸡短视频直链列表。"""
        return self.root / "sp_video_urls.json"

    def ensure_video_urls(self) -> Path:
        """确保持久化视频列表文件存在，若不存在则从 assets/sp_video_urls.json 初始化。"""
        if not self.video_urls_file.exists():
            asset_file = self.asset("sp_video_urls.json")
            if asset_file.exists():
                try:
                    shutil.copyfile(asset_file, self.video_urls_file)
                except OSError:
                    pass
        return self.video_urls_file

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
