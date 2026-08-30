# -*- coding: utf-8 -*-
"""
数据库上传模块：把异常检测结果上传到 Cloudflare D1（REST API，仅标准库）。

设计要点：
  - 直接调用 D1 REST API（POST /accounts/{id}/d1/database/{id}/query），
    不部署 Worker、不引入第三方依赖（urllib.request 实现）。
  - 只插入新记录，永不覆盖：INSERT OR IGNORE + 唯一去重键 dedup_key，
    已存在记录（含展示项目维护的 status/remark）完全不动。
  - 表在每次上传会话开始时用 CREATE TABLE IF NOT EXISTS 幂等创建。
  - HTTP 层通过 opener 参数注入，便于单元测试 mock（不依赖真实网络）。
"""

import hashlib
import json
import os
import urllib.error
import urllib.request

from .config import DB_BATCH_SIZE, ENTRY_MARK_MISSING
from .output import LOG_DATE_RE

API_BASE = "https://api.cloudflare.com/client/v4"
REQUEST_TIMEOUT = 30  # 单次请求超时（秒）

# 建表语句（幂等）：status/remark 由展示项目维护，本工具只写默认值
CREATE_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS anomalies ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " log_date      TEXT NOT NULL,"
    " log_file      TEXT NOT NULL,"
    " entry_time    TEXT,"
    " exit_time     TEXT NOT NULL,"
    " car_number    TEXT NOT NULL,"
    " fee           REAL,"
    " is_suspicious INTEGER NOT NULL DEFAULT 0,"
    " dedup_key     TEXT NOT NULL UNIQUE,"
    " status        INTEGER NOT NULL DEFAULT 0,"
    " remark        TEXT,"
    " created_at    TEXT NOT NULL DEFAULT (datetime('now'))"
    ")"
)

# 单行占位组：8 个参数 + status 字面量 0（本工具永远只写默认值）
INSERT_ROW_PLACEHOLDER = "(?, ?, ?, ?, ?, ?, ?, ?, 0)"
INSERT_SQL_TEMPLATE = (
    "INSERT OR IGNORE INTO anomalies"
    " (log_date, log_file, entry_time, exit_time, car_number,"
    "  fee, is_suspicious, dedup_key, status)"
    " VALUES {values}"
)


class D1UploadError(Exception):
    """上传失败异常：异常信息为面向用户的可读描述。"""


def make_dedup_key(log_file, exit_time, car_number):
    """
    生成去重键：sha256("{log_file}|{exit_time}|{car_number}") 的十六进制摘要。

    同一辆车多次不开闸因 exit_time 不同而键不同；
    重复上传同一日志时键相同，被 SQLite 唯一约束静默忽略，天然幂等。
    """
    raw = f"{log_file}|{exit_time}|{car_number}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def extract_log_date(log_path):
    """从日志文件名提取日期（形如 system.2026-08-22.log → 2026-08-22）；取不到返回 unknown-date。"""
    base = os.path.splitext(os.path.basename(log_path))[0]
    m = LOG_DATE_RE.search(base)
    return m.group(1) if m else "unknown-date"


def build_upload_record(item, log_path):
    """
    把一条异常记录（find_anomalies 的输出项）组装为待上传的字典。

    - 日志内时间只有 HH:MM:SS，须用文件名中的日期补全为全格式；
    - entry_time 为 "-" 时存 None；fee 为 "-"/缺失时存 None，金额转 float；
    - car_number 已在解析层归一化（去空格、转大写）。
    """
    log_file = os.path.basename(log_path)
    log_date = extract_log_date(log_path)
    exit_time = f"{log_date} {item['time']}"

    entry = item.get("entry_time")
    entry_time = f"{log_date} {entry}" if entry and entry != ENTRY_MARK_MISSING else None

    fee = item.get("fee")
    if fee in (None, "", "-"):
        fee = None
    else:
        try:
            fee = float(fee)
        except (TypeError, ValueError):
            fee = None

    car = item["car"]
    return {
        "log_date": log_date,
        "log_file": log_file,
        "entry_time": entry_time,
        "exit_time": exit_time,
        "car_number": car,
        "fee": fee,
        "is_suspicious": int(item.get("anomaly", 0)),
        "dedup_key": make_dedup_key(log_file, exit_time, car),
    }


def build_upload_records(abnormal, log_path):
    """把一批异常记录组装为待上传记录列表（保持检测输出顺序）。"""
    return [build_upload_record(item, log_path) for item in abnormal]


def build_insert_sql(row_count):
    """构造合并 row_count 行的 INSERT OR IGNORE 语句（每行 8 个占位符）。"""
    values = ", ".join([INSERT_ROW_PLACEHOLDER] * row_count)
    return INSERT_SQL_TEMPLATE.format(values=values)


def record_to_params(record):
    """把上传记录转为 SQL 参数列表（顺序与 INSERT 列定义严格一致）。"""
    return [
        record["log_date"],
        record["log_file"],
        record["entry_time"],
        record["exit_time"],
        record["car_number"],
        record["fee"],
        record["is_suspicious"],
        record["dedup_key"],
    ]


def is_upload_configured(cfg):
    """上传配置是否完整可用：开关开启且账户 ID / 数据库 ID / Token 三者齐全。"""
    if not cfg or not cfg.get("enabled"):
        return False
    return all(str(cfg.get(key, "")).strip() for key in ("cf_account", "cf_database", "cf_token"))


def d1_query(account_id, database_id, token, sql, params=None,
             opener=urllib.request.urlopen, timeout=REQUEST_TIMEOUT):
    """
    调用 D1 REST API 执行一条 SQL（参数化，防注入）。

    HTTP 200 且 JSON success == true 视为成功并返回整个响应 JSON；
    否则抛出 D1UploadError，按 Token 无效 / 数据库不存在 / 网络失败给出可读提示。
    opener 仅供测试注入 mock。
    """
    url = f"{API_BASE}/accounts/{account_id}/d1/database/{database_id}/query"
    body = json.dumps({"sql": sql, "params": params or []}).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })
    try:
        with opener(request, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # 尝试读取响应体中的错误详情，辅助定位问题
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "ignore")
        except Exception:
            pass
        if exc.code in (401, 403):
            raise D1UploadError(
                f"认证失败（HTTP {exc.code}）：API Token 无效或缺少 D1:Edit 权限。{detail}"
            ) from exc
        raise D1UploadError(
            f"D1 API 请求失败（HTTP {exc.code}）：{detail or exc.reason}"
        ) from exc
    except urllib.error.URLError as exc:
        raise D1UploadError(f"网络错误，无法连接 Cloudflare API：{exc.reason}") from exc
    except OSError as exc:
        raise D1UploadError(f"网络错误，无法连接 Cloudflare API：{exc}") from exc

    if not isinstance(payload, dict) or payload.get("success") is not True:
        errors = payload.get("errors") if isinstance(payload, dict) else payload
        raise D1UploadError(f"D1 API 返回失败：{errors}")
    return payload


def _sum_changes(payload):
    """从 D1 响应中提取实际插入行数（位于 result[0].meta.changes），取不到返回 0。"""
    try:
        meta = payload["result"][0].get("meta") or {}
        return int(meta.get("changes", 0))
    except (KeyError, IndexError, TypeError, ValueError, AttributeError):
        return 0


def upload_records(records, cfg, opener=urllib.request.urlopen, timeout=REQUEST_TIMEOUT):
    """
    上传记录列表：先幂等建表，再按批量大小合并为多行 INSERT OR IGNORE。

    返回实际新插入的行数（重复记录被唯一约束忽略，不计入 changes）。
    任一请求失败即抛 D1UploadError，调用方应视为整批失败。
    """
    if not is_upload_configured(cfg):
        raise D1UploadError("上传配置不完整：需提供 Cloudflare 账户 ID、数据库 ID 与 API Token。")
    account = str(cfg["cf_account"]).strip()
    database = str(cfg["cf_database"]).strip()
    token = str(cfg["cf_token"]).strip()
    batch_size = max(1, int(cfg.get("db_batch") or DB_BATCH_SIZE))

    # 每次上传会话先发一次建表请求（幂等）
    d1_query(account, database, token, CREATE_TABLE_SQL, opener=opener, timeout=timeout)

    inserted = 0
    for start in range(0, len(records), batch_size):
        chunk = records[start:start + batch_size]
        if not chunk:
            continue
        payload = d1_query(
            account, database, token,
            build_insert_sql(len(chunk)),
            # D1 要求绑定参数为展平列表：多行 VALUES 的参数按行顺序拼接
            [param for record in chunk for param in record_to_params(record)],
            opener=opener,
            timeout=timeout,
        )
        inserted += _sum_changes(payload)
    return inserted
