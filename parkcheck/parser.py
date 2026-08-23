# -*- coding: utf-8 -*-
"""
日志解析模块：负责把原始日志行转成结构化记录。

- 时间转换与各类车牌 / 费用 / 入场字段的提取提取工具。
- parse_log() 是核心入口，输出三组记录供 detector 判定：
  记录A（出场不开闸）、记录B（支付结果下发）、入场记录。

日志格式说明：
  每行形如  HH:MM:SS - 日志内容
  部分业务行（如"出场处理…"）不带时间戳前缀，属于上一条带时间戳行的延续，
  因此解析时需要用"最近一条带时间戳行的时间"作为其时间。
"""

import json
import re

from .config import (
    KEY_OUT_PROCESS,
    KEY_NO_GATE,
    KEY_PAY_DOWN,
    KEY_FEE,
    KEY_ENTRY,
    KEY_PARK_TIME,
)

# 带时间戳前缀的行：分组1=时间, 分组2=内容
LINE_TS_RE = re.compile(r"^(\d{2}:\d{2}:\d{2})\s*-\s*(.*)$")
# 从"车牌《川A...》"中提取车牌
PLATE_RE = re.compile(r"车牌《([^》]+)》")
# 从支付下发 JSON 中提取 carNumber 车牌（容错：JSON 解析失败时用正则兜底）
CARD_NO_RE = re.compile(r'"carNumber"\s*:\s*"([^"]+)"')
# 提取"用户需支付费用"金额，形如：用户需支付费用:692.00
FEE_RE = re.compile(r"用户需支付费用[:：]\s*([\d.]+)")
# 提取入场车牌号，形如：入场车牌号：川AHT168，入场车牌类型：…
ENTRY_RE = re.compile(r"入场车牌号[:：]\s*([^，,]+)")
# 提取相机扫描行中的车牌，形如：…,车牌号：川GF2S38,内外场：…
CAM_PLATE_RE = re.compile(r"车牌号[:：]\s*([^，,]+)")
# 相机扫描方向，形如：方向：入口 / 方向：出口（含"重复上传"等前缀行）
DIR_IN_RE = re.compile(r"方向[:：]\s*入口")
# 提取停车时间，形如：停车时间:4天,剩余:1418分钟 → (天, 分钟)
PARK_TIME_RE = re.compile(r"停车时间[:：]\s*(\d+)\s*天[，,]\s*剩余[:：]?\s*(\d+)\s*分钟")


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


def extract_fee(text):
    """从文本中提取需支付金额（字符串），取不到返回 None。"""
    m = FEE_RE.search(text)
    return m.group(1) if m else None


def extract_entry_plate(text):
    """从入场记录中提取车牌（归一化前原始值），取不到返回 None。"""
    m = ENTRY_RE.search(text)
    return m.group(1).strip() if m else None


def extract_cam_plate(text):
    """从相机扫描行中提取"车牌号"字段（归一化前原始值），取不到返回 None。"""
    m = CAM_PLATE_RE.search(text)
    return m.group(1).strip() if m else None


def extract_park_minutes(text):
    """从文本中提取系统"停车时间"，换算成总分钟数；取不到返回 None。

    形如 "停车时间:4天,剩余:1418分钟" → 4*24*60 + 1418 = 7178 分钟。
    用于与"入场→出场"实际时长比较，判断是否存在被遥控放行等可疑情况。
    """
    m = PARK_TIME_RE.search(text)
    if not m:
        return None
    try:
        days = int(m.group(1))
        minutes = int(m.group(2))
    except ValueError:
        return None
    return days * 24 * 60 + minutes


def parse_log(file_path):
    """
    解析日志，返回 (record_a_list, record_b_list, record_entry_list)
      record_a: [{time:'HH:MM:SS', seconds:int, car:str, fee:str|None,
                  park_minutes:int|None}]
                出场不开闸记录（fee 为该车本次出场"用户需支付费用"，
                park_minutes 为该车本次出场的系统"停车时间"总分钟数。
                费用/停车时间与出场相机识别同时出现，故按日志顺序直接
                取最近一条（仅要求其时刻不晚于本出场），不再设关联窗口）
      record_b: [{time:'HH:MM:SS', seconds:int, car:str}]  支付结果下发记录
      record_entry: [{time:'HH:MM:SS', seconds:int, car:str}]
                入场记录（用于反查异常车辆入场时间）
    """
    record_a = []
    record_b = []
    record_entry = []
    current_time = None          # 最近一条带时间戳行的时刻
    current_seconds = None
    last_fee_seconds = None      # 最近一条"用户需支付费用"行的时刻(秒)
    last_fee = None              # 最近一条"用户需支付费用"金额
    last_park_seconds = None     # 最近一条"停车时间"行的时刻(秒)
    last_park_minutes = None     # 最近一条"停车时间"总分钟数

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
                    # 费用/停车时间行与出场相机识别同时出现，直接按日志顺序取最近一条；
                    # 仅要求其时刻不晚于本次出场（当前秒数 >= 该行秒数），不再设关联窗口
                    fee = None
                    if (last_fee is not None and last_fee_seconds is not None
                            and current_seconds >= last_fee_seconds):
                        fee = last_fee
                    park_minutes = None
                    if (last_park_minutes is not None and last_park_seconds is not None
                            and current_seconds >= last_park_seconds):
                        park_minutes = last_park_minutes
                    record_a.append({
                        "time": current_time,
                        "seconds": current_seconds,
                        "car": car,
                        "fee": fee,
                        "park_minutes": park_minutes,
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

            # 入场事件：相机"方向：入口"扫描（含重复上传），或"入场车牌号"行兜底。
            # 同一辆车被多机位重复扫到也会在此被重复记录，
            # 由 detector 按 ENTRY_DEDUP_WINDOW 归并并取第一次。
            if DIR_IN_RE.search(content):
                car = normalize_car(extract_cam_plate(content))
                if car and current_seconds is not None:
                    record_entry.append({
                        "time": current_time,
                        "seconds": current_seconds,
                        "car": car,
                    })
            if KEY_ENTRY in content:
                car = normalize_car(extract_entry_plate(content))
                if car and current_seconds is not None:
                    record_entry.append({
                        "time": current_time,
                        "seconds": current_seconds,
                        "car": car,
                    })

            # 用户需支付费用行：更新最近费用（放在记录A之后判断，费用行本身非记录A/B）
            if KEY_FEE in content:
                reset_fee(current_seconds)

            # 停车时间行：更新最近停车时间（该行为出场相机扫描后的业务行）
            if KEY_PARK_TIME in content:
                pm = extract_park_minutes(content)
                if pm is not None and current_seconds is not None:
                    last_park_minutes, last_park_seconds = pm, current_seconds

    return record_a, record_b, record_entry