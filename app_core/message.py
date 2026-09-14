"""消息构造与发送辅助。

原插件的 ``customReply`` / ``sendForward`` 分别处理 NapCat 节点转发和
标准合并转发。AstrBot 里统一用消息链表达即可，适配器会自动选择
``send_group_forward_msg`` / ``send_private_forward_msg``。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable, Sequence

from astrbot.api.event import AstrMessageEvent # type: ignore
from astrbot.api.message_components import Image, Node, Nodes, Plain, Video # type: ignore

from .paths import PluginPaths
from .settings import PluginSettings


class MessageMixin:
    """所有功能模块共用的消息发送能力（供插件类混入）。

    依赖宿主类提供 ``settings`` 与 ``paths`` 两个属性。
    """

    settings: PluginSettings
    paths: PluginPaths

    # ------------------------------------------------------------------ #
    # 基础发送
    # ------------------------------------------------------------------ #
    @staticmethod
    async def send_text(event: AstrMessageEvent, text: str) -> None:
        """发送纯文本。"""
        await event.send(event.plain_result(text))

    @staticmethod
    def image_chain(paths: Sequence[str | Path]) -> list:
        """把本地图片路径列表转换成消息链。"""
        chain: list = []
        for item in paths:
            if isinstance(item, Path):
                chain.append(Image.fromFileSystem(str(item)))
            elif isinstance(item, str) and item.startswith(("http://", "https://")):
                chain.append(Image.fromURL(item))
            else:
                chain.append(Image.fromFileSystem(str(item)))
        return chain

    @staticmethod
    def video_component(path: str | Path) -> Video:
        return Video.fromFileSystem(str(path))

    # ------------------------------------------------------------------ #
    # 合并转发
    # ------------------------------------------------------------------ #
    @staticmethod
    def image_nodes(
        event: AstrMessageEvent,
        image_paths: Iterable[str | Path],
        titles: Sequence[str] | None = None,
        self_id: str | None = None,
    ) -> list[Node]:
        """把图片构造成合并转发节点列表（每张图一个节点）。"""
        uin = self_id or event.get_self_id() or "0"
        name = event.get_sender_name() or "涩批"
        nodes: list[Node] = []
        paths = list(image_paths)
        for index, path in enumerate(paths):
            content: list = []
            if titles and index < len(titles) and titles[index]:
                content.append(Plain(text=titles[index]))
            content.append(Image.fromFileSystem(str(path)))
            nodes.append(
                Node(content=content, name=name, uin=str(uin)),
            )
        return nodes

    @staticmethod
    def text_nodes(event: AstrMessageEvent, texts: Sequence[str]) -> list[Node]:
        """把文本列表构造成合并转发节点列表。

        列表中的每一项会成为一个独立节点（适合列表式的多行内容）。
        如果某项本身含换行，会作为同一个节点的多行文本。
        """
        uin = event.get_self_id() or "0"
        name = event.get_sender_name() or "涩批"
        return [
            Node(content=[Plain(text=str(text))], name=name, uin=str(uin))
            for text in texts
        ]

    @staticmethod
    def merged_text_node(event: AstrMessageEvent, texts: Sequence[str]) -> Node:
        """把多段文本合并成**一个**节点（用换行连接）。"""
        uin = event.get_self_id() or "0"
        name = event.get_sender_name() or "涩批"
        return Node(
            content=[Plain(text="\n".join(str(text) for text in texts))],
            name=name,
            uin=str(uin),
        )

    @staticmethod
    def wrap_nodes(nodes: list[Node]) -> Nodes:
        """把节点列表包装成 Nodes 组件。"""
        return Nodes(nodes=nodes)

    def build_batches(
        self,
        nodes: list[Node],
        batch_size: int | None = None,
    ) -> list[list[Node]]:
        """按批切分节点列表。"""
        size = batch_size or self.settings.batch_size
        size = max(1, size)
        return [nodes[i : i + size] for i in range(0, len(nodes), size)]

    # ------------------------------------------------------------------ #
    # 常用反馈
    # ------------------------------------------------------------------ #
    @staticmethod
    async def send_error(event: AstrMessageEvent, text: str) -> None:
        await event.send(event.plain_result(text))

    @staticmethod
    async def send_busy(event: AstrMessageEvent, text: str) -> None:
        """发送"请稍等"之类的提示。"""
        await event.send(event.plain_result(text))

    @staticmethod
    def extract_number(text: str, pattern: str, index: int = 1) -> str | None:
        """用正则提取指定分组。"""
        match = re.search(pattern, text)
        if not match:
            return None
        return match.group(index)

    # ------------------------------------------------------------------ #
    # 临时文件
    # ------------------------------------------------------------------ #
    def temp_path(self, name: str) -> Path:
        return self.paths.temp_file(name)

    def cleanup_temp(self, max_age_seconds: int = 3600) -> int:
        return self.paths.cleanup_temp(max_age_seconds)

    @staticmethod
    def safe_unlink(path: str | Path | None) -> None:
        if not path:
            return
        try:
            if os.path.exists(path):
                os.unlink(path)
        except OSError:
            pass

    # ------------------------------------------------------------------ #
    # 白名单
    # ------------------------------------------------------------------ #
    def whitelist_ok(self, event: AstrMessageEvent) -> bool:
        """会话白名单校验。未启用时始终通过。"""
        if not self.settings.enable_id_whitelist:
            return True
        allowed = set(self.settings.id_whitelist)
        if not allowed:
            return True
        return (
            event.get_group_id() in allowed
            or event.get_sender_id() in allowed
        )

    def session_key(self, event: AstrMessageEvent) -> str:
        """会话唯一键：群聊用群号，私聊用发送者 ID。"""
        return event.get_group_id() or event.get_sender_id() or event.get_session_id()

    @staticmethod
    def truncate(text: str, limit: int = 180) -> str:
        text = (text or "").strip()
        return text if len(text) <= limit else text[: limit - 1] + "…"


class AdminMixin:
    """管理员相关判断。"""

    @staticmethod
    def is_admin_event(event: AstrMessageEvent) -> bool:
        try:
            return bool(event.is_admin())
        except Exception:
            return False

    @classmethod
    async def require_admin(cls, event: AstrMessageEvent) -> bool:
        """校验管理员权限，失败时自动回复并返回 False。"""
        if cls.is_admin_event(event):
            return True
        await event.send(event.plain_result("仅主人可用"))
        return False
