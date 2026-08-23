# -*- coding: utf-8 -*-
"""
结果输出模块：控制台打印异常车辆，并导出 CSV 到独立输出目录。
"""

import csv
import os
import re

from .config import ENTRY_MARK_MISSING

# 从日志文件名提取日期，形如 system.2026-08-22.log → 2026-08-22
LOG_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def output_results(abnormal, log_path, out_dir):
    """
    控制台输出，并将结果导出到独立输出目录下的 CSV 文件。
    CSV 命名规则：异常车辆_<日志日期>.csv（日志形如 system.2026-08-22.log → 2026-08-22）
    """
    # 确保输出目录存在
    os.makedirs(out_dir, exist_ok=True)

    # 从日志文件名提取日期
    base = os.path.splitext(os.path.basename(log_path))[0]
    m = LOG_DATE_RE.search(base)
    date_str = m.group(1) if m else "unknown-date"
    csv_name = f"异常车辆_{date_str}.csv"
    csv_path = os.path.join(out_dir, csv_name)

    print("=" * 60)
    print(f"处理日志：{log_path}")
    print(f"识别异常车辆数：{len(abnormal)}")
    print("=" * 60)
    print(f"{'入场时间':<10}{'出场时间':<10}   {'车牌号':<10} {'用户需支付费用':<12}{'异常'}")
    print("-" * 60)

    # 写 CSV
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["入场时间", "出场时间", "车牌号", "用户需支付费用", "异常"])
        for item in abnormal:
            fee = item.get("fee")
            fee_txt = fee if fee is not None else "-"
            entry = item.get("entry_time", ENTRY_MARK_MISSING)
            anomaly = item.get("anomaly", 0)
            print(f"{entry:<10}{item['time']:<8}   {item['car']:<12} {fee_txt:<12} {anomaly}")
            writer.writerow([entry, item["time"], item["car"], fee_txt, anomaly])

    print("-" * 60)
    print(f"导出文件：{csv_path}")