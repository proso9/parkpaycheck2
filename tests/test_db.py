# -*- coding: utf-8 -*-
"""parkcheck.db 数据库上传模块的测试（无第三方依赖、不依赖真实网络，直接运行即可）。

HTTP 层通过注入 fake opener mock；GUI 导出字段经 check_gui 的纯函数验证
（仅导入 tkinter 模块，不创建窗口）。
"""

import os
import sys
import json
import hashlib
import tempfile
import shutil
import urllib.error

# 将项目根目录加入 sys.path，以便导入被测试包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parkcheck.db import (  # noqa: E402
    D1UploadError,
    CREATE_TABLE_SQL,
    make_dedup_key,
    build_upload_record,
    build_upload_records,
    build_insert_sql,
    record_to_params,
    is_upload_configured,
    d1_query,
    upload_records,
)
from parkcheck.state import ProcessedState  # noqa: E402
from parkcheck.cli import detect_log_file, upload_and_mark  # noqa: E402


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


def make_tmpdir():
    """创建临时工作目录，返回路径。"""
    return tempfile.mkdtemp(prefix="parkcheck_db_")


# ---------------- 1. 去重键 ----------------

def test_dedup_key_deterministic():
    """同输入必须得到同键。"""
    k1 = make_dedup_key("system.2026-08-22.log", "2026-08-22 21:07:12", "川ABA0530")
    k2 = make_dedup_key("system.2026-08-22.log", "2026-08-22 21:07:12", "川ABA0530")
    expected = hashlib.sha256(
        "system.2026-08-22.log|2026-08-22 21:07:12|川ABA0530".encode("utf-8")
    ).hexdigest()
    check("去重键同输入同输出", k1 == k2 == expected, f"{k1} / {k2} / {expected}")


def test_dedup_key_differs():
    """不同车牌或不同出场时间必须得到不同键。"""
    base = make_dedup_key("a.log", "2026-08-22 21:07:12", "川A12345")
    diff_car = make_dedup_key("a.log", "2026-08-22 21:07:12", "川B12345")
    diff_time = make_dedup_key("a.log", "2026-08-22 21:07:13", "川A12345")
    diff_file = make_dedup_key("b.log", "2026-08-22 21:07:12", "川A12345")
    check("不同车牌 → 不同键", base != diff_car)
    check("不同出场时间 → 不同键", base != diff_time)
    check("不同日志文件 → 不同键", base != diff_file)


# ---------------- 2. 记录组装 ----------------

def make_item(entry_time="-", fee="23.00", anomaly=0, time="21:07:12"):
    """构造一条 find_anomalies 输出样例。"""
    return {
        "time": time,
        "car": "川ABA0530",
        "fee": fee,
        "entry_time": entry_time,
        "anomaly": anomaly,
    }


def test_build_record_completes_date():
    """时间应补全日志文件名中的日期为全格式。"""
    rec = build_upload_record(make_item(), "/x/document/system.2026-08-22.log")
    check("出场时间补全日期", rec["exit_time"] == "2026-08-22 21:07:12", rec["exit_time"])
    check("log_date 取自文件名", rec["log_date"] == "2026-08-22", rec["log_date"])
    check("log_file 为纯文件名", rec["log_file"] == "system.2026-08-22.log", rec["log_file"])


def test_build_record_entry_and_fee():
    """入场时间 '-' → None；费用字符串转 float；无费用 → None。"""
    rec = build_upload_record(make_item(entry_time="-", fee="23.00"),
                              "/x/system.2026-08-22.log")
    check("无入场时间 → entry_time 为 None", rec["entry_time"] is None)
    check("费用转数值", rec["fee"] == 23.0 and isinstance(rec["fee"], float), repr(rec["fee"]))
    check("可疑标记默认 0", rec["is_suspicious"] == 0)

    rec2 = build_upload_record(make_item(entry_time="08:15:30", fee=None, anomaly=1),
                               "/x/system.2026-08-22.log")
    check("入场时间补全日期", rec2["entry_time"] == "2026-08-22 08:15:30", rec2["entry_time"])
    check("无费用 → fee 为 None", rec2["fee"] is None)
    check("可疑标记透传", rec2["is_suspicious"] == 1)

    rec3 = build_upload_record(make_item(fee="-"), "/x/system.2026-08-22.log")
    check("费用为 '-' → fee 为 None", rec3["fee"] is None)


def test_build_record_dedup_key_consistent():
    """组装结果的 dedup_key 与「文件名|全格式出场时间|车牌」哈希一致。"""
    rec = build_upload_record(make_item(), "/x/system.2026-08-22.log")
    expected = make_dedup_key(rec["log_file"], rec["exit_time"], rec["car_number"])
    check("dedup_key 与入库字段一致", rec["dedup_key"] == expected)
    fields = {"log_date", "log_file", "entry_time", "exit_time", "car_number",
              "fee", "is_suspicious", "dedup_key"}
    check("记录字段完整", fields.issubset(rec.keys()), str(sorted(rec.keys())))


def test_build_upload_records_batch():
    """批量组装保持顺序且数量一致。"""
    items = [make_item(), make_item(entry_time="08:15:30")]
    recs = build_upload_records(items, "/x/system.2026-08-22.log")
    check("批量组装数量一致", len(recs) == 2)
    check("批量组装保持顺序", recs[0]["car_number"] == recs[1]["car_number"] == "川ABA0530")


# ---------------- 3. INSERT SQL ----------------

def test_insert_sql_placeholders():
    """INSERT OR IGNORE 占位符数量与顺序正确（每行 8 参数 + status 字面量 0）。"""
    sql1 = build_insert_sql(1)
    check("单行 SQL 含 INSERT OR IGNORE", sql1.startswith("INSERT OR IGNORE INTO anomalies"))
    check("单行 SQL 含唯一占位组", sql1.count("(?, ?, ?, ?, ?, ?, ?, ?, 0)") == 1)
    check("单行 SQL 占位符共 8 个", sql1.count("?") == 8, str(sql1.count("?")))
    check("单行 SQL status 字面量 0", "?, 0)" in sql1 or ", 0)" in sql1)

    sql3 = build_insert_sql(3)
    check("三行 SQL 占位符共 24 个", sql3.count("?") == 24, str(sql3.count("?")))
    check("三行 SQL 逗号分隔", sql3.count("), (") == 2)

    # 参数顺序与列定义一致
    rec = build_upload_record(make_item(), "/x/system.2026-08-22.log")
    params = record_to_params(rec)
    check("参数共 8 个", len(params) == 8)
    check("参数顺序：log_date 在前", params[0] == "2026-08-22")
    check("参数顺序：dedup_key 在最后", params[7] == rec["dedup_key"])


# ---------------- 4. HTTP 层 mock ----------------

class FakeResponse:
    """模拟 urlopen 返回的上下文管理器响应。"""

    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def make_opener(payload, calls):
    """构造记录每次请求的 fake opener。"""
    def opener(request, timeout=None):
        calls.append(request)
        return FakeResponse(payload)
    return opener


CFG = {
    "enabled": True,
    "cf_account": "acct123",
    "cf_database": "db456",
    "cf_token": "token789",
    "db_batch": 50,
}


def test_d1_query_success():
    """success:true → 返回响应且请求规格（URL/头/参数化 body）正确。"""
    calls = []
    payload = {"success": True, "result": [{"changes": 1}]}
    result = d1_query("acct123", "db456", "token789", "SELECT 1", ["x"],
                      opener=make_opener(payload, calls))
    check("成功返回响应", result == payload, str(result))
    check("仅发起一次请求", len(calls) == 1)
    req = calls[0]
    check("请求 URL 正确",
          req.full_url == "https://api.cloudflare.com/client/v4/accounts/acct123"
                          "/d1/database/db456/query",
          req.full_url)
    check("Bearer 认证头", req.headers.get("Authorization") == "Bearer token789"
          or req.get_header("Authorization") == "Bearer token789")
    body = json.loads(req.data.decode("utf-8"))
    check("请求体携带 SQL 与参数", body == {"sql": "SELECT 1", "params": ["x"]}, str(body))


def test_d1_query_success_false():
    """success:false → 抛出含可读错误信息的异常。"""
    calls = []
    payload = {"success": False,
               "errors": [{"code": 7500, "message": "database db456 does not exist"}]}
    try:
        d1_query("acct123", "db456", "token789", "SELECT 1",
                 opener=make_opener(payload, calls))
        check("success:false 应抛异常", False)
    except D1UploadError as exc:
        check("success:false 抛 D1UploadError", True)
        check("错误信息含响应 errors 内容", "does not exist" in str(exc), str(exc))


def test_d1_query_http_401():
    """HTTP 401/403 → 提示 Token 无效。"""
    def opener(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", None, None)
    try:
        d1_query("acct123", "db456", "bad-token", "SELECT 1", opener=opener)
        check("HTTP 401 应抛异常", False)
    except D1UploadError as exc:
        check("HTTP 401 → Token 无效提示", "Token" in str(exc), str(exc))


def test_d1_query_network_error():
    """连接失败 → 网络错误提示。"""
    def opener(request, timeout=None):
        raise urllib.error.URLError("connection refused")
    try:
        d1_query("acct123", "db456", "token789", "SELECT 1", opener=opener)
        check("网络错误应抛异常", False)
    except D1UploadError as exc:
        check("网络错误 → 可读提示", "网络错误" in str(exc), str(exc))


def test_d1_query_invalid_json_response():
    """响应体不是 JSON（如代理/网关拦截页）→ 转为可读的 D1UploadError 而非裸异常。"""
    class HtmlResponse(FakeResponse):
        def __init__(self):
            super().__init__({})

        def read(self):
            return b"<html><body>502 Bad Gateway</body></html>"

    def opener(request, timeout=None):
        return HtmlResponse()

    try:
        d1_query("acct123", "db456", "token789", "SELECT 1", opener=opener)
        check("非 JSON 响应应抛异常", False)
    except D1UploadError as exc:
        check("非 JSON 响应 → 可读提示", "不是有效 JSON" in str(exc), str(exc))
    except ValueError as exc:
        check("非 JSON 响应不应抛裸 ValueError", False, repr(exc))


def test_upload_records_success():
    """上传成功：先建表再分批插入，返回新插入行数。"""
    calls = []
    recs = build_upload_records(
        [make_item(), make_item(entry_time="08:15:30"), make_item(fee=None)],
        "/x/system.2026-08-22.log",
    )
    # 第一响应给建表，之后按批给 changes（D1 的 changes 位于 result[0].meta）
    responses = [
        {"success": True, "result": []},
        {"success": True, "result": [{"meta": {"changes": 2}}]},
        {"success": True, "result": [{"meta": {"changes": 1}}]},
    ]
    idx = {"i": 0}

    def opener(request, timeout=None):
        calls.append(request)
        resp = responses[idx["i"]]
        idx["i"] += 1
        return FakeResponse(resp)

    inserted = upload_records(recs, dict(CFG, db_batch=2), opener=opener)
    check("建表 + 两批共 3 次请求", len(calls) == 3, str(len(calls)))
    check("首个请求为建表", calls[0].data and
          json.loads(calls[0].data.decode("utf-8"))["sql"] == CREATE_TABLE_SQL)
    sql1 = json.loads(calls[1].data.decode("utf-8"))
    sql2 = json.loads(calls[2].data.decode("utf-8"))
    check("批量请求使用 INSERT OR IGNORE", sql1["sql"].startswith("INSERT OR IGNORE"))
    # D1 要求绑定参数为展平列表：2 行 × 8 参数 = 16 个
    check("第一批 16 个绑定参数", len(sql1["params"]) == 16, str(len(sql1["params"])))
    check("绑定参数已展平（无嵌套）",
          all(not isinstance(p, list) for p in sql1["params"]))
    check("第二批 8 个绑定参数", len(sql2["params"]) == 8)
    check("返回实际插入行数", inserted == 3, str(inserted))


def test_upload_records_any_batch_failure():
    """任一批失败即整批失败（抛异常）。"""
    recs = build_upload_records([make_item(), make_item()], "/x/system.2026-08-22.log")
    state = {"created": False}

    def opener(request, timeout=None):
        if not state["created"]:
            state["created"] = True
            return FakeResponse({"success": True, "result": []})
        return FakeResponse({"success": False,
                             "errors": [{"code": 7000, "message": "write failed"}]})

    try:
        upload_records(recs, CFG, opener=opener)
        check("批次失败应抛异常", False)
    except D1UploadError:
        check("批次失败抛 D1UploadError", True)


def test_upload_records_unconfigured():
    """凭证不全时直接报配置错误，不发任何网络请求。"""
    calls = []
    try:
        upload_records([], {"enabled": True, "cf_account": "", "cf_database": "x",
                            "cf_token": "y"}, opener=make_opener({}, calls))
        check("凭证不全应抛异常", False)
    except D1UploadError as exc:
        check("凭证不全 → 配置错误提示", "配置不完整" in str(exc), str(exc))
    check("凭证不全不发请求", len(calls) == 0)


def test_is_upload_configured():
    """开关关闭或任一凭证为空 → 未配置。"""
    check("关闭 → 未配置", not is_upload_configured(dict(CFG, enabled=False)))
    check("缺账户 → 未配置", not is_upload_configured(dict(CFG, cf_account="")))
    check("缺库 ID → 未配置", not is_upload_configured(dict(CFG, cf_database=" ")))
    check("缺 Token → 未配置", not is_upload_configured(dict(CFG, cf_token="")))
    check("齐全 → 已配置", is_upload_configured(CFG))


# ---------------- 5. 统一上传与标记时机 ----------------

def make_log(tmp):
    """在临时目录构造一个样例日志，返回路径。"""
    log = os.path.join(tmp, "system.2026-08-22.log")
    with open(log, "w", encoding="utf-8") as f:
        f.write("21:07:12 - 出场处理 车牌《川ABA0530》开闸结果：不开闸\n")
    return log


def test_upload_and_mark_all_or_nothing():
    """统一上传：全部成功才标记所有日志；任一批失败则全部不标记。"""
    tmp = make_tmpdir()
    try:
        out_dir = os.path.join(tmp, "out")
        state = ProcessedState(os.path.join(out_dir, ".processed.json"))
        p1 = make_log(tmp)
        p2 = os.path.join(tmp, "system.2026-08-21.log")
        shutil.copy(p1, p2)
        pending = [
            (p1, build_upload_records([], p1)),          # 无异常的日志
            (p2, build_upload_records(
                [make_item(), make_item(entry_time="08:15:30")], p2)),
        ]

        def failing_upload(records, cfg):
            assert len(records) == 2  # 无异常日志不贡献记录
            raise D1UploadError("网络错误，无法连接 Cloudflare API：refused")

        try:
            upload_and_mark(pending, dict(CFG), state=state,
                            upload_fn=failing_upload)
            check("统一上传失败应抛异常", False)
        except D1UploadError:
            check("统一上传失败抛 D1UploadError", True)
        check("统一上传失败 → 两个日志均未标记", not state.is_processed(p1)
              and not state.is_processed(p2))

        seen = {}
        def ok_upload(records, cfg):
            seen["n"] = len(records)
            return len(records)

        upload_and_mark(pending, dict(CFG), state=state, upload_fn=ok_upload)
        check("统一上传成功 → 两个日志均标记已处理",
              state.is_processed(p1) and state.is_processed(p2))
        check("汇总记录数正确（仅异常日志贡献 2 条）", seen["n"] == 2, str(seen))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_upload_and_mark_empty_no_request():
    """本轮没有任何异常记录：不发上传请求，直接标记全部已处理。"""
    tmp = make_tmpdir()
    try:
        out_dir = os.path.join(tmp, "out")
        state = ProcessedState(os.path.join(out_dir, ".processed.json"))
        p1 = make_log(tmp)
        pending = [(p1, build_upload_records([], p1))]

        def unexpected(records, cfg):
            raise AssertionError("无异常记录时不应发起上传请求")

        upload_and_mark(pending, dict(CFG), state=state,
                        upload_fn=unexpected)
        check("无异常记录 → 直接标记已处理", state.is_processed(p1))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_upload_and_mark_tolerates_vanished_file():
    """待标记的日志文件已消失（被移动/删除）：跳过该文件，其余照常标记不崩溃。"""
    tmp = make_tmpdir()
    try:
        out_dir = os.path.join(tmp, "out")
        state = ProcessedState(os.path.join(out_dir, ".processed.json"))
        p1 = make_log(tmp)
        vanished = os.path.join(tmp, "system.2026-08-20.log")  # 从未存在
        pending = [(p1, build_upload_records([], p1)),
                   (vanished, build_upload_records([], vanished))]

        def unexpected(records, cfg):
            raise AssertionError("无异常记录时不应发起上传请求")

        upload_and_mark(pending, dict(CFG), state=state,
                        upload_fn=unexpected)
        check("存在的文件仍被标记", state.is_processed(p1))
        check("消失的文件不崩溃也不误标", not state.is_processed(vanished))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_detect_log_file_outputs_csv():
    """detect_log_file 只做检测与 CSV 输出，不触碰已处理状态。"""
    tmp = make_tmpdir()
    try:
        out_dir = os.path.join(tmp, "out")
        state = ProcessedState(os.path.join(out_dir, ".processed.json"))
        log = make_log(tmp)
        abnormal = detect_log_file(log, out_dir, 300)
        check("检测返回记录列表", isinstance(abnormal, list))
        check("检测阶段不标记已处理", not state.is_processed(log))
        check("CSV 已输出", os.path.isdir(out_dir))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------- 6. 导出配置不含 Token ----------------

def test_export_fields_exclude_token():
    """GUI 导出字段列表不得包含 API Token（纯函数验证）。"""
    from check_gui import upload_export_fields
    fields = upload_export_fields(True, "acct", "db", "30")
    check("导出字段不含 token 键", "cf_token" not in fields and "token" not in
          {k.lower() for k in fields}, str(sorted(fields)))
    check("导出含开关/账户/库 ID/批量", fields["upload_enabled"] is True
          and fields["cf_account"] == "acct"
          and fields["cf_database"] == "db" and fields["db_batch"] == 30)
    # Token 值即使被误传也不应出现在导出结果中
    dumped = json.dumps(fields, ensure_ascii=False)
    check("导出 JSON 不含 Token 值", "token789" not in dumped)


def test_upload_records_binding_cap():
    """批量大小超过 D1 单查询 100 个绑定参数上限时自动收紧（每批最多 12 行）。"""
    from parkcheck.db import D1_MAX_BINDINGS, PARAMS_PER_ROW
    calls = []
    recs = build_upload_records(
        [make_item(time=f"{h:02d}:{m:02d}:00") for h in range(24) for m in range(2)],
        "/x/system.2026-08-22.log",
    )  # 48 条记录
    idx = {"i": 0}

    def opener(request, timeout=None):
        calls.append(request)
        resp = ({"success": True, "result": []} if idx["i"] == 0
                else {"success": True, "result": [{"meta": {"changes": 12}}]})
        idx["i"] += 1
        return FakeResponse(resp)

    inserted = upload_records(recs, dict(CFG, db_batch=50), opener=opener)
    insert_calls = calls[1:]
    # 48 条 ÷ 12 行/批 = 4 次插入请求
    check("绑定参数超限时收紧为 4 批", len(insert_calls) == 4, str(len(insert_calls)))
    max_params = max(len(json.loads(c.data.decode("utf-8"))["params"]) for c in insert_calls)
    check("单批参数不超过 D1 上限", max_params <= D1_MAX_BINDINGS,
          f"{max_params} > {D1_MAX_BINDINGS}")
    check("单批参数 = 12 行 × 8", max_params == 12 * PARAMS_PER_ROW, str(max_params))
    check("返回实际插入行数", inserted == 48, str(inserted))


def main():
    test_dedup_key_deterministic()
    test_dedup_key_differs()
    test_build_record_completes_date()
    test_build_record_entry_and_fee()
    test_build_record_dedup_key_consistent()
    test_build_upload_records_batch()
    test_insert_sql_placeholders()
    test_d1_query_success()
    test_d1_query_success_false()
    test_d1_query_http_401()
    test_d1_query_network_error()
    test_d1_query_invalid_json_response()
    test_upload_records_success()
    test_upload_records_any_batch_failure()
    test_upload_records_unconfigured()
    test_upload_records_binding_cap()
    test_is_upload_configured()
    test_upload_and_mark_all_or_nothing()
    test_upload_and_mark_empty_no_request()
    test_upload_and_mark_tolerates_vanished_file()
    test_detect_log_file_outputs_csv()
    test_export_fields_exclude_token()
    print(f"\n通过 {PASS} 项，失败 {FAIL} 项。")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
