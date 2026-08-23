# -*- coding: utf-8 -*-
"""
异常判定模块：基于解析出的记录判定"出场不开闸且窗口期无支付下发"的异常车辆。

判定规则：
  对每条记录A（出场不开闸），在记录B（支付结果下发）中查找
  车牌相同 且 时间 ∈ [T, T+window_seconds] 秒 的记录；
  若找不到，且该车能取到应交金额，则判为可疑。
"""

from .config import ENTRY_MARK_MISSING, ENTRY_DEDUP_WINDOW, MIN_PARK_TIME_DEVIATION


def find_anomalies(record_a, record_b, window_seconds, record_entry=None,
                   entry_dedup_window=ENTRY_DEDUP_WINDOW,
                   min_deviation=MIN_PARK_TIME_DEVIATION):
    """
    判定异常：对每条不开闸记录，在支付下发记录中查找
    车牌相同 且 时间 ∈ [T, T+window_seconds] 秒。
    返回不存在该记录的【时间, 车牌, 需支付费用, 入场时间】列表。

    规则：若找不到该车应交金额（fee 为空），该车不算可疑，直接排除。
    入场时间：取该车本次出场之前"最近一次入场事件"的时间；
      同一辆车被相机多机位/多个时刻扫到的"方向：入口"会被先按
      entry_dedup_window 秒窗口归并为同一次入场并取第一次扫描时间。
      无匹配入场则置 ENTRY_MARK_MISSING 标记。
    """
    abnormal = []
    # 按车牌建立支付下发索引，加速查询
    b_index = {}
    for rb in record_b:
        b_index.setdefault(rb["car"], []).append(rb["seconds"])

    # 按车牌归并入场扫描为"入场事件"，同一窗口内重复扫描取第一次
    entry_index = {}
    if record_entry:
        tmp = {}
        for re_item in record_entry:
            tmp.setdefault(re_item["car"], []).append(
                (re_item["seconds"], re_item["time"])
            )
        for car, lst in tmp.items():
            lst.sort()
            events = []  # 归并后的入场事件，每事件保留第一次扫描时间
            for sec, tm in lst:
                if events and sec - events[-1][0] <= entry_dedup_window:
                    continue  # 距上一入场事件不足阈值 → 同一次入场，保留第一次
                events.append((sec, tm))
            entry_index[car] = events

    for ra in record_a:
        candidates = b_index.get(ra["car"], [])
        matched = any(
            ra["seconds"] <= s <= ra["seconds"] + window_seconds
            for s in candidates
        )
        # 仅当"无支付下发匹配 且 能取到应交金额"时才判为可疑
        if not matched and ra.get("fee") is not None:
            entry_time = ENTRY_MARK_MISSING
            entry_seconds = None
            # 取本次出场之前最近的入场事件（事件列表已升序，倒序找）
            for sec, tm in reversed(entry_index.get(ra["car"], [])):
                if sec <= ra["seconds"]:
                    entry_time = tm
                    entry_seconds = sec
                    break
            anomaly = compute_anomaly(ra, entry_seconds, min_deviation)
            abnormal.append({
                "time": ra["time"],
                "car": ra["car"],
                "fee": ra.get("fee"),
                "entry_time": entry_time,
                "anomaly": anomaly,
            })
    return abnormal


def compute_anomaly(ra, entry_seconds, min_deviation=MIN_PARK_TIME_DEVIATION):
    """
    计算"异常"标记：
      0 = 正常
      1 = 可疑（如门卫遥控放行等特殊情况）

    判定：将系统"停车时间"(park_minutes，分钟) 与"入场→出场"实际时长比较，
    仅当系统停车时间比实际时长**至少多出 min_deviation 分钟**（明显不符，
    说明出场远早于系统记录的应放行时刻）时判定为可疑并标记 1；
    差异不足阈值、或找不到入场时间/停车时间时无法判定，标记 0。
    """
    park_minutes = ra.get("park_minutes")
    if park_minutes is None or entry_seconds is None:
        return 0
    actual_minutes = (ra["seconds"] - entry_seconds) / 60.0
    # 取整进位(如系统"剩余"按分钟向上取整)会有小偏差，需超过阈值才算明显异常
    if park_minutes - actual_minutes > min_deviation:
        return 1
    return 0