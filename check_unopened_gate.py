# -*- coding: utf-8 -*-
"""
入口脚本：停车场系统日志异常车辆检测。

实际逻辑已模块化到 parkcheck 包中，本脚本仅为方便命令行调用而保留的薄入口：

    python check_unopened_gate.py [日志文件或目录] [-w 秒] [-o 输出目录]

等价于执行 parkcheck.cli.main()。
"""

import os
import sys

# 确保从项目根目录运行、或通过 `python -m` 运行均可找到 parkcheck 包
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from parkcheck.cli import main  # noqa: E402

if __name__ == "__main__":
    main()