# -*- coding: utf-8 -*-
"""
命令行入口模块：解析命令行参数、收集日志文件并调度处理流程。

统一编排：parse_log → find_anomalies → output_results →（可选）统一上传 D1 → 标记已处理。

上传开启时的语义：先扫描完本轮全部日志并输出 CSV，把所有异常记录汇总后
统一分批上传；全部成功才统一标记已处理。任一批失败则本轮所有日志均不标记，
打印明确错误后结束本轮，下一轮重新检测并重试（INSERT OR IGNORE 去重键
兜底，不产生重复数据）。
"""

import argparse
import os
import sys

from .config import (
    WINDOW_SECONDS,
    DEFAULT_OUT_DIR,
    PROCESSED_STATE_FILE,
    UPLOAD_ENABLED,
    CF_ACCOUNT_ID,
    CF_DATABASE_ID,
    CF_API_TOKEN,
    DB_BATCH_SIZE,
    is_analyzed_log_name,
)
from .db import (
    D1UploadError,
    build_upload_records,
    upload_records,
    is_upload_configured,
)
from .parser import parse_log
from .detector import find_anomalies
from .output import output_results
from .state import ProcessedState


def build_parser():
    """构造命令行解析器，所有判定参数与上传配置均可在此指定。"""
    parser = argparse.ArgumentParser(
        description="停车场系统日志异常车辆检测：出场不开闸且窗口期无支付结果下发"
    )
    parser.add_argument(
        "log", nargs="?", default="document",
        help="日志文件路径，或包含日志的目录（默认指定目录，仅分析其中"
             "符合 system.<YYYY-MM-DD>.log 命名的文件，platform.* 及无日期日志不处理）"
    )
    parser.add_argument(
        "-w", "--window", type=int, default=WINDOW_SECONDS,
        help=f"判定窗口秒数，默认 {WINDOW_SECONDS}"
    )
    parser.add_argument(
        "-o", "--out", default=DEFAULT_OUT_DIR,
        help=f"CSV 导出目录，默认 {DEFAULT_OUT_DIR}（项目根目录，已加入 .gitignore）"
    )
    parser.add_argument(
        "--reprocess", action="store_true",
        help="忽略已处理记录，强制重新处理所有日志"
             "（默认跳过内容未变化的已处理日志，仍在追加写入的日志会自动重新处理）"
    )
    # ---------------- 数据库上传（Cloudflare D1） ----------------
    parser.add_argument(
        "--upload", action="store_true", default=UPLOAD_ENABLED,
        help="启用把异常记录上传到 Cloudflare D1（默认关闭；上传成功才标记日志已处理）"
    )
    parser.add_argument(
        "--cf-account", default=CF_ACCOUNT_ID,
        help="Cloudflare Account ID（上传必需）"
    )
    parser.add_argument(
        "--cf-database", default=CF_DATABASE_ID,
        help="D1 Database UUID（上传必需）"
    )
    parser.add_argument(
        "--cf-token", default=CF_API_TOKEN,
        help="Cloudflare API Token（需 D1:Edit 权限；建议通过环境变量或 GUI 传入，避免留在命令历史）"
    )
    parser.add_argument(
        "--db-batch", type=int, default=DB_BATCH_SIZE,
        help=f"单次上传请求最多合并的记录数，默认 {DB_BATCH_SIZE}"
    )
    return parser


def collect_log_files(path):
    """
    收集待处理的日志文件列表。

    目录则只取其中符合"system.<YYYY-MM-DD>.log"命名的文件（platform.* 等
    其他前缀、不带日期的日志一律排除，不进入分析逻辑）；文件则单列
    （显式指定的单个文件不做命名过滤，由使用者自行决定）；否则报错退出。
    """
    log_files = []
    if os.path.isdir(path):
        for name in sorted(os.listdir(path)):
            if name.endswith(".log") and is_analyzed_log_name(name):
                log_files.append(os.path.join(path, name))
        if not log_files:
            print(f"目录 {path} 下未找到符合 system.<YYYY-MM-DD>.log 命名的日志文件")
            sys.exit(1)
    elif os.path.isfile(path):
        log_files = [path]
    else:
        print(f"路径不存在：{path}")
        sys.exit(1)
    return log_files


def build_upload_cfg(args):
    """从命令行参数组装上传配置字典（供 db.upload_records 使用）。"""
    return {
        "enabled": bool(args.upload),
        "cf_account": args.cf_account,
        "cf_database": args.cf_database,
        "cf_token": args.cf_token,
        "db_batch": args.db_batch,
    }


def check_upload_cfg(upload_cfg):
    """
    校验上传配置：开关开启但凭证不全时，视为未配置上传（等同开关关闭），
    打印一次提示并返回关闭上传的新配置；配置完整则原样返回。
    """
    if upload_cfg.get("enabled") and not is_upload_configured(upload_cfg):
        print("提示：已开启数据库上传，但 Cloudflare 账户 ID / 数据库 ID / API Token "
              "未配置完整，本次运行不上传。")
        return dict(upload_cfg, enabled=False)
    return upload_cfg


def detect_log_file(path, out_dir, window):
    """检测单个日志文件并输出 CSV，返回异常记录列表（不标记已处理）。"""
    record_a, record_b, record_entry = parse_log(path)
    abnormal = find_anomalies(record_a, record_b, window, record_entry)
    output_results(abnormal, path, out_dir)
    return abnormal


def upload_and_mark(pending, upload_cfg, state=None, upload_fn=None):
    """
    统一上传并标记：把本轮全部日志的异常记录汇总后分批上传，全部成功才统一标记。

    pending 为 [(日志路径, 该日志的上传记录列表), ...]。
    任一批失败抛出 D1UploadError，调用方保证此时不标记任何日志
    （下轮重新检测并重试，去重键幂等不产生重复）。upload_fn 仅供测试注入。
    返回实际新插入行数。
    """
    records = [record for _, recs in pending for record in recs]
    if not records:
        # 本轮没有检出任何异常记录：无需请求 D1，直接标记已处理
        inserted = 0
    else:
        if upload_fn is None:
            upload_fn = upload_records
        inserted = upload_fn(records, upload_cfg)
    if state is not None:
        for path, _ in pending:
            state.mark(path)
        state.save()
    return inserted


def main():
    args = build_parser().parse_args()

    upload_cfg = check_upload_cfg(build_upload_cfg(args))
    upload_on = bool(upload_cfg.get("enabled"))

    # 已处理状态表（存放在输出目录下）：内容未变化的日志不再重复处理
    state = None if args.reprocess else ProcessedState(
        os.path.join(args.out, PROCESSED_STATE_FILE)
    )

    processed = skipped = failed = 0
    pending = []   # [(日志路径, 上传记录列表)]：待统一上传的日志
    # 第一阶段：逐个扫描全部日志，检出异常并输出 CSV
    for path in collect_log_files(args.log):
        if state is not None and state.is_processed(path):
            skipped += 1
            print(f"跳过已处理（内容未变化）：{path}")
            continue
        abnormal = detect_log_file(path, args.out, args.window)
        if upload_on:
            # 上传开启：先汇总，扫描完全部日志后统一上传
            pending.append((path, build_upload_records(abnormal, path)))
        else:
            # 未开启上传：行为与无上传版本一致，逐文件立即标记
            if state is not None:
                state.mark(path)
                state.save()
        processed += 1

    # 第二阶段：统一上传——本轮所有日志的异常记录合并分批上传，
    # 全部成功才统一标记已处理；任一批失败则全部不标记，下轮重试
    if pending:
        try:
            inserted = upload_and_mark(pending, upload_cfg, state)
            total = sum(len(recs) for _, recs in pending)
            print(f"已统一上传 {total} 条异常记录到 D1"
                  f"（新插入 {inserted} 条，涉及 {len(pending)} 个日志）。")
        except D1UploadError as exc:
            failed = len(pending)
            print(f"上传失败（本轮 {failed} 个日志均不标记已处理，下轮将自动重试）\n错误：{exc}")

    if state is not None or failed:
        summary = f"本次处理 {processed} 个日志，跳过已处理 {skipped} 个。"
        if failed:
            summary += f"上传失败 {failed} 个（未标记已处理，下轮自动重试）。"
        print(summary)


if __name__ == "__main__":
    main()
