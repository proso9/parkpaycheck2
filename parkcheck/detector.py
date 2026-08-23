# -*- coding: utf-8 -*-
"""
异常判定模块：基于解析出的记录判定"出场不开闸且窗口期无支付下发"的异常车辆。

判定规则：
  对每条记录A（出场不开闸），在记录B（支付结果下发）中查找
  车牌相同 且 时间 ∈ [T, T+window_seconds] 秒 的记录；
  若找不到，且该车能取到应交金额，则判为可疑。
"""

from .config import ENTRY_MARK_MISSING


def find_anomalies(record_a, record_b, window_seconds, record_entry=None):
    """
    判定异常：对每条不开闸记录，在支付下发记录中查找
    车牌相同 且 时间 ∈ [T, T+window_seconds] 秒。
    返回不存在该记录的【时间, 车牌, 需支付费用, 入场时间】列表。

    规则：若找不到该车应交金额（fee 为空），该车不算可疑，直接排除。
    入场时间：先界定"同一次停车事件"的时间范围（以本次出场为下界，
              以该车上一次出场——无则 00:00:00——为上界），再在该范围内
              取"最新一遍"入场扫描；范围内无入场则置 ENTRY_MARK_MISSING 标记。
    """
    abnormal = []
    # 按车牌建立索引，加速查询
    b_index = {}
    for rb in record_b:
        b_index.setdefault(rb["car"], []).append(rb["seconds"])

    # 按车牌索引入场记录（保留时间与秒数），用于反查入场时间
    entry_index = {}
    if record_entry:
        for re_item in record_entry:
            entry_index.setdefault(re_item["car"], []).append(
                (re_item["seconds"], re_item["time"])
            )
        for lst in entry_index.values():
            lst.sort(key=lambda x: x[0])

    # 按车牌索引该车每次出场时刻（不开闸出场同样是一次出场事件），
    # 用于界定本次停车对应入场搜索区间在时间轴上的上界
    exit_index = {}
    for ra0 in record_a:
        exit_index.setdefault(ra0["car"], []).append(ra0["seconds"])
    for lst in exit_index.values():
        lst.sort()

    for ra in record_a:
        candidates = b_index.get(ra["car"], [])
        matched = any(
            ra["seconds"] <= s <= ra["seconds"] + window_seconds
            for s in candidates
        )
        # 仅当"无支付下发匹配 且 能取到应交金额"时才判为可疑
        if not matched and ra.get("fee") is not None:
            entry_time = ENTRY_MARK_MISSING
            entries = entry_index.get(ra["car"], [])
            # ---- 入场时间准确性保障：先界定"同一次停车"，再取"最新一遍" ----
            # ① 同一次停车事件：以本次出场秒为区间下界(含)，向上(更早方向)
            #    查找该车本次停车此时段。先定区间上界：取该车紧随本次出场
            #    之前的最近一次出场；若没有其他出场，则以 00:00:00(秒数0) 为界。
            #    如此把搜索范围限制在"上次出场之后 ~ 本次出场"之间，
            #    落在更早停车周期的入场被排除，避免串号。
            upper = 0  # 区间上界，默认 00:00:00
            for e in reversed(exit_index.get(ra["car"], [])):  # 已升序，倒序取最近
                if e < ra["seconds"]:
                    upper = e
                    break
            # ② 重复扫描取最新：在(上界, 本次出场]区间内自后向前取第一条入场
            for s, t in reversed(entries):
                if upper < s <= ra["seconds"]:
                    entry_time = t
                    break
            abnormal.append({
                "time": ra["time"],
                "car": ra["car"],
                "fee": ra.get("fee"),
                "entry_time": entry_time,
            })
    return abnormal