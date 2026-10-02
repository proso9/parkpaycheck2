# -*- coding: utf-8 -*-
"""
命令行入口模块：解析命令行参数、收集日志文件并调度处理流程。

统一编排：parse_log → find_anomalies → output_results →（可选）统一上传 D1 → 标记已处理。
完整一轮检测封装在 run_detection_round，GUI（check_gui.py）复用同一函数，
保证两条入口的编排行为（输出、标记时机、错误隔离）完全一致。

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
    ENTRY_DEDUP_WINDOW,
    MIN_PARK_TIME_DEVIATION,
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
        "--min-deviation", type=int, default=MIN_PARK_TIME_DEVIATION,
        help=f"最小停车时间偏差(分钟)：系统停车时间比实际时长至少多出该分钟数才"
             f"标记可疑，默认 {MIN_PARK_TIME_DEVIATION}"
    )
    parser.add_argument(
        "--entry-dedup", type=int, default=ENTRY_DEDUP_WINDOW,
        help=f"入场去重窗口(秒)：同车相邻入口扫描间隔不超过该秒数归并为同一次入场，"
             f"默认 {ENTRY_DEDUP_WINDOW}"
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


def list_log_files(path):
    """
    收集待处理的日志文件列表，返回 (文件列表, 错误信息)。

    目录则只取其中符合"system.<YYYY-MM-DD>.log"命名的文件（platform.* 等
    其他前缀、不带日期的日志一律排除，不进入分析逻辑）；文件则单列
    （显式指定的单个文件不做命名过滤，由使用者自行决定）；否则返回错误信息。
    """
    if os.path.isdir(path):
        log_files = [
            os.path.join(path, name)
            for name in sorted(os.listdir(path))
            if is_analyzed_log_name(name)
        ]
        if not log_files:
            return [], f"目录 {path} 下未找到符合 system.<YYYY-MM-DD>.log 命名的日志文件"
        return log_files, None
    if os.path.isfile(path):
        return [path], None
    return [], f"路径不存在：{path}"


def collect_log_files(path):
    """命令行入口的日志收集：收集失败打印错误并退出（错误信息见 list_log_files）。"""
    log_files, err = list_log_files(path)
    if err:
        print(err)
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


def run_detection_round(files, out_dir, window,
                        entry_dedup_window=ENTRY_DEDUP_WINDOW,
                        min_deviation=MIN_PARK_TIME_DEVIATION,
                        skip_processed=True, upload_cfg=None):
    """
    执行一轮完整检测（CLI 与 GUI 共用），返回 (processed, skipped, errors, failed)。

    - 逐文件 解析 → 判定 → 输出 CSV；单个文件失败只跳过该文件并打印错误，
      不中断本轮（该文件不标记已处理，下轮自动重试）；
    - 上传开启：汇总所有异常记录统一分批上传，全部成功才统一标记已处理；
      任一批失败本轮全部不标记（failed = 待上传日志数）；
    - 上传关闭：逐文件立即标记已处理（skip_processed=False 时不标记）。
    """
    upload_on = bool(upload_cfg and upload_cfg.get("enabled"))
    state = ProcessedState(
        os.path.join(out_dir, PROCESSED_STATE_FILE)
    ) if skip_processed else None

    processed = skipped = errors = 0
    pending = []   # [(日志路径, 上传记录列表)]：待统一上传的日志
    # 第一阶段：逐个扫描全部日志，检出异常并输出 CSV
    for path in files:
        if state is not None and state.is_processed(path):
            skipped += 1
            print(f"跳过已处理（内容未变化）：{path}")
            continue
        try:
            record_a, record_b, record_entry = parse_log(path)
            abnormal = find_anomalies(
                record_a, record_b, window, record_entry,
                entry_dedup_window=entry_dedup_window,
                min_deviation=min_deviation,
            )
            output_results(abnormal, path, out_dir)
            if upload_on:
                # 上传开启：先汇总，扫描完全部日志后统一上传
                pending.append((path, build_upload_records(abnormal, path)))
            elif state is not None:
                # 未开启上传：行为与无上传版本一致，逐文件立即标记
                state.mark(path)
                state.save()
        except Exception as exc:
            errors += 1
            print(f"处理失败（跳过该日志，不标记已处理，下轮自动重试）：{path}\n错误：{exc}")
            continue
        processed += 1

    # 第二阶段：统一上传——本轮所有日志的异常记录合并分批上传，
    # 全部成功才统一标记已处理；任一批失败则全部不标记，下轮重试
    failed = 0
    if pending:
        try:
            inserted = upload_and_mark(pending, upload_cfg, state)
            total = sum(len(recs) for _, recs in pending)
            print(f"已统一上传 {total} 条异常记录到 D1"
                  f"（新插入 {inserted} 条，涉及 {len(pending)} 个日志）。")
        except D1UploadError as exc:
            failed = len(pending)
            print(f"上传失败（本轮 {failed} 个日志均不标记已处理，下轮将自动重试）\n错误：{exc}")
    return processed, skipped, errors, failed


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
            try:
                state.mark(path)
            except OSError:
                pass  # 日志文件已消失（如被移动/删除），无需记录
        state.save()
    return inserted


def main():
    args = build_parser().parse_args()

    upload_cfg = check_upload_cfg(build_upload_cfg(args))

    files = collect_log_files(args.log)
    processed, skipped, errors, failed = run_detection_round(
        files, args.out, args.window,
        entry_dedup_window=args.entry_dedup,
        min_deviation=args.min_deviation,
        skip_processed=not args.reprocess,
        upload_cfg=upload_cfg,
    )
    summary = f"本次处理 {processed} 个日志，跳过已处理 {skipped} 个。"
    if errors:
        summary += f"读取失败 {errors} 个（未标记已处理，下轮自动重试）。"
    if failed:
        summary += f"上传失败 {failed} 个（未标记已处理，下轮自动重试）。"
    print(summary)


if __name__ == "__main__":
    main()
