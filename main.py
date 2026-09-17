"""涩批插件 AstrBot 版 —— 插件入口。

AstrBot 只要求插件类所在的文件名为 ``main.py``。
本文件负责把 ``features/`` 目录下按功能拆分的各个模块组装成最终的插件类，
并管理定时任务的启动与停止。

功能模块一览：

* ``features/help.py``          帮助
* ``features/recall.py``        撤回 / R18 / 图片偏好 / 状态
* ``features/urls.py``          网址导航
* ``features/sj.py``            随机短视频
* ``features/pixiv_pid.py``     /pid
* ``features/pixiv_artist.py``  /随机X张Y作品
* ``features/pixiv_tag.py``     /来X张XX图
* ``features/mzt.py``           妹子图
* ``features/mtb.py``           美图吧套图
* ``features/magnet.py``        磁力猫 / 验车
* ``features/cos_images.py``    2图 / 3图
* ``features/subscribe.py``     画师订阅与推送
* ``app_core/``                公共能力（HTTP / 图片 / 浏览器 / 存储 / 配置 …）
"""

from __future__ import annotations

from astrbot.api import logger # type: ignore
from astrbot.api.star import Context, Star, register # type: ignore

from .features import (
    CosImageFeature,
    HelpFeature,
    MagnetFeature,
    MtbFeature,
    MztFeature,
    PixivArtistFeature,
    PixivPidFeature,
    PixivTagFeature,
    RecallFeature,
    SubscribeFeature,
    UrlFeature,
    VideoFeature,
)
from .app_core.paths import PLUGIN_NAME, PluginPaths
from .app_core.scheduler import TimeBasedScheduler
from .app_core.settings import PLUGIN_VERSION, build_settings
from .app_core.storage import JsonStore


@register(
    PLUGIN_NAME,
    "寂寞沙洲冷",
    "涩批插件 AstrBot 版：P站 / 磁力 / 妹子图 / 美图吧 / 网址导航 / 订阅推送",
    PLUGIN_VERSION,
)
class spPlugin(
    HelpFeature,
    RecallFeature,
    UrlFeature,
    VideoFeature,
    PixivPidFeature,
    PixivArtistFeature,
    PixivTagFeature,
    MztFeature,
    MtbFeature,
    MagnetFeature,
    CosImageFeature,
    SubscribeFeature,
    Star,
):
    """涩批插件（原 TRSS-Yunzai 版 sp-plugin 的 AstrBot 重构版）。

    每个功能都在独立的 Python 文件中实现，主入口只负责组装。
    发送 ``/涩批文字帮助`` 可以查看全部指令。
    """

    def __init__(self, context: Context, config=None) -> None:
        Star.__init__(self, context)
        # 注入运行时依赖：配置 + 数据目录
        self.settings = build_settings(config)
        self.paths = PluginPaths(context)
        self.setup_feature(self.settings, self.paths)
        self._subscribe_store = JsonStore(self.paths.subscribe_file, {})
        self.scheduler: TimeBasedScheduler | None = None
        logger.info(
            f"[涩批] 插件已加载，数据目录：{self.paths.root}"
        )

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    async def initialize(self) -> None:
        """插件被激活时启动内置定时任务。"""
        cleaned = self.paths.cleanup_temp(3600)
        if cleaned:
            logger.info(f"[涩批] 已清理 {cleaned} 个过期临时文件")

        if not self.settings.enable_scheduler:
            logger.info("[涩批] 定时任务已在插件配置中关闭")
            return

        scheduler = TimeBasedScheduler(
            JsonStore(self.paths.runtime_file, {}),
            logger=logger,
        )
        scheduler.add_interval_job(
            "画师订阅推送检查",
            self.settings.subscribe_check_interval,
            self.push_updates,
        )
        scheduler.add_daily_job(
            "写真ID增量更新",
            self.settings.scheduler_mzt_cron,
            self._scheduled_mzt_update,
        )
        scheduler.add_daily_job(
            "套图列表增量更新",
            self.settings.scheduler_mtb_cron,
            self._scheduled_mtb_update,
        )
        scheduler.start()
        self.scheduler = scheduler

    async def terminate(self) -> None:
        """插件被卸载/停用时停掉定时任务并清理临时文件。"""
        if self.scheduler is not None:
            await self.scheduler.stop()
            self.scheduler = None
        self.paths.cleanup_temp(0)

    # ------------------------------------------------------------------ #
    # 定时任务的具体实现
    # ------------------------------------------------------------------ #
    async def _scheduled_mzt_update(self) -> None:
        """定时增量更新写真 ID 列表。"""
        from .app_core.mzt import collect_article_ids

        existing = set(self.mzt_ids())
        discovered = await collect_article_ids(self.settings, existing)
        if not discovered:
            logger.info("[涩批] 写真ID增量更新：没有新ID")
            return
        merged = list(dict.fromkeys(discovered + list(existing)))
        self.save_mzt_ids(merged)
        logger.info(
            f"[涩批] 写真ID增量更新完成：新增 {len(discovered)} 个，"
            f"当前共 {len(merged)} 个"
        )

    async def _scheduled_mtb_update(self) -> None:
        """定时增量更新套图 URL 列表。"""
        from .app_core.mtb import collect_album_urls

        existing = self.album_urls()
        merged, total_pages = await collect_album_urls(
            self.settings,
            existing=existing,
            incremental=bool(existing),
        )
        if total_pages is None:
            logger.warning("[涩批] 套图列表增量更新：无法获取总页数")
            return
        self.save_album_urls(merged)
        logger.info(
            f"[涩批] 套图列表增量更新完成：现有 {len(merged)} 个"
            f"（原有 {len(existing)} 个）"
        )


__all__ = ["spPlugin"]
