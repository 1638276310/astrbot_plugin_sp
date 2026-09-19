"""功能：随机短视频（骚鸡/烧鸡/sj）。

对应原 ``sj.js``：从持久化的视频直链列表里随机取一个，下载后发送。
AstrBot 的 Video 组件可以直接吃网络 URL，但快手 CDN 需要 Referer，
所以这里先下载到临时目录再用本地文件发送。

指令 handler 由主类 main.py 注册（与 get_px 同构），
本文件保留 Referer 常量与视频列表读写方法。
"""

from __future__ import annotations

from astrbot.api import logger # type: ignore

from ._base import spFeature
from ..app_core.storage import load_json, save_json

REFERER = "https://www.kuaishou.com/"


class VideoFeature(spFeature):
    """随机短视频相关业务方法（指令 handler 由主类 main.py 注册）。"""

    def video_urls(self) -> list[str]:
        """读取骚鸡短视频直链列表。

        按照 AstrBot 插件规范，所有持久化数据存放在
        ``data/plugin_data/<plugin_name>/sp_video_urls.json`` 下，
        避免插件更新时被覆盖。
        若持久化文件不存在，会自动从 assets/sp_video_urls.json 初始化。
        """
        self.paths.ensure_video_urls()
        data = load_json(self.paths.video_urls_file, [])
        if isinstance(data, list) and data:
            urls = [str(item) for item in data]
            logger.info(f"[涩批DEBUG] video_urls：从持久化文件读到 {len(urls)} 个直链")
            return urls
        # 兜底：尝试从 assets 资源目录读取
        asset_file = self.paths.asset("sp_video_urls.json")
        if asset_file.exists():
            data = load_json(asset_file, [])
            if isinstance(data, list) and data:
                urls = [str(item) for item in data]
                logger.info(
                    f"[涩批DEBUG] video_urls：从 assets 文件读到 {len(urls)} 个直链"
                )
                return urls
        # 兼容旧配置项兜底
        fallback = getattr(self.settings, "sp_video_urls", [])
        logger.info(f"[涩批DEBUG] video_urls：文件与 assets 均为空，使用配置项 {len(fallback)} 个")
        return fallback

    def save_video_urls(self, urls: list[str]) -> bool:
        """保存视频列表到持久化目录。"""
        ok = save_json(self.paths.video_urls_file, urls)
        logger.info(f"[涩批DEBUG] save_video_urls：写入 {len(urls)} 个直链，结果 {ok}")
        return ok
