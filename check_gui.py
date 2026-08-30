# -*- coding: utf-8 -*-
"""
图形界面入口：基于 tkinter 的停车场异常车辆检测工具。

允许在图形界面中配置：
  - 日志文件 / 目录路径
  - CSV 输出目录
  - 各项判定参数（窗口、费用关联窗口、停车时间关联窗口、
    最小停车时间偏差、入场去重窗口）
  - 定时任务（基于 APScheduler）：间隔运行 / 每天固定时间，
    配置项（间隔值/单位、HH:MM、开关）均可直接编辑

配置按分页隔离：路径配置 / 判定参数 / 定时任务 / 运行输出。

关闭窗口后自动收纳到系统托盘（pystray）继续后台运行，
托盘菜单可重新打开主窗口或完全退出。

后台线程执行与命令行一致的处理流程：
  parse_log → find_anomalies → output_results
运行期间的控制台输出会实时回显到界面文本区。
"""

import os
import sys
import json
import time
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
    PROCESSED_STATE_FILE,
    DB_BATCH_SIZE,
    UPLOAD_ENABLED,
    CF_ACCOUNT_ID,
    CF_DATABASE_ID,
    CF_API_TOKEN,
)
from parkcheck.db import (
    D1UploadError,
    build_upload_records,
    upload_records,
    is_upload_configured,
)
from parkcheck.scheduler import (
    SchedulerManager,
    interval_to_seconds,
    validate_daily_time,
)
from parkcheck.parser import parse_log
from parkcheck.detector import find_anomalies
from parkcheck.output import output_results
from parkcheck.state import ProcessedState


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


def upload_export_fields(enabled, account, database, batch):
    """
    上传配置中允许导出到 JSON 的字段（纯函数，便于测试）。

    API Token 涉密，绝不包含在导出字段中；批量大小非法时回退默认值。
    """
    try:
        batch = int(batch)
    except (TypeError, ValueError):
        batch = DB_BATCH_SIZE
    return {
        "upload_enabled": bool(enabled),
        "cf_account": str(account).strip(),
        "cf_database": str(database).strip(),
        "db_batch": batch,
    }


class CheckGui:
    """主窗口：分页配置（路径 / 判定参数 / 定时任务）+ 运行输出；关闭后收纳系统托盘。"""

    def __init__(self, root):
        self.root = root
        root.title("停车场异常车辆检测")
        root.geometry("760x660")
        root.minsize(640, 560)

        self._scheduler = SchedulerManager()   # 定时任务调度器
        self._schedule_running = False         # 定时任务单轮执行重入锁
        self._tray_icon = None                 # 系统托盘图标（pystray）
        self._tray_available = False           # 托盘是否可用
        self._hidden_to_tray = False           # 是否已提示过收纳信息

        self._build_widgets()
        self._set_window_icon()   # 主窗口图标与托盘一致
        self._setup_tray()
        # 关闭按钮：收纳到系统托盘（后台定时任务继续运行），托盘菜单可完全退出
        root.protocol("WM_DELETE_WINDOW", self._on_close)

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

        # 顶部菜单栏：说明 / 配置（含退出程序）
        menubar = tk.Menu(self.root)
        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="查看参数说明…", command=self._show_help)
        menubar.add_cascade(label="说明", menu=help_menu)

        config_menu = tk.Menu(menubar, tearoff=0)
        config_menu.add_command(label="导出配置…", command=self._export_config)
        config_menu.add_command(label="导入配置…", command=self._import_config)
        config_menu.add_separator()
        config_menu.add_command(label="退出程序", command=self._quit_app)
        menubar.add_cascade(label="配置", menu=config_menu)
        self.root.config(menu=menubar)

        # 分页：路径配置 / 判定参数 / 定时任务 / 数据库上传 / 运行输出
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, **pad)

        tab_paths = ttk.Frame(notebook, padding=10)
        tab_params = ttk.Frame(notebook, padding=10)
        tab_sched = ttk.Frame(notebook, padding=10)
        tab_upload = ttk.Frame(notebook, padding=10)
        tab_run = ttk.Frame(notebook, padding=10)
        notebook.add(tab_paths, text="路径配置")
        notebook.add(tab_params, text="判定参数")
        notebook.add(tab_sched, text="定时任务")
        notebook.add(tab_upload, text="数据库上传")
        notebook.add(tab_run, text="运行与输出")

        self._build_paths_tab(tab_paths)
        self._build_params_tab(tab_params)
        self._build_schedule_tab(tab_sched)
        self._build_upload_tab(tab_upload)
        self._build_run_tab(tab_run)

    def _build_upload_tab(self, parent):
        """数据库上传页：开关 + Cloudflare D1 凭证 + 批量大小。

        初始值来自 config 默认（可经环境变量/.env 提供），重启后免手填。
        """
        self._upload_enabled = tk.BooleanVar(value=bool(UPLOAD_ENABLED))
        ttk.Checkbutton(
            parent,
            text="启用数据库上传（把异常记录上传到 Cloudflare D1，上传成功才标记日志已处理）",
            variable=self._upload_enabled,
        ).pack(anchor="w", pady=(4, 8))

        account_row = ttk.Frame(parent)
        account_row.pack(fill="x", pady=4)
        self._cf_account = self._field_row(account_row, "账户 ID：", CF_ACCOUNT_ID)

        database_row = ttk.Frame(parent)
        database_row.pack(fill="x", pady=4)
        self._cf_database = self._field_row(database_row, "数据库 ID：", CF_DATABASE_ID)

        token_row = ttk.Frame(parent)
        token_row.pack(fill="x", pady=4)
        ttk.Label(token_row, text="API Token：").pack(side="left", padx=(0, 6))
        self._cf_token = tk.StringVar(value=CF_API_TOKEN)
        # Token 以掩码显示，且绝不随「导出配置」JSON 导出
        ttk.Entry(token_row, textvariable=self._cf_token, width=28,
                  show="*").pack(side="left", fill="x", expand=True)

        batch_row = ttk.Frame(parent)
        batch_row.pack(fill="x", pady=4)
        ttk.Label(batch_row, text="批量大小：").pack(side="left")
        self._db_batch = tk.StringVar(value=str(DB_BATCH_SIZE))
        ttk.Spinbox(batch_row, from_=1, to=10000, width=8,
                    textvariable=self._db_batch).pack(side="left")
        ttk.Label(batch_row, text="（单次请求最多合并的记录数）").pack(side="left", padx=(6, 0))

        ttk.Label(
            parent,
            text=("说明：需具备 D1:Edit 权限的 Cloudflare API Token；三凭证任一为空视为未配置上传。\n"
                  "上传采用 INSERT OR IGNORE + 去重键，只插入新记录、永不覆盖，重复上传自动忽略。"),
            foreground="#666666", wraplength=680, justify="left",
        ).pack(anchor="w", pady=(12, 0))

    def _build_paths_tab(self, parent):
        """路径配置页：日志目录 + 输出目录。"""
        row1 = ttk.Frame(parent)
        row1.pack(fill="x", pady=4)
        # 默认日志目录 / 输出目录均转成绝对路径，避免相对路径歧义
        self.log_var = self._field_row(
            row1, "日志目录：", os.path.abspath(os.path.join(_PROJECT_ROOT, "document"))
        )
        ttk.Button(row1, text="浏览…", command=self._pick_log).pack(side="left", padx=(6, 0))

        row2 = ttk.Frame(parent)
        row2.pack(fill="x", pady=4)
        self.out_var = self._field_row(
            row2, "输出目录：", os.path.abspath(os.path.join(_PROJECT_ROOT, DEFAULT_OUT_DIR))
        )
        ttk.Button(row2, text="浏览…", command=self._pick_out).pack(side="left", padx=(6, 0))

        row3 = ttk.Frame(parent)
        row3.pack(fill="x", pady=4)
        self.skip_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            row3,
            text="排除已处理的日志文件（内容未变化的不再重复处理，仍在写入的日志会自动重新处理）",
            variable=self.skip_var,
        ).pack(anchor="w")

    def _build_params_tab(self, parent):
        """判定参数页：判定窗口 / 停车时间偏差 / 入场去重窗口（纵向排列，文字左对齐）。"""
        def spin_row(parent_, label, default):
            row = ttk.Frame(parent_)
            row.pack(fill="x", pady=4)
            ttk.Label(row, text=label).pack(side="left", anchor="w")
            var = tk.StringVar(value=str(default))
            ttk.Spinbox(row, from_=0, to=100000, width=8,
                        textvariable=var).pack(side="left", padx=(6, 0))
            return var

        self.window_var = spin_row(parent, "判定窗口(秒)", WINDOW_SECONDS)
        self.dev_var = spin_row(parent, "最小停车时间偏差(分钟)", MIN_PARK_TIME_DEVIATION)
        self.dedup_var = spin_row(parent, "入场去重窗口(秒)", ENTRY_DEDUP_WINDOW)

    def _build_schedule_tab(self, parent):
        """定时任务页：开关 / 类型 / 间隔 / 每天时间 / 状态。"""
        self._schedule_enabled = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            parent,
            text="启用定时任务（按下方配置定时运行检测）",
            variable=self._schedule_enabled,
            command=self._on_schedule_toggle,
        ).pack(anchor="w", pady=(4, 8))

        type_row = ttk.Frame(parent)
        type_row.pack(fill="x", pady=4)
        ttk.Label(type_row, text="任务类型：").pack(side="left")
        self._schedule_type = tk.StringVar(value="interval")
        ttk.Radiobutton(type_row, text="间隔运行", value="interval",
                        variable=self._schedule_type).pack(side="left")
        ttk.Radiobutton(type_row, text="每天固定时间", value="daily",
                        variable=self._schedule_type).pack(side="left", padx=(12, 0))

        interval_row = ttk.Frame(parent)
        interval_row.pack(fill="x", pady=4)
        ttk.Label(interval_row, text="间隔：").pack(side="left")
        self._schedule_value = tk.StringVar(value="30")
        ttk.Spinbox(interval_row, from_=1, to=100000, width=8,
                    textvariable=self._schedule_value).pack(side="left")
        self._schedule_unit = tk.StringVar(value="分")
        ttk.Combobox(interval_row, textvariable=self._schedule_unit,
                     values=["秒", "分", "时"], width=4,
                     state="readonly").pack(side="left", padx=(6, 0))
        ttk.Label(interval_row, text="运行一次").pack(side="left", padx=(6, 0))

        daily_row = ttk.Frame(parent)
        daily_row.pack(fill="x", pady=4)
        ttk.Label(daily_row, text="每天：").pack(side="left")
        self._schedule_time = tk.StringVar(value="08:00")
        ttk.Entry(daily_row, textvariable=self._schedule_time, width=8).pack(side="left")
        ttk.Label(daily_row, text="（HH:MM，如 08:00）").pack(side="left", padx=(6, 0))

        self._schedule_status_var = tk.StringVar(value="定时任务：未启动")
        ttk.Label(parent, textvariable=self._schedule_status_var,
                  foreground="#0066cc").pack(anchor="w", pady=(10, 0))

    def _build_run_tab(self, parent):
        """运行输出页：运行按钮 + 清空 + 结果回显。"""
        btn_frame = ttk.Frame(parent)
        btn_frame.pack(fill="x", pady=(0, 6))
        self.run_btn = ttk.Button(btn_frame, text="开始检测", command=self._start)
        self.run_btn.pack(side="left")
        ttk.Button(btn_frame, text="清空输出", command=self._clear_output).pack(side="left", padx=(6, 0))

        out_frame = ttk.LabelFrame(parent, text="运行与输出", padding=8)
        out_frame.pack(fill="both", expand=True)
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

6. 排除已处理的日志文件
   勾选后，内容未变化的已处理日志自动跳过，避免定时任务
   重复处理旧日志；当天仍在追加写入的日志不受影响，会正常
   重新检测。已处理记录保存在输出目录下的 .processed.json。

7. 定时任务（APScheduler）
   勾选“启用定时任务”后，将按下方配置在后台自动定时检测：
   - 间隔运行：每隔 N 秒/分/时运行一次；
   - 每天固定时间：每天 HH:MM（如 08:00）运行一次。
   定时检测每次触发都会实时读取当前界面的路径与参数配置，
   修改后无需重启即生效；勾选框取消即停止。
   任务状态栏会显示“下次触发”时间，便于确认已生效。

8. 数据库上传（Cloudflare D1）
   勾选“启用数据库上传”后，每检测出一个日志文件的异常记录，
   会先上传到 D1 再把该日志标记为已处理；上传失败不标记，
   下一轮定时触发时自动重试（去重键幂等，不产生重复数据）。
   - 账户 ID / 数据库 ID / API Token 三者任一为空视为未配置上传；
   - API Token 需具备 D1:Edit 权限，仅保存在本机内存中，
     绝不随「导出配置」JSON 导出，导入配置时也会忽略该字段；
   - 上传只插入新记录（INSERT OR IGNORE），已有记录的
     处理状态/备注（由展示项目维护）不会被本工具修改。

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
            "skip_processed": bool(self.skip_var.get()),
            "window": self._read_int(self.window_var, "判定窗口"),
            "deviation": self._read_int(self.dev_var, "最小停车时间偏差"),
            "dedup": self._read_int(self.dedup_var, "入场去重窗口"),
            "schedule_enabled": bool(self._schedule_enabled.get()),
            "schedule_type": self._schedule_type.get(),
            "schedule_value": self._read_int(self._schedule_value, "定时间隔"),
            "schedule_unit": self._schedule_unit.get(),
            "schedule_time": self._schedule_time.get().strip(),
        }
        # 上传配置（upload_export_fields 保证不含 API Token）
        try:
            batch = self._read_int(self._db_batch, "批量大小")
        except ValueError:
            batch = self._db_batch.get()
        data.update(upload_export_fields(
            self._upload_enabled.get(),
            self._cf_account.get(),
            self._cf_database.get(),
            batch,
        ))
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
        if "skip_processed" in data:
            self.skip_var.set(bool(data["skip_processed"]))
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
        # 定时任务配置
        if "schedule_enabled" in data:
            self._schedule_enabled.set(bool(data["schedule_enabled"]))
        if data.get("schedule_type") in ("interval", "daily"):
            self._schedule_type.set(data["schedule_type"])
        if "schedule_value" in data:
            try:
                self._schedule_value.set(str(int(data["schedule_value"])))
            except (TypeError, ValueError):
                pass
        if data.get("schedule_unit") in ("秒", "分", "时"):
            self._schedule_unit.set(data["schedule_unit"])
        if "schedule_time" in data:
            self._schedule_time.set(str(data["schedule_time"]).strip() or self._schedule_time.get())
        # 数据库上传配置（cf_token 涉密，导入时忽略该字段，绝不恢复）
        if "upload_enabled" in data:
            self._upload_enabled.set(bool(data["upload_enabled"]))
        if "cf_account" in data:
            self._cf_account.set(str(data["cf_account"]).strip())
        if "cf_database" in data:
            self._cf_database.set(str(data["cf_database"]).strip())
        if "db_batch" in data:
            try:
                self._db_batch.set(str(int(data["db_batch"])))
            except (TypeError, ValueError):
                pass
        # 应用定时开关：导入后按配置启动/停止
        if self._schedule_enabled.get():
            self._start_schedule()
        else:
            self._stop_schedule()
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

    def _read_upload_cfg(self):
        """从界面读取数据库上传配置（定时任务每次触发都会实时调用）。"""
        return {
            "enabled": bool(self._upload_enabled.get()),
            "cf_account": self._cf_account.get().strip(),
            "cf_database": self._cf_database.get().strip(),
            "cf_token": self._cf_token.get().strip(),
            "db_batch": self._read_int(self._db_batch, "批量大小"),
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
            upload_cfg = self._read_upload_cfg()
        except ValueError as exc:
            messagebox.showerror("参数错误", str(exc))
            return

        self.run_btn.config(state="disabled")
        self._clear_output()
        threading.Thread(
            target=self._run_worker,
            args=(log_path, out_dir, params, self.skip_var.get(), upload_cfg),
            daemon=True,
        ).start()

    def _run_worker(self, log_path, out_dir, params, skip_processed, upload_cfg=None):
        """后台执行处理流程，捕获标准输出回显到界面。"""
        text, status = self._run_detection(log_path, out_dir, params,
                                           skip_processed, upload_cfg)
        self._finish(text, f"运行{status}")

    def _run_detection(self, log_path, out_dir, params, skip_processed=True,
                       upload_cfg=None):
        """
        执行检测流程（纯计算，后台线程可调用），返回 (输出文本, 状态)。

        upload_cfg 开启且配置完整时，每个日志文件检测完成后先上传 D1，
        上传成功才标记已处理；单个文件上传失败不中断整轮，该文件不标记，
        下轮（含定时触发）会自动重新检测并重试（去重键幂等，不产生重复）。
        """
        buf = StringIO()
        try:
            with redirect_stdout(buf):
                files, err = collect_log_files(log_path)
                if err:
                    print(err)
                    return buf.getvalue(), "失败"
                # 开关开启但凭证不全：视为未配置上传（等同关闭），只提示一次
                if upload_cfg and upload_cfg.get("enabled") \
                        and not is_upload_configured(upload_cfg):
                    print("提示：已开启数据库上传，但 Cloudflare 账户 ID / 数据库 ID / "
                          "API Token 未配置完整，本次运行不上传。")
                    upload_cfg = dict(upload_cfg, enabled=False)
                # 已处理状态表（存放在输出目录下）：内容未变化的日志不再重复处理
                state = ProcessedState(
                    os.path.join(out_dir, PROCESSED_STATE_FILE)
                ) if skip_processed else None
                processed = skipped = failed = 0
                for path in files:
                    if state is not None and state.is_processed(path):
                        skipped += 1
                        print(f"跳过已处理（内容未变化）：{path}")
                        continue
                    try:
                        record_a, record_b, record_entry = parse_log(path)
                        abnormal = find_anomalies(
                            record_a, record_b, params["window"], record_entry,
                            entry_dedup_window=params["dedup"],
                            min_deviation=params["deviation"],
                        )
                        output_results(abnormal, path, out_dir)
                        if upload_cfg and upload_cfg.get("enabled"):
                            records = build_upload_records(abnormal, path)
                            inserted = upload_records(records, upload_cfg)
                            print(f"已上传 {len(records)} 条异常记录到 D1"
                                  f"（新插入 {inserted} 条）。")
                    except D1UploadError as exc:
                        # 上传失败：不标记已处理，继续下一个文件，下轮自动重试
                        failed += 1
                        print(f"上传失败（本轮不标记已处理，下轮将自动重试）：{path}\n错误：{exc}")
                        continue
                    # 上传成功（或未开启上传）才落盘状态，中途中断也不丢失进度
                    if state is not None:
                        state.mark(path)
                        state.save()
                    processed += 1
                summary = (f"\n全部处理完成：本次处理 {processed} 个日志，"
                           f"跳过已处理 {skipped} 个。")
                if failed:
                    summary += f"上传失败 {failed} 个（未标记已处理，下轮自动重试）。"
                print(summary)
        except Exception as exc:  # 界面层兜底，避免后台线程静默崩溃
            buf.write(f"\n发生错误：{exc}\n")
            return buf.getvalue(), "出错"
        return buf.getvalue(), "完成"

    def _finish(self, text, status):
        """把输出写到文本框，并恢复按钮状态（须在主线程调用）。"""
        self.output_box.insert("end", text)
        self.output_box.see("end")
        self.run_btn.config(state="normal")
        self.root.title(f"停车场异常车辆检测 - {status}")

    # ---------------- 定时任务（APScheduler） ----------------
    def _on_schedule_toggle(self):
        """定时任务开关：勾选即按当前配置启动，取消即停止。"""
        if self._schedule_enabled.get():
            self._start_schedule()
        else:
            self._stop_schedule()

    def _schedule_cfg_error(self):
        """校验当前定时配置，返回错误信息；无错返回 None。"""
        try:
            self._parse_params()
        except ValueError as exc:
            return f"检测参数无效：{exc}"
        try:
            if self._schedule_type.get() == "daily":
                validate_daily_time(self._schedule_time.get())
            else:
                value = int(self._schedule_value.get())
                if value <= 0:
                    raise ValueError("间隔值必须为正整数")
                interval_to_seconds(value, self._schedule_unit.get())
        except ValueError as exc:
            return str(exc)
        return None

    def _start_schedule(self):
        """按界面配置启动定时任务；配置非法时提示并回退开关。"""
        err = self._schedule_cfg_error()
        if err:
            messagebox.showerror("定时任务", err)
            self._schedule_enabled.set(False)
            self._update_schedule_status()
            return
        if self._schedule_type.get() == "daily":
            self._scheduler.start_daily(self._on_schedule_fire, self._schedule_time.get())
        else:
            self._scheduler.start_interval(
                self._on_schedule_fire,
                int(self._schedule_value.get()),
                self._schedule_unit.get(),
            )
        self._update_schedule_status()

    def _stop_schedule(self):
        """停止定时任务。"""
        self._scheduler.stop()
        self._update_schedule_status()

    def _update_schedule_status(self):
        """刷新定时任务状态标签（须在主线程调用）。"""
        if self._scheduler.running:
            nxt = self._scheduler.next_run
            nxt_txt = nxt.strftime("%Y-%m-%d %H:%M:%S") if nxt else "待定"
            self._schedule_status_var.set(f"定时任务：运行中，下次触发：{nxt_txt}")
        else:
            self._schedule_status_var.set("定时任务：未启动")

    def _on_schedule_fire(self):
        """定时触发入口（APScheduler 后台线程）：按当前界面配置执行一次检测。"""
        if self._schedule_running:
            self._post_text("\n[定时任务] 上一轮检测尚未结束，本次触发跳过。\n")
            return
        try:
            log_path = os.path.abspath(self.log_var.get().strip())
            out_dir = os.path.abspath(self.out_var.get().strip())
            params = self._parse_params()
            upload_cfg = self._read_upload_cfg()
        except ValueError as exc:
            self._post_text(f"\n[定时任务] 配置无效，本次跳过：{exc}\n")
            return
        self._schedule_running = True
        threading.Thread(
            target=self._scheduled_worker,
            args=(log_path, out_dir, params, self.skip_var.get(), upload_cfg),
            daemon=True,
        ).start()

    def _scheduled_worker(self, log_path, out_dir, params, skip_processed,
                          upload_cfg=None):
        """定时任务的后台执行：与手动检测共用同一检测流程（含数据库上传）。"""
        text, status = self._run_detection(log_path, out_dir, params,
                                           skip_processed, upload_cfg)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        self._post_text(f"\n===== 定时任务触发 {stamp}（运行{status}）=====\n{text}")
        self._schedule_running = False
        try:
            self.root.after(0, self._update_schedule_status)
        except RuntimeError:
            pass

    def _post_text(self, text):
        """把文本追加到输出区（任意线程可安全调用）。"""
        def do():
            self.output_box.insert("end", text)
            self.output_box.see("end")
        try:
            self.root.after(0, do)
        except RuntimeError:
            pass

    # ---------------- 应用图标（托盘 + 主窗口共用） ----------------
    def _make_icon_image(self):
        """生成蓝底白 P 的图标图片（PIL Image，供托盘与主窗口复用）。"""
        from PIL import Image, ImageFont, ImageDraw
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        # 蓝色正方形底（四周留 1px 透明边，避免贴边不清晰）
        draw.rectangle((1, 1, 62, 62), fill="#2f6fed")
        # 优先使用系统字体放大 P，失败则退回默认字体（默认字体不支持中文，故用 ASCII）
        try:
            font = ImageFont.truetype("arial.ttf", 55)
        except (OSError, Exception):
            font = ImageFont.load_default()
        # 把文本水平垂直居中
        text = "P"
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        pos = ((64 - tw) / 2 - bbox[0], (64 - th) / 2 - bbox[1])
        draw.text(pos, text, fill="white", font=font)
        return img

    def _set_window_icon(self):
        """为主窗口设置应用图标（与托盘一致的蓝底白 P）。"""
        try:
            from PIL import ImageTk
        except ImportError:
            return
        icon_img = ImageTk.PhotoImage(self._make_icon_image())
        # 保留引用，避免被垃圾回收导致图标消失
        self._window_icon = icon_img
        self.root.iconphoto(True, icon_img)

    # ---------------- 系统托盘 ----------------
    def _setup_tray(self):
        """初始化系统托盘图标（pystray）；不可用时回退为普通最小化。"""
        try:
            import pystray
        except ImportError:
            return
        img = self._make_icon_image()
        menu = pystray.Menu(
            pystray.MenuItem("显示主窗口", self._tray_show),
            pystray.MenuItem("退出程序", self._tray_quit),
        )
        self._tray_icon = pystray.Icon("parkcheck", img, "停车场异常车辆检测", menu)
        threading.Thread(target=self._tray_icon.run, daemon=True).start()
        self._tray_available = True

    def _tray_show(self, icon=None, item=None):
        """托盘菜单：显示主窗口。"""
        try:
            self.root.after(0, self._show_main_window)
        except RuntimeError:
            pass

    def _tray_quit(self, icon=None, item=None):
        """托盘菜单：完全退出程序。"""
        try:
            self.root.after(0, self._quit_app)
        except RuntimeError:
            pass

    def _show_main_window(self):
        """从托盘恢复并置前主窗口。"""
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _on_close(self):
        """关闭按钮：收纳到系统托盘继续后台运行；托盘不可用时最小化到任务栏。"""
        if self._tray_available:
            self.root.withdraw()
            if not self._hidden_to_tray:
                self._hidden_to_tray = True
                try:
                    self._tray_icon.notify(
                        "程序已最小化到系统托盘，双击图标可重新打开。", "停车场异常车辆检测"
                    )
                except Exception:
                    pass
        else:
            self.root.iconify()

    def _quit_app(self):
        """完全退出：停止调度器、移除托盘图标并关闭窗口。"""
        self._scheduler.stop()
        if self._tray_icon is not None:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
        try:
            self.root.destroy()
        except RuntimeError:
            pass


def main():
    root = tk.Tk()
    CheckGui(root)
    root.mainloop()


if __name__ == "__main__":
    main()