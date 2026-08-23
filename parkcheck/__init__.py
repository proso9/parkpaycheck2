# -*- coding: utf-8 -*-
"""
parkcheck 包：停车场系统日志异常车辆检测。

模块划分：
  config   常量与默认参数
  parser   日志解析
  detector 异常判定与入场反查
  output   结果输出
  cli      命令行入口

对外统一导出公共 API，便于外部（含测试）通过 `import parkcheck` 直接使用。
"""

from .config import (
    WINDOW_SECONDS,
    FEE_LINK_WINDOW,
    ENTRY_MARK_MISSING,
    DEFAULT_OUT_DIR,
)
from .parser import parse_log
from .detector import find_anomalies
from .output import output_results
from .cli import main

__all__ = [
    "WINDOW_SECONDS",
    "FEE_LINK_WINDOW",
    "ENTRY_MARK_MISSING",
    "DEFAULT_OUT_DIR",
    "parse_log",
    "find_anomalies",
    "output_results",
    "main",
]