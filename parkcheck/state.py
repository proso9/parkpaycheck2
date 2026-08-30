# -*- coding: utf-8 -*-
"""
已处理文件状态模块：记录已检测过的日志文件，避免重复处理。

状态以 JSON 形式存放在输出目录下（默认 .processed.json），
每条记录保存日志文件的大小与修改时间：
  - 文件未变化 → 视为已处理，跳过；
  - 文件有追加/修改（如当天日志仍在写入）→ 视为未处理，重新检测。
状态文件随输出目录走：换输出目录即重新记录，互不干扰。
"""

import json
import os


class ProcessedState:
    """已处理日志文件状态表：按「绝对路径 → (大小, 修改时间)」记录。"""

    def __init__(self, state_path):
        self._path = state_path
        self._records = {}   # {绝对路径: {"size": 字节数, "mtime": 修改时间戳}}
        self._load()

    @property
    def state_path(self):
        """状态文件路径。"""
        return self._path

    def _load(self):
        """读取状态文件；不存在或内容损坏时从空表开始。"""
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._records = {
                    key: value for key, value in data.items()
                    if isinstance(value, dict) and "size" in value and "mtime" in value
                }
        except (OSError, ValueError):
            self._records = {}

    def is_processed(self, path):
        """文件是否已处理且此后内容未发生变化。"""
        record = self._records.get(os.path.abspath(path))
        if record is None:
            return False
        try:
            stat = os.stat(path)
        except OSError:
            return False
        return record.get("size") == stat.st_size and record.get("mtime") == stat.st_mtime

    def mark(self, path):
        """把文件记为已处理（记录当前大小与修改时间）。"""
        stat = os.stat(path)
        self._records[os.path.abspath(path)] = {
            "size": stat.st_size,
            "mtime": stat.st_mtime,
        }

    def save(self):
        """把状态写回状态文件（输出目录不存在时自动创建）。"""
        parent = os.path.dirname(self._path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(self._records, f, ensure_ascii=False, indent=2)
