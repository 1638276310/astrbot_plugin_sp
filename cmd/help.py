"""功能：涩批帮助。

原 ``help.js``：

* ``#涩批文字帮助`` 等 —— 发送文字版使用说明
* ``#涩批图片帮助`` 等 —— 发送帮助图（插件 assets/help.jpg），
  文件不存在时回退为 HTML 渲染的文字帮助图。
"""

from __future__ import annotations

from pathlib import Path

from astrbot.api.event import AstrMessageEvent, filter

from cmd._base import spFeature

TRIGGER = r"^#?(?:sp|涩批|色批|色胚|涩胚)(?:文字帮助|图片帮助)$"

AUTHOR = "寂寞沙洲冷 QV：1638276310"


def build_help_text() -> str:
    """生成文字版帮助内容。"""
    lines: list[str] = [
        "【涩批(sp)插件文字帮助】",
        "",
        "【美图吧（ku1373 套图）】",
        "· #随机美图吧 - 从已保存的套图列表中随机抽一套，自动解析并发送全部图片",
        "· #套图详情 <URL> - 解析指定套图链接并发送该套图的所有图片",
        "· #更新套图列表 - 增量更新套图列表（仅主人可用）",
        "· #全量更新套图列表 - 从第 1 页到最后一页顺序采集，覆盖原有列表（仅主人可用）",
        "",
        "【网址获取】",
        "· #写真网址 - 获取写真网站网址",
        "· #福利网址 - 获取福利网站网址",
        "· #吃瓜网址 - 获取吃瓜网站网址",
        "· #导航网址 - 获取导航网站网址",
        "· #福利App - 获取福利App下载链接",
        "· #TG电报 - 获取TG电报频道链接",
        "",
        "【妹子图】",
        "· #写真馆<ID> - 获取妹子图图片",
        "· #随机写真 - 随机获取妹子图",
        "· #更新写真ID - 增量更新写真ID列表（仅主人可用）",
        "",
        "【P站图片获取】",
        "· #pid<数字> - 获取P站单张作品",
        "· #随机X张Y作品 - 随机获取画师作品（X ≤ 20）",
        "· #来X张XX图 - 按标签搜索图片（X ≤ 60）",
        "",
        "【磁力链接】",
        "· #磁力猫 <关键词> [文件类型] [排序] [数量] - 磁力猫搜索",
        "   文件类型：全部/影视/音乐/图像/文档/压缩包/安装包/其他",
        "   排序：相关度/文件大小/添加时间/热度/最近下载",
        "· #验车<magnet:...> - 查询磁力链接详情",
        "",
        "【Cosplay图】",
        "· #2图 - 获取二次元图包",
        "· #3图 - 获取三次元图包",
        "",
        "【视频】",
        "· #骚鸡 / #烧鸡 / #sj - 随机发送一个视频",
        "",
        "【订阅与推送】",
        "· #订阅画师<ID> - 订阅画师更新",
        "· #取消订阅<ID> - 取消订阅",
        "· #订阅列表 - 查看已订阅画师",
        "· #sp推送 / #关闭sp推送 - 控制推送",
        "",
        "【设置选项】",
        "· #开启sp撤回 / #关闭sp撤回 - 控制消息撤回",
        "· #设置sp撤回X - 设置撤回时间(10-120秒)",
        "· #设置R18模式X - 0:全部 1:非R18 2:R18",
        "· #设置图片偏好X - 0:无偏好 1:男性 2:女性",
        "",
        "【插件管理】",
        "· AstrBot 中请在 WebUI 的插件页面重载/更新本插件",
        "· #sp状态 - 查看本插件运行状态与定时任务",
        "",
        f"作者：{AUTHOR}",
    ]
    return "\n".join(lines)


HELP_TEMPLATE = """
<div style="font-family: 'Microsoft YaHei', sans-serif; width: 760px; padding: 28px;
            background: #fdfdfa; color: #202224; font-size: 17px; line-height: 1.7;">
  <h1 style="font-size: 30px; margin: 0 0 6px 0;">涩批 AstrBot 插件 · 指令手册</h1>
  <div style="color: #666b70; font-size: 15px; margin-bottom: 18px;">
    AstrBot 版 · 作者：{{ author }}
  </div>
  {% for group in groups %}
  <div style="margin-bottom: 14px;">
    <div style="font-weight: 700; color: #2f86bd; margin-bottom: 4px;">{{ group.title }}</div>
    {% for item in group.items %}
    <div style="padding-left: 10px;">· {{ item }}</div>
    {% endfor %}
  </div>
  {% endfor %}
</div>
"""


def help_groups() -> list[dict]:
    """把帮助文本整理成结构化数据，方便 HTML 模板渲染。"""
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
        if stripped.startswith("·") or stripped.startswith("."):
            item = stripped.lstrip("·. ").strip()
            if current is not None:
                current["items"].append(item)
        elif current is not None:
            current["items"].append(stripped)
    return groups


class HelpFeature(spFeature):
    """帮助相关指令。"""

    @filter.regex(TRIGGER, priority=50)
    async def sp_help(self, event: AstrMessageEvent):
        """涩批插件帮助（文字版 / 图片版）"""
        if not await self.guard(event):
            return

        message = event.get_message_str().strip()
        if "图片" in message:
            async for result in self._send_help_image(event):
                yield result
            return
        yield event.plain_result(build_help_text())

    async def _send_help_image(self, event: AstrMessageEvent):
        """优先发送插件自带帮助图，缺失时用 HTML 渲染。"""
        local = Path(self.paths.asset("help.jpg"))
        if local.exists():
            yield event.image_result(str(local))
            return

        try:
            url = await self.html_render(  # type: ignore[attr-defined]
                HELP_TEMPLATE,
                {"author": AUTHOR, "groups": help_groups()},
                options={"type": "jpeg", "quality": 90, "full_page": True},
            )
            yield event.image_result(url)
        except Exception:
            yield event.plain_result(
                "未找到 assets/help.jpg，且文转图失败，已改为发送文字帮助：\n\n"
                + build_help_text()
            )
