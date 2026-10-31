"""功能：涩批帮助。

* ``/涩批文字帮助`` 等 —— 发送文字版使用说明
* ``/涩批图片帮助`` 等 —— 发送帮助图。直接发送插件自带的
  ``assets/help.png``（新版暗黑风长图）；若该文件不存在，则本地用
  Pillow 把最新帮助文本渲染成兜底长图，再失败则回退到纯文字版。
"""

from __future__ import annotations

from pathlib import Path
import random

from astrbot.api import logger  # type: ignore
from astrbot.api.event import AstrMessageEvent  # type: ignore

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
    """生成文字版帮助内容。

    指令清单以 ``main.py`` 里 ``@filter.command`` 的实际注册为准：
    29 个主指令 + 32 个别名，参数写法与 handler 解析逻辑一致。
    """
    lines: list[str] = [
        "【帮助】",
        "· /涩批文字帮助 - 发送文字版帮助（别名：/sp文字帮助、/色批文字帮助、"
        "/色胚文字帮助、/涩胚文字帮助）",
        "· /涩批图片帮助 - 发送图片版帮助（别名：/sp图片帮助、/色批图片帮助、"
        "/色胚图片帮助、/涩胚图片帮助）",
        "  提示：/涩批文字帮助 的消息里带“图片”时，也会发送图片版帮助。",
        "",
        "【妹子图】",
        "· /写真馆 <ID或链接> - 获取写真图集，可一次多个 ID"
        "（如 /写真馆 171317 171318，一次最多 10 个）",
        "· /潮拍馆 <ID或链接> - 获取潮拍馆图集，用法同上（一次最多 10 个）",
        "· /随机写真 - 从数据目录 mztids.json 里随机取一个 ID（内置 2479 个种子）",
        "· /更新写真ID - 抓取站点最新 ID 并与内置种子、现有数据增量合并"
        "（别名：/更新写真id；不会删除旧 ID）",
        "  链路：ID → APP 接口（mTLS 证书）→ 内置解密 → 原图下载，不访问文章网页。",
        "",
        "【美图吧（ku1373 套图）】",
        "· /随机美图吧 - 从已保存的套图列表中随机抽一套，发送全部图片",
        "· /套图 <URL> - 解析指定套图链接并发送全部图片"
        "（旧名 /套图详情 仍兼容）",
        "· /更新套图列表 - 增量更新并去重合并套图列表（仅主人可用）",
        "· /全量更新套图列表 - 从第 1 页采到最后一页后去重合并（仅主人可用）",
        "  说明：内置 jg.json 种子（64271 条），已有数据只增不删。",
        "",
        "【P站】",
        "· /pid <数字> - 获取该作品的全部图片，例如 /pid 123456",
        "· /随机 <张数> 张 <画师ID> 作品 - 随机取该画师作品（张数 1-20），"
        "例如 /随机 3 张 123456 作品",
        "· /来图 <张数> 张 <标签>图 - 按标签搜索图片（张数 1-60），"
        "例如 /来图 10 张 白丝图",
        "  说明：/来图 的结果受 /设置R18模式 与 /设置图片偏好 影响。",
        "",
        "【磁力】",
        "· /磁力猫 <关键词> [文件类型] [排序] [数量] - 磁力猫搜索"
        "（数量 1-50，默认 10；默认站点 https://魔法猫咪.lol，可在配置中增删镜像）",
        "  文件类型：全部/影视/音乐/图像/文档/压缩包/安装包/其他",
        "  排序：相关度/文件大小/添加时间/热度/最近下载",
        "  例：/磁力猫 关键词 影视 热度 5",
        "· /验车 <magnet:...> - 查询磁力详情并发送截图（最多 9 张）",
        "  例：/验车 magnet:?xt=urn:btih:...",
        "",
        "【网址导航】",
        "· /写真网址（别名：/福利网址、/吃瓜网址、/导航网址、/福利App、"
        "/福利APP、/福利app、/TG电报）- 按指令词取对应分组的网址列表",
        "  说明：这 6 组是同一个指令的别名；在配置 url_groups 里新增"
        "“XX”分组后，可用 /XX网址 获取。",
        "",
        "【Cosplay图 / 视频】",
        "· /2图 - 获取 10 张二次元图包",
        "· /3图 - 获取 10 张现实（三次元）图包",
        "· /骚鸡（别名：/烧鸡、/sj）- 随机发送一个短视频",
        "",
        "【订阅与推送】",
        "· /订阅画师 <画师ID> - 订阅画师更新",
        "· /取消订阅 <画师ID> - 取消订阅",
        "· /订阅列表 - 查看本会话已订阅的画师",
        "· /sp推送 - 开启本会话的更新推送",
        "· /关闭sp推送 - 关闭本会话的更新推送",
        "",
        "【设置（仅主人可用）】",
        "· /开启sp撤回、/关闭sp撤回 - 撤回开关（同一个指令；别名："
        "/开启涩批撤回、/关闭涩批撤回、/开启色批撤回、/关闭色批撤回、"
        "/开启色皮撤回、/关闭色皮撤回、/开启色胚撤回、/关闭色胚撤回）",
        "· /设置sp撤回 <10-120> - 设置撤回时间（秒），别名：/设置涩批撤回、"
        "/设置色批撤回、/设置色皮撤回，例如 /设置sp撤回 60",
        "· /设置R18模式 <0|1|2> - 0 全部 / 1 非R18 / 2 R18"
        "（别名：/设置r18模式）",
        "· /设置图片偏好 <0|1|2> - 0 无偏好 / 1 男性 / 2 女性",
        "· /sp状态 - 查看运行状态与定时任务",
        "",
        "【插件管理】",
        "· AstrBot WebUI → 插件管理 → 涩批：重载插件、修改配置、更新插件",
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
        """优先发送插件自带的 assets/help.png，缺失时本地用 Pillow 即时渲染。

        直接发送静态帮助图，不启动浏览器、不抓取任何外部服务；只有图片
        缺失时才走本地 Pillow 兜底，仍失败则回退为文字版。
        """
        local = Path(self.paths.asset("help.png"))
        if local.exists():
            logger.info(f"[涩批DEBUG] _send_help_image：使用自带帮助图 {local}")
            yield event.image_result(str(local))
            return

        # 本地即时渲染兜底（每次调用都是新图，随文本内容自动更新）
        # 先做一次字体体检：缺中文字体且 assets/fonts 里有自带字体时，
        # 自动把字体安装到系统字体目录，再重试渲染一次（只重试一次，
        # 避免无限循环）。
        logger.info("[涩批DEBUG] _send_help_image：开始本地渲染帮助图")
        try:
            out = self.temp_path(f"sp_help_{random.randint(1000, 9999)}.jpg")
            render_text_help_image(
                groups=help_groups(),
                author=AUTHOR,
                version=PLUGIN_VERSION,
                out_path=out,
            )
            self._log_font_status()
            logger.info(f"[涩批DEBUG] _send_help_image：渲染成功 -> {out}")
            yield event.image_result(str(out))
            return
        except Exception as exc:
            # 渲染失败不再静默吞掉：记一条 warning 便于定位
            # （常见根因：Pillow 未安装 / assets 目录不可写 / 字体加载异常）
            logger.warning(f"[涩批] 本地渲染帮助图失败: {exc!r}")

        # 失败后：先诊断是否字体缺失，缺失则尝试自动修复并重试一次
        try:
            font_diag = diagnose_font_missing()
            logger.info(f"[涩批DEBUG] _send_help_image：字体诊断 {font_diag}")
            if font_diag["missing"]:
                logger.info(
                    f"[涩批] 检测到中文字体缺失: {font_diag['suggestion']}"
                )
                fixed_diag = ensure_chinese_font()
                logger.info(f"[涩批DEBUG] _send_help_image：字体修复 {fixed_diag}")
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
                    logger.info(f"[涩批DEBUG] _send_help_image：重试渲染成功 -> {out}")
                    yield event.image_result(str(out))
                    return
                logger.warning(
                    f"[涩批] 字体自动安装后仍未找到可用字体: "
                    f"{fixed_diag['suggestion']}"
                )
        except Exception as exc:
            logger.warning(f"[涩批] 字体诊断/自动修复过程出错: {exc!r}")

        # 兜底：直接发文字版
        logger.info("[涩批DEBUG] _send_help_image：渲染失败，回退为纯文字帮助")
        yield event.plain_result(
            "未找到 assets/help.png，本地渲染也失败，已改为发送文字帮助"
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
