# -*- coding: utf-8 -*-
"""
图形界面入口：基于 tkinter 的停车场异常车辆检测工具。

允许在图形界面中配置：
  - 日志文件 / 目录路径
  - CSV 输出目录
  - 各项判定参数（窗口、费用关联窗口、停车时间关联窗口、
    最小停车时间偏差、入场去重窗口）

后台线程执行与命令行一致的处理流程：
  parse_log → find_anomalies → output_results
运行期间的控制台输出会实时回显到界面文本区。
"""

import os
import sys
import json
import threading
from io import StringIO
from contextlib import redirect_stdout

# 确保从项目根目录运行、或通过任意方式运行均可找到 parkcheck 包
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _PROJECT_ROOT)

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from parkcheck.config import (
    WINDOW_SECONDS,
    MIN_PARK_TIME_DEVIATION,
    ENTRY_DEDUP_WINDOW,
    DEFAULT_OUT_DIR,
)
from parkcheck.parser import parse_log
from parkcheck.detector import find_anomalies
from parkcheck.output import output_results


def collect_log_files(path):
    """收集待处理日志目录下所有 .log 文件。path 必须为目录。返回 (日志列表, 错误信息)。"""
    if not os.path.isdir(path):
        return [], f"路径不是目录或不存在：{path}"
    files = sorted(
        os.path.join(path, n) for n in os.listdir(path) if n.endswith(".log")
    )
    if not files:
        return [], f"目录 {path} 下未找到 .log 文件"
    return files, None


class CheckGui:
    """主窗口：配置区 + 参数区 + 运行按钮 + 结果回显区。"""

    def __init__(self, root):
        self.root = root
        root.title("停车场异常车辆检测")
        root.geometry("720x640")
        root.minsize(620, 540)

        self._build_widgets()

    # ---------------- 界面搭建 ----------------
    def _field_row(self, parent, label, default):
        """构造一行「标签 + 输入框」并返回输入框变量。"""
        ttk.Label(parent, text=label).pack(side="left", padx=(0, 6))
        var = tk.StringVar(value=str(default))
        entry = ttk.Entry(parent, textvariable=var, width=28)
        entry.pack(side="left", fill="x", expand=True)
        return var

    def _build_widgets(self):
        pad = {"padx": 8, "pady": 4}

        # 顶部菜单栏：说明 / 配置
        menubar = tk.Menu(self.root)
        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="查看参数说明…", command=self._show_help)
        menubar.add_cascade(label="说明", menu=help_menu)

        config_menu = tk.Menu(menubar, tearoff=0)
        config_menu.add_command(label="导出配置…", command=self._export_config)
        config_menu.add_command(label="导入配置…", command=self._import_config)
        menubar.add_cascade(label="配置", menu=config_menu)
        self.root.config(menu=menubar)

        # 顶部：路径配置
        path_frame = ttk.LabelFrame(self.root, text="路径配置", padding=8)
        path_frame.pack(fill="x", **pad)

        row1 = ttk.Frame(path_frame)
        row1.pack(fill="x", pady=2)
        # 默认日志目录 / 输出目录均转成绝对路径，避免相对路径歧义
        self.log_var = self._field_row(
            row1, "日志目录：", os.path.abspath(os.path.join(_PROJECT_ROOT, "document"))
        )
        ttk.Button(row1, text="浏览…", command=self._pick_log).pack(side="left", padx=(6, 0))

        row2 = ttk.Frame(path_frame)
        row2.pack(fill="x", pady=2)
        self.out_var = self._field_row(
            row2, "输出目录：", os.path.abspath(os.path.join(_PROJECT_ROOT, DEFAULT_OUT_DIR))
        )
        ttk.Button(row2, text="浏览…", command=self._pick_out).pack(side="left", padx=(6, 0))

        # 中部：参数配置
        param_frame = ttk.LabelFrame(self.root, text="判定参数", padding=8)
        param_frame.pack(fill="x", **pad)

        def spin_row(parent, label, default):
            frame = ttk.Frame(parent)
            frame.pack(fill="x", pady=2)
            ttk.Label(frame, text=label, width=22).pack(side="left")
            var = tk.StringVar(value=str(default))
            ttk.Spinbox(frame, from_=0, to=100000, width=8, textvariable=var).pack(side="left")
            return var

        grid = ttk.Frame(param_frame)
        grid.pack(fill="x")
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)

        left = ttk.Frame(grid)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        right = ttk.Frame(grid)
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        self.window_var = spin_row(left, "判定窗口(秒)", WINDOW_SECONDS)
        self.dev_var = spin_row(right, "最小停车时间偏差(分钟)", MIN_PARK_TIME_DEVIATION)
        self.dedup_var = spin_row(left, "入场去重窗口(秒)", ENTRY_DEDUP_WINDOW)

        # 底部：运行按钮 + 结果回显
        btn_frame = ttk.Frame(self.root)
        btn_frame.pack(fill="x", **pad)
        self.run_btn = ttk.Button(btn_frame, text="开始检测", command=self._start)
        self.run_btn.pack(side="left")
        ttk.Button(btn_frame, text="清空输出", command=self._clear_output).pack(side="left", padx=(6, 0))

        out_frame = ttk.LabelFrame(self.root, text="运行输出", padding=8)
        out_frame.pack(fill="both", expand=True, **pad)
        self.output_box = tk.Text(out_frame, wrap="none", height=16)
        self.output_box.pack(side="left", fill="both", expand=True)
        scroll_y = ttk.Scrollbar(out_frame, orient="vertical", command=self.output_box.yview)
        scroll_y.pack(side="right", fill="y")
        self.output_box.configure(yscrollcommand=scroll_y.set)

    # ---------------- 事件处理 ----------------
    def _pick_log(self):
        choice = tk.filedialog.askdirectory(title="选择日志目录")
        if choice:
            self.log_var.set(os.path.abspath(choice))

    def _pick_out(self):
        choice = tk.filedialog.askdirectory(title="选择输出目录")
        if choice:
            self.out_var.set(choice)

    def _clear_output(self):
        self.output_box.delete("1.0", "end")

    # ---------------- 说明 / 配置 ----------------
    def _help_text(self):
        """参数说明文本（供"说明"窗口展示，顶部说明处）。"""
        return f"""各项参数说明

1. 判定窗口(秒) —— 默认 {WINDOW_SECONDS}
   出场不开闸的车，其后该秒数内收到同车“支付结果下发”视为正常；
   期间无下发则判为异常。

2. 最小停车时间偏差(分钟) —— 默认 {MIN_PARK_TIME_DEVIATION}
   系统记录“停车时间”若比“入场→出场”实际时长至少多出该分钟数，
   才在“异常”列标 1（如门卫遥控放行等可疑情况）；不足该值视为取整误差。

3. 入场去重窗口(秒) —— 默认 {ENTRY_DEDUP_WINDOW}
   同车相邻两次“方向：入口”扫描间隔不超过该秒数，归并为同一次
   入场并取第一次时间，避免重复计次或把出场重试误判为新停车周期。

4. 日志目录
   待处理日志所属目录（使用绝对路径），将处理其中所有 .log 文件。

5. 输出目录
   CSV 结果导出目录（使用绝对路径），不存在时自动创建。

费用与系统“停车时间”与出场相机识别同时出现，直接按时间取最近一条，无需单独配置。"""

    def _show_help(self):
        """弹出"说明"窗口，展示参数说明。"""
        win = tk.Toplevel(self.root)
        win.title("参数说明")
        win.geometry("560x460")
        win.transient(self.root)
        text = tk.Text(win, wrap="word", padx=12, pady=8)
        text.insert("1.0", self._help_text())
        text.config(state="disabled")
        text.pack(fill="both", expand=True)
        ttk.Button(win, text="关闭", command=win.destroy).pack(pady=8)

    def _export_config(self):
        """导出当前配置为 JSON 文件。"""
        default_name = os.path.join(_PROJECT_ROOT, "检测配置.json")
        path = tk.filedialog.asksaveasfilename(
            title="导出配置",
            initialfile=os.path.basename(default_name),
            initialdir=_PROJECT_ROOT,
            defaultextension=".json",
            filetypes=[("JSON 配置", "*.json")],
        )
        if not path:
            return
        data = {
            "log_dir": self.log_var.get().strip(),
            "out_dir": self.out_var.get().strip(),
            "window": self._read_int(self.window_var, "判定窗口"),
            "deviation": self._read_int(self.dev_var, "最小停车时间偏差"),
            "dedup": self._read_int(self.dedup_var, "入场去重窗口"),
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            messagebox.showinfo("导出配置", f"已导出到：\n{path}")
        except (OSError, ValueError) as exc:
            messagebox.showerror("导出配置", f"导出失败：{exc}")

    def _import_config(self):
        """从 JSON 文件导入配置并填充界面。"""
        path = tk.filedialog.askopenfilename(
            title="导入配置",
            filetypes=[("JSON 配置", "*.json"), ("所有文件", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as exc:
            messagebox.showerror("导入配置", f"读取配置失败：{exc}")
            return
        # 缺失字段使用默认值，非法数字由界面后续校验兜底
        if "log_dir" in data:
            self.log_var.set(str(data["log_dir"]).strip() or self.log_var.get())
        if "out_dir" in data:
            self.out_var.set(str(data["out_dir"]).strip() or self.out_var.get())
        for key, var, label in (
            ("window", self.window_var, "判定窗口"),
            ("deviation", self.dev_var, "最小停车时间偏差"),
            ("dedup", self.dedup_var, "入场去重窗口"),
        ):
            if key in data:
                try:
                    var.set(str(int(data[key])))
                except (TypeError, ValueError):
                    messagebox.showwarning("导入配置",
                                           f"{label} 不是合法整数，已跳过：{data[key]!r}")
        messagebox.showinfo("导入配置", "配置已导入。")

    def _read_int(self, var, label):
        try:
            return int(var.get())
        except ValueError:
            raise ValueError(f"{label} 必须是整数，当前为：{var.get()!r}")

    def _parse_params(self):
        return {
            "window": self._read_int(self.window_var, "判定窗口"),
            "deviation": self._read_int(self.dev_var, "最小停车时间偏差"),
            "dedup": self._read_int(self.dedup_var, "入场去重窗口"),
        }

    def _start(self):
        """校验输入后启动后台线程执行。"""
        # 统一转成绝对路径，避免相对路径受当前工作目录影响
        log_path = os.path.abspath(self.log_var.get().strip())
        out_dir = os.path.abspath(self.out_var.get().strip())
        if not log_path or not out_dir:
            messagebox.showerror("参数错误", "日志路径与输出目录均不能为空。")
            return
        if os.path.exists(out_dir) and not os.path.isdir(out_dir):
            messagebox.showerror("参数错误", f"输出目录已存在但不是目录：{out_dir}")
            return
        try:
            params = self._parse_params()
        except ValueError as exc:
            messagebox.showerror("参数错误", str(exc))
            return

        self.run_btn.config(state="disabled")
        self._clear_output()
        threading.Thread(
            target=self._run_worker,
            args=(log_path, out_dir, params),
            daemon=True,
        ).start()

    def _run_worker(self, log_path, out_dir, params):
        """后台执行处理流程，捕获标准输出回显到界面。"""
        buf = StringIO()
        try:
            with redirect_stdout(buf):
                files, err = collect_log_files(log_path)
                if err:
                    print(err)
                    self._finish(buf, "运行失败")
                    return
                for path in files:
                    record_a, record_b, record_entry = parse_log(path)
                    abnormal = find_anomalies(
                        record_a, record_b, params["window"], record_entry,
                        entry_dedup_window=params["dedup"],
                        min_deviation=params["deviation"],
                    )
                    output_results(abnormal, path, out_dir)
                print("\n全部处理完成。")
            self._finish(buf, "运行完成")
        except Exception as exc:  # 界面层兜底，避免线程静默崩溃
            buf.write(f"\n发生错误：{exc}\n")
            self._finish(buf, "运行出错")

    def _finish(self, buf, status):
        """把输出写到文本框，并恢复按钮状态。"""
        def do():
            self.output_box.insert("end", buf.getvalue())
            self.output_box.see("end")
            self.run_btn.config(state="normal")
            self.root.title(f"停车场异常车辆检测 - {status}")
        try:
            self.root.after(0, do)
        except RuntimeError:
            pass


def main():
    root = tk.Tk()
    CheckGui(root)
    root.mainloop()


if __name__ == "__main__":
    main()