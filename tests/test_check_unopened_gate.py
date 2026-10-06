# -*- coding: utf-8 -*-
"""
parkcheck 包的测试脚本（无第三方依赖，直接运行即可）。

覆盖各类场景，检验程序逻辑与健壮性：
  1. 正常：不开闸 + 窗口内支付下发 → 判定为正常
  2. 异常：不开闸 + 无支付下发 → 判定为异常
  3. 边界：支付下发恰好在 T+300 秒 → 正常
  4. 边界：支付下发在 T+301 秒 → 异常
  5. 支付下发在出场之前 → 不算在窗口内 → 异常
  6. 出场处理行无时间戳，需继承上一层时刻
  7. 车牌含空格/大小写差异 → 归一化后仍能正确匹配
  8. 非法时间格式 → 容错跳过，不崩溃
  9. 出场行缺失车牌 → 容错跳过，不崩溃
  10. carNumber JSON 解析失败 → 退化为正则兜底提取
  11. 同一车牌多次不开闸 → 各自独立判断
  12. 支付下发为其他车牌 → 不算匹配 → 异常
  13. 空日志 → 无异常
  14. 输出 CSV 生成正确（含表头与数据）
  15. 异常车辆反查入场时间成功
  16. 异常车辆找不到入场时间 → 标记占位符
  17. 费用/停车时间按"爆点"关联：仅关联本次"方向：出口"扫描之后的行，
      前车的费用/停车时间不得串到后车（避免金额取错与可疑误判）
  18. 日志选取：目录扫描仅分析 system.<YYYY-MM-DD>.log；
      platform.* 等其他前缀、无日期日志不进入分析逻辑
"""

import os
import sys
import tempfile
import csv

# 将项目根目录加入 sys.path，以便导入被测试包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Windows 控制台默认编码可能是 cp1252/gbk，统一改为 UTF-8 输出，
# 避免 print 中文（Windows 版 GitHub Actions 运行器同样受影响）时抛 UnicodeEncodeError
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # 标准流被替换或不支持重配置时忽略
        pass

import parkcheck as cug  # noqa: E402
from parkcheck.cli import build_parser, run_detection_round  # noqa: E402


# ---------- 工具 ----------

def write_log(filename, lines):
    """把带/不带时间戳的行写入临时日志文件，返回路径。"""
    path = os.path.join(tempfile.gettempdir(), filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))
    return path


def cars_of(abnormal):
    """把异常列表转成 (时间, 车牌) 集合，便于断言。"""
    return {(item["time"], item["car"]) for item in abnormal}


PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[通过] {name}")
    else:
        FAIL += 1
        print(f"[失败] {name}  {detail}")


# ---------- 用例 ----------

def test_normal_within_window():
    path = write_log("t_normal.log", [
        "10:00:00 - 出场处理,车牌《川A12345》，开闸结果：不开闸",
        "10:04:59 - ==>支付结果下发：{\"carNumber\":\"川A12345\",\"payAmount\":5.0}",
    ])
    a, b, entry = cug.parse_log(path)
    abnormal = cug.find_anomalies(a, b, cug.WINDOW_SECONDS, entry)
    check("正常：窗口期内支付下发 → 无异常", not abnormal,
          f"实际异常: {abnormal}")


def test_abnormal_no_pay():
    path = write_log("t_nopay.log", [
        "10:59:00 - 无商户优惠，用户需支付费用:9.00",
        "11:00:00 - 出场处理,车牌《川B99999》，开闸结果：不开闸",
    ])
    a, b, entry = cug.parse_log(path)
    abnormal = cug.find_anomalies(a, b, 300, entry)
    check("异常：无支付下发 → 判定为异常",
          cars_of(abnormal) == {("11:00:00", "川B99999")},
          f"实际: {cars_of(abnormal)}")


def test_abnormal_no_fee_excluded():
    # 新规则：找不到应交金额的可疑车 → 不算（排除）
    path = write_log("t_nofee.log", [
        "11:00:00 - 出场处理,车牌《川B10000》，开闸结果：不开闸",
    ])
    a, b, entry = cug.parse_log(path)
    abnormal = cug.find_anomalies(a, b, 300, entry)
    check("无金额的可疑车 → 不判为异常", not abnormal,
          f"实际: {abnormal}")


def test_boundary_t300():
    path = write_log("t_t300.log", [
        "11:59:00 - 无商户优惠，用户需支付费用:5.00",
        "12:00:00 - 出场处理,车牌《川C11111》，开闸结果：不开闸",
        "12:05:00 - ==>支付结果下发：{\"carNumber\":\"川C11111\"}",
    ])
    a, b, entry = cug.parse_log(path)
    abnormal = cug.find_anomalies(a, b, 300, entry)
    check("边界：支付下发恰在 T+300 → 正常", not abnormal,
          f"实际异常: {abnormal}")


def test_boundary_t301():
    path = write_log("t_t301.log", [
        "12:59:00 - 无商户优惠，用户需支付费用:8.00",
        "13:00:00 - 出场处理,车牌《川D22222》，开闸结果：不开闸",
        "13:05:01 - ==>支付结果下发：{\"carNumber\":\"川D22222\"}",
    ])
    a, b, entry = cug.parse_log(path)
    abnormal = cug.find_anomalies(a, b, 300, entry)
    check("边界：支付下发 T+301 → 异常",
          cars_of(abnormal) == {("13:00:00", "川D22222")},
          f"实际: {cars_of(abnormal)}")


def test_pay_before_event():
    path = write_log("t_paybefore.log", [
        "13:59:00 - ==>支付结果下发：{\"carNumber\":\"川E33333\"}",
        "13:59:00 - 无商户优惠，用户需支付费用:6.00",
        "14:00:00 - 出场处理,车牌《川E33333》，开闸结果：不开闸",
    ])
    a, b, entry = cug.parse_log(path)
    abnormal = cug.find_anomalies(a, b, 300, entry)
    check("支付下发在出场之前 → 不算窗口内 → 异常",
          cars_of(abnormal) == {("14:00:00", "川E33333")},
          f"实际: {cars_of(abnormal)}")


def test_continuation_line_time():
    # 出场处理行不带时间戳，应继承上一行 15:00:00 的时刻
    path = write_log("t_cont.log", [
        "15:00:00 - 相机序列号：123,车牌号：川F44444,方向：出口",
        "15:00:00 - 无商户优惠，用户需支付费用:12.00",
        "出场处理,车牌《川F44444》，开闸结果：不开闸",
        "耗时:142毫秒",
    ])
    a, b, entry = cug.parse_log(path)
    check("无时间戳延续行继承上层时刻",
          len(a) == 1 and a[0]["time"] == "15:00:00" and a[0]["car"] == "川F44444",
          f"记录A: {a}")
    check("无支付下发 → 该延续行时间为异常的",
          cars_of(cug.find_anomalies(a, b, 300, entry)) == {("15:00:00", "川F44444")},
          f"实际: {cars_of(cug.find_anomalies(a, b, 300, entry))}")


def test_normalize_case_space():
    path = write_log("t_norm.log", [
        "16:00:00 - 出场处理,车牌《 川g12345 》，开闸结果：不开闸",
        "16:01:00 - ==>支付结果下发：{\"carNumber\":\"川G12345\"}",
    ])
    a, b, entry = cug.parse_log(path)
    abnormal = cug.find_anomalies(a, b, 300, entry)
    check("车牌带空格/大小写 → 归一化后匹配成功 → 正常", not abnormal,
          f"实际异常: {abnormal}")


def test_invalid_time_ignored():
    # 非法时间 99:99:99 → time_to_seconds 返回 None，该行不被加入，不崩溃
    path = write_log("t_badtime.log", [
        "99:99:99 - 出场处理,车牌《川H55555》，开闸结果：不开闸",
    ])
    a, b, entry = cug.parse_log(path)
    check("非法时间格式 → 容错跳过，无记录", len(a) == 0 and len(b) == 0 and len(entry) == 0,
          f"记录A: {a}, 记录B: {b}")


def test_missing_plate_ignored():
    path = write_log("t_noplate.log", [
        "17:00:00 - 出场处理,开闸结果：不开闸",
    ])
    a, b, entry = cug.parse_log(path)
    check("出场行缺失车牌 → 容错跳过，不崩溃", len(a) == 0,
          f"记录A: {a}")


def test_pay_json_fallback_regex():
    # JSON 非法，但正则仍可提取 carNumber → 正常匹配
    path = write_log("t_badjson.log", [
        "18:00:00 - 出场处理,车牌《川J66666》，开闸结果：不开闸",
        "18:00:30 - ==>支付结果下发：{\"carNumber\":\"川J66666\", 这行不是合法JSON",
    ])
    a, b, entry = cug.parse_log(path)
    check("carNumber JSON 解析失败 → 正则兜底提取",
          len(b) == 1 and b[0]["car"] == "川J66666", f"记录B: {b}")
    abnormal = cug.find_anomalies(a, b, 300, entry)
    check("正则兜底提取后能正确匹配 → 正常", not abnormal,
          f"实际异常: {abnormal}")


def test_multiple_events_same_car():
    path = write_log("t_multi.log", [
        "19:00:00 - 出场处理,车牌《川A7M4R6》，开闸结果：不开闸",
        "19:00:10 - 出场处理,车牌《川A7M4R6》，开闸结果：不开闸",
        "19:00:20 - ==>支付结果下发：{\"carNumber\":\"川A7M4R6\"}",
    ])
    a, b, entry = cug.parse_log(path)
    abnormal = cug.find_anomalies(a, b, 300, entry)
    check("同一车牌多次不开闸、均在窗口内有支付 → 全部正常", not abnormal,
          f"实际异常: {abnormal}")


def test_unrelated_pay_plate():
    path = write_log("t_unrel.log", [
        "19:59:00 - 无商户优惠，用户需支付费用:3.00",
        "20:00:00 - 出场处理,车牌《川K77777》，开闸结果：不开闸",
        "20:00:30 - ==>支付结果下发：{\"carNumber\":\"川Z99999\"}",
    ])
    a, b, entry = cug.parse_log(path)
    abnormal = cug.find_anomalies(a, b, 300, entry)
    check("支付下发为其他车牌 → 不匹配 → 异常",
          cars_of(abnormal) == {("20:00:00", "川K77777")},
          f"实际: {cars_of(abnormal)}")


def test_empty_log():
    path = write_log("t_empty.log", [])
    a, b, entry = cug.parse_log(path)
    abnormal = cug.find_anomalies(a, b, 300, entry)
    check("空日志 → 无异常", len(a) == 0 and len(b) == 0 and not abnormal,
          f"记录A: {a}, 记录B: {b}")


def test_fee_linked_to_event():
    # 需支付费用行与出场处理同块 → 费用正确关联到出场记录
    path = write_log("t_fee.log", [
        "08:12:58 - 无商户优惠，用户需支付费用:692.00",
        "出场处理,车牌《川A7M4R6》，开闸结果：不开闸",
    ])
    a, b, entry = cug.parse_log(path)
    check("费用成功关联到出场记录",
          len(a) == 1 and a[0]["fee"] == "692.00",
          f"记录A: {a}")
    abnormal = cug.find_anomalies(a, b, 300, entry)
    check("异常结果携带费用字段",
          abnormal and abnormal[0].get("fee") == "692.00",
          f"实际: {abnormal}")


def test_fee_linked_only_within_own_burst():
    # 出口相机扫描行是费用关联的边界：扫描之前出现的费用（哪怕同一秒）
    # 属于上一辆车/上一爆点，不得关联到本次出场
    path = write_log("t_fee_burst_prev.log", [
        "10:00:00 - 无商户优惠，用户需支付费用:5.00",   # 上一辆车遗留的费用
        "10:00:00 - 相机序列号：1,车牌号：川A10086,内外场：外场,方向：出口",
        "出场处理,车牌《川A10086》，开闸结果：不开闸",   # 本爆点无费用行 → 不关联
    ])
    a, b, entry = cug.parse_log(path)
    check("出口扫描之前出现的费用不关联本次出场",
          len(a) == 1 and a[0]["fee"] is None,
          f"记录A: {a}")
    check("无应交金额 → 不判为异常",
          cug.find_anomalies(a, b, 300, entry) == [],
          f"实际: {cug.find_anomalies(a, b, 300, entry)}")


def test_fee_linked_after_exit_scan():
    # 真实日志结构：出口扫描 → 费用行 → 出场处理，费用正常关联本次出场
    path = write_log("t_fee_burst_own.log", [
        "10:00:00 - 相机序列号：1,车牌号：川A10087,内外场：外场,方向：出口",
        "10:00:00 - 无商户优惠，用户需支付费用:7.00",
        "出场处理,车牌《川A10087》，开闸结果：不开闸",
    ])
    a, b, entry = cug.parse_log(path)
    check("出口扫描之后的费用关联本次出场",
          len(a) == 1 and a[0]["fee"] == "7.00",
          f"记录A: {a}")


def test_fee_park_time_not_leaked_across_cars():
    # 回归（线上问题）：前车爆点的费用/停车时间不得串到后车上。
    # 川ACP230 出场不开闸后窗口内正常支付；川ADN060 出场不开闸且本爆点
    # 无费用/停车时间行。旧逻辑把 4 小时前川ACP230 的费用(25.00)与停车
    # 时间(740分钟)串给川ADN060，导致金额取错且被误判"异常=1"
    path = write_log("t_fee_leak_across_cars.log", [
        "01:23:10 - 相机序列号：192.168.31.132,车牌号：川ACP230,内外场：外场,方向：出口",
        "01:23:10 - 停车时间:0天,剩余:740分钟",
        "01:23:10 - 无商户优惠，用户需支付费用:25.00",
        "出场处理,车牌《川ACP230》，开闸结果：不开闸",
        "01:23:21 - 无商户优惠，用户需支付费用:25.00",   # 支付流程重算费用（仍是前车）
        "01:23:39 - ==>支付结果下发：{\"carNumber\":\"川ACP230\",\"payAmount\":25.0}",
        "05:37:48 - 相机序列号：192.168.31.132,车牌号：川ADN060,内外场：外场,方向：出口",
        "出场处理,车牌《川ADN060》，开闸结果：不开闸",     # 本爆点无费用/停车时间行
    ])
    a, b, entry = cug.parse_log(path)
    check("后车不取到前车费用/停车时间",
          len(a) == 2 and a[1]["car"] == "川ADN060"
          and a[1]["fee"] is None and a[1]["park_minutes"] is None,
          f"记录A: {a}")
    check("前车窗口内有支付 → 正常；后车无应交金额 → 不输出",
          cug.find_anomalies(a, b, 300, entry) == [],
          f"实际: {cug.find_anomalies(a, b, 300, entry)}")


def test_park_time_not_leaked_across_cars():
    # 回归：前车爆点的"停车时间"不得参与本次出场的可疑判定
    # （否则前车 7178 分钟对比本次约 5 分钟实际时长会误判"异常=1"）
    path = write_log("t_park_leak_across_cars.log", [
        "09:55:00 - 入场车牌号：川B00002，入场车牌类型：临时蓝牌,入场备注：null",
        "10:00:00 - 相机序列号：1,车牌号：川B00001,内外场：外场,方向：出口",
        "10:00:00 - 停车时间:4天,剩余:1418分钟",           # 前车的停车时间 = 7178 分钟
        "10:00:00 - 无商户优惠，用户需支付费用:150.00",
        "出场处理,车牌《川B00001》，开闸结果：开闸",         # 前车正常开闸，非记录A
        "10:00:10 - 相机序列号：1,车牌号：川B00002,内外场：外场,方向：出口",  # 重置
        "10:00:10 - 无商户优惠，用户需支付费用:8.00",        # 本爆点只有费用，无停车时间
        "出场处理,车牌《川B00002》，开闸结果：不开闸",
    ])
    a, b, entry = cug.parse_log(path)
    check("后车不取到前车停车时间，费用取本爆点",
          len(a) == 1 and a[0]["car"] == "川B00002"
          and a[0]["park_minutes"] is None and a[0]["fee"] == "8.00",
          f"记录A: {a}")
    abnormal = cug.find_anomalies(a, b, 300, entry)
    check("停车时间缺失 → 异常标记 0（不因前车停车时间误判可疑）",
          len(abnormal) == 1 and abnormal[0]["anomaly"] == 0,
          f"实际: {abnormal}")


def test_fee_on_same_timestamp_multiple_events():
    # 同一时间戳内多次出场，费用应关联最近一次费用记录
    path = write_log("t_fee_multi.log", [
        "12:00:00 - 无商户优惠，用户需支付费用:10.00",
        "出场处理,车牌《川A20001》，开闸结果：不开闸",
        "12:00:00 - 无商户优惠，用户需支付费用:20.00",
        "出场处理,车牌《川A20002》，开闸结果：不开闸",
    ])
    a, b, entry = cug.parse_log(path)
    check("多次出场各自关联最近费用",
          len(a) == 2 and a[0]["fee"] == "10.00" and a[1]["fee"] == "20.00",
          f"记录A: {a}")


def test_output_csv():
    # 用临时输出目录验证 CSV 生成（表头 + 数据 + 文件名带日期）
    abnormal = [
        {"time": "21:07:12", "car": "川ABA0530", "fee": "692.00", "entry_time": "08:30:00", "anomaly": 1},
        {"time": "08:12:58", "car": "川A7M4R6", "fee": None, "entry_time": "-", "anomaly": 0},
    ]
    out_dir = tempfile.mkdtemp()
    log_path = os.path.join(tempfile.gettempdir(), "system.2026-08-22.log")
    cug.output_results(abnormal, log_path, out_dir)
    csv_path = os.path.join(out_dir, "异常车辆_2026-08-22.csv")
    ok = os.path.exists(csv_path)
    rows = []
    if ok:
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
    check("CSV 文件已生成且命名含日志日期", ok and "异常车辆_2026-08-22.csv" in os.listdir(out_dir),
          f"目录: {os.listdir(out_dir) if ok else '文件未生成'}")
    check("CSV 表头正确", ok and rows[:1] == [["入场时间", "出场时间", "车牌号", "用户需支付费用", "异常"]],
          f"表头: {rows[:1] if ok else ''}")
    check("CSV 数据行正确", ok and rows[1] == ["08:30:00", "21:07:12", "川ABA0530", "692.00", "1"]
          and rows[2] == ["-", "08:12:58", "川A7M4R6", "-", "0"],
          f"数据: {rows if ok else ''}")


def test_entry_time_found():
    # 异常车辆能在同一日志中找到入场时间（取最近一次不晚于出场的入场）
    path = write_log("t_entry_found.log", [
        "08:30:00 - 入场车牌号：川A10001，入场车牌类型：月租车A,入场备注：null",
        "09:00:00 - 无商户优惠，用户需支付费用:5.00",
        "09:00:00 - 出场处理,车牌《川A10001》，开闸结果：不开闸",
    ])
    a, b, entry = cug.parse_log(path)
    abnormal = cug.find_anomalies(a, b, 300, entry)
    check("异常车辆反查入场时间成功",
          len(abnormal) == 1 and abnormal[0]["entry_time"] == "08:30:00",
          f"实际: {abnormal}")


def test_entry_time_missing():
    # 日志中没有该车入场记录 → 入场时间用占位符标记
    path = write_log("t_entry_missing.log", [
        "09:00:00 - 无商户优惠，用户需支付费用:6.00",
        "09:00:00 - 出场处理,车牌《川A20002》，开闸结果：不开闸",
    ])
    a, b, entry = cug.parse_log(path)
    abnormal = cug.find_anomalies(a, b, 300, entry)
    check("找不到入场时间 → 标记占位符",
          len(abnormal) == 1 and abnormal[0]["entry_time"] == cug.ENTRY_MARK_MISSING,
          f"实际: {abnormal}")


def test_entry_takes_latest_scan():
    # 同一入场被相机重复扫到数遍（5秒窗口内）→ 归并为同一次入场，取第一次
    path = write_log("t_entry_latest.log", [
        "07:00:00 - 入场车牌号：川A30001，入场车牌类型：月租车A,入场备注：null",
        "07:00:02 - 入场车牌号：川A30001，入场车牌类型：月租车A,入场备注：null",
        "07:00:04 - 入场车牌号：川A30001，入场车牌类型：月租车A,入场备注：null",
        "09:00:00 - 无商户优惠，用户需支付费用:8.00",
        "09:00:00 - 出场处理,车牌《川A30001》，开闸结果：不开闸",
    ])
    a, b, entry = cug.parse_log(path)
    abnormal = cug.find_anomalies(a, b, 300, entry)
    check("重复入口扫描(5秒内) → 取第一次 07:00:00",
          abnormal and abnormal[0]["entry_time"] == "07:00:00",
          f"实际: {abnormal}")


def test_entry_takes_recent_event():
    # 一天多次停车：本次出场取"最近一次入场事件"（该事件内取第一次）
    path = write_log("t_entry_recent.log", [
        "07:00:00 - 入场车牌号：川A30002，入场车牌类型：临时蓝牌,入场备注：null",
        "07:00:02 - 入场车牌号：川A30002，入场车牌类型：临时蓝牌,入场备注：null",
        "12:30:00 - 入场车牌号：川A30002，入场车牌类型：临时蓝牌,入场备注：null",
        "12:30:02 - 入场车牌号：川A30002，入场车牌类型：临时蓝牌,入场备注：null",
        "14:00:00 - 无商户优惠，用户需支付费用:6.00",
        "14:00:00 - 出场处理,车牌《川A30002》，开闸结果：不开闸",
    ])
    a, b, entry = cug.parse_log(path)
    events = cug.find_anomalies(a, b, 300, entry)
    check("多次入场 → 本次出场取最近一次入场事件第一次 12:30:00",
          events and events[0]["entry_time"] == "12:30:00",
          f"实际: {events}")


def test_exit_retry_shares_entry():
    # 同一辆车连续两次"出场不开闸"（闸未开重试，中间无新入场）
    # → 两次出场都属于同一停车周期，应共享同一次入场（取入口扫描第一次）
    path = write_log("t_exit_retry.log", [
        "11:01:25 - 相机序列号：192.168.31.131,车牌号：川GF2S38,内外场：外场,方向：入口",
        "11:01:26 - 重复上传相机序列号：192.168.31.133,车牌号：川GF2S38,内外场：外场,方向：入口",
        "11:01:25 - 入场车牌号：川GF2S38，入场车牌类型：临时蓝牌,入场备注：null",
        "12:40:53 - 无商户优惠，用户需支付费用:5.00",
        "12:40:53 - 出场处理,车牌《川GF2S38》，开闸结果：不开闸",
        "12:41:04 - 无商户优惠，用户需支付费用:5.00",
        "12:41:04 - 出场处理,车牌《川GF2S38》，开闸结果：不开闸",
    ])
    a, b, entry = cug.parse_log(path)
    abnormal = cug.find_anomalies(a, b, 300, entry)
    check("连续两次出场不开闸 → 共享入场 11:01:25",
          len(abnormal) == 2 and all(it["entry_time"] == "11:01:25" for it in abnormal),
          f"实际: {abnormal}")


def test_entry_matches_recent_parking():
    # 一天多次停车：异常出场应匹配"这次停车"的入场，而非更早周期
    path = write_log("t_entry_multi_park.log", [
        "06:00:00 - 入场车牌号：川A40002，入场车牌类型：临时蓝牌,入场备注：null",
        "09:00:00 - 出场处理,车牌《川A40002》，开闸结果：开闸",  # 第一次停车正常出场
        "09:00:00 - ==>支付结果下发：{\"carNumber\":\"川A40002\"}",
        "10:00:00 - 入场车牌号：川A40002，入场车牌类型：临时蓝牌,入场备注：null",
        "12:29:00 - 无商户优惠，用户需支付费用:5.00",
        "12:30:00 - 出场处理,车牌《川A40002》，开闸结果：不开闸",  # 第二次停车异常
    ])
    a, b, entry = cug.parse_log(path)
    abnormal = cug.find_anomalies(a, b, 300, entry)
    check("多次停车 → 入场取本次(最近)停车 10:00:00",
          abnormal and abnormal[0]["entry_time"] == "10:00:00",
          f"实际: {abnormal}")


def test_entry_not_from_prior_parking():
    # 只有一次入场扫描(无再入场)：本次异常出场取"最近一次入场事件"。
    # 若该车再未入场则仍可回溯到最远一次入场（口径：取最近入口扫描）。
    path = write_log("t_entry_no_entry_this.log", [
        "06:00:00 - 入场车牌号：川A50000，入场车牌类型：临时蓝牌,入场备注：null",
        "07:00:00 - 出场处理,车牌《川A50000》，开闸结果：不开闸",  # 第一次停车正常(有支付)
        "07:00:00 - ==>支付结果下发：{\"carNumber\":\"川A50000\"}",
        "12:00:00 - 无商户优惠，用户需支付费用:5.00",
        "12:00:00 - 出场处理,车牌《川A50000》，开闸结果：不开闸",  # 本次停车异常(无再入场)
    ])
    a, b, entry = cug.parse_log(path)
    abnormal = cug.find_anomalies(a, b, 300, entry)
    check("只有一次入场扫描 → 取最近一次入场 06:00:00",
          len(abnormal) == 1 and abnormal[0]["entry_time"] == "06:00:00",
          f"实际: {abnormal}")


def test_park_time_anomaly_positive():
    # 系统"停车时间"明显大于"入场→出场"实际时长（如门卫遥控放行）→ 异常标记 1
    path = write_log("t_park_anomaly.log", [
        "05:32:06 - 相机序列号：192.168.31.131,车牌号：川ADN060,内外场：外场,方向：入口",
        "05:40:48 - 停车时间:4天,剩余:1418分钟",   # = 7178 分钟
        "05:40:48 - 无商户优惠，用户需支付费用:150.00",
        "05:40:48 - 出场处理,车牌《川ADN060》，开闸结果：不开闸",
    ])
    a, b, entry = cug.parse_log(path)
    abnormal = cug.find_anomalies(a, b, 300, entry)
    check("停车时间 > 实际时长 → 标记异常 1",
          len(abnormal) == 1 and abnormal[0]["anomaly"] == 1,
          f"实际: {abnormal}")


def test_park_time_anomaly_normal():
    # 系统"停车时间"与实际时长相符 → 标记 0
    path = write_log("t_park_normal.log", [
        "06:00:00 - 相机序列号：192.168.31.131,车牌号：川B20000,内外场：外场,方向：入口",
        "06:30:00 - 停车时间:0天,剩余:30分钟",   # = 30 分钟，与实际 30 分钟相符
        "06:30:00 - 无商户优惠，用户需支付费用:3.00",
        "06:30:00 - 出场处理,车牌《川B20000》，开闸结果：不开闸",
    ])
    a, b, entry = cug.parse_log(path)
    abnormal = cug.find_anomalies(a, b, 300, entry)
    check("停车时间 ≈ 实际时长 → 标记 0",
          len(abnormal) == 1 and abnormal[0]["anomaly"] == 0,
          f"实际: {abnormal}")


def test_park_time_anomaly_missing_entry():
    # 找不到入场时间 → 无法比较 → 标记 0
    path = write_log("t_park_no_entry.log", [
        "05:40:48 - 停车时间:4天,剩余:1418分钟",
        "05:40:48 - 无商户优惠，用户需支付费用:150.00",
        "05:40:48 - 出场处理,车牌《川C30000》，开闸结果：不开闸",
    ])
    a, b, entry = cug.parse_log(path)
    abnormal = cug.find_anomalies(a, b, 300, entry)
    check("无入场时间 → 异常标记 0",
          len(abnormal) == 1 and abnormal[0]["entry_time"] == cug.ENTRY_MARK_MISSING
          and abnormal[0]["anomaly"] == 0,
          f"实际: {abnormal}")


def test_park_time_anomaly_small_deviation():
    # 系统"停车时间"比实际时长多出不足偏差阈值(如取整进位) → 不算明显异常，标记 0
    path = write_log("t_park_small_dev.log", [
        "11:01:25 - 相机序列号：192.168.31.131,车牌号：川GF2S38,内外场：外场,方向：入口",
        "12:40:53 - 停车时间:0天,剩余:100分钟",   # = 100 分钟，实际约 99.47 分钟，差 < 阈值
        "12:40:53 - 无商户优惠，用户需支付费用:5.00",
        "12:40:53 - 出场处理,车牌《川GF2S38》，开闸结果：不开闸",
    ])
    a, b, entry = cug.parse_log(path)
    abnormal = cug.find_anomalies(a, b, 300, entry)
    check("停车时间偏差不足阈值(取整) → 标记 0",
          len(abnormal) == 1 and abnormal[0]["anomaly"] == 0,
          f"实际: {abnormal}")


# ---------- 日志选取 ----------

def test_log_name_filter():
    # 仅"system.<YYYY-MM-DD>.log"进入分析；platform.* 与无日期日志一律排除
    cases = [
        ("system.2026-09-05.log", True),
        ("SYSTEM.2026-09-05.LOG", True),   # 大小写不敏感（Windows 文件系统不区分）
        ("platform.2026-09-05.log", False),  # 其他前缀的带日期日志
        ("gate.2026-09-05.log", False),
        ("system.log", False),             # 无日期
        ("platform.log", False),           # 无日期
        ("system.2026-9-5.log", False),    # 日期格式不完整
        ("system.2026-09-05.log.bak", False),
        ("mysystem.2026-09-05.log", False),
    ]
    for name, expected in cases:
        check(f"日志名选取：{name} → {'进入' if expected else '排除'}",
              cug.is_analyzed_log_name(name) == expected,
              f"期望 {expected}，实际 {cug.is_analyzed_log_name(name)}")


def test_collect_log_files_directory_filter():
    # 目录扫描：只收集 system.<YYYY-MM-DD>.log，其余文件不进入分析逻辑
    tmp = tempfile.mkdtemp(prefix="parkcheck_collect_")
    names = [
        "system.2026-09-05.log",
        "system.2026-09-06.log",
        "platform.2026-09-05.log",   # 应排除
        "system.log",                # 应排除（无日期）
        "platform.log",              # 应排除（无日期）
        "readme.txt",                # 应排除（非 .log）
    ]
    for name in names:
        with open(os.path.join(tmp, name), "w", encoding="utf-8") as f:
            f.write("10:00:00 - 出场处理,车牌《川A12345》，开闸结果：不开闸\n")
    files = cug.collect_log_files(tmp)
    got = sorted(os.path.basename(p) for p in files)
    check("目录扫描只收集 system.<日期>.log",
          got == ["system.2026-09-05.log", "system.2026-09-06.log"],
          f"实际: {got}")

    # 目录内没有任何符合命名的日志 → 报错退出
    tmp_empty = tempfile.mkdtemp(prefix="parkcheck_collect_empty_")
    with open(os.path.join(tmp_empty, "platform.log"), "w", encoding="utf-8") as f:
        f.write("placeholder\n")
    try:
        cug.collect_log_files(tmp_empty)
        check("无可分析日志时退出", False, "未触发 SystemExit")
    except SystemExit:
        check("无可分析日志时退出", True)


def test_collect_log_files_explicit_file():
    # 显式指定的单个文件不做命名过滤，由使用者自行决定（便于人工核查个别文件）
    path = write_log("platform.2026-09-05.log", ["placeholder"])
    files = cug.collect_log_files(path)
    check("显式指定文件不经过滤", files == [path], f"实际: {files}")


def test_collect_log_files_uppercase_extension():
    # 回归：.LOG 大写扩展名不应被排除（is_analyzed_log_name 大小写不敏感，
    # Windows 文件系统本身不区分大小写；旧实现的 endswith(".log") 会误排除）
    tmp = tempfile.mkdtemp(prefix="parkcheck_collect_upper_")
    try:
        for name in ("system.2026-09-07.LOG", "system.2026-09-08.Log"):
            with open(os.path.join(tmp, name), "w", encoding="utf-8") as f:
                f.write("10:00:00 - 出场处理,车牌《川A12345》，开闸结果：不开闸\n")
        files = cug.collect_log_files(tmp)
        got = sorted(os.path.basename(p) for p in files)
        check("大写 .LOG 扩展名正常收集",
              got == ["system.2026-09-07.LOG", "system.2026-09-08.Log"], f"实际: {got}")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_cli_parser_params():
    # 所有判定参数均有命令行默认值且可覆盖（与 config.py 默认值一致）
    args = build_parser().parse_args([])
    check("CLI 默认：判定窗口 300", args.window == 300, str(args.window))
    check("CLI 默认：最小偏差 5", args.min_deviation == 5, str(args.min_deviation))
    check("CLI 默认：入场去重 5", args.entry_dedup == 5, str(args.entry_dedup))
    args2 = build_parser().parse_args(
        ["-w", "60", "--min-deviation", "2", "--entry-dedup", "3"])
    check("CLI 覆盖：判定窗口 60", args2.window == 60)
    check("CLI 覆盖：最小偏差 2", args2.min_deviation == 2)
    check("CLI 覆盖：入场去重 3", args2.entry_dedup == 3)


def test_run_detection_round_isolates_bad_file():
    # 单个日志处理失败（如文件被占用/权限异常）只跳过该文件，不中断本轮；
    # 失败文件不标记已处理，正常文件照常标记，下轮自动重试失败文件
    import shutil
    import tempfile
    tmp = tempfile.mkdtemp(prefix="parkcheck_round_")
    try:
        good = os.path.join(tmp, "system.2026-09-10.log")
        with open(good, "w", encoding="utf-8") as f:
            f.write("10:00:00 - 无商户优惠，用户需支付费用:5.00\n"
                    "10:00:00 - 出场处理,车牌《川A60001》，开闸结果：不开闸\n")
        bad = os.path.join(tmp, "system.2026-09-11.log")
        os.makedirs(bad)   # 用目录伪装日志文件，解析时必然抛 OSError
        out_dir = os.path.join(tmp, "out")
        state_path = os.path.join(out_dir, ".processed.json")

        processed, skipped, errors, failed = run_detection_round(
            [good, bad], out_dir, 300, skip_processed=True,
            upload_cfg={"enabled": False})

        check("坏文件不中断本轮：正常文件已处理", processed == 1, str(processed))
        check("坏文件计入错误数", errors == 1, str(errors))
        check("无跳过与上传失败", skipped == 0 and failed == 0)
        state = cug.ProcessedState(state_path)
        check("正常文件已标记已处理", state.is_processed(good))
        check("坏文件未标记已处理（下轮重试）", not state.is_processed(bad))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- 运行 ----------

def main():
    print("开始执行测试用例...\n")
    for fn in [
        test_normal_within_window,
        test_abnormal_no_pay,
        test_abnormal_no_fee_excluded,
        test_boundary_t300,
        test_boundary_t301,
        test_pay_before_event,
        test_continuation_line_time,
        test_normalize_case_space,
        test_invalid_time_ignored,
        test_missing_plate_ignored,
        test_pay_json_fallback_regex,
        test_multiple_events_same_car,
        test_unrelated_pay_plate,
        test_empty_log,
        test_fee_linked_to_event,
        test_fee_linked_only_within_own_burst,
        test_fee_linked_after_exit_scan,
        test_fee_park_time_not_leaked_across_cars,
        test_park_time_not_leaked_across_cars,
        test_fee_on_same_timestamp_multiple_events,
        test_output_csv,
        test_entry_time_found,
        test_entry_time_missing,
        test_entry_takes_latest_scan,
        test_entry_takes_recent_event,
        test_exit_retry_shares_entry,
        test_entry_matches_recent_parking,
        test_entry_not_from_prior_parking,
        test_park_time_anomaly_positive,
        test_park_time_anomaly_normal,
        test_park_time_anomaly_missing_entry,
        test_park_time_anomaly_small_deviation,
        test_log_name_filter,
        test_collect_log_files_directory_filter,
        test_collect_log_files_explicit_file,
        test_collect_log_files_uppercase_extension,
        test_cli_parser_params,
        test_run_detection_round_isolates_bad_file,
    ]:
        fn()
    print(f"\n结果：共 {PASS + FAIL} 条，通过 {PASS}，失败 {FAIL}")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()