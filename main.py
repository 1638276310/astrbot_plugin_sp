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
* ``features/pixiv_tag.py``     /来图 X 张 XX图
* ``features/mzt.py``           妹子图
* ``features/mtb.py``           美图吧套图
* ``features/magnet.py``        磁力猫 / 验车
* ``features/cos_images.py``    2图 / 3图
* ``features/subscribe.py``     画师订阅与推送
* ``app_core/``                公共能力（HTTP / 图片 / 浏览器 / 存储 / 配置 …）
"""

from __future__ import annotations

import asyncio
import random
from pathlib import Path

from astrbot.api import logger # type: ignore
from astrbot.api.all import AstrBotConfig # type: ignore
from astrbot.api.event import AstrMessageEvent, filter # type: ignore
from astrbot.api.message_components import Image, Node, Plain # type: ignore
from astrbot.api.star import Context, Star, register # type: ignore
from astrbot.core.star.star_tools import StarTools # type: ignore

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
from .features.help import build_help_text
from .features.recall import RECALL_TIME_PATTERN, R18_PATTERN, PREFERENCE_PATTERN
from .features.magnet import MAGNET_LINK_PATTERN, MAX_SCREENSHOTS
from .features.mtb import DETAIL_URL_PATTERN
from .features.pixiv_pid import PID_PATTERN
from .features.pixiv_tag import TAG_PATTERN
from .features.pixiv_artist import ARTIST_PATTERN
from .features.cos_images import CATEGORY_MAP, TYPE_NAME, IMAGE_COUNT
from .features.sj import REFERER
from .features.subscribe import SUBSCRIBE_PAT, UNSUBSCRIBE_PAT
from .app_core.http import fetch_bytes
from .app_core.imaging import add_noise, save_bytes
from .app_core.magnetcat import describe_results, parse_command, search_magnet
from .app_core.mtb import collect_album_urls, parse_detail_url
from .app_core.mzt import collect_article_ids, parse_article_id
from .app_core.paths import PLUGIN_NAME, PluginPaths
from .app_core.scheduler import TimeBasedScheduler
from .app_core.settings import (
    ORDER_LABEL,
    ORDER_MAP,
    PLUGIN_VERSION,
    R18_MODE_LABEL,
    R18_MODE_MAP,
    build_settings,
)
from .app_core.storage import JsonStore
from .app_core.verify import fetch_magnet_info


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

    def __init__(self, context: Context, config: AstrBotConfig | None = None) -> None:
        # Star.__init__(self, context, config=None)（见 astrbot/core/star/base.py）。
        # 这里显式传入 config，与 get_px 参考实现保持一致。
        # Pylance 在解析不到 astrbot 包时会把 Star 当成 object（其 __init__ 只接受
        # self），误报"context 应为 0 个位置参数"；运行时签名合法，按 PEP 8/
        # PEP 257 的注释要求说明原因后用 type: ignore 屏蔽该误报。
        super().__init__(context, config)  # type: ignore[call-arg]
        # 注入运行时依赖：配置 + 数据目录
        self.settings = build_settings(config)
        self.paths = PluginPaths(context, plugin_name=PLUGIN_NAME)
        self.setup_feature(self.settings, self.paths)
        self._subscribe_store = JsonStore(self.paths.subscribe_file, {})
        self.scheduler: TimeBasedScheduler | None = None
        logger.info(
            f"[涩批] 插件已加载，数据目录：{self.paths.root}"
        )
        logger.info(
            f"[涩批DEBUG] 插件初始化完成：pixiv_api_base={self.settings.pixiv_api_base} "
            f"dingyue_api={self.settings.dingyue_api} dingyue_key={self.settings.dingyue_key!r} "
            f"magnet_api={self.settings.magnet_api} mzt_site={self.settings.mzt_site} "
            f"mtb_site={self.settings.mtb_site} cos_api={self.settings.cos_api} "
            f"forward_as_node={self.settings.forward_as_node} "
            f"track_pixel={self.settings.track_pixel} "
            f"enable_scheduler={self.settings.enable_scheduler} "
            f"enable_id_whitelist={self.settings.enable_id_whitelist} "
            f"id_whitelist={self.settings.id_whitelist!r} "
            f"subscribe_check_interval={self.settings.subscribe_check_interval}h"
        )

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    async def initialize(self) -> None:
        """插件被激活时启动内置定时任务。

        与 get_px 保持同构：数据目录统一通过 StarTools.get_data_dir
        获取（AstrBot 官方规范路径），不再依赖 context.get_data_dir()。
        """
        logger.info("[涩批DEBUG] initialize() 开始：解析数据目录")
        data_dir = StarTools.get_data_dir(PLUGIN_NAME)
        self.paths.root = Path(data_dir)
        self.paths._ensure()
        logger.info(f"[涩批DEBUG] 数据目录解析为：{self.paths.root}")

        cleaned = self.paths.cleanup_temp(3600)
        if cleaned:
            logger.info(f"[涩批] 已清理 {cleaned} 个过期临时文件")

        if not self.settings.enable_scheduler:
            logger.info("[涩批] 定时任务已在插件配置中关闭")
            logger.info("[涩批DEBUG] initialize() 结束：定时任务未启动（配置关闭）")
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
        logger.info(
            f"[涩批DEBUG] 定时任务已启动：{scheduler.next_run_hint()}"
        )

    async def terminate(self) -> None:
        """插件被卸载/停用时停掉定时任务并清理临时文件。"""
        if self.scheduler is not None:
            await self.scheduler.stop()
            self.scheduler = None
            logger.info("[涩批DEBUG] terminate()：定时任务已停止")
        self.paths.cleanup_temp(0)
        logger.info("[涩批DEBUG] terminate()：临时文件已清理")

    # ------------------------------------------------------------------ #
    # 定时任务的具体实现
    # ------------------------------------------------------------------ #
    async def _scheduled_mzt_update(self) -> None:
        """定时增量更新写真 ID 列表。"""
        logger.info("[涩批DEBUG] 定时任务[写真ID增量更新]开始")
        existing = set(self.mzt_ids())
        logger.info(f"[涩批DEBUG] 定时任务[写真ID增量更新]现有ID数量：{len(existing)}")
        discovered = await collect_article_ids(self.settings, existing)
        if not discovered:
            logger.info("[涩批] 写真ID增量更新：没有新ID")
            logger.info("[涩批DEBUG] 定时任务[写真ID增量更新]结束：无新增")
            return
        merged = list(dict.fromkeys(discovered + list(existing)))
        self.save_mzt_ids(merged)
        logger.info(
            f"[涩批] 写真ID增量更新完成：新增 {len(discovered)} 个，"
            f"当前共 {len(merged)} 个"
        )
        logger.info(
            f"[涩批DEBUG] 定时任务[写真ID增量更新]结束：新增 {len(discovered)} 个，"
            f"当前共 {len(merged)} 个"
        )

    async def _scheduled_mtb_update(self) -> None:
        """定时增量更新套图 URL 列表。"""
        logger.info("[涩批DEBUG] 定时任务[套图列表增量更新]开始")
        existing = self.album_urls()
        logger.info(f"[涩批DEBUG] 定时任务[套图列表增量更新]现有URL数量：{len(existing)}")
        merged, total_pages = await collect_album_urls(
            self.settings,
            existing=existing,
            incremental=bool(existing),
        )
        if total_pages is None:
            logger.warning("[涩批] 套图列表增量更新：无法获取总页数")
            logger.info("[涩批DEBUG] 定时任务[套图列表增量更新]失败：无法获取总页数")
            return
        self.save_album_urls(merged)
        logger.info(
            f"[涩批] 套图列表增量更新完成：现有 {len(merged)} 个"
            f"（原有 {len(existing)} 个）"
        )
        logger.info(
            f"[涩批DEBUG] 定时任务[套图列表增量更新]结束：共 {len(merged)} 个"
            f"（原有 {len(existing)} 个）"
        )

    # ================================================================== #
    # 指令 handler（与 get_px 同构：全部挂在主类上，不用 Mixin 继承）
    # 业务方法继续委托 features/ 下 Mixin 的实现
    # ================================================================== #

    # ------------------------------------------------------------------ #
    # 帮助（features/help.py）
    # ------------------------------------------------------------------ #
    @filter.command(
        "涩批文字帮助",
        alias={"sp文字帮助", "色批文字帮助", "色胚文字帮助", "涩胚文字帮助"},
        priority=50,
    )
    async def sp_help(self, event: AstrMessageEvent):
        """涩批插件帮助（文字版 / 图片版）"""
        self.stop_event_if_needed(event)
        if not await self.guard(event):
            return

        message = event.get_message_str().strip()
        logger.info(f"[涩批DEBUG] /涩批文字帮助：消息={message!r}")
        if "图片" in message:
            async for result in self._send_help_image(event):
                yield result
            return
        logger.info("[涩批DEBUG] /涩批文字帮助：发送文字版帮助")
        yield event.plain_result(build_help_text())

    @filter.command(
        "涩批图片帮助",
        alias={"sp图片帮助", "色批图片帮助", "色胚图片帮助", "涩胚图片帮助"},
        priority=50,
    )
    async def sp_help_image(self, event: AstrMessageEvent):
        """涩批插件帮助（图片版）"""
        self.stop_event_if_needed(event)
        if not await self.guard(event):
            return
        logger.info("[涩批DEBUG] /涩批图片帮助：开始渲染/发送帮助图")
        async for result in self._send_help_image(event):
            yield result

    # ------------------------------------------------------------------ #
    # 撤回 / R18 / 图片偏好 / 状态（features/recall.py）
    # ------------------------------------------------------------------ #
    @filter.command(
        "开启sp撤回",
        alias={
            "关闭sp撤回",
            "开启涩批撤回",
            "关闭涩批撤回",
            "开启色胚撤回",
            "关闭色胚撤回",
            "开启色批撤回",
            "关闭色批撤回",
            "开启色皮撤回",
            "关闭色皮撤回",
        },
        priority=40,
    )
    async def sp_toggle_recall(self, event: AstrMessageEvent):
        """开启/关闭涩批消息撤回（/开启sp撤回 / /关闭sp撤回）"""
        self.stop_event_if_needed(event)
        if not await self.guard(event):
            return

        if not await self.require_admin(event):
            return
        message = event.get_message_str()
        enabled = message.startswith("/开启") or message.startswith("开启")
        self.settings.recall = enabled
        logger.info(
            f"[涩批DEBUG] /sp_toggle_recall：撤回功能已{'开启' if enabled else '关闭'}"
        )
        if enabled:
            yield event.plain_result(
                f"已开启撤回功能（当前撤回时间 {self.settings.recall_time} 秒）"
            )
        else:
            yield event.plain_result("已关闭撤回功能")

    @filter.command(
        "设置sp撤回",
        alias={"设置涩批撤回", "设置色皮撤回", "设置色批撤回"},
        priority=40,
    )
    async def sp_set_recall_time(self, event: AstrMessageEvent, seconds: int = 0):
        """设置涩批消息撤回时间（10-120 秒）"""
        self.stop_event_if_needed(event)
        if not await self.guard(event):
            return

        if not await self.require_admin(event):
            return

        if isinstance(seconds, int) and seconds >= 0:
            raw = str(seconds)
        else:
            m = RECALL_TIME_PATTERN.search(event.get_message_str())
            if not m:
                yield event.plain_result("用法：/设置sp撤回 60（10-120 秒）")
                return
            raw = m.group(1) or ""
        seconds = int(raw)
        if seconds < 10 or seconds > 120:
            yield event.plain_result("建议设置为10-120秒哦")
            return
        self.settings.recall_time = seconds
        logger.info(f"[涩批DEBUG] /sp_set_recall_time：撤回时间已设置为 {seconds} 秒")
        yield event.plain_result(f"已设置撤回时间为{seconds}秒")

    @filter.command(
        "设置R18模式",
        alias={"设置r18模式"},
        priority=40,
    )
    async def sp_set_r18_mode(self, event: AstrMessageEvent, mode: int = -1):
        """设置 R18 模式（0 全部 / 1 非R18 / 2 R18）"""
        self.stop_event_if_needed(event)
        if not await self.guard(event):
            return

        if not await self.require_admin(event):
            return

        if mode >= 0 and mode in (0, 1, 2):
            raw = str(mode)
        else:
            m = R18_PATTERN.search(event.get_message_str())
            if not m:
                yield event.plain_result("用法：/设置R18模式 2（0:全部 1:非R18 2:R18）")
                return
            raw = m.group(1) or ""

        # 参数 mode 是 int（指令尾随数字），r18_key 是字符串映射结果，两者区分开
        r18_key = R18_MODE_MAP.get(raw, "all")
        self.settings.r18_mode = r18_key
        logger.info(
            f"[涩批DEBUG] /sp_set_r18_mode：R18模式已设置为 {r18_key}"
            f"（原始值 {raw}）"
        )
        yield event.plain_result(
            f"已设置R18模式为{r18_key}（{R18_MODE_LABEL.get(r18_key, r18_key)}）\n"
            "0:全部    1:非R18    2:R18"
        )

    @filter.command("设置图片偏好", priority=40)
    async def sp_set_image_preference(self, event: AstrMessageEvent, pref: int = -1):
        """设置图片偏好（0 无偏好 / 1 男性 / 2 女性）"""
        self.stop_event_if_needed(event)
        if not await self.guard(event):
            return

        if not await self.require_admin(event):
            return

        if pref >= 0 and pref in (0, 1, 2):
            raw = str(pref)
        else:
            m = PREFERENCE_PATTERN.search(event.get_message_str())
            if not m:
                yield event.plain_result("用法：/设置图片偏好 1（0:无 1:男 2:女）")
                return
            raw = m.group(1) or ""

        order = ORDER_MAP.get(raw, "popular_d")
        self.settings.image_preference = order
        logger.info(
            f"[涩批DEBUG] /sp_set_image_preference：图片偏好已设置为 {order}"
            f"（原始值 {raw}）"
        )
        yield event.plain_result(
            f"已设置图片偏好为{order}（{ORDER_LABEL.get(order, order)}）\n"
            "0:无偏好    1:男性偏好    2:女性偏好"
        )

    @filter.command("sp状态", priority=40)
    async def sp_status(self, event: AstrMessageEvent):
        """查看涩批插件运行状态"""
        self.stop_event_if_needed(event)
        if not await self.guard(event):
            return

        lines = [
            "【涩批插件状态】",
            f"撤回：{'开启' if self.settings.recall else '关闭'}"
            f"（{self.settings.recall_time} 秒）",
            f"R18模式：{self.settings.r18_mode}"
            f"（{R18_MODE_LABEL.get(self.settings.r18_mode, '')}）",
            f"图片偏好：{self.settings.image_preference}"
            f"（{ORDER_LABEL.get(self.settings.image_preference, '')}）",
            f"合并转发：{'开启' if self.settings.forward_as_node else '关闭'}",
            f"图片噪点：{'开启' if self.settings.track_pixel else '关闭'}",
            f"数据目录：{self.paths.root}",
        ]
        scheduler = getattr(self, "scheduler", None)
        if scheduler is not None:
            hints = scheduler.next_run_hint()
            lines.append("定时任务：" + ("；".join(hints) if hints else "未启用"))
        logger.info(f"[涩批DEBUG] /sp_status：定时任务{'已启动' if scheduler else '未启动'}")
        yield event.plain_result("\n".join(lines))

    # ------------------------------------------------------------------ #
    # 网址导航（features/urls.py）
    # ------------------------------------------------------------------ #
    @filter.command(
        "写真网址",
        alias={"福利网址", "吃瓜网址", "导航网址", "福利App", "福利APP", "福利app", "TG电报"},
        priority=30,
    )
    async def sp_send_urls(self, event: AstrMessageEvent):
        """获取各类网站地址（写真/福利/吃瓜/导航/福利App/TG电报）"""
        self.stop_event_if_needed(event)
        if not await self.guard(event):
            return

        command = event.get_message_str().strip().lstrip("/")
        group = self._resolve_url_group(command)
        if group is None:
            logger.info(f"[涩批DEBUG] /sp_send_urls：未识别的网址指令 {command!r}")
            yield event.plain_result("未识别的网址类型。")
            return

        urls = self.settings.url_group(group)
        if not urls:
            logger.info(f"[涩批DEBUG] /sp_send_urls：分组 {group!r} 的网址列表为空")
            yield event.plain_result(
                f"「{group}」的网址列表为空，请在插件配置 url_groups 中补充。"
            )
            return

        header = [
            f"📱 {group}网址集合",
            f"📊 共 {len(urls)} 个{group}网站",
            "",
        ]
        body = [f"{index}. {url}" for index, url in enumerate(urls, start=1)]
        footer = [
            "",
            "⚠️ 提示：网址仅供参考，请谨慎访问",
            f"/{group}网址 可再次获取",
            "请复制链接到浏览器打开，切勿直接点击",
        ]
        text = "\n".join(header + body + footer)
        logger.info(
            f"[涩批DEBUG] /sp_send_urls：分组 {group!r} 共 {len(urls)} 个网址，"
            f"forward_as_node={self.settings.forward_as_node}"
        )

        if not self.settings.forward_as_node:
            yield event.plain_result(text)
            return

        try:
            # 整个列表作为一个合并转发节点（而非每行一个节点），
            # 避免"每行一条消息"的观感
            nodes = self.text_nodes(event, [text])
            logger.info(f"[涩批DEBUG] /sp_send_urls：以合并转发节点发送")
            yield event.chain_result([self.wrap_nodes(nodes)])
        except Exception as exc:
            logger.warning(f"[涩批DEBUG] /sp_send_urls：合并转发发送失败，回退为纯文本：{exc!r}")
            yield event.plain_result(text)

    # ------------------------------------------------------------------ #
    # 随机短视频（features/sj.py）
    # ------------------------------------------------------------------ #
    @filter.command("骚鸡", alias={"烧鸡", "sj"}, priority=20)
    async def sp_random_video(self, event: AstrMessageEvent):
        """随机发送一个涩批视频"""
        self.stop_event_if_needed(event)
        if not await self.guard(event):
            return

        urls = self.video_urls()
        if not urls:
            logger.info(
                f"[涩批DEBUG] /sp_random_video：视频列表为空"
                f"（{self.paths.video_urls_file}）"
            )
            yield event.plain_result(
                f"视频列表为空，请在数据目录 {self.paths.video_urls_file} 中补充视频直链。"
            )
            return

        url = random.choice(urls)
        target = self.temp_path(f"sp_video_{random.randint(1000, 9999)}.mp4")
        logger.info(
            f"[涩批DEBUG] /sp_random_video：共 {len(urls)} 个视频直链，随机取 {url[:80]}..."
        )
        try:
            data = await fetch_bytes(
                url,
                timeout=90,
                referer=REFERER,
                max_size=200 * 1024 * 1024,
            )
            target.write_bytes(data)
            logger.info(
                f"[涩批DEBUG] /sp_random_video：视频下载成功 {len(data)} 字节 -> {target}"
            )
        except Exception as exc:
            logger.error(f"[涩批DEBUG] /sp_random_video：视频下载失败：{exc!r}")
            yield event.plain_result(f"视频发送失败，请稍后再试（{exc}）")
            return

        try:
            yield event.chain_result([self.video_component(target)])
        except Exception as exc:
            logger.warning(
                f"[涩批DEBUG] /sp_random_video：视频消息发送失败，回退提示：{exc!r}"
            )
            yield event.plain_result("视频发送失败，请稍后再试")
        finally:
            self.safe_unlink(target)

    # ------------------------------------------------------------------ #
    # P 站：/pid（features/pixiv_pid.py）
    # ------------------------------------------------------------------ #
    @filter.command("pid", priority=20)
    async def sp_pixiv_pid(self, event: AstrMessageEvent, pid: str = ""):
        """按 PID 获取 P 站作品（/pid <数字>）"""
        self.stop_event_if_needed(event)
        if not await self.guard(event):
            return

        if not pid:
            m = PID_PATTERN.search(event.get_message_str())
            if not m:
                logger.info("[涩批DEBUG] /pid：未能从消息中解析出 PID")
                yield event.plain_result("用法：/pid <数字>，例如 /pid 123456")
                return
            # group(1) 类型是 str | None；正则已命中且该分组必匹配，用 or "" 兜底
            pid = m.group(1) or ""

        logger.info(f"[涩批DEBUG] /pid：准备获取作品 PID={pid}")
        yield event.plain_result("正在搜索，请稍等...")

        try:
            details = await self.pixiv.fetch_illust(pid)
            if not details or not details.get("body"):
                logger.info(f"[涩批DEBUG] /pid：PID={pid} 无详情数据")
                yield event.plain_result("请输入正确的pid")
                return

            body = details["body"]
            urls = self.image_urls(body)
            if not urls:
                logger.info(f"[涩批DEBUG] /pid：PID={pid} 作品没有可用图片地址")
                yield event.plain_result("该作品没有可用的图片地址")
                return

            logger.info(f"[涩批DEBUG] /pid：PID={pid} 图片数量：{len(urls)}")
            paths = await self.download_images(urls)
            if not paths:
                logger.info(f"[涩批DEBUG] /pid：PID={pid} 图片全部下载失败")
                yield event.plain_result("图片下载失败，请稍后再试")
                return

            logger.info(
                f"[涩批DEBUG] /pid：PID={pid} 成功下载 {len(paths)} 张图片，准备发送"
            )
            text = self.work_text(body)
            for result in await self.send_works(event, [(text, paths)]):
                yield result
        except Exception as exc:
            logger.error(f"[涩批DEBUG] /pid：发生错误：{exc!r}")
            yield event.plain_result(f"发生错误：{exc}")

    # ------------------------------------------------------------------ #
    # P 站：/随机X张Y作品（features/pixiv_artist.py）
    # ------------------------------------------------------------------ #
    @filter.command("随机", priority=20)
    async def sp_pixiv_random_artist(self, event: AstrMessageEvent):
        """随机获取画师作品（/随机 X 张 Y 作品，X ≤ 20）"""
        self.stop_event_if_needed(event)
        if not await self.guard(event):
            return

        parsed = ARTIST_PATTERN.search(event.get_message_str().strip())
        if not parsed:
            logger.info("[涩批DEBUG] /随机：未能解析 张数/画师ID 参数")
            yield event.plain_result("用法：/随机 X 张 Y 作品，例如 /随机 3 张 123456 作品")
            return
        count = int(parsed.group(1) or 0)
        artist_id = parsed.group(2) or ""

        if count <= 0:
            logger.info(f"[涩批DEBUG] /随机：张数 {count} 非法（需 > 0）")
            yield event.plain_result("张数需要大于 0 哦")
            return
        if count > 20:
            logger.info(f"[涩批DEBUG] /随机：张数 {count} 超过上限 20")
            yield event.plain_result("一次最多看20张哦")
            return

        logger.info(
            f"[涩批DEBUG] /随机：画师 {artist_id}，目标张数 {count}，开始获取画师详情"
        )
        yield event.plain_result("正在搜索，请稍等...")

        try:
            artist = await self.pixiv.fetch_artist(artist_id)
            if not artist or artist.get("error") or not artist.get("body"):
                logger.info(f"[涩批DEBUG] /随机：画师 {artist_id} 详情获取失败")
                yield event.plain_result("请输入正确的画师ID")
                return

            body = artist.get("body") or {}
            illusts = body.get("illusts")
            if not isinstance(illusts, dict) or not illusts:
                logger.info(f"[涩批DEBUG] /随机：画师 {artist_id} 没有可用作品")
                yield event.plain_result("该画师没有可用的作品")
                return

            work_ids = list(illusts.keys())
            random.shuffle(work_ids)
            selected = work_ids[:count]
            logger.info(
                f"[涩批DEBUG] /随机：画师 {artist_id} 共 {len(work_ids)} 个作品，"
                f"随机选中 {len(selected)} 个：{selected}"
            )

            works: list[tuple[str, list[str]]] = []
            for work_id in selected:
                details = await self.pixiv.fetch_illust(work_id)
                if not details or not details.get("body"):
                    logger.info(f"[涩批DEBUG] /随机：作品 {work_id} 无详情数据，跳过")
                    continue
                work_body = details["body"]
                urls = self.image_urls(work_body)
                if not urls:
                    logger.info(f"[涩批DEBUG] /随机：作品 {work_id} 无图片地址，跳过")
                    continue
                paths = await self.download_images(urls)
                if not paths:
                    logger.info(f"[涩批DEBUG] /随机：作品 {work_id} 图片下载失败，跳过")
                    continue
                works.append((self.work_text(work_body), paths))

            if not works:
                logger.info(f"[涩批DEBUG] /随机：画师 {artist_id} 没有获取到任何作品")
                yield event.plain_result("没有获取到作品，请稍后再试")
                return

            logger.info(
                f"[涩批DEBUG] /随机：画师 {artist_id} 共 {len(works)} 个作品成功获取，准备发送"
            )
            for result in await self.send_works(event, works):
                yield result
        except Exception as exc:
            logger.error(f"[涩批DEBUG] /随机：发生错误：{exc!r}")
            yield event.plain_result(f"发生错误：{exc}")

    # ------------------------------------------------------------------ #
    # P 站：/来图 X 张 XX图（features/pixiv_tag.py）
    # ------------------------------------------------------------------ #
    @filter.command("来图", priority=20)
    async def sp_pixiv_tag(self, event: AstrMessageEvent):
        """按标签搜索 P 站图片（/来图 X 张 XX图，X ≤ 60）"""
        self.stop_event_if_needed(event)
        if not await self.guard(event):
            return

        parsed = TAG_PATTERN.search(event.get_message_str().strip())
        if not parsed:
            logger.info("[涩批DEBUG] /来图：未能解析 张数/标签 参数")
            yield event.plain_result("用法：/来图 X 张 XX图，例如 /来图 10 张 白丝图")
            return
        count = int(parsed.group(1))
        tag = (parsed.group(2) or "").strip()

        if count <= 0:
            logger.info(f"[涩批DEBUG] /来图：张数 {count} 非法（需 > 0）")
            yield event.plain_result("张数需要大于 0 哦")
            return
        if count > 60:
            logger.info(f"[涩批DEBUG] /来图：张数 {count} 超过上限 60")
            yield event.plain_result("你想冲死吗？")
            return

        logger.info(
            f"[涩批DEBUG] /来图：标签={tag!r} 目标张数={count} "
            f"r18_mode={self.settings.r18_mode} order={self.settings.image_preference}"
        )
        yield event.plain_result("正在搜索，请稍等...")

        try:
            ids = await self.pixiv.fetch_tag_ids(
                tag,
                mode=self.settings.r18_mode,
                order=self.settings.image_preference,
            )
            if not ids:
                logger.info(f"[涩批DEBUG] /来图：标签 {tag!r} 没有搜到任何ID")
                yield event.plain_result("没有这种图啊，涩批！")
                return

            selected = self._random_ids(ids, count)
            logger.info(
                f"[涩批DEBUG] /来图：标签 {tag!r} 共搜到 {len(ids)} 个ID，"
                f"随机选中 {len(selected)} 个"
            )
            works: list[tuple[str, list[str]]] = []
            for pid in selected:
                details = await self.pixiv.fetch_illust(pid)
                if not details or not details.get("body"):
                    logger.info(f"[涩批DEBUG] /来图：作品 {pid} 无详情数据，跳过")
                    continue
                body = details["body"]
                urls = self.image_urls(body)[:5]
                if not urls:
                    logger.info(f"[涩批DEBUG] /来图：作品 {pid} 无图片地址，跳过")
                    continue
                paths = await self.download_images(urls)
                if not paths:
                    logger.info(f"[涩批DEBUG] /来图：作品 {pid} 图片下载失败，跳过")
                    continue
                works.append((self.work_text(body), paths))

            if not works:
                logger.info(f"[涩批DEBUG] /来图：标签 {tag!r} 没有获取到任何图片")
                yield event.plain_result("没有获取到图片，请稍后再试")
                return

            logger.info(
                f"[涩批DEBUG] /来图：标签 {tag!r} 共 {len(works)} 个作品成功获取，准备发送"
            )
            for result in await self.send_works(event, works):
                yield result
            yield event.plain_result("所有图片发送完毕！")
        except Exception as exc:
            logger.error(f"[涩批DEBUG] /来图：发生错误：{exc!r}")
            yield event.plain_result(f"发生错误：{exc}")

    # ------------------------------------------------------------------ #
    # 妹子图（features/mzt.py）
    # ------------------------------------------------------------------ #
    @filter.command("写真馆", priority=20)
    async def sp_mzt_album(self, event: AstrMessageEvent):
        """获取妹子图写真（/写真馆 <ID>）"""
        self.stop_event_if_needed(event)
        if not await self.guard(event):
            return

        article_id = parse_article_id(event.get_message_str())
        if not article_id:
            logger.info("[涩批DEBUG] /写真馆：未能从消息中解析出文章ID")
            yield event.plain_result("用法：/写真馆 <ID>，例如 /写真馆 12345")
            return
        logger.info(f"[涩批DEBUG] /写真馆：开始获取文章ID={article_id}")
        yield event.plain_result("正在搜索，请稍等...")
        async for result in self._send_mzt_album(event, article_id):
            yield result

    @filter.command("随机写真", priority=25)
    async def sp_mzt_random(self, event: AstrMessageEvent):
        """随机获取妹子图（/随机写真）。
        优先级高于 "随机"（priority=20），消除两个指令的前缀冲突：
        AstrBot 对前缀匹配时，优先级高的 handler 先命中。
        """
        self.stop_event_if_needed(event)
        if not await self.guard(event):
            return

        ids = self.mzt_ids()
        if not ids:
            logger.info("[涩批DEBUG] /随机写真：写真ID列表为空")
            yield event.plain_result("写真ID列表为空，请先使用 /更新写真ID")
            return
        article_id = random.choice(ids)
        logger.info(f"[涩批DEBUG] /随机写真：随机抽取ID={article_id}")
        yield event.plain_result(f"写真ID：{article_id} 正在搜索，请稍等...")
        async for result in self._send_mzt_album(event, article_id):
            yield result

    @filter.command("更新写真ID", alias={"更新写真id"}, priority=20)
    async def sp_mzt_update_ids(self, event: AstrMessageEvent):
        """增量更新写真ID列表（仅主人可用）"""
        self.stop_event_if_needed(event)
        if not await self.guard(event):
            return

        if not await self.require_admin(event):
            return
        logger.info("[涩批DEBUG] /更新写真ID：开始增量更新")
        yield event.plain_result("开始增量更新写真ID列表，这可能需要几分钟时间...")

        existing = set(self.mzt_ids())
        logger.info(f"[涩批DEBUG] /更新写真ID：现有ID数量 {len(existing)}")
        try:
            discovered = await collect_article_ids(self.settings, existing)
        except Exception as exc:
            logger.error(f"[涩批DEBUG] /更新写真ID：采集失败 {exc!r}")
            yield event.plain_result(f"更新失败: {exc}")
            return

        if discovered:
            merged = list(dict.fromkeys(discovered + list(existing)))
            self.save_mzt_ids(merged)
            logger.info(
                f"[涩批DEBUG] /更新写真ID：新增 {len(discovered)} 个，"
                f"当前共 {len(merged)} 个"
            )
            yield event.plain_result(
                f"写真ID增量更新完成：新增 {len(discovered)} 个，"
                f"当前共 {len(merged)} 个"
            )
        else:
            logger.info("[涩批DEBUG] /更新写真ID：没有发现新ID")
            yield event.plain_result("写真ID增量更新：没有新ID")

    # ------------------------------------------------------------------ #
    # 美图吧（features/mtb.py）
    # ------------------------------------------------------------------ #
    @filter.command("随机美图吧", priority=25)
    async def sp_mtb_random(self, event: AstrMessageEvent):
        """随机解析一套美图吧套图（/随机美图吧）"""
        self.stop_event_if_needed(event)
        if not await self.guard(event):
            return

        urls = self.album_urls()
        if not urls:
            logger.info("[涩批DEBUG] /随机美图吧：套图URL列表为空")
            yield event.plain_result("套图URL列表为空，请先使用 /更新套图列表")
            return
        url = random.choice(urls)
        logger.info(
            f"[涩批DEBUG] /随机美图吧：共 {len(urls)} 个URL，随机取 {url}"
        )
        logger.info(f"[涩批DEBUG] /随机美图吧：yield 提示语：正在随机抽取一套美图，请稍等...")
        yield event.plain_result("正在随机抽取一套美图，请稍等...")
        logger.info(f"[涩批DEBUG] /随机美图吧：提示语已发送，即将进入 _send_album")
        async for result in self._send_album(event, url):
            logger.info(f"[涩批DEBUG] /随机美图吧：_send_album 产出一个结果，转发中")
            yield result
        logger.info(f"[涩批DEBUG] /随机美图吧：_send_album 全部完成，handler 结束")

    @filter.command("套图详情", priority=25)
    async def sp_mtb_detail(self, event: AstrMessageEvent, url: str = ""):
        """解析指定美图吧套图链接（/套图详情 <URL>）"""
        self.stop_event_if_needed(event)
        if not await self.guard(event):
            return

        if not url:
            m = DETAIL_URL_PATTERN.search(event.get_message_str())
            url = (m.group(1) or "") if m else ""
        if not url or not url.startswith("http"):
            logger.info("[涩批DEBUG] /套图详情：未识别出有效URL")
            yield event.plain_result("用法：/套图详情 <URL>")
            return
        parsed = parse_detail_url(url)
        if not parsed:
            logger.info(f"[涩批DEBUG] /套图详情：URL {url} 解析失败")
            yield event.plain_result("URL 格式不正确")
            return
        logger.info(f"[涩批DEBUG] /套图详情：解析URL成功 {url}")
        logger.info(f"[涩批DEBUG] /套图详情：yield 提示语：正在解析套图，请稍等...")
        yield event.plain_result("正在解析套图，请稍等...")
        logger.info(f"[涩批DEBUG] /套图详情：提示语已发送，即将进入 _send_album")
        async for result in self._send_album(event, parsed):
            logger.info(f"[涩批DEBUG] /套图详情：_send_album 产出一个结果，转发中")
            yield result
        logger.info(f"[涩批DEBUG] /套图详情：_send_album 全部完成，handler 结束")

    @filter.command("更新套图列表", priority=25)
    async def sp_mtb_incremental_update(self, event: AstrMessageEvent):
        """增量更新套图列表（仅主人可用）"""
        self.stop_event_if_needed(event)
        if not await self.guard(event):
            return

        if not await self.require_admin(event):
            return
        existing = self.album_urls()
        if not existing:
            logger.info("[涩批DEBUG] /更新套图列表：列表为空，转全量更新")
            yield event.plain_result("套图URL列表为空，自动转为全量更新...")
            async for result in self._full_update(event):
                yield result
            return

        logger.info(f"[涩批DEBUG] /更新套图列表：增量更新开始，现有 {len(existing)} 个")
        yield event.plain_result("开始增量更新套图URL列表（只采集最新页面）...")
        try:
            merged, total_pages = await collect_album_urls(
                self.settings, existing=existing, incremental=True
            )
        except Exception as exc:
            logger.error(f"[涩批DEBUG] /更新套图列表：增量更新失败 {exc!r}")
            yield event.plain_result(f"增量更新失败: {exc}")
            return

        if total_pages is None:
            logger.warning("[涩批DEBUG] /更新套图列表：无法获取总页数，增量更新终止")
            yield event.plain_result("无法获取总页数，增量更新终止")
            return

        added = len(merged) - len(existing)
        self.save_album_urls(merged)
        logger.info(
            f"[涩批DEBUG] /更新套图列表：增量更新完成，新增 {max(0, added)} 个，"
            f"共 {len(merged)} 个"
        )
        yield event.plain_result(
            f"增量更新完成！本次新增 {max(0, added)} 个套图"
            f"（现有总计 {len(merged)} 个，原为 {len(existing)} 个）"
        )

    @filter.command("全量更新套图列表", priority=25)
    async def sp_mtb_full_update(self, event: AstrMessageEvent):
        """全量更新套图列表（仅主人可用）"""
        self.stop_event_if_needed(event)
        if not await self.guard(event):
            return

        if not await self.require_admin(event):
            return
        logger.info("[涩批DEBUG] /全量更新套图列表：开始全量更新")
        async for result in self._full_update(event):
            yield result

    # ------------------------------------------------------------------ #
    # 磁力（features/magnet.py）
    # ------------------------------------------------------------------ #
    @filter.command("验车", priority=20)
    async def sp_verify_magnet(self, event: AstrMessageEvent, magnet: str = ""):
        """查询磁力链接详情（/验车 magnet:...）"""
        self.stop_event_if_needed(event)
        if not await self.guard(event):
            return

        if not magnet:
            m = MAGNET_LINK_PATTERN.search(event.get_message_str())
            if not m:
                logger.info("[涩批DEBUG] /验车：未能从消息中解析出磁力链接")
                yield event.plain_result("用法：/验车 <magnet:...>")
                return
            magnet = m.group(1) or ""
        if not magnet or not magnet.startswith("magnet:"):
            logger.info(f"[涩批DEBUG] /验车：参数 {magnet!r} 不是合法的磁力链接")
            yield event.plain_result("用法：/验车 <magnet:...>")
            return

        logger.info(f"[涩批DEBUG] /验车：开始查询 {magnet[:80]}...")
        yield event.plain_result("正在验车，请稍等...")

        info = None
        last_error = ""
        for attempt in range(1, 4):
            try:
                info = await fetch_magnet_info(self.settings, magnet)
                if info is not None:
                    break
                last_error = "无效的响应数据"
                logger.info(
                    f"[涩批DEBUG] /验车：第 {attempt} 次尝试返回无效响应数据"
                )
            except Exception as exc:
                last_error = str(exc)
                logger.info(
                    f"[涩批DEBUG] /验车：第 {attempt} 次尝试异常：{exc!r}"
                )
            if attempt < 3:
                await asyncio.sleep(2.0)

        if info is None:
            logger.warning(f"[涩批DEBUG] /验车：3次尝试后仍未查到数据：{last_error}")
            yield event.plain_result(f"查询失败: {last_error or '未知错误'}")
            return

        logger.info(
            f"[涩批DEBUG] /验车：查询成功，文件 {info.name}，"
            f"类型 {info.file_type}，数量 {info.count}，"
            f"大小 {info.size_gb}GB，截图 {len(info.screenshots)} 张"
        )
        yield event.plain_result(info.describe(magnet))

        screenshots = info.screenshots[:MAX_SCREENSHOTS]
        if not screenshots:
            logger.info("[涩批DEBUG] /验车：无截图，跳过图片发送")
            return

        paths: list[str] = []
        for index, shot_url in enumerate(screenshots):
            data = await self.file_cache.get(
                shot_url,
                referer="https://whatslink.info/",
                timeout=self.settings.download_timeout,
                use_memory=False,
            )
            if not data:
                logger.info(
                    f"[涩批DEBUG] /验车：截图 {index} 下载失败 {shot_url[:80]}"
                )
                continue
            if self.settings.track_pixel:
                data = add_noise(data)
            target = self.temp_path(f"verify_{index}_{random.randint(1000, 9999)}.jpg")
            try:
                save_bytes(target, data)
            except OSError as exc:
                logger.info(
                    f"[涩批DEBUG] /验车：截图 {index} 保存失败：{exc!r}"
                )
                continue
            paths.append(str(target))

        if not paths:
            logger.info("[涩批DEBUG] /验车：所有截图均未成功落盘，跳过图片发送")
            return

        logger.info(
            f"[涩批DEBUG] /验车：成功获取 {len(paths)} 张截图，"
            f"forward_as_node={self.settings.forward_as_node}"
        )
        if self.settings.forward_as_node:
            sender_name = event.get_sender_name() or "涩批"
            self_id = str(event.get_self_id() or "0")
            merged: list = []
            for index, path in enumerate(paths, start=1):
                merged.append(
                    Node(content=[Plain(text=f"截图 {index}")], name=sender_name, uin=self_id)
                )
                merged.append(
                    Node(
                        content=[Image.fromFileSystem(path)],
                        name=sender_name,
                        uin=self_id,
                    )
                )
            for batch in self.build_batches(merged):
                yield event.chain_result([self.wrap_nodes(batch)])
        else:
            yield event.chain_result(self.image_chain(paths))

    @filter.command("磁力猫", priority=20)
    async def sp_magnet_cat(
        self,
        event: AstrMessageEvent,
        keyword: str = "",
        file_type: str = "",
        order: str = "",
        count: int = 10,
    ):
        """磁力猫搜索（/磁力猫 关键词 [类型] [排序] [数量]）"""
        self.stop_event_if_needed(event)
        if not await self.guard(event):
            return

        if not keyword:
            parsed = parse_command(event.get_message_str())
            if not parsed:
                logger.info("[涩批DEBUG] /磁力猫：未能解析出搜索关键词")
                yield event.plain_result("用法：/磁力猫 <关键词> [类型] [排序] [数量]")
                return
            keyword, file_type, order, count = parsed
        else:
            try:
                count = int(count) if str(count).isdigit() else 10
            except (ValueError, TypeError):
                count = 10
            file_type = str(file_type or "")
            order = str(order or "")

        if count <= 0:
            count = 10
        count = min(count, 50)

        logger.info(
            f"[涩批DEBUG] /磁力猫：关键词={keyword!r} 类型={file_type!r} "
            f"排序={order!r} 数量={count}"
        )
        yield event.plain_result("正在搜索，请稍等...")

        try:
            results, error = await search_magnet(
                self.settings,
                keyword,
                file_type=file_type,
                order=order,
                count=count,
            )
        except Exception as exc:
            logger.error(f"[涩批DEBUG] /磁力猫：搜索异常 {exc!r}")
            yield event.plain_result(f"搜索失败: {exc}")
            return

        if not results:
            logger.warning(f"[涩批DEBUG] /磁力猫：无搜索结果，error={error!r}")
            yield event.plain_result(error or "所有链接均无搜索结果")
            return

        texts = describe_results(results)
        logger.info(
            f"[涩批DEBUG] /磁力猫：共 {len(results)} 条结果，"
            f"forward_as_node={self.settings.forward_as_node}"
        )
        if self.settings.forward_as_node:
            nodes = self.text_nodes(event, texts)
            for batch in self.build_batches(nodes):
                yield event.chain_result([self.wrap_nodes(batch)])
        else:
            for text in texts:
                yield event.plain_result(text)

    # ------------------------------------------------------------------ #
    # 2图 / 3图（features/cos_images.py）
    # ------------------------------------------------------------------ #
    @filter.command("2图", priority=60)
    async def sp_cos_images(self, event: AstrMessageEvent):
        """获取二次元图包（/2图）"""
        self.stop_event_if_needed(event)
        if not await self.guard(event):
            return
        logger.info(f"[涩批DEBUG] /2图：开始获取二次元图包")
        async for result in self._send_cos(event, "2图"):
            yield result

    @filter.command("3图", priority=60)
    async def sp_cos_images_3d(self, event: AstrMessageEvent):
        """获取三次元/现实图包（/3图）"""
        self.stop_event_if_needed(event)
        if not await self.guard(event):
            return
        logger.info(f"[涩批DEBUG] /3图：开始获取现实图包")
        async for result in self._send_cos(event, "3图"):
            yield result

    # ------------------------------------------------------------------ #
    # 订阅与推送（features/subscribe.py）
    # ------------------------------------------------------------------ #
    @filter.command("订阅画师", priority=20)
    async def sp_subscribe(self, event: AstrMessageEvent, artist_id: str = ""):
        """订阅画师更新（/订阅画师 <ID>）"""
        self.stop_event_if_needed(event)
        if not await self.guard(event):
            return

        if not artist_id:
            m = SUBSCRIBE_PAT.search(event.get_message_str())
            if not m:
                logger.info("[涩批DEBUG] /订阅画师：未能从消息中解析出画师ID")
                yield event.plain_result("用法：/订阅画师 <画师ID>")
                return
            artist_id = m.group(1) or ""

        session = self.session_key(event)
        data = self.load_data()
        logger.info(
            f"[涩批DEBUG] /订阅画师：会话={session!r} 画师ID={artist_id!r} "
            f"当前订阅总数={len(data)}"
        )

        if (
            self.settings.enable_group_limit
            and session not in data
            and len(data) >= self.settings.max_subscribe_sessions
        ):
            logger.info(
                f"[涩批DEBUG] /订阅画师：会话 {session} 超出上限 "
                f"{self.settings.max_subscribe_sessions}"
            )
            yield event.plain_result("已达到会话订阅上限！")
            return

        entry = data.setdefault(session, {"pushEnabled": False, "artists": {}, "umo": ""})
        artists = entry.setdefault("artists", {})
        if (
            self.settings.enable_group_limit
            and len(artists) >= self.settings.max_artists_per_session
        ):
            logger.info(
                f"[涩批DEBUG] /订阅画师：会话 {session} 画师数 "
                f"{len(artists)} 超出上限 {self.settings.max_artists_per_session}"
            )
            yield event.plain_result("该会话已达到画师订阅上限！")
            return

        if artist_id in artists:
            logger.info(f"[涩批DEBUG] /订阅画师：画师 {artist_id} 已在会话 {session} 中")
            yield event.plain_result(f"已经订阅了{artist_id}")
            return

        yield event.plain_result("正在检查画师ID，请稍等...")

        artist = await self.pixiv.fetch_artist(artist_id)
        if not artist or artist.get("error"):
            logger.warning(f"[涩批DEBUG] /订阅画师：画师 {artist_id} 不存在")
            yield event.plain_result("该画师id不存在")
            return

        artist_name = artist_id
        body = artist.get("body")
        if isinstance(body, dict):
            pickup = body.get("pickup")
            if isinstance(pickup, list) and pickup:
                first = pickup[0]
                if isinstance(first, dict) and first.get("userName"):
                    artist_name = str(first["userName"])

        artists[artist_id] = artist_name
        entry["umo"] = event.unified_msg_origin
        self.save_data(data)
        logger.info(
            f"[涩批DEBUG] /订阅画师：会话 {session} 成功订阅 {artist_id}（{artist_name}）"
        )
        yield event.plain_result(f"成功订阅画师{artist_id}（{artist_name}）")

    @filter.command("取消订阅", priority=20)
    async def sp_unsubscribe(self, event: AstrMessageEvent, artist_id: str = ""):
        """取消订阅画师（/取消订阅 <ID>）"""
        self.stop_event_if_needed(event)
        if not await self.guard(event):
            return

        if not artist_id:
            m = UNSUBSCRIBE_PAT.search(event.get_message_str())
            if not m:
                logger.info("[涩批DEBUG] /取消订阅：未能从消息中解析出画师ID")
                yield event.plain_result("用法：/取消订阅 <画师ID>")
                return
            artist_id = m.group(1) or ""
        session = self.session_key(event)
        data = self.load_data()
        entry = data.get(session)
        if not entry or artist_id not in (entry.get("artists") or {}):
            logger.info(
                f"[涩批DEBUG] /取消订阅：会话 {session} 未订阅 {artist_id}"
            )
            yield event.plain_result(f"还未订阅{artist_id}哦")
            return
        entry["artists"].pop(artist_id, None)
        self.save_data(data)
        logger.info(
            f"[涩批DEBUG] /取消订阅：会话 {session} 成功取消订阅 {artist_id}"
        )
        yield event.plain_result(f"成功取消订阅{artist_id}")

    @filter.command("订阅列表", priority=20)
    async def sp_subscribe_list(self, event: AstrMessageEvent):
        """查看本会话已订阅的画师（/订阅列表）"""
        self.stop_event_if_needed(event)
        if not await self.guard(event):
            return

        session = self.session_key(event)
        data = self.load_data()
        entry = data.get(session) or {}
        artists = entry.get("artists") or {}
        if not artists:
            logger.info(f"[涩批DEBUG] /订阅列表：会话 {session} 没有订阅任何画师")
            yield event.plain_result("当前没有订阅任何画师")
            return
        lines = ["订阅列表："]
        for artist_id, artist_name in artists.items():
            lines.append(f"{artist_name}  {artist_id}")
        lines.append(f"推送状态：{'已开启' if entry.get('pushEnabled') else '已关闭'}")
        logger.info(f"[涩批DEBUG] /订阅列表：会话 {session} 共 {len(artists)} 个画师")
        yield event.plain_result("\n".join(lines))

    @filter.command("sp推送", priority=20)
    async def sp_enable_push(self, event: AstrMessageEvent):
        """开启画师更新推送（/sp推送）"""
        self.stop_event_if_needed(event)
        if not await self.guard(event):
            return

        session = self.session_key(event)
        data = self.load_data()
        entry = data.setdefault(session, {"pushEnabled": False, "artists": {}, "umo": ""})
        if entry.get("pushEnabled"):
            logger.info(f"[涩批DEBUG] /sp推送：会话 {session} 已开启推送")
            yield event.plain_result("已经开启了sp推送。")
            return
        entry["pushEnabled"] = True
        entry["umo"] = event.unified_msg_origin
        self.save_data(data)
        logger.info(f"[涩批DEBUG] /sp推送：会话 {session} 成功开启推送")
        yield event.plain_result("已开启sp推送。")

    @filter.command("关闭sp推送", priority=20)
    async def sp_disable_push(self, event: AstrMessageEvent):
        """关闭画师更新推送（/关闭sp推送）"""
        self.stop_event_if_needed(event)
        if not await self.guard(event):
            return

        session = self.session_key(event)
        data = self.load_data()
        entry = data.get(session)
        if not entry:
            logger.info(f"[涩批DEBUG] /关闭sp推送：会话 {session} 没有订阅数据")
            yield event.plain_result("尚未开启sp推送，无需关闭。")
            return
        if not entry.get("pushEnabled"):
            logger.info(f"[涩批DEBUG] /关闭sp推送：会话 {session} 推送本就关闭")
            yield event.plain_result("尚未开启sp推送，无需关闭。")
            return
        entry["pushEnabled"] = False
        self.save_data(data)
        logger.info(f"[涩批DEBUG] /关闭sp推送：会话 {session} 成功关闭推送")
        yield event.plain_result("已关闭sp推送。")

    # ------------------------------------------------------------------ #
    # 主类内部辅助（委托 Mixin 实现）
    # ------------------------------------------------------------------ #
    @staticmethod
    def _random_ids(ids: list[str], count: int) -> list[str]:
        pool = list(ids)
        random.shuffle(pool)
        return pool[:count]

    def _resolve_url_group(self, command: str) -> str | None:
        """把指令名映射到配置中的分组名。"""
        command = command.strip()
        if command == "写真网址":
            return "写真"
        if command == "福利网址":
            return "福利"
        if command == "吃瓜网址":
            return "吃瓜"
        if command == "导航网址":
            return "导航"
        if command.lower().startswith("福利app"):
            return "福利App"
        if command == "TG电报":
            return "TG电报"
        for name in self.settings.group_names:
            if command == f"{name}网址":
                return name
        return None

    async def _send_cos(self, event: AstrMessageEvent, command: str):
        """按 2图/3图 发送对应类别图包。"""
        category = CATEGORY_MAP[command]
        type_name = TYPE_NAME[command]

        yield event.plain_result(f"正在获取{type_name}，请稍等...")

        base = self.settings.cos_api
        separator = "&" if "?" in base else "?"
        url = f"{base}{separator}category={category}"
        logger.info(f"[涩批DEBUG] _send_cos({command})：请求URL {url}")

        paths = await self._download_cos_images(url, prefix=category)
        if not paths:
            logger.warning(f"[涩批DEBUG] _send_cos({command})：图片全部下载失败")
            yield event.plain_result("未能获取到图片，请稍后再试。")
            return

        logger.info(
            f"[涩批DEBUG] _send_cos({command})：共 {len(paths)} 张图片，"
            f"forward_as_node={self.settings.forward_as_node}"
        )
        if self.settings.forward_as_node:
            nodes = self.image_nodes(event, paths)
            for batch in self.build_batches(nodes):
                yield event.chain_result([self.wrap_nodes(batch)])
        else:
            yield event.chain_result(self.image_chain(paths))

    async def _download_cos_images(self, url: str, prefix: str) -> list[str]:
        """从接口抓取若干张图片并保存到本地。"""
        semaphore = asyncio.Semaphore(self.settings.max_concurrent_download)
        results: list[str | None] = [None] * IMAGE_COUNT

        async def worker(index: int) -> None:
            async with semaphore:
                try:
                    data = await fetch_bytes(
                        url,
                        timeout=self.settings.download_timeout,
                        referer=self.settings.cos_api,
                    )
                except Exception as exc:
                    logger.info(
                        f"[涩批DEBUG] _download_cos_images({prefix})："
                        f"第 {index + 1}/{IMAGE_COUNT} 张下载失败：{exc!r}"
                    )
                    return
                if self.settings.track_pixel:
                    data = add_noise(data)
                target = self.temp_path(f"{prefix}_{index}_{random.randint(1000, 9999)}.jpg")
                try:
                    save_bytes(target, data)
                except OSError as exc:
                    logger.info(
                        f"[涩批DEBUG] _download_cos_images({prefix})："
                        f"第 {index + 1}/{IMAGE_COUNT} 张保存失败：{exc!r}"
                    )
                    return
                results[index] = str(target)

        await asyncio.gather(*(worker(i) for i in range(IMAGE_COUNT)))
        paths = [path for path in results if path]
        logger.info(
            f"[涩批DEBUG] _download_cos_images({prefix})："
            f"成功 {len(paths)}/{IMAGE_COUNT} 张"
        )
        return paths


__all__ = ["spPlugin"]
