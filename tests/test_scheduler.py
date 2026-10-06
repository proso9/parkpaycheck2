# -*- coding: utf-8 -*-
"""
parkcheck.scheduler 定时任务模块测试。

该模块依赖 apscheduler（见 requirements.txt），与核心逻辑的
“无第三方依赖”测试分开维护。

覆盖：
  1. 间隔换算：秒/分/时 → 秒
  2. 未知间隔单位 → 抛 ValueError
  3. 每天固定时间格式校验（合法 / 非法）
  4. SchedulerManager 初始状态与未启动时安全 stop
  5. 集成：1 秒间隔任务能触发累计，stop 后不再触发
"""

import os
import sys
import time
import threading

# 将项目根目录加入 sys.path，以便导入被测试包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Windows 控制台默认编码可能是 cp1252/gbk，统一改为 UTF-8 输出，避免 print 中文报错
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from parkcheck.scheduler import (  # noqa: E402
    SchedulerManager,
    interval_to_seconds,
    validate_daily_time,
)

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[通过] {name}")
    else:
        FAIL += 1
        print(f"[失败] {name} {detail}")


def test_interval_to_seconds():
    check("30分 → 1800秒", interval_to_seconds(30, "分") == 1800)
    check("2时 → 7200秒", interval_to_seconds(2, "时") == 7200)
    check("5秒 → 5秒", interval_to_seconds(5, "秒") == 5)
    try:
        interval_to_seconds(1, "天")
        check("未知单位抛错", False, "未抛出 ValueError")
    except ValueError:
        check("未知单位抛错", True)


def test_validate_daily_time():
    check("08:30 合法", validate_daily_time("08:30") == (8, 30))
    check("23:59 合法", validate_daily_time("23:59") == (23, 59))
    for bad in ("25:00", "8:5", "08:60", "abc", ""):
        try:
            validate_daily_time(bad)
            check(f"非法时间 {bad!r} 抛错", False)
        except ValueError:
            check(f"非法时间 {bad!r} 抛错", True)


def test_manager_initial_and_stop():
    mgr = SchedulerManager()
    check("初始未运行", not mgr.running)
    check("初始无下次触发", mgr.next_run is None)
    mgr.stop()  # 未启动时停止应安全
    check("未启动停止安全", not mgr.running)


def test_manager_integration():
    count = {"n": 0}
    lock = threading.Lock()

    def job():
        with lock:
            count["n"] += 1

    mgr = SchedulerManager()
    try:
        mgr.start_interval(job, 1, "秒")
        time.sleep(2.5)
        with lock:
            n1 = count["n"]
    finally:
        mgr.stop()
    time.sleep(1.2)
    with lock:
        n2 = count["n"]
    check("1秒间隔至少触发2次", n1 >= 2, f"实际 {n1}")
    check("stop 后不再触发", n2 == n1, f"{n1} -> {n2}")


def main():
    test_interval_to_seconds()
    test_validate_daily_time()
    test_manager_initial_and_stop()
    test_manager_integration()
    print(f"\n通过 {PASS} 项，失败 {FAIL} 项")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
