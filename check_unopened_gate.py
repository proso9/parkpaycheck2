# -*- coding: utf-8 -*-
"""
停车场系统日志异常车辆检测脚本

功能：
  找出"出场不开闸"且"300秒内无支付结果下发"的异常车辆。

判定逻辑（对应流程图）：
  1. 记录A：行为内包含"出场处理"且含"开闸结果：不开闸"，提取时间T与车牌。
  2. 记录B：行为内包含"==>支付结果下发："，提取时间与 JSON 中的 carNumber 车牌。
  3. 遍历记录A：
      若在记录B中存在"车牌相同 且 时间 ∈ [T, T+窗口] 秒"的记录，视为正常跳过；
      否则判定为异常，输出【时间, 车牌】。

日志格式说明：
  每行形如  HH:MM:SS - 日志内容
  部分业务行（如"出场处理…"）不带时间戳前缀，属于上一条带时间戳行的延续，
  因此解析时需要用"最近一条带时间戳行的时间"作为其时间。

所有判定参数均可通过命令行调节，未硬编码在代码内部。
"""

import re
import json
import argparse
import csv
import os
import sys


# ---------------- 参数定义（可在命令行覆盖，默认值见 argparse 下） ----------------
# 关键词与阈值
KEY_OUT_PROCESS = "出场处理"        # 出场处理标识
KEY_NO_GATE = "开闸结果：不开闸"     # 不开闸标识
KEY_PAY_DOWN = "==>支付结果下发："   # 支付结果下发标识
KEY_FEE = "用户需支付费用"           # 用户需支付费用标识
WINDOW_SECONDS = 300               # 判定窗口：出场后多少秒内算正常
FEE_LINK_WINDOW = 60               # 出场记录与其"需支付费用"行的最大间隔秒数
DEFAULT_OUT_DIR = "output"         # 默认输出目录（不放入 document，且加入 .gitignore）

# ---------- 工具函数 ----------

def time_to_seconds(t_str):
    """将 'HH:MM:SS' 转换为当天秒数；格式不合法时返回 None。"""
    parts = t_str.split(":")
    if len(parts) != 3:
        return None
    try:
        h, m, s = (int(p) for p in parts)
    except ValueError:
        return None
    if not (0 <= h < 24 and 0 <= m < 60 and 0 <= s < 60):
        return None
    return h * 3600 + m * 60 + s


# 带时间戳前缀的行：分组1=时间, 分组2=内容
LINE_TS_RE = re.compile(r"^(\d{2}:\d{2}:\d{2})\s*-\s*(.*)$")
# 从"车牌《川A...》"中提取车牌
PLATE_RE = re.compile(r"车牌《([^》]+)》")
# 从支付下发 JSON 中提取 carNumber 车牌（容错：JSON 解析失败时用正则兜底）
CARD_NO_RE = re.compile(r'"carNumber"\s*:\s*"([^"]+)"')
# 提取"用户需支付费用"金额，形如：用户需支付费用:692.00
FEE_RE = re.compile(r"用户需支付费用[:：]\s*([\d.]+)")


def extract_fee(text):
    """从文本中提取需支付金额（字符串），取不到返回 None。"""
    m = FEE_RE.search(text)
    return m.group(1) if m else None


def normalize_car(car):
    """
    车牌归一化：去首尾及内部空白、统一转为大写。
    保证记录A《…》中的车牌与记录B carNumber 字段在匹配时严格对应，
    避免因空格/大小写差异导致匹配错乱（乱套）。
    """
    if not car:
        return None
    normalized = re.sub(r"\s+", "", str(car)).upper()
    return normalized or None


def extract_plate(text):
    """从文本中提取《…》车牌号，取不到返回 None。"""
    m = PLATE_RE.search(text)
    return m.group(1).strip() if m else None


def extract_pay_car(text):
    """从未支付下发文本中提取 carNumber 车牌。优先 JSON 解析，失败降级为正则。"""
    # 去掉开头的 "==>支付结果下发：" 再尝试解析 JSON
    body = text
    idx = body.find(KEY_PAY_DOWN)
    if idx != -1:
        body = body[idx + len(KEY_PAY_DOWN):]
    try:
        obj = json.loads(body.strip())
        car = obj.get("carNumber")
        if car:
            return str(car).strip()
    except (json.JSONDecodeError, ValueError, TypeError):
        pass
    # JSON 解析失败兜底：正则提取 carNumber
    m = CARD_NO_RE.search(text)
    return m.group(1).strip() if m else None


def parse_log(file_path, fee_link_window=FEE_LINK_WINDOW):
    """
    解析日志，返回 (record_a_list, record_b_list)
      record_a: [{time:'HH:MM:SS', seconds:int, car:str, fee:str|None}]
                出场不开闸记录（fee 为该车本次出场"用户需支付费用"）
      record_b: [{time:'HH:MM:SS', seconds:int, car:str}]  支付结果下发记录
    """
    record_a = []
    record_b = []
    current_time = None          # 最近一条带时间戳行的时刻
    current_seconds = None
    last_fee_seconds = None      # 最近一条"用户需支付费用"行的时刻(秒)
    last_fee = None              # 最近一条"用户需支付费用"金额

    def reset_fee(sec):
        """费用行出现时更新最近费用与时刻。"""
        nonlocal last_fee, last_fee_seconds
        last_fee, last_fee_seconds = None, None
        if sec is not None:
            fee = extract_fee(content)
            if fee is not None:
                last_fee, last_fee_seconds = fee, sec

    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        for raw in f:
            line = raw.rstrip("\r\n")
            if not line:
                continue

            # 判断是否带时间戳前缀
            m = LINE_TS_RE.match(line)
            if m:
                ts, content = m.groups()
                sec = time_to_seconds(ts)
                current_time, current_seconds = ts, sec  # 更新最近时刻
            else:
                # 不带时间戳：业务延续行，使用最近时刻作为当前时刻
                content = line

            # 记录A：出场处理 且 不开闸
            if KEY_OUT_PROCESS in content and KEY_NO_GATE in content:
                car = normalize_car(extract_plate(content))
                if car and current_seconds is not None:
                    # 关联本车本次出场的需支付费用：
                    # 取"最近一条费用"，要求其在出场时刻之前且间隔在阈值内
                    fee = None
                    if last_fee is not None and last_fee_seconds is not None:
                        gap = current_seconds - last_fee_seconds
                        if 0 <= gap <= fee_link_window:
                            fee = last_fee
                    record_a.append({
                        "time": current_time,
                        "seconds": current_seconds,
                        "car": car,
                        "fee": fee,
                    })

            # 记录B：支付结果下发
            if KEY_PAY_DOWN in content:
                car = normalize_car(extract_pay_car(content))
                if car and current_seconds is not None:
                    record_b.append({
                        "time": current_time,
                        "seconds": current_seconds,
                        "car": car,
                    })

            # 用户需支付费用行：更新最近费用（放在记录A之后判断，费用行本身非记录A/B）
            if KEY_FEE in content:
                reset_fee(current_seconds)

    return record_a, record_b


def find_anomalies(record_a, record_b, window_seconds):
    """
    判定异常：对每条不开闸记录，在支付下发记录中查找
    车牌相同 且 时间 ∈ [T, T+window_seconds] 秒。
    返回不存在该记录的【时间, 车牌, 需支付费用】列表。

    规则：若找不到该车应交金额（fee 为空），该车不算可疑，直接排除。
    """
    abnormal = []
    # 按车牌建立索引，加速查询
    b_index = {}
    for rb in record_b:
        b_index.setdefault(rb["car"], []).append(rb["seconds"])

    for ra in record_a:
        candidates = b_index.get(ra["car"], [])
        matched = any(
            ra["seconds"] <= s <= ra["seconds"] + window_seconds
            for s in candidates
        )
        # 仅当"无支付下发匹配 且 能取到应交金额"时才判为可疑
        if not matched and ra.get("fee") is not None:
            abnormal.append({
                "time": ra["time"],
                "car": ra["car"],
                "fee": ra.get("fee"),
            })
    return abnormal


# ---------- 输出 ----------

def output_results(abnormal, log_path, out_dir):
    """
    控制台输出，并将结果导出到独立输出目录下的 CSV 文件。
    CSV 命名规则：异常车辆_<日志日期>.csv（日志形如 system.2026-08-22.log → 2026-08-22）
    """
    # 确保输出目录存在
    os.makedirs(out_dir, exist_ok=True)

    # 从日志文件名提取日期
    base = os.path.splitext(os.path.basename(log_path))[0]
    m = re.search(r"(\d{4}-\d{2}-\d{2})", base)
    date_str = m.group(1) if m else "unknown-date"
    csv_name = f"异常车辆_{date_str}.csv"
    csv_path = os.path.join(out_dir, csv_name)

    print("=" * 60)
    print(f"处理日志：{log_path}")
    print(f"识别异常车辆数：{len(abnormal)}")
    print("=" * 60)
    print(f"{'出场时间':<10}   {'车牌号':<10} {'用户需支付费用'}")
    print("-" * 60)

    # 写 CSV
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["出场时间", "车牌号", "用户需支付费用"])
        for item in abnormal:
            fee = item.get("fee")
            fee_txt = fee if fee is not None else "-"
            print(f"{item['time']:<8}   {item['car']:<12} {fee_txt}")
            writer.writerow([item["time"], item["car"], fee_txt])

    print("-" * 60)
    print(f"导出文件：{csv_path}")


# ---------- 主流程 ----------

def main():
    parser = argparse.ArgumentParser(
        description="停车场系统日志异常车辆检测：出场不开闸且窗口期无支付结果下发"
    )
    parser.add_argument(
        "log", nargs="?", default="document",
        help="日志文件路径，或包含日志的目录（默认指定目录内所有 .log 文件）"
    )
    parser.add_argument(
        "-w", "--window", type=int, default=WINDOW_SECONDS,
        help=f"判定窗口秒数，默认 {WINDOW_SECONDS}"
    )
    parser.add_argument(
        "-o", "--out", default=DEFAULT_OUT_DIR,
        help=f"CSV 导出目录，默认 {DEFAULT_OUT_DIR}（项目根目录，已加入 .gitignore）"
    )
    args = parser.parse_args()

    # 收集待处理的日志文件列表
    log_files = []
    if os.path.isdir(args.log):
        for name in sorted(os.listdir(args.log)):
            if name.endswith(".log"):
                log_files.append(os.path.join(args.log, name))
        if not log_files:
            print(f"目录 {args.log} 下未找到 .log 文件")
            sys.exit(1)
    elif os.path.isfile(args.log):
        log_files = [args.log]
    else:
        print(f"路径不存在：{args.log}")
        sys.exit(1)

    # 逐个处理
    for path in log_files:
        record_a, record_b = parse_log(path)
        abnormal = find_anomalies(record_a, record_b, args.window)
        output_results(abnormal, path, args.out)


if __name__ == "__main__":
    main()