# -*- coding: utf-8 -*-
"""parkcheck.state 已处理文件状态模块的测试（无第三方依赖，直接运行即可）。"""

import os
import sys
import time
import tempfile
import shutil

# 将项目根目录加入 sys.path，以便导入被测试包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parkcheck.state import ProcessedState  # noqa: E402


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
    return tempfile.mkdtemp(prefix="parkcheck_state_")


def test_new_file_not_processed():
    """未记录的文件不应视为已处理。"""
    tmp = make_tmpdir()
    try:
        log = os.path.join(tmp, "system.2026-08-30.log")
        with open(log, "w", encoding="utf-8") as f:
            f.write("10:00:00 - 出场处理\n")
        state = ProcessedState(os.path.join(tmp, ".processed.json"))
        check("未记录的文件 → 未处理", not state.is_processed(log))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_mark_and_reload():
    """mark 后应视为已处理，且保存后重新加载仍然有效。"""
    tmp = make_tmpdir()
    try:
        log = os.path.join(tmp, "system.2026-08-30.log")
        with open(log, "w", encoding="utf-8") as f:
            f.write("10:00:00 - 出场处理\n")
        state_path = os.path.join(tmp, ".processed.json")
        state = ProcessedState(state_path)
        state.mark(log)
        check("mark 后 → 已处理", state.is_processed(log))
        state.save()

        reloaded = ProcessedState(state_path)
        check("保存后重新加载 → 已处理", reloaded.is_processed(log))
        check("状态文件已生成", os.path.isfile(state_path))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_appended_file_reprocessed():
    """文件被追加写入（大小变化）后应重新处理。"""
    tmp = make_tmpdir()
    try:
        log = os.path.join(tmp, "system.2026-08-30.log")
        with open(log, "w", encoding="utf-8") as f:
            f.write("10:00:00 - 出场处理\n")
        state = ProcessedState(os.path.join(tmp, ".processed.json"))
        state.mark(log)
        state.save()

        with open(log, "a", encoding="utf-8") as f:
            f.write("11:00:00 - 出场处理\n")
        check("追加内容后 → 未处理（需重新检测）", not state.is_processed(log))

        # 重新处理后状态再次生效
        state.mark(log)
        check("重新 mark 后 → 已处理", state.is_processed(log))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_mtime_change_reprocessed():
    """大小不变但修改时间变化（如被 touch）后应重新处理。"""
    tmp = make_tmpdir()
    try:
        log = os.path.join(tmp, "system.2026-08-30.log")
        with open(log, "w", encoding="utf-8") as f:
            f.write("10:00:00 - 出场处理\n")
        state = ProcessedState(os.path.join(tmp, ".processed.json"))
        state.mark(log)
        state.save()

        past = time.time() - 3600
        os.utime(log, (past, past))
        check("修改时间变化后 → 未处理（需重新检测）", not state.is_processed(log))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_corrupted_state_file():
    """状态文件损坏时应从空表开始，不崩溃。"""
    tmp = make_tmpdir()
    try:
        state_path = os.path.join(tmp, ".processed.json")
        with open(state_path, "w", encoding="utf-8") as f:
            f.write("{不是合法 JSON")
        state = ProcessedState(state_path)
        log = os.path.join(tmp, "system.2026-08-30.log")
        with open(log, "w", encoding="utf-8") as f:
            f.write("10:00:00 - 出场处理\n")
        check("状态文件损坏 → 全部视为未处理", not state.is_processed(log))
        # 损坏后仍可正常 mark 并覆盖写回
        state.mark(log)
        state.save()
        reloaded = ProcessedState(state_path)
        check("损坏后重新记录保存 → 已处理", reloaded.is_processed(log))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_missing_file_tolerated():
    """已记录的日志文件被删除后，is_processed 应容错返回 False。"""
    tmp = make_tmpdir()
    try:
        log = os.path.join(tmp, "system.2026-08-30.log")
        with open(log, "w", encoding="utf-8") as f:
            f.write("10:00:00 - 出场处理\n")
        state = ProcessedState(os.path.join(tmp, ".processed.json"))
        state.mark(log)
        state.save()
        os.remove(log)
        check("日志文件被删除 → 未处理且不崩溃", not state.is_processed(log))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_case_insensitive_path():
    """Windows 路径不区分大小写：同一文件不同大小写写法应视为同一记录。"""
    if os.path.normcase("A") == os.path.normcase("a"):
        pass  # Windows 平台继续执行；其他平台此用例无意义
    else:
        return
    tmp = make_tmpdir()
    try:
        log = os.path.join(tmp, "system.2026-08-30.log")
        with open(log, "w", encoding="utf-8") as f:
            f.write("10:00:00 - 出场处理\n")
        state = ProcessedState(os.path.join(tmp, ".processed.json"))
        state.mark(log)
        check("小写路径 mark → 大写路径视为已处理",
              state.is_processed(log.upper()), f"路径: {log.upper()}")
        check("大写路径 mark → 小写路径视为已处理",
              state.is_processed(log))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    test_new_file_not_processed()
    test_mark_and_reload()
    test_appended_file_reprocessed()
    test_mtime_change_reprocessed()
    test_corrupted_state_file()
    test_missing_file_tolerated()
    test_case_insensitive_path()
    print(f"\n通过 {PASS} 项，失败 {FAIL} 项。")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
