"""简单的 JSON 持久化工具。

原插件使用 yaml（订阅数据）和 json（ID 列表）两种格式，这里统一为 JSON，
放在 ``data/plugin_data/<plugin_name>/`` 下。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def _dbg(message: str) -> None:
    """统一的控制台 debug 日志输出（失败静默，不影响主流程）。"""
    try:
        from astrbot.api import logger # type: ignore
        logger.info(f"[涩批DEBUG] {message}")
    except Exception:
        pass


def load_json(path: Path, default: Any) -> Any:
    """读取 JSON 文件，文件不存在或损坏时返回默认值。"""
    try:
        if not path.exists():
            _dbg(f"load_json：文件不存在 {path}")
            return default
        with path.open("r", encoding="utf-8") as fp:
            data = json.load(fp)
        _dbg(f"load_json：成功读取 {path}（type={type(data).__name__}）")
        return data if data is not None else default
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        _dbg(f"load_json：读取 {path} 失败，使用默认值：{exc!r}")
        return default


def save_json(path: Path, data: Any) -> bool:
    """原子写入 JSON 文件。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=path.name, suffix=".tmp"
        )
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            json.dump(data, fp, ensure_ascii=False, indent=2)
        os.replace(tmp_name, path)
        _dbg(f"save_json：成功写入 {path}（type={type(data).__name__}）")
        return True
    except OSError as exc:
        _dbg(f"save_json：写入 {path} 失败：{exc!r}")
        return False


class JsonStore:
    """带内存缓存的 JSON 存储。"""

    def __init__(self, path: Path, default: Any) -> None:
        self.path = path
        self._default = default
        self._data: Any = None

    def load(self) -> Any:
        if self._data is None:
            self._data = load_json(self.path, self._copy_default())
        return self._data

    def save(self) -> bool:
        ok = save_json(self.path, self.load())
        _dbg(f"JsonStore.save：{self.path} 保存{'成功' if ok else '失败'}")
        return ok

    def reload(self) -> Any:
        self._data = None
        return self.load()

    def set(self, data: Any, persist: bool = True) -> None:
        self._data = data
        if persist:
            self.save()

    def _copy_default(self) -> Any:
        if isinstance(self._default, dict):
            return dict(self._default)
        if isinstance(self._default, list):
            return list(self._default)
        return self._default
