"""内置定时任务调度器。

原 TRSS 插件依赖 node-schedule / Yunzai 的 task 字段，AstrBot 插件没有等价
机制，这里用 asyncio 实现一个轻量的调度器：

* 每天固定时间执行（HH:MM）—— 写真ID增量更新、套图列表增量更新
* 每 N 小时执行 —— 画师订阅推送检查
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from .storage import JsonStore

JobFunc = Callable[[], Awaitable[None]]


class TimeBasedScheduler:
    """基于 asyncio 的定时任务调度器。"""

    def __init__(self, runtime_store: JsonStore, logger=None) -> None:
        self._store = runtime_store
        self._logger = logger
        self._jobs: list[dict] = []
        self._task: asyncio.Task | None = None
        self._running = False

    # ------------------------------------------------------------------ #
    # 注册任务
    # ------------------------------------------------------------------ #
    def add_daily_job(self, name: str, at: str, func: JobFunc) -> None:
        """每天 at（HH:MM）执行一次。"""
        hour, minute = self._parse_hhmm(at)
        self._jobs.append(
            {
                "name": name,
                "kind": "daily",
                "hour": hour,
                "minute": minute,
                "func": func,
            }
        )

    def add_interval_job(self, name: str, hours: float, func: JobFunc) -> None:
        """每隔 hours 小时执行一次（以插件启动/上次执行为基准）。"""
        self._jobs.append(
            {
                "name": name,
                "kind": "interval",
                "interval": max(60.0, float(hours) * 3600.0),
                "func": func,
            }
        )

    @staticmethod
    def _parse_hhmm(value: str) -> tuple[int, int]:
        try:
            hour_text, minute_text = str(value).split(":", 1)
            hour = int(hour_text) % 24
            minute = int(minute_text) % 60
            return hour, minute
        except Exception:
            return 7, 30

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        if self._running or not self._jobs:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="sp-scheduler")
        self._log("info", f"定时任务已启动，共 {len(self._jobs)} 个任务")

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    @property
    def running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------ #
    # 主循环
    # ------------------------------------------------------------------ #
    async def _loop(self) -> None:
        # 启动后先等 60 秒，避开插件初始化阶段
        await asyncio.sleep(60)
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover
                self._log("error", f"定时任务调度异常: {exc}")
            await asyncio.sleep(30)

    async def _tick(self) -> None:
        now = datetime.now()
        runtime = self._store.load() or {}
        changed = False

        for job in self._jobs:
            name = job["name"]
            key = f"job:{name}"
            if job["kind"] == "daily":
                last_date = str(runtime.get(f"{key}:date", ""))
                target = now.replace(
                    hour=job["hour"], minute=job["minute"], second=0, microsecond=0
                )
                today = now.strftime("%Y-%m-%d")
                if now >= target and last_date != today:
                    runtime[f"{key}:date"] = today
                    changed = True
                    await self._run_job(job)
            else:
                last_ts = float(runtime.get(f"{key}:ts", 0) or 0)
                if time.time() - last_ts >= job["interval"]:
                    runtime[f"{key}:ts"] = time.time()
                    changed = True
                    await self._run_job(job)

        if changed:
            self._store.set(runtime, persist=True)

    async def _run_job(self, job: dict) -> None:
        name = job["name"]
        self._log("info", f"[定时任务] 开始执行 {name}")
        try:
            await job["func"]()
            self._log("info", f"[定时任务] {name} 执行完成")
        except Exception as exc:
            self._log("error", f"[定时任务] {name} 执行失败: {exc}")

    def _log(self, level: str, message: str) -> None:
        if self._logger is None:
            return
        try:
            getattr(self._logger, level, self._logger.info)(message)
        except Exception:
            pass

    def next_run_hint(self) -> list[str]:
        """返回各任务的下次执行提示文本（供帮助指令展示）。"""
        hints: list[str] = []
        now = datetime.now()
        for job in self._jobs:
            if job["kind"] == "daily":
                target = now.replace(
                    hour=job["hour"], minute=job["minute"], second=0, microsecond=0
                )
                if target <= now:
                    target += timedelta(days=1)
                hints.append(f"每日 {job['hour']:02d}:{job['minute']:02d} - {job['name']}")
            else:
                hours = job["interval"] / 3600
                hints.append(f"每 {hours:g} 小时 - {job['name']}")
        return hints
