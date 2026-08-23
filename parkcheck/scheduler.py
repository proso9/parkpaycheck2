# -*- coding: utf-8 -*-
"""
定时任务模块：基于 APScheduler 的后台调度器封装。

SchedulerManager 把「定时运行检测」从 GUI/命令行中解耦：
  - start_interval：按固定间隔运行
  - start_daily：每天固定时刻运行
  - stop / running / next_run：停止、状态与下次触发时间查询

调度器运行在独立后台线程，不阻塞调用方（如 GUI）主线程。
同一时刻只允许一个定时任务；再次启动会先停止旧任务。
"""

import re

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

# 间隔单位 → 秒数换算
INTERVAL_UNIT_SECONDS = {"秒": 1, "分": 60, "时": 3600}

# 每天固定时间格式校验：HH:MM（如 08:30）
TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def interval_to_seconds(value, unit):
    """把「间隔值 + 单位」换算成秒数；未知单位抛 ValueError。"""
    if unit not in INTERVAL_UNIT_SECONDS:
        raise ValueError(
            f"未知间隔单位：{unit!r}，可选：{', '.join(INTERVAL_UNIT_SECONDS)}"
        )
    return int(value) * INTERVAL_UNIT_SECONDS[unit]


def validate_daily_time(time_str):
    """校验每天固定时间格式（HH:MM），返回 (时, 分)；非法时抛 ValueError。"""
    m = TIME_RE.match(str(time_str).strip())
    if not m:
        raise ValueError(f"时间格式需为 HH:MM（如 08:30），当前为：{time_str!r}")
    return int(m.group(1)), int(m.group(2))


class SchedulerManager:
    """APScheduler 后台调度器封装：同一时刻仅允许一个定时任务。"""

    def __init__(self):
        self._scheduler = None
        self._job = None

    @property
    def running(self):
        """是否已有定时任务在运行。"""
        return self._scheduler is not None and self._scheduler.running

    @property
    def next_run(self):
        """下次触发时间（datetime 或 None）。"""
        return self._job.next_run_time if self._job is not None else None

    def start_interval(self, func, value, unit):
        """按间隔启动：每隔 value×unit 秒运行 func。"""
        self._start(func, IntervalTrigger(seconds=interval_to_seconds(value, unit)))

    def start_daily(self, func, time_str):
        """每天固定时间启动：time_str 形如 HH:MM。"""
        hour, minute = validate_daily_time(time_str)
        self._start(func, CronTrigger(hour=hour, minute=minute))

    def _start(self, func, trigger):
        """（重新）创建调度器并注册任务；已有任务先停止。"""
        self.stop()
        self._scheduler = BackgroundScheduler()
        self._job = self._scheduler.add_job(func, trigger)
        self._scheduler.start()

    def stop(self):
        """停止并释放当前调度器与任务（未启动时调用安全）。"""
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
        self._scheduler = None
        self._job = None
