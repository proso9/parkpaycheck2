# -*- coding: utf-8 -*-
"""parkcheck.env .env 读取模块的测试（无第三方依赖、不碰真实 .env，直接运行即可）。"""

import os
import sys
import tempfile
import shutil

# 将项目根目录加入 sys.path，以便导入被测试包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Windows 控制台默认编码可能是 cp1252/gbk，统一改为 UTF-8 输出，避免 print 中文报错
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from parkcheck.env import (  # noqa: E402
    parse_env_file,
    load_env,
    load_project_env,
    project_root,
)
from parkcheck import config  # noqa: E402

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
    return tempfile.mkdtemp(prefix="parkcheck_env_")


def write(path, content):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def test_parse_basic():
    """基本语法：键值、注释、空行、无等号行。"""
    tmp = make_tmpdir()
    try:
        path = os.path.join(tmp, ".env")
        write(path, "\n".join([
            "# 整行注释",
            "CF_ACCOUNT_ID=abc123",
            "  CF_DATABASE_ID =  db-uuid  ",
            "",
            "这一行没有等号应被跳过",
            "DB_BATCH_SIZE=50",
        ]) + "\n")
        env = parse_env_file(path)
        check("基本键值解析", env.get("CF_ACCOUNT_ID") == "abc123")
        check("键值两侧空白被剥离", env.get("CF_DATABASE_ID") == "db-uuid")
        check("无等号行被跳过", "这一行没有等号应被跳过" not in env and len(env) == 3,
              str(env))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_parse_quotes_and_export():
    """引号包裹与 export 前缀。"""
    tmp = make_tmpdir()
    try:
        path = os.path.join(tmp, ".env")
        write(path, "\n".join([
            'CF_API_TOKEN="quoted token"',
            "CF_API_TOKEN2='single quoted # not comment'",
            "export CF_ACCOUNT_ID=exported",
        ]) + "\n")
        env = parse_env_file(path)
        check("双引号去壳", env.get("CF_API_TOKEN") == "quoted token")
        check("单引号去壳且 # 保留", env.get("CF_API_TOKEN2") == "single quoted # not comment")
        check("export 前缀被剥除", env.get("CF_ACCOUNT_ID") == "exported")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_load_env_merges_and_not_override():
    """加载进 os.environ：缺省键写入、已存在的环境变量不被覆盖。"""
    tmp = make_tmpdir()
    try:
        path = os.path.join(tmp, ".env")
        write(path, "PARKCHECK_TEST_A=from_file\nPARKCHECK_TEST_B=from_file\n")
        os.environ["PARKCHECK_TEST_A"] = "from_system"
        try:
            loaded = load_env(path)
            check("已存在的环境变量不被覆盖", os.environ["PARKCHECK_TEST_A"] == "from_system")
            check("新键被写入环境", os.environ["PARKCHECK_TEST_B"] == "from_file")
            check("返回实际加载的键（不含被跳过的）",
                  "PARKCHECK_TEST_B" in loaded and "PARKCHECK_TEST_A" not in loaded)
        finally:
            os.environ.pop("PARKCHECK_TEST_A", None)
            os.environ.pop("PARKCHECK_TEST_B", None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_load_missing_file_tolerated():
    """.env 不存在时静默返回空字典，不报错。"""
    loaded = load_env(os.path.join(make_tmpdir(), "不存在的.env"))
    check(".env 缺失 → 静默返回空", loaded == {})


def test_config_helpers():
    """config 的 _env_bool / _env_int 容错行为。"""
    key_b = "PARKCHECK_TEST_BOOL"
    key_i = "PARKCHECK_TEST_INT"
    try:
        for truthy in ("1", "true", "YES", "On", "y"):
            os.environ[key_b] = truthy
            check(f"布尔真值：{truthy}", config._env_bool(key_b) is True)
        os.environ[key_b] = "0"
        check("布尔假值：0", config._env_bool(key_b) is False)
        os.environ.pop(key_b)
        check("布尔缺省", config._env_bool(key_b, True) is True)

        os.environ[key_i] = "42"
        check("整数解析", config._env_int(key_i, 0) == 42)
        os.environ[key_i] = "abc"
        check("非法整数回退默认", config._env_int(key_i, 50) == 50)
        os.environ.pop(key_i)
        check("缺省整数回退默认", config._env_int(key_i, 50) == 50)
    finally:
        os.environ.pop(key_b, None)
        os.environ.pop(key_i, None)


def test_project_root_and_loader():
    """project_root 指向 parkcheck 上一级；load_project_env 对缺失文件容错。"""
    check("项目根目录定位", os.path.isfile(os.path.join(project_root(), "parkcheck", "config.py")))
    # 真实项目根目录可能没有 .env（可选项），加载不应报错
    loaded = load_project_env()
    check("项目级加载容错（返回字典）", isinstance(loaded, dict))


def main():
    test_parse_basic()
    test_parse_quotes_and_export()
    test_load_env_merges_and_not_override()
    test_load_missing_file_tolerated()
    test_config_helpers()
    test_project_root_and_loader()
    print(f"\n通过 {PASS} 项，失败 {FAIL} 项。")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
