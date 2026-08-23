# -*- coding: utf-8 -*-
"""
命令行入口模块：解析命令行参数、收集日志文件并调度处理流程。

统一编排：parse_log → find_anomalies → output_results。
"""

import argparse
import os
import sys

from .config import WINDOW_SECONDS, DEFAULT_OUT_DIR
from .parser import parse_log
from .detector import find_anomalies
from .output import output_results


def build_parser():
    """构造命令行解析器，所有判定参数均可在此指定。"""
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
    return parser


def collect_log_files(path):
    """收集待处理的日志文件列表：目录则取其中所有 .log，文件则单列，否则报错退出。"""
    log_files = []
    if os.path.isdir(path):
        for name in sorted(os.listdir(path)):
            if name.endswith(".log"):
                log_files.append(os.path.join(path, name))
        if not log_files:
            print(f"目录 {path} 下未找到 .log 文件")
            sys.exit(1)
    elif os.path.isfile(path):
        log_files = [path]
    else:
        print(f"路径不存在：{path}")
        sys.exit(1)
    return log_files


def main():
    args = build_parser().parse_args()

    # 逐个处理日志文件
    for path in collect_log_files(args.log):
        record_a, record_b, record_entry = parse_log(path)
        abnormal = find_anomalies(record_a, record_b, args.window, record_entry)
        output_results(abnormal, path, args.out)


if __name__ == "__main__":
    main()