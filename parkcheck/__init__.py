# -*- coding: utf-8 -*-
"""
parkcheck 包：停车场系统日志异常车辆检测。

模块划分：
  config    常量与默认参数
  parser    日志解析
  detector  异常判定与入场反查
  output    结果输出
  state     已处理文件状态（跳过未变化的已处理日志）
  scheduler 定时任务（APScheduler 封装）
  cli       命令行入口

对外统一导出公共 API，便于外部（含测试）通过 `import parkcheck` 直接使用。
"""

from .config import (
    WINDOW_SECONDS,
    ENTRY_MARK_MISSING,
    DEFAULT_OUT_DIR,
    PROCESSED_STATE_FILE,
    is_analyzed_log_name,
)
from .parser import parse_log
from .detector import find_anomalies
from .output import output_results
from .db import (
    D1UploadError,
    make_dedup_key,
    build_upload_records,
    upload_records,
    is_upload_configured,
)
from .state import ProcessedState
from .scheduler import SchedulerManager
from .cli import main, collect_log_files, list_log_files, run_detection_round

__all__ = [
    "WINDOW_SECONDS",
    "ENTRY_MARK_MISSING",
    "DEFAULT_OUT_DIR",
    "PROCESSED_STATE_FILE",
    "is_analyzed_log_name",
    "parse_log",
    "find_anomalies",
    "output_results",
    "D1UploadError",
    "make_dedup_key",
    "build_upload_records",
    "upload_records",
    "is_upload_configured",
    "ProcessedState",
    "SchedulerManager",
    "main",
    "collect_log_files",
    "list_log_files",
    "run_detection_round",
]