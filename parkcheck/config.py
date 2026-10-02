# -*- coding: utf-8 -*-
"""
配置模块：集中管理检测流程所需的关键词、判定参数与默认输出路径。

所有判定参数均可通过命令行调节（见 cli.main），此处仅存放默认值，
未硬编码在解析/判定逻辑内部。

数据库上传相关配置支持三级来源，优先级从高到低：
  显式命令行参数 > 环境变量（含项目根目录 .env 文件，见 env.py）> 本文件默认值。
  .env 已加入 .gitignore，用于本地持久化 API Token 等涉密信息，不入仓库。
"""

import os

from .env import load_project_env

# 启动时加载项目根目录下的 .env（可选：文件不存在则忽略，已存在的环境变量不被覆盖）
load_project_env()


def _env_bool(key, default=False):
    """从环境变量读取布尔值（1/true/yes/on/y 不分大小写均视为真），缺省返回默认值。"""
    value = os.environ.get(key)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on", "y")


def _env_int(key, default):
    """从环境变量读取整数，非法或缺省返回默认值。"""
    try:
        return int(os.environ[key])
    except (KeyError, TypeError, ValueError):
        return default

# ---------------- 业务关键词 ----------------
KEY_OUT_PROCESS = "出场处理"          # 出场处理标识
KEY_NO_GATE = "开闸结果：不开闸"       # 不开闸标识
KEY_PAY_DOWN = "==>支付结果下发："     # 支付结果下发标识
KEY_FEE = "用户需支付费用"             # 用户需支付费用标识
KEY_ENTRY = "入场车牌号"              # 入场车牌号标识（用于反查异常车辆入场时间）
KEY_PARK_TIME = "停车时间"            # 停车时间标识（出场相机扫描后出现，用于可疑时长判定）

# ---------------- 判定参数（默认值，命令行可覆盖） ----------------
WINDOW_SECONDS = 300                # 判定窗口：出场后多少秒内算正常
MIN_PARK_TIME_DEVIATION = 5         # 最小偏差阈值(分钟)：仅当系统"停车时间"比"入场→出场"
                                    # 实际时长至少多出该分钟数，才判定为异常，避免取整误报
ENTRY_DEDUP_WINDOW = 5              # 重复"方向：入口"扫描归并为同一次入场的窗口(秒)：
                                    # 同一辆车两次入口扫描间隔不超过该秒数视为一次入场，取第一次
                                    # 说明：费用/停车时间按"爆点"关联——紧跟本次"方向：出口"
                                    # 扫描出现，解析到新出口扫描即重置（见 parser.py），
                                    # 无需额外关联窗口参数

# ---------------- 输出相关 ----------------
ENTRY_MARK_MISSING = "-"            # 未找到入场时间时的标记
DEFAULT_OUT_DIR = "output"          # 默认输出目录（不放入 document，且已加入 .gitignore）
PROCESSED_STATE_FILE = ".processed.json"  # 已处理日志状态文件名（存放在输出目录下）

# ---------------- 数据库上传（Cloudflare D1） ----------------
# 仅上传异常记录：INSERT OR IGNORE + 唯一去重键，只插入新记录，永不覆盖。
# 以下默认值均可被环境变量（或 .env）覆盖：UPLOAD_ENABLED / CF_ACCOUNT_ID /
# CF_DATABASE_ID / CF_API_TOKEN / DB_BATCH_SIZE
UPLOAD_ENABLED = _env_bool("UPLOAD_ENABLED", False)   # 是否启用上传（CLI --upload / GUI 勾选可开启）
CF_ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID", "")   # Cloudflare Account ID
CF_DATABASE_ID = os.environ.get("CF_DATABASE_ID", "") # D1 Database UUID
CF_API_TOKEN = os.environ.get("CF_API_TOKEN", "")     # Cloudflare API Token（需 D1:Edit 权限，
                                                      # 建议放 .env，绝不随配置导出）
DB_BATCH_SIZE = _env_int("DB_BATCH_SIZE", 50)         # 单次请求最多合并的记录数