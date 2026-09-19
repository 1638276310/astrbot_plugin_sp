"""功能：涩批帮助。

原 ``help.js``：

* ``/涩批文字帮助`` 等 —— 发送文字版使用说明
* ``/涩批图片帮助`` 等 —— 发送帮助图。优先用插件自带的
  ``assets/help.jpg``；若该文件不存在，则**本地**用 Pillow 把最新帮助
  文本即时渲染成一张长图（不依赖外部截图服务），渲染失败再回退到纯
  文字版。
"""

from __future__ import annotations

import random
from pathlib import Path

from astrbot.api import logger # type: ignore
from astrbot.api.event import AstrMessageEvent, filter # type: ignore

from ..app_core.render import (
    _find_chinese_font,
    diagnose_font_missing,
    ensure_chinese_font,
    render_text_help_image,
)
from ..app_core.settings import PLUGIN_VERSION
from ._base import spFeature

AUTHOR = "寂寞沙洲冷 QV：1638276310"


def build_help_text() -> str:
    """生成文字版帮助内容。"""
    lines: list[str] = [
        "【涩批(sp)插件文字帮助】",
        "",
        "【美图吧（ku1373 套图）】",
        "· /随机美图吧 - 从已保存的套图列表中随机抽一套，自动解析并发送全部图片",
        "· /套图详情 <URL> - 解析指定套图链接并发送该套图的所有图片",
        "· /更新套图列表 - 增量更新套图列表（仅主人可用）",
        "· /全量更新套图列表 - 从第 1 页到最后一页顺序采集，覆盖原有列表（仅主人可用）",
        "",
        "【网址获取】",
        "· /写真网址 - 获取写真网站网址",
        "· /福利网址 - 获取福利网站网址",
        "· /吃瓜网址 - 获取吃瓜网站网址",
        "· /导航网址 - 获取导航网站网址",
        "· /福利App - 获取福利App下载链接",
        "· /TG电报 - 获取TG电报频道链接",
        "",
        "【妹子图】",
        "· /写真馆 <ID> - 获取妹子图图片",
        "· /随机写真 - 随机获取妹子图",
        "· /更新写真ID - 增量更新写真ID列表（仅主人可用）",
        "",
        "【P站图片获取】",
        "· /pid <数字> - 获取P站单张作品",
        "· /随机 X 张 Y 作品 - 随机获取画师作品（X ≤ 20）",
        "· /来图 X 张 XX图 - 按标签搜索图片（X ≤ 60）",
        "",
        "【磁力链接】",
        "· /磁力猫 <关键词> [文件类型] [排序] [数量] - 磁力猫搜索",
        "   文件类型：全部/影视/音乐/图像/文档/压缩包/安装包/其他",
        "   排序：相关度/文件大小/添加时间/热度/最近下载",
        "· /验车 <magnet:...> - 查询磁力链接详情",
        "",
        "【Cosplay图】",
        "· /2图 - 获取二次元图包",
        "· /3图 - 获取三次元图包",
        "",
        "【视频】",
        "· /骚鸡 / /烧鸡 / /sj - 随机发送一个视频",
        "",
        "【订阅与推送】",
        "· /订阅画师 <ID> - 订阅画师更新",
        "· /取消订阅 <ID> - 取消订阅",
        "· /订阅列表 - 查看已订阅画师",
        "· /sp推送 / /关闭sp推送 - 控制推送",
        "",
        "【设置选项】",
        "· /开启sp撤回 / /关闭sp撤回 - 控制消息撤回",
        "· /设置sp撤回 X - 设置撤回时间(10-120秒)",
        "· /设置R18模式 X - 0:全部 1:非R18 2:R18",
        "· /设置图片偏好 X - 0:无偏好 1:男性 2:女性",
        "",
        "【插件管理】",
        "· AstrBot 中请在 WebUI 的插件页面重载/更新本插件",
        "· /sp状态 - 查看本插件运行状态与定时任务",
        "",
        f"作者：{AUTHOR}",
    ]
    return "\n".join(lines)


def help_groups() -> list[dict]:
    """把帮助文本整理成结构化数据（供本地渲染器使用）。

    每个 item 保留**该行在图里应显示的原始文本**：
    - 以 ``·``/``.`` 开头的行，前缀剥掉后存（渲染器会为这类 item 加 ``· `` 项目符号）；
    - 其它普通行（如磁力分组里的缩进说明行），原样存（渲染器不加项目符号）；
    - 行首的 ``·``/``.`` 有无、以及"这是说明行还是指令行"由本函数的
      ``is_command`` 标记决定，渲染器只负责折行与像素对齐，不再自己造前缀。
    """
    groups: list[dict] = []
    current: dict | None = None
    for line in build_help_text().split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("【") and stripped.endswith("】"):
            current = {"title": stripped.strip("【】"), "items": []}
            groups.append(current)
            continue
        if current is None:
            continue
        # 作者行已单独画在图底部署名线，这里跳过，避免被误塞进"插件管理"分组
        if stripped.startswith("作者："):
            continue
        if stripped.startswith("·") or stripped.startswith("."):
            item_text = stripped.lstrip("·. ").strip()
            current["items"].append({"text": item_text, "bullet": True})
        else:
            current["items"].append({"text": stripped, "bullet": False})
    return groups


class HelpFeature(spFeature):
    """帮助相关业务方法（指令 handler 由主类 main.py 注册）。"""

    async def _send_help_image(self, event: AstrMessageEvent):
        """优先发送插件自带帮助图，缺失时本地用 Pillow 即时渲染。

        渲染不依赖任何外部服务（不抓截图、不下载文生图），每次都会按
        最新的帮助文本重新生成一张长图，保证与 ``build_help_text`` 内容
        始终一致。渲染失败时回退为纯文字版。
        """
        local = Path(self.paths.asset("help.jpg"))
        if local.exists():
            logger.debug(f"[涩批DEBUG] _send_help_image：使用自带帮助图 {local}")
            yield event.image_result(str(local))
            return

        # 本地即时渲染（每次调用都是新图，不缓存，随文本内容自动更新）
        # 先做一次字体体检：缺中文字体且 assets/fonts 里有自带字体时，
        # 自动把字体安装到系统字体目录，再重试渲染一次（只重试一次，
        # 避免无限循环）。
        logger.debug("[涩批DEBUG] _send_help_image：开始本地渲染帮助图")
        try:
            out = self.temp_path(f"sp_help_{random.randint(1000, 9999)}.jpg")
            render_text_help_image(
                groups=help_groups(),
                author=AUTHOR,
                version=PLUGIN_VERSION,
                out_path=out,
            )
            self._log_font_status()
            logger.debug(f"[涩批DEBUG] _send_help_image：渲染成功 -> {out}")
            yield event.image_result(str(out))
            return
        except Exception as exc:
            # 渲染失败不再静默吞掉：记一条 warning 便于定位
            # （常见根因：Pillow 未安装 / assets 目录不可写 / 字体加载异常）
            logger.warning(f"[涩批] 本地渲染帮助图失败: {exc!r}")

        # 失败后：先诊断是否字体缺失，缺失则尝试自动修复并重试一次
        try:
            font_diag = diagnose_font_missing()
            logger.debug(f"[涩批DEBUG] _send_help_image：字体诊断 {font_diag}")
            if font_diag["missing"]:
                logger.info(
                    f"[涩批] 检测到中文字体缺失: {font_diag['suggestion']}"
                )
                fixed_diag = ensure_chinese_font()
                logger.debug(f"[涩批DEBUG] _send_help_image：字体修复 {fixed_diag}")
                if not fixed_diag["missing"]:
                    logger.info(
                        "[涩批] 字体自动安装成功，重试渲染帮助图"
                    )
                    out = self.temp_path(f"sp_help_{random.randint(1000, 9999)}.jpg")
                    render_text_help_image(
                        groups=help_groups(),
                        author=AUTHOR,
                        version=PLUGIN_VERSION,
                        out_path=out,
                    )
                    logger.debug(f"[涩批DEBUG] _send_help_image：重试渲染成功 -> {out}")
                    yield event.image_result(str(out))
                    return
                logger.warning(
                    f"[涩批] 字体自动安装后仍未找到可用字体: "
                    f"{fixed_diag['suggestion']}"
                )
        except Exception as exc:
            logger.warning(f"[涩批] 字体诊断/自动修复过程出错: {exc!r}")

        # 兜底：直接发文字版
        logger.debug("[涩批DEBUG] _send_help_image：渲染失败，回退为纯文字帮助")
        yield event.plain_result(
            "未找到 assets/help.jpg，本地渲染也失败，已改为发送文字帮助"
            "（详见服务器日志）：\n\n"
            + build_help_text()
        )

    def _log_font_status(self) -> None:
        """渲染成功后打印一次字体状态（缺失仅 warning，不阻断流程）。"""
        font = _find_chinese_font()
        if font is None:
            logger.warning(
                "[涩批] 帮助图已渲染，但未找到中文字体，"
                "图中中文可能显示为方块，建议在 assets/fonts/ 放入 "
                "思源黑体等 .ttf/.otf 文件"
            )
        else:
            logger.info(f"[涩批] 帮助图渲染成功，使用中文字体: {font}")

    # ------------------------------------------------------------------ #
    # 诊断：查看当前本地渲染用了哪个字体（用于确认中文能否正常显示）
    # ------------------------------------------------------------------ #
    @staticmethod
    def _diagnose_font() -> str:
        p = _find_chinese_font()
        return str(p) if p else "builtin (Pillow 默认字体，无法显示中文)"
