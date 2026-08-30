# -*- coding: utf-8 -*-
"""
.env 文件读取模块（仅标准库）：把本地密钥文件加载进 os.environ。

用途：生产环境中 API Token 等涉密信息写在项目根目录的 .env 文件里
（已加入 .gitignore，不入仓库），程序启动时自动加载，
避免每次重启都要在 GUI 里重新手填，也不把密钥写进代码或配置 JSON。

语法支持：
  - KEY=VALUE（等号两侧空白可省略）
  - 整行注释（# 开头）与行尾不含引号时的 # 注释不予支持（值内含 # 请加引号）
  - 值可用单引号或双引号包裹（包裹时不做转义，直接去壳）
  - 可选的 "export " 前缀（兼容 shell 导出写法）

优先级约定：系统环境变量 > .env 文件（已存在的环境变量不会被覆盖）。
"""

import os

ENV_FILENAME = ".env"


def parse_env_file(path):
    """
    解析 .env 文件，返回 {键: 值} 字典。

    容错约定：文件不存在/不可读抛 OSError 由调用方处理；
    空行、# 注释行、没有等号的行直接跳过；非法键（空键）跳过。
    """
    env = {}
    with open(path, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):].lstrip()
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            # 成对引号包裹时去壳（不做转义，密钥场景足够）
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            if key:
                env[key] = value
    return env


def load_env(path):
    """
    把 .env 文件的键值加载进 os.environ（不覆盖已存在的环境变量）。

    返回本次实际加载的 {键: 值}（便于调用方提示/测试）；
    文件不存在或不可读时静默返回空字典——.env 是可选项，缺失不报错。
    """
    try:
        file_env = parse_env_file(path)
    except OSError:
        return {}
    loaded = {}
    for key, value in file_env.items():
        if key not in os.environ:
            os.environ[key] = value
            loaded[key] = value
    return loaded


def project_root():
    """项目根目录（parkcheck 包的上一级），.env 约定放在这里。"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_project_env():
    """加载项目根目录下的 .env（存在才生效），返回实际加载的键值。"""
    return load_env(os.path.join(project_root(), ENV_FILENAME))
